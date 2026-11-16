"""Где что лежит. Ни одного пути конкретной машины в коде.

Значения считает `comic.config.Config` — один снимок из env, extra_model_paths.yaml
и comic_paths.json; порядок поиска описан там же (env сильнее файла, файл сильнее
yaml, yaml сильнее раскладки рядом с репозиторием).

Этот модуль — тонкий адаптер на время переезда: `tools/*` и `webapp/*` импортируют
`settings` и получают прежние имена, а не собственные вычисления. Поэтому значения
сняты один раз при импорте: подмена `settings.COMFY_URL` в проверках по-прежнему
видна всему, что читает это имя.

Здесь же единственный `sys.path.insert` корня репозитория: без него пакет `comic`
не виден скрипту, запущенному из `tools\\`. COMIC_ROOT меняет настройки, но не место
пакета.
"""
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

from comic.config import (  # noqa: E402  — путь к пакету задаётся выше
    Config,
    read_paths,
    read_yaml_base_path,
)


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


def _as_paths(cfg):
    """Словарь прежнего вида: те же ключи, что отдавал resolve()."""
    return {
        "root": cfg.root,
        "models": cfg.models,
        "shared": cfg.shared,
        "input": cfg.input,
        "output": cfg.output,
        "lm_model": cfg.lm_model or "",
        "lm": cfg.lm_url,
        "url": cfg.comfy_url,
    }


def resolve(env=None, yaml_path=None, paths_path=None):
    """Все пути разом. env — словарь (для проверок), yaml_path — подменить файл."""
    return _as_paths(Config.load(env=env, yaml_path=yaml_path, paths_path=paths_path))


CONFIG = Config.load()
_PATH = _as_paths(CONFIG)

ROOT = CONFIG.root
YAML = ROOT / "extra_model_paths.yaml"
MODELS_DIR = CONFIG.models
SHARED_DIR = CONFIG.shared
INPUT_DIR = CONFIG.input
OUTPUT_ROOT = CONFIG.output
OUTPUT_DIR = OUTPUT_ROOT / "comic"
WF_DIR = ROOT / "workflows"
COMFY_URL = CONFIG.comfy_url
LM_URL = CONFIG.lm_url
LM_MODEL = CONFIG.lm_model or ""
