"""Headless ComfyUI по требованию: поднять после выгрузки VLM и погасить.

Зачем: на 16 ГБ VLM и SDXL не живут вместе. Страница не требует, чтобы
ComfyUI был запущен заранее: задание сначала описывает фото, выгружает
модель, и только потом поднимает сервер. Адрес, порт и команда запуска —
из локального `comfy_launch.json` (в git не попадает) или из env
`COMFY_MAIN`, `COMFY_PYTHON`, `COMFY_LAUNCH_CONFIG`.

Ничего не запускается само по себе: только когда пользователь нажал
«Старт» и только после фазы описания.
"""
import atexit
import json
import os
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse

import settings

_ACTIVE = []


def config_path():
    return Path(os.environ.get("COMFY_LAUNCH_CONFIG") or settings.ROOT / "comfy_launch.json")


def load_config():
    path = config_path()
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def _python_for(main, configured):
    if configured:
        return Path(configured)
    for name in (".venv/Scripts/python.exe", ".venv/bin/python", "python_embeded/python.exe"):
        candidate = main.parent / name
        if candidate.exists():
            return candidate
    return None


def plan():
    """Команда запуска и адрес или None, если не настроено.

    Машинные пути живут только в локальном конфиге/env; в коде их нет.
    """
    cfg = load_config()
    main = os.environ.get("COMFY_MAIN") or cfg.get("main")
    if not main:
        return None
    main = Path(main)
    if not main.exists():
        return None
    python = _python_for(main, os.environ.get("COMFY_PYTHON") or cfg.get("python"))
    if python is None or not Path(python).exists():
        return None
    url = os.environ.get("COMFY_URL") or settings.COMFY_URL
    parsed = urlparse(url)
    port = int(cfg.get("port") or parsed.port or 8199)
    argv = [str(python), str(main), "--port", str(port), "--listen", "127.0.0.1",
            "--disable-auto-launch", "--dont-print-server"]
    yaml = settings.ROOT / "extra_model_paths.yaml"
    if yaml.exists():
        argv += ["--extra-model-paths-config", str(yaml)]
    argv += ["--output-directory", str(settings.OUTPUT_ROOT),
             "--input-directory", str(settings.INPUT_DIR)]
    stop_after = os.environ.get("COMFY_STOP_AFTER")
    if stop_after is None:
        stop_after = cfg.get("stop_after", True)
    return {
        "argv": argv,
        "cwd": str(main.parent),
        "url": f"http://127.0.0.1:{port}",
        "port": port,
        "stop_after": bool(stop_after),
        "timeout": float(cfg.get("startup_timeout") or 180),
        "log": settings.ROOT / "out" / "logs" / "comfy_server.log",
    }


def available():
    return plan() is not None


def _spawn(cfg):
    cfg["log"].parent.mkdir(parents=True, exist_ok=True)
    handle = open(cfg["log"], "ab")
    flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
    try:
        proc = subprocess.Popen(cfg["argv"], cwd=cfg["cwd"], stdout=handle,
                                stderr=subprocess.STDOUT, creationflags=flags)
    finally:
        handle.close()
    return proc


@dataclass
class Handle:
    url: str
    proc: subprocess.Popen
    stop_after: bool = True


def _tail(cfg, lines=8):
    try:
        text = cfg["log"].read_text(encoding="utf-8", errors="replace").splitlines()
    except Exception:
        return ""
    return "\n".join(text[-lines:])


def ensure(emit=None):
    """Поднимает ComfyUI, если он нужен и разрешён. None — уже работает или не настроено."""
    import photo_prompt as pp

    url, error = pp.comfy_state()
    if not error:
        return None
    cfg = plan()
    if cfg is None:
        return None
    explicit = os.environ.get("COMFY_URL")
    if explicit:
        parsed = urlparse(explicit)
        if parsed.hostname not in ("127.0.0.1", "localhost", None) \
                or (parsed.port or 80) != cfg["port"]:
            # Явно назван другой адрес: поднимать свой сервер вместо него нельзя.
            return None

    if emit:
        emit("comfy_launch", f"поднимаю headless ComfyUI на {cfg['url']}", url=cfg["url"])
    proc = _spawn(cfg)
    deadline = time.time() + cfg["timeout"]
    while time.time() < deadline:
        if pp.comfy_probe(cfg["url"]):
            if emit:
                emit("comfy", f"ComfyUI поднят: {cfg['url']}", url=cfg["url"])
            handle = Handle(url=cfg["url"], proc=proc, stop_after=cfg["stop_after"])
            _ACTIVE.append(handle)
            return handle
        if proc.poll() is not None:
            break
        time.sleep(1)
    stop(Handle(url=cfg["url"], proc=proc, stop_after=True))
    tail = _tail(cfg)
    raise pp.PipelineError(
        "headless ComfyUI не поднялся за "
        f"{int(cfg['timeout'])} с.\n{tail[-1200:]}" if tail else
        f"headless ComfyUI не поднялся за {int(cfg['timeout'])} с")


def _terminate(handle):
    if handle is None or handle.proc is None:
        return
    if handle.proc.poll() is None:
        handle.proc.terminate()
        try:
            handle.proc.wait(timeout=15)
        except Exception:
            handle.proc.kill()
    if handle in _ACTIVE:
        _ACTIVE.remove(handle)


def stop(handle, emit=None):
    """Гасит только тот сервер, который подняли мы, и только если так настроено."""
    if handle is None or not handle.stop_after:
        return
    _terminate(handle)
    if emit:
        emit("comfy", "ComfyUI остановлен — память свободна", url=handle.url)


def _stop_all():
    """Выход из приложения (в том числе Ctrl+C) не должен оставлять сервер в памяти."""
    for handle in list(_ACTIVE):
        try:
            _terminate(handle)
        except Exception:
            pass


atexit.register(_stop_all)
