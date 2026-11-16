"""Разбор фото теггером: детали, которых не хватает описанию VLM.

Две ONNX-модели, обе на CPU:
  * `swinv2` (446 МБ) — основной голос: тело, одежда, украшения, кадр;
  * `eva02-large` (1.2 ГБ) — только размер груди: у него градация точнее
    (`huge breasts` он видит там, где swinv2 говорит `large`).

Теггер не заменяет описание, а дополняет: VLM даёт материал, свет и связность,
теггер — то, на что у VLM не хватает слов (размер, ареолы, тип каблука, состояние
молнии, украшения). Пропорций он не даёт: `thick thighs`, `wide hips` и `thighs`
WD14 не выдавал ни на одном из 17 фото (замер — `tools\\tagger_bench.py`).

Выключатель: `COMIC_TAGGER=off`. Каталог моделей: `COMIC_TAGGER_DIR`,
по умолчанию `out\\models\\tagger`. Нет файлов или onnxruntime — `tags()` молча
возвращает пусто, прогон из-за теггера не падает.
"""
import csv
import os
import re
import sys
from pathlib import Path

import settings

ROOT = settings.ROOT

# Репозитории для скачивания (этим пользуется стенд, --download).
REPOS = {
    "swinv2": "SmilingWolf/wd-swinv2-tagger-v3",
    "eva02-large": "SmilingWolf/wd-eva02-large-tagger-v3",
    "vit-large": "SmilingWolf/wd-vit-large-tagger-v3",
}

BASE_MODEL = "swinv2"
SIZE_MODEL = "eva02-large"
VERSION = "swin+eva/1"

BASE_THRESHOLD = 0.25     # на этом пороге swinv2 даёт ~32 тега на фото
SIZE_THRESHOLD = 0.35
MAX_ADDONS = 12

# Порядок важен: берём первый найденный размер, двойных в subject быть не должно.
SIZES = ("huge breasts", "large breasts", "medium breasts", "small breasts")

# Когда вообще звать вторую модель: без груди в кадре её размер не нужен,
# а eva02 стоит ~2 с на фото.
BREAST_HINT = ("breast", "cleavage", "nipple", "areola", "nude", "topless", "bare")

# Полки, которые идут в промпт. Сцена, свет и поза остаются за VLM.
KEEP_BUCKETS = ("анатомия", "одежда/материал", "аксессуар", "кадр")

# Порядок проверки полок. Поза и сцена идут первыми: `hands on own hips` должно
# попасть в позу, а не в анатомию из-за слова `hips`, — иначе в промпт утечёт поза,
# которую и так описал VLM.
BUCKET_ORDER = ("поза", "сцена", "свет", "кадр", "аксессуар", "одежда/материал", "анатомия")

BUCKETS = {
    "анатомия": ("breast", "areola", "nipple", "hip", "waist", "thigh", "belly",
                 "navel", "skin", "collarbone", "shoulder", "butt", "ass", "vulva",
                 "pussy", "pubic", "leg", "thighhigh", "cleavage", "midriff", "back"),
    "одежда/материал": ("stocking", "lace", "sheer", "denim", "leather", "silk", "satin",
                        "cotton", "knit", "dress", "shirt", "skirt", "bra", "panties",
                        "boot", "heel", "shoe", "jacket", "coat", "glove", "belt",
                        "button", "zipper", "fabric", "corset", "garter", "pantyhose",
                        "lingerie", "uniform", "swimsuit", "bikini", "bodysuit",
                        "jumpsuit", "robe", "stockings", "sleeves", "thong", "briefs"),
    "аксессуар": ("necklace", "earring", "glasses", "ring", "watch", "bracelet", "tattoo",
                  "piercing", "choker", "hat", "scarf", "bag", "pendant", "jewelry",
                  "hairband", "hairpin", "tiara", "crown"),
    "поза": ("sitting", "standing", "kneeling", "lying", "crossed", "hand", "arm",
             "leaning", "spread", "holding", "bent"),
    "сцена": ("background", "room", "bed", "sofa", "window", "wall", "carpet", "street",
              "beach", "outdoors", "indoors", "curtain", "mirror", "table", "door",
              "pillow", "flower", "plant", "blinds"),
    "свет": ("light", "sunlight", "shadow", "daylight", "lamp", "glow", "backlight",
             "candle", "neon"),
    "кадр": ("close-up", "closeup", "portrait", "full body", "cowboy shot", "upper body",
             "from side", "from behind", "from above", "pov", "angle"),
}

# Служебные теги: есть почти на каждом фото и о деталях ничего не говорят.
META = {"1girl", "1boy", "2girls", "2boys", "multiple girls", "multiple boys", "solo",
        "solo focus", "realistic", "photorealistic", "photo background", "highres",
        "absurdres", "best quality", "high quality", "looking at viewer", "commentary",
        "watermark", "signature", "web address", "artist name", "english text", "nsfw",
        "explicit", "questionable", "sensitive", "general", "adult", "mature female"}

# Мусор, который нельзя пускать в промпт Illustrious: тянет чужие стили и персонажей.
JUNK = {"cosplay", "parody", "cup", "symbol", "logo", "twitter username", "6+girls",
        "speech bubble", "censored", "artist name"}

PERSON = {"adult woman", "adult man", "woman", "man", "female", "male", "lady", "guy"}

# Места, которые есть на любом кадре с человеком. Сами по себе они не деталь, а
# слот: `skin`, `shoulder`, `hip`, `arm` только разбавляют промпт. Осмысленные
# уточнения (`bare shoulders`, `wide hips`) сюда не попадают — сравнивается тег целиком.
GENERIC = {"skin", "shoulder", "shoulders", "arm", "arms", "hand", "hands", "leg",
           "legs", "hip", "hips", "waist", "back", "belly", "body", "torso"}

# Слоты одежды: в одном слоте вещь одна. Если описание уже назвало платье,
# теггер не должен добавлять к нему халат — это не деталь, а другая одежда.
SLOTS = {
    "body": ("dress", "shirt", "skirt", "blouse", "top", "jacket", "coat", "suit",
             "catsuit", "bodysuit", "jumpsuit", "swimsuit", "bikini", "uniform",
             "robe", "gown", "apron", "sweater", "hoodie", "camisole", "corset", "vest"),
    "legs": ("stockings", "thighhighs", "thighhigh", "pantyhose", "socks", "sock",
             "garter", "tights"),
    "feet": ("boots", "boot", "shoes", "shoe", "heels", "heel", "sandals", "sandal",
             "sneakers", "slippers"),
    "underwear": ("bra", "panties", "underwear", "lingerie", "thong"),
}


def slot_of(tag):
    """Слот одежды по главному слову: `white_dress` -> body, `high_heels` -> feet."""
    words = norm(tag).split()
    for word in reversed(words):
        for slot, keys in SLOTS.items():
            if any(singular(word) == k or (len(k) >= 4 and word.endswith(k)) for k in keys):
                return slot
    return ""

STOP = {"a", "an", "the", "with", "and", "of", "on", "in", "at", "to", "her", "his",
        "its", "from", "by", "very", "slightly", "some", "resting"}


def disabled():
    return (os.environ.get("COMIC_TAGGER") or "").strip().lower() in ("off", "0", "no", "false")


def model_dir():
    return Path(os.environ.get("COMIC_TAGGER_DIR") or (ROOT / "out" / "models" / "tagger"))


def files(model):
    return model_dir() / model / "model.onnx", model_dir() / model / "selected_tags.csv"


def available():
    return all(p.exists() for model in (BASE_MODEL, SIZE_MODEL) for p in files(model))


def ensure(model, download=False):
    """Файлы модели на диске. Без download ничего не качаем: сеть — явное решение."""
    onnx, tags_csv = files(model)
    if onnx.exists() and tags_csv.exists():
        return onnx, tags_csv
    if not download:
        raise FileNotFoundError(f"нет модели {model} в {onnx.parent}")
    from huggingface_hub import hf_hub_download
    onnx.parent.mkdir(parents=True, exist_ok=True)
    for name in ("model.onnx", "selected_tags.csv"):
        hf_hub_download(repo_id=REPOS[model], filename=name, local_dir=str(onnx.parent))
    return onnx, tags_csv


_SESSIONS = {}


def _session(model):
    """Сессия onnxruntime по имени модели. Держим открытой: загрузка дороже прохода."""
    if model in _SESSIONS:
        return _SESSIONS[model]
    import onnxruntime as ort
    onnx, _ = files(model)
    sess = ort.InferenceSession(str(onnx), providers=["CPUExecutionProvider"])
    inp = sess.get_inputs()[0]
    size = int(inp.shape[1]) if len(inp.shape) == 4 and isinstance(inp.shape[1], int) else 448
    with open(files(model)[1], newline="", encoding="utf-8") as f:
        rows = [(r["name"], int(r.get("category", 0))) for r in csv.DictReader(f)]
    _SESSIONS[model] = (sess, inp.name, size, rows)
    return _SESSIONS[model]


def infer(model, image_path, threshold=0.35):
    """Теги одного фото: [(tag, score)], по убыванию уверенности.

    WD14 v3 ждёт float32 BGR в шкале 0..255. С делением на 255 модель видит почти
    чёрный кадр и пишет monochrome/greyscale/no_humans на любое фото — проверено.
    """
    import numpy as np
    from PIL import Image

    sess, inp_name, size, rows = _session(model)
    with Image.open(image_path) as src:
        im = src.convert("RGB")
        w, h = im.size
        side = max(w, h)
        canvas = Image.new("RGB", (side, side), (255, 255, 255))
        canvas.paste(im, ((side - w) // 2, (side - h) // 2))
        im = canvas.resize((size, size), Image.BICUBIC)
    arr = np.asarray(im, dtype=np.float32)[:, :, ::-1]
    out = sess.run(None, {inp_name: np.expand_dims(arr, 0)})[0][0]
    if float(out.max()) > 1.0:                      # старые сборки отдают логиты
        out = 1.0 / (1.0 + np.exp(-out))
    found = [(name, float(score)) for (name, cat), score in zip(rows, out)
             if cat == 0 and float(score) >= threshold]
    return sorted(found, key=lambda kv: -kv[1])


def norm(text):
    """Единый вид тега с обеих сторон: нижний регистр, без весов и подчёркиваний."""
    t = str(text or "").lower()
    t = re.sub(r"\(([^:)]+)(?::[\d.]+)?\)", r"\1", t)
    t = t.replace("_", " ")
    t = re.sub(r"\b(ass|booty)\b", "butt", t)      # один смысл — один тег в сравнении
    t = re.sub(r"[\[\]{}<>\"'`]", " ", t)
    t = re.sub(r"[-/]+", " ", t)
    t = re.sub(r"\s+", " ", t)
    return t.strip(" ,.")


def tokens(tag):
    return {w for w in norm(tag).split() if w and w not in STOP}


def squash(tag):
    return re.sub(r"[^a-z0-9]", "", norm(tag))


def singular(word):
    return word[:-1] if len(word) > 3 and word.endswith("s") else word


def matches(one, other):
    """Один тег против другого: совпадение, вложенность или общие слова."""
    a, b = squash(one), squash(other)
    if not a or not b:
        return False
    if a in b or b in a:
        return True
    ta = {singular(t) for t in tokens(one)}
    tb = {singular(t) for t in tokens(other)}
    if not ta or not tb:
        return False
    return len(ta & tb) / len(ta) >= 0.6


def contained(short, long):
    """Короткий тег внутри длинной фразы: `long hair` в `long wavy blonde hair`."""
    tight, loose = tokens(short), tokens(long)
    return bool(tight) and tight <= loose


def _hit(tag_words, key):
    """Ключ полки совпадает со словом тега, а не с куском другого слова.

    Без этого `glasses` попадает в анатомию из-за `ass`, а `bathrobe` — никуда.
    """
    if " " in key:
        return key in " ".join(tag_words)
    if key in tag_words:
        return True
    if len(key) < 3:
        return False
    return any(w.startswith(key) or w.endswith(key) for w in tag_words)


def bucket_of(tag):
    words = norm(tag).split()
    for name in BUCKET_ORDER:
        if any(_hit(words, k) for k in BUCKETS[name]):
            return name
    return "прочее"


def _same_word(a, b):
    """skin/skinned — одно слово; bra/bracelet — разные (короткие не склеиваем)."""
    if a == b or singular(a) == singular(b):
        return True
    return len(a) >= 4 and len(b) >= 4 and (a.startswith(b) or b.startswith(a))


def subsumes(long_tag, short_tag):
    """Длинный тег содержит все слова короткого: `large breasts` > `breasts`."""
    tight, loose = tokens(short_tag), tokens(long_tag)
    if not tight:
        return False
    return all(any(_same_word(w, v) for v in loose) for w in tight)


def drop_redundant(tags, scores=None):
    """Убирает общие теги, если рядом есть тот же смысл подробнее.

    `breasts` при `large breasts`, `piercing` при `navel piercing`, `dress` при
    `white dress`: иначе подробный тег тратит слот, а модель получает два
    противоречащих по детализации сигнала.
    """
    scores = scores or {}
    keep_list = []
    for tag in tags:
        if any(other != tag and subsumes(other, tag) for other in tags):
            continue
        keep_list.append(tag)
    return sorted(keep_list, key=lambda t: -scores.get(t, 0)) if scores else keep_list


def keep(tag):
    """Идёт ли тег в промпт: своя полка, не служебный, не мусор, не персона."""
    low = norm(tag)
    if low in META or low in JUNK or low in PERSON or low in GENERIC:
        return False
    return bucket_of(low) in KEEP_BUCKETS


# Откровенно половые теги и тон кожи требуют большей уверенности: порог 0.25
# годится для «какой каблук», но ложный `pubic hair` добавит кадру наготы, а
# ложный `dark skin` перекрасит персонажа — оба видны сразу.
STRICT_THRESHOLD = 0.6
STRICT = ("pubic", "pussy", "vulva", "nipple", "areola", "nude", "topless", "anus",
          "cum", "skin")


def confident(tag, score):
    return not any(word in norm(tag) for word in STRICT) or score >= STRICT_THRESHOLD


def read_image_tags(image_path):
    """Теги фото двумя моделями: список для промпта и размер груди отдельно."""
    base = infer(BASE_MODEL, image_path, BASE_THRESHOLD)
    scores = {name: score for name, score in base}
    tags = [name for name, score in base if keep(name) and confident(name, score)]
    tags = drop_redundant(tags, scores)
    seen_slots, unique = set(), []
    for tag in tags:
        slot = slot_of(tag)
        if slot and slot in seen_slots:
            continue
        unique.append(tag)
        if slot:
            seen_slots.add(slot)
    tags = unique

    sizes = [name for name, _ in base if norm(name) in SIZES]
    if any(hint in norm(name) for name, _ in base for hint in BREAST_HINT):
        # Размер — единственная причина звать вторую, тяжёлую модель.
        eva = [(name, score) for name, score in infer(SIZE_MODEL, image_path, SIZE_THRESHOLD)
               if norm(name) in SIZES]
        if sizes:
            # Перебить уверенный ответ swinv2 eva может только уверенно сам:
            # `huge breasts` от него меняет телосложение в кадре сильнее, чем стоит.
            eva = [(name, score) for name, score in eva if score >= 0.5]
        sizes = [name for name, _ in eva] or sizes
    sizes = sorted({norm(s) for s in sizes}, key=SIZES.index) if sizes else []

    tags = [t for t in tags if norm(t) not in SIZES]
    if sizes:
        tags.insert(0, sizes[0])
    return {"tags": tags[:MAX_ADDONS], "size": sizes[0] if sizes else "", "version": VERSION}


def tags(image_path, warn=True):
    """То же, но без падений: нет модели или движка — пустой результат и причина."""
    if disabled():
        return {"tags": [], "size": "", "version": VERSION, "skipped": "COMIC_TAGGER=off"}
    if not available():
        if warn:
            print(f"   теггер выключен: нет моделей в {model_dir()}", file=sys.stderr)
        return {"tags": [], "size": "", "version": VERSION, "skipped": "нет моделей"}
    try:
        return read_image_tags(image_path)
    except Exception as e:                     # onnxruntime нет, битый файл, память
        if warn:
            print(f"   теггер не сработал: {e}", file=sys.stderr)
        return {"tags": [], "size": "", "version": VERSION, "skipped": str(e)[:120]}


def add_to_subject(subject, extra, removed=None):
    """Вставляет теги сразу после персоны: начало первого чанка влияет сильнее.

    Дубли и то, что VLM уже сказал словами, не добавляются: `bare breasts with
    dark areolas` не должно превращаться в `large breasts, bare breasts ...`.
    Одежда проверяется по слоту: к «satin robe» не дописывается `white_dress`.
    `removed` получает теги, которые пришлось убрать (заменённый размер груди) —
    по ним `--no-tags` возвращает исходный текст.
    """
    parts = [p.strip() for p in str(subject or "").split(",") if p.strip()]
    # Размер груди заменяет прежний, а не встаёт рядом: `large breasts, huge breasts`
    # в одном промпте — это два противоречащих указания модели.
    if any(norm(tag) in SIZES for tag in extra):
        drop = [p for p in parts if norm(p) in SIZES]
        if drop:
            parts = [p for p in parts if p not in drop]
            if removed is not None:
                removed.extend(drop)
    used = {slot for slot in (slot_of(p) for p in parts) if slot}
    fresh = []
    for tag in extra:
        if not keep(tag):
            continue
        if any(matches(tag, p) or contained(tag, p) for p in parts + fresh):
            continue
        slot = slot_of(tag)
        if slot and slot in used:
            continue
        fresh.append(tag)
        if slot:
            used.add(slot)
    if not fresh:
        return str(subject or "").strip().strip(",")

    at = 0
    for i, part in enumerate(parts):
        if norm(part) in PERSON:
            at = i + 1
            break
    parts[at:at] = fresh
    return ", ".join(parts)


def strip_from_subject(subject, extra, restore=()):
    """Убирает ровно свои добавки: `--no-tags` не должен оставлять теги в subject.

    `restore` — теги, которые добавка вытеснила (размер груди), их возвращаем,
    иначе отказ от теггера молча терял бы часть описания VLM.
    """
    drop = {norm(t) for t in extra or []}
    parts = [p.strip() for p in str(subject or "").split(",") if p.strip()]
    kept = [p for p in parts if norm(p) not in drop]
    kept += [p.strip() for p in restore or [] if p.strip()]
    return ", ".join(kept)
