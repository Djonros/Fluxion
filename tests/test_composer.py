"""The chat composer of the redesign: «+», attached files, the edit menu, the tool menus, «Думаю…»."""
from __future__ import annotations

from pathlib import Path

import pytest
from PySide6.QtCore import QMimeData, QSettings, QUrl
from PySide6.QtGui import QImage
from PySide6.QtWidgets import QApplication

from desktop_browser import attachments, icons
from desktop_browser.app import EDIT_MENU, THINK_PHRASES, FluxionWindow, with_attachments
from desktop_browser.chat_input import ChatInput


@pytest.fixture(scope="module", autouse=True)
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app


@pytest.fixture()
def window(tmp_path, monkeypatch):
    monkeypatch.setenv("FLUXION_DISABLE_WEBENGINE", "1")
    win = FluxionWindow(qsettings=QSettings(str(tmp_path / "ui.ini"), QSettings.IniFormat))
    yield win
    win.close()


def test_every_outline_icon_renders():
    for name in icons.names():
        assert not icons.line(name, "#94A3B8").pixmap(20, 20).isNull(), name
    frame = icons.spinner_frame(5)
    assert not frame.isNull() and frame.hasAlphaChannel()


def test_attachment_text_of_a_plain_file_is_cut(tmp_path):
    path = tmp_path / "log.txt"
    path.write_text("строка\n" * 20000, encoding="cp1251")
    text = attachments.read_text(path)
    assert text.startswith("строка") and len(text) < attachments.TEXT_LIMIT + 200
    assert "обрезан" in text


def test_a_binary_file_is_not_attached_as_text(tmp_path):
    path = tmp_path / "blob.bin"
    path.write_bytes(b"\x00\x01\x02" * 100)
    with pytest.raises(attachments.AttachmentError):
        attachments.read_text(path)


def test_with_attachments_puts_files_before_the_question(tmp_path):
    good = tmp_path / "main.py"
    good.write_text("print('hi')", encoding="utf-8")
    bad = tmp_path / "x.bin"
    bad.write_bytes(b"\x00" * 10)
    notes = []
    files = [attachments.describe(good), attachments.describe(bad)]
    query = with_attachments("Что делает код?", files, "http://localhost:1", notes.append)
    assert "print('hi')" in query and query.endswith("Что делает код?")
    assert len(notes) == 1 and "x.bin" in notes[0]


def test_dropped_files_and_a_pasted_picture_are_attached(tmp_path):
    field = ChatInput()
    got = []
    field.filesAttached.connect(got.append)
    path = tmp_path / "a.txt"
    path.write_text("x", encoding="utf-8")
    data = QMimeData()
    data.setUrls([QUrl.fromLocalFile(str(path))])
    field.insertFromMimeData(data)
    picture = QMimeData()
    image = QImage(4, 4, QImage.Format_ARGB32)
    image.fill(0xFF00FF00)
    picture.setImageData(image)
    field.insertFromMimeData(picture)
    assert [Path(p) for p in got[0]] == [path] and got[1][0].endswith(".png")
    assert field.text() == ""


def test_chips_show_files_and_modes(window, tmp_path):
    path = tmp_path / "ТЗ.txt"
    path.write_text("техническое задание", encoding="utf-8")
    window.attach_files([str(path), str(path)])  # the same file once
    window.chat_plan.setChecked(True)
    assert len(window._attachments) == 1
    assert window._chips_flow.count() == 3  # two chips and the stretch
    files = window._take_attachments()
    window.chat_plan.setChecked(False)
    assert files[0]["name"] == "ТЗ.txt" and window._chips_flow.count() == 0


def test_plus_menu_switches_the_modes(window):
    window._fill_plus_menu()
    plan = next(a for a in window.plus_menu.actions() if a.text() == window.chat_plan.text())
    plan.trigger()
    assert window.chat_plan.isChecked()
    texts = [a.text() for a in window.plus_menu.actions()]
    assert any(t.startswith("Прикрепить файл") for t in texts)


def test_edit_menu_has_paste_and_tools_are_marked_pro(window):
    menus = {a.text().replace("&", ""): a.menu() for a in window.menuBar().actions() if a.menu()}
    edit = [a.text() for a in menus["Правка"].actions()]
    assert [row[1] for row in EDIT_MENU if row] == [t for t in edit if t]
    assert "Инструменты" not in menus
    production = [a.text() for a in menus["Производство"].actions() if a.text()]
    assert production and all(t.endswith("\tPRO") for t in production)
    assert any(t.startswith("Пакет на лазер") for t in production)


def test_one_thinking_phrase_per_question(window):
    window._messages = [{"role": "assistant", "text": "", "meta": ""}]
    window._start_thinking()
    first = window._messages[-1]["phrase"]
    window._start_thinking()  # a retry of the same answer
    assert window._messages[-1]["phrase"] == first
    window._stop_thinking()
    window._messages.append({"role": "assistant", "text": "", "meta": ""})
    window._start_thinking()
    assert window._messages[-1]["phrase"] == (first + 1) % len(THINK_PHRASES)
    window._stop_thinking()
