"""Страница NiceGUI: форма, таймер и результат.

Здесь нет ни описания фото, ни генерации: страница собирает JobRequest,
ставит его в service.JobService и рисует события задания. Всё, что относится
к графам, живёт в catalog.py, а к пайплайну — в jobs.py.
"""
import asyncio
import uuid
from pathlib import Path

import jobs
import photo_prompt as pp
from nicegui import app, ui
from starlette.responses import FileResponse, Response

from . import catalog
from .service import JobService, UploadError

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
    "error": "ошибка",
    "preview": "итоговый промпт",
}

STATUS_COLOR = {
    "queued": "grey", "running": "primary", "done": "positive",
    "described": "positive", "partial": "warning", "error": "negative",
}


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
    """Варианты из /object_info плюс текущее значение графа.

    Сервер может не знать файл из графа (модель не скачана) — тогда текущее
    значение всё равно должно быть выбрано, иначе форма не построится.
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


def register_page():
    @ui.page("/")
    def index():
        svc = service()
        styles = catalog.style_list()
        usable = [s for s in styles if not s["problems"]]
        state = {"upload_id": None, "job_id": None, "snap_id": None,
                 "last_seq": 0, "info": None, "desc_auto": "", "frames": -1}
        widgets = {}

        ui.page_title("comic-comfy")
        with ui.column().classes("w-full max-w-6xl mx-auto gap-4 p-4"):
            with ui.row().classes("w-full items-center justify-between"):
                with ui.column().classes("gap-0"):
                    ui.label("comic-comfy").classes("text-2xl font-semibold")
                    ui.label("фото → описание → кадр. Всё локально").classes("text-xs opacity-60")
                with ui.row().classes("items-center gap-2"):
                    comfy_badge = ui.badge("ComfyUI: проверяю", color="grey")
                    lm_badge = ui.badge("LM Studio: проверяю", color="grey")
                    ui.button("Проверить сервисы", on_click=lambda: refresh_health()).props("flat dense")

            if not usable:
                with ui.card().classes("w-full"):
                    ui.label("Нет готовых фото-сборок").classes("text-lg font-medium")
                    ui.label("Соберите графы: python tools\\build_workflows.py — "
                             "и обновите страницу.").classes("text-sm opacity-70")
                    for s in styles:
                        ui.label(f"{s['workflow']}: " + "; ".join(s["problems"])).classes(
                            "text-xs text-red-600")
                return

            for s in styles:
                if s["problems"]:
                    ui.label(f"{s['workflow']}: " + "; ".join(s["problems"])).classes(
                        "text-xs text-red-600")

            # ------------------------------------------------ фото
            with ui.card().classes("w-full"):
                ui.label("1. Фото").classes("text-lg font-medium")
                with ui.row().classes("items-start gap-4 w-full"):
                    with ui.column().classes("gap-2"):
                        upload = ui.upload(
                            label="Выбрать фото (PNG, JPG, WebP)", auto_upload=True,
                            max_file_size=30 * 1024 * 1024).props("accept=image/*")
                        upload_name = ui.label("файл не выбран").classes("text-xs opacity-60")
                    preview = ui.image().classes("w-48 rounded border")
                    preview.set_visibility(False)

            # ------------------------------------------------ задание
            with ui.card().classes("w-full"):
                ui.label("2. Задание").classes("text-lg font-medium")
                with ui.row().classes("w-full items-end gap-4 flex-wrap"):
                    style_sel = ui.select(
                        {s["workflow"]: s["label"] for s in usable},
                        value=next((s["workflow"] for s in usable
                                    if s["workflow"] == "07_photo_thepit"),
                                   usable[0]["workflow"]),
                        label="Стиль",
                        on_change=lambda: on_style()).props("outlined dense").classes("min-w-64")
                    scene_in = ui.input("Сцена (необязательно)",
                                        placeholder="на пляже, закат, улица").props(
                        "outlined dense").classes("min-w-64")
                    variants_in = ui.number("Вариантов", value=1, min=1, max=8,
                                            precision=0).props("outlined dense").classes("w-28")
                    recheck_in = ui.checkbox("Recheck (проверить кадр моделью)")

                with ui.row().classes("gap-2 mt-2"):
                    start_btn = ui.button("Старт", on_click=lambda: start(True, False))
                    ui.button("Только описание", on_click=lambda: start(False, False)).props(
                        "outline")
                    ui.button("Сгенерировать по описанию",
                              on_click=lambda: start(True, True)).props("outline")
                    ui.button("Пересчитать промпт", on_click=lambda: recalc()).props("flat")

                desc_area = ui.textarea(
                    "Описание (subject) — можно править",
                    placeholder="заполнится после «Только описание» или авто-запуска").props(
                    "outlined autogrow").classes("w-full")

            # ------------------------------------------------ ход и результат
            with ui.card().classes("w-full"):
                with ui.row().classes("w-full items-center justify-between"):
                    stage_label = ui.label("Задание не запущено").classes("text-lg font-medium")
                    elapsed_label = ui.label("").classes("font-mono text-sm opacity-70")
                queue_label = ui.label("").classes("text-xs opacity-70")
                progress = ui.linear_progress(value=0).props("instant-feedback")
                progress.set_visibility(False)
                result_box = ui.column().classes("w-full gap-2 mt-2")
                error_label = ui.label("").classes("text-red-600 text-sm")
                with ui.expansion("Журнал задания", icon="list").classes("w-full"):
                    log = ui.log(max_lines=300).classes("w-full h-40 text-xs font-mono")

            # ------------------------------------------------ расширенные
            with ui.expansion("Расширенные настройки", icon="tune").classes("w-full"):
                @ui.refreshable
                def render_advanced():
                    _advanced()

            with ui.expansion("Итоговый промпт", icon="notes").classes("w-full"):
                ui.label("Переопределение заменяет собранный текст целиком и главнее "
                         "описания и тегов негатива.").classes("text-xs opacity-70")
                pos_preview = ui.textarea("Positive, который уйдёт в ComfyUI").props(
                    "outlined autogrow readonly").classes("w-full")
                neg_preview = ui.textarea("Negative, который уйдёт в ComfyUI").props(
                    "outlined autogrow readonly").classes("w-full")
                with ui.row().classes("items-center gap-2"):
                    pos_override = ui.checkbox("Переопределить positive целиком")
                    neg_override = ui.checkbox("Переопределить negative целиком")
                pos_custom = ui.textarea("Полный positive (переопределение)").props(
                    "outlined autogrow").classes("w-full")
                neg_custom = ui.textarea("Полный negative (переопределение)").props(
                    "outlined autogrow").classes("w-full")
                pos_custom.set_visibility(False)
                neg_custom.set_visibility(False)
                pos_override.on_value_change(
                    lambda e: pos_custom.set_visibility(bool(e.value)))
                neg_override.on_value_change(
                    lambda e: neg_custom.set_visibility(bool(e.value)))
                with ui.row().classes("items-end gap-2"):
                    neg_add = ui.input("Добавить в негатив").props("outlined dense").classes(
                        "min-w-64")
                    neg_drop = ui.input("Убрать из негатива").props("outlined dense").classes(
                        "min-w-64")

            ui.label("Кадры и паспорт — в out/. Тяжёлые модели запускает пользователь: "
                     "страница только проверяет, что они уже работают.").classes(
                "text-xs opacity-60")

        # ------------------------------------------------------ обработчики
        def style_meta():
            try:
                return catalog.load_meta(style_sel.value)
            except catalog.CatalogError as e:
                ui.notify(str(e), type="negative")
                return None

        def on_style():
            state["desc_auto"] = ""
            render_advanced.refresh()
            recalc(silent=True)

        async def on_upload(e):
            data = await e.file.read()
            try:
                uid, _ = await asyncio.to_thread(svc.save_upload, e.file.name, data)
            except UploadError as ex:
                ui.notify(str(ex), type="negative")
                return
            state["upload_id"] = uid
            upload_name.set_text(e.file.name)
            preview.set_source(f"/media/upload/{uid}")
            preview.set_visibility(True)
            ui.notify("фото загружено", type="positive")

        upload.on_upload(on_upload)

        async def refresh_health():
            comfy_badge.set_text("ComfyUI: проверяю")
            lm_badge.set_text("LM Studio: проверяю")
            info = await asyncio.to_thread(catalog.health, pp.LM, catalog.find_comfy())
            comfy_badge.set_text("ComfyUI: " + ("готов" if info["comfy"]["ok"] else "не отвечает"))
            comfy_badge.props(f'color={"positive" if info["comfy"]["ok"] else "negative"}')
            lm_badge.set_text("LM Studio: " + ("готов" if info["lm"]["ok"] else "не отвечает"))
            lm_badge.props(f'color={"positive" if info["lm"]["ok"] else "warning"}')
            state["lm_models"] = info["lm"]["models"]
            state["info"] = await asyncio.to_thread(catalog.object_info, catalog.find_comfy())
            render_advanced.refresh()

        # ------------------------------------------------ расширенная панель
        def _advanced():
            widgets.clear()
            meta = style_meta()
            if meta is None:
                return
            fields = meta.get("fields") or {}
            info = state.get("info")
            default_model = meta.get("label") or style_sel.value
            ui.label(f"{default_model}: значения по умолчанию взяты из сборки "
                     f"{style_sel.value}. Пустое поле — как в графе, 0 — явный ноль.").classes(
                "text-xs opacity-70")
            if info is None:
                ui.label("ComfyUI не ответил: списки моделей недоступны, "
                         "оставлены значения из графа.").classes("text-xs text-orange-600")

            with ui.row().classes("w-full gap-4 flex-wrap"):
                if "seed" in fields:
                    widgets["seed"] = ui.number(
                        "Seed", placeholder=str(fields["seed"]["default"]),
                        precision=0, min=0).props("outlined dense").classes("w-32")
                if "denoise" in fields:
                    widgets["denoise"] = ui.number(
                        "Denoise", placeholder=str(fields["denoise"]["default"]),
                        step=0.05).props("outlined dense").classes("w-32")
                if "cn_strength" in fields:
                    widgets["cn_strength"] = ui.number(
                        "Сила ControlNet", placeholder=str(fields["cn_strength"]["default"]),
                        step=0.05).props("outlined dense").classes("w-36")
                widgets["style_weight"] = ui.number(
                    "Вес стилевой LoRA", placeholder=str(meta.get("style_weight")),
                    step=0.05).props("outlined dense").classes("w-36")
                if "steps" in fields:
                    widgets["steps"] = ui.number(
                        "Шаги", placeholder=str(fields["steps"]["default"]),
                        precision=0, min=1).props("outlined dense").classes("w-28")
                if "cfg" in fields:
                    widgets["cfg"] = ui.number(
                        "CFG", placeholder=str(fields["cfg"]["default"]),
                        step=0.5).props("outlined dense").classes("w-28")

            with ui.row().classes("w-full gap-4 flex-wrap"):
                if "checkpoint" in fields:
                    b = fields["checkpoint"]
                    opts = _select_options(info, b["class_type"], b["input"], b["default"])
                    widgets["checkpoint"] = ui.select(
                        opts, value=b["default"], label="Чекпойнт").props(
                        "outlined dense").classes("min-w-72")
                if "sampler" in fields:
                    b = fields["sampler"]
                    opts = _select_options(info, b["class_type"], b["input"], b["default"])
                    widgets["sampler"] = ui.select(
                        opts, value=b["default"], label="Сэмплер").props(
                        "outlined dense").classes("min-w-48")
                if "scheduler" in fields:
                    b = fields["scheduler"]
                    opts = _select_options(info, b["class_type"], b["input"], b["default"])
                    widgets["scheduler"] = ui.select(
                        opts, value=b["default"], label="Планировщик").props(
                        "outlined dense").classes("min-w-48")
                models = state.get("lm_models") or []
                widgets["model"] = ui.select(
                    models, label="Модель LM Studio (пусто — первая vision)",
                    with_input=True, new_value_mode="add-unique", clearable=True).props(
                    "outlined dense").classes("min-w-72")

            with ui.row().classes("w-full gap-4 flex-wrap"):
                for slot in meta.get("loras") or []:
                    role = slot.get("role") or "lora"
                    node = str(slot.get("node"))
                    opts = _select_options(info, "LoraLoaderModelOnly", "lora_name",
                                           slot.get("value"))
                    widgets[f"lora_name_{node}"] = ui.select(
                        opts, value=slot.get("value"), label=f"LoRA · {role}").props(
                        "outlined dense").classes("min-w-64")
                    widgets[f"lora_weight_{node}"] = ui.number(
                        f"вес · {role}", value=slot.get("weight"),
                        step=0.05).props("outlined dense").classes("w-28")

            with ui.row().classes("w-full gap-4 flex-wrap"):
                graph = catalog.graph_summary(style_sel.value)
                ui.label(
                    f"Граф: {graph['checkpoint']} · сэмплер {graph['sampler']}/"
                    f"{graph['scheduler']} · шагов {graph['steps']} · "
                    f"denoise {graph['denoise']} · CN {graph['cn_strength']}").classes(
                    "text-xs opacity-70")
            ui.label("Модель LM Studio и чекпойнт ComfyUI — разные настройки: "
                     "первая описывает фото, второй рисует.").classes("text-xs opacity-60")

        # ------------------------------------------------ запрос
        def collect():
            meta = style_meta()
            if meta is None:
                return None, "сборка не готова: " + style_sel.value
            api, _ = pp.load_workflow(style_sel.value)
            problems = pp.check_bindings(api, meta, require_fields=True)
            if problems:
                return None, "; ".join(problems)

            fields = meta.get("fields") or {}
            info = state.get("info")
            over = {}
            for key in ("checkpoint", "steps", "cfg", "sampler", "scheduler"):
                widget = widgets.get(key)
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
                name_w = widgets.get(f"lora_name_{node}")
                weight_w = widgets.get(f"lora_weight_{node}")
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

            number = lambda key: (float(widgets[key].value)
                                  if widgets.get(key) is not None
                                  and widgets[key].value is not None else None)
            seed = widgets.get("seed")
            manual = desc_area.value.strip() or None
            return {
                "workflow": style_sel.value,
                "scene": (scene_in.value or "").strip(),
                "variants": int(variants_in.value or 1),
                "model": (widgets["model"].value if widgets.get("model") else None) or None,
                "negative_extra": (neg_add.value or "").strip(),
                "negative_drop": (neg_drop.value or "").strip(),
                "denoise": number("denoise"),
                "cn_strength": number("cn_strength"),
                "style_weight": number("style_weight"),
                "seed": int(seed.value) if seed is not None and seed.value is not None else None,
                "recheck": bool(recheck_in.value),
                "overrides": {"fields": over, "loras": loras},
                "positive_override": (pos_custom.value or "").strip()
                if pos_override.value else None,
                "negative_override": (neg_custom.value or "").strip()
                if neg_override.value else None,
                "_manual": manual,
            }, ""

        def recalc(silent=False):
            data, why = collect()
            if data is None:
                if not silent:
                    ui.notify(why, type="negative")
                return
            try:
                payload = pp.build_payload(
                    data["workflow"], data["_manual"] or "",
                    data["negative_extra"],
                    denoise=data["denoise"], seed=data["seed"],
                    style_weight=data["style_weight"], cn_strength=data["cn_strength"],
                    overrides=data["overrides"],
                    positive=data["positive_override"], negative=data["negative_override"])
            except pp.PipelineError as e:
                if not silent:
                    ui.notify(str(e), type="negative")
                return
            pos_preview.set_value(payload["positive"])
            neg_preview.set_value(payload["negative"])
            if not pos_custom.value:
                pos_custom.set_value(payload["positive"])
            if not neg_custom.value:
                neg_custom.set_value(payload["negative"])

        def start(generate, manual):
            data, why = collect()
            if data is None:
                ui.notify(why, type="negative")
                return
            if state["upload_id"] is None:
                ui.notify("сначала загрузи фото", type="warning")
                return
            subject = data.pop("_manual")
            if manual and not subject:
                ui.notify("для ручного режима нужно описание", type="warning")
                return
            if pos_override.value and not (pos_custom.value or "").strip():
                ui.notify("включено переопределение positive, но текст пуст", type="warning")
                return
            if neg_override.value and not (neg_custom.value or "").strip():
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
            )
            try:
                job = svc.submit(req, upload_id=state["upload_id"])
            except UploadError as e:
                ui.notify(str(e), type="negative")
                return
            state["job_id"] = job.id
            state["snap_id"] = None
            state["last_seq"] = 0
            state["frames"] = -1
            log.clear()
            ui.notify("задание в очереди" if svc.active_id() else "задание запущено",
                      type="info")

        # ------------------------------------------------ отрисовка задания
        def render_result(snap):
            result_box.clear()
            with result_box:
                overrides = snap["request"].get("overrides") or {}
                parts = [f"{key}={value}" for key, value in
                         (overrides.get("fields") or {}).items()]
                for role, change in (overrides.get("loras") or {}).items():
                    bits = [str(change.get("name") or "").strip(),
                            (f"вес {change['weight']}" if change.get("weight") is not None else "")]
                    parts.append(f"LoRA {role}: " + " ".join(x for x in bits if x))
                if parts:
                    ui.label("Переопределено: " + ", ".join(parts)).classes(
                        "text-xs opacity-70")
                if snap["frames"]:
                    with ui.row().classes("gap-4 flex-wrap"):
                        for frame in snap["frames"]:
                            name = Path(frame["file"]).name
                            url = f"/media/job/{snap['id']}/{name}"
                            with ui.card().classes("p-2"):
                                ui.image(url).classes("w-64 rounded")
                                ui.label(name).classes("text-xs font-mono")
                                ui.label(
                                    f"seed {frame['seed']} · CN {frame['cn_strength']} · "
                                    f"LoRA {frame['style_weight']} · dn {frame['denoise']}").classes(
                                    "text-xs opacity-70")
                                ui.link("Скачать", url + "?download=1").props("target=_blank")
                if snap["error"]:
                    tone = "Частично: " if snap["frames"] else "Ошибка: "
                    ui.label(tone + short(snap["error"], 400)).classes(
                        "text-red-600 text-sm whitespace-pre-wrap")

        def refresh():
            snap = svc.snapshot(state["job_id"])
            if snap is None:
                stage_label.set_text("Задание не запущено")
                return
            state["job_id"] = snap["id"]
            if snap["id"] != state["snap_id"]:
                state["snap_id"] = snap["id"]
                state["last_seq"] = 0
                log.clear()

            label = STAGE_LABEL.get(snap["stage"], snap["stage"])
            if snap["status"] == "queued":
                stage_label.set_text(f"В очереди · позиция {snap['queue'] or 1}")
                elapsed_label.set_text("")
                queue_label.set_text("Один исполнитель: вторая задача ждёт первую.")
                progress.set_visibility(False)
            elif snap["running"]:
                sampling = next((e for e in reversed(snap["events"])
                                 if e["stage"] == "sampling" and e.get("data", {}).get("max")),
                                None)
                variant = next((e for e in reversed(snap["events"])
                                if e["stage"] == "variant"), None)
                extra = ""
                if variant:
                    extra = f" · вариант {variant['data'].get('index')}/" \
                            f"{variant['data'].get('total')}"
                stage_label.set_text(f"{label}{extra}")
                elapsed_label.set_text(fmt_time(snap["elapsed"]))
                queue_label.set_text("")
                if sampling:
                    value = float(sampling["data"].get("value") or 0)
                    total = float(sampling["data"].get("max") or 1)
                    progress.set_value(min(1.0, value / total))
                    progress.set_visibility(True)
                    stage_label.set_text(
                        f"шаги сэмплера{extra} · шаг {int(value)}/{int(total)}")
                else:
                    progress.set_visibility(False)
            else:
                elapsed_label.set_text(fmt_time(snap["elapsed"]))
                queue_label.set_text("")
                if snap["status"] == "described":
                    stage_label.set_text("Описание готово — можно править и генерировать")
                elif snap["status"] == "done":
                    stage_label.set_text(f"Готово · кадров {len(snap['frames'])}")
                elif snap["status"] == "partial":
                    stage_label.set_text(
                        f"Частично · кадров {len(snap['frames'])}, были ошибки")
                else:
                    stage_label.set_text("Ошибка: " + label)
                progress.set_visibility(False)
                error_label.set_text("")
                if snap["status"] in ("error", "partial") and snap["error"]:
                    first = snap["error"].splitlines()[0]
                    error_label.set_text(short(first, 300))

            if snap["description"] and snap["status"] in ("described", "done", "partial"):
                subject = snap["description"].get("subject", "")
                if subject and (not desc_area.value or desc_area.value == state["desc_auto"]):
                    desc_area.set_value(subject)
                    state["desc_auto"] = subject
                if snap["positive"]:
                    pos_preview.set_value(snap["positive"])
                if snap["negative"]:
                    neg_preview.set_value(snap["negative"])

            new = [e for e in snap["events"] if e["seq"] > state["last_seq"]]
            for event in new:
                state["last_seq"] = max(state["last_seq"], event["seq"])
                if event.get("message"):
                    log.push(f"[{fmt_time(event['t'])}] {event['message']}")

            signature = (snap["id"], snap["status"], len(snap["frames"]), snap["error"])
            if signature != state.get("signature"):
                state["signature"] = signature
                render_result(snap)

        # ------------------------------------------------ старт страницы
        render_advanced()
        recalc(silent=True)
        ui.timer(0.6, refresh)
        ui.timer(0.2, refresh_health, once=True)
        latest = svc.snapshot(None)
        if latest:
            state["job_id"] = latest["id"]
            refresh()


def start(host="127.0.0.1", port=8080, show=True, reload=False):
    register_routes()
    register_page()
    ui.run(host=host, port=port, title="comic-comfy", show=show, reload=reload,
           uvicorn_logging_level="warning", language="ru")
