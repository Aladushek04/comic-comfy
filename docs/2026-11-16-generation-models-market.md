# Генеративные модели: чем можно заменить текущие на 16 ГБ

Дата: 2026-11-16. Железо: RTX 4060 Ti 16 ГБ, 32 ГБ RAM, один слот — модели живут по очереди.
Числа рынка — HF API на 16.11.2026 (`pipeline_tag=text-to-image` и `image-text-to-image`,
сортировка по trending и по загрузкам). Время кадра — наши замеры из
`docs/2026-11-12-comic-stack-design.md`, не оценки.

## 1. Что стоит сейчас и сколько это стоит по времени

| Ветка | Модель | Файл | Шаги | Замер | Разрешение |
|---|---|---|---|---|---|
| 01 сцена | `WAI-Nsfw-Illustrious-17` (SDXL) | 6.46 ГБ | 30 + hires ×1.5 | 39 с тёплый, 63 с холодный | 1248×1824 |
| 03 правка | `qwen-image-edit-2511-uncensored` Q4_K_M (20B) | 10.86 ГБ | 20 | 375–397 с | 840×1232 |
| 03b правка | то же + Lightning 4 шага | +0.79 ГБ LoRA | 4 | 102 с | 840×1232 |
| 06 второй вкус | `NoctQA_V1_int8_convrot` (Qwen-Image-2.1, 7B) | 7.26 ГБ | 25, CFG 3 | 81 с | 1024×1024 |
| описание | `Huihui-Qwen3.6-35B` Q4 (LM Studio) | вне репо | — | 5.9 с без размышлений | — |

Вывод из замера: **20B-редактор в 20 шагов на этой карте непригоден**, рабочий режим — только
Lightning в 4 шага. То есть редактор уже живёт на костыле из трёх файлов (Q4-модель + LoRA
шагов + NSFW-LoRA).

## 2. Рынок генерации: кто наверху на 16.11.2026

`text-to-image` по trending: `MiniMax-H3-Image` (609 тыс. загрузок, 1239 лайков),
`Qwen-Image-2.1` (282 тыс., 3647), `Krea-2-Turbo` (86 тыс., 1992), `Z-Image-Turbo` (783 тыс.,
5609), `FLUX.1-dev` (466 тыс., 16 195 — всё ещё жив).

`image-text-to-image` (правки): `Qwen-Image-Layered` (106 тыс., 1196),
`Qwen-Image-Edit-Rapid-AIO` GGUF (109 тыс.), `HiDream-O1-Image` (27 тыс., 564).

Репозитории Comfy-Org дают масштаб: `Comfy-Org/Qwen-Image-2.1` — 9.7 млн загрузок,
`Comfy-Org/Z-Image-Turbo` — 9.7 млн, `Comfy-Org/Krea-2` — 4.5 млн,
`Comfy-Org/MiniMax-H3-Image` — 2.3 млн, `Comfy-Org/Flux-2-Klein-4B` — 1.4 млн.

## 3. Кандидаты по ролям

| Модель | Параметры | Файл (int8 / bf16) | Лицензия | Шаги | Оценка на 4060 Ti | Вердикт |
|---|---|---|---|---|---|---|
| **Qwen-Image-2.1 Turbo** | 7B | 7.26 / 14.23 ГБ | qwen-research | 5–8, CFG 1 | ~30–35 с (оценка по 06) | **главный кандидат**: генерация и правки одной моделью |
| **Flux.2 Klein 4B** | 3.88B | 7.75 ГБ | Apache-2.0 | 4 | ~12–18 с (оценка) | быстрый черновик, но реализм и почти нет NSFW-экосистемы |
| **Z-Image-Turbo** | 6.15B | 6.20 / 12.31 ГБ | Apache-2.0 | 8 | ~22–28 с (оценка) | чистая лицензия, 5.6 тыс. лайков; аниме-тюна нет |
| `Qwen-Image-Edit-Rapid-AIO` | 20B | Q4 ~12 ГБ | qwen-research | 4–8 | ~100 с (оценка) | три файла → один, но остаётся 20B |
| `HiDream-O1-Image` | 8.8B | ~35 ГБ bf16 | MIT | — | не мерили | MIT подкупает, размером не подкупает |
| `Illustrious-XL-v2.0` | 2.6B (SDXL) | 6.94 ГБ | CreativeML OpenRAIL-M | как SDXL | как 01 | база новее нашей, но WAI-17 — финетюн на v1.7 |

## 4. Кто в 16 ГБ не влезает

| Модель | Параметры | bf16 | int8 / 4-bit | Почему мимо |
|---|---|---|---|---|
| `MiniMax-H3-Image` | ~20B (50 слоёв, hidden 5376) | 40.47 ГБ | 21.21 / 13.36 ГБ | DiT 13.4 ГБ + TE 9.75 ГБ по очереди — впритык, и 20B на 288 ГБ/с будет медленным |
| `Krea-2-Turbo` | 12.82B | 26.28 ГБ | ~13 ГБ fp8 | 13 ГБ DiT + TE не оставляет места под VAE и латенты; фото-эстетика, не аниме |
| `Qwen-Image-Layered` | 20.43B | ~50 ГБ | ~12 ГБ Q4 | слои RGBA — красиво для панелей, но это второй 20B на диске |
| `Qwen-Image-Edit-2511` (наш) | 20.43B | ~41 ГБ | 10.86 ГБ Q4 | уже стоит; 375 с в 20 шагов |

Правило простое: **на 16 ГБ комфортно живут 4–8B в int8**, 12B — только fp8 и без запаса,
20B — только Q4 и только если это единственная модель в кадре.

## 5. Рекомендация

1. **Правки перевести с 20B на Qwen-Image-2.1 Turbo.** Та же семья, что уже работает в 06:
   `qwen_image_2.1_turbo_int8_convrot` 7.26 ГБ + LoRA шагов 0.34 ГБ (5/6/8 шагов, CFG 1).
   Узел `TextEncodeQwenImage21` в нашей сборке уже есть. Оценка: 30–35 с вместо 102 с,
   минус 10.9 ГБ файла и минус 9.4 ГБ кодировщика Qwen2.5-VL (2.1 работает на нашем
   `qwen3vl_8b_int8`).
2. **SDXL-ветку не трогать.** 12 стилевых LoRA — это и есть продукт; Illustrious v2 без
   финетюна под NSFW их не заменит. Смотреть не на базу, а на появление WAI/NoobAI на v2.
3. **Flux.2 Klein 4B — один пробный кадр**, не более: интересен как «фото → композиция за
   15 с», но Apache-лицензия не компенсирует отсутствие аниме- и NSFW-тюна.
4. **Z-Image-Turbo — держать в уме** как лицензионно чистую замену, если qwen-research
   когда-нибудь станет проблемой.
5. **MiniMax H3, Krea-2, Layered — не качать.** 20B и 12.8B на этой карте означают
   минуты на кадр, а не секунды.

## 6. Риски

- `qwen-research` — некоммерческая лицензия у Qwen-Image-2.1 и у Noct-Q. Для локального
  хобби-конвейера нормально, для продажи — только Apache-2.0 ветка (Flux.2 Klein, Z-Image).
- Оценки времени для кандидатов получены пересчётом с 06 (та же архитектура 7B, 3.2 с/шаг
  при 1024²), а не замером. Первый кадр каждой новой модели надо мерить отдельно.
- Переход на 2.1 для правок ломает `TextEncodeQwenImageEditPlus`-граф 03: нужен новый граф
  на `TextEncodeQwenImage21`, старый оставить как fallback.
- NSFW-LoRA от 2511 (`qwen-image-edit-plus-nsfw-lora`, `qwen4play_aio`) на 2.1 не переносится:
  нужен мерж/ LoRA под 2.1 (Noct-Q — пример, что такие есть).

## 7. Уточнение области: важны только 07 и 08

Пользователь уточнил: интересны стили и задания `07_photo_*` и `08_photo_gta`, правка фото
(`03`) больше не актуальна. Это меняет выводы выше:

- **Пункт 5.1 снят.** Переводить правки на Qwen-Image-2.1 не нужно. Стек `03`
  (`qwen-image-edit-2511` 10.86 ГБ + `qwen_2.5_vl_7b_fp8` 9.38 ГБ + Lightning 0.79 ГБ +
  NSFW-LoRA 0.55 ГБ ≈ 21.6 ГБ) можно снять с диска.
- **Рынок для 07/08 — это не 20B-редакторы, а три вещи:** база SDXL, порты стилевых LoRA
  и глубина. Новая архитектура без порта LoRA для 07/08 бесполезна: стиль делает LoRA.

### 7.1 Наши стили и куда они уже портированы (Civitai, 16.11.2026)

Проверено по API Civitai по **нашим** model id из `DOWNLOADS.md`, а не по поиску.

| Наш файл | Модель на Civitai | Все версии (16.11.2026) | Что делать |
|---|---|---|---|
| `thepit2_1768210` (Illustrious V2, 435 МБ, 2025-06-04) | TheOfficialPit Style, model 951576 | Illustrious V1 (2024-11-16), Illustrious V2 = наша, **MiniMax H3** v3445779 (142 МБ, 2026-11-11) | новых Illustrious-версий нет; H3 не для 16 ГБ. Отдельно есть чужой «Thepit style lora (Illustrious)» v3 (model 1109194, 243 МБ, 2025-11-25) — только как A/B |
| `thepit_comic` (`ComicPitXX`) | the pit comic Illustrious, 1569742 | только V1 | без изменений |
| `johnpersons_original` (`jpstyle`) | JohnPersons/ThePit — Original style, 1616311 | Illustrious V1 + Pony V1 | портов на новые базы нет |
| `bimbofusion` | Bimbofusion Artstyle (ThePit and others) — Cleaned, 1273315 | Illustrious, Pony, SDXL, SD1.5, Flux.1 D, HiDream | Illustrious-версия = наша, новых нет |
| `GTAV_IL_MIX` (52 МБ, `gtav`) | **GTAV artstyle**, 1891318 | Illustrious (52 МБ) = наша, **ZImageTurbo** v2513561 (162 МБ, 2025-12-18), **Anima** v2946199 (33 МБ, 2026-05-15) | единственный стиль с портами на базы, которые влезают |

Вывод: **ThePit остаётся на Illustrious.** Автор портировал его только в MiniMax H3, а это 20B.
**GTA — единственный, у кого есть и порт под Z-Image, и официальный depth-ControlNet** —
то есть полная цепочка фото → стиль на новой базе существует только для 08.

### 7.2 Базы, которые влезают в 16 ГБ, и есть ли у них ControlNet

Для 07/08 нужны три вещи сразу: img2img с фото, ControlNet глубины и LoRA стиля.

| База | Параметры | Файл | ControlNet | Лицензия | Вердикт |
|---|---|---|---|---|---|
| `WAI-17` (SDXL) — наша | 2.6B | 6.46 ГБ | union promax (глубина, поза, линии) | OpenRAIL-M | остаётся |
| `Z-Image-Turbo` | 6.15B | 6.20 int8 / 12.31 bf16 | **Fun ControlNet Union 2.1**: canny, depth, pose, MLSD, hed, scribble, gray (6.71 ГБ, lite 1.9 ГБ) | Apache-2.0 | единственный кандидат с глубиной |
| `Anima` | 2B (Cosmos-Predict2-2B) | 4.18 ГБ | только VACE-Canny (community, 1.5 тыс.) | non-commercial | аниме-база 2026, но контроля глубины нет |
| `MOSS-Image` | 5.08B | ~10 ГБ bf16 | нет | Apache-2.0 | только текст→картинка |
| `Flux.2 Klein 4B` | 3.88B | 7.75 ГБ | нет, но есть multi-reference editing | Apache-2.0 | фото как референс, без глубины |
| `Krea 2` | 12.8B | ~13 ГБ fp8 | — | community | не влезает с кодировщиком |

### 7.3 Что делать по порядку

1. **ThePit: сверить наш V2 с чужим v3** (model 1109194, 243 МБ) на одном кадре 07.
   Своего обновления у автора нет — только порт в MiniMax H3.
2. **Глубина: Depth Anything 3 вместо DA2 ViT-L.** Узлы `Load Depth Anything 3` /
   `Run Depth Anything 3` уже в сборке, веса 1.34 ГБ (large) или 0.14 ГБ (small),
   Apache-2.0. Влияет на все 07/08 сразу; проверить, что выход совместим с
   `ControlNetApplyAdvanced` (нужна нормализация 0–1, как у DA2).
3. **08 GTA → Z-Image-Turbo** — единственная полная цепочка: наш же LoRA в версии
   `ZImageTurbo` (162 МБ, v2513561) + официальный Fun ControlNet Union с depth +
   8 шагов, Apache-2.0. Anima-версия того же LoRA (33 МБ) есть, но контроля глубины там нет.
4. **Anima** — следить, но не переводить: некоммерческая лицензия и контроль только canny.
5. **MOSS, Krea 2, Flux.2 Klein, MiniMax** — для 07/08 мимо: либо нет ControlNet, либо не влезает.

### 7.4 Альтернативные площадки: tensor.art, seaart.ai, civarchive (16.11.2026)

**tensor.art** — единственный из трёх, где лежит то, чего нет на Civitai/HF:

| Находка | Что это | Для нас |
|---|---|---|
| `Illustrious-XL-V3.0-Stable` (официальный аплоад Onoma AI) | SDXL-база, eps-предсказание, LoRA-friendly, новые токены `contrast/brightness/sharpness/dynamic colors/colorfulness/saturation` | единственная настоящая замена базы для 07/08; файла в открытом доступе нет, v3.5 только в хостинге |
| `Z-Image-Turbo-AIO` (FP8 10 ГБ / BF16 20 ГБ) | один файл с VAE и TE внутри, 8 шагов, CFG 1, `res_multistep`/`simple`, Apache-2.0 | самый практичный вход в Z-Image на 16 ГБ |
| `VICE-GLOSS` (`gta-gloss-style`) | GTA-стиль под Z-Image, 9 шагов, CFG 1, вес 0.8–1.0, identity-neutral | второй GTA-стиль под ту же базу, что и наш порт `ZImageTurbo` |
| GTA-стиль на SD1.5 / Pony / NoobAI / SD3 / FLUX / Z-Image | шесть разных LoRA под одну эстетику | можно сравнивать базы, не меняя стиль |
| «The Pit» | только SD1.5-репринт, 9 МБ | мимо |

Размеры и счётчики на tensor.art рисует JS — в тексте страницы их нет. Скачивание без
аккаунта не подтверждено.

**seaart.ai** — индекс репринтов Civitai, не источник файлов. Скачивание включается
автором отдельным флагом (*Allow Downloads*), плюс обязательный логин; эксклюзивы
(Illustrious v3.6, Flow2.0, Z-Anime) отдаются только генерацией внутри сервиса.
Полезное: `Thepit style lora (Illustrious)` Plankton'а (v3, 18 тыс. загрузок на платформе),
`Bimbofusion Artstyle` (56.9 тыс.), `GTA Style` (`gt9style`, 0.6). Всё это есть на Civitai
с настоящим размером и хешем.

**civarchive.com** — индекс метаданных по SHA256, а не файлохранилище. Полезные точки:

- `https://civarchive.com/api/models/{modelId}?modelVersionId={versionId}` — версии и список зеркал;
- `https://civarchive.com/api/sha256/{sha256}` — все файлы и модели для хеша;
- `https://civarchive.com/api/download/models/{versionId}` — 302 на **первое** зеркало.

Практическое следствие: Civitai с апреля 2026 отдаёт mature-файлы анонимно как **401**
(переехали на civitai.red), поэтому civarchive отдаёт файл только тогда, когда в списке
зеркал есть HF или TensorFiles. Проверено на наших стилях:

| Файл | Версия | Зеркало без логина |
|---|---|---|
| `Thepit style lora (Illustrious)` Plankton v3, 243 МБ | 2438763 | **да**, HF `Muapi/thepit-style-lora-illustrious` |
| `TheOfficialPit Style` V2 (наш `thepit2_1768210`) | 1868373 | нет, только метаданные |
| `Bimbofusion Artstyle` Illustrious, 217.9 МБ | 1442607 | **да**, HF `minaiosu/Catalorian` |
| `GTA Style` Illustrious, 223.1 МБ | 2244192 | **да**, HF-зеркало |
| `GTA Style` Anima / MOSS | 3131420 / 3462074 | нет |
| `John Persons Art Style` — только Krea 2 | 3121517 | да, но база не наша |

Ловушки: `?modelVersionId=1868373` под `/models/1109194` на деле принадлежит модели
**951576** — читать `version.modelId` из JSON, а не id в адресе. Заголовок
«JohnPersons/ThePit - Original style» на civarchive не нашёлся вовсе.

Правило: **качать только с HF-зеркал или с Civitai с токеном.** civarchive в
`download-*.ps1` вслепую не встраивать — он ведёт на Civitai и получает 401.

### 7.5 NSFW как жёсткий фильтр и порядок пайплайна

Пользователь уточнил: кадры — обнажённое тело и явные сцены, а пайплайн идёт фазами
(VLM описывает фото → выгружается → ComfyUI генерирует). Оба уточнения меняют выводы.

**NSFW — не дефицит, а самый конкурентный сегмент.** Топ NSFW-LoRA по базам (Civitai,
сортировка по загрузкам, 16.11.2026):

| База | Топ NSFW-LoRA | Загрузки | Наши стили |
|---|---|---|---|
| `Flux.2 Klein 9B` | SNOFS (Sex, Nudes, Other Fun Stuff) | 329 878 | нет |
| `Krea 2` | krea2 sex / Krea 2 NSFW V5 | 130 678 / 126 926 | нет |
| `Z-Image-Turbo` | NSFW MASTER / BetterNudes / Mystic XXX | 236 802 / 71 267 / 119 373 | **GTA** (порт 162 МБ) |
| `Illustrious` | Furry Anatomy Options / USNR | 85 124 / 95 189 | **ThePit, JP, Bimbo, GTA** |
| `MOSS Image` | Better Nudes / Mystic XXX | 31 006 / 14 238 | GTA (138 МБ, без ControlNet) |
| `Anima` | Tentacle Pit / Anima Turbo | 10 839 / 82 957 | GTA (33 МБ) |

Вывод: **NSFW умеют все шесть баз**, и на новых он даже популярнее. Блокер не в этом.

**Настоящий блокер — booru-теги и стилевые LoRA.** Наш промпт собирается из описания VLM
и добавок WD14 (`large breasts`, `thick lips`, `pubic hair`) — это язык Illustrious.
Z-Image, Klein и Krea 2 обучены на естественном языке; теговый промпт на них надо
проверять отдельно, а `meta.positive_template` переписывать. Anima — единственная из
новых, кто, вероятно, поймёт теги (аниме-датасет), но это непроверено.

**Порядок пайплайна снимает вопрос VRAM, но добавляет вопрос времени.** Пока VLM
загружен, ComfyUI ждёт; после выгрузки 35B в 16 ГБ помещается любая одна модель,
включая Klein 9B в fp8 и Krea 2 в fp8. Цена — не память, а смена модели: 6.5 ГБ SDXL
грузится с диска ~10 с, 10 ГБ Z-Image — ~15–20 с, и это платится на каждый кадр, если
граф меняет модель.

**Следствие для остальных находок.** Любая дополнительная модель в кадре — ещё одна
загрузка: SAM3 (1.8 ГБ), BiRefNet, скорер эстетики. Поэтому скорер кадров лучше держать
на CPU или не заводить вовсе, а SAM3 — только в отдельной фазе маски, не в графе
генерации.

## 8. Источники

- HF API: `/api/models?pipeline_tag=text-to-image|image-text-to-image&sort=trendingScore|downloads`.
- Qwen-Image-2.1: https://huggingface.co/Qwen/Qwen-Image-2.1 (7B, unified t2i + edit)
- Файлы и LoRA шагов для ComfyUI: https://huggingface.co/Comfy-Org/Qwen-Image-2.1
- Noct Q Anime (наш 06): https://huggingface.co/Noctaluna/Noct-Q-Anime-Uncensored-Qwen-Image-2.1
- Flux.2 Klein 4B: https://huggingface.co/black-forest-labs/FLUX.2-klein-4B
- Z-Image-Turbo: https://huggingface.co/Tongyi-MAI/Z-Image-Turbo
- Krea-2: https://huggingface.co/krea/Krea-2-Turbo
- MiniMax H3 Image: https://huggingface.co/MiniMaxAI/MiniMax-H3-Image
- Illustrious XL v2.0: https://huggingface.co/OnomaAIResearch/Illustrious-XL-v2.0
