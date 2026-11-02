"""The HTTP server of «Связь с телефоном»: an OpenAI-compatible API on the home network.

    GET  /v1/health            {"ok": true, "name": "Fluxion", "vision": "..."}   (no code needed)
    GET  /v1/models            the models a phone can name: "fluxion" (chat), "fluxion-vision" (photos)
    POST /v1/chat/completions  OpenAI format; a message with an image_url goes to the vision model,
                               any other to the chat model of the window (``stream: true`` → SSE)

Every call but health needs ``Authorization: Bearer <code>``. The server is plain HTTP for
the home network: it is off until the user turns it on, and a wrong code gets nothing.
Standard library only: nothing to install, and it works in the frozen build.
"""
from __future__ import annotations

import hmac
import json
import logging
import socket
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from .vision import VisionUnavailable

logger = logging.getLogger(__name__)

MAX_BODY = 25 * 1024 * 1024  # a phone photo in base64 is 1–2 MB
CHAT_MODEL = "fluxion"
VISION_MODEL = "fluxion-vision"


def has_image(messages: list) -> bool:
    for message in messages:
        content = message.get("content") if isinstance(message, dict) else None
        if isinstance(content, list) and any(isinstance(p, dict) and p.get("type") == "image_url" for p in content):
            return True
    return False


def as_text(messages: list) -> list[dict]:
    """Messages for the chat model: text parts joined, pictures left out."""
    out = []
    for message in messages:
        if not isinstance(message, dict):
            continue
        content = message.get("content")
        if isinstance(content, list):
            content = "\n".join(str(p.get("text") or "") for p in content if isinstance(p, dict) and p.get("type") == "text")
        role = message.get("role") if message.get("role") in ("system", "user", "assistant") else "user"
        out.append({"role": role, "content": str(content or "")})
    return out


def lan_addresses() -> list[str]:
    """IPv4 addresses of this PC a phone on the same network can reach."""
    found: list[str] = []
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
            probe.connect(("192.168.0.1", 9))  # no packet is sent: it only picks the outgoing interface
            found.append(probe.getsockname()[0])
    except OSError:
        pass
    try:
        for address in socket.gethostbyname_ex(socket.gethostname())[2]:
            if address not in found:
                found.append(address)
    except OSError:
        pass
    return [a for a in found if not a.startswith("127.")] or ["127.0.0.1"]


class PhoneServer:
    """``vision`` picks the vision engine (may raise VisionUnavailable); ``chat_backend`` returns
    the window's model backend or None; ``on_request`` tells the window what a phone asked."""

    def __init__(self, code: str, port: int, vision, chat_backend, host: str = "0.0.0.0", on_request=None):
        self.code = code
        self.port = port
        self.host = host
        self.vision = vision
        self.chat_backend = chat_backend
        self.on_request = on_request or (lambda text: None)
        self.httpd: ThreadingHTTPServer | None = None
        self.thread: threading.Thread | None = None
        self.vision_lock = threading.Lock()  # one photo at a time: the model is big

    # ── life ──────────────────────────────────────────────────────────────────

    def start(self) -> int:
        """Listen; the port really taken (a test passes 0). Raises OSError when the port is busy."""
        server = self

        class Handler(_Handler):
            owner = server

        self.httpd = ThreadingHTTPServer((self.host, self.port), Handler)
        self.httpd.daemon_threads = True
        self.port = self.httpd.server_address[1]
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True, name="fluxion-phone")
        self.thread.start()
        logger.info("Phone link listening on %s:%d", self.host, self.port)
        return self.port

    def stop(self) -> None:
        if self.httpd is not None:
            self.httpd.shutdown()
            self.httpd.server_close()
            self.httpd = None

    @property
    def running(self) -> bool:
        return self.httpd is not None

    def authorized(self, header: str | None) -> bool:
        if not header or not header.startswith("Bearer "):
            return False
        given = header[len("Bearer "):].strip().upper()
        return hmac.compare_digest(given.encode(), self.code.strip().upper().encode())

    # ── answers ───────────────────────────────────────────────────────────────

    def answer_vision(self, messages: list, temperature: float, max_tokens: int) -> tuple[str, str]:
        with self.vision_lock:
            engine = self.vision()
            return engine.complete(messages, temperature, max_tokens), engine.label

    def chat_stream(self, messages: list, temperature: float, max_tokens: int):
        backend = self.chat_backend()
        if backend is None:
            raise VisionUnavailable("В Fluxion на компьютере не выбрана модель для чата")
        from core.config import GenerationSettings

        gen = GenerationSettings(temperature=temperature, max_tokens=max_tokens)
        return backend.stream(as_text(messages), gen)


def _completion(content: str, model: str) -> dict:
    return {
        "id": "chatcmpl-" + uuid.uuid4().hex[:24], "object": "chat.completion", "created": int(time.time()),
        "model": model,
        "choices": [{"index": 0, "message": {"role": "assistant", "content": content}, "finish_reason": "stop"}],
    }


def _chunk(cid: str, model: str, delta: dict, finish: str | None = None) -> bytes:
    data = {"id": cid, "object": "chat.completion.chunk", "created": int(time.time()), "model": model,
            "choices": [{"index": 0, "delta": delta, "finish_reason": finish}]}
    return b"data: " + json.dumps(data, ensure_ascii=False).encode() + b"\n\n"


class _Handler(BaseHTTPRequestHandler):
    owner: PhoneServer
    protocol_version = "HTTP/1.1"
    server_version = "Fluxion"

    def log_message(self, fmt, *args):  # quiet: no console in the frozen build
        logger.debug("phone: " + fmt, *args)

    def _json(self, status: int, payload: dict) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _error(self, status: int, message: str) -> None:
        self._json(status, {"error": {"message": message, "type": "fluxion_error"}})

    def do_GET(self):
        path = self.path.split("?")[0].rstrip("/")
        if path == "/v1/health":
            return self._json(200, {"ok": True, "name": "Fluxion"})
        if not self.owner.authorized(self.headers.get("Authorization")):
            return self._error(401, "Неверный код сопряжения")
        if path == "/v1/models":
            return self._json(200, {"object": "list", "data": [
                {"id": CHAT_MODEL, "object": "model", "owned_by": "fluxion"},
                {"id": VISION_MODEL, "object": "model", "owned_by": "fluxion"},
            ]})
        self._error(404, "Нет такого адреса")

    def do_POST(self):
        path = self.path.split("?")[0].rstrip("/")
        if not self.owner.authorized(self.headers.get("Authorization")):
            return self._error(401, "Неверный код сопряжения")
        if path != "/v1/chat/completions":
            return self._error(404, "Нет такого адреса")
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            length = 0
        if length <= 0 or length > MAX_BODY:
            return self._error(413 if length > MAX_BODY else 400, "Слишком большой или пустой запрос")
        try:
            request = json.loads(self.rfile.read(length))
            messages = request["messages"]
            if not isinstance(messages, list) or not messages:
                raise ValueError
        except (ValueError, KeyError, TypeError):
            return self._error(400, "Запрос не в формате OpenAI chat/completions")
        temperature = float(request.get("temperature") or 0.2)
        max_tokens = int(request.get("max_tokens") or 2048)
        try:
            if has_image(messages):
                self.owner.on_request("фото")
                content, label = self.owner.answer_vision(messages, temperature, max_tokens)
                return self._json(200, _completion(content, label))
            self.owner.on_request("чат")
            tokens = self.owner.chat_stream(messages, temperature, max_tokens)
            if request.get("stream"):
                return self._stream(tokens)
            return self._json(200, _completion("".join(tokens), CHAT_MODEL))
        except VisionUnavailable as exc:
            return self._error(503, str(exc))
        except Exception as exc:  # the phone shows the message; the server keeps running
            logger.exception("phone request failed")
            return self._error(500, f"Ошибка на компьютере: {exc}")

    def _stream(self, tokens) -> None:
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "close")
        self.end_headers()
        self.close_connection = True
        cid = "chatcmpl-" + uuid.uuid4().hex[:24]
        try:
            self.wfile.write(_chunk(cid, CHAT_MODEL, {"role": "assistant"}))
            for token in tokens:
                if token:
                    self.wfile.write(_chunk(cid, CHAT_MODEL, {"content": token}))
                    self.wfile.flush()
            self.wfile.write(_chunk(cid, CHAT_MODEL, {}, "stop"))
            self.wfile.write(b"data: [DONE]\n\n")
        except (BrokenPipeError, ConnectionResetError):
            close = getattr(tokens, "close", None)
            if callable(close):
                close()  # the phone stopped: stop generating


class Link:
    """What the window holds: the server and the vision engine kept between photos."""

    def __init__(self, cfg: dict, ollama_host: str, chat_backend, on_request=None):
        from . import vision

        self.cfg = cfg
        self.ollama_host = ollama_host
        self.engine = None
        self._vision = vision
        self.server = PhoneServer(cfg["code"], cfg["port"], self._pick, chat_backend, on_request=on_request)
        self._stop = threading.Event()
        self._sweeper = threading.Thread(target=self._sweep, daemon=True, name="fluxion-phone-idle")

    def _pick(self):
        self.engine = self._vision.pick(self.cfg, self.ollama_host, embedded=self.engine)
        return self.engine

    def _sweep(self):
        while not self._stop.wait(60):
            engine = self.engine
            if engine is not None:
                try:
                    engine.unload_if_idle()
                except Exception:
                    logger.exception("vision unload failed")

    def start(self) -> int:
        port = self.server.start()
        self._sweeper.start()
        return port

    def stop(self) -> None:
        self._stop.set()
        self.server.stop()
        engine, self.engine = self.engine, None
        if engine is not None:
            try:
                engine.unload_if_idle(0)
            except Exception:
                pass
