"""Зона ошибки: одно место, полный текст и журнал в диалоге.

Раньше текст ошибки обрезался дважды и в двух карточках сразу — в «ходе» и в
«результате», и обрезки не совпадали. Здесь обрезка одна и только в заголовке
(`labels.short`), а сам текст показывается целиком: человеку нужен ответ
модели или ComfyUI, а не его начало.

Журнал задания (все события с временем) живёт в диалоге: на странице он
занимал бы пол-экрана и отвлекал от кадра.
"""
from nicegui import ui

from .. import labels, theme
from ..labels import STAGE_LABEL, STATUS_LABEL, fmt_time
from ..state import ERR, PHASES, phase_states

# Один лимит на одну строку заголовка. Тело ошибки не обрезается вовсе.
TITLE_LIMIT = 120


class ErrorZone:
    """Карточка ошибки и диалог с журналом задания."""

    def __init__(self):
        self.card = None
        self.title = None
        self.text = None
        self.dialog = None
        self.log = None
        self.events = []
        self.signature = None

    def build(self):
        with ui.element("section") as card:
            theme.mark(card, "card", "error")
            with ui.element("div") as head:
                theme.mark(head, "error-head")
                self.title = ui.label("")
                theme.mark(self.title, "err")
                button = ui.button("Журнал", color=None, on_click=self.open_journal)
                theme.mark(button, "btn", "btn-ghost")
            self.text = ui.label("")
            theme.mark(self.text, "error-text")
        self.card = card
        with ui.dialog() as dialog, ui.element("div") as box:
            theme.mark(box, "card")
            theme.mark(ui.label("Журнал задания"), "title")
            self.log = ui.log(max_lines=500)
            theme.mark(self.log, "log")
        self.dialog = dialog
        self.card.set_visibility(False)
        return self

    def set(self, snap, status):
        """Текст ошибки из снапшота. Показывается, только если она есть."""
        if self.card is None:
            return
        snap = snap or {}
        text = str(snap.get("error") or "")
        events = snap.get("events") or []
        self.events = events
        signature = (text, status, len(snap.get("frames") or ()), len(events))
        if signature == self.signature:
            return
        self.signature = signature
        self.card.set_visibility(bool(text))
        if not text:
            return
        first = text.splitlines()[0] if text.strip() else text
        head = STATUS_LABEL["partial"] if status == "partial" else STATUS_LABEL["failed"]
        if status == "partial":
            head += f" · кадров {len(snap.get('frames') or ())}"
        # Где именно упало: этап ошибки у всех один («error»), а фаза — своя.
        where = next((label for (_, label), value
                      in zip(PHASES, phase_states(events, status)) if value == ERR), "")
        if where:
            head += f" · {where}"
        self.title.set_text(f"{head}: {labels.short(first, TITLE_LIMIT)}")
        self.text.set_text(text)

    def open_journal(self):
        """Журнал: все события задания с временем и сообщением."""
        self.log.clear()
        for event in self.events:
            stage = STAGE_LABEL.get(event.get("stage") or "", event.get("stage") or "")
            line = f"[{fmt_time(event.get('t'))}] {stage}"
            message = " ".join(str(event.get("message") or "").split())
            if message:
                line += " · " + message
            self.log.push(line)
        self.dialog.open()
