"""Описание сборки -> граф ComfyUI и `meta`.

Здесь только механика превращения описания в два файла: `<name>.json` (граф для
интерфейса) и `<name>.api.json` (`prompt` для `/prompt` плюс `meta` для формы и
CLI). Сами 14 сборок — данные в `comic/builds.py`, а драйвер, который пишет
файлы и гоняет их через API, — `tools/build_workflows.py`.

Границы, которые нельзя сдвинуть:

  * `DEFS` повторяет схемы `/object_info`: от него зависит и порядок
    `widgets_values` (AGENTS, грабли 1), и порядок входов узла;
  * у seed-виджета KSampler есть скрытый второй элемент `control_after_generate`
    — без него все значения сэмплера съезжают на позицию влево (грабли 1);
  * `extra.linearData.outputs` указывает на `SaveImage`, а не на «максимальный
    id»: иначе App Mode показывает пустоту после прогона (грабли 2);
  * `extra.linearData.inputs` — тройки `[id, widget, {description}]`, и
    описание здесь единственная подпись поля в App Mode (грабли 3);
  * `graph_fields()` выводит поля из **собранного графа**, а не из описания:
    `meta.fields` не может разойтись с тем, что реально уйдёт в ComfyUI.

Описание сборки — словарь `{title, style?, nodes, app}` (см. `comic/builds.py`):
`nodes` — узлы в том порядке, в каком их читает `topo`, `app.inputs` — поля
App Mode по порядку.
"""
import json
from pathlib import Path

# порядок сокетов и виджетов повторяет схемы /object_info
DEFS = {
    "CheckpointLoaderSimple": ([], ["ckpt_name"], ["MODEL", "CLIP", "VAE"]),
    "LoraLoaderModelOnly": (["model"], ["lora_name", "strength_model"], ["MODEL"]),
    "CLIPTextEncode": (["clip"], ["text"], ["CONDITIONING"]),
    "CLIPSetLastLayer": (["clip"], ["stop_at_clip_layer"], ["CLIP"]),
    "EmptyLatentImage": ([], ["width", "height", "batch_size"], ["LATENT"]),
    "KSampler": (["model", "positive", "negative", "latent_image"],
                 ["seed", "steps", "cfg", "sampler_name", "scheduler", "denoise"], ["LATENT"]),
    "VAEDecode": (["samples", "vae"], [], ["IMAGE"]),
    "VAEEncode": (["pixels", "vae"], [], ["LATENT"]),
    "ImageScaleBy": (["image"], ["upscale_method", "scale_by"], ["IMAGE"]),
    "SaveImage": (["images"], ["filename_prefix"], ["IMAGE"]),
    "LoadImage": ([], ["image"], ["IMAGE", "MASK"]),
    "ImageToMask": (["image"], ["channel"], ["MASK"]),
    "GrowMask": (["mask"], ["expand", "tapered_corners"], ["MASK"]),
    "FeatherMask": (["mask"], ["left", "top", "right", "bottom"], ["MASK"]),
    "SetLatentNoiseMask": (["samples", "mask"], [], ["LATENT"]),
    "ImageCompositeMasked": (["destination", "source", "mask"], ["x", "y", "resize_source"], ["IMAGE"]),
    "UnetLoaderGGUF": ([], ["unet_name"], ["MODEL"]),
    "UNETLoader": ([], ["unet_name", "weight_dtype"], ["MODEL"]),
    "CLIPLoader": ([], ["clip_name", "type", "device"], ["CLIP"]),
    "VAELoader": ([], ["vae_name"], ["VAE"]),
    "TextEncodeQwenImageEditPlus": (["clip", "vae", "image1"], ["prompt"], ["CONDITIONING"]),
    "TextEncodeQwenImage21": (["clip", "vae"], ["prompt", "negative_prompt", "resolution"],
                              ["CONDITIONING", "CONDITIONING", "LATENT"]),
    "ImageScaleToTotalPixels": (["image"], ["upscale_method", "megapixels", "resolution_steps"], ["IMAGE"]),
    "ModelSamplingAuraFlow": (["model"], ["shift", "sampling"], ["MODEL"]),
    "DepthAnythingV2Preprocessor": (["image"], ["ckpt_name", "resolution"], ["IMAGE"]),
    "ControlNetLoader": ([], ["control_net_name"], ["CONTROL_NET"]),
    "ControlNetApplyAdvanced": (["positive", "negative", "control_net", "image"],
                                ["strength", "start_percent", "end_percent"],
                                ["CONDITIONING", "CONDITIONING"]),
    "IPAdapterModelLoader": ([], ["ipadapter_file"], ["IPADAPTER"]),
    "CLIPVisionLoader": ([], ["clip_name"], ["CLIP_VISION"]),
    "IPAdapterAdvanced": (["model", "ipadapter", "image", "clip_vision"],
                          ["weight", "weight_type", "combine_embeds", "start_at", "end_at", "embeds_scaling"],
                          ["MODEL"]),
    "DWPreprocessor": (["image"], ["detect_hand", "detect_body", "detect_face", "resolution",
                                   "bbox_detector", "pose_estimator", "scale_stick_for_xinsr_cn"], ["IMAGE", "POSE_KEYPOINT"]),
    "PreviewImage": (["images"], [], []),
}


def topo(nodes):
    ids = [n["id"] for n in nodes]
    deps = {n["id"]: {v[0] for v in n.get("in", {}).values()} for n in nodes}
    order, done = [], set()
    while len(order) < len(ids):
        for i in ids:
            if i in done:
                continue
            if deps[i] <= done:
                order.append(i)
                done.add(i)
                break
        else:
            raise RuntimeError("цикл в графе: " + str(set(ids) - done))
    return order


_TITLES = {
    "CheckpointLoaderSimple": "Чекпойнт",
    "UnetLoaderGGUF": "Модель (GGUF)",
    "UNETLoader": "Модель",
    "CLIPLoader": "Текстовый энкодер",
    "VAELoader": "VAE",
    "CLIPSetLastLayer": "Clip skip -2",
    "DepthAnythingV2Preprocessor": "Карта глубины",
    "ControlNetLoader": "ControlNet Union",
    "ControlNetApplyAdvanced": "ControlNet: применить",
    "DWPreprocessor": "Скелет позы",
    "IPAdapterModelLoader": "IP-Adapter",
    "CLIPVisionLoader": "CLIP Vision",
    "IPAdapterAdvanced": "IP-Adapter: применить",
    "ImageScaleToTotalPixels": "Масштаб до 1 МП",
    "ImageScaleBy": "Апскейл",
    "VAEEncode": "В латент",
    "VAEDecode": "Из латента",
    "ImageCompositeMasked": "Вклейка по маске",
    "GrowMask": "Маска: рост",
    "FeatherMask": "Маска: мягкий край",
    "ImageToMask": "Маска из картинки",
    "SetLatentNoiseMask": "Маска в латент",
    "ModelSamplingAuraFlow": "AuraFlow shift",
    "TextEncodeQwenImage21": "Qwen 2.1: промпт",
    "SaveImage": "Сохранение",
}

NEG_MARKERS = ("worst quality", "photo, photorealistic", "low quality, blurry")


def _auto_title(n, wv):
    """Читаемые подписи узлов: по типу и по фактическому значению виджета."""
    t = n["type"]
    if t == "LoraLoaderModelOnly":
        return "LoRA · " + str(wv[0]).split("\\")[-1].replace(".safetensors", "")
    if t == "LoadImage":
        return "Изображение · " + str(wv[0])
    if t == "KSampler":
        den = wv[6] if len(wv) > 6 else 1
        return "Сэмплер" + (f" · denoise {den}" if den < 1 else " · denoise 1")
    if t in ("CLIPTextEncode", "TextEncodeQwenImageEditPlus"):
        txt = str(wv[0] or "")
        if not txt.strip():
            return "Негатив (пусто)"
        if any(txt.startswith(m) for m in NEG_MARKERS):
            return "Негатив"
        return "Промпт Qwen" if t.startswith("TextEncodeQwen") else "Промпт"
    return _TITLES.get(t)


SUBJECT_SLOT = "{{subject}}"
NEG_SIGNS = ("photo, photorealistic", "worst quality")

# Типы виджетов ComfyUI для тех полей, которые показывает интерфейс. Список
# намеренно короткий: незнакомый вход получает STRING, а точный тип всё равно
# сверяется с /object_info перед отправкой графа.
WIDGET_TYPES = {
    "seed": "INT", "steps": "INT", "denoise": "FLOAT", "cfg": "FLOAT",
    "strength": "FLOAT", "start_percent": "FLOAT", "end_percent": "FLOAT",
    "strength_model": "FLOAT", "strength_clip": "FLOAT", "weight": "FLOAT",
    "ckpt_name": "COMBO", "lora_name": "COMBO", "control_net_name": "COMBO",
    "vae_name": "COMBO", "sampler_name": "COMBO", "scheduler": "COMBO",
    "upscale_method": "COMBO", "image": "COMBO",
}

# Роль слота LoRA — по имени файла: интерфейс подписывает слоты, не угадывая.
LORA_ROLES = (
    ("hands_xl", "hands"), ("sexy_detailer", "body"),
    ("p477y", "character"), ("p4m3l4", "character"),
)


def _field(api, node_id, name):
    """Привязка поля к узлу и входу. Нет входа — нет привязки, а не догадка."""
    node = api.get(str(node_id)) or {}
    if name not in (node.get("inputs") or {}):
        return None
    value = node["inputs"][name]
    if isinstance(value, list):
        return None          # вход занят связью, писать в него нельзя
    return {
        "node": str(node_id),
        "input": name,
        "class_type": node.get("class_type", ""),
        "type": WIDGET_TYPES.get(name, "STRING"),
        "default": value,
    }


def graph_fields(api, style_lora=""):
    """Явные привязки редактируемых полей для формы.

    NiceGUI не знает id узлов и не дублирует значения по умолчанию: он читает
    этот блок из <сборка>.api.json. Поменяли denoise в описании графа —
    пересобрали — форма показывает новое значение без правки веб-кода.
    """
    nodes = list(api.items())
    texts = [(nid, n) for nid, n in nodes if n["class_type"] == "CLIPTextEncode"]
    neg_id = next((nid for nid, n in texts
                   if str(n["inputs"].get("text", "")).startswith(NEG_SIGNS)), None)
    pos_id = next((nid for nid, n in texts if nid != neg_id), None)
    samplers = [(nid, n) for nid, n in nodes if n["class_type"] == "KSampler"]
    main_id = max(samplers, key=lambda x: float(x[1]["inputs"].get("denoise", 0)))[0] \
        if samplers else None

    fields = {}
    if pos_id:
        fields["positive"] = _field(api, pos_id, "text")
    if neg_id:
        fields["negative"] = _field(api, neg_id, "text")
    image = next(((nid, n) for nid, n in nodes if n["class_type"] == "LoadImage"), None)
    if image:
        fields["image"] = _field(api, image[0], "image")
    if main_id:
        for key, name in (("seed", "seed"), ("denoise", "denoise"), ("steps", "steps"),
                          ("cfg", "cfg"), ("sampler", "sampler_name"),
                          ("scheduler", "scheduler")):
            f = _field(api, main_id, name)
            if f:
                fields[key] = f
    cn = next(((nid, n) for nid, n in nodes
               if n["class_type"] == "ControlNetApplyAdvanced"), None)
    if cn:
        for key, name in (("cn_strength", "strength"), ("cn_start", "start_percent"),
                          ("cn_end", "end_percent")):
            f = _field(api, cn[0], name)
            if f:
                fields[key] = f
    ckpt = next(((nid, n) for nid, n in nodes
                 if n["class_type"] == "CheckpointLoaderSimple"), None)
    if ckpt:
        f = _field(api, ckpt[0], "ckpt_name")
        if f:
            fields["checkpoint"] = f
    save = next(((nid, n) for nid, n in nodes if n["class_type"] == "SaveImage"), None)
    if save:
        f = _field(api, save[0], "filename_prefix")
        if f:
            fields["save_prefix"] = f

    slots = []
    for nid, n in nodes:
        if n["class_type"] != "LoraLoaderModelOnly":
            continue
        name = str(n["inputs"].get("lora_name", ""))
        low = name.lower()
        if style_lora and name == style_lora:
            role = "style"
        else:
            role = next((r for frag, r in LORA_ROLES if frag in low), "lora")
        slots.append({
            "role": role, "node": nid, "input": "lora_name",
            "class_type": "LoraLoaderModelOnly",
            "strength_input": "strength_model",
            "value": name,
            "weight": n["inputs"].get("strength_model"),
        })
    return {"fields": fields, "loras": slots,
            "samplers": [nid for nid, n in nodes if n["class_type"] == "KSampler"]}


def meta(spec, api):
    """Стилевые настройки графа для CLI: шаблон промпта, негатив, стилевая LoRA.

    Без этого photo_prompt.py переписывал оба промпта целиком и терял зашитое
    в сборку: солнечный свет и ночной негатив у GTA, тёплую палитру у bimbo.
    Шаблон выводится из самого промпта графа, поэтому расхождению взяться неоткуда.

    Стилевая LoRA приходит из описания сборки по роли `style`, а не поиском
    «у кого вес 0.9»: иначе смена веса в спецификации ломала бы вариант.
    Запасной путь (описание без роли, например старый вызов `build(name, spec)`)
    — единственная ненулевая LoRA графа.
    """
    if not spec.get("style"):
        return None
    texts = [n["inputs"].get("text", "") for n in api.values()
             if n["class_type"] == "CLIPTextEncode"]
    neg_i = next((i for i, t in enumerate(texts) if t.startswith(NEG_SIGNS)), None)
    if neg_i is None or len(texts) < 2:
        return None
    pos_i = next(i for i in range(len(texts)) if i != neg_i)

    sample = str(spec["style"].get("sample") or "")
    after = str(spec["style"].get("after") or "")
    if sample and sample in texts[pos_i]:
        template = texts[pos_i].replace(sample, SUBJECT_SLOT, 1)
    elif after and after in texts[pos_i]:
        template = texts[pos_i].replace(after, after + ", " + SUBJECT_SLOT, 1)
    else:
        return None

    style_lora = str(spec.get("style_lora") or "")
    style_weight = spec.get("style_weight")
    if not style_lora:
        loras = [(n["inputs"]["lora_name"], float(n["inputs"]["strength_model"]))
                 for n in api.values()
                 if n["class_type"] == "LoraLoaderModelOnly"
                 and float(n["inputs"]["strength_model"]) != 0]
        unique = loras[0] if len(loras) == 1 else None
        style_lora = unique[0] if unique else ""
        style_weight = unique[1] if unique else None
    out = {
        "label": spec.get("title") or "",
        "positive_template": template,
        "negative": texts[neg_i],
        "style_lora": style_lora,
        "style_weight": style_weight,
    }
    out.update(graph_fields(api, style_lora))
    return out


def build(name, spec):
    """Описание сборки -> (граф для интерфейса, payload для /prompt).

    Файлы не пишет: это делает драйвер, чтобы проверки могли собрать граф в
    память и сравнить с эталоном.
    """
    nodes = spec["nodes"]
    order = topo(nodes)
    by_id = {n["id"]: n for n in nodes}
    link_id, links, ui_nodes = 1, [], []
    out_links = {}
    node_in = {}

    for nid in order:
        n = by_id[nid]
        sockets, widgets, outs = DEFS[n["type"]]
        wired_order = [s for s in sockets if s in n.get("in", {})]
        for sock in wired_order:
            src = n["in"][sock]
            lid = link_id
            link_id += 1
            typ = DEFS[by_id[src[0]]["type"]][2][src[1]]
            links.append([lid, src[0], src[1], nid, wired_order.index(sock), typ])
            out_links.setdefault((src[0], src[1]), []).append(lid)
            node_in.setdefault(nid, {})[sock] = lid

    for idx, nid in enumerate(order):
        n = by_id[nid]
        sockets, widgets, outs = DEFS[n["type"]]
        wired = n.get("in", {})
        pos = [80 + (idx // 4) * 420, 60 + (idx % 4) * 260]
        ins = []
        for sock in [s for s in sockets if s in wired]:
            typ = DEFS[by_id[wired[sock][0]]["type"]][2][wired[sock][1]]
            ins.append({"name": sock, "type": typ, "link": node_in.get(nid, {}).get(sock)})
        wv = [n.get("w", {}).get(w) for w in widgets]
        # у seed-виджета в UI есть скрытое поле control_after_generate
        if n["type"] in ("KSampler", "FaceDetailer"):
            wv.insert(1, "randomize")
        if n["type"] == "SaveImage":
            wv[0] = "comic/" + name
        node = {
            "id": nid, "type": n["type"], "pos": pos, "size": [380, 120 + 30 * len(widgets)],
            "flags": {}, "order": idx, "mode": 0, "inputs": ins,
            "outputs": [{"name": t, "type": t, "links": out_links.get((nid, i), []), "slot_index": i}
                        for i, t in enumerate(outs)],
            "properties": {"Node name for S&R": n["type"]},
            "widgets_values": wv,
        }
        t = _auto_title(n, wv)
        if t:
            node["title"] = t
        ui_nodes.append(node)

    save_ids = [str(n["id"]) for n in nodes if n["type"] == "SaveImage"]
    workflow = {
        "last_node_id": max(x["id"] for x in nodes),
        "last_link_id": link_id - 1,
        "nodes": ui_nodes, "links": links, "groups": [], "config": {},
        "extra": {
            "ds": {"scale": 0.7, "offset": [0, 0]},
            "linearData": {
                "inputs": [[str(x[0]), x[1]] + ([x[2]] if len(x) > 2 else []) for x in spec["app"]["inputs"]],
                "outputs": spec["app"].get("outputs") or save_ids or [str(max(x["id"] for x in nodes))],
            },
        },
        "version": 0.4,
    }

    api = {}
    for nid in order:
        n = by_id[nid]
        sockets, widgets, _ = DEFS[n["type"]]
        inp = {}
        for w in widgets:
            if w in n.get("w", {}):
                inp[w] = n["w"][w]
        for sock in sockets:
            if sock in n.get("in", {}):
                src = n["in"][sock]
                inp[sock] = [str(src[0]), src[1]]
        if n["type"] == "SaveImage":
            inp["filename_prefix"] = "comic/" + name
        api[str(nid)] = {"class_type": n["type"], "inputs": inp}
    return workflow, api


def write(out_dir, name, workflow, api, meta=None):
    """Пишет пару файлов сборки. Пустой `meta` — ключа нет, и это контракт."""
    out = Path(out_dir)
    (out / (name + ".json")).write_text(
        json.dumps(workflow, ensure_ascii=False, indent=2), encoding="utf-8")
    payload = {"prompt": api}
    if meta:
        payload["meta"] = meta
    (out / (name + ".api.json")).write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
