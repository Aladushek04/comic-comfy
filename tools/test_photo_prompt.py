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

Модели и ComfyUI не нужны: describe/lm_*/generate подменяются.

    python tools/test_photo_prompt.py
"""
import argparse
import contextlib
import io
import json
import shutil
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import photo_prompt as pp
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
    finally:
        shutil.rmtree(TMP, ignore_errors=True)
    print(f"\nитого: ok {len(passed)}, провалов {len(failed)}")
    if failed:
        print("провалено: " + ", ".join(failed))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
