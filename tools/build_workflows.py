"""Генератор воркфлоу ComfyUI: драйвер над описаниями сборок.

Само описание — данные в `comic/builds.py`, сборка графа и `meta` —
`comic/workflow.py`. Здесь только то, что делает из этого файлы и прогон:

  * `build(name, spec)` — собрать и записать пару `<name>.json` /
    `<name>.api.json` (формат `{"prompt": ..., "meta": ...}`);
  * `--run <префикс>` — отправить сборку в `/prompt` и дождаться кадра.

Запуск:
  python build_workflows.py            # только сгенерировать
  python build_workflows.py --run 01   # сгенерировать и прогнать 01 через API

`build(name, spec)` принимает и готовое описание: так проверка формы меняет
значение в генераторе и убеждается, что после пересборки его видно в `meta`.
"""
import json
import sys
import time
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import settings  # noqa: E402  — он же кладёт корень репозитория в sys.path
from comic import workflow as wf  # noqa: E402
from comic.builds import WORKFLOWS  # noqa: E402,F401  — реэкспорт для проверок

BASE = settings.COMFY_URL
OUT = str(settings.WF_DIR)


def build(name, spec=None):
    """Описание сборки -> файлы. Возвращает `prompt` для POST /prompt."""
    spec = WORKFLOWS[name] if spec is None else spec
    graph, api = wf.build(name, spec)
    wf.write(OUT, name, graph, api, wf.meta(spec, api))
    return api


def run(api, label):
    body = json.dumps({"prompt": api}).encode()
    req = urllib.request.Request(BASE + "/prompt", data=body, headers={"Content-Type": "application/json"})
    try:
        res = json.loads(urllib.request.urlopen(req, timeout=30).read())
    except Exception as e:
        detail = getattr(e, "read", lambda: b"")()
        print(label, "ОШИБКА:", e, detail.decode(errors="replace")[:1200])
        return False
    pid = res.get("prompt_id")
    print(label, "в очереди:", pid)
    t0 = time.time()
    while time.time() - t0 < 600:
        time.sleep(3)
        try:
            h = json.loads(urllib.request.urlopen(BASE + "/history/" + pid, timeout=10).read())
        except Exception:
            continue
        if pid in h:
            st = h[pid].get("status", {})
            ok = st.get("status_str") == "success"
            print(label, "готово" if ok else "провал", round(time.time() - t0, 1), "с")
            if not ok:
                print(json.dumps(st.get("messages", [])[-2:], ensure_ascii=False)[:1500])
            return ok
    print(label, "таймаут")
    return False


def pick_targets(target):
    """Сборки по префиксу. Опечатка — ошибка, а не тихий ноль сделанного."""
    names = [n for n in WORKFLOWS if n.startswith(target)]
    if not names:
        sys.exit(f"нет сборки, начинающейся с «{target}».\nЕсть: " + ", ".join(sorted(WORKFLOWS)))
    return names


if __name__ == "__main__":
    settings.utf8_streams()
    # Имя проверяем до сборки: опечатка не должна переписывать файлы и молча ничего не делать.
    targets = []
    if "--run" in sys.argv:
        i = sys.argv.index("--run")
        if i + 1 >= len(sys.argv):
            sys.exit("--run требует имя сборки, напр. --run 07_photo_thepit")
        targets = pick_targets(sys.argv[i + 1])

    apis = {}
    for name in WORKFLOWS:
        apis[name] = build(name)
        print("собран", name)

    if targets:
        failed = [n for n in targets if not run(apis[n], n)]
        if failed:
            print("не прогнались:", ", ".join(failed))
            sys.exit(1)
