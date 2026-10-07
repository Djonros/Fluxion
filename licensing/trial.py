"""Pro trial: a few file edits by the agent without a licence.

The state lives in a small JSON file ``{"device": ..., "remaining": ...}``.
The first consumed edit binds the trial to this machine; a state file carried
over from another machine counts as spent.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

from .fingerprint import device_code

TRIAL_WRITES = 3


def trial_path() -> Path:
    """Return the state file: %APPDATA%/Fluxion when frozen, ``data/`` from sources."""
    if getattr(sys, "frozen", False):
        base = os.environ.get("APPDATA") or os.path.expanduser("~")
        return Path(base) / "Fluxion" / "trial.json"
    return Path(__file__).resolve().parents[1] / "data" / "trial.json"


def _load() -> tuple[str | None, int]:
    """Return ``(device, remaining)``; an unreadable state counts as spent."""
    path = trial_path()
    if not path.is_file():
        return None, TRIAL_WRITES
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        device = data.get("device")
        remaining = int(data.get("remaining", 0))
    except (OSError, ValueError, TypeError, AttributeError):
        return None, 0
    return (str(device) if device else None), max(0, min(remaining, TRIAL_WRITES))


def trial_remaining() -> int:
    """Return how many trial edits are left on this machine."""
    device, remaining = _load()
    if device is not None and device != device_code():
        return 0
    return remaining


def trial_consume() -> bool:
    """Spend one trial edit; return False when none is left or it cannot be saved."""
    remaining = trial_remaining()
    if remaining <= 0:
        return False
    path = trial_path()
    state = {"device": device_code(), "remaining": remaining - 1}
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(state), encoding="utf-8")
    except OSError:
        return False
    return True


def trial_reset() -> None:
    """Restore the full trial by removing the state file."""
    try:
        trial_path().unlink()
    except FileNotFoundError:
        pass
