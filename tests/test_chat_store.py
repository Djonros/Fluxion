"""Chats in SQLite: chats.json moves in without a loss, a save writes one chat, threads do not clash."""
from __future__ import annotations

import json
import sqlite3
import threading
import time

from desktop_browser import memory
from desktop_browser.memory import (
    clear_sessions,
    delete_sessions,
    load_sessions,
    make_session,
    rename_session,
    save_session,
    search_sessions,
)


def _chat(text: str, **extra) -> dict:
    session = make_session([{"role": "user", "content": text}, {"role": "assistant", "content": "ответ"}])
    session.update(extra)
    return session


def test_chats_json_moves_into_the_database_and_stays_as_a_copy():
    old = [{"id": f"c{i}", "title": f"чат {i}", "updated": i, "renamed": i == 3,
            "messages": [{"role": "user", "content": f"вопрос {i}"}]} for i in range(1, 6)]
    memory.chats_path().write_text(json.dumps(old, ensure_ascii=False), encoding="utf-8")
    loaded = load_sessions()
    assert [s["id"] for s in loaded] == ["c1", "c2", "c3", "c4", "c5"]
    assert loaded[2]["renamed"] and loaded[2]["title"] == "чат 3"
    assert memory.db_path().is_file()
    backup = memory.chats_path().with_name("chats.json.bak")
    assert backup.is_file() and json.loads(backup.read_text(encoding="utf-8")) == old
    assert not memory.chats_path().exists()
    memory._cache.clear()  # the next start of the program
    assert [s["id"] for s in load_sessions()] == ["c1", "c2", "c3", "c4", "c5"]


def test_a_save_writes_that_chat_only():
    chats = [_chat(f"вопрос {i}") for i in range(30)]
    for chat in chats:
        save_session(chat)
    db = sqlite3.connect(str(memory.db_path()))
    before = dict(db.execute("SELECT id, data FROM chats"))
    save_session(dict(chats[5], updated=time.time() + 10, messages=chats[5]["messages"] + [
        {"role": "user", "content": "ещё"}]))
    after = dict(db.execute("SELECT id, data FROM chats"))
    db.close()
    changed = [i for i in after if after[i] != before.get(i)]
    assert changed == [chats[5]["id"]]


def test_rename_delete_search_and_the_limit(monkeypatch):
    monkeypatch.setattr(memory, "_MAX_SESSIONS", 3)
    a, b, c, d = (_chat(t, updated=n) for n, t in enumerate(("сборка", "перевод", "чертёж", "расчёт болтов"), 1))
    for s in (a, b, c, d):
        save_session(s)
    assert [s["id"] for s in load_sessions()] == [b["id"], c["id"], d["id"]]  # the oldest goes
    rename_session(c["id"], "Чертёж рамы")
    memory._cache.clear()
    assert next(s for s in load_sessions() if s["id"] == c["id"])["title"] == "Чертёж рамы"
    assert [s["id"] for s in search_sessions(load_sessions(), "рамы")] == [c["id"]]
    assert [s["id"] for s in delete_sessions([b["id"]])] == [c["id"], d["id"]]
    memory._cache.clear()
    assert [s["id"] for s in load_sessions()] == [c["id"], d["id"]]


def test_clear_removes_the_old_files_too():
    memory.chats_path().write_text(json.dumps([{"id": "x", "updated": 1, "messages": [
        {"role": "user", "content": "старый"}]}]), encoding="utf-8")
    load_sessions()
    assert clear_sessions() == []
    assert not memory.chats_path().with_name("chats.json.bak").exists()
    memory._cache.clear()
    assert load_sessions() == []


def test_saves_from_several_threads_are_all_kept():
    errors: list[Exception] = []

    def writer(n: int):
        try:
            for i in range(15):
                save_session(_chat(f"поток {n} вопрос {i}"))
        except Exception as exc:  # pragma: no cover - the failure being tested
            errors.append(exc)

    threads = [threading.Thread(target=writer, args=(n,)) for n in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(20)
    assert not errors
    memory._cache.clear()
    assert len(load_sessions()) == 60
