"""Браузерные проверки интерфейса на заглушках LM Studio и ComfyUI.

Запускает две заглушки, поднимает `webapp/run.py` на свободном порту и водит
настоящий Chromium: раскрывает расширенные настройки, меняет фото, проверяет
сохранение ввода после health-check, повторный кадр Recheck, две вкладки,
потерю связи браузера и отказ WebSocket ComfyUI. Модели и GPU не нужны.

    .venv-web\\Scripts\\python.exe tools\\test_web_ui.py
    .venv-web\\Scripts\\python.exe tools\\test_web_ui.py --shots ui-shots
"""
import argparse
import contextlib
import io
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TMP = Path(tempfile.mkdtemp(prefix="comic_ui_test_"))

# settings читает окружение при импорте: задаём пути до импорта photo_prompt.
os.environ["COMFY_OUTPUT"] = str(TMP / "output")
os.environ["COMFY_INPUT"] = str(TMP / "input")
os.environ["PYTHONIOENCODING"] = "utf-8"
os.environ["PYTHONUTF8"] = "1"
for path in (str(ROOT / "tools"),):
    if path not in sys.path:
        sys.path.insert(0, path)

import test_web_app as tw                                    # noqa: E402
from playwright.sync_api import sync_playwright               # noqa: E402

passed, failed = [], []


def check(name, cond, detail=""):
    (passed if cond else failed).append(name)
    print(("  ok   " if cond else "  FAIL ") + name
          + (f" — {detail}" if detail and not cond else ""))


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def pick_photo(env, *names):
    """Фото для проверки: env, иначе картинка из репозитория."""
    wanted = os.environ.get(env)
    if wanted and Path(wanted).exists():
        return Path(wanted)
    for name in names:
        path = ROOT / name
        if path.exists():
            return path
    raise SystemExit(f"нет картинки для проверки: задай {env}")


def web_python():
    for name in (".venv-web/Scripts/python.exe", ".venv-web/bin/python"):
        p = ROOT / name
        if p.exists():
            return str(p)
    raise SystemExit("нужен .venv-web: NiceGUI и playwright живут там")


def wait_http(url, timeout=30):
    t0 = time.time()
    while time.time() - t0 < timeout:
        try:
            with urllib.request.urlopen(url, timeout=2) as r:
                if r.status == 200:
                    return True
        except Exception:
            time.sleep(0.4)
    return False


def start_app(lm, comfy, port, log):
    env = os.environ.copy()
    env.update({
        "COMFY_URL": comfy.url,
        "LM_URL": lm.url,
        "COMFY_OUTPUT": str(TMP / "output"),
        "COMFY_INPUT": str(TMP / "input"),
        "COMIC_THEME_FILE": str(TMP / "theme.json"),
        "COMIC_TAGGER": "off",
        "PYTHONIOENCODING": "utf-8",
        "PYTHONUTF8": "1",
    })
    out = open(log, "w", encoding="utf-8")
    proc = subprocess.Popen([web_python(), str(ROOT / "webapp" / "run.py"),
                             "--port", str(port), "--no-browser"],
                            cwd=str(ROOT), env=env, stdout=out, stderr=subprocess.STDOUT)
    if not wait_http(f"http://127.0.0.1:{port}/"):
        proc.terminate()
        raise SystemExit("приложение не поднялось:\n" + log.read_text(encoding="utf-8")[-3000:])
    return proc, out


def body(page):
    return page.evaluate("() => document.body.innerText")


def wait_text(page, text, timeout=30000):
    page.wait_for_function("t => document.body.innerText.includes(t)", arg=text,
                           timeout=timeout)


def wait_upload(page, timeout=15000):
    """Загрузка закончена: загрузчик скрылся, превью на месте."""
    page.wait_for_function(
        "() => { const u = document.querySelector('.q-uploader');"
        " if (!u || getComputedStyle(u).display !== 'none') return false;"
        " const img = [...document.querySelectorAll('img')]"
        "   .find(i => (i.getAttribute('src') || '').includes('/media/upload/'));"
        " return !!img && img.naturalWidth > 0; }", timeout=timeout)
    page.wait_for_timeout(250)


def field_value(page, label):
    return page.locator(".q-field", has_text=label).locator("input").first.input_value()


def fill_field(page, label, value):
    page.locator(".q-field", has_text=label).locator("input").first.fill(str(value))


def job_from_url(page):
    search = page.evaluate("() => location.search")
    return search.split("job=")[-1] if "job=" in search else ""


def api_job(port, job_id):
    url = f"http://127.0.0.1:{port}/api/job"
    if job_id:
        url += f"?job_id={job_id}"
    with urllib.request.urlopen(url, timeout=10) as r:
        return json.loads(r.read())


def control(comfy, **values):
    req = urllib.request.Request(comfy.url + "/_control",
                                 data=json.dumps(values).encode(),
                                 headers={"Content-Type": "application/json"})
    urllib.request.urlopen(req, timeout=5).read()


def http_ok(url):
    try:
        with urllib.request.urlopen(url, timeout=10) as r:
            return r.status == 200 and bool(r.read())
    except Exception:
        return False


def fetch(url):
    """Статус и текст страницы: /v2 проверяется и без браузера."""
    try:
        with urllib.request.urlopen(url, timeout=10) as r:
            return r.status, r.read().decode("utf-8", "replace")
    except Exception as e:
        return 0, str(e)


def rgb(hex_value):
    """#7c3aed → rgb(124, 58, 237): так браузер отдаёт вычисленный цвет."""
    h = hex_value.lstrip("#")
    return "rgb(%d, %d, %d)" % (int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16))


def wait_v2_upload(page, timeout=20000):
    """Загрузка на /v2: рамка скрыта, превью с картинкой на месте."""
    page.wait_for_function(
        "() => { const i = Array.from(document.querySelectorAll('img'))"
        ".find(x => (x.getAttribute('src') || '').includes('/media/upload/'));"
        " return !!i && i.naturalWidth > 0; }", timeout=timeout)
    page.wait_for_timeout(300)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--shots", help="куда складывать скриншоты")
    a = ap.parse_args()
    shots = Path(a.shots) if a.shots else Path(
        os.environ.get("TEMP") or tempfile.gettempdir()) / "comic_ui_shots"
    shots.mkdir(parents=True, exist_ok=True)

    photo_a = pick_photo("COMIC_TEST_PHOTO", "preview_07.png", "preview_1.png")
    photo_b = pick_photo("COMIC_TEST_PHOTO_B", "preview_01_style.png", "preview_comic.png")

    lm, comfy = tw.lm_stub(), tw.comfy_stub()
    port = free_port()
    log = TMP / "app.log"
    app = None
    try:
        print("\n0) Ctrl+C останавливает без трейсбека")
        from webapp import run as web_run
        from webapp import ui as web_ui
        saved_start = web_ui.start
        web_ui.start = lambda **kwargs: (_ for _ in ()).throw(KeyboardInterrupt())
        try:
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                code = web_run.main(["--no-browser", "--port", str(free_port())])
        finally:
            web_ui.start = saved_start
        check("код 0 и сообщение об остановке",
              code == 0 and "остановлено" in buf.getvalue(), buf.getvalue()[:160])

        app, _ = start_app(lm, comfy, port, log)
        with sync_playwright() as pw:
            browser = pw.chromium.launch()
            ctx = browser.new_context(viewport={"width": 1440, "height": 900})
            page = ctx.new_page()
            page.goto(f"http://127.0.0.1:{port}/")
            wait_text(page, "Задание не запущено")

            print("\n1) пустой экран и две колонки")
            check("пустой экран: приглашение и результат",
                  "Кадр появится здесь" in body(page))
            size = page.evaluate("""() => {
                const row = [...document.querySelectorAll('.nicegui-row')]
                    .find(r => r.className.includes('items-start'));
                return [...row.children].map(c => {
                    const b = c.getBoundingClientRect();
                    return {x: Math.round(b.x), y: Math.round(b.y), w: Math.round(b.width)};
                });
            }""")
            check("десктоп: настройки слева, результат справа",
                  len(size) == 2 and size[1]["x"] > size[0]["x"] + size[0]["w"] - 5
                  and size[1]["w"] > size[0]["w"], str(size))
            page.screenshot(path=str(shots / "after-empty.png"), full_page=True)

            print("\n1б) тёмная тема и переключатель")
            card_bg = page.evaluate(
                "() => getComputedStyle(document.querySelector('.q-card')).backgroundColor")
            check("тёмная тема по умолчанию: страница, карточки",
                  page.evaluate(
                      "() => document.body.classList.contains('body--dark')") is True
                  and card_bg == "rgb(29, 29, 29)", card_bg)
            page.locator(".comic-theme").click()
            page.wait_for_function(
                "() => !document.body.classList.contains('body--dark')", timeout=10000)
            light_bg = page.evaluate(
                "() => getComputedStyle(document.querySelector('.q-card')).backgroundColor")
            check("переключатель даёт светлую тему", light_bg == "rgb(255, 255, 255)",
                  light_bg)
            page.reload()
            wait_text(page, "Задание не запущено")
            page.wait_for_timeout(500)
            check("выбор темы пережил перезагрузку",
                  page.evaluate(
                      "() => document.body.classList.contains('body--dark')") is False)
            page.locator(".comic-theme").click()
            page.wait_for_function(
                "() => document.body.classList.contains('body--dark')", timeout=10000)
            check("переключатель возвращает тёмную тему",
                  page.evaluate(
                      "() => document.body.classList.contains('body--dark')") is True)

            print("\n2) расширенные настройки внутри панели")
            page.get_by_role("button", name="Расширенные настройки").first.click()
            page.wait_for_timeout(400)
            inside = page.evaluate("""() => {
                const f = [...document.querySelectorAll('.q-field')]
                    .find(e => e.innerText.includes('Denoise'));
                return f ? !!f.closest('.q-expansion-item') : null;
            }""")
            check("Denoise внутри панели", inside is True, str(inside))
            labels = page.evaluate("() => [...document.querySelectorAll('.q-field__label')].map(e => e.innerText)")
            check("подписи «Модель описания» и «Чекпойнт генерации»",
                  any("Модель описания" in x for x in labels)
                  and any("Чекпойнт генерации" in x for x in labels), str(labels))
            check("слоты LoRA подписаны", sum("LoRA ·" in x for x in labels) >= 4,
                  str(labels))
            page.screenshot(path=str(shots / "after-advanced.png"), full_page=True)

            print("\n3) проверка сервисов не стирает ввод")
            fill_field(page, "Denoise", 0.9)
            fill_field(page, "Сцена", "тестовая сцена")
            page.get_by_role("button", name="Проверить сервисы").click()
            page.wait_for_timeout(2500)
            check("denoise остался 0.9", field_value(page, "Denoise") == "0.9",
                  field_value(page, "Denoise"))
            check("сцена осталась", field_value(page, "Сцена") == "тестовая сцена",
                  field_value(page, "Сцена"))
            page.get_by_role("checkbox", name="Recheck").click()
            page.get_by_role("button", name="Проверить сервисы").click()
            page.wait_for_timeout(2000)
            check("recheck остался включён",
                  page.get_by_role("checkbox", name="Recheck").get_attribute(
                      "aria-checked") == "true")

            print("\n4) одно превью и замена фото")
            page.set_input_files('input[type="file"]', str(photo_a))
            wait_upload(page)
            state = page.evaluate("""() => ({
                uploader: (() => { const u = document.querySelector('.q-uploader');
                    return u ? getComputedStyle(u).display !== 'none' : false; })(),
                previews: [...document.querySelectorAll('img')]
                    .filter(i => i.getAttribute('src')?.includes('/media/upload/'))
                    .map(i => ({src: i.getAttribute('src'), w: i.naturalWidth}))
            })""")
            check("полоска загрузчика скрыта", state["uploader"] is False, str(state))
            check("одно превью с картинкой",
                  len(state["previews"]) == 1 and state["previews"][0]["w"] > 0,
                  str(state["previews"]))

            print("\n5) описание и замена фото")
            page.get_by_role("button", name="Только описание").click()
            wait_text(page, "Описание готово", 20000)
            desc_a = page.locator("textarea").first.input_value()
            check("описание заполнилось", "adult woman" in desc_a, desc_a[:60])
            page.get_by_role("button", name="Заменить фото").click()
            page.wait_for_timeout(400)
            check("при замене автоописание очищено",
                  page.locator("textarea").first.input_value() == "")
            page.set_input_files('input[type="file"]', str(photo_b))
            wait_upload(page)
            page.wait_for_timeout(1200)
            check("новое фото не наследует описание",
                  page.locator("textarea").first.input_value() == "",
                  page.locator("textarea").first.input_value()[:60])

            print("\n5a) таймер не затирает пересчитанный промпт")
            page.locator("textarea").first.fill("adult woman, preview persistence test")
            page.get_by_role("button", name="Пересчитать промпт").click()
            page.wait_for_timeout(700)

            def preview_value():
                return page.locator(
                    ".q-field", has_text="Positive, который уйдёт в ComfyUI"
                ).locator("textarea").input_value()

            first_preview = preview_value()
            page.wait_for_timeout(2200)
            second_preview = preview_value()
            check("промпт формы остаётся после обновлений страницы",
                  "preview persistence test" in first_preview
                  and second_preview == first_preview,
                  f"{first_preview[:70]} -> {second_preview[:70]}")

            print("\n6) Recheck: два кадра в галерее")
            page.get_by_role("button", name="Заменить фото").click()
            page.wait_for_timeout(300)
            page.set_input_files('input[type="file"]', str(photo_a))
            wait_upload(page)
            if page.get_by_role("checkbox", name="Recheck").get_attribute("aria-checked") != "true":
                page.get_by_role("checkbox", name="Recheck").click()
            page.get_by_role("button", name="Старт").click()
            wait_text(page, "Готово · кадров 2", 40000)
            job = job_from_url(page)
            frames = api_job(port, job)["frames"]
            check("в задании два кадра", len(frames) == 2, str(len(frames)))
            check("второй кадр уточнённый", frames[-1].get("refined") is True,
                  str(frames))
            gallery = page.evaluate("""() => {
                const card = [...document.querySelectorAll('.q-card')]
                    .find(c => c.innerText.includes('Результат'));
                const imgs = [...card.querySelectorAll('img')]
                    .map(i => ({src: i.getAttribute('src'),
                                w: Math.round(i.getBoundingClientRect().width)}));
                return {imgs, links: [...card.querySelectorAll('a')]
                    .map(a => a.getAttribute('href')).filter(h => h && h.includes('download'))};
            }""")
            big = next(i for i in gallery["imgs"] if i["w"] > 200)
            check("по умолчанию выбран уточнённый кадр",
                  big["src"].endswith(frames[-1]["file"]), str(big))
            names = {f["file"] for f in frames}
            links = {h.split("?")[0].rsplit("/", 1)[-1] for h in gallery["links"]}
            check("оба файла доступны для скачивания", names <= links, str(links))
            check("оба файла реально отдаются",
                  all(http_ok(f"http://127.0.0.1:{port}{h}") for h in gallery["links"]),
                  str(gallery["links"]))
            snap_frames = api_job(port, job)["frames"]
            check("снимок хранит фактический positive и negative",
                  "theofficialpit" in snap_frames[0]["positive"]
                  and "underage" in snap_frames[0]["negative"],
                  str(snap_frames[0].get("positive"))[:80])
            page.get_by_role("button", name="Промпт этого кадра").first.click()
            page.wait_for_timeout(400)
            card_text = page.locator(".q-card", has_text="Результат").first.inner_text()
            low = card_text.lower()
            check("карточка кадра показывает отправленный positive",
                  "positive (ушёл в comfyui)" in low
                  and "theofficialpit" in low and "negative" in low,
                  card_text[-160:])
            page.screenshot(path=str(shots / "after-result.png"), full_page=True)

            print("\n7) две вкладки не путают задания")
            page_b = ctx.new_page()
            page_b.goto(f"http://127.0.0.1:{port}/")
            wait_text(page_b, "Задание не запущено")
            check("новая вкладка пустая", "Готово ·" not in body(page_b))
            page_b.set_input_files('input[type="file"]', str(photo_b))
            wait_upload(page_b)
            page_b.get_by_role("button", name="Старт").click()
            wait_text(page_b, "Готово · кадров 1", 40000)
            job_b = job_from_url(page_b)
            page_b.close()
            page.reload()
            wait_text(page, "Готово · кадров 2", 20000)
            check("перезагрузка первой вкладки вернула её задание",
                  job_from_url(page) == job and job != job_b,
                  f"{job_from_url(page)} {job} {job_b}")

            print("\n8) потеря связи браузера")
            before = (job_from_url(page), "Готово · кадров 2" in body(page))
            ctx.set_offline(True)
            page.wait_for_timeout(5000)
            lost = "Соединение потеряно" in body(page) or "Connection lost" in body(page)
            ctx.set_offline(False)
            page.wait_for_timeout(1500)
            wait_text(page, "Готово · кадров 2", 15000)
            after = (job_from_url(page), "Готово · кадров 2" in body(page))
            check("плашка потери связи появилась", lost, "нет плашки")
            check("после возврата то же задание и результат", before == after,
                  f"{before} -> {after}")

            print("\n9) отказ WebSocket ComfyUI")
            control(comfy, ws_disabled=True)
            page2 = ctx.new_page()
            page2.goto(f"http://127.0.0.1:{port}/")
            wait_text(page2, "Задание не запущено")
            page2.set_input_files('input[type="file"]', str(photo_a))
            wait_upload(page2)
            page2.get_by_role("button", name="Старт").click()
            wait_text(page2, "Готово · кадров 1", 40000)
            job2 = job_from_url(page2)
            events = api_job(port, job2)["events"]
            stages = [e["stage"] for e in events]
            check("без WebSocket кадр всё равно сделан", "done" in stages, str(stages[-6:]))
            check("шагов в событиях нет", "sampling" not in stages, str(stages[-6:]))
            page2.close()
            control(comfy, ws_disabled=False)

            print("\n10) узкий экран")
            page.set_viewport_size({"width": 390, "height": 844})
            page.wait_for_timeout(600)
            narrow = page.evaluate("""() => {
                const row = [...document.querySelectorAll('.nicegui-row')]
                    .find(r => r.className.includes('items-start'));
                const kids = [...row.children].map(c => {
                    const b = c.getBoundingClientRect();
                    return {x: Math.round(b.x), y: Math.round(b.y), w: Math.round(b.width)};
                });
                return {kids, scroll: document.documentElement.scrollWidth,
                        inner: window.innerWidth};
            }""")
            check("одна колонка без горизонтального скролла",
                  narrow["scroll"] <= narrow["inner"]
                  and len({k["x"] for k in narrow["kids"]}) == 1
                  and narrow["kids"][0]["y"] != narrow["kids"][1]["y"], str(narrow))
            page.screenshot(path=str(shots / "after-mobile.png"), full_page=True)

            print("\n11) ход и таймер рядом с результатом")
            page.set_viewport_size({"width": 1440, "height": 900})
            page2 = ctx.new_page()
            page2.goto(f"http://127.0.0.1:{port}/")
            wait_text(page2, "Задание не запущено")
            control(comfy, delay=4)
            page2.set_input_files('input[type="file"]', str(photo_b))
            wait_upload(page2)
            page2.get_by_role("button", name="Старт").click()
            wait_text(page2, "шаги сэмплера", 20000)
            run_shot = shots / "after-run.png"
            page2.screenshot(path=str(run_shot), full_page=True)
            stage = page2.evaluate("""() => {
                const s = [...document.querySelectorAll('.nicegui-label, div')]
                    .find(e => e.children.length === 0
                        && e.innerText.startsWith('шаги сэмплера'));
                return s ? s.innerText : '';
            }""")
            check("видны шаги сэмплера и вариант", "шаг" in stage and "вариант" in stage,
                  stage)
            wait_text(page2, "Готово · кадров 1", 40000)
            page2.close()
            control(comfy, delay=1)
            print("\n12) истёкшая история задания")
            page3 = ctx.new_page()
            page3.goto(f"http://127.0.0.1:{port}/?job=deadbeef00")
            wait_text(page3, "История задания истекла", 15000)
            check("истёкшее задание объясняет себя",
                  "Задание не запущено" not in body(page3))
            page3.close()

            print("\n13) страница /v2 рядом со старой")
            status, html = fetch(f"http://127.0.0.1:{port}/v2")
            check("/v2 отдаёт 200", status == 200, str(status))
            names = [s["label"] for s in tw.catalog.style_list()]
            check("в HTML есть названия стилей из каталога",
                  bool(names) and all(n in html for n in names),
                  str([n for n in names if n not in html]))
            check("в HTML есть требование 1280",
                  "Нужен экран от 1280 px" in html)
            check("старая страница на / по-прежнему жива", "comic-comfy" in fetch(
                f"http://127.0.0.1:{port}/")[1])

            v2 = ctx.new_page()
            v2.set_viewport_size({"width": 1440, "height": 900})
            v2.goto(f"http://127.0.0.1:{port}/v2")
            v2.wait_for_function(
                "() => !document.body.innerText.includes('проверяю')", timeout=30000)

            print("\n13а) оба токена темы и один акцент")
            dark = v2.evaluate("""() => ({
                dark: document.body.classList.contains('body--dark'),
                app: getComputedStyle(document.querySelector('.cp-app')).backgroundColor,
                ink: getComputedStyle(document.querySelector('.cp-app')).color,
                card: getComputedStyle(document.querySelector('.cp-card')).backgroundColor,
                start: getComputedStyle(document.querySelector('.cp-btn--fill')).backgroundColor,
                mono: getComputedStyle(document.querySelector('.cp-num')).fontVariantNumeric,
                field: getComputedStyle(
                    document.querySelector('.cp-app .q-field__control'), ':before').borderColor,
            })""")
            check("тёмная тема: фон, текст и поверхность из токенов",
                  dark["dark"] is True
                  and dark["app"] == rgb(tw.theme.DARK["--bg"])
                  and dark["ink"] == rgb(tw.theme.DARK["--ink"])
                  and dark["card"] == rgb(tw.theme.DARK["--surface"]), str(dark))
            check("рамка поля ввода из токена --line",
                  dark["field"] == rgb(tw.theme.DARK["--line"]), dark["field"])
            check("кнопка Старт — акцентная заливка",
                  dark["start"] == rgb(tw.theme.DARK["--accent-fill"]), dark["start"])
            check("числа моноширинные и табличные",
                  "tabular-nums" in (dark["mono"] or ""), dark["mono"])
            v2.locator(".cp-icon").click()
            v2.wait_for_function(
                "() => !document.body.classList.contains('body--dark')", timeout=10000)
            # Рамка поля перекрашивается переходом Quasar, поэтому ждём конечное
            # значение, а не «прошло 300 мс».
            v2.wait_for_function(
                "line => getComputedStyle(document.querySelector("
                "'.cp-app .q-field__control'), ':before').borderColor === line",
                arg=rgb(tw.theme.LIGHT["--line"]), timeout=5000)
            light = v2.evaluate("""() => ({
                app: getComputedStyle(document.querySelector('.cp-app')).backgroundColor,
                card: getComputedStyle(document.querySelector('.cp-card')).backgroundColor,
                start: getComputedStyle(document.querySelector('.cp-btn--fill')).backgroundColor,
                field: getComputedStyle(
                    document.querySelector('.cp-app .q-field__control'), ':before').borderColor,
            })""")
            check("светлая тема: те же блоки из токенов",
                  light["app"] == rgb(tw.theme.LIGHT["--bg"])
                  and light["card"] == rgb(tw.theme.LIGHT["--surface"])
                  and light["start"] == rgb(tw.theme.LIGHT["--accent-fill"])
                  and light["field"] == rgb(tw.theme.LIGHT["--line"]), str(light))
            v2.reload()
            v2.wait_for_function(
                "() => document.querySelector('.cp-app') !== null", timeout=20000)
            v2.wait_for_timeout(400)
            check("выбор темы пережил перезагрузку вкладки",
                  v2.evaluate(
                      "() => !document.body.classList.contains('body--dark')"))
            v2.locator(".cp-icon").click()
            v2.wait_for_function(
                "() => document.body.classList.contains('body--dark')", timeout=10000)

            print("\n13б) стиль выбирается списком, вес виден")
            styles = v2.evaluate("""() => {
                const rows = Array.from(document.querySelectorAll('.cp-style'));
                return {
                    count: rows.length,
                    weights: rows.map(r => r.querySelector('.cp-style__weight').innerText),
                    pressed: rows.map(r => r.getAttribute('aria-pressed')),
                    weightColor: getComputedStyle(
                        document.querySelector('.cp-style--on .cp-style__weight')).color,
                    select: document.querySelectorAll('.cp-select, select').length,
                };
            }""")
            check("список, а не выпадающий список",
                  styles["count"] == len(names) and styles["select"] == 0,
                  str(styles))
            check("вес LoRA справа у каждого стиля",
                  styles["weights"] == ["0.6", "0.9", "0.9", "0.9", "0.85"],
                  str(styles["weights"]))
            check("выбран ровно один, и он помечен",
                  styles["pressed"].count("true") == 1
                  and styles["weightColor"] == rgb(tw.theme.DARK["--accent"]),
                  str(styles["pressed"]))
            v2.locator(".cp-style").nth(4).click()
            v2.wait_for_timeout(400)
            chosen = v2.evaluate("""() => {
                const row = document.querySelector('.cp-style--on');
                return {text: row.innerText, pressed: row.getAttribute('aria-pressed')};
            }""")
            check("клик по стилю выделяет его",
                  chosen["pressed"] == "true" and "GTA" in chosen["text"], str(chosen))

            print("\n13в) рельс прокручивается страницей, Старт липкий")
            rail = v2.evaluate("""() => {
                const rail = document.querySelector('.cp-rail');
                const start = document.querySelector('.cp-start');
                const cs = getComputedStyle(rail);
                const ss = getComputedStyle(start);
                return {overflow: cs.overflowY, maxHeight: cs.maxHeight,
                        position: cs.position,
                        inner: document.querySelector('.cp-rail__scroll') !== null,
                        start: ss.position, bottom: ss.bottom,
                        background: ss.backgroundColor,
                        page: document.documentElement.scrollHeight - window.innerHeight,
                        width: Math.round(rail.getBoundingClientRect().width)};
            }""")
            check("у рельса нет собственной прокрутки",
                  rail["overflow"] in ("visible", "clip") and rail["inner"] is False
                  and rail["position"] == "static" and rail["maxHeight"] == "none",
                  str(rail))
            check("Старт липнет к низу окна на непрозрачной поверхности",
                  rail["start"] == "sticky" and rail["bottom"] == "16px"
                  and rail["background"] == rgb(tw.theme.DARK["--surface"]), str(rail))
            check("рельс 400 px до 1600", rail["width"] == 400, str(rail["width"]))
            v2.set_viewport_size({"width": 1600, "height": 900})
            v2.wait_for_timeout(400)
            wide = v2.evaluate("() => Math.round("
                               "document.querySelector('.cp-rail').getBoundingClientRect().width)")
            check("рельс 420 px после 1600", wide == 420, str(wide))
            v2.set_viewport_size({"width": 1440, "height": 900})
            v2.wait_for_timeout(400)
            check("список стилей целиком в рельсе, без обрезки",
                  v2.evaluate("""() => {
                      const rows = document.querySelectorAll('.cp-style');
                      const rail = document.querySelector('.cp-rail');
                      return rows.length === 5
                          && rail.scrollHeight <= rail.clientHeight + 1;
                  }"""))
            v2.evaluate("() => window.scrollTo(0, 400)")
            v2.wait_for_timeout(300)
            box = v2.evaluate("""() => {
                const b = document.querySelector('.cp-start').getBoundingClientRect();
                return {top: Math.round(b.top), bottom: Math.round(b.bottom),
                        inner: window.innerHeight,
                        scroll: Math.round(window.scrollY)};
            }""")
            check("после прокрутки страницы Старт на месте",
                  box["scroll"] > 0 and box["top"] >= 0 and box["bottom"] <= box["inner"],
                  str(box))
            v2.evaluate("() => window.scrollTo(0, 0)")
            v2.wait_for_timeout(200)
            v2.screenshot(path=str(shots / "v2-dark.png"), full_page=True)

            print("\n13г) проверка сервисов не стирает ввод")
            scene = v2.locator(".cp-card .q-field input").first
            scene.fill("тестовая сцена v2")
            v2.get_by_role("button", name="Проверить сервисы").click()
            v2.wait_for_function(
                "() => !document.body.innerText.includes('проверяю')", timeout=30000)
            check("сцена на месте после health-check",
                  scene.input_value() == "тестовая сцена v2", scene.input_value())

            print("\n13д) задание V2: старт, адрес и перезагрузка")
            v2.set_input_files('input[type="file"]', str(photo_a))
            wait_v2_upload(v2)
            caption = v2.locator(".cp-frame").locator("..").inner_text()
            check("размер фото из заголовка", "588×860" in caption or "×" in caption,
                  caption[:80])
            v2.get_by_role("button", name="Старт").click()
            wait_text(v2, "Готово", 60000)
            job_v2 = v2.evaluate("() => location.search")
            check("номер задания в адресе /v2",
                  job_v2.startswith("?job=") and len(job_v2) == 15, job_v2)
            check("очередь снова свободна", "очередь: свободна" in body(v2))
            v2.reload()
            v2.wait_for_function("() => document.querySelector('.cp-app') !== null",
                                 timeout=20000)
            wait_text(v2, "Готово", 20000)
            check("перезагрузка вкладки вернула то же задание",
                  v2.evaluate("() => location.search") == job_v2
                  and "Задание не запущено" not in body(v2))
            v2.screenshot(path=str(shots / "v2-done.png"), full_page=True)

            print("\n13ж) фазы задания и шаг текущего прохода")
            v2.set_input_files('input[type="file"]', str(photo_a))
            wait_v2_upload(v2)
            control(comfy, delay=3)
            v2.get_by_role("button", name="Старт").click()
            v2.wait_for_function("() => document.body.innerText.includes('Шаг')",
                                 timeout=40000)
            phases = v2.evaluate("""() => {
                const rows = [...document.querySelectorAll('.cp-phase')];
                return {
                    names: rows.map(r => r.innerText.trim()),
                    done: rows.filter(r => r.classList.contains('cp-phase--done')).length,
                    on: rows.filter(r => r.classList.contains('cp-phase--on')).length,
                    step: document.querySelector('.cp-step').innerText,
                    dots: rows.map(r => getComputedStyle(r.querySelector('.cp-dot'))
                        .backgroundColor),
                    bar: (() => { const b = document.querySelector('.cp-step__bar');
                        return b ? Math.round(b.getBoundingClientRect().width) : 0; })(),
                };
            }""")
            check("четыре фазы в порядке спецификации",
                  phases["names"] == ["Описание", "Промпт", "Генерация", "Кадр"],
                  str(phases["names"]))
            check("шаг сэмплера с номером прохода",
                  phases["step"].startswith("Шаг ")
                  and " · проход 1/2" in phases["step"], phases["step"])
            check("пройденные фазы отмечены, текущая одна",
                  phases["done"] >= 2 and phases["on"] == 1, str(phases))
            check("цвет состояния несут точки фаз",
                  rgb(tw.theme.DARK["--ok"]) in phases["dots"]
                  and rgb(tw.theme.DARK["--accent-fill"]) in phases["dots"],
                  str(phases["dots"]))
            check("полоска шага видна", phases["bar"] > 0, str(phases["bar"]))
            v2.screenshot(path=str(shots / "v2-running.png"), full_page=True)
            wait_text(v2, "Готово", 60000)
            control(comfy, delay=1)
            v2.wait_for_timeout(400)
            check("готовое задание закрывает все четыре фазы",
                  v2.evaluate("() => document.querySelectorAll('.cp-phase--done').length") == 4)

            print("\n13з) вкладки переключают панели и переживают перезагрузку")
            v2.locator(".cp-tab").nth(2).click()
            v2.wait_for_timeout(400)
            panels = v2.evaluate("""() => [...document.querySelectorAll('.cp-panel')]
                .filter(p => getComputedStyle(p).display !== 'none').map(p => p.innerText)""")
            check("вкладка «Промпт» показывает ровно одну панель",
                  len(panels) == 1, str(len(panels)))
            frames = api_job(port, job_from_url(v2))["frames"]
            check("промпт кадра — фактический, из паспорта",
                  bool(frames) and frames[0]["positive"] in panels[0]
                  and frames[0]["negative"] in panels[0], panels[0][:80])
            check("выбрана ровно одна вкладка",
                  v2.evaluate("() => document.querySelectorAll('.cp-tab--on').length") == 1)
            v2.screenshot(path=str(shots / "v2-prompt.png"), full_page=True)
            v2.reload()
            v2.wait_for_function("() => document.querySelector('.cp-app') !== null",
                                 timeout=20000)
            wait_text(v2, "Готово", 20000)
            v2.wait_for_timeout(600)
            check("перезагрузка вернула вкладку «Промпт»",
                  v2.evaluate("""() => [...document.querySelectorAll('.cp-tab')]
                      .find(t => t.classList.contains('cp-tab--on'))
                      .innerText.trim()""") == "Промпт")

            print("\n13и) полоса кадров: выбор по имени файла, без пересборки")
            v2.locator(".cp-tab").nth(0).click()
            v2.wait_for_timeout(300)
            v2.set_input_files('input[type="file"]', str(photo_a))
            wait_v2_upload(v2)
            fill_field(v2, "Вариантов", 2)
            control(comfy, delay=3)
            previous_job = job_from_url(v2)
            v2.get_by_role("button", name="Старт").click()
            v2.wait_for_function("old => location.search !== old", arg=previous_job,
                                 timeout=20000)
            # Новое задание начинается с пустой полосы: иначе «первой» окажется
            # миниатюра прошлого задания, и метка уедет вместе с ней.
            v2.wait_for_function(
                "() => document.querySelectorAll('.cp-thumb').length === 0",
                timeout=20000)
            v2.wait_for_function(
                "() => document.querySelectorAll('.cp-thumb').length >= 1", timeout=60000)
            v2.evaluate("() => document.querySelector('.cp-thumb')"
                        ".setAttribute('data-probe', 'kept')")
            v2.locator(".cp-thumb").first.click()
            v2.wait_for_timeout(300)
            v2.wait_for_function(
                "() => document.querySelectorAll('.cp-thumb').length >= 2", timeout=60000)
            kept = v2.evaluate("""() => ({
                probe: document.querySelectorAll('.cp-thumb')[0].getAttribute('data-probe'),
                on: [...document.querySelectorAll('.cp-thumb')]
                    .findIndex(t => t.classList.contains('cp-thumb--on')),
                count: document.querySelectorAll('.cp-thumb').length,
            })""")
            check("новая миниатюра не пересобрала полосу",
                  kept["probe"] == "kept" and kept["count"] == 2, str(kept))
            check("выбор человека не сбит новым кадром", kept["on"] == 0, str(kept))
            wait_text(v2, "Готово", 60000)
            control(comfy, delay=1)
            v2.wait_for_timeout(400)
            frames = api_job(port, job_from_url(v2))["frames"]
            check("в задании два кадра", len(frames) == 2, str(len(frames)))
            big = v2.evaluate(
                "() => document.querySelector('.cp-frame__img--on')"
                "?.getAttribute('src') || ''")
            check("большой кадр — выбранный", big.endswith(frames[0]["file"]), big)
            v2.locator(".cp-thumb").nth(1).click()
            v2.wait_for_timeout(400)
            big = v2.evaluate(
                "() => document.querySelector('.cp-frame__img--on')"
                "?.getAttribute('src') || ''")
            check("клик по миниатюре меняет кадр", big.endswith(frames[1]["file"]), big)
            v2.screenshot(path=str(shots / "v2-strip.png"), full_page=True)
            v2.reload()
            v2.wait_for_function("() => document.querySelector('.cp-app') !== null",
                                 timeout=20000)
            wait_text(v2, "Готово", 20000)
            v2.wait_for_timeout(800)
            chosen = v2.evaluate("""() => ({
                on: [...document.querySelectorAll('.cp-thumb')]
                    .findIndex(t => t.classList.contains('cp-thumb--on')),
                big: document.querySelector('.cp-frame__img--on')
                    ?.getAttribute('src') || '',
            })""")
            check("выбранный кадр пережил перезагрузку",
                  chosen["on"] == 1 and chosen["big"].endswith(frames[1]["file"]),
                  str(chosen))

            print("\n13и2) история читает паспорт кадра")
            v2.locator(".cp-tab").nth(3).click()
            v2.wait_for_timeout(900)
            rows = v2.evaluate("""() => {
                const table = document.querySelector('.cp-history .q-table');
                if (!table) return [];
                return [...table.querySelectorAll('tbody tr')]
                    .map(r => r.innerText.replace(/\\s+/g, ' ').trim());
            }""")
            check("в истории строки паспорта, свежий кадр сверху",
                  bool(rows) and frames[-1]["file"] in rows[0], str(rows[:2]))
            check("история не из памяти страницы: паспорт копится по фото",
                  len(rows) >= 2, str(len(rows)))
            v2.screenshot(path=str(shots / "v2-history.png"), full_page=True)
            v2.locator(".cp-tab").nth(0).click()
            v2.wait_for_timeout(300)

            print("\n13к) ошибка в одном месте, журнал в диалоге")
            v2.set_input_files('input[type="file"]', str(photo_b))
            wait_v2_upload(v2)
            control(comfy, prompt_fail=True)
            previous_job = job_from_url(v2)
            v2.get_by_role("button", name="Старт").click()
            v2.wait_for_function("old => location.search !== old", arg=previous_job,
                                 timeout=20000)
            v2.wait_for_function(
                "() => { const e = document.querySelector('.cp-error');"
                " return e && getComputedStyle(e).display !== 'none'; }", timeout=60000)
            control(comfy, prompt_fail=False)
            v2.wait_for_timeout(300)
            err = v2.evaluate("""() => ({
                zones: document.querySelectorAll('.cp-error').length,
                title: document.querySelector('.cp-error__head').innerText,
                text: document.querySelector('.cp-error__text').innerText,
                wrap: getComputedStyle(document.querySelector('.cp-error__text')).whiteSpace,
            })""")
            job_error = (api_job(port, job_from_url(v2)).get("error") or "").strip()
            check("зона ошибки одна на странице", err["zones"] == 1, str(err["zones"]))
            check("ошибка видна целиком, без обрезки",
                  bool(job_error) and err["text"].strip() == job_error
                  and err["wrap"] == "pre-wrap", str(err))
            check("в заголовке — фаза, где упало", "Генерация" in err["title"], err["title"])
            v2.screenshot(path=str(shots / "v2-error.png"), full_page=True)
            v2.get_by_role("button", name="Журнал").click()
            v2.wait_for_timeout(600)
            journal = v2.evaluate("""() => {
                const log = document.querySelector('.cp-log');
                return {open: !!log && log.getBoundingClientRect().width > 0,
                        text: log ? log.innerText : ''};
            }""")
            check("журнал задания открылся в диалоге",
                  journal["open"] and "промпт" in journal["text"].lower(),
                  journal["text"][:80])
            v2.screenshot(path=str(shots / "v2-journal.png"))
            v2.keyboard.press("Escape")
            v2.wait_for_timeout(400)

            print("\n13л) узкий экран: требование 1280 вместо раскладки")
            # Плашки `ui.notify` висят несколько секунд и попадают в текст страницы.
            v2.wait_for_function(
                "() => document.querySelectorAll('.q-notification').length === 0",
                timeout=20000)
            v2.set_viewport_size({"width": 1279, "height": 800})
            v2.wait_for_timeout(500)
            narrow = v2.evaluate("""() => ({
                app: getComputedStyle(document.querySelector('.cp-app')).display,
                note: getComputedStyle(document.querySelector('.cp-narrow')).display,
                text: document.body.innerText.trim(),
                scroll: document.documentElement.scrollWidth,
                inner: window.innerWidth,
            })""")
            check("на 1279 видно только требование",
                  narrow["app"] == "none" and narrow["note"] == "flex"
                  and narrow["text"] == "Нужен экран от 1280 px"
                  and narrow["scroll"] <= narrow["inner"], str(narrow))
            v2.set_viewport_size({"width": 1280, "height": 800})
            v2.wait_for_timeout(500)
            check("на 1280 раскладка возвращается",
                  v2.evaluate("() => getComputedStyle("
                              "document.querySelector('.cp-app')).display") != "none")
            v2.screenshot(path=str(shots / "v2-narrow.png"))
            v2.close()
            browser.close()

        print(f"\nскриншоты: {shots}")
    finally:
        if app:
            app.terminate()
            try:
                app.wait(timeout=5)
            except Exception:
                app.kill()
        lm.stop()
        comfy.stop()
        shutil.rmtree(TMP, ignore_errors=True)

    print(f"\nитого: ok {len(passed)}, провалов {len(failed)}")
    if failed:
        print("провалено: " + ", ".join(failed))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
