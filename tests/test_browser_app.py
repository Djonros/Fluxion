"""Tests for the Fluxion Browser build (desktop_browser package)."""
from __future__ import annotations

from types import SimpleNamespace

import pytest
from PySide6.QtCore import QSettings, QUrl
from PySide6.QtWidgets import QApplication

from desktop_browser.app import FluxionWindow
from desktop_browser.browser import (
    FallbackBrowserPanel,
    create_browser_panel,
    linkify,
    looks_like_url,
    normalize_url,
)


@pytest.fixture(scope="module", autouse=True)
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app


@pytest.fixture()
def make_window(tmp_path, monkeypatch):
    monkeypatch.setenv("FLUXION_DISABLE_WEBENGINE", "1")

    def factory(**kwargs):
        ini = tmp_path / f"settings_{len(list(tmp_path.iterdir()))}.ini"
        return FluxionWindow(qsettings=QSettings(str(ini), QSettings.IniFormat), **kwargs)

    return factory


# ── pure helpers ────────────────────────────────────────────────────────


def test_linkify_makes_urls_clickable():
    text = "См. https://example.com/a?b=1&c=2 и http://org.io/x подробности."
    out = linkify(text)
    assert '<a href="https://example.com/a?b=1&amp;c=2">' in out
    assert '<a href="http://org.io/x">' in out
    assert "См." in out and "подробности." in out


def test_linkify_escapes_plain_text():
    out = linkify("<b>не html</b> без ссылок")
    assert "<a href=" not in out
    assert "&lt;b&gt;" in out


def test_url_helpers():
    assert looks_like_url("example.com") is True
    assert looks_like_url("какой-то запрос") is False
    assert normalize_url("example.com") == "https://example.com"
    assert normalize_url("http://example.com") == "http://example.com"


# ── panel construction ──────────────────────────────────────────────────


def test_create_browser_panel_fallback(monkeypatch):
    monkeypatch.setenv("FLUXION_DISABLE_WEBENGINE", "1")
    panel = create_browser_panel(search_base="http://localhost:8080")
    assert isinstance(panel, FallbackBrowserPanel)
    assert panel.search_base == "http://localhost:8080"


def test_fallback_panel_search_renders_results():
    fake = SimpleNamespace(
        run=lambda q: [
            SimpleNamespace(url="https://a.io/x", title="A page", snippet="snip A", content=""),
            SimpleNamespace(url="https://b.io/y", title="B page", snippet="snip B", content=""),
        ]
    )
    panel = FallbackBrowserPanel(web_search=fake)
    panel.search("запрос")
    html = panel.view.toHtml()
    assert 'href="https://a.io/x"' in html
    assert "A page" in html and "B page" in html
    assert "запрос" in html


def test_fallback_panel_search_without_web_search():
    panel = FallbackBrowserPanel(web_search=None)
    panel.search("python 3.14")
    html = panel.view.toHtml()
    assert "duckduckgo.com" in html
    assert "python 3.14" in html


def test_fallback_panel_address_bar_opens_url(monkeypatch):
    panel = FallbackBrowserPanel()
    assert panel.address.minimumWidth() >= 320
    assert panel.address.isClearButtonEnabled()
    opened = []
    monkeypatch.setattr(panel, "open_url", lambda url: opened.append(url))
    panel.address.setText("example.com/docs")
    panel._on_address()
    assert opened == ["https://example.com/docs"]


def test_fallback_panel_address_bar_runs_search(monkeypatch):
    panel = FallbackBrowserPanel()
    searched = []
    monkeypatch.setattr(panel, "search", lambda q: searched.append(q))
    panel.address.setText("python asyncio tutorial")
    panel._on_address()
    assert searched == ["python asyncio tutorial"]


# ── window integration ──────────────────────────────────────────────────


def test_window_builds_hidden_fallback_panel(make_window):
    window = make_window()
    assert isinstance(window.web_panel, FallbackBrowserPanel)
    assert window.web_panel.isHidden()
    assert window.browser_button.isChecked() is False


def test_browser_toggle_button_shows_and_hides_panel(make_window):
    window = make_window()
    window.browser_button.setChecked(True)
    assert not window.web_panel.isHidden()
    window.browser_button.setChecked(False)
    assert window.web_panel.isHidden()


def test_chat_message_urls_are_clickable(make_window):
    window = make_window()
    window._append_message("assistant", "Документация: https://docs.example.com/guide")
    assert 'href="https://docs.example.com/guide"' in window.messages.toHtml()


def test_anchor_opens_in_browser_panel(make_window, monkeypatch):
    window = make_window()
    calls = []
    monkeypatch.setattr(window.web_panel, "open_url", lambda url: calls.append(url))
    window._on_anchor(QUrl("https://example.com/page"))
    assert calls == ["https://example.com/page"]
    assert not window.web_panel.isHidden()
    assert window.browser_button.isChecked()


def test_chat_web_used_autoopens_search(make_window, monkeypatch):
    window = make_window()
    window._append_message("user", "что нового в python 3.14")
    window._append_message("assistant", "")
    window._last_query = "что нового в python 3.14"
    window._web_opened_for_current = False
    searches = []
    monkeypatch.setattr(window.web_panel, "search", lambda q: searches.append(q))
    result = SimpleNamespace(
        strategy=SimpleNamespace(name="WEB"),
        rag_used=False,
        rag_sources=[],
        web_used=True,
        web_sources=["https://a.io"],
    )
    window._on_meta(result)
    assert searches == ["что нового в python 3.14"]
    assert not window.web_panel.isHidden()
    window._on_meta(result)
    assert searches == ["что нового в python 3.14"]  # only once per message


def test_chat_agent_web_search_step_autoopens(make_window, monkeypatch):
    window = make_window()
    window._append_message("user", "найди релизы")
    window._append_message("assistant", "")
    searches = []
    monkeypatch.setattr(window.web_panel, "search", lambda q: searches.append(q))
    step = SimpleNamespace(thought="", tool_name="web_search", tool_args="qwen3 release notes")
    window._on_chat_agent_step(step)
    assert searches == ["qwen3 release notes"]


def test_chat_agent_web_search_step_with_observation_autoopens(make_window, monkeypatch):
    window = make_window()
    window._append_message("user", "найди документацию")
    window._append_message("assistant", "")
    searches = []
    monkeypatch.setattr(window.web_panel, "search", lambda q: searches.append(q))
    step = SimpleNamespace(
        iteration=1,
        thought="ищу",
        tool_name="web_search",
        tool_args="ollama docs",
        observation="результаты",
    )
    window._on_chat_agent_step(step)
    assert searches == ["ollama docs"]


def test_chat_agent_answer_links_clickable(make_window):
    window = make_window()
    window._append_message("user", "вопрос")
    window._append_message("assistant", "")
    window._on_chat_agent_done(
        {"success": True, "final_answer": "Ответ: https://example.com/ans", "iterations_used": 2}
    )
    assert 'href="https://example.com/ans"' in window.messages.toHtml()


def test_window_accepts_web_search_argument(make_window):
    sentinel = object()
    window = make_window(web_search=sentinel)
    assert window.web_search is sentinel


# ── send flow ───────────────────────────────────────────────────────────


def test_send_message_clears_prompt(make_window, monkeypatch):
    from desktop_browser import app as app_module

    class FakeWorker:
        def __init__(self, *args, **kwargs):
            for name in ("token", "meta", "failed", "finished_ok", "text_reset", "finished"):
                setattr(self, name, SimpleNamespace(connect=lambda *a: None))

        def start(self):
            pass

        def deleteLater(self):
            pass

    monkeypatch.setattr(app_module, "ChatWorker", FakeWorker)
    window = make_window(assistant=SimpleNamespace())
    window.prompt.setText("прочитай main.py")
    window.send_message()
    assert window.prompt.text() == ""
    assert isinstance(window.worker, FakeWorker)


def test_send_message_keeps_prompt_when_no_assistant(make_window):
    window = make_window(assistant=None)
    window.prompt.setText("черновик")
    window.send_message()
    assert window.prompt.text() == "черновик"


# ── agent project root ──────────────────────────────────────────────────


def test_resolve_project_root_prefers_picked_folder(tmp_path):
    from desktop_browser.app import resolve_project_root

    settings = SimpleNamespace(project_root=lambda: "C:/fallback/root")
    assert resolve_project_root(str(tmp_path), settings) == str(tmp_path)


def test_resolve_project_root_falls_back_on_missing_folder():
    from desktop_browser.app import resolve_project_root

    settings = SimpleNamespace(project_root=lambda: "C:/fallback/root")
    assert resolve_project_root("C:/gone/nowhere", settings) == "C:/fallback/root"
    assert resolve_project_root("", settings) == "C:/fallback/root"


# ── searxng autostart ───────────────────────────────────────────────────


def test_searxng_local_port_parsing():
    from desktop_browser.searxng import _local_port

    assert _local_port("http://localhost:8080") == 8080
    assert _local_port("http://127.0.0.1:9090") == 9090
    assert _local_port("http://localhost") == 80
    assert _local_port("http://10.0.0.5:8080") is None
    assert _local_port("https://localhost:8080") is None
    assert _local_port("") is None


def test_searxng_autostart_skips_without_docker(monkeypatch):
    import desktop_browser.searxng as launcher

    class FakeClient:
        def __init__(self, base_url="", timeout=0):
            pass

        def is_alive(self):
            return False

    def fail_docker(cmd):
        raise AssertionError("docker must not be called")

    monkeypatch.setattr("web.searxng.SearXNGClient", FakeClient)
    monkeypatch.setattr(launcher, "_docker", fail_docker)
    monkeypatch.setattr(launcher.shutil, "which", lambda name: None)
    assert launcher.ensure_searxng("http://localhost:8080", wait_secs=0) is False


def test_searxng_autostart_starts_container(monkeypatch, tmp_path):
    import subprocess

    import desktop_browser.searxng as launcher

    alive = {"calls": 0}

    class FakeClient:
        def __init__(self, base_url="", timeout=0):
            pass

        def is_alive(self):
            alive["calls"] += 1
            return alive["calls"] > 1

    def fake_docker(cmd):
        assert cmd[1] in ("info", "start")
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    monkeypatch.setattr("web.searxng.SearXNGClient", FakeClient)
    monkeypatch.setattr(launcher, "_docker", fake_docker)
    monkeypatch.setattr(launcher.shutil, "which", lambda name: "docker")
    assert launcher.ensure_searxng("http://localhost:8080", wait_secs=5) is True


# ── chat memory ─────────────────────────────────────────────────────────


@pytest.fixture()
def memory_file(tmp_path, monkeypatch):
    import desktop_browser.memory as memory

    chats = tmp_path / "chats.json"
    legacy = tmp_path / "chat_history.json"
    monkeypatch.setattr(memory, "chats_path", lambda: chats)
    monkeypatch.setattr(memory, "history_path", lambda: legacy)
    return chats


def test_sessions_roundtrip(memory_file):
    from desktop_browser.memory import (
        delete_session,
        load_sessions,
        make_session,
        save_session,
    )

    assert load_sessions() == []
    session = make_session([{"role": "user", "content": "привет"}])
    save_session(session)
    loaded = load_sessions()
    assert len(loaded) == 1
    assert loaded[0]["id"] == session["id"]
    assert loaded[0]["title"] == "привет"
    assert loaded[0]["messages"] == [{"role": "user", "content": "привет"}]
    assert delete_session(session["id"]) == []
    assert load_sessions() == []


def test_sessions_load_ignores_corrupt_and_invalid(memory_file):
    from desktop_browser.memory import load_sessions

    memory_file.write_text("{not json", encoding="utf-8")
    assert load_sessions() == []
    memory_file.write_text('["junk", {"id": 1, "messages": []}, 42]', encoding="utf-8")
    assert load_sessions() == []


def test_sessions_migrate_legacy_history(memory_file, tmp_path):
    from desktop_browser.memory import load_sessions

    legacy = tmp_path / "chat_history.json"
    legacy.write_text(
        '[{"role": "user", "content": "старый вопрос"}]', encoding="utf-8"
    )
    loaded = load_sessions()
    assert len(loaded) == 1
    assert loaded[0]["messages"] == [{"role": "user", "content": "старый вопрос"}]
    assert memory_file.exists()


def test_window_restores_latest_session(make_window, memory_file):
    from desktop_browser.memory import make_session, save_session

    save_session(
        make_session([{"role": "user", "content": "первый чат"}])
    )
    save_session(
        make_session([{"role": "user", "content": "что делает main?"},
                      {"role": "assistant", "content": "Запускает приложение."}])
    )
    window = make_window()
    assert window.history == [
        {"role": "user", "content": "что делает main?"},
        {"role": "assistant", "content": "Запускает приложение."},
    ]
    html_text = window.messages.toHtml()
    assert "что делает main?" in html_text
    assert "Запускает приложение." in html_text


def test_new_topic_keeps_previous_chat(make_window, memory_file):
    from desktop_browser.memory import load_sessions, make_session, save_session

    save_session(make_session([{"role": "user", "content": "важный чат"}]))
    window = make_window()
    assert window.history
    window._new_topic()
    assert window.history == []
    assert window._messages == []
    assert "Добро пожаловать" in window.messages.toHtml()
    sessions = load_sessions()
    assert len(sessions) == 1
    assert sessions[0]["messages"] == [{"role": "user", "content": "важный чат"}]


def test_window_saves_current_chat_on_switch(make_window, memory_file):
    from desktop_browser.memory import load_sessions, make_session, save_session

    save_session(make_session([{"role": "user", "content": "другой чат"}]))
    window = make_window()
    window._new_topic()
    window.history = [{"role": "user", "content": "новая тема"}]
    window._save_memory()
    other = next(s for s in window._sessions if s["title"] == "другой чат")
    window._load_session(other)
    assert window.history == [{"role": "user", "content": "другой чат"}]
    titles = [s["title"] for s in load_sessions()]
    assert "новая тема" in titles
    assert "другой чат" in titles


def test_delete_current_chat(make_window, memory_file, monkeypatch):
    from PySide6.QtWidgets import QMessageBox

    from desktop_browser.memory import load_sessions, make_session, save_session

    monkeypatch.setattr(
        "desktop_browser.app.QMessageBox.question",
        lambda *a, **k: QMessageBox.Yes,
    )
    save_session(make_session([{"role": "user", "content": "на удаление"}]))
    window = make_window()
    assert window._session_id is not None
    window._delete_current_chat()
    assert load_sessions() == []
    assert window.history == []
    assert "Добро пожаловать" in window.messages.toHtml()


def test_window_close_saves_history(make_window, memory_file):
    from PySide6.QtGui import QCloseEvent

    from desktop_browser.memory import load_sessions

    window = make_window(assistant=SimpleNamespace())
    window.history = [{"role": "user", "content": "прочитай main.py"}]
    window.closeEvent(QCloseEvent())
    sessions = load_sessions()
    assert len(sessions) == 1
    assert sessions[0]["messages"] == [{"role": "user", "content": "прочитай main.py"}]


# ── markdown export ─────────────────────────────────────────────────────


def test_session_to_markdown_format():
    from desktop_browser.memory import session_to_markdown

    md = session_to_markdown(
        {
            "title": "вопрос о коде",
            "messages": [
                {"role": "user", "content": "что делает main?"},
                {"role": "assistant", "content": "Запускает приложение."},
            ],
        }
    )
    assert md.startswith("# вопрос о коде")
    assert "**Вы:**" in md and "**Fluxion:**" in md
    assert "что делает main?" in md
    assert "Запускает приложение." in md


def test_safe_filename():
    from desktop_browser.memory import safe_filename

    assert safe_filename("Что делает main? (версия 2)") == "Что_делает_main_версия_2.md"
    assert safe_filename("///") == "chat.md"


def test_window_exports_chat_to_file(make_window, memory_file, monkeypatch, tmp_path):
    target = tmp_path / "export.md"
    monkeypatch.setattr(
        "PySide6.QtWidgets.QFileDialog.getSaveFileName",
        staticmethod(lambda *a, **k: (str(target), "Markdown (*.md)")),
    )
    window = make_window()
    window.history = [
        {"role": "user", "content": "прочитай main.py"},
        {"role": "assistant", "content": "Вот разбор файла."},
    ]
    window._export_current_chat()
    text = target.read_text(encoding="utf-8")
    assert "прочитай main.py" in text
    assert "Вот разбор файла." in text
    banners = [m for m in window._messages if m["role"] == "banner"]
    assert any("экспортирован" in b["text"].lower() for b in banners)


def test_window_export_empty_chat_banners(make_window):
    window = make_window()
    window._export_current_chat()
    banners = [m for m in window._messages if m["role"] == "banner"]
    assert any("пуст" in b["text"].lower() for b in banners)


# ── thinking indicator ───────────────────────────────────────────────────


def test_thinking_indicator_in_chat_bubble(make_window):
    window = make_window()
    window._append_message("user", "вопрос")
    window._append_message("assistant", "")
    window._start_thinking()
    assert window._think_timer.isActive()
    assert "Думаю" in window.messages.toHtml()
    window._on_token("Ответ")
    assert not window._think_timer.isActive()
    assert "thinking" not in window._messages[-1]
    assert window._messages[-1]["text"] == "Ответ"


def test_thinking_spinner_animates(make_window):
    window = make_window()
    window._append_message("assistant", "")
    window._start_thinking()
    first = window.messages.toHtml()
    window._on_think_tick()
    window._on_think_tick()
    second = window.messages.toHtml()
    assert "Думаю" in first and "Думаю" in second
    window._stop_thinking()


def test_placeholder_stays_invitation_while_generating(make_window):
    window = make_window()
    window.prompt.setPlaceholderText("Напишите сообщение...")
    window._set_generating(True)
    assert window.prompt.placeholderText() == "Напишите сообщение..."
    window._set_generating(False)
    assert window.prompt.placeholderText() == "Напишите сообщение..."
