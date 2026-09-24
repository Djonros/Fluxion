"""Wizard auto-install: linkify remedies, winget wrapper, docker daemon start,
and the wizard install button (clean-machine onboarding)."""
from __future__ import annotations

from types import SimpleNamespace

import pytest
from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QApplication

import desktop_browser.installer as installer
from desktop_browser.health import linkify


@pytest.fixture(scope="module", autouse=True)
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app


DEAD_HOST = "http://127.0.0.1:1"


def _status(key, title, available, fix="", remedy="", detail=""):
    return SimpleNamespace(
        key=key, title=title, available=available,
        detail=detail, remedy=remedy, fix=fix,
    )


# ── linkify ──────────────────────────────────────────────────────────────


def test_linkify_makes_single_anchor():
    out = linkify("Установите Ollama (https://ollama.com/download), затем нажмите.")
    assert '<a href="https://ollama.com/download">' in out
    assert out.count("<a ") == 1


def test_linkify_without_urls_is_plain_escaped_text():
    out = linkify("Запустите Docker Desktop <и> нажмите «Перепроверить».")
    assert "<a " not in out
    assert "&lt;и&gt;" in out


# ── installer seams ─────────────────────────────────────────────────────


def test_winget_install_fails_cleanly_without_winget(monkeypatch):
    monkeypatch.setattr(installer.shutil, "which", lambda name: None)
    ok, message = installer.winget_install("Ollama.Ollama")
    assert ok is False
    assert "winget" in message


def test_winget_package_ids_stable():
    assert installer.WINGET_PACKAGES == {
        "ollama": "Ollama.Ollama",
        "docker": "Docker.DockerDesktop",
        "git": "Git.Git",
    }


def test_ensure_docker_false_when_no_cli_and_no_exe(monkeypatch):
    monkeypatch.setattr(installer, "docker_daemon_ok", lambda timeout=12.0: False)
    monkeypatch.setattr(installer, "_docker_desktop_exe", lambda: None)
    assert installer.ensure_docker(wait_secs=0) is False


def test_ensure_docker_true_when_daemon_already_ok(monkeypatch):
    launched = {"count": 0}
    monkeypatch.setattr(installer, "docker_daemon_ok", lambda timeout=12.0: True)
    monkeypatch.setattr(
        installer, "_docker_desktop_exe", lambda: (_ for _ in ()).throw(AssertionError())
    )

    def fail_popen(*a, **k):
        launched["count"] += 1
        raise AssertionError("must not launch when daemon is up")

    monkeypatch.setattr(installer.subprocess, "Popen", fail_popen)
    assert installer.ensure_docker() is True
    assert launched["count"] == 0


def test_refresh_path_swallows_registry_errors(monkeypatch):
    def boom(root, subkey):
        raise OSError("registry unavailable")

    monkeypatch.setattr(installer, "_read_path_key", boom)
    assert installer.refresh_path() is False


# ── wizard UI ────────────────────────────────────────────────────────────


class _NoWorker:
    def __init__(self, *a, **k):
        pass

    def start(self):
        pass

    done = SimpleNamespace(connect=lambda *a: None)


def _make_wizard(tmp_path, monkeypatch):
    from desktop_browser.wizard import FirstRunWizard

    monkeypatch.setattr("desktop_browser.wizard.WizardCheckWorker", _NoWorker)
    qsettings = QSettings(str(tmp_path / "wizard.ini"), QSettings.IniFormat)
    return FirstRunWizard(
        SimpleNamespace(ollama_host=DEAD_HOST, model="m"), None, None, qsettings
    )


def test_wizard_offers_auto_install_with_clickable_link(tmp_path, monkeypatch):
    monkeypatch.setattr("desktop_browser.wizard.winget_available", lambda: True)
    wizard = _make_wizard(tmp_path, monkeypatch)
    statuses = [
        _status(
            "docker",
            "Docker",
            False,
            remedy="Установите Docker Desktop (https://www.docker.com/products/docker-desktop/).",
        ),
        _status("model", "Модель", False, fix="wizard"),
    ]
    wizard._on_checks(statuses)
    assert wizard.install_button.isVisibleTo(wizard)
    assert "docker" in wizard.install_button.text()
    assert (
        '<a href="https://www.docker.com/products/docker-desktop/">'
        in wizard.check_label.text()
    )


def test_wizard_hides_install_when_nothing_installable(tmp_path, monkeypatch):
    monkeypatch.setattr("desktop_browser.wizard.winget_available", lambda: True)
    wizard = _make_wizard(tmp_path, monkeypatch)
    statuses = [
        _status("ollama", "Ollama", True),
        _status("model", "Модель", False, fix="wizard", remedy="скачайте"),
    ]
    wizard._on_checks(statuses)
    assert wizard.install_button.isHidden()


def test_wizard_hides_install_when_winget_missing(tmp_path, monkeypatch):
    monkeypatch.setattr("desktop_browser.wizard.winget_available", lambda: False)
    wizard = _make_wizard(tmp_path, monkeypatch)
    statuses = [_status("docker", "Docker", False, remedy="установите")]
    wizard._on_checks(statuses)
    assert wizard.install_button.isHidden()


def test_wizard_install_done_reruns_checks(tmp_path, monkeypatch):
    rechecked = {"count": 0}

    wizard = _make_wizard(tmp_path, monkeypatch)

    def fake_run_checks():
        rechecked["count"] += 1

    monkeypatch.setattr(wizard, "_run_checks", fake_run_checks)
    wizard.install_status.setVisible(True)
    wizard._on_install_done(False, "docker: не установлен")
    assert "docker" in wizard.install_status.text()
    assert wizard.install_button.isEnabled()
    assert rechecked["count"] == 1
