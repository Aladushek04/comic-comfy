"""Проверки веб-слоя: задания, очередь, события, привязки полей.

Ни моделей, ни GPU, ни ComfyUI: LM Studio и ComfyUI подменяются локальными
HTTP-заглушками на случайных портах. Проверяется то, что реально уходит в
/prompt, что попадает в run.json и как ведёт себя задание при сбоях.

    python tools/test_web_app.py
"""
import contextlib
import io
import base64
import hashlib
import json
import os
import re
import shutil
import struct
import sys
import tempfile
import threading
import time
import zlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
# Корень репозитория добавит settings при импорте ниже: это единственный такой insert.

import comfy_launch as cl
import jobs
import photo_prompt as pp
import build_workflows as bw
import settings as st
from PIL import Image
from webapp import catalog, labels, push, theme
from webapp import ui as web_ui
from webapp.collect import collect_form
from webapp.service import JobService, PNG_SIGNATURE, UploadError
from webapp.state import fmt_setting, missing_job_text, pass_steps


# Теггер в проверках не нужен: его логика проверяется в test_photo_prompt.py,
# а 1.7 ГБ моделей и лишние секунды на кадр здесь ни к чему.
os.environ.setdefault("COMIC_TAGGER", "off")

TMP = Path(tempfile.mkdtemp(prefix="comic_web_test_"))
passed, failed = [], []

st.utf8_streams()      # кириллица и в консоли, и в пайпе (сюда же приходит test_web_ui)


def check(name, cond, detail=""):
    (passed if cond else failed).append(name)
    print(("  ok   " if cond else "  FAIL ") + name + (f" — {detail}" if detail and not cond else ""))


def make(path, color=(40, 60, 80), size=(240, 180)):
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", size, color).save(path)


# --------------------------------------------------------------- заглушки
class _Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def _json(self, obj, code=200):
        body = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _body(self):
        n = int(self.headers.get("Content-Length") or 0)
        return json.loads(self.rfile.read(n) or b"{}")


class Stub:
    def __init__(self, handler):
        self.state = type("S", (), {})()
        self.state.requests = []
        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        self.httpd.state = self.state
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()

    @property
    def url(self):
        return "http://127.0.0.1:%d" % self.httpd.server_address[1]

    def stop(self):
        self.httpd.shutdown()
        self.httpd.server_close()


class LMHandler(_Handler):
    def do_GET(self):
        if self.path.startswith("/api/v1/models"):
            st = self.server.state
            self._json({"models": [
                {"key": "fake-vlm@q4", "capabilities": {"vision": True},
                 "loaded_instances": [{"instance_id": "inst-fake"}]},
            ]})
        else:
            self._json({"error": "no"}, 404)

    def do_POST(self):
        st = self.server.state
        st.requests.append(self.path)
        body = self._body()
        if self.path == "/v1/chat/completions":
            st.chat_call += 1
            text = json.dumps(body.get("messages", []), ensure_ascii=False)
            if "Rewrite this photo description" in text:
                st.scene_calls += 1
                if st.scene_fail:
                    self._json({"choices": [{"message": {"content": "не могу"}}]})
                    return
                asked = text.split("into: ")[-1].split(".")[0] if "into: " in text else st.scene
                self._json({"choices": [{"message": {"content": json.dumps({
                    "subject": "adult woman, linen dress, " + asked,
                    "negative_extra": "indoor room", "notes": "сцена"})}}]})
                return
            if "Compare this image" in text:
                st.recheck_calls += 1
                self._json({"choices": [{"message": {"content": json.dumps(
                    {"missing": st.missing})}}]})
                return
            self._json({"choices": [{"message": {"content": json.dumps({
                "subject": "adult woman, long hair, black dress, standing",
                "negative_extra": "missing glasses", "notes": "тест"})}}]})
            return
        if self.path == "/api/v1/models/unload":
            st.unloaded += 1
            self._json({})
            return
        self._json({"error": "no"}, 404)


class ComfyHandler(_Handler):
    WS_GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"
    CONTROL_KEYS = ("ws_disabled", "delay", "make_files", "empty_outputs", "prompt_fail")

    def _ws_frame(self, obj):
        data = json.dumps(obj, ensure_ascii=False).encode()
        header = bytearray([0x81])
        if len(data) < 126:
            header.append(len(data))
        elif len(data) < 65536:
            header += bytes([126]) + struct.pack(">H", len(data))
        else:
            header += bytes([127]) + struct.pack(">Q", len(data))
        self.wfile.write(bytes(header) + data)
        self.wfile.flush()

    def _ws(self):
        """Мини-WebSocket: один кадр-привет и сценарий шагов, потом закрытие."""
        key = self.headers.get("Sec-WebSocket-Key") or ""
        accept = base64.b64encode(
            hashlib.sha1((key + self.WS_GUID).encode()).digest()).decode()
        self.send_response(101, "Switching Protocols")
        self.send_header("Upgrade", "websocket")
        self.send_header("Connection", "Upgrade")
        self.send_header("Sec-WebSocket-Accept", accept)
        self.end_headers()
        st = self.server.state
        pid = st.pending[-1] if st.pending else "p1"
        for msg in st.ws_script(pid):
            try:
                self._ws_frame(msg)
            except Exception:
                return
            time.sleep(0.02)
        try:
            self.wfile.write(b"\x88\x02\x03\xe8")      # close, normal
            self.wfile.flush()
        except Exception:
            pass

    def do_GET(self):
        st = self.server.state
        st.gets.append(self.path)
        if self.path.startswith("/ws"):
            if st.ws_disabled:
                self._json({"error": "no ws"}, 404)
                return
            self._ws()
            return
        if self.path.startswith("/system_stats"):
            self._json({})
            return
        if self.path.startswith("/object_info"):
            self._json(st.object_info)
            return
        if self.path.startswith("/history/"):
            pid = self.path.rsplit("/", 1)[-1]
            st.polls[pid] = st.polls.get(pid, 0) + 1
            if st.polls[pid] < st.delay:
                self._json({})
                return
            if st.history_error:
                self._json({pid: {"status": {"status_str": "error", "messages": [
                    ["execution_error", {"node_type": "KSampler",
                                        "exception_message": "CUDA out of memory"}]]},
                    "outputs": {}}})
                return
            if st.empty_outputs:
                self._json({pid: {"status": {"status_str": "success"}, "outputs": {}}})
                return
            name = st.frames.get(pid) or f"frame_{pid}.png"
            if st.make_files:
                path = pp.OUTPUT_DIR / name
                if not path.exists():
                    path.parent.mkdir(parents=True, exist_ok=True)
                    Image.new("RGB", (64, 48), (0, 180, 0)).save(path)
            self._json({pid: {"status": {"status_str": "success"}, "outputs": {
                "19": {"images": [{"filename": name}]}}}})
            return
        self._json({}, 404)

    def do_POST(self):
        st = self.server.state
        st.posts.append(self.path)
        if self.path == "/_control":
            body = self._body()
            for key in self.CONTROL_KEYS:
                if key in body:
                    setattr(st, key, body[key])
            self._json({k: getattr(st, k) for k in self.CONTROL_KEYS})
            return
        if self.path == "/prompt":
            payload = self._body()
            st.prompts.append(payload)
            if st.prompt_fail:
                self._json({"error": "invalid prompt"}, 400)
                return
            pid = f"p{len(st.prompts)}"
            st.pending.append(pid)
            self._json({"prompt_id": pid})
            return
        if self.path == "/free":
            self._json({})
            return
        self._json({}, 404)


class OpenAILMHandler(_Handler):
    """Сервис описаний без LM Studio: только OpenAI-совместимый /v1/models."""

    def do_GET(self):
        if self.path.startswith("/v1/models"):
            self._json({"object": "list", "data": [{"id": "huihui@q4"},
                                                   {"id": "text-only@q4"}]})
        else:
            self._json({"error": "not found"}, 404)


def lm_stub():
    stub = Stub(LMHandler)
    s = stub.state
    s.chat_call = s.scene_calls = s.recheck_calls = s.unloaded = 0
    s.scene = "на пляже"
    s.scene_fail = False
    s.missing = "silver bracelet"
    return stub


def comfy_stub():
    stub = Stub(ComfyHandler)
    s = stub.state
    s.gets, s.posts, s.prompts, s.pending = [], [], [], []
    s.polls, s.frames = {}, {}
    s.delay = 1
    s.make_files = True
    s.prompt_fail = s.history_error = s.empty_outputs = False
    s.ws_disabled = False

    def ws_script(pid):
        return [
            {"type": "execution_start", "data": {"prompt_id": pid}},
            {"type": "execution_cached", "data": {"prompt_id": pid, "nodes": ["5"]}},
            {"type": "progress", "data": {"prompt_id": pid, "node": "13",
                                          "value": 6, "max": 20}},
            {"type": "progress_state", "data": {"prompt_id": pid, "nodes": {
                "13": {"state": "running", "value": 12, "max": 20}}}},
            {"type": "executing", "data": {"prompt_id": pid, "node": None}},
        ]
    s.ws_script = ws_script
    s.object_info = {
        "CheckpointLoaderSimple": {"input": {"required": {
            "ckpt_name": [["WAI-Nsfw-Illustrious-17.safetensors", "other.safetensors"], {}]}}},
        "LoraLoaderModelOnly": {"input": {"required": {
            "lora_name": [["thepit2_1768210.safetensors", "other_lora.safetensors"], {}],
            "strength_model": ["FLOAT", {}]}}},
        "KSampler": {"input": {"required": {
            "sampler_name": [["euler_ancestral", "dpmpp_2m"], {}],
            "scheduler": [["normal", "karras"], {}]}}},
    }
    return stub


class FastClock:
    """Реальное время, но опрос истории без трёхсекундных пауз."""

    def time(self):
        return time.time()

    def sleep(self, seconds):
        time.sleep(0.02)


@contextlib.contextmanager
def stand(lm=None, comfy=None, root=None):
    """Подменяет пути, адреса и часы; после выхода всё возвращает."""
    saved = (pp.ROOT, pp.INPUT_DIR, pp.OUTPUT_DIR, pp.LM, pp.COMFY, pp.time,
             os.environ.get("COMFY_URL"))
    base = Path(root or (TMP / ("root_" + str(time.time_ns()))))
    pp.ROOT = base
    pp.INPUT_DIR = base / "input"
    pp.OUTPUT_DIR = base / "output" / "comic"
    pp.LM = lm.url if lm else "http://127.0.0.1:1"
    pp.COMFY = comfy.url if comfy else "http://127.0.0.1:1"
    os.environ["COMFY_URL"] = pp.COMFY
    pp.time = FastClock()
    pp.OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    try:
        yield base
    finally:
        (pp.ROOT, pp.INPUT_DIR, pp.OUTPUT_DIR, pp.LM, pp.COMFY, pp.time,
         saved_env) = saved
        if saved_env is None:
            os.environ.pop("COMFY_URL", None)
        else:
            os.environ["COMFY_URL"] = saved_env


def new_frame(comfy, pid, name=None):
    name = name or f"frame_{pid}.png"
    comfy.state.frames[pid] = name
    make(pp.OUTPUT_DIR / name, (10, 200, 10), (64, 48))
    return name


def book_for(image):
    path = pp.ROOT / "out" / pp.image_tag(str(image)) / "run.json"
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else None


# --------------------------------------------------------------- проверки
def test_catalog_fields():
    print("\n1) форма знает поля из meta, а не из своего кода")
    styles = catalog.style_list()
    names = [s["workflow"] for s in styles]
    check("в списке только фото-сборки",
          names == ["07_photo_bimbo", "07_photo_comic", "07_photo_jp",
                    "07_photo_thepit", "08_photo_gta"], str(names))
    thepit = next(s for s in styles if s["workflow"] == "07_photo_thepit")
    meta = thepit["meta"]
    check("есть русская подпись из сборки", "ThePit" in meta.get("label", ""), meta.get("label"))
    check("поля привязаны к узлам",
          meta["fields"]["denoise"]["node"] == "13"
          and meta["fields"]["denoise"]["input"] == "denoise", str(meta["fields"]["denoise"]))
    check("у поля есть тип и класс узла",
          meta["fields"]["checkpoint"]["class_type"] == "CheckpointLoaderSimple"
          and meta["fields"]["checkpoint"]["type"] == "COMBO",
          str(meta["fields"]["checkpoint"]))
    check("слоты LoRA подписаны ролями",
          {s["role"] for s in meta["loras"]} == {"style", "hands", "body", "character"},
          str([s["role"] for s in meta["loras"]]))
    check("у сборок нет проблем с привязками", all(not s["problems"] for s in styles),
          str([s["problems"] for s in styles]))
    summary = catalog.graph_summary("07_photo_thepit")
    check("сводка графа: сэмплер, шаги, denoise",
          summary["sampler"] == "euler_ancestral" and summary["steps"] == 30
          and summary["denoise"] == 0.75, str(summary))
    check("сводка графа: чекпойнт и ControlNet",
          summary["checkpoint"].endswith(".safetensors") and summary["cn_strength"] == 0.6,
          str(summary))
    check("граф называет оба сэмплера", meta.get("samplers") == ["13", "17"],
          str(meta.get("samplers")))

    info = comfy_stub()
    try:
        check("варианты чекпойнта приходят из /object_info",
              "other.safetensors" in catalog.combo_options(
                  info.state.object_info, "CheckpointLoaderSimple", "ckpt_name"))
        ok, why = catalog.check_value(
            info.state.object_info,
            {"type": "COMBO", "class_type": "CheckpointLoaderSimple",
             "input": "ckpt_name"}, "нет-такой.safetensors")
        check("значение вне списка моделей отвергается", ok is False and "нет-такой" in why, why)
    finally:
        info.stop()

    print("   битая привязка")
    wf = TMP / "wf_broken"
    wf.mkdir(exist_ok=True)
    data = json.loads((pp.WF_DIR / "07_photo_thepit.api.json").read_text(encoding="utf-8"))
    data["meta"]["fields"]["denoise"]["node"] = "999"
    (wf / "07_photo_thepit.api.json").write_text(json.dumps(data, ensure_ascii=False),
                                                 encoding="utf-8")
    saved = pp.WF_DIR
    pp.WF_DIR = wf
    try:
        broken = next(s for s in catalog.style_list()
                      if s["workflow"] == "07_photo_thepit")
        check("пропавший узел назван диагностикой",
              any("узла 999" in p for p in broken["problems"]), str(broken["problems"]))
        lm, comfy = lm_stub(), comfy_stub()
        try:
            with stand(lm, comfy):
                img = TMP / "broken.png"
                make(img)
                result = jobs.run_job(jobs.JobRequest(
                    image=str(img), workflow="07_photo_thepit",
                    subject="adult woman", generate=True))
            check("битая привязка не отправляет граф",
                  result.code == 1 and comfy.state.prompts == [], str(comfy.state.prompts))
            check("ошибка объясняет причину", "узла 999" in result.error, result.error)
        finally:
            lm.stop()
            comfy.stop()
    finally:
        pp.WF_DIR = saved


def test_full_cycle_and_cache():
    print("\n2) полный цикл через заглушки, затем повтор из кэша")
    lm, comfy = lm_stub(), comfy_stub()
    try:
        with stand(lm, comfy) as root:
            img = TMP / "cycle.png"
            make(img, (120, 30, 30))
            new_frame(comfy, "p1")
            result = jobs.run_job(jobs.JobRequest(
                image=str(img), workflow="07_photo_thepit", variants=1, seed=42))
            check("задание успешно", result.ok and result.code == 0, result.error)
            check("описание пришло от VLM", lm.state.chat_call == 1, str(lm.state.chat_call))
            check("в /prompt ушёл граф с фото",
                  comfy.state.prompts and comfy.state.prompts[0]["prompt"]["7"]["inputs"]["image"]
                  .endswith(".png"), str(comfy.state.prompts[:1])[:200])
            texts = {n["inputs"].get("text") for n in comfy.state.prompts[0]["prompt"].values()
                     if n["class_type"] == "CLIPTextEncode"}
            check("positive собран из шаблона и описания",
                  any("theofficialpit" in t and "black dress" in t for t in texts),
                  str([t[:60] for t in texts]))
            check("negative содержит негатив LLM и рамку",
                  any("missing glasses" in t and "underage" in t for t in texts),
                  str([t[:60] for t in texts]))
            book = book_for(img)
            check("run.json записан", bool(book) and len(book["runs"]) == 1, str(book)[:120])
            check("в паспорте seed и файл",
                  book["runs"][0]["seed"] == 42 and book["runs"][0]["workflow"] == "07_photo_thepit",
                  str(book["runs"][0]))
            check("кэш описания создан", pp.read_cache(str(img)) is not None)
            cache_text = pp.cache_path(str(img)).read_text(encoding="utf-8")

            before = lm.state.chat_call
            new_frame(comfy, "p2")
            result2 = jobs.run_job(jobs.JobRequest(
                image=str(img), workflow="07_photo_thepit", variants=1, seed=43))
            check("повтор успешен", result2.ok, result2.error)
            check("повтор не зовёт LM Studio", lm.state.chat_call == before,
                  f"{before} -> {lm.state.chat_call}")
            check("кэш не переписан", pp.cache_path(str(img)).read_text(encoding="utf-8")
                  == cache_text)
            check("в паспорте два кадра", len(book_for(img)["runs"]) == 2,
                  str(len(book_for(img)["runs"])))
    finally:
        lm.stop()
        comfy.stop()


def test_manual_without_lm():
    print("\n3) ручной режим не обращается к LM Studio")
    comfy = comfy_stub()
    try:
        with stand(lm=None, comfy=comfy):
            img = TMP / "manual.png"
            make(img, (30, 120, 30))
            result = jobs.run_job(jobs.JobRequest(
                image=str(img), workflow="07_photo_thepit",
                subject="adult woman, red dress, beach", seed=7))
            check("ручной прогон успешен", result.ok, result.error)
            check("кэш автоописания не создан", pp.read_cache(str(img)) is None)
            texts = [n["inputs"].get("text") for n in comfy.state.prompts[0]["prompt"].values()
                     if n["class_type"] == "CLIPTextEncode"]
            check("описание попало в positive",
                  any("red dress" in t for t in texts), str(texts)[:200])
            check("шаблон стиля сохранён",
                  any("theofficialpit" in t for t in texts), str(texts)[:200])

            only = jobs.run_job(jobs.JobRequest(
                image=str(img), workflow="07_photo_thepit",
                subject="adult woman, green dress", generate=False))
            check("режим «только описание» не генерирует",
                  only.ok and only.stage == "described" and len(comfy.state.prompts) == 1,
                  f"{only.stage} {len(comfy.state.prompts)}")
    finally:
        comfy.stop()


def test_scene_cache():
    print("\n4) кириллическая сцена: свой кэш, сбой не закрепляется")
    lm, comfy = lm_stub(), comfy_stub()
    try:
        with stand(lm, comfy):
            img = TMP / "scene.png"
            make(img, (60, 60, 160))
            result = jobs.run_job(jobs.JobRequest(
                image=str(img), workflow="07_photo_thepit", scene="на пляже", seed=1))
            check("сцена применена", result.ok, result.error)
            scene_cache = pp.read_cache(str(img), "на пляже")
            check("кэш сцены записан с меткой",
                  bool(scene_cache) and scene_cache.get("scene_applied") is True, str(scene_cache))
            check("текст сцены в positive",
                  "на пляже" in (result.positive or ""), result.positive[:120])

            # отдельное фото: сначала базовое описание, потом сцена
            img_b = TMP / "scene_base.png"
            make(img_b, (40, 100, 140))
            base_run = jobs.run_job(jobs.JobRequest(
                image=str(img_b), workflow="07_photo_thepit", seed=10))
            base_text = pp.cache_path(str(img_b)).read_text(encoding="utf-8")
            scene_run = jobs.run_job(jobs.JobRequest(
                image=str(img_b), workflow="07_photo_thepit", scene="в горах", seed=11))
            check("сцена не затёрла базовый кэш",
                  base_run.ok and scene_run.ok
                  and pp.cache_path(str(img_b)).read_text(encoding="utf-8") == base_text,
                  "базовый кэш изменился")
            check("сценический кэш отдельный",
                  pp.read_cache(str(img_b), "в горах") is not None
                  and "в горах" in pp.read_cache(str(img_b), "в горах")["subject"],
                  str(pp.read_cache(str(img_b), "в горах"))[:100])

            calls = lm.state.chat_call
            again = jobs.run_job(jobs.JobRequest(
                image=str(img), workflow="07_photo_thepit", scene="на пляже", seed=2))
            check("повтор берёт сцену из кэша без модели",
                  again.ok and lm.state.chat_call == calls,
                  f"{calls} -> {lm.state.chat_call}")

            other = jobs.run_job(jobs.JobRequest(
                image=str(img), workflow="07_photo_thepit", scene="в горах", seed=3))
            check("другая сцена не берёт чужой кэш",
                  other.ok and lm.state.chat_call > calls, str(lm.state.chat_call))

            lm.state.scene_fail = True
            img2 = TMP / "scene_fail.png"
            make(img2, (160, 60, 60))
            failed_run = jobs.run_job(jobs.JobRequest(
                image=str(img2), workflow="07_photo_thepit", scene="в горах", seed=4))
            check("сбой сцены — провал", failed_run.code == 1, failed_run.error)
            check("кэш под сценой не создан", pp.read_cache(str(img2), "в горах") is None)
            check("базовое описание сохранено", pp.read_cache(str(img2)) is not None)
    finally:
        lm.stop()
        comfy.stop()


def test_settings_in_passport():
    print("\n5) ControlNet 0 и 0.25 различимы, вес LoRA сохраняется на recheck")
    lm, comfy = lm_stub(), comfy_stub()
    try:
        with stand(lm, comfy):
            img = TMP / "cn.png"
            make(img, (90, 90, 10))
            for cn in (0.0, 0.25):
                new_frame(comfy, f"p{len(comfy.state.prompts) + 1}")
                jobs.run_job(jobs.JobRequest(
                    image=str(img), workflow="07_photo_thepit", seed=99,
                    cn_strength=cn, style_weight=0.8, subject="adult woman, test"))
            book = book_for(img)
            check("две записи", len(book["runs"]) == 2, str(len(book["runs"])))
            check("сила ControlNet различима",
                  [r["cn_strength"] for r in book["runs"]] == [0.0, 0.25],
                  str([r.get("cn_strength") for r in book["runs"]]))
            check("вес стиля записан",
                  all(r["style_weight"] == 0.8 for r in book["runs"]),
                  str([r.get("style_weight") for r in book["runs"]]))
            check("без переопределений в паспорте null, а не пустой словарь",
                  all(r.get("overrides") is None for r in book["runs"]),
                  str([r.get("overrides") for r in book["runs"]]))
            strengths = [n["inputs"]["strength"] for n in comfy.state.prompts[-1]["prompt"].values()
                         if n["class_type"] == "ControlNetApplyAdvanced"]
            check("в граф ушла сила 0.25", strengths == [0.25], str(strengths))

            # recheck: второй проход должен сохранить тот же вес LoRA
            before = len(comfy.state.prompts)
            new_frame(comfy, f"p{before + 1}")
            new_frame(comfy, f"p{before + 2}")
            run = jobs.run_job(jobs.JobRequest(
                image=str(img), workflow="07_photo_thepit", seed=5, recheck=True,
                style_weight=0.65, subject="adult woman, plain test"))
            check("recheck сделал повторный проход",
                  run.ok and len(comfy.state.prompts) == before + 2,
                  f"{before} -> {len(comfy.state.prompts)}")
            check("recheck зовёт VLM для проверки", lm.state.recheck_calls == 1,
                  str(lm.state.recheck_calls))
            tail = book_for(img)["runs"][-1]
            check("в записи повторного прохода вес LoRA",
                  tail["style_weight"] == 0.65, str(tail))
            check("в графе повторного прохода вес LoRA",
                  [n["inputs"]["strength_model"] for n in comfy.state.prompts[-1]["prompt"].values()
                   if n["class_type"] == "LoraLoaderModelOnly"
                   and n["inputs"]["lora_name"] == "thepit2_1768210.safetensors"] == [0.65],
                  "вес не дошёл до графа")

            before = len(comfy.state.prompts)
            new_frame(comfy, f"p{before + 1}")
            new_frame(comfy, f"p{before + 2}")
            jobs.run_job(jobs.JobRequest(
                image=str(img), workflow="07_photo_thepit", recheck=True,
                subject="adult woman, another test"))
            pair = book_for(img)["runs"][-2:]
            check("повторный проход без заданного seed берёт seed первого кадра",
                  pair[0]["seed"] == pair[1]["seed"], str([r["seed"] for r in pair]))
    finally:
        lm.stop()
        comfy.stop()


def test_passport_sampler_settings():
    print("\n5a) паспорт кадра помнит шаги, cfg и сэмплер — и в снимке задания")
    lm, comfy = lm_stub(), comfy_stub()
    try:
        with stand(lm, comfy) as root:
            img = TMP / "sampler_pass.png"
            make(img, (30, 60, 90))
            new_frame(comfy, "p1")
            svc = JobService(jobs_root=root / "out" / "jobs")
            job = svc.submit(jobs.JobRequest(
                image="", workflow="07_photo_thepit", subject="adult woman, sampler",
                seed=3), source_path=str(img))
            snap = svc.wait(job.id, timeout=60)
            run = book_for(img)["runs"][0]
            check("steps в паспорте веб-пути", run.get("steps") == 30, str(run.get("steps")))
            check("cfg в паспорте веб-пути", run.get("cfg") == 6.0, str(run.get("cfg")))
            check("сэмплер в паспорте веб-пути", run.get("sampler") == "euler_ancestral",
                  str(run.get("sampler")))
            check("оба прохода в паспорте веб-пути",
                  run.get("passes") == [{"denoise": 0.75, "steps": 30},
                                        {"denoise": 0.3, "steps": 15}],
                  str(run.get("passes")))
            check("обычный кадр не объявлен принятым", run.get("outcome") == "unknown",
                  str(run.get("outcome")))
            frame = (snap["frames"] or [{}])[0]
            check("снимок задания отдаёт настройки кадра",
                  frame.get("steps") == 30 and frame.get("cfg") == 6.0
                  and frame.get("sampler") == "euler_ancestral", str(frame))
            check("планировщик в снимке", frame.get("scheduler") == "normal", str(frame))
            check("снимок задания отдаёт оба прохода",
                  frame.get("passes") == [{"denoise": 0.75, "steps": 30},
                                          {"denoise": 0.3, "steps": 15}],
                  str(frame.get("passes")))
            check("снимок задания отдаёт метку исхода", frame.get("outcome") == "unknown",
                  str(frame.get("outcome")))
    finally:
        lm.stop()
        comfy.stop()


def test_snapshot_frame_keys():
    """5b) снимок задания: проходы и исход есть, и ни один ключ кадра не пропал.

    `passes` и `outcome` попали в белый список вместе с паспортом схемы 2.
    Проверка сторожит весь набор: интерфейс читает 13 ключей из 17, и новый
    ключ не должен вытеснить старый.
    """
    lm, comfy = lm_stub(), comfy_stub()
    try:
        with stand(lm, comfy) as root:
            img = TMP / "frame_keys.png"
            make(img, (33, 44, 55))
            new_frame(comfy, "p1")
            svc = JobService(jobs_root=root / "out" / "jobs")
            job = svc.submit(jobs.JobRequest(
                image="", workflow="07_photo_thepit", subject="adult woman, keys",
                seed=4), source_path=str(img))
            snap = svc.wait(job.id, timeout=60)
            frame = (snap["frames"] or [{}])[0]
            want = {"file", "folder", "seed", "workflow", "cn_strength", "style_weight",
                    "denoise", "refined", "subject", "positive", "negative", "steps",
                    "cfg", "sampler", "scheduler", "passes", "outcome"}
            check("снимок отдаёт все ключи кадра", set(frame) == want,
                  str(sorted(want ^ set(frame))))
            check("проходы и исход в снимке на месте",
                  bool(frame.get("passes")) and frame.get("outcome") == "unknown",
                  str((frame.get("passes"), frame.get("outcome"))))
    finally:
        lm.stop()
        comfy.stop()


def test_failures():
    print("\n6) сбой API, пустой outputs, пропавший кадр")
    lm, comfy = lm_stub(), comfy_stub()
    try:
        with stand(lm, comfy):
            img = TMP / "fail.png"
            make(img, (5, 5, 5))
            comfy.state.prompt_fail = True
            r = jobs.run_job(jobs.JobRequest(image=str(img), workflow="07_photo_thepit",
                                             subject="adult woman", seed=1))
            check("отказ /prompt — провал", r.code == 1 and not r.ok, r.error)
            check("отказ API назван", "HTTP 400" in r.error, r.error)
            check("паспорт не создан", book_for(img) is None, str(book_for(img)))

            events = []
            comfy.state.prompt_fail = False
            comfy.state.empty_outputs = True
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                r = jobs.run_job(jobs.JobRequest(image=str(img), workflow="07_photo_thepit",
                                                 subject="adult woman", seed=2),
                                 emit=events.append)
            text = buf.getvalue()
            check("success без кадра — провал", r.code == 1 and not r.ok, r.error)
            check("сказано, что изображения нет", "изображения нет" in text, text[-160:])

            comfy.state.empty_outputs = False
            comfy.state.make_files = False
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                r = jobs.run_job(jobs.JobRequest(image=str(img), workflow="07_photo_thepit",
                                                 subject="adult woman", seed=3),
                                 emit=events.append)
            text = buf.getvalue()
            # ComfyUI назвал frame, но файла нет: save_run отказывает
            check("файла нет — паспорт не пишется", r.code == 1 and book_for(img) is None,
                  r.error)
            check("причина названа", "кадра нет по пути" in text, text[-200:])
    finally:
        lm.stop()
        comfy.stop()


def test_ws_fallback():
    print("\n7) WebSocket не открылся — кадр всё равно получен через /history")
    lm, comfy = lm_stub(), comfy_stub()
    try:
        with stand(lm, comfy):
            img = TMP / "ws.png"
            make(img, (7, 7, 70))
            comfy.state.ws_disabled = True
            events = []
            jobs.run_job(jobs.JobRequest(
                image=str(img), workflow="07_photo_thepit", subject="adult woman",
                seed=1, client_id="client-test"), emit=events.append)
            stages = [e["stage"] for e in events]
            check("сокет не помешал успеху", any(s == "done" for s in stages), str(stages))
            check("отказ сокета виден в событиях", "ws_failed" in stages, str(stages))
            check("шаги не выдуманы без сокета",
                  "sampling" not in stages, str(stages))
            ask = comfy.state.prompts[0]
            check("client_id ушёл в /prompt", ask.get("client_id") == "client-test",
                  str(ask.get("client_id")))
    finally:
        lm.stop()
        comfy.stop()


def test_ws_steps():
    print("\n7a) WebSocket отдаёт шаги сэмплера")
    lm, comfy = lm_stub(), comfy_stub()
    try:
        with stand(lm, comfy):
            img = TMP / "ws_steps.png"
            make(img, (70, 7, 40))
            comfy.state.delay = 15
            events = []
            r = jobs.run_job(jobs.JobRequest(
                image=str(img), workflow="07_photo_thepit", subject="adult woman",
                seed=1, client_id="client-steps"), emit=events.append)
            stages = [e["stage"] for e in events]
            sampling = [e for e in events if e["stage"] == "sampling"]
            check("задание успешно", r.ok, r.error)
            check("сокет открылся и прислал старт", "comfy_start" in stages, str(stages))
            check("закэшированные узлы видны", "comfy_cached" in stages, str(stages))
            check("шаги получены", bool(sampling) and sampling[-1]["data"].get("max") == 20,
                  str(sampling[-1:]))
            check("шаг и номер узла в событии",
                  sampling[-1]["data"].get("value") == 12
                  and sampling[-1]["data"].get("node") == "13", str(sampling[-1]))
            check("конец сэмплера отмечен", "comfy_done" in stages, str(stages))
    finally:
        lm.stop()
        comfy.stop()


def test_service_queue():
    print("\n8) две вкладки: очередь и состояние на сервере")
    lm, comfy = lm_stub(), comfy_stub()
    try:
        with stand(lm, comfy) as root:
            img = TMP / "queue.png"
            make(img, (80, 10, 80))
            comfy.state.delay = 40          # история «думает» несколько опросов
            svc = JobService(jobs_root=root / "out" / "jobs")
            j1 = svc.submit(jobs.JobRequest(image="", workflow="07_photo_thepit",
                                            subject="adult woman, one", seed=1),
                            source_path=str(img))
            uid, _ = svc.save_upload("two.png", img.read_bytes())
            j2 = svc.submit(jobs.JobRequest(image="", workflow="07_photo_thepit",
                                            subject="adult woman, two", seed=2),
                            upload_id=uid)
            check("активная одна", svc.active_id() == j1.id, str(svc.active_id()))
            check("вторая задача в очереди или уже ждёт исполнителя",
                  j2.status in ("queued", "running"), j2.status)
            first = svc.wait(j1.id, timeout=60)
            check("первое задание дошло", first["status"] == "done", str(first["status"]))
            second = svc.wait(j2.id, timeout=60)
            check("очередь дошла до второй", second["status"] in ("done", "error"),
                  str(second["status"]))
            check("вторая не начиналась до конца первой",
                  j2.started >= j1.finished - 0.05, f"{j2.started} < {j1.finished}")
            again = svc.snapshot(j1.id)
            check("обновление страницы видит то же задание",
                  again["id"] == j1.id and again["frames"], str(again["id"]))
            check("кадры привязаны к папкам задания",
                  all(f["folder"] for f in again["frames"]), str(again["frames"]))
            check("папка результата носит имя фото, а не source",
                  Path(again["frames"][0]["folder"]).name.startswith("queue_"),
                  Path(again["frames"][0]["folder"]).name)
            check("номер варианта виден и после длинного прогона",
                  again.get("variant", {}).get("index") == 1
                  and again["variant"].get("total") == 1, str(again.get("variant")))
    finally:
        lm.stop()
        comfy.stop()


def test_generator_to_form():
    print("\n9) изменение поля в генераторе доходит до формы")
    out = TMP / "wf_build"
    out.mkdir(exist_ok=True)
    saved_out, saved_dir = bw.OUT, pp.WF_DIR
    bw.OUT = str(out)
    try:
        spec = json.loads(json.dumps(bw.WORKFLOWS["07_photo_thepit"]))
        bw.build("07_photo_thepit", spec)
        meta = json.loads((out / "07_photo_thepit.api.json").read_text(encoding="utf-8"))["meta"]
        check("denoise по умолчанию из генератора", meta["fields"]["denoise"]["default"] == 0.75,
              str(meta["fields"]["denoise"]))
        for node in spec["nodes"]:
            if node["id"] == 13:
                node["w"]["denoise"] = 0.83
        bw.build("07_photo_thepit", spec)
        meta = json.loads((out / "07_photo_thepit.api.json").read_text(encoding="utf-8"))["meta"]
        check("после пересборки форма увидит 0.83",
              meta["fields"]["denoise"]["default"] == 0.83, str(meta["fields"]["denoise"]))
        pp.WF_DIR = out
        check("каталог читает новое значение", catalog.load_meta("07_photo_thepit")
              ["fields"]["denoise"]["default"] == 0.83)
    finally:
        bw.OUT, pp.WF_DIR = saved_out, saved_dir


def test_upload_validation():
    print("\n10) загрузка: только картинки, имя не выходит из папки")
    root = TMP / "uploads_root"
    svc = JobService(jobs_root=root)
    try:
        uid, path = svc.save_upload("../../evil.png", _png_bytes())
        check("файл лёг в папку загрузки",
              path.exists() and path.parent.parent == root / "uploads", str(path))
        check("имя без переходов",
              ".." not in path.relative_to(root).parts, str(path.relative_to(root)))
        check("по id файл находится", svc.upload_path(uid) == path)
        check("чужой id не отдаётся", svc.upload_path("../..") is None)
        try:
            svc.save_upload("bad.png", b"not an image")
            check("не картинка отвергается", False)
        except UploadError:
            check("не картинка отвергается", True)
    finally:
        svc.shutdown()


def test_job_retention():
    print("\n22) срок хранения: память, папки заданий, черновики")
    lm, comfy = lm_stub(), comfy_stub()
    try:
        with stand(lm, comfy) as root:
            img = TMP / "retention.png"
            make(img, (20, 40, 60))
            comfy.state.delay = 3                 # первое задание успевает побыть running
            svc = JobService(jobs_root=root / "out" / "jobs", keep_hours=24.0, keep_jobs=30)
            try:
                uid, upload = svc.save_upload("retention.png", img.read_bytes())
                first = svc.submit(jobs.JobRequest(
                    image="", workflow="07_photo_thepit", subject="adult woman, one", seed=1),
                    source_path=str(img))
                while svc.snapshot(first.id)["status"] != "running":
                    time.sleep(0.05)
                second = svc.submit(jobs.JobRequest(
                    image="", workflow="07_photo_thepit", subject="adult woman, two", seed=2),
                    source_path=str(img))
                check("второе задание в очереди", svc.snapshot(second.id)["status"] == "queued",
                      svc.snapshot(second.id)["status"])
                check("черновик загрузки жив после «Старт»", svc.upload_path(uid) == upload)
                gone = svc.purge(now=time.time() + 30 * 3600)
                check("работающее и очередное не снимаются", gone == [], str(gone))
                check("их папки целы",
                      (root / "out" / "jobs" / first.id).is_dir()
                      and (root / "out" / "jobs" / second.id).is_dir())
                svc.wait(first.id, timeout=60)
                svc.wait(second.id, timeout=60)
                frames = [Path(f["folder"]) for f in (svc.snapshot(first.id)["frames"] or [])]
                gone = svc.purge(now=time.time())
                check("свежие завершённые остаются", gone == [], str(gone))
                check("папки двух заданий на диске",
                      (root / "out" / "jobs" / first.id).is_dir()
                      and (root / "out" / "jobs" / second.id).is_dir())

                gone = svc.purge(now=time.time() + 25 * 3600)
                check("истёкшие сняты из памяти",
                      svc.get(first.id) is None and svc.get(second.id) is None, str(gone))
                check("папки истёкших удалены",
                      not (root / "out" / "jobs" / first.id).exists()
                      and not (root / "out" / "jobs" / second.id).exists())
                check("кадры и run.json на месте",
                      bool(frames) and all((f / "run.json").exists() for f in frames),
                      str(frames))
                check("истёкший черновик удалён",
                      not (root / "out" / "jobs" / "uploads" / uid).exists())

                svc2 = JobService(jobs_root=root / "out" / "jobs2", keep_jobs=1)
                try:
                    a = svc2.submit(jobs.JobRequest(image="", workflow="07_photo_thepit",
                                                    subject="adult woman, a", seed=3,
                                                    generate=False), source_path=str(img))
                    svc2.wait(a.id, timeout=60)
                    b = svc2.submit(jobs.JobRequest(image="", workflow="07_photo_thepit",
                                                    subject="adult woman, b", seed=4,
                                                    generate=False), source_path=str(img))
                    svc2.wait(b.id, timeout=60)
                    gone = svc2.purge()
                    check("сверх последних 30 снимается старшее", gone == [a.id], str(gone))
                    check("последнее задание осталось", svc2.get(b.id) is not None)
                    check("его папка цела", (root / "out" / "jobs2" / b.id).is_dir())
                finally:
                    svc2.shutdown()
            finally:
                svc.shutdown()
    finally:
        lm.stop()
        comfy.stop()


def test_expired_job_text():
    print("\n23) истёкшее задание объясняет себя")
    check("без задания — прежняя подпись",
          missing_job_text(None) == "Задание не запущено", missing_job_text(None))
    text = missing_job_text("deadbeef00")
    check("с id — истёкшая история", "истекла" in text and "out" in text, text)
    check("это не «не запущено»", "не запущено" not in text, text)


def test_past_jobs_swept():
    print("\n24) папки прошлых запусков убираются при подъёме сервера")
    jobs_dir = TMP / "past_jobs" / "out" / "jobs"
    old = jobs_dir / "aaaaaaaab1"        # имя как id задания
    fresh = jobs_dir / "bbbbbbbbb2"
    foreign = jobs_dir / "keep_me"       # не id: такая папка не наша
    upload = jobs_dir / "uploads" / "cccccccccc"
    for d in (old, fresh, foreign, upload):
        d.mkdir(parents=True, exist_ok=True)
        (d / "src.png").write_bytes(b"png")
    past = time.time() - 40 * 3600
    os.utime(old, (past, past))
    os.utime(upload, (past, past))
    svc = JobService(jobs_root=jobs_dir)
    try:
        check("папка старого задания удалена", not old.exists())
        check("свежая папка осталась", fresh.exists())
        check("папка не с id не тронута", foreign.exists())
        check("истёкший черновик удалён", not upload.exists())
    finally:
        svc.shutdown()


def test_upload_headers():
    print("\n25) загрузка: размер из заголовка PNG и JPEG")
    svc = JobService(jobs_root=TMP / "header_root")
    try:
        broken_crc = bytearray(_png_bytes())
        struct.pack_into(">I", broken_crc, 16, 9000)     # IHDR без пересчёта CRC
        struct.pack_into(">I", broken_crc, 20, 300)
        try:
            svc.save_upload("crc.png", bytes(broken_crc))
            check("битый CRC не мешает отказу по размеру", False)
        except UploadError as e:
            check("битый CRC не мешает отказу по размеру",
                  "9000×300" in str(e) and "2.7 Мпикс" in str(e), str(e))

        head = bytearray(PNG_SIGNATURE)
        head += struct.pack(">I", 13) + b"IHDR" + struct.pack(">IIBBBBB", 9000, 300, 8, 2, 0, 0, 0)
        head += struct.pack(">I", zlib.crc32(bytes(head[12:])))
        try:
            svc.save_upload("head.png", bytes(head))
            check("обрезанный PNG режется по заголовку", False)
        except UploadError as e:
            check("обрезанный PNG режется по заголовку", "9000×300" in str(e), str(e))

        try:
            svc.save_upload("tiny.png", _png_bytes()[:20])
            check("мелкий обрезок — «не картинка»", False)
        except UploadError as e:
            check("мелкий обрезок — «не картинка»", "не картинка" in str(e), str(e))

        buf = io.BytesIO()
        Image.new("RGB", (64, 64), (5, 6, 7)).save(buf, "JPEG")
        jpeg = bytearray(buf.getvalue())
        sof = jpeg.find(b"\xff\xc0")
        struct.pack_into(">H", jpeg, sof + 5, 30000)     # высота в SOF0
        struct.pack_into(">H", jpeg, sof + 7, 30000)     # ширина
        try:
            svc.save_upload("huge.jpg", bytes(jpeg))
            check("JPEG-бомба отвергается по заголовку", False)
        except UploadError as e:
            check("JPEG-бомба отвергается по заголовку",
                  "30000×30000" in str(e) and "900.0 Мпикс" in str(e), str(e))
    finally:
        svc.shutdown()


def test_upload_limits():
    print("\n21) загрузка: предел картинки до декодирования")
    from webapp import service as svc_mod
    svc = JobService(jobs_root=TMP / "limit_root")
    try:
        wide = TMP / "wide.png"
        Image.new("RGB", (8200, 8), (9, 9, 9)).save(wide)
        try:
            svc.save_upload("wide.png", wide.read_bytes())
            check("картинка шире предела отвергается", False)
        except UploadError as e:
            check("картинка шире предела отвергается", True)
            check("в ошибке сторона, предел и площадь",
                  "8200×8" in str(e) and "8192" in str(e) and "64" in str(e), str(e))

        bomb = bytearray(_png_bytes())
        struct.pack_into(">I", bomb, 16, 7900)      # ширина в IHDR
        struct.pack_into(">I", bomb, 20, 8192)      # высота: стороны в норме
        struct.pack_into(">I", bomb, 29, zlib.crc32(bytes(bomb[12:29])) & 0xffffffff)
        try:
            svc.save_upload("bomb.png", bytes(bomb))
            check("площадь свыше предела отвергается", False)
        except UploadError as e:
            check("площадь свыше предела отвергается",
                  "7900×8192" in str(e) and "64.7 Мпикс" in str(e), str(e))
            check("это отказ по размеру, а не «не картинка»",
                  "не картинка" not in str(e), str(e))
        check("нормальная картинка по-прежнему проходит",
              svc.upload_path(svc.save_upload("ok.png", _png_bytes())[0]) is not None)
    finally:
        svc.shutdown()


def test_overrides():
    print("\n11) переопределения: поля, LoRA и полный промпт")
    comfy = comfy_stub()
    try:
        with stand(lm=None, comfy=comfy):
            img = TMP / "override.png"
            make(img, (33, 66, 99))
            r = jobs.run_job(jobs.JobRequest(
                image=str(img), workflow="07_photo_thepit", seed=1,
                subject="adult woman, ignored by override",
                positive_override="MY OWN POSITIVE, adult woman",
                negative_override="MY OWN NEGATIVE",
                overrides={"fields": {"checkpoint": "other.safetensors", "steps": 12},
                           "loras": {"character": {"name": "other_lora.safetensors",
                                                   "weight": 0.4}}}))
            check("задание с переопределениями успешно", r.ok, r.error)
            sent = comfy.state.prompts[-1]["prompt"]
            check("чекпойнт заменён по привязке",
                  sent["1"]["inputs"]["ckpt_name"] == "other.safetensors",
                  str(sent["1"]["inputs"]))
            check("шаги заменены по привязке", sent["13"]["inputs"]["steps"] == 12,
                  str(sent["13"]["inputs"].get("steps")))
            check("слот персонажа заменён",
                  sent["4"]["inputs"]["lora_name"] == "other_lora.safetensors"
                  and sent["4"]["inputs"]["strength_model"] == 0.4, str(sent["4"]["inputs"]))
            texts = {n["inputs"].get("text") for n in sent.values()
                     if n["class_type"] == "CLIPTextEncode"}
            check("полный positive сохранён и санитизирован",
                  any(str(t).startswith("adult woman") and "MY OWN POSITIVE" in str(t)
                      for t in texts), str(texts)[:200])
            check("полный negative сохранён и замкнут рамкой",
                  any(str(t).startswith("MY OWN NEGATIVE")
                      and str(t).endswith("child, loli, shota, underage")
                      for t in texts), str(texts)[:200])
            check("шаблон стиля не подмешан к переопределению",
                  not any("theofficialpit" in str(t) for t in texts), str(texts)[:200])
            record = book_for(img)["runs"][-1]
            check("переопределения записаны в паспорт",
                  record.get("overrides", {}).get("fields", {}).get("checkpoint")
                  == "other.safetensors"
                  and record["overrides"]["fields"].get("steps") == 12
                  and record["overrides"]["loras"]["character"]["weight"] == 0.4,
                  str(record.get("overrides")))

            bad = jobs.run_job(jobs.JobRequest(
                image=str(img), workflow="07_photo_thepit", seed=2,
                subject="adult woman",
                overrides={"loras": {"no-such-role": {"weight": 0.5}}}))
            check("неизвестный слот LoRA — отказ", bad.code == 1 and
                  "нет слота" in bad.error, bad.error)
            check("неверный граф не отправлен", len(comfy.state.prompts) == 1,
                  str(len(comfy.state.prompts)))
    finally:
        comfy.stop()


def test_pass_logic():
    print("\n12) полоска шагов относится к текущему проходу")
    events = [
        {"stage": "variant", "data": {"index": 1, "total": 2}},
        {"stage": "sampling", "data": {"value": 30, "max": 30, "node": "13"}},
        {"stage": "save", "data": {"file": "a.png"}},
        {"stage": "sampling", "data": {"value": 0.0, "max": 1.0, "node": "19"}},
    ]
    check("после сохранения полоска не врёт", pass_steps(events, ["13"]) is None)
    events += [
        {"stage": "variant", "data": {"index": 2, "total": 2}},
        {"stage": "sampling", "data": {"value": 5, "max": 30, "node": "13"}},
    ]
    step = pass_steps(events, ["13"])
    check("новый вариант показывает свой шаг",
          bool(step) and step["data"]["value"] == 5, str(step))
    check("препроцессор не считается шагом сэмплера",
          pass_steps([{"stage": "sampling",
                       "data": {"value": 1, "max": 1, "node": "9"}}], ["13"]) is None)
    check("старый граф без samplers: только max > 1",
          pass_steps([{"stage": "sampling", "data": {"value": 1, "max": 1, "node": "9"}},
                      {"stage": "sampling", "data": {"value": 3, "max": 8, "node": "13"}}])
          is not None)
    check("recheck скрывает полоску до своих шагов",
          pass_steps(events + [{"stage": "recheck", "data": {}}], ["13"]) is None)
    check("настройка: None — по умолчанию, ноль — ноль",
          fmt_setting(None) == "по умолчанию" and fmt_setting(0) == "0"
          and fmt_setting(0.0) == "0" and fmt_setting(0.25) == "0.25",
          f"{fmt_setting(None)} {fmt_setting(0)} {fmt_setting(0.25)}")


def test_recheck_frames():
    print("\n13) кадр recheck доходит до результата и паспорта")
    lm, comfy = lm_stub(), comfy_stub()
    try:
        with stand(lm, comfy) as root:
            img = TMP / "recheck_frames.png"
            make(img, (21, 42, 63))
            r = jobs.run_job(jobs.JobRequest(
                image=str(img), workflow="07_photo_thepit", subject="adult woman, plain",
                recheck=True, seed=5, style_weight=0.7))
            check("уточнение сделало второй кадр", r.ok and len(r.frames) == 2,
                  f"{r.ok} {len(r.frames)} {r.error}")
            check("первый кадр — основной", r.frames[0].get("refined") is False,
                  str(r.frames[0].get("refined")))
            check("второй кадр — уточнённый", r.frames[1].get("refined") is True,
                  str(r.frames[1].get("refined")))
            check("файлы разные", r.frames[0]["file"] != r.frames[1]["file"],
                  str([f["file"] for f in r.frames]))
            book = book_for(img)
            check("в run.json оба кадра с меткой",
                  [x.get("refined") for x in book["runs"]] == [False, True],
                  str([x.get("refined") for x in book["runs"]]))
            check("исход кадров размечен",
                  [x.get("outcome") for x in book["runs"]] == ["unknown", "refined"],
                  str([x.get("outcome") for x in book["runs"]]))
            check("промпт уточнённого кадра содержит добавку",
                  "silver bracelet" in book["runs"][-1]["subject"],
                  book["runs"][-1]["subject"][:100])

            svc = JobService(jobs_root=root / "out" / "jobs")
            job = svc.submit(jobs.JobRequest(
                image="", workflow="07_photo_thepit", subject="adult woman, second",
                recheck=True, seed=6), source_path=str(TMP / "recheck_frames.png"))
            snap = svc.wait(job.id, timeout=60)
            check("снимок задания отдаёт оба кадра",
                  len(snap["frames"]) == 2
                  and snap["frames"][-1].get("refined") is True, str(snap["frames"])[:200])
            check("снимок задания отдаёт метки исходов",
                  [f.get("outcome") for f in snap["frames"]] == ["unknown", "refined"],
                  str([f.get("outcome") for f in snap["frames"]]))
            check("download-имена привязаны к заданию",
                  all(Path(f["file"]).name for f in snap["frames"]), str(snap["frames"]))
    finally:
        lm.stop()
        comfy.stop()


def test_source_tag():
    print("\n14) задание помнит, с какого фото сделано")
    comfy = comfy_stub()
    try:
        with stand(lm=None, comfy=comfy) as root:
            a = TMP / "tag_a.png"
            b = TMP / "tag_b.png"
            make(a, (1, 2, 3))
            make(b, (9, 8, 7))
            svc = JobService(jobs_root=root / "out" / "jobs")
            job_a = svc.submit(jobs.JobRequest(
                image="", workflow="07_photo_thepit", subject="adult woman", seed=1),
                source_path=str(a))
            job_b = svc.submit(jobs.JobRequest(
                image="", workflow="07_photo_thepit", subject="adult woman", seed=2),
                source_path=str(b))
            tag_a = svc.get(job_a.id).source_tag
            tag_b = svc.get(job_b.id).source_tag
            check("тег совпадает с отпечатком фото",
                  tag_a == pp.image_tag(str(svc.get(job_a.id).source)), tag_a)
            check("разные фото — разные теги", tag_a != tag_b, f"{tag_a} {tag_b}")
            svc.wait(job_a.id, timeout=60)
            svc.wait(job_b.id, timeout=60)
            check("снимок отдаёт тег фото",
                  svc.snapshot(job_a.id)["source_tag"] == tag_a)
    finally:
        comfy.stop()


def test_comfy_rule():
    print("\n15) каталог, health и генерация выбирают один адрес ComfyUI")
    comfy = comfy_stub()
    saved_env = os.environ.get("COMFY_URL")
    saved_url = pp.settings.COMFY_URL
    try:
        os.environ.pop("COMFY_URL", None)
        pp.settings.COMFY_URL = comfy.url
        url, err = pp.comfy_state()
        check("без явного адреса берётся настроенный", url == comfy.url and not err,
              f"{url} {err}")
        check("каталог видит тот же адрес", catalog.find_comfy() == comfy.url,
              catalog.find_comfy())
        os.environ["COMFY_URL"] = "http://127.0.0.1:1"
        url, err = pp.comfy_state()
        check("явный недоступный адрес не подменяется локальным",
              url == "http://127.0.0.1:1" and "задан явно" in err, f"{url} {err}")
        check("каталог показывает тот же явный адрес",
              catalog.find_comfy() == "http://127.0.0.1:1", catalog.find_comfy())
        info = catalog.health("http://127.0.0.1:2")
        check("health даёт ту же диагностику",
              info["comfy"]["ok"] is False and "задан явно" in info["comfy"]["error"],
              str(info["comfy"]))
        code = None
        try:
            pp.resolve_comfy(None)
        except SystemExit as e:
            code = e.code
        check("генерация отказывает с той же причиной",
              code and "задан явно" in str(code), str(code))
    finally:
        pp.settings.COMFY_URL = saved_url
        if saved_env is None:
            os.environ.pop("COMFY_URL", None)
        else:
            os.environ["COMFY_URL"] = saved_env
        comfy.stop()


def test_recheck_isolation():
    print("\n16) Recheck не меняет промпт следующих вариантов")
    lm, comfy = lm_stub(), comfy_stub()
    try:
        with stand(lm, comfy):
            img = TMP / "variants.png"
            make(img, (11, 77, 33))
            r = jobs.run_job(jobs.JobRequest(
                image=str(img), workflow="07_photo_thepit",
                subject="adult woman, plain", variants=2, recheck=True, seed=5))
            check("два основных кадра и уточнённый",
                  len(r.frames) == 3
                  and [f["refined"] for f in r.frames] == [False, True, False],
                  str([(f["file"], f["refined"]) for f in r.frames]))
            texts = []
            for payload in comfy.state.prompts:
                texts.append(next(n["inputs"]["text"] for n in payload["prompt"].values()
                                  if n["class_type"] == "CLIPTextEncode"
                                  and "theofficialpit" in str(n["inputs"]["text"])))
            check("уточнение добавило тег только своему кадру",
                  "silver bracelet" in texts[1]
                  and "silver bracelet" not in texts[0]
                  and "silver bracelet" not in texts[2],
                  str([t[-60:] for t in texts]))
            check("второй вариант ушёл с исходным промптом", texts[0] == texts[2],
                  str([t[-60:] for t in texts]))
    finally:
        lm.stop()
        comfy.stop()


def test_recheck_with_override():
    print("\n17) Recheck при полном переопределении positive")
    lm, comfy = lm_stub(), comfy_stub()
    try:
        with stand(lm, comfy):
            img = TMP / "override_recheck.png"
            make(img, (44, 55, 66))
            r = jobs.run_job(jobs.JobRequest(
                image=str(img), workflow="07_photo_thepit",
                subject="adult woman, ignored", positive_override="MY POSITIVE, adult woman",
                recheck=True, seed=3))
            check("уточнённый кадр создан",
                  len(r.frames) == 2 and r.frames[-1]["refined"] is True,
                  str([(f["file"], f["refined"]) for f in r.frames]))
            texts = [next(n["inputs"]["text"] for n in p["prompt"].values()
                          if n["class_type"] == "CLIPTextEncode"
                          and "MY POSITIVE" in str(n["inputs"]["text"]))
                     for p in comfy.state.prompts]
            check("повтор ушёл с изменённым переопределением",
                  texts[0] == "adult woman, MY POSITIVE"
                  and "silver bracelet" in texts[1] and texts[1] != texts[0],
                  str(texts))
            check("фактический positive уточнённого кадра в галерее",
                  "silver bracelet" in (r.frames[-1].get("positive") or ""),
                  str(r.frames[-1].get("positive"))[:100])
            check("основной кадр хранит своё переопределение",
                  r.frames[0]["positive"] == "adult woman, MY POSITIVE",
                  r.frames[0]["positive"])
    finally:
        lm.stop()
        comfy.stop()


def test_frame_prompt_passport():
    print("\n18) паспорт и карточка кадра помнят фактический промпт")
    lm, comfy = lm_stub(), comfy_stub()
    try:
        with stand(lm, comfy):
            img = TMP / "prompt_book.png"
            make(img, (13, 31, 57))
            r = jobs.run_job(jobs.JobRequest(
                image=str(img), workflow="07_photo_thepit",
                subject="adult woman, exact text", seed=1,
                negative_extra="zzz-drop", style_weight=0.8))
            sent = comfy.state.prompts[0]["prompt"]
            pos = next(n["inputs"]["text"] for n in sent.values()
                       if n["class_type"] == "CLIPTextEncode"
                       and "theofficialpit" in str(n["inputs"]["text"]))
            neg = next(n["inputs"]["text"] for n in sent.values()
                       if n["class_type"] == "CLIPTextEncode"
                       and str(n["inputs"]["text"]).startswith("zzz-drop"))
            check("positive кадра совпадает с отправленным",
                  r.frames[0]["positive"] == pos, r.frames[0]["positive"][:80])
            check("negative кадра совпадает с отправленным",
                  r.frames[0]["negative"] == neg, r.frames[0]["negative"][:80])
            book = book_for(img)["runs"][-1]
            check("run.json хранит фактический positive", book.get("positive") == pos,
                  str(book.get("positive"))[:80])
            check("run.json хранит фактический negative", book.get("negative") == neg,
                  str(book.get("negative"))[:80])
            check("в positive есть и шаблон стиля, и описание",
                  "theofficialpit" in pos and "exact text" in pos, pos[:100])
    finally:
        lm.stop()
        comfy.stop()


def test_preview_matches_frame():
    print("\n18.1) предпросмотр собирает тот же промпт, что кадр")
    comfy = comfy_stub()
    try:
        with stand(lm=None, comfy=comfy):
            for workflow in ("07_photo_thepit", "08_photo_gta"):
                for size, kind in (((180, 240), "портрет"), ((260, 120), "горизонталь")):
                    img = TMP / f"preview_{workflow}_{kind}.png"
                    make(img, (21, 47, 83), size)
                    frame_events, dry_events = [], []
                    r = jobs.run_job(jobs.JobRequest(
                        image=str(img), workflow=workflow, seed=5, tags=False,
                        subject="adult woman, red dress, standing"),
                        emit=frame_events.append)
                    dry = jobs.run_job(jobs.JobRequest(
                        image=str(img), workflow=workflow, seed=5, tags=False,
                        subject="adult woman, red dress, standing", dry_run=True),
                        emit=dry_events.append)
                    label = f"{workflow}, {kind}"
                    sent = comfy.state.prompts[-1]["prompt"]
                    load = next(n["inputs"]["image"] for n in sent.values()
                                if n["class_type"] == "LoadImage")
                    frame = next(e for e in frame_events if e["stage"] == "prompt")["data"]
                    preview = next(e for e in dry_events if e["stage"] == "prompt")["data"]
                    check(f"{label}: positive предпросмотра = кадр",
                          r.ok and dry.ok and dry.positive == frame["positive"]
                          == r.frames[0]["positive"],
                          f"{dry.positive[:80]} | {frame['positive'][:80]}")
                    check(f"{label}: negative предпросмотра = кадр",
                          dry.negative == frame["negative"] == r.frames[0]["negative"],
                          f"{dry.negative[:80]} | {frame['negative'][:80]}")
                    check(f"{label}: предпросмотр знает имя input-файла кадра",
                          preview["applied"].get("image_name") == load,
                          f"{preview['applied'].get('image_name')!r} != {load!r}")
                    check(f"{label}: предпросмотр знает ориентацию кадра",
                          preview["applied"].get("landscape")
                          == pp.image_is_landscape(str(img)),
                          str(preview["applied"]))
    finally:
        comfy.stop()


def test_comfy_launch_plan():
    print("\n19) headless ComfyUI: план запуска")
    main = TMP / "fake_comfy" / "main.py"
    main.parent.mkdir(parents=True, exist_ok=True)
    main.write_text("", encoding="utf-8")
    cfg = TMP / "comfy_launch.json"
    cfg.write_text(json.dumps({
        "main": str(main), "python": sys.executable, "port": 8123,
        "stop_after": True, "startup_timeout": 2,
    }), encoding="utf-8")
    saved_env = {k: os.environ.get(k) for k in
                 ("COMFY_LAUNCH_CONFIG", "COMFY_MAIN", "COMFY_PYTHON")}
    os.environ["COMFY_LAUNCH_CONFIG"] = str(cfg)
    for key in ("COMFY_MAIN", "COMFY_PYTHON"):
        os.environ.pop(key, None)
    try:
        p = cl.plan()
        check("план собран из локального конфига", p is not None and p["port"] == 8123,
              str(p))
        check("адрес и команда", p["url"] == "http://127.0.0.1:8123"
              and str(main) in p["argv"], str(p["argv"]))
        check("модельные пути и папки передаются",
              "--extra-model-paths-config" in p["argv"]
              and "--output-directory" in p["argv"]
              and "--input-directory" in p["argv"], str(p["argv"]))
        check("голова без окна и автооткрытия",
              "--disable-auto-launch" in p["argv"], str(p["argv"]))
        check("запуск разрешён", cl.available() is True)

        class FakeProc:
            def __init__(self):
                self.terminated = False
                self.rc = None

            def poll(self):
                return self.rc

            def terminate(self):
                self.terminated = True
                self.rc = 0

            def wait(self, timeout=None):
                return 0

            def kill(self):
                self.terminated = True

        saved = (pp.comfy_state, cl._spawn, pp.comfy_probe)
        try:
            fake = FakeProc()
            pp.comfy_state = lambda explicit=None: ("http://127.0.0.1:8123", "нет")
            cl._spawn = lambda config: fake
            pp.comfy_probe = lambda url: True
            events = []
            handle = cl.ensure(
                emit=lambda stage, message="", **data:
                events.append({"stage": stage, "message": message, "data": data}))
            check("сервер поднят и возвращён handle",
                  handle is not None and handle.url == "http://127.0.0.1:8123",
                  str(handle))
            check("поднятый сервер взят на учёт", handle in cl._ACTIVE)
            check("старт виден в событиях",
                  any(e["stage"] == "comfy_launch" for e in events), str(events))
            cl.stop(handle)
            check("stop гасит только свой сервер", fake.terminated is True)
            check("после stop учёт чист", handle not in cl._ACTIVE)

            orphan = cl.Handle(url="http://127.0.0.1:8123", proc=FakeProc(),
                               stop_after=False)
            cl._ACTIVE.append(orphan)
            cl._stop_all()
            check("выход из приложения гасит и stop_after=false",
                  orphan.proc.terminated is True and orphan not in cl._ACTIVE)

            pp.comfy_state = lambda explicit=None: ("http://127.0.0.1:9999", "")
            check("уже работающий сервер не трогаем", cl.ensure() is None)

            pp.comfy_state = lambda explicit=None: ("http://127.0.0.1:8123", "нет")
            pp.comfy_probe = lambda url: False
            dead = FakeProc()
            dead.rc = 1
            cl._spawn = lambda config: dead
            code = None
            try:
                cl.ensure()
            except pp.PipelineError as e:
                code = str(e)
            check("провал запуска — понятная ошибка",
                  code and "не поднялся" in code, str(code))
        finally:
            pp.comfy_state, cl._spawn, pp.comfy_probe = saved
    finally:
        for key, value in saved_env.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        os.environ.pop("COMFY_MAIN", None)
        os.environ.pop("COMFY_PYTHON", None)
        os.environ.pop("COMFY_LAUNCH_CONFIG", None)


def test_job_autostart():
    print("\n20) задание поднимает ComfyUI только перед генерацией")
    lm, comfy = lm_stub(), comfy_stub()
    saved = (cl.ensure, cl.stop)
    calls = []
    try:
        with stand(lm, comfy):
            class Handle:
                stop_after = True
                url = "http://127.0.0.1:8123"

            cl.ensure = lambda emit=None: (calls.append("ensure") or Handle())
            cl.stop = lambda handle, emit=None: calls.append("stop")
            photo = TMP / "autostart.png"
            make(photo, (5, 50, 5))
            r = jobs.run_job(jobs.JobRequest(
                image=str(photo), workflow="07_photo_thepit",
                subject="adult woman, auto", seed=1, start_comfy=True))
            check("задание успешно", r.ok, r.error)
            check("сначала подъём, потом остановка", calls == ["ensure", "stop"],
                  str(calls))
            calls.clear()
            jobs.run_job(jobs.JobRequest(
                image=str(photo), workflow="07_photo_thepit",
                subject="adult woman, auto", seed=2, generate=False,
                start_comfy=True))
            check("«только описание» не поднимает сервер", calls == [], str(calls))
            check("сервер не поднимался для кэша без генерации", calls == [])
    finally:
        cl.ensure, cl.stop = saved
        lm.stop()
        comfy.stop()


def _png_bytes():
    buf = io.BytesIO()
    Image.new("RGB", (8, 8), (1, 2, 3)).save(buf, "PNG")
    return buf.getvalue()


def test_openai_service_health():
    print("\n26) страница видит сервис описаний без LM Studio")
    stub = Stub(OpenAILMHandler)
    try:
        info = catalog.health(stub.url)
        check("сервис найден", info["lm"]["ok"] is True, str(info["lm"]))
        check("провайдер openai", info["lm"]["provider"] == "openai", str(info["lm"]))
        check("модели перечислены",
              info["lm"]["models"] == ["huihui@q4", "text-only@q4"],
              str(info["lm"]["models"]))
        check("адрес в ответе тот же", info["lm"]["url"] == stub.url, str(info["lm"]["url"]))
    finally:
        stub.stop()

    dead = catalog.lm_health("http://127.0.0.1:1")
    check("лежащий сервис: понятный отказ",
          dead["ok"] is False and dead["models"] == [] and not dead["provider"], str(dead))


# --------------------------------------------------------------- V2
V2 = Path(__file__).resolve().parent.parent / "webapp"
TOKENS = ("--bg", "--surface", "--surface-2", "--ink", "--ink-2", "--line",
          "--accent", "--accent-fill", "--accent-on", "--ok", "--warn", "--err")
HEX = re.compile(r"#[0-9a-fA-F]{3,8}\b")


def _css_rules(css):
    """Селектор → тело правила. Вложенность `@layer` и `@media` разбору не мешает."""
    rules = []
    for chunk in css.split("}"):
        if "{" not in chunk:
            continue
        selector, body = chunk.rsplit("{", 1)
        selector = selector.split("}")[-1].strip().splitlines()[-1].strip()
        rules.append((selector, body))
    return rules


def _v2_sources():
    """Только модули с разметкой: __init__.py — докстринг пакета, не компонент."""
    files = [p for p in sorted((V2 / "components").glob("*.py")) if p.name != "__init__.py"]
    files.append(V2 / "v2.py")
    return {p.name: p.read_text(encoding="utf-8") for p in files if p.exists()}


def test_theme_tokens():
    print("\n27) токены и классы V2 в одном месте")
    css = theme.css()
    check("обе темы объявляют все 12 токенов",
          set(theme.LIGHT) == set(TOKENS) == set(theme.DARK),
          str(sorted(set(theme.LIGHT) ^ set(TOKENS))))
    check("акцент: #7c3aed заливкой, #a78bfa текстом в тёмной",
          theme.LIGHT["--accent-fill"] == "#7c3aed"
          and theme.DARK["--accent-fill"] == "#7c3aed"
          and theme.LIGHT["--accent"] == "#7c3aed"
          and theme.DARK["--accent"] == "#a78bfa",
          f'{theme.LIGHT["--accent"]} / {theme.DARK["--accent"]}')
    check("пять ступеней типа и шесть отступов",
          [theme.SIZES[k] for k in ("--cp-fs-1", "--cp-fs-5")]
          == ["0.75rem", "1.5rem"]
          and sorted(theme.SIZES[f"--cp-sp-{n}"] for n in (1, 2, 3, 4, 6, 8))
          == ["12px", "16px", "24px", "32px", "4px", "8px"],
          str([theme.SIZES[k] for k in theme.SIZES if k.startswith("--cp-sp")]))
    check("числа раскладки из спецификации",
          theme.SIZES["--cp-topbar"] == "56px" and theme.SIZES["--cp-rail"] == "400px"
          and theme.SIZES["--cp-inspector"] == "320px"
          and theme.SIZES["--cp-gutter"] == "16px"
          and theme.SIZES["--cp-card-pad"] == "16px"
          and theme.SIZES["--cp-frame"] == "min(62vh, 720px)"
          and theme.SIZES["--cp-strip"] == "56px"
          and theme.SIZES["--cp-tabs"] == "32px",
          str(theme.SIZES))
    coloured = {sel for sel, body in _css_rules(css) if HEX.search(body)}
    check("литеральные цвета только в :root и body.body--dark",
          coloured == {":root", "body.body--dark"}, str(coloured))
    check("классы объявлены в @layer components",
          "@layer components {" in css
          and sum(1 for name in theme.CLASSES.values() if f".{name}" in css)
          >= len(theme.CLASSES) - 4,
          str([n for n in theme.CLASSES.values() if f".{n}" not in css]))
    check("акцент Quasar — внутри .cp-app, а не на :root",
          ".cp-app {" in css and "--q-primary: var(--accent-fill)" in css
          and ":root {\n  --q-primary" not in css)
    check("на широком экране рельс 420 px, и правило вне слоёв",
          "@media (min-width: 1600px)" in css
          and css.index("min-width: 1600px") < css.index("@layer components"),
          "правило внутри слоя проиграло бы базовым 400 px")
    check("тёмная тема — токены на body.body--dark", "body.body--dark {" in css)

    sources = _v2_sources()
    bad = [f"{name}: .{what}" for name, src in sources.items()
           for what in ("classes(", "style(") if f".{what}" in src]
    check("в компонентах нет литеральных .classes()/.style()", not bad, str(bad))
    bad = [f"{name}: {m}" for name, src in sources.items() for m in HEX.findall(src)]
    check("в компонентах нет литеральных цветов", not bad, str(bad))
    check("в компонентах нет ui.colors (он перекрасил бы и старую страницу)",
          not [name for name, src in sources.items() if "ui.colors(" in src])

    saved = os.environ.get("COMIC_THEME_FILE")
    file = TMP / "theme_v2.json"
    os.environ["COMIC_THEME_FILE"] = str(file)
    try:
        check("тема по умолчанию тёмная", theme.read() is True, str(theme.path()))
        theme.save(False)
        check("выбор темы пишется в файл из COMIC_THEME_FILE",
              theme.read() is False and file.exists(), str(file))
        theme.save(True)
        check("и читается обратно", theme.read() is True)
    finally:
        if saved is None:
            os.environ.pop("COMIC_THEME_FILE", None)
        else:
            os.environ["COMIC_THEME_FILE"] = saved


def test_collect_form():
    print("\n28) collect_form собирает JobRequest без NiceGUI")
    meta = catalog.load_meta("07_photo_thepit")
    request, why = collect_form({}, meta)
    check("пустое фото — отказ с причиной",
          request is None and "фото" in why, why)
    request, why = collect_form({"image": "abc", "workflow": "07_photo_thepit"}, meta)
    check("нормальная форма — JobRequest", isinstance(request, jobs.JobRequest) and why == "",
          why)
    check("стиль, сцена и варианты доехали",
          request.workflow == "07_photo_thepit" and request.variants == 1,
          str(request))
    check("описание и память: как в старой странице",
          request.subject is None and request.generate is True
          and request.write_cache is True and request.start_comfy is True
          and bool(request.client_id), str(request))

    values = {"image": "abc", "workflow": "07_photo_thepit", "scene": " на пляже ",
              "variants": 3, "denoise": 0.75, "cn_strength": 0.9, "seed": 7,
              "negative_extra": " rain ", "negative_drop": " text ",
              "fields": {"denoise": 0.75, "cn_strength": 0.9, "steps": 20},
              "loras": {"20": {"weight": 0.4}}, "recheck": True}
    request, why = collect_form(values, meta)
    check("denoise как в сборке не становится переопределением",
          request is not None and "denoise" not in request.overrides.get("fields", {}),
          str(request and request.overrides))
    check("переопределения и LoRA уходят в overrides",
          request.overrides.get("fields") == {"steps": 20}
          and request.overrides.get("loras", {}).get("hands", {}).get("weight") == 0.4,
          str(request.overrides))
    check("denoise и сила ControlNet — поля задания, а не overrides",
          request.denoise == 0.75 and request.cn_strength == 0.9
          and "denoise" not in request.overrides.get("fields", {}),
          str((request.denoise, request.cn_strength, request.overrides)))
    check("сцена, seed и негатив на месте",
          request.scene == "на пляже" and request.seed == 7
          and request.negative_extra == "rain" and request.negative_drop == "text"
          and request.variants == 3 and request.recheck is True, str(request))

    request, why = collect_form({"image": "abc", "workflow": "07_photo_thepit",
                                 "manual": True}, meta)
    check("ручной режим без описания — отказ",
          request is None and "описание" in why, why)
    request, _ = collect_form({"image": "abc", "workflow": "07_photo_thepit",
                               "manual": True, "subject": "adult woman"}, meta)
    check("ручной режим не пишет кэш и не генерит описание",
          request.subject == "adult woman" and request.write_cache is False,
          str(request))
    request, why = collect_form({"image": "abc", "workflow": "07_photo_thepit",
                                 "positive_override": "  "}, meta)
    check("пустое переопределение positive — отказ", request is None, why)
    request, why = collect_form({"image": "abc", "workflow": "нет_такой"}, meta)
    check("неизвестная сборка — отказ", request is None and why, why)

    src = (V2 / "collect.py").read_text(encoding="utf-8")
    check("collect.py без NiceGUI", "nicegui" not in src)


def test_labels_same_as_old_page():
    print("\n29) подписи V2 совпадают со старой страницей")
    check("этапы совпадают", labels.STAGE_LABEL == web_ui.STAGE_LABEL,
          str(set(labels.STAGE_LABEL.items()) ^ set(web_ui.STAGE_LABEL.items())))
    check("роли LoRA совпадают", labels.ROLE_LABEL == web_ui.ROLE_LABEL)
    check("очередь ComfyUI не подписана «в очереди»",
          labels.STAGE_LABEL["comfy_queue"] != labels.STAGE_LABEL["queued"],
          labels.STAGE_LABEL["comfy_queue"])
    check("состояния страницы покрывают спецификацию",
          set(labels.STATUS_LABEL) >= {"empty", "queued", "running", "described",
                                       "done", "partial", "failed", "expired",
                                       "blocked"},
          str(sorted(labels.STATUS_LABEL)))


def test_push_from_job():
    print("\n30) задание будит страницу, а не страница опрашивает задание")
    seen = []
    saved = jobs.run_job

    def listener(rev, job_id):
        seen.append((rev, job_id))

    def fake(req, emit=None):
        emit({"seq": 1, "t": 0.1, "stage": "start", "message": "", "data": {}})
        emit({"seq": 2, "t": 0.2, "stage": "done", "message": "", "data": {}})
        return jobs.JobResult(ok=True, code=0, stage="done")

    svc = JobService(jobs_root=TMP / "push_jobs")
    try:
        check("повторный watch не оборачивает пул дважды",
              push.watch(svc) is svc and push.watch(svc) is svc
              and isinstance(svc._pool, push._Pool), str(type(svc._pool)))
        jobs.run_job = fake
        push.hub.subscribe(listener)
        uid, _ = svc.save_upload("photo.png", _png_bytes())
        job = svc.submit(jobs.JobRequest(image="", workflow="07_photo_thepit",
                                         subject="adult woman"), upload_id=uid)
        svc.wait(job.id, timeout=20)
        check("оба шага дошли подписчику",
              [rev for rev, _ in seen] == [1, 2], str(seen))
        check("id задания в патче тот же", {jid for _, jid in seen} == {job.id},
              str(seen))
        check("снапшот сервиса не пострадал от обёртки",
              len(svc.snapshot(job.id)["events"]) == 2, str(svc.snapshot(job.id)["events"]))
    finally:
        jobs.run_job = saved
        push.hub.unsubscribe(listener)
        svc.shutdown()



def main():
    try:
        test_catalog_fields()
        test_full_cycle_and_cache()
        test_manual_without_lm()
        test_scene_cache()
        test_settings_in_passport()
        test_passport_sampler_settings()
        test_snapshot_frame_keys()
        test_failures()
        test_ws_fallback()
        test_ws_steps()
        test_service_queue()
        test_generator_to_form()
        test_upload_validation()
        test_overrides()
        test_pass_logic()
        test_recheck_frames()
        test_source_tag()
        test_comfy_rule()
        test_recheck_isolation()
        test_recheck_with_override()
        test_frame_prompt_passport()
        test_preview_matches_frame()
        test_comfy_launch_plan()
        test_job_autostart()
        test_upload_limits()
        test_job_retention()
        test_expired_job_text()
        test_past_jobs_swept()
        test_upload_headers()
        test_openai_service_health()
        test_theme_tokens()
        test_collect_form()
        test_labels_same_as_old_page()
        test_push_from_job()
    finally:
        shutil.rmtree(TMP, ignore_errors=True)
    print(f"\nитого: ok {len(passed)}, провалов {len(failed)}")
    if failed:
        print("провалено: " + ", ".join(failed))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
