"""Верхняя панель V2: связь, ComfyUI, описание, очередь и переключатель темы.

Проверка сервисов меняет только текст и состояние бейджей (в расширенной панели
будущих фаз — ещё и `set_options` списков): перерисовки нет, поэтому ввод
человека не пропадает (грабли 0.14 №6).
"""
from nicegui import ui

from .. import theme


class TopBar:
    """Пять бейджей и кнопка темы. Обновление — set_text и класс состояния."""

    TONES = (("badge-ok", "ok"), ("badge-warn", "warn"), ("badge-err", "err"))

    def __init__(self, on_health, on_theme):
        self._on_health = on_health
        self._on_theme = on_theme
        self.badges = {}
        self.theme_button = None
        self.theme_tip = None

    def build(self, dark):
        with ui.element("header") as bar:
            theme.mark(bar, "topbar")
            with ui.element("div") as brand:
                theme.mark(brand, "brand")
                theme.mark(ui.label("comic-comfy"), "brand-title")
                theme.mark(ui.label("V2 · фото → описание → кадр"), "meta")
            with ui.element("div") as right:
                theme.mark(right, "badges")
                self.badges["link"] = self._badge("связь: есть")
                self.badges["comfy"] = self._badge("ComfyUI: проверяю")
                self.badges["lm"] = self._badge("Описание: проверяю")
                self.badges["queue"] = self._badge("очередь: свободна")
                check = ui.button("Проверить сервисы", on_click=self._on_health,
                                  color=None)
                theme.mark(check, "btn", "btn-ghost")
                self.theme_button = ui.button(icon="light_mode",
                                              on_click=self._on_theme, color=None)
                theme.mark(self.theme_button, "icon")
                with self.theme_button:
                    self.theme_tip = ui.tooltip("Светлая тема")
        self.set_dark(dark)
        return self

    def _badge(self, text):
        badge = ui.badge(text, color=None)
        theme.mark(badge, "badge")
        return badge

    def set(self, key, text, tone=""):
        """Текст и тон бейджа: тон — класс состояния, не цвет в компоненте."""
        badge = self.badges[key]
        badge.set_text(text)
        for name, value in self.TONES:
            theme.state(badge, name, tone == value)

    def set_link(self, ok):
        self.set("link", "связь: есть" if ok else "связь: нет", "" if ok else "warn")

    def set_queue(self, text, tone=""):
        self.set("queue", text, tone)

    def set_dark(self, dark):
        """Кнопка показывает, куда переключит: в тёмной теме — солнце."""
        action = "Включить светлую тему" if dark else "Включить тёмную тему"
        self.theme_button.props(
            f'icon={"light_mode" if dark else "dark_mode"} aria-label="{action}"')
        self.theme_tip.set_text("Светлая тема" if dark else "Тёмная тема")
