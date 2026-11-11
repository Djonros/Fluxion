"""The settings of «Личный помощник»: %APPDATA%\\Fluxion\\assistant.json — never sent anywhere.

    {"enabled": false,                 # the assistant as a whole
     "paused_until": 0,                # time.time() until which nothing is recorded
     "retention_days": 30,             # older journal lines are deleted
     "notify_tray": true, "notify_bots": false,
     "connectors": {"folders": {"enabled": true, "cloud": false, "settings": {...}}},
     "rules": [{"connector": "folders", "kind": "file.created", "mask": "*.a3d",
                "action": "notify"}]}

``cloud`` — whether the events of a connector may be given to a cloud model; off by default.
"""
from __future__ import annotations

import json
import os
import threading
import time
from pathlib import Path

RETENTION_DEFAULT = 30
RETENTION_LIMITS = (1, 365)
ACTIONS = ("notify", "bots")

_lock = threading.Lock()


def path() -> Path:
    base = os.environ.get("APPDATA") or str(Path.home() / ".config")
    return Path(base) / "Fluxion" / "assistant.json"


def _connector(data) -> dict:
    data = data if isinstance(data, dict) else {}
    settings = data.get("settings")
    return {"enabled": bool(data.get("enabled")), "cloud": bool(data.get("cloud")),
            "settings": settings if isinstance(settings, dict) else {}}


def _rule(data) -> dict | None:
    if not isinstance(data, dict) or not str(data.get("connector") or "").strip():
        return None
    action = str(data.get("action") or "notify")
    return {"connector": str(data["connector"]).strip(), "kind": str(data.get("kind") or "").strip(),
            "mask": str(data.get("mask") or "").strip(), "action": action if action in ACTIONS else "notify",
            "enabled": data.get("enabled", True) is not False}


def normalize(data) -> dict:
    data = data if isinstance(data, dict) else {}
    try:
        days = int(data.get("retention_days", RETENTION_DEFAULT))
    except (TypeError, ValueError):
        days = RETENTION_DEFAULT
    try:
        paused = float(data.get("paused_until") or 0)
    except (TypeError, ValueError):
        paused = 0.0
    connectors = data.get("connectors") if isinstance(data.get("connectors"), dict) else {}
    return {
        "enabled": bool(data.get("enabled")),
        "paused_until": paused,
        "retention_days": min(max(days, RETENTION_LIMITS[0]), RETENTION_LIMITS[1]),
        "notify_tray": data.get("notify_tray", True) is not False,
        "notify_bots": bool(data.get("notify_bots")),
        "connectors": {str(k): _connector(v) for k, v in connectors.items()},
        "rules": [r for r in (_rule(x) for x in data.get("rules") or []) if r],
    }


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


def connector(data: dict, cid: str) -> dict:
    """The settings of one connector (a default, off one when it was never set up)."""
    return data["connectors"].get(cid) or _connector({})


def paused(data: dict, now: float | None = None) -> bool:
    return data["paused_until"] > (time.time() if now is None else now)
