"""Shared test fixtures."""
from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _isolate_chat_history(tmp_path, monkeypatch):
    """Windows of the desktop app restore and save chat sessions; when run
    from sources that is ``data/chats.json`` in the project, i.e. the
    developer's real chat history.  Point every test at a temp file."""
    try:
        import desktop_browser.memory as memory
    except Exception:  # package unavailable in a trimmed environment
        return
    monkeypatch.setattr(memory, "chats_path", lambda: tmp_path / "chats.json")
    monkeypatch.setattr(memory, "history_path", lambda: tmp_path / "chat_history.json")
