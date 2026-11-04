"""«Связь с телефоном» (package phone): the OpenAI-compatible server Fluxion Mobile talks to."""
from __future__ import annotations

import json
import urllib.error
import urllib.request

import pytest

from phone import config, vision
from phone import server as phone_server
from phone.server import PhoneServer, as_text, has_image, lan_addresses
from phone.vision import VisionUnavailable

CODE = "K7M2-Q9XP"
PHOTO = [{"role": "user", "content": [{"type": "text", "text": "что это?"},
                                      {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64,AAAA"}}]}]


class FakeEngine:
    label = "Qwen2.5-VL-7B"

    def __init__(self):
        self.seen = None

    def complete(self, messages, temperature, max_tokens):
        self.seen = (messages, temperature, max_tokens)
        return '{"device": "чайник"}'


class FakeBackend:
    def stream(self, messages, gen):
        self.messages = messages
        yield from ["При", "вет"]


@pytest.fixture
def server():
    engine = FakeEngine()
    backend = FakeBackend()
    asked = []
    srv = PhoneServer(CODE, 0, lambda: engine, lambda: backend, host="127.0.0.1", on_request=asked.append)
    srv.start()
    srv.engine, srv.backend, srv.asked = engine, backend, asked
    yield srv
    srv.stop()


def call(srv, path, body=None, code=CODE):
    request = urllib.request.Request(f"http://127.0.0.1:{srv.port}{path}",
                                     data=json.dumps(body).encode() if body is not None else None,
                                     headers={"Content-Type": "application/json",
                                              **({"Authorization": f"Bearer {code}"} if code else {})})
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            return response.status, response.read().decode(), response.headers.get("Content-Type")
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read().decode(), exc.headers.get("Content-Type")


def test_health_is_open_the_rest_needs_the_code(server):
    status, body, _ = call(server, "/v1/health", code=None)
    assert status == 200 and json.loads(body)["ok"]
    status, body, _ = call(server, "/v1/models", code="WRONG-CODE")
    assert status == 401 and "код" in json.loads(body)["error"]["message"]
    status, body, _ = call(server, "/v1/models", code=CODE.lower())  # typed in small letters on the phone
    assert status == 200
    assert [m["id"] for m in json.loads(body)["data"]] == ["fluxion", "fluxion-vision"]


def test_photo_goes_to_the_vision_model(server):
    status, body, _ = call(server, "/v1/chat/completions",
                           {"model": "fluxion-vision", "messages": PHOTO, "temperature": 0.1, "max_tokens": 500})
    assert status == 200
    reply = json.loads(body)
    assert reply["choices"][0]["message"]["content"] == '{"device": "чайник"}'
    assert server.engine.seen == (PHOTO, 0.1, 500)
    assert server.asked == ["фото"]


def test_text_goes_to_the_chat_model_and_streams(server):
    question = {"messages": [{"role": "user", "content": "Привет"}]}
    status, body, _ = call(server, "/v1/chat/completions", question)
    assert status == 200 and json.loads(body)["choices"][0]["message"]["content"] == "Привет"
    status, body, kind = call(server, "/v1/chat/completions", {**question, "stream": True})
    assert status == 200 and kind.startswith("text/event-stream")
    events = [line[6:] for line in body.splitlines() if line.startswith("data: ")]
    assert events[-1] == "[DONE]"
    text = "".join(json.loads(e)["choices"][0]["delta"].get("content", "") for e in events[:-1])
    assert text == "Привет"


def test_no_vision_model_is_said_in_words():
    def missing():
        raise VisionUnavailable("На компьютере нет модели для фото")

    srv = PhoneServer(CODE, 0, missing, lambda: None, host="127.0.0.1")
    srv.start()
    try:
        status, body, _ = call(srv, "/v1/chat/completions", {"messages": PHOTO})
        assert status == 503 and json.loads(body)["error"]["message"] == "На компьютере нет модели для фото"
        status, body, _ = call(srv, "/v1/chat/completions", {"messages": [{"role": "user", "content": "a"}]})
        assert status == 503 and "модель" in json.loads(body)["error"]["message"]
        status, _, _ = call(srv, "/v1/chat/completions", {"nothing": 1})
        assert status == 400
    finally:
        srv.stop()


def test_messages_for_the_chat_model():
    assert has_image(PHOTO) and not has_image([{"role": "user", "content": "a"}])
    assert as_text(PHOTO + [{"role": "tool", "content": None}]) == [
        {"role": "user", "content": "что это?"}, {"role": "user", "content": ""}]


def test_config_keeps_a_code_and_fixes_a_bad_port(tmp_path, monkeypatch):
    monkeypatch.setenv("APPDATA", str(tmp_path))
    first = config.load()
    assert first["port"] == 8766 and not first["enabled"]
    assert len(first["code"]) == 9 and first["code"][4] == "-"
    assert not set(first["code"].replace("-", "")) & set("01OIL")
    assert config.load()["code"] == first["code"]  # saved on first load: the same after a restart
    config.save({**first, "port": 80, "enabled": True})
    again = config.load()
    assert again["port"] == 8766 and again["enabled"]


def test_vision_files_and_engine_choice(tmp_path, monkeypatch):
    cfg = config.normalize({})
    assert not vision.installed(cfg, tmp_path)
    (tmp_path / vision.MODEL_FILE).write_bytes(b"GGUF")
    (tmp_path / vision.MMPROJ_FILE).write_bytes(b"GGUF")
    assert vision.installed(cfg, tmp_path)
    own = config.normalize({"vision_model": str(tmp_path / "my.gguf"), "vision_mmproj": str(tmp_path / "mm.gguf")})
    assert vision.files(own, tmp_path) == (tmp_path / "my.gguf", tmp_path / "mm.gguf")

    monkeypatch.setattr(vision, "handler_class", lambda: object)
    monkeypatch.setattr(vision, "gpu_layers", lambda: 0)
    engine = vision.pick(cfg, "http://localhost:1", tmp_path)
    assert isinstance(engine, vision.EmbeddedVision)
    assert vision.pick(cfg, "http://localhost:1", tmp_path, embedded=engine) is engine

    monkeypatch.setattr(vision, "handler_class", lambda: None)
    monkeypatch.setattr(vision.OllamaVision, "available", lambda self: False)
    with pytest.raises(VisionUnavailable, match="0.3.9"):
        vision.pick(cfg, "http://localhost:1", tmp_path)
    with pytest.raises(VisionUnavailable, match="Скачать модель для фото"):
        vision.pick(cfg, "http://localhost:1", tmp_path / "empty")
    monkeypatch.setattr(vision.OllamaVision, "available", lambda self: True)
    assert isinstance(vision.pick(cfg, "http://localhost:1", tmp_path / "empty"), vision.OllamaVision)


def test_embedded_engine_unloads_when_idle(tmp_path):
    engine = vision.EmbeddedVision(tmp_path / "m.gguf", tmp_path / "mm.gguf")

    class Llm:
        closed = False

        def close(self):
            Llm.closed = True

    engine.llm = Llm()
    engine.last_used = 0.0
    assert not engine.unload_if_idle(10 ** 9) and engine.llm is not None
    assert engine.unload_if_idle(0) and engine.llm is None and Llm.closed


def test_dialog_shows_address_and_code(tmp_path, monkeypatch):
    import os

    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    pytest.importorskip("PySide6")
    from PySide6.QtWidgets import QApplication

    from desktop_browser.phone_dialog import PhoneDialog

    monkeypatch.setenv("APPDATA", str(tmp_path))
    monkeypatch.setattr(vision, "models_dir", lambda: tmp_path)
    QApplication.instance() or QApplication([])
    dialog = PhoneDialog()
    code = config.load()["code"]
    assert dialog.code.text() == code
    assert ":8766/v1" in dialog.address.text()
    assert "Скачайте" in dialog.vision_label.text() and dialog.download_button.isEnabled()
    assert dialog.link.startswith("fluxion://pair?code=" + code) and ":8766/v1" in dialog.link
    assert not dialog.qr.pixmap().isNull()
    dialog.enabled.setChecked(True)
    dialog._new_code()
    assert "code=" + dialog.code.text() in dialog.link  # the QR follows the new code
    dialog.save()
    saved = config.load()
    assert saved["enabled"] and saved["code"] != code
    dialog.deleteLater()


def test_pair_link_holds_code_and_every_address():
    from urllib.parse import parse_qs, urlsplit

    link = config.pair_link(["http://192.168.31.50:8766/v1", "http://172.17.0.1:8766/v1"], "K7M2-Q9XP")
    parts = urlsplit(link)
    assert (parts.scheme, parts.netloc) == ("fluxion", "pair")
    query = parse_qs(parts.query)
    assert query["code"] == ["K7M2-Q9XP"]
    assert query["url"] == ["http://192.168.31.50:8766/v1", "http://172.17.0.1:8766/v1"]


def test_lan_addresses_put_home_network_first_and_hide_vpn_tunnel(monkeypatch):
    # what a PC with a TUN-mode VPN, Hyper-V and Radmin reports: the probe lands on the tunnel
    class Probe:
        def __init__(self, *args): pass
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def connect(self, address): pass
        def getsockname(self): return ("198.18.0.1", 0)

    monkeypatch.setattr(phone_server.socket, "socket", Probe)
    monkeypatch.setattr(phone_server.socket, "gethostbyname_ex", lambda name: (
        name, [], ["172.17.0.1", "26.24.124.83", "192.168.31.50", "198.18.0.1", "169.254.60.195"]))
    assert lan_addresses() == ["192.168.31.50", "172.17.0.1", "26.24.124.83"]


def test_vision_warning_says_what_the_model_needs_and_checks_memory():
    import os

    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    pytest.importorskip("PySide6")
    from desktop_browser.phone_dialog import vision_requirements

    text, short = vision_requirements(ram_gb=32, vram_gb=12)
    assert "16" in text and "5,5" in text and not short
    assert "медленно" not in text and "Видеокарты не найдено" not in text
    text, short = vision_requirements(ram_gb=14, vram_gb=0)
    assert not short and "14 ГБ" in text and "медленно" in text and "Видеокарты не найдено" in text
    text, short = vision_requirements(ram_gb=8, vram_gb=0)
    assert short and "8 ГБ оперативной памяти" in text and "Скачать всё равно" in text
    assert vision_requirements(ram_gb=0, vram_gb=8)[1] is False  # memory unknown: no alarm


def test_total_ram_and_out_of_memory():
    assert vision.total_ram_gb() >= 0
    assert vision.out_of_memory(RuntimeError("ggml_backend: failed to allocate buffer"))
    assert vision.out_of_memory(MemoryError())
    assert not vision.out_of_memory(RuntimeError("model not found"))


def test_download_asks_first_and_cancel_downloads_nothing(tmp_path, monkeypatch):
    import os

    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    pytest.importorskip("PySide6")
    from PySide6.QtWidgets import QApplication

    import desktop_browser.phone_dialog as phone_dialog

    monkeypatch.setenv("APPDATA", str(tmp_path))
    monkeypatch.setattr(vision, "models_dir", lambda: tmp_path)
    QApplication.instance() or QApplication([])
    monkeypatch.setattr(phone_dialog, "confirm_vision_download", lambda parent=None: False)
    dialog = phone_dialog.PhoneDialog()
    dialog._download()
    assert dialog.worker is None and dialog.download_button.isEnabled()
    dialog.deleteLater()
