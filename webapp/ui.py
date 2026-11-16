"""Страница NiceGUI: форма, таймер и результат.

Здесь нет ни описания фото, ни генерации: страница собирает JobRequest,
ставит его в service.JobService и рисует события задания. Всё, что относится
к графам, живёт в catalog.py, а к пайплайну — в jobs.py.

Страница — один класс: у каждой вкладки свой экземпляр и свой номер задания
в адресе (`/?job=...`), поэтому новая вкладка не подхватывает чужую работу,
а перезагрузка возвращает ту же.
"""
import asyncio
import os
import uuid
from pathlib import Path

import comfy_launch as cl
import jobs
import photo_prompt as pp
import settings
from nicegui import app, ui
from starlette.responses import FileResponse, Response

from . import catalog
from .service import JobService, UploadError
from .state import fmt_setting, missing_job_text, pass_steps

_service = None
_routes_done = False

STAGE_LABEL = {
    "queued": "в очереди",
    "start": "задание принято",
    "comfy_queue": "стоит в очереди ComfyUI",
    "cache": "описание из кэша",
    "cache_write": "кэш описания",
    "manual": "ручное описание",
    "describe": "смотрю фото моделью",
    "scene": "сцена",
    "unload": "освобождаю память",
    "subject": "описание",
    "negative": "негатив",
    "notes": "заметка",
    "setting": "настройка",
    "preset": "пресет",
    "prompt": "собираю промпт",
    "generate": "генерация",
    "variant": "вариант кадра",
    "comfy": "жду ComfyUI",
    "comfy_launch": "поднимаю ComfyUI",
    "comfy_start": "ComfyUI начал",
    "comfy_cached": "граф из кэша",
    "sampling": "шаги сэмплера",
    "comfy_done": "сэмплер закончил",
    "comfy_error": "ошибка ComfyUI",
    "ws_failed": "WebSocket не открылся",
    "save": "сохраняю кадр",
    "recheck": "проверяю кадр",
    "described": "описание готово",
    "done": "готово",
    "partial": "частично",
    "cancelled": "отменено",
    "error": "ошибка",
    "preview": "итоговый промпт",
}

ROLE_LABEL = {"style": "стиль", "body": "тело", "character": "персонаж",
              "hands": "кисти", "lora": "LoRA"}

# Тема — состояние интерфейса, а не задания: файл рядом с кадрами, но не внутри
# папки фото. env COMIC_THEME_FILE нужен проверкам, чтобы не переписывать выбор.
THEME_FILE = Path(os.environ.get("COMIC_THEME_FILE") or pp.ROOT / "out" / "theme.json")

# Цвета подписей: в светлой теме исходные, в тёмной — светлее, иначе на #121212
# красный и оранжевый уходят в грязь. Рамки превью держим теми же переменными:
# Tailwind `border` без цвета на тёмной странице почти не виден.
THEME_CSS = """
<style>
  .comic-error { color: #dc2626; }
  .comic-warn { color: #ea580c; }
  .comic-frame { border: 1px solid rgba(0, 0, 0, .12); }
  body.body--dark .comic-error { color: #f87171; }
  body.body--dark .comic-warn { color: #fbbf24; }
  body.body--dark .comic-frame { border-color: rgba(255, 255, 255, .24); }
</style>
"""


def read_theme():
    """Сохранённый выбор: True — тёмная, False — светлая.

    По умолчанию тёмная: файла нет или он битый — это не повод показывать
    светлую страницу тому, кто просил тёмную.
    """
    try:
        data = pp.read_json(THEME_FILE)
    except (OSError, ValueError):
        return True
    if not isinstance(data, dict) or not isinstance(data.get("dark"), bool):
        return True
    return data["dark"]


def save_theme(dark):
    """Тема переживает перезапуск сервера, поэтому пишется на диск."""
    try:
        pp.write_json(THEME_FILE, {"dark": bool(dark)})
    except OSError as e:
        ui.notify(f"тему не удалось сохранить: {e}", type="warning")


def service():
    global _service
    if _service is None:
        _service = JobService()
    return _service


def fmt_time(seconds):
    seconds = int(max(0, seconds))
    return f"{seconds // 60}:{seconds % 60:02d}"


def short(value, limit=70):
    text = " ".join(str(value or "").split())
    return text[:limit] + ("…" if len(text) > limit else "")


def _select_options(info, class_type, name, current):
    """Варианты из /object_info плюс текущее значение.

    Сервер может не знать файл из графа (модель не скачана) — текущее
    значение всё равно должно остаться выбранным, иначе форма не построится.
    """
    opts = list(catalog.combo_options(info, class_type, name) or [])
    if current and current not in opts:
        opts.insert(0, current)
    return opts or ([current] if current else [])


def _inside(path, root):
    try:
        Path(path).resolve().relative_to(Path(root).resolve())
        return True
    except Exception:
        return False


# ------------------------------------------------------------------ файлы
def register_routes():
    global _routes_done
    if _routes_done:
        return
    _routes_done = True
    svc = service()

    @app.get("/api/job")
    def api_job(job_id: str | None = None):
        """Диагностика задания: то же состояние, что рисует страница."""
        return svc.snapshot(job_id) or {}

    @app.get("/media/upload/{uid}")
    def media_upload(uid: str):
        path = svc.upload_path(uid)
        if path is None:
            return Response(status_code=404)
        return FileResponse(path, media_type="image/png")

    @app.get("/media/job/{job_id}/{name}")
    def media_job(job_id: str, name: str, download: int = 0):
        job = svc.get(job_id)
        frames = (job.result.frames if job and job.result else []) or []
        allowed = {Path(f["file"]).name: f for f in frames}
        if name not in allowed:
            return Response(status_code=404)
        path = Path(allowed[name]["folder"]) / allowed[name]["file"]
        if not path.exists() or not _inside(path, pp.ROOT / "out"):
            return Response(status_code=404)
        return FileResponse(
            path, filename=name if download else None,
            content_disposition_type="attachment" if download else "inline")


class ComicPage:
    """Одна вкладка: черновик формы, подписка на задание и отрисовка."""

    def __init__(self, styles):
        self.svc = service()
        self.styles = styles
        self.usable = [s for s in styles if not s["problems"]]
        request = None
        try:
            request = ui.context.client.request
        except Exception:
            pass
        wanted = request.query_params.get("job") if request else None
        self.state = {
            "job_id": wanted or None,
            "snap_id": None,
            "last_seq": 0,
            "signature": None,
            "snap": None,
            "selected": 0,
            "user_selected": False,
            "info": None,
            "lm_models": [],
            "samplers": [],
            "comfy_url": "",
            "comfy_error": "",
            "upload_id": None,
            "upload_tag": None,
            "desc_auto": "",
            "ws": "",
            "health_synced": False,
            "health_during": False,
            "styles": {s["workflow"]: s for s in styles},
        }
        self.w = {}
        self._combos = []
        self._advanced_refresh = None

    # ------------------------------------------------------------ утилиты
    def meta(self, workflow=None):
        workflow = workflow or self.w["style"].value
        try:
            return catalog.load_meta(workflow)
        except catalog.CatalogError as e:
            ui.notify(str(e), type="negative")
            return None

    def add_combo(self, key, label, class_type, input_name, value, classes=""):
        widget = ui.select([value] if value else [], value=value, label=label).props(
            "outlined dense").classes(classes)
        self._combos.append({"key": key, "widget": widget, "class": class_type,
                             "input": input_name, "fallback": value})
        self.w[key] = widget
        return widget

    def models_note(self):
        if self.state.get("info"):
            return ""
        if cl.available():
            return ("ComfyUI не запущен: списки моделей появятся после первого запуска, "
                    "пока — значения из графа.")
        return ("ComfyUI не ответил: списки моделей недоступны, оставлены "
                "значения из графа.")

    def apply_options(self):
        """Обновляет списки, не трогая то, что пользователь уже выбрал."""
        if not self.w:
            return
        info = self.state.get("info")
        for item in self._combos:
            widget = item["widget"]
            current = widget.value or item["fallback"]
            opts = _select_options(info, item["class"], item["input"], current)
            widget.set_options(opts, value=current if current in opts else opts[0])
        model = self.w.get("model")
        if model is not None:
            current = model.value
            models = list(self.state.get("lm_models") or [])
            if current and current not in models:
                models.insert(0, current)
            if not current and len(models) == 1:
                current = models[0]     # единственная модель сервиса — показываем её
            model.set_options(models, value=current)
        note = self.w.get("models_note")
        if note is not None:
            note.set_text(self.models_note())

    # ------------------------------------------------------------ страница
    def build(self):
        ui.page_title("comic-comfy")
        ui.add_head_html(THEME_CSS)
        self.dark = ui.dark_mode(read_theme())
        with ui.column().classes("w-full max-w-[1440px] mx-auto gap-3 p-3 md:p-4"):
            self.header()
            if not self.usable:
                self.no_graphs()
                return
            for s in self.styles:
                if s["problems"]:
                    ui.label(f"{s['workflow']}: " + "; ".join(s["problems"])).classes(
                        "text-xs comic-error")
            with ui.row().classes("w-full items-start gap-4 flex-wrap lg:flex-nowrap"):
                with ui.column().classes(
                        "w-full lg:w-[420px] shrink-0 gap-3 min-w-0 order-2 lg:order-1"):
                    self.upload_card()
                    self.task_card()
                    self.description_card()
                with ui.column().classes("w-full flex-1 gap-3 min-w-0 order-1 lg:order-2"):
                    self.progress_card()
                    self.result_card()
            with ui.column().classes("w-full gap-2"):
                self.advanced_panel()
                self.prompt_panel()
                self.journal_panel()
        ui.timer(0.6, self.refresh)
        ui.timer(0.2, self.refresh_health, once=True)

    def header(self):
        with ui.row().classes("w-full items-center justify-between gap-2 flex-wrap"):
            with ui.column().classes("gap-0"):
                ui.label("comic-comfy").classes("text-2xl font-semibold leading-tight")
                ui.label("фото → описание → кадр. Всё локально").classes("text-xs opacity-60")
            with ui.row().classes("items-center gap-2 flex-wrap"):
                self.w["link_badge"] = ui.badge("связь: есть", color="positive")
                self.w["comfy_badge"] = ui.badge("ComfyUI: проверяю", color="grey")
                self.w["lm_badge"] = ui.badge("Описание: проверяю", color="grey")
                with self.w["lm_badge"]:
                    self.w["lm_tip"] = ui.tooltip(pp.LM)
                ui.button("Проверить сервисы", on_click=self.refresh_health).props(
                    "flat dense no-caps")
                self.w["theme_button"] = ui.button(
                    icon="light_mode", on_click=self.toggle_theme,
                ).props('flat dense round aria-label="Включить светлую тему"').classes(
                    "comic-theme")
                with self.w["theme_button"]:
                    self.w["theme_tip"] = ui.tooltip("Светлая тема")
        self.w["comfy_note"] = ui.label("").classes("text-xs comic-warn")
        self.update_theme_button()

    def toggle_theme(self):
        """Тёмная тема по умолчанию, светлая — такой же выбор; помним оба."""
        self.dark.toggle()
        save_theme(self.dark.value)
        self.update_theme_button()

    def update_theme_button(self):
        dark = bool(self.dark.value)
        action = "Включить светлую тему" if dark else "Включить тёмную тему"
        self.w["theme_button"].props(
            f'icon={"light_mode" if dark else "dark_mode"} aria-label="{action}"')
        self.w["theme_tip"].set_text("Светлая тема" if dark else "Тёмная тема")

    def no_graphs(self):
        with ui.card().classes("w-full"):
            ui.label("Нет готовых фото-сборок").classes("text-lg font-medium")
            ui.label("Соберите графы: python tools\\build_workflows.py — и обновите "
                     "страницу.").classes("text-sm opacity-70")
            for s in self.styles:
                ui.label(f"{s['workflow']}: " + "; ".join(s["problems"])).classes(
                    "text-xs comic-error")

    # ------------------------------------------------------------ левая часть
    def upload_card(self):
        with ui.card().classes("w-full"):
            ui.label("Фото").classes("text-base font-medium")
            upload = ui.upload(label="Выбрать фото (PNG, JPG, WebP)", auto_upload=True,
                               max_file_size=30 * 1024 * 1024).props(
                "accept=image/* flat bordered").classes("w-full")
            self.w["upload"] = upload
            with ui.column().classes("w-full gap-2") as box:
                self.w["preview_box"] = box
                with ui.element("div").classes(
                        "comic-frame w-full flex items-center justify-center rounded").style(
                        "height:42vh"):
                    self.w["preview"] = ui.image().classes("w-full h-full object-contain")
                with ui.row().classes("items-center gap-2"):
                    ui.button("Заменить фото", icon="swap_horiz",
                              on_click=self.replace_photo).props("flat dense no-caps")
                    ui.label("Кадр целиком, пропорции сохранены").classes(
                        "text-xs opacity-50")
            box.set_visibility(False)
            upload.on_upload(self.on_upload)

    def task_card(self):
        with ui.card().classes("w-full"):
            ui.label("Задание").classes("text-base font-medium")
            self.w["style"] = ui.select(
                {s["workflow"]: s["label"] for s in self.usable},
                value=next((s["workflow"] for s in self.usable
                            if s["workflow"] == "07_photo_thepit"),
                           self.usable[0]["workflow"]),
                label="Стиль", on_change=self.on_style).props(
                "outlined dense").classes("w-full")
            with ui.row().classes("w-full gap-2 items-end flex-wrap"):
                self.w["scene"] = ui.input(
                    "Сцена (необязательно)", placeholder="на пляже, закат, улица").props(
                    "outlined dense").classes("grow min-w-48")
                self.w["variants"] = ui.number("Вариантов", value=1, min=1, max=8,
                                               precision=0).props(
                    "outlined dense").classes("w-24")
            self.w["start"] = ui.button("Старт", icon="play_arrow",
                                        on_click=lambda: self.start(True, False)).props(
                "unelevated").classes("w-full mt-1")

    def description_card(self):
        with ui.card().classes("w-full"):
            ui.label("Описание").classes("text-base font-medium")
            self.w["desc"] = ui.textarea(
                "Описание (subject) — можно править",
                placeholder="заполнится после «Только описание» или авто-запуска").props(
                "outlined autogrow").classes("w-full")
            with ui.row().classes("gap-2 items-center flex-wrap"):
                ui.button("Только описание",
                          on_click=lambda: self.start(False, False)).props(
                    "flat dense no-caps")
                ui.button("Сгенерировать по описанию",
                          on_click=lambda: self.start(True, True)).props(
                    "flat dense no-caps")
                ui.button("Пересчитать промпт", on_click=lambda: self.recalc()).props(
                    "flat dense no-caps")
            self.w["desc_hint"] = ui.label(
                "Автоописание привязано к загруженному фото.").classes("text-xs opacity-50")

    def advanced_panel(self):
        with ui.expansion("Расширенные настройки", icon="tune").classes("w-full"):
            @ui.refreshable
            def panel():
                self.render_advanced()

            panel()
            self._advanced_refresh = panel.refresh

    def prompt_panel(self):
        with ui.expansion("Промпт и переопределения", icon="notes").classes("w-full"):
            ui.label("Это промпт текущей формы. У сохранённого кадра свой промпт — "
                     "в галерее, в «Промпт этого кадра».").classes("text-xs opacity-60")
            ui.label("Полный positive или negative заменяет собранный текст целиком и "
                     "главнее описания и тегов негатива.").classes("text-xs opacity-70")
            self.w["pos_preview"] = ui.textarea(
                "Positive, который уйдёт в ComfyUI").props(
                "outlined autogrow readonly").classes("w-full")
            self.w["neg_preview"] = ui.textarea(
                "Negative, который уйдёт в ComfyUI").props(
                "outlined autogrow readonly").classes("w-full")
            with ui.row().classes("items-center gap-3 flex-wrap"):
                self.w["pos_override"] = ui.checkbox("Переопределить positive")
                self.w["neg_override"] = ui.checkbox("Переопределить negative")
            self.w["pos_custom"] = ui.textarea("Полный positive").props(
                "outlined autogrow").classes("w-full")
            self.w["neg_custom"] = ui.textarea("Полный negative").props(
                "outlined autogrow").classes("w-full")
            self.w["pos_custom"].set_visibility(False)
            self.w["neg_custom"].set_visibility(False)
            self.w["pos_override"].on_value_change(
                lambda e: self.w["pos_custom"].set_visibility(bool(e.value)))
            self.w["neg_override"].on_value_change(
                lambda e: self.w["neg_custom"].set_visibility(bool(e.value)))
            with ui.row().classes("items-end gap-2 flex-wrap"):
                self.w["neg_add"] = ui.input("Добавить в негатив").props(
                    "outlined dense").classes("grow min-w-40")
                self.w["neg_drop"] = ui.input("Убрать из негатива").props(
                    "outlined dense").classes("grow min-w-40")

    def journal_panel(self):
        with ui.expansion("Журнал задания", icon="list").classes("w-full"):
            self.w["log"] = ui.log(max_lines=300).classes(
                "w-full h-40 text-xs font-mono")

    # ------------------------------------------------------------ правая часть
    def progress_card(self):
        with ui.card().classes("w-full"):
            with ui.row().classes("w-full items-baseline justify-between gap-2 flex-wrap"):
                self.w["stage"] = ui.label("Задание не запущено").classes(
                    "text-lg font-medium")
                self.w["elapsed"] = ui.label("").classes("font-mono text-sm opacity-70")
            self.w["pass"] = ui.label("").classes("text-xs opacity-60")
            self.w["progress"] = ui.linear_progress(value=0).props("instant-feedback")
            self.w["progress"].set_visibility(False)
            self.w["queue"] = ui.label("").classes("text-xs opacity-70")
            self.w["error"] = ui.label("").classes("comic-error text-sm whitespace-pre-wrap")

    def result_card(self):
        with ui.card().classes("w-full"):
            ui.label("Результат").classes("text-base font-medium")
            self.w["result"] = ui.column().classes("w-full gap-2")
            with self.w["result"]:
                ui.label("Кадр появится здесь после «Старта».").classes(
                    "text-sm opacity-50 py-8 text-center w-full")

    # ------------------------------------------------------------ загрузка
    def _clear_auto_description(self):
        """Устаревший автотекст не должен переезжать на новое фото."""
        desc = self.w["desc"]
        text = (desc.value or "").strip()
        if not text or text == (self.state["desc_auto"] or "").strip():
            desc.set_value("")
            self.state["desc_auto"] = ""
            self.w["desc_hint"].set_text("Автоописание привязано к загруженному фото.")
        else:
            self.w["desc_hint"].set_text(
                "Текст остался от прошлого фото — это ручная правка, проверьте его.")
            ui.notify("Описание не очищено: оно правилось вручную", type="warning")

    async def on_upload(self, e):
        data = await e.file.read()
        try:
            uid, path = await asyncio.to_thread(self.svc.save_upload, e.file.name, data)
        except UploadError as ex:
            ui.notify(str(ex), type="negative")
            return
        self._clear_auto_description()
        self.state["upload_id"] = uid
        self.state["upload_tag"] = pp.image_tag(path)
        self.w["preview"].set_source(f"/media/upload/{uid}")
        self.w["preview_box"].set_visibility(True)
        self.w["upload"].set_visibility(False)
        self.w["upload"].reset()
        ui.notify("фото загружено", type="positive")

    def replace_photo(self):
        self._clear_auto_description()
        self.state["upload_id"] = None
        self.state["upload_tag"] = None
        self.w["preview"].set_source("")
        self.w["preview_box"].set_visibility(False)
        self.w["upload"].reset()
        self.w["upload"].set_visibility(True)

    # ------------------------------------------------------------ сервисы
    async def refresh_health(self):
        if not self.w.get("comfy_badge"):
            return
        self.w["comfy_badge"].set_text("ComfyUI: проверяю")
        self.w["lm_badge"].set_text("Описание: проверяю")
        info = await asyncio.to_thread(catalog.health, pp.LM)
        lm = info["lm"]
        lm_ok = lm["ok"]
        comfy_ok = info["comfy"]["ok"]
        name = "LM Studio" if lm.get("provider") == "lmstudio" else "Сервис описаний"
        text = f"{name}: " + ("готов" if lm_ok else "не отвечает")
        if lm_ok and lm.get("models"):
            text += f" ({len(lm['models'])})"
        self.w["lm_badge"].set_text(text)
        self.w["lm_badge"].props(f'color={"positive" if lm_ok else "warning"}')
        self.w["lm_tip"].set_text(
            lm["url"] + ("\n" + ", ".join(lm["models"][:3]) if lm.get("models") else ""))
        self.state["comfy_url"] = info["comfy"]["url"]
        self.state["comfy_error"] = info["comfy"]["error"]
        launchable = cl.available()
        if comfy_ok:
            self.w["comfy_badge"].set_text("ComfyUI: готов")
            self.w["comfy_badge"].props('color="positive"')
            self.w["comfy_note"].set_text(info["comfy"]["url"])
        elif launchable:
            self.w["comfy_badge"].set_text("ComfyUI: поднимется сам")
            self.w["comfy_badge"].props('color="warning"')
            self.w["comfy_note"].set_text(
                "Headless ComfyUI запустится после выгрузки модели описания — "
                "память не занята заранее.")
        else:
            self.w["comfy_badge"].set_text("ComfyUI: не отвечает")
            self.w["comfy_badge"].props('color="negative"')
            self.w["comfy_note"].set_text(info["comfy"]["error"] or info["comfy"]["url"])
        self.state["lm_models"] = info["lm"]["models"]
        self.state["info"] = (await asyncio.to_thread(
            catalog.object_info, info["comfy"]["url"])) if comfy_ok else None
        self.apply_options()

    # ------------------------------------------------------------ advanced
    def render_advanced(self):
        """Содержимое панели. Вызывается только внутри её слота."""
        self._combos = []
        meta = self.meta()
        if meta is None:
            ui.label("Сборка не готова: пересоберите графы.").classes("text-sm comic-error")
            return
        fields = meta.get("fields") or {}
        info = self.state.get("info")
        self.state["samplers"] = [str(x) for x in (meta.get("samplers") or [])]
        workflow = self.w["style"].value
        ui.label(f"Значения из сборки {workflow}; пустое поле — как в графе, "
                 f"0 — явный ноль.").classes("text-xs opacity-60")

        ui.label("Описание").classes("text-xs uppercase opacity-60 mt-1")
        with ui.row().classes("w-full gap-3 flex-wrap items-end"):
            models = self.state.get("lm_models") or []
            pinned = settings.LM_MODEL if settings.LM_MODEL in models else None
            if pinned is None and len(models) == 1:
                # Сервис объявил одну модель — не заставляем выбирать её руками.
                pinned = models[0]
            self.w["model"] = ui.select(
                models, value=pinned,
                label="Модель описания (пусто — по умолчанию)",
                with_input=True, new_value_mode="add-unique", clearable=True).props(
                "outlined dense").classes("grow min-w-64")
            self.w["recheck"] = ui.checkbox("Recheck: проверить кадр моделью")

        ui.label("Генерация").classes("text-xs uppercase opacity-60 mt-2")
        with ui.row().classes("w-full gap-3 flex-wrap items-end"):
            if "checkpoint" in fields:
                b = fields["checkpoint"]
                self.add_combo("checkpoint", "Чекпойнт генерации", b["class_type"],
                               b["input"], b["default"], "grow min-w-64")
            if "sampler" in fields:
                b = fields["sampler"]
                self.add_combo("sampler", "Сэмплер", b["class_type"], b["input"],
                               b["default"], "min-w-40")
            if "scheduler" in fields:
                b = fields["scheduler"]
                self.add_combo("scheduler", "Планировщик", b["class_type"], b["input"],
                               b["default"], "min-w-40")
        with ui.row().classes("w-full gap-3 flex-wrap items-end"):
            if "seed" in fields:
                self.w["seed"] = ui.number(
                    "Seed", placeholder=str(fields["seed"]["default"]),
                    precision=0, min=0).props("outlined dense").classes("w-32")
            if "denoise" in fields:
                self.w["denoise"] = ui.number(
                    "Denoise", placeholder=str(fields["denoise"]["default"]),
                    step=0.05).props("outlined dense").classes("w-32")
            if "cn_strength" in fields:
                self.w["cn_strength"] = ui.number(
                    "Сила ControlNet", placeholder=str(fields["cn_strength"]["default"]),
                    step=0.05).props("outlined dense").classes("w-36")
            self.w["style_weight"] = ui.number(
                "Вес стилевой LoRA", placeholder=str(meta.get("style_weight")),
                step=0.05).props("outlined dense").classes("w-44")
            if "steps" in fields:
                self.w["steps"] = ui.number(
                    "Шаги", placeholder=str(fields["steps"]["default"]),
                    precision=0, min=1).props("outlined dense").classes("w-24")
            if "cfg" in fields:
                self.w["cfg"] = ui.number(
                    "CFG", placeholder=str(fields["cfg"]["default"]),
                    step=0.5).props("outlined dense").classes("w-24")

        ui.label("Слоты LoRA").classes("text-xs uppercase opacity-60 mt-2")
        for slot in meta.get("loras") or []:
            role = slot.get("role") or "lora"
            node = str(slot.get("node"))
            with ui.row().classes("w-full gap-3 flex-wrap items-end"):
                self.add_combo(f"lora_name_{node}", f"LoRA · {ROLE_LABEL.get(role, role)}",
                               "LoraLoaderModelOnly", "lora_name", slot.get("value"),
                               "grow min-w-64")
                weight = ui.number("вес", value=slot.get("weight"), step=0.05).props(
                    "outlined dense").classes("w-24")
                self.w[f"lora_weight_{node}"] = weight

        graph = catalog.graph_summary(workflow)
        ui.label(f"В графе: {graph['checkpoint']} · {graph['sampler']}/"
                 f"{graph['scheduler']} · шагов {graph['steps']} · "
                 f"denoise {graph['denoise']} · CN {graph['cn_strength']}").classes(
            "text-xs opacity-60 mt-1")
        self.w["models_note"] = ui.label(self.models_note()).classes(
            "text-xs comic-warn")
        ui.label("Модель описания (LM Studio или другой OpenAI-совместимый сервис на "
                 "LM_URL / lm_url) и чекпойнт генерации (ComfyUI) — разные настройки: "
                 "первая смотрит фото, второй рисует.").classes("text-xs opacity-50")

    def on_style(self):
        """Смена стиля: общие поля остаются, стилевые — к значениям новой сборки."""
        keep = {key: self.w[key].value for key in ("seed", "model", "recheck")
                if key in self.w}
        if self._advanced_refresh:
            self._advanced_refresh()
        for key, value in keep.items():
            if key in self.w and value is not None:
                self.w[key].set_value(value)
        workflow = self.w["style"].value
        ui.notify(f"Denoise, ControlNet, шаги и LoRA сброшены к значениям {workflow}",
                  type="info")
        self.recalc(silent=True)

    # ------------------------------------------------------------ запрос
    def collect(self):
        meta = self.meta()
        if meta is None:
            return None, "сборка не готова: " + str(self.w["style"].value)
        try:
            api, _ = pp.load_workflow(self.w["style"].value)
        except pp.PipelineError as e:
            return None, str(e)
        problems = pp.check_bindings(api, meta, require_fields=True)
        if problems:
            return None, "; ".join(problems)
        fields = meta.get("fields") or {}
        info = self.state.get("info")
        over = {}
        for key in ("checkpoint", "steps", "cfg", "sampler", "scheduler"):
            widget = self.w.get(key)
            b = fields.get(key)
            if widget is None or b is None or widget.value in (None, ""):
                continue
            value = widget.value
            if b.get("type") == "INT":
                value = int(value)
            elif b.get("type") == "FLOAT":
                value = float(value)
            ok, why = catalog.check_value(info, b, value)
            if not ok:
                return None, why
            if value != b.get("default"):
                over[key] = value

        loras = {}
        for slot in meta.get("loras") or []:
            node = str(slot.get("node"))
            name_w = self.w.get(f"lora_name_{node}")
            weight_w = self.w.get(f"lora_weight_{node}")
            change = {}
            if name_w is not None and name_w.value and name_w.value != slot.get("value"):
                ok, why = catalog.check_value(
                    info, {"type": "COMBO", "class_type": "LoraLoaderModelOnly",
                           "input": "lora_name"}, name_w.value)
                if not ok:
                    return None, why
                change["name"] = name_w.value
            if weight_w is not None and weight_w.value is not None \
                    and float(weight_w.value) != float(slot.get("weight") or 0):
                change["weight"] = float(weight_w.value)
            if change:
                loras[str(slot.get("role") or node)] = change

        def number(key):
            widget = self.w.get(key)
            return float(widget.value) if widget is not None and widget.value is not None \
                else None

        seed = self.w.get("seed")
        return {
            "workflow": self.w["style"].value,
            "scene": (self.w["scene"].value or "").strip(),
            "variants": int(self.w["variants"].value or 1),
            "model": (self.w["model"].value if self.w.get("model") else None) or None,
            "negative_extra": (self.w["neg_add"].value or "").strip(),
            "negative_drop": (self.w["neg_drop"].value or "").strip(),
            "denoise": number("denoise"),
            "cn_strength": number("cn_strength"),
            "style_weight": number("style_weight"),
            "seed": int(seed.value) if seed is not None and seed.value is not None else None,
            "recheck": bool(self.w["recheck"].value) if self.w.get("recheck") else False,
            "overrides": ({"fields": over, "loras": loras} if (over or loras) else {}),
            "positive_override": (self.w["pos_custom"].value or "").strip()
            if self.w["pos_override"].value else None,
            "negative_override": (self.w["neg_custom"].value or "").strip()
            if self.w["neg_override"].value else None,
            "_manual": (self.w["desc"].value or "").strip() or None,
        }, ""

    def recalc(self, silent=False):
        data, why = self.collect()
        if data is None:
            if not silent:
                ui.notify(why, type="negative")
            return
        try:
            payload = pp.build_payload(
                data["workflow"], data["_manual"] or "", data["negative_extra"],
                denoise=data["denoise"], seed=data["seed"],
                style_weight=data["style_weight"], cn_strength=data["cn_strength"],
                overrides=data["overrides"],
                positive=data["positive_override"], negative=data["negative_override"])
        except pp.PipelineError as e:
            if not silent:
                ui.notify(str(e), type="negative")
            return
        self.w["pos_preview"].set_value(payload["positive"])
        self.w["neg_preview"].set_value(payload["negative"])
        if not self.w["pos_custom"].value:
            self.w["pos_custom"].set_value(payload["positive"])
        if not self.w["neg_custom"].value:
            self.w["neg_custom"].set_value(payload["negative"])

    def start(self, generate, manual):
        data, why = self.collect()
        if data is None:
            ui.notify(why, type="negative")
            return
        if self.state["upload_id"] is None:
            ui.notify("сначала загрузи фото", type="warning")
            return
        subject = data.pop("_manual")
        if manual and not subject:
            ui.notify("для ручного режима нужно описание", type="warning")
            return
        if self.w["pos_override"].value and not (self.w["pos_custom"].value or "").strip():
            ui.notify("включено переопределение positive, но текст пуст", type="warning")
            return
        if self.w["neg_override"].value and not (self.w["neg_custom"].value or "").strip():
            ui.notify("включено переопределение negative, но текст пуст", type="warning")
            return
        req = jobs.JobRequest(
            image="", workflow=data["workflow"], scene=data["scene"],
            variants=data["variants"], model=data["model"],
            subject=subject if manual else None,
            negative_extra=data["negative_extra"], negative_drop=data["negative_drop"],
            denoise=data["denoise"], cn_strength=data["cn_strength"],
            style_weight=data["style_weight"], seed=data["seed"],
            recheck=data["recheck"], overrides=data["overrides"],
            positive_override=data["positive_override"],
            negative_override=data["negative_override"],
            generate=generate, write_cache=not manual,
            client_id=uuid.uuid4().hex,
            start_comfy=True,
        )
        try:
            job = self.svc.submit(req, upload_id=self.state["upload_id"])
        except UploadError as e:
            ui.notify(str(e), type="negative")
            return
        self.state.update(job_id=job.id, snap_id=None, last_seq=0, signature=None,
                          selected=0, user_selected=False, ws="",
                          health_synced=False, health_during=False)
        self.w["log"].clear()
        self.w["error"].set_text("")
        self.w["stage"].set_text("Задание в очереди" if self.svc.active_id()
                                 else "Задание запущено")
        self.w["elapsed"].set_text("0:00")
        ui.run_javascript(
            f"history.replaceState(null, '', '/?job={job.id}')")
        ui.notify("задание в очереди" if self.svc.active_id() else "задание запущено",
                  type="info")

    # ------------------------------------------------------------ отрисовка
    def current_steps(self, snap):
        """Шаги только текущего прохода: новый вариант не наследует полоску."""
        return pass_steps(snap["events"], self.state.get("samplers") or [])

    def render_result(self, snap):
        box = self.w["result"]
        box.clear()
        frames = snap.get("frames") or []
        with box:
            if not frames:
                ui.label("Кадр появится здесь после «Старта».").classes(
                    "text-sm opacity-50 py-8 text-center w-full")
                if snap.get("error"):
                    ui.label(short(snap["error"], 400)).classes(
                        "comic-error text-sm whitespace-pre-wrap")
                return
            selected = min(self.state.get("selected") or 0, len(frames) - 1)
            frame = frames[selected]
            name = Path(frame["file"]).name
            url = f"/media/job/{snap['id']}/{name}"
            with ui.row().classes("w-full items-center gap-2 flex-wrap"):
                ui.label("Уточнённый кадр" if frame.get("refined") else "Основной кадр").classes(
                    "text-xs uppercase opacity-60")
                ui.label(f"seed {frame['seed']} · CN "
                         f"{fmt_setting(frame.get('cn_strength'))} · LoRA "
                         f"{fmt_setting(frame.get('style_weight'))} · denoise "
                         f"{fmt_setting(frame.get('denoise'))}").classes("text-xs opacity-70")
            with ui.element("div").classes(
                    "comic-frame w-full flex items-center justify-center rounded").style(
                    "height:62vh"):
                ui.image(url).classes("w-full h-full object-contain")
            with ui.row().classes("gap-3 items-center flex-wrap"):
                ui.link("Открыть в полном размере", url).props("target=_blank").classes(
                    "text-sm")
                ui.link("Скачать", url + "?download=1").props("target=_blank").classes(
                    "text-sm font-medium")
            if len(frames) > 1:
                with ui.row().classes("gap-2 items-center flex-wrap"):
                    for index, item in enumerate(frames):
                        thumb_name = Path(item["file"]).name
                        thumb = f"/media/job/{snap['id']}/{thumb_name}"
                        classes = ("w-16 h-16 object-cover rounded cursor-pointer border-2 "
                                   + ("border-primary" if index == selected
                                      else "border-transparent opacity-70"))
                        image = ui.image(thumb).classes(classes)
                        image.on("click", lambda _, i=index: self.select_frame(i))
                        with ui.column().classes("gap-0"):
                            ui.label("уточнённый" if item.get("refined") else "основной").classes(
                                "text-[10px] opacity-60")
                            ui.link("скачать", f"/media/job/{snap['id']}/{thumb_name}"
                                    "?download=1").props("target=_blank").classes(
                                "text-[10px]")
            positive = frame.get("positive") or ""
            negative = frame.get("negative") or ""
            if positive:
                with ui.expansion("Промпт этого кадра", icon="notes").classes("w-full"):
                    ui.label("Positive (ушёл в ComfyUI)").classes(
                        "text-[10px] uppercase opacity-50")
                    ui.label(positive).classes("text-xs whitespace-pre-wrap")
                    if negative:
                        ui.label("Negative").classes("text-[10px] uppercase opacity-50 mt-2")
                        ui.label(negative).classes("text-xs whitespace-pre-wrap")
            elif frame.get("subject"):
                with ui.expansion("Промпт этого кадра", icon="notes").classes("w-full"):
                    ui.label(frame["subject"]).classes("text-xs whitespace-pre-wrap")
                    ui.label("Точный промпт не записан: кадр старой версии.").classes(
                        "text-xs comic-warn mt-1")
            overrides = snap["request"].get("overrides") or {}
            parts = [f"{key}={value}" for key, value in
                     (overrides.get("fields") or {}).items()]
            for role, change in (overrides.get("loras") or {}).items():
                bits = [str(change.get("name") or "").strip(),
                        (f"вес {change['weight']}"
                         if change.get("weight") is not None else "")]
                parts.append(f"LoRA {ROLE_LABEL.get(role, role)}: "
                             + " ".join(x for x in bits if x))
            if parts:
                ui.label("Переопределено: " + ", ".join(parts)).classes("text-xs opacity-70")
            if snap.get("error"):
                tone = "Частично: " if frames else "Ошибка: "
                ui.label(tone + short(snap["error"], 400)).classes(
                    "comic-error text-sm whitespace-pre-wrap")

    def select_frame(self, index):
        self.state["selected"] = index
        self.state["user_selected"] = True
        if self.state.get("snap"):
            self.render_result(self.state["snap"])

    def refresh(self):
        self.w["link_badge"].set_text(
            "связь: есть" if ui.context.client.connected else "связь: нет")
        snap = self.svc.snapshot(self.state["job_id"]) if self.state["job_id"] else None
        if snap is None:
            self.w["stage"].set_text(missing_job_text(self.state["job_id"]))
            self.w["elapsed"].set_text("")
            self.w["pass"].set_text("")
            self.w["queue"].set_text("")
            self.w["progress"].set_visibility(False)
            return
        self.state["snap"] = snap
        if snap["id"] != self.state["snap_id"]:
            self.state.update(snap_id=snap["id"], last_seq=0, selected=0,
                              user_selected=False, signature=None)
            self.w["log"].clear()
        label = STAGE_LABEL.get(snap["stage"], snap["stage"])
        if snap["status"] == "queued":
            self.w["stage"].set_text(f"В очереди · позиция {snap['queue'] or 1}")
            self.w["elapsed"].set_text("")
            self.w["queue"].set_text("Один исполнитель: вторая задача ждёт первую.")
            self.w["pass"].set_text("")
            self.w["progress"].set_visibility(False)
        elif snap["running"]:
            self.w["error"].set_text("")
            step = self.current_steps(snap)
            variant = snap.get("variant")
            extra = f" · вариант {variant.get('index')}/{variant.get('total')}" \
                if variant else ""
            samplers = [str(x) for x in (self.state.get("samplers") or [])]
            # Разделяем каналы: шаги по WebSocket или запасной опрос истории.
            has_ws = any(e["stage"] in ("comfy_start", "comfy_cached", "sampling")
                         for e in snap["events"])
            self.w["pass"].set_text(
                "шаги: WebSocket ComfyUI" if has_ws else "шаги: опрос /history")
            self.w["stage"].set_text(f"{label}{extra}")
            self.w["elapsed"].set_text(fmt_time(snap["elapsed"]))
            self.w["queue"].set_text("")
            if step:
                value = float(step["data"].get("value") or 0)
                total = float(step["data"].get("max") or 1)
                node = str(step["data"].get("node") or "")
                which = ""
                if samplers and node in samplers:
                    which = f" · сэмплер {samplers.index(node) + 1}/{len(samplers)}"
                self.w["progress"].set_value(min(1.0, value / total))
                self.w["progress"].set_visibility(True)
                self.w["stage"].set_text(
                    f"шаги сэмплера{which}{extra} · шаг {int(value)}/{int(total)}")
            else:
                self.w["progress"].set_visibility(False)
                if snap["stage"] == "sampling":
                    self.w["stage"].set_text(f"ComfyUI готовит сэмплер{extra}")
        else:
            self.w["elapsed"].set_text(fmt_time(snap["elapsed"]))
            self.w["queue"].set_text("")
            self.w["pass"].set_text("")
            self.w["progress"].set_visibility(False)
            if snap["status"] == "described":
                self.w["stage"].set_text("Описание готово — можно править и генерировать")
            elif snap["status"] == "done":
                self.w["stage"].set_text(f"Готово · кадров {len(snap['frames'])}")
            elif snap["status"] == "partial":
                self.w["stage"].set_text(
                    f"Частично · кадров {len(snap['frames'])}, были ошибки")
            else:
                self.w["stage"].set_text("Ошибка: " + label)
            self.w["error"].set_text(
                short(snap["error"].splitlines()[0], 300) if snap.get("error") else "")

        # Промпт формы живёт отдельно от промпта сохранённого кадра:
        # таймер не должен затирать «Пересчитать промпт».
        same_photo = (bool(self.state["upload_tag"])
                      and snap.get("source_tag") == self.state["upload_tag"])
        if snap.get("description") and snap["status"] in ("described", "done", "partial"):
            subject = snap["description"].get("subject", "")
            if subject and same_photo:
                current = (self.w["desc"].value or "").strip()
                if not current or current == (self.state["desc_auto"] or "").strip():
                    self.w["desc"].set_value(subject)
                    self.state["desc_auto"] = subject
                    self.recalc(silent=True)

        new = [e for e in snap["events"] if e["seq"] > self.state["last_seq"]]
        for event in new:
            self.state["last_seq"] = max(self.state["last_seq"], event["seq"])
            if event.get("message"):
                self.w["log"].push(f"[{fmt_time(event['t'])}] {event['message']}")
        # Headless ComfyUI поднялся — обновляем бейдж и списки моделей.
        if any(e["stage"] == "comfy" and (e.get("data") or {}).get("url") for e in new) \
                and not self.state.get("health_during"):
            self.state["health_during"] = True
            ui.timer(0.5, self.refresh_health, once=True)

        signature = (snap["id"], snap["status"], len(snap["frames"]), snap["error"])
        if signature != self.state.get("signature"):
            if snap["frames"] and not self.state.get("user_selected"):
                refined = [i for i, f in enumerate(snap["frames"]) if f.get("refined")]
                self.state["selected"] = refined[-1] if refined else 0
            self.state["signature"] = signature
            self.render_result(snap)

        # После задания headless ComfyUI мог подняться или погаснуть — обновляем бейдж.
        if not snap["running"] and not self.state.get("health_synced"):
            self.state["health_synced"] = True
            ui.timer(0.3, self.refresh_health, once=True)


def register_page():
    @ui.page("/")
    def index():
        styles = catalog.style_list()
        ComicPage(styles).build()

    # Второй интерфейс рядом со старым: своя страница, тот же сервис заданий.
    # Импорт внутри функции — чтобы v2 мог взять `service()` из этого модуля.
    from .v2 import register_page as register_v2
    register_v2()


def start(host="127.0.0.1", port=8080, show=True, reload=False):
    register_routes()
    register_page()
    ui.run(host=host, port=port, title="comic-comfy", show=show, reload=reload,
           dark=True, uvicorn_logging_level="warning", language="ru")
