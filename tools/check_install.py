"""Проверка чистой установки: модели из сборок лежат где нужно и описаны в инструкции.

    python tools\\check_install.py
    python tools\\check_install.py --models "путь\\к\\моделям"     # чужая машина
    python tools\\check_install.py --docs DOWNLOADS.md

Что проверяет:
  1. каждая модель, которую грузит хоть одна сборка, лежит в СВОЁМ разделе моделей
     (чекпойнт в loras — ошибка, даже если имя совпало);
  2. каждая такая модель упомянута в DOWNLOADS.md;
  3. каждый граф читается как JSON;
  4. загрузчик, которого проверка не знает, не остаётся незамеченным;
  5. depth-модели узла controlnet_aux отмечены отдельно: их скачивает сам узел.

Код возврата 1, если хоть что-то не так.
"""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import settings

# Расширения, которые в графах означают файл модели.
MODEL_EXT = (".safetensors", ".gguf", ".pth", ".ckpt", ".bin", ".pt")

# Тип загрузчика -> в каком разделе ComfyUI ищет файл. Несколько разделов там,
# где ComfyUI принимает и `diffusion_models`, и `unet`.
LOADERS = {
    "CheckpointLoaderSimple": ("checkpoints",),
    "LoraLoaderModelOnly": ("loras",),
    "LoraLoader": ("loras",),
    "ControlNetLoader": ("controlnet",),
    "CLIPLoader": ("text_encoders", "clip"),
    "DualCLIPLoader": ("text_encoders", "clip"),
    "CLIPVisionLoader": ("clip_vision",),
    "VAELoader": ("vae",),
    "UNETLoader": ("diffusion_models", "unet"),
    "UnetLoaderGGUF": ("diffusion_models", "unet"),
    "UpscaleModelLoader": ("upscale_models",),
    "IPAdapterModelLoader": ("ipadapter",),
}

# Узлы controlnet_aux: файлы качает сам узел в custom_nodes, не в папку моделей.
AUTO_NODES = {"DepthAnythingV2Preprocessor", "DWPreprocessor"}

# Эти имена тоже считаем автоскачиваемыми, если узел незнакомый.
AUTO_DOWNLOADED = {"depth_anything_v2_vitl.pth", "dw-ll_ucoco_384_bs5.torchscript.pt"}


def workflow_models(wf_dir):
    """Список моделей из всех UI-графов: {имя: {types, workflows}}.

    Ошибки чтения возвращаются отдельно: нечитаемый граф — это провал проверки,
    а не строка в логе.
    """
    found, broken = {}, []
    for path in sorted(Path(wf_dir).glob("*.json")):
        if path.name.endswith(".api.json"):
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception as e:
            broken.append((path.name, str(e)[:80]))
            continue
        for node in data.get("nodes") or []:
            node_type = str(node.get("type") or "")
            values = node.get("widgets_values") or []
            if isinstance(values, dict):
                values = list(values.values())
            for value in values:
                if isinstance(value, str) and value.lower().endswith(MODEL_EXT):
                    rec = found.setdefault(value, {"types": set(), "workflows": []})
                    rec["types"].add(node_type)
                    rec["workflows"].append(path.stem)
    return found, broken


def expected_dirs(node_type):
    """Разделы, где ComfyUI найдёт файл для этого узла. None — узел неизвестен."""
    return LOADERS.get(node_type)


def find_file(models_dir, dirs, name):
    """Путь, если файл лежит в одном из разделов. Возвращает (path, tried)."""
    tried = []
    for d in dirs:
        p = Path(models_dir) / d / name
        tried.append(p)
        if p.exists():
            return p, tried
    return None, tried


def anywhere(models_dir, name):
    """Где угодно нашёлся файл с таким именем — чтобы сказать «лежит не там»."""
    if not Path(models_dir).is_dir():
        return None
    base = Path(name).name.lower()
    for p in Path(models_dir).rglob("*"):
        if p.is_file() and p.name.lower() == base:
            return p
    return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--models", help="папка моделей (по умолчанию из settings/extra_model_paths.yaml)")
    ap.add_argument("--docs", help="файл инструкции (по умолчанию DOWNLOADS.md)")
    ap.add_argument("--workflows", help="папка сборок (по умолчанию workflows)")
    a = ap.parse_args()

    models_dir = Path(a.models) if a.models else settings.MODELS_DIR
    docs = Path(a.docs) if a.docs else settings.ROOT / "DOWNLOADS.md"
    wf_dir = Path(a.workflows) if a.workflows else settings.WF_DIR

    print(f"модели: {models_dir}")
    print(f"сборки: {wf_dir}")
    print(f"инструкция: {docs}")

    if not wf_dir.is_dir():
        print(f"  !! папки сборок нет: {wf_dir}")
        return 1
    needed, broken = workflow_models(wf_dir)
    if not needed:
        print(f"  !! в {wf_dir} не нашлось ни одной модели — проверь, что это UI-графы")
        return 1
    text = docs.read_text(encoding="utf-8") if docs.exists() else ""
    if not text:
        print(f"  !! {docs} нет — сверять не с чем")

    problems, auto, unknown = [], [], []
    for name in sorted(needed):
        types = needed[name]["types"]
        where = ", ".join(sorted(set(needed[name]["workflows"])))
        base = Path(name).name

        if types & AUTO_NODES or base in AUTO_DOWNLOADED:
            path, _ = None, None
            for t in types:
                dirs = expected_dirs(t)
                if dirs:
                    path, _ = find_file(models_dir, dirs, name)
                    break
            if not path:
                auto.append(name)
                print(f"  auto      —     {name}   [{where}]")
            else:
                print(f"  ok   {path.stat().st_size / 1e9:5.2f} GB  {name}   [{where}]")
        else:
            dirs = None
            for t in types:
                dirs = dirs or expected_dirs(t)
            if not dirs:
                unknown.append(name)
                problems.append(name)
                print(f"  ????      —     {name}   [{where}]")
                print(f"        проверка не знает загрузчик {sorted(types)} — добавь его в LOADERS")
                continue
            path, tried = find_file(models_dir, dirs, name)
            if path:
                print(f"  ok   {path.stat().st_size / 1e9:5.2f} GB  {name}   [{where}]")
            else:
                problems.append(name)
                print(f"  НЕТ       —     {name}   [{where}]")
                print(f"        ждёт в: {', '.join(str(p) for p in tried)}")
                other = anywhere(models_dir, name)
                if other:
                    print(f"        но файл лежит в {other} — не в том разделе, ComfyUI его не увидит")

        if base.lower() not in text.lower():
            problems.append(name)
            print("        нет в инструкции — на чистой машине не найти")

    for name, err in broken:
        print(f"  БИТЫЙ ГРАФ      {name}: {err}")
        problems.append(name)

    print()
    print(f"моделей в сборках: {len(needed)}")
    if auto:
        print("  скачивает сам узел при первом прогоне: " + ", ".join(sorted(auto)))
    if unknown:
        print("  неизвестный загрузчик: " + ", ".join(sorted(unknown)))
    if broken:
        print("  не читаются: " + ", ".join(n for n, _ in broken))
    if problems:
        print("  проблемы: " + ", ".join(sorted(set(problems))))
        return 1
    print("  всё на месте и описано")
    return 0


if __name__ == "__main__":
    settings.utf8_streams()
    sys.exit(main())
