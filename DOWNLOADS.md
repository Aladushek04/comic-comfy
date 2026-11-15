# Что скачать

Две части: с Hugging Face (~49 ГБ) и с Civitai (~8.4 ГБ). Для всех 13 сборок нужно **~58 ГБ**.

## Куда качать

Пути не зашиты в код. Скрипты берут папку моделей в таком порядке:

1. `-Root <папка>` в командной строке;
2. переменная окружения `COMFY_MODELS`;
3. `base_path` из `extra_model_paths.yaml` — этот файл всё равно правят при установке,
   чтобы ComfyUI видел общие модели.

Дальше `photo_prompt.py` считает `input` и `output` соседями `models` (`ComfyUI-Shared\input`,
`ComfyUI-Shared\output`); если у тебя иначе — задай `COMFY_INPUT` и `COMFY_OUTPUT`.
Адрес ComfyUI — `COMFY_URL` (по умолчанию `http://127.0.0.1:8199`).

Раскладка, которую ждут графы:

```
<модели>\
├── checkpoints       WAI (обязателен), NoobAI
├── loras             thepit2, Sexy_Detailer, P477Y, GTAV_IL_MIX, Qwen-Lightning, MCNL
│   ├── detail        hands_xl
│   └── style         thepit_comic, johnpersons_original, bimbofusion
├── controlnet        Union SDXL promax
├── ipadapter         ip-adapter-plus
├── clip_vision       CLIP-ViT-H
├── upscale_models    4x-UltraSharp
├── diffusion_models  Qwen-Edit Q4, NoctQA
├── text_encoders     qwen_2.5_vl fp8, qwen3vl_8b int8
└── vae               qwen_image_vae, qwen_image_2.1_vae
```

---

## Часть 1. Hugging Face — автоматически

```powershell
powershell -ExecutionPolicy Bypass -File tools\download-hf.ps1
```

Скрипт создаёт папки, проверяет размер каждого файла у сервера и качает только недостающее.
Каждый файл пишется в `<имя>.part` и переименовывается **только после сверки размера**,
поэтому оборванная загрузка не выглядит готовой. Повторный запуск продолжает с места обрыва.

Проверить ссылки, ничего не скачивая (полезно перед долгой загрузкой):

```powershell
powershell -ExecutionPolicy Bypass -File tools\download-hf.ps1 -Verify
```

| Файл | Папка | Размер |
|---|---|---|
| `NoobAI-XL-v1.1.safetensors` | checkpoints | 6.62 |
| `controlnet-union-sdxl-promax.safetensors` | controlnet | 2.34 |
| `ip-adapter-plus_sdxl_vit-h.safetensors` | ipadapter | 0.79 |
| `CLIP-ViT-H-14-laion2B-s32B-b79K.safetensors` | clip_vision | 2.35 |
| `4x-UltraSharp.pth` | upscale_models | 0.06 |
| `qwen-image-edit-2511-uncensored-Q4_K_M.gguf` | diffusion_models | 10.86 |
| `qwen_2.5_vl_7b_fp8_scaled.safetensors` | text_encoders | 8.74 |
| `qwen_image_vae.safetensors` | vae | 0.24 |
| `qwen-image-edit-plus-nsfw-lora.safetensors` | loras | 0.55 |
| `Qwen-Image-Edit-2511-Lightning-4steps-V1.0-bf16.safetensors` | loras | 0.79 |
| `NoctQA_V1_int8_convrot.safetensors` | diffusion_models | 6.76 |
| `qwen3vl_8b_int8_convrot.safetensors` | text_encoders | 8.71 |
| `qwen_image_2.1_vae_bf16.safetensors` | vae | 0.63 |

Размеры в ГБ, как их отдаёт сервер (`-Verify` печатает то же самое).

**Не в списке, потому что качается само:** файлы узлов `comfyui_controlnet_aux` (~1.4 ГБ) —
`depth_anything_v2_vitl.pth` (~1.25 ГБ) и `dw-ll_ucoco_384_bs5.torchscript.pt` (~0.13 ГБ).
Узел тянет их при первом прогоне сборок 04 (поза) и 07/08 (глубина) в
`custom_nodes\comfyui_controlnet_aux\ckpts\`. Нужен офлайн — положи туда заранее.

Готовые воркфлоу авторов скрипт кладёт в `workflows\reference\` (эта папка не в git).

---

## Часть 2. Civitai — скриптом

Нужен токен: `%USERPROFILE%\.civitai\token` или переменная `CIVITAI_TOKEN`.
Все девять файлов нужны сборкам, ссылки и ID проверены через API Civitai.

```powershell
powershell -ExecutionPolicy Bypass -File tools\download-civitai.ps1
powershell -ExecutionPolicy Bypass -File tools\download-civitai.ps1 -Verify   # только проверить ID
```

Каждый файл качается через `tools\civitai-dl.ps1`: `.part`, докачка, сверка размера по API,
переименование. Если Civitai отвечает «Early Access» — помоги браузеру: войди и скачай вручную
по ссылке из таблицы.

| Файл | Куда | Размер | versionId / fileId | Страница |
|---|---|---|---|---|
| `WAI-Nsfw-Illustrious-17.safetensors` | checkpoints | 6.46 | 2883731 / 2763986 | https://civitai.com/models/827184?modelVersionId=2883731 |
| `thepit2_1768210.safetensors` | loras | 0.43 | 1868373 / 1768210 | https://civitai.com/models/951576?modelVersionId=1868373 |
| `Sexy_Detailer.safetensors` | loras | 0.21 | 2261840 / 2154148 | https://civitai.com/models/1998306?modelVersionId=2261840 |
| `P477Y.safetensors` | loras | 0.21 | 2305750 / 2197721 | https://civitai.com/models/2037236?modelVersionId=2305750 |
| `hands_xl.safetensors` | loras\detail | 0.43 | 2212079 / 2105147 | https://civitai.com/models/200255?modelVersionId=2212079 |
| `thepit_comic.safetensors` | loras\style | 0.21 | 1776335 / 1677196 | https://civitai.com/models/1569742?modelVersionId=1776335 |
| `johnpersons_original.safetensors` | loras\style | 0.21 | 1829222 / 1730159 | https://civitai.com/models/1616311?modelVersionId=1829222 |
| `bimbofusion.safetensors` | loras\style | 0.21 | 1442607 / 1345170 | https://civitai.com/models/1273315?modelVersionId=1442607 |
| `GTAV_IL_MIX.safetensors` | loras | 0.05 | 2140775 / 2034567 | https://civitai.com/models/1891318?modelVersionId=2140775 |

**Важно про имена.** На Civitai файлы называются иначе, чем в графах; скрипт сохраняет их под
именами из таблицы (`-OutName`), иначе сборки не найдут LoRA:
`the pit comic Illustrious` → `thepit_comic`, `JohnPersons/ThePit - Original style` →
`johnpersons_original`, `Bimbofusion Artstyle` → `bimbofusion`.

---

## Часть 3. По желанию

Эти файлы сборки не используют, они лежат в каталоге `docs\models.md` для сравнений:

| Файл | Куда | Размер | Страница |
|---|---|---|---|
| `NovaComicILV2.safetensors` | checkpoints | 6.65 | https://civitai.com/models/1969383?modelVersionId=2431039 |
| `digitalInkedComic_v1.safetensors` | loras | 0.21 | https://civitai.com/models/3040352?modelVersionId=3455706 |
| `Clusterfuck_Illustrious-000035.safetensors` | loras | 0.21 | https://civitai.com/models/154425?modelVersionId=1309345 |

`Nova Comic XL` помечен как SFW-ориентированный: нужен только для «приличных» кадров.
Для сравнений стилей есть ещё `style_fernando`, `style_cherrygig`, `thepitstyle_v1` — см. `docs\models.md`.

---

## Когда готово

```powershell
python tools\check_install.py
```

Скрипт сверяет 22 модели из всех 13 сборок с папкой моделей и с этим файлом: покажет, чего
нет на диске и что не описано. Код возврата 1, если чего-то не хватает. Чужую машину можно
проверить так: `python tools\check_install.py --models D:\models`.

Дальше: `python tools\build_workflows.py` — соберёт графы (в них попадут пути и `.api.json`).
Если какой-то файл не встал — напиши, какой, дам зеркало.
