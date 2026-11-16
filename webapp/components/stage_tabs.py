"""Вкладки сцены: «Кадр / Параметры / Промпт / История».

Вкладки переключают панели, а не пересобирают сцену: каждая панель строится
один раз и потом только обновляет значения. Иначе выбор кадра терялся бы на
каждом шаге задания (спецификация, раздел 5: никакого `clear()` и пересборки).

Выбор вкладки хранит страница (`app.storage.tab`): перезагрузка вкладки
браузера возвращает ту же панель.
"""
import contextlib

from nicegui import ui

from .. import theme

TABS = (("frame", "Кадр"), ("params", "Параметры"),
        ("prompt", "Промпт"), ("history", "История"))
NAMES = tuple(name for name, _ in TABS)


class StageTabs:
    """Полоса вкладок и общий контейнер панелей."""

    def __init__(self, on_change=None):
        self._on_change = on_change
        self.current = NAMES[0]
        self.buttons = {}
        self.panels = {}

    def build(self):
        with ui.element("section") as card:
            theme.mark(card, "card")
            with ui.element("div") as bar:
                theme.mark(bar, "tabs")
                for name, label in TABS:
                    button = ui.element("button").props("type=button")
                    theme.mark(button, "tab")
                    with button:
                        ui.label(label)
                    button.on("click", lambda _, key=name: self.select(key))
                    self.buttons[name] = button
            with ui.element("div") as box:
                theme.mark(box, "panels")
                self.box = box
        self.select(self.current, notify=False)
        return self

    @contextlib.contextmanager
    def panel(self, name):
        """Панель вкладки: контекст, внутри которого строится содержимое.

        Панель создаётся внутри своего контейнера: `build()` к этому моменту уже
        вернулся, и «текущим» элементом страницы был бы не контейнер панелей, а
        сцена — панель оказалась бы вне карточки вкладок.

        Видимость ставится сразу: до первого `select` все четыре были бы видны.
        """
        with self.box:
            with ui.element("div") as box:
                theme.mark(box, "panel")
                self.panels[name] = box
                box.set_visibility(name == self.current)
                yield box

    def select(self, name, notify=True):
        """Показать одну панель. Незнакомая вкладка — остаёмся на текущей."""
        if name not in self.buttons:
            return
        self.current = name
        for key, button in self.buttons.items():
            theme.state(button, "tab-on", key == name)
            button.props(f"aria-pressed={'true' if key == name else 'false'}")
        for key, box in self.panels.items():
            box.set_visibility(key == name)
        if notify and self._on_change is not None:
            self._on_change(name)
