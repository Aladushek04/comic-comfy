"""Генератор воркфлоу ComfyUI.

Одно описание графа -> два файла:
  <name>.json      формат графа для интерфейса (open in ComfyUI)
  <name>.api.json  формат payload для POST /prompt (проверка прогоном)

Запуск:
  python build_workflows.py            # только сгенерировать
  python build_workflows.py --run 01   # сгенерировать и прогнать 01 через API
"""
import json
import sys
import time
import urllib.request

BASE = "http://127.0.0.1:8199"
OUT = r"G:\AI\comic-comfy\workflows"

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

SDXL = "WAI-Nsfw-Illustrious-17.safetensors"
QWEN_EDIT = "qwen-image-edit-2511-uncensored-Q4_K_M.gguf"
QWEN_EDIT_TE = "qwen_2.5_vl_7b_fp8_scaled.safetensors"
QWEN_EDIT_VAE = "qwen_image_vae.safetensors"
NOCT = "NoctQA_V1_int8_convrot.safetensors"
NOCT_TE = "qwen3vl_8b_int8_convrot.safetensors"
NOCT_VAE = "qwen_image_2.1_vae_bf16.safetensors"
TEST_PROMPT = ("comic book illustration, theofficialpit style, thick lips, adult woman, short hair, "
               "leather jacket, city street at dusk, bold black outlines, flat colors, clean skin, "
               "high contrast")
STYLE_LORA = "thepit2_1768210.safetensors"
STYLE_APP = "стиль: TheOfficialPit V2 (триггер thick lips)"
STYLE_SUFFIX = ("bold black outlines, flat colors, clean skin, high contrast, "
                "limited palette, clean silhouette")
TEST_NEG = ("photo, photorealistic, 3d render, soft shading, airbrush, gradient, blurry, low quality, "
            "halftone dots, screentone, dotted texture, moire, freckles, skin texture, "
            "bad anatomy, bad hands, extra fingers, fewer fingers, watermark, text, signature, "
            "child, loli, shota, young")

WORKFLOWS = {}

# ---------------------------------------------------------------- 01: txt2img
WORKFLOWS["01_scene_thepit"] = {
    "title": "01 Сцена · ThePit V2",
    "nodes": [
        {"id": 1, "type": "CheckpointLoaderSimple", "w": {"ckpt_name": SDXL}},
        {"id": 2, "type": "LoraLoaderModelOnly", "w": {"lora_name": STYLE_LORA, "strength_model": 0.9},
         "in": {"model": [1, 0]}},
        {"id": 3, "type": "LoraLoaderModelOnly", "w": {"lora_name": "Sexy_Detailer.safetensors", "strength_model": 0.5},
         "in": {"model": [2, 0]}},
        {"id": 30, "type": "CLIPSetLastLayer", "w": {"stop_at_clip_layer": -2}, "in": {"clip": [1, 1]}},
        {"id": 4, "type": "CLIPTextEncode", "w": {"text": TEST_PROMPT}, "in": {"clip": [30, 0]}},
        {"id": 5, "type": "CLIPTextEncode", "w": {"text": TEST_NEG}, "in": {"clip": [30, 0]}},
        {"id": 6, "type": "EmptyLatentImage", "w": {"width": 832, "height": 1216, "batch_size": 1}},
        {"id": 7, "type": "KSampler",
         "w": {"seed": 12345, "steps": 30, "cfg": 6.0, "sampler_name": "euler_ancestral",
               "scheduler": "normal", "denoise": 1.0},
         "in": {"model": [3, 0], "positive": [4, 0], "negative": [5, 0], "latent_image": [6, 0]}},
        {"id": 8, "type": "VAEDecode", "in": {"samples": [7, 0], "vae": [1, 2]}},
        {"id": 9, "type": "ImageScaleBy", "w": {"upscale_method": "lanczos", "scale_by": 1.5}, "in": {"image": [8, 0]}},
        {"id": 10, "type": "VAEEncode", "in": {"pixels": [9, 0], "vae": [1, 2]}},
        {"id": 11, "type": "KSampler",
         "w": {"seed": 12345, "steps": 18, "cfg": 6.0, "sampler_name": "euler_ancestral",
               "scheduler": "normal", "denoise": 0.45},
         "in": {"model": [3, 0], "positive": [4, 0], "negative": [5, 0], "latent_image": [10, 0]}},
        {"id": 12, "type": "VAEDecode", "in": {"samples": [11, 0], "vae": [1, 2]}},
        {"id": 13, "type": "SaveImage", "w": {"filename_prefix": "comic/01_scene"}, "in": {"images": [12, 0]}},
    ],
    "app": {"inputs": [[1, "ckpt_name", {"description": "чекпойнт: WAI / Hassaku / PornMaster"}],
                       [4, "text", {"description": "что нарисовать: персонаж, действие, одежда, фон"}],
                       [5, "text", {"description": "что исключить (общий негатив уже внутри)"}],
                       [6, "width", {"description": "832 для портрета"}],
                       [6, "height", {"description": "1216 для портрета"}],
                       [2, "strength_model", {"description": "стиль: TheOfficialPit V2 (триггер thick lips)"}],
                       [3, "strength_model", {"description": "тело: Sexy_Detailer (0 = выкл)"}],
                       [7, "seed", {"description": "сид: randomize = каждый раз новый"}],
                       [7, "steps", {"description": "28–34 шага"}],
                       [7, "cfg", {"description": "5.5–6.5"}]]},
}

# ---------------------------------------------------------------- 02: inpaint
WORKFLOWS["02_edit_mask"] = {
    "title": "02 Правка по маске",
    "nodes": [
        {"id": 1, "type": "CheckpointLoaderSimple", "w": {"ckpt_name": SDXL}},
        {"id": 2, "type": "LoraLoaderModelOnly", "w": {"lora_name": STYLE_LORA, "strength_model": 0.9},
         "in": {"model": [1, 0]}},
        {"id": 20, "type": "LoraLoaderModelOnly", "w": {"lora_name": "Sexy_Detailer.safetensors", "strength_model": 0.0},
         "in": {"model": [2, 0]}},
        {"id": 3, "type": "LoadImage", "w": {"image": "example.png"}},
        {"id": 14, "type": "LoadImage", "w": {"image": "mask.png"}},
        {"id": 15, "type": "ImageToMask", "w": {"channel": "red"}, "in": {"image": [14, 0]}},
        {"id": 4, "type": "GrowMask", "w": {"expand": 8, "tapered_corners": True}, "in": {"mask": [15, 0]}},
        {"id": 5, "type": "FeatherMask", "w": {"left": 12, "top": 12, "right": 12, "bottom": 12}, "in": {"mask": [4, 0]}},
        {"id": 6, "type": "VAEEncode", "in": {"pixels": [3, 0], "vae": [1, 2]}},
        {"id": 7, "type": "SetLatentNoiseMask", "in": {"samples": [6, 0], "mask": [5, 0]}},
        {"id": 30, "type": "CLIPSetLastLayer", "w": {"stop_at_clip_layer": -2}, "in": {"clip": [1, 1]}},
        {"id": 8, "type": "CLIPTextEncode", "w": {"text": "leather jacket, open collar, detailed fabric, flat colors, halftone dots"}, "in": {"clip": [30, 0]}},
        {"id": 9, "type": "CLIPTextEncode", "w": {"text": TEST_NEG}, "in": {"clip": [30, 0]}},
        {"id": 10, "type": "KSampler",
         "w": {"seed": 12345, "steps": 28, "cfg": 6.0, "sampler_name": "euler_ancestral",
               "scheduler": "normal", "denoise": 0.85},
         "in": {"model": [20, 0], "positive": [8, 0], "negative": [9, 0], "latent_image": [7, 0]}},
        {"id": 11, "type": "VAEDecode", "in": {"samples": [10, 0], "vae": [1, 2]}},
        {"id": 12, "type": "ImageCompositeMasked",
         "w": {"x": 0, "y": 0, "resize_source": False},
         "in": {"destination": [3, 0], "source": [11, 0], "mask": [5, 0]}},
        {"id": 13, "type": "SaveImage", "w": {"filename_prefix": "comic/02_inpaint"}, "in": {"images": [12, 0]}},
    ],
    "app": {"inputs": [[3, "image", {"description": "кадр, который правим"}],
                       [14, "image", {"description": "маска: белое = меняем, чёрное = не трогаем"}],
                       [8, "text", {"description": "что должно появиться в области маски"}],
                       [10, "denoise", {"description": "0.85 — одежда, 0.40 — макияж"}],
                       [10, "seed", {"description": "сид: randomize = каждый раз новый"}],
                       [2, "strength_model", {"description": "стиль: TheOfficialPit V2 (триггер thick lips)"}],
                       [20, "strength_model", {"description": "тело: Sexy_Detailer (0 = выкл)"}]]},
}

# ---------------------------------------------------------------- 03: qwen edit
WORKFLOWS["03_edit_qwen_quality"] = {
    "title": "03 Правка Qwen · качество",
    "nodes": [
        {"id": 1, "type": "UnetLoaderGGUF", "w": {"unet_name": QWEN_EDIT}},
        {"id": 2, "type": "CLIPLoader", "w": {"clip_name": QWEN_EDIT_TE, "type": "qwen_image", "device": "default"}},
        {"id": 3, "type": "VAELoader", "w": {"vae_name": QWEN_EDIT_VAE}},
        {"id": 4, "type": "LoraLoaderModelOnly",
         "w": {"lora_name": "qwen-image-edit-plus-nsfw-lora.safetensors", "strength_model": 0.7},
         "in": {"model": [1, 0]}},
        {"id": 5, "type": "LoadImage", "w": {"image": "example.png"}},
        {"id": 6, "type": "ImageScaleToTotalPixels",
         "w": {"upscale_method": "lanczos", "megapixels": 1.0, "resolution_steps": 1},
         "in": {"image": [5, 0]}},
        {"id": 7, "type": "TextEncodeQwenImageEditPlus",
         "w": {"prompt": "change the jacket to a black leather jacket, keep face, hair and background unchanged"},
         "in": {"clip": [2, 0], "vae": [3, 0], "image1": [6, 0]}},
        {"id": 8, "type": "TextEncodeQwenImageEditPlus", "w": {"prompt": ""},
         "in": {"clip": [2, 0], "vae": [3, 0], "image1": [6, 0]}},
        {"id": 9, "type": "VAEEncode", "in": {"pixels": [6, 0], "vae": [3, 0]}},
        {"id": 10, "type": "KSampler",
         "w": {"seed": 12345, "steps": 20, "cfg": 4.0, "sampler_name": "euler",
               "scheduler": "simple", "denoise": 1.0},
         "in": {"model": [4, 0], "positive": [7, 0], "negative": [8, 0], "latent_image": [9, 0]}},
        {"id": 11, "type": "VAEDecode", "in": {"samples": [10, 0], "vae": [3, 0]}},
        {"id": 12, "type": "SaveImage", "w": {"filename_prefix": "comic/03_qwen_edit"}, "in": {"images": [11, 0]}},
    ],
    "app": {"inputs": [[5, "image", {"description": "кадр для правки"}],
                       [7, "prompt", {"description": "инструкция: что изменить, что оставить"}],
                       [4, "strength_model", {"description": "сила NSFW-LoRA, 0.6–0.9"}],
                       [10, "seed", {"description": "сид: randomize = каждый раз новый"}],
                       [10, "steps", {"description": "20 шагов ≈ 6.5 мин"}],
                       [10, "cfg", {"description": "CFG 4"}]]},
}

# ---------------------------------------------------------------- 03b: qwen fast
FAST = json.loads(json.dumps(WORKFLOWS["03_edit_qwen_quality"]))
for _n in FAST["nodes"]:
    if _n["id"] == 4:
        _n["in"] = {"model": [1, 0]}
    if _n["id"] == 10:
        _n["w"]["steps"] = 4
        _n["w"]["cfg"] = 1.0
        _n["in"]["model"] = [13, 0]
FAST["nodes"].append({
    "id": 13, "type": "LoraLoaderModelOnly",
    "w": {"lora_name": "Qwen-Image-Edit-2511-Lightning-4steps-V1.0-bf16.safetensors",
          "strength_model": 1.0},
    "in": {"model": [4, 0]},
})
FAST["title"] = "03 Правка Qwen · быстро"
FAST["app"] = {"inputs": [[5, "image", {"description": "кадр для правки"}],
                          [7, "prompt", {"description": "инструкция: что изменить, что оставить"}],
                          [4, "strength_model", {"description": "сила NSFW-LoRA, 0.6–0.9"}],
                          [10, "seed", {"description": "сид: randomize = каждый раз новый"}],
                          [10, "steps", {"description": "4 шага с Lightning"}],
                          [10, "cfg", {"description": "CFG 1.0 — обязателен для Lightning"}]]}
for _n in FAST["nodes"]:
    if _n["type"] == "SaveImage":
        _n["w"]["filename_prefix"] = "comic/03b_qwen_fast"
WORKFLOWS["03_edit_qwen_fast"] = FAST

# ---------------------------------------------------------------- 07: фото -> стиль
WORKFLOWS["07_photo_thepit"] = {
    "title": "07 Фото в стиль · ThePit V2",
    # Где в промпте стоит субъект: отсюда CLI собирает тот же текст, что и панель.
    "style": {"sample": "adult woman, portrait"},
    "nodes": [
        {"id": 1, "type": "CheckpointLoaderSimple", "w": {"ckpt_name": SDXL}},
        {"id": 2, "type": "LoraLoaderModelOnly",
         "w": {"lora_name": STYLE_LORA, "strength_model": 0.9},
         "in": {"model": [1, 0]}},
        {"id": 3, "type": "LoraLoaderModelOnly",
         "w": {"lora_name": "Sexy_Detailer.safetensors", "strength_model": 0.0},
         "in": {"model": [2, 0]}},
        {"id": 4, "type": "LoraLoaderModelOnly",
         "w": {"lora_name": "P477Y.safetensors", "strength_model": 0.0},
         "in": {"model": [3, 0]}},
        {"id": 30, "type": "CLIPSetLastLayer", "w": {"stop_at_clip_layer": -2}, "in": {"clip": [1, 1]}},
        {"id": 5, "type": "CLIPTextEncode",
         "w": {"text": "comic book illustration, theofficialpit style, thick lips, adult woman, portrait, "
                       + STYLE_SUFFIX},
         "in": {"clip": [30, 0]}},
        {"id": 6, "type": "CLIPTextEncode", "w": {"text": TEST_NEG}, "in": {"clip": [30, 0]}},
        {"id": 7, "type": "LoadImage", "w": {"image": "photo.png"}},
        {"id": 8, "type": "ImageScaleToTotalPixels",
         "w": {"upscale_method": "lanczos", "megapixels": 1.0, "resolution_steps": 1},
         "in": {"image": [7, 0]}},
        {"id": 9, "type": "DepthAnythingV2Preprocessor",
         "w": {"ckpt_name": "depth_anything_v2_vitl.pth", "resolution": 768},
         "in": {"image": [8, 0]}},
        {"id": 10, "type": "ControlNetLoader", "w": {"control_net_name": "controlnet-union-sdxl-promax.safetensors"}},
        {"id": 11, "type": "ControlNetApplyAdvanced",
         "w": {"strength": 0.6, "start_percent": 0.0, "end_percent": 0.85},
         "in": {"positive": [5, 0], "negative": [6, 0], "control_net": [10, 0], "image": [9, 0]}},
        {"id": 12, "type": "VAEEncode", "in": {"pixels": [8, 0], "vae": [1, 2]}},
        {"id": 13, "type": "KSampler",
         "w": {"seed": 12345, "steps": 30, "cfg": 6.0, "sampler_name": "euler_ancestral",
               "scheduler": "normal", "denoise": 0.75},
         "in": {"model": [4, 0], "positive": [11, 0], "negative": [11, 1], "latent_image": [12, 0]}},
        {"id": 14, "type": "VAEDecode", "in": {"samples": [13, 0], "vae": [1, 2]}},
        {"id": 15, "type": "ImageScaleBy", "w": {"upscale_method": "lanczos", "scale_by": 1.4}, "in": {"image": [14, 0]}},
        {"id": 16, "type": "VAEEncode", "in": {"pixels": [15, 0], "vae": [1, 2]}},
        {"id": 17, "type": "KSampler",
         "w": {"seed": 12345, "steps": 15, "cfg": 6.0, "sampler_name": "euler_ancestral",
               "scheduler": "normal", "denoise": 0.3},
         "in": {"model": [4, 0], "positive": [11, 0], "negative": [11, 1], "latent_image": [16, 0]}},
        {"id": 18, "type": "VAEDecode", "in": {"samples": [17, 0], "vae": [1, 2]}},
        {"id": 19, "type": "SaveImage", "w": {"filename_prefix": "comic/07_style"}, "in": {"images": [18, 0]}},
    ],
    "app": {"inputs": [[7, "image", {"description": "исходное фото"}], [5, "text", {"description": "сцена: что на фото и что должно остаться"}],
                       [13, "denoise", {"description": "0.65 — больше сходства, 0.85 — больше стиля"}],
                       [2, "strength_model", {"description": "стиль: TheOfficialPit V2 (триггер thick lips)"}],
                       [11, "strength", {"description": "жёсткость позы: ControlNet depth"}],
                       [4, "lora_name", {"description": "персонаж: P477Y (Patty) или P4M3L4 (Pamela)"}],
                       [4, "strength_model", {"description": "вес персонажа (0 = выкл)"}],
                       [3, "strength_model", {"description": "тело: Sexy_Detailer (0 = выкл)"}],
                       [13, "seed", {"description": "сид: randomize = каждый раз новый"}]]},
}

# ---------------------------------------------------------------- 08: фото -> GTA постер
WORKFLOWS["08_photo_gta"] = {
    "title": "08 Фото → арт GTA",
    # У GTA в промпте нет субъекта: он вставляется после «saturated colors»,
    # чтобы солнечный свет и палитра остались перед описанием человека.
    "style": {"after": "saturated colors"},
    "nodes": [
        {"id": 1, "type": "CheckpointLoaderSimple", "w": {"ckpt_name": SDXL}},
        {"id": 2, "type": "LoraLoaderModelOnly",
         "w": {"lora_name": "GTAV_IL_MIX.safetensors", "strength_model": 0.85},
         "in": {"model": [1, 0]}},
        {"id": 3, "type": "LoraLoaderModelOnly",
         "w": {"lora_name": "Sexy_Detailer.safetensors", "strength_model": 0.0},
         "in": {"model": [2, 0]}},
        {"id": 30, "type": "CLIPSetLastLayer", "w": {"stop_at_clip_layer": -2}, "in": {"clip": [1, 1]}},
        {"id": 5, "type": "CLIPTextEncode",
         "w": {"text": "GTA V loading screen artwork, gtav, copic-makers, western comics (style), "
                       "copic_markers_painting, digital painting, warm golden sunlight, "
                       "saturated colors, bold black outlines, semi-realistic, Los Santos, palm trees, "
                       "blue sky, beach, lens flare, advertising poster"},
         "in": {"clip": [30, 0]}},
        {"id": 6, "type": "CLIPTextEncode",
         "w": {"text": "photo, photorealistic, 3d render, dark, gloomy, desaturated, black and white, "
                       "night, rain, blurry, low quality, bad anatomy, bad hands, extra fingers, "
                       "watermark, text, signature, child, loli, shota, young"},
         "in": {"clip": [30, 0]}},
        {"id": 7, "type": "LoadImage", "w": {"image": "photo.png"}},
        {"id": 8, "type": "ImageScaleToTotalPixels",
         "w": {"upscale_method": "lanczos", "megapixels": 1.0, "resolution_steps": 1},
         "in": {"image": [7, 0]}},
        {"id": 9, "type": "DepthAnythingV2Preprocessor",
         "w": {"ckpt_name": "depth_anything_v2_vitl.pth", "resolution": 768},
         "in": {"image": [8, 0]}},
        {"id": 10, "type": "ControlNetLoader", "w": {"control_net_name": "controlnet-union-sdxl-promax.safetensors"}},
        {"id": 11, "type": "ControlNetApplyAdvanced",
         "w": {"strength": 0.5, "start_percent": 0.0, "end_percent": 0.8},
         "in": {"positive": [5, 0], "negative": [6, 0], "control_net": [10, 0], "image": [9, 0]}},
        {"id": 12, "type": "VAEEncode", "in": {"pixels": [8, 0], "vae": [1, 2]}},
        {"id": 13, "type": "KSampler",
         "w": {"seed": 12345, "steps": 32, "cfg": 6.5, "sampler_name": "euler_ancestral",
               "scheduler": "normal", "denoise": 0.85},
         "in": {"model": [3, 0], "positive": [11, 0], "negative": [11, 1], "latent_image": [12, 0]}},
        {"id": 14, "type": "VAEDecode", "in": {"samples": [13, 0], "vae": [1, 2]}},
        {"id": 15, "type": "ImageScaleBy", "w": {"upscale_method": "lanczos", "scale_by": 1.3}, "in": {"image": [14, 0]}},
        {"id": 16, "type": "VAEEncode", "in": {"pixels": [15, 0], "vae": [1, 2]}},
        {"id": 17, "type": "KSampler",
         "w": {"seed": 12345, "steps": 16, "cfg": 6.5, "sampler_name": "euler_ancestral",
               "scheduler": "normal", "denoise": 0.3},
         "in": {"model": [3, 0], "positive": [11, 0], "negative": [11, 1], "latent_image": [16, 0]}},
        {"id": 18, "type": "VAEDecode", "in": {"samples": [17, 0], "vae": [1, 2]}},
        {"id": 19, "type": "SaveImage", "w": {"filename_prefix": "comic/08_gta"}, "in": {"images": [18, 0]}},
    ],
    "app": {"inputs": [[7, "image", {"description": "исходное фото"}],
                       [5, "text", {"description": "сцена: город, пляж, пальмы, время суток"}],
                       [13, "denoise", {"description": "0.80–0.90: чем выше, тем сильнее стиль"}],
                       [2, "strength_model", {"description": "сила GTA-стиля"}],
                       [11, "strength", {"description": "жёсткость позы и композиции"}],
                       [3, "strength_model", {"description": "тело: Sexy_Detailer (0 = выкл)"}],
                       [13, "seed", {"description": "сид: randomize = каждый раз новый"}]]},
}

# ---------------------------------------------------------------- 04: pose
WORKFLOWS["04_pose_reference"] = {
    "title": "04 Поза и персонаж",
    "nodes": [
        {"id": 1, "type": "CheckpointLoaderSimple", "w": {"ckpt_name": SDXL}},
        {"id": 2, "type": "LoadImage", "w": {"image": "pose.png"}},
        {"id": 3, "type": "DWPreprocessor",
         "w": {"detect_hand": "enable", "detect_body": "enable", "detect_face": "enable",
               "resolution": 768, "bbox_detector": "yolox_l.onnx",
               "pose_estimator": "dw-ll_ucoco_384_bs5.torchscript.pt", "scale_stick_for_xinsr_cn": "disable"},
         "in": {"image": [2, 0]}},
        {"id": 4, "type": "LoadImage", "w": {"image": "character.png"}},
        {"id": 5, "type": "IPAdapterModelLoader", "w": {"ipadapter_file": "ip-adapter-plus_sdxl_vit-h.safetensors"}},
        {"id": 6, "type": "CLIPVisionLoader", "w": {"clip_name": "CLIP-ViT-H-14-laion2B-s32B-b79K.safetensors"}},
        {"id": 20, "type": "LoraLoaderModelOnly", "w": {"lora_name": STYLE_LORA, "strength_model": 0.9},
         "in": {"model": [1, 0]}},
        {"id": 7, "type": "IPAdapterAdvanced",
         "w": {"weight": 0.8, "weight_type": "linear", "combine_embeds": "concat",
               "start_at": 0.0, "end_at": 1.0, "embeds_scaling": "V only"},
         "in": {"model": [20, 0], "ipadapter": [5, 0], "image": [4, 0], "clip_vision": [6, 0]}},
        {"id": 30, "type": "CLIPSetLastLayer", "w": {"stop_at_clip_layer": -2}, "in": {"clip": [1, 1]}},
        {"id": 8, "type": "CLIPTextEncode", "w": {"text": TEST_PROMPT}, "in": {"clip": [30, 0]}},
        {"id": 9, "type": "CLIPTextEncode", "w": {"text": TEST_NEG}, "in": {"clip": [30, 0]}},
        {"id": 10, "type": "ControlNetLoader", "w": {"control_net_name": "controlnet-union-sdxl-promax.safetensors"}},
        {"id": 11, "type": "ControlNetApplyAdvanced",
         "w": {"strength": 0.8, "start_percent": 0.0, "end_percent": 1.0},
         "in": {"positive": [8, 0], "negative": [9, 0], "control_net": [10, 0], "image": [3, 0]}},
        {"id": 12, "type": "EmptyLatentImage", "w": {"width": 832, "height": 1216, "batch_size": 1}},
        {"id": 13, "type": "KSampler",
         "w": {"seed": 12345, "steps": 30, "cfg": 6.0, "sampler_name": "euler_ancestral",
               "scheduler": "normal", "denoise": 1.0},
         "in": {"model": [7, 0], "positive": [11, 0], "negative": [11, 1], "latent_image": [12, 0]}},
        {"id": 14, "type": "VAEDecode", "in": {"samples": [13, 0], "vae": [1, 2]}},
        {"id": 15, "type": "SaveImage", "w": {"filename_prefix": "comic/04_pose"}, "in": {"images": [14, 0]}},
    ],
    "app": {"inputs": [[2, "image", {"description": "референс позы (фото или рисунок)"}],
                       [4, "image", {"description": "референс персонажа: лицо и стиль"}],
                       [8, "text", {"description": "что нарисовать"}],
                       [11, "strength", {"description": "сила ControlNet: 0.7–0.9"}],
                       [7, "weight", {"description": "сила IP-Adapter: 0.7–0.9"}],
                       [13, "seed", {"description": "сид: randomize = каждый раз новый"}]]},
}

# ---------------------------------------------------------------- 05: style return
WORKFLOWS["05_style_return"] = {
    "title": "05 Возврат стиля",
    "nodes": [
        {"id": 1, "type": "CheckpointLoaderSimple", "w": {"ckpt_name": SDXL}},
        {"id": 2, "type": "LoraLoaderModelOnly", "w": {"lora_name": STYLE_LORA, "strength_model": 0.9},
         "in": {"model": [1, 0]}},
        {"id": 3, "type": "LoadImage", "w": {"image": "example.png"}},
        {"id": 30, "type": "CLIPSetLastLayer", "w": {"stop_at_clip_layer": -2}, "in": {"clip": [1, 1]}},
        {"id": 4, "type": "CLIPTextEncode", "w": {"text": TEST_PROMPT}, "in": {"clip": [30, 0]}},
        {"id": 5, "type": "CLIPTextEncode", "w": {"text": TEST_NEG}, "in": {"clip": [30, 0]}},
        {"id": 6, "type": "VAEEncode", "in": {"pixels": [3, 0], "vae": [1, 2]}},
        {"id": 7, "type": "KSampler",
         "w": {"seed": 12345, "steps": 20, "cfg": 6.0, "sampler_name": "euler_ancestral",
               "scheduler": "normal", "denoise": 0.35},
         "in": {"model": [2, 0], "positive": [4, 0], "negative": [5, 0], "latent_image": [6, 0]}},
        {"id": 8, "type": "VAEDecode", "in": {"samples": [7, 0], "vae": [1, 2]}},
        {"id": 9, "type": "SaveImage", "w": {"filename_prefix": "comic/05_style"}, "in": {"images": [8, 0]}},
    ],
    "app": {"inputs": [[3, "image", {"description": "кадр после Qwen-правки"}],
                       [4, "text", {"description": "описание сцены в стиле комикса"}],
                       [7, "denoise", {"description": "0.30–0.40: перекрасить, не перерисовать"}],
                       [2, "strength_model", {"description": "стиль: TheOfficialPit V2 (триггер thick lips)"}],
                       [7, "seed", {"description": "сид: randomize = каждый раз новый"}]]},
}

# ---------------------------------------------------------------- 06: noct qwen 2.1
WORKFLOWS["06_scene_noct"] = {
    "title": "06 Ночь — Noct Q Anime",
    "nodes": [
        {"id": 1, "type": "UNETLoader", "w": {"unet_name": NOCT, "weight_dtype": "default"}},
        {"id": 2, "type": "CLIPLoader", "w": {"clip_name": NOCT_TE, "type": "qwen_image", "device": "default"}},
        {"id": 3, "type": "VAELoader", "w": {"vae_name": NOCT_VAE}},
        {"id": 4, "type": "TextEncodeQwenImage21",
         "w": {"prompt": "An anime illustration of an adult woman in a leather jacket on a rainy city street at night, "
                         "soft rim light, detailed background, cinematic composition",
               "negative_prompt": "low quality, blurry, bad anatomy, extra fingers, watermark, text, child",
               "resolution": 1024},
         "in": {"clip": [2, 0], "vae": [3, 0]}},
        {"id": 5, "type": "ModelSamplingAuraFlow", "w": {"shift": 3.0, "sampling": "flow"}, "in": {"model": [1, 0]}},
        {"id": 6, "type": "KSampler",
         "w": {"seed": 12345, "steps": 8, "cfg": 1.0, "sampler_name": "res_multistep",
               "scheduler": "simple", "denoise": 1.0},
         "in": {"model": [5, 0], "positive": [4, 0], "negative": [4, 1], "latent_image": [4, 2]}},
        {"id": 7, "type": "VAEDecode", "in": {"samples": [6, 0], "vae": [3, 0]}},
        {"id": 8, "type": "SaveImage", "w": {"filename_prefix": "comic/06_noct"}, "in": {"images": [7, 0]}},
    ],
    "app": {"inputs": [[4, "prompt", {"description": "сцена; начинать с «An anime illustration of…»"}],
                       [4, "negative_prompt", {"description": "что исключить"}],
                       [4, "resolution", {"description": "1024 — нативное"}],
                       [5, "shift", {"description": "shift AuraFlow, рецепт автора 3.0"}],
                       [6, "seed", {"description": "сид: randomize = каждый раз новый"}],
                       [6, "steps", {"description": "8 шагов по рецепту автора"}]]},
}


# ---------------------------------------------------------------- варианты по стилю

# key: (файл, триггер, подпись)
STYLES = {
    "comic": ("style\\thepit_comic.safetensors", "ComicPitXX", "комикс ThePit"),
    "jp": ("style\\johnpersons_original.safetensors", "jpstyle", "оригинал John Persons"),
    "bimbo": ("style\\bimbofusion.safetensors", "bimbofusion artstyle", "бимбо Bimbofusion"),
}


def _swap_style(spec, style):
    lora, trig, label = STYLES[style]
    for n in spec["nodes"]:
        if n["type"] == "LoraLoaderModelOnly" and abs(n["w"].get("strength_model", 0) - 0.9) < 1e-9:
            n["w"]["lora_name"] = lora
        if n["type"] == "CLIPTextEncode" and "thick lips" in str(n["w"].get("text", "")):
            n["w"]["text"] = n["w"]["text"].replace("thick lips", trig)
    for inp in spec["app"]["inputs"]:
        if len(inp) > 2 and inp[2].get("description", "").startswith("стиль:"):
            inp[2]["description"] = "стиль: " + label + " (триггер " + trig + ")"
    spec["title"] = spec["title"].split(" · ")[0] + " · " + label
    return spec


def _add_hands(spec):
    """Слот для кистей: style -> hands -> body -> sampler."""
    nodes = spec["nodes"]
    if any(n["id"] == 20 for n in nodes):   # уже добавлен в базовой сборке
        return spec
    body = next(n for n in nodes if n["id"] == 3)
    body["in"]["model"] = [20, 0]
    nodes.append({
        "id": 20, "type": "LoraLoaderModelOnly",
        "w": {"lora_name": "detail\\hands_xl.safetensors", "strength_model": 0.0},
        "in": {"model": [2, 0]},
    })
    spec["app"]["inputs"].insert(
        7, [20, "strength_model", {"description": "кисти: hands_xl (0 = выкл)"}])
    return spec


_add_hands(WORKFLOWS["01_scene_thepit"])
_add_hands(WORKFLOWS["07_photo_thepit"])
for _style in ("comic", "jp"):
    WORKFLOWS["01_scene_" + _style] = _add_hands(
        _swap_style(json.loads(json.dumps(WORKFLOWS["01_scene_thepit"])), _style))
for _style in ("comic", "jp", "bimbo"):
    WORKFLOWS["07_photo_" + _style] = _add_hands(
        _swap_style(json.loads(json.dumps(WORKFLOWS["07_photo_thepit"])), _style))


def _tune_bimbo(spec):
    """Bimbofusion уводит палитру в холод: снижаем вес, добавляем цветовые якоря."""
    for n in spec["nodes"]:
        if n["type"] == "LoraLoaderModelOnly" and abs(n["w"].get("strength_model", 0) - 0.9) < 1e-9:
            n["w"]["strength_model"] = 0.6
        if n["type"] == "CLIPTextEncode":
            t = str(n["w"].get("text", ""))
            if t.startswith("photo, photorealistic"):
                n["w"]["text"] = t + (", white lenses, opaque lenses, blank glasses, blindfold, "
                                      "grey skin, pale skin, white hair, red eyes, monochrome, desaturated")
            elif t.strip():
                n["w"]["text"] = t + (", warm skin tone, natural skin color, blonde hair, "
                                      "dark sunglasses, dark lenses, eyes visible behind glasses")
        if n["id"] == 13 and n["type"] == "KSampler":
            n["w"]["denoise"] = 0.7
    for inp in spec["app"]["inputs"]:
        if len(inp) > 2 and inp[2].get("description", "").startswith("стиль:"):
            inp[2]["description"] += ", вес 0.6, тёплые якоря"
    return spec


WORKFLOWS["07_photo_bimbo"] = _tune_bimbo(WORKFLOWS["07_photo_bimbo"])


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


def style_meta(spec, api):
    """Стилевые настройки графа для CLI: шаблон промпта, негатив, стилевая LoRA.

    Без этого photo_prompt.py переписывал оба промпта целиком и терял зашитое
    в сборку: солнечный свет и ночной негатив у GTA, тёплую палитру у bimbo.
    Шаблон выводится из самого промпта графа, поэтому расхождению взяться неоткуда.
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

    loras = [(n["inputs"]["lora_name"], float(n["inputs"]["strength_model"]))
             for n in api.values()
             if n["class_type"] == "LoraLoaderModelOnly"
             and float(n["inputs"]["strength_model"]) != 0]
    unique = loras[0] if len(loras) == 1 else None
    return {
        "positive_template": template,
        "negative": texts[neg_i],
        "style_lora": unique[0] if unique else "",
        "style_weight": unique[1] if unique else None,
    }


def build(name, spec):
    nodes = spec["nodes"]
    order = topo(nodes)
    link_id, links, ui_nodes = 1, [], []
    out_links = {}
    node_in = {}

    for nid in order:
        n = next(x for x in nodes if x["id"] == nid)
        sockets, widgets, outs = DEFS[n["type"]]
        wired_order = [s for s in sockets if s in n.get("in", {})]
        for sock in wired_order:
            src = n["in"][sock]
            lid = link_id
            link_id += 1
            typ = DEFS[next(x for x in nodes if x["id"] == src[0])["type"]][2][src[1]]
            links.append([lid, src[0], src[1], nid, wired_order.index(sock), typ])
            out_links.setdefault((src[0], src[1]), []).append(lid)
            node_in.setdefault(nid, {})[sock] = lid

    for idx, nid in enumerate(order):
        n = next(x for x in nodes if x["id"] == nid)
        sockets, widgets, outs = DEFS[n["type"]]
        wired = n.get("in", {})
        pos = [80 + (idx // 4) * 420, 60 + (idx % 4) * 260]
        ins = []
        for sock in [s for s in sockets if s in wired]:
            typ = DEFS[next(x for x in nodes if x["id"] == wired[sock][0])["type"]][2][wired[sock][1]]
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
        n = next(x for x in nodes if x["id"] == nid)
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

    with open(OUT + "\\" + name + ".json", "w", encoding="utf-8") as f:
        json.dump(workflow, f, ensure_ascii=False, indent=2)
    payload = {"prompt": api}
    meta = style_meta(spec, api)
    if meta:
        payload["meta"] = meta
    with open(OUT + "\\" + name + ".api.json", "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    return api


def run(api, label):
    body = json.dumps({"prompt": api}).encode()
    req = urllib.request.Request(BASE + "/prompt", data=body, headers={"Content-Type": "application/json"})
    try:
        res = json.loads(urllib.request.urlopen(req, timeout=30).read())
    except Exception as e:
        detail = getattr(e, "read", lambda: b"")()
        print(label, "ОШИБКА:", e, detail.decode(errors="replace")[:1200])
        return False
    pid = res.get("prompt_id")
    print(label, "в очереди:", pid)
    t0 = time.time()
    while time.time() - t0 < 600:
        time.sleep(3)
        try:
            h = json.loads(urllib.request.urlopen(BASE + "/history/" + pid, timeout=10).read())
        except Exception:
            continue
        if pid in h:
            st = h[pid].get("status", {})
            ok = st.get("status_str") == "success"
            print(label, "готово" if ok else "провал", round(time.time() - t0, 1), "с")
            if not ok:
                print(json.dumps(st.get("messages", [])[-2:], ensure_ascii=False)[:1500])
            return ok
    print(label, "таймаут")
    return False


def pick_targets(target):
    """Сборки по префиксу. Опечатка — ошибка, а не тихий ноль сделанного."""
    names = [n for n in WORKFLOWS if n.startswith(target)]
    if not names:
        sys.exit(f"нет сборки, начинающейся с «{target}».\nЕсть: " + ", ".join(sorted(WORKFLOWS)))
    return names


if __name__ == "__main__":
    # Имя проверяем до сборки: опечатка не должна переписывать файлы и молча ничего не делать.
    targets = []
    if "--run" in sys.argv:
        i = sys.argv.index("--run")
        if i + 1 >= len(sys.argv):
            sys.exit("--run требует имя сборки, напр. --run 07_photo_thepit")
        targets = pick_targets(sys.argv[i + 1])

    apis = {}
    for name, spec in WORKFLOWS.items():
        apis[name] = build(name, spec)
        print("собран", name)

    if targets:
        failed = [n for n in targets if not run(apis[n], n)]
        if failed:
            print("не прогнались:", ", ".join(failed))
            sys.exit(1)
