"""The installer FluxionSetup.exe: Inno Setup script, its build step and the release."""
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ISS = ROOT / "installer" / "fluxion.iss"


def _iss() -> str:
    return ISS.read_text(encoding="utf-8-sig")


def test_iss_per_user_update_friendly():
    text = _iss()
    # Cyrillic messages: Inno Setup reads a script as UTF-8 only with a BOM
    assert ISS.read_bytes().startswith(b"\xef\xbb\xbf")
    assert re.search(r"^PrivilegesRequired=lowest$", text, re.M)
    assert r"DefaultDirName={localappdata}\Programs\Fluxion" in text
    assert re.search(r"^AppId=\{\{[0-9A-F-]{36}\}$", text, re.M), "a fixed AppId makes a setup an update"
    assert "CloseApplications=force" in text
    assert "OutputBaseFilename=FluxionSetup-{#AppVersion}" in text
    assert 'MessagesFile: "compiler:Languages\\Russian.isl"' in text
    assert "skipifsilent" in text and "Запустить Fluxion" in text


def test_iss_never_ships_or_removes_user_data():
    text = _iss()
    files = re.search(r"^Source: .*$", text, re.M).group(0)
    assert "portable.txt" in files and r"data\*" in files
    # the program folder only; the data are removed on request, never silently
    for section in ("InstallDelete", "UninstallDelete"):
        body = text.split(f"[{section}]", 1)[1].split("\n[", 1)[0]
        assert "localappdata}\\Fluxion" not in body and "{userappdata}" not in body
    assert "UninstallSilent" in text and "MB_DEFBUTTON2" in text


def test_build_step_and_release_know_the_installer():
    ps1 = ROOT / "scripts" / "build_installer.ps1"
    assert ps1.read_bytes().startswith(b"\xef\xbb\xbf"), "PowerShell 5.1 needs a BOM for Cyrillic"
    script = ps1.read_text(encoding="utf-8-sig")
    assert "installer\\fluxion.iss" in script and "SHA256" in script
    assert "winget install JRSoftware.InnoSetup" in script
    update = (ROOT / "fluxion-update.bat").read_text(encoding="utf-8")
    assert "build_installer.ps1" in update
    release = (ROOT / "fluxion-release.bat").read_text(encoding="utf-8")
    assert "FluxionSetup-" in release and ".sha256" in release


def test_update_bat_refuses_older_archive_without_force():
    text = (ROOT / "fluxion-update.bat").read_text(encoding="utf-8")
    assert "ТОЛЬКО ДЛЯ РАЗРАБОТЧИКА" in text
    assert '"--force"' in text and "zip_version_old" in text
    guard = text.index("zip_version_old")
    assert guard < text.index("[1/6]"), "the version is checked before anything is changed"


def test_installer_and_app_point_to_the_site():
    from desktop_browser import SITE_URL

    assert SITE_URL == "https://djonros.github.io/Fluxion/"
    assert f"AppPublisherURL={SITE_URL}" in _iss() and f"AppUpdatesURL={SITE_URL}#download" in _iss()
    app = (ROOT / "desktop_browser" / "app.py").read_text(encoding="utf-8")
    assert "act_about.triggered.connect(self._show_about)" in app
    assert 'QUrl(SITE_URL + "#download")' in app
