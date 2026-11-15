# comic-comfy — инструкции для агентов

Локальный конвейер ComfyUI для иллюстраций в стиле комиксов (18+, только взрослые вымышленные персонажи).
Этот файл — то, что нельзя вывести из кода: договорённости, грабли, пути.

## Где что лежит

- ComfyUI: `G:\Comfy-Desktop\ComfyUI-Installs\ComfyUI\ComfyUI` (Desktop 1.1.9, движок 0.45.2, torch 2.12.1+cu130).
- Общие модели: `G:\Comfy-Desktop\ComfyUI-Shared\models` (models/input/output — тоже там).
- Воркфлоу: `G:\AI\comic-comfy\workflows` — источник правды; копия для панели ComfyUI:
  `...\ComfyUI\user\default\workflows`. Что открывать в какой ситуации — `G:\AI\comic-comfy\INDEX.md`.
  Четырнадцать сборок, схема имени `номер_назначение_стиль`: 01_scene_{thepit,comic,jp}, 02_edit_mask,
  03_edit_qwen_{quality,fast}, 04_pose_reference, 05_style_return, 06_scene_noct,
  07_photo_{thepit,comic,jp,bimbo}, 08_photo_gta.
- Каталог моделей и план тестов: `G:\AI\comic-comfy\docs\models.md`.
  Новые файлы раскладываются по подпапкам (`loras\detail`, `loras\comic`, `loras\style`, `loras\qwen`,
  `checkpoints\alt`), ComfyUI отдаёт их как `detail\hands_xl.safetensors`.
- Генератор: `G:\AI\comic-comfy\tools\build_workflows.py`.
- Правила стиля: `G:\AI\comic-comfy\STYLE.md`.
- Дизайн и тайминги: `G:\AI\comic-comfy\docs\2026-11-12-comic-stack-design.md`.

## Как менять воркфлоу

Всё правится только в `build_workflows.py`, потом:

```powershell
$py = "G:\Comfy-Desktop\ComfyUI-Installs\ComfyUI\ComfyUI\.venv\Scripts\python.exe"
& $py "G:\AI\comic-comfy\tools\build_workflows.py"            # собрать
& $py "G:\AI\comic-comfy\tools\build_workflows.py" --run 07   # собрать и прогнать через API
```

Не редактировать JSON в `workflows/` руками — генератор перезапишет. Одно описание даёт два файла:
`<name>.json` (граф для UI) и `<name>.api.json` (payload для `/prompt`).

## Нельзя без явной просьбы

**Спрашивать разрешение перед любой тяжёлой загрузкой памяти.** Правило пользователя:
перед тем как занять видеопамять или оперативку — загрузкой модели, запуском LM Studio,
подъёмом своего ComfyUI, прогоном генерации — сначала спросить «можно загрузить память?»,
дождаться ответа и только потом действовать.

Ни `lms server start`, ни `lms load`, ни ComfyUI на 8199 без согласия. Работать с тем, что уже
работает: проверить порт, и если пусто — сказать об этом и ждать. 16 ГБ одна карта,
и загрузка 12B-модели выбивает ComfyUI.

## Грабли, на которые уже наступали

0. **LoadImage не переключался.** Скрипт подставлял описание нового фото, но в графе оставался
   `photo.png` от прошлого теста — и модель рисовала старое фото с новым промптом. Симптом:
   одежда и поза «как в прошлый раз», хотя описание верное. Теперь `generate()` патчит `LoadImage`
   через `ensure_input()`. **Проверять все вызовы `generate()`** — в пакетном режиме аргумент
   однажды потеряли, и кадры вышли квадратными с чужой позой.
0.1. **CLIP видит ~77 токенов.** Длинный негатив обрезался вместе с важными словами
   (`eyeglasses` стоял в конце и не доходил). Негатив короткий, специфичное — первым.
0.2. **Ключ — не имя файла, а содержимое.** Было: `ensure_input` клал фото под исходным именем и
   копировал только если исходник новее по mtime — два разных `same.png` из разных папок давали
   один файл, и ComfyUI получал первый. Кэш ключевался по stem: `same.png` и `same.jpg` делили
   описание, а `--scene` при попадании в кэш молча игнорировался. Теперь `fingerprint()` (sha1/12)
   входит в имя input-файла, в имя кэша, в имя папки `out\` и в имя временного превью; `--scene`
   входит в имя кэша; кэш пишется **до** подмешивания `--negative-extra`, иначе флаг оседал в
   описании навсегда. Проверка: `python tools\test_photo_prompt.py`.

1. **`control_after_generate`.** В UI-формате у seed-виджета есть скрытый седьмой элемент
   `widgets_values` (`"randomize"`), которого нет в API-формате. Забудешь — все значения KSampler
   съезжают на позицию влево, панель Issues пишет «Invalid input: sampler_name / cfg».
2. **`extra.linearData.outputs`** должен указывать на узел `SaveImage`. Если брать «максимальный id»,
   выходом окажется ImageToMask или LoRA — App Mode покажет пустоту после прогона.
3. **`linearData.inputs`** — тройки `[id, widgetName, {description}]`. Описание это единственный способ
   подписать поле: App Mode берёт имя виджета из ноды и локализует его.
4. **Clip skip.** Для Illustrious нужен `CLIPSetLastLayer(-2)`, иначе контур мягче, цвет грязнее.
5. **Порты.** Desktop держит 8188. Для тестов поднимать свой на 8199 с
   `--extra-model-paths-config G:\AI\comic-comfy\extra_model_paths.yaml`, иначе SaveImage пишет
   в папку установки, а LoadImage не видит общие input.
6. **Два ComfyUI одновременно.** На 16 ГБ не уживаются: шаг падает с 2–3 с до 14–30 с.
7. **Кэш.** Повторный прогон с тем же seed и тем же графом = 3 с (попадание в кэш), а не замер.
   Для честного тайминга менять seed.

## Civitai

Токен лежит в `%USERPROFILE%\.civitai\token` (в чат и в репозиторий не попадает). Скачивание:

```powershell
powershell -ExecutionPolicy Bypass -File G:\AI\comic-comfy\tools\civitai-dl.ps1 `
  -VersionId 2140775 -FileId 2034567 -OutName GTAV_IL_MIX.safetensors
```

Анонимно многие файлы отдают `401`, при этом `HEAD` может отвечать `200` — проверять только реальной
загрузкой. Часть LoRA закрыта логином даже с токеном (Early Access) — тогда качать руками из браузера.

Поиск по каталогу (печатает versionId/fileId/триггеры/размер, токен не выводит):

```powershell
powershell -ExecutionPolicy Bypass -File G:\AI\comic-comfy\tools\civitai-search.ps1 `
  -Query "gta style" -Types LORA -Base Illustrious -Nsfw -Limit 8
```

## Фото → промпт (LM Studio), две фазы

`tools/photo_prompt.py`: VLM смотрит фото и пишет описание, потом модель выгружается, потом ComfyUI
генерирует. На 16 ГБ иначе нельзя — VLM и SDXL вместе не живут.

- Vision есть только у мультимодальных сборок. Проверено на деле:
  **`qwen3.8-27b-uncensored-hauhaucs-aggressive-mtp@iq3_m`** — 22 с на фото, негатив точнее;
  `gemma4-12b-qat-uncensored-hauhaucs-balanced@q4_k_m` — ~40 с, лучше держит кружево.
  `Qwen3-Coder` и `Lobo-27B` текстовые, для фото не годятся.
- **Id модели важен целиком.** Ключ вида `...-mtp@?` даёт 400 Bad Request; нужен точный квант `@iq3_m`.
- Думающие модели тратят токены на рассуждения: держать `max_tokens` 3000 (на 400–1000 ответ обрезался).
- Ответ приходит в ```json-обёртке — `extract_json()` снимает fence и болтовню.
- Возраст: `with_adult()` вырезает `young/teen/girl/boy/1girl/school`, ставит `adult woman|man` первым тегом.
  Негатив всегда заканчивается `NEG_GUARD` (`child, loli, shota, young, underage`).
- Стиль задаёт скрипт (`STYLE_PREFIX`), а не LLM: иначе стиль уплывёт.
- Запускать только с разрешения пользователя (см. «Нельзя без явной просьбы»).
- Полезные ключи: `--style` (thepit|comic|jp|bimbo|gta вместо имени сборки), `--batch <папка>`
  (два прохода: все описания при одной загрузке модели, потом выгрузка и генерация),
  `--variants N` (несколько сидов на фото), `--scene "beach at sunset"` (переписать сцену, сохранив человека),
  `--dry-run` (показать промпт без запуска), `--from-json`, `--negative-extra` / `--negative-drop`,
  `--denoise`, `--seed`, `--force`. Описание кэшируется в `out\<фото>_<хэш>.prompt.json` (для `--scene`
  ещё и `__beach-at-sunset` в имени) и не затирается ручными режимами. `--no-llm --subject` тоже проходит
  через санитайзер возраста.
- Кэш и input привязаны к **содержимому** файла (`fingerprint`, sha1/12), а не к имени: одноимённые фото
  из разных папок больше не подменяют друг друга, `same.png` и `same.jpg` не делят описание.
  Самопроверка: `python tools\test_photo_prompt.py` (25 проверок, модели не нужны).
- Id модели нужен целиком (`...@iq3_m`); ключ `@?` даёт 400. `pick_model` такие ключи пропускает.
- Библиотека: `--save-preset имя` кладёт описание в `out\presets\`, `--preset имя` берёт его для другого фото.
- Результаты: каждый кадр копируется в `out\<фото>_<хэш>\` рядом с `run.json` (workflow, seed, denoise, промпт).
- `--recheck` — после генерации VLM смотрит кадр, дописывает промахи в негатив и делает один повторный проход.
  Стоит +1 описание и +1 генерацию, поэтому по умолчанию выключен.
- Ориентация: горизонтальные фото автоматически получают 1216×832 вместо 832×1216 (в сборках `01_*`).

## Роли LoRA

- Стиль: `thepit2_1768210` (V2, триггер `thick lips`) — прошит в 01/02/04/05/07.
- GTA-постер: `GTAV_IL_MIX` (триггер `gtav, copic-makers, western comics (style), copic_markers_painting`) — только в 08.
  Альтернативы лежат рядом (`GTA_style_XL`, `GTA6_KeyArt_NoobAI`, `gta_style_illustrious`), но по сравнению
  на одном кадре IL-версия чище: нет типографики на одежде и водяного знака.
- Тело: `Sexy_Detailer`, по умолчанию вес 0.
- Персонажи: `P477Y` (Patty), `P4M3L4` (Pamela).

## Проверки перед словом «готово»

- `python build_workflows.py` без ошибок, затем валидатор `widgets_values` по `/object_info`.
- Прогон через `/prompt` + `status_str == success`.
- Для инпейнта: разница вне маски ровно 0.
