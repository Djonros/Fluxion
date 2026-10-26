"""«Инструкция по инструментам» (desktop_browser/manual.py): the pages of
desktop_browser/manual, the window, its tools and the «?» of a tool window."""
from __future__ import annotations

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("FLUXION_DISABLE_WEBENGINE", "1")
pytest.importorskip("PySide6")

from desktop_browser import manual  # noqa: E402

PARTS = ("## Для чего", "## Как запустить", "## Пошагово", "## Что получится", "## Если не получается")


@pytest.fixture(scope="module")
def app():
    from PySide6.QtWidgets import QApplication

    return QApplication.instance() or QApplication([])


def test_every_page_follows_the_template():
    pages = manual.load_pages()
    assert len(pages) >= 15
    for page in pages:
        assert page.title and page.section, page.key
        assert all(part in page.text for part in PARTS), page.key
        assert "<!--" not in page.text and not page.text.startswith("#  ")
    keys = [p.key for p in pages]
    assert len(keys) == len(set(keys))
    assert {"otchet-po-sborke", "arhiv-kd", "normokontrol-autocad", "moy-stil"} <= set(keys)


def test_pro_marks_match_the_licence():
    """A page marked Pro is a tool behind a Pro feature, and the Pro tools are marked."""
    pages = {p.key: p for p in manual.load_pages()}
    for key in ("otchet-po-sborke", "ispravit-v-modeli", "paket-na-lazer", "normokontrol-autocad", "arhiv-kd",
                "moy-stil", "doobuchenie"):
        assert pages[key].pro, key
    for key in ("detal-v-kompas", "katalog-materialov", "poisk-po-dokumentam", "chat", "dataset-iz-dokumentov"):
        assert not pages[key].pro, key


def test_every_tool_of_the_pages_opens_in_the_window(tmp_path, app):
    from PySide6.QtCore import QSettings

    from desktop_browser.app import FluxionWindow

    window = FluxionWindow(qsettings=QSettings(str(tmp_path / "ui.ini"), QSettings.IniFormat))
    tools = window.manual_tools()
    for page in manual.load_pages():
        assert not page.tool or page.tool in tools, (page.key, page.tool)
    window.close()


def test_parse_search_and_open_a_tool(tmp_path, app):
    (tmp_path / "20-b.md").write_text("# Второй\n<!-- раздел: КОМПАС-3D | инструмент: cad | pro -->\n"
                                      "## Для чего\nпро ёлку", encoding="utf-8")
    (tmp_path / "10-a.md").write_text("# Первый\n<!-- раздел: Начало | инструмент: - -->\nтекст", encoding="utf-8")
    pages = manual.load_pages(tmp_path)
    assert [(p.key, p.section, p.tool, p.pro) for p in pages] == [("a", "Начало", "", False),
                                                                  ("b", "КОМПАС-3D", "cad", True)]
    assert manual.matches(pages[1], "елку компас") and not manual.matches(pages[0], "елку")
    opened = []
    dialog = manual.ManualDialog(page="b", tools={"cad": lambda: opened.append("cad")}, pages=pages)
    assert dialog.current.key == "b" and "Pro — функция Fluxion Pro" in dialog.view.toPlainText()
    assert dialog.open_button.isEnabled()
    dialog.open_button.click()
    assert opened == ["cad"]
    dialog = manual.ManualDialog(page="a", pages=pages)
    assert dialog.current.key == "a" and not dialog.open_button.isVisibleTo(dialog)
    dialog.search.setText("ёлку")
    assert dialog.current.key == "b" and dialog.tree.topLevelItemCount() == 1
    dialog.search.setText("нет такого")
    assert "Ничего не найдено" in dialog.view.toPlainText()


def test_help_button_opens_the_page_of_its_tool(app, monkeypatch):
    from PySide6.QtWidgets import QDialog

    shown = []
    monkeypatch.setattr(manual, "open_manual", lambda parent, page="", tools=None: shown.append(page))
    dialog = QDialog()
    button = manual.help_button(dialog, "arhiv-kd")
    button.click()
    assert shown == ["arhiv-kd"] and button.text() == "?"


def test_tool_windows_have_the_help_button(tmp_path, app):
    from PySide6.QtWidgets import QPushButton

    from desktop_browser.archive_dialog import ArchiveDialog
    from desktop_browser.writing_style_dialog import WritingStyleDialog

    for dialog in (ArchiveDialog(tmp_path / "a"), WritingStyleDialog(None, tmp_path / "w")):
        assert any(b.text() == "?" for b in dialog.findChildren(QPushButton)), type(dialog).__name__
