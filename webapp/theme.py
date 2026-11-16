"""Токены, классы и тема V2 — одно место, где живут цвета, размеры и имена классов.

Компоненты не пишут ни `.classes(...)`, ни `.style(...)`: они называют роль
(`theme.mark(el, "card")`, `theme.state(el, "style-on", True)`), а строку класса
и её вид знает этот файл. Поэтому правило «ни одного литерального цвета вне
`:root`/`body.body--dark`» проверяется в одном файле, а не обходом компонентов.

Тема — состояние интерфейса, а не задания: файл лежит рядом с кадрами, но не
внутри папки фото. env `COMIC_THEME_FILE` нужен проверкам, чтобы не переписывать
выбор человека.
"""
import os
from pathlib import Path

import photo_prompt as pp


def path():
    """Файл выбора темы: env `COMIC_THEME_FILE` сильнее пути рядом с кадрами.

    Считается при каждом обращении, а не при импорте: проверки подменяют и env,
    и `pp.ROOT`, а решение о теме должно браться из того, что задано сейчас.
    """
    return Path(os.environ.get("COMIC_THEME_FILE") or pp.ROOT / "out" / "theme.json")


def read():
    """Сохранённый выбор: True — тёмная, False — светлая.

    Файла нет или он битый — это не повод показывать светлую страницу тому,
    кто просил тёмную, поэтому по умолчанию тёмная.
    """
    try:
        data = pp.read_json(path())
    except (OSError, ValueError):
        return True
    if not isinstance(data, dict) or not isinstance(data.get("dark"), bool):
        return True
    return data["dark"]


def save(dark):
    """Тема переживает перезапуск сервера, поэтому пишется на диск.

    Через `pp.write_json`: временный файл рядом с целевым, fsync и os.replace.
    `write_text` рвёт файл пополам, если процесс умрёт посередине записи.
    """
    pp.write_json(path(), {"dark": bool(dark)})


# ------------------------------------------------------------------ токены
# Цвета: 12 имён из спецификации (раздел 4). Больше в интерфейсе нет ни одного
# цвета — компоненты ссылаются только на эти имена.
LIGHT = {
    "--bg": "#f7f7f8",
    "--surface": "#ffffff",
    "--surface-2": "#f1f1f4",
    "--ink": "#16161a",
    "--ink-2": "#5b5b66",
    "--line": "#dcdce3",
    "--accent": "#7c3aed",
    "--accent-fill": "#7c3aed",
    "--accent-on": "#ffffff",
    "--ok": "#15803d",
    "--warn": "#b45309",
    "--err": "#dc2626",
}

DARK = {
    "--bg": "#0f0f11",
    "--surface": "#16161a",
    "--surface-2": "#1e1e24",
    "--ink": "#ececf1",
    "--ink-2": "#9a9aa8",
    "--line": "#2a2a33",
    # В тёмной теме акцент для текста светлее заливки: #7c3aed на #0f0f11 читается
    # хуже, чем #a78bfa, а заливка кнопки остаётся той же.
    "--accent": "#a78bfa",
    "--accent-fill": "#7c3aed",
    "--accent-on": "#ffffff",
    "--ok": "#4ade80",
    "--warn": "#fbbf24",
    "--err": "#f87171",
}

# Размеры: тоже токены. Пять ступеней типа и шесть отступов из спецификации —
# других значений в правилах ниже нет.
SIZES = {
    "--cp-fs-1": "0.75rem",
    "--cp-fs-2": "0.875rem",
    "--cp-fs-3": "1rem",
    "--cp-fs-4": "1.25rem",
    "--cp-fs-5": "1.5rem",
    "--cp-sp-1": "4px",
    "--cp-sp-2": "8px",
    "--cp-sp-3": "12px",
    "--cp-sp-4": "16px",
    "--cp-sp-6": "24px",
    "--cp-sp-8": "32px",
    "--cp-topbar": "56px",
    "--cp-rail": "400px",
    "--cp-inspector": "320px",
    "--cp-gutter": "16px",
    "--cp-card-pad": "16px",
    "--cp-frame": "min(62vh, 720px)",
    "--cp-photo": "220px",
    "--cp-drop": "180px",
    "--cp-row": "40px",
    "--cp-btn": "40px",
    "--cp-tabs": "32px",
    "--cp-strip": "56px",
    "--cp-radius": "0.5rem",
    "--cp-radius-sm": "0.25rem",
    "--cp-radius-full": "999px",
    "--cp-font-mono": 'ui-monospace, SFMono-Regular, "Cascadia Mono", Consolas, monospace',
}

# Классы: единственный словарь «роль → строка». Компонент называет роль,
# поэтому литеральных строк классов в нём нет.
CLASSES = {
    "app": "cp-app",
    "narrow": "cp-narrow",
    "topbar": "cp-topbar",
    "brand": "cp-brand",
    "brand-title": "cp-brand__title",
    "badges": "cp-badges",
    "badge": "cp-badge",
    "badge-ok": "cp-badge--ok",
    "badge-warn": "cp-badge--warn",
    "badge-err": "cp-badge--err",
    "icon": "cp-icon",
    "layout": "cp-layout",
    "rail": "cp-rail",
    "rail-scroll": "cp-rail__scroll",
    "stage": "cp-stage",
    "card": "cp-card",
    "title": "cp-title",
    "meta": "cp-meta",
    "num": "cp-num",
    "drop": "cp-drop",
    "drop-title": "cp-drop-title",
    "upload": "cp-upload",
    "frame": "cp-frame",
    "photo": "cp-photo",
    "styles": "cp-styles",
    "style": "cp-style",
    "style-on": "cp-style--on",
    "style-name": "cp-style__name",
    "style-weight": "cp-style__weight",
    "btn": "cp-btn",
    "btn-fill": "cp-btn--fill",
    "btn-ghost": "cp-btn--ghost",
    "actions": "cp-actions",
    "start": "cp-start",
    "status": "cp-status",
    "warn": "cp-warn",
    "err": "cp-err",
    "stage-empty": "cp-stage__empty",
}


def mark(element, *names):
    """Навешивает cp-классы по ролям: строки класса живут только здесь."""
    return element.classes(" ".join(CLASSES[name] for name in names))


def state(element, name, on):
    """Включает и выключает класс состояния (выбранный стиль, «идёт» и т.п.)."""
    cls = CLASSES[name]
    return element.classes(add=cls if on else None, remove=None if on else cls)


def _block(selector, tokens):
    body = "".join(f"  {name}: {value};\n" for name, value in tokens.items())
    return f"{selector} {{\n{body}}}\n"


# ------------------------------------------------------------------ стили
# `@layer components` уже объявлен шаблоном NiceGUI (theme, base, quasar,
# nicegui, components, utilities…), поэтому cp-классы перебивают Quasar без
# `!important` и без войны специфичности.
COMPONENTS = """
/* NiceGUI оборачивает страницу в .nicegui-content с padding и gap: для V2 они
   лишние — сетку считает .cp-layout, а верхняя панель липнет к нулю. */
.nicegui-content:has(> .cp-app) {
  padding: 0;
  gap: 0;
  align-items: stretch;
}

.cp-app {
  /* Акцент Quasar внутри V2: кнопки, поля и прогресс берут его из токенов.
     На :root этого нет намеренно — ui.colors() перекрасил бы и старую страницу. */
  --q-primary: var(--accent-fill);
  --q-secondary: var(--ink-2);
  --q-accent: var(--accent);
  --q-positive: var(--ok);
  --q-negative: var(--err);
  --q-warning: var(--warn);
  --q-info: var(--ink-2);
  background: var(--bg);
  color: var(--ink);
  font-size: var(--cp-fs-3);
  min-height: 100vh;
  width: 100%;
}

.cp-topbar {
  position: sticky;
  top: 0;
  z-index: 20;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--cp-sp-4);
  height: var(--cp-topbar);
  padding: 0 var(--cp-sp-4);
  background: var(--surface);
  border-bottom: 1px solid var(--line);
}

.cp-brand {
  display: flex;
  align-items: baseline;
  gap: var(--cp-sp-2);
  min-width: 0;
}

.cp-brand__title {
  font-size: var(--cp-fs-4);
  font-weight: 600;
  color: var(--ink);
  line-height: 1;
}

.cp-badges {
  display: flex;
  align-items: center;
  gap: var(--cp-sp-2);
  flex-wrap: wrap;
  justify-content: flex-end;
}

.cp-badge {
  display: inline-flex;
  align-items: center;
  gap: var(--cp-sp-1);
  min-height: var(--cp-sp-6);
  padding: 0 var(--cp-sp-3);
  border: 1px solid var(--line);
  border-radius: var(--cp-radius-full);
  background: var(--surface-2);
  color: var(--ink-2);
  font-size: var(--cp-fs-1);
  white-space: nowrap;
}

.cp-badge--ok {
  color: var(--ok);
  border-color: var(--ok);
}

.cp-badge--warn {
  color: var(--warn);
  border-color: var(--warn);
}

.cp-badge--err {
  color: var(--err);
  border-color: var(--err);
}

.cp-icon {
  min-width: var(--cp-btn);
  height: var(--cp-btn);
  padding: 0;
  border-radius: var(--cp-radius-full);
  background: transparent;
  color: var(--ink-2);
}

.cp-layout {
  display: grid;
  grid-template-columns: var(--cp-rail) minmax(0, 1fr);
  gap: var(--cp-gutter);
  padding: var(--cp-gutter);
  max-width: 1800px;
  margin: 0 auto;
  align-items: start;
}

.cp-rail {
  display: flex;
  flex-direction: column;
  gap: var(--cp-gutter);
  position: sticky;
  top: calc(var(--cp-topbar) + var(--cp-gutter));
  max-height: calc(100vh - var(--cp-topbar) - var(--cp-gutter) * 2);
}

/* Прокручивается только содержимое рельса: «Старт» — сосед, а не накладка,
   иначе липкая карточка закрывала бы список стилей. */
.cp-rail__scroll {
  display: flex;
  flex: 0 1 auto;
  flex-direction: column;
  gap: var(--cp-gutter);
  min-height: 0;
  overflow-y: auto;
  padding-right: var(--cp-sp-1);
}

.cp-stage {
  display: flex;
  flex-direction: column;
  gap: var(--cp-gutter);
  min-width: 0;
}

.cp-card {
  display: flex;
  flex-direction: column;
  gap: var(--cp-sp-3);
  width: 100%;
  padding: var(--cp-card-pad);
  background: var(--surface);
  border: 1px solid var(--line);
  border-radius: var(--cp-radius);
}

.cp-title {
  font-size: var(--cp-fs-2);
  font-weight: 600;
  color: var(--ink);
}

.cp-meta {
  font-size: var(--cp-fs-1);
  color: var(--ink-2);
}

.cp-num {
  font-family: var(--cp-font-mono);
  font-variant-numeric: tabular-nums;
}

.cp-drop {
  position: relative;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: var(--cp-sp-2);
  min-height: var(--cp-drop);
  padding: var(--cp-sp-4);
  border: 1px dashed var(--line);
  border-radius: var(--cp-radius);
  background: var(--surface-2);
  color: var(--ink-2);
  font-size: var(--cp-fs-2);
  text-align: center;
  cursor: pointer;
}

.cp-drop:hover {
  border-color: var(--accent-fill);
}

.cp-drop-title {
  font-size: var(--cp-fs-3);
  color: var(--ink);
}

/* Загрузчик — невидимый слой на всю рамку: он ловит drag&drop, а подсказку и
   клик рисует рамка. Свой заголовок Quasar (подпись, «0.0B / 0.00%», кнопки)
   здесь лишний. */
.cp-upload {
  position: absolute;
  inset: 0;
  opacity: 0;
}

.cp-upload .q-uploader {
  height: 100%;
  min-height: 0;
  border: none;
  border-radius: var(--cp-radius);
  background: transparent;
  box-shadow: none;
}

.cp-frame {
  display: flex;
  align-items: center;
  justify-content: center;
  height: var(--cp-photo);
  overflow: hidden;
  border: 1px solid var(--line);
  border-radius: var(--cp-radius);
  background: var(--surface-2);
}

.cp-photo {
  max-width: 100%;
  max-height: 100%;
  object-fit: contain;
}

.cp-styles {
  display: flex;
  flex-direction: column;
  gap: var(--cp-sp-1);
}

.cp-style {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--cp-sp-2);
  width: 100%;
  min-height: var(--cp-row);
  padding: var(--cp-sp-2) var(--cp-sp-3);
  border: 1px solid transparent;
  border-radius: var(--cp-radius-sm);
  background: transparent;
  color: var(--ink);
  font-size: var(--cp-fs-2);
  text-align: left;
  cursor: pointer;
}

.cp-style:hover {
  background: var(--surface-2);
}

.cp-style--on {
  border-color: var(--accent-fill);
  background: var(--surface-2);
  color: var(--accent);
}

.cp-style__name {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.cp-style__weight {
  color: var(--ink-2);
}

.cp-style--on .cp-style__weight {
  color: var(--accent);
}

.cp-btn {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  gap: var(--cp-sp-2);
  min-height: var(--cp-btn);
  padding: 0 var(--cp-sp-4);
  border: 1px solid transparent;
  border-radius: var(--cp-radius-sm);
  background: var(--surface-2);
  color: var(--ink);
  font-size: var(--cp-fs-2);
  text-transform: none;
  box-shadow: none;
}

.cp-btn--fill {
  background: var(--accent-fill);
  color: var(--accent-on);
}

.cp-btn--ghost {
  border-color: var(--line);
  background: transparent;
  color: var(--ink-2);
}

.cp-btn:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}

.cp-actions {
  display: flex;
  flex-direction: column;
  gap: var(--cp-sp-2);
}

.cp-start {
  display: flex;
  flex: 0 0 auto;
  flex-direction: column;
  gap: var(--cp-sp-2);
  width: 100%;
  padding: var(--cp-card-pad);
  background: var(--surface);
  border: 1px solid var(--line);
  border-radius: var(--cp-radius);
}

.cp-status {
  font-size: var(--cp-fs-2);
  color: var(--ink);
}

/* Поля Quasar рисуют рамку своим цветом (в тёмной теме — почти белым): здесь она
   из токенов, как и подписи. Иначе в вычисленных стилях появляется цвет не из
   палитры — ровно то, что запрещает визуальный контракт. */
.cp-app .q-field--outlined .q-field__control:before {
  border: 1px solid var(--line);
  border-radius: var(--cp-radius-sm);
}

.cp-app .q-field--outlined .q-field__control:hover:before {
  border-color: var(--ink-2);
}

.cp-app .q-field--outlined.q-field--focused .q-field__control:after {
  border: 1px solid var(--accent-fill);
  border-radius: var(--cp-radius-sm);
}

.cp-app .q-field__native,
.cp-app .q-field__input {
  color: var(--ink);
}

.cp-app .q-field__label {
  color: var(--ink-2);
}

.cp-warn {
  color: var(--warn);
  font-size: var(--cp-fs-1);
}

.cp-err {
  color: var(--err);
  font-size: var(--cp-fs-1);
}

.cp-stage__empty {
  display: flex;
  align-items: center;
  justify-content: center;
  height: var(--cp-frame);
  border: 1px dashed var(--line);
  border-radius: var(--cp-radius);
  background: var(--surface);
  color: var(--ink-2);
  font-size: var(--cp-fs-2);
  text-align: center;
}

/* ПК только: ниже 1280 страница не перестраивается, а показывает требование.
   Заметка стоит вне .cp-app, иначе её спрятал бы тот же display: none.
   Сами медиазапросы — в WIDE и NARROW ниже: нелейерные правила сильнее слоёв. */
.cp-narrow {
  display: none;
  position: fixed;
  inset: 0;
  align-items: center;
  justify-content: center;
  padding: var(--cp-sp-8);
  background: var(--bg);
  color: var(--ink);
  font-size: var(--cp-fs-4);
  text-align: center;
}
"""


# Токены в медиазапросах стоят вне `@layer`: нелейерные правила сильнее любых
# слоёв, иначе 420 px на широком экране проигрывали бы базовым 400 px.
WIDE = """
@media (min-width: 1600px) {
  :root {
    --cp-rail: 420px;
  }
}
"""

NARROW = """
@media (max-width: 1279.98px) {
  .cp-app {
    display: none;
  }

  .cp-narrow {
    display: flex;
  }
}
"""


def css():
    """Стили страницы V2: токены обеих тем, размеры и cp-классы.

    Токены стоят в `:root` и `body.body--dark` — как в спецификации; ни одного
    литерального цвета вне этих двух блоков здесь нет.
    """
    return (
        "<style>\n"
        + _block(":root", {**LIGHT, **SIZES})
        + _block("body.body--dark", DARK)
        + WIDE
        + "@layer components {\n"
        + COMPONENTS
        + "}\n"
        + NARROW
        + "</style>\n"
    )
