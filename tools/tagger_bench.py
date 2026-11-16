"""Замер: что из описания VLM подтверждает теггер, а что он видит сверх.

Стенд отвечает на один вопрос: сколько деталей описания держится на самом фото,
а сколько добавлено моделью «по смыслу» и пропущено. Для каждого кэша
`out\\<фото>_<хэш>.prompt.json` берётся исходное фото из input, гоняется WD14-теггер
(onnxruntime, CPU, один проход 448x448) и сравнивается с тегами `subject`.

Что печатается:
  * покрытие: какие теги VLM теггер подтверждает слово в слово;
  * «видит сверх»: детали, которые теггер нашёл, а описание пропустило
    (разложены по полкам: анатомия, одежда/материал, аксессуар, поза, сцена, свет);
  * спор с негативом: тег в `negative_extra`, который теггер уверенно видит на фото.

Видеопамять не трогается: только CPU. Модель качается по `--download` и лежит
в `out\\models\\tagger\\<имя>`, повторный запуск в сеть не ходит.

    python tools\\tagger_bench.py --model eva02-large --download
    python tools\\tagger_bench.py --model swinv2 --threshold 0.25 --limit 5
"""
import argparse
import json
import os
import sys
from pathlib import Path

import settings
import tagger

OUT = settings.ROOT / "out"
INPUT_DIR = settings.INPUT_DIR
IMG_EXT = (".png", ".jpg", ".jpeg", ".webp")

# Негатив часто пишут как «чего не должно быть»: такие теги не спорят с фото.
ANTI = ("missing", "opaque", "plain", "closed", "extra", "fewer", "fully", "absent",
        "wrong", "bad ", "no ", "open ", "empty")


def read_caches():
    """Пары (фото, кэш). Сценические варианты и старые кэши без хэша отмечаем, но берём."""
    pairs, skipped = [], []
    for path in sorted(OUT.glob("*.prompt.json")):
        stem = path.name[: -len(".prompt.json")]
        if "__s-" in stem:
            skipped.append((stem, "сценический вариант того же фото"))
            continue
        photo = next((INPUT_DIR / (stem + ext) for ext in IMG_EXT
                      if (INPUT_DIR / (stem + ext)).exists()), None)
        if photo is None:
            skipped.append((stem, "нет исходного фото в input"))
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as e:
            skipped.append((stem, f"кэш не читается: {e}"))
            continue
        if not str(data.get("subject") or "").strip():
            skipped.append((stem, "пустой subject"))
            continue
        pairs.append((photo, data, path))
    return pairs, skipped


def compare(data, tagger_tags):
    """Разбор одного фото: покрытие, пропуски и спор с негативом."""
    vlm = [t.strip() for t in str(data.get("subject") or "").split(",") if t.strip()]
    neg = [t.strip() for t in str(data.get("negative_extra") or "").split(",") if t.strip()]
    names = [name for name, _ in tagger_tags]

    covered, missing = [], []
    for tag in vlm:
        if tagger.norm(tag) in tagger.PERSON:
            continue
        hit = next((g for g in names
                    if tagger.matches(tag, g) or tagger.contained(g, tag)), None)
        (covered if hit else missing).append((tag, hit))

    extra = []
    for name, score in tagger_tags:
        if tagger.norm(name) in tagger.META:
            continue
        if any(tagger.matches(tag, name) or tagger.contained(name, tag) for tag in vlm):
            continue
        extra.append((name, score, tagger.bucket_of(name)))

    conflicts = []
    for tag in neg:
        if tagger.norm(tag).startswith(ANTI):
            continue
        hit = next((g for g in names
                    if tagger.matches(tag, g) or tagger.contained(g, tag)), None)
        if hit:
            conflicts.append((tag, hit))

    return {"vlm": vlm, "negative": neg, "covered": covered, "missing": missing,
            "extra": extra, "conflicts": conflicts}


def report(results, skipped, args):
    """Markdown-отчёт: сначала итог, потом каждое фото."""
    lines = ["# Что теггер подтверждает в описаниях VLM", ""]
    lines.append(f"Модель: `{tagger.REPOS[args.model]}` (`{args.model}`), порог {args.threshold}.")
    lines.append(f"Фото в замере: {len(results)}, пропущено: {len(skipped)}.")
    lines.append("")

    total_cov = sum(len(r["covered"]) for r in results)
    total_base = total_cov + sum(len(r["missing"]) for r in results)
    total_extra = sum(len(r["extra"]) for r in results)
    if total_base:
        lines.append(f"- покрыто теггером: **{total_cov} из {total_base}** тегов описания"
                     f" ({100 * total_cov / total_base:.0f}%)")
    else:
        lines.append("- покрытие: нет данных")
    lines.append(f"- теггер видит сверх описания: **{total_extra}** тегов")
    conflicts = sum(len(r["conflicts"]) for r in results)
    lines.append(f"- спор с негативом: **{conflicts}** тегов")
    if results:
        avg = sum(r.get("ms", 0) for r in results) / len(results)
        lines.append(f"- время на фото (CPU): **{avg:.0f} мс**")
    lines.append("")

    by_bucket = {}
    for r in results:
        for name, score, bucket in r["extra"]:
            by_bucket.setdefault(bucket, []).append((name, score))
    if by_bucket:
        lines += ["## Что пропущено, по полкам", ""]
        for bucket in sorted(by_bucket, key=lambda b: -len(by_bucket[b])):
            freq = {}
            for name, _ in by_bucket[bucket]:
                freq[name] = freq.get(name, 0) + 1
            top = sorted(freq.items(), key=lambda kv: -kv[1])[:12]
            lines.append(f"**{bucket}** ({len(by_bucket[bucket])}): "
                         + ", ".join(f"`{n}`" + (f" ×{c}" if c > 1 else "") for n, c in top))
            lines.append("")

    for r in results:
        lines += [f"## {r['name']}", ""]
        lines.append("Описание: " + ", ".join(f"~~{t}~~" if h else t for t, h in r["covered"]))
        lines.append("")
        if r["missing"]:
            lines.append("Не подтверждено: " + ", ".join(t for t, _ in r["missing"]))
            lines.append("")
        if r["extra"]:
            lines.append("Теггер видит сверх: "
                         + ", ".join(f"`{n}` {s:.2f}" for n, s, _ in r["extra"][:25]))
            lines.append("")
        if r["conflicts"]:
            lines.append("Спор с негативом: " + ", ".join(f"`{a}` → `{b}`" for a, b in r["conflicts"]))
            lines.append("")

    if skipped:
        lines += ["## Пропущено", ""]
        lines += [f"- `{stem}` — {why}" for stem, why in skipped]
        lines.append("")
    return "\n".join(lines)


def main():
    import time

    settings.utf8_streams()
    ap = argparse.ArgumentParser(description="Сверка описаний VLM с теггером WD14")
    ap.add_argument("--model", default=tagger.BASE_MODEL, choices=sorted(tagger.REPOS))
    ap.add_argument("--model-dir", default="",
                    help="каталог моделей (по умолчанию out\\models\\tagger)")
    ap.add_argument("--threshold", type=float, default=0.35)
    ap.add_argument("--limit", type=int, default=0, help="сколько фото взять (0 — все)")
    ap.add_argument("--download", action="store_true", help="скачать модель, если её нет")
    ap.add_argument("--report", default="", help="по умолчанию out\\bench\\tagger_<модель>.md")
    a = ap.parse_args()
    if a.model_dir:
        os.environ["COMIC_TAGGER_DIR"] = a.model_dir
    if not a.report:
        a.report = str(OUT / "bench" / f"tagger_{a.model}.md")

    pairs, skipped = read_caches()
    if a.limit:
        pairs = pairs[: a.limit]
    if not pairs:
        sys.exit("Не нашлось ни одного фото с описанием — проверь out\\*.prompt.json и input.")

    tagger.ensure(a.model, a.download)
    print(f"теггер: {a.model}, порог {a.threshold}, фото: {len(pairs)}")

    results = []
    for photo, data, cache in pairs:
        started = time.perf_counter()
        found = tagger.infer(a.model, photo, a.threshold)
        elapsed = time.perf_counter() - started
        r = compare(data, found)
        r["name"] = photo.stem
        r["photo"] = str(photo)
        r["cache"] = cache.name
        r["ms"] = round(elapsed * 1000)
        r["tagger"] = [[n, round(s, 3)] for n, s in found]
        results.append(r)
        print(f"  {photo.stem}: тегов описания {len(r['covered']) + len(r['missing'])}, "
              f"подтверждено {len(r['covered'])}, сверх {len(r['extra'])}, {r['ms']} мс")

    Path(a.report).parent.mkdir(parents=True, exist_ok=True)
    Path(a.report).write_text(report(results, skipped, a), encoding="utf-8")
    Path(a.report).with_suffix(".json").write_text(
        json.dumps({"model": a.model, "threshold": a.threshold,
                    "results": results, "skipped": skipped},
                   ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\nотчёт: {a.report}")


if __name__ == "__main__":
    main()
