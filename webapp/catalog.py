"""Что показать в форме: сборки, их привязки и списки моделей ComfyUI.

Ни одного id узла и ни одного значения по умолчанию здесь нет: всё берётся
из `meta` в `<сборка>.api.json`, который пишет build_workflows.py, и из
`/object_info` работающего ComfyUI. Пересобрали граф — форма изменилась сама.
"""
import json
import urllib.request

import photo_prompt as pp

PHOTO_PREFIXES = ("07_",)
# 08z — тоже фото-сборка (Z-Image), но имя не начинается с 08_photo_, поэтому
# перечислена точно. `--style gta` по-прежнему ведёт на 08_photo_gta: у 08z
# короткого имени нет, она выбирается по имени сборки.
PHOTO_EXACT = ("08_photo_gta", "08z_photo_gta")


class CatalogError(RuntimeError):
    """Сборка не годится для формы: нет meta, нет привязок, битый JSON."""


def style_list():
    """Фото-сборки для выбора стиля: 07_* и 08_photo_gta."""
    out = []
    for path in sorted(pp.WF_DIR.glob("*.api.json")):
        name = path.name[:-len(".api.json")]
        if not (name.startswith(PHOTO_PREFIXES) or name in PHOTO_EXACT):
            continue
        try:
            meta = json.loads(path.read_text(encoding="utf-8")).get("meta") or {}
        except Exception as e:
            out.append({"workflow": name, "label": name, "meta": {},
                        "problems": [f"граф не читается: {e}"]})
            continue
        problems = []
        if not meta.get("fields"):
            problems.append("в meta нет блока fields — пересобери воркфлоу")
        else:
            try:
                api, _ = pp.load_workflow(name)
                problems += pp.check_bindings(api, meta, require_fields=True)
            except pp.PipelineError as e:
                problems.append(str(e))
        out.append({
            "workflow": name,
            "style": next((s for s, w in pp.STYLE_ALIAS.items() if w == name), ""),
            "label": meta.get("label") or name,
            "meta": meta,
            "problems": problems,
        })
    return out


def load_meta(workflow):
    try:
        meta = pp.workflow_meta(workflow)
    except pp.PipelineError as e:
        raise CatalogError(str(e)) from None
    if not meta.get("fields"):
        raise CatalogError(f"{workflow}: в meta нет полей — пересобери воркфлоу")
    return meta


def graph_summary(workflow):
    """Что реально стоит в графе: чекпойнт, LoRA, сэмплер, шаги."""
    api, meta = pp.load_workflow(workflow)
    nodes = list(api.values())
    samplers = [(nid, n) for nid, n in api.items() if n["class_type"] == "KSampler"]
    main = max(samplers, key=lambda x: float(x[1]["inputs"].get("denoise", 0)))[1]["inputs"] \
        if samplers else {}
    return {
        "checkpoint": next((n["inputs"].get("ckpt_name") for n in nodes
                            if n["class_type"] == "CheckpointLoaderSimple"), ""),
        "loras": [
            {"name": n["inputs"].get("lora_name"), "weight": n["inputs"].get("strength_model"),
             "role": next((s["role"] for s in meta.get("loras") or []
                           if str(s.get("node")) == nid), "")}
            for nid, n in api.items() if n["class_type"] == "LoraLoaderModelOnly"
        ],
        "sampler": main.get("sampler_name", ""),
        "scheduler": main.get("scheduler", ""),
        "steps": main.get("steps"),
        "denoise": main.get("denoise"),
        "cn_strength": next((n["inputs"].get("strength") for n in nodes
                             if n["class_type"] == "ControlNetApplyAdvanced"), None),
        "controlnet": next((n["inputs"].get("control_net_name") for n in nodes
                            if n["class_type"] == "ControlNetLoader"), ""),
        "style_lora": meta.get("style_lora", ""),
        "style_weight": meta.get("style_weight"),
        "label": meta.get("label", ""),
    }


def object_info(url, timeout=5):
    """Списки значений из /object_info. Сервер не ответил — None, без догадок."""
    try:
        with urllib.request.urlopen(url.rstrip("/") + "/object_info", timeout=timeout) as r:
            return json.loads(r.read())
    except Exception:
        return None


def combo_options(info, class_type, input_name):
    """Варианты для COMBO-входа или None, если сервер их не назвал."""
    node = (info or {}).get(class_type) or {}
    for section in ("required", "optional"):
        spec = (node.get("input") or {}).get(section) or {}
        if input_name in spec:
            value = spec[input_name]
            first = value[0] if isinstance(value, list) and value else None
            if isinstance(first, list):
                return [str(x) for x in first]
            return None
    return None


def check_value(info, binding, value):
    """Значение из списка должно быть в списке ComfyUI. Пустая проверка — ок True."""
    if binding.get("type") != "COMBO":
        return True, ""
    options = combo_options(info, binding.get("class_type", ""), binding.get("input", ""))
    if options is None:
        return True, ""
    if value not in options:
        return False, (f"«{value}» нет в списке {binding.get('class_type')}."
                       f"{binding.get('input')} — проверь модели в ComfyUI")
    return True, ""


def probe(url, path="/system_stats", timeout=4):
    try:
        with urllib.request.urlopen(url.rstrip("/") + path, timeout=timeout) as r:
            return r.status == 200
    except Exception:
        return False


def find_comfy():
    """Адрес ComfyUI по тому же правилу, что у генерации."""
    return pp.comfy_state()[0]


def lm_health(lm_url):
    """Сервис описаний: LM Studio или обычный OpenAI-совместимый сервер.

    Порядок тот же, что у `photo_prompt.lm_models()`: сначала `/api/v1/models`,
    затем `/v1/models`. Модели без объявленных возможностей (второй случай) считаем
    зрячими — за этим такой сервис и берут. Ключи вида `...@?` пропускаем: такой id
    даёт 400 при запросе.
    """
    base = str(lm_url).rstrip("/")
    for path, provider in (("/api/v1/models", "lmstudio"), ("/v1/models", "openai")):
        try:
            with urllib.request.urlopen(base + path, timeout=5) as r:
                data = json.loads(r.read())
        except Exception:
            continue
        models = []
        for m in (data.get("models") or data.get("data") or []):
            key = str(m.get("key") or m.get("id") or "")
            if not key or "@?" in key:
                continue
            if provider == "openai" or (m.get("capabilities") or {}).get("vision"):
                models.append(key)
        return {"ok": True, "url": base, "provider": provider, "models": models}
    return {"ok": False, "url": base, "provider": "", "models": []}


def health(lm_url):
    """Лёгкие проверки уже запущенных сервисов. Ничего не поднимает.

    Адрес ComfyUI выбирается тем же правилом, что и для генерации, поэтому
    «готов» на странице означает ровно тот сервер, куда уйдёт задание.
    """
    lm = lm_health(lm_url)
    url, error = pp.comfy_state()
    comfy = {"ok": not error, "url": url, "error": error}
    return {"lm": lm, "comfy": comfy}
