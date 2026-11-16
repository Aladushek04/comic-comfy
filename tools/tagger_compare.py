"""Сравнение теггеров по уже собранным замерам `tagger_bench.py`.

Читает `out\\bench\\tagger_*.json` (их пишет замер, по файлу на модель) и сводит:
  * итог по модели: покрытие описания, теги сверх, тегов на фото, мс на фото;
  * согласие: сколько пар «фото + тег» видят все модели, сколько — только одна;
  * анатомию: на скольких фото из 17 каждая модель видит ключевые теги;
  * мусор: теги, которые в промпт Illustrious пускать нельзя.

    python tools\\tagger_compare.py
    python tools\\tagger_compare.py --report out\\bench\\tagger_compare.md
"""
import argparse
import json
import sys
from pathlib import Path

import settings
import tagger

BENCH = settings.ROOT / "out" / "bench"

# Ключевая анатомия: ровно то, что описания VLM пропускают.
ANATOMY = ("large breasts", "huge breasts", "medium breasts", "cleavage", "nipples",
           "areola", "areolae", "navel", "pubic hair", "thighhighs", "thick thighs",
           "wide hips", "hips", "thighs", "bare shoulders", "collarbone")


def same_concept(want, tag):
    """Тег содержит все слова искомого: `dark areolae` закрывает `areola`,
    а голое `breasts` не закрывает `large breasts`."""
    wa = {tagger.singular(w) for w in tagger.norm(want).split()}
    wb = {tagger.singular(w) for w in tagger.norm(tag).split()}
    return bool(wa) and wa <= wb


def load():
    """Замеры с диска. Два файла на одну модель — берём свежий и говорим об этом."""
    files = sorted(BENCH.glob("tagger_*.json"), key=lambda p: p.stat().st_mtime)
    out, dups = {}, []
    for path in files:
        data = json.loads(path.read_text(encoding="utf-8"))
        model = data.get("model") or path.stem
        if model in out:
            dups.append(f"{path.name} перекрывает {out[model][0]}")
        out[model] = (path.name, data)
    return {model: data for model, (_, data) in out.items()}, dups


def agree(runs):
    """Сколько моделей видят каждую пару (фото, тег) — по всем моделям сразу."""
    seen = {}
    for name, data in runs.items():
        for r in data["results"]:
            for tag, score in r["tagger"]:
                key = (r["name"], tagger.norm(tag))
                if key[1] in tagger.META:
                    continue
                seen.setdefault(key, {})[name] = score
    counts = {1: [], 2: [], 3: []}
    for (photo, tag), models in seen.items():
        counts[min(len(models), 3)].append((photo, tag, models))
    return counts


def anatomy_rate(runs):
    """На скольких фото модель видит ключевой анатомический тег."""
    table = {}
    for name, data in runs.items():
        per_photo = [[tagger.norm(t) for t, _ in r["tagger"]] for r in data["results"]]
        table[name] = {want: sum(1 for tags in per_photo
                                 if any(same_concept(want, t) for t in tags))
                       for want in ANATOMY}
    return table


def main():
    settings.utf8_streams()
    ap = argparse.ArgumentParser(description="Сводка по теггерам")
    ap.add_argument("--report", default=str(BENCH / "tagger_compare.md"))
    a = ap.parse_args()

    runs, dups = load()
    if len(runs) < 2:
        sys.exit("Нужно минимум два замера: python tools\\tagger_bench.py --model ...")
    for note in dups:
        print(f"внимание: {note}")

    lines = ["# Сравнение теггеров", "", f"Замеров: {len(runs)}, фото: "
             f"{max(len(d['results']) for d in runs.values())}.", ""]

    header = ["Модель", "Покрытие описания", "Сверх", "Тегов/фото", "Мс/фото", "Мусор"]
    rows = []
    for name, data in sorted(runs.items()):
        res = data["results"]
        cov = sum(len(r["covered"]) for r in res)
        base = cov + sum(len(r["missing"]) for r in res)
        extra = sum(len(r["extra"]) for r in res)
        tags = sum(len(r["tagger"]) for r in res)
        ms = sum(r.get("ms", 0) for r in res) / max(len(res), 1)
        junk = sum(1 for r in res for t, _ in r["tagger"] if tagger.norm(t) in tagger.JUNK)
        rows.append([name, f"{100 * cov / base:.0f}% ({cov}/{base})" if base else "—",
                     str(extra), f"{tags / max(len(res), 1):.1f}", f"{ms:.0f}", str(junk)])

    lines.append("| " + " | ".join(header) + " |")
    lines.append("|" + "---|" * len(header))
    for row in rows:
        lines.append("| " + " | ".join(row) + " |")
    lines.append("")

    counts = agree(runs)
    lines += ["## Согласие", ""]
    lines.append(f"- видят все модели: **{len(counts[3])}** пар «фото + тег»")
    lines.append(f"- видят две из трёх: **{len(counts[2])}**")
    lines.append(f"- видит только одна: **{len(counts[1])}**")
    lines.append("")
    if counts[1]:
        lines.append("### Только одна модель (первые 20)")
        lines.append("")
        for photo, tag, models in sorted(counts[1], key=lambda x: x[0])[:20]:
            who = ", ".join(f"{m} {s:.2f}" for m, s in sorted(models.items()))
            lines.append(f"- `{photo}` / `{tag}` — {who}")
        lines.append("")

    rate = anatomy_rate(runs)
    lines += ["## Анатомия: на скольких фото найдено", ""]
    names = sorted(rate)
    lines.append("| Тег | " + " | ".join(names) + " |")
    lines.append("|---|" + "---|" * len(names))
    for want in ANATOMY:
        lines.append(f"| `{want}` | " + " | ".join(str(rate[n][want]) for n in names) + " |")
    lines.append("")

    Path(a.report).parent.mkdir(parents=True, exist_ok=True)
    Path(a.report).write_text("\n".join(lines), encoding="utf-8")

    print(" | ".join(header))
    for row in rows:
        print(" | ".join(row))
    print(f"\nсогласие: все {len(counts[3])}, две {len(counts[2])}, одна {len(counts[1])}")
    print(f"отчёт: {a.report}")


if __name__ == "__main__":
    main()
