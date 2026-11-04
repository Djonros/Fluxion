"""«Fluxion везде» (desktop_browser/everywhere.py): the hotkey text, the requests, the quick window."""
from __future__ import annotations

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")

from PySide6.QtCore import QSettings  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from desktop_browser import everywhere  # noqa: E402

COLORS = {"bg1": "#0F1520", "bg2": "#151D2C", "border": "#1E293B", "text1": "#E6EAF2", "text2": "#94A3B8",
          "accent": "#00E5FF", "primary": "#7B2FFF"}


@pytest.fixture(scope="module", autouse=True)
def qapp():
    yield QApplication.instance() or QApplication([])


class FakeBackend:
    def __init__(self, reply="Готовый текст."):
        self.reply = reply
        self.messages = None

    def stream(self, messages, gen=None):
        self.messages = messages
        first, _, rest = self.reply.partition(" ")
        yield from (first + " ", rest)


def _wait(qapp, window, seconds=5.0):
    import time

    end = time.monotonic() + seconds
    while window.worker is not None and time.monotonic() < end:
        qapp.processEvents()
        time.sleep(0.01)
    qapp.processEvents()


def test_hotkey_text_is_understood():
    assert everywhere.parse_hotkey("Ctrl+Alt+Space") == (0x2 | 0x1, 0x20)
    assert everywhere.parse_hotkey("Ctrl+Shift+F") == (0x2 | 0x4, ord("F"))
    assert everywhere.parse_hotkey("Alt+F2") == (0x1, 0x71)
    assert everywhere.parse_hotkey("Space") is None  # a bare key would be taken from every program
    assert everywhere.parse_hotkey("Ctrl+Йцук") is None


def test_requests_of_the_buttons():
    rewrite = everywhere.build_messages("rewrite", "текст")
    assert rewrite[0]["role"] == "system" and everywhere.ANSWER_ONLY in rewrite[0]["content"]
    assert rewrite[1] == {"role": "user", "content": "текст"}
    assert "немецкий" in everywhere.build_messages("translate", "x", language="немецкий")[0]["content"]
    assert "вежливее" in everywhere.build_messages("rewrite", "x", own="вежливее")[0]["content"]
    assert "МАНЕРА" in everywhere.build_messages("style", "x", style="МАНЕРА")[0]["content"]
    assert len(everywhere.build_messages("rewrite", "я" * 20000)[1]["content"]) == everywhere.MAX_TEXT


def test_quick_window_answers_and_gives_the_answer_out(qapp):
    backend = FakeBackend("Прошу закупить лист.")
    window = everywhere.QuickWindow(lambda: backend, lambda text: "", COLORS)
    window.open_with("прошу закупить лист", 0, "Ctrl+Alt+Space")
    assert window.isVisible() and not window.note.isVisible()
    assert window.run("shorter")
    _wait(qapp, window)
    assert window.answer == "Прошу закупить лист."
    assert backend.messages[1]["content"] == "прошу закупить лист"
    assert window.copy_button.isEnabled() and window.chat_button.isEnabled()
    assert not window.paste_button.isEnabled()  # no program to paste into in a test
    window._on_copy()
    assert QApplication.clipboard().text() == "Прошу закупить лист."
    sent = []
    window.to_chat.connect(sent.append)
    window._on_chat()
    assert sent == ["прошу закупить лист"] and not window.isVisible()


def test_no_selection_says_how_to_go_on(qapp):
    window = everywhere.QuickWindow(lambda: FakeBackend(), lambda text: "", COLORS)
    window.open_with("", 0)
    assert window.note.isVisible() and "администратора" in window.note.text()
    assert not window.run("rewrite")  # nothing to work on yet
    window.source.setPlainText("вставленный текст")
    assert window.run("rewrite")
    _wait(qapp, window)
    window.hide()


def test_my_style_without_pro_and_no_model(qapp):
    window = everywhere.QuickWindow(lambda: None, lambda text: "", COLORS)
    window.open_with("текст", 0)
    assert not window.run("style")
    assert "Pro" in window.result.toPlainText()
    window = everywhere.QuickWindow(lambda: None, lambda text: "МАНЕРА", COLORS)
    window.open_with("текст", 0)
    assert not window.run("rewrite")
    assert "Модель не подключена" in window.result.toPlainText()


def test_hotkey_off_windows_or_bad_keys_is_not_registered(monkeypatch):
    hotkey = everywhere.GlobalHotkey()
    assert not hotkey.register("Space")
    monkeypatch.setattr(everywhere.sys, "platform", "linux")
    assert not hotkey.register("Ctrl+Alt+Space")
    assert everywhere.capture_selection() == ""


def test_in_chat_puts_the_text_into_the_input(qapp, tmp_path, monkeypatch):
    monkeypatch.setenv("FLUXION_DISABLE_WEBENGINE", "1")
    from desktop_browser.app import FluxionWindow

    window = FluxionWindow(qsettings=QSettings(str(tmp_path / "ui.ini"), QSettings.IniFormat))
    window._quick_to_chat("выделенный текст")
    assert window.prompt.text() == "выделенный текст"
    assert window.everywhere_on.isChecked()  # on by default; the hotkey itself only from main()
    assert getattr(window, "everywhere", None) is None
    window.close()
