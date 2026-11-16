"""Расширенные параметры кадра: диалог, а не рельс.

Рельс подобран по окну 900 px (спецификация, раздел 2), поэтому переопределения
живут в диалоге: три группы — «Генерация», «Стиль», «Прочее». Поля не хранят
своих значений по умолчанию: подсказка берётся из `meta.fields[].default` и
`meta.style_weight` выбранной сборки, а пустое поле значит «как в графе».

Ноль остаётся нулём: `denoise 0` — это значение, а не «пусто» (решение 7.7).
В Python `0 == ""` — ложь, поэтому ноль не путается с пустым полем; а вот `or`
в форме превратил бы его в значение по умолчанию, и такой путь здесь закрыт.

Отсюда в `collect_form` уходит тот же плоский словарь, что у старой страницы:
`steps` и `cfg` — поля графа, они едут в `values["fields"]`, остальное — ключами.
Поэтому проверки формы остаются одни и те же, а переопределения проходят через
`comic/guard.py` на общем пути сборки промпта.

«Вариантов» связан с полем рельса двусторонней привязкой: два независимых поля
показывали бы два разных числа.
"""
from nicegui import ui

from .. import theme

# Группы и порядок полей — как в плане P5c: генерация, стиль, прочее.
GROUPS = (
    ("Генерация", ("steps", "cfg", "denoise", "cn_strength")),
    ("Стиль", ("style_weight",)),
    ("Прочее", ("seed", "variants", "negative_extra")),
)

LABELS = {
    "steps": "Шаги",
    "cfg": "CFG",
    "denoise": "Denoise",
    "cn_strength": "Сила ControlNet",
    "style_weight": "Вес стилевой LoRA",
    "seed": "Сид",
    "variants": "Вариантов",
    "negative_extra": "Дополнительный негатив",
}

# Поля графа: `collect_form` ждёт их вложенными в `values["fields"]`, потому что
# там же живут их привязки из meta (узел, вход, тип).
GRAPH_FIELDS = ("steps", "cfg")

# Текстовые поля: у них пустое значение — пустая строка, а не None.
TEXT_FIELDS = ("negative_extra",)

# Подпись диалога: одно правило на все поля, чтобы не повторять его восемь раз.
HINT = "Пусто — значение из графа. Ноль остаётся нулём."


def defaults(meta):
    """Значения сборки по полям диалога: из meta, а не из кода.

    `style_weight` лежит не в `fields`, а рядом (это вес стилевой LoRA), поэтому
    берётся из `meta.style_weight`. У «Вариантов» значения в графе нет — это
    свойство задания, и по умолчанию их один.
    """
    meta = meta or {}
    fields = meta.get("fields") or {}
    out = {}
    for _, keys in GROUPS:
        for key in keys:
            if key == "style_weight":
                out[key] = meta.get("style_weight")
            elif key == "variants":
                out[key] = 1
            elif key in TEXT_FIELDS:
                out[key] = None
            else:
                out[key] = (fields.get(key) or {}).get("default")
    return out


def flat(entered):
    """Введённые значения диалога -> фрагмент словаря `collect_form`.

    Пустое поле не попадает вовсе: тогда `collect_form` берёт значение сборки.
    Ноль попадает: `0` — это значение, а не «пусто».
    """
    out, fields = {}, {}
    for key, value in (entered or {}).items():
        if value is None:
            continue
        if isinstance(value, str) and not value.strip():
            continue
        if key in GRAPH_FIELDS:
            fields[key] = value
        else:
            out[key] = value
    if fields:
        out["fields"] = fields
    return out


def placeholder(key, default):
    """Подсказка поля: значение сборки или «как в графе», если его нет.

    У сида подсказка другая: пустое поле даёт случайный сид на каждый вариант
    (`jobs._seed`), а число из графа — это значение графа, а не то, что уйдёт
    в кадр. Обещать «как в графе · 12345» значило бы соврать.
    """
    if key == "seed":
        return "пусто — случайный"
    if default is None or default == "":
        return "как в графе"
    if isinstance(default, float) and default.is_integer():
        default = int(default)
    return f"как в графе · {default}"


class AdvancedDialog:
    """Диалог с группами полей; значения читаются на «Старт», а не по клику."""

    def __init__(self, variants=None):
        self.variants = variants        # поле рельса: одно число на две подписи
        self.dialog = None
        self.fields = {}

    def build(self):
        with ui.dialog() as dialog, ui.element("div") as box:
            theme.mark(box, "card", "dialog")
            theme.mark(ui.label("Расширенные параметры"), "title")
            theme.mark(ui.label(HINT), "meta")
            for title, keys in GROUPS:
                with ui.element("div") as group:
                    theme.mark(group, "group")
                    theme.mark(ui.label(title), "prompt-label")
                    with ui.element("div") as grid:
                        theme.mark(grid, "group-fields")
                        for key in keys:
                            self.fields[key] = self._field(key)
            with ui.element("div") as actions:
                theme.mark(actions, "dialog-actions")
                reset = ui.button("Сбросить", color=None, on_click=self.reset)
                theme.mark(reset, "btn", "btn-ghost")
                done = ui.button("Готово", color=None, on_click=dialog.close)
                theme.mark(done, "btn", "btn-fill")
        self.dialog = dialog
        return self

    def _field(self, key):
        """Одно поле диалога. Значение пустое, подсказка — из meta."""
        if key in TEXT_FIELDS:
            return ui.input(LABELS[key]).props("outlined dense")
        element = ui.number(LABELS[key]).props("outlined dense")
        if key == "variants" and self.variants is not None:
            # Двусторонняя привязка: число вариантов в рельсе и здесь — одно.
            element.bind_value(self.variants, "value")
        elif key == "variants":
            element.props("min=1 max=8 precision=0")
        elif key == "seed":
            element.props("min=0 precision=0")
        elif key == "steps":
            element.props("precision=0")
        else:
            element.props("step=0.05")
        return element

    def open(self, meta=None):
        """Открыть с подсказками текущей сборки: дефолты меняются вместе со стилем."""
        hints = defaults(meta or {})
        for key, element in self.fields.items():
            if key == "variants" and self.variants is not None:
                continue
            element.props(f'placeholder="{placeholder(key, hints.get(key))}"')
        if self.dialog is not None:
            self.dialog.open()

    def reset(self):
        """Все поля — пустые, то есть «как в графе»."""
        for key, element in self.fields.items():
            element.set_value("" if key in TEXT_FIELDS else None)

    @property
    def values(self):
        """Фрагмент словаря `collect_form`: пустые поля не попадают вовсе."""
        return flat({key: element.value for key, element in self.fields.items()})
