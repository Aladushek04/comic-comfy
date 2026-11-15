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
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import check_install as ci
import photo_prompt as pp
import settings as st
from PIL import Image

TMP = Path(tempfile.mkdtemp(prefix="photo_prompt_test_"))
passed, failed = [], []


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
    check("slug безопасен для имени", beach.name.endswith("__beach-at-sunset.prompt.json"), beach.name)
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


def run_main(argv, subject="adult woman, black dress", scene_result=None):
    """main() с подменёнными моделью и ComfyUI; возвращает stdout."""
    calls = []

    def fake_describe(image_path, model):
        calls.append(("describe", image_path))
        return {"subject": subject, "negative_extra": "missing glasses", "notes": "тест"}

    def fake_scene(desc, scene, model):
        calls.append(("scene", scene))
        return {"subject": subject + ", " + scene, "negative_extra": "missing glasses",
                "notes": "тест", "scene": scene}

    saved = (pp.describe, pp.rewrite_scene, pp.lm_models, pp.pick_model, pp.unload_all, sys.argv)
    pp.describe, pp.rewrite_scene = fake_describe, fake_scene
    pp.lm_models, pp.pick_model = (lambda: {}), (lambda resp, wanted: "test-model")
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
        unload_all=False,
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
    pp.lm_models, pp.pick_model = (lambda: {}), (lambda r, w: "m")
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
    pp.lm_models = lambda: (calls.append("lm_models") or {"models": []})
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
        pp.lm_models = lambda: (_ for _ in ()).throw(RuntimeError("LM Studio не запущен"))
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
    pp.lm_models = lambda: resp
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
    pos, neg = texts
    for tag in ("warm skin tone", "natural skin color", "dark lenses"):
        check(f"bimbo сохранил «{tag}»", tag in pos, pos[:160])
    check("bimbo негатив сохранил «white lenses»", "white lenses" in neg, neg[:160])

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
    for p in sorted(tools.iterdir()):
        if p.suffix.lower() not in (".py", ".ps1") or p.name == Path(__file__).name:
            continue
        for i, line in enumerate(p.read_text(encoding="utf-8").splitlines(), 1):
            if disk.search(line):
                bad.append(f"{p.name}:{i}")
    check("в скриптах нет литералов диска", not bad, ", ".join(bad))

    yaml = TMP / "extra_model_paths.yaml"
    yaml.write_text("comic_shared:\n  base_path: Q:\\models\n", encoding="utf-8")
    got = st.resolve(env={}, yaml_path=yaml)
    check("base_path читается из yaml", str(got["models"]) == "Q:\\models", str(got["models"]))
    check("input/output рядом с models",
          got["input"] == got["models"].parent / "input"
          and got["output"] == got["models"].parent / "output", str(got))
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
    needed = ci.workflow_models(tools.parent / "workflows")
    for name in needed:
        p = models / Path(name)
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
    check("все 19 моделей найдены", "моделей в сборках: 19" in out, out[-200:])

    (models / "depth_anything_v2_vitl.pth").unlink()
    rc, out = check_install_run()
    check("depth не считается пропажей (его качает узел)", rc == 0, out[-200:])

    missing = models / "loras" / "thepit2_1768210.safetensors"
    if not missing.exists():
        missing = next(p for p in models.rglob("thepit2_1768210.safetensors"))
    missing.unlink()
    rc, out = check_install_run()
    check("пропала модель -> код 1", rc == 1 and "thepit2_1768210" in out, out[-200:])
    check("пропажа названа", "нет на диске" in out, out[-200:])

    missing.write_bytes(b"x")
    docs.write_text("\n".join(n for n in needed if "bimbofusion" not in n), encoding="utf-8")
    rc, out = check_install_run()
    check("модель не описана в инструкции -> код 1",
          rc == 1 and "не описано" in out, out[-200:])


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
    finally:
        shutil.rmtree(TMP, ignore_errors=True)
    print(f"\nитого: ok {len(passed)}, провалов {len(failed)}")
    if failed:
        print("провалено: " + ", ".join(failed))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
