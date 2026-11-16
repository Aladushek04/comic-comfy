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
  * негатив всегда замыкается рамкой `NEG_GUARD` — она последнее, что попадает
    в строку. `join_tags` выкидывает уже сказанное, поэтому в хвосте остаётся
    «underage», а не весь блок;
  * `with_adult` (AGENTS 0.18) вызывается **до** сборки, в конвейере: здесь
    только склейка, поэтому «рамка возраста» не может быть забыта в одном месте
    и применена в другом.

`landscape` и имя файла — аргументы `build()`: на текст они не влияют, но
предпросмотр обязан передать те же входы, что и кадр, и `applied` это
показывает. Переопределения (`positive_override`/`negative_override`) сюда не
входят: это явная замена текста целиком, её делает вызывающий.
"""
from typing import NamedTuple

# Плейсхолдер субъекта в шаблоне промпта из build_workflows.py.
SUBJECT_SLOT = "{{subject}}"

# Рамка возраста: последнее, что попадает в негатив на любом пути.
NEG_GUARD = "child, loli, shota, underage"

# Запасное ядро негатива: только для старого .api.json без meta. У сборок свой
# негатив из графа (у GTA «night, rain, dark», у bimbo «white lenses, grey skin»).
NEG_CORE = ("photorealistic, 3d render, bad anatomy, bad hands, extra fingers, "
            "watermark, text, signature, blurry, low quality")


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

    def __init__(self, meta, fields=None):
        self.meta = dict(meta or {})
        self.fields = dict(fields or {})

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

    def positive(self, subject):
        """Шаблон сборки с подставленным субъектом."""
        reason = self.refuse_reason()
        if reason:
            raise PromptError(reason)
        body = str(subject or "").strip().rstrip(",")
        return join_tags(self.template().replace(SUBJECT_SLOT, body))

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
        return BuiltPrompt(self.positive(subject), self.negative(negative_extra), applied)
