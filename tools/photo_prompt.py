"""Фото -> промпт (LM Studio) -> генерация (ComfyUI), две фазы последовательно.

Зачем две фазы: на 16 ГБ VLM и SDXL не живут вместе. Сначала модель смотрит фото
и пишет описание, потом выгружается, и только затем запускается ComfyUI.

    python photo_prompt.py --image photo.png --workflow 07_photo_thepit
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
  --no-think   размышления выключены по умолчанию; флаг оставлен для явности
  --think      включить размышления (86 с против 6 с на фото); то же делает LM_THINKING=on
  --no-tags    не дописывать в subject теги WD14-теггера
"""
import argparse
import base64
import hashlib
import json
import os
import random
import re
import shutil
import socket
import ssl
import subprocess
import sys
import threading
import time
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import settings
import tagger
from comic import cache  # noqa: E402  — корень репозитория добавляет settings.py
from comic import run  # noqa: E402
from comic.guard import (  # noqa: E402
    AGE_WORDS, GENERIC_PERSON, ensure_guard, sanitize_subject, scrub_tags,
)
from comic.prompt import (  # noqa: E402
    NEG_CORE, NEG_GUARD, SUBJECT_SLOT, PromptBuilder, join_tags,
)

LM = settings.LM_URL or "http://127.0.0.1:1234"


def thinking_off():
    """Размышления выключены по умолчанию: одно фото 86 с и 1934 токена против 5.9 с и 97.

    LM_THINKING=on или --think включают их обратно, off — оставляет выключенными.
    Поле безобидное: сервер, который его не знает, получает запрос без него
    (см. post_chat) — так LM Studio на 1234 не ломается.
    """
    value = (os.environ.get("LM_THINKING") or "off").strip().lower()
    return value not in ("on", "1", "yes", "true")


def think_payload():
    """Поля запроса для выключенных размышлений (пусто, если их разрешили)."""
    if not thinking_off():
        return {}, ""
    return {"chat_template_kwargs": {"enable_thinking": False}}, " /no_think"


_THINK_FIELD = None    # None — ещё не знаем, понимает ли сервер chat_template_kwargs


def post_chat(payload, timeout=300):
    """Запрос к сервису описаний с запасным путём без поля размышлений.

    LM Studio и часть сборок llama.cpp отвергают незнакомое поле с 400. Второй
    запрос без него дешевле, чем упавшее описание, и запоминается до конца прогона.
    """
    global _THINK_FIELD
    if "chat_template_kwargs" in payload and _THINK_FIELD is not False:
        try:
            r = post(LM + "/v1/chat/completions", payload, timeout=timeout)
            _THINK_FIELD = True
            return r
        except RuntimeError as e:
            if "400" not in str(e):
                raise
            _THINK_FIELD = False
            print("   сервер не принял chat_template_kwargs — повторяю без поля")
            payload = {k: v for k, v in payload.items() if k != "chat_template_kwargs"}
    return post(LM + "/v1/chat/completions", payload, timeout=timeout)
COMFY = settings.COMFY_URL
ROOT = settings.ROOT
WF_DIR = settings.WF_DIR


def comfy_probe(url):
    try:
        get(url + "/system_stats", timeout=4)
        return True
    except Exception:
        return False


COMFY_LOCAL = ("http://127.0.0.1:8199", "http://127.0.0.1:8188")


def comfy_candidates(explicit=None):
    """Один порядок адресов для CLI, каталога, health и генерации.

    Явный адрес (флаг или COMFY_URL) — единственный кандидат: подставлять
    локальный сервер вместо названного нельзя, там другие модели и папка вывода.
    """
    fixed = explicit or os.environ.get("COMFY_URL")
    if fixed:
        return [fixed], True
    return list(dict.fromkeys((settings.COMFY_URL,) + COMFY_LOCAL)), False


def comfy_state(explicit=None):
    """(адрес, ошибка). Пустая ошибка — сервер ответил."""
    candidates, fixed = comfy_candidates(explicit)
    for cand in candidates:
        if comfy_probe(cand):
            return cand, ""
    if fixed:
        return candidates[0], (f"ComfyUI не отвечает на {candidates[0]} — адрес задан явно, "
                               f"другой не подставляю")
    return candidates[0], ("ComfyUI не отвечает ни на " + ", ".join(candidates)
                           + " — запусти его и повтори")


def resolve_comfy(explicit=None):
    """Свой стенд, иначе Desktop. Явный адрес не подменяется локальным.

    Порядок: --comfy, затем COMFY_URL из настроек, затем локальные 8199 и 8188.
    Если адрес задан явно (флагом или переменной) и не отвечает — отказ, а не тихий
    переход на другой сервер: там могут быть другие модели и папки вывода.
    """
    url, error = comfy_state(explicit)
    if error:
        sys.exit(error)
    return url

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
OUTPUT_DIR = settings.OUTPUT_DIR
INPUT_DIR = settings.INPUT_DIR


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


def input_name(image_path):
    """Имя, под которым фото ляжет в input: без копирования (нужно предпросмотру).

    `ensure_input` кладёт файл и возвращает это же имя, поэтому предпросмотр и
    кадр называют один файл, а `--dry-run` ничего не пишет на диск.
    """
    src = Path(image_path)
    return image_tag(src) + src.suffix.lower()


def ensure_input(image_path):
    """Кладёт фото в общую папку input и возвращает имя для LoadImage.

    Имя включает отпечаток содержимого, поэтому два разных same.png больше не
    делят один файл, и неважно, чей mtime новее: у разных фото разные имена.
    Одинаковые байты дают одно имя и одну копию.
    """
    src = Path(image_path)
    name = input_name(src)
    dst = INPUT_DIR / name
    if not dst.exists() or dst.stat().st_size != src.stat().st_size:
        INPUT_DIR.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)
    return name


def scene_slug(scene):
    """Имя сцены для кэша: см. comic.cache.scene_slug (кириллица не исчезает)."""
    return cache.scene_slug(scene)


def cache_path(image_path, scene=None):
    """Кэш привязан к содержимому файла и к сцене.

    Раньше ключом был stem: same.png и same.jpg попадали в один файл, а --scene
    при попадании в кэш молча игнорировался. Теперь другая сцена — другой файл.
    """
    return cache.path_for(ROOT / "out", image_tag(image_path), scene)


# Атомарный JSON (tmp -> fsync -> os.replace) живёт в comic.cache: им пишутся и
# кэш, и паспорт кадра, и пресеты, и тема. Имена оставлены прежними — их зовут
# webapp и проверки, — но второго места, где запись может стать неатомарной,
# быть не должно.
read_json = cache.read_json
write_json = cache.write_json


def read_cache(image_path, scene=None):
    """Описание с диска, если оно снято с этого же содержимого и для этой же сцены."""
    rec = cache.read(cache_path(image_path, scene))
    if rec is None:
        return None
    why = cache.check(rec, scene=scene or "", describe_version=DESCRIBE_VERSION,
                      sha1=lambda: fingerprint(image_path))
    if why == cache.SCENE_UNMARKED:
        # Записи до 3af4b58 писались под ключом сцены даже когда сцену применить
        # не удалось: доверять их полю scene нельзя, сцену надо переписать.
        print("   кэш сцены без метки (старая версия) — сцену переписываю заново")
        return None
    if why == cache.DESCRIBE:
        # В описании нет цвета глаз: с ним модель угадывает радужку и красит её
        # в красный. Ручные описания (--no-llm, --from-json) метку не ставят и
        # сюда не попадают — их переснимать нечем.
        print("   кэш описания снят старым промптом (без цвета глаз) — описываю заново")
        return None
    if why:
        return None
    return cache.to_dict(rec)


def write_cache(image_path, desc, scene=None):
    """Пишет описание вместе с отпечатком и сценой — кэш сам себя проверяет."""
    return cache.write(cache_path(image_path, scene), cache.from_desc(
        desc, sha1=fingerprint(image_path), source=str(image_path),
        scene=scene or "", describe_version=DESCRIBE_VERSION))


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
    think_fields, think_suffix = think_payload()
    ask = (f"Rewrite this photo description, placing the same person into: {scene}.\n"
           f"Keep face, hair, body type, glasses and jewellery. Change clothing and background "
           f"as the scene requires. Return ONLY JSON with subject, negative_extra, notes."
           f"{think_suffix}\n\n"
           f"{desc['subject']}")
    payload = {"model": model, "temperature": 0.6, "max_tokens": 3000,
               "messages": [{"role": "user", "content": ask}]}
    payload.update(think_fields)
    try:
        r = post_chat(payload)
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

# Рамка возраста, запасное ядро негатива и плейсхолдер субъекта живут в
# comic\prompt.py: там же сборка промпта. Здесь только прежние имена — на них
# ссылаются старые вызовы и проверки.

# Версия промпта описания. Запись кэша помнит, каким промптом она снята: без цвета
# глаз модель красит радужку случайным насыщенным цветом (чаще всего красным),
# поэтому старые описания переснимаются сами, а не живут с этой ошибкой.
DESCRIBE_VERSION = 2

SYSTEM = """You describe photographs for a text-to-image model.
Return ONLY a JSON object with three string fields: subject, negative_extra, notes.

Rules:
- subject: English, comma-separated tags, 25-45 words. Cover: who, hair, eye colour,
  face details (eye makeup, lips), glasses/accessories, clothing, pose, hands, framing,
  background, light.
- Always name the eye colour right after the hair: blue eyes, green eyes, brown eyes,
  hazel eyes, grey eyes. The image model has no default colour and paints a saturated
  random one when the colour is missing (red or glowing eyes most often). Here grey and
  hazel are both wrong: in flat comic colours grey renders almost white and hazel
  renders brown. So never write grey eyes or hazel eyes — for grey, grey-green, olive
  or hazel-looking irises write green eyes. If the eyes are closed, turned away or
  hidden, omit the colour instead of guessing.
- Name the eye makeup when it is visible and say how heavy it is: heavy black eyeliner,
  smokey eyeshadow, light eye makeup, bare eyelids.
- Bare skin is named plainly: topless, bare chest, bare breasts, bare legs. Do not soften
  it to bare shoulders or bare arms when the chest is actually uncovered.
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
{"subject":"adult woman, early 30s, long wavy blonde hair, blue eyes, black framed glasses, light eye makeup, red sleeveless dress, thin gold necklace, arms spread wide, neutral grey studio background, soft frontal light","negative_extra":"white lenses, opaque glasses, missing necklace","notes":"главное — сохранить очки и цепочку."}"""


def with_adult(subject):
    """Взрослый — первым тегом, с явным родом. Убирает дубли-роды и всё школьное.

    Сама логика — `comic.guard.sanitize_subject`: ею же проверяются переопределения
    промпта, иначе рамку можно было снять, заменив текст целиком.
    """
    return sanitize_subject(subject)


def enrich_subject(desc, image_path, warn=True):
    """Дописывает к subject детали, которые видит теггер, а VLM пропустил.

    Метка addons делает операцию идемпотентной: запись с текущей версией теггера
    второй раз не считается, а старая запись без метки дозаполняется на месте —
    без обращения к VLM и без потери описания.
    """
    if (desc.get("addons") or "") == tagger.VERSION:
        return desc.get("subject", "")
    found = tagger.tags(str(image_path), warn=warn)
    if found.get("skipped"):
        print(f"   теги не добавлены: {found['skipped']}")
        return desc.get("subject", "")
    before = [p.strip() for p in str(desc.get("subject", "")).split(",") if p.strip()]
    removed = []
    after = tagger.add_to_subject(desc.get("subject", ""), found["tags"], removed)
    added = [p.strip() for p in after.split(",") if p.strip() and p.strip() not in before]
    desc["addon_tags"] = added
    desc["addons"] = tagger.VERSION
    if removed:
        desc["addon_replaced"] = removed
    if found.get("size"):
        desc["addon_size"] = found["size"]
    desc["subject"] = after
    if added:
        print("   теггер добавил:", ", ".join(added))
    return desc["subject"]


def apply_tags(desc, image_path, no_tags=False, dry_run=False):
    """Теггер в конвейере: выключатель, ленивый dry-run и снятие своих добавок."""
    if no_tags:
        # Теги могли прийти из кэша: --no-tags снимает ровно их, а не весь subject.
        return tagger.strip_from_subject(desc.get("subject", ""), desc.get("addon_tags") or [],
                                         desc.get("addon_replaced") or [])
    if dry_run and (desc.get("addons") or "") != tagger.VERSION:
        # --dry-run обещает не грузить модели; теггер — тоже модель, пусть и на CPU.
        print("   теги не считаю: --dry-run не грузит модели")
        return desc.get("subject", "")
    return enrich_subject(desc, image_path)


def clean_negative(desc):
    """Убирает из негатива то, что subject прямо называет.

    «long dark hair» в subject и `dark hair` в негативе — самопротиворечие, а не
    страховка: модель получает приказ убрать то, что ей же велено нарисовать.
    `missing glasses` при «clear glasses» сюда не попадает — там другое слово.
    """
    subject = [p.strip() for p in str(desc.get("subject") or "").split(",") if p.strip()]
    neg = [p.strip() for p in str(desc.get("negative_extra") or "").split(",") if p.strip()]
    kept = [n for n in neg
            if not any(tagger.matches(n, s) or tagger.contained(n, s) for s in subject)]
    if len(kept) != len(neg):
        print("   негатив без самопротиворечий:",
              ", ".join(sorted(set(neg) - set(kept))))
        desc["negative_extra"] = ", ".join(kept)
    return desc.get("negative_extra", "")


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


def lm_models(strict=True):
    """Модели сервиса описаний. LM Studio и обычный OpenAI-сервер отвечают по-разному.

    LM Studio: `/api/v1/models` -> `{"models": [...]}` с capabilities и loaded_instances.
    Всякий OpenAI-совместимый сервис (llama.cpp, llama-swap): `/v1/models` ->
    `{"data": [{"id": ...}]}`, выгрузки нет, vision не объявлен — считаем, что он есть:
    описание просят у сервиса именно за это.
    """
    try:
        return get(LM + "/api/v1/models")
    except Exception as first:
        try:
            resp = get(LM + "/v1/models", timeout=10)
        except Exception as second:
            if not strict:
                return None
            sys.exit(f"Сервис описаний не отвечает на {LM}:\n"
                     f"  /api/v1/models: {first}\n  /v1/models: {second}\n"
                     f"Запусти LM Studio (lms server start) или укажи LM_URL другого сервиса.")
    models = [{"key": m.get("id"), "capabilities": {"vision": True}, "loaded_instances": []}
              for m in (resp.get("data") or []) if m.get("id")]
    return {"models": models, "openai": True}


def model_list(resp):
    """LM Studio 0.4 отдаёт {'models':[...]}, старые сборки — {'data':[...]}."""
    return resp.get("models") or resp.get("data") or []


def pick_model(resp, wanted):
    ms = model_list(resp)
    if wanted:
        return wanted
    keys = {str(m.get("key", "")) for m in ms}
    pinned = getattr(settings, "LM_MODEL", "")
    if pinned and pinned in keys:
        return pinned
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


def face_crop(image_path, max_side=640):
    """Верх кадра крупным планом: на целом фото радужка — несколько пикселей.

    Без зума модель не видит цвет глаз и называет его наугад (`brown` на серых),
    а без названного цвета SDXL красит радужку в случайный насыщенный — чаще
    всего красный. Зум верхней части кадра (лицо в портрете и в полный рост)
    стоит ~600 токенов и делает цвет глаз читаемым.
    """
    try:
        from PIL import Image
        with Image.open(image_path) as im:
            im = im.convert("RGB")
            w, h = im.size
            crop = im.crop((int(w * 0.10), 0, int(w * 0.90), int(h * 0.45)))
            if max(crop.size) > max_side:
                crop.thumbnail((max_side, max_side))
            tmp = ROOT / "out" / "tmp"
            tmp.mkdir(parents=True, exist_ok=True)
            dst = tmp / f"{image_tag(image_path)}_face.png"
            crop.save(dst)
            return str(dst)
    except Exception as e:
        print("   зум на лицо не сделался:", e)
        return ""


def describe(image_path, model):
    image_path = prep_image(image_path)
    b64 = base64.b64encode(Path(image_path).read_bytes()).decode()
    face = face_crop(image_path)
    face_b64 = base64.b64encode(Path(face).read_bytes()).decode() if face else ""
    think_fields, think_suffix = think_payload()
    base = {
        "model": model,
        "temperature": 0.2,
        # Думающие модели (Gemma4, Qwen3.6) тратят токены на рассуждения:
        # на 400–1000 ответ обрезался, JSON не доходил.
        "max_tokens": 3000,
        "messages": [
            {"role": "system", "content": SYSTEM},
            {"role": "user", "content": [
                {"type": "text",
                 "text": ("Describe this photo as JSON. Do not explain. "
                          "The first image is the whole photo, the second is the "
                          "same photo zoomed on the head." if face_b64 else
                          "Describe this photo as JSON. Do not explain.") + think_suffix},
                {"type": "image_url", "image_url": {"url": "data:image/png;base64," + b64}},
            ] + ([{"type": "image_url",
                   "image_url": {"url": "data:image/png;base64," + face_b64}}]
                 if face_b64 else [])},
        ],
    }
    base.update(think_fields)

    last = ""
    for budget in (3000, 6000):
        payload = dict(base)
        payload["max_tokens"] = budget
        try:
            r = post_chat(payload)
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
    """Освобождает память под SDXL. По умолчанию — только модель этого прогона.

    Обычный OpenAI-сервер выгрузки не умеет: там оставляем как есть и говорим об этом,
    иначе один и тот же вызов падал бы на 404 после честно отработавшего описания.
    """
    resp = lm_models(strict=False)
    if resp is None:
        print("  выгрузка пропущена: сервис описаний не отвечает")
        return []
    if resp.get("openai"):
        print("  выгрузка не нужна: сервис сам держит модель в памяти")
        return []
    if unload_everything:
        return unload_all(resp)
    return unload_model(resp, model)


def compose_negative(negative_extra, meta=None):
    """Сначала то, что важно подавить на этом фото, потом негатив сборки, потом рамка.

    Негатив берётся из графа: у GTA там «night, rain, dark», у bimbo — «white lenses,
    opaque lenses, grey skin». Раньше CLI подменял его своим коротким ядром целиком.
    Сборка — в comic\prompt.py: обёртка осталась ради прежних имён.
    """
    return PromptBuilder(meta).negative(negative_extra)


def compose_positive(meta, subject, workflow=""):
    """Промпт = шаблон сборки с подставленным субъектом.

    Шаблон приходит из графа (meta.positive_template), поэтому CLI выдаёт ровно тот
    промпт, что и панель, вместе со стилевыми настройками сборки. Если meta нет
    (старый .api.json) — собираем из таблицы STYLE_PREFIX, как раньше.
    """
    builder = PromptBuilder(meta)
    if builder.refuse_reason() is None:
        return builder.positive(subject)
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


def workflow_from_file(name):
    """Сборка по имени кадра: SaveImage пишет префикс comic/<сборка>_NNNNN_.png."""
    return run.workflow_from_file(name, WF_DIR)


# Метки исхода живут в формате паспорта (comic.run): набор закрытый, и второе
# место с тем же списком разошлось бы с первым.
OUTCOMES = run.OUTCOMES


def save_run(image_path, workflow, desc, seed, denoise, name,
             cn_strength=None, style_weight=None, overrides=None, refined=False,
             positive=None, negative=None, settings=None, outcome="unknown"):
    """Складывает кадр и его паспорт в папку out/<фото>_<хэш>/.

    Сборка пишется в каждый кадр, а не одна на папку: в одной папке лежат кадры
    разных стилей, и общее поле «workflow» врало. У старых записей сборка
    восстанавливается из имени файла — префикс SaveImage и есть имя сборки.

    В запись идут все настройки, от которых зависит кадр: `cn_strength` и
    `style_weight` тоже, иначе два кадра с одинаковым seed выглядят одинаково
    подписанными, хотя сделаны с разной силой ControlNet или LoRA.
    `positive` и `negative` — фактический запрос (шаблон стиля, переопределения
    и негатив вместе): по одному `subject` его не восстановить.
    `settings` — применённые настройки сэмплера из `build_payload`: шаги, cfg,
    сэмплер и планировщик. По аргументам их не восстановить: `--denoise` и поля
    формы правят граф на лету.
    `outcome` — метка исхода: по умолчанию `unknown`, повторный кадр recheck
    помечается `refined`. `accepted`/`rejected` ставит действие пользователя в
    галерее, `cancelled` — отмена. Метка не из `OUTCOMES` — отказ, а не запись
    в паспорт чего попало.
    `None` означает «как в сборке».

    Сам формат паспорта — в `comic.run` (схема 2, миграция старых записей):
    здесь только собирается запись нового кадра.

    Возвращает папку или None, если кадра нет в OUTPUT_DIR: паспорт на
    несуществующий файл хуже, чем отсутствие паспорта.
    """
    if outcome not in OUTCOMES:
        raise PipelineError(f"метка исхода {outcome!r} не из списка {', '.join(OUTCOMES)}")
    if not name:
        print("   паспорт не пишу: ComfyUI не назвал файл")
        return None
    src = OUTPUT_DIR / name
    if not src.exists():
        print(f"   кадра нет по пути {src} — паспорт не пишу")
        print(f"   папка вывода задана как {OUTPUT_DIR}; проверь COMFY_OUTPUT "
              f"или --output-directory у ComfyUI")
        return None

    folder = ROOT / "out" / image_tag(image_path)
    try:
        records, head = run.read_book(folder, read_json=read_json)
    except run.RunError as e:
        # Паспорт уже есть — переписываем только прочитанный: пустой «паспорт»
        # вместо истории хуже, чем кадр без записи, и молча теряет прошлые кадры.
        raise PipelineError(f"{e} — кадр не записан, паспорт не тронут") from e
    folder.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, folder / name)
    for r in records:
        if not r.workflow:
            r.workflow = workflow_from_file(r.file)
    records.append(run.RunRecord(
        workflow=workflow,
        seed=seed, denoise=denoise, file=name,
        cn_strength=cn_strength, style_weight=style_weight,
        overrides=overrides or None,
        refined=bool(refined),
        positive=positive, negative=negative,
        subject=desc.get("subject", ""), negative_extra=desc.get("negative_extra", ""),
        # Настройки главного прохода: без них паспорт не отличает кадр на 30 шагах
        # от кадра на 15. Нет настроек — None, а не 0.
        steps=(settings or {}).get("steps"),
        cfg=(settings or {}).get("cfg"),
        sampler=(settings or {}).get("sampler"),
        scheduler=(settings or {}).get("scheduler"),
        # Оба прохода: у 07 главный 0.75/30 и второй 0.3/15. По одному `denoise`
        # не видно, что кадр сделан двумя сэмплерами.
        passes=run.passes_of((settings or {}).get("passes")),
        # Исход кадра: по умолчанию unknown, а не accepted — принял кадр человек,
        # и до его решения паспорт не имеет права это утверждать.
        outcome=outcome,
    ))
    run.write(folder, records, head=head, write_json=write_json)
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
        think_fields, think_suffix = think_payload()
        b64 = base64.b64encode(Path(image_path).read_bytes()).decode()
        ask = ("Compare this image with the description. List ONLY what is clearly missing or wrong "
               "as short tags. Return ONLY JSON: {\"missing\": \"tag1, tag2\"}."
               f"{think_suffix}\n\n"
               f"Description: {desc.get('subject', '')}")
        payload = {
            "model": model, "max_tokens": 3000, "temperature": 0.1,
            "messages": [{"role": "user", "content": [
                {"type": "text", "text": ask},
                {"type": "image_url", "image_url": {"url": "data:image/png;base64," + b64}},
            ]}],
        }
        payload.update(think_fields)
        r = post_chat(payload, timeout=600)
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
        raise PipelineError(f"нет файла {path} — сначала собери воркфлоу "
                            f"(python tools\\build_workflows.py)")
    data = json.loads(path.read_text(encoding="utf-8"))
    return data["prompt"], data.get("meta") or {}


def workflow_meta(workflow):
    """Только meta сборки: поля формы и стилевой шаблон, без копии графа."""
    path = WF_DIR / (workflow + ".api.json")
    if not path.exists():
        raise PipelineError(f"нет файла {path} — сначала собери воркфлоу")
    return json.loads(path.read_text(encoding="utf-8")).get("meta") or {}


class PipelineError(RuntimeError):
    """Ошибка пайплайна, которую показывают пользователю без трейсбека."""


class BindingsError(PipelineError):
    """Поле формы не совпало с графом: неверный граф не отправляем."""


def binding(meta, key):
    """Привязка поля из meta.fields. Нет привязки — отказ, а не догадка."""
    fields = (meta or {}).get("fields") or {}
    b = fields.get(key)
    if not isinstance(b, dict) or not b.get("node"):
        raise BindingsError(f"поле «{key}» не привязано к узлу графа: пересобери воркфлоу")
    return b


def check_bindings(api, meta, require_fields=False):
    """Все привязки из meta должны существовать и быть записываемыми входами.

    require_fields=True — режим формы: сборка без блока fields не показывается,
    потому что интерфейсу неоткуда взять поля. CLI со старым .api.json работает
    по-прежнему: там meta неполная, и это не повод отказывать в генерации.
    """
    problems = []
    fields = (meta or {}).get("fields") or {}
    if require_fields and not fields:
        problems.append("в meta нет блока fields — форма не узнает поля, пересобери воркфлоу")
    for key, b in fields.items():
        node = api.get(str(b.get("node")))
        if not node:
            problems.append(f"поле «{key}»: узла {b.get('node')} нет в графе")
            continue
        name = b.get("input")
        if name not in (node.get("inputs") or {}):
            problems.append(f"поле «{key}»: у узла {b['node']} нет входа {name}")
        elif isinstance(node["inputs"][name], list):
            problems.append(f"поле «{key}»: вход {name} занят связью, писать нельзя")
    for slot in (meta or {}).get("loras") or []:
        node = api.get(str(slot.get("node")))
        if not node or "lora_name" not in (node.get("inputs") or {}):
            problems.append(f"слот LoRA {slot.get('role') or slot.get('node')}: "
                            f"узла {slot.get('node')} нет в графе")
        elif node["class_type"] != "LoraLoaderModelOnly":
            problems.append(f"слот LoRA {slot.get('role')}: узел {slot.get('node')} "
                            f"оказался {node['class_type']}, а не LoraLoaderModelOnly")
    return problems


def _coerce(value, kind, key):
    if kind == "INT":
        try:
            if isinstance(value, bool):
                raise ValueError
            return int(value)
        except (TypeError, ValueError):
            raise BindingsError(f"поле «{key}» ждёт целое число, получено {value!r}") from None
    if kind == "FLOAT":
        try:
            if isinstance(value, bool):
                raise ValueError
            return float(value)
        except (TypeError, ValueError):
            raise BindingsError(f"поле «{key}» ждёт число, получено {value!r}") from None
    if kind == "COMBO":
        if not isinstance(value, str) or not value.strip():
            raise BindingsError(f"поле «{key}» ждёт одно значение из списка, получено {value!r}")
        return value
    return str(value)


def apply_fields(api, meta, values):
    """Пишет переопределения в копию графа по привязкам. Возвращает применённое."""
    applied = {}
    for key, value in (values or {}).items():
        if value is None:
            continue
        b = binding(meta, key)
        node = api.get(str(b["node"]))
        if node is None:
            raise BindingsError(f"поле «{key}»: узла {b['node']} нет в графе")
        name = b["input"]
        if name not in node["inputs"] or isinstance(node["inputs"][name], list):
            raise BindingsError(f"поле «{key}»: вход {name} у узла {b['node']} недоступен")
        node["inputs"][name] = _coerce(value, b.get("type", "STRING"), key)
        applied[key] = node["inputs"][name]
    return applied


def apply_loras(api, meta, loras):
    """Меняет имя и вес LoRA конкретного слота (style/body/character/hands)."""
    applied = {}
    slots = {s.get("role"): s for s in (meta or {}).get("loras") or []}
    for role, change in (loras or {}).items():
        slot = slots.get(role)
        if slot is None:
            raise BindingsError(f"в этой сборке нет слота LoRA «{role}»")
        node = api.get(str(slot["node"]))
        if node is None or node["class_type"] != "LoraLoaderModelOnly":
            raise BindingsError(f"слот «{role}»: узла {slot['node']} нет в графе")
        if isinstance(change, str):
            change = {"name": change}
        if change.get("name"):
            node["inputs"]["lora_name"] = str(change["name"])
            applied.setdefault(role, {})["name"] = node["inputs"]["lora_name"]
        if change.get("weight") is not None:
            node["inputs"]["strength_model"] = _coerce(change["weight"], "FLOAT",
                                                       f"{role}.weight")
            applied.setdefault(role, {})["weight"] = node["inputs"]["strength_model"]
    return applied


def sampler_settings(samplers):
    """Применённые настройки сэмплеров: главный проход и все проходы графа.

    Значения читаются из собранного графа, а не из аргументов: `--denoise` правит
    главный KSampler, поля формы могут переписать `steps` и `cfg`, и паспорт должен
    говорить то, что реально ушло в ComfyUI. Главный проход — тот же, который правит
    `denoise` (наибольший denoise), поэтому шаги, cfg и сэмплер в записи принадлежат
    одному узлу, а не смеси двух.

    `passes` идут в порядке графа: главный проход первым, у 07 это 0.75/30, потом
    0.3/15. Сортировать по denoise не нужно и опасно: с `--denoise 0.2` главный
    проход уехал бы на второе место, и паспорт снова назвал бы второй проход первым.

    `None` вместо нулей: у старого паспорта настройки не записаны, и 0 шагов
    выглядел бы настоящим значением.
    """
    if not samplers:
        return {"steps": None, "cfg": None, "sampler": None, "scheduler": None,
                "passes": []}
    main = max(samplers, key=lambda s: float(s["inputs"].get("denoise") or 0))
    inputs = main["inputs"]
    return {
        "steps": inputs.get("steps"),
        "cfg": inputs.get("cfg"),
        "sampler": inputs.get("sampler_name"),
        "scheduler": inputs.get("scheduler"),
        "passes": [{"denoise": s["inputs"].get("denoise"), "steps": s["inputs"].get("steps")}
                   for s in samplers],
    }


def build_payload(workflow, subject, negative_extra, denoise=None, seed=None, landscape=False,
                  style_weight=None, image_name=None, cn_strength=None,
                  overrides=None, positive=None, negative=None):
    """Копия API-графа под конкретный кадр плюс итоговые промпты.

    Правки не касаются файла сборки: граф загружается заново, поэтому два
    задания с разными настройками не мешают друг другу. Полный positive или
    negative (overrides) заменяет собранный текст целиком — это явное
    переопределение, а не добавка к описанию. Рамку возраста заменить нельзя:
    positive проходит `sanitize_subject`, negative замыкается `ensure_guard`
    (`comic.guard`), поэтому переопределение не снимает правила проекта 18+.
    """
    api, meta = load_workflow(workflow)
    problems = check_bindings(api, meta)
    if problems:
        raise BindingsError("; ".join(problems))

    prefix = meta.get("positive_template") or STYLE_PREFIX.get(workflow)
    if not prefix:
        raise PipelineError(f"для {workflow} не задан стилевой блок: пересобери воркфлоу")

    positive_text = (sanitize_subject(positive) if positive is not None
                     else compose_positive(meta, subject, workflow))
    negative_text = (ensure_guard(negative) if negative is not None
                     else compose_negative(negative_extra, meta))

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
        if node["class_type"] == "ControlNetApplyAdvanced" and cn_strength is not None:
            old = node["inputs"].get("strength")
            node["inputs"]["strength"] = float(cn_strength)
            print(f"   сила ControlNet: {old} -> {cn_strength}")
        if node["class_type"] == "EmptyLatentImage" and landscape:
            w = node["inputs"].get("width")
            h = node["inputs"].get("height")
            if w and h and w < h:
                node["inputs"]["width"], node["inputs"]["height"] = h, w
                print(f"   разрешение под горизонталь: {h}x{w}")
        if node["class_type"] in ("CLIPTextEncode", "TextEncodeQwenImageEditPlus"):
            t = str(node["inputs"].get("text", ""))
            if t.startswith("photo, photorealistic") or t.startswith("worst quality"):
                node["inputs"]["text"] = negative_text
                patched += 1
            elif t.strip():
                node["inputs"]["text"] = positive_text
                patched += 1
        if node["class_type"] == "KSampler":
            samplers.append(node)
    if patched == 0:
        raise PipelineError("не нашёл текстовых узлов для подстановки")

    for s in samplers:
        if seed is not None:
            s["inputs"]["seed"] = int(seed)
    if denoise is not None and samplers:
        main = max(samplers, key=lambda s: float(s["inputs"].get("denoise", 0)))
        main["inputs"]["denoise"] = float(denoise)

    applied = {}
    if overrides:
        applied.update(apply_fields(api, meta, overrides.get("fields")))
        applied["loras"] = apply_loras(api, meta, overrides.get("loras"))
    if style_weight is not None and not weight_set:
        # Слот стиля могли переименовать переопределением — тогда сверяем по meta.
        slots = {s.get("role"): s for s in meta.get("loras") or []}
        if not (overrides or {}).get("loras", {}).get("style"):
            raise BindingsError(f"--style-weight: не понял, какая LoRA стилевая в {workflow} "
                                f"(meta.style_lora={style_lora!r}) — пересобери воркфлоу")
        applied.setdefault("style_weight", float(style_weight))

    return {
        "prompt": api,
        "meta": meta,
        "positive": positive_text,
        "negative": negative_text,
        "applied": applied,
        "settings": sampler_settings(samplers),
    }


def wait_for_prompt(pid, progress=None, client_id=None, timeout=900):
    """Ждёт /history и, если есть progress, читает шаги из /ws.

    История — источник правды: WebSocket может не подняться, отвалиться или
    промолчать на закэшированном графе, и кадр всё равно должен быть найден.
    """
    stop = threading.Event()
    if progress and client_id and COMFY.startswith(("http://", "https://")):
        ready = threading.Event()
        threading.Thread(target=_ws_watch,
                         args=(COMFY, client_id, pid, progress, stop, ready),
                         daemon=True).start()
        # Ждём рукопожатие: иначе первые шаги можно не успеть увидеть.
        ready.wait(0.5)
    t0 = time.time()
    try:
        while time.time() - t0 < timeout:
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
    finally:
        stop.set()
    print("  таймаут: кадр не появился")
    return False, None


def _ws_open(base, client_id, timeout=5):
    """WebSocket без внешних пакетов: рукопожатие и текстовые кадры.

    Свой клиент нужен, чтобы шаги работали и в окружении ComfyUI, и в .venv-web:
    веб-зависимости не обязаны попадать в venv с torch. Если сервер не говорит
    по-WebSocket, вызывающий просто остаётся на опросе /history.
    """
    url = base.rstrip("/")
    secure = url.startswith("https://")
    rest = re.sub(r"^https?://", "", url)
    hostport, _, path = rest.partition("/")
    host, _, port = hostport.partition(":")
    sock = socket.create_connection((host, int(port or (443 if secure else 80))),
                                    timeout=timeout)
    if secure:
        sock = ssl.create_default_context().wrap_socket(sock, server_hostname=host)
    key = base64.b64encode(os.urandom(16)).decode()
    request = (f"GET /{(path + '/') if path else ''}ws?clientId={client_id} HTTP/1.1\r\n"
               f"Host: {hostport}\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n"
               f"Sec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\n\r\n")
    sock.sendall(request.encode())
    head = b""
    while b"\r\n\r\n" not in head:
        chunk = sock.recv(4096)
        if not chunk:
            raise OSError("сервер закрыл соединение на рукопожатии")
        head += chunk
        if len(head) > 65536:
            raise OSError("слишком длинный ответ рукопожатия")
    first, _, leftover = head.partition(b"\r\n\r\n")
    lines = first.decode("latin-1").split("\r\n")
    if " 101 " not in lines[0]:
        raise OSError("WebSocket: " + lines[0])
    expected = base64.b64encode(
        hashlib.sha1((key + "258EAFA5-E914-47DA-95CA-C5AB0DC85B11").encode()).digest()
    ).decode()
    for line in lines[1:]:
        if line.lower().startswith("sec-websocket-accept:"):
            if line.split(":", 1)[1].strip() != expected:
                raise OSError("WebSocket: неверный Sec-WebSocket-Accept")
            break
    else:
        raise OSError("WebSocket: сервер не подтвердил рукопожатие")
    return _WSConn(sock, leftover)


class _WSConn:
    """Чтение текстовых кадров. Пинг подтверждаем, закрытие считаем концом."""

    def __init__(self, sock, leftover=b""):
        self.sock = sock
        self.buf = leftover

    def _read(self, n):
        while len(self.buf) < n:
            chunk = self.sock.recv(4096)
            if not chunk:
                raise EOFError("соединение закрыто")
            self.buf += chunk
        data, self.buf = self.buf[:n], self.buf[n:]
        return data

    def recv(self):
        b1, b2 = self._read(2)
        opcode = b1 & 0x0F
        masked = bool(b2 & 0x80)
        length = b2 & 0x7F
        if length == 126:
            length = int.from_bytes(self._read(2), "big")
        elif length == 127:
            length = int.from_bytes(self._read(8), "big")
        mask = self._read(4) if masked else b""
        payload = self._read(length) if length else b""
        if masked:
            payload = bytes(c ^ mask[i % 4] for i, c in enumerate(payload))
        if opcode == 0x8:
            raise EOFError("сервер закрыл WebSocket")
        if opcode == 0x9:
            self._send(0xA, payload)
            return ""
        if opcode == 0xA:
            return ""
        return payload.decode("utf-8", "replace")

    def _send(self, opcode, payload):
        mask = os.urandom(4)
        data = bytes(c ^ mask[i % 4] for i, c in enumerate(payload))
        self.sock.sendall(bytes([0x80 | opcode, 0x80 | len(data)]) + mask + data)

    def close(self):
        try:
            self._send(0x8, b"\x03\xe8")
        except Exception:
            pass
        try:
            self.sock.close()
        except Exception:
            pass


def _ws_watch(base, client_id, pid, progress, stop, ready=None):
    """Слушает /ws и отдаёт шаги. Падение сокета не мешает опросу истории."""
    try:
        conn = _ws_open(base, client_id)
    except Exception as e:
        progress({"stage": "ws_failed", "message": f"WebSocket не открылся: {e}"})
        if ready is not None:
            ready.set()
        return
    if ready is not None:
        ready.set()
    try:
        while not stop.is_set():
            try:
                raw = conn.recv()
            except TimeoutError:
                # Тишина во время загрузки модели — не повод бросать канал.
                if stop.is_set():
                    return
                continue
            except Exception:
                # Сокет закрылся — не беда: историю всё равно опрашиваем, кадр не теряется.
                return
            if not isinstance(raw, str) or not raw:
                continue
            try:
                msg = json.loads(raw)
            except Exception:
                continue
            kind = msg.get("type")
            data = msg.get("data") or {}
            if data.get("prompt_id") and data["prompt_id"] != pid:
                continue
            if kind == "execution_start":
                progress({"stage": "comfy_start", "prompt_id": pid})
            elif kind == "execution_cached":
                progress({"stage": "comfy_cached", "nodes": len(data.get("nodes") or [])})
            elif kind == "progress":
                progress({"stage": "sampling", "value": data.get("value"),
                          "max": data.get("max"), "node": data.get("node")})
            elif kind == "progress_state":
                for node, state in (data.get("nodes") or {}).items():
                    if state.get("state") == "running":
                        progress({"stage": "sampling", "value": state.get("value"),
                                  "max": state.get("max"), "node": node})
            elif kind == "executing" and data.get("node") is None:
                progress({"stage": "comfy_done"})
            elif kind == "execution_error":
                progress({"stage": "comfy_error",
                          "message": str(data.get("exception_message") or "ошибка узла")})
    finally:
        conn.close()


def generate(workflow, subject, negative_extra, denoise=None, seed=None, landscape=False,
             style_weight=None, image_name=None, cn_strength=None, overrides=None,
             positive=None, negative=None, progress=None, client_id=None, report=None):
    try:
        payload = build_payload(workflow, subject, negative_extra, denoise=denoise, seed=seed,
                                landscape=landscape, style_weight=style_weight,
                                image_name=image_name, cn_strength=cn_strength,
                                overrides=overrides, positive=positive, negative=negative)
    except PipelineError as e:
        sys.exit(str(e))
    api = payload["prompt"]
    positive_text, negative_text = payload["positive"], payload["negative"]
    if isinstance(report, dict):
        # Фактический запрос: его сохраняют паспорт кадра и галерея.
        report.update({"positive": positive_text, "negative": negative_text,
                       "settings": payload.get("settings") or {}})

    print("   positive:", positive_text[:160] + ("…" if len(positive_text) > 160 else ""))
    print("   negative:", negative_text[:160] + ("…" if len(negative_text) > 160 else ""))

    body = {"prompt": api}
    if client_id:
        body["client_id"] = client_id
    pid = post(COMFY + "/prompt", body)["prompt_id"]
    print(f"  в очереди: {pid}")
    if progress:
        progress({"stage": "comfy_queue", "prompt_id": pid})
    return wait_for_prompt(pid, progress=progress, client_id=client_id)


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
    skipped = []
    for i, f in enumerate(files, 1):
        print(f"\n=== [{i}/{len(files)}] {f.name}")
        fresh = False        # описание для этой сцены готово сейчас и его можно кэшировать
        cached = read_cache(str(f), args.scene) if need_llm and not args.force else None
        base_desc = (read_cache(str(f))
                     if need_llm and not args.force and args.scene and not cached else None)
        if args.from_json:
            desc = json.loads(Path(args.from_json).read_text(encoding="utf-8"))
        elif cached:
            desc = cached
            print(f"   из кэша {cache_path(str(f), args.scene).name}")
        elif args.no_llm:
            desc = {"subject": args.subject or "", "negative_extra": "", "notes": "без LLM"}
        elif args.dry_run:
            print("   в кэше нет описания, а --dry-run модель не грузит — пропускаю")
            skipped.append(f.name)
            continue
        elif base_desc:
            print("   фото уже описано — переписываю только сцену")
            desc = base_desc
        else:
            if model is None:
                model = pick_model(lm_models(), args.model)
            desc = describe(str(f), model)
            described += 1
            fresh = True

        if args.scene and need_llm and not cached:
            if model is None:
                model = pick_model(lm_models(), args.model)
            print(f"   сцена: {args.scene}")
            new = rewrite_scene(desc, args.scene, model)
            described += 1
            if not new:
                # Не пишем исходный текст под ключом сцены: иначе следующий запуск
                # возьмёт его из кэша и сцену уже не попробует.
                print("   сцену применить не удалось — пропускаю файл")
                if fresh:
                    print("   описание фото сохранено без сцены:",
                          write_cache(str(f), desc, None).name)
                skipped.append(f.name)
                continue
            desc = new
            fresh = True
        if args.scene and args.no_llm:
            desc["subject"] = (desc["subject"].rstrip(", ") + ", " + args.scene).strip(", ")

        desc["subject"] = with_adult(desc.get("subject", ""))
        if desc["subject"]:
            before = desc["subject"]
            desc["subject"] = apply_tags(desc, str(f), args.no_tags, args.dry_run)
            # Дозаполнили кэш этого фото — перезапишем. Ручное описание и пресет
            # в кэш не пишем: там его быть не должно (пункт 0.11.2 в AGENTS).
            if cached and not args.no_tags and not args.dry_run and desc["subject"] != before:
                fresh = True
        clean_negative(desc)
        if not desc["subject"]:
            print("   пустое описание — пропускаю")
            skipped.append(f.name)
            print("   нет описания, кадр не появится")
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
        if skipped:
            print("без описания: " + ", ".join(skipped))
            return 1
        return 0
    if not descs:
        print(f"\nкадров: 0, провалов: {len(skipped)}"
              + (f", без описания: {len(skipped)} ({', '.join(skipped)})" if skipped else "")
              + " — генерировать нечего")
        return 1

    COMFY = resolve_comfy(args.comfy)
    frames, failed, folders = 0, 0, []
    for i, (f, desc) in enumerate(descs.items(), 1):
        for v in range(max(1, args.variants)):
            sd = (args.seed + v) if args.seed is not None else random.randint(1, 2_000_000_000)
            print(f"\n=== [{i}/{len(descs)}] {f.name}, вариант {v + 1}, seed {sd}")
            report = {}
            ok, name = generate(workflow, desc["subject"], desc.get("negative_extra", ""),
                                denoise=args.denoise, seed=sd,
                                landscape=image_is_landscape(str(f)), style_weight=args.style_weight,
                                cn_strength=args.cn_strength,
                                image_name=ensure_input(str(f)), report=report)
            if not ok:
                failed += 1
                print("   кадр не получен")
                continue
            folder = save_run(str(f), workflow, desc, sd, args.denoise, name,
                              cn_strength=args.cn_strength, style_weight=args.style_weight,
                              positive=report.get("positive"),
                              negative=report.get("negative"),
                              settings=report.get("settings"))
            if folder is None:
                failed += 1
                continue
            frames += 1
            folders.append(folder)
    print(f"\nкадров: {frames}, провалов: {failed}"
          + (f", без описания: {len(skipped)} ({', '.join(skipped)})" if skipped else ""))
    if folders:
        print("  результаты:", ", ".join(sorted({p.name for p in folders})))
    # Пропуск на любом этапе — не успех: код возврата это покажет.
    return 0 if frames and not failed and not skipped else 1


def build_request(a, workflow):
    """Аргументы CLI -> задание. Один и тот же JobRequest получает и страница."""
    import jobs
    req = jobs.JobRequest(
        image=a.image, workflow=workflow, scene=a.scene or "",
        variants=max(1, a.variants), model=a.model,
        subject=a.subject if a.no_llm else None,
        negative_extra=a.negative_extra, negative_drop=a.negative_drop,
        denoise=a.denoise, cn_strength=a.cn_strength, style_weight=a.style_weight,
        seed=a.seed, force=a.force, recheck=a.recheck, keep_llm=a.keep_llm,
        unload_all=a.unload_all, comfy=a.comfy, tags=not a.no_tags,
        generate=a.generate and not a.dry_run,
        dry_run=a.dry_run, save_preset=a.save_preset, write_cache=True,
    )
    if a.preset:
        p = preset_path(a.preset)
        if not p.exists():
            sys.exit(f"нет пресета {p}")
        req.description = json.loads(p.read_text(encoding="utf-8"))
        req.description_source = f"пресета {p.name}"
    elif a.from_json:
        req.description = json.loads(Path(a.from_json).read_text(encoding="utf-8"))
        req.description_source = a.from_json
    return req


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
    ap.add_argument("--no-tags", action="store_true",
                    help="не дописывать в subject теги WD14-теггера")
    ap.add_argument("--no-think", action="store_true",
                    help="размышления выключены по умолчанию; флаг оставлен для явности")
    ap.add_argument("--think", action="store_true",
                    help="включить размышления модели описания (86 с против 6 с на фото)")
    ap.add_argument("--keep-llm", action="store_true")
    ap.add_argument("--unload-all", action="store_true",
                    help="выгрузить из LM Studio все модели, а не только свою (трогает чужие задачи)")
    ap.add_argument("--comfy", help="адрес ComfyUI, по умолчанию автоопределение")
    ap.add_argument("--negative-extra", default="", help="дописать в негатив вручную")
    ap.add_argument("--negative-drop", default="", help="вычеркнуть слова из негатива LLM")
    ap.add_argument("--denoise", type=float, help="перебить denoise основного сэмплера")
    ap.add_argument("--cn-strength", type=float,
                    help="перебить силу ControlNet (глубина/поза); ниже — свободнее сцена, "
                         "выше — точнее повтор исходной геометрии")
    ap.add_argument("--style-weight", type=float,
                    help="перебить вес стилевой LoRA (по умолчанию как в сборке: 0.9 / 0.85 / 0.6)")
    ap.add_argument("--seed", type=int)
    ap.add_argument("--force", action="store_true", help="не брать описание из кэша")
    ap.add_argument("--dry-run", action="store_true", help="показать промпт, ничего не запускать")
    ap.add_argument("--generate", action="store_true")
    a = ap.parse_args()

    # Размышления выключаются через окружение: так их видят и CLI, и страница,
    # и подмены в проверках — без глобальной переменной и лишних аргументов.
    if a.no_think:
        os.environ["LM_THINKING"] = "off"
    if a.think:
        os.environ["LM_THINKING"] = "on"

    workflow = resolve_workflow(a.workflow, a.style)
    if a.batch:
        sys.exit(run_batch(a, workflow))
        return
    if not a.image:
        sys.exit("нужен --image или --batch")
    a.workflow = workflow
    if a.no_llm and not a.subject:
        sys.exit("--no-llm требует --subject")

    import jobs
    result = jobs.run_job(build_request(a, workflow), emit=jobs.cli_sink)
    if result.code != 0:
        sys.exit(result.code)
    if a.dry_run or not a.generate:
        return
    sys.exit(0)


if __name__ == "__main__":
    settings.utf8_streams()
    main()
