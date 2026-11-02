"""The settings of «Связь с телефоном»: %APPDATA%\\Fluxion\\phone.json — never sent anywhere.

    {"enabled": true, "port": 8766, "code": "K7M2-Q9XP",
     "vision_model": "", "vision_mmproj": "",     # own files instead of the catalog ones
     "ollama_model": "qwen2.5vl:7b"}              # used when the embedded model is not here
"""
from __future__ import annotations

import json
import os
import secrets
import threading
from pathlib import Path

DEFAULT_PORT = 8766
DEFAULT_OLLAMA_MODEL = "qwen2.5vl:7b"
# No 0/O, 1/I/L: the code is typed on a phone from the screen of the PC.
ALPHABET = "23456789ABCDEFGHJKMNPQRSTUVWXYZ"

_lock = threading.Lock()


def path() -> Path:
    base = os.environ.get("APPDATA") or str(Path.home() / ".config")
    return Path(base) / "Fluxion" / "phone.json"


def new_code() -> str:
    raw = "".join(secrets.choice(ALPHABET) for _ in range(8))
    return raw[:4] + "-" + raw[4:]


def normalize(data) -> dict:
    data = data if isinstance(data, dict) else {}
    try:
        port = int(data.get("port") or DEFAULT_PORT)
    except (TypeError, ValueError):
        port = DEFAULT_PORT
    if not 1024 <= port <= 65535:
        port = DEFAULT_PORT
    code = str(data.get("code") or "").strip().upper()
    return {
        "enabled": bool(data.get("enabled")),
        "port": port,
        "code": code if len(code) >= 6 else new_code(),
        "vision_model": str(data.get("vision_model") or "").strip(),
        "vision_mmproj": str(data.get("vision_mmproj") or "").strip(),
        "ollama_model": str(data.get("ollama_model") or DEFAULT_OLLAMA_MODEL).strip(),
    }


def load() -> dict:
    try:
        data = normalize(json.loads(path().read_text(encoding="utf-8")))
    except (OSError, ValueError):
        data = normalize({})
        try:
            save(data)  # the code shown in the window stays the same after a restart
        except OSError:
            pass
    return data


def save(data: dict) -> Path:
    with _lock:
        target = path()
        target.parent.mkdir(parents=True, exist_ok=True)
        tmp = target.with_name(target.name + ".tmp")
        tmp.write_text(json.dumps(normalize(data), ensure_ascii=False, indent=1), encoding="utf-8")
        os.replace(tmp, target)
        return target
