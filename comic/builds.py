"""Все 12 сборок — данные: база, LoRA с ролями и триггерами, промпты, сэмплеры.

Здесь нет ни одной операции над словарями графа. Сборка описывается строкой
таблицы: какой стиль (роль `style`, вес, триггер), какое тело, кисти и
персонаж, какой промпт и негатив, какие сэмплеры, разрешение и поля App Mode.
`comic/workflow.py` собирает из этого граф и `meta`, `tools/build_workflows.py`
пишет файлы и гоняет их через API.

Повторяющиеся формы графа описаны рецептами (`scene`, `photo`):
их строки отличаются только данными. Одиночные формы (маска, поза, возврат
стиля, Noct) — узлы как данные, без рецепта на один случай.

Что здесь заменено данными (spec 7.2):

  * `_swap_style` — строка `style=Lora(...)` у сборки. Раньше стиль искался
    по весу 0.9, поэтому смена веса в спецификации ломала бы вариант, а чужой
    `theofficialpit style` оставался в промпте рядом с чужой LoRA;
  * `_add_hands` — слот `hands=Lora(..., "hands")` в рецепте: LoRA рук — такая
    же строка, как тело и персонаж, и стоит на своём месте в цепочке;
  * `_tune_bimbo` — поля `positive_extra`, `negative_extra`, `denoise` и
    `style_note` у строки бимбо, а не правка чужой сборки после факта.

Ничего не менять здесь «по пути»: тексты промптов и значения графа заморожены
эталонами `tools/golden/` (spec 7.7), и P3 — переезд, а не правка.
"""
from dataclasses import dataclass, replace

SDXL = "WAI-Nsfw-Illustrious-17.safetensors"
NOCT = "NoctQA_V1_int8_convrot.safetensors"
NOCT_TE = "qwen3vl_8b_int8_convrot.safetensors"
NOCT_VAE = "qwen_image_2.1_vae_bf16.safetensors"
# Глубина 07/08: Depth Anything 3, нативные узлы ComfyUI (`geometry_estimation`).
# DA2 (`depth_anything_v2_vitl.pth`, узел `DepthAnythingV2Preprocessor`) остаётся
# запасным путём: старые графы живут в истории git, и `DEFS` его помнит.
DA3 = "depth_anything_3_mono_large.safetensors"
CONTROLNET = "controlnet-union-sdxl-promax.safetensors"

# Общий негатив сборок Illustrious: фотореализм и мусор против плоского стиля.
TEST_NEG = ("photo, photorealistic, 3d render, soft shading, airbrush, gradient, blurry, low quality, "
            "halftone dots, screentone, dotted texture, moire, freckles, skin texture, "
            "bad anatomy, bad hands, extra fingers, fewer fingers, watermark, text, signature, "
            "child, loli, shota, young")

# Сцена 01 и образец 04: субъект — человек в городе. `{}` — триггер стиля.
SCENE_SAMPLE = ("comic book illustration, {}, adult woman, short hair, leather jacket, "
                "city street at dusk, bold black outlines, flat colors, clean skin, high contrast")

# Хвост плоского стиля: стоит после субъекта в 05 и в 07.
STYLE_SUFFIX = ("bold black outlines, flat colors, clean skin, high contrast, "
                "limited palette, clean silhouette")

# Субъект фото-ветки: отсюда `meta.positive_template` получает {{subject}}.
PHOTO_SAMPLE = "adult woman, portrait"
PHOTO_PROMPT = "comic book illustration, {}, " + PHOTO_SAMPLE + ", " + STYLE_SUFFIX

GTA_PROMPT = ("GTA V loading screen artwork, gtav, copic-makers, western comics (style), "
              "copic_markers_painting, digital painting, warm golden sunlight, "
              "saturated colors, bold black outlines, semi-realistic, Los Santos, palm trees, "
              "blue sky, beach, lens flare, advertising poster")
GTA_NEG = ("photo, photorealistic, 3d render, dark, gloomy, desaturated, black and white, "
           "night, rain, blurry, low quality, bad anatomy, bad hands, extra fingers, "
           "watermark, text, signature, child, loli, shota, young")

NOCT_PROMPT = ("An anime illustration of an adult woman in a leather jacket on a rainy city street at night, "
               "soft rim light, detailed background, cinematic composition")
NOCT_NEG = "low quality, blurry, bad anatomy, extra fingers, watermark, text, child"

MASK_PROMPT = "leather jacket, open collar, detailed fabric, flat colors"

# Подписи полей App Mode: одни и те же в разных сборках, поэтому строки общие.
LABELS = {
    "seed": "сид: randomize = каждый раз новый",
    "body": "тело: Sexy_Detailer (0 = выкл)",
    "hands": "кисти: hands_xl (0 = выкл)",
    "image": "исходное фото",
}


@dataclass(frozen=True)
class Lora:
    """Строка LoRA: файл, роль в цепочке и триггер.

    `trigger` — как триггер называется в подписи поля, `prompt_trigger` — что
    реально стоит в промпте. У ThePit они разные: подпись говорит «thick lips»,
    а в промпте перед триггером идёт ещё `theofficialpit style`, которого нет
    у остальных стилей (spec 7.4).
    """
    file: str
    role: str
    weight: float = 0.0
    trigger: str = ""
    prompt_trigger: str = ""
    label: str = ""

    def in_prompt(self):
        """Фрагмент промпта, который ставится на место `{}` в шаблоне."""
        return self.prompt_trigger or self.trigger

    def at(self, weight):
        """Тот же файл с другим весом (тело в 01 стоит на 0.5, в 07 — на 0)."""
        return replace(self, weight=weight)

    def desc(self):
        """Подпись поля «стиль» в App Mode."""
        return f"стиль: {self.label} (триггер {self.trigger})"


PIT = Lora("thepit2_1768210.safetensors", "style", 0.9,
           trigger="thick lips", prompt_trigger="theofficialpit style, thick lips",
           label="TheOfficialPit V2")
COMIC = Lora("style\\thepit_comic.safetensors", "style", 0.9,
             trigger="ComicPitXX", label="комикс ThePit")
JP = Lora("style\\johnpersons_original.safetensors", "style", 0.9,
          trigger="jpstyle", label="оригинал John Persons")
BIMBO = Lora("style\\bimbofusion.safetensors", "style", 0.6,
             trigger="bimbofusion artstyle", label="бимбо Bimbofusion")
GTA = Lora("GTAV_IL_MIX.safetensors", "style", 0.85,
           trigger="gtav, copic-makers, western comics (style), copic_markers_painting",
           label="GTAV IL")

BODY = Lora("Sexy_Detailer.safetensors", "body", 0.0)
HANDS = Lora("detail\\hands_xl.safetensors", "hands", 0.0)
PATTY = Lora("P477Y.safetensors", "character", 0.0)


@dataclass
class Build:
    """Собранное описание сборки: узлы, поля App Mode, место субъекта в промпте."""
    name: str
    title: str
    nodes: list
    app: list
    style: dict | None = None
    style_lora: str = ""
    style_weight: float | None = None

    def spec(self):
        """Описание в том виде, в каком его читает `comic.workflow.build`."""
        out = {
            "title": self.title,
            "nodes": self.nodes,
            "app": {"inputs": [[nid, field, {"description": desc}] for nid, field, desc in self.app]},
        }
        if self.style:
            out["style"] = self.style
        if self.style_lora:
            out["style_lora"] = self.style_lora
            out["style_weight"] = self.style_weight
        return out


def node(nid, typ, w=None, **wired):
    """Узел графа: id, тип, значения виджетов и связи `сокет=(узел, слот)`."""
    out = {"id": nid, "type": typ}
    if w:
        out["w"] = dict(w)
    if wired:
        out["in"] = {name: list(src) for name, src in wired.items()}
    return out


def lora_node(nid, lora, model):
    return node(nid, "LoraLoaderModelOnly",
                {"lora_name": lora.file, "strength_model": lora.weight}, model=model)


def sampler(nid, *, model, positive, negative, latent, steps, cfg, denoise,
            seed=12345, sampler_name="euler_ancestral", scheduler="normal"):
    return node(nid, "KSampler",
                {"seed": seed, "steps": steps, "cfg": cfg, "sampler_name": sampler_name,
                 "scheduler": scheduler, "denoise": denoise},
                model=model, positive=positive, negative=negative, latent_image=latent)


def clip_text(nid, text, clip):
    return node(nid, "CLIPTextEncode", {"text": text}, clip=clip)


def decode(nid, samples, vae):
    return node(nid, "VAEDecode", samples=samples, vae=vae)


def encode(nid, pixels, vae):
    return node(nid, "VAEEncode", pixels=pixels, vae=vae)


def save(nid, images):
    # filename_prefix сборщик ставит сам: `comic/<имя сборки>`.
    return node(nid, "SaveImage", images=images)


# ---------------------------------------------------------------- рецепты

def scene(*, name, title, style, body, hands=None, prompt, negative=TEST_NEG):
    """01: текст -> картинка с hires-проходом. Стиль, тело и (если есть) кисти.

    Порядок узлов: кисти добавляются последними, поэтому топологический порядок
    (а с ним pos и order графа) остаётся прежним.
    """
    model = 3
    nodes = [
        node(1, "CheckpointLoaderSimple", {"ckpt_name": SDXL}),
        lora_node(2, style, (1, 0)),
        lora_node(3, body, (20, 0) if hands else (2, 0)),
        node(30, "CLIPSetLastLayer", {"stop_at_clip_layer": -2}, clip=(1, 1)),
        clip_text(4, prompt, (30, 0)),
        clip_text(5, negative, (30, 0)),
        node(6, "EmptyLatentImage", {"width": 832, "height": 1216, "batch_size": 1}),
        sampler(7, model=(model, 0), positive=(4, 0), negative=(5, 0), latent=(6, 0),
                steps=30, cfg=6.0, denoise=1.0),
        decode(8, (7, 0), (1, 2)),
        node(9, "ImageScaleBy", {"upscale_method": "lanczos", "scale_by": 1.5}, image=(8, 0)),
        encode(10, (9, 0), (1, 2)),
        sampler(11, model=(model, 0), positive=(4, 0), negative=(5, 0), latent=(10, 0),
                steps=18, cfg=6.0, denoise=0.45),
        decode(12, (11, 0), (1, 2)),
        save(13, (12, 0)),
    ]
    if hands:
        nodes.append(lora_node(20, hands, (2, 0)))
    app = [
        (1, "ckpt_name", "чекпойнт: WAI / Hassaku / PornMaster"),
        (4, "text", "что нарисовать: персонаж, действие, одежда, фон"),
        (5, "text", "что исключить (общий негатив уже внутри)"),
        (6, "width", "832 для портрета"),
        (6, "height", "1216 для портрета"),
        (2, "strength_model", style.desc()),
        (3, "strength_model", LABELS["body"]),
    ]
    if hands:
        app.append((20, "strength_model", LABELS["hands"]))
    app += [
        (7, "seed", LABELS["seed"]),
        (7, "steps", "28–34 шага"),
        (7, "cfg", "5.5–6.5"),
    ]
    return Build(name, title, nodes, app)


def photo(*, name, title, style, body, hands=None, character=None,
          positive, negative, cn=(0.6, 0.0, 0.85), upscale=1.4,
          first=(30, 6.0, 0.75), second=(15, 6.0, 0.3),
          sample=PHOTO_SAMPLE, after="", style_desc=None, style_note="",
          text_desc="сцена: что на фото и что должно остаться",
          denoise_desc="0.65 — больше сходства, 0.85 — больше стиля; "
                       "0.85–0.9 со слабой глубиной — смена места действия",
          cn_desc="глубина: 0.55–0.65 сохранить сцену, 0.25–0.3 сменить место действия",
          positive_extra="", negative_extra=""):
    """07/08: фото -> стиль через ControlNet глубины и hires-проход.

    Глубину считает Depth Anything 3 (21 → 22 → 23), а не DA2 из
    `controlnet_aux`: узел кастомного пакета тянул свои веса сам и жил вне
    `/object_info`-сверки. Промпты, `meta` и ControlNet не меняются.

    `style` — место субъекта в промпте: `{"sample": ...}` (07, субъект уже в
    тексте) или `{"after": ...}` (08, субъект вставляется после «saturated
    colors», чтобы свет и палитра шли первыми).
    """
    model = 4 if character else 3
    if after and after not in positive:
        raise ValueError(f"в промпте нет «{after}»: субъект некуда подставить")
    nodes = [
        node(1, "CheckpointLoaderSimple", {"ckpt_name": SDXL}),
        lora_node(2, style, (1, 0)),
        lora_node(3, body, (20, 0) if hands else (2, 0)),
    ]
    if character:
        nodes.append(lora_node(4, character, (3, 0)))
    nodes += [
        node(30, "CLIPSetLastLayer", {"stop_at_clip_layer": -2}, clip=(1, 1)),
        clip_text(5, positive + positive_extra, (30, 0)),
        clip_text(6, negative + negative_extra, (30, 0)),
        node(7, "LoadImage", {"image": "photo.png"}),
        node(8, "ImageScaleToTotalPixels",
             {"upscale_method": "lanczos", "megapixels": 1.0, "resolution_steps": 1},
             image=(7, 0)),
        # Глубина — Depth Anything 3 нативными узлами: модель, инференс, рендер.
        # 21/22/23 свободны: 20 занят LoRA рук, 13 — сэмплером.
        # `mode` и `output` — DynamicCombo: в /prompt это **строка** с ключом
        # варианта, а поля варианта идут рядом отдельными входами с точкой
        # (`output.normalization`). Так же их сериализует и фронтенд ComfyUI
        # (`<combo>.<поле>`); объект вместо строки узел молча теряет — проверено
        # падением `execute() missing 1 required positional argument: 'mode'`.
        # `v2_style` — та же нормализация, что у DA2 в controlnet_aux: полярность
        # глубины остаётся совместимой с union-ControlNet. `min_max`/`raw` без
        # замера не берём.
        node(21, "LoadDA3Model", {"model_name": DA3, "weight_dtype": "default"}),
        node(22, "DA3Inference",
             {"resolution": 504, "resize_method": "upper_bound_resize",
              "mode": "mono"},
             da3_model=(21, 0), image=(8, 0)),
        node(23, "DA3Render",
             {"output": "depth", "output.normalization": "v2_style",
              "output.apply_sky_clip": False},
             da3_geometry=(22, 0)),
        node(10, "ControlNetLoader", {"control_net_name": CONTROLNET}),
        node(11, "ControlNetApplyAdvanced",
             {"strength": cn[0], "start_percent": cn[1], "end_percent": cn[2]},
             positive=(5, 0), negative=(6, 0), control_net=(10, 0), image=(23, 0)),
        encode(12, (8, 0), (1, 2)),
        sampler(13, model=(model, 0), positive=(11, 0), negative=(11, 1), latent=(12, 0),
                steps=first[0], cfg=first[1], denoise=first[2]),
        decode(14, (13, 0), (1, 2)),
        node(15, "ImageScaleBy", {"upscale_method": "lanczos", "scale_by": upscale}, image=(14, 0)),
        encode(16, (15, 0), (1, 2)),
        sampler(17, model=(model, 0), positive=(11, 0), negative=(11, 1), latent=(16, 0),
                steps=second[0], cfg=second[1], denoise=second[2]),
        decode(18, (17, 0), (1, 2)),
        save(19, (18, 0)),
    ]
    if hands:
        nodes.append(lora_node(20, hands, (2, 0)))
    app = [
        (7, "image", LABELS["image"]),
        (5, "text", text_desc),
        (13, "denoise", denoise_desc),
        (2, "strength_model", (style_desc or style.desc()) + style_note),
        (11, "strength", cn_desc),
    ]
    if character:
        app += [
            (4, "lora_name", "персонаж: P477Y (Patty) или P4M3L4 (Pamela)"),
            (4, "strength_model", "вес персонажа (0 = выкл)"),
        ]
    if hands:
        app.append((20, "strength_model", LABELS["hands"]))
    app += [
        (3, "strength_model", LABELS["body"]),
        (13, "seed", LABELS["seed"]),
    ]
    return Build(name, title, nodes, app,
                 style={"after": after} if after else {"sample": sample},
                 style_lora=style.file, style_weight=style.weight)


# ---------------------------------------------------------------- 12 сборок

BUILDS = {b.name: b for b in [
    scene(name="01_scene_thepit", title="01 Сцена · ThePit V2",
          style=PIT, body=BODY.at(0.5), hands=HANDS,
          prompt=SCENE_SAMPLE.format(PIT.in_prompt())),
    scene(name="01_scene_comic", title="01 Сцена · комикс ThePit",
          style=COMIC, body=BODY.at(0.5), hands=HANDS,
          prompt=SCENE_SAMPLE.format(COMIC.in_prompt())),
    scene(name="01_scene_jp", title="01 Сцена · оригинал John Persons",
          style=JP, body=BODY.at(0.5), hands=HANDS,
          prompt=SCENE_SAMPLE.format(JP.in_prompt())),

    photo(name="07_photo_thepit", title="07 Фото в стиль · ThePit V2",
          style=PIT, body=BODY, hands=HANDS, character=PATTY,
          positive=PHOTO_PROMPT.format(PIT.in_prompt()), negative=TEST_NEG),
    photo(name="07_photo_comic", title="07 Фото в стиль · комикс ThePit",
          style=COMIC, body=BODY, hands=HANDS, character=PATTY,
          positive=PHOTO_PROMPT.format(COMIC.in_prompt()), negative=TEST_NEG),
    photo(name="07_photo_jp", title="07 Фото в стиль · оригинал John Persons",
          style=JP, body=BODY, hands=HANDS, character=PATTY,
          positive=PHOTO_PROMPT.format(JP.in_prompt()), negative=TEST_NEG),
    photo(name="07_photo_bimbo", title="07 Фото в стиль · бимбо Bimbofusion",
          style=BIMBO, body=BODY, hands=HANDS, character=PATTY,
          positive=PHOTO_PROMPT.format(BIMBO.in_prompt()), negative=TEST_NEG,
          # Bimbofusion уводит палитру в холод: вес ниже, цветовые якоря в
          # промпте и негатив против холодного дрейфа. Человека описывает фото,
          # поэтому здесь только то, что от него не зависит.
          style_note=", вес 0.6, тёплые якоря",
          positive_extra=", warm skin tone, natural skin color",
          negative_extra=", grey skin, pale skin, red eyes, monochrome, desaturated",
          first=(30, 6.0, 0.7)),

    photo(name="08_photo_gta", title="08 Фото → арт GTA",
          style=GTA, body=BODY,
          positive=GTA_PROMPT, negative=GTA_NEG,
          cn=(0.5, 0.0, 0.8), upscale=1.3,
          first=(32, 6.5, 0.85), second=(16, 6.5, 0.3),
          after="saturated colors", style_desc="сила GTA-стиля",
          text_desc="сцена: город, пляж, пальмы, время суток",
          denoise_desc="0.80–0.90: чем выше, тем сильнее стиль",
          cn_desc="жёсткость позы и композиции"),
]}

# ---------------------------------------------------------------- одиночные формы

BUILDS["02_edit_mask"] = Build(
    "02_edit_mask", "02 Правка по маске",
    nodes=[
        node(1, "CheckpointLoaderSimple", {"ckpt_name": SDXL}),
        lora_node(2, PIT, (1, 0)),
        lora_node(20, BODY, (2, 0)),
        node(3, "LoadImage", {"image": "example.png"}),
        node(14, "LoadImage", {"image": "mask.png"}),
        node(15, "ImageToMask", {"channel": "red"}, image=(14, 0)),
        node(4, "GrowMask", {"expand": 8, "tapered_corners": True}, mask=(15, 0)),
        node(5, "FeatherMask", {"left": 12, "top": 12, "right": 12, "bottom": 12}, mask=(4, 0)),
        encode(6, (3, 0), (1, 2)),
        node(7, "SetLatentNoiseMask", samples=(6, 0), mask=(5, 0)),
        node(30, "CLIPSetLastLayer", {"stop_at_clip_layer": -2}, clip=(1, 1)),
        clip_text(8, MASK_PROMPT, (30, 0)),
        clip_text(9, TEST_NEG, (30, 0)),
        sampler(10, model=(20, 0), positive=(8, 0), negative=(9, 0), latent=(7, 0),
                steps=28, cfg=6.0, denoise=0.85),
        decode(11, (10, 0), (1, 2)),
        node(12, "ImageCompositeMasked", {"x": 0, "y": 0, "resize_source": False},
             destination=(3, 0), source=(11, 0), mask=(5, 0)),
        save(13, (12, 0)),
    ],
    app=[
        (3, "image", "кадр, который правим"),
        (14, "image", "маска: белое = меняем, чёрное = не трогаем"),
        (8, "text", "что должно появиться в области маски"),
        (10, "denoise", "0.85 — одежда, 0.40 — макияж"),
        (10, "seed", LABELS["seed"]),
        (2, "strength_model", PIT.desc()),
        (20, "strength_model", LABELS["body"]),
    ])

BUILDS["04_pose_reference"] = Build(
    "04_pose_reference", "04 Поза и персонаж",
    nodes=[
        node(1, "CheckpointLoaderSimple", {"ckpt_name": SDXL}),
        node(2, "LoadImage", {"image": "pose.png"}),
        node(3, "DWPreprocessor",
             {"detect_hand": "enable", "detect_body": "enable", "detect_face": "enable",
              "resolution": 768, "bbox_detector": "yolox_l.onnx",
              "pose_estimator": "dw-ll_ucoco_384_bs5.torchscript.pt",
              "scale_stick_for_xinsr_cn": "disable"}, image=(2, 0)),
        node(4, "LoadImage", {"image": "character.png"}),
        node(5, "IPAdapterModelLoader", {"ipadapter_file": "ip-adapter-plus_sdxl_vit-h.safetensors"}),
        node(6, "CLIPVisionLoader", {"clip_name": "CLIP-ViT-H-14-laion2B-s32B-b79K.safetensors"}),
        lora_node(20, PIT, (1, 0)),
        node(7, "IPAdapterAdvanced",
             {"weight": 0.8, "weight_type": "linear", "combine_embeds": "concat",
              "start_at": 0.0, "end_at": 1.0, "embeds_scaling": "V only"},
             model=(20, 0), ipadapter=(5, 0), image=(4, 0), clip_vision=(6, 0)),
        node(30, "CLIPSetLastLayer", {"stop_at_clip_layer": -2}, clip=(1, 1)),
        clip_text(8, SCENE_SAMPLE.format(PIT.in_prompt()), (30, 0)),
        clip_text(9, TEST_NEG, (30, 0)),
        node(10, "ControlNetLoader", {"control_net_name": CONTROLNET}),
        node(11, "ControlNetApplyAdvanced",
             {"strength": 0.8, "start_percent": 0.0, "end_percent": 1.0},
             positive=(8, 0), negative=(9, 0), control_net=(10, 0), image=(3, 0)),
        node(12, "EmptyLatentImage", {"width": 832, "height": 1216, "batch_size": 1}),
        sampler(13, model=(7, 0), positive=(11, 0), negative=(11, 1), latent=(12, 0),
                steps=30, cfg=6.0, denoise=1.0),
        decode(14, (13, 0), (1, 2)),
        save(15, (14, 0)),
    ],
    app=[
        (2, "image", "референс позы (фото или рисунок)"),
        (4, "image", "референс персонажа: лицо и стиль"),
        (8, "text", "что нарисовать"),
        (11, "strength", "сила ControlNet: 0.7–0.9"),
        (7, "weight", "сила IP-Adapter: 0.7–0.9"),
        (13, "seed", LABELS["seed"]),
    ])

BUILDS["05_style_return"] = Build(
    "05_style_return", "05 Возврат стиля",
    nodes=[
        node(1, "CheckpointLoaderSimple", {"ckpt_name": SDXL}),
        lora_node(2, PIT, (1, 0)),
        node(3, "LoadImage", {"image": "example.png"}),
        node(30, "CLIPSetLastLayer", {"stop_at_clip_layer": -2}, clip=(1, 1)),
        clip_text(4, f"comic book illustration, {PIT.in_prompt()}, " + STYLE_SUFFIX, (30, 0)),
        clip_text(5, TEST_NEG, (30, 0)),
        encode(6, (3, 0), (1, 2)),
        sampler(7, model=(2, 0), positive=(4, 0), negative=(5, 0), latent=(6, 0),
                steps=20, cfg=6.0, denoise=0.35),
        decode(8, (7, 0), (1, 2)),
        save(9, (8, 0)),
    ],
    app=[
        (3, "image", "кадр после Qwen-правки"),
        (4, "text", "описание текущего кадра; по умолчанию только блок стиля, пример не подставляется"),
        (7, "denoise", "0.30–0.40: перекрасить, не перерисовать"),
        (2, "strength_model", PIT.desc()),
        (7, "seed", LABELS["seed"]),
    ])

BUILDS["06_scene_noct"] = Build(
    "06_scene_noct", "06 Ночь — Noct Q Anime",
    nodes=[
        node(1, "UNETLoader", {"unet_name": NOCT, "weight_dtype": "default"}),
        node(2, "CLIPLoader", {"clip_name": NOCT_TE, "type": "qwen_image", "device": "default"}),
        node(3, "VAELoader", {"vae_name": NOCT_VAE}),
        node(4, "TextEncodeQwenImage21",
             {"prompt": NOCT_PROMPT, "negative_prompt": NOCT_NEG, "resolution": 1024},
             clip=(2, 0), vae=(3, 0)),
        node(5, "ModelSamplingAuraFlow", {"shift": 3.0, "sampling": "flow"}, model=(1, 0)),
        sampler(6, model=(5, 0), positive=(4, 0), negative=(4, 1), latent=(4, 2),
                steps=8, cfg=1.0, denoise=1.0,
                sampler_name="res_multistep", scheduler="simple"),
        decode(7, (6, 0), (3, 0)),
        save(8, (7, 0)),
    ],
    app=[
        (4, "prompt", "сцена; начинать с «An anime illustration of…»"),
        (4, "negative_prompt", "что исключить"),
        (4, "resolution", "1024 — нативное"),
        (5, "shift", "shift AuraFlow, рецепт автора 3.0"),
        (6, "seed", LABELS["seed"]),
        (6, "steps", "8 шагов по рецепту автора"),
    ])

# Порядок как в прежней таблице: от него зависит порядок печати драйвера.
ORDER = [
    "01_scene_thepit", "02_edit_mask",
    "07_photo_thepit", "08_photo_gta", "04_pose_reference", "05_style_return",
    "06_scene_noct", "01_scene_comic", "01_scene_jp",
    "07_photo_comic", "07_photo_jp", "07_photo_bimbo",
]
if sorted(ORDER) != sorted(BUILDS):
    raise RuntimeError("ORDER и BUILDS разошлись: "
                       + str(sorted(set(ORDER) ^ set(BUILDS))))
BUILDS = {name: BUILDS[name] for name in ORDER}

# Описания сборок в том виде, в каком их читает сборщик графа. Проверки и
# драйвер берут отсюда: описание остаётся словарём, поэтому проверка формы
# может поменять значение и пересобрать граф, не трогая таблицу сборок.
WORKFLOWS = {name: build.spec() for name, build in BUILDS.items()}
