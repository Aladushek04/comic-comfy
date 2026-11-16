"""Верхняя панель V2: связь, ComfyUI, описание, очередь и переключатель темы.

Бейдж — нейтральная пилюля с точкой состояния: цвет несёт только точка. Так
зелёная рамка не читается как нажатая кнопка, а состояние видно в обеих темах.

Проверка сервисов меняет только текст и точку (`set`), списки — `set_options`:
панель не перерисовывается, поэтому ввод человека не пропадает (грабли 0.14 №6).
"""
from nicegui import ui

from .. import theme


class TopBar:
    """Полоса сверху: имя страницы, бейджи и две кнопки."""

    TONES = (("badge-ok", "ok"), ("badge-warn", "warn"), ("badge-err", "err"))

    def __init__(self, on_health, on_theme):
        self._on_health = on_health
        self._on_theme = on_theme
        self.badges = {}
        self.texts = {}
        self.theme_button = None
        self.theme_tip = None

    def build(self, dark):
        with ui.element("header") as bar:
            theme.mark(bar, "topbar")
            with ui.element("div") as brand:
                theme.mark(brand, "brand")
                theme.mark(ui.label("comic-comfy"), "brand-title")
                theme.mark(ui.label("V2 · фото → промпт → кадры"), "meta")
            with ui.element("div") as right:
                theme.mark(right, "badges")
                self._badge("link", "связь: есть")
                self._badge("comfy", "ComfyUI: проверяю")
                self._badge("lm", "Описание: проверяю")
                self._badge("queue", "очередь: свободна")
                check = ui.button("Проверить сервисы", on_click=self._on_health,
                                  color=None)
                theme.mark(check, "btn", "btn-ghost")
                self.theme_button = ui.button(icon="light_mode",
                                              on_click=self._on_theme, color=None)
                theme.mark(self.theme_button, "icon")
                with self.theme_button:
                    self.theme_tip = ui.tooltip("Тёмная тема")
        self.set_dark(dark)
        return self

    def _badge(self, key, text):
        """Пилюля с точкой: класс состояния — на пилюле, текст — на своей подписи."""
        with ui.element("div") as badge:
            theme.mark(badge, "badge")
            theme.mark(ui.element("span"), "dot")
            label = ui.label(text)
            theme.mark(label, "badge-text")
        self.badges[key] = badge
        self.texts[key] = label

    def set(self, key, text, tone=""):
        """Текст и точка бейджа: цвет — только у точки, пилюля нейтральная."""
        self.texts[key].set_text(text)
        badge = self.badges[key]
        for name, value in self.TONES:
            theme.state(badge, name, tone == value)

    def set_link(self, ok):
        self.set("link", "связь: есть" if ok else "связь: нет", "" if ok else "warn")

    def set_queue(self, text, tone=""):
        self.set("queue", text, tone)

    def set_dark(self, dark):
        """Кнопка темы показывает, что будет после нажатия, а не что сейчас."""
        action = "включить светлую тему" if dark else "включить тёмную тему"
        self.theme_button.props(
            f'icon={"light_mode" if dark else "dark_mode"} aria-label="{action}"')
        self.theme_tip.set_text("Тёмная тема" if dark else "Светлая тема")
