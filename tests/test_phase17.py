"""Phase 17: last-mile product fixes (fetch notice, update checker)."""
from __future__ import annotations

import sys
from types import SimpleNamespace

import pytest
from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QApplication

from desktop_browser.app import FluxionWindow
from web.fetcher import ContentFetcher
from web.pipeline import WebSearch


@pytest.fixture(scope="module", autouse=True)
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app


@pytest.fixture()
def make_window(tmp_path, monkeypatch):
    monkeypatch.setenv("FLUXION_DISABLE_WEBENGINE", "1")

    def factory(**kwargs):
        ini = tmp_path / f"settings_{len(list(tmp_path.iterdir()))}.ini"
        return FluxionWindow(qsettings=QSettings(str(ini), QSettings.IniFormat), **kwargs)

    return factory


# ── 17.1 fetch notice ─────────────────────────────────────────────────────


def test_fetcher_brief_mode_without_trafilatura(monkeypatch):
    monkeypatch.setitem(sys.modules, "trafilatura", None)
    assert ContentFetcher().brief_mode is True


def test_websearch_brief_mode_delegates_to_fetcher():
    ws = WebSearch(client=object(), fetcher=SimpleNamespace(brief_mode=True), cache=object())
    assert ws.brief_mode is True


def test_chat_web_used_shows_lite_banner(make_window):
    window = make_window(web_search=SimpleNamespace(brief_mode=True))
    window._messages.append({"role": "assistant", "text": "", "meta": ""})
    window._on_meta(SimpleNamespace(web_used=True, web_sources=["a"]))
    assert "fetch_lite" in window.banners._keys
    assert not window.banners.isHidden()


def test_chat_web_full_mode_no_banner(make_window):
    window = make_window(web_search=SimpleNamespace(brief_mode=False))
    window._messages.append({"role": "assistant", "text": "", "meta": ""})
    window._on_meta(SimpleNamespace(web_used=True, web_sources=["a"]))
    assert "fetch_lite" not in window.banners._keys
    assert window.banners.isHidden()


# ── 17.2 update checker ───────────────────────────────────────────────────


def test_update_is_newer_semver():
    from core.update import is_newer

    assert is_newer("0.10.0", "0.9.1") is True
    assert is_newer("0.9.0", "0.9.0") is False
    assert is_newer("1.0.0", "0.9.9") is True
    assert is_newer("0.9.0", "0.10.0") is False
    assert is_newer("v1.0.0", "0.9.0") is True


def test_check_for_updates_parses_release(monkeypatch):
    import core.update as upd

    class FakeResp:
        def raise_for_status(self):
            return None

        def json(self):
            return {
                "tag_name": "v1.2.0",
                "html_url": "https://github.com/x/y/releases/tag/v1.2.0",
                "body": "release notes",
            }

    monkeypatch.setattr(upd.httpx, "get", lambda *a, **k: FakeResp())
    info = upd.check_for_updates("1.1.0")
    assert info is not None
    assert info.latest_version == "1.2.0"
    assert info.is_newer is True
    assert info.release_url.endswith("v1.2.0")
    assert info.release_notes == "release notes"


def test_check_for_updates_error_returns_none(monkeypatch):
    import core.update as upd

    def boom(*a, **k):
        raise upd.httpx.ConnectError("offline")

    monkeypatch.setattr(upd.httpx, "get", boom)
    assert upd.check_for_updates("1.0.0") is None
