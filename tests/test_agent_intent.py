"""The chat asks to switch the agent on when a message needs files changed (2026-11-14: the agent was
off, a project skeleton went to the chat model and Fluxion «worked idle»)."""
from __future__ import annotations

import pytest

from desktop_browser.intent import needs_files

SCREENSHOT = """Нужно создать директории:
- backend/
- frontend/
- infra/
- docs/
- scripts/

Добавь:
- .gitignore
- README.md
- docs/architecture.md с кратким описанием структуры.

Не добавляй бизнес-логику."""


@pytest.mark.parametrize("text", [
    SCREENSHOT,
    "прочитай main.py и исправь баг",
    "Исправь ошибку в функции parse",
    "создай файл utils.py с функцией slugify",
    "добавь в README раздел про установку",
    "переименуй класс Parser в Reader",
    "удали из config.yaml ключ debug",
    "сделай структуру проекта для FastAPI",
    "напиши тесты для calc.py",
    "отрефактори модуль rag/indexer.py",
    "надо поменять порт в docker-compose.yml на 8080",
    "Create a .gitignore for a Python project",
    "fix the bug in app/server.py",
    "add a function to utils that parses dates",
    "rename the folder src/ to app/",
])
def test_requests_to_change_files(text):
    assert needs_files(text)


@pytest.mark.parametrize("text", [
    "привет",
    "Объясни этот проект",
    "Найди возможную ошибку",  # a look, not a change
    "как создать файл в Python?",
    "что такое .gitignore",
    "почему main.py падает на старте?",
    "объясни, как добавить функцию в модуль",
    "покажи пример кода для чтения CSV",
    "сколько будет 2+2",
    "напиши письмо поставщику о сроках",  # writing, but no file
    "/компас втулка 30 на 20 длиной 50",
    "How do I create a virtualenv?",
    "what does this function do?",
    "explain the structure of the project",
    "",
])
def test_questions_and_other_messages(text):
    assert not needs_files(text)


# ── the window: asked before sending ────────────────────────────────────────

from tests.test_retry import answer, qapp, window  # noqa: E402,F401  (fixtures of the chat window)


def test_the_skeleton_is_not_sent_without_the_agent(window):
    window.prompt.setText(SCREENSHOT)
    window.send_message()
    assert "ask_agent" in window.banners._keys
    assert not window._messages and window.sent_to_agent == []  # nothing went anywhere yet
    assert window.prompt.text().strip() == SCREENSHOT.strip()  # the text stays in the field


def test_switch_the_agent_on_and_send(window):
    window.prompt.setText("создай папки backend и frontend")
    window.send_message()
    window._send_with_agent()
    assert window.chat_agent_files.isChecked()
    assert window.sent_to_agent == ["создай папки backend и frontend"]
    assert "ask_agent" not in window.banners._keys


def test_send_to_the_chat_as_it_is(window):
    window.prompt.setText("создай папки backend и frontend")
    window.send_message()
    window._send_as_chat()
    assert window.sent_to_agent == [] and not window.chat_agent_files.isChecked()
    assert [e["typed"] for e in window._messages if e["role"] == "user"] == ["создай папки backend и frontend"]


def test_never_ask_again(window):
    window.prompt.setText("создай папки backend и frontend")
    window.send_message()
    window._never_ask_agent()
    assert not window._ask_agent_first()
    answer(window)
    window.prompt.setText("добавь файл .gitignore")
    window.send_message()
    assert "ask_agent" not in window.banners._keys
    assert len([e for e in window._messages if e["role"] == "user"]) == 2


def test_no_question_with_the_agent_on_or_for_a_question(window):
    window.chat_agent_files.setChecked(True)
    window.prompt.setText("создай папку docs")
    window.send_message()
    assert window.sent_to_agent == ["создай папку docs"] and "ask_agent" not in window.banners._keys
    window.chat_agent_files.setChecked(False)
    window.prompt.setText("как создать папку в Python?")
    window.send_message()
    assert "ask_agent" not in window.banners._keys


def test_the_setting_switch(window):
    window.agent_hint.setChecked(False)
    assert not window._ask_agent_first()
    window.agent_hint.setChecked(True)
    assert window._ask_agent_first()
