"""Стили V2 — списком, справа вес стилевой LoRA из `meta` сборки.

Не выпадающий список и не чипы (визуальный контракт, пункт 5): выбранный пункт
виден целиком, а вес рядом — 0.9 у thepit, 0.6 у bimbo, 0.85 у GTA. Вес берётся
из `meta.style_weight`, а не из константы: пересобрали граф — подпись изменилась.
"""
from nicegui import ui

from .. import theme
from ..state import fmt_setting

DEFAULT = "07_photo_thepit"


class StylePicker:
    """Вертикальный список сборок; выбранная помечена классом состояния."""

    def __init__(self, styles, on_change):
        self.usable = [s for s in styles if not s["problems"]]
        self.broken = [s for s in styles if s["problems"]]
        self._on_change = on_change
        self.rows = {}
        self.value = self._default()

    def _default(self):
        for style in self.usable:
            if style["workflow"] == DEFAULT:
                return style["workflow"]
        return self.usable[0]["workflow"] if self.usable else None

    def build(self):
        with ui.element("section") as card:
            theme.mark(card, "card")
            theme.mark(ui.label("Стиль"), "title")
            if not self.usable:
                self._blocked(card)
            else:
                with ui.element("div") as box:
                    theme.mark(box, "styles")
                    for style in self.usable:
                        self.rows[style["workflow"]] = self._row(style)
                for style in self.broken:
                    theme.mark(ui.label(
                        f"{style['workflow']}: " + "; ".join(style["problems"])), "err")
        self.mark_selected()
        return self

    def _row(self, style):
        meta = style.get("meta") or {}
        with ui.element("button") as row:
            theme.mark(row, "style")
            row.props("type=button")
            with ui.element("span") as name:
                theme.mark(name, "style-name")
                ui.label(style["label"])
            weight = ui.label(fmt_setting(meta.get("style_weight")))
            theme.mark(weight, "style-weight", "num")
            row.on("click", lambda _, wf=style["workflow"]: self.select(wf))
        return row

    def _blocked(self, card):
        theme.mark(ui.label("Нет годных фото-сборок"), "err")
        theme.mark(ui.label("Соберите графы: python tools\\build_workflows.py — "
                            "и обновите страницу."), "meta")

    def select(self, workflow):
        if workflow == self.value:
            return
        self.value = workflow
        self.mark_selected()
        self._on_change(workflow)

    def mark_selected(self):
        """Выделение — класс состояния и aria-pressed, а не отдельная разметка."""
        for workflow, row in self.rows.items():
            on = workflow == self.value
            theme.state(row, "style-on", on)
            row.props(f"aria-pressed={'true' if on else 'false'}")
