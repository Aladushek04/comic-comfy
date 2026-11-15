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
  settings.py           пути из env или extra_model_paths.yaml, без привязки к диску
  check_install.py      сверяет модели из сборок с диском и с DOWNLOADS.md
  download-hf.ps1       модели с Hugging Face (.part, докачка, точный размер)
  download-civitai.ps1  LoRA со Civitai, все ID проверены через API
  civitai-dl.ps1        один файл по versionId/fileId
  civitai-search.ps1    поиск по каталогу Civitai
  test_photo_prompt.py  самопроверка CLI и конвейера (модели не нужны)
workflows/              13 готовых сборок (открываются в ComfyUI)
docs/                   дизайн-документ, каталог моделей, кандидаты
INDEX.md                какую сборку открывать в какой ситуации
STYLE.md                единые настройки стиля и промпт-блоки
AGENTS.md               инструкции для агентов: пути, грабли, проверки
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
