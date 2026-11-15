"""Очередь и состояние заданий. Никакого NiceGUI: только потоки и данные.

Один исполнитель на всё приложение: две вкладки не запускают две генерации,
вторая задача ждёт в очереди. Состояние живёт на сервере, поэтому обновление
вкладки показывает текущий этап, а не начинает задание заново.
"""
import io
import shutil
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


def _safe_stem(name):
    stem = Path(str(name or "photo")).stem
    keep = "".join(ch for ch in stem if ch.isalnum() or ch in "-_")[:40]
    return keep or "photo"


def save_png(folder, data, name="photo"):
    """Пересохраняет загруженное фото в PNG: без EXIF и посторонних байтов."""
    if len(data) > MAX_UPLOAD:
        raise UploadError(f"файл больше {MAX_UPLOAD // (1024 * 1024)} МБ")
    from PIL import Image, ImageOps
    try:
        with Image.open(io.BytesIO(data)) as im:
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
    status: str = "queued"          # queued | running | done | partial | error | described
    stage: str = "queued"
    variant: dict | None = None
    events: list = field(default_factory=list)
    result: jobs.JobResult | None = None
    error: str = ""
    lock: threading.Lock = field(default_factory=threading.Lock, repr=False, compare=False)


class JobService:
    """Один поток исполнения, много читателей состояния."""

    def __init__(self, workers=1, jobs_root=None):
        self.jobs_root = Path(jobs_root or (pp.ROOT / "out" / "jobs"))
        self._jobs = {}
        self._order = []
        self._lock = threading.Lock()
        self._active = None
        self._latest = None
        self._pool = ThreadPoolExecutor(max_workers=workers, thread_name_prefix="comic-job")

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
        self._pool.submit(self._run, job)
        return job

    def _run(self, job):
        with job.lock:
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

        try:
            result = jobs.run_job(job.request, emit=sink)
        except BaseException as e:                  # поток не должен умереть молча
            result = jobs.JobResult(error=f"{type(e).__name__}: {e}", code=1, stage="error")
        with job.lock:
            job.result = result
            job.finished = time.time()
            job.error = result.error if not result.ok else ""
            if result.ok:
                job.status = "described" if result.stage == "described" else "done"
            else:
                job.status = "partial" if result.partial else "error"
            job.stage = result.stage or ("done" if result.ok else "error")
        with self._lock:
            if self._active == job.id:
                self._active = None

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
                            "style_weight", "denoise", "refined", "subject")})
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
