"""User data folder: source run, installed (frozen) build, portable mode, migration."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from core import app_paths


@pytest.fixture()
def frozen_exe(tmp_path, monkeypatch):
    """A frozen build in tmp_path/app; LOCALAPPDATA in tmp_path/local."""
    exe = tmp_path / "app" / "FluxionBrowserLite.exe"
    exe.parent.mkdir()
    exe.write_bytes(b"")
    monkeypatch.delenv("FLUXION_HOME", raising=False)
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "local"))
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", str(exe))
    return exe


def test_source_run_uses_repository(monkeypatch):
    monkeypatch.setattr(sys, "frozen", False, raising=False)
    root = Path(app_paths.__file__).resolve().parents[1]
    assert app_paths.app_home() == root
    assert app_paths.data_dir() == root / "data"
    assert app_paths.anchor("data/chroma") == "data/chroma"
    assert app_paths.user_config_path() is None
    assert app_paths.migrate_legacy_data() == []


def test_installed_build_uses_local_appdata(frozen_exe, tmp_path):
    home = tmp_path / "local" / "Fluxion"
    assert not app_paths.is_portable()
    assert app_paths.app_home() == home
    assert app_paths.data_dir() == home / "data"
    assert Path(app_paths.anchor("data/chroma")) == home / "data" / "chroma"
    assert app_paths.anchor(str(tmp_path / "abs")) == str(tmp_path / "abs")


def test_fluxion_home_overrides(frozen_exe, tmp_path, monkeypatch):
    monkeypatch.setenv("FLUXION_HOME", str(tmp_path / "custom"))
    assert app_paths.data_dir() == tmp_path / "custom" / "data"


def test_portable_marker_keeps_exe_folder(frozen_exe):
    (frozen_exe.parent / "portable.txt").write_text("", encoding="utf-8")
    assert app_paths.is_portable()
    assert app_paths.data_dir() == frozen_exe.resolve().parent / "data"
    assert app_paths.migrate_legacy_data() == []


def test_user_config_found_in_data_folder(frozen_exe, tmp_path):
    assert app_paths.user_config_path() is None
    cfg = tmp_path / "local" / "Fluxion" / "config" / "config.yaml"
    cfg.parent.mkdir(parents=True)
    cfg.write_text("backend: {}\n", encoding="utf-8")
    assert app_paths.user_config_path() == cfg


def _old_data(exe: Path) -> Path:
    old = exe.parent / "data"
    (old / "chroma").mkdir(parents=True)
    (old / "chats.json").write_text('{"sessions": []}', encoding="utf-8")
    (old / "chroma" / "index.bin").write_bytes(b"x")
    env_py = old / "training_env" / "Scripts" / "python.exe"
    env_py.parent.mkdir(parents=True)
    env_py.write_bytes(b"")
    cfg = exe.parent / "config" / "config.yaml"
    cfg.parent.mkdir()
    cfg.write_text("mine: 1\n", encoding="utf-8")
    return old


def test_migration_copies_old_data_once(frozen_exe, tmp_path):
    old = _old_data(frozen_exe)
    new = tmp_path / "local" / "Fluxion"

    assert app_paths.migrate_legacy_data() == ["data", "config"]
    assert (new / "data" / "chats.json").read_text(encoding="utf-8") == '{"sessions": []}'
    assert (new / "data" / "chroma" / "index.bin").read_bytes() == b"x"
    assert (new / "config" / "config.yaml").read_text(encoding="utf-8") == "mine: 1\n"
    # the venv is not copied; the old data is left in place
    assert not (new / "data" / "training_env").exists()
    assert (old / "chats.json").is_file()
    marker = json.loads((new / "data" / app_paths.MIGRATION_MARKER).read_text(encoding="utf-8"))
    assert marker["copied"] == ["data", "config"]

    # a second start changes nothing, even when the old data changed
    (old / "chats.json").write_text("newer", encoding="utf-8")
    assert app_paths.migrate_legacy_data() == []
    assert (new / "data" / "chats.json").read_text(encoding="utf-8") == '{"sessions": []}'


def test_migration_skipped_when_new_data_exists(frozen_exe, tmp_path):
    _old_data(frozen_exe)
    new_data = tmp_path / "local" / "Fluxion" / "data"
    new_data.mkdir(parents=True)
    (new_data / "chats.json").write_text("current", encoding="utf-8")
    assert app_paths.migrate_legacy_data() == []
    assert (new_data / "chats.json").read_text(encoding="utf-8") == "current"


def test_migration_without_old_data_does_nothing(frozen_exe, tmp_path):
    assert app_paths.migrate_legacy_data() == []
    assert not (tmp_path / "local" / "Fluxion").exists()


def test_call_sites_follow_data_folder(frozen_exe, tmp_path):
    from desktop_browser import crash, memory, searxng

    data = tmp_path / "local" / "Fluxion" / "data"
    # conftest points chats_path at tmp_path; the folder behind it is _base_dir
    assert memory._base_dir() == data
    assert crash.crash_dir() == data / "logs"
    assert searxng._data_dir() == data / "searxng"


def test_web_cache_default_in_data_folder(frozen_exe, tmp_path):
    from web.cache import WebCache

    cache = WebCache()
    assert cache.db_path == tmp_path / "local" / "Fluxion" / "data" / "web_cache.db"


def test_training_env_next_to_old_exe_still_found(frozen_exe, tmp_path):
    from desktop_browser import training

    legacy = frozen_exe.parent / "data" / "training_env"
    py = training.env_python_path(legacy)
    py.parent.mkdir(parents=True)
    py.write_bytes(b"")
    (legacy / training.ENV_MARKER).write_text("{}", encoding="utf-8")
    new_data = tmp_path / "local" / "Fluxion" / "data"
    assert training.training_env_dir(new_data).resolve() == legacy.resolve()


def test_prepare_config_prefers_user_config(frozen_exe, tmp_path, monkeypatch):
    import run_desktop_browser

    monkeypatch.setenv("AI_AGENT_CONFIG", "")  # restored after the test
    monkeypatch.delenv("AI_AGENT_CONFIG")
    monkeypatch.chdir(tmp_path)
    cfg = tmp_path / "local" / "Fluxion" / "config" / "config.yaml"
    cfg.parent.mkdir(parents=True)
    cfg.write_text("x: 1\n", encoding="utf-8")
    run_desktop_browser._prepare_config()
    assert Path(run_desktop_browser.os.environ["AI_AGENT_CONFIG"]) == cfg


def test_about_dialog_shows_site_and_data(tmp_path, monkeypatch):
    import os

    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    os.environ.setdefault("FLUXION_DISABLE_WEBENGINE", "1")
    pytest.importorskip("PySide6")
    from PySide6.QtCore import QSettings
    from PySide6.QtWidgets import QApplication, QMessageBox

    from desktop_browser import SITE_URL, app as app_module

    QApplication.instance() or QApplication([])
    shown = {}

    def fake_exec(box):
        shown["text"] = box.text()
        shown["buttons"] = [b.text() for b in box.buttons()]
        return 0

    monkeypatch.setattr(QMessageBox, "exec", fake_exec)
    window = app_module.FluxionWindow(qsettings=QSettings(str(tmp_path / "ui.ini"), QSettings.IniFormat))
    window._show_about()
    window.close()
    assert SITE_URL in shown["text"] and app_module.APP_VERSION in shown["text"]
    assert "Открыть сайт" in shown["buttons"]
