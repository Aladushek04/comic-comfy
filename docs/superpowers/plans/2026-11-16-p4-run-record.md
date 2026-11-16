# P4: запись прогона (схема 2) и заморозка meta

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task.

**Goal:** `run.json` описывается типизированной записью с версией схемы, а снапшот отдаёт `passes` и `outcome`; `meta` всех 14 сборок заморожена, чтобы P3 не сломал контракт интерфейса.

**Architecture:** `comic/run.py` владеет форматом: `PassRecord`, `RunRecord`, `read(folder)`, `write(folder, rec)`; старый `save_run` и `run.json` читаются через миграцию. Снапшот `webapp/service.py` получает новые ключи из белого списка.

**Tech Stack:** Python 3.13, stdlib.

**Spec:** `docs/superpowers/specs/2026-11-16-comicpit-v2-design.md`, 7.2 (RunRecord), 7.7 (миграция, outcome).

## Global Constraints

- Старые `run.json` читаются; `denoise: null` остаётся `null`, а не 0.0.
- Второй проход восстанавливается из сборки, а не из константы 0.3 (у `01_scene_thepit` 0.45, у `06` проход один).
- `outcome` по умолчанию `unknown`; значения только из `OUTCOMES`.
- Запись только атомарная.
- Наборы зелёные (412+ / 208+), `check_install` — 0.

## Review Focus

- Запись, созданная до P0 (без `steps`/`passes`), не должна получить выдуманные значения.
- Снапшот не должен ломать существующие ключи: UI читает 13 из 15.
- `meta` сравнивается целиком, включая `positive_template` и `negative`: иначе P3 сможет поменять промпт.

---

### Task 1: `comic/run.py`

**Files:** Create `comic/run.py`; Modify `tools/photo_prompt.py` (`save_run` 849-907, `workflow_from_file` 836-846); Test: `tools/test_photo_prompt.py`.

**Interfaces:** `PassRecord(denoise, steps)`, `RunRecord(schema, workflow, seed, passes, cn_strength, style_weight, overrides, outcome, positive, negative, file, ...)`, `read(folder) -> list[RunRecord]`, `write(folder, records)`.

- [ ] **Step 1:** проверка — запись из `out\v2-baseline\run.json` читается, получает `schema=2`, `passes` сохраняются как есть.
- [ ] **Step 2:** падает.
- [ ] **Step 3:** реализовать модель и миграцию; `save_run` пишет через неё.
- [ ] **Step 4:** 412+ зелёные.
- [ ] **Step 5:** коммит `feat: запись прогона, схема 2`.

### Task 2: старые паспорта

- [ ] **Step 1:** проверка — запись без `passes` получает проходы из своей сборки (`07` → 0.75/30 + 0.3/15, `06` → один проход); если сборку не восстановить — `passes=None` и `estimated=True`.
- [ ] **Step 2:** падает.
- [ ] **Step 3:** реализовать.
- [ ] **Step 4:** зелёные.
- [ ] **Step 5:** коммит `feat: миграция старых паспортов`.

### Task 3: снапшот

**Files:** Modify `webapp/service.py` (370-371, 396-398); Test: `tools/test_web_app.py`.

- [ ] **Step 1:** проверка — `snapshot()["frames"][0]` содержит `passes` и `outcome`, остальные ключи на месте.
- [ ] **Step 2:** падает.
- [ ] **Step 3:** добавить ключи в белый список.
- [ ] **Step 4:** 208+ зелёные.
- [ ] **Step 5:** коммит `feat: снапшот отдаёт проходы и исход`.

### Task 4: заморозка meta

**Files:** Create `tools/golden/meta.json`; Test: `tools/test_photo_prompt.py`.

- [ ] **Step 1:** проверка — хеш `meta` (поля `fields`, `loras`, `style_lora`, `style_weight`, `positive_template`, `negative`, `samplers`) всех 14 сборок равен эталону.
- [ ] **Step 2:** падает (эталона нет).
- [ ] **Step 3:** записать эталон из текущих `.api.json`.
- [ ] **Step 4:** зелёные; `build_workflows.py` не меняет ни одного байта `meta`.
- [ ] **Step 5:** коммит `test: заморозка meta перед интерфейсом`.
