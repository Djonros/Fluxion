"""First-run setup wizard (Phase 14.3): check deps → install → pull model → test chat."""
from __future__ import annotations

import html

from PySide6.QtCore import Qt, QThread, Signal
from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QProgressBar,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from .health import DependencyStatus, check_all, linkify
from .installer import WINGET_PACKAGES, ensure_docker, refresh_path, winget_available, winget_install


class WizardCheckWorker(QThread):
    done = Signal(object)

    def __init__(self, settings, web_search, rag_service, parent=None):
        super().__init__(parent)
        self.settings = settings
        self.web_search = web_search
        self.rag_service = rag_service

    def run(self) -> None:
        self.done.emit(check_all(self.settings, self.web_search, self.rag_service))


class InstallWorker(QThread):
    """Silently installs missing winget packages, then brings services up."""

    line = Signal(str)
    done = Signal(bool, str)

    def __init__(self, keys: list[str], parent=None):
        super().__init__(parent)
        self.keys = keys

    def run(self) -> None:
        ok_all = True
        report: list[str] = []
        for key in self.keys:
            package = WINGET_PACKAGES.get(key)
            if package is None:
                continue
            self.line.emit(f"Устанавливаем {key} ({package})…")
            ok, output = winget_install(package, on_line=self.line.emit)
            ok_all = ok_all and ok
            entry = f"{key}: {'установлен' if ok else 'не установлен'}"
            if not ok:
                tail = output.strip().splitlines()[-1] if output.strip() else ""
                if tail:
                    entry += f" — {tail}"
            report.append(entry)
        refresh_path()
        if "docker" in self.keys:
            self.line.emit("Запускаем Docker Desktop…")
            if not ensure_docker(wait_secs=150.0, on_status=self.line.emit):
                ok_all = False
                report.append(
                    "docker: демон не поднялся — возможно, нужна перезагрузка после установки"
                )
        self.done.emit(ok_all, "\n".join(report))


class PullWorker(QThread):
    progress = Signal(str, int)  # status text, percent (-1 = indeterminate)
    failed = Signal(str)
    done = Signal()

    def __init__(self, host: str, model: str, parent=None):
        super().__init__(parent)
        self.host = host
        self.model = model

    def run(self) -> None:
        from core.ollama_client import OllamaClient

        client = OllamaClient(host=self.host, model=self.model)
        try:
            for event in client.pull():
                status = str(event.get("status", "")).strip()
                total = event.get("total")
                completed = event.get("completed")
                if total and completed is not None:
                    pct = int(100 * completed / total)
                else:
                    pct = -1
                self.progress.emit(status, pct)
        except Exception as exc:
            self.failed.emit(f"Ошибка загрузки модели: {exc}")
            return
        self.done.emit()


class GgufDownloadWorker(QThread):
    """Downloads the default catalog GGUF with progress + resume (roadmap 18.6)."""

    progress = Signal(str, int)  # status text, percent (-1 = indeterminate)
    failed = Signal(str)
    done = Signal()

    def __init__(self, model_id: str = "qwen2.5-coder-7b-q4km", parent=None):
        super().__init__(parent)
        self.model_id = model_id

    def run(self) -> None:
        from core.model_manager import ModelManager

        try:
            mm = ModelManager()
            mm.download(
                self.model_id,
                on_progress=lambda done, total: self.progress.emit(
                    "Скачиваем модель…", int(100 * done / total) if total else -1
                ),
            )
        except Exception as exc:
            self.failed.emit(f"Ошибка загрузки модели: {exc}")
            return
        self.done.emit()


class LlamaTestWorker(QThread):
    """Chat smoke-test for the embedded llama.cpp backend."""

    ok = Signal(str)
    failed = Signal(str)

    def __init__(self, settings, parent=None):
        super().__init__(parent)
        self.settings = settings

    def run(self) -> None:
        try:
            from core.backend_factory import BackendFactory

            backend = BackendFactory.create_primary(self.settings)
            reply = str(
                backend.generate([{"role": "user", "content": "Ответь одним словом: работает?"}])
            )
        except Exception as exc:
            self.failed.emit(f"Движок llama.cpp не ответил: {exc}")
            return
        text = reply.strip().splitlines()[0] if reply.strip() else ""
        self.ok.emit(text[:120] or "(пустой ответ)")


def _backend_kind(settings) -> str:
    """ollama | llama_cpp — which engine the wizard should set up."""
    try:
        from core.backend_factory import _detect_backend_type

        return _detect_backend_type(settings)
    except Exception:
        return "ollama"


class TestWorker(QThread):
    ok = Signal(str)
    failed = Signal(str)

    def __init__(self, host: str, model: str, parent=None):
        super().__init__(parent)
        self.host = host
        self.model = model

    def run(self) -> None:
        from core.ollama_client import OllamaClient, OllamaError

        client = OllamaClient(host=self.host, model=self.model)
        try:
            reply = client.chat(
                [{"role": "user", "content": "Ответь одним словом: работает?"}],
                options={"num_predict": 16},
            )
        except OllamaError as exc:
            self.failed.emit(f"Модель не ответила: {exc}")
            return
        except Exception as exc:
            self.failed.emit(f"Ошибка соединения: {exc}")
            return
        text = reply.strip().splitlines()[0] if reply.strip() else ""
        self.ok.emit(text[:120] or "(пустой ответ)")


class FirstRunWizard(QDialog):
    """Check → download model → verify chat. Sets ``onboarded`` on finish."""

    def __init__(self, settings, web_search, rag_service, qsettings, parent=None):
        super().__init__(parent)
        self.settings = settings
        self.web_search = web_search
        self.rag_service = rag_service
        self.qsettings = qsettings
        self._statuses: list[DependencyStatus] = []
        self._pull_worker: PullWorker | None = None
        self._test_worker: TestWorker | None = None
        self._install_worker: InstallWorker | None = None

        self.setWindowTitle("Fluxion — первичная настройка")
        self.setMinimumWidth(520)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 20, 24, 20)
        layout.setSpacing(10)

        title = QLabel("Добро пожаловать!")
        title.setStyleSheet("font-size: 16px; font-weight: 600;")
        layout.addWidget(title)
        layout.addWidget(QLabel("Проверим зависимости. Всё настраивается локально — код не покидает машину."))

        self.check_label = QLabel("Проверяем зависимости…")
        self.check_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.check_label.setWordWrap(True)
        self.check_label.setOpenExternalLinks(True)
        layout.addWidget(self.check_label)

        self.install_button = QPushButton("Установить недостающее автоматически")
        self.install_button.setObjectName("themeButton")
        self.install_button.clicked.connect(self._start_install)
        self.install_button.setVisible(False)
        layout.addWidget(self.install_button)

        self.install_status = QLabel()
        self.install_status.setWordWrap(True)
        self.install_status.setVisible(False)
        layout.addWidget(self.install_status)

        self.pull_button = QPushButton()
        self.pull_button.clicked.connect(self._start_pull)
        self.pull_button.setVisible(False)
        layout.addWidget(self.pull_button)

        self.pull_progress = QProgressBar()
        self.pull_progress.setVisible(False)
        layout.addWidget(self.pull_progress)

        self.pull_status = QLabel()
        self.pull_status.setWordWrap(True)
        self.pull_status.setVisible(False)
        layout.addWidget(self.pull_status)

        buttons = QHBoxLayout()
        self.test_button = QPushButton("Проверить связь")
        self.test_button.clicked.connect(self._start_test)
        buttons.addWidget(self.test_button)
        buttons.addStretch()
        self.finish_button = QPushButton("Готово")
        self.finish_button.setObjectName("sendButton")
        self.finish_button.clicked.connect(self.accept)
        buttons.addWidget(self.finish_button)
        layout.addLayout(buttons)

        self.test_result = QLabel()
        self.test_result.setWordWrap(True)
        layout.addWidget(self.test_result)

        self._run_checks()

    # ── checks ───────────────────────────────────────────────────────────

    def _run_checks(self) -> None:
        self.check_worker = WizardCheckWorker(
            self.settings, self.web_search, self.rag_service
        )
        self.check_worker.done.connect(self._on_checks)
        self.check_worker.start()

    def _on_checks(self, statuses: list[DependencyStatus]) -> None:
        self._statuses = statuses
        lines = []
        model_missing = False
        for s in statuses:
            mark = "✅" if s.available else "⚠️"
            line = f"<b>{html.escape(s.title)}</b>: {html.escape(s.detail)}"
            if not s.available and s.remedy:
                line += f"<br><i>{linkify(s.remedy)}</i>"
            lines.append(f"{mark} {line}")
            if s.key == "model" and not s.available:
                model_missing = True
        self.check_label.setText("<br>".join(lines))
        if model_missing:
            model = getattr(self.settings, "model", "модель")
            if _backend_kind(self.settings) == "llama_cpp":
                self.pull_button.setText("Скачать модель (GGUF, с докачкой)")
            else:
                self.pull_button.setText(f"Скачать модель {model}")
            self.pull_button.setVisible(True)
        installable = [
            s.key for s in statuses
            if not s.available and s.key in WINGET_PACKAGES
        ]
        if installable and winget_available():
            self.install_button.setText(
                f"Установить автоматически ({', '.join(installable)})"
            )
            self.install_button.setVisible(True)
        else:
            self.install_button.setVisible(False)

    # ── auto-install ─────────────────────────────────────────────────────

    def _start_install(self) -> None:
        keys = [
            s.key for s in self._statuses
            if not s.available and s.key in WINGET_PACKAGES
        ]
        if not keys:
            return
        self.install_button.setEnabled(False)
        self.install_status.setVisible(True)
        self.install_status.setText("Установка может занять несколько минут…")
        self._install_worker = InstallWorker(keys)
        self._install_worker.line.connect(self._on_install_line)
        self._install_worker.done.connect(self._on_install_done)
        self._install_worker.start()

    def _on_install_line(self, text: str) -> None:
        self.install_status.setText(text)

    def _on_install_done(self, ok: bool, report: str) -> None:
        self.install_status.setText(("✅ " if ok else "⚠️ ") + report)
        self.install_button.setEnabled(True)
        self._run_checks()

    # ── model pull ───────────────────────────────────────────────────────

    def _start_pull(self) -> None:
        self.pull_button.setEnabled(False)
        self.pull_progress.setVisible(True)
        self.pull_progress.setRange(0, 100)
        self.pull_status.setVisible(True)
        if _backend_kind(self.settings) == "llama_cpp":
            self.pull_status.setText("Скачиваем модель…")
            self._pull_worker = GgufDownloadWorker()
        else:
            self.pull_status.setText("Подключение к Ollama…")
            self._pull_worker = PullWorker(
                getattr(self.settings, "ollama_host", "http://localhost:11434"),
                getattr(self.settings, "model", ""),
            )
        self._pull_worker.progress.connect(self._on_pull_progress)
        self._pull_worker.failed.connect(self._on_pull_failed)
        self._pull_worker.done.connect(self._on_pull_done)
        self._pull_worker.start()

    def _on_pull_progress(self, status: str, pct: int) -> None:
        if status:
            self.pull_status.setText(status)
        if pct >= 0:
            self.pull_progress.setValue(pct)
        else:
            self.pull_progress.setRange(0, 0)

    def _on_pull_failed(self, message: str) -> None:
        self.pull_progress.setRange(0, 100)
        self.pull_progress.setVisible(False)
        self.pull_status.setText(f"⚠️ {message}")
        self.pull_button.setEnabled(True)

    def _on_pull_done(self) -> None:
        self.pull_progress.setRange(0, 100)
        self.pull_progress.setValue(100)
        self.pull_status.setText("✅ Модель скачана.")
        self.pull_button.setVisible(False)
        self._run_checks()

    # ── connection test ──────────────────────────────────────────────────

    def _start_test(self) -> None:
        self.test_button.setEnabled(False)
        self.test_result.setText("Проверяем связь с моделью…")
        if _backend_kind(self.settings) == "llama_cpp":
            self._test_worker = LlamaTestWorker(self.settings)
        else:
            self._test_worker = TestWorker(
                getattr(self.settings, "ollama_host", "http://localhost:11434"),
                getattr(self.settings, "model", ""),
            )
        self._test_worker.ok.connect(self._on_test_ok)
        self._test_worker.failed.connect(self._on_test_failed)
        self._test_worker.start()

    def _on_test_ok(self, reply: str) -> None:
        self.test_result.setText(f"✅ Модель отвечает: «{reply}»")
        self.test_button.setEnabled(True)

    def _on_test_failed(self, message: str) -> None:
        self.test_result.setText(f"⚠️ {message}")
        self.test_button.setEnabled(True)

    # ── finish ───────────────────────────────────────────────────────────

    def accept(self) -> None:
        self.qsettings.setValue("onboarded", 1)
        super().accept()

    def reject(self) -> None:
        self.qsettings.setValue("onboarded", 1)
        super().reject()
