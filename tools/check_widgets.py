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
SEED_NAMES = ("seed", "noise_seed")
REPORT = settings.ROOT / "out" / "bench" / "widgets_check.md"


def is_widget(spec):
    """Виджет ли это поле. COMBO в новых версиях приходит как "COMBO"+options."""
    if not isinstance(spec, (list, tuple)) or not spec:
        return False
    typ = spec[0]
    if isinstance(typ, list) or typ == "COMBO":
        return True
    opts = spec[1] if len(spec) > 1 and isinstance(spec[1], dict) else {}
    if opts.get("forceInput"):
        return False
    return typ in WIDGET_SCALARS


def widget_names(info):
    """Имена виджетов узла в порядке /object_info: required, потом optional."""
    out = []
    for group in ("required", "optional"):
        for name, spec in (info.get("input", {}).get(group) or {}).items():
            if is_widget(spec):
                out.append(name)
    return out


def spec_of(info, name):
    for group in ("required", "optional"):
        found = (info.get("input", {}).get(group) or {}).get(name)
        if found:
            return found
    return None


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
        want = widget_names(meta)
        declared = DEFS[typ][1]
        got = list(node["widgets_values"])
        note = ""
        # скрытое поле control_after_generate у seed-виджета (AGENTS, грабли 1)
        if len(got) == len(want) + 1 and want and want[0] in SEED_NAMES:
            if got[1] != "randomize":
                rows.append((name, nid, typ, "провал",
                             f"второй элемент {got[1]!r}, ждали 'randomize'"))
                fails += 1
                continue
            got = got[:1] + got[2:]
            note = "control_after_generate на месте"
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
        for wname, value in zip(want, got):
            spec = spec_of(meta, wname)
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
            elif typ_decl == "INT" and not isinstance(value, int):
                rows.append((name, nid, typ, "провал", f"{wname}={value!r} не INT"))
                fails += 1
            elif typ_decl == "FLOAT" and not isinstance(value, (int, float)):
                rows.append((name, nid, typ, "провал", f"{wname}={value!r} не FLOAT"))
                fails += 1
            elif typ_decl == "STRING" and not isinstance(value, str):
                rows.append((name, nid, typ, "провал", f"{wname}={value!r} не STRING"))
                fails += 1
        rows.append((name, nid, typ, "ok", note or "ok"))
    return rows, fails, warns


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
             "- тип значения (INT/FLOAT/STRING); вариант COMBO проверяется мягко: "
             "часть моделей узел скачивает сам при первом прогоне.", "",
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
