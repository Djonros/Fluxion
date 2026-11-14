"""The spinner while the model thinks: before the first word, in a pause of the answer, between the agent's
steps; with the seconds after a few (2026-11-14: in long pauses the chat looked hung)."""
from __future__ import annotations

import time
from types import SimpleNamespace

from desktop_browser import app as app_module
from desktop_browser.app import THINK_ICON

from tests.test_retry import answer, qapp, window  # noqa: F401  (fixtures of the chat window)


def _asking(window, text="расскажи про сварку"):
    window.prompt.setText(text)
    window.send_message()
    return window._messages[-1]


def test_spinner_before_the_first_word_and_its_seconds(window):
    entry = _asking(window)
    assert entry.get("thinking") and THINK_ICON in window.messages.toHtml()
    assert window._pause_timer.isActive()
    window._think_since = time.monotonic() - 12
    window._on_pause_tick()
    assert "12 с" in window.messages.toPlainText()


def test_a_pause_in_the_answer_brings_the_spinner_back(window):
    _asking(window)
    window._on_token("Сварка — это")
    entry = window._messages[-1]
    assert not entry.get("thinking") and not entry.get("waiting")
    window._on_pause_tick()  # just now: no spinner
    assert not entry.get("waiting")
    window._last_activity = time.monotonic() - (app_module.THINK_PAUSE_S + 0.5)
    window._on_pause_tick()
    assert entry.get("waiting")
    text = window.messages.toPlainText()
    assert "Сварка — это" in text and THINK_ICON in window.messages.toHtml()  # after the text, not instead
    window._on_token(" соединение")
    assert not entry.get("waiting") and "Сварка — это соединение" in window.messages.toPlainText()


def test_between_the_agent_steps(window):
    _asking(window, "сколько весит лист")
    window._on_chat_agent_step(SimpleNamespace(tool_name="read_file", tool_args="main.py", observation="x = 1",
                                               thought="Читаю файл."))
    entry = window._messages[-1]
    assert entry.get("waiting") and "main.py" in entry["text"]
    window._on_chat_agent_step(SimpleNamespace(tool_name="finish", tool_args="", observation="", thought=""))
    assert not entry.get("waiting")


def test_the_end_and_stop_put_the_spinner_away(window):
    _asking(window)
    window._on_token("ответ")
    window._last_activity = time.monotonic() - 10
    window._on_pause_tick()
    assert window._messages[-1].get("waiting")
    window._set_generating(False)  # done, failed or «Стоп»
    assert not window._messages[-1].get("waiting") and not window._pause_timer.isActive()
    assert not window._think_timer.isActive()
    window._render_messages()
    assert THINK_ICON not in window.messages.toHtml()


def test_no_spinner_while_idle(window):
    _asking(window)
    answer(window)
    window._last_activity = time.monotonic() - 100
    window._on_pause_tick()
    assert not window._messages[-1].get("waiting")


def test_the_same_step_again_is_one_line(window):
    _asking(window, "сколько весит лист")
    step = SimpleNamespace(tool_name="write_file", tool_args="docs/a.md\n# A\n", thought="Create the file.",
                           observation="No change: docs/a.md already has exactly this content")
    for _ in range(5):
        window._on_chat_agent_step(step)
    text = window._messages[-1]["text"]
    assert text.count("write_file docs/a.md") == 1 and text.endswith("(повторено 5 раз)")
    window._on_chat_agent_step(SimpleNamespace(tool_name="read_file", tool_args="b.py", thought="", observation="x"))
    assert window._messages[-1]["text"].count("(повторено 5 раз)") == 1
