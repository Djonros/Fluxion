"""The chat input of several lines, «Забыть проект», the address bar without localhost:8080
and the window of «Деталь в КОМПАС» that fits the screen."""
from __future__ import annotations

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("FLUXION_DISABLE_WEBENGINE", "1")
pytest.importorskip("PySide6")

from PySide6.QtCore import QEvent, QSettings, Qt  # noqa: E402
from PySide6.QtGui import QKeyEvent  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


def press(widget, key, modifiers=Qt.NoModifier, text=""):
    widget.keyPressEvent(QKeyEvent(QEvent.KeyPress, key, modifiers, text))


# ── the chat input ──────────────────────────────────────────────────────────

def test_enter_sends_shift_enter_breaks_the_line(app):
    from desktop_browser.chat_input import ChatInput

    field = ChatInput()
    sent, agent = [], []
    field.returnPressed.connect(lambda: sent.append(field.text()))
    field.agentRequested.connect(lambda: agent.append(field.text()))
    field.setText("первая строка")
    press(field, Qt.Key_Return, Qt.ShiftModifier)
    field.insertPlainText("вторая")
    assert field.text() == "первая строка\nвторая" and not sent
    press(field, Qt.Key_Return)
    assert sent == ["первая строка\nвторая"]
    press(field, Qt.Key_Enter, Qt.ControlModifier)
    assert agent == ["первая строка\nвторая"]


def test_the_field_grows_then_scrolls_and_keeps_a_long_paste(app):
    from desktop_browser.chat_input import ChatInput

    field = ChatInput()
    field.resize(400, field.height())
    field.show()
    one = field.height()
    field.setText("\n".join(f"строка {i}" for i in range(3)))
    app.processEvents()
    three = field.height()
    field.setText("\n".join(f"строка {i}" for i in range(30)))
    app.processEvents()
    many = field.height()
    assert one < three < many
    field.setText("\n".join(f"строка {i}" for i in range(60)))
    app.processEvents()
    assert field.height() == many                      # at most MAX_LINES, then a scroll bar
    assert field.verticalScrollBarPolicy() == Qt.ScrollBarAsNeeded
    long_text = "x" * 50_000                           # QLineEdit cut it at 32 767
    field.setText(long_text)
    assert len(field.text()) == 50_000
    field.close()


# ── the window ──────────────────────────────────────────────────────────────

class _Rag:
    def __init__(self):
        self.searched = 0

    def search(self, query, top_k=5):
        self.searched += 1
        return []

    def count(self):
        return 12


@pytest.fixture
def window(app, tmp_path, monkeypatch):
    from desktop_browser import searxng
    from desktop_browser.app import FluxionWindow

    monkeypatch.setattr(searxng, "searxng_enabled", lambda: False)
    monkeypatch.setattr(searxng, "set_searxng_enabled", lambda enabled: None)
    qs = QSettings(str(tmp_path / "ui.ini"), QSettings.IniFormat)
    qs.setValue("project_path", str(tmp_path))
    w = FluxionWindow(qsettings=qs, rag_service=_Rag())
    yield w
    w.close()


def test_the_prompt_is_the_multi_line_field(window):
    from desktop_browser.chat_input import ChatInput

    assert isinstance(window.prompt, ChatInput)
    assert "Shift+Enter" in window.prompt.toolTip()


def test_forget_project(window, tmp_path, monkeypatch):
    window._messages = [{"role": "user", "text": "старое"}]
    window.history = [{"role": "user", "content": "старое"}]
    window.forget_project()
    assert window.history == [] and len(window._messages) == 1 and "Проект забыт" in window._messages[0]["text"]
    assert window.qsettings.value("project_path") == "" and window.project_path.text() == ""
    assert window._project_forgotten() and window.index_chip.text() == "index: off"
    # the agent has no folder now: it says so instead of working in some other folder
    window._send_agent_message("найди ошибку")
    assert "Проект забыт" in window._messages[-1]["text"]
    # picking a folder brings the project back
    from PySide6.QtWidgets import QFileDialog

    monkeypatch.setattr(QFileDialog, "getExistingDirectory", staticmethod(lambda *a, **k: str(tmp_path)))
    window._pick_project_folder()
    assert not window._project_forgotten() and window.index_chip.text() == "index: 12"


def test_the_assistant_does_not_search_a_forgotten_project():
    from core.config import Settings
    from orchestrator.assistant import Assistant
    from orchestrator.router import Strategy

    rag = _Rag()
    assistant = Assistant(backend=None, settings=Settings(), rag_service=rag)
    assistant._prepare("что делает функция parse в проекте", force_strategy=Strategy.RAG)
    assert rag.searched == 1
    assistant.use_project = False
    assistant._prepare("что делает функция parse в проекте", force_strategy=Strategy.RAG)
    assert rag.searched == 1


def test_the_address_bar_does_not_ask_localhost_8080(window):
    """SearXNG off (the default): the browser searches DuckDuckGo; on — the configured SearXNG."""
    assert window._search_base == ""
    window._apply_searxng_choice(True)
    assert window._search_base == window._searxng_url
    window._apply_searxng_choice(False)
    assert window._search_base == ""
    panel = window.web_panel
    if hasattr(panel, "search_base"):
        assert panel.search_base == ""


# ── «Деталь в КОМПАС» ───────────────────────────────────────────────────────

def test_the_part_window_fits_the_screen(app):
    from PySide6.QtGui import QGuiApplication

    from desktop_browser.text_dialog import SCREEN_SHARE, TextDialog

    label = "Опишите деталь, размеры в мм и материал. " * 40
    dialog = TextDialog("Деталь в КОМПАС-3D", label)
    area = QGuiApplication.primaryScreen().availableGeometry()
    assert dialog.width() <= area.width() * SCREEN_SHARE + 1
    assert dialog.height() <= area.height() * SCREEN_SHARE + 1
    assert dialog.maximumHeight() <= area.height() * SCREEN_SHARE + 1
    dialog.editor.setPlainText("втулка 30 на 20")
    assert dialog.text() == "втулка 30 на 20"
