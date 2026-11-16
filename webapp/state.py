"""Чистые правила отрисовки, без NiceGUI: их можно проверить без браузера.

Здесь только то, что легко ошибить в UI и что не зависит от виджетов:
какие шаги считать шагами текущего прохода, где задание на таймлайне фаз и
как подписывать настройку.
"""
RESET_STAGES = ("generate", "variant", "recheck", "save")


# Фазы задания — крупные шаги, которые видит человек. Четыре, а не пять: сцена и
# теги живут внутри описания, проверка кадра — внутри кадра, и отдельные фазы
# только дробили бы прогресс. Ключ фазы машинный, подпись — в `PHASES`.
PHASES = (
    ("describe", "Описание"),
    ("prompt", "Промпт"),
    ("generate", "Генерация"),
    ("frame", "Кадр"),
)

# Этап задания → фаза. `error` здесь намеренно нет: ошибка не двигает таймлайн
# дальше, а красит ту фазу, где задание упало, — её видно по последнему событию.
PHASE_STAGES = {
    "describe": ("queued", "start", "cache", "manual", "describe", "scene",
                 "unload", "cache_write", "subject", "negative", "notes", "setting"),
    # «Описание готово» — это конец промпта: кадра ещё нет, генерация не начата.
    "prompt": ("preset", "prompt", "preview", "described"),
    "generate": ("comfy", "comfy_launch", "comfy_start", "comfy_cached", "comfy_queue",
                 "generate", "variant", "sampling", "comfy_done"),
    "frame": ("save", "recheck", "done", "partial"),
}
PHASE_OF = {stage: index for index, (name, _) in enumerate(PHASES)
            for stage in PHASE_STAGES[name]}

PENDING = "pending"          # фаза ещё не начиналась
ON = "on"                    # идёт сейчас
DONE = "done"                # пройдена
WARN = "warn"                # закончилась, но не полностью
ERR = "err"                  # здесь упало
PHASE_STATES = (PENDING, ON, DONE, WARN, ERR)


def phase_index(stage):
    """Номер фазы по этапу задания; None — этап фазы не двигает."""
    return PHASE_OF.get(str(stage or ""))


def phase_states(events, status="running"):
    """Состояния четырёх фаз по событиям задания.

    Фаза берётся из последнего события, а не из `snap["stage"]`: этап задания
    переписывается каждым событием, а `variant`/`save` идут подряд, и по одному
    полю не видно, где именно остановилось задание.
    """
    if status == "queued":
        return [PENDING] * len(PHASES)
    current = -1
    for event in events or ():
        index = phase_index((event or {}).get("stage"))
        if index is not None and index > current:
            current = index
    states = []
    for index in range(len(PHASES)):
        if index < current:
            states.append(DONE)
        elif index > current:
            states.append(PENDING)
        elif status in ("failed", "error"):
            states.append(ERR)
        elif status == "partial":
            states.append(WARN)
        elif status in ("done", "described"):
            states.append(DONE)
        else:
            states.append(ON)
    return states


def failed_phase(events, status="failed"):
    """Подпись фазы, где упало задание. Этап ошибки у всех один — «error»."""
    for (_, label), value in zip(PHASES, phase_states(events, status)):
        if value == ERR:
            return label
    return ""


def is_sampler_step(event, samplers=()):
    data = event.get("data") or {}
    if not data.get("max"):
        return False
    node = str(data.get("node") or "")
    if samplers:
        return node in {str(x) for x in samplers}
    return float(data["max"]) > 1      # старый граф без meta.samplers


def pass_steps(events, samplers=()):
    """Последний шаг текущего прохода, а не предыдущего.

    Новый вариант, Recheck и сохранение начинают новый проход: полоска
    предыдущего не должна висеть, пока не пришёл первый шаг нового.
    """
    start = 0
    for index, event in enumerate(events):
        if event.get("stage") in RESET_STAGES:
            start = index + 1
    step = None
    for event in events[start:]:
        if is_sampler_step(event, samplers):
            step = event
    return step


def fmt_setting(value, suffix=""):
    """Нет переопределения — «по умолчанию»; ноль остаётся нулём."""
    if value is None:
        return "по умолчанию"
    if isinstance(value, float) and value.is_integer():
        value = int(value)
    return f"{value}{suffix}"


def step_text(step, samplers=()):
    """«Шаг 7/30 · проход 1/2»: шаг сэмплера и его место в `meta.samplers`.

    Проход — это номер узла-сэмплера в сборке, а не номер варианта: у 07 их два
    (0.75/30 и 0.3/15), и «шаг 1/1» на втором проходе читался бы как новый кадр.
    Узел вне `meta.samplers` (препроцессор, старый граф) прохода не получает.
    """
    data = (step or {}).get("data") or {}
    total = int(float(data.get("max") or 0))
    if not step or not total:
        return ""
    text = f"Шаг {int(float(data.get('value') or 0))}/{total}"
    nodes = [str(x) for x in samplers or ()]
    node = str(data.get("node") or "")
    if node and node in nodes:
        text += f" · проход {nodes.index(node) + 1}/{len(nodes)}"
    return text


def step_fraction(step):
    """Доля текущего шага от 0 до 1: None — шага нет."""
    data = (step or {}).get("data") or {}
    total = float(data.get("max") or 0)
    if not step or total <= 0:
        return None
    return max(0.0, min(1.0, float(data.get("value") or 0) / total))


def missing_job_text(job_id):
    """Подпись, когда задания из адреса больше нет.

    Снятое по сроку задание — не «не запущено»: кадры и их паспорт лежат
    в out\\<фото>_<хэш>\\, и человек должен узнать, где их искать.
    """
    if not job_id:
        return "Задание не запущено"
    return "История задания истекла: файлы кадров остались в out\\<фото>_<хэш>"
