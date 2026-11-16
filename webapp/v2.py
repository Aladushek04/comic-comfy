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

Сцена пока пустая: фазы, вкладки и кадр — следующие фазы, каждая добавит свой
модуль в `webapp/components`.
"""
import asyncio

import comfy_launch as cl
import photo_prompt as pp
from nicegui import Event, core, ui

from . import catalog, labels, push, theme
from .collect import collect_form
from .components import photo_slot, run_controls, start_action, style_picker, topbar
from .labels import STAGE_LABEL, short
from .service import UploadError
from .ui import service

# Состояния сервиса и состояния страницы — разные словари: «ошибка» сервиса
# показывается как «failed», а «истёкшее задание» сервис вообще не знает.
SERVICE_STATUS = {"error": "failed"}


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
        }
        self.dark = None
        self.push_event = None
        self.watchdog = None
        self.topbar = None
        self.photo = None
        self.picker = None
        self.controls = None
        self.start = None

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
                    self.picker = style_picker.StylePicker(
                        self.styles, self.on_style).build()
                    self.controls = run_controls.RunControls().build()
                    self.start = start_action.StartAction(self.submit).build()
                with ui.element("main") as stage:
                    theme.mark(stage, "stage")
                    self.empty_stage()
        self.watchdog = ui.timer(2.0, self.tick, active=False)
        self.sync()

    def empty_stage(self):
        """Сцена до появления кадра: рамка в высоту будущего кадра."""
        with ui.element("section") as card:
            theme.mark(card, "card")
            theme.mark(ui.label("Кадр"), "title")
            theme.mark(ui.label("Кадр появится здесь после «Старта»."), "stage-empty")

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

        Догон здесь один и тот же путь — полный снапшот; поля патча появятся
        вместе с PhaseTimeline, когда рисовать станет больше, чем состояние.
        """
        if job_id == self.state["job_id"]:
            if rev and rev <= self.state["last_rev"]:
                return
            self.state["last_rev"] = max(self.state["last_rev"], rev or 0)
        self.sync()

    async def on_connect(self, client=None):
        self.subscribe()
        self.topbar.set_link(True)
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
        active = bool(snap and snap["running"])
        if active != self.state["active"] and self.watchdog is not None:
            self.state["active"] = active
            if active:
                self.watchdog.activate()
            else:
                self.watchdog.deactivate()

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
