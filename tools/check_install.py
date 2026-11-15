"""Проверка чистой установки: все модели из сборок на месте и все они есть в инструкции.

    python tools\\check_install.py
    python tools\\check_install.py --models "путь\\к\\моделям"     # чужая машина
    python tools\\check_install.py --docs DOWNLOADS.md

Что проверяет:
  1. каждая модель, которую грузит хоть одна сборка, лежит в COMFY_MODELS;
  2. каждая такая модель упомянута в DOWNLOADS.md (иначе чистую установку не пройти);
  3. depth-модель отмечена отдельно: её скачивает сам узел controlnet_aux.

Код возврата 1, если чего-то не хватает.
"""
import argparse
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import settings

# тип узла -> откуда берётся имя файла в UI-JSON
MODEL_NODES = {
    "CheckpointLoaderSimple": "ckpt_name",
    "LoraLoaderModelOnly": "lora_name",
    "ControlNetLoader": "control_net_name",
    "CLIPLoader": "clip_name",
    "CLIPVisionLoader": "clip_name",
    "VAELoader": "vae_name",
    "UNETLoader": "unet_name",
    "UpscaleModelLoader": "model_name",
    "DepthAnythingV2Preprocessor": "ckpt_name",
}

# Эти файлы качает не репозиторий, а сам узел при первом прогоне.
AUTO_DOWNLOADED = {"depth_anything_v2_vitl.pth"}


def workflow_models(wf_dir):
    """{имя файла: [сборки, которые его грузят]} по всем UI-графам."""
    found = {}
    for path in sorted(Path(wf_dir).glob("*.json")):
        if path.name.endswith(".api.json"):
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception as e:
            print(f"  {path.name}: не читается ({e})")
            continue
        for node in data.get("nodes") or []:
            if node.get("type") not in MODEL_NODES:
                continue
            values = node.get("widgets_values") or []
            if not values:
                continue
            name = str(values[0])
            found.setdefault(name, []).append(path.stem)
    return found


def disk_index(models_dir):
    index = {}
    if models_dir.is_dir():
        for p in models_dir.rglob("*"):
            if p.is_file():
                index.setdefault(p.name.lower(), p)
    return index


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

    needed = workflow_models(wf_dir)
    index = disk_index(models_dir)
    text = docs.read_text(encoding="utf-8") if docs.exists() else ""
    if not text:
        print(f"  !! {docs} нет — сверять не с чем")

    missing_disk, missing_docs, auto = [], [], []
    for name in sorted(needed):
        base = Path(name).name
        on_disk = index.get(base.lower())
        documented = base.lower() in text.lower()
        where = ", ".join(sorted(set(needed[name])))

        if base in AUTO_DOWNLOADED and not on_disk:
            auto.append(name)
            mark = "auto"
        elif on_disk:
            mark = "ok  "
        else:
            mark = "НЕТ "
            missing_disk.append(name)

        size = f"{on_disk.stat().st_size / 1e9:5.2f} GB" if on_disk else "      —   "
        print(f"  {mark} {size}  {name}   [{where}]")
        if not documented:
            missing_docs.append(name)
            print("        нет в инструкции — на чистой машине не найти")

    print()
    total = len(needed)
    print(f"моделей в сборках: {total}")
    if auto:
        print("  скачивает сам узел при первом прогоне: " + ", ".join(sorted(auto)))
    if missing_disk:
        print("  нет на диске: " + ", ".join(sorted(missing_disk)))
    if missing_docs:
        print("  не описано в инструкции: " + ", ".join(sorted(missing_docs)))
    if not missing_disk and not missing_docs:
        print("  всё на месте и описано")
    return 0 if not missing_disk and not missing_docs else 1


if __name__ == "__main__":
    sys.exit(main())
