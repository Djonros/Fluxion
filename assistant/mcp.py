"""A client of MCP servers over stdio (Model Context Protocol), the standard library only.

A server is a program Fluxion starts (``npx -y @modelcontextprotocol/server-filesystem C:\\Проекты``,
``uvx mcp-server-git``, an .exe of a vendor); they talk JSON-RPC 2.0, one message per line,
on its stdin and stdout. Fluxion asks ``initialize``, ``tools/list`` and ``tools/call`` —
the tools of the server become the tools of «Личный помощник».

The server runs on this computer with the user's rights: only the servers the user added
start, and a tool that may change something runs after the user's «Да» (``apps``).
"""
from __future__ import annotations

import json
import os
import queue
import shutil
import subprocess
import sys
import threading
from dataclasses import dataclass, field

PROTOCOL = "2025-06-18"
START_TIMEOUT = 60.0     # npx downloads the server the first time
CALL_TIMEOUT = 120.0
OUTPUT_LIMIT = 12000     # characters of a tool result given to the model


class McpError(RuntimeError):
    """Said to the user as it is."""


@dataclass
class McpTool:
    name: str
    description: str = ""
    schema: dict = field(default_factory=lambda: {"type": "object", "properties": {}})
    read_only: bool = False   # the server says the tool changes nothing (annotations.readOnlyHint)


def _no_window() -> dict:
    if sys.platform == "win32":
        return {"creationflags": getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)}
    return {}


def resolve(command: str) -> str:
    """``npx`` → the full path of npx.cmd on Windows; the command as it is when not found."""
    return shutil.which(command) or command


def result_text(result: dict) -> str:
    """The content of a tools/call result as text for the model."""
    parts = []
    for item in result.get("content") or []:
        kind = item.get("type")
        if kind == "text":
            parts.append(str(item.get("text", "")))
        elif kind == "resource":
            res = item.get("resource") or {}
            parts.append(str(res.get("text") or res.get("uri") or ""))
        elif kind in ("image", "audio"):
            parts.append(f"[{kind}: {item.get('mimeType', '')}]")
        elif kind == "resource_link":
            parts.append(f"[{item.get('name') or ''} {item.get('uri') or ''}]".strip())
    if not parts and result.get("structuredContent") is not None:
        parts.append(json.dumps(result["structuredContent"], ensure_ascii=False))
    text = "\n".join(p for p in parts if p).strip()
    if len(text) > OUTPUT_LIMIT:
        text = text[:OUTPUT_LIMIT] + f"\n… (обрезано, всего {len(text)} символов)"
    return text


class McpServer:
    def __init__(self, command: str, args: list[str] | None = None, env: dict | None = None,
                 cwd: str | None = None, name: str = ""):
        self.command = command
        self.args = [str(a) for a in args or []]
        self.env = {str(k): str(v) for k, v in (env or {}).items()}
        self.cwd = cwd or None
        self.name = name or command
        self.info: dict = {}
        self._proc: subprocess.Popen | None = None
        self._next = 0
        self._lock = threading.Lock()
        self._waiting: dict[int, queue.Queue] = {}
        self._stderr: list[str] = []

    # ── the process ──
    def start(self, timeout: float = START_TIMEOUT) -> dict:
        env = dict(os.environ, **self.env)
        try:
            self._proc = subprocess.Popen(
                [resolve(self.command), *self.args], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                stderr=subprocess.PIPE, cwd=self.cwd, env=env, **_no_window())
        except OSError as exc:
            raise McpError(f"не запускается «{self.command}»: {exc}") from exc
        threading.Thread(target=self._read, daemon=True, name="fluxion-mcp-out").start()
        threading.Thread(target=self._read_err, daemon=True, name="fluxion-mcp-err").start()
        result = self.request("initialize", {
            "protocolVersion": PROTOCOL, "capabilities": {},
            "clientInfo": {"name": "Fluxion", "version": _version()}}, timeout)
        self.info = result.get("serverInfo") or {}
        self.notify("notifications/initialized")
        return result

    def stop(self) -> None:
        proc, self._proc = self._proc, None
        if proc is None:
            return
        try:
            proc.stdin.close()
        except OSError:
            pass
        try:
            proc.wait(3)
        except subprocess.TimeoutExpired:
            proc.kill()
        for q in list(self._waiting.values()):
            q.put({"error": {"message": "сервер остановлен"}})

    def alive(self) -> bool:
        return self._proc is not None and self._proc.poll() is None

    def __enter__(self):
        self.start()
        return self

    def __exit__(self, *exc):
        self.stop()

    # ── JSON-RPC ──
    def _send(self, message: dict) -> None:
        if not self.alive():
            raise McpError(self._died())
        data = (json.dumps(message, ensure_ascii=False) + "\n").encode("utf-8")
        with self._lock:
            try:
                self._proc.stdin.write(data)
                self._proc.stdin.flush()
            except OSError as exc:
                raise McpError(self._died() or str(exc)) from exc

    def notify(self, method: str, params: dict | None = None) -> None:
        self._send({"jsonrpc": "2.0", "method": method, **({"params": params} if params else {})})

    def request(self, method: str, params: dict | None = None, timeout: float = CALL_TIMEOUT) -> dict:
        with self._lock:
            self._next += 1
            rid = self._next
        box: queue.Queue = queue.Queue()
        self._waiting[rid] = box
        try:
            self._send({"jsonrpc": "2.0", "id": rid, "method": method, "params": params or {}})
            try:
                reply = box.get(timeout=timeout)
            except queue.Empty:
                raise McpError(f"сервер «{self.name}» не ответил за {timeout:.0f} с на {method}") from None
        finally:
            self._waiting.pop(rid, None)
        if "error" in reply:
            err = reply["error"] or {}
            raise McpError(str(err.get("message") or err))
        return reply.get("result") or {}

    def _read(self) -> None:
        proc = self._proc
        for raw in iter(proc.stdout.readline, b""):
            try:
                msg = json.loads(raw.decode("utf-8", "replace"))
            except ValueError:
                continue  # a server printing its own log to stdout
            if not isinstance(msg, dict):
                continue
            if "id" in msg and ("result" in msg or "error" in msg):
                box = self._waiting.get(msg["id"])
                if box is not None:
                    box.put(msg)
            elif "id" in msg and "method" in msg:  # the server asks us: ping, roots — answer simply
                answer = {"jsonrpc": "2.0", "id": msg["id"]}
                if msg["method"] == "ping":
                    answer["result"] = {}
                elif msg["method"] == "roots/list":
                    answer["result"] = {"roots": []}
                else:
                    answer["error"] = {"code": -32601, "message": "Fluxion этого не умеет"}
                try:
                    self._send(answer)
                except McpError:
                    return
        for box in list(self._waiting.values()):
            box.put({"error": {"message": self._died()}})

    def _read_err(self) -> None:
        proc = self._proc
        for raw in iter(proc.stderr.readline, b""):
            self._stderr.append(raw.decode("utf-8", "replace").rstrip())
            del self._stderr[:-20]

    def _died(self) -> str:
        tail = "; ".join(line for line in self._stderr[-3:] if line)
        return f"сервер «{self.name}» закрылся" + (f": {tail}" if tail else "")

    # ── what it offers ──
    def tools(self) -> list[McpTool]:
        out, cursor = [], None
        while True:
            result = self.request("tools/list", {"cursor": cursor} if cursor else {})
            for t in result.get("tools") or []:
                if not isinstance(t, dict) or not t.get("name"):
                    continue
                notes = t.get("annotations") or {}
                out.append(McpTool(str(t["name"]), str(t.get("description") or ""),
                                   t.get("inputSchema") or {"type": "object", "properties": {}},
                                   bool(notes.get("readOnlyHint"))))
            cursor = result.get("nextCursor")
            if not cursor:
                return out

    def call(self, tool: str, arguments: dict | None = None, timeout: float = CALL_TIMEOUT) -> str:
        result = self.request("tools/call", {"name": tool, "arguments": arguments or {}}, timeout)
        text = result_text(result)
        if result.get("isError"):
            raise McpError(text or f"инструмент {tool} вернул ошибку")
        return text or "(пустой ответ)"


def _version() -> str:
    try:
        from desktop_browser import APP_VERSION

        return APP_VERSION
    except Exception:
        return "0"
