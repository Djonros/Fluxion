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


@pytest.fixture(autouse=True)
def _spent_trial_by_default(tmp_path_factory, monkeypatch):
    """The Pro trial lives in ``data/trial.json`` of the project, i.e. the
    developer's real trial.  Point it at a temp file and start every test with
    the trial spent, so "no licence" keeps meaning "no file writes"; tests about
    the trial reset it themselves."""
    try:
        import licensing.trial as trial
    except Exception:
        return
    path = tmp_path_factory.mktemp("trial") / "trial.json"
    path.write_text('{"device": null, "remaining": 0}', encoding="utf-8")
    monkeypatch.setattr(trial, "trial_path", lambda: path)


@pytest.fixture(autouse=True)
def _close_app_windows():
    """Windows of the desktop app start background checks (engine status,
    health, model list).  A window left open by a test kept such a thread
    running until interpreter exit, which crashed the process after the last
    test ("QThread: Destroyed while thread is still running").  Closing the
    window waits for its workers."""
    yield
    import sys

    widgets = sys.modules.get("PySide6.QtWidgets")
    app = widgets.QApplication.instance() if widgets is not None else None
    if app is None:
        return
    for widget in app.topLevelWidgets():
        if not isinstance(widget, widgets.QMainWindow) or widget.property("test_closed"):
            continue
        widget.setProperty("test_closed", True)
        try:
            widget.close()
        except Exception:  # a test replaced a worker with a stub that cannot stop
            pass
    app.processEvents()
