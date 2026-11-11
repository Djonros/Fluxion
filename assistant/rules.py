"""Rules of «Личный помощник»: an event that matches → a notice, without a model.

    {"connector": "folders", "kind": "file.created", "mask": "*.a3d", "action": "notify"}

``kind`` "" — any event of the connector; ``mask`` "" — any file or program; the mask is
matched against the name of the file (or the program) and the title of the event.
``action``: "notify" — a notice in the tray; "bots" — a message to the bots too.
"""
from __future__ import annotations

import fnmatch
import os

from .connector import Event


def matches(rule: dict, event: Event) -> bool:
    if not rule.get("enabled", True) or rule.get("connector") != event.connector:
        return False
    if rule.get("kind") and rule["kind"] != event.kind:
        return False
    masks = [m.strip().lower() for m in str(rule.get("mask") or "").replace(",", ";").split(";") if m.strip()]
    if not masks:
        return True
    names = (os.path.basename(event.ref).lower(), event.title.lower())
    return any(fnmatch.fnmatch(name, m) or (not any(c in m for c in "*?[") and m in name)
               for m in masks for name in names)


def actions(rules: list[dict], event: Event) -> set[str]:
    return {r["action"] for r in rules if matches(r, event)}
