"""Release consistency: one version everywhere, documented in the changelog."""
from __future__ import annotations

import re
from pathlib import Path

from core.update import is_newer
from desktop_browser import APP_VERSION

ROOT = Path(__file__).resolve().parents[1]


def test_version_format():
    assert re.fullmatch(r"\d+\.\d+\.\d+", APP_VERSION), APP_VERSION


def test_changelog_has_section_for_current_version():
    """fluxion-release.bat tags v<APP_VERSION>; the changelog must describe it."""
    changelog = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    assert f"## [{APP_VERSION}]" in changelog


def test_release_notes_name_current_version():
    notes = (ROOT / "RELEASE_NOTES_RU.md").read_text(encoding="utf-8")
    assert APP_VERSION in notes.splitlines()[0]


def test_crash_report_uses_app_version():
    from desktop_browser.crash import app_context

    assert app_context()["version"] == f"desktop-{APP_VERSION}"


def test_update_check_sees_minor_bump_as_newer():
    # numeric comparison: 0.10.0 must beat 0.9.x (string comparison would not)
    assert is_newer("0.10.0", "0.9.0")
    assert is_newer(f"v{APP_VERSION}".lstrip("v"), "0.9.0")
    assert not is_newer("0.9.9", "0.10.0")


def test_release_date_matches_changelog():
    """The update period of a licence is counted from this build's release date."""
    from licensing import RELEASE_DATE

    changelog = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    heading = re.search(rf"^## \[{re.escape(APP_VERSION)}\] — (\d{{4}}-\d{{2}}-\d{{2}})", changelog, re.M)
    assert heading, f"no dated changelog heading for {APP_VERSION}"
    assert heading.group(1) == RELEASE_DATE, "bump licensing/release.py together with the version"

