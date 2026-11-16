# ComicPit-V2: состояние на 17.11.2026

Ветка `comicpit-v2`, тег `v1` — состояние до пересборки. Проект:
`docs/superpowers/specs/2026-11-16-comicpit-v2-design.md`, интерфейс —
`docs/superpowers/specs/2026-11-16-comicpit-v2-ui.md`, инвентаризация —
`docs/2026-11-16-comicpit-v2-audit.md`.

## Что сделано

| Фаза | Итог |
|---|---|
| P0 | паспорт пишет `steps/cfg/sampler/passes/outcome`; baseline 10 кадров в `out\v2-baseline` |
| P1a | `comic/config.py`, `tools/settings.py` — адаптер, `pyproject.toml` |
| P1b | `comic/cache.py` со схемой и миграцией |
| P2 | `comic/prompt.py` — один путь сборки; предпросмотр равен кадру; `comic/guard.py` — рамка и на переопределениях |
| P4 | `comic/run.py` схема 2; миграция старых паспортов; `meta` заморожена в `tools/golden/meta.json` |
| P5a-c | интерфейс `/v2`: рельс, стили списком, фазы, вкладки, кадр, полоса, ошибка, отмена, расширенный диалог |
| P3 | 14 → 12 сборок описаны данными; `build_workflows.py` 897 → 80 строк; графы не изменились ни на байт |
| P6a | `run_batch` удалён, пачка через `run_job`; мёртвый код; графы 03; `tools/sync_panel.py` |
| P6b | 72.46 ГБ старых моделей перенесены в `D:\models_archive`; на `E:` осталось 30.28 ГБ |

## Проверки

| Набор | Было (V1) | Стало |
|---|---|---|
| `test_photo_prompt.py` | 273 | **521** |
| `test_web_app.py` (из `.venv-web`) | 181 | **329** |
| `test_web_ui.py` (Chromium) | 35 | **99** |
| `check_install.py` | 22 модели | **17 моделей** |

## Что отложено

- **V2.1:** 08 на Z-Image Turbo — база, порт GTAV под `ZImageTurbo`, Fun ControlNet Union,
  Depth Anything 3, NSFW-LoRA. Файлы складываются в `E:\v2.1-models` (манифест с хешами там же).
- **Инспектор ≥1600** убран из спецификации: панели живут вкладками на любой ширине.
- **Переименование `webapp/` → `web/`** не делалось: оно тянет импорты в 428 проверках.
- **`--batch` и пресеты:** `--preset`/`--save-preset` в пачке отвергаются (одно имя на папку).
- **Теггер pixai** и **HSWQ fp8** — кандидаты на A/B, не решения.

## Что проверять после переезда на V2.1

1. `tools\check_install.py` — код 0, модели на месте после переноса из `E:\v2.1-models`.
2. `python tools\build_workflows.py` — сборки без ошибок, `sync_panel.py --check` — 0 расхождений.
3. Один кадр `07_photo_thepit` и один `08_photo_gta` на тех же сидах, что baseline, —
   сравнить с `out\v2-baseline` по стилю и времени.
