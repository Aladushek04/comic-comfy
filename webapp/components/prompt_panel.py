"""Промпт выбранного кадра: фактический positive и negative.

Ничего не собирается: строки берутся из записи кадра, которую положил туда
`jobs.run_job` из отчёта `generate()` (0.14 №10). Собранный заново промпт
разошёлся бы с тем, что ушло в ComfyUI: шаблон стиля, переопределения и
негатив складываются в графе, а не в форме.

Кадр старой версии промпта не хранит: там показываем `subject` и честно
говорим, что точный текст не записан.
"""
from nicegui import ui

from .. import theme


class PromptPanel:
    """Positive и negative выбранного кадра, без пересборки."""

    def __init__(self):
        self.note = None
        self.positive = None
        self.negative = None
        self.signature = None

    def build(self):
        self.note = ui.label("Промпт появится вместе с кадром.")
        theme.mark(self.note, "meta")
        with ui.element("div") as box:
            theme.mark(box, "prompt")
            theme.mark(ui.label("Positive (ушёл в ComfyUI)"), "prompt-label")
            self.positive = ui.label("")
            theme.mark(self.positive, "prompt-text")
            theme.mark(ui.label("Negative"), "prompt-label")
            self.negative = ui.label("")
            theme.mark(self.negative, "prompt-text")
        self.box = box
        return self

    def set(self, frame):
        """Кадр (или None): тексты и подпись. Одинаковый кадр не переписывается."""
        if self.note is None:
            return
        positive = (frame or {}).get("positive") or ""
        negative = (frame or {}).get("negative") or ""
        signature = (positive, negative, (frame or {}).get("subject") or "")
        if signature == self.signature:
            return
        self.signature = signature
        self.positive.set_text(positive)
        self.negative.set_text(negative)
        self.positive.set_visibility(bool(positive))
        self.negative.set_visibility(bool(negative))
        if frame and positive:
            note = ""
        elif frame and frame.get("subject"):
            note = ("Точный промпт не записан: кадр старой версии. Вот описание, "
                    "из которого он собран: " + str(frame.get("subject")))
        elif frame:
            note = "У кадра нет ни промпта, ни описания — паспорт пуст."
        else:
            note = "Промпт появится вместе с кадром."
        self.note.set_text(note)
        self.note.set_visibility(bool(note))
