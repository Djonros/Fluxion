"""Interface language (desktop_browser/i18n.py): tr(), the catalogs of locales/,
the menu «Вид → Язык / Language» and the manual in the chosen language."""
from __future__ import annotations

import ast
import json
import os
import re
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("FLUXION_DISABLE_WEBENGINE", "1")
pytest.importorskip("PySide6")

from desktop_browser import i18n, manual  # noqa: E402

PACKAGE = Path(i18n.__file__).parent


@pytest.fixture
def english():
    i18n.set_language("en")
    yield
    i18n.set_language(i18n.DEFAULT_LANGUAGE)


def tr_keys() -> set[str]:
    keys = set()
    for path in PACKAGE.glob("*.py"):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8-sig"))):
            if (isinstance(node, ast.Call) and getattr(node.func, "id", None) == "tr" and node.args
                    and isinstance(node.args[0], ast.Constant)):
                keys.add(node.args[0].value)
    return keys


def test_russian_is_the_key_and_the_default():
    assert i18n.language() == "ru"
    assert i18n.tr("Закрыть") == "Закрыть"
    assert i18n.tr("Отчёт: {value}", value="a.xlsx") == "Отчёт: a.xlsx"


def test_english(english):
    assert i18n.language() == "en"
    assert i18n.tr("Закрыть") == "Close"
    assert i18n.tr("Отчёт: {value}", value="a.xlsx") == "Report: a.xlsx"
    assert i18n.tr("Строки, которой нет в переводе") == "Строки, которой нет в переводе"


def test_unknown_language_and_broken_file(tmp_path):
    assert i18n.set_language("xx") == "ru"
    (tmp_path / "en.json").write_text("{not json", encoding="utf-8")
    assert i18n.load_catalog("en", tmp_path) == {}


@pytest.mark.parametrize("code", [code for code in i18n.LANGUAGES if code != "ru"])
def test_every_text_of_the_window_is_translated(code):
    """Each tr("…") of desktop_browser has a text in every language, with the same {fields}."""
    catalog = i18n.load_catalog(code)
    fields = lambda text: sorted(re.findall(r"\{(\w+)", text))  # noqa: E731
    missing = [key for key in tr_keys() if key not in catalog and re.search("[а-яё]", key, re.I)]
    assert not missing, missing[:10]
    for key, text in catalog.items():
        assert fields(key) == fields(text), key
        assert text.startswith("\n") == key.startswith("\n") and text.endswith(" ") == key.endswith(" "), key


def test_language_menu_saves_the_choice(tmp_path, monkeypatch):
    from PySide6.QtCore import QSettings
    from PySide6.QtWidgets import QApplication, QMessageBox

    from desktop_browser.app import FluxionWindow

    QApplication.instance() or QApplication([])
    qsettings = QSettings(str(tmp_path / "ui.ini"), QSettings.IniFormat)
    window = FluxionWindow(qsettings=qsettings)
    assert set(window.language_actions) == set(i18n.LANGUAGES)
    assert window.language_actions["ru"].isChecked()
    asked = []
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: asked.append(a[1:3]) or QMessageBox.No)
    window.choose_language("en")
    assert i18n.saved_language(qsettings) == "en"
    assert asked == [("Interface language", "The language changes after Fluxion restarts. Restart now?")]
    assert window.language_actions["en"].isChecked() and not window.language_actions["ru"].isChecked()
    window.choose_language("ru")  # the language in use: nothing to restart
    assert i18n.saved_language(qsettings) == "ru" and len(asked) == 1
    window.close()


def test_manual_in_english_keeps_the_russian_pages_it_lacks():
    russian = manual.load_pages(code="ru")
    english = manual.load_pages(code="en")
    assert [p.key for p in english] == [p.key for p in russian]
    page = {p.key: p for p in english}["yazyk-interfeysa"]
    assert page.title == "Interface language" and page.section == "Начало работы"
    assert {p.key: p for p in russian}["yazyk-interfeysa"].title == "Язык интерфейса"
