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


@pytest.fixture(autouse=True)
def _no_embedded_engine_by_default(monkeypatch):
    """Without an explicit backend the app picks llama.cpp when it is
    installed.  Whether it is installed differs between a developer machine
    and CI, so tests assume it is not, unless they say otherwise."""
    try:
        import core.backend_factory as factory
    except Exception:
        return
    monkeypatch.setattr(factory, "_llama_cpp_installed", lambda: False)


@pytest.fixture(autouse=True)
def _no_real_key_search(monkeypatch):
    """The licence dialog looks for key files in Downloads/Desktop; tests must
    not scan the developer's real folders."""
    try:
        import licensing.store as store
    except Exception:
        return
    monkeypatch.setattr(store, "key_search_dirs", lambda: [])


@pytest.fixture(autouse=True)
def _isolated_models_dir(tmp_path, monkeypatch):
    """Never touch the developer's real models folder (%LOCALAPPDATA%\\Fluxion
    \\models): downloads, imports and trained models go to a temp folder.
    Tests about the default location unset this variable themselves."""
    monkeypatch.setenv("FLUXION_MODELS_DIR", str(tmp_path / "models"))
