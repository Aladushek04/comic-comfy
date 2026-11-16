# Рынок CV-моделей для конвейера: что есть, чего нет, что брать

Дата: 2026-11-16. Источники чисел: HF API (загрузки, лайки, параметры, размеры файлов,
лицензии) на 16.11.2026 по шести тегам `image-segmentation`, `image-to-text`,
`unconditional-image-generation`, `zero-shot-image-classification`,
`image-feature-extraction`, `image-classification`; локальная ревизия диска
`E:\ComfyUI-Shared\models` и `G:\Comfy-Desktop\ComfyUI-Installs\ComfyUI\ComfyUI`;
замеры теггера — `out\bench\tagger_compare.md`. Качественные факты (лицензии, VRAM,
бенчмарки) — из карточек моделей и обзоров, ссылки в конце.

## 1. Что у нас есть сейчас

Проверено на диске, не по памяти. `python tools\check_install.py` — 22 модели, код 0.

| Что | Файл | Размер | Где | Роль | Состояние |
|---|---|---|---|---|---|
| Теггер, основной голос | `wd-swinv2-tagger-v3` (ONNX) | 446 МБ | `out\models\tagger\swinv2` | теги в `subject`, CPU | ок, 0.5 с/фото |
| Теггер, размер груди | `wd-eva02-large-tagger-v3` (ONNX) | 1.2 ГБ | `out\models\tagger\eva02-large` | уточнение градации | ок, 2.0 с/фото |
| Кодировщик картинки | `CLIP-ViT-H-14-laion2B-s32B-b79K` | 2.4 ГБ | `models\clip_vision` | IP-Adapter, сборка 04 | ок |
| ControlNet | `controlnet-union-sdxl-promax` + `controlnet-canny-sdxl` | 2.4 + 2.3 ГБ | `models\controlnet` | поза, глубина, линии | ок |
| Апскейл | `4x-UltraSharp` | 64 МБ | `models\upscale_models` | финал | ок |
| SAM 1 | `sam_vit_b_01ec64.pth` | 358 МБ | `ComfyUI\models\sams` | пришёл с Impact Pack, нигде не зовётся | лежит без дела |
| Глубина | `depth_anything_v2_vitl.pth` | авто | качает `controlnet_aux` | вход ControlNet | ок |
| Поза | `dw-ll_ucoco_384_bs5` (DWPose) | авто | качает `controlnet_aux` | вход ControlNet | ок |
| VLM-кодировщики | `qwen_2.5_vl_7b_fp8` (9.4 ГБ), `qwen3vl_8b_int8` (9.4 ГБ) | 18.8 ГБ | `models\text_encoders` | Qwen-Edit и Noct | ок |
| VLM-описания | `Huihui-Qwen3.6-35B-A3B` Q4 | вне репозитория | LM Studio / llama.cpp, 8765 | описание фото | ок |

Пустые каталоги, которые ComfyUI создал под CV: `sams` (кроме vit_b), `detection`,
`background_removal`, `classifiers`, `onnx`, `geometry_estimation`, `clip`.
Мусор: 135 МБ недокачанного файла в `out\models\tagger\swinv2\.cache\huggingface\download`
(остался от замера `vit-large`).

**Чего нет вообще:** ни одной модели сегментации по тексту, ни одного маттера фона,
ни одного парсера человека, ни одного классификатора/скорера кадров, ни OCR для
страниц комикса. Маска в `02_edit_mask` — это нарисованный руками `mask.png`.

## 2. Рынок по шести тегам: что тег реально продаёт

| Тег HF | Лидер по загрузкам | Наша задача из этого тега | Есть? |
|---|---|---|---|
| `image-segmentation` | `BiRefNet` 999 тыс. | маски под инпейнт, вырезание персонажа | нет |
| `image-to-text` | `GLM-OCR` 1.8 млн, `blip-base` 1.29 млн | описание кадра (у нас внешний VLM), OCR реплик | описание есть, OCR нет |
| `unconditional-image-generation` | `ddpm-cifar10-32` 30 тыс. | — | не нужен |
| `zero-shot-image-classification` | `clip-vit-base-patch32` 17.7 млн | отбор кадров по описанию, NSFW-фильтр | нет |
| `image-feature-extraction` | `dinov2-base` 3.5 млн | похожесть кадров, консистентность персонажа | нет |
| `image-classification` | `vit-base-patch16-224` 15.3 млн | теггеры, NSFW, эстетика | WD14 (2 модели) |

Два вывода, которые важны до всякой таблицы:

1. **`unconditional-image-generation` — мёртвый тег.** Там DDPM с CIFAR-10 и repack
   видео-апскейлера. Наши чекпойнты живут в `text-to-image`; смотреть в этот тег нечего.
2. **`image-to-text` — это на 90% OCR.** Настоящее описание кадра лежит в
   `image-text-to-text` (Qwen-VL, JoyCaption, Florence-2). Зато OCR в этом теге —
   ровно то, чего не хватает воркфлоу страниц с репликами.

## 3. Шортлист по задачам

### 3.1 Маски: сегментация и маттинг

| Модель | Что даёт | Параметры / файл | Лицензия | Загрузки | Наш вердикт |
|---|---|---|---|---|---|
| `facebook/sam3` | маска по фразе («the dress», «hands»), точки, боксы, трекинг | 848M, 3.45 ГБ | Meta SAM License | 1.97 млн | база, но репозиторий gated |
| **`Comfy-Org/sam3.1`** | то же, мультиобъектный трекинг, fp16 | 1.75 ГБ | Meta SAM License | 424 тыс. | **брать**: нативные узлы уже в нашей сборке |
| `facebook/sam2.1-hiera-large` | маска по точке/боксу, без текста | 224M, 0.9 ГБ | Apache-2.0 | 261 тыс. | запас, если SAM3 не влезет |
| **`ZhengPeng7/BiRefNet`** | альфа-матт, вырезание персонажа | 221M, 0.44 ГБ | MIT | 999 тыс. | **брать**: лучший и лицензионно чистый |
| `briaai/RMBG-2.0` | то же, данные BRIA | 221M, 0.88 ГБ | CC BY-NC 4.0 | 394 тыс. | не брать: NC, а rembg тянет именно её |
| `briaai/VRMBG-3.0` | видео-маттинг | 220M, 0.89 ГБ | gated, NC | 132 | мимо: это видео |
| **`mattmdjaga/segformer_b2_clothes`** | 18 классов одежды (ATR) | 27M, ~0.11 ГБ | MIT | 371 тыс. | **брать**: дешёвая маска «куртка/платье/ноги» на CPU |
| `fashn-ai/fashn-human-parser` | 18 классов, есть `hands` и `torso` | SegFormer-B4, 0.26 ГБ | NVIDIA SegFormer | 76 тыс. | брать вторым: кисти и торс отдельными классами |
| `jonathandinu/face-parsing` | 19 классов лица (глаза, губы, брови) | 85M, 0.34 ГБ | MIT | 151 тыс. | по желанию: правки лица |
| `IDEA-Research/grounding-dino-base` | текст → боксы | 233M, 0.94 ГБ | Apache-2.0 | 1.47 млн | только как лицензионно-чистый запас |

Цифры по качеству: BiRefNet general 92.8 мс / 3.5 ГиБ на 4090 при 1024 (hr — 298 мс /
10.5 ГиБ); на 4060 Ti ждать примерно вдвое дольше. Средний IoU `segformer_b2_clothes`
0.69, но `belt` 0.30 и `scarf` 0.29 — эти классы нельзя пускать в маску.

### 3.2 Теги и описания

| Модель | Что даёт | Размер | Лицензия | Загрузки | Наш вердикт |
|---|---|---|---|---|---|
| `wd-swinv2-tagger-v3` (наш) | 10 861 тег, быстрый | 98M, 446 МБ ONNX | Apache-2.0 | 561 тыс. | оставить основным голосом |
| `wd-eva02-large-tagger-v3` (наш) | 8 106 тегов, точнее градация | 315M, 1.26 ГБ | Apache-2.0 | 45 тыс. | оставить для груди |
| **`pixai-labs/pixai-tagger-v1.0`** | 30 877 тегов, срез май 2026, головы rating/style | 486M, 1.95 ГБ | Apache-2.0 | 36 тыс. | **кандидат на замену WD14**, но на CPU дорого |
| `lodestones/taggerine` | 74 625 тегов (e621+danbooru), DINOv3 ViT-H | 1.1B, 5.27 ГБ | Apache-2.0 | 0 (62 лайка) | не брать: нет ONNX, нет F1, GPU-only |
| **`fancyfeast/llama-joycaption-beta-one`** | описание кадра, обучен не отказывать | 8B, GGUF Q4 4.9 ГБ | Llama 3.1 Community | 66 тыс. | **брать как локальный запас** к 35B |
| `microsoft/Florence-2-large-ft` | описание+боксы, без явной лексики | 0.77B, 1.54 ГБ | MIT | 28 тыс. | мимо: анатомию не опишет |
| `Qwen/Qwen3-VL-8B-Instruct` | сильный VLM | 8.8B | Apache-2.0 | 6.9 млн | у нас уже есть int8-кодировщик |
| `kha-white/manga-ocr-base` | OCR японского | 0.11B | Apache-2.0 | 299 тыс. | для страниц с репликами |
| `zai-org/GLM-OCR` | OCR, лидер тега | — | — | 1.8 млн | альтернатива manga-ocr |

По независимому замеру: `wd-eva02` mAP 0.4822, `wd-swinv2` 0.4603 — то есть запас
у новых теггеров есть, но он не в разы. PixAI v1.0 в своём же бенчмарке — самый
медленный из восьми (48.3 img/s на H100 при batch 16), на нашем CPU это десятки
секунд на фото. Отсюда правило: **PixAI — только на GPU и только если он реально
лучше на наших кадрах**, иначе выигрыш в тегах не стоит потери 2.6 с → 30 с.

### 3.3 Классификация, эстетика, признаки

| Модель | Что даёт | Размер | Лицензия | Загрузки | Наш вердикт |
|---|---|---|---|---|---|
| `Freepik/nsfw_image_detector` | 4 уровня, лучший на AI-картинках (100/96/100%) | 86M, 0.17 ГБ | MIT | 665 тыс. | брать, если нужен фильтр кадров |
| `Falconsai/nsfw_image_detection` | популярный, но на генерации падает до 84/69/36% | 86M | Apache-2.0 | 1.58 млн | не брать: сам Freepik так советует |
| `AdamCodd/vit-base-nsfw-detector` | на генерации 0.86 acc, считает ложбину NSFW | 86M | Apache-2.0 | 681 тыс. | не брать: перекос на «sexy» |
| `shunk031/aesthetics-predictor-v2` | оценка эстетики AVA (SRCC 0.721) | CLIP-L + голова, ~1.7 ГБ | MIT/CLIP | 19 тыс. | брать для отбора лучшего из `--variants` |
| `q-future/Q-ReAlign-Mini` (2026) | оценка качества, 0.8B | 0.8B | не проверено | — | следующий шаг, если LAION мало |
| `facebook/dinov3-vitl16` | признаки для похожести и консистентности | 303M, 1.21 ГБ | gated, custom | 693 тыс., 2305 лайков | по желанию: сравнение кадров с референсом |
| `facebook/dinov2-base` | то же, старее, открытее | 86M | Apache-2.0 | 3.54 млн | дешёвая замена DINOv3 |

## 4. Что брать: приоритеты

| # | Файл | Куда | ГБ | Зачем | Лицензия |
|---|---|---|---|---|---|
| 1 | `sam3.1_multiplex_fp16.safetensors` | `models\checkpoints` | 1.75 | маска по фразе вместо рисования руками | Meta SAM |
| 2 | `BiRefNet` (general) | `models\background_removal` | 0.44 | вырезание персонажа для панелей | MIT |
| 3 | `segformer_b2_clothes` | `models\segmentation` | 0.11 | маска одежды/ног/лица на CPU | MIT |
| 4 | `fashn-human-parser` | `models\segmentation` | 0.26 | кисти и торс отдельными классами | NVIDIA SegFormer |
| 5 | `pixai-tagger-v1.0` | `out\models\tagger` | 1.95 | теги 2026 года вместо 2024 | Apache-2.0 |
| 6 | `joycaption-beta-one` GGUF Q4 + mmproj | LM Studio | 4.92 | локальное описание без 35B | Llama 3.1 |
| 7 | `aesthetics-predictor-v2` | `out\models\scorer` | 1.7 | выбор лучшего из вариантов | MIT |
| 8 | `manga-ocr` | `out\models\ocr` | 0.4 | чтение реплик на страницах | Apache-2.0 |

Итого приоритеты 1–4: **2.6 ГБ** и закрывают главную дыру (маски).
1–8: **11.5 ГБ**, на `E:` свободно 271 ГБ.

Отдельно, без скачивания: в нашей сборке ComfyUI уже лежат узлы
`SAM3_Detect` / `SAM3_VideoTrack` / `SAM3_TrackToMask`, `SAM3D Body`,
`Depth Anything 3`, `MoGe`, `SDPose`. То есть половина «новых моделей рынка» —
это вопрос файла весов, а не установки нод.

## 5. Чего не брать

- `RMBG-2.0` и `VRMBG-3.0` — некоммерческие; BiRefNet лучше по лицензии и не хуже по краю.
- `taggerine` — 5.3 ГБ, без ONNX, без опубликованного F1, на CPU «slow enough to test your patience».
- `Falconsai/nsfw_image_detection` — на генерации деградирует, а у нас весь контент генеративный.
- `unconditional-image-generation` целиком — тег про DDPM, к SDXL отношения не имеет.
- `GroundingDINO + SAM` как основной путь — Apache-2.0 приятен, но это два файла вместо
  одного SAM3.1, и качество масок ниже. Оставить запасом на случай лицензионных вопросов.

## 6. Риски и что проверить до «готово»

1. **SAM3 держит ~1.8 ГБ и не отдаёт их сразу.** На 12 ГБ он роняет следующий шаг; на 16 ГБ
   гнать отдельной фазой и выгружать до загрузки SDXL — как уже сделано с VLM.
2. **Промпт SAM3 — до 32 токенов**, запятые ищутся независимо и объединяются в маску.
   «red satin dress with lace trim» превратится в союз трёх масок. Для одежды точнее
   `individual_masks=true` и короткая фраза.
3. **Порог ниже 0.3** заставляет SAM3 «находить» то, чего на кадре нет. Держать 0.5.
4. **Парсеры обучены на фото.** На Illustrious-иллюстрации границы плывут: длинные волосы
   поверх одежды, свободные рукава. Маску после парсера всё равно растить `GrowMask`.
5. **PixAI-теггер на CPU** — десятки секунд на фото, в пачке из 40 кадров это минуты.
   Мерить на 17 кадрах из `out\bench`, а не на одном.
6. **Лицензии.** Meta SAM License разрешает коммерческое использование, но требует тех же
   условий при передаче и запрещает военное; для локального приватного конвейера годится,
   для продукта — на юридическую проверку.

## 7. Источники

- HF API: `/api/models?pipeline_tag=...` по шести тегам, `/api/models/{id}?blobs=true` — числа на 16.11.2026.
- SAM 3: https://github.com/facebookresearch/sam3/ , https://ai.meta.com/research/publications/sam-3-segment-anything-with-concepts/
- SAM License: https://huggingface.co/facebook/sam-3d-body-dinov3/blob/main/LICENSE
- Узлы ComfyUI: https://docs.comfy.org/tutorials/utility/video-segment-sam3 , PR https://github.com/Comfy-Org/ComfyUI/pull/13408
- Веса для ComfyUI: https://huggingface.co/Comfy-Org/sam3.1
- BiRefNet скорость/VRAM: https://modelsthatrun.dev/models/birefnet/
- Сравнение маттеров: https://www.openeasel.app/benchmarks/background-removal
- RMBG-2.0 лицензия: https://github.com/Bria-AI/RMBG-2.0/
- Парсер одежды: https://huggingface.co/mattmdjaga/segformer_b2_clothes , https://huggingface.co/fashn-ai/fashn-human-parser
- PixAI tagger: https://huggingface.co/pixai-labs/pixai-tagger-v1.0
- Taggerine: https://huggingface.co/lodestones/taggerine
- Сравнение WD-теггеров: https://huggingface.co/realphongha/danbooru-tag-query
- JoyCaption: https://huggingface.co/fancyfeast/llama-joycaption-beta-one-hf-llava , GGUF https://huggingface.co/concedo/llama-joycaption-beta-one-hf-llava-mmproj-gguf
- NSFW на AI-картинках: https://huggingface.co/Freepik/nsfw_image_detector
- Эстетика: https://arxiv.org/html/2312.17090
