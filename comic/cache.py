"""Кэш описаний: формат, версия схемы и миграция старых записей.

Файл `out\\<фото>_<хэш>[__<сцена>].prompt.json` пишет и читает только этот модуль.
`photo_prompt` держит прежние имена (`cache_path`, `read_cache`, `write_cache`)
обёртками, поэтому вызывающий код и проверки не меняются, а ключи JSON знает
одно место.

Схема:
  1 — записи до появления поля `schema` (всё, что уже лежит на диске);
  2 — текущая: тот же набор полей плюс явная метка `schema`.

Версия схемы — про формат файла, а не про содержание описания. `scene_applied`
миграция не выдумывает: запись со сценой, но без этой метки, не считается
сценической, иначе сбой переписывания сцены закреплялся в кэше навсегда
(AGENTS 0.11.2). `describe_version` (ключ `describe`) — отдельная вещь: ею
помечено, каким промптом снято описание, и старые описания переснимаются сами.

Поле `raw` пишется с самого начала и не читается: это сырой ответ модели, он
нужен только человеку, который разбирает странное описание. Поэтому миграция его
переносит, а `read` не толкует.
"""
import json
import os
import tempfile
import time
from dataclasses import dataclass, field, replace
from pathlib import Path

SCHEMA = 2
LEGACY_SCHEMA = 1

# Причины отказа записи: photo_prompt печатает по ним подсказку и идёт описывать заново.
SCENE = "scene"                      # в файле чужая сцена (имя совпало, текст — нет)
SCENE_UNMARKED = "scene_unmarked"    # запись со сценой без метки «scene правдиво»
DESCRIBE = "describe"                # описание снято старым промптом
SHA1 = "sha1"                        # отпечаток файла другой

# Поля записи и ключи, под которыми они лежат в JSON. Имена в Python читаемее
# (`note`, `describe_version`), а ключи на диске — исторические: их видят пресеты,
# паспорт кадра и руки пользователя, поэтому они не меняются.
_KEYS = {
    "schema": "schema",
    "sha1": "sha1",
    "source": "source",
    "scene": "scene",
    "scene_applied": "scene_applied",
    "describe_version": "describe",
    "subject": "subject",
    "negative_extra": "negative_extra",
    "note": "notes",
    "addons": "addons",
    "addon_tags": "addon_tags",
    "addon_replaced": "addon_replaced",
    "addon_size": "addon_size",
    "raw": "raw",
}
_JSON_KEYS = frozenset(_KEYS.values())


@dataclass
class CacheRecord:
    """Одно описание вместе с тем, что делает его пригодным.

    `extra` — ключи, которых схема не знает: будущая версия или ручная правка
    могут положить своё поле, и терять его при чтении-записи нельзя.
    """
    schema: int = SCHEMA
    sha1: str = ""
    source: str = ""
    scene: str = ""
    scene_applied: bool = False
    describe_version: int = 1
    subject: str = ""
    negative_extra: str = ""
    note: str = ""
    addons: str = ""
    addon_tags: list = field(default_factory=list)
    addon_replaced: list = field(default_factory=list)
    addon_size: str = ""
    raw: str = ""
    extra: dict = field(default_factory=dict)


def _text(value):
    return "" if value is None else str(value)


def _number(value, default):
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _strings(value):
    """Список строк: в старых записях мог оказаться не список, а строка."""
    if isinstance(value, str):
        return [value] if value.strip() else []
    if not isinstance(value, (list, tuple)):
        return []
    return [str(x) for x in value]


def migrate(data):
    """Старый dict (с диска или из `describe`) -> CacheRecord без потерь.

    Отсутствие `schema` — это не «новая запись», а первая схема: файлы на диске
    писались до её появления. Поэтому schema=1, а не SCHEMA, и `scene_applied`
    берётся только из файла — иначе старый сбой сцены выглядел бы исправленным.
    """
    if isinstance(data, CacheRecord):
        return replace(data, addon_tags=list(data.addon_tags),
                       addon_replaced=list(data.addon_replaced), extra=dict(data.extra))
    payload = dict(data or {})
    known = {name: payload[key] for name, key in _KEYS.items() if key in payload}
    return CacheRecord(
        schema=_number(known.get("schema"), LEGACY_SCHEMA),
        sha1=_text(known.get("sha1")),
        source=_text(known.get("source")),
        scene=_text(known.get("scene")),
        scene_applied=bool(known.get("scene_applied", False)),
        describe_version=_number(known.get("describe_version"), 1),
        subject=_text(known.get("subject")),
        negative_extra=_text(known.get("negative_extra")),
        note=_text(known.get("note")),
        addons=_text(known.get("addons")),
        addon_tags=_strings(known.get("addon_tags")),
        addon_replaced=_strings(known.get("addon_replaced")),
        addon_size=_text(known.get("addon_size")),
        raw=_text(known.get("raw")),
        extra={k: v for k, v in payload.items() if k not in _JSON_KEYS},
    )


def to_dict(rec):
    """CacheRecord -> тот же JSON, что лежал на диске раньше.

    `schema` отдаётся как есть: у мигрированной старой записи он 1, и это видно
    вызывающему. Текущую схему ставит только `write`.
    """
    data = dict(rec.extra)
    data.update({key: getattr(rec, name) for name, key in _KEYS.items()})
    return data


def from_desc(desc, *, sha1="", source="", scene="", describe_version=1,
              scene_applied=True):
    """Описание -> запись кэша: поля кэша ставятся поверх описания.

    `scene_applied=True` по умолчанию — так пишет конвейер: он кладёт под ключ
    сцены только текст, который сцена действительно переписала (AGENTS 0.11.2).
    """
    rec = migrate(desc)
    rec.sha1 = _text(sha1)
    rec.source = _text(source)
    rec.scene = _text(scene)
    rec.scene_applied = bool(scene_applied)
    rec.describe_version = _number(describe_version, 1)
    return rec


def check(rec, *, scene="", describe_version=1, sha1=None):
    """Почему запись не годится ("" — годится).

    `sha1` — вызываемый, а не значение: отпечаток считается хэшем файла, и зря
    хэшировать фото на каждое чтение кэша незачем. Запись без отпечатка (ручная
    правка) отдаётся: сверять нечего.
    """
    if rec.scene != _text(scene):
        # Имя файла могло совпасть, но описание — от другой сцены.
        return SCENE
    if scene and not rec.scene_applied:
        return SCENE_UNMARKED
    if rec.describe_version < _number(describe_version, 1):
        return DESCRIBE
    if rec.sha1 and sha1 is not None and rec.sha1 != sha1():
        return SHA1
    return ""


def read(path):
    """Запись с диска или None: нет файла, битый JSON, не словарь."""
    p = Path(path)
    if not p.exists():
        return None
    try:
        payload = read_json(p)
    except Exception:
        return None
    if not isinstance(payload, dict):
        return None
    return migrate(payload)


def write(path, rec):
    """Пишет запись атомарно и всегда текущей схемой.

    Перезапись старой записи (её прочитали, поправили теги, сохранили) поднимает
    `schema` до SCHEMA: на диске не должно остаться файла, который выглядит
    старым, хотя его писал новый код.
    """
    rec = migrate(rec)
    data = to_dict(rec)
    data["schema"] = SCHEMA
    return write_json(path, data)


def read_json(path, retries=5):
    """JSON с диска; на Windows короткий повтор.

    os.replace делает старый файл на миг недоступным для чтения: без повтора
    случайный читатель в этот момент получил бы PermissionError.
    """
    for attempt in range(retries):
        try:
            return json.loads(Path(path).read_text(encoding="utf-8"))
        except PermissionError:
            if attempt == retries - 1:
                raise
            time.sleep(0.02)


def write_json(path, data, indent=2):
    """Пишет JSON целиком или не трогает файл: обрыв не оставляет половину.

    Временный файл лежит рядом с целевым: os.replace атомарен только в пределах
    одной файловой системы. fsync до замены — чтобы после сбоя питания на диске
    остался прежний файл, а не пустой новый.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(data, ensure_ascii=False, indent=indent)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=path.name + ".", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(text)
            f.flush()
            os.fsync(f.fileno())
        # На Windows replace не проходит, пока файл открыт читателем: короткая
        # пауза и повтор дешевле, чем сорванная запись паспорта из-за вкладки.
        for attempt in range(10):
            try:
                os.replace(tmp, path)
                break
            except PermissionError:
                if attempt == 9:
                    raise
                time.sleep(0.02)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
    return path
