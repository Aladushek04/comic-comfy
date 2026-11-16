"""История кадров: паспорт `run.json` папки кадра, а не память страницы.

Читается через `comic.run`: формат паспорта знает только он (схема 2, миграция
старых записей). Страница не помнит кадры между заданиями, поэтому после
перезагрузки вкладки история восстанавливается из файла, а не из сессии
(спецификация, раздел 6, пункт 6).

Нечитаемый паспорт — отказ `RunError`, а не пустая таблица: пустая история
молча теряет прежние кадры (AGENTS 0.15). Поэтому ошибка показывается текстом.
"""
from pathlib import Path

from nicegui import ui

from comic import run as comic_run

from .. import theme
from ..state import fmt_setting

COLUMNS = (
    {"name": "file", "label": "кадр", "field": "file", "align": "left"},
    {"name": "seed", "label": "seed", "field": "seed", "align": "right"},
    {"name": "denoise", "label": "denoise", "field": "denoise", "align": "right"},
    {"name": "outcome", "label": "исход", "field": "outcome", "align": "left"},
)

# Паспорт копит все кадры фото, а не только этого задания: без страниц таблица
# растянула бы страницу на сотни строк.
HISTORY_ROWS = 10


def rows_of(records):
    """Записи паспорта → строки таблицы. Сборка и проходы — в подписи исхода."""
    rows = []
    for record in records:
        parts = [str(record.workflow or "")]
        passes = " → ".join(f"{fmt_setting(p.denoise)}/{fmt_setting(p.steps)}"
                            for p in (record.passes or ()))
        if passes:
            parts.append(passes)
        if record.refined:
            parts.append("уточнённый")
        if record.estimated:
            parts.append("проходы из сборки")
        rows.append({
            "file": Path(record.file or "").name,
            "seed": record.seed,
            "denoise": fmt_setting(record.denoise),
            "outcome": " · ".join(x for x in parts if x),
        })
    return rows


class HistoryPanel:
    """Таблица кадров из паспорта папки."""

    def __init__(self):
        self.note = None
        self.table = None
        self.key = None

    def build(self):
        self.note = ui.label("История появится вместе с кадром.")
        theme.mark(self.note, "meta")
        self.table = ui.table(rows=[], columns=list(COLUMNS), row_key="file",
                              pagination=HISTORY_ROWS)
        theme.mark(self.table, "history")
        self.table.props("dense flat hide-no-data")
        return self

    def refresh(self, folder, count=0):
        """Читает паспорт папки.

        Ключ — папка и число кадров задания: паспорт копится по фото, и второе
        задание на то же фото пишет в тот же файл. По одной папке таблица
        осталась бы от прошлого задания.
        """
        if self.note is None:
            return
        folder = str(folder or "")
        key = (folder, int(count or 0))
        if key == self.key:
            return
        self.key = key
        if not folder:
            self._show([], "История появится вместе с кадром.")
            return
        try:
            records = comic_run.read(folder)
        except comic_run.RunError as e:
            self._show([], str(e))
            return
        if not records:
            self._show([], f"В паспорте {Path(folder).name} записей нет.")
            return
        # Свежий кадр сверху: паспорт пишется в порядке появления.
        self._show(list(reversed(rows_of(records))),
                   f"кадров в паспорте: {len(records)}")

    def _show(self, rows, note):
        self.table.rows = rows
        self.table.update()
        self.note.set_text(note)
        self.note.set_visibility(bool(note))
