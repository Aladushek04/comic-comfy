"""Главное действие V2: «Старт» и «Только описание».

Разделение обязательно (спецификация, раздел 1): описание живёт без ComfyUI,
генерация — нет. Если сервер не отвечает и сам не поднимется, «Старт» выключен
с причиной, а «Только описание» остаётся живым.

`status` — состояние страницы (empty/queued/running/…), а не этап задания:
этап говорит, что делает задание, состояние — что видно и что можно нажать.
"""
from nicegui import ui

from .. import theme
from ..labels import STATUS_LABEL


class StartAction:
    """Липкая карточка с состоянием и двумя кнопками."""

    def __init__(self, on_start):
        self._on_start = on_start
        self.state = {
            "status": "empty",
            "queue": 0,
            "detail": "",
            "photo": False,
            "comfy": False,
            "launchable": False,
            "styles": True,
            "reason": "",
        }
        self.status = None
        self.detail = None
        self.start_button = None
        self.describe_button = None
        self.note = None

    def build(self):
        with ui.element("section") as card:
            theme.mark(card, "start")
            with ui.element("div") as actions:
                theme.mark(actions, "actions")
                self.status = ui.label(STATUS_LABEL["empty"])
                theme.mark(self.status, "status")
                self.detail = ui.label("")
                theme.mark(self.detail, "meta")
                self.start_button = ui.button(
                    "Старт", icon="play_arrow", color=None,
                    on_click=lambda: self._on_start(True, False))
                theme.mark(self.start_button, "btn", "btn-fill")
                self.describe_button = ui.button(
                    "Только описание", color=None,
                    on_click=lambda: self._on_start(False, False))
                theme.mark(self.describe_button, "btn", "btn-ghost")
                self.note = ui.label("")
                theme.mark(self.note, "warn")
        self._apply()
        return self

    def update(self, **values):
        """Абсолютные значения, а не приращения: состояние всегда одно."""
        self.state.update(values)
        self._apply()

    def _label(self):
        status = self.state["status"]
        label = STATUS_LABEL.get(status, status)
        if status == "queued" and self.state["queue"]:
            return f"{label} · позиция {self.state['queue']}"
        if status == "running" and self.state["detail"]:
            return f"{label}: {self.state['detail']}"
        return label

    def _apply(self):
        if self.status is None:
            return
        self.status.set_text(self._label())
        shown = self.state["status"] in ("running", "expired")
        self.detail.set_text(self.state["detail"] if shown else "")
        has_photo = bool(self.state["photo"])
        has_style = bool(self.state["styles"])
        comfy = bool(self.state["comfy"] or self.state["launchable"])
        self.start_button.set_enabled(has_photo and has_style and comfy)
        self.describe_button.set_enabled(has_photo and has_style)
        note = self._note(has_photo, has_style, comfy)
        self.note.set_text(note)
        self.note.set_visibility(bool(note))

    def _note(self, has_photo, has_style, comfy):
        if not has_style:
            return "Нет годных сборок: соберите графы заново."
        if not has_photo:
            if self.state["status"] in ("done", "partial", "failed", "described"):
                return "Загрузите фото, чтобы запустить ещё раз."
            return "Сначала загрузите фото."
        if not comfy:
            return (self.state["reason"]
                    or "ComfyUI не отвечает — генерация ждёт сервер, описание работает.")
        return ""
