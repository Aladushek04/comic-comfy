"""Фото V2: пунктирная рамка с загрузкой или превью с размером из заголовка.

Грабли 0.14, которые закрывает этот компонент:
№1 — `on_upload` получает **корутину объектом** (`self.handle`), а не лямбду:
лямбду NiceGUI не ожидает, и загрузка молча не заканчивается;
№5 — после успеха `reset()`, иначе «Заменить фото» показывает старую очередь.
Размер берётся из заголовка PNG/JPEG **до** `Image.open` (0.15) — тем же
`service.image_size`, которым сервис проверяет предел картинки.
"""
import asyncio

import photo_prompt as pp
from nicegui import ui

from .. import service, theme
from ..service import UploadError


class PhotoSlot:
    """Одно фото: пусто — рамка «Перетащи фото», готово — превью и замена."""

    def __init__(self, svc, on_change):
        self.svc = svc
        self._on_change = on_change
        self.upload_id = None
        self.tag = None
        self.upload = None
        self.preview = None
        self.drop = None
        self.box = None
        self.caption = None
        self.replace_button = None

    def build(self):
        with ui.element("section") as card:
            theme.mark(card, "card")
            # Шапка: имя фото, размер и замена в одной строке — так карточка
            # занимает на 60 px меньше, и рельс влезает в окно 900 px.
            with ui.element("div") as head:
                theme.mark(head, "photo-head")
                theme.mark(ui.label("Фото"), "title")
                self.caption = ui.label("")
                theme.mark(self.caption, "meta", "num", "photo-caption")
                self.replace_button = ui.button(icon="swap_horiz", on_click=self.clear,
                                                color=None)
                theme.mark(self.replace_button, "icon")
                with self.replace_button:
                    ui.tooltip("Заменить фото")
            with ui.element("div") as drop:
                theme.mark(drop, "drop")
                theme.mark(ui.label("Перетащи фото"), "drop-title")
                theme.mark(ui.label("PNG, JPG или WebP · до 30 МБ"), "meta")
                theme.mark(ui.label("1 фото · 2 описание · 3 кадр"), "meta")
                self.upload = ui.upload(
                    auto_upload=True, max_file_size=service.MAX_UPLOAD).props(
                    "accept=image/*")
                theme.mark(self.upload, "upload")
                self.upload.on_upload(self.handle)      # корутина объектом, не лямбда
                # Клик по всей рамке открывает выбор файла: диалог вызывается из
                # настоящего клика (иначе браузер его блокирует), а `input` внутри
                # невидимого загрузчика уже слушает drag&drop сам.
                drop.on("click", js_handler=(
                    "(e) => { if (e.target.tagName === 'INPUT') return;"
                    " const i = document.querySelector('.cp-drop input[type=file]');"
                    " if (i) i.click(); }"))
            with ui.element("div") as box:
                theme.mark(box, "frame")
                self.preview = ui.image()
                theme.mark(self.preview, "photo")
        self.drop = drop
        self.box = box
        box.set_visibility(False)
        self.replace_button.set_visibility(False)
        return self

    async def handle(self, e):
        data = await e.file.read()
        try:
            uid, path = await asyncio.to_thread(
                self.svc.save_upload, e.file.name, data)
        except UploadError as ex:
            ui.notify(str(ex), type="negative")
            return
        self.upload_id = uid
        self.tag = pp.image_tag(path)
        self.preview.set_source(f"/media/upload/{uid}")
        self.caption.set_text(self._caption(e.file.name, data))
        self.upload.reset()                             # №5: очередь файлов пуста
        self.upload.set_visibility(False)
        self.drop.set_visibility(False)
        self.box.set_visibility(True)
        self.replace_button.set_visibility(True)
        ui.notify("фото загружено")
        self._on_change(uid, self.tag)

    def clear(self):
        """Замена фото: старый черновик больше не нужен, описание к нему тоже."""
        self.upload_id = None
        self.tag = None
        self.preview.set_source("")
        self.caption.set_text("")
        self.box.set_visibility(False)
        self.upload.reset()
        self.upload.set_visibility(True)
        self.drop.set_visibility(True)
        self.replace_button.set_visibility(False)
        self._on_change(None, None)

    @staticmethod
    def _caption(name, data):
        """Имя, размер в пикселях и вес файла: размер — из заголовка, не из PIL."""
        size = service.image_size(data)
        kb = max(1, round(len(data) / 1024))
        if size:
            return f"{name} · {size[0]}×{size[1]} · {kb} КБ"
        return f"{name} · {kb} КБ"
