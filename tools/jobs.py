"""Задание целиком: описание -> сцена -> выгрузка VLM -> ComfyUI -> кадры.

Один и тот же код ведёт задание и из командной строки, и из веб-интерфейса.
Разница только в подписчике: CLI печатает сообщения в консоль, страница
получает структурированные события (этап, время, данные) и рисует по ним
таймер и прогресс. Поэтому логика не разъезжается между двумя входами.

События:
  stage      машинное имя этапа (queued, cache, describe, scene, unload,
             prompt, comfy, sampling, save, recheck, done, error, partial)
  message    строка для консольного лога (может быть пустой)
  data       числа и имена, которые нужны интерфейсу
  t          секунды от начала задания

Модели и ComfyUI здесь не запускаются: задание делает ровно то, что делает
CLI, и ждёт уже работающие сервисы.
"""
import json
import random
import re
import time
from dataclasses import dataclass, field
from pathlib import Path

import comfy_launch as cl
import photo_prompt as pp


@dataclass
class JobRequest:
    image: str
    workflow: str
    scene: str = ""
    variants: int = 1
    model: str | None = None
    subject: str | None = None            # ручное описание: LM Studio не зовём
    description: dict | None = None       # готовое описание (пресет, файл, страница)
    description_source: str = ""
    negative_extra: str = ""
    negative_drop: str = ""
    denoise: float | None = None
    cn_strength: float | None = None
    style_weight: float | None = None
    seed: int | None = None
    force: bool = False
    recheck: bool = False
    keep_llm: bool = False
    unload_all: bool = False
    comfy: str | None = None
    overrides: dict = field(default_factory=dict)
    positive_override: str | None = None
    negative_override: str | None = None
    save_preset: str | None = None
    generate: bool = True
    dry_run: bool = False
    write_cache: bool = True              # ручные правки в кэш не пишутся
    tags: bool = True                     # дописывать теги WD14-теггера в subject
    client_id: str | None = None
    start_comfy: bool = False             # поднять headless ComfyUI после фазы описания


@dataclass
class JobResult:
    ok: bool = False
    code: int = 1
    partial: bool = False
    frames: list = field(default_factory=list)
    folders: list = field(default_factory=list)
    description: dict | None = None
    positive: str = ""
    negative: str = ""
    error: str = ""
    stage: str = ""
    elapsed: float = 0.0


class Emitter:
    """События задания: список для страницы и sink для консоли."""

    def __init__(self, sink=None):
        self.sink = sink
        self.t0 = time.monotonic()
        self.events = []

    def __call__(self, stage, message="", **data):
        event = {
            "seq": len(self.events) + 1,
            "t": round(time.monotonic() - self.t0, 2),
            "stage": stage,
            "message": message,
            "data": data,
        }
        self.events.append(event)
        if self.sink:
            try:
                self.sink(event)
            except Exception:
                pass
        return event

    def progress(self, data):
        """Событие от ComfyUI/WebSocket: stage внутри словаря."""
        data = dict(data or {})
        stage = data.pop("stage", "progress")
        message = data.pop("message", "")
        return self(stage, message, source="comfy", **data)

    @property
    def elapsed(self):
        return round(time.monotonic() - self.t0, 2)


def cli_sink(event):
    """Консоль CLI: печатаем только то, что раньше печаталось."""
    if event.get("message"):
        print(event["message"], flush=True)


def _seed(req, index):
    return (req.seed + index) if req.seed is not None else random.randint(1, 2_000_000_000)


def _sampler_fields(report):
    """Настройки сэмплера из отчёта generate: кадр и паспорт говорят одно и то же.

    Берутся из собранного графа (report["settings"]), а не из req: `--denoise`
    правит главный проход, поля формы могут переписать steps и cfg.
    """
    s = (report or {}).get("settings") or {}
    return {"steps": s.get("steps"), "cfg": s.get("cfg"),
            "sampler": s.get("sampler"), "scheduler": s.get("scheduler"),
            "passes": s.get("passes")}


def _negative_tags(desc, req):
    extra = [x.strip() for x in (desc.get("negative_extra") or "").split(",") if x.strip()]
    drop = [x.strip().lower() for x in (req.negative_drop or "").split(",") if x.strip()]
    if drop:
        extra = [x for x in extra if x.lower() not in drop]
    extra += [x.strip() for x in (req.negative_extra or "").split(",") if x.strip()]
    return ", ".join(dict.fromkeys(extra))


def _prompts(req, desc, image_name=None):
    """Итоговые positive/negative по графу: их видит и CLI, и страница."""
    return pp.build_payload(
        req.workflow, desc.get("subject", ""), desc.get("negative_extra", ""),
        denoise=req.denoise, seed=req.seed, landscape=False,
        style_weight=req.style_weight, image_name=image_name,
        cn_strength=req.cn_strength, overrides=req.overrides,
        positive=req.positive_override, negative=req.negative_override,
    )


def run_job(req, emit=None):
    """Полный проход одного фото. Возвращает JobResult, не бросает SystemExit."""
    out = Emitter(emit)
    result = JobResult()
    desc = None
    came_from_llm = False
    from_cache = False
    scene_failed = False
    model = req.model
    t_desc = t_unload = 0.0
    handle = None

    out("start", "", workflow=req.workflow, image=str(req.image),
        variants=max(1, req.variants))

    try:
        # ---- 0. Готовое описание: пресет, файл, ручной ввод ------------------
        if req.description is not None:
            desc = dict(req.description)
            out("cache", f"0) описание из {req.description_source or 'готового источника'}")
        elif req.subject is not None:
            if not str(req.subject).strip():
                raise pp.PipelineError("пустое описание — генерировать нечего")
            desc = {"subject": req.subject, "negative_extra": "", "notes": "без LLM"}
            out("manual", "0) ручное описание, LM Studio не нужен")
        else:
            # ---- 1. Кэш, описание фото, сцена -------------------------------
            cached = None if req.force else pp.read_cache(str(req.image), req.scene or None)
            base_desc = (None if (req.force or not req.scene)
                         else pp.read_cache(str(req.image)))
            if cached:
                desc = cached
                from_cache = True
                out("cache", f"0) описание из кэша "
                             f"{pp.cache_path(str(req.image), req.scene or None).name} "
                             f"(--force чтобы пересмотреть фото)", cache=True)
            elif req.dry_run:
                what = "описания для этой сцены" if req.scene else "описания"
                hint = " (--force отключает кэш — убери его)" if req.force else ""
                pp.legacy_cache_hint(req.image)
                raise pp.PipelineError(
                    f"--dry-run ничего не загружает, а в кэше нет {what}{hint}:\n"
                    f"  {pp.cache_path(str(req.image), req.scene or None)}\n"
                    "Дай готовое описание: --preset имя, --from-json файл или "
                    "--no-llm --subject \"…\";\n"
                    "либо убери --dry-run — тогда VLM посмотрит фото (это загрузка памяти).")
            else:
                pp.legacy_cache_hint(req.image)
                models = pp.lm_models()
                model = pp.pick_model(models, req.model)
                if base_desc:
                    desc = base_desc
                    out("scene", "1) фото уже описано — переписываю только сцену")
                else:
                    out("describe", f"1) смотрю фото моделью {model}", model=model)
                    t = time.monotonic()
                    desc = pp.describe(str(req.image), model)
                    t_desc = time.monotonic() - t
                    came_from_llm = True
                if req.scene:
                    out("scene", f"   сцена: {req.scene}", scene=req.scene)
                    new = pp.rewrite_scene(desc, req.scene, model)
                    if new:
                        new["negative_extra"] = ", ".join(dict.fromkeys(
                            [x.strip() for x in (desc.get("negative_extra") or "").split(",")
                             if x.strip()]
                            + [x.strip() for x in (new.get("negative_extra") or "").split(",")
                               if x.strip()]))
                        desc = new
                        came_from_llm = True
                    else:
                        # Сцену просили, но модель её не переписала. Писать это в кэш
                        # под ключом сцены нельзя: следующий запуск взял бы чужой текст.
                        scene_failed = True
                if not req.keep_llm:
                    t = time.monotonic()
                    freed = pp.free_llm(model, req.unload_all)
                    t_unload = time.monotonic() - t
                    out("unload", f"2) выгружено: {', '.join(freed) if freed else 'нечего'} "
                                  f"({t_unload:.1f} с)", freed=freed)

        if scene_failed:
            if not req.dry_run and req.write_cache:
                print("   описание фото сохранено без сцены:",
                      pp.write_cache(str(req.image), desc, None).name)
            raise pp.PipelineError(
                "сцену применить не удалось (модель не вернула описание сцены) — "
                "кадр не генерирую\n"
                "описание фото не потеряно: повторный запуск попробует сцену снова")

        if req.scene and req.subject is not None:
            desc["subject"] = (desc["subject"].rstrip(", ") + ", " + req.scene).strip(", ")

        # ---- 2. Кэш пишем до флагов командной строки --------------------
        desc["subject"] = pp.with_adult(desc.get("subject", ""))
        if desc["subject"]:
            before = desc["subject"]
            desc["subject"] = pp.apply_tags(desc, str(req.image),
                                            no_tags=not req.tags, dry_run=req.dry_run)
            # Теги дозаполнили готовый кэш — его надо перезаписать. Пресет и ручной
            # ввод в кэш фото не пишем: иначе они осядут в нём навсегда.
            if from_cache and req.tags and not req.dry_run and desc["subject"] != before:
                came_from_llm = True
        pp.clean_negative(desc)
        if came_from_llm and not req.dry_run and req.write_cache and desc["subject"].strip():
            out("cache_write", "   сохранено: " + str(pp.write_cache(
                str(req.image), desc, req.scene or None)), cache=True)
        elif not req.dry_run and not came_from_llm:
            out("cache_write", "   кэш не тронут")

        desc["negative_extra"] = _negative_tags(desc, req)
        result.description = desc

        out("subject", "   subject: " + (desc["subject"] or "(пусто)"),
            subject=desc["subject"])
        if desc.get("negative_extra"):
            out("negative", "   negative_extra: " + desc["negative_extra"],
                negative_extra=desc["negative_extra"])
        if desc.get("notes"):
            out("notes", "   заметка: " + str(desc["notes"]))
        if req.denoise is not None:
            out("setting", f"   denoise: {req.denoise}", denoise=req.denoise)
        if req.seed is not None:
            out("setting", f"   seed: {req.seed}", seed=req.seed)

        if not desc["subject"].strip():
            raise pp.PipelineError(
                "описание не получилось — генерацию не запускаю. Проверь заметку выше.")

        if req.save_preset:
            path = pp.preset_path(req.save_preset)
            pp.write_json(path, desc)
            out("preset", "   пресет: " + path.name)

        # ---- 3. Итоговые промпты и проверка привязок ---------------------
        payload = _prompts(req, desc)
        result.positive = payload["positive"]
        result.negative = payload["negative"]
        out("prompt", "", positive=payload["positive"], negative=payload["negative"],
            applied=payload.get("applied") or {})

        if req.dry_run:
            out("preview", "\nитоговый промпт:\n  " + payload["positive"]
                + "\n\nитоговый негатив:\n  " + payload["negative"])
            result.ok, result.code, result.stage = True, 0, "dry_run"
            return result

        if not req.generate:
            out("described", f"\nготово описаний: 1 (генерация не запускалась)")
            result.ok, result.code, result.stage = True, 0, "described"
            return result

        # ---- 4. ComfyUI: очередь, шаги, кадры ------------------------------
        if req.start_comfy:
            # Только здесь: VLM уже выгружен, память под SDXL свободна.
            handle = cl.ensure(emit=out)
        comfy = pp.resolve_comfy(req.comfy)
        pp.COMFY = comfy
        out("comfy", "ComfyUI: " + comfy, url=comfy)

        n = max(1, req.variants)
        landscape = pp.image_is_landscape(str(req.image))
        out("generate", f"3) генерация в ComfyUI, вариантов: {n}")
        t = time.monotonic()
        ok_all = True
        for i in range(n):
            sd = _seed(req, i)
            out("variant", f"   вариант {i + 1}/{n}, seed {sd}", index=i + 1, total=n, seed=sd)
            report = {}
            ok, name = pp.generate(
                req.workflow, desc["subject"], desc.get("negative_extra", ""),
                denoise=req.denoise, seed=sd, landscape=landscape,
                style_weight=req.style_weight, image_name=pp.ensure_input(str(req.image)),
                cn_strength=req.cn_strength, overrides=req.overrides,
                positive=req.positive_override, negative=req.negative_override,
                progress=out.progress, client_id=req.client_id, report=report)
            ok_all &= bool(ok)
            folder = None
            if name:
                folder = pp.save_run(str(req.image), req.workflow, desc, sd, req.denoise, name,
                                     cn_strength=req.cn_strength,
                                     style_weight=req.style_weight,
                                     overrides=req.overrides or None,
                                     positive=report.get("positive"),
                                     negative=report.get("negative"),
                                     settings=report.get("settings"))
                if folder is None:
                    ok_all = False
                else:
                    result.frames.append({
                        "file": name, "folder": str(folder), "seed": sd,
                        "workflow": req.workflow, "cn_strength": req.cn_strength,
                        "style_weight": req.style_weight, "denoise": req.denoise,
                        "refined": False, "subject": desc.get("subject", ""),
                        "positive": report.get("positive", ""),
                        "negative": report.get("negative", ""),
                        **_sampler_fields(report),
                    })
                    result.folders.append(str(folder))
                    out("save", f"   кадр сохранён: {name}", file=name, folder=str(folder))
            if req.recheck and i == 0 and name:
                ok_recheck, refined = _recheck(req, desc, name, out, sd)
                ok_all &= ok_recheck
                if refined:
                    result.frames.append(refined)
                    result.folders.append(refined["folder"])
        t_gen = time.monotonic() - t

        result.elapsed = out.elapsed
        result.partial = (not ok_all) and bool(result.frames)
        out("done", f"\nитого: описание {t_desc:.0f} с + выгрузка {t_unload:.1f} с + "
                    f"генерация {t_gen:.0f} с = {t_desc + t_unload + t_gen:.0f} с",
            frames=len(result.frames), total=n, elapsed=out.elapsed)
        result.ok = bool(ok_all) and bool(result.frames)
        result.code = 0 if result.ok else 1
        result.stage = "done" if result.ok else "partial"
        if not result.ok:
            out("partial", "   часть кадров не сделана — это не успех", frames=len(result.frames))
        return result

    except SystemExit as e:                     # resolve_comfy и generate говорят так
        return _fail(out, result, str(e.code if e.code not in (None, 0) else "остановлено"))
    except pp.PipelineError as e:
        return _fail(out, result, str(e))
    except Exception as e:                      # неожиданное — тоже ошибка задания
        return _fail(out, result, f"{type(e).__name__}: {e}")
    finally:
        if handle is not None:
            cl.stop(handle, emit=out)


def _fail(out, result, message):
    result.ok = False
    result.code = 1
    result.error = message
    result.stage = "error"
    result.elapsed = out.elapsed
    out("error", message, error=message)
    if result.frames:
        result.partial = True
    return result


def _augment(text, miss):
    """Добавляет промахи в готовый positive или усиливает уже имеющиеся теги.

    Возвращает (новый текст, что изменилось). Пустой второй список значит
    «повтор ничего не изменит».
    """
    low = text.lower()
    add = [x.strip() for x in miss.split(",") if x.strip() and x.strip().lower() not in low]
    if add:
        return ", ".join([text.rstrip(", ")] + add), add
    out, bumped = text, []
    for tag in [x.strip() for x in miss.split(",") if x.strip()]:
        if tag.lower() in out.lower() and f"({tag}" not in out:
            out = re.sub(re.escape(tag), f"(({tag}:1.2))", out, count=1,
                         flags=re.IGNORECASE)
            bumped.append(tag)
    return out, bumped


def _recheck(req, desc, name, out, seed):
    """Проверка кадра моделью и один повторный проход.

    Возвращает (всё_цело, кадр_уточнения | None). Описание основного прохода
    не меняется: правка живёт в копии, иначе второй вариант уходил бы в
    ComfyUI с добавками от уточнения первого кадра.
    """
    out("recheck", "   проверка кадра моделью")
    pp.comfy_free()
    try:
        models = pp.lm_models()
        model = pp.pick_model(models, req.model)
        miss = pp.recheck(desc, pp.OUTPUT_DIR / name, model)
        if not req.keep_llm:
            pp.free_llm(model, req.unload_all)
    except SystemExit as e:
        out("recheck", f"   проверку выполнить не удалось: {e.code} — повторный проход пропущен")
        return True, None
    if miss is None:
        out("recheck", "   проверку выполнить не удалось — повторный проход пропущен")
        return True, None
    if not miss:
        out("recheck", "   всё на месте")
        return True, None

    out("recheck", "   не доехало: " + miss, missing=miss)
    local = dict(desc)
    # Промах = чего не хватает на кадре, поэтому усиливаем ПРОМПТ, а не негатив.
    if req.positive_override:
        # Полный positive — это и есть запрос; добавлять теги в описание
        # бессмысленно, иначе повтор уйдёт с тем же графом.
        text, changes = _augment(req.positive_override, miss)
        if not changes:
            out("recheck", "   positive переопределён и уже содержит промахи — "
                           "повтор не изменит результата")
            return True, None
        out("recheck", "   positive переопределён, изменено: " + ", ".join(changes))
        return _one_more(req, local, out, seed, positive=text)

    ok_all, frame = True, None
    subj = local.get("subject", "")
    low = subj.lower()
    add = [x.strip() for x in miss.split(",") if x.strip() and x.strip().lower() not in low]
    if add:
        local["subject"] = ", ".join([subj] + add)
        out("recheck", "   промпт дополнен: " + ", ".join(add))
        out("recheck", "   повторный проход с усиленным промптом")
        ok_all, frame = _one_more(req, local, out, seed)
    else:
        subj2, bumped = subj, []
        for tag in [x.strip() for x in miss.split(",") if x.strip()]:
            if tag.lower() in subj2.lower() and f"({tag}" not in subj2:
                subj2 = re.sub(re.escape(tag), f"(({tag}:1.2))", subj2, count=1,
                               flags=re.IGNORECASE)
                bumped.append(tag)
        if bumped:
            local["subject"] = subj2
            out("recheck", "   усилено весом: " + ", ".join(bumped))
            ok_all, frame = _one_more(req, local, out, seed)
        else:
            out("recheck", "   повтор не изменит результата — пропускаю")
    return ok_all, frame


def _one_more(req, desc, out, seed=None, positive=None):
    sd = seed if seed is not None else (req.seed if req.seed is not None else _seed(req, 0))
    refined_positive = positive if positive is not None else req.positive_override
    report = {}
    ok, name2 = pp.generate(
        req.workflow, desc["subject"], desc.get("negative_extra", ""),
        denoise=req.denoise, seed=sd, landscape=pp.image_is_landscape(str(req.image)),
        style_weight=req.style_weight, image_name=pp.ensure_input(str(req.image)),
        cn_strength=req.cn_strength, overrides=req.overrides,
        positive=refined_positive, negative=req.negative_override,
        progress=out.progress, client_id=req.client_id, report=report)
    if not ok or not name2:
        return False, None
    folder = pp.save_run(str(req.image), req.workflow, desc, sd, req.denoise, name2,
                         cn_strength=req.cn_strength, style_weight=req.style_weight,
                         overrides=req.overrides or None, refined=True,
                         positive=report.get("positive"),
                         negative=report.get("negative"),
                         settings=report.get("settings"))
    if folder is None:
        return False, None
    out("save", f"   кадр сохранён: {name2}", file=name2, folder=str(folder),
        refined=True)
    return True, {
        "file": name2, "folder": str(folder), "seed": sd,
        "workflow": req.workflow, "cn_strength": req.cn_strength,
        "style_weight": req.style_weight, "denoise": req.denoise,
        "refined": True, "subject": desc.get("subject", ""),
        # Фактический запрос: для полного переопределения это он, а не subject.
        "positive": report.get("positive") or refined_positive or "",
        "negative": report.get("negative", ""),
        **_sampler_fields(report),
    }
