"""Паспорт прогона: `out\\<фото>_<хэш>\\run.json`, схема 2.

Формат знает только этот модуль: `photo_prompt.save_run` собирает запись и зовёт
`write`, поэтому ключи JSON и правила миграции живут в одном месте — как у кэша
описаний (`comic.cache`).

Схема:
  1 — записи, которые писал прежний код: без `schema`, а у самых старых нет ни
      `steps`, ни `passes`, ни `outcome`;
  2 — текущая: та же запись плюс явная метка `schema`, оба прохода и исход кадра.

Две вещи, ради которых формат описан отдельно:

  * `denoise: null` значит «как в сборке», а не 0.0. Миграция не подставляет
    числа: ноль шагов и 0.0 denoise выглядели бы настоящими значениями
    (AGENTS 0.15).
  * старый паспорт не переписывается. `read` отдаёт запись схемы 2, но на диск
    уходят только поля, которые действительно изменили, — поэтому в записи лежит
    `raw`, ровно тот dict, что прочитан с диска. Запись, созданная до P0, не
    получает ни `outcome`, ни восстановленных `passes`: иначе паспорт начал бы
    утверждать то, чего не знает.

`read` не глотает ошибку, как `comic.cache.read`: нечитаемый `run.json` — это
отказ (`RunError`), а не пустая история. Пустой паспорт молча теряет записи о
прежних кадрах (AGENTS 0.15), поэтому `save_run` переводит `RunError` в
`PipelineError` и кадр не пишется.
"""
import json
from dataclasses import dataclass, field, replace
from pathlib import Path

from . import cache
from .config import project_root

SCHEMA = 2
RUN_NAME = "run.json"

# Исход кадра: набор закрытый. `unknown` — по умолчанию: принял кадр человек, и
# до его решения паспорт не имеет права утверждать `accepted`.
UNKNOWN = "unknown"
OUTCOMES = ("unknown", "accepted", "rejected", "refined", "cancelled")

# Поля записи и ключи, под которыми они лежат в JSON. Имена в Python читаемее,
# а ключи на диске — исторические: их видят паспорт кадра и руки пользователя,
# поэтому они не меняются.
_KEYS = {
    "schema": "schema",
    "workflow": "workflow",
    "seed": "seed",
    "denoise": "denoise",
    "file": "file",
    "cn_strength": "cn_strength",
    "style_weight": "style_weight",
    "overrides": "overrides",
    "refined": "refined",
    "positive": "positive",
    "negative": "negative",
    "subject": "subject",
    "negative_extra": "negative_extra",
    "steps": "steps",
    "cfg": "cfg",
    "sampler": "sampler",
    "scheduler": "scheduler",
    "passes": "passes",
    "outcome": "outcome",
}
_JSON_KEYS = frozenset(_KEYS.values())

# Ключи прохода: denoise и steps пишет граф всегда, cfg и сэмплер — если их дал
# сам проход (spec 7.2). Остальное сохраняется как есть.
_PASS_KEYS = ("denoise", "steps", "cfg", "sampler")


class RunError(RuntimeError):
    """Паспорт не читается: кадр писать нельзя, иначе история потеряется."""


@dataclass
class PassRecord:
    """Один проход сэмплера: у 07 их два (0.75/30 и 0.3/15), у 06 — один.

    По одному `denoise` не видно, что кадр сделан двумя сэмплерами, а второй
    проход у каждой сборки свой: у 08 это 0.3/16, у bimbo первый 0.7/30.
    `None` вместо нулей: не записанное значение не должно выглядеть настоящим.
    """
    denoise: float | None = None
    steps: int | None = None
    cfg: float | None = None
    sampler: str | None = None
    extra: dict = field(default_factory=dict)


@dataclass
class RunRecord:
    """Один кадр в паспорте.

    `extra` — ключи, которых схема не знает: будущая версия или ручная правка
    могут положить своё поле, и терять его при чтении-записи нельзя.
    `raw` — то, что лежало на диске (`None` у новой записи), `origin` — значения
    полей сразу после миграции. По ним `to_dict` отличает «поле не трогали» от
    «поле изменили»: старое остаётся на диске как было.
    """
    schema: int = SCHEMA
    workflow: str = ""
    seed: int | None = None
    denoise: float | None = None
    file: str = ""
    cn_strength: float | None = None
    style_weight: float | None = None
    overrides: dict | None = None
    refined: bool = False
    positive: str = ""
    negative: str = ""
    subject: str = ""
    negative_extra: str = ""
    steps: int | None = None
    cfg: float | None = None
    sampler: str = ""
    scheduler: str = ""
    passes: list | None = None
    outcome: str = UNKNOWN
    estimated: bool = False
    extra: dict = field(default_factory=dict)
    raw: dict | None = field(default=None, repr=False, compare=False)
    origin: dict = field(default_factory=dict, repr=False, compare=False)


def _text(value):
    return "" if value is None else str(value)


def _outcome(value):
    """Метка исхода из файла: незнакомая не ломает чтение, но и не проходит.

    Ручная правка паспорта не должна ронять галерею, поэтому неизвестное
    значение читается как `unknown`. Сам текст остаётся в `raw` и на диске.
    """
    text = _text(value)
    return text if text in OUTCOMES else UNKNOWN


def _pass(item):
    """Один проход из JSON или из готовой записи."""
    if isinstance(item, PassRecord):
        return replace(item, extra=dict(item.extra))
    if not isinstance(item, dict):
        return None
    return PassRecord(
        denoise=item.get("denoise"), steps=item.get("steps"),
        cfg=item.get("cfg"), sampler=item.get("sampler"),
        extra={k: v for k, v in item.items() if k not in _PASS_KEYS},
    )


def passes_of(value):
    """Список проходов из настроек кадра (`settings["passes"]`). `None` — как есть.

    Настройки приходят из собранного графа списком dict-ов, а запись хранит
    `PassRecord`: перевод в одном месте, чтобы `save_run` не собирал формат сам.
    """
    if not isinstance(value, (list, tuple)):
        return None
    items = [p for p in (_pass(item) for item in value) if p is not None]
    return items or None


def _pass_dict(rec):
    """Проход -> JSON. Незаписанное не появляется: `cfg: null` — выдумка."""
    data = dict(rec.extra)
    for name in _PASS_KEYS:
        value = getattr(rec, name)
        if value is not None:
            data[name] = value
    return data


def _value(name, value):
    """Поле записи -> значение для JSON."""
    if name == "passes":
        return None if value is None else [_pass_dict(p) for p in value]
    return value


def workflow_from_file(name, wf_dir=None):
    """Сборка по имени кадра: SaveImage пишет префикс `<сборка>_NNNNN_.png`.

    У старых паспортов поля `workflow` нет, но имя файла его помнит — по нему и
    восстанавливаем. Ищем самое длинное совпадение: `07_photo_jp` не должен
    проиграть более короткому имени.
    """
    stem = Path(str(name or "")).name
    best = ""
    for w in build_names(wf_dir):
        if stem.startswith(w) and len(w) > len(best):
            best = w
    return best


def build_names(wf_dir=None):
    """Имена сборок: файлы `<сборка>.api.json` в папке графов."""
    try:
        return [p.name[:-len(".api.json")]
                for p in Path(_wf_dir(wf_dir)).glob("*.api.json")]
    except OSError:
        return []


def _wf_dir(wf_dir):
    return Path(wf_dir) if wf_dir else project_root() / "workflows"


def migrate(data):
    """Старый dict (с диска) -> RunRecord без выдуманных значений.

    `denoise: null` остаётся null, а незаписанные `steps`/`cfg`/`sampler` —
    `None`: миграция не подставляет числа, иначе 0.0 и 0 шагов выглядели бы
    настоящими значениями (AGENTS 0.15).
    """
    if isinstance(data, RunRecord):
        return replace(data, passes=passes_of(data.passes), extra=dict(data.extra),
                       origin=dict(data.origin), raw=dict(data.raw) if data.raw else None)
    payload = dict(data or {})
    known = {name: payload[key] for name, key in _KEYS.items() if key in payload}
    rec = RunRecord(
        schema=SCHEMA,
        workflow=_text(known.get("workflow")),
        seed=known.get("seed"),
        denoise=known.get("denoise"),
        file=_text(known.get("file")),
        cn_strength=known.get("cn_strength"),
        style_weight=known.get("style_weight"),
        overrides=known.get("overrides"),
        refined=bool(known.get("refined", False)),
        positive=_text(known.get("positive")),
        negative=_text(known.get("negative")),
        subject=_text(known.get("subject")),
        negative_extra=_text(known.get("negative_extra")),
        steps=known.get("steps"),
        cfg=known.get("cfg"),
        sampler=_text(known.get("sampler")),
        scheduler=_text(known.get("scheduler")),
        passes=passes_of(known.get("passes")),
        outcome=_outcome(known.get("outcome")),
        extra={k: v for k, v in payload.items() if k not in _JSON_KEYS},
        raw=payload,
    )
    rec.origin = {name: getattr(rec, name) for name in _KEYS}
    return rec


def to_dict(rec):
    """RunRecord -> JSON паспорта.

    У новой записи пишутся все поля схемы 2. У записи с диска — только те, что
    отличаются от прочитанного: так старый паспорт не получает ни `schema`, ни
    `outcome`, ни восстановленных `passes`, а исправленное поле (например
    сборка, найденная по имени кадра) сохраняется.
    """
    if rec.raw is None:
        data = dict(rec.extra)
        data.update({key: _value(name, getattr(rec, name)) for name, key in _KEYS.items()})
        return data
    data = dict(rec.raw)
    for name, key in _KEYS.items():
        value = getattr(rec, name)
        if name in rec.origin and value == rec.origin[name]:
            continue
        data[key] = _value(name, value)
    for key, value in rec.extra.items():
        if data.get(key) != value:
            data[key] = value
    return data


def read_book(folder, *, read_json=None):
    """Записи паспорта и его верхние ключи: чужие ключи не теряются при записи.

    Нет файла — пустая история и пустая шапка. Файл есть, но не читается —
    `RunError`: начать историю с нуля значит потерять прежние кадры.
    """
    read_json = cache.read_json if read_json is None else read_json
    path = Path(folder) / RUN_NAME
    if not path.exists():
        return [], {}
    try:
        book = read_json(path)
    except Exception as e:
        raise RunError(f"паспорт {path} не читается ({e})") from e
    if not isinstance(book, dict) or not isinstance(book.get("runs"), list):
        raise RunError(f"паспорт {path} не читается (нет списка runs)")
    records = []
    for item in book["runs"]:
        if not isinstance(item, dict):
            raise RunError(f"паспорт {path} не читается (запись не словарь)")
        records.append(migrate(item))
    head = {k: v for k, v in book.items() if k not in ("runs", "workflows")}
    return records, head


def read(folder, *, read_json=None):
    """Записи паспорта в папке кадра: список RunRecord (пусто — файла нет)."""
    return read_book(folder, read_json=read_json)[0]


def write(folder, records, *, head=None, write_json=None):
    """Пишет паспорт атомарно: записи, список сборок и чужие верхние ключи.

    Общее поле `workflow` старого паспорта убирается: сборка живёт в каждом
    кадре, иначе один стиль подписывает кадры другого (AGENTS 0.7).
    """
    write_json = cache.write_json if write_json is None else write_json
    records = list(records)
    for rec in records:
        if rec.outcome not in OUTCOMES:
            raise ValueError(f"метка исхода {rec.outcome!r} не из списка "
                             f"{', '.join(OUTCOMES)}")
    book = dict(head or {})
    book.pop("workflow", None)
    book["runs"] = [to_dict(rec) for rec in records]
    book["workflows"] = sorted({rec.workflow for rec in records if rec.workflow})
    return write_json(Path(folder) / RUN_NAME, book)
