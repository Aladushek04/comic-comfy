"""Фазы задания и шаг текущего прохода.

Четыре фазы (описание → промпт → генерация → кадр) и под ними «Шаг 7/30 ·
проход 1/2». Шаг берётся только у сэмплера: препроцессоры ComfyUI шлют
`progress` с `max=1`, и без фильтра подпись застревает на «шаг 0/1», пока идёт
сэмплер (0.14 №4). Номер прохода — место узла в `meta.samplers`, поэтому у 07
видно «проход 1/2», а не «шаг 1/1».

Ничего не считает само: фазы и шаг приходят из `webapp.state`, сюда — только
отрисовка. Подпись заголовка — из `labels.STAGE_LABEL`, чтобы этап задания
назывался одинаково со старой страницей.
"""
from nicegui import ui

from .. import state, theme
from ..labels import STAGE_LABEL, STATUS_LABEL, fmt_time


def title_text(snap, status):
    """Заголовок фазы: что делает задание прямо сейчас или чем кончилось."""
    snap = snap or {}
    frames = len(snap.get("frames") or [])
    stage = STAGE_LABEL.get(snap.get("stage") or "", snap.get("stage") or "")
    if status == "queued":
        return f"{STATUS_LABEL['queued']} · позиция {snap.get('queue') or 1}"
    if status == "running":
        return stage or STATUS_LABEL["running"]
    if status == "done":
        return f"{STATUS_LABEL['done']} · кадров {frames}"
    if status == "partial":
        return f"{STATUS_LABEL['partial']} · кадров {frames}"
    if status == "cancelled":
        # Отмена — исход, а не ошибка: кадров может не быть вовсе.
        return f"{STATUS_LABEL['cancelled']} · кадров {frames}" if frames \
            else STATUS_LABEL["cancelled"]
    if status == "failed":
        where = state.failed_phase(snap.get("events") or [], status)
        return f"{STATUS_LABEL['failed']} · {where}" if where else STATUS_LABEL["failed"]
    return STATUS_LABEL.get(status, STATUS_LABEL["empty"])


class PhaseTimeline:
    """Таймлайн фаз: четыре плашки, шаг прохода и полоска прогресса."""

    def __init__(self):
        self.title = None
        self.elapsed = None
        self.step = None
        self.bar = None
        self.rows = []
        self.signature = None

    def build(self):
        with ui.element("section") as card:
            theme.mark(card, "card", "phases")
            with ui.element("div") as head:
                theme.mark(head, "phase-head")
                self.title = ui.label(STATUS_LABEL["empty"])
                theme.mark(self.title, "title")
                self.elapsed = ui.label("")
                theme.mark(self.elapsed, "meta", "num")
            with ui.element("div") as row:
                theme.mark(row, "phase-row")
                for _, label in state.PHASES:
                    with ui.element("div") as phase:
                        theme.mark(phase, "phase")
                        theme.mark(ui.element("span"), "dot")
                        theme.mark(ui.label(label), "phase-name")
                    self.rows.append(phase)
            self.step = ui.label("")
            theme.mark(self.step, "meta", "num", "step")
            self.bar = ui.linear_progress(value=0, size="4px", show_value=False)
            theme.mark(self.bar, "step-bar")
            self.bar.set_visibility(False)
        self.set(None, "empty", ())
        return self

    def set(self, snap, status, samplers=()):
        """Полный снапшот: состояния фаз, шаг и таймер.

        Подпись не трогаем, если не изменилось ничего: задание шлёт шаги до 4 Гц,
        и лишние патчи в DOM — это те же мигания, только мельче.
        """
        if self.title is None:
            return
        events = (snap or {}).get("events") or []
        step = state.pass_steps(events, samplers)
        signature = (status, (snap or {}).get("stage"), len(events),
                     (snap or {}).get("queue"), len((snap or {}).get("frames") or []),
                     (snap or {}).get("elapsed"), state.step_text(step, samplers))
        if signature == self.signature:
            return
        self.signature = signature
        for row, value in zip(self.rows, state.phase_states(events, status)):
            for name in ("phase-done", "phase-on", "phase-warn", "phase-err"):
                theme.state(row, name, False)
            if value != state.PENDING:
                theme.state(row, "phase-" + value, True)
        self.title.set_text(title_text(snap, status))
        self.elapsed.set_text(fmt_time((snap or {}).get("elapsed") or 0)
                              if snap else "")
        self.step.set_text(state.step_text(step, samplers))
        fraction = state.step_fraction(step)
        if fraction is None:
            self.bar.set_visibility(False)
        else:
            self.bar.set_value(fraction)
            self.bar.set_visibility(True)
