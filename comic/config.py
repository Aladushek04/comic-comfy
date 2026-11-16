"""Настройки ComicPit-V2: один снимок путей и адресов.

Порядок разрешения — env сильнее файла, файл сильнее yaml, yaml сильнее раскладки
рядом с репозиторием:

  1. переменные окружения: COMIC_ROOT, COMFY_MODELS, COMFY_SHARED, COMFY_INPUT,
     COMFY_OUTPUT, COMFY_URL, LM_URL, LM_MODEL, LM_THINKING;
  2. `base_path` из extra_model_paths.yaml — этот файл всё равно правят при установке,
     чтобы ComfyUI видел общие модели;
  3. `comic_paths.json` в корне проекта (локальный, в git не попадает; образец —
     comic_paths.example.json) — input/output и закреплённая модель описания;
  4. раскладка рядом с репозиторием: ROOT\\models, а input/output — рядом с models.

Модели могут переехать на другой диск, а input/output остаться рядом с ComfyUI,
поэтому input/output ищутся отдельно: явный COMFY_INPUT/COMFY_OUTPUT, затем
COMFY_SHARED + подпапка, затем comic_paths.json, затем подпапка рядом с models.
Явные env сильнее файла, файл сильнее вывода из base_path.

`Config.load()` читает окружение один раз: снимок, а не живой `os.environ`. Поэтому
проверки передают свой `env` и свои файлы, а не подменяют глобальные имена.
"""
import json
import os
import re
from dataclasses import dataclass
from pathlib import Path

# Порт 8199 — свой стенд, а не Desktop на 8188: там другие модели и папка вывода.
DEFAULT_COMFY_URL = "http://127.0.0.1:8199"
YAML_NAME = "extra_model_paths.yaml"
PATHS_NAME = "comic_paths.json"

# Значения LM_THINKING, которые включают размышления (по умолчанию выключены:
# одно фото — 86 с и 1934 токена против 5.9 с и 97).
TRUTHY = ("on", "1", "yes", "true")


def project_root():
    """Корень репозитория от файла, а не от текущего каталога."""
    return Path(__file__).resolve().parent.parent


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


def _thinking(env):
    """Размышления VLM: только явное on/1/yes/true их включает."""
    return (env.get("LM_THINKING") or "off").strip().lower() in TRUTHY


@dataclass(frozen=True)
class Config:
    """Пути и адреса одного прогона.

    `lm_url` и `lm_model` пустые, когда сервис описаний не задан: подставлять
    адрес по умолчанию — дело вызывающего (`photo_prompt.LM`), потому что
    «пусто» и «явно 1234» ведут себя по-разному при выгрузке модели.
    """
    root: Path
    models: Path
    shared: Path
    input: Path
    output: Path
    comfy_url: str = DEFAULT_COMFY_URL
    lm_url: str = ""
    lm_model: str | None = None
    thinking: bool = False

    @classmethod
    def load(cls, root=None, *, env=None, yaml_path=None, paths_path=None):
        """Снимок настроек. env и пути можно подменить — этим пользуются проверки.

        root=None — COMIC_ROOT из окружения, иначе корень репозитория; от него же
        считаются extra_model_paths.yaml и comic_paths.json по умолчанию.
        """
        env = os.environ if env is None else env
        root = Path(root or env.get("COMIC_ROOT") or project_root())
        yaml_path = root / YAML_NAME if yaml_path is None else Path(yaml_path)
        paths_path = root / PATHS_NAME if paths_path is None else Path(paths_path)

        models = env.get("COMFY_MODELS")
        models_dir = Path(models) if models else (read_yaml_base_path(yaml_path)
                                                 or root / "models")

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

        return cls(
            root=root,
            models=models_dir,
            shared=shared_dir,
            input=pick("COMFY_INPUT", "input", "input"),
            output=pick("COMFY_OUTPUT", "output", "output"),
            comfy_url=env.get("COMFY_URL") or DEFAULT_COMFY_URL,
            lm_url=env.get("LM_URL") or cfg.get("lm_url") or "",
            lm_model=env.get("LM_MODEL") or cfg.get("lm_model") or None,
            thinking=_thinking(env),
        )
