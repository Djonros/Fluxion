"""Phase 14 tests: onboarding, health checks, banners, crash logs, wizard."""
from __future__ import annotations

import sys
from types import SimpleNamespace

import pytest
from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QApplication

from desktop_browser.app import FluxionWindow
from desktop_browser.health import (
    check_docker,
    check_model,
    check_ollama,
    check_rag,
    check_searxng,
    summary_line,
)


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


DEAD_HOST = "http://127.0.0.1:1"


def _status(key, title, available, fix="", remedy=""):
    return SimpleNamespace(
        key=key, title=title, available=available, detail="", remedy=remedy, fix=fix
    )


# ── health checks (14.1) ────────────────────────────────────────────────


def test_check_ollama_unreachable_has_remedy():
    status = check_ollama(DEAD_HOST)
    assert not status.available
    assert "Ollama" in status.remedy


def test_check_model_dead_host_mentions_ollama():
    status = check_model(DEAD_HOST, "qwen2.5-coder:7b-instruct")
    assert not status.available
    assert "Ollama" in status.remedy


def test_check_model_missing_points_to_wizard(monkeypatch):
    monkeypatch.setattr(
        "core.ollama_client.OllamaClient.list_models", lambda self: ["other:latest"]
    )
    status = check_model("http://localhost:11434", "qwen2.5-coder:7b-instruct")
    assert not status.available
    assert status.fix == "wizard"


def test_check_docker_detected_and_missing(monkeypatch):
    import desktop_browser.health as health

    monkeypatch.setattr(health.shutil, "which", lambda name: "C:/docker.exe")
    monkeypatch.setattr(health, "_docker_daemon_ok", lambda cli: True)
    assert check_docker().available
    # 17.5: Docker — опциональное улучшение (нужно только SearXNG)
    monkeypatch.setattr(health, "_docker_daemon_ok", lambda cli: False)
    status = check_docker()
    assert status.available
    assert status.fix == ""
    monkeypatch.setattr(health.shutil, "which", lambda name: None)
    status = check_docker()
    assert status.available
    assert "SearXNG" in status.remedy


def test_check_searxng_states():
    missing = check_searxng(None)
    assert not missing.available

    enhanced = SimpleNamespace(
        provider_tier="enhanced",
        client=SimpleNamespace(base_url="http://x:8080"),
    )
    assert check_searxng(enhanced).available

    basic = SimpleNamespace(
        provider_tier="basic",
        client=SimpleNamespace(base_url="http://x:8080"),
    )
    status = check_searxng(basic)
    assert status.available  # 17.5: базовый режим без SearXNG — это норма
    assert status.fix == "searxng"

    dead = SimpleNamespace(client=SimpleNamespace(base_url="http://x:8080"))
    assert check_searxng(dead).available


def test_check_rag_states():
    assert not check_rag(None).available
    assert check_rag(SimpleNamespace(count=lambda: 5)).available
    empty = check_rag(SimpleNamespace(count=lambda: 0))
    assert not empty.available
    assert "Проиндексировать" in empty.remedy


def test_summary_line_lists_problems_only():
    ok = _status("ollama", "Ollama", True)
    bad = _status("docker", "Docker", False)
    assert summary_line([ok]) == ""
    assert "Docker" in summary_line([ok, bad])


# ── zero-silence banner (14.2) ──────────────────────────────────────────


def test_banner_is_visible_but_not_in_history(make_window):
    window = make_window()
    window._append_banner("Веб-поиск недоступен: нет Docker")
    assert window._messages[-1]["role"] == "banner"
    assert window.history == []
    assert "Веб-поиск недоступен" in window.messages.toHtml()


def test_startup_health_banner_emitted(make_window):
    window = make_window()
    window._on_startup_health(
        [_status("ollama", "Ollama", False), _status("docker", "Docker", True)]
    )
    banners = [m for m in window._messages if m["role"] == "banner"]
    assert len(banners) == 1
    assert "Ollama" in banners[0]["text"]
    assert "Состояние" in banners[0]["text"]

    window._on_startup_health(
        [_status("ollama", "Ollama", False), _status("docker", "Docker", False)]
    )
    banners = [m for m in window._messages if m["role"] == "banner"]
    assert len(banners) == 1  # banner shown once per session


# ── agent zero-silence (14.2) ───────────────────────────────────────────


def test_agent_web_search_reports_dead_searxng():
    from unittest.mock import MagicMock

    from orchestrator import CodingAgent

    web_search = SimpleNamespace(
        client=SimpleNamespace(base_url="http://localhost:8080", is_alive=lambda: False)
    )
    agent = CodingAgent(backend=MagicMock(), project_root=".", web_search=web_search)
    result = agent._tool_web_search("запрос")
    assert not result.success
    assert "localhost:8080" in result.error
    assert "Docker" in result.error


# ── crash logs (14.4) ───────────────────────────────────────────────────


def test_write_crash_creates_log(tmp_path, monkeypatch):
    import desktop_browser.crash as crash

    monkeypatch.setattr(crash, "crash_dir", lambda: tmp_path)
    try:
        raise ValueError("boom")
    except ValueError:
        path = crash.write_crash(*sys.exc_info())
    text = path.read_text(encoding="utf-8")
    assert "ValueError: boom" in text
    assert "Fluxion" in text
    assert crash.last_crash_log() == path


def test_crash_report_context():
    import desktop_browser.crash as crash

    ctx = crash.app_context()
    assert ctx["app"] == "Fluxion"
    assert "python" in ctx and "os" in ctx


# ── wizard (14.3) ───────────────────────────────────────────────────────


def test_wizard_marks_onboarded_and_offers_pull(tmp_path, monkeypatch):
    from desktop_browser.wizard import FirstRunWizard

    class _NoWorker:
        def __init__(self, *a, **k):
            pass

        def start(self):
            pass

        done = SimpleNamespace(connect=lambda *a: None)

    monkeypatch.setattr("desktop_browser.wizard.WizardCheckWorker", _NoWorker)
    statuses = [
        _status("ollama", "Ollama", True),
        _status("model", "Модель", False, fix="wizard", remedy="скачайте"),
    ]
    qsettings = QSettings(str(tmp_path / "wizard.ini"), QSettings.IniFormat)
    wizard = FirstRunWizard(
        SimpleNamespace(ollama_host=DEAD_HOST, model="m"), None, None, qsettings
    )
    wizard._on_checks(statuses)
    assert "Скачать модель" in wizard.pull_button.text()
    wizard.accept()
    assert int(qsettings.value("onboarded", 0)) == 1


def test_wizard_pull_failure_shows_message(monkeypatch):
    from desktop_browser.wizard import FirstRunWizard

    class _NoWorker:
        def __init__(self, *a, **k):
            pass

        def start(self):
            pass

        done = SimpleNamespace(connect=lambda *a: None)

    monkeypatch.setattr("desktop_browser.wizard.WizardCheckWorker", _NoWorker)
    wizard = FirstRunWizard(
        SimpleNamespace(ollama_host=DEAD_HOST, model="m"),
        None,
        None,
        SimpleNamespace(setValue=lambda *a: None),
    )
    wizard._on_pull_failed("Нет соединения")
    assert "Нет соединения" in wizard.pull_status.text()
    assert wizard.pull_button.isEnabled()


def test_main_skips_wizard_and_wraps_crashes():
    import inspect

    import desktop_browser.app as app_module

    source = inspect.getsource(app_module.main)
    assert 'FLUXION_DESKTOP_SMOKE") != "1"' in source  # wizard skipped in smoke
    assert "show_crash_dialog" in source  # crash path wired
