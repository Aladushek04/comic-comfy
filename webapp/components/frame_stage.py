"""Кадр на сцене: большое изображение, подпись и ссылки.

Кадр показывается двумя `<img>` друг над другом: новый проявляется поверх
прежнего, поэтому при выборе миниатюры картинка не мигает пустым местом
(спецификация, раздел 5). Источник меняется только когда меняется кадр —
шаги задания не трогают картинку.

Промпт здесь не собирается и не показывается: у кадра свой фактический
`positive`/`negative` из паспорта, и живёт он на вкладке «Промпт» (0.14 №10).
"""
from pathlib import Path

from nicegui import ui

from .. import theme
from ..state import fmt_setting


class FrameStage:
    """Большой кадр выбранной записи."""

    def __init__(self):
        self.head = None
        self.kind = None
        self.meta = None
        self.open_link = None
        self.download = None
        self.box = None
        self.empty = None
        self.images = []
        self.active = 0
        self.shown = None

    def build(self):
        with ui.element("section") as card:
            theme.mark(card, "card")
            with ui.element("div") as head:
                theme.mark(head, "row")
                self.kind = ui.label("")
                theme.mark(self.kind, "meta")
                self.meta = ui.label("")
                theme.mark(self.meta, "meta", "num")
                with ui.element("div") as links:
                    theme.mark(links, "links")
                    self.open_link = ui.link("Открыть в полном размере", "")
                    theme.mark(self.open_link, "link")
                    self.open_link.props("target=_blank")
                    self.download = ui.link("Скачать", "")
                    theme.mark(self.download, "link")
                    self.download.props("target=_blank")
            with ui.element("div") as box:
                theme.mark(box, "frame-box")
                for _ in range(2):
                    image = ui.element("img").props('alt=""')
                    theme.mark(image, "frame-img")
                    self.images.append(image)
            self.empty = ui.label("")
            theme.mark(self.empty, "stage-empty")
        self.head = head
        self.box = box
        self.head.set_visibility(False)
        self.box.set_visibility(False)
        self.set(None, "")
        return self

    def set(self, frame, job_id="", hint=""):
        """Кадр (или ничего) и подпись. `hint` — текст пустой рамки."""
        if self.box is None:
            return
        key = Path(str(frame.get("file") or "")).name if frame else ""
        url = f"/media/job/{job_id}/{key}" if key and job_id else ""
        if url != self.shown:
            self.shown = url
            if url:
                other = 1 - self.active
                self.images[other].props(f'src="{url}"')
                theme.state(self.images[other], "frame-img-on", True)
                theme.state(self.images[self.active], "frame-img-on", False)
                self.active = other
            else:
                for image in self.images:
                    theme.state(image, "frame-img-on", False)
        self.head.set_visibility(bool(frame))
        self.box.set_visibility(bool(frame))
        self.empty.set_visibility(not frame)
        if not frame:
            self.empty.set_text(hint or "Кадр появится здесь после «Старта».")
            return
        self.kind.set_text("Уточнённый кадр" if frame.get("refined") else "Основной кадр")
        self.meta.set_text(
            f"seed {frame.get('seed')} · CN {fmt_setting(frame.get('cn_strength'))}"
            f" · LoRA {fmt_setting(frame.get('style_weight'))}"
            f" · denoise {fmt_setting(frame.get('denoise'))}")
        self.open_link.props(f'href="{url}"')
        self.download.props(f'href="{url}?download=1"')
