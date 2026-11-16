"""Страница V2 на /v2: верхняя панель, рельс, сцена. Старая страница живёт на /.

Правила страницы (спецификация, разделы 2–6):

- только ПК: ниже 1280 страница не перестраивается, а показывает требование;
- один акцент: фиолетовый из токенов `--accent-fill` / `--accent`, ни одного
  дефолтного синего NiceGUI;
- ни одного опроса раз в 600 мс: состояние приходит толчком (`nicegui.Event`),
  полным снапшотом при подключении и сторожевым таймером 2 с, который работает
  только пока задание живо;
- номер задания живёт в адресе (`/v2?job=…`), поэтому новая вкладка не
  подхватывает чужую работу (грабли 0.14 №7).

Сцена ничего не считает сама: фазы, шаг прохода, кадры, параметры, промпт и
история приходят из `service.snapshot()` и `comic.run`. Страница только решает,
какая запись выбрана и какая вкладка открыта; и то, и другое живёт в
`app.storage.tab`, поэтому перезагрузка вкладки возвращает тот же кадр.

Страница не решает за сервис: «Отмена» зовёт `service.cancel` (очередь — снять,
работающему — `/interrupt`).
"""
import asyncio
from pathlib import Path

import comfy_launch as cl
import photo_prompt as pp
from nicegui import Event, app, core, ui

from . import catalog, labels, push, theme
from .collect import collect_form
from .components import (error_zone, frame_stage, frame_strip, history_panel,
                        params_panel, phase_timeline, photo_slot, prompt_panel,
                        run_controls, stage_tabs, start_action, style_picker, topbar)
from .labels import STAGE_LABEL, short
from .service import UploadError
from .ui import service

# Состояния сервиса и состояния страницы — разные словари: «ошибка» сервиса
# показывается как «failed», а «истёкшее задание» сервис вообще не знает.
SERVICE_STATUS = {"error": "failed"}

# Ключи выбора в `app.storage.tab`: вкладка и кадр переживают перезагрузку.
TAB_KEY = "v2_tab"
FRAME_KEY = "v2_frame"
USER_KEY = "v2_user_frame"


class V2Page:
    """Одна вкладка: черновик формы, подписка на задание и отрисовка."""

    def __init__(self, styles):
        self.svc = service()
        self.styles = styles
        self.state = {
            "job_id": None,
            "last_rev": 0,
            "snap": None,
            "active": False,
            "comfy": False,
            "launchable": cl.available(),
            "reason": "",
            "photo": False,
            "styles_ok": any(not s["problems"] for s in styles),
            "samplers": [],
            "samplers_wf": None,
            "frames_job": None,
            "selected": None,
            "user_selected": False,
        }
        self.dark = None
        self.push_event = None
        self.watchdog = None
        self.topbar = None
        self.photo = None
        self.picker = None
        self.controls = None
        self.start = None
        self.timeline = None
        self.tabs = None
        self.frame = None
        self.strip = None
        self.params = None
        self.prompt = None
        self.history = None
        self.error = None

    # ------------------------------------------------------------ разметка
    def build(self, dark):
        ui.add_head_html(theme.css())
        self.dark = ui.dark_mode(dark)
        client = ui.context.client
        request = client.request
        self.state["job_id"] = (request.query_params.get("job") or None) if request else None
        client.on_connect(self.on_connect)
        client.on_disconnect(self.on_disconnect)
        client.on_delete(self.on_delete)

        theme.mark(ui.label("Нужен экран от 1280 px"), "narrow")
        with ui.element("div") as root:
            theme.mark(root, "app")
            self.topbar = topbar.TopBar(on_health=self.refresh_health,
                                        on_theme=self.toggle_theme).build(dark)
            with ui.element("div") as layout:
                theme.mark(layout, "layout")
                with ui.element("aside") as rail:
                    theme.mark(rail, "rail")
                    # Своей прокрутки у рельса нет: список стилей не обрезается,
                    # прокручивается страница, а «Старт» липнет к низу окна.
                    self.photo = photo_slot.PhotoSlot(self.svc, self.on_photo).build()
                    self.picker = style_picker.StylePicker(self.styles, self.on_style).build()
                    self.controls = run_controls.RunControls().build()
                    self.start = start_action.StartAction(self.submit, self.cancel).build()
                with ui.element("main") as stage:
                    theme.mark(stage, "stage")
                    self.timeline = phase_timeline.PhaseTimeline().build()
                    self.error = error_zone.ErrorZone().build()
                    self.tabs = stage_tabs.StageTabs(self.on_tab).build()
                    with self.tabs.panel("frame"):
                        self.frame = frame_stage.FrameStage().build()
                        self.strip = frame_strip.FrameStrip(self.select_frame).build()
                    with self.tabs.panel("params"):
                        self.params = params_panel.ParamsPanel().build()
                    with self.tabs.panel("prompt"):
                        self.prompt = prompt_panel.PromptPanel().build()
                    with self.tabs.panel("history"):
                        self.history = history_panel.HistoryPanel().build()
        self.watchdog = ui.timer(2.0, self.tick, active=False)
        self.sync()

    # ------------------------------------------------------------ связь
    def subscribe(self):
        """Подписка в scope страницы: NiceGUI снимет её при удалении клиента."""
        if self.push_event is None:
            self.push_event = Event()
            self.push_event.subscribe(self.on_push)
        push.hub.subscribe(self._wake)

    def _wake(self, rev, job_id):
        """Из потока задания: будим клиента в его цикле, а не в чужом."""
        event = self.push_event
        loop = core.loop
        if event is None or loop is None or not loop.is_running():
            return
        try:
            loop.call_soon_threadsafe(event.emit, rev, job_id)
        except RuntimeError:
            pass                # цикл уже закрыт: сервер останавливается

    def on_push(self, rev, job_id):
        """Патч задания: rev монотонен, разрыв значит «пропустил шаг — догоняй».

        Догон здесь один и тот же путь — полный снапшот: полей в патче немного,
        а рисовать по ним фазы, шаг и кадры пришлось бы вторым путём отрисовки.
        """
        if job_id == self.state["job_id"]:
            if rev and rev <= self.state["last_rev"]:
                return
            self.state["last_rev"] = max(self.state["last_rev"], rev or 0)
        self.sync()

    async def on_connect(self, client=None):
        self.subscribe()
        self.topbar.set_link(True)
        self.restore_tab()
        self.sync()
        await self.refresh_health()

    def on_disconnect(self, client=None):
        self.topbar.set_link(False)
        push.hub.unsubscribe(self._wake)        # подписка снимается вместе с вкладкой

    def on_delete(self, client=None):
        push.hub.unsubscribe(self._wake)
        if self.push_event is not None:
            self.push_event.unsubscribe(self.on_push)
            self.push_event = None

    # ------------------------------------------------------------ выбор вкладки и кадра
    def tab_storage(self):
        """Хранилище вкладки браузера; до подключения его ещё нет."""
        try:
            return app.storage.tab
        except (RuntimeError, AssertionError):
            return {}

    def restore_tab(self):
        """Перезагрузка вкладки возвращает ту же панель и тот же кадр."""
        data = self.tab_storage()
        name = data.get(TAB_KEY)
        if name in stage_tabs.NAMES:
            self.tabs.select(name, notify=False)
        self.state["selected"] = data.get(FRAME_KEY) or None
        self.state["user_selected"] = bool(data.get(USER_KEY))

    def on_tab(self, name):
        """Смена вкладки: запоминаем и, если надо, читаем паспорт кадра."""
        self.tab_storage()[TAB_KEY] = name
        if name == "history":
            self.history.refresh(*self.history_key())

    def history_key(self, snap=None):
        """Папка кадра и число кадров задания: по ним видно, что паспорт изменился."""
        snap = snap or self.state["snap"] or {}
        frame = self.selected_frame(snap)
        return (str((frame or {}).get("folder") or ""), len(snap.get("frames") or ()))

    def select_frame(self, key):
        """Выбор миниатюры: дальше автовыбор не перебивает выбор человека."""
        self.state["selected"] = key
        self.state["user_selected"] = True
        data = self.tab_storage()
        data[FRAME_KEY] = key
        data[USER_KEY] = True
        self.render_frames(self.state["snap"])

    # ------------------------------------------------------------ сервисы
    async def refresh_health(self):
        """Проверка сервисов: только бейджи и списки, никакой перерисовки."""
        if self.topbar is None:
            return
        self.topbar.set("comfy", "ComfyUI: проверяю")
        self.topbar.set("lm", "Описание: проверяю")
        info = await asyncio.to_thread(catalog.health, pp.LM)
        lm = info["lm"]
        comfy = info["comfy"]
        launchable = cl.available()
        name = "LM Studio" if lm.get("provider") == "lmstudio" else "Сервис описаний"
        text = f"{name}: " + ("готов" if lm["ok"] else "не отвечает")
        if lm["ok"] and lm.get("models"):
            text += f" ({len(lm['models'])})"
        self.topbar.set("lm", text, "ok" if lm["ok"] else "warn")
        if comfy["ok"]:
            self.topbar.set("comfy", "ComfyUI: готов", "ok")
            reason = ""
        elif launchable:
            self.topbar.set("comfy", "ComfyUI: поднимется сам", "warn")
            reason = ""
        else:
            self.topbar.set("comfy", "ComfyUI: не отвечает", "err")
            reason = short(comfy["error"] or comfy["url"], 120)
        self.state.update(comfy=bool(comfy["ok"]), launchable=launchable, reason=reason)
        self.start.update(comfy=self.state["comfy"], launchable=launchable,
                          reason=reason, styles=self.state["styles_ok"])

    def toggle_theme(self):
        """Тёмная тема по умолчанию, светлая — такой же выбор; помним оба."""
        self.dark.toggle()
        dark = bool(self.dark.value)
        try:
            theme.save(dark)
        except OSError as e:
            ui.notify(f"тему не удалось сохранить: {e}", type="warning")
        self.topbar.set_dark(dark)

    # ------------------------------------------------------------ форма
    def on_photo(self, upload_id, tag):
        self.state["photo"] = upload_id is not None
        self.start.update(photo=self.state["photo"])

    def on_style(self, workflow):
        """Смена стиля: стилевые поля живут в meta, поэтому форма ничего не хранит."""
        self.state["style"] = workflow

    def submit(self, generate, manual):
        """Старт: JobRequest из формы в очередь сервиса. Без ComfyUI — описание."""
        if not self.state["styles_ok"]:
            ui.notify("нет годных сборок: соберите графы", type="warning")
            return
        try:
            meta = catalog.load_meta(self.picker.value)
        except catalog.CatalogError as e:
            ui.notify(str(e), type="negative")
            return
        values = {"image": self.photo.upload_id, "workflow": self.picker.value,
                  "generate": generate, "manual": manual, "subject": "",
                  **self.controls.values}
        request, why = collect_form(values, meta)
        if request is None:
            ui.notify(why, type="warning")
            return
        try:
            job = self.svc.submit(request, upload_id=self.photo.upload_id)
        except UploadError as e:
            ui.notify(str(e), type="negative")
            return
        self.state.update(job_id=job.id, last_rev=0, snap=None)
        ui.run_javascript(f"history.replaceState(null, '', '/v2?job={job.id}')")
        self.sync()
        ui.notify("задание в очереди" if job.status == "queued" else "задание запущено")

    async def cancel(self):
        """Отмена задания: сервис решает, снять из очереди или прервать ComfyUI.

        `POST /interrupt` — сетевой вызов, поэтому он уходит в поток: цикл
        страницы не должен ждать ответа ComfyUI.
        """
        job_id = self.state["job_id"]
        if not job_id:
            return
        ok, note = await asyncio.to_thread(self.svc.cancel, job_id)
        if not ok or note:
            ui.notify(note or "отменять нечего", type="warning")
        self.sync()

    # ------------------------------------------------------------ состояние
    def sync(self):
        """Полный снапшот: он же «догон» после разрыва rev и после переподключения."""
        if self.start is None:
            return
        job_id = self.state["job_id"]
        snap = self.svc.snapshot(job_id) if job_id else None
        self.state["snap"] = snap
        if snap is None:
            if job_id:
                status, detail = "expired", labels.EXPIRED_HINT
            else:
                status = "empty" if self.state["styles_ok"] else "blocked"
                detail = ""
        else:
            status = SERVICE_STATUS.get(snap["status"], snap["status"])
            detail = STAGE_LABEL.get(snap["stage"], snap["stage"])
        self.start.update(status=status, queue=(snap or {}).get("queue") or 0,
                          detail=detail, styles=self.state["styles_ok"])
        self.topbar.set_queue(*self._queue(snap))
        self.render(snap, status)
        active = bool(snap and snap["running"])
        if active != self.state["active"] and self.watchdog is not None:
            self.state["active"] = active
            if active:
                self.watchdog.activate()
            else:
                self.watchdog.deactivate()

    def render(self, snap, status):
        """Сцена: фазы, ошибка и кадры. Всё остальное — по выбору кадра."""
        self.timeline.set(snap, status, self.samplers(snap))
        self.error.set(snap, status)
        self.render_frames(snap, status)

    def render_frames(self, snap, status=None):
        """Полоса и выбранный кадр. Выбор человека главнее автовыбора."""
        snap = snap or {}
        frames = snap.get("frames") or []
        job_id = snap.get("id") or ""
        if job_id != self.state["frames_job"]:
            previous = self.state["frames_job"]
            self.state["frames_job"] = job_id
            self.strip.reset()
            if previous:
                # Другое задание: прежний выбор к нему не относится.
                self.state["selected"] = None
                self.state["user_selected"] = False
        keys = [Path(str(f.get("file") or "")).name for f in frames]
        if frames and not self.state["user_selected"]:
            refined = [k for k, f in zip(keys, frames) if f.get("refined")]
            self.state["selected"] = refined[-1] if refined else keys[0]
        elif self.state["selected"] not in keys:
            self.state["selected"] = keys[0] if keys else None
        self.strip.set(frames, job_id, self.state["selected"])
        frame = self.selected_frame(snap)
        hint = labels.EXPIRED_HINT if status == "expired" else ""
        self.frame.set(frame, job_id, hint)
        self.params.set(frame, snap.get("request") or {})
        self.prompt.set(frame)
        if self.tabs.current == "history":
            self.history.refresh(*self.history_key(snap))

    def selected_frame(self, snap=None):
        """Запись выбранного кадра по имени файла: номера в списке не годятся."""
        frames = (snap or self.state["snap"] or {}).get("frames") or []
        key = self.state["selected"]
        for frame in frames:
            if Path(str(frame.get("file") or "")).name == key:
                return frame
        return None

    def samplers(self, snap):
        """Узлы-сэмплеры сборки задания: шаг без них не отличить от препроцессора."""
        workflow = ((snap or {}).get("request") or {}).get("workflow") or ""
        if workflow != self.state["samplers_wf"]:
            self.state["samplers_wf"] = workflow
            try:
                meta = catalog.load_meta(workflow) if workflow else {}
            except catalog.CatalogError:
                meta = {}
            self.state["samplers"] = [str(x) for x in (meta.get("samplers") or [])]
        return self.state["samplers"]

    @staticmethod
    def _queue(snap):
        """Очередь в одном бейдже: свободна, идёт или позиция в очереди."""
        if snap is None:
            return "очередь: свободна", ""
        if snap["status"] == "queued":
            return f"очередь: позиция {snap['queue'] or 1}", "warn"
        if snap["running"]:
            return "очередь: идёт", "warn"
        return "очередь: свободна", ""

    def tick(self):
        """Сторожевой таймер: push не пришёл — берём снапшот сами.

        Работает только пока задание живо: `sync` гасит его на первом же
        снапшоте с готовым заданием, поэтому простоя в опросе нет.
        """
        self.sync()


def register_page():
    """Маршрут /v2. Зовётся из `webapp.ui.register_page`: точка входа одна."""
    push.watch(service())        # будильник на события заданий, сервис не меняется

    @ui.page("/v2", dark=True, title="comic-comfy · V2", language="ru")
    def page_v2():
        # Тема попадает в первый HTML: цветовая схема берётся из файла выбора,
        # поэтому вспышки нет. `Page` — общий объект маршрута, а выбор темы один
        # на всех (out\theme.json), так что запись сюда и есть «текущая тема».
        dark = theme.read()
        ui.context.client.page.dark = dark
        V2Page(catalog.style_list()).build(dark)
