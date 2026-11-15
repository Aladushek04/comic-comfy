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
for path in (str(ROOT / "tools"), str(ROOT)):
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
