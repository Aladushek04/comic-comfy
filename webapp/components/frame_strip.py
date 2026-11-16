"""Полоса кадров: миниатюры задания и выбранный кадр.

Полоса никогда не вызывает `clear()`: миниатюры только добавляются, а выделение
ставится классом на уже существующий элемент. Иначе каждый шаг задания
пересобирал бы полосу и выбранный кадр «прыгал» бы на первый (спецификация,
раздел 5; AGENTS 0.14).

Ключ кадра — имя файла, а не номер в списке: кадры приходят по одному, и номер
уже существующего кадра не меняется, а вот порядок после Recheck — да.
"""
from pathlib import Path

from nicegui import ui

from .. import theme


class FrameStrip:
    """Миниатюры кадров: `ключ → элемент`, выделение по имени файла."""

    def __init__(self, on_select):
        self._on_select = on_select
        self.thumbs = {}
        self.box = None

    def build(self):
        with ui.element("section") as card:
            theme.mark(card, "card")
            self.note = ui.label("Кадров пока нет")
            theme.mark(self.note, "meta")
            with ui.element("div") as box:
                theme.mark(box, "strip")
        self.box = box
        self.note.set_visibility(True)
        return self

    def set(self, frames, job_id, selected=None):
        """Добавляет новые кадры и ставит выделение. Существующие не трогает."""
        if self.box is None:
            return
        for frame in frames or ():
            key = Path(str(frame.get("file") or "")).name
            if not key or key in self.thumbs:
                continue
            # Миниатюра создаётся внутри полосы: `build()` уже вернулся, и без
            # этого «текущим» был бы контейнер сцены.
            with self.box:
                image = ui.element("img").props(
                    f'src="/media/job/{job_id}/{key}" alt="{key}"')
                theme.mark(image, "thumb")
                image.on("click", lambda _, name=key: self._on_select(name))
            self.thumbs[key] = image
        for key, image in self.thumbs.items():
            theme.state(image, "thumb-on", key == selected)
        self.note.set_visibility(not frames)

    def reset(self):
        """Новое задание: старые миниатюры удаляются по одной, без `clear()`."""
        for image in self.thumbs.values():
            image.delete()
        self.thumbs = {}
