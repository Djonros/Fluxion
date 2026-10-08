"""Window-level tests of the desktop app (desktop_browser).

Ported from the removed legacy ``desktop`` package, whose window this app grew
out of: same widgets and behaviour (chat, agent, models, training, licence,
project page).  The browser panel is disabled (no QtWebEngine in tests) and
chat history is isolated by tests/conftest.py.
"""
from pathlib import Path
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("FLUXION_DISABLE_WEBENGINE", "1")

import sys
import time
from types import SimpleNamespace

import pytest
from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QApplication

from cli.theme import DARK, LIGHT
from desktop_browser.app import FluxionWindow


class FakeAssistant:
    def __init__(self, tokens=None, delay=0.0):
        self.tokens = list(tokens or ["Привет", "!", " Как", " дела?"])
        self.delay = delay
        self.calls = []

    def ask_stream(self, query, history=None, **kwargs):
        self.calls.append({"query": query, "history": list(history or [])})
        meta = SimpleNamespace(
            strategy=SimpleNamespace(name="DIRECT"),
            rag_used=False,
            rag_sources=[],
            web_used=False,
            web_sources=[],
        )
        if self.delay:

            def stream():
                for token in self.tokens:
                    time.sleep(self.delay)
                    yield token

            return meta, stream()
        return meta, iter(list(self.tokens))


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app


@pytest.fixture(autouse=True)
def _logs_in_tmp(tmp_path, monkeypatch):
    """Training runs mirror their log to data/logs; keep the repo clean."""
    import desktop_browser.crash as crash

    monkeypatch.setattr(crash, "crash_dir", lambda: tmp_path)


def _window(qapp, tmp_path, assistant=None, backend=None, settings=None):
    qsettings = QSettings(str(tmp_path / "ui.ini"), QSettings.IniFormat)
    return FluxionWindow(
        assistant=assistant, backend=backend, settings=settings, qsettings=qsettings
    )


def _drain(qapp, window, timeout=5.0):
    deadline = time.time() + timeout
    flags = (
        window.generating,
        window.agent_running,
        window.indexing,
        window.training_running,
        window.env_installing,
    )
    while any(flags) and time.time() < deadline:
        qapp.processEvents()
        time.sleep(0.01)
    qapp.processEvents()


def _drain_models(qapp, window, timeout=5.0):
    deadline = time.time() + timeout
    while window._model_worker is not None and time.time() < deadline:
        qapp.processEvents()
        time.sleep(0.01)
    qapp.processEvents()


def test_streaming_reply_appends_tokens(qapp, tmp_path):
    fake = FakeAssistant(tokens=["Привет", "!"])
    window = _window(qapp, tmp_path, assistant=fake)
    window.prompt.setText("Тест")
    window.send_message()
    _drain(qapp, window)

    assert not window.generating
    assert "Привет!" in window.messages.toPlainText()
    assert fake.calls[0]["query"] == "Тест"
    assert fake.calls[0]["history"] == []
    assert window.history == [
        {"role": "user", "content": "Тест"},
        {"role": "assistant", "content": "Привет!"},
    ]


def test_history_passed_on_second_message(qapp, tmp_path):
    fake = FakeAssistant(tokens=["Ок"])
    window = _window(qapp, tmp_path, assistant=fake)
    window.prompt.setText("Первый")
    window.send_message()
    _drain(qapp, window)
    window.prompt.setText("Второй")
    window.send_message()
    _drain(qapp, window)

    assert len(fake.calls) == 2
    assert fake.calls[1]["history"] == [
        {"role": "user", "content": "Первый"},
        {"role": "assistant", "content": "Ок"},
    ]


def test_stop_interrupts_generation(qapp, tmp_path):
    fake = FakeAssistant(tokens=["1", "2", "3", "4", "5"], delay=0.05)
    window = _window(qapp, tmp_path, assistant=fake)
    window.prompt.setText("Стоп-тест")
    window.send_message()

    deadline = time.time() + 5
    while "1" not in window.messages.toPlainText() and time.time() < deadline:
        qapp.processEvents()
        time.sleep(0.01)

    window.stop_generation()
    _drain(qapp, window)

    assert not window.generating
    assert "1" in window.messages.toPlainText()
    assert "4" not in window.messages.toPlainText()
    assert "5" not in window.messages.toPlainText()
    assert not window.stop_button.isVisible()


def test_meta_line_rendered(qapp, tmp_path):
    fake = FakeAssistant(tokens=["Ответ"])
    fake_ask = fake.ask_stream

    def ask_stream(query, history=None, **kwargs):
        meta, stream = fake_ask(query, history, **kwargs)
        meta.rag_used = True
        meta.rag_sources = ["a.py::f", "b.py::g"]
        return meta, stream

    fake.ask_stream = ask_stream
    window = _window(qapp, tmp_path, assistant=fake)
    window.prompt.setText("По коду")
    window.send_message()
    _drain(qapp, window)

    text = window.messages.toPlainText()
    assert "RAG: 2 источн." in text
    assert "direct" in text


def test_no_engine_message(qapp, tmp_path):
    window = _window(qapp, tmp_path)
    window.prompt.setText("Привет")
    window.send_message()

    assert not window.generating
    assert "Движок не подключён" in window.messages.toPlainText()
    assert window.status_label.text() == "Движок: не подключён"


def test_send_ignored_while_generating(qapp, tmp_path):
    fake = FakeAssistant(tokens=["x", "y"], delay=0.05)
    window = _window(qapp, tmp_path, assistant=fake)
    window.prompt.setText("Первый")
    window.send_message()

    deadline = time.time() + 5
    while len(fake.calls) < 1 and time.time() < deadline:
        qapp.processEvents()
        time.sleep(0.01)
    assert window.generating

    window.prompt.setText("Второй")
    window.send_message()
    assert len(fake.calls) == 1

    window.stop_generation()
    _drain(qapp, window)


def test_theme_toggle_persists(qapp, tmp_path):
    ini = tmp_path / "ui.ini"
    first = FluxionWindow(qsettings=QSettings(str(ini), QSettings.IniFormat))
    assert first.theme == DARK
    first.toggle_theme()
    assert first.theme == LIGHT
    first.qsettings.sync()

    second = FluxionWindow(qsettings=QSettings(str(ini), QSettings.IniFormat))
    assert second.theme == LIGHT
    assert second.act_theme.text() == "Тёмная тема"


# ── agent page ──────────────────────────────────────────────────────────────


class FakeAgent:
    def __init__(self, steps, result, delay=0.0):
        self.steps = steps
        self.result = result
        self.delay = delay
        self.last_result = result
        self.task = None

    def run_iter(self, task, context=""):
        self.task = task
        for step in self.steps:
            if self.delay:
                time.sleep(self.delay)
            yield step
        return self.result


def _step(iteration, thought="", tool="", args="", observation=""):
    return SimpleNamespace(
        iteration=iteration,
        thought=thought,
        action="",
        tool_name=tool,
        tool_args=args,
        observation=observation,
        is_final=False,
    )


def _agent_result(success=True, answer="Готово", iterations=2):
    return SimpleNamespace(success=success, final_answer=answer, iterations_used=iterations)


def _agent_window(qapp, tmp_path, steps, result, delay=0.0):
    created = []

    def factory(task, allow_write, max_iter):
        agent = FakeAgent(steps, result, delay=delay)
        agent.task = task
        agent.allow_write = allow_write
        agent.max_iter = max_iter
        created.append(agent)
        return agent

    window = _window(qapp, tmp_path)
    window.agent_factory = factory
    return window, created


def _send_to_agent(qapp, window, text):
    window.chat_agent_files.setChecked(True)
    window.prompt.setText(text)
    window.send_message()
    _drain(qapp, window, timeout=5.0)


def test_agent_streams_steps(qapp, tmp_path, monkeypatch):
    import licensing

    monkeypatch.setattr(licensing, "feature_enabled", lambda feature: False)
    steps = [
        _step(1, thought="Прочитаю файл", tool="read_file", args="main.py", observation="print('hi')"),
        _step(2, tool="finish", args="Готово"),
    ]
    window, created = _agent_window(qapp, tmp_path, steps, _agent_result())
    window.agent_iterations.setValue(3)
    _send_to_agent(qapp, window, "Прочитай main.py")

    assert not window.generating
    text = window.messages.toPlainText()
    assert "Прочитай main.py" in text
    assert "read_file" in text
    assert "Готово" in text
    assert "2 итер." in text
    assert created[0].task == "Прочитай main.py"
    assert created[0].max_iter == 3
    assert not created[0].allow_write


def test_agent_stop_between_steps(qapp, tmp_path):
    steps = [
        _step(1, tool="read_file", args="a.py", observation="x"),
        _step(2, tool="read_file", args="b.py", observation="y"),
        _step(3, tool="finish", args="done"),
    ]
    window, _ = _agent_window(qapp, tmp_path, steps, _agent_result(), delay=0.05)
    window.chat_agent_files.setChecked(True)
    window.prompt.setText("Задача")
    window.send_message()

    deadline = time.time() + 5
    while "a.py" not in window.messages.toPlainText() and time.time() < deadline:
        qapp.processEvents()
        time.sleep(0.01)

    window.stop_generation()
    _drain(qapp, window)

    assert not window.generating
    text = window.messages.toPlainText()
    assert "остановлено пользователем" in text
    assert "a.py" in text
    assert not window.stop_button.isVisible()


def test_agent_write_gate_blocked_without_pro(qapp, tmp_path, monkeypatch):
    import licensing

    monkeypatch.setattr(licensing, "feature_enabled", lambda feature: False)
    steps = [_step(1, tool="read_file", args="x.py", observation="x = 1")]
    window, created = _agent_window(qapp, tmp_path, steps, _agent_result())

    _send_to_agent(qapp, window, "Создай файл")
    assert window.chat_agent_files.isChecked()
    assert created[0].allow_write is False
    assert "agent_write" in window.banners._keys
    assert "Pro" in window.chat_agent_files.toolTip()


def test_agent_write_allowed_with_pro(qapp, tmp_path, monkeypatch):
    import licensing

    monkeypatch.setattr(licensing, "feature_enabled", lambda feature: True)
    steps = [_step(1, tool="write_file", args="x.py", observation="Wrote x.py")]
    window, created = _agent_window(qapp, tmp_path, steps, _agent_result())

    _send_to_agent(qapp, window, "Создай файл")
    assert created[0].allow_write
    assert "agent_write" not in window.banners._keys


def test_agent_without_factory_shows_message(qapp, tmp_path):
    window = _window(qapp, tmp_path)
    window.prompt.setText("Задача")
    window.send_agent_once()
    assert "Движок не подключён" in window.messages.toPlainText()
    assert not window.generating


def test_agent_has_no_page_and_no_menu(qapp, tmp_path):
    window = _window(qapp, tmp_path)
    nav = [button.text() for button in window.nav_group.buttons()]
    assert nav == ["Чат", "Проект", "Обучение", "Модели"]
    assert window.pages.count() == 4
    menus = [action.text().replace("&", "") for action in window.menuBar().actions()]
    assert "Агент" not in menus
    for name in ("agent_view", "agent_task", "agent_write", "run_agent", "act_write"):
        assert not hasattr(window, name)


def test_ctrl_enter_runs_agent_once_without_touching_chips(qapp, tmp_path, monkeypatch):
    import licensing
    from PySide6.QtGui import QShortcut

    monkeypatch.setattr(licensing, "feature_enabled", lambda feature: True)
    steps = [_step(1, tool="read_file", args="main.py", observation="x")]
    fake = FakeAssistant(tokens=["Ответ"])
    window, created = _chat_agent_window(qapp, tmp_path, steps, _agent_result(), assistant=fake)

    keys = {s.key().toString() for s in window.prompt.findChildren(QShortcut)}
    assert {"Ctrl+Return", "Ctrl+Enter"} <= keys

    window.prompt.setText("Прочитай main.py")
    window.send_agent_once()
    _drain(qapp, window)

    assert len(created) == 1
    assert created[0].task == "Прочитай main.py"
    assert created[0].allow_write is False
    assert not window.chat_agent_files.isChecked()
    assert not window.chat_plan.isChecked()
    assert fake.calls == []

    window.prompt.setText("Обычный вопрос")
    window.send_message()
    _drain(qapp, window)
    assert len(created) == 1
    assert len(fake.calls) == 1


def test_plan_mode_sends_prefix_and_never_writes(qapp, tmp_path, monkeypatch):
    import licensing
    from desktop_browser.app import PLAN_RUN_URL, PLAN_TASK_PREFIX

    monkeypatch.setattr(licensing, "feature_enabled", lambda feature: True)
    plan = "1. Прочитать main.py\n2. Исправить ошибку"
    window, created = _chat_agent_window(
        qapp, tmp_path, [], _agent_result(answer=plan), assistant=FakeAssistant()
    )
    window.chat_agent_files.setChecked(True)
    window.chat_plan.setChecked(True)
    window.prompt.setText("Почини расчёт")
    window.send_message()
    _drain(qapp, window)

    assert created[0].task == f"{PLAN_TASK_PREFIX}\n\nПочини расчёт"
    assert created[0].task.startswith("Составь нумерованный план действий по задаче.")
    assert created[0].allow_write is False
    assert window._last_plan == plan
    assert PLAN_TASK_PREFIX not in window.messages.toPlainText()
    assert "Выполнить план" in window.messages.toPlainText()
    assert PLAN_RUN_URL in window.messages.toHtml()


def test_run_plan_passes_saved_plan_with_chip_write_access(qapp, tmp_path, monkeypatch):
    import licensing
    from PySide6.QtCore import QUrl
    from desktop_browser.app import PLAN_RUN_URL

    monkeypatch.setattr(licensing, "feature_enabled", lambda feature: True)
    plan = "1. Шаг один\n2. Шаг два"
    window, created = _chat_agent_window(
        qapp, tmp_path, [], _agent_result(answer=plan), assistant=FakeAssistant()
    )
    window.chat_plan.setChecked(True)
    window.prompt.setText("Задача")
    window.send_message()
    _drain(qapp, window)
    assert window._last_plan == plan

    window.chat_plan.setChecked(False)
    window.chat_agent_files.setChecked(True)
    window._on_anchor(QUrl(PLAN_RUN_URL))
    _drain(qapp, window)

    assert len(created) == 2
    assert created[1].task == "Реализуй план:\n" + plan
    assert created[1].allow_write is True
    assert PLAN_RUN_URL not in window.messages.toHtml()

    window.chat_agent_files.setChecked(False)
    window.run_plan()
    _drain(qapp, window)
    assert created[2].task == "Реализуй план:\n" + plan
    assert created[2].allow_write is False


def test_failed_plan_run_is_not_offered_for_execution(qapp, tmp_path, monkeypatch):
    import licensing
    from desktop_browser.app import PLAN_RUN_URL

    monkeypatch.setattr(licensing, "feature_enabled", lambda feature: True)
    stuck = _agent_result(success=False, answer="Агент остановлен: модель повторяет действие.")
    window, created = _chat_agent_window(qapp, tmp_path, [], stuck, assistant=FakeAssistant())
    window.chat_plan.setChecked(True)
    window.prompt.setText("Задача")
    window.send_message()
    _drain(qapp, window)

    assert len(created) == 1
    assert window._last_plan == ""
    assert "Агент остановлен" in window.messages.toPlainText()
    assert "Выполнить план" not in window.messages.toPlainText()
    assert PLAN_RUN_URL not in window.messages.toHtml()
    window.run_plan()
    assert len(created) == 1


def test_run_plan_without_a_plan_does_nothing(qapp, tmp_path):
    window, created = _chat_agent_window(qapp, tmp_path, [], _agent_result())
    window.run_plan()
    assert created == []
    assert not window.generating


def test_unlimited_iterations_available_with_pro(qapp, tmp_path, monkeypatch):
    import desktop_browser.app as app_module

    monkeypatch.setattr(app_module, "load_activation", lambda: _pro_license())
    window, created = _chat_agent_window(qapp, tmp_path, [], _agent_result())
    assert not window.agent_unlimited.isHidden()
    assert window.agent_unlimited.isEnabled()
    assert window.agent_iterations.minimum() == 1
    assert window.agent_iterations.maximum() == 50

    window.agent_iterations.setValue(7)
    assert window._agent_max_iterations() == 7
    window.agent_unlimited.setChecked(True)
    assert not window.agent_iterations.isEnabled()
    assert window._agent_max_iterations() == 0

    window.prompt.setText("Задача")
    window.send_agent_once()
    _drain(qapp, window)
    assert created[0].max_iter == 0


def test_free_plan_has_only_the_iteration_spinbox(qapp, tmp_path, monkeypatch):
    import desktop_browser.app as app_module

    monkeypatch.setattr(app_module, "load_activation", lambda: None)
    window, created = _chat_agent_window(qapp, tmp_path, [], _agent_result())
    assert window.agent_unlimited.isHidden()
    assert not window.agent_unlimited.isEnabled()
    window.agent_iterations.setValue(9)
    window.agent_unlimited.setChecked(True)
    assert window._agent_max_iterations() == 9

    window.prompt.setText("Задача")
    window.send_agent_once()
    _drain(qapp, window)
    assert created[0].max_iter == 9


def test_unlimited_is_dropped_when_pro_ends(qapp, tmp_path, monkeypatch):
    import desktop_browser.app as app_module

    state = {"lic": _pro_license()}
    monkeypatch.setattr(app_module, "load_activation", lambda: state["lic"])
    window = _window(qapp, tmp_path)
    window.agent_unlimited.setChecked(True)
    state["lic"] = None
    window._refresh_license_badge()
    assert not window.agent_unlimited.isChecked()
    assert window.agent_unlimited.isHidden()
    assert window.agent_iterations.isEnabled()


def test_git_button_lives_in_chat_and_reflects_state(qapp, tmp_path, monkeypatch):
    from orchestrator import git_helper

    project = tmp_path / "project"
    project.mkdir()
    state = {"repo": False}
    monkeypatch.setattr(git_helper, "is_repo", lambda root: state["repo"])
    window = _window(qapp, tmp_path)
    window.qsettings.setValue("project_path", str(project))

    window._refresh_git_button()
    assert window.agent_git_button.text() == "Git: нет репозитория"
    state["repo"] = True
    window._refresh_git_button()
    assert window.agent_git_button.text() == "Git: репозиторий"
    window.qsettings.setValue("agent_git_enabled", 0)
    window._refresh_git_button()
    assert window.agent_git_button.text() == "Git: выключен"
    window.qsettings.setValue("agent_git_enabled", 1)
    window._refresh_git_button()
    assert window.agent_git_button.text() == "Git: репозиторий"
    assert window.agent_git_button.parent() is window.agent_settings


def test_chat_settings_hold_model_and_modes_sit_under_the_input(qapp, tmp_path):
    window = _window(qapp, tmp_path)
    settings = window.agent_settings
    for widget in (
        window.model_combo,
        window.model_refresh_button,
        window.agent_iterations,
        window.agent_unlimited,
        window.agent_git_button,
    ):
        assert widget.parent() is settings
    composer = window.prompt.parent()
    assert window.send_button.parent() is composer
    for chip in (window.chat_agent_files, window.chat_plan):
        assert chip.parent() is not composer
        assert chip.objectName() == "modeChip"
    assert "Настройки чата" in window.agent_settings_button.text()

    window.show()
    window._show_agent_settings()
    qapp.processEvents()
    assert settings.isVisible()
    assert settings.y() > window.agent_settings_button.mapToGlobal(
        window.agent_settings_button.rect().topLeft()
    ).y()
    settings.hide()


def test_training_shows_live_activity(qapp, tmp_path, monkeypatch):
    import desktop_browser.app as app_module

    clock = {"now": 1000.0}
    monkeypatch.setattr(app_module.time, "monotonic", lambda: clock["now"])
    window = _window(qapp, tmp_path)
    assert window.training_busy.isHidden()

    window._set_training_running(True)
    assert not window.training_busy.isHidden()
    assert window.training_busy.maximum() == 0
    assert window._activity_timer.isActive()

    clock["now"] += 75
    window._on_training_log("step 10/200")
    clock["now"] += 12
    window._tick_activity()
    text = window.training_activity.text()
    assert text.startswith("Обучение идёт · 1 мин 27 с")
    assert "12 с назад" in text
    assert "Стоп" not in text

    clock["now"] += 400
    window._tick_activity()
    assert "Если журнал молчит больше 30 минут" in window.training_activity.text()

    window._set_training_running(False)
    assert window.training_busy.isHidden()
    assert not window._activity_timer.isActive()


def test_training_log_shows_text_as_is_and_formats_status(qapp, tmp_path):
    window = _window(qapp, tmp_path)
    window.training_view.clear()
    window._on_training_log('HTTP Request: HEAD "HTTP/1.1 200 OK" & <tag>')
    window._on_training_failed("Команда завершилась с кодом 1")
    text = window.training_view.toPlainText()
    assert 'HTTP Request: HEAD "HTTP/1.1 200 OK" & <tag>' in text
    assert "&quot;" not in text
    assert "<b>" not in text
    assert "Команда завершилась с кодом 1" in text
    assert "font-weight:700" in window.training_view.toHtml().replace(" ", "")


def test_training_refused_when_run_from_archive(qapp, tmp_path, monkeypatch):
    import desktop_browser.app as app_module
    from desktop_browser import training

    calls = []
    monkeypatch.setattr(training, "install_training_environment", lambda *a, **k: calls.append(a))
    window = _window(qapp, tmp_path)
    monkeypatch.setattr(window, "_temp_run", lambda: True)
    window._refresh_training_env()
    assert window.training_env_label.text() == app_module.TEMP_RUN_TRAINING
    assert window.training_install_button.isHidden()
    assert window.training_reinstall_button.isHidden()
    window.install_training_env()
    window.run_training()
    assert calls == []
    assert "запущена прямо из архива" in window.training_view.toPlainText()
    assert not window.env_installing and not window.training_running


def test_env_install_shows_live_activity(qapp, tmp_path):
    window = _window(qapp, tmp_path)
    window._start_activity("Установка окружения обучения")
    assert window.training_activity.text().startswith("Установка окружения обучения · 0 с")
    window._on_env_install_failed("нет сети")
    assert window.training_busy.isHidden()


# ── trial edits ─────────────────────────────────────────────────────────────


def test_trial_gives_write_access_and_counts_in_tooltip(qapp, tmp_path, monkeypatch):
    import licensing
    from licensing import trial_reset

    monkeypatch.setattr(licensing, "feature_enabled", lambda feature: False)
    trial_reset()
    steps = [_step(1, tool="write_file", args="x.py", observation="Wrote x.py")]
    window, created = _agent_window(qapp, tmp_path, steps, _agent_result())

    _send_to_agent(qapp, window, "Создай файл")
    assert created[0].allow_write is True
    assert "осталось 3 пробных правок" in window.chat_agent_files.toolTip()
    assert "agent_write" in window.banners._keys


def test_trial_dialog_consent_spends_one_edit(qapp, tmp_path, monkeypatch):
    import licensing
    from desktop_browser import trial_gate
    from licensing import trial_remaining, trial_reset

    monkeypatch.setattr(licensing, "feature_enabled", lambda feature: False)
    trial_reset()
    asked = []

    def allow(parent, path, kind, remaining):
        asked.append((path, kind, remaining))
        return True

    monkeypatch.setattr(trial_gate, "ask_trial_write", allow)
    window = _window(qapp, tmp_path)
    gate = trial_gate.TrialWriteGate()
    gate.attach(window)
    gate.changed.connect(window._on_trial_changed)

    assert gate.confirm("src/app.py", "edit") is True
    assert asked == [("src/app.py", "edit", 3)]
    assert trial_remaining() == 2
    assert "осталось 2 пробных правок" in window.chat_agent_files.toolTip()


def test_trial_dialog_refusal_keeps_the_edit(qapp, tmp_path, monkeypatch):
    from desktop_browser import trial_gate
    from licensing import trial_remaining, trial_reset

    trial_reset()
    monkeypatch.setattr(trial_gate, "ask_trial_write", lambda *args: False)
    gate = trial_gate.TrialWriteGate()
    assert gate.confirm("a.py", "write") is False
    assert trial_remaining() == 3


def test_spent_trial_is_refused_without_a_dialog(qapp, tmp_path, monkeypatch):
    from desktop_browser import trial_gate

    def unexpected(*args):
        raise AssertionError("the dialog must not open when the trial is spent")

    monkeypatch.setattr(trial_gate, "ask_trial_write", unexpected)
    assert trial_gate.TrialWriteGate().confirm("a.py", "write") is False


def test_trial_confirmation_from_worker_thread(qapp, tmp_path, monkeypatch):
    import threading

    from desktop_browser import trial_gate
    from licensing import trial_remaining, trial_reset

    trial_reset()
    gui_thread = threading.current_thread()
    seen = []

    def allow(parent, path, kind, remaining):
        seen.append(threading.current_thread() is gui_thread)
        return True

    monkeypatch.setattr(trial_gate, "ask_trial_write", allow)
    gate = trial_gate.TrialWriteGate()
    answers = []
    worker = threading.Thread(target=lambda: answers.append(gate.confirm("a.py", "write")))
    worker.start()
    deadline = time.time() + 5
    while worker.is_alive() and time.time() < deadline:
        qapp.processEvents()
        time.sleep(0.01)
    worker.join(timeout=1)

    assert answers == [True]
    assert seen == [True]
    assert trial_remaining() == 2


def test_trial_dialog_text(qapp, tmp_path, monkeypatch):
    from PySide6.QtWidgets import QMessageBox

    from desktop_browser import trial_gate

    shown = {}

    def fake_exec(box):
        shown["text"] = box.text()
        shown["buttons"] = [button.text() for button in box.buttons()]
        return 0

    monkeypatch.setattr(QMessageBox, "exec", fake_exec)
    assert trial_gate.ask_trial_write(None, "src/app.py", "edit", 2) is False
    assert shown["text"] == "Пробная запись: src/app.py. Разрешить? Осталось попыток: 2"
    assert shown["buttons"] == ["Разрешить", "Отменить"]


# ── sticky scroll ───────────────────────────────────────────────────────────


def _settle(qapp, rounds=5):
    for _ in range(rounds):
        qapp.processEvents()


def test_sticky_scroll_follows_stream_but_not_a_reading_user(qapp, tmp_path):
    window = _window(qapp, tmp_path, assistant=FakeAssistant())
    window.resize(1000, 640)
    window.show()
    _settle(qapp)
    for index in range(40):
        window._messages.append({"role": "user", "text": f"Вопрос {index}", "meta": ""})
    window._messages.append({"role": "assistant", "text": "", "meta": ""})
    window._render_messages()
    _settle(qapp)
    bar = window.messages.verticalScrollBar()
    assert bar.maximum() > 0
    assert bar.value() == bar.maximum()

    for index in range(15):
        window._on_token(f"строка {index}\n\n")
        _settle(qapp)
        assert bar.value() == bar.maximum()

    bar.setValue(bar.maximum() // 3)
    _settle(qapp)
    reading_at = bar.value()
    for index in range(15):
        window._on_token(f"ещё {index}\n\n")
        _settle(qapp)
        assert bar.value() == reading_at
    assert bar.maximum() > reading_at

    bar.setValue(bar.maximum())
    _settle(qapp)
    for index in range(5):
        window._on_token(f"снова {index}\n\n")
        _settle(qapp)
        assert bar.value() == bar.maximum()
    window.close()


def test_sticky_scroll_wired_to_training_log(qapp, tmp_path):
    window = _window(qapp, tmp_path)
    window.resize(1000, 640)
    window.show()
    window.pages.setCurrentIndex(2)
    _settle(qapp)
    assert window.training_view.property("sticky") is True
    for index in range(200):
        window._on_training_log(f"шаг {index}")
    _settle(qapp)
    bar = window.training_view.verticalScrollBar()
    assert bar.maximum() > 0
    assert bar.value() == bar.maximum()

    bar.setValue(0)
    _settle(qapp)
    window._on_training_log("ещё строка")
    _settle(qapp)
    assert bar.value() == 0
    window.close()


# ── model selector ──────────────────────────────────────────────────────────


class FakeOllamaClient:
    def __init__(self, models, error=None):
        self.models = models
        self.error = error
        self.model = "old:1b"

    def list_models(self):
        if self.error:
            raise self.error
        return list(self.models)


class FakeBackend:
    def __init__(self, client):
        self.client = client

    def is_available(self):
        return True


def test_model_combo_populates_and_selects_current(qapp, tmp_path):
    backend = FakeBackend(FakeOllamaClient(["a:1b", "b:7b"]))
    settings = SimpleNamespace(model="b:7b")
    window = _window(qapp, tmp_path, backend=backend, settings=settings)
    _drain_models(qapp, window)

    assert window.model_combo.isEnabled()
    assert window.model_combo.count() == 2
    assert [window.model_combo.itemText(i) for i in range(2)] == ["a:1b", "b:7b"]
    assert window.model_combo.currentText() == "b:7b"


def test_model_selection_updates_engine_and_persists(qapp, tmp_path):
    client = FakeOllamaClient(["a:1b", "b:7b"])
    settings = SimpleNamespace(model="a:1b")
    window = _window(qapp, tmp_path, assistant=FakeAssistant(), backend=FakeBackend(client), settings=settings)
    _drain_models(qapp, window)

    window._on_model_selected("b:7b")

    assert settings.model == "b:7b"
    assert client.model == "b:7b"
    assert window.model_combo.currentText() == "b:7b"
    window.qsettings.sync()
    assert window.qsettings.value("model") == "b:7b"
    assert "b:7b" in window.status_label.text()


def test_model_selection_ignored_while_generating(qapp, tmp_path):
    client = FakeOllamaClient(["a:1b", "b:7b"])
    settings = SimpleNamespace(model="a:1b")
    window = _window(qapp, tmp_path, backend=FakeBackend(client), settings=settings)
    _drain_models(qapp, window)

    window._set_generating(True)
    window._on_model_selected("b:7b")
    window._set_generating(False)

    assert settings.model == "a:1b"
    assert client.model == "old:1b"
    assert window.model_combo.currentText() == "a:1b"


def test_model_list_failure_shows_current_only(qapp, tmp_path):
    backend = FakeBackend(FakeOllamaClient([], error=RuntimeError("ollama down")))
    settings = SimpleNamespace(model="a:1b")
    window = _window(qapp, tmp_path, backend=backend, settings=settings)
    _drain_models(qapp, window)

    assert not window.model_combo.isEnabled()
    assert window.model_combo.count() == 1
    assert window.model_combo.currentText() == "a:1b"
    assert "ollama down" in window.model_combo.toolTip()


def test_model_combo_without_backend(qapp, tmp_path):
    window = _window(qapp, tmp_path)
    assert not window.model_combo.isEnabled()
    assert window.model_combo.count() == 0


def test_saved_model_applied_on_startup(qapp, tmp_path):
    ini = tmp_path / "ui.ini"
    qsettings = QSettings(str(ini), QSettings.IniFormat)
    qsettings.setValue("model", "qwen3:4b")
    qsettings.sync()

    client = FakeOllamaClient(["qwen3:4b", "x:1b"])
    settings = SimpleNamespace(model="old:7b")
    FluxionWindow(
        backend=FakeBackend(client),
        settings=settings,
        qsettings=QSettings(str(ini), QSettings.IniFormat),
    )

    assert settings.model == "qwen3:4b"
    assert client.model == "qwen3:4b"


# ── chat agent mode ─────────────────────────────────────────────────────────


def _chat_agent_window(qapp, tmp_path, steps, result, assistant=None):
    created = []

    def factory(task, allow_write, max_iter):
        agent = FakeAgent(steps, result)
        agent.task = task
        agent.allow_write = allow_write
        agent.max_iter = max_iter
        created.append(agent)
        return agent

    window = _window(qapp, tmp_path, assistant=assistant or FakeAssistant())
    window.agent_factory = factory
    return window, created


def test_chat_agent_toggle_routes_message_to_agent(qapp, tmp_path, monkeypatch):
    import licensing

    monkeypatch.setattr(licensing, "feature_enabled", lambda feature: False)
    steps = [_step(1, tool="read_file", args="main.py", observation="print('hi')")]
    window, created = _chat_agent_window(qapp, tmp_path, steps, _agent_result())

    window.chat_agent_files.setChecked(True)
    window.prompt.setText("Прочитай main.py")
    window.send_message()
    _drain(qapp, window)

    assert len(created) == 1
    assert created[0].task == "Прочитай main.py"
    assert created[0].allow_write is False
    text = window.messages.toPlainText()
    assert "read_file" in text
    assert "Готово" in text
    assert window.history[-1] == {"role": "assistant", "content": "Готово"}
    assert not window.generating


def test_chat_agent_write_with_pro(qapp, tmp_path, monkeypatch):
    import licensing

    monkeypatch.setattr(licensing, "feature_enabled", lambda feature: True)
    steps = [_step(1, tool="write_file", args="x.py", observation="Wrote x.py")]
    window, created = _chat_agent_window(qapp, tmp_path, steps, _agent_result())

    window.chat_agent_files.setChecked(True)
    window.prompt.setText("Создай файл")
    window.send_message()
    _drain(qapp, window)

    assert created[0].allow_write is True


def test_chat_agent_toggle_without_factory_warns(qapp, tmp_path):
    window = _window(qapp, tmp_path, assistant=FakeAssistant())

    window.chat_agent_files.setChecked(True)

    assert not window.chat_agent_files.isChecked()
    assert "Движок не подключён" in window.messages.toPlainText()


def test_chat_without_toggle_uses_assistant(qapp, tmp_path, monkeypatch):
    import licensing

    monkeypatch.setattr(licensing, "feature_enabled", lambda feature: True)
    fake = FakeAssistant(tokens=["Ответ"])
    steps = [_step(1, tool="read_file", args="a.py")]
    window, created = _chat_agent_window(qapp, tmp_path, steps, _agent_result(), assistant=fake)

    window.prompt.setText("Обычный вопрос")
    window.send_message()
    _drain(qapp, window)

    assert created == []
    assert len(fake.calls) == 1
    assert "Ответ" in window.messages.toPlainText()


# ── training page ───────────────────────────────────────────────────────────


def _training_window(qapp, tmp_path, pipeline):
    settings = SimpleNamespace(
        model="a:1b", paths=SimpleNamespace(data_dir=str(tmp_path / "data"))
    )
    window = _window(
        qapp,
        tmp_path,
        assistant=FakeAssistant(),
        backend=FakeBackend(FakeOllamaClient(["a:1b"])),
        settings=settings,
    )
    window.training_pipeline = pipeline
    return window


def _mark_training_env_ready(window):
    """The start button also needs a training environment, which a test
    machine may not have; these tests are about the licence gate."""
    window._env_ready = True
    window._refresh_training_steps()


def _fake_pipeline(result=None, error=None, delay=0.0):
    captured = []

    def pipeline(params, *, log, stop_requested):
        captured.append(params)
        log("stage: обучаю")
        if delay:
            deadline = time.time() + 5
            while not stop_requested() and time.time() < deadline:
                time.sleep(delay)
        if error:
            raise error
        return result or {
            "adapter": "data/lora_output/adapter",
            "merged": "data/merged_model",
            "gguf": "data/gguf/m.f16.gguf",
            "ollama_model": "fluxion-x",
            "registered": True,
            "created": True,
            "cancelled": False,
            "trainer": "unsloth",
        }

    return pipeline, captured


def test_training_gate_blocked_without_pro(qapp, tmp_path, monkeypatch):
    import licensing

    monkeypatch.setattr(licensing, "feature_enabled", lambda feature: False)
    pipeline, _ = _fake_pipeline()
    window = _training_window(qapp, tmp_path, pipeline)

    assert not window.training_run_button.isEnabled()
    assert "Pro" in window.training_subtitle.text()
    window.training_dataset.setText("ds.jsonl")
    window.run_training()
    assert not window.training_running


def test_training_gate_opens_with_pro(qapp, tmp_path, monkeypatch):
    import licensing

    monkeypatch.setattr(licensing, "feature_enabled", lambda feature: True)
    pipeline, _ = _fake_pipeline()
    window = _training_window(qapp, tmp_path, pipeline)
    _mark_training_env_ready(window)

    assert window.training_run_button.isEnabled()
    assert "Pro-функция" not in window.training_subtitle.text()


def test_training_run_streams_log_and_switches_model(qapp, tmp_path, monkeypatch):
    import licensing

    monkeypatch.setattr(licensing, "feature_enabled", lambda feature: True)
    pipeline, captured = _fake_pipeline()
    window = _training_window(qapp, tmp_path, pipeline)

    window.training_dataset.setText("ds.jsonl")
    window.training_epochs.setValue(5)
    window.training_adapter_name.setText("my-lora")
    window.run_training()
    _drain(qapp, window)
    _drain_models(qapp, window)

    assert not window.training_running
    params = captured[0]
    assert params.dataset == "ds.jsonl"
    assert params.epochs == 5
    assert params.adapter_name == "my-lora"
    text = window.training_view.toPlainText()
    assert "stage: обучаю" in text
    assert "Готово" in text
    assert "зарегистрирован" in text
    assert window.settings.model == "fluxion-x"
    assert window.model_combo.currentText() == "fluxion-x"


def test_training_failure_shows_message(qapp, tmp_path, monkeypatch):
    import licensing

    monkeypatch.setattr(licensing, "feature_enabled", lambda feature: True)
    from desktop_browser.training import TrainingError

    pipeline, _ = _fake_pipeline(error=TrainingError("нет окружения"))
    window = _training_window(qapp, tmp_path, pipeline)
    _mark_training_env_ready(window)

    window.training_dataset.setText("ds.jsonl")
    window.run_training()
    _drain(qapp, window)

    assert not window.training_running
    assert "нет окружения" in window.training_view.toPlainText()
    assert window.training_run_button.isEnabled()
    assert not window.training_stop_button.isVisible()


def test_training_stop_cancels(qapp, tmp_path, monkeypatch):
    import licensing

    monkeypatch.setattr(licensing, "feature_enabled", lambda feature: True)
    pipeline, _ = _fake_pipeline(result={"cancelled": True}, delay=0.02)
    window = _training_window(qapp, tmp_path, pipeline)

    window.training_dataset.setText("ds.jsonl")
    window.run_training()
    # Regression: the start button left the activity bar hidden.
    assert not window.training_busy.isHidden()
    assert window._activity_timer.isActive()
    assert window.training_activity.text().startswith("Обучение идёт")
    assert not window.training_stop_button.isHidden()
    window.stop_training()
    _drain(qapp, window)

    assert not window.training_running
    assert window.training_busy.isHidden()
    assert "Остановлено пользователем" in window.training_view.toPlainText()


def test_training_requires_dataset(qapp, tmp_path, monkeypatch):
    import licensing

    monkeypatch.setattr(licensing, "feature_enabled", lambda feature: True)
    pipeline, captured = _fake_pipeline()
    window = _training_window(qapp, tmp_path, pipeline)

    window.run_training()
    assert captured == []
    assert "датасета" in window.training_view.toPlainText()


# ── training environment install ────────────────────────────────────────────


def test_env_install_button_offered_and_runs(qapp, tmp_path, monkeypatch):
    import desktop_browser.training as training

    monkeypatch.setattr(training, "_detect_trainer", lambda log, env=None: (None, "", None))
    monkeypatch.setattr(training, "detect_training_env", lambda env_dir: None)
    monkeypatch.setattr(training, "find_host_python", lambda: ("py312", "ok"))
    calls = []

    def fake_install(env_dir, *, log, stop_requested, host_python=None, auto_install_python=False, offline_wheels=None):
        calls.append((env_dir, host_python, auto_install_python))
        log("venv создан")

    monkeypatch.setattr(training, "install_training_environment", fake_install)

    window = _training_window(qapp, tmp_path, _fake_pipeline()[0])
    assert not window.training_install_button.isHidden()
    assert "не найдено" in window.training_env_label.text()

    window.install_training_env()
    assert window.env_installing
    _drain(qapp, window)

    assert not window.env_installing
    assert calls and "training_env" in str(calls[0][0])
    assert calls[0][1] == "py312"
    assert calls[0][2] is False
    assert "venv создан" in window.training_view.toPlainText()
    assert "установлено" in window.training_view.toPlainText()


def test_env_install_prompts_and_autoinstalls_when_python_missing(qapp, tmp_path, monkeypatch):
    import desktop_browser.training as training
    from desktop_browser.app import QMessageBox

    monkeypatch.setattr(training, "_detect_trainer", lambda log, env=None: (None, "", None))
    monkeypatch.setattr(training, "detect_training_env", lambda env_dir: None)
    monkeypatch.setattr(
        training, "find_host_python", lambda: (None, "нужен Python 3.11/3.12")
    )
    monkeypatch.setattr(QMessageBox, "question", staticmethod(lambda *a, **k: QMessageBox.Yes))
    calls = []

    def fake_install(env_dir, *, log, stop_requested, host_python=None, auto_install_python=False, offline_wheels=None):
        calls.append((host_python, auto_install_python))
        log("python 3.12 установлен")

    monkeypatch.setattr(training, "install_training_environment", fake_install)

    window = _training_window(qapp, tmp_path, _fake_pipeline()[0])
    window.install_training_env()
    _drain(qapp, window)

    assert calls and calls[0] == (None, True)
    assert "python 3.12 установлен" in window.training_view.toPlainText()


def test_env_install_declined_when_python_missing(qapp, tmp_path, monkeypatch):
    import desktop_browser.training as training
    from desktop_browser.app import QMessageBox

    monkeypatch.setattr(training, "_detect_trainer", lambda log, env=None: (None, "", None))
    monkeypatch.setattr(training, "detect_training_env", lambda env_dir: None)
    monkeypatch.setattr(
        training, "find_host_python", lambda: (None, "нужен Python 3.11/3.12")
    )
    monkeypatch.setattr(QMessageBox, "question", staticmethod(lambda *a, **k: QMessageBox.No))

    def fail_install(*a, **k):
        raise AssertionError("install must not run when declined")

    monkeypatch.setattr(training, "install_training_environment", fail_install)

    window = _training_window(qapp, tmp_path, _fake_pipeline()[0])
    window.install_training_env()
    assert not window.env_installing
    assert "отменена" in window.training_view.toPlainText()


def test_env_label_hidden_when_system_trainer_present(qapp, tmp_path, monkeypatch):
    import desktop_browser.training as training

    def trainer(settings, dataset, max_samples):
        return "adapter"

    monkeypatch.setattr(
        training, "_detect_trainer", lambda log, env=None: (trainer, "unsloth", None)
    )
    monkeypatch.setattr(training, "detect_training_env", lambda env_dir: None)

    window = _training_window(qapp, tmp_path, _fake_pipeline()[0])
    assert window.training_install_button.isHidden()
    assert "системное" in window.training_env_label.text()


def test_env_label_ready_when_marker_exists(qapp, tmp_path, monkeypatch):
    import desktop_browser.training as training

    env_py = tmp_path / "training_env" / "Scripts" / "python.exe"
    monkeypatch.setattr(training, "detect_training_env", lambda env_dir: str(env_py))

    window = _training_window(qapp, tmp_path, _fake_pipeline()[0])
    assert window.training_install_button.isHidden()
    assert not window.training_reinstall_button.isHidden()
    assert "готово" in window.training_env_label.text()


def test_reinstall_env_asks_first(qapp, tmp_path, monkeypatch):
    import desktop_browser.app as app_module
    import desktop_browser.training as training

    env_py = tmp_path / "training_env" / "Scripts" / "python.exe"
    monkeypatch.setattr(training, "detect_training_env", lambda env_dir: str(env_py))
    window = _training_window(qapp, tmp_path, _fake_pipeline()[0])
    installs = []
    monkeypatch.setattr(window, "install_training_env", lambda: installs.append(1))
    monkeypatch.setattr(
        app_module.QMessageBox, "question", lambda *a, **k: app_module.QMessageBox.No
    )
    window.training_reinstall_button.click()
    assert installs == []
    monkeypatch.setattr(
        app_module.QMessageBox, "question", lambda *a, **k: app_module.QMessageBox.Yes
    )
    window.training_reinstall_button.click()
    assert installs == [1]


def test_failed_reinstall_offers_install_again(qapp, tmp_path, monkeypatch):
    import desktop_browser.training as training

    window = _training_window(qapp, tmp_path, _fake_pipeline()[0])
    monkeypatch.setattr(training, "detect_training_env", lambda env_dir: None)
    monkeypatch.setattr(training, "_detect_trainer", lambda log, env=None: (None, "", None))
    window.env_installing = True
    window._on_env_install_failed("Команда завершилась с кодом 1")
    assert not window.training_install_button.isHidden()
    assert window.training_reinstall_button.isHidden()


# ── license dialog & badge ──────────────────────────────────────────────────


def _pro_license():
    from licensing.models import License

    return License(email="djonros@gmail.com", plan="pro")


def test_license_dialog_free_status(qapp, tmp_path, monkeypatch):
    import desktop_browser.license_dialog as ld

    monkeypatch.setattr(ld, "load_activation", lambda path=None: None)
    dialog = ld.LicenseDialog()
    assert dialog.status_label.text() == "Текущий план: FREE"
    assert not dialog.deactivate_button.isEnabled()
    assert not dialog.buy_button.isHidden()
    opened = []
    monkeypatch.setattr(ld.QDesktopServices, "openUrl", lambda url: opened.append(url.toString()))
    dialog.buy_button.click()
    assert opened == [ld.PRO_PAGE_URL]


def test_license_dialog_activate_success(qapp, tmp_path, monkeypatch):
    import desktop_browser.license_dialog as ld

    lic = _pro_license()
    state = {"active": None}
    monkeypatch.setattr(ld, "activate_text", lambda key, path=None: state.update(active=key) or lic)
    monkeypatch.setattr(ld, "load_activation", lambda path=None: lic)

    dialog = ld.LicenseDialog()
    dialog.key_input.setText(" test.key.value ")
    dialog.activate_button.click()

    assert state["active"] == "test.key.value"
    assert "PRO" in dialog.status_label.text()
    assert "djonros@gmail.com" in dialog.email_label.text()
    assert dialog.expires_label.text() == "Срок: бессрочная"
    assert "Активирована" in dialog.message_label.text()
    assert dialog.key_input.text() == ""
    assert dialog.deactivate_button.isEnabled()


def test_license_dialog_activate_error(qapp, tmp_path, monkeypatch):
    import desktop_browser.license_dialog as ld
    from licensing import LicenseError

    monkeypatch.setattr(ld, "load_activation", lambda path=None: None)

    def _fail(key, path=None):
        raise LicenseError("malformed license key")

    monkeypatch.setattr(ld, "activate_text", _fail)
    dialog = ld.LicenseDialog()
    dialog.key_input.setText("bad-key")
    dialog.activate_button.click()

    assert "Ошибка активации" in dialog.message_label.text()
    assert dialog.status_label.text() == "Текущий план: FREE"


def test_license_dialog_activate_empty_key(qapp, tmp_path, monkeypatch):
    import desktop_browser.license_dialog as ld

    called = {"activate": False}
    monkeypatch.setattr(
        ld, "activate_text", lambda key, path=None: called.update(activate=True) or _pro_license()
    )
    monkeypatch.setattr(ld, "load_activation", lambda path=None: None)
    dialog = ld.LicenseDialog()
    dialog.activate_button.click()
    assert not called["activate"]
    assert "Вставьте ключ" in dialog.message_label.text()


def test_license_dialog_explains_lapsed_updates(qapp, tmp_path, monkeypatch):
    from datetime import datetime, timezone

    import desktop_browser.license_dialog as ld
    import licensing.release as release
    from licensing.models import License

    lic = License(
        email="buyer@example.com", plan="pro",
        issued_at=datetime(2026, 10, 7, tzinfo=timezone.utc),
    )
    monkeypatch.setattr(ld, "load_activation", lambda path=None: lic)

    monkeypatch.setattr(release, "RELEASE_DATE", "2027-01-10")
    dialog = ld.LicenseDialog()
    assert dialog.status_label.text() == "Текущий план: PRO"
    assert "обновления до 2027-10-07" in dialog.expires_label.text()
    assert dialog.buy_button.isHidden()

    monkeypatch.setattr(release, "RELEASE_DATE", "2028-02-01")
    dialog = ld.LicenseDialog()
    assert dialog.status_label.text() == "Текущий план: PRO (обновления закончились)"
    assert "2027-10-07" in dialog.message_label.text()
    assert not dialog.buy_button.isHidden()


def test_license_dialog_deactivate(qapp, tmp_path, monkeypatch):
    import desktop_browser.license_dialog as ld

    lic = _pro_license()
    state = {"active": lic}

    def _load(path=None):
        return state["active"]

    def _clear(path=None):
        state["active"] = None
        return True

    monkeypatch.setattr(ld, "load_activation", _load)
    monkeypatch.setattr(ld, "clear_activation", _clear)
    dialog = ld.LicenseDialog()
    assert "PRO" in dialog.status_label.text()

    dialog.deactivate_button.click()
    assert dialog.status_label.text() == "Текущий план: FREE"
    assert "деактивирована" in dialog.message_label.text()
    assert not dialog.deactivate_button.isEnabled()


def test_window_license_badge_free(qapp, tmp_path, monkeypatch):
    import desktop_browser.app as app_module

    monkeypatch.setattr(app_module, "load_activation", lambda: None)
    window = _window(qapp, tmp_path)
    assert window.license_button.text() == "FREE"
    assert window.act_license.text() == "Лицензия: FREE"
    assert window.license_button.property("plan") == "free"


def test_window_license_badge_pro_and_refresh(qapp, tmp_path, monkeypatch):
    import desktop_browser.app as app_module

    state = {"lic": _pro_license()}
    monkeypatch.setattr(app_module, "load_activation", lambda: state["lic"])
    window = _window(qapp, tmp_path)
    assert window.license_button.text() == "PRO"
    assert window.act_license.text() == "Лицензия: PRO"
    assert window.license_button.property("plan") == "pro"
    assert window.license_button.toolTip() == "Лицензия PRO: djonros@gmail.com"

    state["lic"] = None
    window._refresh_license_badge()
    assert window.license_button.text() == "FREE"
    assert window.act_license.text() == "Лицензия: FREE"


# ── project page ────────────────────────────────────────────────────────────


class FakeRagService:
    def __init__(self, count=5, error=None, delay=0.0):
        self.count_value = count
        self.error = error
        self.delay = delay
        self.indexed_paths = []

    def count(self):
        return self.count_value

    def index(self, path):
        import time as _time

        self.indexed_paths.append(path)
        if self.delay:
            _time.sleep(self.delay)
        if self.error:
            raise self.error
        return self.count_value


def test_project_page_indexes_folder(qapp, tmp_path):
    rag = FakeRagService(count=12)
    window = _window(qapp, tmp_path)
    window.rag_service = rag
    window.refresh_project_status()
    assert window.index_status.text() == "RAG: 12 чанков в индексе"

    window.project_path.setText(str(tmp_path))
    window._start_indexing()
    assert window.indexing
    assert not window.index_button.isEnabled()

    _drain(qapp, window)

    assert not window.indexing
    assert rag.indexed_paths == [str(tmp_path)]
    assert "12 чанков" in window.project_view.toPlainText()
    assert window.index_button.isEnabled()
    assert not window.index_stop_button.isVisible()


def test_project_page_index_error(qapp, tmp_path):
    rag = FakeRagService(error=RuntimeError("boom"))
    window = _window(qapp, tmp_path)
    window.rag_service = rag
    window.project_path.setText(str(tmp_path))
    window._start_indexing()
    _drain(qapp, window)

    assert not window.indexing
    assert "Ошибка индексации" in window.project_view.toPlainText()


def test_project_page_without_rag(qapp, tmp_path):
    window = _window(qapp, tmp_path)
    assert window.index_status.text() == "RAG: недоступен (движок не подключён)"
    window.project_path.setText(str(tmp_path))
    window._start_indexing()
    assert not window.indexing
    assert "Движок не подключён" in window.project_view.toPlainText()


def test_project_page_requires_path(qapp, tmp_path):
    rag = FakeRagService()
    window = _window(qapp, tmp_path)
    window.rag_service = rag
    window._start_indexing()
    assert rag.indexed_paths == []
    assert not window.indexing


def test_project_path_persisted(qapp, tmp_path):
    ini = tmp_path / "ui.ini"
    qsettings = QSettings(str(ini), QSettings.IniFormat)
    qsettings.setValue("project_path", "C:/code/my-project")
    qsettings.sync()

    window = FluxionWindow(rag_service=FakeRagService(), qsettings=QSettings(str(ini), QSettings.IniFormat))
    assert window.project_path.text() == "C:/code/my-project"


def test_training_preset_file_fills_form_and_reaches_pipeline(qapp, tmp_path, monkeypatch):
    import licensing

    monkeypatch.setattr(licensing, "feature_enabled", lambda feature: True)
    pipeline, captured = _fake_pipeline()
    window = _training_window(qapp, tmp_path, pipeline)
    preset = Path(__file__).resolve().parents[1] / "presets" / "training" / "quick-check.json"

    assert window._load_training_preset_file(str(preset))
    assert window.training_preset.currentData() == "low"
    assert window.training_epochs.value() == 1
    assert window.training_max_samples.value() == 200
    assert "Быстрая проверка" in window.training_preset_chip.text()

    window.training_dataset.setText("ds.jsonl")
    window.run_training()
    _drain(qapp, window)
    assert captured[0].preset_file == str(preset)

    # choosing a built-in preset again drops the file
    window.training_preset.setCurrentIndex(window.training_preset.findData("standard"))
    assert window._training_preset_path == ""
    assert window.training_preset_chip.text() == "встроенный пресет"


def test_training_preset_file_invalid_shows_reason(qapp, tmp_path, monkeypatch):
    import licensing

    monkeypatch.setattr(licensing, "feature_enabled", lambda feature: True)
    pipeline, _ = _fake_pipeline()
    window = _training_window(qapp, tmp_path, pipeline)
    bad = tmp_path / "bad.json"
    bad.write_text('{"format": "fluxion-training-preset", "version": 1, "id": "x", "name": "x", '
                   '"trainer": "low", "base_model": "attacker/evil"}', encoding="utf-8")

    assert not window._load_training_preset_file(str(bad))
    assert window._training_preset_path == ""
    assert "не поддерживается" in window.training_view.toPlainText()


def test_license_dialog_activates_key_file(qapp, tmp_path, monkeypatch):
    import desktop_browser.license_dialog as ld

    key_file = tmp_path / "client-20261001.key"
    key_file.write_text("key", encoding="utf-8")
    lic = _pro_license()
    state = {"file": None, "active": None}
    monkeypatch.setattr(ld, "load_activation", lambda path=None: state["active"])

    def _activate_file(path, target=None):
        state["file"] = str(path)
        state["active"] = lic
        return lic

    monkeypatch.setattr(ld, "activate_file", _activate_file)
    monkeypatch.setattr(ld.QFileDialog, "getOpenFileName", staticmethod(lambda *a, **k: (str(key_file), "")))
    dialog = ld.LicenseDialog(search_dirs=[])
    dialog.file_button.click()
    assert state["file"] == str(key_file)
    assert "PRO" in dialog.status_label.text()
    assert "Активирована" in dialog.message_label.text()


def test_license_dialog_offers_found_key(qapp, tmp_path, monkeypatch):
    import desktop_browser.license_dialog as ld

    found = tmp_path / "fluxion-license.key"
    found.write_text("key", encoding="utf-8")
    lic = _pro_license()
    state = {"active": None, "file": None}
    monkeypatch.setattr(ld, "load_activation", lambda path=None: state["active"])
    monkeypatch.setattr(ld, "find_key_files", lambda dirs=None: [found])

    def _activate_file(path, target=None):
        state.update(file=str(path), active=lic)
        return lic

    monkeypatch.setattr(ld, "activate_file", _activate_file)
    dialog = ld.LicenseDialog(search_dirs=[tmp_path])
    assert not dialog.found_button.isHidden()
    assert "fluxion-license.key" in dialog.found_button.text()
    dialog.found_button.click()
    assert state["file"] == str(found)
    assert dialog.found_button.isHidden()


def test_license_dialog_no_offer_when_pro(qapp, monkeypatch, tmp_path):
    import desktop_browser.license_dialog as ld

    monkeypatch.setattr(ld, "load_activation", lambda path=None: _pro_license())
    monkeypatch.setattr(ld, "find_key_files", lambda dirs=None: [tmp_path / "x.key"])
    dialog = ld.LicenseDialog(search_dirs=[tmp_path])
    assert dialog.found_button.isHidden()


def test_training_advanced_settings_open_readably(qapp, tmp_path, monkeypatch):
    """The advanced block used to squeeze its fields into thin strips."""
    import licensing
    from PySide6.QtWidgets import QScrollArea

    monkeypatch.setattr(licensing, "feature_enabled", lambda feature: True)
    pipeline, _ = _fake_pipeline()
    window = _training_window(qapp, tmp_path, pipeline)
    assert window.training_advanced.isHidden()
    assert window.training_advanced_button.text().startswith("▸")

    window.training_advanced_button.setChecked(True)
    assert not window.training_advanced.isHidden()
    assert window.training_advanced_button.text().startswith("▾")
    # the page scrolls instead of compressing the form
    parent = window.training_advanced.parentWidget()
    while parent is not None and not isinstance(parent, QScrollArea):
        parent = parent.parentWidget()
    assert isinstance(parent, QScrollArea)
    assert window.training_view.minimumHeight() >= 200
    # plain-language labels, no Ollama jargon by default
    assert window.training_export.text() == "Собрать готовую модель для чата (GGUF)"
    assert window.training_export_note.text()

    window.training_advanced_button.setChecked(False)
    assert window.training_advanced.isHidden()


def test_dataset_chip_warns_about_unknown_format(qapp, tmp_path):
    window = _window(qapp, tmp_path)
    good = tmp_path / "good.jsonl"
    good.write_text('{"instruction": "q", "output": "a"}\n', encoding="utf-8")
    window.training_dataset.setText(str(good))
    assert "✓" in window.dataset_chip.text()
    bad = tmp_path / "bad.jsonl"
    bad.write_text('{"text": "t"}\n', encoding="utf-8")
    window.training_dataset.setText(str(bad))
    assert "формат не распознан" in window.dataset_chip.text()
    assert "Alpaca" in window.dataset_chip.toolTip()


def test_training_log_is_mirrored_to_a_file(qapp, tmp_path, monkeypatch):
    """The window may die with the process; the log on disk must survive."""
    import desktop_browser.crash as crash

    monkeypatch.setattr(crash, "crash_dir", lambda: tmp_path)
    window = _window(qapp, tmp_path)
    window._start_activity("Обучение идёт")
    window._note_training_log_path()
    window._on_training_log("Loading weights: 100% <ok> & done")
    window._training_log_html("<b>Готово.</b> Адаптер: a")
    path = window.training_log_path
    assert path.parent == tmp_path and path.name.startswith("training-")
    text = path.read_text(encoding="utf-8")  # flushed while still open
    assert "Loading weights: 100% <ok> & done" in text
    assert "Готово. Адаптер: a" in text
    window._stop_activity()
    assert window._training_log_file is None


def test_native_crashes_are_recorded(tmp_path, monkeypatch):
    import faulthandler

    import desktop_browser.crash as crash

    monkeypatch.setattr(crash, "crash_dir", lambda: tmp_path)
    try:
        path = crash.enable_native_crash_log()
        assert path == tmp_path / "native-crash.log"
        assert faulthandler.is_enabled()
        assert "started" in path.read_text(encoding="utf-8")
    finally:
        faulthandler.enable(file=sys.__stderr__)  # what pytest had before
        if crash._native_log is not None:
            crash._native_log.close()
            crash._native_log = None


class _FakeBox:
    """Stand-in for QMessageBox: clicks the button whose label starts with `answer`."""

    answer = ""
    Question = AcceptRole = DestructiveRole = RejectRole = 0

    def __init__(self, parent=None):
        self.buttons = []
        self.text = ""

    def setIcon(self, icon): pass
    def setWindowTitle(self, title): pass
    def setDefaultButton(self, button): pass

    def setText(self, text):
        self.text = text
        type(self).last_text = text

    def addButton(self, label, role):
        self.buttons.append(label)
        return label

    def exec(self):
        return 0

    def clickedButton(self):
        return next(b for b in self.buttons if b.startswith(type(self).answer))


@pytest.mark.parametrize("answer, expected", [("Взять", 5000), ("Весь", 0), ("Отмена", None)])
def test_huge_dataset_asks_to_take_a_part(qapp, tmp_path, monkeypatch, answer, expected):
    import desktop_browser.app as app_module

    dataset = tmp_path / "big.jsonl"
    line = '{"question": "q", "answer": "a"}\n'
    dataset.write_text(line * 25_000, encoding="utf-8")
    window = _window(qapp, tmp_path)
    monkeypatch.setattr(_FakeBox, "answer", answer)
    monkeypatch.setattr(app_module, "QMessageBox", _FakeBox)
    assert window._confirm_dataset_size(str(dataset)) == expected
    assert "25 000" in _FakeBox.last_text
    if expected:
        assert window.training_max_samples.value() == 5000


def test_small_dataset_or_explicit_limit_is_not_questioned(qapp, tmp_path, monkeypatch):
    import desktop_browser.app as app_module

    dataset = tmp_path / "big.jsonl"
    dataset.write_text('{"question": "q", "answer": "a"}\n' * 25_000, encoding="utf-8")
    small = tmp_path / "small.jsonl"
    small.write_text('{"question": "q", "answer": "a"}\n' * 10, encoding="utf-8")
    monkeypatch.setattr(app_module, "QMessageBox", None)  # any dialog would fail
    window = _window(qapp, tmp_path)
    assert window._confirm_dataset_size(str(small)) == 0
    window.training_max_samples.setValue(300)
    assert window._confirm_dataset_size(str(dataset)) == 300


def test_training_frees_the_chat_model(qapp, tmp_path, monkeypatch):
    import licensing

    monkeypatch.setattr(licensing, "feature_enabled", lambda feature: True)
    pipeline, _ = _fake_pipeline()
    window = _training_window(qapp, tmp_path, pipeline)
    unloaded = []
    window.backend.unload = lambda: unloaded.append(True)
    dataset = tmp_path / "ds.jsonl"
    dataset.write_text('{"question": "q", "answer": "a"}\n', encoding="utf-8")
    window.training_dataset.setText(str(dataset))
    window.run_training()
    _drain(qapp, window)
    assert unloaded == [True]
    assert "Модель чата выгружена" in window.training_view.toPlainText()
