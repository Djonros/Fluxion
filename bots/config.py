"""The settings of the bots: %APPDATA%\\Fluxion\\bots.json — the user's own file, never sent anywhere.

    {"telegram": {"token": "...", "enabled": true, "proxy": "",
                  "allowed": [{"id": "123", "name": "Евгений", "notify": true, "chat": "123"}]},
     "max": {...}, "vk": {...},
     "folders": ["C:\\\\Проекты"]}      # the only files a bot may read, model and send

A new user is not answered until confirmed: the first message gets a six-digit code,
the owner types it in «Боты в мессенджерах» and the user is added to ``allowed``.
"""
from __future__ import annotations

import json
import os
import secrets
import threading
import time
from pathlib import Path

PLATFORMS = ("telegram", "max", "vk")
TITLES = {"telegram": "Telegram", "max": "MAX", "vk": "ВКонтакте"}
CODE_TTL = 15 * 60  # seconds a confirmation code is valid

_lock = threading.Lock()


def path() -> Path:
    base = os.environ.get("APPDATA") or str(Path.home() / ".config")
    return Path(base) / "Fluxion" / "bots.json"


def _platform(data) -> dict:
    data = data if isinstance(data, dict) else {}
    allowed = []
    for user in data.get("allowed") or []:
        if isinstance(user, dict) and str(user.get("id") or "").strip():
            item = {"id": str(user["id"]).strip(), "name": str(user.get("name") or "").strip()}
            if user.get("notify") and str(user.get("chat") or "").strip():
                item.update(notify=True, chat=str(user["chat"]).strip())
            allowed.append(item)
    return {"token": str(data.get("token") or "").strip(), "enabled": bool(data.get("enabled")),
            "proxy": str(data.get("proxy") or "").strip(), "allowed": allowed}


def normalize(data) -> dict:
    data = data if isinstance(data, dict) else {}
    out = {name: _platform(data.get(name)) for name in PLATFORMS}
    out["folders"] = [str(f) for f in data.get("folders") or [] if str(f).strip()]
    return out


def load() -> dict:
    try:
        return normalize(json.loads(path().read_text(encoding="utf-8")))
    except (OSError, ValueError):
        return normalize({})


def save(data: dict) -> Path:
    with _lock:
        target = path()
        target.parent.mkdir(parents=True, exist_ok=True)
        tmp = target.with_name(target.name + ".tmp")
        tmp.write_text(json.dumps(normalize(data), ensure_ascii=False, indent=1), encoding="utf-8")
        os.replace(tmp, target)
        return target


def is_allowed(data: dict, platform: str, user_id: str) -> bool:
    return any(u["id"] == str(user_id) for u in data[platform]["allowed"])


# ── confirmation codes: kept in memory only, while Fluxion runs ──────────────

_pending: dict[str, dict] = {}  # code -> {"platform", "id", "name", "time"}


def new_code(platform: str, user_id: str, name: str) -> str:
    """The code a new user is told; the same user asking again gets the same code."""
    with _lock:
        now = time.time()
        for code, p in list(_pending.items()):
            if now - p["time"] > CODE_TTL:
                del _pending[code]
            elif p["platform"] == platform and p["id"] == str(user_id):
                return code
        code = f"{secrets.randbelow(10 ** 6):06d}"
        _pending[code] = {"platform": platform, "id": str(user_id), "name": name, "time": now}
        return code


def pending() -> list[dict]:
    with _lock:
        now = time.time()
        return [dict(p, code=c) for c, p in _pending.items() if now - p["time"] <= CODE_TTL]


def confirm(code: str) -> dict | None:
    """The owner typed *code*: the user is allowed from now on; the user, or None for a wrong code."""
    code = "".join(ch for ch in str(code) if ch.isdigit())
    with _lock:
        p = _pending.pop(code, None)
    if p is None or time.time() - p["time"] > CODE_TTL:
        return None
    data = load()
    users = data[p["platform"]]["allowed"]
    if not any(u["id"] == p["id"] for u in users):
        users.append({"id": p["id"], "name": p["name"]})
    save(data)
    return p


def allowed_file(file: str | Path, folders: list[str]) -> bool:
    """A bot reads and sends only what lies in the allowed folders."""
    try:
        target = Path(file).resolve()
    except OSError:
        return False
    for folder in folders:
        try:
            target.relative_to(Path(folder).resolve())
            return True
        except (ValueError, OSError):
            continue
    return False
