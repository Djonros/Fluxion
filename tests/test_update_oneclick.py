"""One-click update (core/update.py, FluxionWindow): the installer of the release,
its SHA256, the check once a day, installed vs portable copies. No network."""
from __future__ import annotations

import hashlib
import os
import sys
from contextlib import contextmanager
from pathlib import Path

import pytest

import core.update as upd
from core import app_paths

ROOT = Path(__file__).resolve().parents[1]
PAYLOAD = b"MZ fake installer" * 1000
PAYLOAD_SHA = hashlib.sha256(PAYLOAD).hexdigest()


def _asset(name: str) -> dict:
    return {"name": name, "browser_download_url": f"https://example.test/{name}", "size": 10}


def _info(**kw) -> upd.UpdateInfo:
    values = dict(current_version="0.20.0", latest_version="0.21.0", release_url="https://example.test/r",
                  release_notes="", is_newer=True, installer_url="https://example.test/FluxionSetup-0.21.0.exe",
                  installer_name="FluxionSetup-0.21.0.exe", sha256_url="https://example.test/FluxionSetup-0.21.0.exe.sha256")
    values.update(kw)
    return upd.UpdateInfo(**values)


# ── the assets of a release ──────────────────────────────────────────────────


def test_pick_installer_prefers_release_version_and_pairs_checksum():
    assets = [_asset("FluxionBrowser-Lite-win64-20261027.7z"), _asset("FluxionSetup-0.20.0.exe"),
              _asset("FluxionSetup-0.21.0.exe"), _asset("FluxionSetup-0.21.0.exe.sha256"),
              _asset("FluxionSetup-0.20.0.exe.sha256")]
    installer, checksum = upd.pick_installer(assets, "0.21.0")
    assert installer["name"] == "FluxionSetup-0.21.0.exe"
    assert checksum["name"] == "FluxionSetup-0.21.0.exe.sha256"
    # no exact version: the newest installer
    installer, _ = upd.pick_installer(assets, "9.9.9")
    assert installer["name"] == "FluxionSetup-0.21.0.exe"


def test_pick_installer_without_installer_or_checksum():
    assert upd.pick_installer([_asset("FluxionBrowser-Lite-win64-1.7z")], "1.0.0") == (None, None)
    assert upd.pick_installer(None) == (None, None)
    installer, checksum = upd.pick_installer([_asset("FluxionSetup-1.0.0.exe")], "1.0.0")
    assert installer and checksum is None


def test_check_for_updates_returns_installer_assets(monkeypatch):
    class Resp:
        def raise_for_status(self):
            pass

        def json(self):
            return {"tag_name": "v0.21.0", "html_url": "https://example.test/r", "body": "notes",
                    "assets": [_asset("FluxionSetup-0.21.0.exe"), _asset("FluxionSetup-0.21.0.exe.sha256")]}

    monkeypatch.setattr(upd.httpx, "get", lambda *a, **k: Resp())
    info = upd.check_for_updates("0.20.0")
    assert info.is_newer and info.installer_name == "FluxionSetup-0.21.0.exe"
    assert info.installer_url.endswith("/FluxionSetup-0.21.0.exe")
    assert info.sha256_url.endswith(".exe.sha256")


def test_parse_sha256():
    assert upd.parse_sha256(f"{PAYLOAD_SHA.upper()} *FluxionSetup-0.21.0.exe\n") == PAYLOAD_SHA
    assert upd.parse_sha256("not a hash") is None
    assert upd.parse_sha256("") is None


# ── download and checksum ────────────────────────────────────────────────────


@pytest.fixture()
def fake_net(monkeypatch):
    """httpx.get gives the checksum text, httpx.stream the installer bytes."""
    state = {"sha": f"{PAYLOAD_SHA} *FluxionSetup-0.21.0.exe\n", "payload": PAYLOAD}

    class TextResp:
        def __init__(self, text):
            self.text = text

        def raise_for_status(self):
            pass

    class StreamResp:
        headers = {}

        def raise_for_status(self):
            pass

        def iter_bytes(self, size):
            data = state["payload"]
            for i in range(0, len(data), 4096):
                yield data[i:i + 4096]

    @contextmanager
    def stream(method, url, **kw):
        StreamResp.headers = {"content-length": str(len(state["payload"]))}
        yield StreamResp()

    monkeypatch.setattr(upd.httpx, "get", lambda url, **kw: TextResp(state["sha"]))
    monkeypatch.setattr(upd.httpx, "stream", stream)
    return state


def test_download_checks_sha256(tmp_path, fake_net):
    seen = []
    path = upd.download_installer(_info(), tmp_path, on_progress=lambda d, t: seen.append((d, t)))
    assert path == tmp_path / "FluxionSetup-0.21.0.exe" and path.read_bytes() == PAYLOAD
    assert seen[-1] == (len(PAYLOAD), len(PAYLOAD))
    assert not list(tmp_path.glob("*.part"))


def test_download_refuses_wrong_checksum(tmp_path, fake_net):
    fake_net["payload"] = PAYLOAD + b"tampered"
    with pytest.raises(upd.UpdateError) as err:
        upd.download_installer(_info(), tmp_path)
    assert err.value.kind == "checksum_mismatch"
    assert not any(tmp_path.iterdir()), "a file that does not match is deleted"


def test_download_refuses_missing_checksum(tmp_path, fake_net):
    with pytest.raises(upd.UpdateError) as err:
        upd.download_installer(_info(sha256_url=""), tmp_path)
    assert err.value.kind == "checksum_missing"
    fake_net["sha"] = "<html>404</html>"
    with pytest.raises(upd.UpdateError) as err:
        upd.download_installer(_info(), tmp_path)
    assert err.value.kind == "checksum_missing"


def test_download_can_be_cancelled(tmp_path, fake_net):
    with pytest.raises(upd.UpdateError) as err:
        upd.download_installer(_info(), tmp_path, cancelled=lambda: True)
    assert err.value.kind == "cancelled"
    assert not any(tmp_path.iterdir())


def test_installer_runs_silently_and_restarts_the_app():
    assert {"/VERYSILENT", "/SUPPRESSMSGBOXES", "/RESTARTAPP"} <= set(upd.SILENT_ARGS)
    iss = (ROOT / "installer" / "fluxion.iss").read_text(encoding="utf-8-sig")
    assert "Check: RestartAppRequested" in iss and "skipifnotsilent" in iss
    assert "'/RESTARTAPP'" in iss


# ── once a day ───────────────────────────────────────────────────────────────


def test_should_auto_check_once_a_day():
    now = 1_000_000.0
    assert upd.should_auto_check(None, now)
    assert upd.should_auto_check(0, now)
    assert not upd.should_auto_check(now - 3600, now)
    assert upd.should_auto_check(now - upd.CHECK_INTERVAL, now)
    assert upd.should_auto_check(now + 3600, now), "a clock moved back"
    assert upd.should_auto_check("garbage", now)
    assert not upd.should_auto_check(None, now, enabled=False)


# ── installed copy vs portable ───────────────────────────────────────────────


@pytest.fixture()
def frozen_exe(tmp_path, monkeypatch):
    exe = tmp_path / "app" / "FluxionBrowserLite.exe"
    exe.parent.mkdir()
    exe.write_bytes(b"")
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", str(exe))
    return exe


def test_installed_copy_has_the_uninstaller(frozen_exe):
    assert not app_paths.is_installed(), "an unpacked .7z"
    (frozen_exe.parent / "unins000.exe").write_bytes(b"")
    assert app_paths.is_installed()
    (frozen_exe.parent / "portable.txt").write_text("", encoding="utf-8")
    assert not app_paths.is_installed(), "portable mode"


def test_source_run_is_not_installed(monkeypatch):
    monkeypatch.setattr(sys, "frozen", False, raising=False)
    assert not app_paths.is_installed()


# ── the window ───────────────────────────────────────────────────────────────


@pytest.fixture()
def window(tmp_path, monkeypatch):
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    os.environ.setdefault("FLUXION_DISABLE_WEBENGINE", "1")
    pytest.importorskip("PySide6")
    from PySide6.QtCore import QSettings
    from PySide6.QtWidgets import QApplication

    from desktop_browser.app import FluxionWindow

    QApplication.instance() or QApplication([])
    monkeypatch.delenv("FLUXION_NO_UPDATE_CHECK", raising=False)
    win = FluxionWindow(qsettings=QSettings(str(tmp_path / "ui.ini"), QSettings.IniFormat))
    yield win
    win.close()


def test_auto_check_runs_once_a_day(window, monkeypatch):
    started = []
    monkeypatch.setattr(window, "_check_updates", lambda auto=False: started.append(auto))
    assert window.auto_check_updates() and started == [True]
    assert not window.auto_check_updates(), "the second start the same day"
    window.qsettings.setValue(window.UPDATES_LAST_KEY, 0)
    window.qsettings.setValue(window.UPDATES_AUTO_KEY, False)
    assert not window.auto_check_updates(), "turned off"
    assert started == [True]


def test_auto_check_is_silent_without_update_or_for_skipped(window, monkeypatch):
    shown = []
    monkeypatch.setattr(window, "_show_update_dialog", lambda info: shown.append(info.latest_version))
    from PySide6.QtWidgets import QMessageBox

    monkeypatch.setattr(QMessageBox, "warning", lambda *a, **k: shown.append("warning"))
    monkeypatch.setattr(QMessageBox, "information", lambda *a, **k: shown.append("information"))
    window._on_update_result(None, auto=True)
    window._on_update_result(_info(is_newer=False), auto=True)
    window.qsettings.setValue(window.UPDATES_SKIP_KEY, "0.21.0")
    window._on_update_result(_info(), auto=True)
    assert shown == []
    window._on_update_result(_info(), auto=False)  # the menu still shows a skipped version
    window.qsettings.setValue(window.UPDATES_SKIP_KEY, "")
    window._on_update_result(_info(), auto=True)
    assert shown == ["0.21.0", "0.21.0"]


def test_update_now_only_for_installed_copy(window, frozen_exe, monkeypatch):
    assert not window._can_update_in_place(_info())
    (frozen_exe.parent / "unins000.exe").write_bytes(b"")
    assert window._can_update_in_place(_info())
    assert not window._can_update_in_place(_info(sha256_url=""))


def test_update_dialog_buttons(window, frozen_exe, monkeypatch):
    from PySide6.QtWidgets import QMessageBox

    (frozen_exe.parent / "unins000.exe").write_bytes(b"")
    seen = {}

    def fake_exec(box):
        seen["buttons"] = [b.text() for b in box.buttons()]
        seen["text"] = box.text()
        seen["check"] = box.checkBox().text()
        return 0

    monkeypatch.setattr(QMessageBox, "exec", fake_exec)
    window._show_update_dialog(_info(release_notes="Новое: <b>всё</b>"))
    assert sorted(seen["buttons"]) == sorted(["Обновить сейчас", "Пропустить эту версию", "Позже"])
    assert "&lt;b&gt;всё&lt;/b&gt;" in seen["text"] and "#download" in seen["text"]
    assert seen["check"] == "Проверять обновления автоматически"


def test_failed_update_messages(window, monkeypatch):
    from PySide6.QtWidgets import QMessageBox

    texts = []
    monkeypatch.setattr(QMessageBox, "warning", lambda parent, title, text: texts.append(text))
    window._on_update_failed("cancelled")
    window._on_update_failed("checksum_mismatch")
    window._on_update_failed("network:timeout")
    assert len(texts) == 2 and "Контрольная сумма" in texts[0] and "timeout" in texts[1]
