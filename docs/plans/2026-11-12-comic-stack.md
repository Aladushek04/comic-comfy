# NSFW комикс-стек: план реализации

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Поднять в ComfyUI Desktop три слоя NSFW-генерации и правок (SDXL-лошадка, Qwen-Edit по инструкции, Noct Q Anime) с пятью рабочими воркфлоу.

**Architecture:** Модели лежат в общей папке Desktop на G:, ноды ставятся в venv установки, воркфлоу — отдельные JSON в `G:\AI\comic-comfy\workflows`. Каждый слой проверяется прогоном через HTTP API ComfyUI с проверкой выходного файла.

**Tech Stack:** ComfyUI 0.45.2 (Desktop 1.1.9), torch 2.12.1+cu130, ComfyUI-Manager, ComfyUI-GGUF, Impact Pack, IPAdapter_plus, controlnet_aux, segment_anything, Florence2, Differential Diffusion.

**Spec:** `G:\AI\comic-comfy\docs\2026-11-12-comic-stack-design.md` (v3)

## Global Constraints

- Модели только в `G:\Comfy-Desktop\ComfyUI-Shared\models`; C: не использовать.
- Установка ComfyUI: `G:\Comfy-Desktop\ComfyUI-Installs\ComfyUI\ComfyUI`.
- Python для pip: `G:\Comfy-Desktop\ComfyUI-Installs\ComfyUI\ComfyUI\.venv\Scripts\python.exe`.
- Только взрослые вымышленные персонажи; возрастные теги в негативе по умолчанию.
- Ничего не удалять из существующих папок моделей (SeedVR2 остаётся).
- Каждый файл проверяется по размеру: расхождение >1% — перекачать.

## Review Focus

- 16 ГБ VRAM: Q4 11.66 ГБ + GGUF-энкодер → оффлоад в RAM; если проход >120 с, перейти на Q3_K_M.
- Шов по краю маски на инпейнте SDXL (чекпойнт не inpaint-обучен) — проверять именно край, не центр.
- Qwen-Edit меняет лицо при сильной правке — проверять сходство лица, а не только «получилось».
- Стиль после Qwen-правки уезжает в полуреализм — проверять наличие возвратного прохода WAI.
- Civitai-загрузки требуют токен и падают на 401 без него — проверять HTTP-код, не размер.

---

### Task 1: Ноды

**Files:**
- Modify: `G:\Comfy-Desktop\ComfyUI-Installs\ComfyUI\ComfyUI\custom_nodes\` (клоны)

**Interfaces:**
- Produces: доступные классы нод `UnetLoaderGGUF`, `FaceDetailer`, `IPAdapterAdvanced`, `ControlNetLoader`+`Union`, `SAMLoader`, `Florence2Run`, `DifferentialDiffusion`.

- [ ] **Step 1: Поставить ноды через Manager** — в UI ComfyUI → Manager → Install Custom Nodes: `ComfyUI-GGUF`, `ComfyUI-Impact-Pack`, `ComfyUI_IPAdapter_plus`, `comfyui_controlnet_aux`, `comfyui_segment_anything`, `ComfyUI-Florence2`, `ComfyUI-Differential-Diffusion`.
- [ ] **Step 2: Проверить, что каждая папка есть**

Run: `Get-ChildItem "G:\Comfy-Desktop\ComfyUI-Installs\ComfyUI\ComfyUI\custom_nodes" -Directory | Select-Object -ExpandProperty Name`
Expected: семь имён из шага 1.

- [ ] **Step 3: Проверить импорт**

Run: `.\.venv\Scripts\python.exe -c "import importlib.util as u; print(u.find_spec('impact'))"`
Expected: не `None`.

- [ ] **Step 4: Перезапустить ComfyUI и проверить `/object_info`**

Run: `curl.exe -s http://127.0.0.1:8000/object_info | Select-String "UnetLoaderGGUF|FaceDetailer|IPAdapterAdvanced"`
Expected: все три имени в ответе.

### Task 2: Слой 1 — модели

**Files:**
- Create: `...\models\checkpoints\NoobAI-XL-v1.1.safetensors`, `...\models\controlnet\controlnet-union-sdxl-promax.safetensors`, `...\models\ipadapter\ip-adapter-plus_sdxl_vit-h.safetensors`, `...\models\clip_vision\CLIP-ViT-H-14-laion2B-s32B-b79K.safetensors`, `...\models\upscale_models\4x-UltraSharp.pth`

**Interfaces:**
- Produces: чекпойнт для воркфлоу 01–03; Union-ControlNet, покрывающий openpose/depth/canny одним файлом.

- [ ] **Step 1: Скачать HF-файлы**

```
curl.exe -L -o "G:\Comfy-Desktop\ComfyUI-Shared\models\checkpoints\NoobAI-XL-v1.1.safetensors" "https://huggingface.co/Laxhar/noobai-XL-1.1/resolve/main/NoobAI-XL-v1.1.safetensors"
curl.exe -L -o "...\controlnet\controlnet-union-sdxl-promax.safetensors" "https://huggingface.co/xinsir/controlnet-union-sdxl-1.0/resolve/main/diffusion_pytorch_model_promax.safetensors"
curl.exe -L -o "...\upscale_models\4x-UltraSharp.pth" "https://huggingface.co/lokCX/4x-Ultrasharp/resolve/main/4x-UltraSharp.pth"
```

- [ ] **Step 2: Проверить размеры**

Run: `Get-ChildItem ... | Select Name,Length`
Expected: NoobAI 7.11 ГБ ±1%, Union ~2.5 ГБ, UltraSharp ~67 МБ.

- [ ] **Step 3: Скачать Civitai-файлы (нужен токен)**

WAI v17 (`Civitai 827184`, версия v17.0), Sexy_Detailer (`1998306`), 2 стилевые LoRA. Ссылки — через `https://civitai.com/api/download/models/<versionId>?token=<TOKEN>`.

- [ ] **Step 4: Проверить, что чекпойнт виден ComfyUI**

Run: запрос `/object_info/CheckpointLoaderSimple`
Expected: `WAI-Nsfw-Illustrious-17.safetensors` и `NoobAI-XL-v1.1.safetensors` в списке.

### Task 3: Воркфлоу 01 — сцена с нуля

**Files:**
- Create: `G:\AI\comic-comfy\workflows\01_txt2img_scene.json`

**Interfaces:**
- Consumes: чекпойнт из Task 2.
- Produces: граф с узлами `CheckpointLoaderSimple` → `CLIPTextEncode` (позитив/негатив) → `EmptyLatentImage` (832×1216) → `KSampler` (euler, 30, cfg 6, clip skip 2) → `VAEDecode` → `SaveImage`; отдельные `PrimitiveString` для качества, NSFW-тегов и негатива.

- [ ] **Step 1: Собрать граф** по сигнатурам выше; `Sexy_Detailer` подключается через `LoraLoaderModelOnly` с весом 0.8 и обходом по умолчанию.
- [ ] **Step 2: Прогнать через API**

Run: `curl.exe -X POST http://127.0.0.1:8000/prompt -H "Content-Type: application/json" -d @payload.json`
Expected: `{"prompt_id": "..."}`, затем в `/history/<id>` статус `success`.

- [ ] **Step 3: Проверить выход**

Run: `Get-ChildItem "G:\Comfy-Desktop\ComfyUI-Shared\output" -Filter *.png | Sort LastWriteTime -Desc | Select -First 1`
Expected: файл 832×1216, время < 25 с.

### Task 4: Воркфлоу 02 — инпейнт по маске

**Files:**
- Create: `G:\AI\comic-comfy\workflows\02_inpaint_edit.json`

**Interfaces:**
- Consumes: чекпойнт Task 2, ноды Task 1.
- Produces: `LoadImage` → `GrowMask` → `BlurMask` → инпейнт → `ImageCompositeMasked` (вклейка с альфой).

- [ ] **Step 1:** Собрать граф с двумя пресетами: одежда (denoise 0.9) и макияж (denoise 0.4).
- [ ] **Step 2:** Прогнать на тестовом кадре из Task 3.
- [ ] **Step 3:** Проверить, что вне маски пиксели не изменились (сравнить хеш области через `ImageChops`/`compare` в PIL).
- [ ] **Step 4:** Проверить, что шва нет — визуально по краю маски.

### Task 5: Воркфлоу 03 — поза и персонаж

**Files:**
- Create: `G:\AI\comic-comfy\workflows\03_pose_and_character.json`

**Interfaces:**
- Consumes: Union-ControlNet, IP-Adapter из Task 2.
- Produces: `ControlNetApplyAdvanced` (openpose/depth) + `IPAdapterAdvanced` перед `KSampler`.

- [ ] **Step 1:** Собрать граф, препроцессор — `DWPreprocessor` из controlnet_aux.
- [ ] **Step 2:** Прогнать с референсом позы.
- [ ] **Step 3:** Проверить, что скелет совпадает: наложить препроцессорную карту на выход (визуально).

### Task 6: Слой 2 — Qwen-Edit по инструкции

**Files:**
- Create: `...\models\diffusion_models\qwen-image-edit-2511-uncensored-Q4_K_M.gguf`, `...\models\text_encoders\Qwen2.5-VL-7B-Instruct-q4_0.gguf`, `...\models\vae\qwen_image_vae.safetensors`, `...\models\loras\qwen-image-edit-plus-nsfw-lora.safetensors`, `G:\AI\comic-comfy\workflows\04_qwen_instruction_edit.json`

**Interfaces:**
- Produces: граф `UnetLoaderGGUF` → `CLIPLoaderGGUF` → `TextEncodeQwenImageEditPlus` → `KSampler` (cfg 4, 20 шагов) → `VAEDecode`; второй проход `LoadImage`+`VAEEncode`+`KSampler` (denoise 0.35) на WAI.

- [ ] **Step 1:** Скачать четыре файла (11.66 / 4.43 / 0.25 / 0.56 ГБ), проверить размеры.
- [ ] **Step 2:** Собрать граф правки + возвратный проход.
- [ ] **Step 3:** Прогнать «снять верхнюю одежду» на кадре из Task 3.
- [ ] **Step 4:** Проверить время прохода и что лицо не изменилось (сравнить кроп лица по SSIM > 0.85).

### Task 7: Слой 3 — Noct Q Anime

**Files:**
- Create: `...\models\diffusion_models\NoctQA_V1_int8_convrot.safetensors`, `...\models\text_encoders\qwen3vl_8b_int8_convrot.safetensors`, `...\models\vae\qwen_image_2.1_vae_bf16.safetensors`, `G:\AI\comic-comfy\workflows\05_noct_anime_scene.json`

**Interfaces:**
- Produces: граф из приложенного автором `NoctQ_Anime_V1_workflow.json` (25 шагов, euler, simple, cfg 3).

- [ ] **Step 1:** Скачать три файла, проверить размеры.
- [ ] **Step 2:** Импортировать авторский JSON, заменить пути на свои.
- [ ] **Step 3:** Прогнать сцену и записать время.

### Task 8: Сводная проверка и тайминги

**Files:**
- Modify: `G:\AI\comic-comfy\docs\2026-11-12-comic-stack-design.md` (раздел «Проверка»)

- [ ] **Step 1:** Прогнать пять сценариев из спеки.
- [ ] **Step 2:** Записать в спеку: время каждого шага, рабочие denoise, выбранные LoRA и веса.
- [ ] **Step 3:** Отметить, что дефолтом стало (WAI или Noct) по результату глазами.
