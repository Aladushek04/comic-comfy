"""Параметры запуска V2: сцена, число вариантов и вход в расширенные.

Сцена переписывает фон, сохраняя человека; на фото-ветке ControlNet глубины
держит исходную комнату, поэтому «пляж» проступает только в проёмах окон —
силу ControlNet и denoise правит расширенная панель.

Расширенные параметры живут в диалоге: рельс подобран по окну 900 px, и ещё
восемь полей в нём не помещаются. Здесь только вход в диалог — в строке
заголовка, чтобы карточка не выросла ни на строку.
"""
from nicegui import ui

from .. import theme


class RunControls:
    """Два поля, которые не зависят от сборки, и вход в расширенные."""

    def __init__(self, on_advanced=None):
        self._on_advanced = on_advanced
        self.advanced_button = None

    def build(self):
        with ui.element("section") as card:
            theme.mark(card, "card", "params")
            with ui.element("div") as head:
                theme.mark(head, "params-head")
                theme.mark(ui.label("Параметры запуска"), "title")
                if self._on_advanced is not None:
                    self.advanced_button = ui.button(
                        "Расширенные…", color=None, on_click=self._on_advanced)
                    theme.mark(self.advanced_button, "link-btn")
            with ui.element("div") as rows:
                theme.mark(rows, "params-rows")
                self.scene = ui.input("Сцена (необязательно)",
                                      placeholder="на пляже, закат, улица").props(
                    "outlined dense")
                self.variants = ui.number("Вариантов", value=1, min=1, max=8,
                                          precision=0).props("outlined dense")
        return self

    @property
    def values(self):
        """То, что уходит в `collect_form` рядом с фото и стилем."""
        return {"scene": (self.scene.value or "").strip(),
                "variants": int(self.variants.value or 1)}
