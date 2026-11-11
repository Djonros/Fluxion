"""What every connector of «Личный помощник» is: one source, its events and its tools.

A connector says, before it is turned on, what it reads (``reads``), what it writes to
the journal (``records``) and what it never does (``never``) — the window shows these
lines to the user as they are. Running, it gives its events to ``emit``; it may also
offer tools to the agent of the assistant (``tools``, ``apps.AppTool``): a name, a
description, a JSON schema of the arguments and a permission (``ask`` — only after «Да»).
"""
from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass, field


@dataclass
class Event:
    connector: str
    kind: str             # "file.created", "app.focus", …
    title: str            # one line a person reads in the journal
    ref: str = ""         # what it is about: a path, a program
    detail: str = ""      # a little more, never the content of a file or a letter
    time: float = field(default_factory=time.time)
    duration: float = 0.0  # seconds, for the activity intervals


class Connector:
    """A source of events. Subclasses set the class fields and override start/stop."""

    id = ""
    title = ""
    description = ""
    reads: tuple[str, ...] = ()
    records: tuple[str, ...] = ()
    never: tuple[str, ...] = ()

    def __init__(self, settings: dict | None = None):
        self.settings = dict(settings or {})
        self._emit: Callable[[Event], None] | None = None
        self.state = ""      # what the window shows: "" while off
        self.ok = True

    # ── running ──
    def start(self, emit: Callable[[Event], None]) -> None:
        self._emit = emit
        self.state, self.ok = "работает", True

    def stop(self) -> None:
        self._emit = None
        self.state = ""

    def emit(self, event: Event) -> None:
        if self._emit is not None:
            self._emit(event)

    def fail(self, text: str) -> None:
        self.state, self.ok = text, False

    # ── what it offers ──
    def tools(self, cloud: bool = False) -> list:
        """Tools for the agent of the assistant (``apps.AppTool``); *cloud* — the cloud model may use them."""
        return []
