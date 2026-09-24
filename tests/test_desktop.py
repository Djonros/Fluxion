"""Desktop GUI tests (offscreen Qt).

Covers: streaming chat reply, conversation history, stop button,
no-engine fallback and theme persistence via injected QSettings.
"""
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import time
from types import SimpleNamespace

import pytest
from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QApplication

from cli.theme import DARK, LIGHT
from desktop.app import FluxionWindow


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
    assert second.theme_button.text() == "Тёмная тема"


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


def test_agent_streams_steps(qapp, tmp_path):
    steps = [
        _step(1, thought="Прочитаю файл", tool="read_file", args="main.py", observation="print('hi')"),
        _step(2, tool="finish", args="Готово"),
    ]
    window, created = _agent_window(qapp, tmp_path, steps, _agent_result())
    window.agent_task.setText("Прочитай main.py")
    window.agent_iterations.setValue(3)
    window.run_agent()
    _drain(qapp, window, timeout=5.0)

    assert not window.agent_running
    text = window.agent_view.toPlainText()
    assert "Итерация 1" in text
    assert "read_file" in text
    assert "Агент выполнено" in text or "выполнено" in text
    assert "Готово" in text
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
    window.agent_task.setText("Задача")
    window.run_agent()

    deadline = time.time() + 5
    while "Итерация 1" not in window.agent_view.toPlainText() and time.time() < deadline:
        qapp.processEvents()
        time.sleep(0.01)

    window.stop_agent()
    _drain(qapp, window)

    assert not window.agent_running
    text = window.agent_view.toPlainText()
    assert "Остановлено пользователем" in text
    assert "Итерация 1" in text
    assert not window.agent_stop_button.isVisible()


def test_agent_write_gate_blocked_without_pro(qapp, tmp_path, monkeypatch):
    import licensing

    monkeypatch.setattr(licensing, "feature_enabled", lambda feature: False)
    window, _ = _agent_window(qapp, tmp_path, [], _agent_result())

    window.agent_write.setChecked(True)
    assert not window.agent_write.isChecked()
    assert "Pro" in window.agent_view.toPlainText()


def test_agent_write_allowed_with_pro(qapp, tmp_path, monkeypatch):
    import licensing

    monkeypatch.setattr(licensing, "feature_enabled", lambda feature: True)
    steps = [_step(1, tool="write_file", args="x.py", observation="Wrote x.py")]
    window, created = _agent_window(qapp, tmp_path, steps, _agent_result())
    window.agent_write.setChecked(True)
    assert window.agent_write.isChecked()

    window.agent_task.setText("Создай файл")
    window.run_agent()
    _drain(qapp, window)
    assert created[0].allow_write


def test_agent_without_factory_shows_message(qapp, tmp_path):
    window = _window(qapp, tmp_path)
    window.agent_task.setText("Задача")
    window.run_agent()
    assert "Движок не подключён" in window.agent_view.toPlainText()
    assert not window.agent_running


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
    from desktop.training import TrainingError

    pipeline, _ = _fake_pipeline(error=TrainingError("нет окружения"))
    window = _training_window(qapp, tmp_path, pipeline)

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
    window.stop_training()
    _drain(qapp, window)

    assert not window.training_running
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
    import desktop.training as training

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
    import desktop.training as training
    from desktop.app import QMessageBox

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
    import desktop.training as training
    from desktop.app import QMessageBox

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
    import desktop.training as training

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
    import desktop.training as training

    env_py = tmp_path / "training_env" / "Scripts" / "python.exe"
    monkeypatch.setattr(training, "detect_training_env", lambda env_dir: str(env_py))

    window = _training_window(qapp, tmp_path, _fake_pipeline()[0])
    assert window.training_install_button.isHidden()
    assert "готово" in window.training_env_label.text()


# ── license dialog & badge ──────────────────────────────────────────────────


def _pro_license():
    from licensing.models import License

    return License(email="djonros@gmail.com", plan="pro")


def test_license_dialog_free_status(qapp, tmp_path, monkeypatch):
    import desktop.license_dialog as ld

    monkeypatch.setattr(ld, "load_activation", lambda path=None: None)
    dialog = ld.LicenseDialog()
    assert dialog.status_label.text() == "Текущий план: FREE"
    assert not dialog.deactivate_button.isEnabled()


def test_license_dialog_activate_success(qapp, tmp_path, monkeypatch):
    import desktop.license_dialog as ld

    lic = _pro_license()
    state = {"active": None}
    monkeypatch.setattr(ld, "activate", lambda key, path=None: state.update(active=key) or lic)
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
    import desktop.license_dialog as ld
    from licensing import LicenseError

    monkeypatch.setattr(ld, "load_activation", lambda path=None: None)

    def _fail(key, path=None):
        raise LicenseError("malformed license key")

    monkeypatch.setattr(ld, "activate", _fail)
    dialog = ld.LicenseDialog()
    dialog.key_input.setText("bad-key")
    dialog.activate_button.click()

    assert "Ошибка активации" in dialog.message_label.text()
    assert dialog.status_label.text() == "Текущий план: FREE"


def test_license_dialog_activate_empty_key(qapp, tmp_path, monkeypatch):
    import desktop.license_dialog as ld

    called = {"activate": False}
    monkeypatch.setattr(
        ld, "activate", lambda key, path=None: called.update(activate=True) or _pro_license()
    )
    monkeypatch.setattr(ld, "load_activation", lambda path=None: None)
    dialog = ld.LicenseDialog()
    dialog.activate_button.click()
    assert not called["activate"]
    assert "Вставьте ключ" in dialog.message_label.text()


def test_license_dialog_deactivate(qapp, tmp_path, monkeypatch):
    import desktop.license_dialog as ld

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
    import desktop.app as app_module

    monkeypatch.setattr(app_module, "load_activation", lambda: None)
    window = _window(qapp, tmp_path)
    assert window.license_button.text() == "Лицензия: FREE"
    assert window.license_button.property("plan") == "free"


def test_window_license_badge_pro_and_refresh(qapp, tmp_path, monkeypatch):
    import desktop.app as app_module

    state = {"lic": _pro_license()}
    monkeypatch.setattr(app_module, "load_activation", lambda: state["lic"])
    window = _window(qapp, tmp_path)
    assert window.license_button.text() == "Лицензия: PRO"
    assert window.license_button.property("plan") == "pro"
    assert window.license_button.toolTip() == "djonros@gmail.com"

    state["lic"] = None
    window._refresh_license_badge()
    assert window.license_button.text() == "Лицензия: FREE"


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
