# Каталог моделей: что лежит и для чего

Все пути — относительно папки моделей (`COMFY_MODELS` или `base_path` в `extra_model_paths.yaml`).
Откуда что скачать — `DOWNLOADS.md`; проверить раскладку — `python tools\check_install.py`.
ComfyUI показывает подпапки через обратный слэш: `detail\hands_xl.safetensors`.

**С 17.11.2026 часть файлов уехала в `D:\models_archive`** — всё, что перечислено в разделе
«Что уехало на диск D» ниже. В таблицах они остались как история: на `E:` их нет, сборки их
не используют. На `E:` лежит только то, что перечислено в разделе «Что остаётся на E:».

## Чекпойнты

| Файл | База | Размер | Для чего |
|---|---|---|---|
| `checkpoints\WAI-Nsfw-Illustrious-17.safetensors` | Illustrious | 6.5 | основная лошадка, прошит в 01/02/04/05/07/08 |
| `checkpoints\NoobAI-XL-v1.1.safetensors` | Illustrious | 6.6 | альтернатива, больше знания персонажей |
| `checkpoints\NovaComicILV2.safetensors` | Illustrious | 6.7 | западный комикс, SFW-ориентирован |
| `checkpoints\alt\hassaku_v3_4.safetensors` | Illustrious | 6.5 | **новый**: ярче и теплее WAI, под солнечные постеры |
| `checkpoints\alt\pornmaster_pro_v6.safetensors` | Illustrious | 6.6 | **новый**: явная анатомия без подбора LoRA |

## Стиль

| Файл | Размер | Триггер | Для чего |
|---|---|---|---|
| `loras\thepit2_1768210.safetensors` | 0.43 | `thick lips` | основной стиль TheOfficialPit V2, прошит в 01/02/04/05/07 |
| `loras\style\thepit_comic.safetensors` | 0.21 | `ComicPitXX` | комикс-вариант, прошит в 01/07 comic |
| `loras\style\johnpersons_original.safetensors` | 0.21 | `jpstyle` | вариант John Persons, прошит в 01/07 jp |
| `loras\style\bimbofusion.safetensors` | 0.21 | `bimbofusion artstyle` | тёплая палитра, вес 0.6, прошит в 07 bimbo |
| `loras\theofficialpit_artstyle_IL.safetensors` | 0.21 | `th3p1txxx` | лёгкая версия стиля |
| `loras\thepitstyle_v1.safetensors` | 0.21 | — | ещё один вариант ThePit (civitai model 2566177, v2883466), 57 загрузок, триггера нет |
| `loras\style\style_fernando.safetensors` | 0.21 | `f3rn4nd0` | **новый**: западный комикс, толстые линии |
| `loras\style\style_cherrygig.safetensors` | 0.43 | `chxrrygxg` | **новый**: западный стиль, мягче |
| `loras\digitalInkedComic_v1.safetensors` | 0.21 | — | старый комикс-стиль, больше не в воркфлоу |
| `loras\Clusterfuck_Illustrious-000035.safetensors` | 0.21 | — | запасной западный стиль |

## Детализация и персонажи

| Файл | Размер | Триггер | Для чего |
|---|---|---|---|
| `loras\Sexy_Detailer.safetensors` | 0.21 | — | тело; в графах вес 0, слот есть — поднимать вручную |
| `loras\detail\hands_xl.safetensors` | 0.43 | — | **новый**: кисти, главный брак |
| `loras\detail\detailed_perfection.safetensors` | 0.43 | `detailed` | **новый**: кожа, лицо, тело одним весом |
| `loras\P477Y.safetensors` | 0.21 | `P477Y` | Patty (Coxville) |
| `loras\P4M3L4.safetensors` | 0.21 | `P4M3L4` | Pamela (Coxville) |

## Комикс-страницы

| Файл | Размер | Триггер | Для чего |
|---|---|---|---|
| `loras\comic\comic_page_generator.safetensors` | 0.21 | `hcr_generator, comic, Multi view, Frame, Text, speech bubble` | **новый**: панели и реплики |
| `loras\comic\comic_book_page.safetensors` | 0.43 | `cartoon` | **новый**: цельная страница комикса |

## GTA

| Файл | Размер | Триггер | Статус |
|---|---|---|---|
| `loras\GTAV_IL_MIX.safetensors` | 0.05 | `gtav, copic-makers, western comics (style), copic_markers_painting` | в работе, 08 |
| `loras\GTA6_KeyArt_NoobAI.safetensors` | 0.16 | `gta6style` | хуже: рисует текст на одежде |
| `loras\GTA_style_XL.safetensors` | 0.85 | `GTA style` | хуже: водяной знак, пластик |
| `loras\GTA style.safetensors` | 0.85 | `GTA style` | **дубль** предыдущего, удалить после проверки |
| `loras\gta_style_illustrious.safetensors` | 0.21 | `gt9style` | не тестировался |
| `loras\GTAVstyleDoguXLv2.safetensors` | 0.02 | `gtv style` | не тестировался |

## Прочее (не LoRA)

- `controlnet\controlnet-union-sdxl-promax.safetensors` — поза, глубина, линии одним файлом.
- `ipadapter\ip-adapter-plus_sdxl_vit-h.safetensors` + `clip_vision\CLIP-ViT-H-…` — референс персонажа.
- `upscale_models\4x-UltraSharp.pth` — финальный апскейл.
- `diffusion_models\` — Qwen-Edit 2511 uncensored Q4, NoctQA (Qwen 2.1), SeedVR2 для видео.
- `loras\qwen\qwen4play_aio.safetensors` — **новый**: all-in-one NSFW для Qwen-редактора, конкурент MCNL.
- `loras\Qwen-Image-Edit-2511-Lightning-4steps-V1.0-bf16.safetensors` — 4 шага для Qwen.

## План тестов (по одному кадру на файл)

1. `hands_xl` — кадр из 01, где кисти схлопываются; сравнить до/после при весе 0.6.
2. `detailed_perfection` — тот же кадр, вес 0.5, смотреть кожу и лицо.
3. `hassaku_v3_4` — сцена из 01 вместо WAI: свет и палитра.
4. `pornmaster_pro_v6` — явная сцена из 01, сравнить анатомию с WAI.
5. `comic_page_generator` — первый шаг к воркфлоу 09 (страницы с панелями).
6. `qwen4play_aio` — та же правка, что в 03b, против MCNL.
7. `style_fernando` / `style_cherrygig` — тот же кадр, что 07, против ThePit V2.
8. `thepitstyle_v1` (model 2566177) — там же, третий участник A/B: у него нет триггера, поэтому
   сравнивать при одинаковом промпте и весе 0.85.

После тестов лишнее удалить, оставленное — прописать в `STYLE.md` и `AGENTS.md`.

## Что уехало на диск D (17.11.2026)

Старые и не используемые текущими стилями файлы переносятся в `D:\models_archive\`
(не удаляются). `D:` свободен на 803.8 ГБ, перенос — 72.46 ГБ.

| Файл | Размер | Почему |
|---|---|---|
| `diffusion_models\qwen-image-edit-2511-uncensored-Q4_K_M.gguf` | 10.86 | редактор 03 снят с проекта |
| `text_encoders\qwen_2.5_vl_7b_fp8_scaled.safetensors` | 8.74 | кодировщик только для 03 |
| `vae\qwen_image_vae.safetensors` | 0.24 | только для 03 |
| `loras\Qwen-Image-Edit-2511-Lightning-4steps…` | 0.79 | только для 03 |
| `loras\qwen-image-edit-plus-nsfw-lora.safetensors` | 0.55 | только для 03 |
| `loras\qwen\qwen4play_aio.safetensors` | 0.55 | только для 03 |
| `diffusion_models\seedvr2_7b_int8_convrot.safetensors` | 7.76 | видео-апскейл, ни одной сборки |
| `diffusion_models\seedvr2_3b_int8_convrot.safetensors` | 3.22 | то же |
| `vae\seedvr2_ema_vae_fp16.safetensors` | 0.47 | то же |
| `controlnet\controlnet-canny-sdxl.safetensors` | 2.33 | в графах только union promax |
| `checkpoints\sd_xl_base_1.0.safetensors` | 6.46 | не используется |
| `checkpoints\NoobAI-XL-v1.1.safetensors` | 6.62 | альтернатива, ни одна сборка не берёт |
| `checkpoints\NovaComicILV2.safetensors` | 6.65 | то же |
| `checkpoints\alt\hassaku_v3_4.safetensors` | 6.46 | то же |
| `checkpoints\alt\pornmaster_pro_v6.safetensors` | 6.62 | то же |
| 15 LoRA вне сборок (GTA_style_XL, style_cherrygig, detailed_perfection, comic_book_page, thepit_il, thepitstyle_v1, theofficialpit_artstyle_IL, digitalInkedComic_v1, Clusterfuck, gta_style_illustrious, comic_page_generator, style_fernando, GTA6_KeyArt_NoobAI, thepit_lycoris, GTAVstyleDoguXLv2) | 4.15 | ни одна сборка не ссылается |

## Что остаётся на E: и что с ним будет в V2.1

| Файл | Роль сейчас | План V2.1 |
|---|---|---|
| `WAI-Nsfw-Illustrious-17` | база всех 07/08 | **не меняется.** Кандидат на A/B — fp8-репак `WAI-illustrious-HSWQ` (4.75 ГБ) только если упрёмся в скорость загрузки |
| `thepit2_1768210` | стиль 07_thepit | **не меняется.** A/B с Plankton v3 (243 МБ, без триггера) |
| `style\thepit_comic`, `style\johnpersons_original`, `style\bimbofusion` | стили 07 | не меняются: портов на другие базы нет |
| `GTAV_IL_MIX` | стиль 08 | остаётся для SDXL; **добавится** порт `ZImageTurbo` (162 МБ, v2513561) |
| `controlnet-union-sdxl-promax` | глубина/поза 07/08 | остаётся; для Z-Image **добавится** `Fun ControlNet Union 2.1` (1.9 ГБ lite или 6.71 ГБ) |
| `depth_anything_v2_vitl` | глубина | **заменяется** на Depth Anything 3 (1.34 ГБ) |
| `NoctQA_V1_int8_convrot` + `qwen3vl_8b_int8` + `qwen_image_2.1_vae` | сцена 06 | не меняются |
| `CLIP-ViT-H-14`, `ip-adapter-plus`, `4x-UltraSharp` | референс, апскейл | не меняются |
| WD14 `swinv2` + `eva02-large` | теггер | не меняются; кандидат на A/B — pixai-tagger-v1.0 (только GPU) |
| — | — | **добавится** база `Z-Image-Turbo` AIO FP8 10 ГБ для 08 на новой базе |
| — | — | **добавится** NSFW-LoRA под Z-Image (`NSFW MASTER`, 237 тыс. загрузок) |

