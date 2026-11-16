"""Сверка `widgets_values` всех сборок с `/object_info` живого ComfyUI.

Зачем: панель ComfyUI читает значения виджетов **по порядку**, а не по имени.
Ошибка в порядке — «Invalid input: sampler_name / cfg» вместо кадра (AGENTS,
грабли 1), и её не видно ни в JSON, ни в проверках без сервера. Здесь имена,
порядок и типы виджетов каждого узла каждой сборки сверяются с тем, что реально
отдаёт запущенный ComfyUI.

Сервер не запускается сам: если на порту из `comfy_launch.json` (или `COMFY_URL`)
уже кто-то отвечает — работаем с ним и не гасим; если пусто — поднимаем
headless через `comfy_launch` и гасим после проверки. Модели в память не
грузятся: `/object_info` — это перечисление узлов, не генерация.

Запуск:
  python tools/check_widgets.py            # проверить и записать отчёт
  python tools/check_widgets.py --dump     # ещё и сохранить список виджетов

Отчёт: `out/bench/widgets_check.md`, код возврата 1 при любом расхождении.
"""
import json
import sys
import time
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import settings  # noqa: E402
import comfy_launch  # noqa: E402
from comic.workflow import DEFS  # noqa: E402

WIDGET_SCALARS = ("INT", "FLOAT", "STRING", "BOOLEAN")
# DynamicCombo: /object_info отдаёт тип строкой, значение входа — **ключ
# варианта строкой**, а поля выбранного варианта фронтенд добавляет отдельными
# виджетами `<combo>.<поле>` (см. dynamicComboWidget в comfyui_frontend_package)
# и `/prompt` ждёт их отдельными входами. Объект вместо строки ComfyUI молча
# теряет: узел падает с «missing 1 required positional argument».
DYNAMIC_COMBOS = ("COMFY_DYNAMICCOMBO_V3",)
SEED_NAMES = ("seed", "noise_seed")
REPORT = settings.ROOT / "out" / "bench" / "widgets_check.md"


def is_widget(spec):
    """Виджет ли это поле. COMBO в новых версиях приходит как "COMBO"+options."""
    if not isinstance(spec, (list, tuple)) or not spec:
        return False
    typ = spec[0]
    if isinstance(typ, list) or typ == "COMBO" or typ in DYNAMIC_COMBOS:
        return True
    opts = spec[1] if len(spec) > 1 and isinstance(spec[1], dict) else {}
    if opts.get("forceInput"):
        return False
    return typ in WIDGET_SCALARS


def is_dynamic(spec):
    return isinstance(spec, (list, tuple)) and bool(spec) and spec[0] in DYNAMIC_COMBOS


def input_groups(info):
    for group in ("required", "optional"):
        yield (info.get("input", {}).get(group) or {}).items()


def input_names(info):
    """Все входы узла по /object_info: сокеты и виджеты, без раскрытия вариантов."""
    return [name for group in input_groups(info) for name, _ in group]


def combo_options(spec):
    """Варианты COMBO/DynamicCombo: строки или объекты `{key, inputs}`."""
    if not isinstance(spec, (list, tuple)) or len(spec) < 2:
        return []
    return (spec[1] if isinstance(spec[1], dict) else {}).get("options") or []


def option_of(spec, chosen):
    return next((o for o in combo_options(spec) if isinstance(o, dict) and o.get("key") == chosen),
                None)


def widget_tree(info, chosen_of):
    """`[(имя, spec)]` виджетов узла, с раскрытием выбранного варианта DynamicCombo.

    `chosen_of(имя, позиция)` — чем выбран вариант: в UI-графе значение лежит по
    позиции в `widgets_values`, в `/prompt` — под тем же именем. Поля варианта
    получают имена с точкой (`output.normalization`) — ровно так их добавляет
    фронтенд ComfyUI и ждёт `/prompt`.
    """
    out = []

    def walk(items, prefix):
        for name, spec in items:
            if not is_widget(spec):
                continue
            full = prefix + name
            idx = len(out)
            out.append((full, spec))
            if not is_dynamic(spec):
                continue
            option = option_of(spec, chosen_of(full, idx))
            if option is None:
                continue
            for group in ("required", "optional"):
                nested = ((option.get("inputs") or {}).get(group) or {}).items()
                walk(list(nested), full + ".")

    for group in ("required", "optional"):
        walk(list(((info.get("input", {}).get(group)) or {}).items()), "")
    return out


def ui_chosen(values):
    def chosen_of(name, idx):
        return values[idx] if values is not None and idx < len(values) else None
    return chosen_of


def api_chosen(live):
    def chosen_of(name, idx):
        return live.get(name)
    return chosen_of


def widget_names(info, values=None):
    """Имена виджетов узла в порядке /object_info: required, потом optional."""
    return [name for name, _ in widget_tree(info, ui_chosen(values))]


def widget_specs(info, values=None):
    """`{имя: spec}` для всех виджетов узла, включая поля выбранного варианта."""
    return dict(widget_tree(info, ui_chosen(values)))


def api_specs(info, live):
    """То же, но выбор варианта берётся из `/prompt` по имени входа."""
    return dict(widget_tree(info, api_chosen(live)))


def value_problem(wname, spec, value):
    """Что не так со значением входа. Пустая строка — всё в порядке."""
    typ = spec[0]
    if typ in DYNAMIC_COMBOS:
        keys = [o.get("key") for o in combo_options(spec) if isinstance(o, dict)]
        if not isinstance(value, str):
            return (f"{wname}={value!r} не строка: ComfyUI ждёт ключ варианта "
                    f"из {keys}, иначе вход теряется целиком")
        if keys and value not in keys:
            return f"{wname}: выбран {value!r}, а варианты {keys}"
        return ""
    if isinstance(typ, list) or typ == "COMBO":
        return ""            # мягко: часть моделей узел скачивает сам
    if typ == "INT" and not isinstance(value, int):
        return f"{wname}={value!r} не INT"
    if typ == "FLOAT" and not isinstance(value, (int, float)):
        return f"{wname}={value!r} не FLOAT"
    if typ == "STRING" and not isinstance(value, str):
        return f"{wname}={value!r} не STRING"
    if typ == "BOOLEAN" and not isinstance(value, bool):
        return f"{wname}={value!r} не BOOLEAN"
    return ""


def is_link(value):
    return (isinstance(value, list) and len(value) == 2
            and isinstance(value[0], str) and isinstance(value[1], int))


def probe(url):
    try:
        with urllib.request.urlopen(url + "/object_info", timeout=5) as r:
            return json.loads(r.read())
    except Exception:
        return None


def wait_object_info(url, timeout, proc):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if proc.poll() is not None:
            return None
        info = probe(url)
        if info:
            return info
        time.sleep(1)
    return None


def check_build(name, data, info):
    """(строки отчёта, провалов, предупреждений) по одной сборке."""
    rows, fails, warns = [], 0, 0
    for node in data["nodes"]:
        typ, nid = node["type"], node["id"]
        meta = info.get(typ)
        if meta is None:
            rows.append((name, nid, typ, "провал", "узла нет в /object_info"))
            fails += 1
            continue
        if typ not in DEFS:
            rows.append((name, nid, typ, "провал", "типа нет в DEFS генератора"))
            fails += 1
            continue
        got = list(node["widgets_values"])
        note = ""
        # скрытое поле control_after_generate у seed-виджета (AGENTS, грабли 1)
        base = widget_names(meta)
        if len(got) == len(base) + 1 and base and base[0] in SEED_NAMES:
            if got[1] != "randomize":
                rows.append((name, nid, typ, "провал",
                             f"второй элемент {got[1]!r}, ждали 'randomize'"))
                fails += 1
                continue
            got = got[:1] + got[2:]
            note = "control_after_generate на месте"
        want = widget_names(meta, got)
        declared = DEFS[typ][1]
        if declared != want:
            rows.append((name, nid, typ, "провал",
                         f"DEFS {declared} ≠ /object_info {want}"))
            fails += 1
            continue
        if len(got) != len(want):
            rows.append((name, nid, typ, "провал",
                         f"значений {len(got)}, виджетов {len(want)}: {want}"))
            fails += 1
            continue
        specs = widget_specs(meta, got)
        for wname, value in zip(want, got):
            spec = specs.get(wname)
            if not spec:
                continue
            typ_decl = spec[0]
            if isinstance(typ_decl, list) or typ_decl == "COMBO":
                options = typ_decl if isinstance(typ_decl, list) else \
                    (spec[1] if len(spec) > 1 and isinstance(spec[1], dict) else {}).get("options") or []
                if options and value not in options:
                    # Мягко: часть моделей узел скачивает сам при первом прогоне.
                    warns += 1
                    note = (note + "; " if note else "") + f"{wname}: {value!r} нет в списке"
                continue
            problem = value_problem(wname, spec, value)
            if problem:
                rows.append((name, nid, typ, "провал", problem))
                fails += 1
        rows.append((name, nid, typ, "ok", note or "ok"))
    return rows, fails, warns


def check_api(name, payload, info):
    """(строки, провалов) по `prompt` из `<сборка>.api.json`.

    `widgets_values` — то, что читает панель; `/prompt` получает другой файл, и
    расхождение между ними ловится только здесь. Так и вышло с DA3: в UI-графе
    объект DynamicCombo выглядел правдоподобно, а `/prompt` терял вход целиком
    («missing 1 required positional argument: 'mode'»).
    """
    rows, fails = [], 0
    for nid, node in (payload.get("prompt") or {}).items():
        typ = node.get("class_type", "")
        live = node.get("inputs") or {}
        meta = info.get(typ)
        if meta is None:
            rows.append((name, nid, typ, "провал", "узла нет в /object_info (API)"))
            fails += 1
            continue
        specs = api_specs(meta, live)
        sockets = set(input_names(meta))
        problems = []
        for key, value in live.items():
            if is_link(value):
                if key not in sockets and key not in specs:
                    problems.append(f"{key}: связи некуда идти")
                continue
            if key in specs:
                problem = value_problem(key, specs[key], value)
                if problem:
                    problems.append(problem)
            elif key not in sockets:
                problems.append(f"{key!r} нет в /object_info")
        for problem in problems:
            rows.append((name, nid, typ, "провал", "API: " + problem))
            fails += 1
        rows.append((name, nid, typ, "ok", "API: ok"))
    return rows, fails


def report(rows, fails, warns, url, nodes_in_server):
    used = sorted({r[2] for r in rows})
    problems = [r for r in rows if r[3] != "ok"]
    lines = ["# Сверка widgets_values с /object_info", "",
             f"Дата: {time.strftime('%Y-%m-%d %H:%M')}",
             f"Сервер: {url}, узлов в /object_info: {nodes_in_server}",
             f"Проверено узлов: {len(rows)} в {len({r[0] for r in rows})} сборках, "
             f"типов: {len(used)}", "",
             f"**Провалов: {fails}**, предупреждений: {warns}", "",
             "## Что сверялось", "",
             "- имя и порядок виджетов каждого узла против `/object_info` "
             "(сначала `required`, потом `optional`, только виджеты) — именно по этому "
             "порядку панель читает `widgets_values`;",
             "- скрытый второй элемент `control_after_generate` у seed-виджета: "
             "на своём месте и равен `randomize`;",
             "- тип значения (INT/FLOAT/STRING/BOOLEAN); вариант COMBO проверяется мягко: "
             "часть моделей узел скачивает сам при первом прогоне;",
             "- DynamicCombo: значение — **ключ варианта строкой**, а поля варианта — "
             "отдельные виджеты с точкой (`output.normalization`), как их добавляет "
             "фронтенд; объект вместо строки ComfyUI теряет молча;",
             "- `prompt` из `<сборка>.api.json` — тот файл, который уходит в `/prompt`: "
             "каждый его вход должен быть в `/object_info`, а DynamicCombo — тем же "
             "ключом строкой. UI-граф и API-граф проверяются отдельно.", "",
             "## Узлы, где что-то не сошлось", ""]
    if problems:
        lines += ["| сборка | узел | тип | что не так |", "|---|---|---|---|"]
        lines += [f"| {b} | {n} | {t} | {why} |" for b, n, t, _, why in problems]
    else:
        lines.append("Нет: все имена, порядок и типы виджетов совпали.")
    lines += ["", "## Виджеты по типам (из /object_info)", ""]
    for typ in used:
        lines.append(f"- `{typ}`: " + ", ".join(DEFS[typ][1]))
    REPORT.parent.mkdir(parents=True, exist_ok=True)
    REPORT.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main():
    cfg = comfy_launch.plan()
    url = (cfg or {}).get("url") or settings.COMFY_URL
    info = probe(url)
    proc, spawned = None, False
    if info:
        print("ComfyUI уже отвечает:", url)
    else:
        if cfg is None:
            sys.exit(f"ComfyUI на {url} не отвечает, а comfy_launch.json не настроен")
        print("поднимаю", cfg["url"], "…")
        proc = comfy_launch._spawn(cfg)
        spawned = True
        info = wait_object_info(cfg["url"], cfg["timeout"], proc)
        if info is None:
            comfy_launch.stop(comfy_launch.Handle(url=cfg["url"], proc=proc, stop_after=True))
            sys.exit("ComfyUI не поднялся.\n" + comfy_launch._tail(cfg))
    try:
        print("узлов в /object_info:", len(info))
        rows, fails, warns = [], 0, 0
        for path in sorted(settings.WF_DIR.glob("*.json")):
            if path.name.endswith(".api.json"):
                continue
            data = json.loads(path.read_text(encoding="utf-8"))
            r, f, w = check_build(path.stem, data, info)
            rows += r
            fails += f
            warns += w
            api_path = path.with_name(path.stem + ".api.json")
            if api_path.exists():
                payload = json.loads(api_path.read_text(encoding="utf-8"))
                r, f = check_api(path.stem, payload, info)
                rows += r
                fails += f
        report(rows, fails, warns, url, len(info))
        if "--dump" in sys.argv:
            used = sorted({r[2] for r in rows})
            dump = REPORT.parent / "widgets_object_info.json"
            dump.write_text(json.dumps({t: widget_names(info[t]) for t in used},
                                       ensure_ascii=False, indent=2), encoding="utf-8")
            print("виджеты по типам:", dump)
        print("отчёт:", REPORT)
        print(f"провалов {fails}, предупреждений {warns}")
        return 1 if fails else 0
    finally:
        if spawned:
            comfy_launch.stop(comfy_launch.Handle(url=url, proc=proc, stop_after=True))
            print("сервер погашен")


if __name__ == "__main__":
    settings.utf8_streams()
    sys.exit(main())
