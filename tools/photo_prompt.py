"""Фото -> промпт (LM Studio) -> генерация (ComfyUI), две фазы последовательно.

Зачем две фазы: на 16 ГБ VLM и SDXL не живут вместе. Сначала модель смотрит фото
и пишет описание, потом выгружается, и только затем запускается ComfyUI.

    python photo_prompt.py --image G:\\...\\photo.png --workflow 07_photo_thepit
    python photo_prompt.py --image ... --workflow 07_photo_comic --generate
    python photo_prompt.py --image ... --workflow 07_photo_thepit --subject "..." --no-llm

Ключи:
  --workflow   имя сборки из workflows\\ (без .json)
  --generate   после описания сразу запустить ComfyUI
  --no-llm     не звать LM Studio, взять --subject
  --model      id модели в LM Studio (иначе первая загруженная / первая vision)
  --keep-llm   не выгружать модель (только если хватает памяти)
"""
import argparse
import base64
import json
import os
import random
import re
import shutil
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

LM = "http://127.0.0.1:1234"
COMFY = "http://127.0.0.1:8199"
ROOT = Path(r"G:\AI\comic-comfy")
WF_DIR = ROOT / "workflows"


def comfy_probe(url):
    try:
        get(url + "/system_stats", timeout=4)
        return True
    except Exception:
        return False


def resolve_comfy(explicit=None):
    """Свой тестовый стенд 8199, иначе Desktop на 8188."""
    for cand in (explicit, "http://127.0.0.1:8199", "http://127.0.0.1:8188"):
        if cand and comfy_probe(cand):
            return cand
    sys.exit("ComfyUI не отвечает ни на 8199, ни на 8188 — запусти его и повтори")

# Стилевой блок берём из генератора по префиксу имени сборки.
STYLE_PREFIX = {
    "01_scene_thepit": "comic book illustration, theofficialpit style, thick lips, ",
    "01_scene_comic": "comic book illustration, theofficialpit style, ComicPitXX, ",
    "01_scene_jp": "comic book illustration, theofficialpit style, jpstyle, ",
    "07_photo_thepit": "comic book illustration, theofficialpit style, thick lips, ",
    "07_photo_comic": "comic book illustration, theofficialpit style, ComicPitXX, ",
    "07_photo_jp": "comic book illustration, theofficialpit style, jpstyle, ",
    "07_photo_bimbo": "comic book illustration, theofficialpit style, bimbofusion artstyle, ",
    "08_photo_gta": "GTA V loading screen artwork, gtav, copic-makers, western comics (style), copic_markers_painting, ",
}

STYLE_SUFFIX = ("bold black outlines, flat colors, clean skin, high contrast, limited palette, "
                "clean silhouette")

STYLE_ALIAS = {
    "thepit": "07_photo_thepit",    "comic": "07_photo_comic",
    "jp": "07_photo_jp",
    "bimbo": "07_photo_bimbo",
    "gta": "08_photo_gta",
}
OUTPUT_DIR = Path(r"G:\Comfy-Desktop\ComfyUI-Shared\output\comic")
INPUT_DIR = Path(r"G:\Comfy-Desktop\ComfyUI-Shared\input")


def ensure_input(image_path):
    """Кладёт фото в общую папку input и возвращает имя для LoadImage."""
    src = Path(image_path)
    dst = INPUT_DIR / src.name
    if not dst.exists() or dst.stat().st_mtime < src.stat().st_mtime:
        INPUT_DIR.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)
    return src.name


def resolve_workflow(workflow, style):
    if workflow:
        return workflow
    if style:
        if style in STYLE_ALIAS:
            return STYLE_ALIAS[style]
        if style in STYLE_PREFIX:
            return style
    sys.exit("укажи --workflow или --style: " + ", ".join(STYLE_ALIAS))


def rewrite_scene(desc, scene, model):
    """Текстовый проход: та же персона, другая сцена. Фото не отправляем."""
    ask = (f"Rewrite this photo description, placing the same person into: {scene}.\n"
           f"Keep face, hair, body type, glasses and jewellery. Change clothing and background "
           f"as the scene requires. Return ONLY JSON with subject, negative_extra, notes.\n\n"
           f"{desc['subject']}")
    payload = {"model": model, "temperature": 0.6, "max_tokens": 3000,
               "messages": [{"role": "user", "content": ask}]}
    try:
        r = post(LM + "/v1/chat/completions", payload)
        content = r["choices"][0]["message"].get("content") or ""
        data = extract_json(content)
        if not data:
            return None
        subject = with_adult(data.get("subject", ""))
        if not subject:
            return None
        return {
            "subject": subject,
            "negative_extra": ", ".join(scrub_tags(data.get("negative_extra", ""))),
            "notes": str(data.get("notes", "")).strip(),
            "scene": scene,
        }
    except Exception as e:
        print("   сцена не переписалась:", e)
        return None

# CLIP у SDXL видит ~77 токенов. Всё, что дальше, отбрасывается — поэтому списки короткие,
# а важное (что подавить именно на этом фото) идёт первым.
NEG_CORE = ("photorealistic, 3d render, bad anatomy, bad hands, extra fingers, "
            "watermark, text, signature, blurry, low quality")

NEG_GUARD = "child, loli, shota, underage"

SYSTEM = """You describe photographs for a text-to-image model.
Return ONLY a JSON object with three string fields: subject, negative_extra, notes.

Rules:
- subject: English, comma-separated tags, 25-45 words. Cover: who, hair, face details,
  glasses/accessories, clothing, pose, hands, framing, background, light.
- Always start with "adult woman" or "adult man". Never write age words:
  young, teen, teenage, girl, boy, child, kid, adolescent, schoolgirl, schoolboy.
- Describe only what is visible. Do not invent objects, do not change the scene.
- Always name small objects that must survive stylisation: glasses, lenses, jewellery,
  necklace, earrings, belt, tattoos, watch.
- No style words, no quality tags, no artist names, no trigger words, no LoRA names.
- negative_extra: 3-8 comma-separated things to suppress for THIS photo
  (e.g. "white lenses, missing glasses, extra fingers").
- notes: one short sentence in Russian about what is fragile in this photo.

Example output:
{"subject":"adult woman, early 30s, long wavy blonde hair, black framed glasses, red sleeveless dress, thin gold necklace, arms spread wide, neutral grey studio background, soft frontal light","negative_extra":"white lenses, opaque glasses, missing necklace","notes":"главное — сохранить очки и цепочку."}"""

# Возрастные слова, которые модель любит добавлять в описание.
AGE_WORDS = ("young", "teen", "teenage", "teenager", "girl", "boy", "child", "kid",
             "schoolgirl", "schoolboy", "adolescent", "loli", "shota", "underage", "minor")


def scrub_tags(text):
    """Убирает возрастные слова и точные дубли, сохраняя порядок."""
    out, seen = [], set()
    for part in str(text).split(","):
        tag = part.strip().strip(".")
        if not tag:
            continue
        low = tag.lower()
        if any(re.search(r"(?<![a-z])" + re.escape(w) + r"(?![a-z])", low) for w in AGE_WORDS):
            continue
        if low in seen:
            continue
        seen.add(low)
        out.append(tag)
    return out


GENERIC_PERSON = {"woman", "women", "lady", "female", "man", "male", "guy", "gentleman", "person"}


def with_adult(subject):
    """Взрослый — первым тегом, с явным родом. Убирает дубли-роды и всё школьное."""
    raw = scrub_tags(subject)
    if not raw:
        return ""
    low = str(subject).lower()
    male = re.search(r"(?<![a-z])(man|male|guy|gentleman|boy)(?![a-z])", low)
    female = re.search(r"(?<![a-z])(woman|women|female|lady|girl)(?![a-z])", low)
    man = bool(male) and not bool(female)

    tags = [t for t in raw if t.lower() not in GENERIC_PERSON and "school" not in t.lower()]
    adult_idx = next((i for i, t in enumerate(tags) if "adult" in t.lower()), None)
    if adult_idx is None:
        tags.insert(0, "adult man" if man else "adult woman")
    else:
        if adult_idx != 0:
            tags.insert(0, tags.pop(adult_idx))
        first = tags[0].lower()
        if "man" not in first and "woman" not in first:
            tags[0] = tags[0] + (" man" if man else " woman")
    return ", ".join(tags)


def extract_json(text):
    """Достаёт JSON из ответа, снимая ```json-обёртку и болтовню вокруг."""
    t = str(text).strip()
    t = re.sub(r"^```(?:json)?\s*", "", t)
    t = re.sub(r"\s*```$", "", t).strip()
    i, j = t.find("{"), t.rfind("}")
    if i == -1 or j <= i:
        return None
    try:
        return json.loads(t[i:j + 1])
    except Exception:
        return None


def post(url, payload, timeout=300):
    data = json.dumps(payload).encode()
    req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as e:
        detail = e.read().decode(errors="replace")[:600]
        raise RuntimeError(f"HTTP {e.code}: {detail}") from None


def get(url, timeout=30):
    with urllib.request.urlopen(url, timeout=timeout) as r:
        return json.loads(r.read())


def lm_models():
    try:
        return get(LM + "/api/v1/models")
    except Exception as e:
        sys.exit(f"LM Studio не отвечает на {LM}: {e}\nЗапусти сервер: lms server start")


def model_list(resp):
    """LM Studio 0.4 отдаёт {'models':[...]}, старые сборки — {'data':[...]}."""
    return resp.get("models") or resp.get("data") or []


def pick_model(resp, wanted):
    ms = model_list(resp)
    if wanted:
        return wanted
    vision = [m for m in ms if m.get("capabilities", {}).get("vision")
              and "@?" not in str(m.get("key", ""))]
    # Предпочтение: свежая Qwen3.8 (быстрее и точнее в негативе), затем любая загруженная.
    for pref in ("qwen3.8", "qwen3.6"):
        for m in vision:
            if pref in str(m.get("key", "")).lower() and m.get("loaded_instances"):
                return m.get("key")
        for m in vision:
            if pref in str(m.get("key", "")).lower():
                return m.get("key")
    for m in vision:
        if m.get("loaded_instances"):
            return m.get("key")
    if vision:
        return vision[0].get("key")
    sys.exit("Не нашёл vision-модель в LM Studio (ищи capabilities.vision=true). Укажи --model явно.")


def prep_image(image_path, max_side=1280):
    """Уменьшает фото перед отправкой: токены считаются по пикселям, а не по байтам."""
    try:
        from PIL import Image
        with Image.open(image_path) as im:
            if max(im.size) <= max_side:
                return image_path
            im = im.convert("RGB")
            im.thumbnail((max_side, max_side))
            tmp = ROOT / "out" / "tmp"
            tmp.mkdir(parents=True, exist_ok=True)
            dst = tmp / (Path(image_path).stem + f"_{max_side}.png")
            im.save(dst)
            print(f"   фото уменьшено до {im.size[0]}x{im.size[1]} (было больше {max_side})")
            return str(dst)
    except Exception as e:
        print("   уменьшить не удалось:", e)
        return image_path


def describe(image_path, model):
    image_path = prep_image(image_path)
    b64 = base64.b64encode(Path(image_path).read_bytes()).decode()
    base = {
        "model": model,
        "temperature": 0.2,
        # Думающие модели (Gemma4, Qwen3.6) тратят токены на рассуждения:
        # на 400–1000 ответ обрезался, JSON не доходил.
        "max_tokens": 3000,
        "messages": [
            {"role": "system", "content": SYSTEM},
            {"role": "user", "content": [
                {"type": "text", "text": "Describe this photo as JSON. Do not explain."},
                {"type": "image_url", "image_url": {"url": "data:image/png;base64," + b64}},
            ]},
        ],
    }

    last = ""
    for budget in (3000, 6000):
        payload = dict(base)
        payload["max_tokens"] = budget
        try:
            r = post(LM + "/v1/chat/completions", payload)
        except Exception as e:
            last = f"запрос не прошёл: {e}"
            continue
        choice = r["choices"][0]
        u = r.get("usage") or {}
        if u:
            print(f"   токенов: вход {u.get('prompt_tokens')}, выход {u.get('completion_tokens')}")
        content = choice["message"].get("content") or ""
        if not content.strip():
            last = f"пустой content (finish={choice.get('finish_reason')}, бюджет {budget})"
            continue
        data = extract_json(content)
        if data is None:
            last = "не нашёл JSON в ответе"
            continue
        subject = with_adult(data.get("subject", ""))
        if not subject:
            last = "модель вернула пустое описание"
            continue
        neg = ", ".join(scrub_tags(data.get("negative_extra", "")))
        return {
            "subject": subject,
            "negative_extra": neg,
            "notes": str(data.get("notes", "")).strip(),
            "raw": content.strip()[:400],
        }
    return {"subject": "", "negative_extra": "", "notes": f"LLM не дал результата: {last}"}


def unload_all(resp):
    """Выгружает все загруженные инстансы: без этого SDXL не влезет в 16 ГБ."""
    freed = []
    for m in model_list(resp):
        for inst in m.get("loaded_instances", []) or []:
            iid = inst.get("instance_id") or inst.get("id") or m.get("key")
            try:
                post(LM + "/api/v1/models/unload", {"instance_id": iid}, timeout=60)
                freed.append(iid)
            except Exception as e:
                print(f"  не удалось выгрузить {iid}: {e}")
    return freed


def build_negative(negative_extra):
    """Сначала то, что важно подавить на этом фото, потом короткое ядро, потом рамка.

    Порядок важен: CLIP обрезает хвост, поэтому специфичное идёт первым.
    """
    parts, seen = [], set()
    for chunk in (negative_extra or "", NEG_CORE, NEG_GUARD):
        for tag in chunk.split(","):
            t = tag.strip()
            if t and t.lower() not in seen:
                seen.add(t.lower())
                parts.append(t)
    return ", ".join(parts)


def build_positive(style_prefix, subject):
    """Стиль — только триггер и пара слов, чтобы субъект не уезжал за 77 токенов."""
    return style_prefix + subject.strip().rstrip(",") + ", bold outlines, flat colors"


def image_is_landscape(path):
    try:
        from PIL import Image
        with Image.open(path) as im:
            return im.width > im.height
    except Exception:
        return False


def preset_path(name):
    return ROOT / "out" / "presets" / (name + ".json")


def save_run(image_path, workflow, desc, seed, denoise, name):
    """Складывает кадр и его паспорт в папку out/<фото>/."""
    folder = ROOT / "out" / Path(image_path).stem
    folder.mkdir(parents=True, exist_ok=True)
    if name:
        src = OUTPUT_DIR / name
        if src.exists():
            shutil.copy2(src, folder / name)
    book_path = folder / "run.json"
    book = {"workflow": workflow, "runs": []}
    if book_path.exists():
        try:
            book = json.loads(book_path.read_text(encoding="utf-8"))
        except Exception:
            pass
    book.setdefault("runs", []).append({
        "seed": seed, "denoise": denoise, "file": name,
        "subject": desc.get("subject", ""), "negative_extra": desc.get("negative_extra", ""),
    })
    book_path.write_text(json.dumps(book, ensure_ascii=False, indent=2), encoding="utf-8")
    return folder


def comfy_free():
    """Отпускает модели ComfyUI: иначе VLM и SDXL не делят 16 ГБ."""
    try:
        req = urllib.request.Request(COMFY + "/free",
                                     data=json.dumps({"unload_models": True, "free_memory": True}).encode(),
                                     headers={"Content-Type": "application/json"})
        urllib.request.urlopen(req, timeout=90).read()
        print("   ComfyUI освободил память")
    except Exception as e:
        print("   /free не сработал:", e)


def recheck(desc, image_path, model):
    """Смотрит готовый кадр и говорит, что из описания не доехало. None = проверить не вышло."""
    try:
        b64 = base64.b64encode(Path(image_path).read_bytes()).decode()
        ask = ("Compare this image with the description. List ONLY what is clearly missing or wrong "
               "as short tags. Return ONLY JSON: {\"missing\": \"tag1, tag2\"}.\n\n"
               f"Description: {desc.get('subject', '')}")
        r = post(LM + "/v1/chat/completions", {
            "model": model, "max_tokens": 3000, "temperature": 0.1,
            "messages": [{"role": "user", "content": [
                {"type": "text", "text": ask},
                {"type": "image_url", "image_url": {"url": "data:image/png;base64," + b64}},
            ]}],
        }, timeout=600)
        content = r["choices"][0]["message"].get("content") or ""
        data = extract_json(content) or {}
        miss = [t.strip() for t in str(data.get("missing", "")).split(",") if t.strip()]
        return ", ".join(scrub_tags(", ".join(miss)))
    except Exception as e:
        print("   проверка не удалась:", e)
        return None


def generate(workflow, subject, negative_extra, denoise=None, seed=None, landscape=False, style_weight=None, image_name=None):
    api_path = WF_DIR / (workflow + ".api.json")
    if not api_path.exists():
        sys.exit(f"нет файла {api_path} — сначала собери воркфлоу")
    api = json.loads(api_path.read_text(encoding="utf-8"))["prompt"]

    prefix = STYLE_PREFIX.get(workflow)
    if not prefix:
        sys.exit(f"для {workflow} не задан стилевой префикс в STYLE_PREFIX")

    positive = build_positive(prefix, subject)
    negative = build_negative(negative_extra)

    patched = 0
    samplers = []
    if image_name:
        for node in api.values():
            if node["class_type"] == "LoadImage":
                node["inputs"]["image"] = image_name
                print(f"   ????????: {image_name}")
    for node in api.values():
        if node["class_type"] == "LoraLoaderModelOnly" and style_weight is not None:
            if abs(float(node["inputs"].get("strength_model", 0)) - 0.9) < 1e-9:
                node["inputs"]["strength_model"] = float(style_weight)
                print(f"   вес стилевой LoRA: {style_weight}")
        if node["class_type"] == "EmptyLatentImage" and landscape:
            w = node["inputs"].get("width")
            h = node["inputs"].get("height")
            if w and h and w < h:
                node["inputs"]["width"], node["inputs"]["height"] = h, w
                print(f"   разрешение под горизонталь: {h}x{w}")
        if node["class_type"] in ("CLIPTextEncode", "TextEncodeQwenImageEditPlus"):
            t = str(node["inputs"].get("text", ""))
            if t.startswith("photo, photorealistic") or t.startswith("worst quality"):
                node["inputs"]["text"] = negative
                patched += 1
            elif t.strip():
                node["inputs"]["text"] = positive
                patched += 1
        if node["class_type"] == "KSampler":
            samplers.append(node)
    if patched == 0:
        sys.exit("не нашёл текстовых узлов для подстановки")

    for s in samplers:
        if seed is not None:
            s["inputs"]["seed"] = int(seed)
    if denoise is not None and samplers:
        main = max(samplers, key=lambda s: float(s["inputs"].get("denoise", 0)))
        main["inputs"]["denoise"] = float(denoise)

    print("   positive:", positive[:160] + ("…" if len(positive) > 160 else ""))
    print("   negative:", negative[:160] + ("…" if len(negative) > 160 else ""))

    pid = post(COMFY + "/prompt", {"prompt": api})["prompt_id"]
    print(f"  в очереди: {pid}")
    t0 = time.time()
    while time.time() - t0 < 900:
        time.sleep(3)
        try:
            h = get(COMFY + "/history/" + pid)
        except Exception:
            continue
        if pid in h:
            st = h[pid]["status"]["status_str"]
            imgs = [v for o in h[pid]["outputs"].values() for v in o.get("images", [])]
            name = imgs[0]["filename"] if imgs else "?"
            print(f"  {st} за {round(time.time()-t0,1)} с -> {name}")
            return st == "success", name
    print("  таймаут")
    return False, None


def run_batch(args, workflow):
    """Пачка фото в два прохода: сначала все описания, потом выгрузка и генерация.

    Так модель грузится один раз, а не на каждый файл.
    """
    global COMFY
    folder = Path(args.batch)
    if not folder.is_dir():
        sys.exit(f"{folder} не папка")
    files = sorted(p for p in folder.iterdir()
                   if p.suffix.lower() in (".png", ".jpg", ".jpeg", ".webp"))
    if not files:
        sys.exit("в папке нет картинок")

    need_llm = not args.no_llm and not args.from_json
    print(f"пачка: {len(files)} файлов, стиль {workflow}, "
          f"описание: {'LLM' if need_llm else 'без модели'}")

    model = pick_model(lm_models(), args.model) if need_llm else None
    descs = {}
    for i, f in enumerate(files, 1):
        out = ROOT / "out" / (f.stem + ".prompt.json")
        print(f"\n=== [{i}/{len(files)}] {f.name}")
        if args.from_json:
            desc = json.loads(Path(args.from_json).read_text(encoding="utf-8"))
        elif out.exists() and not args.force and need_llm:
            desc = json.loads(out.read_text(encoding="utf-8"))
            print("   из кэша")
        elif args.no_llm:
            desc = {"subject": args.subject or "", "negative_extra": "", "notes": "без LLM"}
        else:
            desc = describe(str(f), model)
            if args.scene:
                new = rewrite_scene(desc, args.scene, model)
                if new:
                    desc = new
        if args.scene and args.no_llm:
            desc["subject"] = (desc["subject"].rstrip(", ") + ", " + args.scene).strip(", ")

        extra = [x.strip() for x in (desc.get("negative_extra") or "").split(",") if x.strip()]
        drop = [x.strip().lower() for x in args.negative_drop.split(",") if x.strip()]
        if drop:
            extra = [x for x in extra if x.lower() not in drop]
        extra += [x.strip() for x in args.negative_extra.split(",") if x.strip()]
        desc["negative_extra"] = ", ".join(dict.fromkeys(extra))
        desc["subject"] = with_adult(desc.get("subject", ""))
        if not desc["subject"]:
            print("   пустое описание — пропускаю")
            continue
        descs[f] = desc
        if need_llm:
            (ROOT / "out").mkdir(exist_ok=True)
            out.write_text(json.dumps(desc, ensure_ascii=False, indent=2), encoding="utf-8")
        print("   subject:", desc["subject"][:110])

    if need_llm and not args.keep_llm:
        freed = unload_all(lm_models())
        print(f"\nвыгружено: {', '.join(freed) if freed else 'нечего'}")

    if args.dry_run or not args.generate:
        print(f"\nготово описаний: {len(descs)} (генерация не запускалась)")
        return

    COMFY = resolve_comfy(args.comfy)
    total = 0
    for i, (f, desc) in enumerate(descs.items(), 1):
        for v in range(max(1, args.variants)):
            sd = (args.seed + v) if args.seed is not None else random.randint(1, 2_000_000_000)
            print(f"\n=== [{i}/{len(descs)}] {f.name}, вариант {v + 1}, seed {sd}")
            generate(workflow, desc["subject"], desc.get("negative_extra", ""),
                     denoise=args.denoise, seed=sd,
                     landscape=image_is_landscape(str(f)), style_weight=args.style_weight,
                     image_name=ensure_input(str(f)))
            total += 1
    print(f"\nвсего кадров: {total}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--image")
    ap.add_argument("--batch", help="папка с фото: обработать все по очереди")
    ap.add_argument("--workflow")
    ap.add_argument("--style", help="короткое имя: thepit, comic, jp, bimbo, gta")
    ap.add_argument("--scene", help="переписать сцену, сохранив человека (напр. 'beach at sunset')")
    ap.add_argument("--variants", type=int, default=1, help="сколько кадров на одно фото")
    ap.add_argument("--model")
    ap.add_argument("--subject")
    ap.add_argument("--no-llm", action="store_true")
    ap.add_argument("--from-json", help="взять описание из готового .prompt.json")
    ap.add_argument("--preset", help="взять описание из библиотеки out\\presets\\<имя>.json")
    ap.add_argument("--save-preset", help="сохранить текущее описание в библиотеку под этим именем")
    ap.add_argument("--recheck", action="store_true",
                    help="после генерации проверить кадр моделью и один раз дописать негатив")
    ap.add_argument("--keep-llm", action="store_true")
    ap.add_argument("--comfy", help="адрес ComfyUI, по умолчанию автоопределение")
    ap.add_argument("--negative-extra", default="", help="дописать в негатив вручную")
    ap.add_argument("--negative-drop", default="", help="вычеркнуть слова из негатива LLM")
    ap.add_argument("--denoise", type=float, help="перебить denoise основного сэмплера")
    ap.add_argument("--style-weight", type=float, help="перебить вес стилевой LoRA (сейчас 0.9)")
    ap.add_argument("--seed", type=int)
    ap.add_argument("--force", action="store_true", help="не брать описание из кэша")
    ap.add_argument("--dry-run", action="store_true", help="показать промпт, ничего не запускать")
    ap.add_argument("--generate", action="store_true")
    a = ap.parse_args()

    workflow = resolve_workflow(a.workflow, a.style)
    if a.batch:
        run_batch(a, workflow)
        return
    if not a.image:
        sys.exit("нужен --image или --batch")
    a.workflow = workflow
    landscape = image_is_landscape(a.image)

    global COMFY
    if a.generate and not a.dry_run:
        COMFY = resolve_comfy(a.comfy)

    out = ROOT / "out" / (Path(a.image).stem + ".prompt.json")
    t_desc = t_unload = 0.0
    desc = None
    came_from_llm = False

    if a.preset:
        p = preset_path(a.preset)
        if not p.exists():
            sys.exit(f"нет пресета {p}")
        desc = json.loads(p.read_text(encoding="utf-8"))
        print(f"0) описание из пресета {p.name}")
    elif a.from_json:
        desc = json.loads(Path(a.from_json).read_text(encoding="utf-8"))
        print(f"0) описание из {a.from_json}")
    elif out.exists() and not a.force and not a.no_llm:
        desc = json.loads(out.read_text(encoding="utf-8"))
        print(f"0) описание из кэша {out} (--force чтобы пересмотреть фото)")
    elif a.no_llm:
        if not a.subject:
            sys.exit("--no-llm требует --subject")
        desc = {"subject": a.subject, "negative_extra": "", "notes": "без LLM"}
    else:
        models = lm_models()
        model = pick_model(models, a.model)
        print(f"1) смотрю фото моделью {model}")
        t = time.time()
        desc = describe(a.image, model)
        t_desc = time.time() - t
        came_from_llm = True
        if a.scene:
            print(f"   сцена: {a.scene}")
            new = rewrite_scene(desc, a.scene, model)
            if new:
                new["negative_extra"] = ", ".join(
                    dict.fromkeys([x.strip() for x in (desc.get("negative_extra") or "").split(",") if x.strip()]
                                  + [x.strip() for x in (new.get("negative_extra") or "").split(",") if x.strip()]))
                desc = new
        if not a.keep_llm:
            t = time.time()
            freed = unload_all(lm_models())
            t_unload = time.time() - t
            print(f"2) выгружено: {', '.join(freed) if freed else 'нечего'} ({t_unload:.1f} с)")

    if a.scene and a.no_llm:
        desc["subject"] = (desc["subject"].rstrip(", ") + ", " + a.scene).strip(", ")

    extra = [x.strip() for x in (desc.get("negative_extra") or "").split(",") if x.strip()]
    drop = [x.strip().lower() for x in a.negative_drop.split(",") if x.strip()]
    if drop:
        extra = [x for x in extra if x.lower() not in drop]
    extra += [x.strip() for x in a.negative_extra.split(",") if x.strip()]
    desc["negative_extra"] = ", ".join(dict.fromkeys(extra))
    desc["subject"] = with_adult(desc.get("subject", ""))

    print("   subject:", desc["subject"] or "(пусто)")
    if desc.get("negative_extra"):
        print("   negative_extra:", desc["negative_extra"])
    if desc.get("notes"):
        print("   заметка:", desc["notes"])
    if a.denoise is not None:
        print(f"   denoise: {a.denoise}")
    if a.seed is not None:
        print(f"   seed: {a.seed}")

    if not desc["subject"].strip():
        sys.exit("описание не получилось — генерацию не запускаю. Проверь заметку выше.")

    if came_from_llm and not a.dry_run:
        (ROOT / "out").mkdir(exist_ok=True)
        out.write_text(json.dumps(desc, ensure_ascii=False, indent=2), encoding="utf-8")
        print("   сохранено:", out)
    else:
        print("   кэш не тронут")

    if a.save_preset:
        preset_path(a.save_preset).parent.mkdir(parents=True, exist_ok=True)
        preset_path(a.save_preset).write_text(json.dumps(desc, ensure_ascii=False, indent=2), encoding="utf-8")
        print("   пресет:", preset_path(a.save_preset).name)

    if a.dry_run:
        prefix = STYLE_PREFIX.get(a.workflow, "")
        print("\nитоговый промпт:")
        print(" ", build_positive(prefix, desc["subject"]))
        print("\nитоговый негатив:")
        print(" ", build_negative(desc["negative_extra"]))
        return

    if a.generate:
        n = max(1, a.variants)
        landscape = image_is_landscape(a.image)
        print(f"3) генерация в ComfyUI, вариантов: {n}")
        t = time.time()
        seeded = a.seed is not None
        ok_all = True
        for i in range(n):
            sd = (a.seed + i) if seeded else random.randint(1, 2_000_000_000)
            print(f"   вариант {i + 1}/{n}, seed {sd}")
            ok, name = generate(a.workflow, desc["subject"], desc.get("negative_extra", ""),
                                denoise=a.denoise, seed=sd, landscape=landscape, style_weight=a.style_weight, image_name=ensure_input(a.image))
            ok_all &= ok
            if name:
                save_run(a.image, a.workflow, desc, sd, a.denoise, name)
            if a.recheck and i == 0 and name:
                print("   проверка кадра моделью")
                comfy_free()
                models = lm_models()
                model = pick_model(models, a.model)
                miss = recheck(desc, OUTPUT_DIR / name, model)
                if not a.keep_llm:
                    unload_all(lm_models())
                if miss is None:
                    print("   проверку выполнить не удалось — повторный проход пропущен")
                elif miss:
                    print("   не доехало:", miss)
                    # Промах = чего не хватает на кадре, поэтому усиливаем ПРОМПТ, а не негатив.
                    subj = desc.get("subject", "")
                    low = subj.lower()
                    add = [x.strip() for x in miss.split(",")
                           if x.strip() and x.strip().lower() not in low]
                    if add:
                        desc["subject"] = ", ".join([subj] + add)
                        print("   промпт дополнен:", ", ".join(add))
                        print("   повторный проход с усиленным промптом")
                        ok, name2 = generate(a.workflow, desc["subject"], desc.get("negative_extra", ""),
                                             denoise=a.denoise, seed=sd, landscape=landscape, style_weight=a.style_weight, image_name=ensure_input(a.image))
                        ok_all &= ok
                        if name2:
                            save_run(a.image, a.workflow, desc, sd, a.denoise, name2)
                    else:
                        # Слово уже в промпте, но не отрисовалось — усиливаем вес.
                        subj2, bumped = subj, []
                        for tag in [x.strip() for x in miss.split(",") if x.strip()]:
                            if tag.lower() in subj2.lower() and f"({tag}" not in subj2:
                                subj2 = re.sub(re.escape(tag), f"(({tag}:1.2))", subj2,
                                               count=1, flags=re.IGNORECASE)
                                bumped.append(tag)
                        if bumped:
                            desc["subject"] = subj2
                            print("   усилено весом:", ", ".join(bumped))
                            ok, name2 = generate(a.workflow, desc["subject"], desc.get("negative_extra", ""),
                                                 denoise=a.denoise, seed=sd, landscape=landscape, style_weight=a.style_weight, image_name=ensure_input(a.image))
                            ok_all &= ok
                            if name2:
                                save_run(a.image, a.workflow, desc, sd, a.denoise, name2)
                        else:
                            print("   повтор не изменит результата — пропускаю")
                else:
                    print("   всё на месте")
        t_gen = time.time() - t
        print(f"\nитого: описание {t_desc:.0f} с + выгрузка {t_unload:.1f} с + генерация {t_gen:.0f} с "
              f"= {t_desc + t_unload + t_gen:.0f} с")
        sys.exit(0 if ok_all else 1)


if __name__ == "__main__":
    main()
