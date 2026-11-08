"""Microsoft Store build: package detection, no in-app updater, the MSIX template and images."""
from __future__ import annotations

import json
import re
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

from core import app_paths

ROOT = Path(__file__).resolve().parents[1]
MSIX = ROOT / "installer" / "msix"


@pytest.fixture()
def installed_exe(tmp_path, monkeypatch):
    """A FluxionSetup install: frozen, the uninstaller next to the exe."""
    exe = tmp_path / "app" / "FluxionBrowserLite.exe"
    exe.parent.mkdir()
    exe.write_bytes(b"")
    (exe.parent / "unins000.exe").write_bytes(b"")
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", str(exe))
    monkeypatch.delenv("FLUXION_STORE_BUILD", raising=False)
    return exe


def test_ordinary_process_is_not_a_package(monkeypatch):
    monkeypatch.delenv("FLUXION_STORE_BUILD", raising=False)
    assert app_paths._query_package_full_name() == ""  # pytest itself is not packaged
    assert not app_paths.is_store_package()


def test_store_package_is_not_updated_in_place(installed_exe, monkeypatch):
    assert app_paths.is_installed()
    monkeypatch.setenv("FLUXION_STORE_BUILD", "1")
    assert app_paths.is_store_package()
    assert not app_paths.is_installed()


def test_store_package_has_no_run_key_autostart(installed_exe, monkeypatch):
    from desktop_browser import bots_dialog

    monkeypatch.setattr(sys, "platform", "win32")
    assert bots_dialog.autostart_available()
    monkeypatch.setenv("FLUXION_STORE_BUILD", "1")
    assert not bots_dialog.autostart_available()


def test_manifest_template_fields_match_identity_example():
    text = (MSIX / "AppxManifest.xml").read_text(encoding="utf-8")
    fields = set(re.findall(r"\{\{(\w+)\}\}", text))
    example = json.loads((MSIX / "store-identity.example.json").read_text(encoding="utf-8"))
    assert fields == {k for k in example if not k.startswith("_")} | {"Version"}
    filled = re.sub(r"\{\{\w+\}\}", "x", text)
    root = ET.fromstring(filled)
    ns = {"m": "http://schemas.microsoft.com/appx/manifest/foundation/windows10"}
    app = root.find("m:Applications/m:Application", ns)
    assert app.get("Executable") == "FluxionBrowserLite.exe"
    assert app.get("EntryPoint") == "Windows.FullTrustApplication"


def test_assets_cover_manifest_images(tmp_path):
    pytest.importorskip("PIL")
    sys.path.insert(0, str(ROOT / "scripts"))
    try:
        import msix_assets
    finally:
        sys.path.pop(0)
    msix_assets.build(tmp_path)
    names = {p.name for p in (tmp_path / "Assets").iterdir()}
    text = (MSIX / "AppxManifest.xml").read_text(encoding="utf-8")
    for ref in re.findall(r"Assets\\(\w+)\.png", text):
        assert f"{ref}.scale-100.png" in names, ref
        assert f"{ref}.scale-200.png" in names, ref
    assert "Square44x44Logo.targetsize-24_altform-unplated.png" in names
    from PIL import Image

    with Image.open(tmp_path / "Assets" / "Wide310x150Logo.scale-200.png") as img:
        assert img.size == (620, 300)
