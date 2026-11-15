# comic-comfy

Локальный конвейер ComfyUI для иллюстраций в стиле комиксов: генерация с нуля, правка по маске,
правка инструкцией через Qwen-Edit, фото → стиль, фото → рекламный арт GTA.

Здесь только то, что нельзя восстановить: графы, генератор, скрипты и правила стиля.
Модели (десятки гигабайт) не входят — они скачиваются скриптами.

## Структура

```
tools/
  build_workflows.py    генератор: одно описание -> UI-JSON + API-JSON + App Mode
  photo_prompt.py       фото -> описание (VLM) -> генерация (ComfyUI), две фазы
  jobs.py               задание целиком и события для CLI и страницы
  settings.py           пути из env или extra_model_paths.yaml, без привязки к диску
  check_install.py      сверяет модели из сборок с диском и с DOWNLOADS.md
  download-hf.ps1       модели с Hugging Face (.part, докачка, точный размер)
  download-civitai.ps1  LoRA со Civitai, все ID проверены через API
  civitai-dl.ps1        один файл по versionId/fileId
  civitai-search.ps1    поиск по каталогу Civitai
  test_photo_prompt.py  самопроверка CLI и конвейера (модели не нужны)
  test_web_app.py       самопроверка заданий и веб-слоя на HTTP-заглушках
webapp/
  ui.py                 страница NiceGUI: форма, таймер, результат
  service.py            одна очередь, состояние заданий на сервере
  catalog.py            сборки, meta.fields, списки из /object_info
  run.py                локальный сервер на loopback
workflows/              14 готовых сборок (открываются в ComfyUI)
docs/                   дизайн-документ, каталог моделей, кандидаты
INDEX.md                какую сборку открывать в какой ситуации
STYLE.md                единые настройки стиля и промпт-блоки
AGENTS.md               инструкции для агентов: пути, грабли, проверки
ComicWeb.cmd            запуск веб-интерфейса
requirements-web.txt    зависимости веб-слоя (отдельное окружение)
```

## Требования

- Windows, NVIDIA 16 ГБ VRAM (проверено на RTX 4060 Ti).
- ComfyUI Desktop (0.45+), torch 2.12+.
- Python 3.13 из venv ComfyUI.
- Для `photo_prompt.py` — LM Studio с vision-моделью (проверено: Qwen3.8-27B IQ3_M, Gemma4-12B).

## Быстрый старт

Сначала модели: `DOWNLOADS.md` (две команды, ~58 ГБ) и проверка `python tools\check_install.py`.
Пути берутся из `extra_model_paths.yaml` или переменных `COMFY_MODELS` / `COMFY_INPUT` /
`COMFY_OUTPUT`, править код не нужно.

```powershell
$py = "<путь>\ComfyUI\.venv\Scripts\python.exe"
& $py tools\build_workflows.py                    # собрать графы
& $py tools\build_workflows.py --run 07_photo_thepit   # собрать и прогнать
```

Фото → стиль:

```powershell
& $py tools\photo_prompt.py --image "photo.jpg" --style thepit --generate
& $py tools\photo_prompt.py --batch "папка" --style comic --variants 3 --generate
```

Стили: `thepit`, `comic`, `jp`, `bimbo`, `gta`.

## Веб-интерфейс

Локальная страница на NiceGUI: одно фото, стиль, сцена и варианты. Модели не запускает,
только проверяет, что LM Studio и ComfyUI уже работают.

```powershell
python -m venv .venv-web
.venv-web\Scripts\python.exe -m pip install -r requirements-web.txt
ComicWeb.cmd                                  # откроет http://127.0.0.1:8080
.venv-web\Scripts\python.exe webapp\run.py --port 8090 --no-browser
```

Веб-зависимости живут в `.venv-web` и не ставятся в окружение ComfyUI. Запуск работает из
любого каталога: корень репозитория считается от `webapp\run.py`, а не от текущего каталога.

Что на странице: загрузка фото с превью, стиль из сборок `07_*` и `08_photo_gta`, сцена,
число вариантов, «Старт» (весь цикл), «Только описание» (без генерации, описание можно
править) и «Сгенерировать по описанию» (ручной режим, LM Studio не нужен). Ниже — этап,
таймер, шаги сэмплера, журнал, галерея кадров и «Скачать». Расширенная панель: seed, denoise,
сила ControlNet, вес стилевой LoRA, recheck, чекпойнт, сэмплер, слоты LoRA, модель LM Studio,
добавление и удаление негативных тегов, полный positive/negative.

Ручные правки не попадают в кэш автоописания. Полный positive или negative — явное
переопределение: он заменяет собранный текст целиком и главнее описания и тегов.

**Как поле доходит до формы** (пример — denoise):

1. В `tools\build_workflows.py` у сборки `07_photo_thepit` стоит `denoise: 0.75`.
2. `python tools\build_workflows.py` пишет это значение и привязку в
   `workflows\07_photo_thepit.api.json`: `meta.fields.denoise = {node: "13", input: "denoise",
   type: "FLOAT", default: 0.75}`.
3. Страница читает `meta` и показывает `0.75` подсказкой в поле. Поменяйте значение в
   генераторе на `0.83`, пересоберите графы, обновите страницу — в поле будет `0.83`.
   Код `webapp\ui.py` при этом не меняется.

Пустое поле значит «как в сборке», `0` — явный ноль. Значения из списков (`чекпойнт`, `LoRA`,
`сэмплер`) берутся из `/object_info` работающего ComfyUI; если файла из графа нет на диске,
он всё равно остаётся выбранным.

Обновление NiceGUI: поправьте версию в `requirements-web.txt` и повторите
`pip install -r requirements-web.txt`; после обновления проверьте `python tools\test_web_app.py`.
Графы после `git pull` пересоберите: `*.api.json` не хранится в репозитории.

## Что важно знать

- Стиль задаёт граф, а не языковая модель: `photo_prompt.py` подставляет субъекта в шаблон промпта
  из сборки (`meta.positive_template`), поэтому CLI и панель дают одинаковый стиль.
- CLIP у SDXL режет текст на чанки по 77 токенов и кодирует их подряд: длинный промпт не
  обрезается, но дальние теги влияют слабее — важное ставим в начало.
- Перед загрузкой моделей в память спрашивать разрешение (см. `AGENTS.md`).
- Civitai-токен в репозиторий не попадает: он лежит в `%USERPROFILE%\.civitai\token`.

## Контент

Только взрослые вымышленные персонажи. Никаких реальных людей, никаких несовершеннолетних.
Локальное законодательство — на пользователе.
