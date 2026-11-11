"""«Приложения» of «Личный помощник»: any program becomes tools of the assistant.

    {"id": "files", "name": "Файлы проекта", "kind": "mcp", "enabled": true, "cloud": false,
     "command": "npx", "args": ["-y", "@modelcontextprotocol/server-filesystem", "C:\\\\Проекты"],
     "env": {}, "tools": {"write_file": "ask", "read_file": "auto", "move_file": "off"}}
    {"id": "find", "name": "Поиск в 1С", "kind": "cli", "command": "C:\\\\bin\\\\find1c.exe --text {input}",
     "description": "ищет номенклатуру в 1С по названию"}
    {"id": "crm", "name": "CRM", "kind": "http", "url": "https://crm.local/api/find?q={input}",
     "method": "GET", "header": "Authorization", "description": "клиенты и заказы"}

``mcp`` — an MCP server (``mcp.McpServer``): its own tools, as many as it has. ``cli`` — a
program with a command line: one tool, ``{input}`` is replaced by what the assistant asks.
``http`` — a web API: one tool, ``{input}`` in the address (GET) or ``{"input": …}`` (POST);
the key of the header is kept in the Credential Manager (``secrets``), not in json.

Every tool has a permission: ``auto`` — runs at once; ``ask`` — after the user's «Да»;
``off`` — the assistant does not see it. By default a tool runs only after «Да», except
the tools an MCP server marks as read-only.
"""
from __future__ import annotations

import json
import re
import shlex
import subprocess
import threading
import urllib.parse
from collections.abc import Callable
from dataclasses import dataclass, field

from . import secrets
from .mcp import OUTPUT_LIMIT, McpError, McpServer, _no_window, child_env

KINDS = ("mcp", "cli", "http")
POLICIES = ("auto", "ask", "off")
CLI_TIMEOUT = 120
HTTP_TIMEOUT = 60

PRESETS = [  # «Добавить приложение» offers these; they need Node.js (npx) or uv (uvx)
    {"name": "Файлы в папке", "kind": "mcp", "command": "npx",
     "args": ["-y", "@modelcontextprotocol/server-filesystem", "{folder}"],
     "about": "читать, искать и записывать файлы в выбранной папке"},
    {"name": "Git-репозиторий", "kind": "mcp", "command": "uvx", "args": ["mcp-server-git", "--repository", "{folder}"],
     "about": "история, изменения и коммиты репозитория"},
    {"name": "База SQLite", "kind": "mcp", "command": "uvx", "args": ["mcp-server-sqlite", "--db-path", "{file}"],
     "about": "запросы к базе SQLite"},
    {"name": "Память (заметки)", "kind": "mcp", "command": "npx", "args": ["-y", "@modelcontextprotocol/server-memory"],
     "about": "Fluxion запоминает факты о людях, проектах, договорённостях"},
    {"name": "Свой MCP-сервер", "kind": "mcp", "command": "", "args": [], "about": "команда запуска сервера"},
    {"name": "Программа с командной строкой", "kind": "cli", "command": "", "about": "команда с {input}"},
    {"name": "Веб-API", "kind": "http", "url": "", "about": "адрес с {input}"},
]


class AppError(RuntimeError):
    """Said to the user as it is."""


@dataclass
class AppTool:
    app: str            # the id of the app
    app_name: str
    name: str           # the tool's own name in the app
    description: str
    run: Callable[[dict], str]
    schema: dict = field(default_factory=lambda: {"type": "object", "properties": {}})
    policy: str = "ask"
    cloud: bool = False

    @property
    def full_name(self) -> str:
        return f"{self.app}__{self.name}"


def slug(text: str, taken: set[str] = frozenset()) -> str:
    base = re.sub(r"[^a-z0-9]+", "_", text.lower()).strip("_") or "app"
    out, n = base, 2
    while out in taken:
        out, n = f"{base}{n}", n + 1
    return out


def normalize_app(data) -> dict | None:
    if not isinstance(data, dict) or str(data.get("kind")) not in KINDS:
        return None
    tools = data.get("tools") if isinstance(data.get("tools"), dict) else {}
    out = {
        "id": str(data.get("id") or "").strip() or slug(str(data.get("name") or "")),
        "name": str(data.get("name") or "").strip() or "Приложение",
        "kind": str(data["kind"]),
        "enabled": data.get("enabled", True) is not False,
        "cloud": bool(data.get("cloud")),
        "command": str(data.get("command") or "").strip(),
        "args": [str(a) for a in data.get("args") or []],
        "env": {str(k): str(v) for k, v in (data.get("env") or {}).items()} if isinstance(data.get("env"), dict) else {},
        "url": str(data.get("url") or "").strip(),
        "method": "POST" if str(data.get("method")).upper() == "POST" else "GET",
        "header": str(data.get("header") or "").strip(),
        "description": str(data.get("description") or "").strip(),
        "tools": {str(k): v for k, v in tools.items() if v in POLICIES},
    }
    return out


def secret_name(app: dict) -> str:
    return f"app:{app['id']}"


# ── a program with a command line ──

def cli_argv(template: str, value: str) -> list[str]:
    """The command line of *template* with ``{input}`` → *value*, as a list (no shell)."""
    parts = shlex.split(template, posix=False)
    argv = []
    for part in parts:
        if len(part) >= 2 and part[0] == part[-1] == '"':
            part = part[1:-1]
        argv.append(part.replace("{input}", value))
    if not argv:
        raise AppError("команда не задана")
    return argv


def _decode(data: bytes) -> str:
    for enc in ("utf-8", "cp866", "cp1251"):
        try:
            return data.decode(enc)
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", "replace")


def run_cli(template: str, value: str, timeout: int = CLI_TIMEOUT) -> str:
    argv = cli_argv(template, value)
    from .mcp import resolve

    argv[0] = resolve(argv[0])
    try:
        done = subprocess.run(argv, capture_output=True, timeout=timeout, env=child_env(), **_no_window())
    except FileNotFoundError as exc:
        raise AppError(f"нет программы «{argv[0]}»") from exc
    except subprocess.TimeoutExpired as exc:
        raise AppError(f"программа не закончила за {timeout} с") from exc
    text = (_decode(done.stdout) + ("\n" + _decode(done.stderr) if done.stderr.strip() else "")).strip()
    if done.returncode:
        text = f"код выхода {done.returncode}\n{text}"
    return text[:OUTPUT_LIMIT] or "(программа ничего не вывела)"


# ── a web API ──

def run_http(app: dict, value: str, client=None) -> str:
    import httpx

    url = app["url"]
    if not url.lower().startswith(("http://", "https://")):
        raise AppError("адрес должен начинаться с http:// или https://")
    headers = {}
    key = secrets.get_secret(secret_name(app)) if app["header"] else ""
    if key:
        if not key.isascii():
            raise AppError("ключ должен быть латиницей и цифрами: проверьте, что он вставлен целиком")
        headers[app["header"]] = key
    own = client is None
    client = client or httpx.Client(timeout=HTTP_TIMEOUT)
    try:
        if app["method"] == "POST":
            resp = client.post(url.replace("{input}", urllib.parse.quote(value)), json={"input": value},
                               headers=headers)
        else:
            resp = client.get(url.replace("{input}", urllib.parse.quote(value)), headers=headers)
    except httpx.HTTPError as exc:
        raise AppError(f"адрес не отвечает: {exc}") from exc
    finally:
        if own:
            client.close()
    text = resp.text
    if resp.status_code >= 400:
        raise AppError(f"ответ {resp.status_code}: {text[:500]}")
    try:
        text = json.dumps(resp.json(), ensure_ascii=False, indent=1)
    except ValueError:
        pass
    return text[:OUTPUT_LIMIT]


INPUT_SCHEMA = {"type": "object", "properties": {"input": {"type": "string", "description": "что передать"}},
                "required": ["input"]}


class Apps:
    """The enabled apps of the settings; MCP servers start when their tools are first asked."""

    def __init__(self, apps: list[dict]):
        self.apps = [a for a in (normalize_app(x) for x in apps) if a and a["enabled"]]
        self.servers: dict[str, McpServer] = {}
        self.errors: dict[str, str] = {}
        self._lock = threading.Lock()

    def server(self, app: dict) -> McpServer:
        with self._lock:
            srv = self.servers.get(app["id"])
            if srv is not None and srv.alive():
                return srv
            if not app["command"]:
                raise AppError("не задана команда запуска сервера")
            srv = McpServer(app["command"], app["args"], app["env"], name=app["name"])
            srv.start()
            self.servers[app["id"]] = srv
            return srv

    def tools(self) -> list[AppTool]:
        out = []
        for app in self.apps:
            try:
                out.extend(t for t in self.app_tools(app) if t.policy != "off")
                self.errors.pop(app["id"], None)
            except (AppError, McpError) as exc:
                self.errors[app["id"]] = str(exc)
        return out

    def app_tools(self, app: dict) -> list[AppTool]:
        """All tools of one app with their permissions ("off" too — for the window)."""
        policy = app["tools"]
        if app["kind"] == "mcp":
            srv = self.server(app)
            return [AppTool(app["id"], app["name"], t.name, t.description,
                            (lambda args, n=t.name, s=srv: s.call(n, args)), t.schema,
                            policy.get(t.name) or ("auto" if t.read_only else "ask"), app["cloud"])
                    for t in srv.tools()]
        if app["kind"] == "cli":
            if not app["command"]:
                raise AppError("не задана команда")
            return [AppTool(app["id"], app["name"], "run", app["description"] or f"запустить {app['name']}",
                            lambda args, a=app: run_cli(a["command"], str(args.get("input", ""))),
                            INPUT_SCHEMA, policy.get("run") or "ask", app["cloud"])]
        return [AppTool(app["id"], app["name"], "request", app["description"] or f"спросить {app['name']}",
                        lambda args, a=app: run_http(a, str(args.get("input", ""))),
                        INPUT_SCHEMA, policy.get("request") or "ask", app["cloud"])]

    def stop(self) -> None:
        with self._lock:
            servers, self.servers = self.servers, {}
        for srv in servers.values():
            srv.stop()


def check(app: dict) -> tuple[list[AppTool], str]:
    """«Проверить» of the window: the tools of *app*, or the error in words."""
    apps = Apps([dict(app, enabled=True)])
    try:
        return apps.app_tools(apps.apps[0]), ""
    except (AppError, McpError) as exc:
        return [], str(exc)
    except Exception as exc:
        return [], str(exc) or type(exc).__name__
    finally:
        apps.stop()
