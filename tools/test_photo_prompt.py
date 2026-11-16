"""Самопроверка P1: исходник и кэш больше не подменяются одноимёнными файлами,
а провал не выдаётся за успех.

Проверки:
  1. same.png из двух папок -> два разных файла в input, оба с правильным содержимым.
  2. same.png и same.jpg -> разные кэши; кэш одного не отдаётся другому.
  3. --scene даёт отдельный кэш и не теряется при попадании в кэш.
  4. Кэш с чужим отпечатком не берётся.
  5. Флаги командной строки (--negative-extra) не оседают в кэше.
  6. generate() без картинки и пакетный режим при провале дают код 1.
  7. build_workflows.py --run: опечатка в имени и отказ API -> код 1.
  8. --dry-run не трогает LM Studio; выгрузка не трогает чужие модели.

Модели и ComfyUI не нужны: describe/lm_*/generate подменяются.

    python tools/test_photo_prompt.py
"""
import argparse
import contextlib
import io
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import check_install as ci
import jobs
import photo_prompt as pp
import settings as st
from PIL import Image

TMP = Path(tempfile.mkdtemp(prefix="photo_prompt_test_"))
passed, failed = [], []

st.utf8_streams()      # кириллица и в консоли, и в пайпе

# Теггер в проверках не нужен: 1.7 ГБ моделей и 2 с на фото. Отдельный тест
# подменяет pp.tagger.tags напрямую и проверяет саму логику добавки.
os.environ.setdefault("COMIC_TAGGER", "off")


def capture(fn):
    """Выполняет fn, возвращает (результат, весь stdout)."""
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        val = fn()
    return val, buf.getvalue()


def check(name, cond, detail=""):
    (passed if cond else failed).append(name)
    print(("  ok   " if cond else "  FAIL ") + name + (f" — {detail}" if detail and not cond else ""))


def make(path, color, size=(240, 180)):
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", size, color).save(path)


def patch_roots():
    pp.ROOT = TMP / "root"
    pp.INPUT_DIR = TMP / "input"
    pp.OUTPUT_DIR = TMP / "output"
    (pp.ROOT).mkdir(parents=True, exist_ok=True)


def test_input_no_collision():
    print("\n1) подмена исходника")
    a, b = TMP / "A", TMP / "B"
    make(a / "same.png", (255, 0, 0))
    make(b / "same.png", (0, 0, 255))
    old = time.time() - 86400
    import os
    os.utime(b / "same.png", (old, old))  # второй файл старше — прежняя логика его не копировала

    n1 = pp.ensure_input(str(a / "same.png"))
    n2 = pp.ensure_input(str(b / "same.png"))
    p1 = Image.open(pp.INPUT_DIR / n1).getpixel((10, 10))
    p2 = Image.open(pp.INPUT_DIR / n2).getpixel((10, 10))
    check("два same.png -> разные имена", n1 != n2, f"{n1} == {n2}")
    check("ComfyUI получает первый файл", p1 == (255, 0, 0), str(p1))
    check("ComfyUI получает второй файл", p2 == (0, 0, 255), str(p2))
    check("имя устойчиво при повторе", pp.ensure_input(str(a / "same.png")) == n1)
    check("имя читаемо", n1.startswith("same_") and n1.endswith(".png"), n1)


def test_cache_by_content():
    print("\n2) кэш по содержимому, а не по имени")
    a = TMP / "A" / "same.png"
    j = TMP / "same.jpg"
    make(j, (0, 255, 0))
    pc, pj = pp.cache_path(str(a)), pp.cache_path(str(j))
    check("same.png и same.jpg -> разные кэши", pc != pj, f"{pc.name} == {pj.name}")
    pp.write_cache(str(a), {"subject": "adult woman, red", "negative_extra": ""})
    check("кэш png читается для png", pp.read_cache(str(a)) is not None)
    check("кэш png НЕ отдаётся jpg", pp.read_cache(str(j)) is None)


def test_cache_scene():
    print("\n3) сцена входит в ключ кэша")
    img = TMP / "A" / "same.png"
    plain, beach = pp.cache_path(str(img)), pp.cache_path(str(img), "beach at sunset")
    check("сцена -> другой файл кэша", plain != beach, f"{plain.name} == {beach.name}")
    check("slug безопасен для имени", "__beach-at-sunset-" in beach.name, beach.name)
    check("без сцены сценический кэш не берётся", pp.read_cache(str(img), "beach at sunset") is None)
    pp.write_cache(str(img), {"subject": "adult woman, beach"}, "beach at sunset")
    got = pp.read_cache(str(img), "beach at sunset")
    check("сценический кэш читается", bool(got) and got.get("scene") == "beach at sunset")
    check("обычный кэш не подменяется сценическим", pp.read_cache(str(img)) is not None)


def test_stale_fingerprint():
    print("\n4) кэш с чужим отпечатком")
    img = TMP / "A" / "same.png"
    p = pp.cache_path(str(img))
    data = json.loads(p.read_text(encoding="utf-8"))
    data["sha1"] = "000000000000"
    p.write_text(json.dumps(data), encoding="utf-8")
    check("чужой sha1 -> кэш не берётся", pp.read_cache(str(img)) is None)


def run_main(argv, subject="adult woman, black dress", scene_result=None, scene_fails=False):
    """main() с подменёнными моделью и ComfyUI; возвращает stdout."""
    calls = []

    def fake_describe(image_path, model):
        calls.append(("describe", image_path))
        return {"subject": subject, "negative_extra": "missing glasses", "notes": "тест"}

    def fake_scene(desc, scene, model):
        calls.append(("scene", scene))
        if scene_fails:
            return None
        return {"subject": subject + ", " + scene, "negative_extra": "missing glasses",
                "notes": "тест", "scene": scene}

    saved = (pp.describe, pp.rewrite_scene, pp.lm_models, pp.pick_model, pp.unload_all, sys.argv)
    pp.describe, pp.rewrite_scene = fake_describe, fake_scene
    pp.lm_models, pp.pick_model = (lambda strict=True: {}), (lambda resp, wanted: "test-model")
    pp.unload_all = lambda resp: []
    sys.argv = ["photo_prompt.py"] + argv
    buf = io.StringIO()
    try:
        with contextlib.redirect_stdout(buf):
            pp.main()
    finally:
        pp.describe, pp.rewrite_scene, pp.lm_models, pp.pick_model, pp.unload_all, sys.argv = saved
    return buf.getvalue(), calls


def test_cli_flags_not_cached():
    print("\n5) флаги прогона не остаются в кэше")
    img = TMP / "C" / "flags.png"
    make(img, (120, 120, 120))
    run_main(["--image", str(img), "--style", "thepit", "--negative-extra", "zzz-tag"])
    cached = pp.read_cache(str(img))
    check("кэш записан", cached is not None)
    check("--negative-extra не попал в кэш", "zzz-tag" not in (cached or {}).get("negative_extra", ""),
          (cached or {}).get("negative_extra", ""))
    out2, calls2 = run_main(["--image", str(img), "--style", "thepit"])
    check("второй прогон без флага чист", "zzz-tag" not in out2, out2.strip().splitlines()[-1])
    check("второй прогон не зовёт модель", calls2 == [], str(calls2))


def test_scene_not_ignored_on_cache_hit():
    print("\n6) --scene не теряется при попадании в кэш")
    img = TMP / "D" / "scene.png"
    make(img, (200, 100, 50))
    run_main(["--image", str(img), "--style", "thepit"])
    check("описание без сцены закэшировано", pp.read_cache(str(img)) is not None)
    out, calls = run_main(["--image", str(img), "--style", "thepit", "--scene", "beach at sunset"])
    check("со сценой модель вызвана", any(c[0] == "scene" for c in calls), str(calls))
    check("в промпте есть сцена", "beach at sunset" in out, out[-200:])
    cached = pp.read_cache(str(img), "beach at sunset")
    check("сценический кэш записан отдельно", bool(cached) and "beach" in cached["subject"])
    check("кэш без сцены не затёрт", "beach" not in (pp.read_cache(str(img)) or {}).get("subject", ""))

    calls_before = list(calls)
    out2, calls2 = run_main(["--image", str(img), "--style", "thepit", "--scene", "beach at sunset"])
    check("повтор со сценой идёт из кэша", calls2 == [], str(calls2))
    check("повтор сохраняет сцену", "beach at sunset" in out2)


class _Clock:
    """Время без сна: generate() ждёт до 900 с реального времени."""

    def __init__(self):
        self.t = 0.0

    def time(self):
        self.t += 1.0
        return self.t

    def sleep(self, seconds):
        pass


def fake_comfy(history):
    pp.post = lambda url, payload, timeout=300: {"prompt_id": "pid-1"}
    pp.get = lambda url, timeout=30: {"pid-1": history}


def test_generate_requires_image():
    print("\n7) generate(): success без кадра — не успех")
    saved = (pp.post, pp.get, pp.time)
    pp.time = _Clock()
    try:
        fake_comfy({"status": {"status_str": "success"}, "outputs": {}})
        (ok, name), text = capture(lambda: pp.generate("07_photo_thepit", "adult woman", "", seed=1))
        check("success без картинок -> провал", ok is False and name is None, f"{ok}, {name}")
        check("сказано, что изображения нет", "изображения нет" in text, text.strip()[-120:])

        fake_comfy({"status": {"status_str": "success"},
                    "outputs": {"9": {"images": [{"filename": "comic/x_00001_.png"}]}}})
        (ok, name), _ = capture(lambda: pp.generate("07_photo_thepit", "adult woman", "", seed=1))
        check("success с картинкой -> успех", ok is True and name == "comic/x_00001_.png", f"{ok}, {name}")

        fake_comfy({"status": {"status_str": "error", "messages": [
            ["execution_error", {"node_type": "KSampler",
                                 "exception_message": "CUDA out of memory"}]]}, "outputs": {}})
        (ok, name), text = capture(lambda: pp.generate("07_photo_thepit", "adult woman", "", seed=1))
        check("error -> провал", ok is False and name is None, f"{ok}, {name}")
        check("причина видна в логе", "CUDA out of memory" in text and "KSampler" in text,
              text.strip()[-160:])
    finally:
        pp.post, pp.get, pp.time = saved


def batch_args(folder, **over):
    ns = argparse.Namespace(
        batch=str(folder), workflow=None, style="thepit", scene=None, variants=1,
        model=None, subject=None, no_llm=False, from_json=None, preset=None, save_preset=None,
        recheck=False, keep_llm=False, comfy=None, negative_extra="", negative_drop="",
        denoise=None, style_weight=None, seed=7, force=False, dry_run=False, generate=True,
        unload_all=False, cn_strength=None, no_tags=False,
    )
    for k, v in over.items():
        setattr(ns, k, v)
    return ns


def test_batch_reports_failure():
    print("\n8) пакетный режим: кадры, run.json, код возврата")
    folder = TMP / "batch"
    for n in ("a.png", "b.png"):
        make(folder / n, (30, 30, 30))
    saved = (pp.describe, pp.lm_models, pp.pick_model, pp.unload_all,
             pp.resolve_comfy, pp.generate)
    pp.describe = lambda p, m: {"subject": "adult woman, test", "negative_extra": "", "notes": ""}
    pp.lm_models, pp.pick_model = (lambda strict=True: {}), (lambda r, w: "m")
    pp.unload_all = lambda r: []
    pp.resolve_comfy = lambda c: "http://127.0.0.1:8199"
    books = lambda: list((pp.ROOT / "out").rglob("run.json"))
    try:
        pp.generate = lambda *a, **k: (False, None)
        rc, text = capture(lambda: pp.run_batch(batch_args(folder), "07_photo_thepit"))
        check("всё упало -> код 1", rc == 1, str(rc))
        check("кадров 0, провалов 2", "кадров: 0, провалов: 2" in text, text.strip()[-160:])
        check("run.json не создан", books() == [], str(books()))

        def fake_generate(*a, **k):
            name = "07_photo_thepit_00001_.png"
            pp.OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
            (pp.OUTPUT_DIR / name).write_bytes(b"png")
            return True, name

        pp.generate = fake_generate
        rc, text = capture(lambda: pp.run_batch(batch_args(folder), "07_photo_thepit"))
        check("все кадры -> код 0", rc == 0, str(rc))
        check("кадров 2, провалов 0", "кадров: 2, провалов: 0" in text, text.strip()[-160:])
        check("run.json на каждое фото", len(books()) == 2, str(books()))
        check("в run.json записан кадр",
              all(json.loads(b.read_text(encoding="utf-8"))["runs"][0]["file"] for b in books()))

        # одиночный путь: провал генерации должен дать код 1, а не тихий ноль
        img = TMP / "one.png"
        make(img, (1, 2, 3))
        pp.generate = lambda *a, **k: (False, None)
        code = None
        try:
            with contextlib.redirect_stdout(io.StringIO()):
                run_main(["--image", str(img), "--style", "thepit", "--no-llm",
                          "--subject", "adult woman", "--generate"])
        except SystemExit as e:
            code = e.code
        check("одиночный прогон при провале -> код 1", code == 1, str(code))
    finally:
        (pp.describe, pp.lm_models, pp.pick_model, pp.unload_all,
         pp.resolve_comfy, pp.generate) = saved


def test_build_workflows_exit_codes():
    print("\n9) build_workflows.py --run: код возврата")
    tool = str(Path(__file__).parent / "build_workflows.py")
    r = subprocess.run([sys.executable, tool, "--run", "07_photo_tehpit"],
                       capture_output=True, text=True, encoding="utf-8", errors="replace")
    out = (r.stdout or "") + (r.stderr or "")
    check("опечатка в имени -> код 1", r.returncode == 1, f"код {r.returncode}")
    check("опечатка объяснена", "нет сборки" in out, out.strip()[-160:])
    check("опечатка перечисляет сборки", "07_photo_thepit" in out, out.strip()[-160:])

    r = subprocess.run([sys.executable, tool, "--run"],
                       capture_output=True, text=True, encoding="utf-8", errors="replace")
    check("--run без имени -> ошибка", r.returncode != 0, f"код {r.returncode}")

    with socket.socket() as s:
        s.settimeout(0.3)
        busy = s.connect_ex(("127.0.0.1", 8199)) == 0
    if busy:
        print("  пропуск проверки отказа API: 8199 занят, не хочу ставить реальную генерацию")
        return
    r = subprocess.run([sys.executable, tool, "--run", "07_photo_thepit"],
                       capture_output=True, text=True, encoding="utf-8", errors="replace")
    out = (r.stdout or "") + (r.stderr or "")
    check("отказ API -> код 1", r.returncode == 1, f"код {r.returncode}")
    check("причина отказа в логе", "ОШИБКА" in out, out.strip()[-160:])


def run_plain(argv):
    """main() с подставленным argv, без подмены функций."""
    saved = sys.argv
    sys.argv = ["photo_prompt.py"] + argv
    buf = io.StringIO()
    code = 0
    try:
        with contextlib.redirect_stdout(buf):
            pp.main()
    except SystemExit as e:
        code = e.code
    finally:
        sys.argv = saved
    return code, buf.getvalue()


def test_dry_run_does_not_touch_lm():
    print("\n10) --dry-run не трогает LM Studio")
    img = TMP / "dry.png"
    make(img, (5, 5, 5))
    calls = []
    saved = (pp.lm_models, pp.pick_model, pp.describe, pp.unload_model,
             pp.unload_all, pp.post, pp.resolve_comfy, pp.comfy_probe)
    pp.lm_models = lambda strict=True: (calls.append("lm_models") or {"models": []})
    pp.pick_model = lambda r, w: (calls.append("pick_model") or "m")
    pp.describe = lambda p, m: (calls.append("describe") or
                                {"subject": "adult woman", "negative_extra": "", "notes": ""})
    pp.unload_model = lambda r, m: (calls.append("unload_model") or [])
    pp.unload_all = lambda r: (calls.append("unload_all") or [])
    pp.post = lambda url, payload, timeout=300: (calls.append("post") or {})
    pp.resolve_comfy = lambda c=None: (calls.append("resolve_comfy") or "http://x")
    try:
        code, text = run_plain(["--image", str(img), "--style", "thepit", "--dry-run"])
        check("без кэша -> отказ, код не 0", code not in (0, None), str(code))
        check("LM Studio не тронут", calls == [], str(calls))
        check("сказано, чего не хватает", "в кэше нет" in text, text.strip()[-160:])
        check("подсказан выход", "--no-llm --subject" in text or "--preset" in text,
              text.strip()[-160:])

        pp.write_cache(str(img), {"subject": "adult woman, black dress", "negative_extra": ""})
        calls.clear()
        code, text = run_plain(["--image", str(img), "--style", "thepit", "--dry-run"])
        check("с кэшем -> код 0", code == 0, str(code))
        check("с кэшем LM Studio не тронут", calls == [], str(calls))
        check("промпт показан", "adult woman, black dress" in text, text.strip()[-160:])

        calls.clear()
        code, text = run_plain(["--image", str(img), "--style", "thepit",
                                "--no-llm", "--subject", "adult woman, test", "--dry-run"])
        check("--no-llm --dry-run без LM", code == 0 and calls == [], f"{code} {calls}")

        # пакетный --dry-run ведёт себя так же
        folder = TMP / "dry_batch"
        make(folder / "x.png", (9, 9, 9))
        calls.clear()
        rc, text = capture(lambda: pp.run_batch(batch_args(folder, dry_run=True), "07_photo_thepit"))
        check("пакет без кэша -> код 1", rc == 1, str(rc))
        check("пакет LM Studio не тронул", calls == [], str(calls))
        check("пакет сказал, что показать нечего", "ни одного описания" in text, text.strip()[-160:])

        pp.write_cache(str(folder / "x.png"),
                       {"subject": "adult woman, from cache", "negative_extra": ""})
        calls.clear()
        rc, text = capture(lambda: pp.run_batch(batch_args(folder, dry_run=True), "07_photo_thepit"))
        check("пакет с кэшем -> код 0", rc == 0, str(rc))
        check("пакет с кэшем без LM", calls == [], str(calls))
        check("пакет взял описание из кэша", "adult woman, from cache" in text, text.strip()[-160:])

        # полностью закэшированная пачка не должна требовать LM Studio вообще
        pp.lm_models = lambda strict=True: (_ for _ in ()).throw(RuntimeError("LM Studio не запущен"))
        rc, text = capture(lambda: pp.run_batch(
            batch_args(folder, dry_run=False, generate=False), "07_photo_thepit"))
        check("закэшированная пачка живёт без LM Studio", rc == 0, str(rc))
    finally:
        (pp.lm_models, pp.pick_model, pp.describe, pp.unload_model,
         pp.unload_all, pp.post, pp.resolve_comfy, pp.comfy_probe) = saved


def test_unload_only_own_model():
    print("\n11) выгрузка не трогает чужие модели")
    resp = {"models": [
        {"key": "ours-vlm", "loaded_instances": [{"instance_id": "inst-ours"}]},
        {"key": "other-task-27b", "loaded_instances": [{"instance_id": "inst-other"}]},
    ]}
    unloaded = []
    saved = (pp.lm_models, pp.post)
    pp.lm_models = lambda strict=True: resp
    pp.post = lambda url, payload, timeout=300: (unloaded.append(payload["instance_id"]) or {})
    try:
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            freed = pp.unload_model(resp, "ours-vlm")
        text = buf.getvalue()
        check("выгружена только своя модель", unloaded == ["inst-ours"], str(unloaded))
        check("freed содержит свою", freed == ["inst-ours"], str(freed))
        check("о чужой сказано", "other-task-27b" in text and "не трогаю" in text, text.strip()[-160:])

        unloaded.clear()
        with contextlib.redirect_stdout(io.StringIO()):
            pp.unload_all(resp)
        check("--unload-all выгружает всё", sorted(unloaded) == ["inst-other", "inst-ours"],
              str(unloaded))

        # обычный прогон (без --generate) выгружает только свою модель
        img = TMP / "unload.png"
        make(img, (5, 6, 7))
        saved2 = (pp.pick_model, pp.describe)
        pp.pick_model = lambda r, w: "ours-vlm"
        pp.describe = lambda p, m: {"subject": "adult woman, test", "negative_extra": "",
                                    "notes": ""}
        unloaded.clear()
        try:
            code, text = run_plain(["--image", str(img), "--style", "thepit"])
        finally:
            pp.pick_model, pp.describe = saved2
        check("прогон выгрузил только свою", unloaded == ["inst-ours"], str(unloaded))
        check("чужую не тронул", "other-task-27b" not in unloaded, str(unloaded))
    finally:
        pp.lm_models, pp.post = saved


def capture_payload(workflow, subject="adult woman, test", extra="", weight=None, wf_dir=None):
    """Прогоняет generate() с заглушкой ComfyUI и возвращает (тексты, LoRA) из запроса."""
    sent = {}
    saved = (pp.post, pp.get, pp.time, pp.WF_DIR)
    pp.post = lambda url, payload, timeout=300: (sent.update(payload["prompt"]) or {"prompt_id": "p"})
    pp.get = lambda url, timeout=30: {"p": {"status": {"status_str": "success"},
                                            "outputs": {"9": {"images": [{"filename": "x.png"}]}}}}
    pp.time = _Clock()
    if wf_dir:
        pp.WF_DIR = wf_dir
    try:
        with contextlib.redirect_stdout(io.StringIO()):
            pp.generate(workflow, subject, extra, seed=1, style_weight=weight)
    finally:
        pp.post, pp.get, pp.time, pp.WF_DIR = saved
    texts = [n["inputs"]["text"] for n in sent.values() if n["class_type"] == "CLIPTextEncode"]
    loras = {n["inputs"]["lora_name"]: n["inputs"]["strength_model"]
             for n in sent.values() if n["class_type"] == "LoraLoaderModelOnly"}
    return texts, loras


def test_style_from_graph():
    print("\n12) CLI берёт стиль из графа, а не из своей таблицы")
    names = ("07_photo_thepit", "07_photo_comic", "07_photo_jp", "07_photo_bimbo", "08_photo_gta")
    for name in names:
        data = json.loads((pp.WF_DIR / (name + ".api.json")).read_text(encoding="utf-8"))
        meta = data.get("meta") or {}
        nonzero = [n["inputs"] for n in data["prompt"].values()
                   if n["class_type"] == "LoraLoaderModelOnly" and n["inputs"]["strength_model"]]
        check(f"{name}: шаблон с {{{{subject}}}}", "{{subject}}" in (meta.get("positive_template") or ""),
              str(meta)[:120])
        check(f"{name}: негатив и стилевая LoRA из графа",
              bool(meta.get("negative")) and len(nonzero) == 1
              and meta.get("style_lora") == nonzero[0]["lora_name"]
              and meta.get("style_weight") == nonzero[0]["strength_model"],
              f"{meta.get('style_lora')} {meta.get('style_weight')} vs {nonzero}")

    texts, _ = capture_payload("08_photo_gta")
    pos, neg = texts
    for tag in ("warm golden sunlight", "saturated colors", "Los Santos", "palm trees",
                "lens flare", "advertising poster"):
        check(f"GTA сохранил «{tag}»", tag in pos, pos[:160])
    for tag in ("dark", "gloomy", "night", "rain"):
        check(f"GTA негатив сохранил «{tag}»", tag in neg, neg[:160])
    check("GTA: субъект между светом и композицией",
          pos.index("warm golden sunlight") < pos.index("adult woman") < pos.index("bold black outlines"),
          pos[:200])

    texts, _ = capture_payload("07_photo_bimbo")
    neg = next(t for t in texts if t.startswith("photo, photorealistic"))
    pos = next(t for t in texts if not t.startswith("photo, photorealistic"))
    for tag in ("warm skin tone", "natural skin color"):
        check(f"bimbo сохранил «{tag}»", tag in pos, pos[:160])
    for tag in ("blonde hair", "dark sunglasses", "white hair"):
        check(f"bimbo не навязывает «{tag}»", tag not in pos + " " + neg,
              (pos + " " + neg)[-200:])
    texts, _ = capture_payload("07_photo_bimbo",
                               subject="adult woman, brown hair, dark sunglasses")
    bim_pos = next(t for t in texts if not t.startswith("photo, photorealistic"))
    check("bimbo: волосы и очки берутся из описания",
          "brown hair" in bim_pos and "dark sunglasses" in bim_pos
          and "blonde hair" not in bim_pos, bim_pos[:200])

    _, loras = capture_payload("07_photo_bimbo", weight=0.45)
    check("bimbo: вес 0.6 -> 0.45", loras.get("style\\bimbofusion.safetensors") == 0.45, str(loras))
    _, loras = capture_payload("08_photo_gta", weight=0.45)
    check("GTA: вес 0.85 -> 0.45", loras.get("GTAV_IL_MIX.safetensors") == 0.45, str(loras))
    _, loras = capture_payload("07_photo_thepit", weight=0.55)
    check("thepit: вес 0.9 -> 0.55", loras.get("thepit2_1768210.safetensors") == 0.55, str(loras))
    check("прочие LoRA остались на нуле",
          all(v == 0 for k, v in loras.items() if k != "thepit2_1768210.safetensors"), str(loras))

    legacy = TMP / "wf_legacy"
    legacy.mkdir(parents=True, exist_ok=True)
    data = json.loads((pp.WF_DIR / "07_photo_thepit.api.json").read_text(encoding="utf-8"))
    data.pop("meta", None)
    (legacy / "07_photo_thepit.api.json").write_text(json.dumps(data, ensure_ascii=False),
                                                    encoding="utf-8")
    texts, _ = capture_payload("07_photo_thepit", wf_dir=legacy)
    check("граф без meta: промпт собран по STYLE_PREFIX",
          texts[0].startswith("comic book illustration"), texts[0][:100])
    check("граф без meta: негатив собран", "bad anatomy" in texts[1], texts[1][:100])

    data["meta"] = dict(data.get("meta") or {}, style_lora="")
    (legacy / "07_photo_thepit.api.json").write_text(json.dumps(data, ensure_ascii=False),
                                                     encoding="utf-8")
    code = None
    try:
        capture_payload("07_photo_thepit", weight=0.5, wf_dir=legacy)
    except SystemExit as e:
        code = e.code
    check("--style-weight без опознанной LoRA -> отказ", code not in (0, None), str(code))


def test_portable_paths():
    print("\n13) пути и установка не зависят от машины автора")
    tools = Path(__file__).parent
    disk = re.compile(r"(?<![\w.])[A-Za-z]:\\")
    bad = []
    scanned = [p for p in sorted(tools.iterdir())
               if p.suffix.lower() in (".py", ".ps1") and p.name != Path(__file__).name]
    webapp = tools.parent / "webapp"
    if webapp.is_dir():
        scanned += [p for p in sorted(webapp.iterdir()) if p.suffix == ".py"]
    for p in scanned:
        for i, line in enumerate(p.read_text(encoding="utf-8").splitlines(), 1):
            if disk.search(line):
                bad.append(f"{p.name}:{i}")
    check("в скриптах нет литералов диска", not bad, ", ".join(bad))

    yaml = TMP / "extra_model_paths.yaml"
    yaml.write_text("comic_shared:\n  base_path: Q:\\models\n", encoding="utf-8")
    no_paths = TMP / "нет-путей.json"
    got = st.resolve(env={}, yaml_path=yaml, paths_path=no_paths)
    check("base_path читается из yaml", str(got["models"]) == "Q:\\models", str(got["models"]))
    check("без comic_paths.json input/output рядом с models",
          got["input"] == got["models"].parent / "input"
          and got["output"] == got["models"].parent / "output", str(got))

    paths = TMP / "comic_paths.json"
    paths.write_text(json.dumps({"input": "R:\\in", "output": "R:\\out",
                                 "lm_model": "pinned@iq3_m",
                                 "lm_url": "http://127.0.0.1:8765"}), encoding="utf-8")
    got = st.resolve(env={}, yaml_path=yaml, paths_path=paths)
    check("comic_paths.json задаёт input/output отдельно от models",
          str(got["input"]) == "R:\\in" and str(got["output"]) == "R:\\out",
          f'{got["input"]} / {got["output"]}')
    check("comic_paths.json не меняет models", str(got["models"]) == "Q:\\models", str(got["models"]))
    check("comic_paths.json задаёт модель описания", got["lm_model"] == "pinned@iq3_m",
          str(got.get("lm_model")))
    got = st.resolve(env={"LM_MODEL": "env@q8"}, yaml_path=yaml, paths_path=paths)
    check("LM_MODEL важнее файла", got["lm_model"] == "env@q8", str(got.get("lm_model")))
    check("comic_paths.json задаёт адрес сервиса описаний",
          got["lm"] == "http://127.0.0.1:8765", str(got.get("lm")))
    got = st.resolve(env={"LM_URL": "http://127.0.0.1:9999"}, yaml_path=yaml, paths_path=paths)
    check("LM_URL важнее файла", got["lm"] == "http://127.0.0.1:9999", str(got.get("lm")))
    got = st.resolve(env={"COMFY_INPUT": str(TMP / "env_in")}, yaml_path=yaml, paths_path=paths)
    check("COMFY_INPUT важнее comic_paths.json", got["input"] == TMP / "env_in", str(got["input"]))
    got = st.resolve(env={"COMFY_SHARED": str(TMP / "shared")}, yaml_path=yaml, paths_path=paths)
    check("COMFY_SHARED важнее comic_paths.json",
          got["input"] == TMP / "shared" / "input"
          and got["output"] == TMP / "shared" / "output", str(got["input"]))
    bad = TMP / "битый-пути.json"
    bad.write_text("{", encoding="utf-8")
    got = st.resolve(env={}, yaml_path=yaml, paths_path=bad)
    check("битый comic_paths.json — как отсутствующий",
          got["input"] == got["models"].parent / "input", str(got["input"]))
    got = st.resolve(env={}, yaml_path=yaml, paths_path=no_paths)
    check("без comic_paths.json модель не задана", got["lm_model"] == "",
          str(got.get("lm_model")))

    got = st.resolve(env={"COMFY_MODELS": str(TMP / "m")}, yaml_path=yaml)
    check("env важнее yaml", got["models"] == TMP / "m", str(got["models"]))
    got = st.resolve(env={}, yaml_path=TMP / "нет-такого.yaml")
    check("без yaml — раскладка рядом с репозиторием", got["models"] == st.ROOT / "models",
          str(got["models"]))

    # CLI из любой папки: пути считаются от файла скрипта, не от текущего каталога
    img = TMP / "cwd.png"
    make(img, (7, 7, 7))
    r = subprocess.run([sys.executable, str(tools / "photo_prompt.py"), "--image", str(img),
                        "--style", "thepit", "--no-llm", "--subject", "adult woman",
                        "--dry-run"], cwd=str(TMP), capture_output=True, text=True,
                       encoding="utf-8", errors="replace")
    check("CLI работает из чужого cwd", r.returncode == 0 and "итоговый промпт" in r.stdout,
          (r.stdout + r.stderr).strip()[-200:])

    # check_install: полный набор, отсутствие depth, пропавшая модель, пропавшая строка в доке
    models = TMP / "install_models"
    needed, broken = ci.workflow_models(tools.parent / "workflows")
    check("все графы читаются", not broken, str(broken))

    def install_path(name, rec):
        for t in rec["types"]:
            dirs = ci.expected_dirs(t)
            if dirs:
                return models / dirs[0] / name
        return models / Path(name).name

    for name, rec in needed.items():
        p = install_path(name, rec)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(b"x")
    docs = TMP / "DOWNLOADS.md"
    docs.write_text("\n".join(needed), encoding="utf-8")

    def check_install_run():
        r = subprocess.run([sys.executable, str(tools / "check_install.py"),
                            "--models", str(models), "--docs", str(docs)],
                           capture_output=True, text=True, encoding="utf-8", errors="replace")
        return r.returncode, (r.stdout + r.stderr)

    rc, out = check_install_run()
    check("полный набор -> код 0", rc == 0, out[-200:])
    check("найдены все 22 модели сборок", "моделей в сборках: 22" in out, out[-200:])

    (models / "depth_anything_v2_vitl.pth").unlink()
    rc, out = check_install_run()
    check("depth не считается пропажей (его качает узел)", rc == 0, out[-200:])

    missing = models / "loras" / "thepit2_1768210.safetensors"
    if not missing.exists():
        missing = next(p for p in models.rglob("thepit2_1768210.safetensors"))
    missing.unlink()
    rc, out = check_install_run()
    check("пропала модель -> код 1", rc == 1 and "thepit2_1768210" in out, out[-200:])
    check("пропажа названа", "ждёт в" in out, out[-200:])

    missing.write_bytes(b"x")
    docs.write_text("\n".join(n for n in needed if "bimbofusion" not in n), encoding="utf-8")
    rc, out = check_install_run()
    check("модель не описана в инструкции -> код 1",
          rc == 1 and "нет в инструкции" in out, out[-200:])


def test_passport_records_workflow():
    print("\n14) run.json: сборка у каждого кадра")
    img = TMP / "passport.png"
    make(img, (11, 22, 33))
    saved = (pp.ROOT, pp.OUTPUT_DIR)
    pp.ROOT = TMP / "root_pass"
    pp.OUTPUT_DIR = TMP / "out_comic"
    pp.OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    try:
        tag = pp.image_tag(img)
        folder = pp.ROOT / "out" / tag
        folder.mkdir(parents=True)
        # старый паспорт: одна сборка на папку, у записей её нет
        (folder / "run.json").write_text(json.dumps({"workflow": "07_photo_thepit", "runs": [
            {"seed": 1, "denoise": None, "file": "08_photo_gta_00001_.png",
             "subject": "x", "negative_extra": ""},
            {"seed": 2, "denoise": None, "file": "07_photo_bimbo_00003_.png",
             "subject": "x", "negative_extra": ""},
        ]}), encoding="utf-8")
        (pp.OUTPUT_DIR / "07_photo_thepit_00099_.png").write_bytes(b"png")
        with contextlib.redirect_stdout(io.StringIO()):
            pp.save_run(str(img), "07_photo_thepit",
                        {"subject": "x", "negative_extra": ""}, 3, None,
                        "07_photo_thepit_00099_.png")
        book = json.loads((folder / "run.json").read_text(encoding="utf-8"))
        check("сборка записана у нового кадра",
              book["runs"][-1]["workflow"] == "07_photo_thepit", str(book["runs"][-1]))
        check("старые записи восстановлены по имени кадра",
              [r["workflow"] for r in book["runs"][:2]] == ["08_photo_gta", "07_photo_bimbo"],
              str(book["runs"][:2]))
        check("общее поле workflow убрано", "workflow" not in book, str(list(book)))
        check("workflows перечислены",
              book["workflows"] == ["07_photo_bimbo", "07_photo_thepit", "08_photo_gta"],
              str(book.get("workflows")))
        check("кадр скопирован в папку", (folder / "07_photo_thepit_00099_.png").exists())
    finally:
        pp.ROOT, pp.OUTPUT_DIR = saved


def test_scene_cache_cyrillic():
    print("\n15) кэш не путает сцены на русском")
    img = TMP / "ru.png"
    make(img, (3, 4, 5))
    p0 = pp.cache_path(str(img))
    p1 = pp.cache_path(str(img), "на пляже")
    p2 = pp.cache_path(str(img), "в горах")
    check("три сцены — три файла", len({p0, p1, p2}) == 3, f"{p0.name} | {p1.name} | {p2.name}")
    check("кириллица не исчезает", p1.name != p0.name and "__s-" in p1.name, p1.name)

    pp.write_cache(str(img), {"subject": "в лесу", "negative_extra": ""}, "в лесу")
    check("чужой сценический кэш не отдаётся", pp.read_cache(str(img), "на пляже") is None)
    check("свой сценический кэш читается", pp.read_cache(str(img), "в лесу") is not None)

    # подменённое поле scene: имя файла совпало, описание чужое
    data = json.loads(pp.cache_path(str(img), "в лесу").read_text(encoding="utf-8"))
    data["scene"] = "в горах"
    pp.cache_path(str(img), "в лесу").write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    check("несовпадение scene в файле ловится", pp.read_cache(str(img), "в лесу") is None)

    _, calls = run_main(["--image", str(img), "--style", "thepit", "--scene", "на пляже"])
    check("новая сцена заставляет описать фото", any(c[0] == "scene" for c in calls), str(calls))
    check("кэш сцены записан", pp.read_cache(str(img), "на пляже") is not None)

    _, calls2 = run_main(["--image", str(img), "--style", "thepit", "--scene", "в горах"])
    check("другая сцена не берёт чужой кэш", any(c[0] == "scene" for c in calls2), str(calls2))

    _, calls3 = run_main(["--image", str(img), "--style", "thepit", "--scene", "на пляже"])
    check("повтор той же сцены — из кэша", calls3 == [], str(calls3))


def test_partial_failure_visible():
    print("\n16) пачка и паспорт не скрывают сбой")
    folder = TMP / "batch_part"
    for n in ("good.png", "bad.png"):
        make(folder / n, (8, 8, 8))
    saved = (pp.describe, pp.lm_models, pp.pick_model, pp.unload_all,
             pp.resolve_comfy, pp.generate)

    def fake_describe(path, model):
        if Path(path).name == "bad.png":
            return {"subject": "", "negative_extra": "", "notes": "модель молчит"}
        return {"subject": "adult woman, good", "negative_extra": "", "notes": ""}

    def good_generate(*a, **k):
        name = "07_photo_thepit_00002_.png"
        pp.OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
        (pp.OUTPUT_DIR / name).write_bytes(b"png")
        return True, name

    pp.describe = fake_describe
    pp.lm_models, pp.pick_model, pp.unload_all = lambda strict=True: {}, lambda r, w: "m", lambda r: []
    pp.resolve_comfy = lambda c: "http://x"
    try:
        pp.generate = good_generate
        rc, text = capture(lambda: pp.run_batch(batch_args(folder, force=True), "07_photo_thepit"))
        check("пустое описание -> код 1", rc == 1, str(rc))
        check("пропуск назван в итоге", "без описания: 1 (bad.png)" in text, text.strip()[-200:])
        check("кадр по второму фото сделан", "кадров: 1" in text, text.strip()[-200:])
        books = list((pp.ROOT / "out").rglob("run.json"))
        tag = pp.image_tag(str(folder / "good.png"))
        own = (pp.ROOT / "out" / tag / "run.json")
        check("run.json есть у описанного фото", own.exists(), str(books))
        check("run.json у пропущенного не появился",
              not (pp.ROOT / "out" / pp.image_tag(str(folder / "bad.png")) / "run.json").exists())

        # ComfyUI назвал файл, которого нет в папке вывода
        pp.generate = lambda *a, **k: (True, "ghost.png")
        rc, text = capture(lambda: pp.run_batch(batch_args(folder, force=True), "07_photo_thepit"))
        check("паспорт без кадра -> код 1", rc == 1, str(rc))
        check("сказано, что кадра нет", "кадра нет по пути" in text, text.strip()[-200:])
        runs = json.loads(own.read_text(encoding="utf-8"))["runs"]
        check("паспорт не вырос", len(runs) == 1, str(len(runs)))

        img = TMP / "ghost.png"
        make(img, (1, 1, 1))
        code = None
        try:
            with contextlib.redirect_stdout(io.StringIO()):
                run_main(["--image", str(img), "--style", "thepit", "--no-llm",
                          "--subject", "adult woman", "--generate"])
        except SystemExit as e:
            code = e.code
        check("одиночный путь при пропавшем кадре -> код 1", code == 1, str(code))
    finally:
        (pp.describe, pp.lm_models, pp.pick_model, pp.unload_all,
         pp.resolve_comfy, pp.generate) = saved


def test_check_install_covers_all_loaders():
    print("\n17) check_install видит любые загрузчики")
    wf = TMP / "wf_scan"
    wf.mkdir(exist_ok=True)
    (wf / "fake.json").write_text(json.dumps({"nodes": [
        {"type": "CheckpointLoaderSimple", "widgets_values": ["base.safetensors"]},
        {"type": "UnetLoaderGGUF", "widgets_values": ["qwen-q4.gguf"]},
        {"type": "IPAdapterModelLoader", "widgets_values": ["ip-adapter.safetensors"]},
    ]}), encoding="utf-8")
    found, broken = ci.workflow_models(wf)
    check("GGUF найден", any(n.endswith(".gguf") for n in found), str(sorted(found)))
    check("IP-Adapter найден", any("ip-adapter" in n for n in found), str(sorted(found)))
    check("граф прочитан без ошибок", not broken, str(broken))

    models = TMP / "scan_models"
    (models / "checkpoints").mkdir(parents=True, exist_ok=True)
    (models / "checkpoints" / "base.safetensors").write_bytes(b"x")
    docs = TMP / "scan_docs.md"
    docs.write_text("base.safetensors qwen-q4.gguf ip-adapter.safetensors", encoding="utf-8")

    def run_check(wf_dir=None):
        r = subprocess.run([sys.executable, str(Path(__file__).parent / "check_install.py"),
                            "--models", str(models), "--docs", str(docs),
                            "--workflows", str(wf_dir or wf)],
                           capture_output=True, text=True, encoding="utf-8", errors="replace")
        return r.returncode, (r.stdout or "") + (r.stderr or "")

    rc, out = run_check()
    check("не хватает GGUF и IP-Adapter -> код 1", rc == 1, out.strip()[-200:])
    check("обе пропажи названы",
          "qwen-q4.gguf" in out and "ip-adapter.safetensors" in out, out.strip()[-300:])

    (models / "diffusion_models").mkdir(exist_ok=True)
    (models / "diffusion_models" / "qwen-q4.gguf").write_bytes(b"x")
    (models / "ipadapter").mkdir(exist_ok=True)
    (models / "ipadapter" / "ip-adapter.safetensors").write_bytes(b"x")
    rc, out = run_check()
    check("всё на месте -> код 0", rc == 0, out.strip()[-200:])

    rc, out = run_check(TMP / "нет-такой-папки")
    check("нет папки графов -> код 1", rc == 1, out.strip()[-200:])


def test_check_install_strictness():
    print("\n20) check_install: раздел модели и битый граф")
    wf = TMP / "wf_strict"
    wf.mkdir(exist_ok=True)
    (wf / "good.json").write_text(json.dumps({"nodes": [
        {"type": "CheckpointLoaderSimple", "widgets_values": ["base.safetensors"]}]}),
        encoding="utf-8")
    models = TMP / "strict_models"
    (models / "loras").mkdir(parents=True, exist_ok=True)
    (models / "loras" / "base.safetensors").write_bytes(b"x")
    docs = TMP / "strict_docs.md"
    docs.write_text("base.safetensors", encoding="utf-8")

    def run_check():
        r = subprocess.run([sys.executable, str(Path(__file__).parent / "check_install.py"),
                            "--models", str(models), "--docs", str(docs), "--workflows", str(wf)],
                           capture_output=True, text=True, encoding="utf-8", errors="replace")
        return r.returncode, (r.stdout or "") + (r.stderr or "")

    rc, out = run_check()
    check("чекпойнт в loras -> код 1", rc == 1, out.strip()[-200:])
    check("сказано, что не в том разделе", "не в том разделе" in out, out.strip()[-300:])

    (models / "checkpoints").mkdir(exist_ok=True)
    (models / "loras" / "base.safetensors").rename(models / "checkpoints" / "base.safetensors")
    rc, out = run_check()
    check("на своём месте -> код 0", rc == 0, out.strip()[-200:])

    (wf / "broken.json").write_text("{ это не json", encoding="utf-8")
    rc, out = run_check()
    check("битый граф -> код 1", rc == 1, out.strip()[-200:])
    check("битый граф назван", "БИТЫЙ ГРАФ" in out and "broken.json" in out, out.strip()[-300:])

    (wf / "broken.json").unlink()
    (wf / "unknown.json").write_text(json.dumps({"nodes": [
        {"type": "SomeNewLoader", "widgets_values": ["new.safetensors"]}]}), encoding="utf-8")
    rc, out = run_check()
    check("незнакомый загрузчик -> код 1", rc == 1, out.strip()[-200:])
    check("подсказано, что делать", "добавь его в LOADERS" in out, out.strip()[-300:])


def test_scene_failure_not_cached():
    print("\n19) сбой сцены не закрепляется в кэше")
    img = TMP / "scene_fail.png"
    make(img, (2, 3, 4))
    code = None
    try:
        with contextlib.redirect_stdout(io.StringIO()):
            run_main(["--image", str(img), "--style", "thepit", "--scene", "в горах"],
                     scene_fails=True)
    except SystemExit as e:
        code = e.code
    check("неудачная сцена -> код 1", code == 1, str(code))
    check("кэша под сценой нет", pp.read_cache(str(img), "в горах") is None)
    check("описание фото сохранено без сцены", pp.read_cache(str(img)) is not None)

    _, calls = run_main(["--image", str(img), "--style", "thepit", "--scene", "в горах"])
    check("повтор снова пишет сцену, а не берёт чужой кэш",
          any(c[0] == "scene" for c in calls), str(calls))
    got = pp.read_cache(str(img), "в горах")
    check("после успеха кэш сцены есть", got is not None)
    check("в нём та самая сцена", "в горах" in (got or {}).get("subject", ""), str(got)[:120])

    # пачка: файл со сбойной сценой пропускается и даёт код 1
    folder = TMP / "batch_scene"
    make(folder / "a.png", (4, 5, 6))
    saved = (pp.describe, pp.rewrite_scene, pp.lm_models, pp.pick_model,
             pp.unload_all, pp.resolve_comfy)
    pp.describe = lambda p, m: {"subject": "adult woman, tree", "negative_extra": "", "notes": ""}
    pp.rewrite_scene = lambda d, s, m: None
    pp.lm_models, pp.pick_model, pp.unload_all = lambda strict=True: {}, lambda r, w: "m", lambda r: []
    pp.resolve_comfy = lambda c: "http://x"
    try:
        rc, text = capture(lambda: pp.run_batch(
            batch_args(folder, scene="в горах", force=True), "07_photo_thepit"))
        check("пачка со сбойной сценой -> код 1", rc == 1, str(rc))
        check("файл назван пропущенным", "без описания: 1 (a.png)" in text, text.strip()[-200:])
        check("пачка не пишет кэш под сценой",
              pp.read_cache(str(folder / "a.png"), "в горах") is None)
        check("пачка сохранила базовое описание",
              pp.read_cache(str(folder / "a.png")) is not None)
    finally:
        (pp.describe, pp.rewrite_scene, pp.lm_models, pp.pick_model,
         pp.unload_all, pp.resolve_comfy) = saved


def test_comfy_url_setting():
    print("\n18) COMFY_URL учитывается при генерации")
    saved_probe = pp.comfy_probe
    saved_env = os.environ.get("COMFY_URL")
    probed = []
    try:
        pp.comfy_probe = lambda url: (probed.append(url), url.startswith("http://remote:9999"))[1]
        os.environ["COMFY_URL"] = "http://remote:9999"
        check("адрес из env используется", pp.resolve_comfy(None) == "http://remote:9999", str(probed))

        probed.clear()
        pp.comfy_probe = lambda url: (probed.append(url), False)[1]
        code = None
        try:
            pp.resolve_comfy(None)
        except SystemExit as e:
            code = e.code
        check("недоступный заданный адрес -> отказ", code not in (0, None), str(code))
        check("локальные порты не подставляются",
              all("127.0.0.1" not in u for u in probed), str(probed))

        os.environ.pop("COMFY_URL", None)
        probed.clear()
        pp.comfy_probe = lambda url: (probed.append(url), url.endswith(":8199"))[1]
        check("без env — свой стенд", pp.resolve_comfy(None).endswith(":8199"), str(probed))
    finally:
        pp.comfy_probe = saved_probe
        if saved_env is None:
            os.environ.pop("COMFY_URL", None)
        else:
            os.environ["COMFY_URL"] = saved_env


def test_batch_scene_cache_no_lm():
    print("\n21) --batch --scene с готовой сценой не зовёт модель")
    folder = TMP / "batch_scene_cache"
    make(folder / "a.png", (6, 6, 6))
    img = folder / "a.png"
    calls = []
    saved = (pp.describe, pp.rewrite_scene, pp.lm_models, pp.pick_model,
             pp.unload_all, pp.unload_model, pp.resolve_comfy)
    pp.describe = lambda p, m: (calls.append("describe") or
                                {"subject": "adult woman, tree", "negative_extra": "",
                                 "notes": ""})
    pp.rewrite_scene = lambda d, s, m: (calls.append("rewrite") or
                                        {"subject": "adult woman, tree, " + s,
                                         "negative_extra": "", "notes": "", "scene": s})
    pp.lm_models = lambda strict=True: (calls.append("lm_models") or {"models": []})
    pp.pick_model = lambda r, w: (calls.append("pick_model") or "m")
    pp.unload_all = lambda r: (calls.append("unload_all") or [])
    pp.unload_model = lambda r, m: (calls.append("unload_model") or [])
    pp.resolve_comfy = lambda c: "http://x"
    try:
        pp.write_cache(str(img), {"subject": "adult woman, tree, в горах", "negative_extra": "",
                                  "notes": "", "scene": "в горах"}, "в горах")
        rc, text = capture(lambda: pp.run_batch(batch_args(folder, scene="в горах", generate=False),
                                                "07_photo_thepit"))
        check("кэш сцены: код 0", rc == 0, str(rc))
        check("кэш сцены: модель не тронута", calls == [], str(calls))

        calls.clear()
        rc, text = capture(lambda: pp.run_batch(batch_args(folder, scene="в горах", dry_run=True, generate=False),
                                                "07_photo_thepit"))
        check("dry-run с кэшем сцены: код 0 и без модели", rc == 0 and calls == [],
              f"{rc} {calls}")

        # только базовое описание: фото не пересматриваем, переписываем сцену
        pp.cache_path(str(img), "в горах").unlink()
        pp.write_cache(str(img), {"subject": "adult woman, tree", "negative_extra": "",
                                  "notes": ""})
        calls.clear()
        rc, text = capture(lambda: pp.run_batch(batch_args(folder, scene="в горах", generate=False),
                                                "07_photo_thepit"))
        check("базовый кэш: фото заново не описывают", "describe" not in calls, str(calls))
        check("базовый кэш: сцена переписана", "rewrite" in calls, str(calls))
        check("базовый кэш: сцена закэширована",
              pp.read_cache(str(img), "в горах") is not None)

        # запись старой версии без метки scene_applied
        p = pp.cache_path(str(img), "в горах")
        data = json.loads(p.read_text(encoding="utf-8"))
        data.pop("scene_applied", None)
        p.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        check("старая запись без метки не читается", pp.read_cache(str(img), "в горах") is None)

        calls.clear()
        capture(lambda: pp.run_batch(batch_args(folder, scene="в горах", generate=False), "07_photo_thepit"))
        check("старую запись переписывают", "rewrite" in calls, str(calls))
        check("после перезаписи метка есть",
              bool((pp.read_cache(str(img), "в горах") or {}).get("scene_applied")))

        # одиночный запуск: старая запись тоже не считается готовой сценой
        img2 = TMP / "old_scene.png"
        make(img2, (7, 8, 9))
        p2 = pp.cache_path(str(img2), "в горах")
        p2.parent.mkdir(parents=True, exist_ok=True)
        p2.write_text(json.dumps({"subject": "adult woman, christmas tree",
                                  "negative_extra": "", "notes": "", "scene": "в горах",
                                  "sha1": pp.fingerprint(str(img2)), "source": str(img2)},
                                 ensure_ascii=False), encoding="utf-8")
        _, calls2 = run_main(["--image", str(img2), "--style", "thepit", "--scene", "в горах"])
        check("одиночный путь: старая запись не берётся",
              any(c[0] == "scene" for c in calls2), str(calls2))
        check("одиночный путь: кэш переписан с меткой",
              bool((pp.read_cache(str(img2), "в горах") or {}).get("scene_applied")))
    finally:
        (pp.describe, pp.rewrite_scene, pp.lm_models, pp.pick_model,
         pp.unload_all, pp.unload_model, pp.resolve_comfy) = saved


def test_controlnet_strength_flag():
    print("\n22) --cn-strength перебивает силу ControlNet")
    def payload_for(**kw):
        sent = {}
        saved = (pp.post, pp.get, pp.time)
        pp.post = lambda url, payload, timeout=300: (sent.update(payload["prompt"]) or
                                                     {"prompt_id": "p"})
        pp.get = lambda url, timeout=30: {"p": {"status": {"status_str": "success"},
                                                "outputs": {"9": {"images": [
                                                    {"filename": "x.png"}]}}}}
        pp.time = _Clock()
        try:
            with contextlib.redirect_stdout(io.StringIO()):
                pp.generate("07_photo_thepit", "adult woman", "", seed=1, **kw)
        finally:
            pp.post, pp.get, pp.time = saved
        return [n["inputs"]["strength"] for n in sent.values()
                if n["class_type"] == "ControlNetApplyAdvanced"]

    check("без флага — как в сборке", payload_for() == [0.6], str(payload_for()))
    check("с флагом — новое значение", payload_for(cn_strength=0.25) == [0.25],
          str(payload_for(cn_strength=0.25)))

    got = {}
    saved_gen, saved_resolve = pp.generate, pp.resolve_comfy
    pp.generate = lambda *a, **k: (got.update(k) or (False, None))
    pp.resolve_comfy = lambda c=None: "http://stub"
    img = TMP / "cn.png"
    make(img, (6, 7, 8))
    try:
        try:
            with contextlib.redirect_stdout(io.StringIO()):
                run_main(["--image", str(img), "--style", "thepit", "--no-llm",
                          "--subject", "adult woman", "--cn-strength", "0.3", "--generate"])
        except SystemExit:
            pass
    finally:
        pp.generate, pp.resolve_comfy = saved_gen, saved_resolve
    check("main передаёт силу ControlNet", got.get("cn_strength") == 0.3, str(got))


def test_passport_records_generation_settings():
    print("\n23) run.json помнит силу ControlNet и вес стиля")
    img = TMP / "settings.png"
    make(img, (9, 9, 9))
    saved = (pp.ROOT, pp.OUTPUT_DIR)
    pp.ROOT = TMP / "root_settings"
    pp.OUTPUT_DIR = TMP / "out_settings"
    pp.OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    try:
        for name, cn in (("07_photo_thepit_00001_.png", 0.0),
                         ("07_photo_thepit_00002_.png", 0.25)):
            (pp.OUTPUT_DIR / name).write_bytes(b"png")
            with contextlib.redirect_stdout(io.StringIO()):
                pp.save_run(str(img), "07_photo_thepit",
                            {"subject": "x", "negative_extra": ""}, 42, 0.75, name,
                            cn_strength=cn, style_weight=0.9)
        folder = pp.ROOT / "out" / pp.image_tag(img)
        book = json.loads((folder / "run.json").read_text(encoding="utf-8"))
        check("сила ControlNet в паспорте",
              [r["cn_strength"] for r in book["runs"]] == [0.0, 0.25],
              str([r.get("cn_strength") for r in book["runs"]]))
        check("вес стиля в паспорте", all(r["style_weight"] == 0.9 for r in book["runs"]))
        check("два кадра различаются настройками, а не только файлом",
              book["runs"][0] != book["runs"][1], "записи совпали")
        check("seed и denoise по-прежнему пишутся",
              all(r["seed"] == 42 and r["denoise"] == 0.75 for r in book["runs"]))

        seen = {}
        saved_save, saved_gen, saved_resolve = pp.save_run, pp.generate, pp.resolve_comfy
        pp.save_run = lambda *a, **k: (seen.update(k) or None)
        pp.generate = lambda *a, **k: (True, "f.png")
        pp.resolve_comfy = lambda c=None: "http://stub"
        img2 = TMP / "settings2.png"
        make(img2, (4, 4, 4))
        try:
            try:
                with contextlib.redirect_stdout(io.StringIO()):
                    run_main(["--image", str(img2), "--style", "thepit", "--no-llm",
                              "--subject", "adult woman", "--cn-strength", "0.3",
                              "--style-weight", "0.7", "--generate"])
            except SystemExit:
                pass
        finally:
            pp.save_run, pp.generate, pp.resolve_comfy = saved_save, saved_gen, saved_resolve
        check("main передаёт настройки в паспорт",
              seen.get("cn_strength") == 0.3 and seen.get("style_weight") == 0.7, str(seen))
    finally:
        pp.ROOT, pp.OUTPUT_DIR = saved


def test_passport_records_sampler_settings():
    print("\n24) run.json: применённые шаги, cfg и сэмплер")
    img = TMP / "sampler.png"
    make(img, (14, 15, 16))
    payload = pp.build_payload("07_photo_thepit", "adult woman", "", seed=1)
    settings = payload.get("settings") or {}
    check("build_payload отдаёт настройки сэмплера",
          settings.get("steps") == 30 and settings.get("cfg") == 6.0
          and settings.get("sampler") == "euler_ancestral"
          and settings.get("scheduler") == "normal", str(settings))

    saved = (pp.ROOT, pp.OUTPUT_DIR)
    pp.ROOT = TMP / "root_sampler"
    pp.OUTPUT_DIR = TMP / "out_sampler"
    pp.OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    try:
        frame = "07_photo_thepit_00011_.png"
        (pp.OUTPUT_DIR / frame).write_bytes(b"png")
        with contextlib.redirect_stdout(io.StringIO()):
            pp.save_run(str(img), "07_photo_thepit",
                        {"subject": "x", "negative_extra": ""}, 1, 0.75, frame,
                        settings=settings)
        folder = pp.ROOT / "out" / pp.image_tag(img)
        run = json.loads((folder / "run.json").read_text(encoding="utf-8"))["runs"][0]
        check("steps в паспорте", run.get("steps") == 30, str(run.get("steps")))
        check("cfg в паспорте", run.get("cfg") == 6.0, str(run.get("cfg")))
        check("сэмплер в паспорте", run.get("sampler") == "euler_ancestral",
              str(run.get("sampler")))
        check("scheduler в паспорте", run.get("scheduler") == "normal",
              str(run.get("scheduler")))

        # Без настроек паспорт не выдумывает нули: denoise: null остаётся null,
        # а шаги — пустыми, а не 0 (0 шагов выглядит как настоящее значение).
        frame2 = "07_photo_thepit_00012_.png"
        (pp.OUTPUT_DIR / frame2).write_bytes(b"png")
        with contextlib.redirect_stdout(io.StringIO()):
            pp.save_run(str(img), "07_photo_thepit",
                        {"subject": "x", "negative_extra": ""}, 2, None, frame2)
        runs = json.loads((folder / "run.json").read_text(encoding="utf-8"))["runs"]
        check("denoise без настройки остался null", runs[1].get("denoise") is None,
              str(runs[1].get("denoise")))
        check("без настроек шаги не выдуманы",
              runs[1].get("steps") is None and runs[1].get("cfg") is None,
              str({k: runs[1].get(k) for k in ("steps", "cfg", "sampler")}))
        check("запись с настройками читается вместе с новой",
              runs[0]["steps"] == 30 and runs[0]["denoise"] == 0.75, str(runs[0])[:160])
    finally:
        pp.ROOT, pp.OUTPUT_DIR = saved


def test_atomic_json():
    print("\n25) JSON пишется целиком или не пишется вовсе")
    img = TMP / "root" / "atomic.png"
    make(img, (12, 34, 56))
    cache = pp.write_cache(str(img), {"subject": "adult woman, one", "negative_extra": ""})
    saved_fsync = os.fsync
    pp.os.fsync = lambda fd: (_ for _ in ()).throw(OSError("диск отвалился"))
    try:
        try:
            pp.write_cache(str(img), {"subject": "adult woman, two"})
            check("обрыв записи кэша виден", False)
        except OSError:
            check("обрыв записи кэша виден", True)
    finally:
        pp.os.fsync = saved_fsync
    check("прежний кэш читается",
          json.loads(cache.read_text(encoding="utf-8"))["subject"].endswith("one"))
    check("временных файлов не осталось", not list(cache.parent.glob("*.tmp")),
          str(list(cache.parent.glob("*.tmp"))))

    saved_roots = (pp.ROOT, pp.OUTPUT_DIR)
    pp.ROOT, pp.OUTPUT_DIR = TMP / "root_atomic", TMP / "out_atomic"
    pp.OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    try:
        frame = "07_photo_thepit_00042_.png"
        (pp.OUTPUT_DIR / frame).write_bytes(b"png")
        with contextlib.redirect_stdout(io.StringIO()):
            pp.save_run(str(img), "07_photo_thepit",
                        {"subject": "adult woman", "negative_extra": ""}, 1, None, frame)
        book = pp.ROOT / "out" / pp.image_tag(str(img)) / "run.json"
        runs_before = len(json.loads(book.read_text(encoding="utf-8"))["runs"])
        pp.os.fsync = lambda fd: (_ for _ in ()).throw(OSError("диск отвалился"))
        try:
            with contextlib.redirect_stdout(io.StringIO()):
                try:
                    pp.save_run(str(img), "07_photo_thepit",
                                {"subject": "adult woman", "negative_extra": ""}, 2, None, frame)
                    check("обрыв записи паспорта виден", False)
                except OSError:
                    check("обрыв записи паспорта виден", True)
        finally:
            pp.os.fsync = saved_fsync
        check("прежний паспорт читается",
              len(json.loads(book.read_text(encoding="utf-8"))["runs"]) == runs_before)
        check("паспорт не оставил .tmp", not list(book.parent.glob("*.tmp")))

        good = jobs.run_job(jobs.JobRequest(
            image=str(img), workflow="07_photo_thepit", subject="adult woman",
            generate=False, save_preset="atomic"))
        check("пресет записан", good.ok and pp.preset_path("atomic").exists(), str(good.error))
        pp.os.fsync = lambda fd: (_ for _ in ()).throw(OSError("диск отвалился"))
        try:
            broken = jobs.run_job(jobs.JobRequest(
                image=str(img), workflow="07_photo_thepit", subject="adult woman",
                generate=False, save_preset="atomic"))
            check("обрыв записи пресета виден",
                  not broken.ok and "диск отвалился" in broken.error, str(broken.error))
        finally:
            pp.os.fsync = saved_fsync
        preset = pp.preset_path("atomic")
        check("прежний пресет читается",
              json.loads(preset.read_text(encoding="utf-8"))["subject"] == "adult woman")
        check("пресет не оставил .tmp", not list(preset.parent.glob("*.tmp")))
    finally:
        pp.ROOT, pp.OUTPUT_DIR = saved_roots

    target = TMP / "atomic_race.json"
    bad, stop = [], threading.Event()

    def reader():
        while not stop.is_set():
            try:
                pp.read_json(target)
            except FileNotFoundError:
                pass
            except Exception as e:
                bad.append(repr(e))
                return

    pp.write_json(target, {"runs": []})
    t = threading.Thread(target=reader, daemon=True)
    t.start()
    try:
        for n in range(30):
            pp.write_json(target, {"runs": [{"seed": n}] * 50})
    finally:
        stop.set()
        t.join(timeout=5)
    check("чтение во время записи не видит половину", not bad, str(bad[:1]))


def test_utf8_streams():
    print("\n26) кириллица в пайп без PYTHONIOENCODING")

    class Fake:
        def __init__(self):
            self.encoding = "cp1252"
            self.got = None

        def reconfigure(self, **kw):
            self.got = kw
            self.encoding = kw.get("encoding", self.encoding)

    fake = Fake()
    st.utf8_streams([fake])
    check("поток переключён на utf-8",
          fake.got == {"encoding": "utf-8", "errors": "replace"}, str(fake.got))
    once = dict(fake.got)
    st.utf8_streams([fake])
    check("повторный вызов ничего не меняет", fake.got == once, str(fake.got))

    script = Path(__file__).with_name("check_install.py")
    env = {k: v for k, v in os.environ.items()
           if k not in ("PYTHONIOENCODING", "PYTHONUTF8")}
    proc = subprocess.run([sys.executable, str(script)], cwd=str(pp.ROOT.parent),
                          env=env, capture_output=True)
    text = proc.stdout.decode("utf-8")            # строго: не latin-1 и не cp1252
    err = proc.stderr.decode("utf-8")
    check("вывод в пайп — UTF-8 с кириллицей", "модели:" in text, text[:120])
    check("трейсбека кодировки нет",
          "UnicodeEncodeError" not in err and "Traceback" not in err, err[-200:])


def test_passport_not_lost():
    print("\n27) паспорт не перезаписывается, если не читается")
    img = TMP / "root" / "notlost.png"
    make(img, (7, 8, 9))
    saved_roots = (pp.ROOT, pp.OUTPUT_DIR)
    pp.ROOT, pp.OUTPUT_DIR = TMP / "root_notlost", TMP / "out_notlost"
    pp.OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    try:
        frame = "07_photo_thepit_00077_.png"
        (pp.OUTPUT_DIR / frame).write_bytes(b"png")
        with contextlib.redirect_stdout(io.StringIO()):
            pp.save_run(str(img), "07_photo_thepit",
                        {"subject": "adult woman", "negative_extra": ""}, 1, None, frame)
            pp.save_run(str(img), "07_photo_thepit",
                        {"subject": "adult woman", "negative_extra": ""}, 2, None, frame)
        book = pp.ROOT / "out" / pp.image_tag(str(img)) / "run.json"
        before = book.read_bytes()
        saved_read = pp.read_json
        pp.read_json = lambda *a, **k: (_ for _ in ()).throw(PermissionError("занят"))
        try:
            with contextlib.redirect_stdout(io.StringIO()):
                try:
                    pp.save_run(str(img), "07_photo_thepit",
                                {"subject": "adult woman", "negative_extra": ""},
                                2, None, frame)
                    check("невозможность прочитать паспорт видна", False)
                except pp.PipelineError as e:
                    check("невозможность прочитать паспорт видна",
                          "паспорт" in str(e) and "занят" in str(e), str(e))
        finally:
            pp.read_json = saved_read
        check("старый паспорт цел побайтно", book.read_bytes() == before)
        check("прежняя история на месте",
              len(json.loads(book.read_text(encoding="utf-8"))["runs"]) == 2)
    finally:
        pp.ROOT, pp.OUTPUT_DIR = saved_roots


def test_style_prompts_consistent():
    print("\n28) промпты сборок не спорят сами с собой")

    def texts(name):
        data = json.loads((pp.WF_DIR / (name + ".api.json")).read_text(encoding="utf-8"))
        return [n["inputs"]["text"] for n in data["prompt"].values()
                if n["class_type"] == "CLIPTextEncode"]

    two = texts("02_edit_mask")
    pos = next(t for t in two if "leather jacket" in t)
    neg = next(t for t in two if t.startswith("photo, photorealistic"))
    check("02: позитив не просит полутонов", "halftone" not in pos, pos)
    check("02: негатив по-прежнему их запрещает", "halftone dots" in neg, neg[:140])

    five = texts("05_style_return")
    five_pos = next(t for t in five if t.startswith("comic book illustration"))
    for tag in ("short hair", "leather jacket", "city street"):
        check(f"05: пример не задаёт «{tag}»", tag not in five_pos, five_pos[:140])
    check("05: блок стиля на месте",
          "theofficialpit style" in five_pos and "flat colors" in five_pos, five_pos[:140])

    ui = json.loads((pp.WF_DIR / "07_photo_thepit.json").read_text(encoding="utf-8"))
    descs = [str(i[2].get("description", ""))
             for i in ui["extra"]["linearData"]["inputs"]]
    check("07: в поле глубины есть рецепт смены сцены",
          any("0.25" in d and "сменить место" in d for d in descs), str(descs)[:240])
    check("07: в поле denoise есть рецепт смены сцены",
          any("0.85–0.9" in d and "смена места" in d for d in descs), str(descs)[:240])


def test_default_model():
    print("\n29) модель описания по умолчанию закрепляется локально")
    resp = {"models": [
        {"key": "gemma@q4", "capabilities": {"vision": True}},
        {"key": "qwen3.8-x@iq3_m", "capabilities": {"vision": True}},
        {"key": "text-only", "capabilities": {}},
    ]}
    saved = st.LM_MODEL
    try:
        st.LM_MODEL = "qwen3.8-x@iq3_m"
        check("закреплённая модель важнее порядка списка",
              pp.pick_model(resp, None) == "qwen3.8-x@iq3_m", pp.pick_model(resp, None))
        st.LM_MODEL = "нет-на-диске@iq4"
        check("недоступная закреплённая — откат к vision",
              pp.pick_model(resp, None) == "qwen3.8-x@iq3_m", pp.pick_model(resp, None))
        st.LM_MODEL = "qwen3.8-x@iq3_m"
        check("явный выбор сильнее закрепления",
              pp.pick_model(resp, "gemma@q4") == "gemma@q4")
    finally:
        st.LM_MODEL = saved


def test_tagger_addon():
    """30) теггер дописывает детали, не дублирует и умеет отключаться."""
    print("\n30) теггер дописывает детали в subject")
    img = TMP / "tagger" / "photo.png"
    make(img, (12, 34, 56))
    real_tags = pp.tagger.tags
    calls = []

    def fake_tags(image_path, warn=True):
        calls.append(str(image_path))
        return {"tags": ["large breasts", "thighhighs", "jewelry", "cosplay"],
                "size": "large breasts", "version": pp.tagger.VERSION}

    try:
        pp.tagger.tags = fake_tags
        desc = {"subject": "adult woman, red dress, sitting on a bed"}
        subject = pp.enrich_subject(desc, str(img))
        check("теги встали после персоны",
              subject.startswith("adult woman, large breasts, thighhighs, jewelry"), subject)
        check("мусор cosplay отброшен", "cosplay" not in subject, subject)
        check("в addon_tags только добавленное",
              desc.get("addon_tags") == ["large breasts", "thighhighs", "jewelry"],
              str(desc.get("addon_tags")))
        check("метка версии записана", desc.get("addons") == pp.tagger.VERSION,
              str(desc.get("addons")))

        before = len(calls)
        again = pp.enrich_subject(desc, str(img))
        check("повтор не зовёт теггер", len(calls) == before, f"вызовов {len(calls) - before}")
        check("повтор не меняет subject", again == subject, again)

        desc2 = {"subject": "adult woman, large breasts with dark areolas, black lace top"}
        subject2 = pp.enrich_subject(desc2, str(img))
        check("размер не продублирован, когда VLM его уже назвал",
              subject2.count("breasts") == 1, subject2)

        stripped = pp.apply_tags(desc, str(img), no_tags=True)
        check("--no-tags снимает ровно свои теги",
              "thighhighs" not in stripped and "jewelry" not in stripped
              and stripped.startswith("adult woman, red dress"), stripped)

        before = len(calls)
        pp.apply_tags({"subject": "adult woman, plain dress"}, str(img), dry_run=True)
        check("--dry-run не зовёт теггер", len(calls) == before, f"вызовов {len(calls) - before}")

        pp.tagger.tags = lambda image_path, warn=True: {
            "tags": [], "size": "", "version": pp.tagger.VERSION, "skipped": "нет моделей"}
        desc4 = {"subject": "adult woman, plain dress"}
        subject4 = pp.enrich_subject(desc4, str(img))
        check("без модели subject не тронут", subject4 == "adult woman, plain dress", subject4)
        check("без модели метка не ставится", not desc4.get("addons"), str(desc4.get("addons")))

        pp.tagger.tags = real_tags
        os.environ["COMIC_TAGGER"] = "off"
        empty = real_tags(str(img))
        check("COMIC_TAGGER=off не грузит модель",
              empty["tags"] == [] and bool(empty.get("skipped")), str(empty))
        check("половой тег на низкой уверенности не проходит",
              not pp.tagger.confident("female pubic hair", 0.3)
              and pp.tagger.confident("female pubic hair", 0.9))
        check("обычный тег проходит на пороге 0.25",
              pp.tagger.confident("thighhighs", 0.25))
        check("glasses — аксессуар, а не анатомия из-за 'ass'",
              pp.tagger.bucket_of("glasses") == "аксессуар", pp.tagger.bucket_of("glasses"))
        check("bathrobe — одежда, а не прочее",
              pp.tagger.bucket_of("bathrobe") == "одежда/материал", pp.tagger.bucket_of("bathrobe"))
        check("общий тег уступает подробному",
              pp.tagger.drop_redundant(["breasts", "large breasts"]) == ["large breasts"],
              str(pp.tagger.drop_redundant(["breasts", "large breasts"])))
        check("халат не превращается в платье",
              "white dress" not in pp.tagger.add_to_subject(
                  "adult woman, satin robe, bare legs", ["white dress"]),
              pp.tagger.add_to_subject("adult woman, satin robe, bare legs", ["white dress"]))
        check("каблук не дублируется синонимом",
              "high heels" not in pp.tagger.add_to_subject(
                  "adult woman, red dress, strappy heels", ["high heels"]),
              pp.tagger.add_to_subject("adult woman, red dress, strappy heels", ["high heels"]))
        check("чулки к платью добавляются — другой слот",
              "thighhighs" in pp.tagger.add_to_subject(
                  "adult woman, red dress", ["thighhighs"]))
        check("skin, shoulder, hip не проходят как пустые места",
              not any(pp.tagger.keep(t) for t in ("skin", "shoulder", "hip", "arm")))
        check("bare shoulders и wide hips проходят",
              pp.tagger.keep("bare shoulders") and pp.tagger.keep("wide hips"))
        check("thong — одежда, а не «прочее»",
              pp.tagger.bucket_of("thong") == "одежда/материал",
              pp.tagger.bucket_of("thong"))
        sized = pp.tagger.add_to_subject("adult woman, large breasts, red dress",
                                         ["huge breasts"])
        check("размер заменяет прежний, а не встаёт рядом",
              sized.count("breasts") == 1 and "huge breasts" in sized, sized)
        removed = []
        pp.tagger.add_to_subject("adult woman, large breasts, red dress",
                                 ["huge breasts"], removed)
        back = pp.tagger.strip_from_subject("adult woman, huge breasts, red dress",
                                            ["huge breasts"], removed)
        check("--no-tags возвращает вытесненный размер",
              "large breasts" in back and "huge" not in back, back)
        desc5 = {"subject": "adult woman, long dark hair",
                 "negative_extra": "dark hair, missing glasses"}
        pp.clean_negative(desc5)
        check("негатив без самопротиворечий",
              desc5["negative_extra"] == "missing glasses", desc5["negative_extra"])
    finally:
        pp.tagger.tags = real_tags
        os.environ["COMIC_TAGGER"] = "off"


def test_tagger_does_not_pollute_cache():
    """31) ручное описание не оседает в кэше фото из-за добавленных тегов."""
    print("\n31) теги не затаскивают ручное описание в кэш")
    img = TMP / "manual" / "photo.png"
    make(img, (40, 50, 60))
    saved = pp.tagger.tags
    pp.tagger.tags = lambda image_path, warn=True: {
        "tags": ["thighhighs"], "size": "", "version": pp.tagger.VERSION}
    cache = pp.cache_path(str(img))
    try:
        cache.unlink(missing_ok=True)
        req = jobs.JobRequest(image=str(img), workflow="07_photo_thepit",
                              subject="adult woman, red dress", generate=False,
                              write_cache=True)
        with contextlib.redirect_stdout(io.StringIO()):
            res = jobs.run_job(req)
        check("ручное описание: кэш не создан", not cache.exists(), str(cache))
        check("ручное описание: теги в кадре есть",
              "thighhighs" in (res.description or {}).get("subject", ""),
              str((res.description or {}).get("subject"))[:120])
    finally:
        pp.tagger.tags = saved
        cache.unlink(missing_ok=True)


def test_openai_provider():
    """32) обычный OpenAI-сервис: /v1/models, без выгрузки, размышления по флагу."""
    print("\n32) сервис описаний без LM Studio")
    saved = (pp.get, pp.post, os.environ.get("LM_THINKING"))

    def fake_get(url, timeout=30):
        if url.endswith("/api/v1/models"):
            raise OSError("HTTP Error 404: Not Found")
        return {"object": "list", "data": [{"id": "huihui@q4"}, {"id": "other"}]}

    try:
        pp.get = fake_get
        resp = pp.lm_models()
        check("модель берётся из /v1/models", pp.pick_model(resp, None) == "huihui@q4",
              str(resp)[:160])
        check("явная модель сильнее списка", pp.pick_model(resp, "given") == "given")
        check("провайдер помечен openai", resp.get("openai") is True, str(resp.get("openai")))

        posted = []
        pp.post = lambda url, payload, timeout=300: (posted.append(url) or {})
        check("выгрузка не зовётся и не падает",
              pp.free_llm("huihui@q4") == [] and not posted, str(posted))

        pp.get = lambda url, timeout=30: (_ for _ in ()).throw(OSError("нет связи"))
        check("лежащий сервис: выгрузка молчит", pp.free_llm("m") == [])

        img = TMP / "provider" / "p.png"
        make(img, (9, 9, 9))
        seen = {}

        def fake_post(url, payload, timeout=300):
            seen.clear()
            seen.update(payload)
            return {"choices": [{"message": {"content":
                    '{"subject":"adult woman, red dress","negative_extra":"","notes":""}'}}]}

        pp.post = fake_post
        os.environ["LM_THINKING"] = "off"
        pp.describe(str(img), "huihui@q4")
        check("thinking выключен в запросе",
              seen.get("chat_template_kwargs") == {"enable_thinking": False},
              str(seen.get("chat_template_kwargs")))
        text = seen["messages"][1]["content"][0]["text"]
        check("/no_think в тексте запроса", "/no_think" in text, text[:70])

        os.environ.pop("LM_THINKING", None)
        seen.clear()
        pp.describe(str(img), "huihui@q4")
        check("по умолчанию размышления выключены",
              seen.get("chat_template_kwargs") == {"enable_thinking": False},
              str(seen.get("chat_template_kwargs")))

        os.environ["LM_THINKING"] = "on"
        seen.clear()
        pp.describe(str(img), "huihui@q4")
        check("LM_THINKING=on возвращает размышления",
              "chat_template_kwargs" not in seen, str(list(seen)))
        check("с размышлениями /no_think не добавляется",
              "/no_think" not in seen["messages"][1]["content"][0]["text"])

        attempts = []

        def picky_post(url, payload, timeout=300):
            attempts.append("chat_template_kwargs" in payload)
            if "chat_template_kwargs" in payload:
                raise RuntimeError("HTTP 400: unknown field chat_template_kwargs")
            return {"choices": [{"message": {"content":
                    '{"subject":"adult woman, red dress","negative_extra":"","notes":""}'}}]}

        pp.post = picky_post
        pp._THINK_FIELD = None
        os.environ.pop("LM_THINKING", None)
        pp.describe(str(img), "m")
        check("400 на незнакомое поле: повтор без него", attempts == [True, False], str(attempts))
        check("поле запомнено как ненужное", pp._THINK_FIELD is False, str(pp._THINK_FIELD))
    finally:
        pp.get, pp.post = saved[0], saved[1]
        pp._THINK_FIELD = None
        if saved[2] is None:
            os.environ.pop("LM_THINKING", None)
        else:
            os.environ["LM_THINKING"] = saved[2]


def test_eye_colour_zoom():
    """33) цвет глаз: промпт требует его, запрос несёт зум на лицо, старый кэш не живёт."""
    print("\n33) цвет глаз в описании")
    check("SYSTEM требует цвет глаз", "eye colour" in pp.SYSTEM, pp.SYSTEM[:120])
    check("SYSTEM запрещает серые и ореховые глаза",
          "never write grey eyes or hazel eyes" in pp.SYSTEM)
    check("SYSTEM требует называть наготу прямо", "topless" in pp.SYSTEM)
    check("SYSTEM запрещает угадывать при закрытых глазах",
          "closed" in pp.SYSTEM and "omit" in pp.SYSTEM)
    check("в примере ответа есть цвет глаз", "blue eyes" in pp.SYSTEM)

    img = TMP / "eyes" / "p.png"
    make(img, (120, 130, 140), size=(90, 140))
    seen = {}
    saved = pp.post

    def fake_post(url, payload, timeout=300):
        seen.clear()
        seen.update(payload)
        return {"choices": [{"message": {"content":
                '{"subject":"adult woman, grey eyes","negative_extra":"","notes":""}'}}]}

    try:
        pp.post = fake_post
        desc = pp.describe(str(img), "m")
        parts = seen["messages"][1]["content"]
        check("запрос несёт два изображения", len(parts) == 3, str(len(parts)))
        check("второе изображение названо зумом",
              "zoomed on the head" in parts[0]["text"], parts[0]["text"][:120])
        check("описание доехало", desc["subject"] == "adult woman, grey eyes", desc["subject"])

        saved_crop = pp.face_crop
        pp.face_crop = lambda image_path, max_side=640: ""
        pp.describe(str(img), "m")
        check("без зума запрос остаётся с одним изображением",
              len(seen["messages"][1]["content"]) == 2, str(seen["messages"][1]["content"]))
        pp.face_crop = saved_crop

        crop = pp.face_crop(str(img))
        check("зум на лицо существует", bool(crop) and Path(crop).exists(), str(crop))

        pp.post = lambda url, payload, timeout=300: {
            "choices": [{"message": {"content":
                '{"subject":"adult woman, brown eyes","negative_extra":"","notes":""}'}}]}
        cache = pp.write_cache(str(img), pp.describe(str(img), "m"))
        check("свежий кэш читается с меткой",
              (pp.read_cache(str(img)) or {}).get("describe") == pp.DESCRIBE_VERSION,
              str(pp.read_cache(str(img)))[:120])
        data = json.loads(cache.read_text(encoding="utf-8"))
        data.pop("describe", None)
        pp.write_json(cache, data)
        check("старый кэш без метки не отдаётся", pp.read_cache(str(img)) is None,
              str(pp.read_cache(str(img)))[:120])
        cache.unlink(missing_ok=True)
    finally:
        pp.post = saved


def main():
    patch_roots()
    try:
        test_input_no_collision()
        test_cache_by_content()
        test_cache_scene()
        test_stale_fingerprint()
        test_cli_flags_not_cached()
        test_scene_not_ignored_on_cache_hit()
        test_generate_requires_image()
        test_batch_reports_failure()
        test_build_workflows_exit_codes()
        test_dry_run_does_not_touch_lm()
        test_unload_only_own_model()
        test_style_from_graph()
        test_portable_paths()
        test_passport_records_workflow()
        test_scene_cache_cyrillic()
        test_partial_failure_visible()
        test_check_install_covers_all_loaders()
        test_comfy_url_setting()
        test_scene_failure_not_cached()
        test_check_install_strictness()
        test_batch_scene_cache_no_lm()
        test_controlnet_strength_flag()
        test_passport_records_generation_settings()
        test_passport_records_sampler_settings()
        test_atomic_json()
        test_utf8_streams()
        test_passport_not_lost()
        test_style_prompts_consistent()
        test_default_model()
        test_tagger_addon()
        test_tagger_does_not_pollute_cache()
        test_openai_provider()
        test_eye_colour_zoom()
    finally:
        shutil.rmtree(TMP, ignore_errors=True)
    print(f"\nитого: ok {len(passed)}, провалов {len(failed)}")
    if failed:
        print("провалено: " + ", ".join(failed))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
