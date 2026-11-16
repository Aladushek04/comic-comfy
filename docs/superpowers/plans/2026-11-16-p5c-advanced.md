# P5c: расширенная панель и отмена

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task.

**Goal:** из интерфейса можно переопределить параметры кадра и отменить задание — и то и другое с проверками, а не «на глаз».

**Architecture:** `webapp/service.py` получает `cancel(job_id)` (очередь — снять, работа — `POST /interrupt` в ComfyUI и статус `cancelled`). В `webapp/components/` появляется `advanced_dialog.py` — диалог с группами полей, значения по умолчанию из `meta`, пустое поле значит «как в графе». Кнопка «Отмена» живёт в блоке запуска и видна только в `queued`/`running`.

**Spec:** `docs/superpowers/specs/2026-11-16-comicpit-v2-ui.md`, разделы 1, 3 (№5, №6), 7.

## Global Constraints

- Рельс не растёт: расширенные — в диалоге, а не в рельсе.
- Значения по умолчанию — из `meta.fields[].default` и `/object_info`, не из кода.
- Переопределения проходят через `comic/guard.py` (рамка не снимается).
- Отмена не трогает кадры и `run.json` на диске (0.15).
- Старая страница `/` не меняется; три набора зелёные (464 / 286 / 87).

## Review Focus

- Отмена работающего задания освобождает GPU: без `/interrupt` сервис продолжит сэмплить.
- Отмена в очереди не отправляет `/interrupt` и не трогает чужое задание.
- `cancelled` — это исход кадра, а не ошибка: в паспорт идёт `outcome=cancelled`, статус задания `cancelled`.
- Пустое поле ≠ ноль: `0` как значение должно оставаться возможным.

---

### Task 1: `service.cancel`

**Files:** Modify `webapp/service.py`; Test: `tools/test_web_app.py` (HTTP-заглушка ComfyUI уже есть).

- [ ] **Step 1:** проверка — отмена в очереди снимает задание (`status=cancelled`, `/prompt` не вызывался); отмена работающего шлёт `POST /interrupt` и ставит `cancelled`; чужое задание не трогается.
- [ ] **Step 2:** падает.
- [ ] **Step 3:** реализовать `cancel(job_id)` с двумя ветками; адрес ComfyUI — из `settings`/`Config`.
- [ ] **Step 4:** зелёные.
- [ ] **Step 5:** коммит `feat: отмена задания`.

### Task 2: кнопка отмены

**Files:** Modify `webapp/components/start_action.py`, `webapp/v2.py`.

- [ ] **Step 1:** проверка — в `queued`/`running` видна «Отмена», в остальных состояниях её нет; нажатие вызывает сервис.
- [ ] **Step 2:** падает.
- [ ] **Step 3:** реализовать.
- [ ] **Step 4:** 87+ браузерных зелёные.
- [ ] **Step 5:** коммит `feat: кнопка отмены в V2`.

### Task 3: расширенный диалог

**Files:** Create `webapp/components/advanced_dialog.py`; Modify `webapp/v2.py`, `webapp/collect.py`.

- [ ] **Step 1:** проверка — диалог открывается, показывает дефолты из `meta`, пустое поле означает «как в графе», введённое значение уходит в `JobRequest` и попадает в паспорт кадра.
- [ ] **Step 2:** падает.
- [ ] **Step 3:** реализовать группы: генерация (шаги, cfg, denoise, cn), стиль (вес), прочее (сид, вариантов, негатив).
- [ ] **Step 4:** зелёные; `0` в поле остаётся нулём.
- [ ] **Step 5:** коммит `feat: расширенные параметры в V2`.
