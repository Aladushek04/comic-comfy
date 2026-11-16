"""Параметры запуска V2: сцена и число вариантов.

Сцена переписывает фон, сохраняя человека; на фото-ветке ControlNet глубины
держит исходную комнату, поэтому «пляж» проступает только в проёмах окон —
силу ControlNet и denoise правит расширенная панель следующих фаз.
"""
from nicegui import ui

from .. import theme


class RunControls:
    """Два поля, которые не зависят от сборки: сцена и варианты."""

    def build(self):
        with ui.element("section") as card:
            theme.mark(card, "card", "params")
            theme.mark(ui.label("Параметры запуска"), "title")
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
