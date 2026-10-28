"""The history of chats: the list in the sidebar, going back to a chat, renaming,
search and deleting (desktop_browser.memory and the window)."""
from __future__ import annotations

import json
import time

import pytest
from PySide6.QtCore import QSettings, Qt
from PySide6.QtWidgets import QApplication, QMessageBox

from desktop_browser import memory
from desktop_browser.app import FluxionWindow
from desktop_browser.memory import (
    clear_sessions,
    day_group,
    delete_sessions,
    load_sessions,
    make_session,
    rename_session,
    save_session,
    search_sessions,
)


@pytest.fixture(scope="module", autouse=True)
def qapp():
    yield QApplication.instance() or QApplication([])


@pytest.fixture()
def make_window(tmp_path, monkeypatch):
    monkeypatch.setenv("FLUXION_DISABLE_WEBENGINE", "1")
    windows = []

    def factory(**kwargs):
        ini = tmp_path / f"settings_{len(windows)}.ini"
        window = FluxionWindow(qsettings=QSettings(str(ini), QSettings.IniFormat), **kwargs)
        windows.append(window)
        return window

    yield factory
    for window in windows:
        window.close()


@pytest.fixture()
def answer(monkeypatch):
    """What the user answers in QMessageBox.question; the questions asked are kept."""
    asked = []
    state = {"answer": QMessageBox.Yes}

    def question(_parent, _title, text, *args, **kwargs):
        asked.append(text)
        return state["answer"]

    monkeypatch.setattr("desktop_browser.app.QMessageBox.question", question)
    state["asked"] = asked
    return state


def _chat(text, **extra):
    session = make_session([{"role": "user", "content": text}, {"role": "assistant", "content": "ответ: " + text}])
    session.update(extra)
    return session


def _titles(window):
    return [window.history_list.item(i).text() for i in range(window.history_list.count())]


def _item(window, title):
    return next(window.history_list.item(i) for i in range(window.history_list.count())
                if window.history_list.item(i).text() == title)


# ── memory ──────────────────────────────────────────────────────────────


def test_a_chat_keeps_sources_mode_model_and_folder():
    session = _chat("что делает main?", mode={"agent_files": True, "plan": False}, model="qwen", project="C:/proj",
                    view=[{"role": "user", "text": "что делает main?", "meta": ""},
                          {"role": "assistant", "text": "Запускает.", "meta": "RAG", "thinking": True,
                           "sources": [{"n": 1, "file": "C:/proj/main.py", "label": "main.py"}, {"bad": 1}],
                           "sources_title": "Источники"}])
    save_session(session)
    loaded = load_sessions()[0]
    assert loaded["mode"] == {"agent_files": True, "plan": False}
    assert loaded["model"] == "qwen" and loaded["project"] == "C:/proj"
    assert loaded["view"][1] == {"role": "assistant", "text": "Запускает.", "meta": "RAG",
                                 "sources": [{"file": "C:/proj/main.py", "label": "main.py", "n": 1}],
                                 "sources_title": "Источники"}
    assert not memory.chats_path().with_name("chats.json.tmp").exists()


def test_the_file_of_an_older_version_is_read():
    memory.chats_path().write_text(json.dumps(
        [{"id": "a", "title": "старый", "updated": 5, "messages": [{"role": "user", "content": "старый"}]}]),
        encoding="utf-8")
    loaded = load_sessions()
    assert loaded[0]["title"] == "старый" and loaded[0]["created"] == 5
    assert loaded[0]["mode"] == {"agent_files": False, "plan": False} and "view" not in loaded[0]


def test_the_name_is_the_first_question_and_a_new_name_stays():
    session = make_session([{"role": "user", "content": "  как   собрать\nпроект " + "x" * 80}])
    assert session["title"].startswith("как собрать проект") and len(session["title"]) == 60
    assert make_session([])["title"] == "Чат"
    save_session(session)
    rename_session(session["id"], "  Сборка  ")
    # the window saves the chat again with the title from the first question
    save_session(dict(session, title="как собрать", updated=time.time()))
    assert load_sessions()[0]["title"] == "Сборка"
    rename_session(session["id"], "")  # an empty name: the first question again
    assert load_sessions()[0]["title"].startswith("как собрать проект")


def test_delete_several_and_clear_all():
    a, b, c = _chat("один"), _chat("два"), _chat("три")
    for s in (a, b, c):
        save_session(s)
    assert [s["id"] for s in delete_sessions([a["id"], c["id"]])] == [b["id"]]
    memory.history_path().write_text('[{"role": "user", "content": "старый"}]', encoding="utf-8")
    assert clear_sessions() == []
    assert load_sessions() == []  # the old single-chat file does not come back


def test_search_looks_in_names_and_messages():
    chats = [_chat("Сборка проекта"), _chat("перевод письма")]
    assert search_sessions(chats, "") == chats
    assert search_sessions(chats, "СБОРКА") == chats[:1]
    assert search_sessions(chats, "ответ письма") == chats[1:]
    assert search_sessions(chats, "нет такого") == []


def test_day_groups():
    now = time.mktime((2026, 10, 28, 12, 0, 0, 0, 0, -1))
    assert day_group(now - 3600, now) == "today"
    assert day_group(now - 86400, now) == "yesterday"
    assert day_group(now - 5 * 86400, now) == "earlier"


# ── the window ──────────────────────────────────────────────────────────


def test_the_sidebar_lists_chats_newest_first(make_window):
    save_session(_chat("давний", updated=time.time() - 10 * 86400))
    save_session(_chat("свежий"))
    window = make_window()
    assert _titles(window) == ["Сегодня", "свежий", "Раньше", "давний"]
    assert not window.history_list.item(0).flags() & Qt.ItemIsSelectable
    window.history_search.setText("давн")
    assert _titles(window) == ["Раньше", "давний"]
    window.history_search.setText("ничего такого")
    assert _titles(window) == ["Ничего не найдено"]


def test_a_click_opens_the_chat_with_sources_and_chips(make_window):
    save_session(_chat("про проект", mode={"agent_files": True, "plan": True},
                      view=[{"role": "user", "text": "про проект", "meta": ""},
                            {"role": "assistant", "text": "ответ: про проект", "meta": "",
                             "sources": [{"n": 1, "file": "C:/p/readme.md", "label": "readme.md"}]}]))
    save_session(_chat("другой"))
    window = make_window(agent_factory=lambda *a, **k: None)
    assert window.history[0]["content"] == "другой"
    assert not window.chat_agent_files.isChecked()  # at start the chips stay as chosen last
    window._on_history_clicked(_item(window, "про проект"))
    assert window.history[0]["content"] == "про проект"
    assert "readme.md" in window.messages.toHtml()
    assert window.chat_agent_files.isChecked() and window.chat_plan.isChecked()
    # the chat goes on: the next save updates the same chat, not a new one
    window.history.append({"role": "user", "content": "дальше"})
    window._save_memory()
    assert len(load_sessions()) == 2
    assert load_sessions()[-1]["messages"][-1]["content"] == "дальше"
    assert _titles(window)[1] == "про проект"
    # without the engine the agent chip is not turned on (it would only complain into the chat)
    other = make_window()  # opens «про проект», the latest chat, without its chips
    other._open_chat(load_sessions()[0]["id"])
    other._open_chat(load_sessions()[-1]["id"])
    assert not other.chat_agent_files.isChecked() and other.chat_plan.isChecked()
    assert all("Движок" not in m["text"] for m in other._messages)


def test_opening_a_chat_of_another_folder_asks_to_open_it(make_window, answer, tmp_path):
    folder = tmp_path / "proj"
    folder.mkdir()
    save_session(_chat("в папке", project=str(folder)))
    save_session(_chat("без папки"))
    window = make_window()
    answer["answer"] = QMessageBox.No
    window._open_chat(load_sessions()[0]["id"])
    assert answer["asked"] and window.project_path.text() == ""
    window._open_chat(load_sessions()[1]["id"])
    answer["answer"] = QMessageBox.Yes
    window._open_chat(load_sessions()[0]["id"])
    assert window.project_path.text() == str(folder) and not window._project_forgotten()


def test_no_switch_while_answering(make_window):
    save_session(_chat("первый"))
    save_session(_chat("второй"))
    window = make_window()
    window.generating = True
    window._open_chat(load_sessions()[0]["id"])
    assert window.history[0]["content"] == "второй"
    window.generating = False


def test_delete_with_confirmation_and_several_at_once(make_window, answer):
    for text in ("один", "два", "три"):
        save_session(_chat(text))
    window = make_window()
    answer["answer"] = QMessageBox.No
    _item(window, "один").setSelected(True)
    window._delete_selected_chats()
    assert len(load_sessions()) == 3
    answer["answer"] = QMessageBox.Yes
    window.history_list.clearSelection()
    _item(window, "один").setSelected(True)
    _item(window, "три").setSelected(True)  # the open chat
    window._delete_selected_chats()
    assert "2" in answer["asked"][-1]
    assert [s["title"] for s in load_sessions()] == ["два"]
    assert window.history == [] and "Добро пожаловать" in window.messages.toHtml()
    assert _titles(window) == ["Сегодня", "два"]


def test_clear_all_history(make_window, answer):
    save_session(_chat("один"))
    save_session(_chat("два"))
    window = make_window()
    window._clear_history()
    assert load_sessions() == [] and window.history == []
    assert _titles(window) == ["Пока нет чатов"]


def test_rename_from_the_list(make_window, monkeypatch):
    save_session(_chat("первый вопрос"))
    window = make_window()
    monkeypatch.setattr("desktop_browser.app.QInputDialog.getText", lambda *a, **k: ("Мой чат", True))
    window._rename_chat(window._session_id)
    assert _titles(window) == ["Сегодня", "Мой чат"]
    window.history.append({"role": "user", "content": "ещё"})
    window._save_memory()
    assert load_sessions()[0]["title"] == "Мой чат"


def test_forget_project_keeps_the_history(make_window, tmp_path):
    folder = tmp_path / "proj"
    folder.mkdir()
    window = make_window()
    window.project_path.setText(str(folder))
    window.history = [{"role": "user", "content": "про проект"}]
    window._messages = [{"role": "user", "text": "про проект", "meta": ""}]
    window.forget_project()
    sessions = load_sessions()
    assert len(sessions) == 1 and sessions[0]["project"] == str(folder)
    window.history = [{"role": "user", "content": "без проекта"}]
    window._save_memory()
    assert load_sessions()[-1]["project"] == ""
