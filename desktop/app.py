from __future__ import annotations

import html
import sys
from pathlib import Path

from PySide6.QtCore import QSettings, QThread, QTimer, Signal
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import (
    QApplication,
    QButtonGroup,
    QCheckBox,
    QComboBox,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QStackedWidget,
    QTextBrowser,
    QVBoxLayout,
    QWidget,
)

from cli.theme import DARK, LIGHT, Theme
from core.language import LANG_NAMES, matches_language, resolve_target
from licensing.store import load_activation
from .training import TrainingError, TrainingParams, run_pipeline


APP_ROOT = Path(__file__).resolve().parent.parent

WELCOME_HTML = (
    "<h3>Добро пожаловать в Fluxion</h3>"
    "<p>Спросите о коде, архитектуре или начните с примера сверху.</p>"
)


class ChatWorker(QThread):
    """Streams tokens from Assistant.ask_stream in a background thread."""

    token = Signal(str)
    meta = Signal(object)
    failed = Signal(str)
    finished_ok = Signal()
    text_reset = Signal()

    def __init__(self, assistant, query: str, history: list[dict[str, str]], parent=None):
        super().__init__(parent)
        self.assistant = assistant
        self.query = query
        self.history = history
        self.target = ""
        self._stop = False

    def stop(self) -> None:
        self._stop = True

    def run(self) -> None:
        query = self.query
        for attempt in (1, 2):
            try:
                result, stream = self.assistant.ask_stream(query=query, history=self.history)
            except Exception as exc:
                self.failed.emit(f"Ошибка генерации: {exc}")
                return
            if attempt == 1:
                self.meta.emit(result)
            joined = ""
            mismatch = False
            try:
                for chunk in stream:
                    if self._stop:
                        break
                    joined += chunk
                    self.token.emit(chunk)
                    if (
                        attempt == 1
                        and self.target
                        and not mismatch
                        and len(joined) > 240
                        and not matches_language(joined, self.target)
                    ):
                        mismatch = True
                        break
            except Exception as exc:
                self.failed.emit(f"Ошибка генерации: {exc}")
                return
            if mismatch and not self._stop:
                self.text_reset.emit()
                self.history.append({"role": "user", "content": query})
                self.history.append({"role": "assistant", "content": joined})
                query = (
                    "[system] Перепиши предыдущий ответ строго на "
                    f"{LANG_NAMES[self.target]} языке, сохранив смысл и код."
                )
                continue
            break
        self.finished_ok.emit()


class StatusWorker(QThread):
    """Checks backend availability without blocking the UI thread."""

    result = Signal(bool)

    def __init__(self, backend, parent=None):
        super().__init__(parent)
        self.backend = backend

    def run(self) -> None:
        try:
            available = bool(self.backend.is_available())
        except Exception:
            available = False
        self.result.emit(available)


class AgentWorker(QThread):
    """Runs CodingAgent.run_iter in a background thread, emitting steps."""

    step = Signal(object)
    done = Signal(dict)
    failed = Signal(str)

    def __init__(self, agent_factory, task: str, allow_write: bool, max_iter: int, parent=None):
        super().__init__(parent)
        self.agent_factory = agent_factory
        self.task = task
        self.allow_write = allow_write
        self.max_iter = max_iter
        self._stop = False

    def stop(self) -> None:
        self._stop = True

    def run(self) -> None:
        try:
            agent = self.agent_factory(self.task, self.allow_write, self.max_iter)
        except Exception as exc:
            self.failed.emit(f"Ошибка запуска агента: {exc}")
            return

        cancelled = False
        result = None
        try:
            gen = agent.run_iter(self.task)
            while True:
                try:
                    step = next(gen)
                except StopIteration as stop:
                    result = stop.value or getattr(agent, "last_result", None)
                    break
                self.step.emit(step)
                if self._stop:
                    cancelled = True
                    result = getattr(agent, "last_result", None)
                    break
        except Exception as exc:
            self.failed.emit(f"Ошибка агента: {exc}")
            return

        self.done.emit({
            "success": bool(getattr(result, "success", False)),
            "final_answer": getattr(result, "final_answer", "") or "",
            "iterations_used": getattr(result, "iterations_used", 0) or 0,
            "cancelled": cancelled,
        })


class IndexWorker(QThread):
    """Indexes a directory into RAG without blocking the UI thread."""

    done = Signal(int)
    failed = Signal(str)

    def __init__(self, rag_service, path: str, parent=None):
        super().__init__(parent)
        self.rag_service = rag_service
        self.path = path

    def run(self) -> None:
        try:
            count = self.rag_service.index(self.path)
        except Exception as exc:
            self.failed.emit(f"Ошибка индексации: {exc}")
            return
        self.done.emit(int(count))


class ModelListWorker(QThread):
    """Fetches available Ollama models without blocking the UI thread."""

    done = Signal(list)
    failed = Signal(str)

    def __init__(self, backend, parent=None):
        super().__init__(parent)
        self.backend = backend

    def run(self) -> None:
        client = getattr(self.backend, "client", None)
        if client is None or not hasattr(client, "list_models"):
            self.failed.emit("Смена моделей доступна только для Ollama-движка")
            return
        try:
            models = list(client.list_models())
        except Exception as exc:
            self.failed.emit(f"Не удалось получить список моделей: {exc}")
            return
        self.done.emit(models)


class TrainingWorker(QThread):
    """Runs the finetune pipeline in a background thread."""

    log = Signal(str)
    done = Signal(object)
    failed = Signal(str)

    def __init__(self, pipeline, params: TrainingParams, parent=None):
        super().__init__(parent)
        self.pipeline = pipeline
        self.params = params
        self._stop = False

    def stop(self) -> None:
        self._stop = True

    def run(self) -> None:
        try:
            result = self.pipeline(
                self.params, log=self.log.emit, stop_requested=lambda: self._stop
            )
        except TrainingError as exc:
            self.failed.emit(str(exc))
            return
        except SystemExit as exc:
            self.failed.emit(f"Обучение прервано (exit {exc.code})")
            return
        except Exception as exc:
            self.failed.emit(f"Ошибка обучения: {exc}")
            return
        self.done.emit(result)


class EnvWorker(QThread):
    """Installs the training venv in a background thread."""

    log = Signal(str)
    done = Signal()
    failed = Signal(str)

    def __init__(self, installer, parent=None):
        super().__init__(parent)
        self.installer = installer
        self._stop = False

    def stop(self) -> None:
        self._stop = True

    def run(self) -> None:
        try:
            self.installer(log=self.log.emit, stop_requested=lambda: self._stop)
        except TrainingError as exc:
            self.failed.emit(str(exc))
            return
        except Exception as exc:
            self.failed.emit(f"Ошибка установки окружения: {exc}")
            return
        self.done.emit()


class FluxionWindow(QMainWindow):
    def __init__(
        self,
        assistant=None,
        backend=None,
        settings=None,
        qsettings: QSettings | None = None,
        agent_factory=None,
        rag_service=None,
        training_pipeline=None,
    ) -> None:
        super().__init__()
        self.assistant = assistant
        self.backend = backend
        self.settings = settings
        self.agent_factory = agent_factory
        self.rag_service = rag_service
        self.training_pipeline = training_pipeline
        self.qsettings = qsettings if qsettings is not None else QSettings("Fluxion", "Fluxion")
        self.theme = LIGHT if self.qsettings.value("theme", "dark") == "light" else DARK
        self.history: list[dict[str, str]] = []
        self._messages: list[dict[str, str]] = []
        self._think_frame = 0
        self._think_timer = QTimer(self)
        self._think_timer.setInterval(400)
        self._think_timer.timeout.connect(self._on_think_tick)
        self.worker: ChatWorker | None = None
        self.status_worker: StatusWorker | None = None
        self.agent_worker: AgentWorker | None = None
        self.index_worker: IndexWorker | None = None
        self._model_worker: ModelListWorker | None = None
        self.training_worker: TrainingWorker | None = None
        self.env_worker: EnvWorker | None = None
        self._applying_model = False
        self.generating = False
        self.agent_running = False
        self.indexing = False
        self.training_running = False
        self.env_installing = False
        saved_model = str(self.qsettings.value("model", "") or "")
        if (
            saved_model
            and self.settings is not None
            and getattr(self.settings, "model", "") != saved_model
        ):
            self.settings.model = saved_model
            client = getattr(self.backend, "client", None)
            if client is not None and hasattr(client, "model"):
                client.model = saved_model
        self.setWindowTitle("Fluxion")
        self.setMinimumSize(920, 620)
        self.resize(1120, 760)
        self.setWindowIcon(QIcon(str(APP_ROOT / "assets" / "icon.png")))
        self._build_ui()
        self.apply_theme(self.theme)
        self._refresh_license_badge()
        self._start_status_check()
        self._load_models()

    def _build_ui(self) -> None:
        root = QWidget()
        layout = QHBoxLayout(root)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        self.sidebar = QFrame()
        self.sidebar.setObjectName("sidebar")
        self.sidebar.setFixedWidth(220)
        sidebar_layout = QVBoxLayout(self.sidebar)
        sidebar_layout.setContentsMargins(20, 24, 20, 20)
        sidebar_layout.setSpacing(10)

        brand = QLabel("∞  Fluxion")
        brand.setObjectName("brand")
        tagline = QLabel("Локальный AI-ассистент")
        tagline.setObjectName("tagline")
        sidebar_layout.addWidget(brand)
        sidebar_layout.addWidget(tagline)
        sidebar_layout.addSpacing(22)

        self.pages = QStackedWidget()
        self.nav_group = QButtonGroup(self)
        self.nav_group.setExclusive(True)
        for index, title in enumerate(("Чат", "Проект", "Агент", "Обучение")):
            button = QPushButton(title)
            button.setCheckable(True)
            button.setProperty("nav", True)
            button.clicked.connect(lambda checked=False, page=index: self.pages.setCurrentIndex(page))
            self.nav_group.addButton(button)
            sidebar_layout.addWidget(button)
            if index == 0:
                button.setChecked(True)

        sidebar_layout.addStretch()
        self.status_label = QLabel()
        self.status_label.setObjectName("engineStatus")
        sidebar_layout.addWidget(self.status_label)
        self.license_button = QPushButton()
        self.license_button.setObjectName("licenseButton")
        self.license_button.clicked.connect(self._open_license_dialog)
        sidebar_layout.addWidget(self.license_button)
        self.theme_button = QPushButton()
        self.theme_button.setObjectName("themeButton")
        self.theme_button.clicked.connect(self.toggle_theme)
        sidebar_layout.addWidget(self.theme_button)

        self.pages.addWidget(self._chat_page())
        self.pages.addWidget(self._project_page())
        self.pages.addWidget(self._agent_page())
        self.pages.addWidget(self._training_page())

        layout.addWidget(self.sidebar)
        layout.addWidget(self.pages, 1)
        self.setCentralWidget(root)

    def _chat_page(self) -> QWidget:
        page = QWidget()
        page.setObjectName("page")
        layout = QVBoxLayout(page)
        layout.setContentsMargins(36, 30, 36, 28)
        layout.setSpacing(16)

        title = QLabel("Чат")
        title.setObjectName("pageTitle")
        subtitle = QLabel("Спросите Fluxion о коде или начните с одного из примеров.")
        subtitle.setObjectName("subtitle")
        layout.addWidget(title)
        layout.addWidget(subtitle)

        suggestions = QHBoxLayout()
        for text in ("Объясни этот проект", "Найди возможную ошибку", "Предложи рефакторинг"):
            button = QPushButton(text)
            button.setProperty("suggestion", True)
            button.clicked.connect(lambda checked=False, value=text: self.prompt.setText(value))
            suggestions.addWidget(button)
        layout.addLayout(suggestions)

        toolbar = QHBoxLayout()
        toolbar.addWidget(QLabel("Модель:"))
        self.model_combo = QComboBox()
        self.model_combo.setMinimumWidth(230)
        self.model_combo.setEnabled(False)
        self.model_combo.textActivated.connect(self._on_model_selected)
        toolbar.addWidget(self.model_combo)
        self.model_refresh_button = QPushButton("Обновить")
        self.model_refresh_button.setObjectName("themeButton")
        self.model_refresh_button.clicked.connect(self._load_models)
        toolbar.addWidget(self.model_refresh_button)
        toolbar.addStretch()
        self.chat_agent_files = QCheckBox("Агент: доступ к файлам")
        self.chat_agent_files.setToolTip(
            "Сообщение выполнит агент с инструментами проекта; запись файлов — Pro"
        )
        self.chat_agent_files.toggled.connect(self._on_chat_agent_files_toggled)
        toolbar.addWidget(self.chat_agent_files)
        layout.addLayout(toolbar)

        self.messages = QTextBrowser()
        self.messages.setObjectName("messages")
        self.messages.setOpenExternalLinks(False)
        layout.addWidget(self.messages, 1)

        composer = QFrame()
        composer.setObjectName("composer")
        composer_layout = QHBoxLayout(composer)
        composer_layout.setContentsMargins(12, 10, 10, 10)
        self.prompt = QLineEdit()
        self.prompt.setPlaceholderText("Напишите сообщение...")
        self.prompt.returnPressed.connect(self.send_message)
        self.send_button = QPushButton("Отправить")
        self.send_button.setObjectName("sendButton")
        self.send_button.clicked.connect(self.send_message)
        self.stop_button = QPushButton("Стоп")
        self.stop_button.setObjectName("stopButton")
        self.stop_button.clicked.connect(self.stop_generation)
        self.stop_button.setVisible(False)
        composer_layout.addWidget(self.prompt, 1)
        composer_layout.addWidget(self.stop_button)
        composer_layout.addWidget(self.send_button)
        layout.addWidget(composer)

        self._render_messages()
        return page

    # ── project page ─────────────────────────────────────────────────────

    def _project_page(self) -> QWidget:
        page = QWidget()
        page.setObjectName("page")
        layout = QVBoxLayout(page)
        layout.setContentsMargins(36, 30, 36, 28)
        layout.setSpacing(14)

        title = QLabel("Проект")
        title.setObjectName("pageTitle")
        subtitle = QLabel("Выберите папку проекта и проиндексируйте код — чат будет использовать его как контекст.")
        subtitle.setObjectName("subtitle")
        layout.addWidget(title)
        layout.addWidget(subtitle)

        path_row = QHBoxLayout()
        self.project_path = QLineEdit()
        self.project_path.setPlaceholderText("Папка проекта не выбрана")
        self.project_path.setReadOnly(True)
        saved = self.qsettings.value("project_path", "")
        if saved:
            self.project_path.setText(str(saved))
        pick_button = QPushButton("Выбрать…")
        pick_button.setObjectName("themeButton")
        pick_button.clicked.connect(self._pick_project_folder)
        path_row.addWidget(self.project_path, 1)
        path_row.addWidget(pick_button)
        layout.addLayout(path_row)

        actions = QHBoxLayout()
        self.index_button = QPushButton("Проиндексировать")
        self.index_button.setObjectName("sendButton")
        self.index_button.clicked.connect(self._start_indexing)
        self.index_stop_button = QPushButton("Стоп")
        self.index_stop_button.setObjectName("stopButton")
        self.index_stop_button.clicked.connect(self._stop_indexing)
        self.index_stop_button.setVisible(False)
        actions.addWidget(self.index_button)
        actions.addWidget(self.index_stop_button)
        actions.addStretch()
        layout.addLayout(actions)

        self.index_status = QLabel()
        self.index_status.setObjectName("subtitle")
        self.index_status.setText(self._chunks_label())
        layout.addWidget(self.index_status)

        self.project_view = QTextBrowser()
        self.project_view.setObjectName("messages")
        layout.addWidget(self.project_view, 1)
        return page

    def _chunks_label(self) -> str:
        if self.rag_service is None:
            return "RAG: недоступен (движок не подключён)"
        try:
            count = self.rag_service.count()
        except Exception:
            count = 0
        return f"RAG: {count} чанков в индексе"

    def refresh_project_status(self) -> None:
        """Update the RAG chunks label (e.g. after engine wiring)."""
        if not self.indexing:
            self.index_status.setText(self._chunks_label())

    def _pick_project_folder(self) -> None:
        from PySide6.QtWidgets import QFileDialog

        start = self.project_path.text() or str(Path.home())
        folder = QFileDialog.getExistingDirectory(self, "Выберите папку проекта", start)
        if folder:
            self.project_path.setText(folder)
            self.qsettings.setValue("project_path", folder)

    def _start_indexing(self) -> None:
        path = self.project_path.text().strip()
        if not path or self.indexing:
            return
        if self.rag_service is None:
            self.project_view.setHtml("<p>Движок не подключён. Запустите приложение через python -m desktop.</p>")
            return

        self.indexing = True
        self.index_button.setEnabled(False)
        self.index_stop_button.setVisible(True)
        self.index_status.setText("Индексация… первая загрузка модели эмбеддингов может занять время.")
        self.project_view.setHtml(f"<p><b>Индексация:</b> {html.escape(path)}</p>")

        self.index_worker = IndexWorker(self.rag_service, path)
        self.index_worker.done.connect(self._on_index_done)
        self.index_worker.failed.connect(self._on_index_failed)
        self.index_worker.finished.connect(self.index_worker.deleteLater)
        self.index_worker.start()

    def _stop_indexing(self) -> None:
        # Индексация атомарна внутри RAGService — остановка недоступна,
        # кнопка лишь информирует; оставляем завершение естественным путём.
        self.index_status.setText("Завершение текущей операции…")

    def _set_indexing_done(self) -> None:
        self.indexing = False
        self.index_button.setEnabled(True)
        self.index_stop_button.setVisible(False)
        self.index_worker = None

    def _on_index_done(self, count: int) -> None:
        self._set_indexing_done()
        self.index_status.setText(self._chunks_label())
        self.project_view.append(f"<p><b>Готово:</b> проиндексировано {count} чанков. Чат теперь будет автоматически использовать RAG для вопросов по коду.</p>")

    def _on_index_failed(self, message: str) -> None:
        self._set_indexing_done()
        self.index_status.setText(self._chunks_label())
        self.project_view.append(f"<p><b>{html.escape(message)}</b></p>")

    # ── agent page ───────────────────────────────────────────────────────

    def _agent_page(self) -> QWidget:
        page = QWidget()
        page.setObjectName("page")
        layout = QVBoxLayout(page)
        layout.setContentsMargins(36, 30, 36, 28)
        layout.setSpacing(14)

        title = QLabel("Агент")
        title.setObjectName("pageTitle")
        subtitle = QLabel("Опишите задачу — агент решит её пошагово с помощью инструментов.")
        subtitle.setObjectName("subtitle")
        layout.addWidget(title)
        layout.addWidget(subtitle)

        self.agent_task = QLineEdit()
        self.agent_task.setPlaceholderText("Например: прочитай main.py и объясни, что он делает")
        self.agent_task.returnPressed.connect(self.run_agent)
        layout.addWidget(self.agent_task)

        options = QHBoxLayout()
        self.agent_write = QCheckBox("Разрешить запись файлов (Pro)")
        self.agent_write.toggled.connect(self._on_write_toggled)
        options.addWidget(self.agent_write)
        options.addStretch()
        iter_label = QLabel("Макс. итераций:")
        options.addWidget(iter_label)
        self.agent_iterations = QSpinBox()
        self.agent_iterations.setRange(1, 50)
        self.agent_iterations.setValue(8)
        options.addWidget(self.agent_iterations)
        layout.addLayout(options)

        buttons = QHBoxLayout()
        self.agent_run_button = QPushButton("Запустить")
        self.agent_run_button.setObjectName("sendButton")
        self.agent_run_button.clicked.connect(self.run_agent)
        self.agent_stop_button = QPushButton("Стоп")
        self.agent_stop_button.setObjectName("stopButton")
        self.agent_stop_button.clicked.connect(self.stop_agent)
        self.agent_stop_button.setVisible(False)
        buttons.addWidget(self.agent_run_button)
        buttons.addWidget(self.agent_stop_button)
        buttons.addStretch()
        layout.addLayout(buttons)

        self.agent_view = QTextBrowser()
        self.agent_view.setObjectName("messages")
        self.agent_view.setHtml("<p>Готов к запуску. Опишите задачу выше.</p>")
        layout.addWidget(self.agent_view, 1)
        return page

    def _on_write_toggled(self, checked: bool) -> None:
        if not checked:
            return
        from licensing import feature_enabled

        if not feature_enabled("agent_write"):
            self.agent_write.blockSignals(True)
            self.agent_write.setChecked(False)
            self.agent_write.blockSignals(False)
            self.agent_view.setHtml(
                "<p><b>Запись файлов — Pro-функция.</b></p>"
                "<p>Активируйте лицензию в CLI: <code>/license activate &lt;key&gt;</code></p>"
            )

    def run_agent(self) -> None:
        task = self.agent_task.text().strip()
        if not task or self.agent_running or self.generating:
            return
        if self.agent_factory is None:
            self.agent_view.setHtml(
                "<p>Движок не подключён. Запустите приложение через python -m desktop.</p>"
            )
            return

        self._set_agent_running(True)
        self._agent_log(f"<p><b>Задача:</b> {html.escape(task)}</p>")
        self.agent_worker = AgentWorker(
            self.agent_factory,
            task,
            self.agent_write.isChecked(),
            self.agent_iterations.value(),
        )
        self.agent_worker.step.connect(self._on_agent_step)
        self.agent_worker.done.connect(self._on_agent_done)
        self.agent_worker.failed.connect(self._on_agent_failed)
        self.agent_worker.finished.connect(self.agent_worker.deleteLater)
        self.agent_worker.start()
        self.agent_task.clear()

    def stop_agent(self) -> None:
        if self.agent_worker is not None and self.agent_running:
            self.agent_worker.stop()

    def _set_agent_running(self, active: bool) -> None:
        self.agent_running = active
        self.agent_run_button.setEnabled(not active)
        self.agent_stop_button.setVisible(active)
        if not active:
            self.agent_worker = None

    def _agent_log(self, fragment: str) -> None:
        current = self.agent_view.toHtml()
        self.agent_view.setHtml(current + fragment)
        scrollbar = self.agent_view.verticalScrollBar()
        scrollbar.setValue(scrollbar.maximum())

    def _on_agent_step(self, step) -> None:
        parts = [f"<p><b>Итерация {getattr(step, 'iteration', '?')}</b></p>"]
        thought = getattr(step, "thought", "")
        if thought:
            parts.append(f"<p><i>{html.escape(thought[:500])}</i></p>")
        tool = getattr(step, "tool_name", "")
        if tool:
            args = html.escape(getattr(step, "tool_args", "") or "")
            parts.append(f"<p>→ <code>{html.escape(tool)} {args}</code></p>")
        observation = getattr(step, "observation", "")
        if observation:
            text = html.escape(observation[:1500])
            parts.append(f"<pre>{text}</pre>")
        self._agent_log("".join(parts))

    def _on_agent_done(self, result: dict) -> None:
        if result.get("cancelled"):
            self._agent_log("<p><b>Остановлено пользователем.</b></p>")
        status = "выполнено" if result.get("success") else "не завершено"
        self._agent_log(
            f"<p><b>Агент {status}</b> ({result.get('iterations_used', 0)} итер.)</p>"
        )
        answer = result.get("final_answer") or ""
        if answer:
            self._agent_log(f"<p><b>Ответ:</b></p><p>{html.escape(answer)}</p>")
        self._set_agent_running(False)

    def _on_agent_failed(self, message: str) -> None:
        self._agent_log(f"<p><b>{html.escape(message)}</b></p>")
        self._set_agent_running(False)

    # ── training page ────────────────────────────────────────────────────

    def _training_page(self) -> QWidget:
        page = QWidget()
        page.setObjectName("page")
        layout = QVBoxLayout(page)
        layout.setContentsMargins(36, 30, 36, 28)
        layout.setSpacing(14)

        title = QLabel("Обучение")
        title.setObjectName("pageTitle")
        self.training_subtitle = QLabel(
            "QLoRA дообучение на вашем датасете (Pro). Готовая модель появится в списке моделей."
        )
        self.training_subtitle.setObjectName("subtitle")
        layout.addWidget(title)
        layout.addWidget(self.training_subtitle)

        self.training_env_label = QLabel()
        self.training_env_label.setObjectName("subtitle")
        self.training_env_label.setWordWrap(True)
        layout.addWidget(self.training_env_label)
        self.training_install_button = QPushButton("Установить окружение обучения")
        self.training_install_button.setObjectName("sendButton")
        self.training_install_button.clicked.connect(self.install_training_env)
        self.training_install_button.setVisible(False)
        layout.addWidget(self.training_install_button)

        dataset_row = QHBoxLayout()
        self.training_dataset = QLineEdit()
        self.training_dataset.setPlaceholderText("Датасет .jsonl (ChatML, поле messages)")
        dataset_row.addWidget(self.training_dataset, 1)
        self.training_pick_button = QPushButton("Выбрать…")
        self.training_pick_button.setObjectName("themeButton")
        self.training_pick_button.clicked.connect(self._pick_dataset_file)
        dataset_row.addWidget(self.training_pick_button)
        layout.addLayout(dataset_row)

        form = QFormLayout()
        form.setSpacing(10)
        self.training_preset = QComboBox()
        self.training_preset.addItem("low — 6 ГБ VRAM (unsloth)", "low")
        self.training_preset.addItem("standard — 8–12 ГБ (HF peft+trl)", "standard")
        form.addRow("Пресет:", self.training_preset)
        self.training_epochs = QSpinBox()
        self.training_epochs.setRange(1, 20)
        self.training_epochs.setValue(TrainingParams().epochs)
        form.addRow("Эпохи:", self.training_epochs)
        self.training_max_samples = QSpinBox()
        self.training_max_samples.setRange(0, 1_000_000)
        self.training_max_samples.setValue(0)
        self.training_max_samples.setSpecialValueText("все")
        form.addRow("Макс. примеров:", self.training_max_samples)
        self.training_base_model = QLineEdit()
        self.training_base_model.setText(TrainingParams().base_model)
        form.addRow("Базовая модель (HF):", self.training_base_model)
        self.training_adapter_name = QLineEdit()
        self.training_adapter_name.setText(TrainingParams().adapter_name)
        form.addRow("Имя адаптера:", self.training_adapter_name)
        self.training_ollama_model = QLineEdit()
        self.training_ollama_model.setText(TrainingParams().ollama_model_name)
        form.addRow("Имя модели Ollama:", self.training_ollama_model)
        self.training_description = QLineEdit()
        self.training_description.setPlaceholderText("Короткое описание (необязательно)")
        form.addRow("Описание:", self.training_description)
        self.training_export = QCheckBox("Экспорт GGUF + ollama create (нужен LLAMA_CPP_DIR)")
        self.training_export.setChecked(True)
        form.addRow("", self.training_export)
        layout.addLayout(form)

        buttons = QHBoxLayout()
        self.training_run_button = QPushButton("Начать обучение")
        self.training_run_button.setObjectName("sendButton")
        self.training_run_button.clicked.connect(self.run_training)
        self.training_stop_button = QPushButton("Стоп")
        self.training_stop_button.setObjectName("stopButton")
        self.training_stop_button.clicked.connect(self.stop_training)
        self.training_stop_button.setVisible(False)
        buttons.addWidget(self.training_run_button)
        buttons.addWidget(self.training_stop_button)
        buttons.addStretch()
        layout.addLayout(buttons)

        self.training_view = QTextBrowser()
        self.training_view.setObjectName("messages")
        self.training_view.setHtml("<p>QLoRA: обучение → merge → GGUF → Ollama → реестр адаптеров.</p>")
        layout.addWidget(self.training_view, 1)
        return page

    def _pick_dataset_file(self) -> None:
        from PySide6.QtWidgets import QFileDialog

        start = self.training_dataset.text().strip() or str(Path.home())
        path, _ = QFileDialog.getOpenFileName(
            self, "Выберите датасет", start, "JSONL (*.jsonl);;Все файлы (*.*)"
        )
        if path:
            self.training_dataset.setText(path)

    def _refresh_training_gate(self) -> None:
        from licensing import feature_enabled

        allowed = feature_enabled("qlora")
        widgets = (
            self.training_dataset,
            self.training_pick_button,
            self.training_preset,
            self.training_epochs,
            self.training_max_samples,
            self.training_base_model,
            self.training_adapter_name,
            self.training_ollama_model,
            self.training_description,
            self.training_export,
            self.training_run_button,
        )
        for widget in widgets:
            widget.setEnabled(allowed)
        if allowed:
            self.training_subtitle.setText(
                "QLoRA дообучение на вашем датасете (Pro). Готовая модель появится в списке моделей."
            )
        else:
            self.training_subtitle.setText(
                "QLoRA-обучение — Pro-функция. Активируйте лицензию кнопкой «Лицензия» слева."
            )

    def _env_dir(self):
        paths = getattr(self.settings, "paths", None) if self.settings is not None else None
        data_dir = getattr(paths, "data_dir", None)
        if data_dir:
            return Path(data_dir) / "training_env"
        return Path("data") / "training_env"

    def _refresh_training_env(self) -> None:
        from .training import _detect_trainer, detect_training_env

        env_py = detect_training_env(self._env_dir())
        if env_py:
            self.training_env_label.setText(
                f"Окружение обучения: готово (venv: {Path(env_py).parent.parent.name})"
            )
            self.training_install_button.setVisible(False)
            return
        trainer, name, _ = _detect_trainer(lambda message: None)
        if trainer is not None:
            self.training_env_label.setText(
                f"Окружение обучения: системное Python ({name})"
            )
            self.training_install_button.setVisible(False)
            return
        self.training_env_label.setText(
            "Окружение обучения не найдено. Нажмите кнопку — программа сама "
            "проверит/установит Python 3.12 и развернёт venv "
            "(несколько ГБ: torch, unsloth, peft, trl)."
        )
        self.training_install_button.setVisible(True)

    def install_training_env(self) -> None:
        if self.env_installing:
            return
        from .training import (
            find_host_python,
            find_offline_wheels,
            install_training_environment,
        )

        host_python, _ = find_host_python()
        auto_install = False
        if host_python is None:
            answer = QMessageBox.question(
                self,
                "Python не найден",
                "Для обучения нужен Python 3.12, он не найден на компьютере.\n\n"
                "Установить его автоматически через winget?\n"
                "Появится запрос прав администратора (UAC), потребуется интернет.",
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.No,
            )
            if answer != QMessageBox.Yes:
                self._on_training_log(
                    "<b>Установка отменена: установите Python 3.11/3.12 вручную "
                    "и повторите.</b>"
                )
                return
            auto_install = True
        env_dir = self._env_dir()
        offline_wheels = find_offline_wheels()
        if offline_wheels is not None:
            self._on_training_log(
                f"<b>Обнаружен офлайн-пак обучения: {offline_wheels}</b>"
            )
        self.env_installing = True
        self.training_install_button.setEnabled(False)
        self.training_view.setHtml("<p><b>Установка окружения обучения…</b></p>")
        self.env_worker = EnvWorker(
            lambda log, stop_requested: install_training_environment(
                env_dir,
                log=log,
                stop_requested=stop_requested,
                host_python=host_python,
                auto_install_python=auto_install,
                offline_wheels=offline_wheels,
            )
        )
        self.env_worker.log.connect(self._on_training_log)
        self.env_worker.done.connect(self._on_env_installed)
        self.env_worker.failed.connect(self._on_env_install_failed)
        self.env_worker.finished.connect(self.env_worker.deleteLater)
        self.env_worker.start()

    def _on_env_installed(self) -> None:
        self.env_installing = False
        self.training_install_button.setEnabled(True)
        self._on_training_log("Окружение обучения установлено.")
        self._refresh_training_env()

    def _on_env_install_failed(self, message: str) -> None:
        self.env_installing = False
        self.training_install_button.setEnabled(True)
        self._on_training_log(f"<b>{html.escape(message)}</b>")

    def _default_training_pipeline(self):
        def pipeline(params: TrainingParams, *, log, stop_requested):
            return run_pipeline(
                params,
                log=log,
                stop_requested=stop_requested,
                data_dir=self.settings.paths.data_dir,
                client=getattr(self.backend, "client", None),
            )

        return pipeline

    def run_training(self) -> None:
        if self.training_running or self.generating or self.agent_running:
            return
        from licensing import feature_enabled

        if not feature_enabled("qlora"):
            self.training_view.setHtml(
                "<p><b>QLoRA-обучение — Pro-функция.</b></p>"
                "<p>Активируйте лицензию кнопкой «Лицензия» слева.</p>"
            )
            return
        if self.settings is None:
            self.training_view.setHtml(
                "<p>Движок не подключён. Запустите приложение через python -m desktop.</p>"
            )
            return
        dataset = self.training_dataset.text().strip()
        if not dataset:
            self.training_view.setHtml("<p><b>Укажите файл датасета (.jsonl).</b></p>")
            return
        params = TrainingParams(
            dataset=dataset,
            preset=self.training_preset.currentData() or "low",
            epochs=self.training_epochs.value(),
            max_samples=self.training_max_samples.value(),
            base_model=self.training_base_model.text().strip() or TrainingParams().base_model,
            adapter_name=self.training_adapter_name.text().strip() or TrainingParams().adapter_name,
            description=self.training_description.text().strip(),
            ollama_model_name=(
                self.training_ollama_model.text().strip() or TrainingParams().ollama_model_name
            ),
            export_gguf=self.training_export.isChecked(),
        )

        self.training_running = True
        self.training_run_button.setEnabled(False)
        self.training_stop_button.setVisible(True)
        self.training_view.setHtml(f"<p><b>Обучение запущено:</b> {html.escape(params.adapter_name)}</p>")
        pipeline = self.training_pipeline or self._default_training_pipeline()
        self.training_worker = TrainingWorker(pipeline, params)
        self.training_worker.log.connect(self._on_training_log)
        self.training_worker.done.connect(self._on_training_done)
        self.training_worker.failed.connect(self._on_training_failed)
        self.training_worker.finished.connect(self.training_worker.deleteLater)
        self.training_worker.start()

    def stop_training(self) -> None:
        if self.training_worker is not None and self.training_running:
            self.training_worker.stop()
            self._on_training_log("Остановка после текущего этапа…")

    def _set_training_running(self, active: bool) -> None:
        self.training_running = active
        self.training_run_button.setEnabled(not active)
        self.training_stop_button.setVisible(active)
        if not active:
            self.training_worker = None

    def _on_training_log(self, message: str) -> None:
        self.training_view.append(html.escape(message).replace("\n", "<br>"))
        scrollbar = self.training_view.verticalScrollBar()
        scrollbar.setValue(scrollbar.maximum())

    def _on_training_done(self, result: dict) -> None:
        if result.get("cancelled"):
            self._on_training_log("Остановлено пользователем.")
        parts = [
            f"Адаптер: {result.get('adapter', '—')}",
            f"Merged: {result.get('merged', '—')}",
            f"GGUF: {result.get('gguf', '—')}",
            f"Ollama: {'создана' if result.get('created') else 'не создавалась'}",
            f"Реестр: {'зарегистрирован' if result.get('registered') else 'нет'}",
        ]
        self._on_training_log("<b>Готово.</b> " + " | ".join(html.escape(p) for p in parts))
        if result.get("registered") and result.get("ollama_model"):
            try:
                self._on_model_selected(result["ollama_model"])
            except Exception:
                pass
            self._load_models()
        self._set_training_running(False)

    def _on_training_failed(self, message: str) -> None:
        self._on_training_log(f"<b>{html.escape(message)}</b>")
        self._set_training_running(False)

    # ── model selection ─────────────────────────────────────────────────

    def _load_models(self) -> None:
        if self.backend is None:
            self._show_models_unavailable("Движок не подключён")
            return
        if self._model_worker is not None:
            return
        self.model_combo.setEnabled(False)
        self.model_combo.setToolTip("Загрузка списка моделей...")
        self._model_worker = ModelListWorker(self.backend)
        self._model_worker.done.connect(self._on_models_loaded)
        self._model_worker.failed.connect(self._on_models_failed)
        self._model_worker.finished.connect(self._on_model_worker_finished)
        self._model_worker.start()

    def _on_model_worker_finished(self) -> None:
        worker, self._model_worker = self._model_worker, None
        if worker is not None:
            worker.deleteLater()

    def _show_models_unavailable(self, message: str) -> None:
        model = getattr(self.settings, "model", "")
        self._applying_model = True
        self.model_combo.blockSignals(True)
        self.model_combo.clear()
        if model:
            self.model_combo.addItem(model)
        self.model_combo.blockSignals(False)
        self._applying_model = False
        self.model_combo.setEnabled(False)
        self.model_combo.setToolTip(message)

    def _on_models_loaded(self, models: list) -> None:
        names = [str(name) for name in models or []]
        current = getattr(self.settings, "model", "")
        if current and current not in names:
            names.insert(0, current)
        self._applying_model = True
        self.model_combo.blockSignals(True)
        self.model_combo.clear()
        self.model_combo.addItems(names)
        if current:
            self.model_combo.setCurrentText(current)
        self.model_combo.blockSignals(False)
        self._applying_model = False
        self.model_combo.setEnabled(True)
        self.model_combo.setToolTip("Модель Ollama для чата и агента")

    def _on_models_failed(self, message: str) -> None:
        self._show_models_unavailable(message)

    def _on_model_selected(self, name: str) -> None:
        if self.generating or self.agent_running:
            self._applying_model = True
            self.model_combo.blockSignals(True)
            self.model_combo.setCurrentText(getattr(self.settings, "model", ""))
            self.model_combo.blockSignals(False)
            self._applying_model = False
            return
        if not name or self.settings is None or getattr(self.settings, "model", "") == name:
            return
        self.settings.model = name
        client = getattr(self.backend, "client", None)
        if client is not None and hasattr(client, "model"):
            client.model = name
        self._applying_model = True
        self.model_combo.blockSignals(True)
        self.model_combo.setCurrentText(name)
        self.model_combo.blockSignals(False)
        self._applying_model = False
        self.qsettings.setValue("model", name)
        self._start_status_check()

    # ── engine status ────────────────────────────────────────────────────

    def _start_status_check(self) -> None:
        if self.assistant is None:
            self.status_label.setText("Движок: не подключён")
            return
        model = getattr(self.settings, "model", "")
        self.status_label.setText(f"Модель: {model} — проверка...")
        if self.backend is None:
            return
        self.status_worker = StatusWorker(self.backend)
        self.status_worker.result.connect(self._on_status_result)
        self.status_worker.start()

    def _on_status_result(self, available: bool) -> None:
        model = getattr(self.settings, "model", "")
        state = "online" if available else "offline"
        text = f"Модель: {model} — {state}" if model else f"Движок: {state}"
        self.status_label.setText(text)
        self.status_label.setProperty("state", state)
        self.style().unpolish(self.status_label)
        self.style().polish(self.status_label)

    # ── chat flow ────────────────────────────────────────────────────────

    def send_message(self) -> None:
        text = self.prompt.text().strip()
        if not text or self.generating or self.agent_running:
            return
        if self.assistant is None:
            self._append_message("assistant", "Движок не подключён. Запустите приложение через python -m desktop.")
            return
        if self.chat_agent_files.isChecked() and self.agent_factory is not None:
            self._send_agent_message(text)
            return

        history = list(self.history)
        self._append_message("user", text)
        self.history.append({"role": "user", "content": text})
        self._append_message("assistant", "")
        self._start_thinking()

        self._set_generating(True)
        self.worker = ChatWorker(self.assistant, text, history)
        self.worker.target = resolve_target(getattr(self.settings, "language", "auto"), text)
        self.worker.token.connect(self._on_token)
        self.worker.meta.connect(self._on_meta)
        self.worker.failed.connect(self._on_failed)
        self.worker.finished_ok.connect(self._on_done)
        self.worker.text_reset.connect(self._on_text_reset)
        self.worker.finished.connect(self.worker.deleteLater)
        self.worker.start()

    def stop_generation(self) -> None:
        if self.worker is not None and self.generating:
            self.worker.stop()
        if self.agent_worker is not None and self.generating:
            self.agent_worker.stop()

    def _on_chat_agent_files_toggled(self, checked: bool) -> None:
        if checked:
            if self.agent_factory is None:
                self.chat_agent_files.setChecked(False)
                self._append_message("assistant", "Движок не подключён. Запустите приложение через python -m desktop.")
                return
            self.prompt.setPlaceholderText("Задача для агента: например, прочитай main.py и исправь баг...")
        else:
            self.prompt.setPlaceholderText("Напишите сообщение...")

    def _send_agent_message(self, task: str) -> None:
        from licensing import feature_enabled

        self._append_message("user", task)
        self.history.append({"role": "user", "content": task})
        self._append_message("assistant", "")
        self._start_thinking()

        self._set_generating(True)
        self.agent_worker = AgentWorker(
            self.agent_factory,
            task,
            bool(feature_enabled("agent_write")),
            self.agent_iterations.value(),
        )
        self.agent_worker.step.connect(self._on_chat_agent_step)
        self.agent_worker.done.connect(self._on_chat_agent_done)
        self.agent_worker.failed.connect(self._on_chat_agent_failed)
        self.agent_worker.finished.connect(self.agent_worker.deleteLater)
        self.agent_worker.start()

    def _on_chat_agent_step(self, step) -> None:
        if not self._messages:
            return
        self._stop_thinking()
        parts = []
        thought = getattr(step, "thought", "")
        if thought:
            parts.append(thought[:300])
        tool = getattr(step, "tool_name", "")
        if tool:
            args = getattr(step, "tool_args", "") or ""
            parts.append(f"→ {tool} {args}".rstrip())
        if parts:
            entry = self._messages[-1]
            entry["text"] = (entry["text"] + "\n" if entry["text"] else "") + "\n".join(parts)
            self._render_messages()

    def _on_chat_agent_done(self, result: dict) -> None:
        entry = self._messages[-1]
        fragments = []
        if entry["text"]:
            fragments.append(entry["text"])
        if result.get("cancelled"):
            fragments.append("(остановлено пользователем)")
        answer = result.get("final_answer") or ""
        if answer:
            fragments.append(answer)
            self.history.append({"role": "assistant", "content": answer})
        entry["text"] = "\n".join(fragments) or "(пустой ответ)"
        self._set_generating(False)
        self.agent_worker = None
        self._render_messages()

    def _on_chat_agent_failed(self, message: str) -> None:
        entry = self._messages[-1]
        entry["text"] = (entry["text"] + "\n" if entry["text"] else "") + message
        self._set_generating(False)
        self.agent_worker = None
        self._render_messages()

    def _set_generating(self, active: bool) -> None:
        self.generating = active
        self.send_button.setEnabled(not active)
        self.stop_button.setVisible(active)
        if not active:
            self._stop_thinking()
            self.worker = None
            agent_hint = (
                hasattr(self, "chat_agent_files")
                and self.chat_agent_files is not None
                and self.chat_agent_files.isChecked()
            )
            self.prompt.setPlaceholderText(
                "Задача для агента: например, прочитай main.py и исправь баг..."
                if agent_hint
                else "Напишите сообщение..."
            )

    # ── thinking indicator ────────────────────────────────────────────────

    def _start_thinking(self) -> None:
        if self._messages:
            self._messages[-1]["thinking"] = True
        self._think_frame = 0
        self._think_timer.start()
        self._render_messages()

    def _stop_thinking(self) -> None:
        self._think_timer.stop()
        for entry in self._messages:
            entry.pop("thinking", None)

    def _on_think_tick(self) -> None:
        self._think_frame = (self._think_frame + 1) % 4
        self._render_messages()

    def _on_meta(self, result) -> None:
        parts = []
        strategy = getattr(getattr(result, "strategy", None), "name", "")
        if strategy:
            parts.append(strategy.lower())
        if getattr(result, "rag_used", False):
            parts.append(f"RAG: {len(getattr(result, 'rag_sources', []) or [])} источн.")
        if getattr(result, "web_used", False):
            parts.append(f"web: {len(getattr(result, 'web_sources', []) or [])} источн.")
            tier = getattr(getattr(self, "web_search", None), "provider_tier", "")
            if tier == "basic":
                parts.append("поиск: базовый (один движок)")
            elif tier == "enhanced":
                parts.append("поиск: расширенный (SearXNG)")
            if getattr(getattr(self, "web_search", None), "brief_mode", False):
                parts.append("краткая выжимка (Full — полные страницы)")
        if parts:
            self._messages[-1]["meta"] = "[" + " | ".join(parts) + "]"
            self._render_messages()

    def _on_token(self, chunk: str) -> None:
        self._stop_thinking()
        self._messages[-1]["text"] += chunk
        self._render_messages()

    def _on_text_reset(self) -> None:
        if self._messages:
            self._messages[-1]["text"] = ""
            self._messages[-1]["meta"] = ""
        self._start_thinking()

    def _on_done(self) -> None:
        text = self._messages[-1]["text"]
        if not text.strip():
            self._messages[-1]["text"] += "(пустой ответ)"
        else:
            self.history.append({"role": "assistant", "content": text})
        self._set_generating(False)
        self._render_messages()

    def _on_failed(self, message: str) -> None:
        current = self._messages[-1]
        current["text"] = (current["text"] + "\n" if current["text"] else "") + message
        self._set_generating(False)
        self._render_messages()

    def _append_message(self, role: str, text: str) -> None:
        self._messages.append({"role": role, "text": text, "meta": ""})
        self._render_messages()

    def _render_messages(self) -> None:
        if not self._messages:
            self.messages.setHtml(WELCOME_HTML)
        else:
            parts = []
            for entry in self._messages:
                who = "Вы" if entry["role"] == "user" else "Fluxion"
                parts.append(f"<p><b>{who}</b></p>")
                if entry["meta"]:
                    parts.append(f"<p><i>{html.escape(entry['meta'])}</i></p>")
                if entry.get("thinking"):
                    spinner = "◐◓◑◒"[self._think_frame % 4]
                    parts.append(f"<p><i>{spinner} Думаю…</i></p>")
                    continue
                body = html.escape(entry["text"]).replace("\n", "<br>") or "…"
                parts.append(f"<p>{body}</p>")
            self.messages.setHtml("".join(parts))
        scrollbar = self.messages.verticalScrollBar()
        scrollbar.setValue(scrollbar.maximum())

    # ── license ──────────────────────────────────────────────────────────

    def _refresh_license_badge(self) -> None:
        lic = load_activation()
        is_pro = lic is not None and lic.is_pro
        self.license_button.setText("Лицензия: PRO" if is_pro else "Лицензия: FREE")
        self.license_button.setToolTip(lic.email if lic is not None else "")
        self.license_button.setProperty("plan", "pro" if is_pro else "free")
        self.style().unpolish(self.license_button)
        self.style().polish(self.license_button)
        self._refresh_training_gate()
        self._refresh_training_env()

    def _open_license_dialog(self) -> None:
        from .license_dialog import LicenseDialog

        dialog = LicenseDialog(self)
        dialog.exec()
        self._refresh_license_badge()

    # ── theme ────────────────────────────────────────────────────────────

    def toggle_theme(self) -> None:
        self.apply_theme(LIGHT if self.theme.name == "dark" else DARK)

    def apply_theme(self, theme: Theme) -> None:
        self.theme = theme
        self.qsettings.setValue("theme", theme.name)
        self.theme_button.setText("Светлая тема" if theme.name == "dark" else "Тёмная тема")
        panel = "#111827" if theme.name == "dark" else "#ffffff"
        surface = "#161f2e" if theme.name == "dark" else "#eef2f7"
        border = "#263247" if theme.name == "dark" else "#d9e0ea"
        state = self.status_label.property("state")
        status_color = theme.success if state == "online" else (theme.error if state == "offline" else theme.dim)
        self.setStyleSheet(
            f"""
            QMainWindow, QWidget#page {{ background: {theme.bg}; color: {theme.fg}; }}
            QWidget {{ font-family: "Segoe UI", sans-serif; font-size: 14px; }}
            QFrame#sidebar {{ background: {panel}; border-right: 1px solid {border}; }}
            QLabel#brand {{ color: {theme.accent2}; font-size: 25px; font-weight: 700; }}
            QLabel#tagline, QLabel#subtitle {{ color: {theme.dim}; }}
            QLabel#engineStatus {{ color: {status_color}; }}
            QLabel#pageTitle {{ color: {theme.fg}; font-size: 28px; font-weight: 700; }}
            QPushButton {{ border: 0; border-radius: 9px; padding: 10px 12px; text-align: left; color: {theme.fg}; }}
            QPushButton[nav="true"]:hover, QPushButton#themeButton:hover {{ background: {surface}; }}
            QPushButton#licenseButton:hover {{ background: {surface}; }}
            QPushButton#licenseButton[plan="pro"] {{ color: {theme.success}; font-weight: 600; }}
            QPushButton#licenseButton[plan="free"] {{ color: {theme.dim}; }}
            QLabel#licenseStatus[plan="pro"] {{ color: {theme.success}; font-size: 18px; font-weight: 700; }}
            QLabel#licenseStatus[plan="free"] {{ color: {theme.fg}; font-size: 18px; font-weight: 700; }}
            QLabel#licenseMessage {{ color: {theme.dim}; }}
            QPushButton[nav="true"]:checked {{ background: {theme.accent}; color: white; font-weight: 600; }}
            QPushButton[suggestion="true"] {{ background: {surface}; border: 1px solid {border}; }}
            QPushButton[suggestion="true"]:hover {{ border-color: {theme.accent2}; }}
            QTextBrowser#messages {{ background: {panel}; color: {theme.fg}; border: 1px solid {border}; border-radius: 14px; padding: 18px; }}
            QTextBrowser#messages pre {{ background: {surface}; border-radius: 8px; padding: 10px; font-family: Consolas, monospace; font-size: 12px; }}
            QCheckBox {{ color: {theme.fg}; spacing: 8px; }}
            QSpinBox {{ background: {panel}; color: {theme.fg}; border: 1px solid {border}; border-radius: 6px; padding: 5px 8px; }}
            QComboBox {{ background: {panel}; color: {theme.fg}; border: 1px solid {border}; border-radius: 6px; padding: 5px 8px; }}
            QComboBox:disabled {{ color: {theme.dim}; }}
            QComboBox QAbstractItemView {{ background: {panel}; color: {theme.fg}; border: 1px solid {border}; selection-background-color: {theme.accent}; selection-color: white; }}
            QFrame#composer {{ background: {panel}; border: 1px solid {border}; border-radius: 13px; }}
            QLineEdit {{ background: transparent; color: {theme.fg}; border: 0; padding: 7px; selection-background-color: {theme.accent}; }}
            QPushButton#sendButton {{ background: {theme.accent}; color: white; padding: 10px 18px; font-weight: 600; }}
            QPushButton#sendButton:hover {{ background: {theme.accent2}; }}
            QPushButton#stopButton {{ background: {surface}; color: {theme.error}; border: 1px solid {border}; padding: 10px 16px; }}
            QPushButton#stopButton:hover {{ border-color: {theme.error}; }}
            """
        )

    # ── shutdown ─────────────────────────────────────────────────────────

    def closeEvent(self, event) -> None:
        if self.worker is not None and self.generating:
            self.worker.stop()
            self.worker.wait(2000)
        if self.agent_worker is not None and (self.agent_running or self.generating):
            self.agent_worker.stop()
            self.agent_worker.wait(2000)
        if self.index_worker is not None and self.indexing:
            self.index_worker.wait(5000)
        if self.status_worker is not None:
            self.status_worker.wait(10000)
        if self._model_worker is not None:
            self._model_worker.wait(5000)
        if self.training_worker is not None and self.training_running:
            self.training_worker.stop()
            if not self.training_worker.wait(15000):
                self.training_worker.terminate()
                self.training_worker.wait(3000)
        if self.env_worker is not None and self.env_installing:
            self.env_worker.stop()
            if not self.env_worker.wait(10000):
                self.env_worker.terminate()
                self.env_worker.wait(3000)
        super().closeEvent(event)


def main() -> int:
    import os

    from orchestrator import CodingAgent

    from .engine import build_engine

    app = QApplication(sys.argv)
    app.setApplicationName("Fluxion")
    app.setOrganizationName("Fluxion")
    app.setStyle("Fusion")

    try:
        assistant, backend, settings, rag_service, web_search = build_engine()

        def agent_factory(task: str, allow_write: bool, max_iter: int):
            return CodingAgent(
                backend=backend,
                project_root=str(settings.project_root()),
                rag_service=rag_service,
                web_search=web_search,
                max_iterations=max_iter,
                allow_write=allow_write,
                lang=getattr(settings, "language", "auto"),
            )

        window = FluxionWindow(
            assistant=assistant,
            backend=backend,
            settings=settings,
            agent_factory=agent_factory,
            rag_service=rag_service,
        )
    except Exception as exc:
        window = FluxionWindow()
        window.status_label.setText(f"Ошибка движка: {exc}")
    window.show()

    if os.environ.get("FLUXION_DESKTOP_SMOKE") == "1":
        app.processEvents()
        lic = load_activation()
        print(
            "[smoke] license:",
            lic.email if lic is not None else None,
            "pro =", bool(lic is not None and lic.is_pro),
        )
        window.close()
        if os.environ.get("FLUXION_SMOKE_RAG") == "1":
            return _rag_smoke_check(rag_service)
        return 0

    return app.exec()


def _rag_smoke_check(rag_service) -> int:
    """Index a tiny in-memory project and search it; returns process exit code."""
    import tempfile

    print("[rag-smoke] rag_service:", type(rag_service).__name__ if rag_service else None)
    if rag_service is None:
        print("[rag-smoke] FAIL: RAG service unavailable")
        return 3
    try:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "proj"
            root.mkdir()
            (root / "sample.py").write_text(
                "def fetch_user(user_id):\n"
                "    '''Return a user by id.'''\n"
                "    return {'id': user_id, 'name': 'Alice'}\n",
                encoding="utf-8",
            )
            count = rag_service.index(root)
            print(f"[rag-smoke] indexed chunks: {count}")
            if count < 1:
                print("[rag-smoke] FAIL: no chunks indexed")
                return 4
            results = rag_service.search("fetch a user by id", top_k=3)
            print(f"[rag-smoke] search results: {len(results)}")
            if not results:
                print("[rag-smoke] FAIL: search returned nothing")
                return 5
            print("[rag-smoke] OK")
            return 0
    except Exception as exc:
        print(f"[rag-smoke] FAIL: {exc.__class__.__name__}: {exc}")
        return 6


if __name__ == "__main__":
    raise SystemExit(main())
