# NSFW комикс-стек в ComfyUI Desktop (дизайн, v3)

Дата: 2026-11-12. Статус: на утверждение. v3 уточняет v2: Qwen возвращается в стек в нецензурированном варианте.

## Зачем

Локальный конвейер для иллюстраций в стиле комиксов с откровенным контентом (18+) и правкой готового кадра:
снять/сменить одежду, белье, макияж, позу, оставить того же персонажа в новой сцене. Всё на своей машине.

## Что изменилось после проверки Qwen

В v2 Qwen был исключён — но это касалось только официальной 20B-модели, обученной на отфильтрованных данных.
Фактически есть три рабочих пути:

- `Qwen-Image-2.1-Turbo` (7B, 8 шагов) — база сама без встроенного фильтра, uncensored из коробки.
- `Noctaluna/Noct-Q-Anime-Uncensored-Qwen-Image-2.1` — мерж под аниме: взрослые персонажи, белье, обнажённость, явные сцены.
- `Qwen-Image-Edit-2511` + NSFW-LoRA (`MCNL v1`, `SexGod 2511`) или готовая GGUF-сборка `ChrisColeTech/qwen-image-edit-uncensored-GGUF`.

Значит, гибрид из первого выбора остаётся, но редактор берём нецензурированный.

## Что уже есть

- ComfyUI Desktop 1.1.9, движок v0.45.2 (Qwen 2.1 требует 0.37+), torch 2.12.1+cu130, CUDA работает.
- `--enable-manager`, сторонних нод нет.
- Модели: `G:\Comfy-Desktop\ComfyUI-Shared\models` (пусто, кроме SeedVR2).
- Свободно на G: 265 ГБ.

## Цели

1. Генерация откровенных иллюстраций 1024×1536 на RTX 4060 Ti 16 ГБ, 10–25 с на кадр.
2. Правка по маске: снять/добавить одежду, сменить белье, макияж — без потери стиля.
3. Правка по инструкции: «убери одежду», «замени на белье», «другой ракурс» — без ручной маски.
4. Управление позой (ControlNet) и постоянство персонажа (IP-Adapter / character LoRA).
5. Готовые воркфлоу, которые открываются и работают.

## Не цели

- Страницы комикса с панелями и бабблами.
- Обучение своих LoRA.
- Видео.
- Реальные люди в кадре — исключено.

## Рамки

- Только взрослые вымышленные персонажи. Возрастные теги обязательны, детские/школьные — в негатив.
- Локальное законодательство — на пользователе. Материал не покидает диск.
- GPU 16 ГБ Ada (fp8 ок, fp4 нет), RAM 32 ГБ, всё на G:.

## Слои

**Слой 1 — рабочая лошадка, стиль заперт (SDXL/Illustrious).**
WAI-Nsfw-Illustrious v17 + комикс-стилевые LoRA. Генерация 10–15 с, инпейнт тем же чекпойнтом,
ControlNet, IP-Adapter. Вся NSFW-LoRA-экосистема здесь. Лицензия коммерчески пригодна.

**Слой 2 — смысловые правки (Qwen-Image-Edit 2511 uncensored).**
GGUF Q4 + NSFW-LoRA. Даёт то, чего не умеет инпейнт: инструкция вместо маски, работа по референсу.
Стиль возвращаем проходом на WAI (img2img, denoise 0.30–0.40).

**Слой 3 — второй вкус генерации (Noct Q Anime / Qwen 2.1).**
Чистый аниме-рендер, мягкий свет, длинные промпты полными предложениями, без датабору-тегов.
Ставится по желанию; лицензия — research, только некоммерческое использование.

## Файлы

Всё в `G:\Comfy-Desktop\ComfyUI-Shared\models`.

### Слой 1

| Файл | Куда | Размер | Источник |
|---|---|---|---|
| `WAI-Nsfw-Illustrious-17.safetensors` | checkpoints | 6.80 ГБ | Civitai 827184 (v17.0, nsfw: true) |
| `NoobAI-XL-v1.1.safetensors` | checkpoints | 7.11 ГБ | HF `Laxhar/noobai-XL-1.1` |
| `NovaComicILV2.safetensors` | checkpoints | 6.65 ГБ | Civitai 1969383 (SFW-ориентирован) |
| `Sexy_Detailer.safetensors` | loras | 0.21 ГБ | Civitai 1998306 |
| comic-ink / halftone LoRA (2 шт.) | loras | ~0.4 ГБ | Civitai, подбор на тесте |
| ControlNet SDXL openpose / depth / canny | controlnet | ~2.5–4 ГБ | `xinsir` / `lllyasviel` |
| IP-Adapter SDXL + CLIP vision | ipadapter / clip_vision | ~1.5 ГБ | `h94/IP-Adapter` |
| `4x-UltraSharp.pth` | upscale_models | 0.07 ГБ | OpenModelDB |

### Слой 2

| Файл | Куда | Размер | Источник |
|---|---|---|---|
| `qwen-image-edit-2511-uncensored-Q4_K_M.gguf` | diffusion_models | 11.66 ГБ | HF `ChrisColeTech/qwen-image-edit-uncensored-GGUF` |
| `Qwen2.5-VL-7B-Instruct-q4_0.gguf` | text_encoders | 4.43 ГБ | там же |
| `qwen_image_vae.safetensors` | vae | 0.25 ГБ | там же |
| `qwen-image-edit-plus-nsfw-lora.safetensors` (MCNL v1) | loras | 0.56 ГБ | HF `ScottzillaSystems/...` |
| опционально `SEXGOD_FemaleNudity_QwenEdit_2511_v2.safetensors` | loras | 2.36 ГБ | HF `Baraje/...` |

### Слой 3 (по желанию)

| Файл | Куда | Размер | Источник |
|---|---|---|---|
| `NoctQA_V1_int8_convrot.safetensors` | diffusion_models | 7.26 ГБ | HF `Noctaluna/Noct-Q-Anime-Uncensored-Qwen-Image-2.1` |
| `qwen3vl_8b_int8_convrot.safetensors` | text_encoders | ~8 ГБ | HF `Comfy-Org/Qwen-Image-2.1` |
| `qwen_image_2.1_vae_bf16.safetensors` | vae | ~0.25 ГБ | там же |
| `NoctQ_Anime_V1_workflow.json` | workflows | — | там же |

Итого: слой 1 ≈ 15 ГБ, +слой 2 ≈ 32 ГБ, +слой 3 ≈ 48 ГБ.

## Ноды (Manager)

- `ComfyUI-GGUF` — Qwen в Q4.
- `ComfyUI-Impact-Pack` — Detailer, SAM, апскейл.
- `comfyui_segment_anything` + `ComfyUI-Florence2` — авто-маски по тексту.
- `comfyui_controlnet_aux` — препроцессоры позы/глубины/линий.
- `ComfyUI_IPAdapter_plus` — референс персонажа.
- `ComfyUI-Differential-Diffusion` — мягкие края маски.

## Воркфлоу

`G:\AI\comic-comfy\workflows`.

**01_txt2img_scene.json** — сцена с нуля на WAI v17.
832×1216 / 1024×1536, Euler a, 28–34 шага, CFG 5.5–6.5. Промпт: качество → персонаж → действие → одежда/её отсутствие → свет, фон.
`Sexy_Detailer` и NSFW-теги — переключателями. Негатив с возрастными ограничителями — отдельная нода, не редактируется по ходу.

**02_inpaint_edit.json** — по маске.
Маска кистью или Florence-2 («shirt», «bra», «panties», «face») → GrowMask + BlurMask → перерисовка тем же чекпойнтом → вклейка с мягкой альфой.
Пресеты: одежда (0.85–0.95), макияж (0.35–0.45). Поза не меняется.

**03_pose_and_character.json** — поза и постоянство.
ControlNet openpose (+depth) с референса, IP-Adapter для лица/стиля, дальше обычная генерация.

**04_qwen_instruction_edit.json** — правка инструкцией.
Qwen-Edit 2511 uncensored Q4 + MCNL NSFW-LoRA (вес 0.6–0.9), CFG 4, 20 шагов (или Lightning 4 шага).
Промпт императивный, с «keep face, hair and background unchanged». Второй проход: WAI img2img 0.30–0.40 для возврата комикс-стиля.

**05_noct_anime_scene.json** — слой 3, по желанию.
Готовый JSON от автора модели: 25 шагов, Euler, simple, CFG 3.

## Проверка

1. Сцена с нуля на WAI и на Noct Q Anime — сравнить стиль и время.
2. Раздеть по маске: верхняя одежда → обнажённый торс. Лицо не изменилось, шва нет.
3. То же инструкцией через Qwen-Edit: сравнить с инпейнтом по качеству и скорости.
4. Макияж: помада + smoky eyes при неизменном лице.
5. Поза: тот же персонаж в позе с референса.

Критерии: лицо узнаваемо, штрих и палитра как в исходнике, руки без грубых ошибок, нет «пластилина» у маски.
Тайминги пишем в этот файл.

## Риски

- 16 ГБ: Q4 11.66 ГБ + GGUF-энкодер 4.43 → часть уходит в RAM, проход 40–90 с. Если тесно — Q3_K_M.
- Qwen-Edit любит менять лицо при сильных правках: держим `keep face` в промпте и низкий denoise на возврате стиля.
- Qwen 2.1 и мержи — некоммерческая лицензия; для продаж только слой 1.
- NSFW-LoRA к 2511 — комьюнити-адаптеры, качество на разных типажах неровное: тестируем два (MCNL и SexGod), оставляем один.
- Civitai-файлы качаются вручную (нужен аккаунт).
- Анатомия на сложных ракурсах — норма для SDXL, чинится detailer-проходом.

## Что осталось строителю

- Скачать чекпойнты и LoRA с Civitai (ссылки дам), остальное — с HF.
- Подобрать 2 стилевые LoRA и зафиксировать веса.
- Собрать пять графов, снять тайминги, записать пресеты denoise.

---

## Результаты прогона (2026-11-13)

Собрано и проверено на RTX 4060 Ti 16 GB, ComfyUI 0.45.2, torch 2.12.1+cu130.

| Воркфлоу | Что делает | Время | Выход |
|---|---|---|---|
| `01_txt2img_scene` | сцена с нуля, 30 шагов + hires ×1.5 | 39 с (тёплый), 63 с (холодный) | 1248×1824 |
| `02_inpaint_edit` | правка по маске, denoise 0.85 | 54 с | размер исходника |
| `03_qwen_instruction_edit` | Qwen-Edit 2511, 20 шагов | 375–397 с | 840×1232 |
| `03b_qwen_fast` | то же + Lightning LoRA, 4 шага, CFG 1 | 102 с | 840×1232 |
| `04_pose_and_character` | ControlNet Union + IP-Adapter | 84 с | 832×1216 |
| `05_style_return` | возврат стиля img2img, denoise 0.35 | 24 с | размер исходника |
| `06_noct_anime_scene` | Noct Q Anime, 25 шагов, CFG 3 | 81 с | 1024×1024 |

Решения, принятые по факту:

- **Qwen-Edit на 16 ГБ в 20 шагов непригоден** (6.5 минуты). Рабочий режим — `03b` с Lightning-LoRA:
  4 шага, CFG 1, 102 с. Докачан `Qwen-Image-Edit-2511-Lightning-4steps-V1.0-bf16.safetensors` (0.85 ГБ).
- **Инпейнт проверен на пикселях**: внутри маски кадр меняется (средняя разница 9.4), вне маски —
  ровно 0.0. Маска берётся из отдельного чёрно-белого файла (`mask.png`, белое = правим), а не из альфы.
- **FaceDetailer не подключён**: у Impact Pack нет `bbox_detector` (не хватает ultralytics-модели).
  Лица и макияж пока делаются через `02_inpaint_edit`, этого достаточно.
- **Differential Diffusion и SAM3 — в ядре 0.45.2**, отдельные паки не нужны.
- Воркфлоу лежат и в `G:\AI\comic-comfy\workflows`, и в панели ComfyUI
  (`ComfyUI\user\default\workflows`) — открываются из левого сайдбара.
- Тестовые входы: `example.png`, `mask.png`, `pose.png`, `character.png` в общей папке `input`.

