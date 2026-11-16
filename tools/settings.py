"""Где что лежит. Ни одного пути конкретной машины в коде.

Порядок поиска:
  1. переменные окружения: COMIC_ROOT, COMFY_MODELS, COMFY_SHARED, COMFY_INPUT,
     COMFY_OUTPUT, COMFY_URL;
  2. `base_path` из extra_model_paths.yaml — этот файл всё равно правят при установке,
     чтобы ComfyUI видел общие модели;
  3. раскладка рядом с репозиторием (ROOT\\models, ROOT\\input, ROOT\\output).

В установке Desktop input/output лежат рядом с models — это последний запасной вариант,
а не правило: CLI обязан смотреть туда же, куда пишет ComfyUI.

Модели могут переехать на другой диск, а input/output остаться рядом с ComfyUI.
Поэтому input/output ищутся отдельно: явный COMFY_INPUT/COMFY_OUTPUT, затем
COMFY_SHARED + подпапка, затем `comic_paths.json` в корне проекта (локальный,
в git не попадает; образец — comic_paths.example.json), затем подпапка рядом
с каталогом моделей. Явные env сильнее файла, файл сильнее вывода из base_path.
"""
import json
import os
import re
import sys
from pathlib import Path

ROOT = Path(os.environ.get("COMIC_ROOT") or Path(__file__).resolve().parent.parent)
YAML = ROOT / "extra_model_paths.yaml"


def utf8_streams(streams=None):
    """Русский текст в stdout/stderr и при перенаправлении в файл или пайп.

    В консоли Python пишет через WriteConsoleW, а в пайп — в кодировке локали
    (на этой машине cp1252): print с кириллицей падал с UnicodeEncodeError.
    errors="replace" оставляет падение невозможным и в чужом терминале.
    """
    for stream in (sys.stdout, sys.stderr) if streams is None else streams:
        if not hasattr(stream, "reconfigure"):
            continue
        if (getattr(stream, "encoding", "") or "").lower() in ("utf-8", "utf8"):
            continue
        stream.reconfigure(encoding="utf-8", errors="replace")


def read_yaml_base_path(yaml_path):
    """base_path из extra_model_paths.yaml без зависимости от PyYAML."""
    path = Path(yaml_path)
    if not path.exists():
        return None
    for line in path.read_text(encoding="utf-8").splitlines():
        m = re.match(r"\s*base_path\s*:\s*(.+?)\s*$", line)
        if m:
            value = m.group(1).split(" #")[0].strip().strip('"').strip("'")
            return Path(value) if value else None
    return None


def read_paths(path):
    """input/output и закреплённая модель из локального comic_paths.json.

    Битый или нечитаемый файл считается отсутствующим: настройки импортируются
    всеми точками входа, и падать из-за опечатки в одном локальном файле нельзя.
    """
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    if not isinstance(data, dict):
        return {}
    return {key: str(data[key]) for key in ("input", "output", "lm_model", "lm_url")
            if isinstance(data.get(key), str) and data[key].strip()}


def resolve(env=None, yaml_path=None, paths_path=None):
    """Все пути разом. env — словарь (для проверок), yaml_path — подменить файл."""
    env = os.environ if env is None else env
    yaml_path = YAML if yaml_path is None else yaml_path
    root = Path(env.get("COMIC_ROOT") or ROOT)
    paths_path = root / "comic_paths.json" if paths_path is None else Path(paths_path)

    models = env.get("COMFY_MODELS")
    if models:
        models_dir = Path(models)
    else:
        models_dir = read_yaml_base_path(yaml_path) or (ROOT / "models")

    shared = env.get("COMFY_SHARED")
    shared_dir = Path(shared) if shared else models_dir.parent
    cfg = read_paths(paths_path)

    def pick(env_key, field, folder):
        """input/output независимо от models: env -> COMFY_SHARED -> файл -> рядом с models."""
        if env.get(env_key):
            return Path(env[env_key])
        if shared:
            return shared_dir / folder
        if cfg.get(field):
            return Path(cfg[field])
        return shared_dir / folder

    return {
        "root": root,
        "models": models_dir,
        "shared": shared_dir,
        "input": pick("COMFY_INPUT", "input", "input"),
        "output": pick("COMFY_OUTPUT", "output", "output"),
        "lm_model": env.get("LM_MODEL") or cfg.get("lm_model") or "",
        "lm": env.get("LM_URL") or cfg.get("lm_url") or "",
        "url": env.get("COMFY_URL") or "http://127.0.0.1:8199",
    }


_PATH = resolve()

ROOT = _PATH["root"]
MODELS_DIR = _PATH["models"]
SHARED_DIR = _PATH["shared"]
INPUT_DIR = _PATH["input"]
OUTPUT_ROOT = _PATH["output"]
OUTPUT_DIR = OUTPUT_ROOT / "comic"
WF_DIR = ROOT / "workflows"
COMFY_URL = _PATH["url"]
LM_URL = _PATH["lm"]
LM_MODEL = _PATH["lm_model"]
