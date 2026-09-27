from __future__ import annotations

import html
import sys
import time
import uuid
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import QPoint, QRect, QSize, Qt, QSettings, QThread, QTimer, QUrl, Signal
from PySide6.QtGui import QAction, QDesktopServices, QIcon, QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QApplication,
    QButtonGroup,
    QCheckBox,
    QComboBox,
    QDialog,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLayout,
    QLineEdit,
    QMainWindow,
    QMenu,
    QMenuBar,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QSpinBox,
    QSplitter,
    QStackedWidget,
    QStatusBar,
    QToolButton,
    QTextBrowser,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from cli.theme import DARK, LIGHT, Theme
from core.language import LANG_NAMES, matches_language, resolve_target
from licensing.store import load_activation
from . import APP_VERSION
from .banner import BrandBanner
from .browser import create_browser_panel, linkify
from .memory import delete_session, load_sessions, save_session, session_title
from .training import TrainingError, TrainingParams, run_pipeline


APP_ROOT = Path(__file__).resolve().parent.parent


def resolve_project_root(picked_value: str, settings) -> str:
    """Prefer the folder chosen in the GUI; fall back to the config-based root."""
    picked = (picked_value or "").strip()
    if picked and Path(picked).is_dir():
        return picked
    return str(settings.project_root())

WELCOME_HTML = (
    "<h3>Добро пожаловать в Fluxion</h3>"
    "<p>Спросите о коде, архитектуре или начните с примера сверху.</p>"
)

AGENT_READY_HTML = (
    "<p><span style=\"color:#00E5FF; font-size:15px;\">●</span> <b>Готов к запуску.</b> "
    "Опишите задачу выше и нажмите «Запустить» (Ctrl+Enter).</p>"
)


class FlowLayout(QLayout):
    """Примеры-чипы переносятся по ширине, а не обрезаются."""

    def __init__(self, parent=None, spacing=8):
        super().__init__(parent)
        self.setContentsMargins(0, 0, 0, 0)
        self._spacing = spacing
        self._items: list = []

    def addItem(self, item):
        self._items.append(item)

    def count(self):
        return len(self._items)

    def itemAt(self, i):
        return self._items[i] if 0 <= i < len(self._items) else None

    def takeAt(self, i):
        return self._items.pop(i) if 0 <= i < len(self._items) else None

    def hasHeightForWidth(self):
        return True

    def heightForWidth(self, width):
        return self._do_layout(QRect(0, 0, width, 0), True)

    def setGeometry(self, rect):
        super().setGeometry(rect)
        self._do_layout(rect, False)

    def sizeHint(self):
        return self.minimumSize()

    def minimumSize(self):
        size = QSize()
        for item in self._items:
            size = size.expandedTo(item.minimumSize())
        return size

    def _do_layout(self, rect: QRect, test_only: bool) -> int:
        x, y, line_h = rect.x(), rect.y(), 0
        for item in self._items:
            hint = item.sizeHint()
            if x + hint.width() > rect.right() and line_h > 0:
                x, y = rect.x(), y + line_h + self._spacing
                line_h = 0
                hint = item.sizeHint()
            if not test_only:
                item.setGeometry(QRect(QPoint(x, y), hint))
            x += hint.width() + self._spacing
            line_h = max(line_h, hint.height())
        return y + line_h - rect.y()


class BannerStack(QFrame):
    """Предупреждения с причиной и действием; key = идемпотентность."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("bannerStack")
        self._box = QVBoxLayout(self)
        self._box.setContentsMargins(0, 0, 0, 0)
        self._box.setSpacing(6)
        self._keys: dict[str, QFrame] = {}
        self.hide()

    def show_warning(self, text: str, action_text: str | None = None, on_action=None, key: str | None = None) -> None:
        if key is not None:
            self.dismiss_key(key)
        banner = QFrame()
        banner.setObjectName("banner")
        row = QHBoxLayout(banner)
        row.setContentsMargins(12, 8, 12, 8)
        row.setSpacing(10)
        icon = QLabel("⚠")
        icon.setObjectName("bannerIcon")
        row.addWidget(icon)
        label = QLabel(text)
        label.setWordWrap(True)
        label.setObjectName("bannerText")
        row.addWidget(label, 1)
        if action_text and on_action:
            btn = QPushButton(action_text)
            btn.setObjectName("bannerAction")
            btn.clicked.connect(on_action)
            row.addWidget(btn)
        close = QPushButton("×")
        close.setObjectName("bannerClose")
        close.clicked.connect(lambda: self._remove(banner))
        row.addWidget(close)
        if key is not None:
            self._keys[key] = banner
        self._box.addWidget(banner)
        self.show()

    def dismiss_key(self, key: str) -> None:
        banner = self._keys.pop(key, None)
        if banner is not None:
            self._remove(banner)

    def _remove(self, banner: QFrame) -> None:
        self._box.removeWidget(banner)
        for k, b in list(self._keys.items()):
            if b is banner:
                self._keys.pop(k)
        banner.deleteLater()
        if self._box.count() == 0:
            self.hide()

    def clear(self) -> None:
        self._keys.clear()
        while self._box.count():
            item = self._box.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        self.hide()


class StepIndicator(QWidget):
    """Точки 1-2-3 с фиксированной семантикой: done / current / todo."""

    def __init__(self, steps: list[str], parent=None):
        super().__init__(parent)
        self._dots: list[QLabel] = []
        row = QHBoxLayout(self)
        row.setContentsMargins(0, 4, 0, 4)
        row.setSpacing(10)
        for index, label in enumerate(steps):
            dot = QLabel(str(index + 1))
            dot.setObjectName("stepDot")
            dot.setAlignment(Qt.AlignCenter)
            dot.setFixedSize(24, 24)
            dot.setProperty("state", "todo")
            row.addWidget(dot)
            row.addWidget(QLabel(label))
            self._dots.append(dot)
            if index < len(steps) - 1:
                line = QFrame()
                line.setObjectName("stepLine")
                line.setFixedWidth(28)
                line.setFrameShape(QFrame.HLine)
                row.addWidget(line)
        row.addStretch()

    def set_states(self, states: list[str]) -> None:
        for dot, state in zip(self._dots, states):
            dot.setProperty("state", state)
            dot.style().unpolish(dot)
            dot.style().polish(dot)


class StatusChip(QToolButton):
    """Чип статус-бара: точка+текст, клик = поповер. API-совместим с QLabel
    по вызовам setText/setProperty, которые использует существующая логика."""

    def __init__(self, object_name: str, parent=None):
        super().__init__(parent)
        self.setObjectName(object_name)
        self.setProperty("state", "todo")
        self._raw = ""
        self._builder = None
        self.clicked.connect(self._show_popover)

    def setText(self, text: str) -> None:  # noqa: N802 — Qt API
        self._raw = str(text)
        super().setText(f"● {self._raw}")

    def text(self) -> str:  # noqa: N802 — Qt API
        return self._raw

    def set_popover_builder(self, builder) -> None:
        self._builder = builder

    def _show_popover(self) -> None:
        if self._builder is None:
            return
        pop = ChipPopover()
        self._builder(pop)
        pop.show_at(self)


class ChipPopover(QWidget):
    """Поповер чипа: факты моно + действия. Qt.Popup = сам закроется по клику вне/Esc."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowFlags(Qt.Popup | Qt.FramelessWindowHint)
        self.setAttribute(Qt.WA_DeleteOnClose)
        self.setObjectName("chipPopover")
        self._box = QVBoxLayout(self)
        self._box.setContentsMargins(14, 12, 14, 12)
        self._box.setSpacing(8)

    def add_row(self, key: str, value: str) -> None:
        row = QHBoxLayout()
        row.setSpacing(10)
        k = QLabel(key)
        k.setObjectName("popKey")
        v = QLabel(value)
        v.setObjectName("popValue")
        v.setTextInteractionFlags(Qt.TextSelectableByMouse)
        row.addWidget(k)
        row.addWidget(v, 1)
        self._box.addLayout(row)

    def add_note(self, text: str) -> None:
        note = QLabel(text)
        note.setObjectName("popNote")
        note.setWordWrap(True)
        self._box.addWidget(note)

    def add_action(self, text: str, callback) -> None:
        btn = QPushButton(text)
        btn.setObjectName("popAction")
        btn.clicked.connect(lambda: (self.close(), callback()))
        self._box.addWidget(btn)

    def show_at(self, anchor: QWidget) -> None:
        self.adjustSize()
        origin = anchor.mapToGlobal(QPoint(0, 0))
        y = origin.y() - self.height() - 6
        x = origin.x()
        screen = anchor.screen()
        if screen is not None:
            x = min(x, screen.geometry().right() - self.width() - 8)
        self.move(max(8, x), y)
        self.show()


class UpdateWorker(QThread):
    """Checks GitHub Releases for a newer version in the background."""

    done = Signal(object)

    def __init__(self, current_version: str, parent=None):
        super().__init__(parent)
        self._version = current_version

    def run(self) -> None:
        from core.update import check_for_updates

        self.done.emit(check_for_updates(self._version))


class ModelDownloadWorker(QThread):
    """Downloads a catalog GGUF model with progress/resume (roadmap 18.2)."""

    progress = Signal(int, int)  # done_bytes, total_bytes (0 = unknown)
    failed = Signal(str)
    done = Signal(str)

    def __init__(self, model_id: str, parent=None):
        super().__init__(parent)
        self.model_id = model_id

    def run(self) -> None:
        from core.model_manager import ModelManager

        try:
            mm = ModelManager()
            path = mm.download(
                self.model_id, on_progress=lambda d, t: self.progress.emit(d, t)
            )
        except Exception as exc:
            self.failed.emit(f"Ошибка загрузки модели: {exc}")
            return
        self.done.emit(str(path))


class ModelImportWorker(QThread):
    """Imports a GGUF from disk or reuses Ollama blobs (no re-download)."""

    done = Signal(str)
    failed = Signal(str)

    def __init__(self, source_path: str | None = None, parent=None):
        super().__init__(parent)
        self.source_path = source_path

    def run(self) -> None:
        from core.model_manager import ModelManager

        mm = ModelManager()
        try:
            if self.source_path:
                item = mm.import_from_disk(self.source_path)
                self.done.emit(f"Импортирована модель: {item.filename}")
                return
            imported = mm.import_from_ollama()
            if imported:
                names = ", ".join(i.filename for i in imported)
                self.done.emit(f"Импортировано из Ollama: {names}")
            else:
                self.done.emit("В хранилище Ollama GGUF-модели не найдены.")
        except Exception as exc:
            self.failed.emit(f"Ошибка импорта: {exc}")


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


class HealthWorker(QThread):
    """Runs dependency health checks off the UI thread (Phase 14)."""

    done = Signal(object)

    def __init__(self, settings, web_search, rag_service, parent=None):
        super().__init__(parent)
        self.settings = settings
        self.web_search = web_search
        self.rag_service = rag_service

    def run(self) -> None:
        from .health import check_all

        self.done.emit(check_all(self.settings, self.web_search, self.rag_service))


class SearxFixWorker(QThread):
    """Auto-fix attempt: start the SearXNG docker container."""

    done = Signal(bool)

    def __init__(self, base_url: str, parent=None):
        super().__init__(parent)
        self.base_url = base_url

    def run(self) -> None:
        from .searxng import ensure_searxng

        self.done.emit(ensure_searxng(self.base_url))


class DockerFixWorker(QThread):
    """Starts Docker Desktop (daemon) and then the SearXNG container."""

    done = Signal(bool)

    def __init__(self, base_url: str = "", parent=None):
        super().__init__(parent)
        self.base_url = base_url

    def run(self) -> None:
        from .installer import ensure_docker

        if not ensure_docker():
            self.done.emit(False)
            return
        if self.base_url:
            from .searxng import ensure_searxng

            self.done.emit(ensure_searxng(self.base_url))
        else:
            self.done.emit(True)


class GitInitWorker(QThread):
    """Initialises a git repository in the agent project folder."""

    done = Signal(bool, str)

    def __init__(self, root: str, parent=None):
        super().__init__(parent)
        self.root = root

    def _git(self, *args: str):
        import subprocess

        return subprocess.run(
            ["git", *args],
            cwd=self.root,
            capture_output=True,
            text=True,
            timeout=90,
        )

    def run(self) -> None:
        result = self._git("init")
        if result.returncode != 0:
            self.done.emit(False, (result.stderr or result.stdout).strip()[:300])
            return
        for key, value in (("user.name", "Fluxion"), ("user.email", "fluxion@local")):
            probe = self._git("config", "--get", key)
            if probe.returncode != 0 or not probe.stdout.strip():
                self._git("config", key, value)
        self._git("add", "-A")
        result = self._git("commit", "-m", "Initial commit (Fluxion)")
        combined = (result.stdout or "") + (result.stderr or "")
        if result.returncode != 0 and "nothing to commit" not in combined:
            self.done.emit(False, (result.stderr or result.stdout).strip()[:300])
            return
        self.done.emit(True, f"Репозиторий инициализирован: {self.root}")


class HealthDialog(QDialog):
    """Dependency dashboard: state, reason, remedy, fix (Phase 14.1)."""

    def __init__(self, window, parent=None):
        super().__init__(parent or window)
        self.window = window
        self.setWindowTitle("Fluxion — состояние")
        self.setMinimumWidth(600)
        self._fix_workers: list[QThread] = []

        outer = QVBoxLayout(self)
        outer.setContentsMargins(20, 16, 20, 16)
        outer.setSpacing(10)

        self.rows_box = QVBoxLayout()
        outer.addLayout(self.rows_box)

        buttons = QHBoxLayout()
        recheck = QPushButton("Перепроверить")
        recheck.setObjectName("themeButton")
        recheck.clicked.connect(self._run_checks)
        buttons.addWidget(recheck)
        wizard_btn = QPushButton("Мастер настройки…")
        wizard_btn.setObjectName("themeButton")
        wizard_btn.clicked.connect(self._open_wizard)
        buttons.addWidget(wizard_btn)
        buttons.addStretch()
        close_btn = QPushButton("Закрыть")
        close_btn.clicked.connect(self.accept)
        buttons.addWidget(close_btn)
        outer.addLayout(buttons)

        self._run_checks()

    def _run_checks(self) -> None:
        while self.rows_box.count():
            item = self.rows_box.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()
        info = QLabel("Проверяем зависимости…")
        self.rows_box.addWidget(info)
        self._worker = HealthWorker(
            self.window.settings, self.window.web_search, self.window.rag_service
        )
        self._worker.done.connect(self._on_result)
        self._worker.start()

    def _on_result(self, statuses) -> None:
        while self.rows_box.count():
            item = self.rows_box.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()
        for status in statuses:
            self.rows_box.addWidget(self._make_row(status))
        self.rows_box.addStretch()

    def _make_row(self, status) -> QWidget:
        from PySide6.QtWidgets import QFrame

        from .health import linkify as linkify_url

        row = QFrame()
        row.setFrameShape(QFrame.NoFrame)
        layout = QHBoxLayout(row)
        layout.setContentsMargins(0, 4, 0, 4)

        mark = QLabel("✅" if status.available else "⚠️")
        mark.setFixedWidth(28)
        layout.addWidget(mark)

        text = QLabel(
            f"<b>{html.escape(status.title)}</b> — {html.escape(status.detail)}"
        )
        if not status.available:
            text.setText(
                f"<b>{html.escape(status.title)}</b> — {html.escape(status.detail)}"
                f"<br><i>{linkify_url(status.remedy)}</i>"
            )
        text.setWordWrap(True)
        text.setOpenExternalLinks(True)
        layout.addWidget(text, 1)

        if not status.available and status.fix == "searxng":
            fix_button = QPushButton("Исправить")
            fix_button.setObjectName("themeButton")
            fix_button.clicked.connect(lambda checked=False, b=fix_button: self._fix_searxng(b))
            layout.addWidget(fix_button)
        elif not status.available and status.fix == "docker":
            fix_button = QPushButton("Исправить")
            fix_button.setObjectName("themeButton")
            fix_button.clicked.connect(lambda checked=False, b=fix_button: self._fix_docker(b))
            layout.addWidget(fix_button)
        return row

    def _fix_searxng(self, button: QPushButton) -> None:
        client = getattr(self.window.web_search, "client", None)
        base_url = getattr(client, "base_url", "http://localhost:8080")
        button.setEnabled(False)
        button.setText("Запуск…")
        worker = SearxFixWorker(base_url)
        worker.done.connect(lambda ok, b=button: self._on_fix_done(ok, b))
        self._fix_workers.append(worker)
        worker.start()

    def _on_fix_done(self, ok: bool, button: QPushButton) -> None:
        button.setText("Исправить" if not ok else "Готово")
        if ok:
            self._run_checks()
        else:
            button.setEnabled(True)
            button.setToolTip("Не удалось запустить контейнер — см. подсказку выше")

    def _fix_docker(self, button: QPushButton) -> None:
        client = getattr(self.window.web_search, "client", None)
        base_url = getattr(client, "base_url", "")
        button.setEnabled(False)
        button.setText("Запуск…")
        worker = DockerFixWorker(base_url)
        worker.done.connect(lambda ok, b=button: self._on_fix_done(ok, b))
        self._fix_workers.append(worker)
        worker.start()

    def _open_wizard(self) -> None:
        from .wizard import FirstRunWizard

        FirstRunWizard(
            self.window.settings,
            self.window.web_search,
            self.window.rag_service,
            self.window.qsettings,
            parent=self,
        ).exec()
        self._run_checks()


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
            "verification": getattr(result, "verification", "") or "",
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
        web_search=None,
        training_pipeline=None,
    ) -> None:
        super().__init__()
        self.assistant = assistant
        self.backend = backend
        self.settings = settings
        self.agent_factory = agent_factory
        self.rag_service = rag_service
        self.web_search = web_search
        self.training_pipeline = training_pipeline
        self.qsettings = qsettings if qsettings is not None else QSettings("Fluxion", "Fluxion")
        self.theme = LIGHT if self.qsettings.value("theme", "dark") == "light" else DARK
        self.history: list[dict[str, str]] = []
        self._messages: list[dict[str, str]] = []
        self._think_frame = 0
        self._think_timer = QTimer(self)
        self._think_timer.setInterval(400)
        self._think_timer.timeout.connect(self._on_think_tick)
        self._sessions: list[dict] = []
        self._session_id: str | None = None
        self.worker: ChatWorker | None = None
        self.status_worker: StatusWorker | None = None
        self.agent_worker: AgentWorker | None = None
        self.index_worker: IndexWorker | None = None
        self._model_worker: ModelListWorker | None = None
        self._health_worker: HealthWorker | None = None
        self.training_worker: TrainingWorker | None = None
        self.env_worker: EnvWorker | None = None
        self._applying_model = False
        self.generating = False
        self.agent_running = False
        self.indexing = False
        self.training_running = False
        self.env_installing = False
        self._last_query = ""
        self._web_opened_for_current = False
        web_cfg = getattr(settings, "web", None) if settings is not None else None
        self._search_base = str(getattr(web_cfg, "searxng_url", "") or "")
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
        self.setWindowIcon(QIcon(str(APP_ROOT / "assets" / "icon-256.png")))
        self._build_ui()
        self.apply_theme(self.theme)
        self._restore_memory()
        self._refresh_license_badge()
        self._start_status_check()
        self._startup_health_check()
        self._load_models()

    # ── chat memory ──────────────────────────────────────────────────────

    def _restore_memory(self) -> None:
        self._sessions = load_sessions()
        if self._sessions:
            self._load_session(self._sessions[-1])
        self._refresh_chat_selector()

    def _load_session(self, session: dict) -> None:
        self._session_id = session["id"]
        self.history = [dict(m) for m in session["messages"]]
        self._messages = [
            {"role": m["role"], "text": m["content"], "meta": ""} for m in self.history
        ]
        self._render_messages()

    def _save_memory(self) -> None:
        if not self.history:
            return
        self._session_id = self._session_id or uuid.uuid4().hex
        session = {
            "id": self._session_id,
            "title": session_title(self.history),
            "updated": time.time(),
            "messages": [dict(m) for m in self.history],
        }
        self._sessions = save_session(session)
        self._refresh_chat_selector()

    def _refresh_chat_selector(self) -> None:
        selector = getattr(self, "chat_selector", None)
        if selector is None:
            return
        selector.blockSignals(True)
        selector.clear()
        current_known = any(s["id"] == self._session_id for s in self._sessions)
        if self._session_id is not None and not current_known:
            selector.addItem("Текущий чат", None)
            selector.setCurrentIndex(0)
        for s in reversed(self._sessions):
            selector.addItem(s["title"], s["id"])
            if s["id"] == self._session_id:
                selector.setCurrentIndex(selector.count() - 1)
        selector.blockSignals(False)

    def _on_chat_selected(self, index: int) -> None:
        if self.generating or self.agent_running:
            self._refresh_chat_selector()
            return
        session_id = self.chat_selector.itemData(index)
        if session_id is None or session_id == self._session_id:
            return
        self._save_memory()
        session = next((s for s in self._sessions if s["id"] == session_id), None)
        if session:
            self._load_session(session)
            self._refresh_chat_selector()

    def _new_topic(self) -> None:
        if self.generating or self.agent_running:
            return
        self._save_memory()
        self._session_id = None
        self.history = []
        self._messages = []
        self._render_messages()
        self._refresh_chat_selector()

    def _export_current_chat(self) -> None:
        from PySide6.QtWidgets import QFileDialog

        from .memory import safe_filename, session_to_markdown

        if not self.history:
            self._append_banner("Экспорт: чат пуст — нечего сохранять.")
            return
        session = {
            "id": self._session_id or "",
            "title": session_title(self.history),
            "updated": time.time(),
            "messages": list(self.history),
        }
        default = safe_filename(session["title"])
        path, _ = QFileDialog.getSaveFileName(
            self, "Экспорт чата", default, "Markdown (*.md)"
        )
        if not path:
            return
        try:
            Path(path).write_text(session_to_markdown(session), encoding="utf-8")
        except OSError as exc:
            self._append_banner(f"Экспорт не удался: {exc}")
            return
        self._append_banner(f"Чат экспортирован: {path}")

    def _delete_current_chat(self) -> None:
        if self._session_id is None:
            return
        answer = QMessageBox.question(
            self,
            "Удалить чат",
            "Удалить текущий чат из истории?",
            QMessageBox.Yes | QMessageBox.No,
        )
        if answer != QMessageBox.Yes:
            return
        self._sessions = delete_session(self._session_id)
        self._session_id = None
        self.history = []
        self._messages = []
        self._render_messages()
        self._refresh_chat_selector()

    def new_chat(self) -> None:
        self._save_memory()
        self._session_id = None
        self.history = []
        self._messages = []
        self._stop_thinking()
        self._render_messages()
        self._refresh_chat_selector()

    def export_chat_md(self) -> None:
        from PySide6.QtWidgets import QFileDialog

        if not self._messages:
            return
        path, _ = QFileDialog.getSaveFileName(
            self, "Экспорт чата", "fluxion-chat.md", "Markdown (*.md)"
        )
        if not path:
            return
        blocks = []
        for entry in self._messages:
            who = "Вы" if entry["role"] == "user" else "Fluxion"
            blocks.append(f"**{who}**\n\n{entry['text']}\n")
        Path(path).write_text("\n---\n\n".join(blocks), encoding="utf-8")
        self.statusBar().showMessage(f"Чат экспортирован: {path}", 5000)

    def _refresh_index_chip(self) -> None:
        if not hasattr(self, "index_chip"):
            return
        if self.rag_service is None:
            self.index_chip.setText("index: n/a")
            self.index_chip.setProperty("state", "todo")
        else:
            try:
                count = self.rag_service.count()
            except Exception:
                count = 0
            self.index_chip.setText(f"index: {count}")
            self.index_chip.setProperty("state", "ok" if count else "todo")
        self.index_chip.style().unpolish(self.index_chip)
        self.index_chip.style().polish(self.index_chip)

    def _build_engine_popover(self, pop: ChipPopover) -> None:
        backend_name = type(self.backend).__name__ if self.backend is not None else "не подключён"
        model = getattr(self.settings, "model", "") or "—"
        state = str(self.status_label.property("state") or "todo")
        pop.add_row("Движок", backend_name)
        pop.add_row("Модель", model)
        pop.add_row("Статус", state)
        if state == "offline":
            pop.add_note(
                "Возможные причины: Ollama не запущена, модель не загружена "
                "или движок отключён конфигурацией."
            )
        pop.add_action("Повторить проверку", self._retry_engine_check)
        pop.add_action("Открыть чат (сменить модель)", lambda: self.pages.setCurrentIndex(0))

    def _retry_engine_check(self) -> None:
        self._load_models()
        self._start_status_check()

    def _build_index_popover(self, pop: ChipPopover) -> None:
        if self.rag_service is None:
            pop.add_row("Индекс", "недоступен (движок не подключён)")
            pop.add_action("Повторить проверку", self._retry_engine_check)
            return
        try:
            count = self.rag_service.count()
        except Exception:
            count = 0
        pop.add_row("Чанков", str(count))
        pop.add_row("Папка", str(self.qsettings.value("project_path", "") or "—"))
        pop.add_row("Обновлён", str(self.qsettings.value("index_updated_at", "") or "—"))
        pop.add_row("Эмбеддинг", self._embed_backend_id())
        pop.add_action("Переиндексировать", self._start_indexing)
        pop.add_action("Страница проекта", lambda: self.pages.setCurrentIndex(1))

    def _build_ui(self) -> None:
        # ── Menu Bar (новое) ────────────────────────────────────────
        self._build_menu()

        root = QWidget()
        layout = QHBoxLayout(root)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # ── Sidebar (очищен от мусора) ──────────────────────────────
        self.sidebar = QFrame()
        self.sidebar.setObjectName("sidebar")
        self.sidebar.setFixedWidth(208)
        sidebar_layout = QVBoxLayout(self.sidebar)
        sidebar_layout.setContentsMargins(16, 24, 16, 16)
        sidebar_layout.setSpacing(8)

        self.brand_banner = BrandBanner()
        tagline = QLabel("Локальный AI-ассистент")
        tagline.setObjectName("tagline")
        sidebar_layout.addWidget(self.brand_banner)
        sidebar_layout.addWidget(tagline)
        sidebar_layout.addSpacing(22)

        self.pages = QStackedWidget()
        self.nav_group = QButtonGroup(self)
        self.nav_group.setExclusive(True)

        for index, title in enumerate(("Чат", "Проект", "Агент", "Обучение", "Модели")):
            button = QPushButton(title)
            button.setCheckable(True)
            button.setProperty("nav", True)
            button.clicked.connect(lambda checked=False, page=index: self.pages.setCurrentIndex(page))
            self.nav_group.addButton(button)
            sidebar_layout.addWidget(button)
            if index == 0:
                button.setChecked(True)

        sidebar_layout.addStretch()

        self.pages.addWidget(self._chat_page())
        self.pages.addWidget(self._project_page())
        self.pages.addWidget(self._agent_page())
        self.pages.addWidget(self._training_page())
        self.pages.addWidget(self._models_page())
        for i in range(self.pages.count()):
            self.pages.widget(i).setObjectName("page")

        if hasattr(self, "agent_write"):
            self.act_write.blockSignals(True)
            self.act_write.setChecked(self.agent_write.isChecked())
            self.act_write.blockSignals(False)
            self.agent_write.toggled.connect(self._sync_act_write)

        self.web_panel = create_browser_panel(
            web_search=self.web_search, search_base=self._search_base
        )
        self.web_panel.hide()

        splitter = QSplitter()
        splitter.addWidget(self.pages)
        splitter.addWidget(self.web_panel)
        splitter.setStretchFactor(0, 1)
        splitter.setStretchFactor(1, 1)
        splitter.setCollapsible(1, False)
        self.web_panel.setMinimumWidth(360)
        splitter.setSizes([700, 480])

        layout.addWidget(self.sidebar)
        layout.addWidget(splitter, 1)
        self.setCentralWidget(root)

        # ── статус-бар: кокпит с кликабельными чипами ──────────────────
        bar = self.statusBar()
        bar.setObjectName("mainStatusBar")
        bar.setSizeGripEnabled(False)
        self.status_label = StatusChip("engineChip")
        self.status_label.setText("Движок: не подключён")
        self.status_label.set_popover_builder(self._build_engine_popover)
        bar.addWidget(self.status_label)
        self.index_chip = StatusChip("indexChip")
        self.index_chip.setText("index: —")
        self.index_chip.set_popover_builder(self._build_index_popover)
        bar.addWidget(self.index_chip)

        self.browser_button = QToolButton()
        self.browser_button.setText("Браузер")
        self.browser_button.setCheckable(True)
        self.browser_button.setToolTip("Показать/скрыть встроенный браузер")
        self.browser_button.toggled.connect(self._toggle_web_panel)
        bar.addPermanentWidget(self.browser_button)

        self.health_button = QToolButton()
        self.health_button.setText("Состояние")
        self.health_button.setToolTip("Диагностика зависимостей: Ollama, модель, Docker, веб-поиск, индекс")
        self.health_button.clicked.connect(self._open_health_dialog)
        bar.addPermanentWidget(self.health_button)

        self.license_button = QPushButton()
        self.license_button.setObjectName("licenseButton")
        self.license_button.clicked.connect(self._open_license_dialog)
        bar.addPermanentWidget(self.license_button)

        self._refresh_index_chip()

    def _build_menu(self) -> None:
        bar = self.menuBar()

        # Файл
        m_file = bar.addMenu("&Файл")
        act_export = QAction("Экспорт чата .md…", self)
        act_export.setShortcut("Ctrl+E")
        act_export.triggered.connect(self.export_chat_md)
        m_file.addAction(act_export)
        m_file.addSeparator()
        act_quit = QAction("Закрыть окно", self)
        act_quit.setShortcut("Ctrl+W")
        act_quit.triggered.connect(self.close)
        m_file.addAction(act_quit)

        # Правка
        m_edit = bar.addMenu("&Правка")
        act_copy = QAction("Копировать", self)
        act_copy.setShortcut("Ctrl+C")
        act_copy.triggered.connect(self._copy_selection)
        m_edit.addAction(act_copy)

        # Вид
        m_view = bar.addMenu("&Вид")
        self.act_theme = QAction("Светлая тема" if self.theme.name == "dark" else "Тёмная тема", self)
        self.act_theme.triggered.connect(self.toggle_theme)
        m_view.addAction(self.act_theme)

        # Агент
        m_agent = bar.addMenu("&Агент")
        act_run = QAction("Запустить задачу", self)
        act_run.setShortcut("Ctrl+Enter")
        act_run.triggered.connect(self.run_agent)
        m_agent.addAction(act_run)

        self.act_write = QAction("Разрешить запись файлов", self)
        self.act_write.setCheckable(True)
        self.act_write.setChecked(self.agent_write.isChecked() if hasattr(self, "agent_write") else False)
        self.act_write.toggled.connect(self._on_write_toggled)
        m_agent.addAction(self.act_write)

        # Проект
        m_proj = bar.addMenu("&Проект")
        act_pick = QAction("Выбрать папку…", self)
        act_pick.triggered.connect(self._pick_project_folder)
        m_proj.addAction(act_pick)
        act_index = QAction("Проиндексировать", self)
        act_index.triggered.connect(self._start_indexing)
        m_proj.addAction(act_index)

        # Помощь
        m_help = bar.addMenu("&Помощь")
        act_update = QAction("Проверить обновления…", self)
        act_update.triggered.connect(self._check_updates)
        m_help.addAction(act_update)
        self.act_license = QAction("Лицензия…", self)
        self.act_license.triggered.connect(self._open_license_dialog)
        m_help.addAction(self.act_license)
        act_about = QAction("О Fluxion", self)
        # act_about.triggered.connect(self._show_about)
        m_help.addAction(act_about)

    def _copy_selection(self) -> None:
        widget = QApplication.focusWidget()
        copy = getattr(widget, "copy", None)
        if callable(copy):
            copy()

    def _sync_act_write(self, checked: bool) -> None:
        act = getattr(self, "act_write", None)
        if act is not None and act.isChecked() != checked:
            act.blockSignals(True)
            act.setChecked(checked)
            act.blockSignals(False)

    # ── web panel ────────────────────────────────────────────────────────

    def _toggle_web_panel(self, checked: bool) -> None:
        self.web_panel.setVisible(checked)

    def _show_web_panel(self) -> None:
        if self.web_panel.isHidden():
            self.web_panel.setVisible(True)
        if not self.browser_button.isChecked():
            self.browser_button.setChecked(True)

    def _on_anchor(self, url) -> None:
        self._open_in_browser(url.toString() if isinstance(url, QUrl) else str(url))

    def _open_in_browser(self, url: str) -> None:
        if not url:
            return
        self._show_web_panel()
        self.web_panel.open_url(url)

    def _show_web_results(self, query: str) -> None:
        if not query:
            return
        self._show_web_panel()
        self.web_panel.search(query)

    def _chat_page(self) -> QWidget:
        page = QWidget()
        page.setObjectName("page")
        layout = QVBoxLayout(page)
        layout.setContentsMargins(36, 24, 36, 20)
        layout.setSpacing(12)

        # ── хедер: заголовок слева, модель и операции справа ─────────
        header = QHBoxLayout()
        title = QLabel("Чат")
        title.setObjectName("pageTitle")
        header.addWidget(title)
        header.addStretch()
        header.addWidget(QLabel("Модель:"))
        self.model_combo = QComboBox()
        self.model_combo.setMinimumWidth(220)
        self.model_combo.setEnabled(False)
        self.model_combo.textActivated.connect(self._on_model_selected)
        header.addWidget(self.model_combo)
        self.model_refresh_button = QPushButton("⟳")
        self.model_refresh_button.setObjectName("iconButton")
        self.model_refresh_button.setToolTip("Обновить список моделей")
        self.model_refresh_button.clicked.connect(self._load_models)
        header.addWidget(self.model_refresh_button)
        self.chat_kebab = QToolButton()
        self.chat_kebab.setText("⋮")
        self.chat_kebab.setObjectName("iconButton")
        self.chat_kebab.setPopupMode(QToolButton.InstantPopup)
        kebab_menu = QMenu(self.chat_kebab)
        act_new = kebab_menu.addAction("Новый чат")
        act_new.setShortcut("Ctrl+N")
        act_new.triggered.connect(self.new_chat)
        act_export = kebab_menu.addAction("Экспорт чата в .md…")
        act_export.triggered.connect(self.export_chat_md)
        self.chat_kebab.setMenu(kebab_menu)
        header.addWidget(self.chat_kebab)
        layout.addLayout(header)

        subtitle = QLabel("Спросите Fluxion о коде или начните с одного из примеров.")
        subtitle.setObjectName("subtitle")
        layout.addWidget(subtitle)

        # ── чипы-примеры: wrap вместо обрезания ──────────────────────
        flow = FlowLayout()
        for text in ("Объясни этот проект", "Найди возможную ошибку", "Предложи рефакторинг"):
            button = QPushButton(text)
            button.setProperty("suggestion", True)
            button.clicked.connect(lambda checked=False, value=text: self.prompt.setText(value))
            flow.addWidget(button)
        layout.addLayout(flow)

        self.messages = QTextBrowser()
        self.messages.setObjectName("messages")
        self.messages.setOpenExternalLinks(False)
        self.messages.anchorClicked.connect(self._on_anchor)
        layout.addWidget(self.messages, 1)

        # ── предупреждения над вводом, не внутри скролла ─────────────
        self.banners = BannerStack()
        layout.addWidget(self.banners)

        composer = QFrame()
        composer.setObjectName("composer")
        composer_layout = QHBoxLayout(composer)
        composer_layout.setContentsMargins(12, 10, 10, 10)
        composer_layout.setSpacing(10)
        self.chat_agent_files = QCheckBox("Агент: файлы")
        self.chat_agent_files.setToolTip(
            "Сообщение выполнит агент с инструментами проекта; запись файлов — Pro"
        )
        self.chat_agent_files.toggled.connect(self._on_chat_agent_files_toggled)
        composer_layout.addWidget(self.chat_agent_files)
        self.prompt = QLineEdit()
        self.prompt.setPlaceholderText("Напишите сообщение...")
        self.prompt.returnPressed.connect(self.send_message)
        composer_layout.addWidget(self.prompt, 1)
        self.stop_button = QPushButton("Стоп")
        self.stop_button.setObjectName("stopButton")
        self.stop_button.clicked.connect(self.stop_generation)
        self.stop_button.setVisible(False)
        self.send_button = QPushButton("Отправить")
        self.send_button.setObjectName("sendButton")
        self.send_button.clicked.connect(self.send_message)
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
        layout.setContentsMargins(36, 24, 36, 20)
        layout.setSpacing(12)

        # ── хедер: primary на своём месте ────────────────────────────
        header = QHBoxLayout()
        title = QLabel("Проект")
        title.setObjectName("pageTitle")
        header.addWidget(title)
        header.addStretch()
        self.index_stop_button = QPushButton("Стоп")
        self.index_stop_button.setObjectName("stopButton")
        self.index_stop_button.clicked.connect(self._stop_indexing)
        self.index_stop_button.setVisible(False)
        header.addWidget(self.index_stop_button)
        self.index_button = QPushButton("Проиндексировать")
        self.index_button.setObjectName("sendButton")
        self.index_button.clicked.connect(self._start_indexing)
        header.addWidget(self.index_button)
        layout.addLayout(header)

        subtitle = QLabel(
            "Выберите папку проекта и проиндексируйте код — "
            "чат будет использовать его как контекст."
        )
        subtitle.setObjectName("subtitle")
        layout.addWidget(subtitle)

        # ── карточка пути: mono + стат-строка чипами ─────────────────
        card = QFrame()
        card.setObjectName("stepCard")
        card_layout = QVBoxLayout(card)
        card_layout.setContentsMargins(16, 14, 16, 14)
        card_layout.setSpacing(10)
        path_row = QHBoxLayout()
        self.project_path = QLineEdit()
        self.project_path.setObjectName("monoField")
        self.project_path.setPlaceholderText("Папка проекта не выбрана")
        self.project_path.setReadOnly(True)
        saved = self.qsettings.value("project_path", "")
        if saved:
            self.project_path.setText(str(saved))
        path_row.addWidget(self.project_path, 1)
        pick_button = QPushButton("Выбрать…")
        pick_button.setObjectName("iconButton")
        pick_button.clicked.connect(self._pick_project_folder)
        path_row.addWidget(pick_button)
        card_layout.addLayout(path_row)

        stats = QHBoxLayout()
        stats.setSpacing(10)
        self.chunks_chip = QLabel("чанки: —")
        self.chunks_chip.setObjectName("statusChip")
        self.index_date_chip = QLabel("обновлён: —")
        self.index_date_chip.setObjectName("statusChip")
        self.embed_chip = QLabel("эмбеддинг: —")
        self.embed_chip.setObjectName("statusChip")
        stats.addWidget(self.chunks_chip)
        stats.addWidget(self.index_date_chip)
        stats.addWidget(self.embed_chip)
        stats.addStretch()
        card_layout.addLayout(stats)
        layout.addWidget(card)

        # ── баннеры: движок и реиндекс ───────────────────────────────
        self.project_banners = BannerStack()
        layout.addWidget(self.project_banners)

        # index_status оставлен для совместимости логики, но скрыт:
        # его работу теперь видно в чипах и логе
        self.index_status = QLabel()
        self.index_status.setObjectName("subtitle")
        self.index_status.setText(self._chunks_label())
        self.index_status.setVisible(False)
        layout.addWidget(self.index_status)

        self.project_view = QTextBrowser()
        self.project_view.setObjectName("messages")
        layout.addWidget(self.project_view, 1)

        self.pages.currentChanged.connect(self._on_page_changed)
        self._refresh_project_stats()
        return page

    def _embed_backend_id(self) -> str:
        """Идентификатор эмбеддинг-бэкенда без жёсткой зависимости от API RAG."""
        for owner in (self.rag_service, self.settings, self.backend):
            for attr in ("embedding_backend_id", "embedding_model", "embed_model", "embedding"):
                value = getattr(owner, attr, "")
                if value:
                    return str(value)
        return "—"

    def _on_page_changed(self, index: int) -> None:
        if index == 1:
            self._refresh_project_stats()

    def _refresh_project_stats(self) -> None:
        chunks = getattr(self, "chunks_chip", None)
        if chunks is None:
            return
        if self.indexing:
            chunks.setText("чанки: индексация…")
            chunks.setProperty("state", "current")
            chunks.setToolTip("Первая загрузка модели эмбеддингов может занять время.")
        elif self.rag_service is None:
            chunks.setText("чанки: n/a")
            chunks.setProperty("state", "todo")
            chunks.setToolTip("")
        else:
            try:
                count = self.rag_service.count()
            except Exception:
                count = 0
            chunks.setText(f"чанки: {count}")
            chunks.setProperty("state", "ok" if count else "todo")
            chunks.setToolTip("")
        updated = str(self.qsettings.value("index_updated_at", "") or "")
        self.index_date_chip.setText(f"обновлён: {updated or '—'}")
        self.embed_chip.setText(f"эмбеддинг: {self._embed_backend_id()}")
        for chip in (chunks, self.index_date_chip, self.embed_chip):
            chip.style().unpolish(chip)
            chip.style().polish(chip)
        self._check_reindex_banner()

    def _check_reindex_banner(self) -> None:
        banners = getattr(self, "project_banners", None)
        if banners is None:
            return
        if self.rag_service is None:
            banners.show_warning("Движок не подключён: индексация недоступна.", key="engine")
        else:
            banners.dismiss_key("engine")
        stored = str(self.qsettings.value("index_embed_backend", "") or "")
        current = self._embed_backend_id()
        if stored and current != "—" and stored != current:
            banners.show_warning(
                "Индекс собран другим эмбеддинг-бэкендом — ответы могут устареть.",
                "Переиндексировать",
                self._start_indexing,
                key="reindex",
            )
        else:
            banners.dismiss_key("reindex")

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
        self._refresh_index_chip()

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
            self.project_view.setHtml("<p>Движок не подключён. Запустите приложение через python -m desktop_browser.</p>")
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
        self._refresh_index_chip()
        self.qsettings.setValue("index_updated_at", datetime.now().strftime("%Y-%m-%d %H:%M"))
        self.qsettings.setValue("index_embed_backend", self._embed_backend_id())
        self._refresh_project_stats()

    def _on_index_failed(self, message: str) -> None:
        self._set_indexing_done()
        self.index_status.setText(self._chunks_label())
        self.project_view.append(f"<p><b>{html.escape(message)}</b></p>")

    # ── agent page ───────────────────────────────────────────────────────

    def _agent_page(self) -> QWidget:
        page = QWidget()
        page.setObjectName("page")
        layout = QVBoxLayout(page)
        layout.setContentsMargins(36, 24, 36, 20)
        layout.setSpacing(12)

        # ── хедер: заголовок слева, управление справа (одна primary) ──
        header = QHBoxLayout()
        title = QLabel("Агент")
        title.setObjectName("pageTitle")
        header.addWidget(title)
        header.addStretch()
        self.agent_advanced_button = QPushButton("⚙ Дополнительно")
        self.agent_advanced_button.setObjectName("iconButton")
        self.agent_advanced_button.setCheckable(True)
        self.agent_advanced_button.clicked.connect(
            lambda checked=False: self.agent_advanced.setVisible(
                self.agent_advanced_button.isChecked()
            )
        )
        header.addWidget(self.agent_advanced_button)
        self.agent_stop_button = QPushButton("Стоп")
        self.agent_stop_button.setObjectName("stopButton")
        self.agent_stop_button.clicked.connect(self.stop_agent)
        self.agent_stop_button.setVisible(False)
        header.addWidget(self.agent_stop_button)
        self.agent_run_button = QPushButton("Запустить")
        self.agent_run_button.setObjectName("sendButton")
        self.agent_run_button.clicked.connect(self.run_agent)
        header.addWidget(self.agent_run_button)
        layout.addLayout(header)

        subtitle = QLabel("Опишите задачу — агент решит её пошагово с помощью инструментов.")
        subtitle.setObjectName("subtitle")
        layout.addWidget(subtitle)

        # ── задача: многострочная, Enter = перенос, Ctrl+Enter = запуск ──
        self.agent_task = QTextEdit()
        self.agent_task.setObjectName("agentTask")
        self.agent_task.setPlaceholderText(
            "Например: прочитай main.py и объясни, что он делает\n"
            "Можно описать задачу в несколько строк."
        )
        self.agent_task.setFixedHeight(88)
        QShortcut(QKeySequence("Ctrl+Return"), self.agent_task, activated=self.run_agent)
        layout.addWidget(self.agent_task)

        # ── дополнительно: свёрнуто по умолчанию ──
        self.agent_advanced = QFrame()
        self.agent_advanced.setObjectName("advancedBox")
        adv = QHBoxLayout(self.agent_advanced)
        adv.setContentsMargins(12, 10, 12, 10)
        adv.addWidget(QLabel("Макс. итераций:"))
        self.agent_iterations = QSpinBox()
        self.agent_iterations.setRange(1, 50)
        self.agent_iterations.setValue(8)
        adv.addWidget(self.agent_iterations)
        adv.addStretch()
        self.agent_advanced.setVisible(False)
        layout.addWidget(self.agent_advanced)

        # ── write-доступ: чип, а не голая галка ──
        gate = QHBoxLayout()
        self.agent_write = QCheckBox("Разрешить запись файлов (Pro)")
        self.agent_write.setObjectName("writeChip")
        self.agent_write.toggled.connect(self._on_write_toggled)
        gate.addWidget(self.agent_write)
        gate.addStretch()
        layout.addLayout(gate)

        self.agent_view = QTextBrowser()
        self.agent_view.setObjectName("messages")
        self.agent_view.setOpenExternalLinks(False)
        self.agent_view.anchorClicked.connect(self._on_anchor)
        self.agent_view.setHtml(AGENT_READY_HTML)
        layout.addWidget(self.agent_view, 1)
        return page

    # ── git control ──────────────────────────────────────────────────────

    def _agent_project_root(self) -> Path:
        picked = str(self.qsettings.value("project_path", "") or "").strip()
        settings = getattr(self, "settings", None)
        if settings is not None:
            try:
                return Path(resolve_project_root(picked, settings))
            except Exception:
                pass
        return Path(picked) if picked else Path.cwd()

    def _git_enabled(self) -> bool:
        return bool(int(self.qsettings.value("agent_git_enabled", 1) or 1))

    def _refresh_git_button(self) -> None:
        if not hasattr(self, "agent_git_button"):
            return
        if not self._git_enabled():
            self.agent_git_button.setText("Git: выключен")
            return
        from orchestrator import git_helper

        try:
            repo = git_helper.is_repo(self._agent_project_root())
        except Exception:
            repo = False
        self.agent_git_button.setText("Git: репозиторий" if repo else "Git: нет репозитория")

    def _open_git_dialog(self) -> None:
        import shutil

        from orchestrator import git_helper

        if not self._git_enabled():
            box = QMessageBox(self)
            box.setWindowTitle("Git")
            box.setText("Git-инструменты агента выключены. Включить их снова?")
            enable = box.addButton("Включить", QMessageBox.YesRole)
            box.addButton("Оставить выключенными", QMessageBox.NoRole)
            box.exec()
            if box.clickedButton() is enable:
                self.qsettings.setValue("agent_git_enabled", 1)
                self._refresh_git_button()
            return

        if not shutil.which("git"):
            QMessageBox.warning(
                self,
                "Git",
                "Git не установлен. Установите его через мастер настройки "
                "или вручную: https://git-scm.com/download/win",
            )
            return

        root = self._agent_project_root()
        try:
            repo = git_helper.is_repo(root)
        except Exception:
            repo = False
        if repo:
            QMessageBox.information(
                self,
                "Git",
                f"Проект уже в git-репозитории:\n{root}\n\n"
                "Агент может использовать git_status, git_diff и git_commit.",
            )
            return

        box = QMessageBox(self)
        box.setWindowTitle("Git-репозиторий")
        box.setText(
            f"Папка проекта не является git-репозиторием:\n{root}\n\n"
            "Инициализировать репозиторий (git init + первый коммит) "
            "или работать без git-инструментов?"
        )
        init_btn = box.addButton("Инициализировать", QMessageBox.YesRole)
        no_git_btn = box.addButton("Без git", QMessageBox.NoRole)
        box.addButton("Отмена", QMessageBox.RejectRole)
        box.exec()
        clicked = box.clickedButton()
        if clicked is init_btn:
            self._agent_log("<p><i>Инициализируем git-репозиторий…</i></p>")
            if getattr(self, "agent_git_button", None) is not None:
                self.agent_git_button.setEnabled(False)
            self._git_init_worker = GitInitWorker(str(root))
            self._git_init_worker.done.connect(self._on_git_init_done)
            self._git_init_worker.start()
        elif clicked is no_git_btn:
            self.qsettings.setValue("agent_git_enabled", 0)
            self._refresh_git_button()
            self._agent_log("<p>Git-инструменты агента отключены.</p>")

    def _on_git_init_done(self, ok: bool, message: str) -> None:
        if getattr(self, "agent_git_button", None) is not None:
            self.agent_git_button.setEnabled(True)
        self._agent_log(
            f"<p>{'✅' if ok else '⚠️'} {html.escape(message)}</p>"
        )
        self._refresh_git_button()

    def _on_write_toggled(self, checked: bool) -> None:
        if not checked:
            return
        from licensing import feature_enabled

        if not feature_enabled("agent_write"):
            self.agent_write.blockSignals(True)
            self.agent_write.setChecked(False)
            self.agent_write.blockSignals(False)
            self.agent_view.setHtml(
                "<p><span style=\"color:#FFB454; font-size:15px;\">⚠</span> "
                "<b>Запись файлов — Pro-функция.</b></p>"
                "<p style=\"color:#94A3B8; margin-left:24px;\">Активируйте лицензию: "
                "меню Помощь → Лицензия… или в CLI <code>/license activate &lt;key&gt;</code></p>"
            )

    def run_agent(self) -> None:
        task = self.agent_task.toPlainText().strip()
        if not task or self.agent_running or self.generating:
            return
        if self.agent_factory is None:
            self.agent_view.setHtml(
                "<p>Движок не подключён. Запустите приложение через python -m desktop_browser.</p>"
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
        iteration = html.escape(str(getattr(step, "iteration", "?")))
        parts = [
            f"<p><span style=\"color:#B4FF39; font-size:15px;\">●</span> "
            f"<b>Итерация {iteration}</b></p>"
        ]
        thought = getattr(step, "thought", "")
        if thought:
            parts.append(
                f"<p style=\"color:#94A3B8; margin-left:24px;\"><i>{html.escape(thought[:500])}</i></p>"
            )
        from orchestrator.presentation import format_tool, render_chat_html

        tool = getattr(step, "tool_name", "")
        args = getattr(step, "tool_args", "") or ""
        tool_text = format_tool(step) if tool else ""
        if tool_text:
            parts.append(
                f"<div style=\"margin-left:24px; color:#00E5FF;\">{render_chat_html(tool_text)}</div>"
            )
        if tool == "web_search" and args:
            self._show_web_results(getattr(step, "tool_args", "") or "")
        observation = getattr(step, "observation", "")
        if observation:
            parts.append(f"<pre style=\"margin-left:24px;\">{linkify(observation[:1500])}</pre>")
        self._agent_log("".join(parts))

    def _on_agent_done(self, result: dict) -> None:
        if result.get("cancelled"):
            self._agent_log(
                "<p><span style=\"color:#FFB454; font-size:15px;\">●</span> "
                "<b>Остановлено пользователем.</b></p>"
            )
        ok = bool(result.get("success"))
        dot = "#B4FF39" if ok else "#FF5C5C"
        status = "выполнено" if ok else "не завершено"
        self._agent_log(
            f"<p><span style=\"color:{dot}; font-size:15px;\">●</span> <b>Агент {status}</b> "
            f"<span style=\"color:#94A3B8;\">({result.get('iterations_used', 0)} итер.)</span></p>"
        )
        answer = result.get("final_answer") or ""
        if answer:
            from orchestrator.presentation import render_chat_html

            self._agent_log(
                f"<div style=\"margin-left:24px;\"><b>Ответ:</b><br>{render_chat_html(answer)}</div>"
            )
        verification = result.get("verification", "")
        labels = {
            "passed": ("Верификация: тесты пройдены", "#B4FF39"),
            "failed": ("Верификация: тесты НЕ пройдены", "#FF5C5C"),
            "syntax_only": ("Верификация: тестов нет — проверен только синтаксис", "#FFC53D"),
        }
        if verification in labels:
            label, color = labels[verification]
            self._agent_log(
                f"<p><span style=\"color:{color}; font-size:15px;\">●</span> <b>{label}</b></p>"
            )
        self._set_agent_running(False)

    def _on_agent_failed(self, message: str) -> None:
        self._agent_log(
            f"<p><span style=\"color:#FF5C5C; font-size:15px;\">●</span> "
            f"<b>{html.escape(message)}</b></p>"
        )
        self._set_agent_running(False)

    # ── training page ────────────────────────────────────────────────────

    def _training_page(self) -> QWidget:
        page = QWidget()
        page.setObjectName("page")
        layout = QVBoxLayout(page)
        layout.setContentsMargins(36, 24, 36, 20)
        layout.setSpacing(12)

        title = QLabel("Обучение")
        title.setObjectName("pageTitle")
        layout.addWidget(title)
        self.training_subtitle = QLabel(
            "QLoRA дообучение на вашем датасете (Pro). Готовая модель появится в списке моделей."
        )
        self.training_subtitle.setObjectName("subtitle")
        layout.addWidget(self.training_subtitle)

        self.training_steps = StepIndicator(
            ["Окружение", "Данные и параметры", "Запуск и мониторинг"]
        )
        layout.addWidget(self.training_steps)

        # ── шаг 1: окружение ─────────────────────────────────────────
        card1 = QFrame()
        card1.setObjectName("stepCard")
        c1 = QVBoxLayout(card1)
        c1.setContentsMargins(16, 14, 16, 14)
        c1.setSpacing(8)
        self.training_env_label = QLabel()
        self.training_env_label.setObjectName("subtitle")
        self.training_env_label.setWordWrap(True)
        c1.addWidget(self.training_env_label)
        self.training_install_button = QPushButton("Установить окружение обучения")
        self.training_install_button.setObjectName("sendButton")
        self.training_install_button.clicked.connect(self.install_training_env)
        self.training_install_button.setVisible(False)
        c1.addWidget(self.training_install_button)
        layout.addWidget(card1)

        # ── шаг 2: данные и параметры ────────────────────────────────
        card2 = QFrame()
        card2.setObjectName("stepCard")
        c2 = QVBoxLayout(card2)
        c2.setContentsMargins(16, 14, 16, 14)
        c2.setSpacing(10)
        dataset_row = QHBoxLayout()
        self.training_dataset = QLineEdit()
        self.training_dataset.setPlaceholderText("Датасет .jsonl (ChatML, поле messages)")
        self.training_dataset.textChanged.connect(self._validate_dataset)
        dataset_row.addWidget(self.training_dataset, 1)
        self.training_pick_button = QPushButton("Выбрать…")
        self.training_pick_button.setObjectName("iconButton")
        self.training_pick_button.clicked.connect(self._pick_dataset_file)
        dataset_row.addWidget(self.training_pick_button)
        self.dataset_chip = QLabel("датасет: не выбран")
        self.dataset_chip.setObjectName("statusChip")
        dataset_row.addWidget(self.dataset_chip)
        c2.addLayout(dataset_row)

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
        c2.addLayout(form)

        self.training_advanced_button = QPushButton("⚙ Дополнительно")
        self.training_advanced_button.setObjectName("iconButton")
        self.training_advanced_button.setCheckable(True)
        self.training_advanced_button.clicked.connect(
            lambda checked=False: self.training_advanced.setVisible(
                self.training_advanced_button.isChecked()
            )
        )
        c2.addWidget(self.training_advanced_button)

        self.training_advanced = QFrame()
        self.training_advanced.setObjectName("advancedBox")
        adv = QFormLayout(self.training_advanced)
        adv.setContentsMargins(12, 10, 12, 10)
        adv.setSpacing(10)
        self.training_base_model = QLineEdit()
        self.training_base_model.setText(TrainingParams().base_model)
        adv.addRow("Базовая модель (HF):", self.training_base_model)
        self.training_adapter_name = QLineEdit()
        self.training_adapter_name.setText(TrainingParams().adapter_name)
        adv.addRow("Имя адаптера:", self.training_adapter_name)
        self.training_ollama_model = QLineEdit()
        self.training_ollama_model.setText(TrainingParams().ollama_model_name)
        adv.addRow("Имя модели Ollama:", self.training_ollama_model)
        self.training_description = QLineEdit()
        self.training_description.setPlaceholderText("Короткое описание (необязательно)")
        adv.addRow("Описание:", self.training_description)
        self.training_export = QCheckBox("Экспорт GGUF + ollama create (нужен LLAMA_CPP_DIR)")
        self.training_export.setChecked(True)
        adv.addRow("", self.training_export)
        self.training_advanced.setVisible(False)
        c2.addWidget(self.training_advanced)
        layout.addWidget(card2)

        # ── шаг 3: запуск и мониторинг ───────────────────────────────
        card3 = QFrame()
        card3.setObjectName("stepCard")
        c3 = QVBoxLayout(card3)
        c3.setContentsMargins(16, 14, 16, 14)
        c3.setSpacing(10)
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
        c3.addLayout(buttons)
        self.training_view = QTextBrowser()
        self.training_view.setObjectName("messages")
        self.training_view.setHtml("<p>QLoRA: обучение → merge → GGUF → Ollama → реестр адаптеров.</p>")
        c3.addWidget(self.training_view, 1)
        layout.addWidget(card3, 1)

        self._refresh_training_steps()
        return page

    # ── models page (roadmap 18.2) ────────────────────────────────────────

    def _models_page(self) -> QWidget:
        from core.model_manager import ModelManager

        self.model_mgr = ModelManager()

        page = QWidget()
        page.setObjectName("page")
        layout = QVBoxLayout(page)
        layout.setContentsMargins(36, 24, 36, 20)
        layout.setSpacing(12)

        header = QHBoxLayout()
        title = QLabel("Модели")
        title.setObjectName("pageTitle")
        header.addWidget(title)
        header.addStretch()
        self.models_refresh_button = QPushButton("⟳")
        self.models_refresh_button.setObjectName("iconButton")
        self.models_refresh_button.setToolTip("Обновить список")
        self.models_refresh_button.clicked.connect(self._refresh_models)
        header.addWidget(self.models_refresh_button)
        layout.addLayout(header)

        subtitle = QLabel(
            "Каталог GGUF-моделей: скачивание с докачкой, импорт без перекачки. "
            "Хранилище: %LOCALAPPDATA%\\Fluxion\\models"
        )
        subtitle.setObjectName("subtitle")
        subtitle.setWordWrap(True)
        layout.addWidget(subtitle)

        self.models_status = QLabel()
        self.models_status.setObjectName("subtitle")
        self.models_status.setWordWrap(True)
        layout.addWidget(self.models_status)

        self.models_progress = QProgressBar()
        self.models_progress.setVisible(False)
        layout.addWidget(self.models_progress)

        self._model_buttons: dict[str, QPushButton] = {}
        for entry in self.model_mgr.catalog():
            card = QFrame()
            card.setObjectName("stepCard")
            row = QHBoxLayout(card)
            row.setContentsMargins(16, 12, 16, 12)
            row.setSpacing(10)
            name = QLabel(f"<b>{entry.name}</b>")
            name.setTextFormat(Qt.RichText)
            row.addWidget(name, 1)
            meta = QLabel(
                f"{entry.size_bytes / 1_000_000_000:.1f} ГБ"
                + (f" · ~{entry.vram_gb:.0f} ГБ VRAM" if entry.vram_gb else "")
                + (" · эмбеддинги" if entry.task == "embedding" else "")
                + (" · по умолчанию" if entry.default else "")
            )
            meta.setObjectName("statusChip")
            row.addWidget(meta)
            button = QPushButton()
            button.setProperty("suggestion", True)
            button.clicked.connect(lambda checked=False, mid=entry.model_id: self._on_model_button(mid))
            row.addWidget(button)
            self._model_buttons[entry.model_id] = button
            layout.addWidget(card)

        actions = QHBoxLayout()
        import_disk = QPushButton("Импорт с диска…")
        import_disk.setProperty("suggestion", True)
        import_disk.clicked.connect(self._import_model_disk)
        actions.addWidget(import_disk)
        import_ollama = QPushButton("Импорт из Ollama")
        import_ollama.setProperty("suggestion", True)
        import_ollama.clicked.connect(self._import_models_ollama)
        actions.addWidget(import_ollama)
        actions.addStretch()
        layout.addLayout(actions)

        self.models_installed = QLabel()
        self.models_installed.setObjectName("subtitle")
        self.models_installed.setWordWrap(True)
        layout.addWidget(self.models_installed)
        layout.addStretch()

        self._refresh_models()
        return page

    def _refresh_models(self) -> None:
        mm = getattr(self, "model_mgr", None)
        if mm is None:
            return
        for model_id, button in self._model_buttons.items():
            entry = mm.catalog_entry(model_id)
            if entry is None:
                continue
            if mm.is_installed(model_id):
                button.setText("Удалить")
            else:
                button.setText(f"Скачать ({entry.size_bytes / 1_000_000_000:.1f} ГБ)")
        installed = mm.list_installed()
        if installed:
            lines = [f"• {i.filename} — {i.size_bytes / 1_000_000_000:.2f} ГБ ({i.source or 'импорт'})" for i in installed]
            self.models_installed.setText("Установлены:\n" + "\n".join(lines))
        else:
            self.models_installed.setText("Установленных моделей нет.")

    def _on_model_button(self, model_id: str) -> None:
        mm = getattr(self, "model_mgr", None)
        if mm is None:
            return
        entry = mm.catalog_entry(model_id)
        if entry is None:
            return
        if mm.is_installed(model_id):
            answer = QMessageBox.question(
                self, "Удалить модель", f"Удалить {entry.filename} из хранилища?",
                QMessageBox.Yes | QMessageBox.No,
            )
            if answer == QMessageBox.Yes:
                mm.delete(entry.filename)
                self.models_status.setText(f"Удалено: {entry.filename}")
                self._refresh_models()
            return
        self._start_model_download(model_id)

    def _start_model_download(self, model_id: str) -> None:
        if getattr(self, "_model_download_worker", None) is not None:
            return
        self.models_progress.setVisible(True)
        self.models_progress.setRange(0, 0)
        self.models_status.setText("Скачиваем модель… докачка поддерживается")
        for button in self._model_buttons.values():
            button.setEnabled(False)
        self._model_download_worker = ModelDownloadWorker(model_id)
        self._model_download_worker.progress.connect(self._on_model_download_progress)
        self._model_download_worker.done.connect(self._on_model_download_done)
        self._model_download_worker.failed.connect(self._on_model_download_failed)
        self._model_download_worker.finished.connect(self._model_download_worker.deleteLater)
        self._model_download_worker.start()

    def _on_model_download_progress(self, done: int, total: int) -> None:
        if total > 0:
            self.models_progress.setRange(0, total)
            self.models_progress.setValue(done)
            self.models_status.setText(
                f"Скачиваем модель… {done / 1e9:.1f} / {total / 1e9:.1f} ГБ"
            )
        else:
            self.models_progress.setRange(0, 0)

    def _on_model_download_done(self, path: str) -> None:
        self._model_download_worker = None
        self.models_progress.setVisible(False)
        for button in self._model_buttons.values():
            button.setEnabled(True)
        self.models_status.setText(f"Модель загружена: {Path(path).name}")
        self._refresh_models()
        try:
            entry_name = Path(path).name
            default_entry = next((e for e in self.model_mgr.catalog() if e.filename == entry_name), None)
            if (
                default_entry is not None
                and default_entry.task == "chat"
                and str(getattr(self.settings, "backend", "") or "").lower() in ("llama_cpp", "llamacpp")
            ):
                self.settings.gguf_path = path
        except Exception:
            pass

    def _on_model_download_failed(self, message: str) -> None:
        self._model_download_worker = None
        self.models_progress.setVisible(False)
        for button in self._model_buttons.values():
            button.setEnabled(True)
        self.models_status.setText(message)

    def _import_model_disk(self) -> None:
        from PySide6.QtWidgets import QFileDialog

        path, _ = QFileDialog.getOpenFileName(self, "Выберите GGUF-модель", "", "GGUF (*.gguf)")
        if not path:
            return
        self._start_model_import(path)

    def _import_models_ollama(self) -> None:
        self._start_model_import(None)

    def _start_model_import(self, source_path: str | None) -> None:
        if getattr(self, "_model_import_worker", None) is not None:
            return
        self.models_status.setText("Импорт…")
        self._model_import_worker = ModelImportWorker(source_path)
        self._model_import_worker.done.connect(self._on_model_import_done)
        self._model_import_worker.failed.connect(self._on_model_download_failed)
        self._model_import_worker.finished.connect(self._model_import_worker.deleteLater)
        self._model_import_worker.start()

    def _on_model_import_done(self, message: str) -> None:
        self._model_import_worker = None
        self.models_status.setText(message)
        self._refresh_models()

    def _pick_dataset_file(self) -> None:
        from PySide6.QtWidgets import QFileDialog

        start = self.training_dataset.text().strip() or str(Path.home())
        path, _ = QFileDialog.getOpenFileName(
            self, "Выберите датасет", start, "JSONL (*.jsonl);;Все файлы (*.*)"
        )
        if path:
            self.training_dataset.setText(path)

    def _validate_dataset(self, path: str) -> None:
        chip = getattr(self, "dataset_chip", None)
        if chip is None:
            return
        path = path.strip()
        if not path:
            chip.setText("датасет: не выбран")
            state = "todo"
        else:
            file = Path(path)
            if file.is_file():
                chip.setText(f"датасет: {file.stat().st_size / 1_048_576:.1f} МБ ✓")
                state = "ok"
            else:
                chip.setText("датасет: файл не найден ⚠")
                state = "warn"
        chip.setProperty("state", state)
        chip.style().unpolish(chip)
        chip.style().polish(chip)
        self._refresh_training_steps()

    def _refresh_training_steps(self) -> None:
        ind = getattr(self, "training_steps", None)
        if ind is None:
            return
        env_ready = getattr(self, "_env_ready", False)
        allowed = getattr(self, "_train_allowed", False)
        running = getattr(self, "training_running", False)
        ind.set_states([
            "done" if env_ready else "current",
            "done" if running else ("current" if env_ready else "todo"),
            "current" if running else "todo",
        ])
        if hasattr(self, "training_run_button"):
            self.training_run_button.setEnabled(allowed and env_ready and not running)

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
                "QLoRA-обучение — Pro-функция. Активируйте лицензию: меню Помощь → Лицензия…"
            )
        self._train_allowed = allowed
        self._refresh_training_steps()

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
            self._env_ready = True
            self._refresh_training_steps()
            return
        trainer, name, _ = _detect_trainer(lambda message: None)
        if trainer is not None:
            self.training_env_label.setText(
                f"Окружение обучения: системное Python ({name})"
            )
            self.training_install_button.setVisible(False)
            self._env_ready = True
            self._refresh_training_steps()
            return
        self.training_env_label.setText(
            "Окружение обучения не найдено. Нажмите кнопку — программа сама "
            "проверит/установит Python 3.12 и развернёт venv "
            "(несколько ГБ: torch, unsloth, peft, trl)."
        )
        self.training_install_button.setVisible(True)
        self._env_ready = False
        self._refresh_training_steps()

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
                "<p>Движок не подключён. Запустите приложение через python -m desktop_browser.</p>"
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
        self._refresh_training_steps()

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

    def _open_health_dialog(self) -> None:
        HealthDialog(self).exec()

    def _startup_health_check(self) -> None:
        if self.settings is None:
            return
        self._health_banner_done = False
        self._health_worker = HealthWorker(
            self.settings, self.web_search, self.rag_service, parent=self
        )
        self._health_worker.done.connect(self._on_startup_health)
        self._health_worker.start()

    def _on_startup_health(self, statuses) -> None:
        if getattr(self, "_health_banner_done", False):
            return
        self._health_banner_done = True
        from .health import summary_line

        line = summary_line(statuses)
        if line:
            self._append_banner(
                f"{line}. Нажмите «Состояние» слева внизу — там причина и кнопка «Исправить»."
            )

    def _append_banner(self, text: str) -> None:
        """Zero Silence (14.2): visible explanation instead of silent degradation."""
        self._messages.append({"role": "banner", "text": text, "meta": ""})
        self._render_messages()

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
        if hasattr(self, "banners"):
            if available:
                self.banners.clear()
            else:
                self.banners.show_warning(
                    "Модель недоступна: проверь Ollama или движок.",
                    "Повторить",
                    self._load_models,
                )

    # ── chat flow ────────────────────────────────────────────────────────

    def send_message(self) -> None:
        text = self.prompt.text().strip()
        if not text or self.generating or self.agent_running:
            return
        if self.assistant is None:
            self._append_message("assistant", "Движок не подключён. Запустите приложение через python -m desktop_browser.")
            return
        self.prompt.clear()
        if self.chat_agent_files.isChecked() and self.agent_factory is not None:
            self._send_agent_message(text)
            return

        history = list(self.history)
        self._last_query = text
        self._web_opened_for_current = False
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
                self._append_message("assistant", "Движок не подключён. Запустите приложение через python -m desktop_browser.")
                return
            self.prompt.setPlaceholderText("Задача для агента: например, прочитай main.py и исправь баг...")
        else:
            self.prompt.setPlaceholderText("Напишите сообщение...")

    def _send_agent_message(self, task: str) -> None:
        from licensing import feature_enabled

        self._last_query = task
        self._web_opened_for_current = False
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
        from orchestrator.presentation import format_agent_step

        tool = getattr(step, "tool_name", "")
        args = getattr(step, "tool_args", "") or ""
        if tool == "web_search" and args:
            self._show_web_results(args)
            self._maybe_show_fetch_notice()
        # finish is not shown here: its text is the final answer, added once
        # in _on_chat_agent_done (it used to appear twice).
        text = format_agent_step(step)
        if text:
            entry = self._messages[-1]
            entry["text"] = (entry["text"] + "\n" if entry["text"] else "") + text
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
            if fragments:
                fragments.append("")  # blank line between the steps and the answer
            fragments.append(answer)
            self.history.append({"role": "assistant", "content": answer})
        entry["text"] = "\n".join(fragments) or "(пустой ответ)"
        self._set_generating(False)
        self.agent_worker = None
        self._render_messages()
        self._save_memory()

    def _on_chat_agent_failed(self, message: str) -> None:
        entry = self._messages[-1]
        entry["text"] = (entry["text"] + "\n" if entry["text"] else "") + message
        self._set_generating(False)
        self.agent_worker = None
        self._render_messages()
        self._save_memory()

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
        if parts:
            self._messages[-1]["meta"] = "[" + " | ".join(parts) + "]"
            self._render_messages()
        if getattr(result, "web_used", False) and not self._web_opened_for_current:
            self._web_opened_for_current = True
            self._show_web_results(self._last_query)
        if getattr(result, "web_used", False):
            self._maybe_show_fetch_notice()

    def _maybe_show_fetch_notice(self) -> None:
        if getattr(self.web_search, "brief_mode", False):
            self.banners.show_warning(
                "Lite-режим: страница в краткой выжимке. Полная — в Full-сборке.",
                key="fetch_lite",
            )

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
        self._save_memory()

    def _on_failed(self, message: str) -> None:
        current = self._messages[-1]
        current["text"] = (current["text"] + "\n" if current["text"] else "") + message
        self._set_generating(False)
        self._render_messages()
        self._save_memory()

    def _append_message(self, role: str, text: str) -> None:
        self._messages.append({"role": role, "text": text, "meta": ""})
        self._render_messages()

    def _render_messages(self) -> None:
        if not self._messages:
            self.messages.setHtml(WELCOME_HTML)
        else:
            parts = []
            for entry in self._messages:
                if entry["role"] == "banner":
                    parts.append(f"<p><i>⚠ {html.escape(entry['text'])}</i></p>")
                    continue
                who = "Вы" if entry["role"] == "user" else "Fluxion"
                parts.append(f"<p><b>{who}</b></p>")
                if entry["meta"]:
                    parts.append(f"<p><i>{html.escape(entry['meta'])}</i></p>")
                if entry.get("thinking"):
                    spinner = "◐◓◑◒"[self._think_frame % 4]
                    parts.append(f"<p><i>{spinner} Думаю…</i></p>")
                    continue
                from orchestrator.presentation import render_chat_html

                body = render_chat_html(entry["text"]) or "…"
                parts.append(f"<div>{body}</div>")
            self.messages.setHtml("".join(parts))
        scrollbar = self.messages.verticalScrollBar()
        scrollbar.setValue(scrollbar.maximum())

    # ── license ──────────────────────────────────────────────────────────

    def _refresh_license_badge(self) -> None:
        lic = load_activation()
        is_pro = lic is not None and lic.is_pro
        act = getattr(self, "act_license", None)
        if act is not None:
            act.setText("Лицензия: PRO" if is_pro else "Лицензия: FREE")
            act.setToolTip(lic.email if lic is not None else "")
        btn = getattr(self, "license_button", None)
        if btn is not None:
            btn.setText("PRO" if is_pro else "FREE")
            btn.setToolTip(("Лицензия PRO: " + lic.email) if is_pro and lic.email else "Лицензия: FREE")
            btn.setProperty("plan", "pro" if is_pro else "free")
            self.style().unpolish(btn)
            self.style().polish(btn)
        self._refresh_training_gate()
        self._refresh_training_env()

    def _open_license_dialog(self) -> None:
        from .license_dialog import LicenseDialog

        dialog = LicenseDialog(self)
        dialog.exec()
        self._refresh_license_badge()

    # ── updates ──────────────────────────────────────────────────────────

    def _check_updates(self) -> None:
        self.statusBar().showMessage("Проверка обновлений…")
        self._update_worker = UpdateWorker(APP_VERSION)
        self._update_worker.done.connect(self._on_update_result)
        self._update_worker.finished.connect(self._update_worker.deleteLater)
        self._update_worker.start()

    def _on_update_result(self, info) -> None:
        self.statusBar().showMessage("Готово", 3000)
        if info is None:
            QMessageBox.warning(
                self, "Обновления", "Не удалось проверить обновления: нет соединения с GitHub."
            )
            return
        if not info.is_newer:
            QMessageBox.information(
                self, "Обновления", f"У вас последняя версия — {info.current_version}."
            )
            return
        box = QMessageBox(self)
        box.setWindowTitle("Доступна новая версия")
        box.setText(f"Fluxion {info.latest_version} (у вас {info.current_version}).")
        notes = info.release_notes.strip()
        if notes:
            box.setInformativeText(notes[:1500])
        open_btn = box.addButton("Открыть страницу загрузки", QMessageBox.AcceptRole)
        box.addButton("Позже", QMessageBox.RejectRole)
        box.exec()
        if box.clickedButton() is open_btn and info.release_url:
            QDesktopServices.openUrl(QUrl(info.release_url))

    # ── theme ────────────────────────────────────────────────────────────

    def toggle_theme(self) -> None:
        self.apply_theme(LIGHT if self.theme.name == "dark" else DARK)

    def apply_theme(self, theme: Theme) -> None:
        self.theme = theme
        self.qsettings.setValue("theme", theme.name)
        if hasattr(self, "act_theme"):
            self.act_theme.setText("Светлая тема" if theme.name == "dark" else "Тёмная тема")

        mono_font = '"JetBrains Mono", Consolas, monospace'
        ui_font = '"Space Grotesk", "Segoe UI", sans-serif'

        bg0 = "#0A0E17" if theme.name == "dark" else "#F8FAFC"
        bg1 = "#0F1520" if theme.name == "dark" else "#FFFFFF"
        bg2 = "#151D2C" if theme.name == "dark" else "#F1F5F9"
        border = "#1E293B" if theme.name == "dark" else "#E2E8F0"
        text1 = "#E6EAF2" if theme.name == "dark" else "#0F172A"
        text2 = "#94A3B8" if theme.name == "dark" else "#64748B"
        accent = "#00E5FF"
        primary = "#7B2FFF"
        ok = "#B4FF39"
        err = "#FF5C5C"

        self.setStyleSheet(f"""
            QMainWindow, QWidget#page {{ background: {bg0}; color: {text1}; font-family: {ui_font}; font-size: 14px; }}

            /* Сайдбар */
            QFrame#sidebar {{ background: {bg1}; border-right: 1px solid {border}; }}
            QLabel#brand {{ color: {accent}; font-size: 20px; font-weight: 700; font-family: {mono_font}; }}
            QLabel#tagline, QLabel#subtitle {{ color: {text2}; font-size: 13px; }}
            QPushButton[nav="true"] {{ border: none; border-radius: 6px; padding: 8px 12px; text-align: left; background: transparent; color: {text1}; }}
            QPushButton[nav="true"]:hover {{ background: {bg2}; }}
            QPushButton[nav="true"]:checked {{ background: {bg2}; color: {accent}; font-weight: 600; border-left: 2px solid {accent}; }}

            /* Заголовки */
            QLabel#pageTitle {{ color: {text1}; font-size: 20px; font-weight: 600; }}

            /* Инпуты и контролы */
            QComboBox, QSpinBox, QLineEdit {{ background: {bg2}; color: {text1}; border: 1px solid {border}; border-radius: 6px; padding: 6px 10px; font-family: {ui_font}; selection-background-color: {accent}; }}
            QComboBox:focus, QSpinBox:focus, QLineEdit:focus {{ border-color: {accent}; }}
            QComboBox:disabled {{ color: {text2}; }}
            QComboBox QAbstractItemView {{ background: {bg1}; color: {text1}; border: 1px solid {border}; selection-background-color: {accent}; }}

            /* Кнопки */
            QPushButton {{ border: none; border-radius: 6px; padding: 8px 14px; color: {text1}; background: transparent; }}
            QPushButton:hover {{ background: {bg2}; }}
            QPushButton[suggestion="true"] {{ background: {bg2}; border: 1px solid {border}; }}
            QPushButton[suggestion="true"]:hover {{ border-color: {accent}; }}

            /* Primary (Отправить, Запустить, Проиндексировать) */
            QPushButton#sendButton {{ background: {primary}; color: white; font-weight: 600; }}
            QPushButton#sendButton:hover {{ background: #8B4BFF; }}
            QPushButton#sendButton:disabled {{ background: {bg2}; color: {text2}; }}

            /* Stop / Danger */
            QPushButton#stopButton {{ background: {bg2}; color: {err}; border: 1px solid {border}; }}
            QPushButton#stopButton:hover {{ border-color: {err}; }}

            /* Чат и логи */
            QTextBrowser#messages {{ background: {bg1}; color: {text1}; border: 1px solid {border}; border-radius: 6px; padding: 16px; }}
            QTextBrowser#messages pre {{ background: {bg2}; border-radius: 6px; padding: 10px; font-family: {mono_font}; font-size: 13px; }}
            QFrame#composer {{ background: {bg1}; border: 1px solid {border}; border-radius: 6px; }}
            QFrame#composer QLineEdit {{ background: transparent; border: none; padding: 6px; }}

            /* Чекбоксы */
            QCheckBox {{ color: {text1}; spacing: 8px; }}

            /* Статусы */
            QLabel#engineStatus {{ color: {ok}; font-family: {mono_font}; font-size: 12px; }}
            QLabel#engineStatus[state="offline"] {{ color: {err}; }}
            QLabel#licenseStatus[plan="pro"] {{ color: {ok}; font-size: 18px; font-weight: 700; }}
            QLabel#licenseStatus[plan="free"] {{ color: {text1}; font-size: 18px; font-weight: 700; }}
            QLabel#licenseMessage {{ color: {text2}; }}

            /* Менюбар */
            QMenuBar {{ background: {bg1}; color: {text1}; border-bottom: 1px solid {border}; }}
            QMenuBar::item:selected {{ background: {bg2}; }}
            QMenu {{ background: {bg1}; color: {text1}; border: 1px solid {border}; }}
            QMenu::item:selected {{ background: {bg2}; color: {accent}; }}

            /* Статус-бар */
            QStatusBar#mainStatusBar {{ background: {bg1}; color: {text2}; border-top: 1px solid {border}; font-family: {mono_font}; font-size: 12px; }}
            QStatusBar::item {{ border: none; }}
            QLabel#statusChip {{ color: {text2}; font-family: {mono_font}; font-size: 12px; padding: 0 10px; }}
            QPushButton#licenseButton {{ background: transparent; border: none; color: {text2}; font-size: 12px; padding: 2px 8px; }}
            QPushButton#licenseButton[plan="pro"] {{ color: {ok}; font-weight: 600; }}
            QToolButton {{ background: transparent; color: {text2}; border: none; border-radius: 6px; padding: 4px 8px; font-family: {ui_font}; }}
            QToolButton:hover {{ background: {bg2}; color: {text1}; }}
            QToolButton:checked {{ color: {accent}; }}

            /* Иконки-кнопки и баннеры */
            QPushButton#iconButton, QToolButton#iconButton {{ background: transparent; border: 1px solid {border}; border-radius: 6px; padding: 3px 10px; font-size: 15px; }}
            QPushButton#iconButton:hover, QToolButton#iconButton:hover {{ background: {bg2}; border-color: {accent}; }}
            QFrame#banner {{ background: {bg2}; border: 1px solid {border}; border-left: 3px solid #FFB454; border-radius: 6px; }}
            QLabel#bannerIcon {{ color: #FFB454; font-size: 15px; }}
            QLabel#bannerText {{ color: {text1}; }}
            QPushButton#bannerAction {{ background: {bg1}; border: 1px solid {border}; border-radius: 6px; padding: 4px 10px; }}
            QPushButton#bannerAction:hover {{ border-color: {accent}; }}
            QPushButton#bannerClose {{ background: transparent; color: {text2}; padding: 2px 6px; font-size: 14px; }}

            /* Страница агента */
            QTextEdit#agentTask {{ background: {bg1}; color: {text1}; border: 1px solid {border}; border-radius: 6px; padding: 10px; }}
            QTextEdit#agentTask:focus {{ border-color: {accent}; }}
            QFrame#advancedBox {{ background: {bg2}; border: 1px solid {border}; border-radius: 6px; }}
            QCheckBox#writeChip {{ background: {bg2}; border: 1px solid {border}; border-radius: 6px; padding: 6px 12px; }}
            QCheckBox#writeChip:checked {{ border-color: {accent}; color: {accent}; }}

            /* Карточки шагов и индикатор */
            QFrame#stepCard {{ background: {bg1}; border: 1px solid {border}; border-radius: 6px; }}
            QLabel#stepDot {{ border: 1px solid {border}; border-radius: 12px; color: {text2}; background: transparent; font-weight: 600; }}
            QLabel#stepDot[state="done"] {{ background: {ok}; border-color: {ok}; color: {bg0}; }}
            QLabel#stepDot[state="current"] {{ border-color: {accent}; color: {accent}; }}
            QFrame#stepLine {{ border-top: 1px solid {border}; }}
            QLabel#statusChip[state="ok"] {{ color: {ok}; }}
            QLabel#statusChip[state="warn"] {{ color: #FFB454; }}
            QLabel#statusChip[state="todo"] {{ color: {text2}; }}
            QLabel#statusChip[state="current"] {{ color: {accent}; }}
            QLineEdit#monoField {{ background: {bg2}; color: {text1}; border: 1px solid {border}; border-radius: 6px; padding: 8px 10px; font-family: Consolas, "JetBrains Mono", monospace; font-size: 13px; }}

            /* Кликабельные чипы статус-бара и их поповеры */
            QToolButton#engineChip, QToolButton#indexChip {{ background: transparent; border: 0; padding: 2px 10px; font-family: Consolas, "JetBrains Mono", monospace; font-size: 12px; color: {text2}; }}
            QToolButton#engineChip:hover, QToolButton#indexChip:hover {{ background: {bg2}; border-radius: 6px; }}
            QToolButton#engineChip[state="online"], QToolButton#indexChip[state="ok"] {{ color: {ok}; }}
            QToolButton#engineChip[state="offline"], QToolButton#indexChip[state="err"] {{ color: {err}; }}
            QToolButton#indexChip[state="warn"] {{ color: #FFB454; }}
            QToolButton#engineChip[state="current"], QToolButton#indexChip[state="current"] {{ color: {accent}; }}
            QWidget#chipPopover {{ background: {bg1}; border: 1px solid {border}; border-radius: 6px; }}
            QLabel#popKey {{ color: {text2}; }}
            QLabel#popValue {{ color: {text1}; font-family: Consolas, "JetBrains Mono", monospace; font-size: 12px; }}
            QLabel#popNote {{ color: #FFB454; }}
            QPushButton#popAction {{ background: {bg2}; border: 1px solid {border}; border-radius: 6px; padding: 6px 10px; text-align: left; }}
            QPushButton#popAction:hover {{ border-color: {accent}; }}

            /* Браузер-панель */
            QWidget#webPanel {{ background: {bg1}; border: 1px solid {border}; border-radius: 6px; }}
            QLabel#webPanelHeader {{ color: {text2}; }}
            QLineEdit#webAddress {{ background: {bg2}; color: {text1}; border: 1px solid {border}; border-radius: 6px; padding: 6px 10px; selection-background-color: {accent}; }}
            QPushButton#webNavButton {{ background: {bg2}; color: {text1}; border: 1px solid {border}; border-radius: 6px; padding: 6px 10px; text-align: center; min-width: 24px; }}
            QPushButton#webNavButton:hover {{ border-color: {accent}; }}
            QSplitter::handle {{ background: {border}; width: 3px; }}
        """)
        dark = theme.name == "dark"
        for target in (self.web_panel, getattr(self, "brand_banner", None)):
            apply_colors = getattr(target, "apply_colors", None)
            if callable(apply_colors):
                apply_colors(dark)

    # ── shutdown ─────────────────────────────────────────────────────────

    def closeEvent(self, event) -> None:
        self._save_memory()
        if self.worker is not None and self.generating:
            self.worker.stop()
            self.worker.wait(2000)
        if self.agent_worker is not None and (self.agent_running or self.generating):
            self.agent_worker.stop()
            self.agent_worker.wait(2000)
        if self.index_worker is not None and self.indexing:
            self.index_worker.wait(5000)
        if getattr(self, "_update_worker", None) is not None:
            self._update_worker.wait(5000)
        if getattr(self, "_model_download_worker", None) is not None:
            self._model_download_worker.wait(10000)
        if getattr(self, "_model_import_worker", None) is not None:
            self._model_import_worker.wait(10000)
        if self.status_worker is not None:
            self.status_worker.wait(10000)
        if self._health_worker is not None:
            self._health_worker.wait(10000)
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

    try:
        import PySide6.QtWebEngineWidgets  # noqa: F401  (must precede QApplication)
    except Exception:
        pass

    app = QApplication(sys.argv)
    app.setApplicationName("Fluxion")
    app.setOrganizationName("Fluxion")
    app.setStyle("Fusion")

    from .crash import install_excepthook

    install_excepthook()

    try:
        try:
            assistant, backend, settings, rag_service, web_search = build_engine()

            if web_search is not None and os.environ.get("FLUXION_DESKTOP_SMOKE") != "1":
                from threading import Thread

                from .searxng import ensure_searxng

                Thread(
                    target=ensure_searxng,
                    args=(settings.web.searxng_url,),
                    daemon=True,
                    name="searxng-autostart",
                ).start()

            def agent_factory(task: str, allow_write: bool, max_iter: int):
                qs = QSettings("Fluxion", "Fluxion")
                picked = str(qs.value("project_path", "") or "")
                return CodingAgent(
                    backend=backend,
                    project_root=resolve_project_root(picked, settings),
                    rag_service=rag_service,
                    web_search=web_search,
                    max_iterations=max_iter,
                    allow_write=allow_write,
                    git_enabled=bool(int(qs.value("agent_git_enabled", 1) or 1)),
                    lang=getattr(settings, "language", "auto"),
                )

            window = FluxionWindow(
                assistant=assistant,
                backend=backend,
                settings=settings,
                agent_factory=agent_factory,
                rag_service=rag_service,
                web_search=web_search,
            )
        except Exception as exc:
            rag_service = None
            window = FluxionWindow()
            window.status_label.setText(f"Ошибка движка: {exc}")
        window.show()

        if (
            os.environ.get("FLUXION_DESKTOP_SMOKE") != "1"
            and getattr(window, "settings", None) is not None
            and not window.qsettings.value("onboarded", 0)
        ):
            from .wizard import FirstRunWizard

            FirstRunWizard(
                window.settings,
                window.web_search,
                window.rag_service,
                window.qsettings,
                parent=window,
            ).exec()

        if os.environ.get("FLUXION_DESKTOP_SMOKE") == "1":
            app.processEvents()
            lic = load_activation()
            print(
                "[smoke] license:",
                lic.email if lic is not None else None,
                "pro =", bool(lic is not None and lic.is_pro),
            )
            window.close()
            result = _rag_smoke_check(rag_service) if os.environ.get("FLUXION_SMOKE_RAG") == "1" else 0
        else:
            result = app.exec()

        window.deleteLater()
        app.processEvents()
        del window
        return result
    except Exception:
        from .crash import show_crash_dialog, write_crash

        log_path = write_crash(*sys.exc_info())
        try:
            show_crash_dialog(log_path)
        except Exception:
            pass
        return 1


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
