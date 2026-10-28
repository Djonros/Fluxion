"""Selected text and rows stay readable in both themes: the contrast of the text
on the selection is at least 4.5:1 (WCAG AA). White on cyan was about 1.3:1."""
from __future__ import annotations

import re

import pytest
from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QApplication


def _luminance(color: str) -> float:
    value = color.lstrip("#")
    channels = [int(value[i:i + 2], 16) / 255 for i in (0, 2, 4)]
    channels = [c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4 for c in channels]
    return 0.2126 * channels[0] + 0.7152 * channels[1] + 0.0722 * channels[2]


def contrast(a: str, b: str) -> float:
    light, dark = sorted((_luminance(a), _luminance(b)), reverse=True)
    return (light + 0.05) / (dark + 0.05)


@pytest.fixture(scope="module", autouse=True)
def qapp():
    yield QApplication.instance() or QApplication([])


@pytest.mark.parametrize("theme_name", ["dark", "light"])
def test_the_program_selection_is_readable(tmp_path, monkeypatch, theme_name):
    from cli.theme import DARK, LIGHT
    from desktop_browser.app import FluxionWindow

    monkeypatch.setenv("FLUXION_DISABLE_WEBENGINE", "1")
    window = FluxionWindow(qsettings=QSettings(str(tmp_path / "s.ini"), QSettings.IniFormat))
    window.apply_theme(DARK if theme_name == "dark" else LIGHT)
    sheet = window.styleSheet()
    pairs = re.findall(r"selection-background-color:\s*(#[0-9A-Fa-f]{6});\s*selection-color:\s*(#[0-9A-Fa-f]{6})", sheet)
    assert pairs and sheet.count("selection-background-color") == len(pairs)  # each one sets the text colour too
    for background, text in pairs:
        assert contrast(background, text) >= 4.5, (background, text)
    window.close()

