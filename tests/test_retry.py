"""«Повторить», «Повторить агентом», «Изменить», «Ответить заново» and the hint to use the agent."""
from __future__ import annotations

from types import SimpleNamespace

import pytest
from PySide6.QtCore import QSettings, Qt
from PySide6.QtGui import QKeyEvent
from PySide6.QtWidgets import QApplication

from desktop_browser import app as app_module
from desktop_browser.app import EDIT_URL, RETRY_AGENT_URL, RETRY_URL, FluxionWindow


@pytest.fixture(scope="module", autouse=True)
def qapp():
    yield QApplication.instance() or QApplication([])


class FakeWorker:
    """Answers at once: no thread, no model."""

    def __init__(self, assistant, query, history, parent=None):
        self.query = query
        for name in ("token", "meta", "failed", "finished_ok", "text_reset", "finished", "notice"):
            setattr(self, name, SimpleNamespace(connect=lambda *a: None))

    def start(self):
        pass

    def stop(self):
        pass

    def wait(self, *args):
        return True

    def deleteLater(self):
        pass


@pytest.fixture()
def window(tmp_path, monkeypatch):
    monkeypatch.setenv("FLUXION_DISABLE_WEBENGINE", "1")
    monkeypatch.setattr(app_module, "ChatWorker", FakeWorker)
    win = FluxionWindow(qsettings=QSettings(str(tmp_path / "ui.ini"), QSettings.IniFormat),
                        assistant=SimpleNamespace(use_project=True))
    sent_to_agent = []
    monkeypatch.setattr(win, "_send_agent_message",
                        lambda text, plan=False, shown=None, files=None: sent_to_agent.append(text))
    win.sent_to_agent = sent_to_agent
    win.agent_factory = object()
    yield win
    win.close()


def answer(window, text="ответ"):
    window._messages[-1]["text"] = text
    window._stop_thinking()
    window._set_generating(False)
    window.history.append({"role": "assistant", "content": text})
    window._render_messages()


def test_retry_replaces_the_last_question_and_answer(window):
    window.prompt.setText("сколько весит лист 10 мм")
    window.send_message()
    answer(window)
    html = window.messages.toHtml()
    assert RETRY_URL in html and EDIT_URL in html and RETRY_AGENT_URL in html
    window.retry_last()
    users = [e for e in window._messages if e["role"] == "user"]
    assert [e["typed"] for e in users] == ["сколько весит лист 10 мм"]  # not a second copy
    assert [h["content"] for h in window.history if h["role"] == "user"] == ["сколько весит лист 10 мм"]
    assert window.worker.query == "сколько весит лист 10 мм"


def test_retry_with_the_agent(window):
    window.prompt.setText("исправь опечатку в README")
    window.send_message()  # the agent is off: asked first
    window._send_as_chat()
    answer(window)
    window.retry_last(agent=True)
    assert window.sent_to_agent == ["исправь опечатку в README"]
    assert sum(e["role"] == "user" for e in window._messages) == 0  # the stub agent shows nothing


def test_a_request_to_change_files_suggests_the_agent(window):
    window.prompt.setText("исправь ошибку в main.py")
    window.send_message()
    assert "ask_agent" in window.banners._keys and not window._messages  # asked before sending
    window._send_as_chat()
    assert "retry_agent" not in window.banners._keys  # «как есть» was the answer: not asked again
    window.prompt.setText("сколько весит лист")
    window.send_message()


def test_edit_and_up_arrow_give_the_last_message_back(window):
    window.prompt.setText("первый вопрос")
    window.send_message()
    answer(window)
    assert window.prompt.text() == ""
    window.prompt.setFocus()
    QApplication.sendEvent(window.prompt, QKeyEvent(QKeyEvent.KeyPress, Qt.Key_Up, Qt.NoModifier))
    assert window.prompt.text() == "первый вопрос"
    window.prompt.clear()
    window._on_anchor(EDIT_URL)
    assert window.prompt.text() == "первый вопрос"


def test_no_retry_while_an_answer_is_being_written(window):
    window.prompt.setText("вопрос")
    window.send_message()
    assert window.generating
    assert RETRY_URL not in window.messages.toHtml()
    window.retry_last()
    assert sum(e["role"] == "user" for e in window._messages) == 1
