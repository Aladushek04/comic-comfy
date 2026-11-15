# Каталог моделей: что лежит и для чего

Все пути — относительно папки моделей (`COMFY_MODELS` или `base_path` в `extra_model_paths.yaml`).
Откуда что скачать — `DOWNLOADS.md`; проверить раскладку — `python tools\check_install.py`.
ComfyUI показывает подпапки через обратный слэш: `detail\hands_xl.safetensors`.

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
| `loras\Sexy_Detailer.safetensors` | 0.21 | — | тело, вес 0.5 |
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
