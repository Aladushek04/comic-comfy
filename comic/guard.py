"""Возрастная рамка: единственное место, где она ставится.

Раньше `with_adult`/`scrub_tags` жили в `photo_prompt.py` и применялись только к
описанию от VLM. Полные переопределения промпта (`positive_override`,
`negative_override`) шли в граф как есть, то есть для проекта 18+ рамку можно
было снять по желанию — единственная такая дыра (аудит, риск «переопределения
обходят возрастную рамку»). Теперь санитайзер и замыкание негатива лежат здесь,
а `build_payload` зовёт их на любом пути: CLI, страница, предпросмотр.

  * `sanitize_subject` — прежний `with_adult`: вырезает возрастные слова и
    ставит взрослого первым тегом с явным родом;
  * `ensure_guard` — дописывает в конец негатива недостающие слова `NEG_GUARD`,
    не переставляя и не переписывая уже написанное: текст переопределения
    остаётся тем, что ввёл человек, плюс рамка.

`photo_prompt.with_adult` остался обёрткой: прежние имена и поведение на месте.
"""
import re

from comic.prompt import NEG_GUARD

# Возрастные слова, которые модель любит добавлять в описание.
AGE_WORDS = ("young", "teen", "teenage", "teenager", "girl", "boy", "child", "kid",
             "schoolgirl", "schoolboy", "adolescent", "loli", "shota", "underage", "minor")

GENERIC_PERSON = {"woman", "women", "lady", "female", "man", "male", "guy",
                  "gentleman", "person"}


def _word_in(word, text):
    """Слово целиком (text уже в нижнем регистре): `girl` не в `schoolgirl`."""
    return re.search(r"(?<![a-z])" + re.escape(word) + r"(?![a-z])", text) is not None


def scrub_tags(text):
    """Убирает возрастные слова и точные дубли, сохраняя порядок."""
    out, seen = [], set()
    for part in str(text).split(","):
        tag = part.strip().strip(".")
        if not tag:
            continue
        low = tag.lower()
        if any(_word_in(w, low) for w in AGE_WORDS):
            continue
        if low in seen:
            continue
        seen.add(low)
        out.append(tag)
    return out


def sanitize_subject(text):
    """Взрослый — первым тегом, с явным родом. Убирает дубли-роды и всё школьное."""
    raw = scrub_tags(text)
    if not raw:
        return ""
    low = str(text).lower()
    male = _word_in("man", low) or _word_in("male", low) or _word_in("guy", low) \
        or _word_in("gentleman", low) or _word_in("boy", low)
    female = _word_in("woman", low) or _word_in("women", low) or _word_in("female", low) \
        or _word_in("lady", low) or _word_in("girl", low)
    man = male and not female

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


def ensure_guard(negative):
    """Замыкает негатив рамкой, не переставляя уже написанное.

    Недостающие слова `NEG_GUARD` дописываются в конец в своём порядке. Уже
    названное остаётся на месте: негатив переопределения — текст человека, и
    единственное, что мы вправе к нему добавить, — рамку.
    """
    text = str(negative or "").strip()
    low = text.lower()
    missing = [t.strip() for t in NEG_GUARD.split(",")
               if t.strip() and not _word_in(t.strip(), low)]
    if not missing:
        return text
    tail = ", ".join(missing)
    return f"{text.rstrip(', ')}, {tail}" if text else tail
