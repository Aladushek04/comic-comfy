"""Параметры кадра: проходы, cfg, сэмплер, ControlNet, вес, seed, исход.

Всё берётся из записи кадра (`snap["frames"][i]`), а не из формы: у сохранённого
кадра свои значения, и «по умолчанию» в форме не значит «так и было». Пока
кадра нет — показываем то, что просили у задания (`snap["request"]`), с честной
подписью «кадр не сделан».

`None` печатается как «по умолчанию», а не как 0: `denoise: null` значит «как в
сборке» (AGENTS 0.15), и подставлять ноль нельзя.
"""
from nicegui import ui

from .. import labels, theme
from ..state import fmt_setting

# Подписи строк — один список на все состояния: строки создаются один раз, а
# значения меняются, поэтому панель не пересобирается при выборе кадра.
ROWS = (
    ("workflow", "Сборка"),
    ("seed", "Seed"),
    ("passes", "Проходы"),
    ("cfg", "CFG"),
    ("sampler", "Сэмплер"),
    ("scheduler", "Планировщик"),
    ("denoise", "Denoise"),
    ("cn_strength", "ControlNet"),
    ("style_weight", "Вес стиля"),
    ("outcome", "Исход"),
    ("overrides", "Переопределено"),
)


def passes_text(frame):
    """Проходы кадра: «0.75/30 → 0.3/15». Нет записи — пусто, без догадок."""
    parts = []
    for item in frame.get("passes") or ():
        parts.append(f"{fmt_setting(item.get('denoise'))}/{fmt_setting(item.get('steps'))}")
    return " → ".join(parts)


def overrides_text(request):
    """Что переопределили в форме: поля и веса LoRA, по одной строке."""
    overrides = (request or {}).get("overrides") or {}
    parts = [f"{key}={value}" for key, value in (overrides.get("fields") or {}).items()]
    for role, change in (overrides.get("loras") or {}).items():
        bits = [str(change.get("name") or "").strip(),
                (f"вес {change['weight']}" if change.get("weight") is not None else "")]
        parts.append(f"LoRA {labels.ROLE_LABEL.get(role, role)}: "
                     + " ".join(x for x in bits if x))
    return ", ".join(parts)


def values_of(frame, request=None):
    """Значения строк: кадр главнее запроса, запрос — только пока кадра нет."""
    request = request or {}
    if not frame:
        return {
            "workflow": request.get("workflow") or "",
            "seed": fmt_setting(request.get("seed")),
            "denoise": fmt_setting(request.get("denoise")),
            "cn_strength": fmt_setting(request.get("cn_strength")),
            "style_weight": fmt_setting(request.get("style_weight")),
            "overrides": overrides_text(request),
        }
    return {
        "workflow": frame.get("workflow") or "",
        "seed": fmt_setting(frame.get("seed")),
        "passes": passes_text(frame),
        "cfg": fmt_setting(frame.get("cfg")),
        "sampler": frame.get("sampler") or "",
        "scheduler": frame.get("scheduler") or "",
        "denoise": fmt_setting(frame.get("denoise")),
        "cn_strength": fmt_setting(frame.get("cn_strength")),
        "style_weight": fmt_setting(frame.get("style_weight")),
        "outcome": frame.get("outcome") or "",
        "overrides": overrides_text(request),
    }


class ParamsPanel:
    """Строки «подпись → значение» для выбранного кадра."""

    def __init__(self):
        self.note = None
        self.rows = {}
        self.signature = None

    def build(self):
        self.note = ui.label("Параметры появятся вместе с кадром.")
        theme.mark(self.note, "meta")
        with ui.element("div") as box:
            theme.mark(box, "kv")
            for key, label in ROWS:
                with ui.element("div") as row:
                    theme.mark(row, "kv-row")
                    theme.mark(ui.label(label), "kv-key")
                    value = ui.label("")
                    theme.mark(value, "kv-value", "num")
                self.rows[key] = (row, value)
        return self

    def set(self, frame, request=None):
        """Кадр (или None) и запрос задания: значения строк и подпись."""
        if self.note is None:
            return
        values = values_of(frame, request)
        signature = (tuple(sorted(values.items())), bool(frame))
        if signature == self.signature:
            return
        self.signature = signature
        for key, (row, label) in self.rows.items():
            text = values.get(key) or ""
            label.set_text(text)
            row.set_visibility(bool(text))
        if frame:
            note = ""
        elif request:
            note = "Кадр не сделан: показано, что просили у задания."
        else:
            note = "Параметры появятся вместе с кадром."
        self.note.set_text(note)
        self.note.set_visibility(not frame)
