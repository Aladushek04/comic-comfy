"""Очередь и состояние заданий. Никакого NiceGUI: только потоки и данные.

Один исполнитель на всё приложение: две вкладки не запускают две генерации,
вторая задача ждёт в очереди. Состояние живёт на сервере, поэтому обновление
вкладки показывает текущий этап, а не начинает задание заново.

`cancel` — единственное действие, которое трогает чужой процесс: задание из
очереди снимается до старта, работающему уходит `POST /interrupt` в ComfyUI,
и только если оно уже отправило туда свой промпт.
"""
import io
import re
import shutil
import struct
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path

import jobs
import photo_prompt as pp


class UploadError(ValueError):
    """Файл не картинка или слишком большой."""


MAX_UPLOAD = 30 * 1024 * 1024
MAX_SIDE = 8192                 # сторона выше — отказ до декодирования
MAX_PIXELS = 64_000_000         # площадь выше — отказ до декодирования

PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
# SOF0..SOF15 без DHT (C4), JPG (C8) и DAC (CC): только у них есть размеры кадра.
SOF_MARKERS = frozenset(range(0xC0, 0xD0)) - {0xC4, 0xC8, 0xCC}
JOB_ID = re.compile(r"^[0-9a-f]{10}$")


def _safe_stem(name):
    stem = Path(str(name or "photo")).stem
    keep = "".join(ch for ch in stem if ch.isalnum() or ch in "-_")[:40]
    return keep or "photo"


def _size_text(width, height):
    """Размер для текста ошибки: «8200×8 (0.1 Мпикс)»."""
    return f"{width}×{height} ({width * height / 1e6:.1f} Мпикс)"


def _too_big(width, height):
    return width > MAX_SIDE or height > MAX_SIDE or width * height > MAX_PIXELS


def _too_big_text(width, height):
    return (f"файл {_size_text(width, height)} больше предела: сторона до "
            f"{MAX_SIDE} px, площадь до {MAX_PIXELS / 1e6:.1f} Мпикс")


def _png_size(data):
    """Размер из IHDR: чанк ищем, а не читаем по фиксированному смещению."""
    pos = len(PNG_SIGNATURE)
    while pos + 8 <= len(data):
        length, kind = struct.unpack(">I4s", data[pos:pos + 8])
        if kind == b"IHDR" and pos + 16 <= len(data):
            return struct.unpack(">II", data[pos + 8:pos + 16])
        pos += 12 + length
    return None


def _jpeg_size(data):
    """Размер из первого SOF: к этому моменту кадр ещё не декодирован."""
    pos = 2
    while pos + 4 <= len(data):
        if data[pos] != 0xFF:
            pos += 1
            continue
        marker = data[pos + 1]
        if marker in (0xD8, 0x01) or 0xD0 <= marker <= 0xD7:
            pos += 2
            continue
        if marker == 0xDA:              # начались данные кадра
            return None
        length = struct.unpack(">H", data[pos + 2:pos + 4])[0]
        if marker in SOF_MARKERS and pos + 9 <= len(data):
            height, width = struct.unpack(">HH", data[pos + 5:pos + 9])
            return width, height
        pos += 2 + length
    return None


def image_size(data):
    """Размер из заголовка PNG или JPEG, до Image.open.

    Зачем до открытия: PIL отказывается открывать кадр больше ~179 Мпикс своей
    защитой от «бомб» и не называет размер, а verify() на обрезанном файле
    говорит «не картинка» — оба ответа бесполезны тому, кто прислал панораму.
    None — формат не опознан по заголовку: там решает проверка после открытия.
    """
    if data.startswith(PNG_SIGNATURE):
        return _png_size(data)
    if data[:2] == b"\xff\xd8":
        return _jpeg_size(data)
    return None


def save_png(folder, data, name="photo"):
    """Пересохраняет загруженное фото в PNG: без EXIF и посторонних байтов."""
    if len(data) > MAX_UPLOAD:
        raise UploadError(f"файл больше {MAX_UPLOAD // (1024 * 1024)} МБ")
    size = image_size(data)
    if size and _too_big(*size):
        raise UploadError(_too_big_text(*size))
    from PIL import Image, ImageOps
    try:
        with Image.open(io.BytesIO(data)) as im:
            if _too_big(*im.size):          # остальные форматы: размер из заголовка
                raise UploadError(_too_big_text(*im.size))
            im.verify()
        with Image.open(io.BytesIO(data)) as im:
            im = ImageOps.exif_transpose(im)
            if im.mode not in ("RGB", "RGBA"):
                im = im.convert("RGB")
            im.thumbnail((4096, 4096))
            folder.mkdir(parents=True, exist_ok=True)
            dst = Path(folder) / (_safe_stem(name) + ".png")
            im.save(dst, "PNG")
            return dst
    except UploadError:
        raise
    except Image.DecompressionBombError as e:
        raise UploadError(f"картинка больше предела: сторона до {MAX_SIDE} px, "
                          f"площадь до {MAX_PIXELS / 1e6:.1f} Мпикс") from e
    except Exception as e:
        raise UploadError(f"это не картинка: {e}") from None


@dataclass
class Job:
    id: str
    folder: Path
    source: Path
    request: jobs.JobRequest
    source_tag: str = ""
    created: float = field(default_factory=time.time)
    started: float | None = None
    finished: float | None = None
    status: str = "queued"          # queued | running | done | partial | error | described | cancelled
    stage: str = "queued"
    variant: dict | None = None
    events: list = field(default_factory=list)
    result: jobs.JobResult | None = None
    error: str = ""
    # Отмена: событие читает задание, `prompt_id` — адрес того, что прерывать.
    # Промпт запоминается, когда ComfyUI назвал id: `/interrupt` без него прервал
    # бы чужой граф, а снятое из очереди задание не должно звать ComfyUI вовсе.
    cancel: threading.Event = field(default_factory=threading.Event,
                                    repr=False, compare=False)
    prompt_id: str | None = None
    future: object | None = field(default=None, repr=False, compare=False)
    lock: threading.Lock = field(default_factory=threading.Lock, repr=False, compare=False)


class JobService:
    """Один поток исполнения, много читателей состояния."""

    def __init__(self, workers=1, jobs_root=None, keep_hours=24.0, keep_jobs=30):
        self.jobs_root = Path(jobs_root or (pp.ROOT / "out" / "jobs"))
        self.keep_hours = float(keep_hours)
        self.keep_jobs = int(keep_jobs)
        self._jobs = {}
        self._order = []
        self._lock = threading.Lock()
        self._active = None
        self._latest = None
        self._pool = ThreadPoolExecutor(max_workers=workers, thread_name_prefix="comic-job")
        self.purge()        # подъём сервера: папки прошлых запусков уже никому не нужны

    # ------------------------------------------------------------ уборка
    def purge(self, now=None):
        """Снимает старые задания из памяти и их папки с диска.

        Кадры и `run.json` живут в `out\\<фото>_<хэш>\\` и не трогаются: это
        результат работы. Задание в очереди и работающее не снимаются никогда,
        даже если часы ушли вперёд. Возвращает список снятых id.
        """
        now = time.time() if now is None else now
        keep = self.keep_hours * 3600
        dropped = []
        with self._lock:
            finished = [jid for jid in self._order
                        if jid in self._jobs
                        and self._jobs[jid].status not in ("queued", "running")]
            fresh = set(finished[-self.keep_jobs:]) if self.keep_jobs > 0 else set(finished)
            for jid in list(self._order):
                job = self._jobs.get(jid)
                if job is None:
                    self._order.remove(jid)
                    continue
                if job.status in ("queued", "running"):
                    continue
                if jid in fresh and now - (job.finished or job.created) < keep:
                    continue
                self._jobs.pop(jid, None)
                self._order.remove(jid)
                dropped.append((jid, job.folder))
            if self._latest not in self._jobs:
                self._latest = self._order[-1] if self._order else None
            alive = set(self._jobs)
        for jid, folder in dropped:
            self._drop_folder(jid, folder)
        self.sweep_jobs(now=now, keep_ids=alive)
        self.purge_uploads(now=now)
        return [jid for jid, _ in dropped]

    def sweep_jobs(self, now=None, keep_ids=()):
        """Убирает папки заданий, оставшиеся от прошлых запусков сервера.

        В памяти таких заданий уже нет (сервер перезапущен), вкладок с ними тоже:
        папка нужна только на время задания, а кадры лежат в out\\<фото>_<хэш>\\).
        Трогаем только папки с именем id задания: uploads и чужие папки — не наши.
        """
        now = time.time() if now is None else now
        keep_hours = self.keep_hours * 3600
        root = self.jobs_root
        gone = []
        if not root.is_dir():
            return gone
        keep = set(keep_ids)
        for folder in root.iterdir():
            if not JOB_ID.match(folder.name) or folder.name in keep:
                continue
            try:
                if not folder.is_dir() or now - folder.stat().st_mtime <= keep_hours:
                    continue
            except OSError:
                continue
            shutil.rmtree(folder, ignore_errors=True)
            gone.append(folder.name)
        return gone

    def _drop_folder(self, jid, folder):
        """Удаляет папку задания; uploads и чужие пути не трогает."""
        folder = Path(folder)
        if folder.name != str(jid) or not JOB_ID.match(folder.name):
            return
        if folder.parent.resolve() != self.jobs_root.resolve():
            return
        shutil.rmtree(folder, ignore_errors=True)

    def purge_uploads(self, now=None, hours=None):
        """Убирает черновики загрузок старше срока. После «Старт» черновик живёт:
        пока задание идёт, `/media/upload/{uid}` показывает по нему превью."""
        now = time.time() if now is None else now
        hours = self.keep_hours if hours is None else float(hours)
        root = self.jobs_root / "uploads"
        if not root.is_dir():
            return []
        gone = []
        for folder in root.iterdir():
            try:
                old = now - folder.stat().st_mtime > hours * 3600
            except OSError:
                continue
            if folder.is_dir() and old:
                shutil.rmtree(folder, ignore_errors=True)
                gone.append(folder.name)
        return gone

    # ------------------------------------------------------------ загрузка
    def save_upload(self, name, data):
        """Черновик фото для превью: id и путь. Задание создаётся на «Старт»."""
        uid = uuid.uuid4().hex[:12]
        folder = self.jobs_root / "uploads" / uid
        path = save_png(folder, data, name)
        return uid, path

    def upload_path(self, uid):
        folder = self.jobs_root / "uploads" / str(uid)
        if not folder.is_dir() or Path(uid).name != str(uid):
            return None
        files = list(folder.glob("*.png"))
        return files[0] if files else None

    # ------------------------------------------------------------ задания
    def submit(self, request, upload_id=None, source_path=None):
        """Ставит задание в очередь. Возвращает Job сразу, без ожидания."""
        self.purge()        # заодно убирает папки прошлых запусков и старые черновики
        src = Path(source_path) if source_path else self.upload_path(upload_id)
        if src is None or not src.exists():
            raise UploadError("сначала загрузи фото")
        jid = uuid.uuid4().hex[:10]
        folder = self.jobs_root / jid
        folder.mkdir(parents=True, exist_ok=True)
        # Имя файла сохраняем: иначе кадры всех загрузок попадают в out\source_<хэш>\.
        local = folder / (src.stem + src.suffix.lower())
        shutil.copy2(src, local)
        request.image = str(local)
        job = Job(id=jid, folder=folder, source=local, request=request,
                  source_tag=pp.image_tag(local))
        with self._lock:
            self._jobs[jid] = job
            self._order.append(jid)
            self._latest = jid
        job.future = self._pool.submit(self._run, job)
        return job

    def _run(self, job):
        with job.lock:
            if job.cancel.is_set():
                # Снятое из очереди не доходит до ComfyUI: ни /prompt, ни кадра.
                job.status = job.stage = "cancelled"
                job.finished = time.time()
                return
            job.status = "running"
            job.started = time.time()
        with self._lock:
            self._active = job.id

        def sink(event):
            with job.lock:
                job.events.append(event)
                job.stage = event.get("stage") or job.stage
                if event.get("stage") == "variant":
                    job.variant = event.get("data") or {}
                if event.get("stage") == "comfy_queue":
                    job.prompt_id = (event.get("data") or {}).get("prompt_id") or job.prompt_id

        try:
            result = jobs.run_job(job.request, emit=sink, cancel=job.cancel)
        except BaseException as e:                  # поток не должен умереть молча
            result = jobs.JobResult(error=f"{type(e).__name__}: {e}", code=1, stage="error")
        with job.lock:
            job.result = result
            job.finished = time.time()
            if job.cancel.is_set():
                # Отмена — исход, а не ошибка: текст провала не показываем.
                job.status = job.stage = "cancelled"
                job.error = ""
            else:
                job.error = result.error if not result.ok else ""
                if result.ok:
                    job.status = "described" if result.stage == "described" else "done"
                else:
                    job.status = "partial" if result.partial else "error"
                job.stage = result.stage or ("done" if result.ok else "error")
        with self._lock:
            if self._active == job.id:
                self._active = None

    # ------------------------------------------------------------ отмена
    def cancel(self, job_id):
        """Отменяет задание. Возвращает `(снято, замечание)`.

        В очереди — задание снимается и ComfyUI не зовётся вовсе. Работающее —
        `POST /interrupt` в ComfyUI (без него сервис продолжит сэмплить и держать
        GPU), но только если это задание уже отправило туда свой промпт: иначе
        прервался бы чужой граф. Недоделанный кадр не появляется, поэтому и в
        паспорт не попадает; `run.json` и готовые кадры не трогаются (AGENTS 0.15).

        Отмена — исход, а не ошибка: статус `cancelled`, текста провала нет.
        """
        job = self.get(job_id)
        if job is None:
            return False, "задания нет"
        with job.lock:
            if job.status not in ("queued", "running"):
                return False, "задание уже закончилось"
            queued = job.status == "queued"
            prompt_id = job.prompt_id
            job.cancel.set()
            job.status = job.stage = "cancelled"
            job.finished = time.time()
            # Событие будит открытые вкладки (push подписан на список событий):
            # без него страница узнала бы об отмене только сторожевым таймером.
            job.events.append({
                "seq": len(job.events) + 1,
                "t": round(max(0.0, job.finished - (job.started or job.created)), 2),
                "stage": "cancelled",
                "message": "снято из очереди" if queued else "прервано в ComfyUI",
                "data": {},
            })
        if queued and job.future is not None:
            # Задание ещё не взято исполнителем — снимаем до старта. Если его уже
            # взяли, `_run` увидит тот же флаг и выйдет, не дойдя до ComfyUI.
            job.future.cancel()
        return True, self._interrupt(job) if prompt_id else ""

    @staticmethod
    def comfy_url(job=None):
        """Адрес ComfyUI для отмены: тот, куда ушло задание, иначе из настроек."""
        return str((job.request.comfy if job is not None else None) or pp.COMFY).rstrip("/")

    def _interrupt(self, job):
        """`POST /interrupt`: отпускает GPU. Ошибка — замечание, а не провал."""
        try:
            pp.post(self.comfy_url(job) + "/interrupt", {}, timeout=10)
            return ""
        except Exception as e:
            return f"ComfyUI не принял /interrupt: {e}"

    # ------------------------------------------------------------ чтение
    def get(self, job_id):
        with self._lock:
            return self._jobs.get(str(job_id))

    def active_id(self):
        with self._lock:
            return self._active

    def latest(self):
        with self._lock:
            jid = self._active or self._latest
            return self._jobs.get(jid) if jid else None

    def queue_position(self, job):
        with self._lock:
            waiting = [j for j in self._order if j in self._jobs
                       and self._jobs[j].status == "queued"
                       and self._jobs[j].created <= job.created]
        try:
            return waiting.index(job.id) + 1
        except ValueError:
            return 0

    def snapshot(self, job_id=None):
        job = self.get(job_id) if job_id else self.latest()
        if job is None:
            return None
        with job.lock:
            events = list(job.events)
            result = job.result
            status, stage = job.status, job.stage
            variant = job.variant
            started, finished = job.started, job.finished
            error = job.error
        running = status in ("queued", "running")
        elapsed = (finished or time.time()) - (started or job.created) if running \
            else (result.elapsed if result and result.elapsed else
                  round((finished or time.time()) - (started or job.created), 1))
        frames = []
        for frame in (result.frames if result else []):
            frames.append({k: frame.get(k) for k in
                           ("file", "folder", "seed", "workflow", "cn_strength",
                            "style_weight", "denoise", "refined", "subject", "positive",
                            "negative", "steps", "cfg", "sampler", "scheduler",
                            "passes", "outcome")})
        desc = (result.description if result else None) or None
        return {
            "id": job.id,
            "source_tag": job.source_tag,
            "status": status,
            "stage": stage,
            "queue": self.queue_position(job) if status == "queued" else 0,
            "running": running,
            "variant": variant,
            "elapsed": round(max(0.0, elapsed), 1),
            "events": events[-40:],
            "error": error,
            "description": desc,
            "positive": result.positive if result else "",
            "negative": result.negative if result else "",
            "frames": frames,
            "partial": bool(result.partial) if result else False,
            "request": {
                "workflow": job.request.workflow,
                "scene": job.request.scene,
                "variants": job.request.variants,
                "manual": job.request.subject is not None,
                "seed": job.request.seed,
                "denoise": job.request.denoise,
                "cn_strength": job.request.cn_strength,
                "style_weight": job.request.style_weight,
                "overrides": job.request.overrides or {},
            },
        }

    def wait(self, job_id, timeout=120):
        """Для проверок: дождаться конца задания."""
        t0 = time.time()
        while time.time() - t0 < timeout:
            job = self.get(job_id)
            if job and job.status not in ("queued", "running"):
                return self.snapshot(job_id)
            time.sleep(0.05)
        raise TimeoutError(f"задание {job_id} не закончилось за {timeout} с")

    def shutdown(self):
        self._pool.shutdown(wait=False, cancel_futures=True)
