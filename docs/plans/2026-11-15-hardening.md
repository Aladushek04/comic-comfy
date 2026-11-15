# Устойчивость comic-comfy: срок хранения, атомарная запись, предел картинки, UTF-8

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Панель и CLI перестают накапливать мусор, портить JSON при обрыве, декодировать гигантские картинки в RAM и падать на кириллице при перенаправлении вывода.

**Architecture:** Четыре независимых правки в существующих модулях: уборка внутри `JobService` (память + папки `out\jobs`), один хелпер атомарной записи JSON в `photo_prompt` вместо трёх прямых `write_text`, проверка размера картинки по заголовку в `save_png`, и общий `settings.utf8_streams()` в точках входа. Каждая правка закрыта проверками в существующих самопроверках, без моделей и GPU.

**Tech Stack:** Python 3.14, Pillow, NiceGUI (только чтение кода), самопроверки на `http.server`-заглушках.

**Spec:** `docs/2026-11-15-hardening-design.md`

## Global Constraints

- Никаких загрузок моделей и обращений к GPU/ComfyUI/LM Studio: все проверки — на заглушках.
- Все сообщения пользователю — по-русски, в стиле окружающего кода.
- Пути — только через `tools\settings.py` / env; путь конкретной машины в код не попадает.
- `workflows\*.json` руками не правятся (только генератор).
- Существующие проверки обязаны остаться зелёными: `tools\test_photo_prompt.py` (180), `tools\test_web_app.py` (147), `tools\test_web_ui.py` (30). Новые добавляются в те же файлы.
- Кадры в `out\<фото>_<хэш>\` и `out\presets\` не удаляются никогда.
- Числа из спеки: `keep_hours=24.0`, `keep_jobs=30`, `MAX_SIDE=8192`, `MAX_PIXELS=64_000_000`, `MAX_UPLOAD` остаётся 30 МБ.

## Review Focus

1. **Файл 30 МБ с огромным заголовком** (PNG-бомба, панорама 40000 px, EXIF-поворот) — ожидается отказ с числами, а не OOM, не `DecompressionBombError`, не трейсбек.
2. **Обрезанный/битый PNG**, у которого заголовок читается, а CRC нет — отказ должен быть про размер, если размер велик, и «это не картинка» только если размер в норме.
3. **Вывод CLI/тестов в файл или пайп на машине с cp1252** — кириллица читается как UTF-8, коды возврата не меняются, трейсбека нет.
4. **Чтение паспорта во время его записи** (вкладка опрашивает `run.json`, пока пишется новый кадр) — читается либо прежний, либо новый JSON, никогда не половина.
5. **Уборка на живом сервере** — открытая вкладка на только что завершённом задании не теряет результат, а истёкшее задание объясняет себя вместо «Задание не запущено».

---

### Task 1: Предел картинки по заголовку

**Files:**
- Modify: `webapp/service.py:24-53`
- Test: `tools/test_web_app.py` (новая функция `test_upload_limits`, вызов в `main()`)

**Interfaces:**
- Consumes: `UploadError`, `MAX_UPLOAD`, `save_upload(name, data)` — как сейчас.
- Produces: `MAX_SIDE: int = 8192`, `MAX_PIXELS: int = 64_000_000` в `webapp/service.py`; `save_png` по-прежнему возвращает `Path` и бросает `UploadError`.

- [ ] **Step 1: Написать падающую проверку**

В `tools/test_web_app.py` рядом с `test_upload_validation` (после неё) добавить:

```python
def test_upload_limits():
    print("\n21) загрузка: предел картинки до декодирования")
    from webapp import service as svc_mod
    svc = JobService(jobs_root=TMP / "limit_root")
    try:
        wide = TMP / "wide.png"
        Image.new("RGB", (8200, 8), (9, 9, 9)).save(wide)
        try:
            svc.save_upload("wide.png", wide.read_bytes())
            check("картинка шире предела отвергается", False)
        except UploadError as e:
            check("картинка шире предела отвергается", True)
            check("в ошибке сторона, предел и площадь",
                  "8200×8" in str(e) and "8192" in str(e) and "64" in str(e), str(e))

        bomb = bytearray(_png_bytes())
        struct.pack_into(">I", bomb, 16, 7900)      # ширина в IHDR
        struct.pack_into(">I", bomb, 20, 8192)      # высота: стороны в норме
        try:
            svc.save_upload("bomb.png", bytes(bomb))
            check("площадь свыше предела отвергается", False)
        except UploadError as e:
            check("площадь свыше предела отвергается",
                  "7900×8192" in str(e) and "64.7 Мпикс" in str(e), str(e))
            check("заголовок важнее CRC: это не «не картинка»",
                  "не картинка" not in str(e), str(e))
        check("нормальная картинка по-прежнему проходит",
              svc.upload_path(svc.save_upload("ok.png", _png_bytes())[0]) is not None)
    finally:
        svc.shutdown()
```

Вызов в `main()`: `test_upload_limits()` последним, до `finally`.

- [ ] **Step 2: Убедиться, что проверка падает**

Run: `python tools\test_web_app.py`
Expected: FAIL — «картинка шире предела отвергается», файл принимается.

- [ ] **Step 3: Реализовать предел в `webapp/service.py`**

Рядом с `MAX_UPLOAD`: `MAX_SIDE = 8192`, `MAX_PIXELS = 64_000_000` и хелпер

```python
def _size_text(width, height):
    """Размер для текста ошибки: «8200×8 (0.1 Мпикс)»."""
```

В `save_png` после `if len(data) > MAX_UPLOAD: ...` и импорта Pillow:
`Image.MAX_IMAGE_PIXELS = None` (свой предел строже и, в отличие от PIL, называет настоящий размер), затем в первом `Image.open` **до** `im.verify()`:

```python
width, height = im.size
if width > MAX_SIDE or height > MAX_SIDE or width * height > MAX_PIXELS:
    raise UploadError(
        f"файл {_size_text(width, height)} больше предела: сторона до "
        f"{MAX_SIDE} px, площадь до {MAX_PIXELS / 1e6:.1f} Мпикс")
```

Порядок остаётся: `im.size` → предел → `verify()` → `exif_transpose` → `convert` → `thumbnail(4096)` → `save`.

- [ ] **Step 4: Проверить**

Run: `python tools\test_web_app.py`
Expected: `итого: ok 151, провалов 0` (147 + 4).

- [ ] **Step 5: Коммит**

```bash
git add webapp/service.py tools/test_web_app.py
git commit -m "Загрузка: отказ по стороне и площади до декодирования"
```

---

### Task 2: Атомарная запись JSON

**Files:**
- Modify: `tools/photo_prompt.py` (импорт `tempfile`, новый `write_json`, `write_cache:210-221`, `save_run:630-651`)
- Modify: `tools/jobs.py:260-264` (пресет)
- Test: `tools/test_photo_prompt.py` (новая функция `test_atomic_json`, вызов в `main()`)

**Interfaces:**
- Consumes: `photo_prompt.ROOT`, `OUTPUT_DIR`, `image_tag`, `fingerprint` — как сейчас.
- Produces: `photo_prompt.write_json(path, data, indent=2) -> Path`. Возвращает путь целевого файла, бросает исключение записи наружу, временный файл не оставляет.

- [ ] **Step 1: Написать падающие проверки**

В `tools/test_photo_prompt.py` (импортировать `threading`, `jobs`):

```python
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

    # паспорт: две записи, вторая срывается — первая остаётся читаемой
    saved_roots = (pp.ROOT, pp.OUTPUT_DIR)
    pp.ROOT, pp.OUTPUT_DIR = TMP / "root_atomic", TMP / "out_atomic"
    pp.OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    try:
        frame = "07_photo_thepit_00042_.png"
        (pp.OUTPUT_DIR / frame).write_bytes(b"png")
        with contextlib.redirect_stdout(io.StringIO()):
            pp.save_run(str(img), "07_photo_thepit",
                        {"subject": "adult woman", "negative_extra": ""}, 1, None, frame)
        book = pp.ROOT / "out" / pp.image_tag(img) / "run.json"
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

        # пресет: та же гарантия
        pp.os.fsync = lambda fd: (_ for _ in ()).throw(OSError("диск отвалился"))
        try:
            try:
                jobs.run_job(jobs.JobRequest(
                    image=str(img), workflow="07_photo_thepit", subject="adult woman",
                    generate=False, save_preset="atomic"))
                check("обрыв записи пресета виден", False)
            except OSError:
                check("обрыв записи пресета виден", True)
        finally:
            pp.os.fsync = saved_fsync
    finally:
        pp.ROOT, pp.OUTPUT_DIR = saved_roots

    # читатель рядом с писателем не видит половину файла
    target = TMP / "atomic_race.json"
    bad = []
    stop = threading.Event()

    def reader():
        while not stop.is_set():
            try:
                json.loads(target.read_text(encoding="utf-8"))
            except FileNotFoundError:
                pass
            except Exception as e:
                bad.append(repr(e))
                return

    t = threading.Thread(target=reader, daemon=True)
    pp.write_json(target, {"runs": []})
    t.start()
    try:
        for n in range(30):
            pp.write_json(target, {"runs": [{"seed": n}] * 50})
    finally:
        stop.set()
        t.join(timeout=5)
    check("чтение во время записи не видит половину", not bad, str(bad[:1]))
```

Вызов в `main()`: `test_atomic_json()` последним, до `finally`.

- [ ] **Step 2: Убедиться, что проверки падают**

Run: `python tools\test_photo_prompt.py`
Expected: FAIL — `AttributeError: module 'photo_prompt' has no attribute 'write_json'`.

- [ ] **Step 3: Реализовать `write_json` и перевести три записи**

В `tools/photo_prompt.py` (нужен `import tempfile`):

```python
def write_json(path, data, indent=2):
    """Пишет JSON целиком или не трогает файл: обрыв не оставляет половину.

    Временный файл лежит рядом с целевым: os.replace атомарен только в пределах
    одной файловой системы. fsync до замены — чтобы после сбоя питания на диске
    остался прежний файл, а не пустой новый.
    """
```

Тело: `mkstemp(dir=<папка целевого>, prefix=path.name + ".", suffix=".tmp")` → `os.fdopen(fd, "w", encoding="utf-8")` → `json.dumps(data, ensure_ascii=False, indent=indent)` → `flush()` → `os.fsync(fileno())` → `os.replace(tmp, path)`; при любом исключении `os.unlink(tmp)` и повторный `raise`.

Затем: `write_cache` и `save_run` пишут через `write_json(p, data)` / `write_json(book_path, book)` вместо `write_text`; в `jobs.py` запись пресета — `pp.write_json(path, desc)` (mkdir остаётся или переезжает в хелпер).

- [ ] **Step 4: Проверить**

Run: `python tools\test_photo_prompt.py`
Expected: `итого: ok 189, провалов 0` (180 + 9).

- [ ] **Step 5: Коммит**

```bash
git add tools/photo_prompt.py tools/jobs.py tools/test_photo_prompt.py
git commit -m "Кэш, паспорт и пресет пишутся атомарно: os.replace вместо write_text"
```

---

### Task 3: UTF-8 в точках входа

**Files:**
- Modify: `tools/settings.py` (новый `utf8_streams`, `import sys`)
- Modify: `tools/photo_prompt.py` (`__main__`), `tools/build_workflows.py` (`__main__`), `tools/check_install.py` (`__main__`), `tools/test_photo_prompt.py` (уровень модуля), `tools/test_web_app.py` (уровень модуля)
- Test: `tools/test_photo_prompt.py` (новая функция `test_utf8_streams`)

**Interfaces:**
- Consumes: `sys.stdout`, `sys.stderr` — настоящие или подменённые потоки.
- Produces: `settings.utf8_streams(streams=None) -> None`; по умолчанию `(sys.stdout, sys.stderr)`, идемпотентна, потоки без `reconfigure` пропускает.

- [ ] **Step 1: Написать падающие проверки**

```python
def test_utf8_streams():
    print("\n26) кириллица в пайп без PYTHONIOENCODING")

    class Fake:
        encoding = "cp1252"

        def __init__(self):
            self.got = None

        def reconfigure(self, **kw):
            self.got = kw

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
```

В `tools/test_photo_prompt.py` добавить `import settings as st` (если ещё нет) и `import subprocess`/`Path` уже есть.

- [ ] **Step 2: Убедиться, что проверки падают**

Run: `python tools\test_photo_prompt.py`
Expected: FAIL — `AttributeError: module 'settings' has no attribute 'utf8_streams'`.

- [ ] **Step 3: Реализовать хелпер и вызвать в точках входа**

`tools/settings.py`:

```python
def utf8_streams(streams=None):
    """Русский текст в stdout/stderr и при перенаправлении в файл или пайп.

    В консоли Python пишет через WriteConsoleW, а в пайп — в кодировке локали
    (на этой машине cp1252): print с кириллицей падал с UnicodeEncodeError.
    errors="replace" оставляет падение невозможным и в чужом терминале.
    """
```

Тело: перебрать `streams or (sys.stdout, sys.stderr)`; если у потока есть `reconfigure` и `(getattr(stream, "encoding", "") or "").lower()` не `utf-8`/`utf8` — `stream.reconfigure(encoding="utf-8", errors="replace")`.

Вызовы: первой строкой соответствующих блоков — `if __name__ == "__main__":` в `photo_prompt.py`, `build_workflows.py`, `check_install.py`; на уровне модуля (после импортов) в `test_photo_prompt.py` и `test_web_app.py` — так импорт `test_web_app` покрывает и `test_web_ui.py`.

- [ ] **Step 4: Проверить оба режима без переменной**

Run: `python tools\test_photo_prompt.py` → `итого: ok 193, провалов 0` (189 + 4).
Run: `python tools\test_web_app.py` → все проверки зелёные, кириллица в выводе читается.
Run (пайп, env очищен): `python tools\check_install.py | Select-Object -First 3` → русский текст, без `UnicodeEncodeError`.

- [ ] **Step 5: Коммит**

```bash
git add tools/settings.py tools/photo_prompt.py tools/build_workflows.py tools/check_install.py tools/test_photo_prompt.py tools/test_web_app.py
git commit -m "UTF-8 в точках входа: кириллица не падает при перенаправлении вывода"
```

---

### Task 4: Срок хранения заданий и черновиков

**Files:**
- Modify: `webapp/service.py:78-122` (`__init__`, `submit`, новые `purge`, `purge_uploads`)
- Test: `tools/test_web_app.py` (новая функция `test_job_retention`)

**Interfaces:**
- Consumes: `Job`, `jobs.JobRequest`, `pp.ROOT`.
- Produces: `JobService(workers=1, jobs_root=None, keep_hours=24.0, keep_jobs=30)`; `JobService.purge(now=None) -> list[str]` (снятые id); `JobService.purge_uploads(now=None, hours=None) -> list[str]`; вызовы в `__init__` и в начале `submit`.

- [ ] **Step 1: Написать падающие проверки**

```python
def test_job_retention():
    print("\n22) срок хранения: память, папки заданий, черновики")
    lm, comfy = lm_stub(), comfy_stub()
    try:
        with stand(lm, comfy) as root:
            img = TMP / "retention.png"
            make(img, (20, 40, 60))
            comfy.state.delay = 3                 # первое задание успевает побыть running
            svc = JobService(jobs_root=root / "out" / "jobs", keep_hours=24.0, keep_jobs=30)
            try:
                uid, upload = svc.save_upload("retention.png", img.read_bytes())
                first = svc.submit(jobs.JobRequest(
                    image="", workflow="07_photo_thepit", subject="adult woman, one", seed=1),
                    source_path=str(img))
                while svc.snapshot(first.id)["status"] != "running":
                    time.sleep(0.05)
                second = svc.submit(jobs.JobRequest(
                    image="", workflow="07_photo_thepit", subject="adult woman, two", seed=2),
                    source_path=str(img))
                check("второе задание в очереди", svc.snapshot(second.id)["status"] == "queued",
                      svc.snapshot(second.id)["status"])
                check("черновик загрузки жив после «Старт»", svc.upload_path(uid) == upload)
                gone = svc.purge(now=time.time() + 30 * 3600)
                check("работающее и очередное не снимаются", gone == [], str(gone))
                check("их папки целы", (root / "out" / "jobs" / first.id).is_dir()
                      and (root / "out" / "jobs" / second.id).is_dir())
                svc.wait(first.id, timeout=60)
                svc.wait(second.id, timeout=60)
                frames = [Path(f["folder"]) for f in (svc.snapshot(first.id)["frames"] or [])]
                gone = svc.purge(now=time.time())
                check("свежие завершённые остаются", gone == [], str(gone))
                check("папки двух заданий на диске",
                      (root / "out" / "jobs" / first.id).is_dir()
                      and (root / "out" / "jobs" / second.id).is_dir())

                # срок: оба снимаются, папки удаляются, кадры и run.json — нет
                gone = svc.purge(now=time.time() + 25 * 3600)
                check("истёкшие сняты из памяти",
                      svc.get(first.id) is None and svc.get(second.id) is None, str(gone))
                check("папки истёкших удалены",
                      not (root / "out" / "jobs" / first.id).exists()
                      and not (root / "out" / "jobs" / second.id).exists())
                check("кадры и run.json на месте",
                      all((f / "run.json").exists() for f in frames), str(frames))

                up_dir = root / "out" / "jobs" / "uploads" / uid
                check("истёкший черновик удалён", not up_dir.exists())

                # счётчик: два завершённых, keep_jobs=1 — снимается старшее
                svc2 = JobService(jobs_root=root / "out" / "jobs2", keep_jobs=1)
                try:
                    a = svc2.submit(jobs.JobRequest(image="", workflow="07_photo_thepit",
                                                    subject="adult woman, a", seed=3,
                                                    generate=False), source_path=str(img))
                    svc2.wait(a.id, timeout=60)
                    b = svc2.submit(jobs.JobRequest(image="", workflow="07_photo_thepit",
                                                    subject="adult woman, b", seed=4,
                                                    generate=False), source_path=str(img))
                    svc2.wait(b.id, timeout=60)
                    gone = svc2.purge()
                    check("сверх последних 30 снимается старшее", gone == [a.id], str(gone))
                    check("последнее задание осталось", svc2.get(b.id) is not None)
                    check("его папка цела", (root / "out" / "jobs2" / b.id).is_dir())
                finally:
                    svc2.shutdown()
            finally:
                svc.shutdown()
                # кадры, созданные stand(), убираем за собой
    finally:
        lm.stop()
        comfy.stop()
```

Вызов в `main()`: `test_job_retention()` последним.

- [ ] **Step 2: Убедиться, что проверка падает**

Run: `python tools\test_web_app.py`
Expected: FAIL — `TypeError: __init__() got an unexpected keyword argument 'keep_hours'`.

- [ ] **Step 3: Реализовать уборку в `webapp/service.py`**

`__init__` принимает `keep_hours=24.0, keep_jobs=30`, сохраняет их и вызывает `self.purge()` (подъём сервера — единственный момент, когда видны папки прошлых запусков).

`purge(now=None)`:
- под `self._lock`: `queued`/`running` не трогаются; завершённые в порядке `_order`; остаётся задание, если `now - (finished or created) < keep_hours * 3600` **и** оно входит в последние `keep_jobs` завершённых (`finished[-keep_jobs:]`, при `keep_jobs <= 0` ограничение по числу выключено); остальные удаляются из `_jobs` и `_order`; `_latest` переезжает на последнее оставшееся;
- после снятия блокировки — удалить папки: только если `folder.parent.resolve() == (jobs_root / ???)` нет: проверка «имя папки == id и папка лежит внутри `self.jobs_root`, но не в `uploads`», затем `shutil.rmtree(..., ignore_errors=True)`;
- в конце `self.purge_uploads(now=now)`;
- вернуть список снятых id.

`purge_uploads(now=None, hours=None)`: папки `jobs_root\uploads\<uid>` со `st_mtime` старше `hours or keep_hours` — удалить, вернуть id.

В `submit()` первой строкой (до проверки исходника) — `self.purge()`: он же чистит папки прошлых запусков.

- [ ] **Step 4: Проверить**

Run: `python tools\test_web_app.py`
Expected: `итого: ok 161, провалов 0` (151 + 10).

- [ ] **Step 5: Коммит**

```bash
git add webapp/service.py tools/test_web_app.py
git commit -m "Задания и черновики загрузок чистятся по сроку; кадры не трогаются"
```

---

### Task 5: Истёкшее задание объясняет себя

**Files:**
- Modify: `webapp/state.py` (новый `missing_job_text`), `webapp/ui.py:23`, `webapp/ui.py:818-825`
- Test: `tools/test_web_app.py` (новая функция `test_expired_job_text`), `tools/test_web_ui.py` (раздел 12)

**Interfaces:**
- Consumes: `JobService.snapshot(job_id)` → `None` для снятого/неизвестного задания.
- Produces: `webapp.state.missing_job_text(job_id) -> str`: с id — «История задания истекла: файлы кадров остались в out\<фото>_<хэш>», без id — «Задание не запущено».

- [ ] **Step 1: Написать падающие проверки**

`tools/test_web_app.py`:

```python
def test_expired_job_text():
    print("\n23) истёкшее задание объясняет себя")
    check("без задания — прежняя подпись",
          missing_job_text(None) == "Задание не запущено", missing_job_text(None))
    text = missing_job_text("deadbeef00")
    check("с id — истёкшая история", "истекла" in text and "out" in text, text)
    check("это не «не запущено»", "не запущено" not in text, text)
```

Импорт в шапке: `from webapp.state import fmt_setting, missing_job_text, pass_steps`.

`tools/test_web_ui.py`, перед `browser.close()` (после раздела 11):

```python
            print("\n12) истёкшая история задания")
            page3 = ctx.new_page()
            page3.goto(f"http://127.0.0.1:{port}/?job=deadbeef00")
            wait_text(page3, "История задания истекла", 15000)
            check("истёкшее задание объясняет себя",
                  "Задание не запущено" not in body(page3))
            page3.close()
```

- [ ] **Step 2: Убедиться, что проверки падают**

Run: `python tools\test_web_app.py`
Expected: FAIL — `ImportError: cannot import name 'missing_job_text'`.

- [ ] **Step 3: Реализовать текст и подключить к странице**

`webapp/state.py` — функция с docstring: почему нельзя показывать «не запущено» (пользователь не поймёт, что кадры на диске целы).

`webapp/ui.py`: импорт `missing_job_text` из `.state`; в `refresh()` при `snap is None` ставить `self.w["stage"].set_text(missing_job_text(self.state["job_id"]))`.

- [ ] **Step 4: Проверить**

Run: `python tools\test_web_app.py` → `итого: ok 164, провалов 0` (161 + 3).
Run: `.venv-web\Scripts\python.exe tools\test_web_ui.py` → `итого: ok 31, провалов 0`.

- [ ] **Step 5: Коммит**

```bash
git add webapp/state.py webapp/ui.py tools/test_web_app.py tools/test_web_ui.py
git commit -m "Страница с ?job= на снятое задание говорит об истёкшей истории"
```

---

### Task 6: Документация и итоговые числа

**Files:**
- Modify: `AGENTS.md` (раздел «Грабли» — новый пункт; «Проверки перед словом готово» — числа)
- Modify: `README.md:22-23,147-149` (числа проверок и заметка про кодировку)

**Interfaces:**
- Consumes: реальные итоги `test_photo_prompt.py`, `test_web_app.py`, `test_web_ui.py`.
- Produces: документ, совпадающий с поведением кода.

- [ ] **Step 1: Прогнать все проверки и записать числа**

Run: `python tools\test_photo_prompt.py`, `python tools\test_web_app.py`, `.venv-web\Scripts\python.exe tools\test_web_ui.py`, `python tools\check_install.py`
Expected: все провалов 0; числа из вывода переносятся в тексты.

- [ ] **Step 2: Дописать AGENTS.md**

Новый пункт «0.15» с четырьмя фактами: что чистится (задания, черновики) и что не чистится (кадры, `run.json`, пресеты); почему JSON пишется только через `photo_prompt.write_json`; почему предел картинки проверяется по заголовку и почему `Image.MAX_IMAGE_PIXELS` выключен; зачем `settings.utf8_streams()` в точках входа. Обновить числа в «Проверки перед словом готово».

- [ ] **Step 3: Поправить README**

Числа проверок в строках 22-23 и 147-149; одна строка о том, что переменные окружения для кодировки больше не нужны.

- [ ] **Step 4: Коммит**

```bash
git add AGENTS.md README.md
git commit -m "Документация: сроки хранения, атомарная запись, предел картинки, UTF-8"
```
