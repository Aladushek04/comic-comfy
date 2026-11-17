"""Сборка positive и negative: единственное место, где склеивается промпт.

Стилевой блок приходит из meta графа (`positive_template` с `{{subject}}` и
`negative`), а не из таблицы CLI: поэтому CLI, предпросмотр и страница выдают
одни и те же байты (AGENTS 0.5). Нет шаблона — отказ (`refuse_reason`), а не
тихая подстановка из `STYLE_PREFIX`: промпт уходит в граф целиком, и догадка
здесь дороже ошибки.

Порядок и рамка:

  * `join_tags` склеивает без дублей и сохраняет порядок. Важное ставим в
    начало: ComfyUI режет текст на чанки по 77 токенов и кодирует их подряд,
    ничего не теряя, но дальние чанки влияют слабее (AGENTS 0.1);
  * якорь тона кожи (`meta.skin_anchor`, сейчас только у 08z) идёт **перед**
    шаблоном, а не в хвост: замер на одном фото показал, что те же слова после
    субъекта не двигали ничего, а в начале — двигали (AGENTS 0.22). Субъект,
    который сам называет тон, якорь отменяет (`names_skin`);
  * негатив всегда замыкается рамкой `NEG_GUARD` — она последнее, что попадает
    в строку. `join_tags` выкидывает уже сказанное, поэтому в хвосте остаётся
    «underage», а не весь блок;
  * `with_adult` (AGENTS 0.18) вызывается **до** сборки, в конвейере: здесь
    только склейка, поэтому «рамка возраста» не может быть забыта в одном месте
    и применена в другом.

`landscape` и имя файла — аргументы `build()`: на текст они не влияют, но
предпросмотр обязан передать те же входы, что и кадр, и `applied` это
показывает. Переопределения (`positive_override`/`negative_override`) сюда не
входят: это явная замена текста целиком, её делает вызывающий — `build_payload`
пропускает их через `comic.guard`, чтобы рамка возраста не снималась.
"""
import re
from typing import NamedTuple

# Плейсхолдер субъекта в шаблоне промпта из build_workflows.py.
SUBJECT_SLOT = "{{subject}}"

# Рамка возраста: последнее, что попадает в негатив на любом пути.
NEG_GUARD = "child, loli, shota, underage"

# Запасное ядро негатива: только для старого .api.json без meta. У сборок свой
# негатив из графа (у GTA «night, rain, dark», у bimbo «white lenses, grey skin»).
NEG_CORE = ("photorealistic, 3d render, bad anatomy, bad hands, extra fingers, "
            "watermark, text, signature, blurry, low quality")

# Тон кожи, который субъект может назвать сам. Якорь сборки (`meta.skin_anchor`)
# ставится только к субъекту без своего тона: два противоположных указания в
# одном промпте — и ранний токен победил бы, то есть ровно наоборот нужному
# (AGENTS 0.22). Слова сравниваются целиком: `tan skin` не ловится в `tanned`.
SKIN_TONES = ("dark skin", "brown skin", "ebony", "tan skin", "olive skin",
              "pale skin", "fair skin", "light skin")

# Цвета радужки, которые VLM называет по закрытым глазам (AGENTS 0.18).
EYE_COLOURS = ("blue eyes", "green eyes", "brown eyes", "hazel eyes", "grey eyes",
               "gray eyes", "amber eyes", "red eyes", "violet eyes", "black eyes")


def names_skin(text):
    """Субъект уже называет тон кожи: `dark skin`, `ebony`, `pale skin` и т. п."""
    low = str(text or "").lower()
    for tone in SKIN_TONES:
        words = tone.split()
        body = r"[\s\-]+".join(re.escape(w) for w in words)
        if re.search(r"(?<![a-z])" + body + r"(?![a-z])", low):
            return True
        # «dark-skinned» называет тот же тон, что и «dark skin»: пропустить его
        # значило бы поставить светлый якорь тёмнокожему субъекту.
        if len(words) == 2 and re.search(
                r"(?<![a-z])" + re.escape(words[0]) + r"[\s\-]+skinned(?![a-z])", low):
            return True
    return False


def drop_eye_colour(subject):
    """Убирает цвет радужки из субъекта: по закрытым глазам его не видно.

    VLM на фото с закрытыми глазами всё равно называет цвет (на деле сказал
    `green eyes` по опущенным векам), а база потом рисует выдуманный цвет.
    Список цветов закрытый: `EYE_COLOURS`, иначе `eye makeup` и `eye contact`
    попали бы под нож.
    """
    parts = [p.strip() for p in str(subject or "").split(",")]
    kept = [p for p in parts if p and p.lower() not in EYE_COLOURS]
    return ", ".join(kept)


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


class PromptError(ValueError):
    """Собрать промпт нельзя: в meta нет годного стилевого блока."""


class BuiltPrompt(NamedTuple):
    """Итог сборки: строки для графа и то, что реально применено.

    `applied` — не `positive`: здесь входы и источник шаблона, по ним видно, что
    предпросмотр собрал промпт с теми же landscape и файлом, что и кадр.
    """
    positive: str
    negative: str
    applied: dict


class PromptBuilder:
    """meta графа (+ привязки полей) -> промпт кадра.

    `fields` — `meta.fields` из `.api.json`: если их передали, builder знает,
    куда пойдёт текст, и отказывается собирать промпт, которому нет привязки.
    """

    def __init__(self, meta, fields=None, tags_known=True):
        self.meta = dict(meta or {})
        self.fields = dict(fields or {})
        # Считались ли теги WD14. Нужны, чтобы отличить «теги сказали, что тон
        # кожи не назван» от «тегов не было вовсе»: во втором случае свидетельства
        # нет, и ставить светлый якорь тёмнокожему субъекту нельзя.
        self.tags_known = bool(tags_known)

    def template(self):
        """Шаблон positive из графа (пустая строка, если его нет)."""
        return str(self.meta.get("positive_template") or "")

    def refuse_reason(self):
        """Почему этим meta собирать нельзя. None — можно."""
        tpl = self.template()
        if not tpl:
            return ("в meta нет positive_template: стиль берётся из графа, а не из "
                    "таблицы CLI — пересобери воркфлоу")
        if SUBJECT_SLOT not in tpl:
            return f"в positive_template нет {SUBJECT_SLOT}: субъект некуда подставить"
        if self.fields and "positive" not in self.fields:
            return "в meta.fields нет привязки positive: собранный промпт некуда подставить"
        return None

    def skin_anchor(self, subject):
        """Якорь тона кожи: (текст в начало positive, что с ним стало).

        Сборка задаёт его только там, где база уводит светлую кожу в тёмную
        (08z под GTA-палитрой). Ставится в самое начало: ранние токены весят
        больше (AGENTS 0.1), и замер это подтвердил — те же слова после
        субъекта не двигали ничего, а в начале — двигали (AGENTS 0.22).
        Субъект со своим тоном якорь отменяет: иначе промпт говорит две
        противоположные вещи, и побеждает ранний токен.
        """
        anchor = str(self.meta.get("skin_anchor") or "").strip()
        if not anchor:
            return "", ""
        if names_skin(subject):
            return "", "skipped: subject names skin tone"
        if not self.tags_known:
            # Теги не считались: молчание описания здесь ничего не доказывает.
            # Ошибка в одну сторону дешевле: без якоря светлая кожа уйдёт в
            # загар (как было до него), с якорем тёмнокожей субъект побелеет.
            return "", "skipped: tags not counted"
        return anchor, "applied"

    def positive(self, subject):
        """Шаблон сборки с подставленным субъектом; якорь тона кожи — первым."""
        reason = self.refuse_reason()
        if reason:
            raise PromptError(reason)
        body = str(subject or "").strip().rstrip(",")
        anchor, _ = self.skin_anchor(subject)
        return join_tags(anchor, self.template().replace(SUBJECT_SLOT, body))

    def negative(self, negative_extra=""):
        """Сначала подавляемое на этом фото, потом негатив сборки, потом рамка."""
        return join_tags(negative_extra, self.meta.get("negative") or NEG_CORE, NEG_GUARD)

    def build(self, subject, negative_extra="", *, landscape=False, image_name=None):
        """Обе строки за один вызов: один путь для CLI, панели и предпросмотра."""
        applied = {
            "template": "meta.positive_template",
            "landscape": bool(landscape),
            "image_name": str(image_name or ""),
        }
        # Что стало с якорем тона кожи — видно и в предпросмотре, и в паспорте
        # кадра: молчание оставило бы их одинаковыми при разных промптах.
        anchor_state = self.skin_anchor(subject)[1]
        if anchor_state:
            applied["skin_anchor"] = anchor_state
        return BuiltPrompt(self.positive(subject), self.negative(negative_extra), applied)


def skin_anchor_state(meta, subject, tags_known=True):
    """Что стало с якорем тона кожи: `applied`, `skipped: …` или пусто.

    Для тех, кто собирает positive не через `build()` (переопределение
    промпта в `photo_prompt.build_payload`): якорь там не ставится, и записать
    это честнее, чем промолчать. `tags_known=False` — теги WD14 не считались,
    и молчание описания о тоне кожи тогда ничего не доказывает.
    """
    return PromptBuilder(meta, tags_known=tags_known).skin_anchor(subject)[1]
