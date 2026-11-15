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
  civitai-dl.ps1        скачивание модели по versionId/fileId
  civitai-search.ps1    поиск по каталогу Civitai
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

- Стиль задаёт скрипт, а не языковая модель: промпт-блок подставляется из кода, чтобы манера не уплывала.
- CLIP у SDXL видит ~77 токенов: негатив короткий, специфичное — первым.
- Перед загрузкой моделей в память спрашивать разрешение (см. `AGENTS.md`).
- Civitai-токен в репозиторий не попадает: он лежит в `%USERPROFILE%\.civitai\token`.

## Контент

Только взрослые вымышленные персонажи. Никаких реальных людей, никаких несовершеннолетних.
Локальное законодательство — на пользователе.
