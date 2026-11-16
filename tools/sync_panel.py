"""Копия графов для панели ComfyUI: только из репозитория в панель.

Источник правды — `workflows\\` в репозитории: его пишет `build_workflows.py`.
Панель ComfyUI (`user\\default\\workflows`) — копия, и раньше её обновляли
руками, поэтому она разошлась с репозиторием (у `03_*` в панели 17 узлов
против 12). Здесь копирование в одну сторону: файл панели никогда не едет
обратно в репозиторий.

    python tools\\sync_panel.py            # скопировать графы и убрать снятые
    python tools\\sync_panel.py --check    # только сказать, что разошлось

Папка панели берётся из конфигурации, а не из кода: env `COMFY_PANEL`, иначе
`panel` из `comfy_launch.json`, иначе `user\\default\\workflows` рядом с `main`
оттуда же (или из `COMFY_MAIN`). Машинных путей в этом файле нет.

Чужие файлы панели (`utility_*`, всё не по схеме `NN_*.json`) не трогаются:
это не наши сборки. Снятые из репозитория сборки (`NN_*.json`, которых в
`workflows\\` больше нет) убираются из панели, чтобы она не предлагала
удалённые графы; `--no-prune` это отключает.
"""
import argparse
import os
import re
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import comfy_launch as cl  # noqa: E402  — он же читает comfy_launch.json
import settings  # noqa: E402

# Раскладка самого ComfyUI, а не путь этой машины: панель лежит рядом с main.py.
PANEL_SUBDIR = ("user", "default", "workflows")
# Сборка называется NN_назначение: по этой схеме и отличаем свои файлы от чужих.
BUILD_NAME = re.compile(r"^\d\d_.*\.json$")

ACTION = {"new": "скопирован", "update": "обновлён", "same": "совпадает",
          "stale": "убран из панели"}


class PanelError(RuntimeError):
    """Папку панели не нашли или она не папка."""


def panel_dir():
    """Папка графов панели ComfyUI: env -> comfy_launch.json -> рядом с main."""
    explicit = os.environ.get("COMFY_PANEL") or cl.load_config().get("panel")
    if explicit:
        return Path(explicit)
    main = os.environ.get("COMFY_MAIN") or cl.load_config().get("main")
    if not main:
        raise PanelError(
            "папка панели ComfyUI не задана: укажи COMFY_PANEL или panel в "
            "comfy_launch.json (либо main — тогда панель считается рядом с ним)")
    return Path(main).parent.joinpath(*PANEL_SUBDIR)


def repo_graphs(source=None):
    """Графы репозитория для панели: `<сборка>.json`, без `<сборка>.api.json`."""
    source = Path(source or settings.WF_DIR)
    return sorted(p for p in source.glob("*.json")
                  if not p.name.endswith(".api.json"))


def is_build_name(name):
    """Наш ли это файл панели: `NN_*.json`. `utility_*` — чужой, не трогаем."""
    return bool(BUILD_NAME.match(name))


def diff(source=None, panel=None):
    """Что разойдётся: [(имя, действие)], действия — new/update/same/stale."""
    graphs = repo_graphs(source)
    panel = Path(panel) if panel else panel_dir()
    names = {p.name for p in graphs}
    rows = []
    for src in graphs:
        dst = panel / src.name
        if not dst.exists():
            rows.append((src.name, "new"))
        elif dst.read_bytes() != src.read_bytes():
            rows.append((src.name, "update"))
        else:
            rows.append((src.name, "same"))
    if panel.is_dir():
        for dst in sorted(panel.iterdir()):
            if dst.is_file() and is_build_name(dst.name) and dst.name not in names:
                rows.append((dst.name, "stale"))
    return rows


def sync(source=None, panel=None, prune=True):
    """Копирует графы в панель и возвращает [(имя, действие)] для отчёта."""
    source = Path(source or settings.WF_DIR)
    panel = Path(panel) if panel else panel_dir()
    if not source.is_dir():
        raise PanelError(f"нет папки графов {source} — сначала "
                         f"python tools\\build_workflows.py")
    if not panel.is_dir():
        raise PanelError(
            f"нет папки панели {panel}: ComfyUI ещё не создавал "
            f"user\\default\\workflows (запусти его один раз) или проверь COMFY_PANEL")
    rows = []
    for src in repo_graphs(source):
        dst = panel / src.name
        if dst.exists() and dst.read_bytes() == src.read_bytes():
            rows.append((src.name, "same"))
            continue
        action = "update" if dst.exists() else "new"
        shutil.copy2(src, dst)
        rows.append((src.name, action))
    if prune:
        names = {p.name for p in repo_graphs(source)}
        for dst in sorted(panel.iterdir()):
            if dst.is_file() and is_build_name(dst.name) and dst.name not in names:
                dst.unlink()
                rows.append((dst.name, "stale"))
    return rows


def main(argv=None):
    ap = argparse.ArgumentParser(
        description="Копия графов из репозитория в панель ComfyUI (в одну сторону)")
    ap.add_argument("--check", action="store_true",
                    help="только показать расхождения, ничего не копировать")
    ap.add_argument("--source", help="папка графов репозитория (по умолчанию workflows)")
    ap.add_argument("--panel", help="папка панели ComfyUI (по умолчанию из конфига/env)")
    ap.add_argument("--no-prune", action="store_true",
                    help="не убирать из панели графы, снятые с репозитория")
    a = ap.parse_args(argv)
    try:
        panel = Path(a.panel) if a.panel else panel_dir()
        if a.check:
            rows = diff(a.source, panel)
        else:
            rows = sync(a.source, panel, prune=not a.no_prune)
    except PanelError as e:
        print(e)
        return 1

    changed = [r for r in rows if r[1] != "same"]
    for name, action in rows:
        print(f"  {ACTION[action]}: {name}")
    print(f"панель: {panel}")
    print(f"графов в репозитории: {len(rows) - sum(1 for r in rows if r[1] == 'stale')}, "
          f"изменилось: {len(changed)}")
    if a.check:
        return 1 if changed else 0
    return 0


if __name__ == "__main__":
    settings.utf8_streams()
    sys.exit(main())
