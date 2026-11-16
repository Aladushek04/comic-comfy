# P3: декларативные сборки

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task.

**Goal:** 14 сборок описываются таблицей данных, а `build_workflows.py` только собирает из неё граф и `meta`. `_swap_style`, `_add_hands`, `_tune_bimbo` и патч 03b исчезают.

**Architecture:** `comic/workflow.py` получает `Build` (dataclass) и `build(build) -> (graph, api)`. Спецификации всех сборок — данные в `tools/build_workflows.py` (или `comic/builds.py`), без хирургии словарей. Графы после пересборки обязаны совпасть с текущими по структуре, а `meta` — с `tools/golden/meta.json` побайтно.

**Spec:** `docs/superpowers/specs/2026-11-16-comicpit-v2-design.md`, 7.2 (декларативные сборки), 7.7.

## Global Constraints

- `meta` всех 14 сборок равна `tools/golden/meta.json` — это контракт интерфейса.
- Текст промптов не меняется: `tools/golden/prompts.json` остаётся эталоном.
- Порядок `widgets_values` сохраняется: иначе панель ComfyUI покажет «Invalid input».
- `control_after_generate` остаётся на своём месте (скрытый седьмой элемент).
- `check_install.py` — код 0; `test_photo_prompt.py` (464+) и `test_web_app.py` (327+) зелёные.
- Никакой генерации; ComfyUI поднимать только для сверки `widgets_values` с `/object_info`, и только с разрешения (проверить занятость GPU).

## Review Focus

- Стиль ищется по роли, а не по весу 0.9: смена веса в спецификации не должна ломать варианты.
- Сборка 03b (быстрый редактор) — это данные (4 шага, CFG 1, свой LoRA), а не патч JSON.
- `_add_hands` перестаёт быть отдельной функцией: LoRA рук — строка в `loras[]` с ролью `hands`.
- `graph_fields()` продолжает выводить поля из графа, а не из спецификации, иначе `meta.fields` разойдётся с тем, что реально в графе.

---

### Task 1: `comic/workflow.py`

**Files:** Create `comic/workflow.py`; Test: `tools/test_photo_prompt.py`.

**Interfaces:** `Lora(file, role, weight, trigger)`, `Build(name, title, base, loras, prompts, resolution, samplers, app_fields, style)`, `build(b) -> (graph, api)`; `load_build(name)`.

- [ ] **Step 1:** проверка — `build()` для `07_photo_thepit` даёт граф, структурно равный `workflows\07_photo_thepit.json` (сравнение `json.dumps(sort_keys=True)`), и `api` с `meta`, равной текущей.
- [ ] **Step 2:** падает.
- [ ] **Step 3:** реализовать сборщик на существующих `DEFS`/`topo`.
- [ ] **Step 4:** зелёные.
- [ ] **Step 5:** коммит `feat: сборщик графов на данных`.

### Task 2: все 14 спецификаций

- [ ] **Step 1:** проверка — все 14 сборок структурно равны текущим файлам, `meta` равна `tools/golden/meta.json`.
- [ ] **Step 2:** падает.
- [ ] **Step 3:** перенести спецификации, убрать `_swap_style`/`_add_hands`/`_tune_bimbo`/патч 03b.
- [ ] **Step 4:** `build_workflows.py` без ошибок, файлы не изменились, 464+/327+.
- [ ] **Step 5:** коммит `refactor: 14 сборок описаны данными`.

### Task 3: сверка с ComfyUI

- [ ] **Step 1:** проверить занятость GPU; если свободен — поднять ComfyUI на 8199 с `extra_model_paths.yaml`.
- [ ] **Step 2:** сверить `widgets_values` каждого узла с `/object_info` (имена и порядок виджетов).
- [ ] **Step 3:** погасить сервер, записать результат в `out\bench\widgets_check.md`.
- [ ] **Step 4:** коммит `test: сверка виджетов с /object_info`.
