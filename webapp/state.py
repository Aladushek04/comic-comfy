"""Чистые правила отрисовки, без NiceGUI: их можно проверить без браузера.

Здесь только то, что легко ошибить в UI и что не зависит от виджетов:
какие шаги считать шагами текущего прохода и как подписывать настройку.
"""
RESET_STAGES = ("generate", "variant", "recheck", "save")


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


def missing_job_text(job_id):
    """Подпись, когда задания из адреса больше нет.

    Снятое по сроку задание — не «не запущено»: кадры и их паспорт лежат
    в out\\<фото>_<хэш>\\, и человек должен узнать, где их искать.
    """
    if not job_id:
        return "Задание не запущено"
    return "История задания истекла: файлы кадров остались в out\\<фото>_<хэш>"
