"""Где что лежит. Ни одного пути конкретной машины в коде.

Порядок поиска:
  1. переменные окружения: COMIC_ROOT, COMFY_MODELS, COMFY_SHARED, COMFY_INPUT,
     COMFY_OUTPUT, COMFY_URL;
  2. `base_path` из extra_model_paths.yaml — этот файл всё равно правят при установке,
     чтобы ComfyUI видел общие модели;
  3. раскладка рядом с репозиторием (ROOT\\models, ROOT\\input, ROOT\\output).

Общая папка ComfyUI-Shared — родитель models; input/output лежат рядом с models.
Так устроена установка Desktop, и CLI обязан смотреть туда же, куда пишет ComfyUI.
"""
import os
import re
from pathlib import Path

ROOT = Path(os.environ.get("COMIC_ROOT") or Path(__file__).resolve().parent.parent)
YAML = ROOT / "extra_model_paths.yaml"


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


def resolve(env=None, yaml_path=None):
    """Все пути разом. env — словарь (для проверок), yaml_path — подменить файл."""
    env = os.environ if env is None else env
    yaml_path = YAML if yaml_path is None else yaml_path

    models = env.get("COMFY_MODELS")
    if models:
        models_dir = Path(models)
    else:
        models_dir = read_yaml_base_path(yaml_path) or (ROOT / "models")

    shared = env.get("COMFY_SHARED")
    shared_dir = Path(shared) if shared else models_dir.parent

    return {
        "root": Path(env.get("COMIC_ROOT") or ROOT),
        "models": models_dir,
        "shared": shared_dir,
        "input": Path(env.get("COMFY_INPUT") or shared_dir / "input"),
        "output": Path(env.get("COMFY_OUTPUT") or shared_dir / "output"),
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
