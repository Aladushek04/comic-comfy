"""Сборка JobRequest из значений формы — без NiceGUI.

`ui.collect` читал виджеты и попутно проверял meta; здесь то же самое на плоском
словаре значений, поэтому проверяется в T0 без браузера, моделей и GPU.

Правила не изменились: пустое поле значит «как в сборке», ноль остаётся нулём,
переопределение уходит только при отличии от значения сборки, а причина отказа
возвращается текстом — страница её показывает и ничего не запускает.
"""
import uuid

import jobs
import photo_prompt as pp

from . import catalog

# Поля графа, которые форма может переопределить. Остальные приходят из meta
# и правятся только вместе с графом.
FIELD_KEYS = ("checkpoint", "steps", "cfg", "sampler", "scheduler")


def _float(values, key):
    value = values.get(key)
    if value is None or value == "":
        return None
    return float(value)


def _seed(values):
    value = values.get("seed")
    if value is None or value == "":
        return None
    return int(value)


def collect_form(values, meta, info=None):
    """Собирает JobRequest. Возвращает `(request, "")` или `(None, причина)`.

    values — плоский словарь выбора: image (id загрузки), workflow, scene,
    variants, model, negative_extra/drop, denoise, cn_strength, style_weight,
    seed, recheck, generate, manual, subject, fields (checkpoint/steps/cfg/
    sampler/scheduler), loras ({node: {name, weight}}), positive_override,
    negative_override. info — ответ `/object_info`; None значит «списки не
    проверяем», а не «проверка пройдена».
    """
    workflow = str(values.get("workflow") or "")
    if not str(values.get("image") or ""):
        return None, "сначала загрузи фото"
    if meta is None:
        return None, f"сборка не готова: {workflow}"
    try:
        api, _ = pp.load_workflow(workflow)
    except pp.PipelineError as e:
        return None, str(e)
    problems = pp.check_bindings(api, meta, require_fields=True)
    if problems:
        return None, "; ".join(problems)

    fields = meta.get("fields") or {}
    given = values.get("fields") or {}
    over = {}
    for key in FIELD_KEYS:
        binding = fields.get(key)
        if binding is None or given.get(key) in (None, ""):
            continue
        value = given[key]
        if binding.get("type") == "INT":
            value = int(value)
        elif binding.get("type") == "FLOAT":
            value = float(value)
        ok, why = catalog.check_value(info, binding, value)
        if not ok:
            return None, why
        if value != binding.get("default"):
            over[key] = value

    loras = {}
    for slot in meta.get("loras") or []:
        node = str(slot.get("node"))
        change = (values.get("loras") or {}).get(node) or {}
        out = {}
        name = change.get("name")
        if name and name != slot.get("value"):
            ok, why = catalog.check_value(
                info, {"type": "COMBO", "class_type": "LoraLoaderModelOnly",
                       "input": "lora_name"}, name)
            if not ok:
                return None, why
            out["name"] = name
        weight = change.get("weight")
        if weight is not None and float(weight) != float(slot.get("weight") or 0):
            out["weight"] = float(weight)
        if out:
            loras[str(slot.get("role") or node)] = out

    manual = bool(values.get("manual"))
    subject = str(values.get("subject") or "").strip()
    if manual and not subject:
        return None, "для ручного режима нужно описание"
    positive = values.get("positive_override")
    negative = values.get("negative_override")
    if positive is not None and not str(positive).strip():
        return None, "включено переопределение positive, но текст пуст"
    if negative is not None and not str(negative).strip():
        return None, "включено переопределение negative, но текст пуст"

    request = jobs.JobRequest(
        image="",                       # путь подставит service.submit
        workflow=workflow,
        scene=str(values.get("scene") or "").strip(),
        variants=int(values.get("variants") or 1),
        model=values.get("model") or None,
        subject=subject if manual else None,
        negative_extra=str(values.get("negative_extra") or "").strip(),
        negative_drop=str(values.get("negative_drop") or "").strip(),
        denoise=_float(values, "denoise"),
        cn_strength=_float(values, "cn_strength"),
        style_weight=_float(values, "style_weight"),
        seed=_seed(values),
        recheck=bool(values.get("recheck")),
        overrides=({"fields": over, "loras": loras} if (over or loras) else {}),
        positive_override=str(positive).strip() if positive is not None else None,
        negative_override=str(negative).strip() if negative is not None else None,
        generate=bool(values.get("generate", True)),
        write_cache=not manual,         # ручные правки в кэш не пишутся
        client_id=uuid.uuid4().hex,
        start_comfy=True,
    )
    return request, ""
