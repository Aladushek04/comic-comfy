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
  --unload-all выгрузить из LM Studio все модели, а не только свою
  --dry-run    показать промпт, ничего не загружая: нужен кэш/пресет/--no-llm
"""
import argparse
import base64
import hashlib
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

# Запасной вариант: основной стилевой блок приходит из графа (meta.positive_template),
# эту таблицу используем только для старых .api.json без meta.
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

STYLE_ALIAS = {
    "thepit": "07_photo_thepit",    "comic": "07_photo_comic",
    "jp": "07_photo_jp",
    "bimbo": "07_photo_bimbo",
    "gta": "08_photo_gta",
}
OUTPUT_DIR = Path(r"G:\Comfy-Desktop\ComfyUI-Shared\output\comic")
INPUT_DIR = Path(r"G:\Comfy-Desktop\ComfyUI-Shared\input")


_FINGERPRINTS = {}


def fingerprint(image_path):
    """Отпечаток содержимого (12 hex). Имя файла ничего не гарантирует.

    same.png из двух папок — разные фотографии с одним именем, поэтому ключом
    должен быть хэш байтов, а не stem. Кэш по (mtime, size) экономит чтение:
    при повторном вызове в одном прогоне файл уже посчитан.
    """
    src = Path(image_path)
    st = src.stat()
    key = (str(src.resolve()), st.st_mtime_ns, st.st_size)
    if key not in _FINGERPRINTS:
        h = hashlib.sha1()
        with open(src, "rb") as f:
            for block in iter(lambda: f.read(1 << 20), b""):
                h.update(block)
        _FINGERPRINTS[key] = h.hexdigest()[:12]
    return _FINGERPRINTS[key]


def image_tag(image_path):
    """Читаемое и уникальное имя: <stem>_<хэш>."""
    src = Path(image_path)
    return f"{src.stem}_{fingerprint(src)}"


def ensure_input(image_path):
    """Кладёт фото в общую папку input и возвращает имя для LoadImage.

    Имя включает отпечаток содержимого, поэтому два разных same.png больше не
    делят один файл, и неважно, чей mtime новее: у разных фото разные имена.
    Одинаковые байты дают одно имя и одну копию.
    """
    src = Path(image_path)
    name = image_tag(src) + src.suffix.lower()
    dst = INPUT_DIR / name
    if not dst.exists() or dst.stat().st_size != src.stat().st_size:
        INPUT_DIR.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)
    return name


def scene_slug(scene):
    if not scene:
        return ""
    return re.sub(r"[^a-z0-9]+", "-", str(scene).lower()).strip("-")[:40].strip("-")


def cache_path(image_path, scene=None):
    """Кэш привязан к содержимому файла и к сцене.

    Раньше ключом был stem: same.png и same.jpg попадали в один файл, а --scene
    при попадании в кэш молча игнорировался. Теперь другая сцена — другой файл.
    """
    tag = image_tag(image_path)
    slug = scene_slug(scene)
    if slug:
        tag += "__" + slug
    return ROOT / "out" / (tag + ".prompt.json")


def read_cache(image_path, scene=None):
    """Описание с диска, если оно снято с этого же содержимого. Иначе None."""
    p = cache_path(image_path, scene)
    if not p.exists():
        return None
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return None
    if data.get("sha1") and data["sha1"] != fingerprint(image_path):
        return None
    return data


def write_cache(image_path, desc, scene=None):
    """Пишет описание вместе с отпечатком и сценой — кэш сам себя проверяет."""
    p = cache_path(image_path, scene)
    p.parent.mkdir(parents=True, exist_ok=True)
    data = dict(desc)
    data["sha1"] = fingerprint(image_path)
    data["source"] = str(image_path)
    data["scene"] = scene or ""
    p.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return p


def legacy_cache_hint(image_path):
    """Объясняет, почему старый out\\<фото>.prompt.json не используется."""
    legacy = ROOT / "out" / (Path(image_path).stem + ".prompt.json")
    if legacy.exists():
        print(f"   старый кэш {legacy.name} не беру: в нём нет отпечатка файла")
        return True
    return False


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

# ComfyUI режет текст на чанки по 77 токенов и кодирует их подряд (SDTokenizer: batched_tokens),
# поэтому длинный промпт не обрезается. Проверено токенизатором: GTA-блок — 1 чанк,
# со субъектом — 2, 120 тегов — 7. Важное всё равно ставим в начало: дальние теги влияют слабее.
NEG_CORE = ("photorealistic, 3d render, bad anatomy, bad hands, extra fingers, "
            "watermark, text, signature, blurry, low quality")

NEG_GUARD = "child, loli, shota, underage"

# Плейсхолдер субъекта в шаблоне промпта из build_workflows.py.
SUBJECT_SLOT = "{{subject}}"

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
            dst = tmp / f"{image_tag(image_path)}_{max_side}.png"
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
    """Выгружает все загруженные инстансы: без этого SDXL не влезет в 16 ГБ.

    Трогает и чужие задачи, поэтому по умолчанию не используется — только --unload-all.
    """
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


def unload_model(resp, model):
    """Выгружает инстансы только этой модели: чужие задачи остаются как были.

    Память под SDXL нужна, но выбивать из LM Studio модель, загруженную другой
    задачей, — не наше дело: о такой модели сообщаем и оставляем её в покое.
    """
    freed, left = [], set()
    for m in model_list(resp):
        inst = m.get("loaded_instances") or []
        if not inst:
            continue
        if m.get("key") == model:
            for i in inst:
                iid = i.get("instance_id") or i.get("id") or m.get("key")
                try:
                    post(LM + "/api/v1/models/unload", {"instance_id": iid}, timeout=60)
                    freed.append(iid)
                except Exception as e:
                    print(f"  не удалось выгрузить {iid}: {e}")
        else:
            left.add(str(m.get("key")))
    if left:
        print("  остались загруженными (не трогаю):", ", ".join(sorted(left)))
        print("  если SDXL не влезет в память — выгрузи их сам или добавь --unload-all")
    return freed


def free_llm(model, unload_everything=False):
    """Освобождает память под SDXL. По умолчанию — только модель этого прогона."""
    if unload_everything:
        return unload_all(lm_models())
    return unload_model(lm_models(), model)


def join_tags(*chunks):
    """Склеивает теги без дублей, сохраняя порядок."""
    parts, seen = [], set()
    for chunk in chunks:
        for tag in str(chunk or "").split(","):
            t = tag.strip()
            if t and t.lower() not in seen:
                seen.add(t.lower())
                parts.append(t)
    return ", ".join(parts)


def compose_negative(negative_extra, meta=None):
    """Сначала то, что важно подавить на этом фото, потом негатив сборки, потом рамка.

    Негатив берётся из графа: у GTA там «night, rain, dark», у bimbo — «white lenses,
    opaque lenses, grey skin». Раньше CLI подменял его своим коротким ядром целиком.
    """
    base = (meta or {}).get("negative") or NEG_CORE
    return join_tags(negative_extra, base, NEG_GUARD)


def compose_positive(meta, subject, workflow=""):
    """Промпт = шаблон сборки с подставленным субъектом.

    Шаблон приходит из графа (meta.positive_template), поэтому CLI выдаёт ровно тот
    промпт, что и панель, вместе со стилевыми настройками сборки. Если meta нет
    (старый .api.json) — собираем из таблицы STYLE_PREFIX, как раньше.
    """
    tpl = (meta or {}).get("positive_template") or ""
    if tpl:
        return join_tags(tpl.replace(SUBJECT_SLOT, str(subject).strip().rstrip(",")))
    return join_tags(STYLE_PREFIX.get(workflow, ""), subject, "bold outlines, flat colors")


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
    """Складывает кадр и его паспорт в папку out/<фото>_<хэш>/."""
    folder = ROOT / "out" / image_tag(image_path)
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


def print_comfy_error(status):
    """Почему ComfyUI не отдал кадр: без этого провал выглядел как успех."""
    for event, data in status.get("messages") or []:
        if event in ("execution_error", "execution_interrupted"):
            data = data or {}
            node = data.get("node_type") or data.get("node_id") or "?"
            msg = str(data.get("exception_message") or event).strip()
            print(f"  ошибка узла {node}: {msg[:400]}")
            return


def load_workflow(workflow):
    """Граф и его стилевые настройки. meta пишет build_workflows.py из самого графа."""
    path = WF_DIR / (workflow + ".api.json")
    if not path.exists():
        sys.exit(f"нет файла {path} — сначала собери воркфлоу")
    data = json.loads(path.read_text(encoding="utf-8"))
    return data["prompt"], data.get("meta") or {}


def generate(workflow, subject, negative_extra, denoise=None, seed=None, landscape=False, style_weight=None, image_name=None):
    api, meta = load_workflow(workflow)

    prefix = meta.get("positive_template") or STYLE_PREFIX.get(workflow)
    if not prefix:
        sys.exit(f"для {workflow} не задан стилевой блок: пересобери воркфлоу")

    positive = compose_positive(meta, subject, workflow)
    negative = compose_negative(negative_extra, meta)

    patched = 0
    samplers = []
    style_lora = meta.get("style_lora") or ""
    weight_set = False
    if image_name:
        for node in api.values():
            if node["class_type"] == "LoadImage":
                node["inputs"]["image"] = image_name
                print(f"   исходник: {image_name}")
    for node in api.values():
        if node["class_type"] == "LoraLoaderModelOnly" and style_weight is not None:
            # Стилевая LoRA ищется по имени из meta, а не по «весу 0.9»: у bimbo 0.6,
            # у GTA 0.85, поэтому прежняя проверка молча ничего не меняла.
            if style_lora and node["inputs"].get("lora_name") == style_lora:
                old = node["inputs"].get("strength_model")
                node["inputs"]["strength_model"] = float(style_weight)
                weight_set = True
                print(f"   вес стилевой LoRA {style_lora}: {old} -> {style_weight}")
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
    if style_weight is not None and not weight_set:
        sys.exit(f"--style-weight: не понял, какая LoRA стилевая в {workflow} "
                 f"(meta.style_lora={style_lora!r}) — пересобери воркфлоу")

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
        if pid not in h:
            continue
        hist = h[pid]
        status = hist.get("status") or {}
        st = status.get("status_str", "?")
        # Успех = есть файл в outputs (SaveImage, PreviewImage, видео). Любой другой узел
        # с полем filename тоже считается: важен результат, а не имя ключа.
        files = [v for o in (hist.get("outputs") or {}).values()
                 for items in (o or {}).values() if isinstance(items, list)
                 for v in items if isinstance(v, dict) and v.get("filename")]
        name = files[0]["filename"] if files else None
        if st != "success":
            print(f"  {st} за {round(time.time()-t0,1)} с")
            print_comfy_error(status)
            return False, None
        if not name:
            # Пустой outputs при status_str=success: граф без SaveImage, отмена или
            # пустой выход. Кадра нет — это провал, а не успех.
            print(f"  success за {round(time.time()-t0,1)} с, но изображения нет — считаю провалом")
            print_comfy_error(status)
            return False, None
        print(f"  success за {round(time.time()-t0,1)} с -> {name}")
        return True, name
    print("  таймаут: кадр не появился")
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

    # --dry-run модель не грузит: значит и опрашивать LM Studio незачем. В остальных
    # случаях модель спрашиваем только когда действительно нужно описание.
    model = None
    described = 0
    descs = {}
    for i, f in enumerate(files, 1):
        print(f"\n=== [{i}/{len(files)}] {f.name}")
        fresh = False
        cached = read_cache(str(f), args.scene) if need_llm and not args.force else None
        if args.from_json:
            desc = json.loads(Path(args.from_json).read_text(encoding="utf-8"))
        elif cached:
            desc = cached
            print(f"   из кэша {cache_path(str(f), args.scene).name}")
        elif args.no_llm:
            desc = {"subject": args.subject or "", "negative_extra": "", "notes": "без LLM"}
        elif args.dry_run:
            print("   в кэше нет описания, а --dry-run модель не грузит — пропускаю")
            continue
        else:
            if model is None:
                model = pick_model(lm_models(), args.model)
            desc = describe(str(f), model)
            described += 1
            fresh = True
            if args.scene:
                new = rewrite_scene(desc, args.scene, model)
                if new:
                    desc = new
        if args.scene and args.no_llm:
            desc["subject"] = (desc["subject"].rstrip(", ") + ", " + args.scene).strip(", ")

        desc["subject"] = with_adult(desc.get("subject", ""))
        if not desc["subject"]:
            print("   пустое описание — пропускаю")
            continue
        # Кэш — до подмешивания ключей командной строки, иначе флаги прогона
        # остаются в описании навсегда.
        if fresh:
            print("   сохранено:", write_cache(str(f), desc, args.scene).name)

        extra = [x.strip() for x in (desc.get("negative_extra") or "").split(",") if x.strip()]
        drop = [x.strip().lower() for x in args.negative_drop.split(",") if x.strip()]
        if drop:
            extra = [x for x in extra if x.lower() not in drop]
        extra += [x.strip() for x in args.negative_extra.split(",") if x.strip()]
        desc["negative_extra"] = ", ".join(dict.fromkeys(extra))
        descs[f] = desc
        print("   subject:", desc["subject"][:110])

    if need_llm and described and model and not args.keep_llm:
        freed = free_llm(model, args.unload_all)
        print(f"\nвыгружено: {', '.join(freed) if freed else 'нечего'}")

    if args.dry_run or not args.generate:
        print(f"\nготово описаний: {len(descs)} (генерация не запускалась)")
        if args.dry_run and not descs:
            print("нечего показать: ни одного описания")
            return 1
        return 0
    if not descs:
        print("\nни одного описания — генерировать нечего")
        return 1

    COMFY = resolve_comfy(args.comfy)
    frames, failed, folders = 0, 0, []
    for i, (f, desc) in enumerate(descs.items(), 1):
        for v in range(max(1, args.variants)):
            sd = (args.seed + v) if args.seed is not None else random.randint(1, 2_000_000_000)
            print(f"\n=== [{i}/{len(descs)}] {f.name}, вариант {v + 1}, seed {sd}")
            ok, name = generate(workflow, desc["subject"], desc.get("negative_extra", ""),
                                denoise=args.denoise, seed=sd,
                                landscape=image_is_landscape(str(f)), style_weight=args.style_weight,
                                image_name=ensure_input(str(f)))
            if not ok:
                failed += 1
                print("   кадр не получен")
                continue
            frames += 1
            folders.append(save_run(str(f), workflow, desc, sd, args.denoise, name))
    print(f"\nкадров: {frames}, провалов: {failed}")
    if folders:
        print("  результаты:", ", ".join(sorted({p.name for p in folders})))
    # Ноль кадров или хоть один провал — не успех: код возврата это покажет.
    return 0 if frames and not failed else 1


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
    ap.add_argument("--unload-all", action="store_true",
                    help="выгрузить из LM Studio все модели, а не только свою (трогает чужие задачи)")
    ap.add_argument("--comfy", help="адрес ComfyUI, по умолчанию автоопределение")
    ap.add_argument("--negative-extra", default="", help="дописать в негатив вручную")
    ap.add_argument("--negative-drop", default="", help="вычеркнуть слова из негатива LLM")
    ap.add_argument("--denoise", type=float, help="перебить denoise основного сэмплера")
    ap.add_argument("--style-weight", type=float,
                    help="перебить вес стилевой LoRA (по умолчанию как в сборке: 0.9 / 0.85 / 0.6)")
    ap.add_argument("--seed", type=int)
    ap.add_argument("--force", action="store_true", help="не брать описание из кэша")
    ap.add_argument("--dry-run", action="store_true", help="показать промпт, ничего не запускать")
    ap.add_argument("--generate", action="store_true")
    a = ap.parse_args()

    workflow = resolve_workflow(a.workflow, a.style)
    if a.batch:
        sys.exit(run_batch(a, workflow))
        return
    if not a.image:
        sys.exit("нужен --image или --batch")
    a.workflow = workflow
    landscape = image_is_landscape(a.image)

    global COMFY
    if a.generate and not a.dry_run:
        COMFY = resolve_comfy(a.comfy)

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
    elif a.no_llm:
        if not a.subject:
            sys.exit("--no-llm требует --subject")
        desc = {"subject": a.subject, "negative_extra": "", "notes": "без LLM"}
    else:
        # Ключ кэша — содержимое файла и сцена, а не stem: same.png и same.jpg
        # больше не делят описание, а --scene не теряется при попадании в кэш.
        cached = None if a.force else read_cache(a.image, a.scene)
        if cached:
            desc = cached
            print(f"0) описание из кэша {cache_path(a.image, a.scene).name} "
                  f"(--force чтобы пересмотреть фото)")
        elif a.dry_run:
            # --dry-run обещает «ничего не запускать», а просмотр фото поднимает VLM
            # в LM Studio и потом выгружает модели. Показывать нечего — так и скажем.
            what = "описания для этой сцены" if a.scene else "описания"
            hint = " (--force отключает кэш — убери его)" if a.force else ""
            legacy_cache_hint(a.image)
            print(f"--dry-run ничего не загружает, а в кэше нет {what}{hint}:\n"
                  f"  {cache_path(a.image, a.scene)}\n"
                  "Дай готовое описание: --preset имя, --from-json файл или "
                  "--no-llm --subject \"…\";\n"
                  "либо убери --dry-run — тогда VLM посмотрит фото (это загрузка памяти).")
            sys.exit(1)
        else:
            legacy_cache_hint(a.image)
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
                freed = free_llm(model, a.unload_all)
                t_unload = time.time() - t
                print(f"2) выгружено: {', '.join(freed) if freed else 'нечего'} ({t_unload:.1f} с)")

    if a.scene and a.no_llm:
        desc["subject"] = (desc["subject"].rstrip(", ") + ", " + a.scene).strip(", ")

    # Кэш пишем до подмешивания ключей командной строки: иначе --negative-extra
    # остаётся в описании навсегда и всплывает в следующем прогоне без флага.
    desc["subject"] = with_adult(desc.get("subject", ""))
    if came_from_llm and not a.dry_run and desc["subject"].strip():
        print("   сохранено:", write_cache(a.image, desc, a.scene))
    elif not a.dry_run:
        print("   кэш не тронут")

    extra = [x.strip() for x in (desc.get("negative_extra") or "").split(",") if x.strip()]
    drop = [x.strip().lower() for x in a.negative_drop.split(",") if x.strip()]
    if drop:
        extra = [x for x in extra if x.lower() not in drop]
    extra += [x.strip() for x in a.negative_extra.split(",") if x.strip()]
    desc["negative_extra"] = ", ".join(dict.fromkeys(extra))

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

    if a.save_preset:
        preset_path(a.save_preset).parent.mkdir(parents=True, exist_ok=True)
        preset_path(a.save_preset).write_text(json.dumps(desc, ensure_ascii=False, indent=2), encoding="utf-8")
        print("   пресет:", preset_path(a.save_preset).name)

    if a.dry_run:
        _, meta = load_workflow(a.workflow)
        print("\nитоговый промпт:")
        print(" ", compose_positive(meta, desc["subject"], a.workflow))
        print("\nитоговый негатив:")
        print(" ", compose_negative(desc["negative_extra"], meta))
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
                    free_llm(model, a.unload_all)
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
