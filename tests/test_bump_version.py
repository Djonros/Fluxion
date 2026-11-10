"""fluxion-version.bat: scripts/bump_version.py raises the version for a release."""
from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

spec = importlib.util.spec_from_file_location("bump_version", Path(__file__).parent.parent / "scripts" / "bump_version.py")
bump_version = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bump_version)


def project(tmp_path, unreleased=True):
    (tmp_path / "desktop_browser").mkdir()
    (tmp_path / "licensing").mkdir()
    (tmp_path / "desktop_browser" / "__init__.py").write_bytes(b'"""x"""\r\nAPP_VERSION = "0.28.0"\r\n')
    (tmp_path / "licensing" / "release.py").write_text('RELEASE_DATE = "2026-11-04"\nUPDATE_PERIOD_DAYS = 365\n',
                                                     encoding="utf-8")
    body = "## [Unreleased]\r\n\r\n### Added\r\n- Повтор сообщения.\r\n- Архив КД в чате.\r\n\r\n" if unreleased else ""
    (tmp_path / "CHANGELOG.md").write_bytes(("# Changelog\r\n\r\n" + body + "## [0.28.0] — 2026-11-04\r\n\r\n- old\r\n")
                                            .encode("utf-8"))
    (tmp_path / "RELEASE_NOTES_RU.md").write_text(
        "# Fluxion 0.28.0 — старое\n\nСтарый текст.\n\nПрограмма одна, со всеми инструментами: Pro ключом.\n",
        encoding="utf-8")
    return tmp_path


def test_bump_changes_the_version_the_date_the_changelog_and_drafts_the_notes(tmp_path):
    root = project(tmp_path)
    files = bump_version.bump("0.29.0", "2026-11-10", root)
    assert set(files) == {"desktop_browser/__init__.py", "licensing/release.py", "CHANGELOG.md", "RELEASE_NOTES_RU.md"}
    init = (root / "desktop_browser" / "__init__.py").read_bytes()
    assert b'APP_VERSION = "0.29.0"\r\n' in init  # the line endings are kept
    assert 'RELEASE_DATE = "2026-11-10"' in (root / "licensing" / "release.py").read_text(encoding="utf-8")
    changelog = (root / "CHANGELOG.md").read_bytes().decode("utf-8")
    assert "## [0.29.0] — 2026-11-10\r\n" in changelog and "[Unreleased]" not in changelog
    notes = (root / "RELEASE_NOTES_RU.md").read_text(encoding="utf-8")
    assert notes.startswith("# Fluxion 0.29.0") and "- Архив КД в чате." in notes
    assert "Старый текст" not in notes and "Программа одна, со всеми инструментами" in notes


@pytest.mark.parametrize("version", ["0.28.0", "0.27.9", "1.0", "v0.29.0"])
def test_a_version_not_greater_or_not_x_y_z_is_refused(tmp_path, version):
    root = project(tmp_path)
    with pytest.raises(bump_version.BumpError) as error:
        bump_version.bump(version, "2026-11-10", root)
    assert error.value.code == 2
    assert 'APP_VERSION = "0.28.0"' in (root / "desktop_browser" / "__init__.py").read_text(encoding="utf-8")


def test_nothing_unreleased_nothing_changed(tmp_path):
    root = project(tmp_path, unreleased=False)
    assert bump_version.main(["0.29.0", "--root", str(root)]) == 3
    assert 'APP_VERSION = "0.28.0"' in (root / "desktop_browser" / "__init__.py").read_text(encoding="utf-8")


def test_dry_run_writes_nothing(tmp_path):
    root = project(tmp_path)
    assert bump_version.main(["0.29.0", "--dry-run", "--root", str(root)]) == 0
    assert 'APP_VERSION = "0.28.0"' in (root / "desktop_browser" / "__init__.py").read_text(encoding="utf-8")


def test_the_real_project_can_be_bumped_without_writing():
    current = bump_version.current_version()
    major, minor, patch = bump_version.parse(current)
    files = bump_version.bump(f"{major}.{minor + 1}.0", "2099-01-01", dry_run=True) if bump_version.unreleased_points(
        (bump_version.ROOT / "CHANGELOG.md").read_text(encoding="utf-8")) else []
    assert all(isinstance(f, str) for f in files)
