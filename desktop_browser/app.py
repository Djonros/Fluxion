from __future__ import annotations

import html
import re
import os
import sys
import time
import uuid
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import QPoint, QRect, QSize, Qt, QSettings, QThread, QTimer, QUrl, Signal
from PySide6.QtGui import QAction, QColor, QDesktopServices, QIcon, QKeySequence, QShortcut, QTextDocument
from PySide6.QtWidgets import (
    QApplication,
    QButtonGroup,
    QCheckBox,
    QComboBox,
    QDialog,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLayout,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
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
    QScrollArea,
    QToolButton,
    QTextBrowser,
    QVBoxLayout,
    QWidget,
)

from cli.theme import DARK, LIGHT, Theme
from core.language import LANG_NAMES, matches_language, resolve_target
from licensing.store import load_activation
from . import APP_VERSION, SITE_URL
from .banner import BrandBanner
from .i18n import LANGUAGES, SETTING_KEY as LANGUAGE_SETTING, language, load_catalog, tr
from .browser import create_browser_panel
from .memory import delete_session, load_sessions, save_session, session_title
from .runtime import TEMP_RUN_WARNING, is_temp_run
from .training import TrainingError, TrainingParams, run_pipeline


APP_ROOT = Path(__file__).resolve().parent.parent


def message_sound_enabled(qsettings: QSettings) -> bool:
    """The "sound when Fluxion answered" switch; on by default."""
    raw = qsettings.value("message_sound", 1)
    return str(raw).strip().lower() not in ("0", "false", "")


def flag_setting(qsettings: QSettings, key: str, default: bool = False) -> bool:
    """A stored on/off switch; a stored 0 may come back as int or text."""
    raw = qsettings.value(key, int(default))
    return str(raw).strip().lower() not in ("0", "false", "")


def voice_model_id(qsettings: QSettings) -> str:
    """The speech recognition model of voice input (voice.models.SPEECH_MODELS)."""
    from voice.models import DEFAULT_SPEECH_MODEL, speech_model

    value = str(qsettings.value("voice_model", DEFAULT_SPEECH_MODEL) or "")
    return value if speech_model(value) is not None else DEFAULT_SPEECH_MODEL


def git_tools_enabled(qsettings: QSettings) -> bool:
    """Read the "agent git tools" switch; a stored 0 may come back as int or text."""
    raw = qsettings.value("agent_git_enabled", 1)
    return str(raw).strip().lower() not in ("0", "false", "")


def resolve_project_root(picked_value: str, settings) -> str:
    """Prefer the folder chosen in the GUI; fall back to the config-based root."""
    picked = (picked_value or "").strip()
    if picked and Path(picked).is_dir():
        return picked
    return str(settings.project_root())

WELCOME_HTML = (
    tr("<h3>Добро пожаловать в Fluxion</h3>"
    "<p>Спросите о коде, архитектуре или начните с примера сверху.</p>")
)

PLAN_TASK_PREFIX = (
    tr("Составь нумерованный план действий по задаче. Не изменяй файлы. "
    "Прочитай нужные файлы по одному разу, затем вызови finish: "
    "в ответе — нумерованный список шагов.")
)
PLAN_RUN_PREFIX = tr("Реализуй план:\n")
PLAN_RUN_URL = "fluxion://run-plan"
SPEAK_URL = "fluxion://speak/"  # + index of the answer in _messages
HANDS_FREE_WAIT = 8.0  # seconds to wait for speech before listening again
HANDS_FREE_SILENT_TURNS = 3  # turns without speech that switch hands-free mode off
HANDS_FREE_STOP = re.compile(
    r"^\W*(стоп|хватит|отбой|выход|выключи(?:сь)?(?:\s+голос\w*)?|закончи\w*|конец)\W*$", re.IGNORECASE
)
SPEAK_ICON = "fluxion-icon:speaker"  # images of the chat document (_refresh_voice_icons)
SPEAK_ICON_STOP = "fluxion-icon:stop"
AGENT_DEFAULT_ITERATIONS = 15
STICKY_SCROLL_SLACK = 8
ACTIVITY_QUIET_WARNING = 300
LARGE_DATASET = 20_000  # records; above it training asks to take a part
SUGGESTED_SAMPLES = 5_000
TEMP_RUN_TRAINING = (
    tr("Обучение недоступно: программа запущена прямо из архива (временная папка). "
    "Распакуйте Fluxion в обычную папку и запустите оттуда — иначе окружение "
    "обучения и результаты пропадут при закрытии архиватора.")
)
VERIFICATION_LABELS = {
    "passed": tr("тесты пройдены"),
    "failed": tr("тесты НЕ пройдены"),
    "syntax_only": tr("тестов нет — проверен только синтаксис"),
}


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


class InstallerDownloadWorker(QThread):
    """One-click update: downloads FluxionSetup.exe of the release and checks its SHA256."""

    progress = Signal(int, int)  # done_bytes, total_bytes (0 = unknown)
    failed = Signal(str)  # UpdateError.kind; "network:<message>" for a network error
    done = Signal(str)  # the checked installer

    def __init__(self, info, parent=None):
        super().__init__(parent)
        self.info = info
        self._cancel = False

    def cancel(self) -> None:
        self._cancel = True

    def run(self) -> None:
        import tempfile

        from core.update import UpdateError, download_installer

        try:
            path = download_installer(
                self.info,
                Path(tempfile.gettempdir()) / "FluxionUpdate",
                on_progress=lambda done, total: self.progress.emit(done, total),
                cancelled=lambda: self._cancel,
            )
        except UpdateError as exc:
            self.failed.emit(f"network:{exc}" if exc.kind == "network" else exc.kind)
            return
        self.done.emit(str(path))


class ModelDownloadWorker(QThread):
    """Downloads a catalog GGUF model with progress/resume (roadmap 18.2)."""

    progress = Signal(int, int)  # done_bytes, total_bytes (0 = unknown)
    failed = Signal(str)
    done = Signal(str)

    def __init__(self, model_id: str, parent=None, hf_file=None):
        super().__init__(parent)
        self.model_id = model_id
        self.hf_file = hf_file  # core.model_manager.HfFile: a file by link, not from the catalog

    def run(self) -> None:
        from core.model_manager import ModelManager

        try:
            mm = ModelManager()
            report = lambda d, t: self.progress.emit(d, t)  # noqa: E731
            if self.hf_file is not None:
                path = mm.download_hf_file(self.hf_file, on_progress=report)
            else:
                path = mm.download(self.model_id, on_progress=report)
        except Exception as exc:
            self.failed.emit(tr("Ошибка загрузки модели: {exc}", exc=exc))
            return
        self.done.emit(str(path))


class HfFilesWorker(QThread):
    """Lists the GGUF files of a Hugging Face model for "Скачать по ссылке…"."""

    done = Signal(list)
    failed = Signal(str)

    def __init__(self, repo_id: str, parent=None):
        super().__init__(parent)
        self.repo_id = repo_id

    def run(self) -> None:
        from core.model_manager import list_hf_gguf_files

        try:
            files = list_hf_gguf_files(self.repo_id)
        except Exception as exc:
            self.failed.emit(str(exc))
            return
        self.done.emit(files)


class HfDownloadDialog(QDialog):
    """Pick any GGUF file of a Hugging Face model by its link."""

    def __init__(self, parent=None, installed: set[str] | None = None):
        super().__init__(parent)
        self.setWindowTitle(tr("Скачать модель по ссылке"))
        self.setMinimumWidth(560)
        self.installed = {name.lower() for name in installed or ()}
        self.selected_file = None
        self._worker: HfFilesWorker | None = None
        self._wanted = ("", "")  # (file path, quant) named in the link

        layout = QVBoxLayout(self)
        hint = QLabel(
            tr("Вставьте ссылку на модель в формате GGUF с Hugging Face: страницу модели "
            "(например, huggingface.co/bartowski/Qwen2.5-7B-Instruct-GGUF) или ссылку на файл.")
        )
        hint.setWordWrap(True)
        layout.addWidget(hint)
        row = QHBoxLayout()
        self.link = QLineEdit()
        self.link.setPlaceholderText(tr("https://huggingface.co/автор/модель-GGUF"))
        self.link.returnPressed.connect(self.find_files)
        row.addWidget(self.link, 1)
        self.find_button = QPushButton(tr("Найти файлы"))
        self.find_button.clicked.connect(self.find_files)
        row.addWidget(self.find_button)
        layout.addLayout(row)

        self.files = QListWidget()
        self.files.setMinimumHeight(220)
        self.files.currentItemChanged.connect(self._on_current)
        self.files.itemDoubleClicked.connect(lambda _item: self._accept_selected())
        layout.addWidget(self.files, 1)
        self.status = QLabel()
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        note = QLabel(
            tr("Модели не из каталога Fluxion не проверены: чат обычно работает, а агент "
            "и вызовы инструментов могут работать хуже. Чем меньше файл, тем быстрее "
            "модель и ниже качество; Q4_K_M — хороший баланс.")
        )
        note.setObjectName("subtitle")
        note.setWordWrap(True)
        layout.addWidget(note)

        buttons = QHBoxLayout()
        buttons.addStretch()
        cancel = QPushButton(tr("Отмена"))
        cancel.clicked.connect(self.reject)
        buttons.addWidget(cancel)
        self.download_button = QPushButton(tr("Скачать"))
        self.download_button.setEnabled(False)
        self.download_button.clicked.connect(self._accept_selected)
        buttons.addWidget(self.download_button)
        layout.addLayout(buttons)

    def find_files(self) -> None:
        from core.model_manager import parse_hf_link

        if self._worker is not None:
            return
        try:
            repo_id, path, quant = parse_hf_link(self.link.text())
        except ValueError as exc:
            self.status.setText(str(exc))
            return
        self._wanted = (path, quant)
        self.files.clear()
        self.download_button.setEnabled(False)
        self.find_button.setEnabled(False)
        self.status.setText(tr("Ищем файлы модели {repo_id}…", repo_id=repo_id))
        self._worker = HfFilesWorker(repo_id, self)
        self._worker.done.connect(self.show_files)
        self._worker.failed.connect(self._on_failed)
        self._worker.finished.connect(self._on_finished)
        self._worker.start()

    def _on_finished(self) -> None:
        worker, self._worker = self._worker, None
        self.find_button.setEnabled(True)
        if worker is not None:
            worker.deleteLater()

    def _on_failed(self, message: str) -> None:
        self.status.setText(message)

    def show_files(self, files: list) -> None:
        from core.model_manager import hf_file_problem

        self.files.clear()
        path, quant = self._wanted
        pick = None
        for file in files:
            problem = hf_file_problem(file.path)
            size = tr("{value:.1f} ГБ", value=file.size_bytes / 1e9) if file.size_bytes else tr("размер неизвестен")
            text = f"{file.path} · {size}"
            if file.filename.lower() in self.installed:
                text += tr(" · уже скачана")
            if problem:
                text += f" · {problem}"
            item = QListWidgetItem(text)
            item.setData(Qt.UserRole, file)
            if problem:
                item.setFlags(item.flags() & ~Qt.ItemIsEnabled & ~Qt.ItemIsSelectable)
            self.files.addItem(item)
            if problem or pick is not None:
                continue
            if (path and file.path == path) or (quant and quant.lower() in file.filename.lower()):
                pick = item
        usable = [self.files.item(i) for i in range(self.files.count())
                  if self.files.item(i).flags() & Qt.ItemIsEnabled]
        if not files:
            self.status.setText(tr("В этой модели нет файлов GGUF. Ищите репозиторий с «GGUF» в названии."))
        elif not usable:
            self.status.setText(tr("Подходящих файлов нет."))
        else:
            self.status.setText(tr("Файлов GGUF: {n_usable}. Выберите один и нажмите «Скачать».", n_usable=len(usable)))
        if pick is None:
            pick = next((i for i in usable if "q4_k_m" in i.text().lower()), None)
        if pick is not None:
            self.files.setCurrentItem(pick)

    def _on_current(self, item, _previous=None) -> None:
        self.download_button.setEnabled(item is not None and bool(item.flags() & Qt.ItemIsEnabled))

    def _accept_selected(self) -> None:
        item = self.files.currentItem()
        if item is None or not item.flags() & Qt.ItemIsEnabled:
            return
        self.selected_file = item.data(Qt.UserRole)
        self.accept()

    def done(self, result: int) -> None:  # noqa: A003 — QDialog.done
        if self._worker is not None:
            self._worker.wait(3000)
        super().done(result)


class SpeechModelDownloadWorker(QThread):
    """Downloads the files of a speech recognition model (voice.models)."""

    progress = Signal(int, int)
    failed = Signal(str)
    done = Signal(str)

    def __init__(self, model_id: str, parent=None):
        super().__init__(parent)
        self.model_id = model_id

    def run(self) -> None:
        from voice import models

        try:
            path = models.download(self.model_id, on_progress=lambda d, t: self.progress.emit(d, t))
        except Exception as exc:
            self.failed.emit(tr("Ошибка загрузки модели распознавания речи: {exc}", exc=exc))
            return
        self.done.emit(str(path))


class VoiceInputWorker(QThread):
    """Voice input: records the microphone until stop() or a pause in speech,
    then recognises it with faster-whisper. *audio* skips the microphone (tests)."""

    level = Signal(float)
    recognizing = Signal()
    done = Signal(str)
    failed = Signal(str)

    def __init__(self, model_dir: str, parent=None, audio=None, wait_seconds: float | None = None):
        super().__init__(parent)
        from voice.stt import Recorder

        self.model_dir = model_dir
        self.audio = audio
        self.recorder = Recorder(on_level=self.level.emit, wait_seconds=wait_seconds)

    def stop(self) -> None:
        self.recorder.stop()

    def run(self) -> None:
        from voice import stt

        try:
            audio = self.audio if self.audio is not None else self.recorder.record()
            self.recognizing.emit()
            text = stt.transcribe(audio, self.model_dir)
        except Exception as exc:
            self.failed.emit(str(exc))
            return
        self.done.emit(text)


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
                self.done.emit(tr("Импортирована модель: {filename}", filename=item.filename))
                return
            imported = mm.import_from_ollama()
            if imported:
                names = ", ".join(i.filename for i in imported)
                self.done.emit(tr("Импортировано из Ollama: {names}", names=names))
            else:
                self.done.emit(tr("В хранилище Ollama GGUF-модели не найдены."))
        except Exception as exc:
            self.failed.emit(tr("Ошибка импорта: {exc}", exc=exc))


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
        self.style = ""  # «Мой стиль»: the user's manner of writing for the system prompt
        self.source = None  # «/архив»: the design archive instead of the project index (rag/archive.py)
        self._stop = False

    def stop(self) -> None:
        self._stop = True

    def run(self) -> None:
        query = self.query
        for attempt in (1, 2):
            try:
                extra = {"style": self.style} if self.style else {}
                if self.source is not None:
                    extra["source"] = self.source
                result, stream = self.assistant.ask_stream(query=query, history=self.history, **extra)
            except Exception as exc:
                self.failed.emit(tr("Ошибка генерации: {exc}", exc=exc))
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
                self.failed.emit(tr("Ошибка генерации: {exc}", exc=exc))
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


CAD_PREFIXES = ("/компас", "/kompas")
CAD_HINT = tr("Например: втулка 30 на 20 длиной 50, фаска 1, бронза")


def cad_command(text: str) -> str | None:
    """The part description of a "/компас ..." chat message; None for other messages."""
    lowered = text.lower()
    for prefix in CAD_PREFIXES:
        rest = lowered[len(prefix):]
        if lowered.startswith(prefix) and (not rest or rest[0].isspace()):
            return text[len(prefix):].strip()
    return None


ACAD_PREFIXES = ("/автокад", "/autocad")
ARCHIVE_PREFIXES = ("/архив", "/archive")


def archive_command(text: str) -> str | None:
    """The question of an "/архив …" chat message ("" — open the archive window); None for other messages."""
    lowered = text.lower()
    for prefix in ARCHIVE_PREFIXES:
        rest = lowered[len(prefix):]
        if lowered.startswith(prefix) and (not rest or rest[0].isspace()):
            return text[len(prefix):].strip()
    return None


CALC_PREFIXES = ("/расчёт", "/расчет", "/calc")


def calc_command(text: str) -> str | None:
    """The question of a "/расчёт …" chat message ("" — open the calculator); None for other messages."""
    lowered = text.lower()
    for prefix in CALC_PREFIXES:
        rest = lowered[len(prefix):]
        if lowered.startswith(prefix) and (not rest or rest[0].isspace()):
            return text[len(prefix):].strip()
    return None


def acad_command(text: str) -> str | None:
    """The drawing of an "/автокад путь.dwg" chat message ("" — choose it); None for other messages."""
    lowered = text.lower()
    for prefix in ACAD_PREFIXES:
        rest = lowered[len(prefix):]
        if lowered.startswith(prefix) and (not rest or rest[0].isspace()):
            return text[len(prefix):].strip().strip('"')
    return None


_SPOKEN_CAD_RE = re.compile(r"^\W*(?:в\s+)?(?:компас[еау]?|kompas)\b[\s,.:;!—-]*(.+)$", re.IGNORECASE | re.DOTALL)


_SPOKEN_CALC_RE = re.compile(
    r"^\W*(?:расч[её]т[\s,.:;!—-]+|(?:посчитай|рассчитай|проверь)\s+(?=(?:сварн\w*\s+)?(?:шов|швы|болт|балк|"
    r"лонжерон|раму|гибк|развертк|развёртк|вал|ось|шпонк)))(.+)$", re.IGNORECASE | re.DOTALL)


def spoken_command(text: str) -> str:
    """A slash cannot be said: "Компас, втулка 30 на 20" -> "/компас втулка 30 на 20",
    "Расчёт, шов катет 6…" or "посчитай балку…" -> "/расчёт …"."""
    match = _SPOKEN_CAD_RE.match(text)
    if match:
        return f"{CAD_PREFIXES[0]} {match.group(1).strip()}"
    match = _SPOKEN_CALC_RE.match(text)
    return f"{CALC_PREFIXES[0]} {match.group(1).strip()}" if match else text


class CadWorker(QThread):
    """One step of a part conversation (package cad) in a background thread:
    the chat model reads the description (and answers), Fluxion asks for the
    missing main sizes and material, or KOMPAS builds and saves the part."""

    progress = Signal(str)
    asked = Signal(str)
    done = Signal(str)
    failed = Signal(str)

    def __init__(self, conversation, message: str, parent=None):
        super().__init__(parent)
        self.conversation = conversation
        self.message = message

    def run(self) -> None:
        from cad import kompas, spec

        try:
            reply = self.conversation.step(self.message)
        except spec.SpecError as exc:
            self.failed.emit(tr("Не получилось разобрать описание: {exc}", exc=exc))
            return
        except Exception as exc:
            self.failed.emit(tr("Модель не ответила: {exc}", exc=exc))
            return
        if reply.kind == "ask":
            self.asked.emit(reply.text)
            return
        if reply.kind != "build":
            self.done.emit(reply.text)
            return
        part = reply.part
        self.progress.emit(reply.text)
        self.progress.emit(tr("Строю в КОМПАС-3D…"))
        try:
            result = kompas.build_part(part, kompas.part_path(part["name"]), log=lambda line: None)
        except kompas.KompasError as exc:
            self.failed.emit(str(exc))
            return
        except Exception as exc:  # COM errors carry the KOMPAS message
            reason = getattr(exc, "strerror", None) or str(exc)
            self.failed.emit(tr("КОМПАС-3D не смог построить деталь: {reason}", reason=reason))
            return
        from cad.text2cad import result_text

        self.done.emit(result_text(part, result))


class CalcWorker(QThread):
    """One step of a «/расчёт» conversation (package calc) in a background thread: the chat
    model reads the question, Fluxion asks for what is missing or the formulas count and
    the model puts the verdict into words."""

    asked = Signal(str)
    done = Signal(str, str, dict)   # text, kind of the calculation, its raw values
    failed = Signal(str)

    def __init__(self, conversation, message: str, parent=None):
        super().__init__(parent)
        self.conversation = conversation
        self.message = message

    def run(self) -> None:
        from calc.chat import explain
        from calc.core import CalcError

        try:
            reply = self.conversation.step(self.message)
        except CalcError as exc:
            self.failed.emit(tr("Не получилось разобрать вопрос: {exc}", exc=exc))
            return
        except Exception as exc:
            self.failed.emit(tr("Модель не ответила: {exc}", exc=exc))
            return
        if reply.kind == "ask":
            self.asked.emit(reply.text)
            return
        if reply.kind != "result":
            self.done.emit(reply.text, reply.calc if reply.kind == "error" else "", reply.params or {})
            return
        text = reply.text
        comment = explain(reply.result, self.conversation.backend)
        if comment:
            text += tr("\n\nПояснение (модель): ") + comment
        self.done.emit(text, reply.calc, reply.params or {})


class AssemblyReportWorker(QThread):
    """Summary statement and metal report of a KOMPAS-3D assembly (cad/report.py):
    the assembly is opened read-only, the report is saved as an Excel workbook."""

    progress = Signal(str)
    ready = Signal(object)  # cad.report.Report: the window shows the warnings, then saves it
    failed = Signal(str)

    def __init__(self, path: str, parent=None, fixes=None):
        super().__init__(parent)
        self.path = path
        self.fixes = fixes or []  # cad.fix.SectionFix the designer agreed to: made first

    def run(self) -> None:
        from cad import assembly, report

        try:
            import pythoncom

            pythoncom.CoInitialize()  # COM in a background thread
        except ImportError:  # not Windows: assembly.read explains that pywin32 is missing
            pythoncom = None
        try:
            if self.fixes:
                self._fix()
            top = assembly.read(self.path, log=self.progress.emit)
            self.progress.emit(tr("Считаю ведомость и металлоёмкость…"))
            result = report.build(top, report.load_decisions(top.file))
            report.add_welds(result, log=self.progress.emit)
        except assembly.AssemblyError as exc:
            self.failed.emit(str(exc))
            return
        except Exception as exc:  # COM errors carry the KOMPAS message
            self.failed.emit(tr("Не получилось составить отчёт: {exc}", exc=exc))
            return
        finally:
            if pythoncom is not None:
                pythoncom.CoUninitialize()
        self.ready.emit(result)

    def _fix(self) -> None:
        """Backups first, then the fixes; the report is read again from the fixed model."""
        from cad import fix

        files = list(dict.fromkeys([self.path] + [f.file or self.path for f in self.fixes]))
        stamp = datetime.now().strftime("%Y-%m-%d %H-%M-%S")
        for file in files:
            self.progress.emit(tr("Резервная копия: {value}", value=fix.backup(file, stamp)))
        sections = [f for f in self.fixes if isinstance(f, fix.SectionFix)]
        if sections:
            for line in fix.apply_section_fixes(self.path, sections, log=lambda _line: None):
                self.progress.emit(line)
        materials: dict[tuple, list] = {}
        for f in self.fixes:
            if isinstance(f, fix.MaterialFix):
                materials.setdefault((f.material, f.density), []).append(f)
        for (material, density), group in materials.items():
            for line in fix.apply_material_fixes(self.path, group, material, density, log=lambda _line: None):
                self.progress.emit(line)


class ReportWarningsDialog(QDialog):
    """The disputed places of an assembly report, one row each: the designer marks
    every one ("Проверено"; for a bought unit also "Покупное") and only then the
    document can be made."""

    def __init__(self, result, parent=None):
        super().__init__(parent)
        from PySide6.QtWidgets import QAbstractItemView, QHeaderView, QTableWidget, QTableWidgetItem

        self.result = result
        self.setWindowTitle(tr("Перед оформлением отчёта"))
        self.resize(1100, 560)
        box = QVBoxLayout(self)
        intro = QLabel(
            tr("<b>{value}</b>: спорных мест — {n_remarks}. "
            "Отметьте каждое: отчёт формируется, только когда отмечено всё. "
            "У покупных снимите «Покупное», если это ваше изготовление. Отметки запоминаются для этого изделия. "
            "Двойной щелчок по строке открывает модель в КОМПАС, по пути к файлу — его папку.", value=html.escape(result.top.title), n_remarks=len(result.remarks))
        )
        intro.setWordWrap(True)
        intro.setTextFormat(Qt.RichText)
        box.addWidget(intro)
        headers = [tr("Проверено"), tr("Покупное"), tr("Уровень"), tr("Замечание"), tr("Обозначение и наименование"), tr("Где"),
                   tr("Что будет и что сделать")]
        self.table = QTableWidget(len(result.remarks), len(headers))
        self.table.setHorizontalHeaderLabels(headers)
        self.table.verticalHeader().setVisible(False)
        self.table.setSelectionMode(QAbstractItemView.NoSelection)
        self.table.setWordWrap(True)
        for row, r in enumerate(result.remarks):
            check = QTableWidgetItem()
            check.setFlags(Qt.ItemIsUserCheckable | Qt.ItemIsEnabled)
            check.setCheckState(Qt.Checked if r["checked"] else Qt.Unchecked)
            self.table.setItem(row, 0, check)
            bought = QTableWidgetItem()
            if r["purchased"] is not None:
                bought.setFlags(Qt.ItemIsUserCheckable | Qt.ItemIsEnabled)
                bought.setCheckState(Qt.Checked if r["purchased"] else Qt.Unchecked)
            else:
                bought.setFlags(Qt.ItemIsEnabled)
            self.table.setItem(row, 1, bought)
            for col, text in enumerate((r["level"], r["problem"], r["title"], r["where"], r["message"]), 2):
                item = QTableWidgetItem(text)
                item.setFlags(Qt.ItemIsEnabled)
                if (col == 4 or col == 6 and self._borrowed(r)) and r.get("file"):  # links: the model, its folder
                    from cad.report import model_file

                    item.setToolTip(tr("Открыть: {value}", value=model_file(r['file'])) if col == 4
                                    else tr("Показать в папке: {value}", value=model_file(r['file'])))
                    item.setForeground(QColor("#0563C1"))
                    font = item.font()
                    font.setUnderline(True)
                    item.setFont(font)
                self.table.setItem(row, col, item)
        header = self.table.horizontalHeader()
        for col in (0, 1, 2):
            header.setSectionResizeMode(col, QHeaderView.ResizeToContents)
        for col, width in ((3, 200), (4, 250), (5, 190), (6, 300)):
            self.table.setColumnWidth(col, width)
        header.setStretchLastSection(True)
        self.table.resizeRowsToContents()  # after the widths: rows as high as their text
        self.table.itemChanged.connect(self._update)
        self.table.cellDoubleClicked.connect(self.open_model)
        box.addWidget(self.table, 1)
        buttons = QHBoxLayout()
        from .manual import help_button

        buttons.addWidget(help_button(self, "otchet-po-sborke"))
        self.counter = QLabel()
        buttons.addWidget(self.counter, 1)
        from cad.fix import material_fixes, section_fixes

        self.fixes = section_fixes(result.top)
        self.material_fixes = material_fixes(result.top)
        self.fix_requested = False
        if self.fixes:
            fix_button = QPushButton(tr("Исправить в модели ({n_fixes})…", n_fixes=len(self.fixes)))
            fix_button.setToolTip(tr("Перенести покупные в «Прочие изделия» спецификации их сборок"))
            fix_button.clicked.connect(self._ask_fix)
            buttons.addWidget(fix_button)
        if self.material_fixes:
            material_button = QPushButton(tr("Задать материал ({n_material_fixes})…", n_material_fixes=len(self.material_fixes)))
            material_button.setToolTip(tr("Выбрать материал в справочнике «Материалы» КОМПАС-3D для деталей без материала"))
            material_button.clicked.connect(self._ask_material)
            buttons.addWidget(material_button)
        cancel = QPushButton(tr("Отмена — исправлю модель"))
        cancel.clicked.connect(self.reject)
        buttons.addWidget(cancel)
        self.make_button = QPushButton(tr("Сформировать отчёт"))
        self.make_button.setObjectName("sendButton")
        self.make_button.clicked.connect(self.accept)
        buttons.addWidget(self.make_button)
        box.addLayout(buttons)
        self._update()

    def fix_text(self) -> str:
        """What will change, for the designer's consent."""
        from cad.fix import OTHER_SECTION

        files = list(dict.fromkeys(f.file or self.result.top.file for f in self.fixes))
        lines = [f"• {f.text}" for f in self.fixes[:25]]
        if len(self.fixes) > 25:
            lines.append(tr("…и ещё {value}", value=len(self.fixes) - 25))
        return (tr("Fluxion перенесёт покупные изделия в раздел «") + OTHER_SECTION + tr("» спецификации их сборок: "
                "изменит раздел объекта спецификации, а где его нет — создаст (сборке без спецификации — "
                "и саму спецификацию). Файлы самих изделий "
                "(и библиотеки покупных) не меняются.\n\n") + "\n".join(lines)
                + tr("\n\nИзменятся файлы (перед этим — резервная копия в папке «Резервные копии Fluxion» рядом):\n")
                + "\n".join(files)
                + tr("\n\nСборка должна быть закрыта в КОМПАС-3D. После исправления отчёт составится заново."))

    def _ask_fix(self) -> None:
        marks = self.marks()
        made_here = {r["title"] for r in self.result.remarks  # "Покупное" taken off: not moved
                     if r["purchased"] is not None and not marks[r["id"]].get("purchased", True)}
        self.fixes = [f for f in self.fixes if f.component.title not in made_here]
        if not self.fixes:
            QMessageBox.information(self, tr("Исправить в модели"), tr("Исправлять нечего: всё отмечено как своё изготовление."))
            return
        answer = QMessageBox.question(self, tr("Исправить в модели"), self.fix_text(),
                                      QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
        if answer == QMessageBox.Yes:
            self.fix_requested = True
            self.done(QDialog.Rejected)

    def _ask_material(self) -> None:
        dialog = MaterialFixDialog(self.material_fixes, self.result.top.file, self)
        if dialog.exec() != QDialog.Accepted:
            return
        self.fixes = dialog.chosen()
        self.fix_requested = True
        self.done(QDialog.Rejected)

    @staticmethod
    def _borrowed(r: dict) -> bool:
        """A model from another project or a shared library: the message has its path."""
        from cad.report import OTHER_PROJECT, SHARED_LIBRARY

        return r.get("problem") in (OTHER_PROJECT, SHARED_LIBRARY)

    def open_model(self, row: int, col: int = 0) -> str:
        """The model of the row in KOMPAS (a local part: its assembly); for a borrowed
        model a double click on its path shows the file in its folder."""
        from cad.report import model_file

        r = self.result.remarks[row]
        file = model_file(r.get("file", ""))
        if not file:
            return file
        if col == 6 and self._borrowed(r):
            if sys.platform == "win32" and Path(file).exists():
                import subprocess

                subprocess.Popen(["explorer", "/select,", str(Path(file))])
            else:
                import ntpath

                QDesktopServices.openUrl(QUrl.fromLocalFile(ntpath.dirname(file)))
        else:
            QDesktopServices.openUrl(QUrl.fromLocalFile(file))
        return file

    def marks(self) -> dict:
        out = {}
        for row, r in enumerate(self.result.remarks):
            mark = {"checked": self.table.item(row, 0).checkState() == Qt.Checked}
            if r["purchased"] is not None:
                mark["purchased"] = self.table.item(row, 1).checkState() == Qt.Checked
            out[r["id"]] = mark
        return out

    def _update(self, *_args) -> None:
        marks = self.marks()
        done = sum(1 for m in marks.values() if m["checked"])
        total = len(marks)
        self.counter.setText(tr("Отмечено {done} из {total}", done=done, total=total))
        self.make_button.setEnabled(done == total)  # blocked until every place is marked


class MaterialFixDialog(QDialog):
    """Details without a material: the designer ticks them, chooses the material in the
    «Материалы» library of KOMPAS-3D and agrees to the change of the files listed."""

    def __init__(self, fixes, product_file: str, parent=None):
        super().__init__(parent)
        self.fixes, self.product_file = fixes, product_file
        self.material: tuple[str, float] | None = None
        self.setWindowTitle(tr("Задать материал"))
        self.resize(820, 520)
        box = QVBoxLayout(self)
        intro = QLabel(tr("Отметьте детали и выберите материал в справочнике «Материалы» КОМПАС-3D. "
                       "Материал записывается в файл детали (у локальной — в файл сборки); перед этим "
                       "Fluxion сделает резервную копию каждого файла."))
        intro.setWordWrap(True)
        box.addWidget(intro)
        self.list = QListWidget()
        for f in fixes:
            item = QListWidgetItem(f"{f.text} — {f.file or product_file}")
            item.setFlags(Qt.ItemIsUserCheckable | Qt.ItemIsEnabled)
            item.setCheckState(Qt.Checked)
            self.list.addItem(item)
        self.list.itemChanged.connect(self._update)
        box.addWidget(self.list, 1)
        row = QHBoxLayout()
        self.material_label = QLabel(tr("Материал не выбран"))
        self.material_label.setWordWrap(True)
        row.addWidget(self.material_label, 1)
        choose = QPushButton(tr("Выбрать в справочнике КОМПАС…"))
        choose.clicked.connect(self._choose)
        row.addWidget(choose)
        box.addLayout(row)
        buttons = QHBoxLayout()
        from .manual import help_button

        buttons.addWidget(help_button(self, "ispravit-v-modeli"))
        buttons.addStretch(1)
        cancel = QPushButton(tr("Отмена"))
        cancel.clicked.connect(self.reject)
        buttons.addWidget(cancel)
        self.apply_button = QPushButton(tr("Задать материал"))
        self.apply_button.setObjectName("sendButton")
        self.apply_button.clicked.connect(self._confirm)
        buttons.addWidget(self.apply_button)
        box.addLayout(buttons)
        self._update()

    def checked(self) -> list:
        return [f for i, f in enumerate(self.fixes) if self.list.item(i).checkState() == Qt.Checked]

    def chosen(self) -> list:
        material, density = self.material
        for f in self.checked():
            f.material, f.density = material, density
        return self.checked()

    def set_material(self, material: str, density: float) -> None:
        from cad.assembly import material_text

        self.material = (material, density)
        self.material_label.setText(tr("Материал: <b>{value}</b>, "
                                    "плотность {density:g} г/см³", value=html.escape(material_text(material)), density=density))
        self._update()

    def _choose(self) -> None:
        from cad.fix import choose_kompas_material

        try:
            got = choose_kompas_material(int(self.winId()))
        except Exception as exc:  # no KOMPAS or no library
            QMessageBox.warning(self, tr("Справочник «Материалы»"), str(exc))
            return
        if got:
            self.set_material(*got)

    def confirm_text(self) -> str:
        from cad.assembly import material_text

        fixes = self.checked()
        files = list(dict.fromkeys(f.file or self.product_file for f in fixes))
        product_dir = str(Path(self.product_file).parent).lower() if self.product_file else ""
        shared = [f for f in files if product_dir and not f.lower().startswith(product_dir)]
        lines = [f"• {f.text}" for f in fixes[:25]] + ([tr("…и ещё {value}", value=len(fixes) - 25)] if len(fixes) > 25 else [])
        text = (tr("Задать материал «{value}» ({value2:g} г/см³):\n\n", value=material_text(self.material[0]), value2=self.material[1])
                + "\n".join(lines) + tr("\n\nИзменятся файлы (перед этим — резервная копия в папке "
                "«Резервные копии Fluxion» рядом):\n") + "\n".join(files))
        if shared:
            text += (tr("\n\nВнимание: эти файлы лежат вне папки изделия — возможно, общие для других "
                     "изделий, материал поменяется и там:\n") + "\n".join(shared))
        return text + tr("\n\nСборка и детали должны быть закрыты в КОМПАС-3D. После этого отчёт составится заново.")

    def _confirm(self) -> None:
        answer = QMessageBox.question(self, tr("Задать материал"), self.confirm_text(),
                                      QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
        if answer == QMessageBox.Yes:
            self.accept()

    def _update(self, *_args) -> None:
        self.apply_button.setEnabled(self.material is not None and bool(self.checked()))


class MaterialsDialog(QDialog):
    """The materials catalog of "Деталь в КОМПАС": the shipped one plus the user's
    %APPDATA%\\Fluxion\\materials.json; open that file, import CSV/Excel, reload."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle(tr("Каталог материалов"))
        self.resize(720, 520)
        layout = QVBoxLayout(self)
        self.info = QLabel()
        self.info.setWordWrap(True)
        layout.addWidget(self.info)
        self.list = QListWidget()
        layout.addWidget(self.list, 1)
        buttons = QHBoxLayout()
        from .manual import help_button

        buttons.addWidget(help_button(self, "katalog-materialov"))
        for title, slot in ((tr("Открыть файл каталога"), self._open_file),
                            (tr("Импорт из Excel/CSV…"), self._import),
                            (tr("Обновить"), self.refresh)):
            button = QPushButton(title)
            button.clicked.connect(slot)
            buttons.addWidget(button)
        buttons.addStretch(1)
        close = QPushButton(tr("Закрыть"))
        close.clicked.connect(self.accept)
        buttons.addWidget(close)
        layout.addLayout(buttons)
        self.refresh()

    def refresh(self) -> None:
        from cad import materials

        catalog, errors = materials.load_with_errors(reload=True)
        self.list.clear()
        kinds = {"solid": tr("сплошной"), "sheet": tr("лист"), "checker": tr("рифлёный лист")}
        for m in catalog:
            line = tr("{short} — {value}, {density:g} г/см³", short=m.short, value=kinds.get(m.kind, m.kind), density=m.density)
            if m.thicknesses:
                line += tr(", толщины: ") + " ".join(f"{t:g}" for t in m.thicknesses)
            if m.source == "user":
                line += tr("  [свой]")
            item = QListWidgetItem(line)
            item.setToolTip(tr("{name}\nсинонимы: {value}", name=m.name, value=', '.join(m.synonyms)) + (f"\n{m.note}" if m.note else ""))
            self.list.addItem(item)
        text = (tr("Материалов: {n_catalog}. Свои материалы — в файле {value} "
                "(запись с тем же названием заменяет встроенную).", n_catalog=len(catalog), value=materials.user_catalog_path()))
        if errors:
            text += tr("\nНе прочитано: ") + "; ".join(errors)
        self.info.setText(text)

    def _open_file(self) -> None:
        from cad import materials

        path = materials.ensure_user_catalog()
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(path)))

    def _import(self) -> None:
        from PySide6.QtWidgets import QFileDialog

        from cad import materials

        path, _ = QFileDialog.getOpenFileName(
            self, tr("Импорт материалов"), str(Path.home()), tr("Таблицы (*.xlsx *.csv);;Все файлы (*)")
        )
        if not path:
            return
        result = materials.import_table(Path(path))
        lines = [tr("Добавлено: {n_added}, заменено: {n_replaced}.", n_added=len(result.added), n_replaced=len(result.replaced))]
        if result.path:
            lines.append(tr("Каталог: {path}", path=result.path))
        if result.errors:
            lines.append(tr("Пропущены строки:\n") + "\n".join(result.errors[:30]))
        QMessageBox.information(self, tr("Импорт материалов"), "\n".join(lines))
        self.refresh()


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

        from core.proc import no_window

        return subprocess.run(
            ["git", *args],
            cwd=self.root,
            capture_output=True,
            text=True,
            timeout=90,
            **no_window(),
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
        self.done.emit(True, tr("Репозиторий инициализирован: {root}", root=self.root))


class HealthDialog(QDialog):
    """Dependency dashboard: state, reason, remedy, fix (Phase 14.1)."""

    def __init__(self, window, parent=None):
        super().__init__(parent or window)
        self.window = window
        self.setWindowTitle(tr("Fluxion — состояние"))
        self.setMinimumWidth(600)
        self._fix_workers: list[QThread] = []

        outer = QVBoxLayout(self)
        outer.setContentsMargins(20, 16, 20, 16)
        outer.setSpacing(10)

        self.rows_box = QVBoxLayout()
        outer.addLayout(self.rows_box)

        buttons = QHBoxLayout()
        recheck = QPushButton(tr("Перепроверить"))
        recheck.setObjectName("themeButton")
        recheck.clicked.connect(self._run_checks)
        buttons.addWidget(recheck)
        wizard_btn = QPushButton(tr("Мастер настройки…"))
        wizard_btn.setObjectName("themeButton")
        wizard_btn.clicked.connect(self._open_wizard)
        buttons.addWidget(wizard_btn)
        buttons.addStretch()
        close_btn = QPushButton(tr("Закрыть"))
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
        info = QLabel(tr("Проверяем зависимости…"))
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
            fix_button = QPushButton(tr("Исправить"))
            fix_button.setObjectName("themeButton")
            fix_button.clicked.connect(lambda checked=False, b=fix_button: self._fix_searxng(b))
            layout.addWidget(fix_button)
        elif not status.available and status.fix == "docker":
            fix_button = QPushButton(tr("Исправить"))
            fix_button.setObjectName("themeButton")
            fix_button.clicked.connect(lambda checked=False, b=fix_button: self._fix_docker(b))
            layout.addWidget(fix_button)
        return row

    def _fix_searxng(self, button: QPushButton) -> None:
        # Pressing "fix" is an explicit choice of extended search: remember it.
        action = getattr(self.window, "act_searxng", None)
        if action is not None and not action.isChecked():
            action.blockSignals(True)
            action.setChecked(True)
            action.blockSignals(False)
        self.window._apply_searxng_choice(True)
        client = getattr(self.window.web_search, "client", None)
        base_url = getattr(client, "base_url", "http://localhost:8080")
        button.setEnabled(False)
        button.setText(tr("Запуск…"))
        worker = SearxFixWorker(base_url)
        worker.done.connect(lambda ok, b=button: self._on_fix_done(ok, b))
        self._fix_workers.append(worker)
        worker.start()

    def _on_fix_done(self, ok: bool, button: QPushButton) -> None:
        button.setText(tr("Исправить") if not ok else tr("Готово"))
        if ok:
            self._run_checks()
        else:
            button.setEnabled(True)
            button.setToolTip(tr("Не удалось запустить контейнер — см. подсказку выше"))

    def _fix_docker(self, button: QPushButton) -> None:
        client = getattr(self.window.web_search, "client", None)
        base_url = getattr(client, "base_url", "")
        button.setEnabled(False)
        button.setText(tr("Запуск…"))
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
            self.failed.emit(tr("Ошибка запуска агента: {exc}", exc=exc))
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
            self.failed.emit(tr("Ошибка агента: {exc}", exc=exc))
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
            self.failed.emit(tr("Ошибка индексации: {exc}", exc=exc))
            return
        self.done.emit(int(count))


# Pages of the main window (QStackedWidget indexes)
PAGE_CHAT, PAGE_PROJECT, PAGE_TRAINING, PAGE_MODELS = range(4)


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
            self.failed.emit(tr("Смена моделей доступна только для Ollama-движка"))
            return
        try:
            models = list(client.list_models())
        except Exception as exc:
            self.failed.emit(tr("Не удалось получить список моделей: {exc}", exc=exc))
            return
        self.done.emit(models)


# Filters of the "Модели" page: (label, kind).  A kind is a catalog tag, or
# installed / rag (embeddings) / free / pro.
MODEL_KINDS = (
    (tr("Все модели"), ""),
    (tr("Для кода"), "code"),
    (tr("Для текстов и документов"), "text"),
    (tr("Для слабых компьютеров"), "small"),
    (tr("Поиск по проекту (эмбеддинги)"), "rag"),
    (tr("Установленные"), "installed"),
    (tr("Бесплатные"), "free"),
    ("Pro", "pro"),
)
MODEL_VRAM_LIMITS = (
    (tr("Любая видеокарта"), 0.0),
    (tr("До 4 ГБ видеопамяти"), 4.0),
    (tr("До 8 ГБ"), 8.0),
    (tr("До 12 ГБ"), 12.0),
    (tr("До 16 ГБ"), 16.0),
    (tr("До 24 ГБ"), 24.0),
)


class CatalogWorker(QThread):
    """Fetches the website model catalog and the video card memory off the UI thread."""

    done = Signal(int, str, float)

    def __init__(self, manager, parent=None):
        super().__init__(parent)
        self.manager = manager

    def run(self) -> None:
        from core.model_manager import catalog_url, detect_gpu_memory_gb

        try:
            gpu_gb = float(detect_gpu_memory_gb())
        except Exception:
            gpu_gb = 0.0
        if not catalog_url():
            self.done.emit(0, "", gpu_gb)
            return
        try:
            count = self.manager.fetch_remote_catalog()
        except Exception as exc:  # offline is normal: the bundled catalog stays
            self.done.emit(0, str(exc), gpu_gb)
            return
        self.done.emit(count, "", gpu_gb)


class CloudApiWorker(QThread):
    """Checks the cloud API or lists its models without blocking the UI thread."""

    done = Signal(bool, str, list)

    def __init__(self, cfg, action: str, parent=None):
        super().__init__(parent)
        self.cfg = cfg
        self.action = action

    def run(self) -> None:
        from core.cloud_api import check_connection, list_models

        if self.action == "models":
            try:
                models = list_models(self.cfg)
            except Exception as exc:
                self.done.emit(False, str(exc), [])
                return
            self.done.emit(True, tr("Моделей у провайдера: {n_models}.", n_models=len(models)), models)
            return
        try:
            ok, message = check_connection(self.cfg)
        except Exception as exc:
            ok, message = False, str(exc)
        self.done.emit(ok, message, [])


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
            self.failed.emit(tr("Обучение прервано (exit {code})", code=exc.code))
            return
        except Exception as exc:
            self.failed.emit(tr("Ошибка обучения: {exc}", exc=exc))
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
            self.failed.emit(tr("Ошибка установки окружения: {exc}", exc=exc))
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
        self.cad_worker: CadWorker | None = None
        self.voice_worker: VoiceInputWorker | None = None
        self._speech_download_worker: SpeechModelDownloadWorker | None = None
        self._speaker = None  # voice.tts.Speaker, created on first use
        self._speaking_index = -1  # answer being read aloud
        self._hands_free = False  # hands-free voice mode: listen, send, read aloud, listen again
        self._hands_free_silent = 0
        self._hands_free_timer = QTimer(self)
        self._hands_free_timer.setInterval(400)
        self._hands_free_timer.timeout.connect(self._on_hands_free_tick)
        self._speech_timer = QTimer(self)
        self._speech_timer.setInterval(500)
        self._speech_timer.timeout.connect(self._on_speech_tick)
        self._cad_conversation = None  # cad.text2cad.Conversation waiting for an answer
        self._calc_conversation = None  # calc.chat.Conversation waiting for an answer
        self._last_calc: tuple[str, dict] | None = None  # the last chat calculation, for the calculator window
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
        self._last_plan = ""
        self._plan_entry: dict | None = None
        self._plan_pending = False
        self._stop_requested = False
        self._web_opened_for_current = False
        web_cfg = getattr(settings, "web", None) if settings is not None else None
        # the address bar searches through SearXNG only when extended search is on:
        # by default nothing is asked of localhost:8080 (DuckDuckGo instead)
        self._searxng_url = str(getattr(web_cfg, "searxng_url", "") or "")
        self._search_base = self._searxng_url if self._searxng_on() else ""
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
        saved_gguf = str(self.qsettings.value("gguf_path", "") or "")
        if saved_gguf and self.settings is not None and Path(saved_gguf).is_file():
            # the chat model chosen in "⚙ Настройки чата" for the embedded engine
            self._set_gguf_path(saved_gguf)
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
            selector.addItem(tr("Текущий чат"), None)
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
            self._append_banner(tr("Экспорт: чат пуст — нечего сохранять."))
            return
        session = {
            "id": self._session_id or "",
            "title": session_title(self.history),
            "updated": time.time(),
            "messages": list(self.history),
        }
        default = safe_filename(session["title"])
        path, _ = QFileDialog.getSaveFileName(
            self, tr("Экспорт чата"), default, "Markdown (*.md)"
        )
        if not path:
            return
        try:
            Path(path).write_text(session_to_markdown(session), encoding="utf-8")
        except OSError as exc:
            self._append_banner(tr("Экспорт не удался: {exc}", exc=exc))
            return
        self._append_banner(tr("Чат экспортирован: {path}", path=path))

    def _delete_current_chat(self) -> None:
        if self._session_id is None:
            return
        answer = QMessageBox.question(
            self,
            tr("Удалить чат"),
            tr("Удалить текущий чат из истории?"),
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

    def _project_forgotten(self) -> bool:
        return str(self.qsettings.value("project_forgotten", False)).lower() in ("1", "true")

    def _set_project_forgotten(self, forgotten: bool) -> None:
        self.qsettings.setValue("project_forgotten", bool(forgotten))
        if self.assistant is not None:
            self.assistant.use_project = not forgotten
        self._refresh_index_chip()

    def forget_project(self) -> None:
        """Chat menu and «Проект» → «Забыть проект»: a new chat that refers to nothing.

        The conversation starts over, the project folder is unselected and its
        index is not searched for answers, «Мой стиль» is off.  Nothing is deleted
        on disk: picking the folder again (or indexing) brings the project back."""
        if self.generating or self.agent_running:
            return
        self.new_chat()
        self.qsettings.setValue("project_path", "")
        if hasattr(self, "project_path"):
            self.project_path.setText("")
        if hasattr(self, "chat_style") and self.chat_style.isChecked():
            self.chat_style.setChecked(False)
        self._set_project_forgotten(True)
        self._refresh_git_button()
        self._append_message("assistant", tr(
            "Проект забыт. Новый чат: ответы — без файлов проекта и прошлых сообщений. Индекс на диске "
            "сохранён — чтобы вернуть проект, выберите папку: меню «Проект» → «Выбрать папку…»."))
        self.statusBar().showMessage(tr("Проект забыт"), 5000)

    def export_chat_md(self) -> None:
        from PySide6.QtWidgets import QFileDialog

        if not self._messages:
            return
        path, _ = QFileDialog.getSaveFileName(
            self, tr("Экспорт чата"), "fluxion-chat.md", "Markdown (*.md)"
        )
        if not path:
            return
        blocks = []
        for entry in self._messages:
            who = tr("Вы") if entry["role"] == "user" else "Fluxion"
            blocks.append(f"**{who}**\n\n{entry['text']}\n")
        Path(path).write_text("\n---\n\n".join(blocks), encoding="utf-8")
        self.statusBar().showMessage(tr("Чат экспортирован: {path}", path=path), 5000)

    def _refresh_index_chip(self) -> None:
        if not hasattr(self, "index_chip"):
            return
        if self.rag_service is None:
            self.index_chip.setText("index: n/a")
            self.index_chip.setProperty("state", "todo")
        elif self._project_forgotten():
            self.index_chip.setText("index: off")
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
        backend_name = type(self.backend).__name__ if self.backend is not None else tr("не подключён")
        if self._is_cloud_backend():
            backend_name = tr("Облачная модель (API)")
        model = getattr(self.settings, "model", "") or "—"
        state = str(self.status_label.property("state") or "todo")
        pop.add_row(tr("Движок"), backend_name)
        pop.add_row(tr("Модель"), model)
        pop.add_row(tr("Статус"), state)
        if state == "offline":
            pop.add_note(
                tr("Возможные причины: Ollama не запущена, модель не загружена "
                "или движок отключён конфигурацией.")
            )
        pop.add_action(tr("Повторить проверку"), self._retry_engine_check)
        pop.add_action(tr("Открыть чат (сменить модель)"), lambda: self.pages.setCurrentIndex(0))

    def _retry_engine_check(self) -> None:
        self._load_models()
        self._start_status_check()

    def _build_index_popover(self, pop: ChipPopover) -> None:
        if self.rag_service is None:
            pop.add_row(tr("Индекс"), tr("недоступен (движок не подключён)"))
            pop.add_action(tr("Повторить проверку"), self._retry_engine_check)
            return
        try:
            count = self.rag_service.count()
        except Exception:
            count = 0
        pop.add_row(tr("Чанков"), str(count))
        pop.add_row(tr("Папка"), str(self.qsettings.value("project_path", "") or "—"))
        pop.add_row(tr("Обновлён"), str(self.qsettings.value("index_updated_at", "") or "—"))
        pop.add_row(tr("Эмбеддинг"), self._embed_backend_id())
        pop.add_action(tr("Переиндексировать"), self._start_indexing)
        pop.add_action(tr("Страница проекта"), lambda: self.pages.setCurrentIndex(1))

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
        tagline = QLabel(tr("Локальный AI-ассистент"))
        tagline.setObjectName("tagline")
        sidebar_layout.addWidget(self.brand_banner)
        sidebar_layout.addWidget(tagline)
        sidebar_layout.addSpacing(22)

        self.pages = QStackedWidget()
        self.nav_group = QButtonGroup(self)
        self.nav_group.setExclusive(True)

        # Chat and Project are in the sidebar; "Модели" and "Обучение" open from
        # the top menu "Модели" (pages 2 and 3 keep their indexes).
        for index, title in ((PAGE_CHAT, tr("Чат")), (PAGE_PROJECT, tr("Проект"))):
            button = QPushButton(title)
            button.setCheckable(True)
            button.setProperty("nav", True)
            button.setProperty("page", index)
            button.clicked.connect(lambda checked=False, page=index: self.pages.setCurrentIndex(page))
            self.nav_group.addButton(button)
            sidebar_layout.addWidget(button)
            if index == PAGE_CHAT:
                button.setChecked(True)

        sidebar_layout.addStretch()

        self.pages.addWidget(self._chat_page())
        self.pages.addWidget(self._project_page())
        self.pages.addWidget(self._training_page())
        self.pages.addWidget(self._models_page())
        for i in range(self.pages.count()):
            self.pages.widget(i).setObjectName("page")
        self.pages.currentChanged.connect(self._sync_nav)

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
        self.status_label.setText(tr("Движок: не подключён"))
        self.status_label.set_popover_builder(self._build_engine_popover)
        bar.addWidget(self.status_label)
        self.index_chip = StatusChip("indexChip")
        self.index_chip.setText("index: —")
        self.index_chip.set_popover_builder(self._build_index_popover)
        bar.addWidget(self.index_chip)

        self.browser_button = QToolButton()
        self.browser_button.setText(tr("Браузер"))
        self.browser_button.setCheckable(True)
        self.browser_button.setToolTip(tr("Показать/скрыть встроенный браузер"))
        self.browser_button.toggled.connect(self._toggle_web_panel)
        bar.addPermanentWidget(self.browser_button)

        self.health_button = QToolButton()
        self.health_button.setText(tr("Состояние"))
        self.health_button.setToolTip(tr("Диагностика зависимостей: Ollama, модель, Docker, веб-поиск, индекс"))
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
        m_file = bar.addMenu(tr("&Файл"))
        act_export = QAction(tr("Экспорт чата .md…"), self)
        act_export.setShortcut("Ctrl+E")
        act_export.triggered.connect(self.export_chat_md)
        m_file.addAction(act_export)
        m_file.addSeparator()
        act_quit = QAction(tr("Закрыть окно"), self)
        act_quit.setShortcut("Ctrl+W")
        act_quit.triggered.connect(self.close)
        m_file.addAction(act_quit)

        # Правка
        m_edit = bar.addMenu(tr("&Правка"))
        act_copy = QAction(tr("Копировать"), self)
        act_copy.setShortcut("Ctrl+C")
        act_copy.triggered.connect(self._copy_selection)
        m_edit.addAction(act_copy)
        m_edit.addSeparator()
        self.act_searxng = QAction(tr("Расширенный веб-поиск (SearXNG в Docker)"), self)
        self.act_searxng.setCheckable(True)
        self.act_searxng.setChecked(self._searxng_on())
        self.act_searxng.toggled.connect(self._toggle_searxng)
        m_edit.addAction(self.act_searxng)

        # Вид
        m_view = bar.addMenu(tr("&Вид"))
        self.act_theme = QAction(tr("Светлая тема") if self.theme.name == "dark" else tr("Тёмная тема"), self)
        self.act_theme.triggered.connect(self.toggle_theme)
        m_view.addAction(self.act_theme)
        m_lang = m_view.addMenu("Язык / Language")  # in both languages: findable in either
        self.language_actions = {}
        for code, name in LANGUAGES.items():
            act = QAction(name, self, checkable=True)
            act.setChecked(code == language())
            act.triggered.connect(lambda _checked=False, code=code: self.choose_language(code))
            m_lang.addAction(act)
            self.language_actions[code] = act

        # Проект
        m_proj = bar.addMenu(tr("&Проект"))
        act_pick = QAction(tr("Выбрать папку…"), self)
        act_pick.triggered.connect(self._pick_project_folder)
        m_proj.addAction(act_pick)
        act_index = QAction(tr("Проиндексировать"), self)
        act_index.triggered.connect(self._start_indexing)
        m_proj.addAction(act_index)
        m_proj.addSeparator()
        act_forget = QAction(tr("Забыть проект"), self)
        act_forget.triggered.connect(self.forget_project)
        m_proj.addAction(act_forget)

        # Модели: the catalog, the cloud model and training open from here
        m_models = bar.addMenu(tr("&Модели"))
        act_models = QAction(tr("Скачать и выбрать модели…"), self)
        act_models.setShortcut("Ctrl+M")
        act_models.triggered.connect(lambda: self.pages.setCurrentIndex(PAGE_MODELS))
        m_models.addAction(act_models)
        act_cloud = QAction(tr("Облачная модель по API…"), self)
        act_cloud.triggered.connect(self._open_cloud_section)
        m_models.addAction(act_cloud)
        m_models.addSeparator()
        act_training = QAction(tr("Обучение модели…"), self)
        act_training.triggered.connect(lambda: self.pages.setCurrentIndex(PAGE_TRAINING))
        m_models.addAction(act_training)

        # Инструменты: part in KOMPAS-3D by description (prototype, for everyone)
        m_tools = bar.addMenu(tr("&Инструменты"))
        act_cad = QAction(tr("Деталь в КОМПАС…"), self)
        act_cad.triggered.connect(self._open_cad_dialog)
        m_tools.addAction(act_cad)
        act_materials = QAction(tr("Каталог материалов…"), self)
        act_materials.triggered.connect(self._open_materials_dialog)
        m_tools.addAction(act_materials)
        act_calc = QAction(tr("Инженерный калькулятор…"), self)
        act_calc.setToolTip(tr("Сварные швы, болты, балки и рамы, гибка листа, валы — по формулам. В чате: /расчёт …"))
        act_calc.triggered.connect(lambda: self._open_calc())
        m_tools.addAction(act_calc)
        m_tools.addSeparator()
        act_report = QAction(tr("Ведомость и металлоёмкость сборки (Pro)…"), self)
        act_report.setToolTip(tr("Сводная ведомость, детали, металлоёмкость и замечания по сборке КОМПАС-3D в Excel"))
        act_report.triggered.connect(self._open_assembly_report)
        m_tools.addAction(act_report)
        act_laser = QAction(tr("Пакет на лазер (DXFka)…"), self)
        act_laser.setToolTip(tr("DXF всех листовых деталей сборки по папкам толщин (Pro)"))
        act_laser.triggered.connect(self._open_laser_package)
        m_tools.addAction(act_laser)
        m_tools.addSeparator()
        act_acad = QAction(tr("Нормоконтроль чертежа AutoCAD (Pro)…"), self)
        act_acad.setToolTip(tr("Штамп, шрифты, слои, размеры и масштабы чертежа DWG/DXF по ЕСКД и СПДС, отчёт в Excel"))
        act_acad.triggered.connect(self._open_acad_check)
        m_tools.addAction(act_acad)
        m_tools.addSeparator()
        act_style = QAction(tr("Мой стиль письма…"), self)
        act_style.setToolTip(tr("Служебки, пояснительные записки и письма заказчикам вашим стилем (Pro)"))
        act_style.triggered.connect(self._open_writing_style)
        m_tools.addAction(act_style)
        act_archive = QAction(tr("Архив КД (Pro)…"), self)
        act_archive.setToolTip(tr("Поиск по архиву КД: КОМПАС-3D, PDF, документы. Вопросы в чате: /архив …"))
        act_archive.triggered.connect(self._open_archive)
        m_tools.addAction(act_archive)

        # Помощь
        m_help = bar.addMenu(tr("&Помощь"))
        act_manual = QAction(tr("Инструкция по инструментам…"), self)
        act_manual.setShortcut("F1")
        act_manual.setToolTip(tr("Как пользоваться каждым инструментом Fluxion: пошагово, с примерами"))
        act_manual.triggered.connect(lambda: self.open_manual())
        m_help.addAction(act_manual)
        act_update = QAction(tr("Проверить обновления…"), self)
        act_update.triggered.connect(lambda: self._check_updates())
        m_help.addAction(act_update)
        self.act_license = QAction(tr("Лицензия…"), self)
        self.act_license.triggered.connect(self._open_license_dialog)
        m_help.addAction(self.act_license)
        act_about = QAction(tr("О Fluxion"), self)
        act_about.triggered.connect(self._show_about)
        m_help.addAction(act_about)

    def _show_about(self) -> None:
        """Menu Помощь → О Fluxion: the version, the site, the folder of the user's data."""
        from core.app_paths import data_dir

        site = SITE_URL.split("://", 1)[-1].rstrip("/")
        version = tr("Версия {version}", version=APP_VERSION)
        site_line = tr("Сайт: {link}", link=f'<a href="{SITE_URL}">{site}</a>')
        data_line = tr("Данные: {path}", path=html.escape(str(data_dir())))
        box = QMessageBox(self)
        box.setWindowTitle(tr("О Fluxion"))
        box.setTextFormat(Qt.RichText)
        box.setText(f"<b>Fluxion</b><br>{version}<br>{site_line}<br>{data_line}")
        label = box.findChild(QLabel, "qt_msgbox_label")
        if label is not None:
            label.setOpenExternalLinks(True)
            label.setTextInteractionFlags(Qt.TextBrowserInteraction)
        site_btn = box.addButton(tr("Открыть сайт"), QMessageBox.ActionRole)
        box.addButton(tr("Закрыть"), QMessageBox.RejectRole)
        box.exec()
        if box.clickedButton() is site_btn:
            QDesktopServices.openUrl(QUrl(SITE_URL))

    def _sync_nav(self, index: int) -> None:
        """Sidebar shows the open page; none is lit for pages from the menu."""
        self.nav_group.setExclusive(False)
        for button in self.nav_group.buttons():
            button.setChecked(button.property("page") == index)
        self.nav_group.setExclusive(True)

    def _open_cloud_section(self) -> None:
        self.pages.setCurrentIndex(PAGE_MODELS)
        scroll = self.pages.widget(PAGE_MODELS)
        card = getattr(self, "_cloud_card", None)
        if isinstance(scroll, QScrollArea) and card is not None:
            QTimer.singleShot(0, lambda: scroll.ensureWidgetVisible(card, 0, 0))

    def _searxng_on(self) -> bool:
        try:
            from .searxng import searxng_enabled

            return searxng_enabled()
        except Exception:
            return False

    def _apply_searxng_choice(self, enabled: bool) -> None:
        from .searxng import set_searxng_enabled

        set_searxng_enabled(enabled)
        provider = getattr(self.web_search, "provider", None)
        if hasattr(provider, "set_enabled"):
            provider.set_enabled(enabled)
        self._search_base = getattr(self, "_searxng_url", "") if enabled else ""
        panel = getattr(self, "web_panel", None)
        if hasattr(panel, "set_search_base"):
            panel.set_search_base(self._search_base)

    def _toggle_searxng(self, enabled: bool) -> None:
        """Menu Правка: switch SearXNG (Docker) on or off, remembered."""
        self._apply_searxng_choice(enabled)
        client = getattr(self.web_search, "client", None)
        base_url = getattr(client, "base_url", "") or "http://localhost:8080"
        if enabled:
            self.statusBar().showMessage(tr("Запускаю SearXNG в Docker…"), 8000)
            worker = SearxFixWorker(base_url, self)
            worker.done.connect(self._on_searxng_started)
            self._searxng_worker = worker
            worker.start()
        else:
            from threading import Thread

            from .searxng import stop_searxng

            Thread(target=stop_searxng, daemon=True, name="searxng-stop").start()
            self.statusBar().showMessage(
                tr("Расширенный поиск выключен: используется встроенный, Docker не нужен."), 8000
            )

    def _on_searxng_started(self, ok: bool) -> None:
        if ok:
            self.statusBar().showMessage(tr("SearXNG запущен: веб-поиск в расширенном режиме."), 8000)
        else:
            self.statusBar().showMessage(
                tr("SearXNG не запустился (нужен Docker Desktop) — пока работает встроенный поиск."), 12000
            )

    def _copy_selection(self) -> None:
        widget = QApplication.focusWidget()
        copy = getattr(widget, "copy", None)
        if callable(copy):
            copy()

    # ── web panel ────────────────────────────────────────────────────────

    def _toggle_web_panel(self, checked: bool) -> None:
        self.web_panel.setVisible(checked)

    def _show_web_panel(self) -> None:
        if self.web_panel.isHidden():
            self.web_panel.setVisible(True)
        if not self.browser_button.isChecked():
            self.browser_button.setChecked(True)

    def _on_anchor(self, url) -> None:
        target = url.toString() if isinstance(url, QUrl) else str(url)
        if target == PLAN_RUN_URL:
            self.run_plan()
            return
        if target.startswith(SPEAK_URL):
            self._toggle_speak_answer(target[len(SPEAK_URL):])
            return
        if target.startswith("file:"):  # a source of the answer: the document itself
            QDesktopServices.openUrl(QUrl(target))
            return
        self._open_in_browser(target)

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
        title = QLabel(tr("Чат"))
        title.setObjectName("pageTitle")
        header.addWidget(title)
        header.addStretch()
        self.agent_settings_button = QToolButton()
        self.agent_settings_button.setText(tr("⚙ Настройки чата"))
        self.agent_settings_button.setObjectName("iconButton")
        self.agent_settings_button.setToolTip(tr("Модель, число итераций агента, git-инструменты"))
        self.agent_settings_button.clicked.connect(self._show_agent_settings)
        header.addWidget(self.agent_settings_button)
        self.chat_kebab = QToolButton()
        self.chat_kebab.setText("⋮")
        self.chat_kebab.setObjectName("iconButton")
        self.chat_kebab.setPopupMode(QToolButton.InstantPopup)
        kebab_menu = QMenu(self.chat_kebab)
        act_new = kebab_menu.addAction(tr("Новый чат"))
        act_new.setShortcut("Ctrl+N")
        act_new.triggered.connect(self.new_chat)
        act_export = kebab_menu.addAction(tr("Экспорт чата в .md…"))
        act_export.triggered.connect(self.export_chat_md)
        kebab_menu.addSeparator()
        act_forget = kebab_menu.addAction(tr("Забыть проект"))
        act_forget.setToolTip(tr("Новый чат без проекта: модель не ищет ответы в файлах проекта"))
        act_forget.triggered.connect(self.forget_project)
        self.chat_kebab.setMenu(kebab_menu)
        header.addWidget(self.chat_kebab)
        layout.addLayout(header)

        subtitle = QLabel(tr("Спросите Fluxion о коде или начните с одного из примеров."))
        subtitle.setObjectName("subtitle")
        layout.addWidget(subtitle)

        # ── чипы-примеры: wrap вместо обрезания ──────────────────────
        flow = FlowLayout()
        for text in (tr("Объясни этот проект"), tr("Найди возможную ошибку"), tr("Предложи рефакторинг")):
            button = QPushButton(text)
            button.setProperty("suggestion", True)
            button.clicked.connect(lambda checked=False, value=text: self.prompt.setText(value))
            flow.addWidget(button)
        layout.addLayout(flow)

        self.messages = QTextBrowser()
        self.messages.setObjectName("messages")
        self.messages.setOpenExternalLinks(False)
        self.messages.setOpenLinks(False)
        self.messages.anchorClicked.connect(self._on_anchor)
        self._wire_sticky_scroll(self.messages)
        layout.addWidget(self.messages, 1)

        # ── предупреждения над вводом, не внутри скролла ─────────────
        self.banners = BannerStack()
        layout.addWidget(self.banners)

        composer = QFrame()
        composer.setObjectName("composer")
        composer_layout = QHBoxLayout(composer)
        composer_layout.setContentsMargins(12, 10, 10, 10)
        composer_layout.setSpacing(10)
        from .chat_input import ChatInput

        self.prompt = ChatInput()
        self.prompt.setPlaceholderText(tr("Напишите сообщение..."))
        self.prompt.setToolTip(
            tr("Enter — отправить; Shift+Enter — новая строка; Ctrl+Enter — разово выполнить агентом")
        )
        self.prompt.returnPressed.connect(self.send_message)
        self.prompt.agentRequested.connect(self.send_agent_once)
        for keys in ("Ctrl+Return", "Ctrl+Enter"):
            shortcut = QShortcut(QKeySequence(keys), self.prompt)
            shortcut.setContext(Qt.WidgetShortcut)
            shortcut.activated.connect(self.send_agent_once)
        composer_layout.addWidget(self.prompt, 1)
        self.voice_level = QProgressBar()
        self.voice_level.setObjectName("voiceLevel")
        self.voice_level.setRange(0, 100)
        self.voice_level.setTextVisible(False)
        self.voice_level.setFixedSize(56, 6)
        self.voice_level.setVisible(False)
        composer_layout.addWidget(self.voice_level)
        self.mic_button = QToolButton()
        self.mic_button.setObjectName("composerIcon")
        self.mic_button.setIconSize(QSize(20, 20))
        self.mic_button.setProperty("recording", False)
        self.mic_button.setToolTip(tr("Голосовой ввод (Ctrl+Пробел): говорите, пауза или повторное нажатие — стоп"))
        self.mic_button.clicked.connect(self.toggle_voice_input)
        composer_layout.addWidget(self.mic_button)
        self.speak_toggle = QToolButton()
        self.speak_toggle.setObjectName("composerIcon")
        self.speak_toggle.setIconSize(QSize(20, 20))
        self.speak_toggle.setCheckable(True)
        self.speak_toggle.setChecked(flag_setting(self.qsettings, "voice_speak_answers"))
        self.speak_toggle.toggled.connect(self._on_speak_answers_toggled)
        self.hands_free_button = QToolButton()
        self.hands_free_button.setObjectName("composerIcon")
        self.hands_free_button.setIconSize(QSize(20, 20))
        self.hands_free_button.setCheckable(True)
        self.hands_free_button.setToolTip(
            tr("Голосом без рук (Ctrl+Shift+Пробел): Fluxion слушает, отправляет, читает ответ вслух "
            "и снова слушает. «Стоп» голосом или эта кнопка — выключить")
        )
        self.hands_free_button.toggled.connect(self.set_hands_free)
        self._show_speak_toggle()
        composer_layout.addWidget(self.speak_toggle)
        composer_layout.addWidget(self.hands_free_button)
        hands_free_shortcut = QShortcut(QKeySequence("Ctrl+Shift+Space"), self)
        hands_free_shortcut.activated.connect(self.hands_free_button.toggle)
        voice_shortcut = QShortcut(QKeySequence("Ctrl+Space"), self)
        voice_shortcut.activated.connect(self.toggle_voice_input)
        self.stop_button = QPushButton(tr("Стоп"))
        self.stop_button.setObjectName("stopButton")
        self.stop_button.clicked.connect(self.stop_generation)
        self.stop_button.setVisible(False)
        self.send_button = QPushButton(tr("Отправить"))
        self.send_button.setObjectName("sendButton")
        self.send_button.clicked.connect(self.send_message)
        composer_layout.addWidget(self.stop_button)
        composer_layout.addWidget(self.send_button)
        layout.addWidget(composer)

        modes = QHBoxLayout()
        modes.setContentsMargins(2, 0, 2, 0)
        modes.setSpacing(6)
        self.chat_agent_files = QCheckBox(tr("✎ Агент: файлы"))
        self.chat_agent_files.setObjectName("modeChip")
        self.chat_agent_files.setCursor(Qt.PointingHandCursor)
        self.chat_agent_files.toggled.connect(self._on_chat_agent_files_toggled)
        modes.addWidget(self.chat_agent_files)
        self.chat_plan = QCheckBox(tr("☰ План"))
        self.chat_plan.setObjectName("modeChip")
        self.chat_plan.setCursor(Qt.PointingHandCursor)
        self.chat_plan.setToolTip(
            tr("Агент составит нумерованный план и не изменит файлы; "
            "под ответом появится «Выполнить план»")
        )
        modes.addWidget(self.chat_plan)
        self.chat_style = QCheckBox(tr("✍ Мой стиль"))
        self.chat_style.setObjectName("modeChip")
        self.chat_style.setCursor(Qt.PointingHandCursor)
        self.chat_style.setToolTip(tr("Писать служебки, пояснительные записки и письма заказчикам вашим стилем (Pro). "
                                   "Образцы — Инструменты → «Мой стиль письма…»"))
        self.chat_style.toggled.connect(self._on_chat_style_toggled)
        modes.addWidget(self.chat_style)
        modes.addStretch()
        hint = QLabel(tr("Ctrl+Enter — агенту"))
        hint.setObjectName("modeHint")
        modes.addWidget(hint)
        layout.addLayout(modes)

        self._build_agent_settings()
        self._render_messages()
        return page

    def _build_agent_settings(self) -> None:
        """Create the chat settings popover: model, iteration limit, git tools."""
        self.agent_settings = QFrame(self, Qt.Popup | Qt.FramelessWindowHint)
        self.agent_settings.setObjectName("chipPopover")
        box = QVBoxLayout(self.agent_settings)
        box.setContentsMargins(14, 12, 14, 12)
        box.setSpacing(10)

        box.addWidget(self._settings_caption(tr("Модель")))
        model_row = QHBoxLayout()
        model_row.setSpacing(8)
        self.model_combo = QComboBox()
        self.model_combo.setMinimumWidth(240)
        self.model_combo.setEnabled(False)
        self.model_combo.textActivated.connect(self._on_model_selected)
        model_row.addWidget(self.model_combo, 1)
        self.model_refresh_button = QPushButton("⟳")
        self.model_refresh_button.setObjectName("iconButton")
        self.model_refresh_button.setToolTip(tr("Обновить список моделей"))
        self.model_refresh_button.clicked.connect(self._load_models)
        model_row.addWidget(self.model_refresh_button)
        box.addLayout(model_row)
        self.message_sound = QCheckBox(tr("Звук, когда Fluxion ответил"))
        self.message_sound.setToolTip(tr("Короткий сигнал в конце ответа чата и агента"))
        self.message_sound.setChecked(message_sound_enabled(self.qsettings))
        self.message_sound.toggled.connect(
            lambda checked: self.qsettings.setValue("message_sound", int(checked))
        )
        box.addWidget(self.message_sound)

        box.addWidget(self._settings_caption(tr("Голос")))
        self.voice_speak_answers = QCheckBox(tr("Озвучивать ответы"))
        self.voice_speak_answers.setToolTip(tr("Fluxion читает ответ вслух голосом Windows; код не читается"))
        self.voice_speak_answers.setChecked(self.speak_toggle.isChecked())
        self.voice_speak_answers.toggled.connect(self.speak_toggle.setChecked)
        box.addWidget(self.voice_speak_answers)
        self.voice_send_at_once = QCheckBox(tr("Отправлять сразу после диктовки"))
        self.voice_send_at_once.setToolTip(tr("Иначе распознанный текст остаётся в поле ввода, его можно поправить"))
        self.voice_send_at_once.setChecked(flag_setting(self.qsettings, "voice_send_at_once"))
        self.voice_send_at_once.toggled.connect(
            lambda checked: self.qsettings.setValue("voice_send_at_once", int(checked))
        )
        box.addWidget(self.voice_send_at_once)

        box.addWidget(self._settings_caption(tr("Агент")))
        row = QHBoxLayout()
        row.setSpacing(10)
        row.addWidget(QLabel(tr("Макс. итераций:")))
        self.agent_iterations = QSpinBox()
        self.agent_iterations.setRange(1, 50)
        self.agent_iterations.setValue(AGENT_DEFAULT_ITERATIONS)
        row.addWidget(self.agent_iterations)
        row.addStretch()
        box.addLayout(row)
        self.agent_unlimited = QCheckBox(tr("Без лимита"))
        self.agent_unlimited.setToolTip(
            tr("Pro: агент работает, пока не закончит задачу (жёсткий потолок — 200 итераций)")
        )
        self.agent_unlimited.toggled.connect(
            lambda checked: self.agent_iterations.setEnabled(not checked)
        )
        box.addWidget(self.agent_unlimited)
        self.agent_git_button = QPushButton()
        self.agent_git_button.setObjectName("popAction")
        self.agent_git_button.setToolTip(
            tr("Git-инструменты агента: снимок проекта перед правками, "
            "git_status / git_diff / git_commit")
        )
        self.agent_git_button.clicked.connect(self._on_git_button)
        box.addWidget(self.agent_git_button)
        self._refresh_git_button()
        self._refresh_agent_settings()

    @staticmethod
    def _settings_caption(text: str) -> QLabel:
        caption = QLabel(text)
        caption.setObjectName("popKey")
        return caption

    def _on_git_button(self) -> None:
        self.agent_settings.hide()
        self._open_git_dialog()

    def _pro_active(self) -> bool:
        lic = load_activation()
        return lic is not None and lic.is_pro

    def _refresh_agent_settings(self) -> None:
        """Show "Без лимита" only with a Pro licence; drop it when Pro is gone."""
        pro = self._pro_active()
        self.agent_unlimited.setVisible(pro)
        self.agent_unlimited.setEnabled(pro)
        if not pro and self.agent_unlimited.isChecked():
            self.agent_unlimited.setChecked(False)

    def _show_agent_settings(self) -> None:
        self._refresh_agent_settings()
        popover = self.agent_settings
        self._refresh_git_button()
        popover.adjustSize()
        button = self.agent_settings_button
        origin = button.mapToGlobal(QPoint(0, button.height() + 6))
        left = origin.x() + button.width() - popover.width()
        popover.move(max(8, left), origin.y())
        popover.show()

    def _agent_max_iterations(self) -> int:
        """Return the iteration limit for the next agent run; 0 means no limit."""
        if self.agent_unlimited.isChecked() and self._pro_active():
            return 0
        return self.agent_iterations.value()

    # ── sticky scroll ────────────────────────────────────────────────────

    def _wire_sticky_scroll(self, view: QTextBrowser) -> None:
        """Make *view* follow new output until the user scrolls away from the bottom."""
        view.setProperty("sticky", True)
        scrollbar = view.verticalScrollBar()

        def on_value(value: int) -> None:
            if view.property("sticky_hold"):
                return
            view.setProperty("sticky", value >= scrollbar.maximum() - STICKY_SCROLL_SLACK)

        scrollbar.valueChanged.connect(on_value)

    def _follow_scroll(self, view: QTextBrowser) -> None:
        """Scroll *view* to the bottom once the document is laid out, if it is sticky."""
        if not view.property("sticky"):
            return
        scrollbar = view.verticalScrollBar()
        QTimer.singleShot(0, view, lambda: scrollbar.setValue(scrollbar.maximum()))

    def _set_html_keeping_scroll(self, view: QTextBrowser, content: str) -> None:
        """Replace the document of *view* without moving a reader who scrolled up."""
        scrollbar = view.verticalScrollBar()
        sticky = bool(view.property("sticky"))
        position = scrollbar.value()
        view.setProperty("sticky_hold", True)
        try:
            view.setHtml(content)
            scrollbar.setValue(scrollbar.maximum() if sticky else position)
        finally:
            view.setProperty("sticky_hold", False)
        self._follow_scroll(view)

    # ── project page ─────────────────────────────────────────────────────

    def _project_page(self) -> QWidget:
        page = QWidget()
        page.setObjectName("page")
        layout = QVBoxLayout(page)
        layout.setContentsMargins(36, 24, 36, 20)
        layout.setSpacing(12)

        # ── хедер: primary на своём месте ────────────────────────────
        header = QHBoxLayout()
        title = QLabel(tr("Проект"))
        title.setObjectName("pageTitle")
        header.addWidget(title)
        header.addStretch()
        self.index_stop_button = QPushButton(tr("Стоп"))
        self.index_stop_button.setObjectName("stopButton")
        self.index_stop_button.clicked.connect(self._stop_indexing)
        self.index_stop_button.setVisible(False)
        header.addWidget(self.index_stop_button)
        self.index_button = QPushButton(tr("Проиндексировать"))
        self.index_button.setObjectName("sendButton")
        self.index_button.clicked.connect(self._start_indexing)
        header.addWidget(self.index_button)
        layout.addLayout(header)

        subtitle = QLabel(
            tr("Выберите папку проекта и проиндексируйте её: код, тексты и документы "
            "(PDF, Word, Excel, PowerPoint) — чат будет использовать их как контекст.")
        )
        subtitle.setObjectName("subtitle")
        subtitle.setWordWrap(True)
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
        self.project_path.setPlaceholderText(tr("Папка проекта не выбрана"))
        self.project_path.setReadOnly(True)
        saved = self.qsettings.value("project_path", "")
        if saved:
            self.project_path.setText(str(saved))
        path_row.addWidget(self.project_path, 1)
        pick_button = QPushButton(tr("Выбрать…"))
        pick_button.setObjectName("iconButton")
        pick_button.clicked.connect(self._pick_project_folder)
        path_row.addWidget(pick_button)
        card_layout.addLayout(path_row)

        stats = QHBoxLayout()
        stats.setSpacing(10)
        self.chunks_chip = QLabel(tr("чанки: —"))
        self.chunks_chip.setObjectName("statusChip")
        self.index_date_chip = QLabel(tr("обновлён: —"))
        self.index_date_chip.setObjectName("statusChip")
        self.embed_chip = QLabel(tr("эмбеддинг: —"))
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
        if index == 0:
            self._refresh_git_button()
        if index == 1:
            self._refresh_project_stats()

    def _refresh_project_stats(self) -> None:
        chunks = getattr(self, "chunks_chip", None)
        if chunks is None:
            return
        if self.indexing:
            chunks.setText(tr("чанки: индексация…"))
            chunks.setProperty("state", "current")
            chunks.setToolTip(tr("Первая загрузка модели эмбеддингов может занять время."))
        elif self.rag_service is None:
            chunks.setText(tr("чанки: n/a"))
            chunks.setProperty("state", "todo")
            chunks.setToolTip("")
        else:
            try:
                count = self.rag_service.count()
            except Exception:
                count = 0
            chunks.setText(tr("чанки: {count}", count=count))
            chunks.setProperty("state", "ok" if count else "todo")
            chunks.setToolTip("")
        updated = str(self.qsettings.value("index_updated_at", "") or "")
        self.index_date_chip.setText(tr("обновлён: {value}", value=updated or '—'))
        self.embed_chip.setText(tr("эмбеддинг: {value}", value=self._embed_backend_id()))
        for chip in (chunks, self.index_date_chip, self.embed_chip):
            chip.style().unpolish(chip)
            chip.style().polish(chip)
        self._check_reindex_banner()

    def _check_reindex_banner(self) -> None:
        banners = getattr(self, "project_banners", None)
        if banners is None:
            return
        if self.rag_service is None:
            banners.show_warning(tr("Движок не подключён: индексация недоступна."), key="engine")
        else:
            banners.dismiss_key("engine")
        stored = str(self.qsettings.value("index_embed_backend", "") or "")
        current = self._embed_backend_id()
        if stored and current != "—" and stored != current:
            banners.show_warning(
                tr("Индекс собран другим эмбеддинг-бэкендом — ответы могут устареть."),
                tr("Переиндексировать"),
                self._start_indexing,
                key="reindex",
            )
        else:
            banners.dismiss_key("reindex")

    def _chunks_label(self) -> str:
        if self.rag_service is None:
            return tr("RAG: недоступен (движок не подключён)")
        try:
            count = self.rag_service.count()
        except Exception:
            count = 0
        return tr("RAG: {count} чанков в индексе", count=count)

    def refresh_project_status(self) -> None:
        """Update the RAG chunks label (e.g. after engine wiring)."""
        if not self.indexing:
            self.index_status.setText(self._chunks_label())
        self._refresh_index_chip()

    def _pick_project_folder(self) -> None:
        from PySide6.QtWidgets import QFileDialog

        start = self.project_path.text() or str(Path.home())
        folder = QFileDialog.getExistingDirectory(self, tr("Выберите папку проекта"), start)
        if folder:
            self.project_path.setText(folder)
            self.qsettings.setValue("project_path", folder)
            self._set_project_forgotten(False)
            self._refresh_git_button()

    def _start_indexing(self) -> None:
        path = self.project_path.text().strip()
        if not path or self.indexing:
            return
        if self.rag_service is None:
            self.project_view.setHtml(tr("<p>Движок не подключён. Запустите приложение через python -m desktop_browser.</p>"))
            return

        self.indexing = True
        self.index_button.setEnabled(False)
        self.index_stop_button.setVisible(True)
        self.index_status.setText(tr("Индексация… первая загрузка модели эмбеддингов может занять время."))
        self.project_view.setHtml(tr("<p><b>Индексация:</b> {value}</p>", value=html.escape(path)))

        self.index_worker = IndexWorker(self.rag_service, path)
        self.index_worker.done.connect(self._on_index_done)
        self.index_worker.failed.connect(self._on_index_failed)
        self.index_worker.finished.connect(self.index_worker.deleteLater)
        self.index_worker.start()

    def _stop_indexing(self) -> None:
        # Индексация атомарна внутри RAGService — остановка недоступна,
        # кнопка лишь информирует; оставляем завершение естественным путём.
        self.index_status.setText(tr("Завершение текущей операции…"))

    def _set_indexing_done(self) -> None:
        self.indexing = False
        self.index_button.setEnabled(True)
        self.index_stop_button.setVisible(False)
        self.index_worker = None

    def _on_index_done(self, count: int) -> None:
        self._set_indexing_done()
        self._set_project_forgotten(False)
        self.index_status.setText(self._chunks_label())
        self.project_view.append(tr("<p><b>Готово:</b> проиндексировано {count} чанков. Чат теперь будет сам искать ответы в коде и документах папки.</p>", count=count))
        self._refresh_index_chip()
        self.qsettings.setValue("index_updated_at", datetime.now().strftime("%Y-%m-%d %H:%M"))
        self.qsettings.setValue("index_embed_backend", self._embed_backend_id())
        self._refresh_project_stats()

    def _on_index_failed(self, message: str) -> None:
        self._set_indexing_done()
        self.index_status.setText(self._chunks_label())
        self.project_view.append(f"<p><b>{html.escape(message)}</b></p>")

    # ── agent page ───────────────────────────────────────────────────────

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
        return git_tools_enabled(self.qsettings)

    def _refresh_git_button(self) -> None:
        if not hasattr(self, "agent_git_button"):
            return
        if not self._git_enabled():
            self.agent_git_button.setText(tr("Git: выключен"))
            return
        from orchestrator import git_helper

        try:
            repo = git_helper.is_repo(self._agent_project_root())
        except Exception:
            repo = False
        self.agent_git_button.setText(tr("Git: репозиторий") if repo else tr("Git: нет репозитория"))

    def _open_git_dialog(self) -> None:
        import shutil

        from orchestrator import git_helper

        if not self._git_enabled():
            box = QMessageBox(self)
            box.setWindowTitle("Git")
            box.setText(tr("Git-инструменты агента выключены. Включить их снова?"))
            enable = box.addButton(tr("Включить"), QMessageBox.YesRole)
            box.addButton(tr("Оставить выключенными"), QMessageBox.NoRole)
            box.exec()
            if box.clickedButton() is enable:
                self.qsettings.setValue("agent_git_enabled", 1)
                self._refresh_git_button()
            return

        if not shutil.which("git"):
            QMessageBox.warning(
                self,
                "Git",
                tr("Git не установлен. Установите его через мастер настройки "
                "или вручную: https://git-scm.com/download/win"),
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
                tr("Проект уже в git-репозитории:\n{root}\n\n"
                "Агент может использовать git_status, git_diff и git_commit.", root=root),
            )
            return

        box = QMessageBox(self)
        box.setWindowTitle(tr("Git-репозиторий"))
        box.setText(
            tr("Папка проекта не является git-репозиторием:\n{root}\n\n"
            "Инициализировать репозиторий (git init + первый коммит) "
            "или работать без git-инструментов?", root=root)
        )
        init_btn = box.addButton(tr("Инициализировать"), QMessageBox.YesRole)
        no_git_btn = box.addButton(tr("Без git"), QMessageBox.NoRole)
        box.addButton(tr("Отмена"), QMessageBox.RejectRole)
        box.exec()
        clicked = box.clickedButton()
        if clicked is init_btn:
            self.statusBar().showMessage(tr("Инициализируем git-репозиторий…"))
            if getattr(self, "agent_git_button", None) is not None:
                self.agent_git_button.setEnabled(False)
            self._git_init_worker = GitInitWorker(str(root))
            self._git_init_worker.done.connect(self._on_git_init_done)
            self._git_init_worker.start()
        elif clicked is no_git_btn:
            self.qsettings.setValue("agent_git_enabled", 0)
            self._refresh_git_button()
            self.statusBar().showMessage(tr("Git-инструменты агента отключены."), 8000)

    def _on_git_init_done(self, ok: bool, message: str) -> None:
        if getattr(self, "agent_git_button", None) is not None:
            self.agent_git_button.setEnabled(True)
        self.statusBar().showMessage(f"{'✅' if ok else '⚠️'} {message}", 12000)
        self._refresh_git_button()

    # ── training page ────────────────────────────────────────────────────

    def _training_page(self) -> QWidget:
        page = QWidget()
        page.setObjectName("page")
        layout = QVBoxLayout(page)
        layout.setContentsMargins(36, 24, 36, 20)
        layout.setSpacing(12)

        title = QLabel(tr("Обучение"))
        title.setObjectName("pageTitle")
        layout.addWidget(title)
        self.training_subtitle = QLabel(
            tr("QLoRA дообучение на вашем датасете (Pro). Готовая модель появится в списке моделей.")
        )
        self.training_subtitle.setObjectName("subtitle")
        layout.addWidget(self.training_subtitle)

        self.training_steps = StepIndicator(
            [tr("Окружение"), tr("Данные и параметры"), tr("Запуск и мониторинг")]
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
        self.training_install_button = QPushButton(tr("Установить окружение обучения"))
        self.training_install_button.setObjectName("sendButton")
        self.training_install_button.clicked.connect(self.install_training_env)
        self.training_install_button.setVisible(False)
        c1.addWidget(self.training_install_button)
        self.training_reinstall_button = QPushButton(tr("Переустановить окружение"))
        self.training_reinstall_button.setToolTip(
            tr("Удалить окружение обучения и поставить его заново: если обучение "
            "пишет, что PyTorch без CUDA или окружение повреждено")
        )
        self.training_reinstall_button.clicked.connect(self.reinstall_training_env)
        self.training_reinstall_button.setVisible(False)
        c1.addWidget(self.training_reinstall_button, 0, Qt.AlignLeft)
        layout.addWidget(card1)

        # ── шаг 2: данные и параметры ────────────────────────────────
        card2 = QFrame()
        card2.setObjectName("stepCard")
        c2 = QVBoxLayout(card2)
        c2.setContentsMargins(16, 14, 16, 14)
        c2.setSpacing(10)
        dataset_row = QHBoxLayout()
        self.training_dataset = QLineEdit()
        self.training_dataset.setPlaceholderText(tr("Датасет .jsonl (ChatML, поле messages)"))
        self.training_dataset.textChanged.connect(self._validate_dataset)
        dataset_row.addWidget(self.training_dataset, 1)
        self.training_pick_button = QPushButton(tr("Выбрать…"))
        self.training_pick_button.setObjectName("iconButton")
        self.training_pick_button.clicked.connect(self._pick_dataset_file)
        dataset_row.addWidget(self.training_pick_button)
        self.training_docs_button = QPushButton(tr("Из документов…"))
        self.training_docs_button.setObjectName("iconButton")
        self.training_docs_button.setToolTip(tr("Составить датасет «вопрос — ответ» по вашим документам"))
        self.training_docs_button.clicked.connect(self._make_dataset_from_docs)
        dataset_row.addWidget(self.training_docs_button)
        self.dataset_chip = QLabel(tr("датасет: не выбран"))
        self.dataset_chip.setObjectName("statusChip")
        dataset_row.addWidget(self.dataset_chip)
        c2.addLayout(dataset_row)

        form = QFormLayout()
        form.setSpacing(10)
        self.training_preset = QComboBox()
        self.training_preset.addItem(tr("low — 6–8 ГБ VRAM (unsloth)"), "low")
        self.training_preset.addItem(tr("standard — 8–12 ГБ (HF peft+trl)"), "standard")
        form.addRow(tr("Пресет:"), self.training_preset)
        # A preset file (e.g. downloaded from the Fluxion website) overrides the
        # built-in preset; choosing a built-in preset again drops the file.
        self._training_preset_path = ""
        self._applying_training_preset = False
        preset_file_row = QHBoxLayout()
        self.training_preset_file_button = QPushButton(tr("Загрузить из файла…"))
        self.training_preset_file_button.setObjectName("iconButton")
        self.training_preset_file_button.clicked.connect(self._pick_training_preset_file)
        preset_file_row.addWidget(self.training_preset_file_button)
        self.training_preset_chip = QLabel(tr("встроенный пресет"))
        self.training_preset_chip.setObjectName("statusChip")
        preset_file_row.addWidget(self.training_preset_chip, 1)
        form.addRow("", preset_file_row)
        self.training_preset.currentIndexChanged.connect(self._on_training_preset_changed)
        self.training_epochs = QSpinBox()
        self.training_epochs.setRange(1, 20)
        self.training_epochs.setValue(TrainingParams().epochs)
        form.addRow(tr("Эпохи:"), self.training_epochs)
        self.training_max_samples = QSpinBox()
        self.training_max_samples.setRange(0, 1_000_000)
        self.training_max_samples.setValue(0)
        self.training_max_samples.setSpecialValueText(tr("все"))
        form.addRow(tr("Макс. примеров:"), self.training_max_samples)
        c2.addLayout(form)

        self.training_advanced_button = QPushButton(tr("▸ Дополнительные настройки"))
        self.training_advanced_button.setObjectName("iconButton")
        self.training_advanced_button.setCheckable(True)
        self.training_advanced_button.toggled.connect(self._toggle_training_advanced)
        c2.addWidget(self.training_advanced_button, 0, Qt.AlignLeft)

        self.training_advanced = QFrame()
        self.training_advanced.setObjectName("advancedBox")
        adv_box = QVBoxLayout(self.training_advanced)
        adv_box.setContentsMargins(14, 12, 14, 12)
        adv_box.setSpacing(10)
        adv_hint = QLabel(
            tr("Обычно менять не нужно: значения по умолчанию подходят для первого обучения.")
        )
        adv_hint.setObjectName("subtitle")
        adv_hint.setWordWrap(True)
        adv_box.addWidget(adv_hint)
        adv = QFormLayout()
        adv.setSpacing(10)
        adv.setFieldGrowthPolicy(QFormLayout.AllNonFixedFieldsGrow)
        adv.setRowWrapPolicy(QFormLayout.WrapLongRows)
        self.training_base_model = QLineEdit()
        self.training_base_model.setText(TrainingParams().base_model)
        self.training_base_model.setToolTip(
            tr("Модель с Hugging Face, которую дообучаем. Пресет из файла задаёт её сам.")
        )
        adv.addRow(tr("Базовая модель:"), self.training_base_model)
        self.training_ollama_model = QLineEdit()
        self.training_ollama_model.setText(TrainingParams().ollama_model_name)
        self.training_ollama_model.setToolTip(
            tr("Под этим названием дообученная модель появится в списке «Модель» в чате "
            "(и в Ollama, если она установлена).")
        )
        adv.addRow(tr("Название модели:"), self.training_ollama_model)
        self.training_adapter_name = QLineEdit()
        self.training_adapter_name.setText(TrainingParams().adapter_name)
        self.training_adapter_name.setToolTip(
            tr("Под этим именем адаптер LoRA сохранится в реестре адаптеров.")
        )
        adv.addRow(tr("Название адаптера:"), self.training_adapter_name)
        self.training_description = QLineEdit()
        self.training_description.setPlaceholderText(tr("Необязательно: для чего эта модель"))
        adv.addRow(tr("Описание:"), self.training_description)
        adv_box.addLayout(adv)
        self.training_export = QCheckBox(tr("Собрать готовую модель для чата (GGUF)"))
        self.training_export.setChecked(True)
        adv_box.addWidget(self.training_export)
        self.training_export_note = QLabel()
        self.training_export_note.setObjectName("subtitle")
        self.training_export_note.setWordWrap(True)
        adv_box.addWidget(self.training_export_note)
        self._refresh_training_export_note()
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
        self.training_run_button = QPushButton(tr("Начать обучение"))
        self.training_run_button.setObjectName("sendButton")
        self.training_run_button.clicked.connect(self.run_training)
        self.training_stop_button = QPushButton(tr("Стоп"))
        self.training_stop_button.setObjectName("stopButton")
        self.training_stop_button.clicked.connect(self.stop_training)
        self.training_stop_button.setVisible(False)
        buttons.addWidget(self.training_run_button)
        buttons.addWidget(self.training_stop_button)
        buttons.addStretch()
        c3.addLayout(buttons)
        self.training_busy = QProgressBar()
        self.training_busy.setRange(0, 0)
        self.training_busy.setTextVisible(False)
        self.training_busy.setFixedHeight(6)
        self.training_busy.setVisible(False)
        c3.addWidget(self.training_busy)
        self.training_activity = QLabel()
        self.training_activity.setObjectName("subtitle")
        self.training_activity.setWordWrap(True)
        self.training_activity.setVisible(False)
        c3.addWidget(self.training_activity)
        self._activity_timer = QTimer(self)
        self._activity_timer.setInterval(1000)
        self._activity_timer.timeout.connect(self._tick_activity)
        self._activity_title = ""
        self._activity_started = 0.0
        self._activity_last = 0.0
        self.training_view = QTextBrowser()
        self.training_view.setObjectName("messages")
        self.training_view.setMinimumHeight(220)
        self._wire_sticky_scroll(self.training_view)
        self.training_view.setHtml(
            tr("<p>Этапы: обучение → объединение с базовой моделью → готовая модель (GGUF) "
            "→ список моделей программы.</p>")
        )
        c3.addWidget(self.training_view, 1)
        layout.addWidget(card3, 1)

        self._refresh_training_steps()
        # Scrollable: opening "Дополнительные настройки" used to squeeze the
        # fields into thin strips because the page had no room to grow.
        scroll = QScrollArea()
        scroll.setObjectName("pageScroll")
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setWidget(page)
        return scroll

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
        title = QLabel(tr("Модели"))
        title.setObjectName("pageTitle")
        header.addWidget(title)
        header.addStretch()
        self.models_refresh_button = QPushButton("⟳")
        self.models_refresh_button.setObjectName("iconButton")
        self.models_refresh_button.setToolTip(tr("Обновить список и каталог с сайта"))
        self.models_refresh_button.clicked.connect(self._refresh_models_and_catalog)
        header.addWidget(self.models_refresh_button)
        layout.addLayout(header)

        subtitle = QLabel(
            tr("Каталог GGUF-моделей: скачивание с докачкой, импорт без перекачки. "
            "Хранилище: %LOCALAPPDATA%\\Fluxion\\models")
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

        filters = QHBoxLayout()
        filters.setSpacing(8)
        self.models_search = QLineEdit()
        self.models_search.setPlaceholderText(tr("Поиск модели…"))
        self.models_search.setClearButtonEnabled(True)
        self.models_search.textChanged.connect(self._apply_model_filter)
        filters.addWidget(self.models_search, 1)
        self.models_kind = QComboBox()
        for label, kind in MODEL_KINDS:
            self.models_kind.addItem(label, kind)
        self.models_kind.currentIndexChanged.connect(self._apply_model_filter)
        filters.addWidget(self.models_kind)
        self.models_vram = QComboBox()
        for label, gb in MODEL_VRAM_LIMITS:
            self.models_vram.addItem(label, gb)
        self.models_vram.setToolTip(tr("Показать модели, которым хватит памяти видеокарты"))
        self.models_vram.currentIndexChanged.connect(self._apply_model_filter)
        filters.addWidget(self.models_vram)
        layout.addLayout(filters)

        self._model_cards_box = QVBoxLayout()
        self._model_cards_box.setSpacing(12)
        layout.addLayout(self._model_cards_box)
        self.models_empty = QLabel(tr("Под условия поиска не подходит ни одна модель."))
        self.models_empty.setObjectName("subtitle")
        self.models_empty.setVisible(False)
        layout.addWidget(self.models_empty)
        self._model_buttons: dict[str, QPushButton] = {}
        self._model_cards: dict[str, tuple] = {}
        self._catalog_worker: CatalogWorker | None = None
        self._gpu_memory_gb = 0.0
        self._build_model_cards()

        actions = QHBoxLayout()
        import_disk = QPushButton(tr("Импорт с диска…"))
        import_disk.setProperty("suggestion", True)
        import_disk.clicked.connect(self._import_model_disk)
        actions.addWidget(import_disk)
        by_link = QPushButton(tr("Скачать по ссылке…"))
        by_link.setProperty("suggestion", True)
        by_link.setToolTip(tr("Любая модель GGUF с Hugging Face, не только из списка"))
        by_link.clicked.connect(self._download_model_by_link)
        actions.addWidget(by_link)
        import_ollama = QPushButton(tr("Импорт из Ollama"))
        import_ollama.setProperty("suggestion", True)
        import_ollama.clicked.connect(self._import_models_ollama)
        actions.addWidget(import_ollama)
        actions.addStretch()
        layout.addLayout(actions)

        self.models_installed = QLabel()
        self.models_installed.setObjectName("subtitle")
        self.models_installed.setWordWrap(True)
        layout.addWidget(self.models_installed)
        self._cloud_card = self._cloud_api_card()
        layout.addWidget(self._cloud_card)
        layout.addWidget(self._speech_models_card())
        layout.addStretch()

        self._refresh_models()
        self._start_catalog_fetch()
        # Scrollable: the catalog plus the cloud section do not fit a small window.
        scroll = QScrollArea()
        scroll.setObjectName("pageScroll")
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setWidget(page)
        return scroll

    # ── speech recognition models (voice input) ───────────────────────────

    def _speech_models_card(self) -> QFrame:
        from voice.models import SPEECH_MODELS

        card = QFrame()
        card.setObjectName("stepCard")
        box = QVBoxLayout(card)
        box.setContentsMargins(16, 12, 16, 12)
        box.setSpacing(8)
        title = QLabel(
            tr("<b>Распознавание речи</b>"
            '<br><span style="color:#94A3B8;">Модель Whisper для голосового ввода в чате (кнопка микрофона или Ctrl+Пробел). '
            "Речь распознаётся на компьютере, звук никуда не отправляется. Бесплатно.</span>")
        )
        title.setTextFormat(Qt.RichText)
        title.setWordWrap(True)
        box.addWidget(title)
        self._speech_buttons: dict[str, QPushButton] = {}
        for model in SPEECH_MODELS:
            row = QHBoxLayout()
            label = QLabel(
                tr("<b>{value}</b> · {value2:.1f} ГБ"
                '<br><span style="color:#94A3B8;">{value3}</span>', value=html.escape(model.name), value2=model.size_bytes / 1e9, value3=html.escape(model.summary))
            )
            label.setTextFormat(Qt.RichText)
            label.setWordWrap(True)
            row.addWidget(label, 1)
            button = QPushButton()
            button.clicked.connect(lambda checked=False, mid=model.model_id: self._on_speech_model_button(mid))
            row.addWidget(button)
            self._speech_buttons[model.model_id] = button
            box.addLayout(row)
        self._refresh_speech_models()
        return card

    def _refresh_speech_models(self) -> None:
        from voice.models import is_installed

        current = voice_model_id(self.qsettings)
        busy = self._speech_download_worker is not None
        for model_id, button in self._speech_buttons.items():
            if not is_installed(model_id):
                button.setText(tr("Скачать"))
                button.setEnabled(not busy)
            elif model_id == current:
                button.setText(tr("✓ Используется"))
                button.setEnabled(False)
            else:
                button.setText(tr("Использовать"))
                button.setEnabled(not busy)

    def _on_speech_model_button(self, model_id: str) -> None:
        from voice.models import is_installed

        if is_installed(model_id):
            self.qsettings.setValue("voice_model", model_id)
            self._refresh_speech_models()
            return
        if self._speech_download_worker is not None:
            return
        self.models_progress.setVisible(True)
        self.models_progress.setRange(0, 0)
        self.models_status.setText(tr("Скачиваем модель распознавания речи… докачка поддерживается"))
        worker = SpeechModelDownloadWorker(model_id, self)
        self._speech_download_worker = worker
        worker.progress.connect(self._on_speech_download_progress)
        worker.done.connect(lambda path, mid=model_id: self._on_speech_download_done(mid))
        worker.failed.connect(self._on_speech_download_failed)
        worker.finished.connect(lambda: self._on_speech_download_finished(worker))
        self._refresh_speech_models()
        worker.start()

    def _on_speech_download_progress(self, done: int, total: int) -> None:
        if total > 0:
            self.models_progress.setRange(0, total)
            self.models_progress.setValue(min(done, total))
            self.models_status.setText(
                tr("Скачиваем модель распознавания речи… {value:.2f} / {value2:.2f} ГБ", value=done / 1e9, value2=total / 1e9)
            )

    def _on_speech_download_done(self, model_id: str) -> None:
        from voice.models import speech_model

        self.qsettings.setValue("voice_model", model_id)
        self.models_progress.setVisible(False)
        self.models_status.setText(
            tr("Модель {value} загружена: голосовой ввод в чате — кнопка микрофона или Ctrl+Пробел.", value=speech_model(model_id).name)
        )

    def _on_speech_download_failed(self, message: str) -> None:
        self.models_progress.setVisible(False)
        self.models_status.setText(message)

    def _on_speech_download_finished(self, worker) -> None:
        if self._speech_download_worker is worker:
            self._speech_download_worker = None
        worker.deleteLater()
        self._refresh_speech_models()

    # ── cloud model by API (Pro) ──────────────────────────────────────────

    def _is_cloud_backend(self) -> bool:
        return self.backend is not None and type(self.backend).__name__ == "APIBackend"

    def _cloud_api_card(self) -> QFrame:
        from core.cloud_api import PROVIDERS, load_cloud_config, provider_by_key

        cfg = load_cloud_config()
        card = QFrame()
        card.setObjectName("stepCard")
        box = QVBoxLayout(card)
        box.setContentsMargins(16, 12, 16, 12)
        box.setSpacing(8)
        title = QLabel(
            tr('<b>Облачная модель по API</b> <span style="color:#8B4BFF; font-weight:600;">PRO</span>'
            '<br><span style="color:#94A3B8;">Чат и агент работают через модель провайдера: '
            "OpenRouter, Groq, Together, OpenAI или любой OpenAI-совместимый адрес (vLLM, LM Studio). "
            "Вопросы и фрагменты кода из них уходят провайдеру, токены оплачиваются у него.</span>")
        )
        title.setTextFormat(Qt.RichText)
        title.setWordWrap(True)
        box.addWidget(title)

        form = QFormLayout()
        self.cloud_provider = QComboBox()
        for provider in PROVIDERS:
            self.cloud_provider.addItem(provider.name, provider.key)
        index = self.cloud_provider.findData(cfg.provider)
        self.cloud_provider.setCurrentIndex(index if index >= 0 else 0)
        self.cloud_provider.currentIndexChanged.connect(self._on_cloud_provider_changed)
        form.addRow(tr("Провайдер"), self.cloud_provider)

        self.cloud_base_url = QLineEdit(cfg.base_url)
        self.cloud_base_url.setPlaceholderText("https://…/v1")
        form.addRow(tr("Адрес API"), self.cloud_base_url)

        key_row = QHBoxLayout()
        self.cloud_key = QLineEdit(cfg.api_key)
        self.cloud_key.setEchoMode(QLineEdit.Password)
        self.cloud_key.setPlaceholderText(tr("ключ из личного кабинета провайдера"))
        key_row.addWidget(self.cloud_key, 1)
        show_key = QToolButton()
        show_key.setText(tr("Показать"))
        show_key.setCheckable(True)
        show_key.toggled.connect(
            lambda on: self.cloud_key.setEchoMode(QLineEdit.Normal if on else QLineEdit.Password)
        )
        key_row.addWidget(show_key)
        form.addRow(tr("Ключ"), key_row)

        model_row = QHBoxLayout()
        self.cloud_model = QComboBox()
        self.cloud_model.setEditable(True)
        if cfg.model:
            self.cloud_model.addItem(cfg.model)
        example = provider_by_key(cfg.provider).example_model
        self.cloud_model.lineEdit().setPlaceholderText(example or tr("имя модели у провайдера"))
        model_row.addWidget(self.cloud_model, 1)
        self.cloud_models_button = QPushButton(tr("Список моделей"))
        self.cloud_models_button.setProperty("suggestion", True)
        self.cloud_models_button.clicked.connect(lambda: self._start_cloud_worker("models"))
        model_row.addWidget(self.cloud_models_button)
        form.addRow(tr("Модель"), model_row)
        box.addLayout(form)

        self.cloud_enabled = QCheckBox(tr("Использовать облачную модель вместо локальной"))
        self.cloud_enabled.setChecked(cfg.enabled)
        box.addWidget(self.cloud_enabled)

        actions = QHBoxLayout()
        self.cloud_check_button = QPushButton(tr("Проверить связь"))
        self.cloud_check_button.setProperty("suggestion", True)
        self.cloud_check_button.clicked.connect(lambda: self._start_cloud_worker("check"))
        actions.addWidget(self.cloud_check_button)
        self.cloud_save_button = QPushButton(tr("Сохранить"))
        self.cloud_save_button.setProperty("suggestion", True)
        self.cloud_save_button.clicked.connect(self._cloud_save)
        actions.addWidget(self.cloud_save_button)
        actions.addStretch()
        box.addLayout(actions)

        self.cloud_status = QLabel()
        self.cloud_status.setObjectName("subtitle")
        self.cloud_status.setWordWrap(True)
        box.addWidget(self.cloud_status)

        self._cloud_worker: CloudApiWorker | None = None
        self._refresh_cloud_card()
        return card

    def _on_cloud_provider_changed(self, _index: int) -> None:
        from core.cloud_api import provider_by_key

        provider = provider_by_key(str(self.cloud_provider.currentData() or ""))
        if provider.base_url:
            self.cloud_base_url.setText(provider.base_url)
        # A model id of one provider rarely exists at another: suggest one.
        self.cloud_model.clear()
        if provider.example_model:
            self.cloud_model.setEditText(provider.example_model)
        self.cloud_model.lineEdit().setPlaceholderText(
            provider.example_model or tr("имя модели у провайдера")
        )

    def _cloud_form_config(self):
        from core.cloud_api import CloudConfig

        return CloudConfig(
            enabled=self.cloud_enabled.isChecked(),
            provider=str(self.cloud_provider.currentData() or "custom"),
            base_url=self.cloud_base_url.text().strip(),
            api_key=self.cloud_key.text().strip(),
            model=self.cloud_model.currentText().strip(),
        )

    def _refresh_cloud_card(self, prefix: str = "") -> None:
        if not hasattr(self, "cloud_status"):
            return
        from core.cloud_api import env_overrides
        from licensing import feature_enabled

        allowed = bool(feature_enabled("multi_model"))
        self.cloud_save_button.setText(tr("Сохранить") if allowed else tr("Доступно в Pro"))
        lines = [prefix] if prefix else []
        if self._is_cloud_backend():
            lines.append(tr("Сейчас чат и агент работают через облачную модель {model}.", model=self.backend.model))
        elif self.cloud_enabled.isChecked() and not allowed:
            lines.append(tr("Облачная модель включена, но лицензия Pro не активна: работает локальная модель."))
        overrides = env_overrides()
        if overrides:
            lines.append(
                tr("Переменные ") + ", ".join(overrides)
                + tr(" заданы в системе или в файле .env и действуют вместо настроек здесь.")
            )
        if not allowed:
            lines.append(tr("Облачные модели доступны в Fluxion Pro: меню Помощь → Лицензия…"))
        self.cloud_status.setText("\n".join(lines))

    def _start_cloud_worker(self, action: str) -> None:
        if self._cloud_worker is not None:
            return
        self.cloud_status.setText(
            tr("Получаем список моделей…") if action == "models" else tr("Проверяем связь с провайдером…")
        )
        self.cloud_check_button.setEnabled(False)
        self.cloud_models_button.setEnabled(False)
        worker = CloudApiWorker(self._cloud_form_config(), action, self)
        worker.done.connect(
            lambda ok, message, models, act=action: self._on_cloud_worker_done(act, ok, message, models)
        )
        worker.finished.connect(self._on_cloud_worker_finished)
        self._cloud_worker = worker
        worker.start()

    def _on_cloud_worker_finished(self) -> None:
        worker, self._cloud_worker = self._cloud_worker, None
        self.cloud_check_button.setEnabled(True)
        self.cloud_models_button.setEnabled(True)
        if worker is not None:
            worker.deleteLater()

    def _on_cloud_worker_done(self, action: str, ok: bool, message: str, models: list) -> None:
        if action == "models" and ok:
            current = self.cloud_model.currentText().strip()
            self.cloud_model.clear()
            self.cloud_model.addItems([str(m) for m in models])
            self.cloud_model.setEditText(current or (str(models[0]) if models else ""))
            if models and not current:
                message += tr(" Выберите модель в списке.")
        self.cloud_status.setText(("✓ " if ok else "✗ ") + message)

    def _cloud_save(self) -> None:
        from core.cloud_api import apply_cloud_config, save_cloud_config
        from licensing import feature_enabled

        if not feature_enabled("multi_model"):
            self.cloud_status.setText(
                tr("Облачные модели доступны в Fluxion Pro. Активируйте лицензию: меню Помощь → Лицензия…")
            )
            return
        cfg = self._cloud_form_config()
        if cfg.enabled and not cfg.complete:
            self.cloud_status.setText(tr("Чтобы включить облачную модель, заполните адрес, ключ и модель."))
            return
        if self.generating or self.agent_running:
            self.cloud_status.setText(tr("Дождитесь конца ответа или работы агента и сохраните ещё раз."))
            return
        try:
            save_cloud_config(cfg)
        except OSError as exc:
            self.cloud_status.setText(tr("Не удалось сохранить настройки: {exc}", exc=exc))
            return
        apply_cloud_config(cfg)
        self._switch_backend()
        self._refresh_cloud_card(tr("Сохранено.") if self._is_cloud_backend() or not cfg.enabled
                                 else tr("Сохранено, но облачная модель не подключилась."))

    def _switch_backend(self) -> None:
        """Rebuild the chat engine after the cloud settings changed (no restart)."""
        if self.settings is None:
            return
        from cli.app import _startup_backend

        from .engine import _wire_gguf_paths

        try:
            _wire_gguf_paths(self.settings)
        except Exception:
            pass
        try:
            backend, _gated = _startup_backend(self.settings)
        except Exception as exc:  # noqa: BLE001 — keep the old engine
            self._append_banner(tr("Не удалось сменить движок: {exc}", exc=exc))
            return
        old = self.backend
        if old is not None and type(old) is not type(backend):
            unload = getattr(old, "unload", None)
            if callable(unload):
                try:
                    unload()  # frees the embedded model's memory when going to the cloud
                except Exception:
                    pass
        self.backend = backend
        if self.assistant is not None and hasattr(self.assistant, "backend"):
            self.assistant.backend = backend
        self._load_models()
        self._start_status_check()

    def _build_model_cards(self) -> None:
        """(Re)create one card per catalog model; the filters only hide cards."""
        box = self._model_cards_box
        while box.count():
            item = box.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()
        self._model_buttons = {}
        self._model_cards = {}
        for entry in self.model_mgr.catalog():
            card = QFrame()
            card.setObjectName("stepCard")
            row = QHBoxLayout(card)
            row.setContentsMargins(16, 12, 16, 12)
            row.setSpacing(10)
            pro_mark = (
                ' <span style="color:#8B4BFF; font-weight:600;">PRO</span>' if entry.pro else ""
            )
            summary = (
                f'<br><span style="color:#94A3B8;">{html.escape(entry.summary)}</span>'
                if entry.summary else ""
            )
            name = QLabel(f"<b>{html.escape(entry.name)}</b>{pro_mark}{summary}")
            name.setTextFormat(Qt.RichText)
            name.setWordWrap(True)
            row.addWidget(name, 1)
            meta = QLabel(
                tr("{value:.1f} ГБ", value=entry.size_bytes / 1_000_000_000)
                + (tr(" · ~{vram_gb:.0f} ГБ VRAM", vram_gb=entry.vram_gb) if entry.vram_gb else "")
                + (tr(" · эмбеддинги") if entry.task == "embedding" else "")
                + (tr(" · по умолчанию") if entry.default else "")
            )
            meta.setObjectName("statusChip")
            row.addWidget(meta)
            button = QPushButton()
            button.setProperty("suggestion", True)
            button.clicked.connect(lambda checked=False, mid=entry.model_id: self._on_model_button(mid))
            row.addWidget(button)
            self._model_buttons[entry.model_id] = button
            self._model_cards[entry.model_id] = (card, entry)
            box.addWidget(card)
        self._apply_model_filter()

    def _model_matches(self, entry, text: str, kind: str, vram_gb: float) -> bool:
        if text:
            haystack = " ".join(
                [entry.name, entry.summary, entry.repo_id, entry.filename, " ".join(entry.tags)]
            ).casefold()
            if not all(word in haystack for word in text.split()):
                return False
        if kind == "installed":
            if not self.model_mgr.is_installed(entry.model_id):
                return False
        elif kind == "rag":
            if entry.task != "embedding":
                return False
        elif kind == "pro":
            if not entry.pro:
                return False
        elif kind == "free":
            if entry.pro:
                return False
        elif kind and kind not in entry.tags:
            return False
        return not (vram_gb > 0 and entry.vram_gb > vram_gb)

    def _apply_model_filter(self, *_args) -> None:
        cards = getattr(self, "_model_cards", None)
        if cards is None or not hasattr(self, "models_search"):
            return
        text = self.models_search.text().strip().casefold()
        kind = str(self.models_kind.currentData() or "")
        vram_gb = float(self.models_vram.currentData() or 0)
        if vram_gb < 0:  # "my video card"
            vram_gb = float(self._gpu_memory_gb or 0)
        shown = 0
        for card, entry in cards.values():
            visible = self._model_matches(entry, text, kind, vram_gb)
            card.setHidden(not visible)
            shown += visible
        self.models_empty.setHidden(shown > 0 or not cards)

    def _refresh_models_and_catalog(self) -> None:
        self._refresh_models()
        self._start_catalog_fetch()

    def _start_catalog_fetch(self) -> None:
        """New models from the website catalog, and the video card's memory."""
        if getattr(self, "_catalog_worker", None) is not None:
            return
        self._catalog_worker = CatalogWorker(self.model_mgr, self)
        self._catalog_worker.done.connect(self._on_catalog_fetched)
        self._catalog_worker.start()

    def _on_catalog_fetched(self, count: int, error: str, gpu_gb: float) -> None:
        worker, self._catalog_worker = self._catalog_worker, None
        if worker is not None:
            worker.wait(1000)
            worker.deleteLater()
        if gpu_gb > 0 and self.models_vram.findData(-1.0) < 0:
            # "my card": the detected memory, right after "any card"
            self._gpu_memory_gb = gpu_gb
            self.models_vram.insertItem(1, tr("Моя видеокарта ({gpu_gb:.0f} ГБ)", gpu_gb=gpu_gb), -1.0)
        if count <= 0:
            if error:
                self.models_refresh_button.setToolTip(tr("Обновить список. {error}", error=error))
            return
        self.models_refresh_button.setToolTip(tr("Обновить список и каталог с сайта"))
        if getattr(self, "_model_download_worker", None) is not None:
            return  # no rebuild under a running download; the next refresh does it
        shown = [entry for _card, entry in self._model_cards.values()]
        if shown != self.model_mgr.catalog():
            self._build_model_cards()
            self._refresh_models()

    def _refresh_models(self) -> None:
        from core.model_manager import model_allowed

        mm = getattr(self, "model_mgr", None)
        if mm is None:
            return
        for model_id, button in self._model_buttons.items():
            entry = mm.catalog_entry(model_id)
            if entry is None:
                continue
            if mm.is_installed(model_id):
                button.setText(tr("Удалить"))
            elif not model_allowed(entry):
                button.setText(tr("Доступно в Pro"))
            else:
                button.setText(tr("Скачать ({value:.1f} ГБ)", value=entry.size_bytes / 1_000_000_000))
        installed = mm.list_installed()
        if installed:
            lines = [tr("• {filename} — {value:.2f} ГБ ({value2})", filename=i.filename, value=i.size_bytes / 1_000_000_000, value2=i.source or tr('импорт')) for i in installed]
            self.models_installed.setText(tr("Установлены:\n") + "\n".join(lines))
        else:
            self.models_installed.setText(tr("Установленных моделей нет."))

    def _on_model_button(self, model_id: str) -> None:
        from core.model_manager import model_allowed

        mm = getattr(self, "model_mgr", None)
        if mm is None:
            return
        entry = mm.catalog_entry(model_id)
        if entry is None:
            return
        if mm.is_installed(model_id):
            answer = QMessageBox.question(
                self, tr("Удалить модель"), tr("Удалить {filename} из хранилища?", filename=entry.filename),
                QMessageBox.Yes | QMessageBox.No,
            )
            if answer == QMessageBox.Yes:
                mm.delete(entry.filename)
                self.models_status.setText(tr("Удалено: {filename}", filename=entry.filename))
                self._refresh_models()
                self._load_models()
            return
        if not model_allowed(entry):
            self.models_status.setText(
                tr("«{name}» доступна в Fluxion Pro. "
                "Активируйте лицензию: меню Помощь → Лицензия…", name=entry.name)
            )
            return
        self._start_model_download(model_id)

    def _download_model_by_link(self) -> None:
        if getattr(self, "_model_download_worker", None) is not None:
            self.models_status.setText(tr("Дождитесь окончания текущей загрузки."))
            return
        mm = getattr(self, "model_mgr", None)
        installed = {m.filename for m in mm.list_installed()} if mm is not None else set()
        dialog = HfDownloadDialog(self, installed=installed)
        if dialog.exec() != QDialog.Accepted or dialog.selected_file is None:
            return
        self._start_model_download("", hf_file=dialog.selected_file)

    def _start_model_download(self, model_id: str, hf_file=None) -> None:
        if getattr(self, "_model_download_worker", None) is not None:
            return
        self.models_progress.setVisible(True)
        self.models_progress.setRange(0, 0)
        self.models_status.setText(tr("Скачиваем модель… докачка поддерживается"))
        for button in self._model_buttons.values():
            button.setEnabled(False)
        self._model_download_worker = ModelDownloadWorker(model_id, hf_file=hf_file)
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
                tr("Скачиваем модель… {value:.1f} / {value2:.1f} ГБ", value=done / 1e9, value2=total / 1e9)
            )
        else:
            self.models_progress.setRange(0, 0)

    def _on_model_download_done(self, path: str) -> None:
        from core.backend_factory import _detect_backend_type

        self._model_download_worker = None
        self.models_progress.setVisible(False)
        for button in self._model_buttons.values():
            button.setEnabled(True)
        self.models_status.setText(tr("Модель загружена: {value}", value=Path(path).name))
        self._refresh_models()
        try:
            entry_name = Path(path).name
            default_entry = next((e for e in self.model_mgr.catalog() if e.filename == entry_name), None)
            task = default_entry.task if default_entry is not None else self.model_mgr._sidecar(Path(path)).get("task", "chat")
            if task == "chat" and _detect_backend_type(self.settings) == "llama_cpp":
                self._set_gguf_path(path)
                self.qsettings.setValue("gguf_path", path)
        except Exception:
            pass
        self._load_models()  # the new model appears in "⚙ Настройки чата" → «Модель»
        self._start_status_check()  # the engine picks the model up: show it online

    def _on_model_download_failed(self, message: str) -> None:
        self._model_download_worker = None
        self.models_progress.setVisible(False)
        for button in self._model_buttons.values():
            button.setEnabled(True)
        self.models_status.setText(message)

    def _import_model_disk(self) -> None:
        from PySide6.QtWidgets import QFileDialog

        path, _ = QFileDialog.getOpenFileName(self, tr("Выберите GGUF-модель"), "", "GGUF (*.gguf)")
        if not path:
            return
        self._start_model_import(path)

    def _import_models_ollama(self) -> None:
        self._start_model_import(None)

    def _start_model_import(self, source_path: str | None) -> None:
        if getattr(self, "_model_import_worker", None) is not None:
            return
        self.models_status.setText(tr("Импорт…"))
        self._model_import_worker = ModelImportWorker(source_path)
        self._model_import_worker.done.connect(self._on_model_import_done)
        self._model_import_worker.failed.connect(self._on_model_download_failed)
        self._model_import_worker.finished.connect(self._model_import_worker.deleteLater)
        self._model_import_worker.start()

    def _on_model_import_done(self, message: str) -> None:
        self._model_import_worker = None
        self.models_status.setText(message)
        self._refresh_models()
        self._load_models()

    def _toggle_training_advanced(self, opened: bool) -> None:
        self.training_advanced.setVisible(opened)
        self.training_advanced_button.setText(
            ("▾ " if opened else "▸ ") + tr("Дополнительные настройки")
        )

    def _refresh_training_export_note(self) -> None:
        if os.environ.get("LLAMA_CPP_DIR", "") and Path(os.environ["LLAMA_CPP_DIR"]).is_dir():
            text = (
                tr("После обучения модель соберётся в один файл и появится в списке "
                "«Модель» в чате.")
            )
        else:
            text = (
                tr("Для сборки нужна папка llama.cpp в переменной окружения LLAMA_CPP_DIR. "
                "Без неё сохранится адаптер, а готовую модель можно собрать позже.")
            )
        self.training_export_note.setText(text)

    def _pick_training_preset_file(self) -> None:
        from PySide6.QtWidgets import QFileDialog

        path, _ = QFileDialog.getOpenFileName(
            self, tr("Выберите пресет обучения"), str(Path.home()),
            tr("Пресет Fluxion (*.json);;Все файлы (*.*)"),
        )
        if path:
            self._load_training_preset_file(path)

    def _load_training_preset_file(self, path: str) -> bool:
        """Apply a preset file to the training form. Returns success."""
        from finetune.presets import PresetError, load_preset

        try:
            preset = load_preset(path)
        except PresetError as exc:
            self.training_preset_chip.setText(tr("пресет не загружен"))
            self.training_view.setHtml(tr("<p><b>Пресет не загружен.</b> {value}</p>", value=html.escape(str(exc))))
            return False
        self._applying_training_preset = True
        try:
            index = self.training_preset.findData(preset.trainer)
            if index >= 0:
                self.training_preset.setCurrentIndex(index)
            epochs = preset.settings.get("num_train_epochs")
            if epochs:
                self.training_epochs.setValue(int(epochs))
            self.training_max_samples.setValue(preset.max_samples or 0)
            if preset.base_model:
                self.training_base_model.setText(preset.base_model)
        finally:
            self._applying_training_preset = False
        self._training_preset_path = path
        self.training_preset_chip.setText(tr("из файла: {name}", name=preset.name))
        vram = tr(" Нужно от {min_vram_gb:g} ГБ видеопамяти.", min_vram_gb=preset.min_vram_gb) if preset.min_vram_gb else ""
        self.training_view.setHtml(
            tr("<p><b>Пресет «{value}» загружен.</b>{vram}</p>"
            "<p>{value2}</p>", value=html.escape(preset.name), vram=vram, value2=html.escape(preset.description))
        )
        return True

    def _on_training_preset_changed(self, _index: int) -> None:
        if self._applying_training_preset or not self._training_preset_path:
            return
        self._training_preset_path = ""
        self.training_preset_chip.setText(tr("встроенный пресет"))

    def _pick_dataset_file(self) -> None:
        from PySide6.QtWidgets import QFileDialog

        start = self.training_dataset.text().strip() or str(Path.home())
        path, _ = QFileDialog.getOpenFileName(
            self, tr("Выберите датасет"), start, tr("JSONL (*.jsonl);;Все файлы (*.*)")
        )
        if path:
            self.training_dataset.setText(path)

    def _make_dataset_from_docs(self) -> None:
        from .doc_dataset_dialog import DocDatasetDialog

        dialog = DocDatasetDialog(self.backend, self.settings.paths.data_dir, self)
        if dialog.exec() == QDialog.Accepted and dialog.saved_path:
            self.training_dataset.setText(dialog.saved_path)

    def _validate_dataset(self, path: str) -> None:
        chip = getattr(self, "dataset_chip", None)
        if chip is None:
            return
        path = path.strip()
        if not path:
            chip.setText(tr("датасет: не выбран"))
            state = "todo"
        else:
            file = Path(path)
            if file.is_file():
                from finetune.dataset_loader import check_dataset

                problem = check_dataset(file)
                if problem:
                    chip.setText(tr("датасет: формат не распознан ⚠"))
                    chip.setToolTip(problem)
                    state = "warn"
                else:
                    from finetune.dataset_loader import estimate_samples

                    count = f"{estimate_samples(file):,}".replace(",", " ")
                    chip.setText(
                        tr("датасет: {value:.1f} МБ · ~{count} примеров ✓", value=file.stat().st_size / 1_048_576, count=count)
                    )
                    chip.setToolTip("")
                    state = "ok"
            else:
                chip.setText(tr("датасет: файл не найден ⚠"))
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
                tr("QLoRA дообучение на вашем датасете (Pro). Готовая модель появится в списке моделей.")
            )
        else:
            self.training_subtitle.setText(
                tr("QLoRA-обучение — Pro-функция. Активируйте лицензию: меню Помощь → Лицензия…")
            )
        self._train_allowed = allowed
        self._refresh_training_steps()

    def _env_dir(self):
        from .training import training_env_dir

        paths = getattr(self.settings, "paths", None) if self.settings is not None else None
        data_dir = getattr(paths, "data_dir", None) or "data"
        return training_env_dir(data_dir)

    def _temp_run(self) -> bool:
        """Tell whether this exe runs from an archiver's temp folder."""
        return bool(getattr(sys, "frozen", False)) and is_temp_run(sys.executable)

    def _refresh_training_env(self) -> None:
        from .training import _detect_trainer, detect_training_env, env_problem

        self.training_reinstall_button.setVisible(False)
        if self._temp_run():
            self.training_env_label.setText(TEMP_RUN_TRAINING)
            self.training_install_button.setVisible(False)
            self._env_ready = False
            self._refresh_training_steps()
            return
        problem = env_problem(self._env_dir())
        if problem:
            self.training_env_label.setText(problem)
            self.training_install_button.setVisible(True)
            self._env_ready = False
            self._refresh_training_steps()
            return
        env_py = detect_training_env(self._env_dir())
        if env_py:
            self.training_env_label.setText(
                tr("Окружение обучения: готово (venv: {value})", value=Path(env_py).parent.parent.name)
            )
            self.training_install_button.setVisible(False)
            self.training_reinstall_button.setVisible(True)
            self._env_ready = True
            self._refresh_training_steps()
            return
        trainer, name, _ = _detect_trainer(lambda message: None)
        if trainer is not None:
            self.training_env_label.setText(
                tr("Окружение обучения: системное Python ({name})", name=name)
            )
            self.training_install_button.setVisible(False)
            self._env_ready = True
            self._refresh_training_steps()
            return
        self.training_env_label.setText(
            tr("Окружение обучения не найдено. Нажмите кнопку — программа сама "
            "проверит/установит Python 3.12 и развернёт venv "
            "(несколько ГБ: torch, unsloth, peft, trl).")
        )
        self.training_install_button.setVisible(True)
        self._env_ready = False
        self._refresh_training_steps()

    def reinstall_training_env(self) -> None:
        if self.env_installing or self.training_running:
            return
        answer = QMessageBox.question(
            self,
            tr("Переустановить окружение обучения?"),
            tr("Окружение обучения будет удалено и установлено заново: PyTorch с CUDA, "
            "unsloth, peft, trl. Это несколько ГБ и может занять больше часа "
            "(часть пакетов возьмётся из кэша pip).\n\nПродолжить?"),
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if answer == QMessageBox.Yes:
            self.install_training_env()

    def install_training_env(self) -> None:
        if self.env_installing:
            return
        if self._temp_run():
            self.training_view.setHtml(f"<p><b>{html.escape(TEMP_RUN_TRAINING)}</b></p>")
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
                tr("Python не найден"),
                tr("Для обучения нужен Python 3.12, он не найден на компьютере.\n\n"
                "Установить его автоматически через winget?\n"
                "Python ставится для текущего пользователя, без окон установщика и без "
                "запроса прав администратора; потребуется интернет."),
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.No,
            )
            if answer != QMessageBox.Yes:
                self._training_log_html(
                    tr("<b>Установка отменена: установите Python 3.11/3.12 вручную "
                    "и повторите.</b>")
                )
                return
            auto_install = True
        env_dir = self._env_dir()
        offline_wheels = find_offline_wheels()
        if offline_wheels is not None:
            self._training_log_html(
                tr("<b>Обнаружен офлайн-пак обучения: {value}</b>", value=html.escape(str(offline_wheels)))
            )
        self.env_installing = True
        self._start_activity(tr("Установка окружения обучения"))
        self.training_install_button.setEnabled(False)
        self.training_reinstall_button.setEnabled(False)
        self.training_view.setHtml(tr("<p><b>Установка окружения обучения…</b></p>"))
        self._note_training_log_path()
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
        self._stop_activity()
        self.training_install_button.setEnabled(True)
        self.training_reinstall_button.setEnabled(True)
        self._on_training_log(tr("Окружение обучения установлено."))
        self._refresh_training_env()

    def _on_env_install_failed(self, message: str) -> None:
        self.env_installing = False
        self._stop_activity()
        self.training_install_button.setEnabled(True)
        self.training_reinstall_button.setEnabled(True)
        self._training_log_html(f"<b>{html.escape(message)}</b>")
        self._refresh_training_env()  # a reinstall clears the old environment first

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
        if self._temp_run():
            self.training_view.setHtml(f"<p><b>{html.escape(TEMP_RUN_TRAINING)}</b></p>")
            return
        from licensing import feature_enabled

        if not feature_enabled("qlora"):
            self.training_view.setHtml(
                tr("<p><b>QLoRA-обучение — Pro-функция.</b></p>"
                "<p>Активируйте лицензию кнопкой «Лицензия» слева.</p>")
            )
            return
        if self.settings is None:
            self.training_view.setHtml(
                tr("<p>Движок не подключён. Запустите приложение через python -m desktop_browser.</p>")
            )
            return
        dataset = self.training_dataset.text().strip()
        if not dataset:
            self.training_view.setHtml(tr("<p><b>Укажите файл датасета (.jsonl).</b></p>"))
            return
        max_samples = self._confirm_dataset_size(dataset)
        if max_samples is None:
            return
        params = TrainingParams(
            dataset=dataset,
            preset=self.training_preset.currentData() or "low",
            epochs=self.training_epochs.value(),
            max_samples=max_samples,
            base_model=self.training_base_model.text().strip() or TrainingParams().base_model,
            adapter_name=self.training_adapter_name.text().strip() or TrainingParams().adapter_name,
            description=self.training_description.text().strip(),
            ollama_model_name=(
                self.training_ollama_model.text().strip() or TrainingParams().ollama_model_name
            ),
            export_gguf=self.training_export.isChecked(),
            preset_file=self._training_preset_path,
        )

        # One switch for the flag, the buttons and the activity bar: setting
        # the flag directly left the progress bar and the clock hidden.
        self._set_training_running(True)
        self.training_view.setHtml(tr("<p><b>Обучение запущено:</b> {value}</p>", value=html.escape(params.adapter_name)))
        self._write_training_log_file(tr("Обучение запущено: {adapter_name}", adapter_name=params.adapter_name))
        self._note_training_log_path()
        self._release_chat_model()
        pipeline = self.training_pipeline or self._default_training_pipeline()
        self.training_worker = TrainingWorker(pipeline, params)
        self.training_worker.log.connect(self._on_training_log)
        self.training_worker.done.connect(self._on_training_done)
        self.training_worker.failed.connect(self._on_training_failed)
        self.training_worker.finished.connect(self.training_worker.deleteLater)
        self.training_worker.start()

    def _release_chat_model(self) -> None:
        """Free the embedded chat model's memory for the trainer (reloads on the next message)."""
        unload = getattr(self.backend, "unload", None)
        if not callable(unload):
            return
        try:
            unload()
        except Exception:  # noqa: BLE001 — training must not fail because of the chat model
            return
        self._training_log_html(tr("Модель чата выгружена из памяти на время обучения."))

    def _confirm_dataset_size(self, dataset: str) -> int | None:
        """Offer to cap a huge dataset; returns max samples or None to cancel."""
        chosen = self.training_max_samples.value()
        if chosen:
            return chosen
        from finetune.dataset_loader import estimate_samples

        estimate = estimate_samples(dataset)
        if estimate <= LARGE_DATASET:
            return 0
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Question)
        box.setWindowTitle(tr("Большой датасет"))
        box.setText(
            tr("В датасете около {estimate:,} примеров.", estimate=estimate).replace(",", " ")
            + tr("\n\nНа одной видеокарте {value} эпох(и) по всему "
            "файлу займут очень долго (на 8 ГБ — сутки и больше), а подготовка данных "
            "потребует много оперативной памяти.\n\n"
            "Взять первые {SUGGESTED_SAMPLES:,} примеров? ", value=self.training_epochs.value(), SUGGESTED_SAMPLES=SUGGESTED_SAMPLES).replace(",", " ")
            + tr("Число можно поменять в поле «Макс. примеров».")
        )
        limit = box.addButton(tr("Взять {SUGGESTED_SAMPLES:,}", SUGGESTED_SAMPLES=SUGGESTED_SAMPLES).replace(",", " "), QMessageBox.AcceptRole)
        everything = box.addButton(tr("Весь файл"), QMessageBox.DestructiveRole)
        box.addButton(tr("Отмена"), QMessageBox.RejectRole)
        box.setDefaultButton(limit)
        box.exec()
        clicked = box.clickedButton()
        if clicked is limit:
            self.training_max_samples.setValue(SUGGESTED_SAMPLES)
            return SUGGESTED_SAMPLES
        if clicked is everything:
            return 0
        return None

    def stop_training(self) -> None:
        if self.training_worker is not None and self.training_running:
            self.training_worker.stop()
            self._on_training_log(tr("Остановка после текущего этапа…"))

    def _set_training_running(self, active: bool) -> None:
        self.training_running = active
        self.training_run_button.setEnabled(not active)
        self.training_stop_button.setVisible(active)
        if active:
            self._start_activity(tr("Обучение идёт"))
        else:
            self.training_worker = None
            self._stop_activity()
        self._refresh_training_steps()

    def _start_activity(self, title: str) -> None:
        """Show the moving bar and a live clock for a long training-page operation."""
        now = time.monotonic()
        self._activity_title = title
        self._activity_started = now
        self._activity_last = now
        self._open_training_log_file(title)
        self.training_busy.setVisible(True)
        self.training_activity.setVisible(True)
        self._activity_timer.start()
        self._tick_activity()

    def _open_training_log_file(self, title: str) -> None:
        """Mirror the training log to data/logs: it survives a crash of the app."""
        self._close_training_log_file()
        from .crash import crash_dir

        kind = "env-install" if title == tr("Установка окружения обучения") else "training"
        stamp = time.strftime("%Y%m%d-%H%M%S")
        try:
            path = crash_dir() / f"{kind}-{stamp}.log"
            self._training_log_file = path.open("a", encoding="utf-8")
        except OSError:
            self._training_log_file = None
            return
        self.training_log_path = path

    def _note_training_log_path(self) -> None:
        if getattr(self, "_training_log_file", None) is not None:
            self._training_log_html(
                tr("Журнал также пишется в файл: ") + html.escape(str(self.training_log_path))
            )

    def _close_training_log_file(self) -> None:
        handle = getattr(self, "_training_log_file", None)
        self._training_log_file = None
        if handle is not None:
            try:
                handle.close()
            except OSError:
                pass

    def _write_training_log_file(self, fragment: str) -> None:
        handle = getattr(self, "_training_log_file", None)
        if handle is None:
            return
        text = re.sub(r"<br\s*/?>", "\n", fragment)
        text = html.unescape(re.sub(r"<[^>]+>", "", text))  # tags first: &lt; is text
        try:
            handle.write(f"{time.strftime('%H:%M:%S')} {text}\n")
            handle.flush()
        except OSError:
            pass

    def _stop_activity(self) -> None:
        self._close_training_log_file()
        self._activity_timer.stop()
        self.training_busy.setVisible(False)
        self.training_activity.setVisible(False)

    @staticmethod
    def _format_span(seconds: float) -> str:
        seconds = int(seconds)
        if seconds < 60:
            return tr("{seconds} с", seconds=seconds)
        if seconds < 3600:
            return tr("{value} мин {value2:02d} с", value=seconds // 60, value2=seconds % 60)
        return tr("{value} ч {value2:02d} мин", value=seconds // 3600, value2=seconds % 3600 // 60)

    def _tick_activity(self) -> None:
        """Refresh the clock: total time and how long the log has been silent."""
        now = time.monotonic()
        quiet = now - self._activity_last
        text = (
            tr("{activity_title} · {value} · "
            "последняя строка журнала — {value2} назад", activity_title=self._activity_title, value=self._format_span(now - self._activity_started), value2=self._format_span(quiet))
        )
        if quiet >= ACTIVITY_QUIET_WARNING:
            text += (
                tr(". Долгий шаг без вывода (загрузка модели, сборка) — это нормально. "
                "Если журнал молчит больше 30 минут, нажмите «Стоп».")
            )
        self.training_activity.setText(text)

    def _on_training_log(self, message: str) -> None:
        """Append a plain-text line of the training or install log."""
        self._training_log_html(html.escape(message).replace("\n", "<br>"))

    def _training_log_html(self, fragment: str) -> None:
        """Append already formatted HTML to the training log.

        The fragment is wrapped in a tag: ``append`` guesses the format, and a
        line without tags was shown as plain text with ``&quot;`` and ``<b>``
        visible.
        """
        self._activity_last = time.monotonic()
        self._write_training_log_file(fragment)
        self.training_view.append(f"<span>{fragment}</span>")
        self._follow_scroll(self.training_view)

    def _on_training_done(self, result: dict) -> None:
        if result.get("cancelled"):
            self._on_training_log(tr("Остановлено пользователем."))
        parts = [
            tr("Адаптер: {value}", value=result.get('adapter', '—')),
            f"Merged: {result.get('merged', '—')}",
            f"GGUF: {result.get('gguf', '—')}",
            tr("Ollama: {value}", value=tr('создана') if result.get('created') else tr('не создавалась')),
            tr("Реестр: {value}", value=tr('зарегистрирован') if result.get('registered') else tr('нет')),
        ]
        self._training_log_html(tr("<b>Готово.</b> ") + " | ".join(html.escape(p) for p in parts))
        if result.get("registered") and result.get("ollama_model"):
            try:
                self._on_model_selected(result["ollama_model"])
            except Exception:
                pass
            self._load_models()
        self._set_training_running(False)

    def _on_training_failed(self, message: str) -> None:
        self._training_log_html(f"<b>{html.escape(message)}</b>")
        self._set_training_running(False)

    # ── model selection ─────────────────────────────────────────────────

    def _load_models(self) -> None:
        if self.backend is None:
            self._show_models_unavailable(tr("Движок не подключён"))
            return
        if self._is_cloud_backend():
            self._show_models_unavailable(tr("Облачная модель: сменить её можно на странице «Модели»"))
            return
        if self._is_embedded_backend():
            self._show_local_models()
            return
        if self._model_worker is not None:
            return
        self.model_combo.setEnabled(False)
        self.model_combo.setToolTip(tr("Загрузка списка моделей..."))
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
        if self._is_cloud_backend():
            model = str(getattr(self.backend, "model", "") or model)
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
        self.model_combo.setToolTip(tr("Модель Ollama для чата и агента"))

    def _on_models_failed(self, message: str) -> None:
        self._show_models_unavailable(message)

    def _is_embedded_backend(self) -> bool:
        """The built-in llama.cpp engine: it runs a GGUF file, not an Ollama model."""
        return getattr(self.backend, "client", None) is None and isinstance(
            getattr(self.backend, "model_path", None), str
        )

    def _local_chat_models(self) -> list[tuple[str, str]]:
        """(name, path) of the chat models in the models folder: downloaded on
        the "Модели" page or imported; the code-search model is left out."""
        from core.model_manager import ModelManager

        try:
            mm = getattr(self, "model_mgr", None) or ModelManager()
            catalog = {m.filename.lower(): m for m in mm.catalog()}
            installed = mm.list_installed()
        except Exception:
            return []
        models = []
        for item in installed:
            entry = catalog.get(item.filename.lower())
            if (entry.task if entry is not None else item.task) != "chat":
                continue
            models.append((entry.name if entry is not None else item.filename, str(item.path)))
        return models

    @staticmethod
    def _same_path(a: str, b: str) -> bool:
        return bool(a and b) and os.path.normcase(os.path.abspath(a)) == os.path.normcase(os.path.abspath(b))

    def _show_local_models(self) -> None:
        models = self._local_chat_models()
        current = str(getattr(self.backend, "model_path", "") or "")
        if current and Path(current).is_file() and not any(self._same_path(current, p) for _n, p in models):
            models.insert(0, (Path(current).name, current))  # e.g. a gguf_path from config.yaml
        self._applying_model = True
        self.model_combo.blockSignals(True)
        self.model_combo.clear()
        for name, path in models:
            self.model_combo.addItem(name, path)
            if self._same_path(current, path):
                self.model_combo.setCurrentIndex(self.model_combo.count() - 1)
        self.model_combo.blockSignals(False)
        self._applying_model = False
        self.model_combo.setEnabled(bool(models))
        self.model_combo.setToolTip(
            tr("Модель для чата и агента") if models else tr("Скачайте модель на странице «Модели»")
        )

    def _set_gguf_path(self, path: str) -> None:
        self.settings.gguf_path = path
        engine_settings = getattr(self.backend, "settings", None)
        if engine_settings is not None and engine_settings is not self.settings:
            engine_settings.gguf_path = path  # the engine loads it on the next request

    def _on_local_model_selected(self) -> None:
        path = str(self.model_combo.currentData() or "")
        if self.generating or self.agent_running:
            self._show_local_models()  # no switch mid-answer: show the model in use
            return
        if not path or self.settings is None:
            return
        if self._same_path(path, str(getattr(self.backend, "model_path", "") or "")):
            return
        self._set_gguf_path(path)
        self.qsettings.setValue("gguf_path", path)
        self._start_status_check()

    def _on_model_selected(self, name: str) -> None:
        if self._is_embedded_backend():
            self._on_local_model_selected()
            return
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
                tr("{line}. Нажмите «Состояние» слева внизу — там причина и кнопка «Исправить».", line=line)
            )

    def _append_banner(self, text: str) -> None:
        """Zero Silence (14.2): visible explanation instead of silent degradation."""
        self._messages.append({"role": "banner", "text": text, "meta": ""})
        self._render_messages()

    def _start_status_check(self) -> None:
        if self.assistant is None:
            self.status_label.setText(tr("Движок: не подключён"))
            return
        model = self._status_model_name()
        self.status_label.setText(tr("Модель: {model} — проверка...", model=model))
        if self.backend is None:
            return
        # parented: a new check replaces self.status_worker while the old one may
        # still run, and a parentless running QThread dropped by Python kills the app
        worker = StatusWorker(self.backend, self)
        self.status_worker = worker
        worker.result.connect(self._on_status_result)
        worker.finished.connect(lambda: self._on_status_worker_finished(worker))
        worker.start()

    def _on_status_worker_finished(self, worker) -> None:
        # forget it before deleting: closeEvent waits on self.status_worker
        if self.status_worker is worker:
            self.status_worker = None
        worker.deleteLater()

    def _status_model_name(self) -> str:
        """The model the engine actually uses: the GGUF file for llama.cpp
        (the "model" setting is an Ollama name and misled on llama.cpp)."""
        if self._is_cloud_backend():
            return tr("{value} (облако)", value=getattr(self.backend, 'model', ''))
        path = getattr(self.backend, "model_path", None)
        if isinstance(path, str):
            if path and Path(path).is_file():
                return Path(path).name
            return tr("не скачана (страница «Модели»)")
        return getattr(self.settings, "model", "")

    def _on_status_result(self, available: bool) -> None:
        model = self._status_model_name()
        state = "online" if available else "offline"
        text = tr("Модель: {model} — {state}", model=model, state=state) if model else tr("Движок: {state}", state=state)
        self.status_label.setText(text)
        self.status_label.setProperty("state", state)
        self.style().unpolish(self.status_label)
        self.style().polish(self.status_label)
        if hasattr(self, "banners"):
            if available:
                self.banners.clear()
            else:
                self.banners.show_warning(
                    tr("Модель недоступна: проверь Ollama или движок."),
                    tr("Повторить"),
                    self._load_models,
                )

    # ── chat flow ────────────────────────────────────────────────────────

    def send_message(self) -> None:
        """Send the prompt: to the agent when a chip asks for it, else to the chat."""
        self._submit(agent_once=False)

    def send_agent_once(self) -> None:
        """Ctrl+Enter: run this one message through the agent; chips stay as they are."""
        self._submit(agent_once=True)

    def _submit(self, agent_once: bool) -> None:
        text = self.prompt.text().strip()
        if not text or self.generating or self.agent_running:
            return
        source = None
        question = archive_command(text)
        if question is not None:
            if not question:
                self.prompt.clear()
                self._open_archive()
                return
            source = self._archive_source()
            if source is None:
                return
        drawing = acad_command(text) if source is None else None
        if drawing is not None:
            self.prompt.clear()
            if drawing:
                self.start_acad_check(drawing)
            else:
                self._open_acad_check()
            return
        calc_question = calc_command(text) if source is None else None
        if calc_question is not None:
            self.prompt.clear()
            if calc_question:
                self._send_calc_message(calc_question, shown=text)
            else:
                self._open_calc()
            return
        if self._calc_conversation is not None and source is None:
            # the answer to Fluxion's question about the calculation
            self.prompt.clear()
            self._send_calc_message(text, shown=text, answer=True)
            return
        description = cad_command(text)
        if description is not None:
            if not description:
                self._open_cad_dialog()
                return
            self.prompt.clear()
            self._send_cad_message(description, shown=text)
            return
        if self._cad_conversation is not None and source is None:
            # the answer to Fluxion's question about the part
            self.prompt.clear()
            self._send_cad_message(text, shown=text, answer=True)
            return
        wants_agent = source is None and (agent_once or self.chat_agent_files.isChecked()
                                          or self.chat_plan.isChecked())
        if (self.agent_factory if wants_agent else self.assistant) is None:
            self._append_message("assistant", tr("Движок не подключён. Запустите приложение через python -m desktop_browser."))
            return
        self.prompt.clear()
        if wants_agent:
            self._send_agent_message(text, plan=self.chat_plan.isChecked())
            return

        history = list(self.history)
        self._last_query = text
        self._web_opened_for_current = False
        self._append_message("user", text)
        self.history.append({"role": "user", "content": text})
        self._append_message("assistant", "")
        self._start_thinking()

        self._set_generating(True)
        if self.assistant is not None:
            self.assistant.use_project = not self._project_forgotten()
        self.worker = ChatWorker(self.assistant, question or text, history, self)
        self.worker.target = resolve_target(getattr(self.settings, "language", "auto"), text)
        self.worker.style = self._writing_style_block(text)
        self.worker.source = source
        self.worker.token.connect(self._on_token)
        self.worker.meta.connect(self._on_meta)
        self.worker.failed.connect(self._on_failed)
        self.worker.finished_ok.connect(self._on_done)
        self.worker.text_reset.connect(self._on_text_reset)
        self.worker.finished.connect(self.worker.deleteLater)
        self.worker.start()

    # ── «Пакет на лазер»: DXF of the sheet details by thickness (core of DXFka) ──

    def _open_laser_package(self) -> None:
        """Menu Инструменты → Пакет на лазер: pick an .a3d and the DXF folder."""
        from PySide6.QtWidgets import QFileDialog

        from cad.laser import PRO_FEATURE, default_out_dir
        from licensing import feature_enabled

        if self.generating or self.agent_running or getattr(self, "laser_job", None) is not None:
            return
        if not feature_enabled(PRO_FEATURE):
            QMessageBox.information(
                self, tr("Пакет на лазер"),
                tr("«Пакет на лазер» — функция Pro: DXF развёрток всех листовых деталей сборки, "
                "разложенные по папкам толщин.\nАктивировать: меню «Помощь» → «Лицензия…»."))
            return
        start = str(self.qsettings.value("report_dir", "") or "")
        path, _ = QFileDialog.getOpenFileName(self, tr("Сборка КОМПАС-3D"), start, tr("Сборки КОМПАС-3D (*.a3d)"))
        if not path:
            return
        self.qsettings.setValue("report_dir", str(Path(path).parent))
        out = QFileDialog.getExistingDirectory(self, tr("Папка для DXF (подпапки по толщинам создаются сами)"),
                                               str(default_out_dir(path).parent))
        if not out:
            return
        self.start_laser_package(path, str(Path(out) / Path(path).stem))  # one subfolder per assembly

    def start_laser_package(self, assembly: str, out_dir: str, auto: bool = True) -> None:
        from cad.laser import LaserJob

        self.pages.setCurrentIndex(PAGE_CHAT)
        self._append_message("user", tr("Пакет на лазер: {value}", value=Path(assembly).name))
        self._append_message("assistant", tr("Обхожу сборку…"))
        self._laser_entry = self._messages[-1]
        self._laser_entry["meta"] = tr("[КОМПАС-3D | DXFka | автоматический режим]") if auto else tr("[КОМПАС-3D | DXFka]")
        self._set_generating(True)
        job = LaserJob(assembly, out_dir, auto=auto)
        self.laser_job = job
        self._laser_lines: list[str] = []
        self._laser_timer = QTimer(self)
        self._laser_timer.setInterval(150)
        self._laser_timer.timeout.connect(self._poll_laser)
        self._laser_timer.start()
        job.start()

    def _laser_say(self, text: str, replace_last: bool = False) -> None:
        if replace_last and self._laser_lines:
            self._laser_lines[-1] = text
        else:
            self._laser_lines.append(text)
        self._laser_entry["text"] = "\n".join(self._laser_lines[-40:])
        self._render_messages()

    def _poll_laser(self) -> None:
        """Messages of the DXFka worker: progress into the chat, questions as dialogs."""
        import queue as queue_module

        job = getattr(self, "laser_job", None)
        if job is None:
            return
        while True:
            try:
                msg = job.queue.get_nowait()
            except queue_module.Empty:
                break
            kind = msg.get("type")
            if kind == "LOG" and msg.get("level") in ("warning", "error"):
                self._laser_say(("⚠ " if msg["level"] == "warning" else "✖ ") + msg["text"])
            elif kind == "PROGRESS" and msg.get("total"):
                line = tr("Деталь {done} из {total}", done=msg["done"], total=msg["total"])
                replace = bool(self._laser_lines) and self._laser_lines[-1] == getattr(self, "_laser_progress", None)
                self._laser_progress = line
                self._laser_say(line, replace_last=replace)
            elif kind == "TRAVERSAL_DONE":
                count = job.export(msg.get("parts") or [])
                self._laser_say(tr("В сборке деталей: {value}; на лазер (из листа): "
                                "{count}; остальные пропущены.", value=len(job.parts) + len(job.left_out), count=count))
                if not count:
                    self._finish_laser(tr("Листовых деталей в сборке нет — DXF не нужен."))
                    return
            elif kind == "PAUSE":
                self._laser_pause(msg.get("title", ""), msg.get("instruction", ""))
            elif kind == "ASK_THICKNESS":
                value, ok = QInputDialog.getText(
                    self, tr("Толщина детали"), tr("{value}\nТолщина, мм (пусто — пропустить деталь):", value=msg.get('part', '')),
                    text=msg.get("default", ""))
                job.answer_thickness(value if ok else None)
            elif kind == "DONE":
                self._finish_laser(msg.get("text", tr("Экспорт завершён.")))
                return
            elif kind == "FATAL":
                self._finish_laser("✖ " + msg.get("text", tr("Ошибка DXFka.")))
                return

    def _laser_pause(self, title: str, instruction: str) -> None:
        """Manual step of DXFka: the designer checks the view in KOMPAS, then goes on."""
        box = QMessageBox(self)
        box.setWindowTitle(tr("Пакет на лазер"))
        box.setText(title)
        box.setInformativeText(instruction)
        go = box.addButton(tr("Продолжить"), QMessageBox.AcceptRole)
        skip = box.addButton(tr("Пропустить деталь"), QMessageBox.RejectRole)
        box.addButton(tr("Остановить"), QMessageBox.DestructiveRole)
        box.exec()
        clicked = box.clickedButton()
        job = self.laser_job
        if clicked is go:
            job.resume()
        elif clicked is skip:
            job.skip()
        else:
            job.cancel()

    def _finish_laser(self, text: str) -> None:
        from cad.laser import thickness_summary

        job = self.laser_job
        self._laser_timer.stop()
        summary = thickness_summary(job.out_dir)
        lines = [text]
        if summary:
            lines.append(tr("По толщинам: ") + "; ".join(tr("{name} мм — {count} DXF", name=name, count=count) for name, count in summary) + ".")
            lines.append(tr("Папка: {out_dir}", out_dir=job.out_dir))
        self._laser_lines.append("\n".join(lines))
        self._laser_entry["text"] = "\n".join(self._laser_lines[-40:])
        if summary:
            self._laser_entry["sources"] = [{"file": job.out_dir, "label": tr("Открыть папку с DXF")}]
            self._laser_entry["sources_title"] = tr("Ссылки")
        self.laser_job = None
        self._set_generating(False)
        self._render_messages()
        self._save_memory()
        self._notify_answer(lines[0])

    def _pro_feature(self, feature: str, title: str, text: str) -> bool:
        """Is *feature* of Fluxion Pro on? If not, say what it is and how to activate it."""
        from licensing import feature_enabled

        if feature_enabled(feature):
            return True
        QMessageBox.information(self, title, text + tr("\nАктивировать: меню «Помощь» → «Лицензия…»."))
        return False

    def _report_pro(self) -> bool:
        from cad.report import PRO_FEATURE

        return self._pro_feature(PRO_FEATURE, tr("Ведомость и металлоёмкость"), tr("«Ведомость и металлоёмкость сборки» — функция Pro: сводная ведомость, металлоёмкость, сварка, покраска и трудоёмкость сборки КОМПАС-3D в Excel, нормоконтроль перед отчётом, «Исправить в модели» и «Задать материал»."))

    def _acad_pro(self) -> bool:
        from cad.acad_check import PRO_FEATURE

        return self._pro_feature(PRO_FEATURE, tr("Нормоконтроль AutoCAD"), tr("«Нормоконтроль чертежа AutoCAD» — функция Pro: штамп, масштабы, шрифты, слои и размеры чертежа DWG/DXF по ЕСКД и СПДС, замечания в Excel."))

    def _archive_pro(self) -> bool:
        from rag.archive import PRO_FEATURE

        return self._pro_feature(PRO_FEATURE, tr("Архив КД"),
                                 tr("«Архив КД» — функция Pro: поиск по архиву КД — чертежам, спецификациям и "
                                 "моделям КОМПАС-3D, PDF и документам — с ответом и ссылками на файлы."))

    def _open_archive(self) -> None:
        """Menu Инструменты → Архив КД: the archive folders and the update of its index."""
        if not self._archive_pro():
            return
        from .archive_dialog import ArchiveDialog

        ArchiveDialog(self._style_dir(), self).exec()

    def _archive_source(self):
        """The archive index for an «/архив» question, or None (no Pro, nothing indexed yet)."""
        if not self._archive_pro():
            return None
        from rag.archive import ArchiveIndex

        index = ArchiveIndex(self._style_dir())
        if not index.file_count:
            index.close()
            self.prompt.clear()
            self._append_message("assistant", tr("Архив КД ещё не проиндексирован: Инструменты → «Архив КД (Pro)…», "
                                              "добавьте папки архива и нажмите «Обновить индекс»."))
            return None
        return index

    def _open_assembly_report(self) -> None:
        """Menu Инструменты → Ведомость и металлоёмкость сборки: pick an .a3d, report in Excel."""
        from PySide6.QtWidgets import QFileDialog

        if self.generating or self.agent_running or getattr(self, "report_worker", None) is not None:
            return
        if not self._report_pro():
            return
        start = str(self.qsettings.value("report_dir", "") or "")
        path, _ = QFileDialog.getOpenFileName(self, tr("Сборка КОМПАС-3D"), start, tr("Сборки КОМПАС-3D (*.a3d)"))
        if path:
            self.qsettings.setValue("report_dir", str(Path(path).parent))
            self.start_assembly_report(path)

    def start_assembly_report(self, path: str) -> None:
        if not self._report_pro():
            return
        self.pages.setCurrentIndex(PAGE_CHAT)
        self._append_message("user", tr("Ведомость и металлоёмкость: {value}", value=Path(path).name))
        self._append_message("assistant", "")
        entry = self._messages[-1]
        entry["meta"] = tr("[КОМПАС-3D | сборка открыта только для чтения]")
        self._report_entry = entry
        self._set_generating(True)
        self._run_report_worker(path)

    def _run_report_worker(self, path: str, fixes=None) -> None:
        worker = AssemblyReportWorker(path, self, fixes)
        self.report_worker = worker
        worker.progress.connect(self._on_report_progress)
        worker.ready.connect(self._on_report_ready)
        worker.failed.connect(self._on_report_failed)
        worker.finished.connect(lambda: self._on_report_worker_finished(worker))
        worker.start()

    def _on_report_progress(self, text: str) -> None:
        entry = self._report_entry
        entry["text"] = (entry["text"] + "\n" if entry["text"] else "") + text
        self._render_messages()

    def _mark_warnings(self, result) -> tuple[bool, dict]:
        """The disputed places, each to be marked: (made, the marks)."""
        dialog = ReportWarningsDialog(result, self)
        made = dialog.exec() == QDialog.Accepted
        self._pending_fixes = dialog.fixes if dialog.fix_requested else None
        return made, dialog.marks()

    def _on_report_ready(self, result) -> None:
        from cad import report

        source = Path(result.top.file).name if result.top.file else ""
        if result.remarks:
            made, marks = self._mark_warnings(result)
            if result.top.file:  # marked once, not asked again
                report.save_decisions(result.top.file, report.decisions_from_marks(result, marks))
            fixes, self._pending_fixes = getattr(self, "_pending_fixes", None), None
            if fixes and result.top.file:  # the designer agreed: fix the model, then the report again
                self._report_entry["text"] = tr("Исправляю модель…")
                self._render_messages()
                self._run_report_worker(result.top.file, fixes)
                return
            if not made:
                left = sum(1 for m in marks.values() if not m["checked"])
                self._report_entry["text"] = (
                    tr("Отчёт не сформирован: не отмечено {left} из {n_marks} спорных мест. Отметки сохранены.\n", left=left, n_marks=len(marks))
                    + report.warnings_text(result))
                self._report_entry["sources"] = report.report_links(result)
                self._report_entry["sources_title"] = tr("Модели")
                self._set_generating(False)
                self._render_messages()
                return
            if report.purchase_changed(result, marks):
                # "bought or made here" changes the metal report and the welds: build it again
                self._report_entry["text"] = tr("Пересчитываю с вашими отметками…")
                self._render_messages()
                self._run_report_worker(result.top.file)
                return
            for r in result.remarks:
                r["checked"] = marks.get(r["id"], {}).get("checked", r["checked"])
        try:
            path = str(report.save_xlsx(result, report.default_path(result.top), source=source))
        except Exception as exc:  # the workbook open in Excel, no rights to the folder
            self._on_report_failed(tr("Не удалось сохранить отчёт: {exc}", exc=exc))
            return
        summary = report.summary_text(result)
        self._report_entry["text"] = summary + tr("\nОтчёт: {path}", path=path)
        self._report_entry["sources"] = report.report_links(result, path)
        self._report_entry["sources_title"] = tr("Ссылки")
        self._set_generating(False)
        self._render_messages()
        self._save_memory()
        self._notify_answer(summary)
        QDesktopServices.openUrl(QUrl.fromLocalFile(path))

    def _on_report_failed(self, message: str) -> None:
        self._report_entry["text"] = message
        self._set_generating(False)
        self._render_messages()

    def _on_report_worker_finished(self, worker) -> None:
        # forget it before deleting: closeEvent waits on self.report_worker
        if getattr(self, "report_worker", None) is worker:
            self.report_worker = None
        worker.deleteLater()

    # ── «Мой стиль»: memos, explanatory notes and letters in the user's manner ──

    def _style_dir(self) -> str:
        """Where «Мой стиль» keeps the profile: the data folder of the settings."""
        paths = getattr(self.settings, "paths", None)
        return getattr(paths, "data_dir", None) or "data"

    def _writing_style_pro(self) -> bool:
        from licensing import feature_enabled
        from orchestrator.writing_style import PRO_FEATURE

        if feature_enabled(PRO_FEATURE):
            return True
        QMessageBox.information(
            self, tr("Мой стиль письма"),
            tr("«Мой стиль» — функция Pro: служебные и пояснительные записки, письма заказчикам "
            "в вашей манере по вашим образцам.\nАктивировать: меню «Помощь» → «Лицензия…»."))
        return False

    def _open_writing_style(self) -> bool:
        """Menu Инструменты → Мой стиль письма: the samples and the profile. True when saved."""
        if not self._writing_style_pro():
            return False
        from .writing_style_dialog import WritingStyleDialog

        return WritingStyleDialog(self.backend, self._style_dir(), self).exec() == QDialog.Accepted

    def _on_chat_style_toggled(self, checked: bool) -> None:
        if not checked:
            return
        from orchestrator import writing_style

        ready = self._writing_style_pro() and (
            writing_style.load(self._style_dir()).ready or self._open_writing_style())
        if not ready or not writing_style.load(self._style_dir()).ready:
            self.chat_style.blockSignals(True)
            self.chat_style.setChecked(False)
            self.chat_style.blockSignals(False)

    def _writing_style_block(self, query: str) -> str:
        """The user's manner for the system prompt while «✍ Мой стиль» is on ("" otherwise)."""
        chip = getattr(self, "chat_style", None)
        if chip is None or not chip.isChecked():
            return ""
        from licensing import feature_enabled
        from orchestrator import writing_style

        if not feature_enabled(writing_style.PRO_FEATURE):
            return ""
        return writing_style.prompt_block(writing_style.load(self._style_dir()), query)

    def manual_tools(self) -> dict:
        """The tools «Открыть инструмент» of the manual opens, by the names its pages use."""
        return {
            "models": lambda: self.pages.setCurrentIndex(PAGE_MODELS),
            "license": self._open_license_dialog,
            "project": lambda: self.pages.setCurrentIndex(PAGE_PROJECT),
            "training": lambda: self.pages.setCurrentIndex(PAGE_TRAINING),
            "dataset": self._make_dataset_from_docs,
            "writing_style": self._open_writing_style,
            "cad": self._open_cad_dialog,
            "materials": self._open_materials_dialog,
            "assembly_report": self._open_assembly_report,
            "laser": self._open_laser_package,
            "acad": self._open_acad_check,
            "archive": self._open_archive,
            "calc": self._open_calc,
        }

    def open_manual(self, page: str = "") -> None:
        """Menu Помощь → Инструкция по инструментам (F1): desktop_browser/manual."""
        from .manual import open_manual

        open_manual(self, page, self.manual_tools())

    def _open_acad_check(self) -> None:
        """Menu Инструменты → Нормоконтроль чертежа AutoCAD: pick a DWG/DXF, remarks, report in Excel."""
        from PySide6.QtWidgets import QFileDialog

        from .acad_check_dialog import ACAD_FILTER

        if self.generating or self.agent_running or getattr(self, "acad_worker", None) is not None:
            return
        if not self._acad_pro():
            return
        start = str(self.qsettings.value("acad_dir", "") or "")
        path, _ = QFileDialog.getOpenFileName(self, tr("Чертёж AutoCAD"), start, ACAD_FILTER)
        if path:
            self.start_acad_check(path)

    def start_acad_check(self, path: str) -> None:
        from .acad_check_dialog import AcadCheckWorker

        if self.generating or self.agent_running or getattr(self, "acad_worker", None) is not None:
            return
        if not self._acad_pro():
            return
        self.qsettings.setValue("acad_dir", str(Path(path).parent))
        self.pages.setCurrentIndex(PAGE_CHAT)
        self._append_message("user", tr("Нормоконтроль: {value}", value=Path(path).name))
        self._append_message("assistant", "")
        self._acad_entry = self._messages[-1]
        self._acad_entry["meta"] = tr("[AutoCAD | чертёж открыт только для чтения]")
        self._set_generating(True)
        worker = AcadCheckWorker(path, self)
        self.acad_worker = worker
        worker.progress.connect(self._on_acad_progress)
        worker.ready.connect(self._on_acad_ready)
        worker.failed.connect(self._on_acad_failed)
        worker.finished.connect(lambda: self._on_acad_worker_finished(worker))
        worker.start()

    def _on_acad_progress(self, text: str) -> None:
        entry = self._acad_entry
        entry["text"] = (entry["text"] + "\n" if entry["text"] else "") + text
        self._render_messages()

    def _on_acad_ready(self, result) -> None:
        from cad import acad_check, report

        from .acad_check_dialog import AcadWarningsDialog, report_links

        entry = self._acad_entry
        if result.remarks:
            dialog = AcadWarningsDialog(result, self)
            made = dialog.exec() == QDialog.Accepted
            marks = dialog.marks()
            report.save_decisions(result.drawing.file, marks)  # marked once, not asked again
            if not made:
                left = sum(1 for m in marks.values() if not m["checked"])
                entry["text"] = (tr("Отчёт не сформирован: не отмечено {left} из {n_marks} замечаний. "
                                 "Отметки сохранены.\n", left=left, n_marks=len(marks)) + acad_check.warnings_text(result))
                entry["sources"] = report_links(result)
                entry["sources_title"] = tr("Ссылки")
                self._set_generating(False)
                self._render_messages()
                return
            for r in result.remarks:
                r["checked"] = marks.get(r["id"], {}).get("checked", r["checked"])
        try:
            path = acad_check.save_xlsx(result, acad_check.default_path(result))
        except Exception as exc:  # the workbook open in Excel, no rights to the folder
            self._on_acad_failed(tr("Не удалось сохранить отчёт: {exc}", exc=exc))
            return
        summary = acad_check.summary_text(result)
        entry["text"] = summary + tr("\nОтчёт: {path}", path=path)
        entry["sources"] = report_links(result, path)
        entry["sources_title"] = tr("Ссылки")
        self._set_generating(False)
        self._render_messages()
        self._save_memory()
        self._notify_answer(summary)
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(path)))

    def _on_acad_failed(self, message: str) -> None:
        self._acad_entry["text"] = message
        self._set_generating(False)
        self._render_messages()

    def _on_acad_worker_finished(self, worker) -> None:
        if getattr(self, "acad_worker", None) is worker:
            self.acad_worker = None
        worker.deleteLater()

    def _open_cad_dialog(self) -> None:
        """Menu Инструменты → Деталь в КОМПАС: the text of the chat input, or ask for it."""
        if self.generating or self.agent_running:
            return
        text = self.prompt.text().strip()
        description = cad_command(text)
        if description is None:
            description = text
        if not description:
            from .text_dialog import ask_text

            description, ok = ask_text(
                self,
                tr("Деталь в КОМПАС-3D"),
                tr("Опишите деталь, размеры в мм и материал — модель выберет шаблон "
                "(пластина, фланец, втулка, вал, уголок, гайка; из листа со сгибами: уголок, кронштейн, "
                "П- и Z-профиль, лоток или короб, полка с отбортовкой), КОМПАС построит её, "
                "для листовых — и развёртку в DXF.\n"
                "Чего не хватит, Fluxion спросит в чате.\n")
                + CAD_HINT,
            )
            description = description.strip()
            if not ok or not description:
                return
        else:
            self.prompt.clear()
        self._send_cad_message(description, shown=f"/компас {description}")

    def _open_calc(self, start: tuple[str, dict] | None = None) -> None:
        """Инструменты → Инженерный калькулятор; after «/расчёт» in the chat — with that calculation filled in."""
        from .calc_dialog import CalcDialog

        CalcDialog(self, start or self._last_calc).exec()

    def _send_calc_message(self, message: str, shown: str, answer: bool = False) -> None:
        """«/расчёт …»: the model reads the question, the formulas of the package calc count."""
        self.pages.setCurrentIndex(PAGE_CHAT)
        self._last_query = shown
        self._append_message("user", shown)
        self.history.append({"role": "user", "content": shown})
        if self.backend is None:
            self._calc_conversation = None
            self._append_message("assistant", tr("Движок не подключён. Запустите приложение через python -m desktop_browser."))
            return
        if not answer or self._calc_conversation is None:
            from calc.chat import Conversation

            self._calc_conversation = Conversation(self.backend)
        self._append_message("assistant", "")
        self._calc_entry = self._messages[-1]
        self._calc_entry["meta"] = tr("[Инженерный калькулятор | расчёт по формулам]")
        self._start_thinking()
        self._set_generating(True)
        self.calc_worker = CalcWorker(self._calc_conversation, message, self)
        self.calc_worker.asked.connect(self._on_calc_asked)
        self.calc_worker.done.connect(self._on_calc_done)
        self.calc_worker.failed.connect(self._on_calc_failed)
        self.calc_worker.finished.connect(self.calc_worker.deleteLater)
        self.calc_worker.start()

    def _on_calc_asked(self, question: str) -> None:
        self._calc_entry["text"] = question
        self.history.append({"role": "assistant", "content": question})
        self._notify_answer()
        self.calc_worker = None
        self._set_generating(False)
        self.prompt.setPlaceholderText(tr("Ответ про расчёт: недостающие данные… или «отмена»"))
        self._render_messages()
        self._save_memory()

    def _on_calc_done(self, text: str, calc_kind: str, params: dict) -> None:
        if calc_kind:
            self._last_calc = (calc_kind, params)
            text += tr("\nФормулы и отчёт: Инструменты → «Инженерный калькулятор…» — расчёт уже заполнен.")
        self._calc_entry["text"] = text
        self.history.append({"role": "assistant", "content": text})
        self._notify_answer(text.splitlines()[0])
        self._end_calc_turn()

    def _on_calc_failed(self, message: str) -> None:
        self._calc_entry["text"] = message
        self._end_calc_turn()

    def _end_calc_turn(self) -> None:
        self.calc_worker = None
        self._calc_conversation = None
        self._set_generating(False)
        self._render_messages()
        self._save_memory()

    def _open_materials_dialog(self) -> None:
        """Инструменты → Каталог материалов: the merged catalog, the user's file, import."""
        dialog = MaterialsDialog(self)
        dialog.exec()

    def _send_cad_message(self, message: str, shown: str, answer: bool = False) -> None:
        """Part in KOMPAS-3D: *message* starts a new description or, with *answer*,
        answers Fluxion's question; the model and KOMPAS work in the background."""
        self.pages.setCurrentIndex(PAGE_CHAT)
        self._last_query = shown
        self._append_message("user", shown)
        self.history.append({"role": "user", "content": shown})
        if self.backend is None:
            self._cad_conversation = None
            self._append_message("assistant", tr("Движок не подключён. Запустите приложение через python -m desktop_browser."))
            return
        if not answer or self._cad_conversation is None:
            from cad.text2cad import Conversation

            self._cad_conversation = Conversation(self.backend)
        self._append_message("assistant", "")
        # banners may be added to the chat meanwhile: keep the answer's entry
        self._cad_entry = self._messages[-1]
        self._cad_entry["meta"] = tr("[КОМПАС-3D]")
        self._start_thinking()

        self._set_generating(True)
        self.cad_worker = CadWorker(self._cad_conversation, message, self)
        self.cad_worker.progress.connect(self._on_cad_progress)
        self.cad_worker.asked.connect(self._on_cad_asked)
        self.cad_worker.done.connect(self._on_cad_done)
        self.cad_worker.failed.connect(self._on_cad_failed)
        self.cad_worker.finished.connect(self.cad_worker.deleteLater)
        self.cad_worker.start()

    def _add_cad_line(self, text: str) -> dict:
        entry = self._cad_entry
        entry["text"] = (entry["text"] + "\n" if entry["text"] else "") + text
        return entry

    def _on_cad_progress(self, text: str) -> None:
        self._add_cad_line(text)
        self._render_messages()

    def _mark_cad_source(self) -> None:
        """"без модели" in the meta line when the rules read the description (cad/quick.py)."""
        draft = getattr(self._cad_conversation, "draft", None)
        if draft is not None and getattr(draft, "source", "") == "rules":
            self._cad_entry["meta"] = tr("[КОМПАС-3D | разобрано без модели]")

    def _on_cad_asked(self, question: str) -> None:
        """Fluxion asks for what is missing; the next chat message is the answer."""
        self._mark_cad_source()
        entry = self._add_cad_line(question)
        self.history.append({"role": "assistant", "content": entry["text"]})
        self._notify_answer()
        self.cad_worker = None
        self._set_generating(False)
        self.prompt.setPlaceholderText(tr("Ответ про деталь: размеры, материал… или «отмена»"))
        self._render_messages()
        self._save_memory()

    def _on_cad_done(self, text: str) -> None:
        self._mark_cad_source()
        entry = self._add_cad_line(text)
        self.history.append({"role": "assistant", "content": entry["text"]})
        self._notify_answer(text)  # read aloud the result, not every size of the template again
        self._end_cad_turn()

    def _on_cad_failed(self, message: str) -> None:
        self._add_cad_line(message)
        self._end_cad_turn()

    def _end_cad_turn(self) -> None:
        self.cad_worker = None
        self._cad_conversation = None
        self._set_generating(False)  # also puts back the usual placeholder
        self._render_messages()
        self._save_memory()

    # ── voice: input (faster-whisper) and reading answers aloud (SAPI) ────

    def toggle_voice_input(self) -> None:
        """Microphone button / Ctrl+Space: start recording, or stop it and recognise what was said."""
        if self._hands_free:
            self.hands_free_button.setChecked(False)  # the microphone takes the mode off
            return
        self._start_listening()

    def _start_listening(self, wait_seconds: float | None = None) -> bool:
        if self.voice_worker is not None:
            self.voice_worker.stop()
            return False
        from voice.models import is_installed, model_path, speech_model

        model_id = voice_model_id(self.qsettings)
        if not is_installed(model_id):
            self._append_banner(
                tr("Для голосового ввода нужна модель распознавания речи ({value}). "
                "Скачайте её: меню «Модели» (Ctrl+M) → раздел «Распознавание речи».", value=speech_model(model_id).name)
            )
            return False
        self._stop_speaking()  # do not record Fluxion's own voice
        worker = VoiceInputWorker(str(model_path(model_id)), self, wait_seconds=wait_seconds)
        self.voice_worker = worker
        worker.level.connect(self._on_voice_level)
        worker.recognizing.connect(self._on_voice_recognizing)
        worker.done.connect(self._on_voice_done)
        worker.failed.connect(self._on_voice_failed)
        worker.finished.connect(lambda: self._on_voice_worker_finished(worker))
        self._set_voice_recording(True)
        worker.start()
        return True

    def _set_voice_recording(self, active: bool, placeholder: str = "") -> None:
        self.mic_button.setProperty("recording", active)
        self._refresh_voice_icons()
        self.voice_level.setValue(0)
        self.voice_level.setVisible(active)
        if active:
            self._placeholder_before_voice = self.prompt.placeholderText()
            self.prompt.setPlaceholderText(tr("Говорите… пауза или Ctrl+Пробел — стоп"))
        else:
            self.prompt.setPlaceholderText(placeholder or getattr(self, "_placeholder_before_voice", ""))

    def _on_voice_level(self, level: float) -> None:
        self.voice_level.setValue(min(100, int(level * 600)))

    def _on_voice_recognizing(self) -> None:
        self.voice_level.setVisible(False)
        self.mic_button.setEnabled(False)
        self.prompt.setPlaceholderText(tr("Распознаю речь…"))

    def _on_voice_done(self, text: str) -> None:
        if self._hands_free:
            self._hands_free_heard(text)
            return
        if not text:
            self._set_voice_recording(False, tr("Не расслышал — нажмите микрофон и скажите ещё раз"))
            return
        self._set_voice_recording(False)
        current = self.prompt.text().strip()
        self.prompt.setText(f"{current} {text}" if current else spoken_command(text))
        self.prompt.setFocus()
        if self.voice_send_at_once.isChecked() and not self.generating:
            self.send_message()

    def _on_voice_failed(self, message: str) -> None:
        if self._hands_free:
            self.set_hands_free(False)
        self._set_voice_recording(False)
        self._append_banner(tr("Голосовой ввод: {message}", message=message))

    def _on_voice_worker_finished(self, worker) -> None:
        # forget it before deleting: closeEvent waits on self.voice_worker
        if self.voice_worker is worker:
            self.voice_worker = None
            self.mic_button.setEnabled(True)
            self.mic_button.setProperty("recording", False)
            self._refresh_voice_icons()
            self.voice_level.setVisible(False)
        worker.deleteLater()

    def set_hands_free(self, on: bool) -> None:
        """Hands-free voice mode: listen → send → read the answer aloud → listen again."""
        if on == self._hands_free:
            return
        if on:
            from voice.models import is_installed

            if not is_installed(voice_model_id(self.qsettings)):
                self.hands_free_button.setChecked(False)
                self._start_listening()  # explains where to get the speech model
                return
        self._hands_free = on
        self._hands_free_silent = 0
        if self.hands_free_button.isChecked() != on:
            self.hands_free_button.setChecked(on)
        if on:
            self._hands_free_timer.start()
            self._on_hands_free_tick()
        else:
            self._hands_free_timer.stop()
            self._stop_speaking()
            if self.voice_worker is not None:
                self.voice_worker.stop()
            self._set_voice_recording(False)
        self._refresh_voice_icons()

    def _on_hands_free_tick(self) -> None:
        """Listen again when Fluxion has answered and finished reading aloud."""
        if not self._hands_free or self.voice_worker is not None or self.generating:
            return
        if self._speaker is not None and self._speaker.speaking():
            return
        self._start_listening(wait_seconds=HANDS_FREE_WAIT)

    def _hands_free_heard(self, text: str) -> None:
        self._set_voice_recording(False)
        if not text:
            self._hands_free_silent += 1
            if self._hands_free_silent >= HANDS_FREE_SILENT_TURNS:
                self.set_hands_free(False)
                self._append_banner(tr("Голосом без рук: выключено — речи не слышно."))
            return  # the timer listens again
        self._hands_free_silent = 0
        if HANDS_FREE_STOP.match(text):
            self.set_hands_free(False)
            self._append_banner(tr("Голосом без рук: выключено по команде «стоп»."))
            return
        self.prompt.setText(spoken_command(text))
        self.send_message()

    def _refresh_voice_icons(self) -> None:
        """Line icons of the composer and of "Озвучить" in the colours of the theme."""
        if not hasattr(self, "speak_toggle"):
            return  # the theme is applied before the chat page exists
        from . import icons

        colors = getattr(self, "_icon_colors", {"fg": "#94A3B8", "on": "#00E5FF", "rec": "#FF5C5C"})
        recording = bool(self.mic_button.property("recording"))
        self.mic_button.style().unpolish(self.mic_button)  # [recording="true"] border
        self.mic_button.style().polish(self.mic_button)
        self.mic_button.setIcon(icons.stop(colors["rec"]) if recording else icons.microphone(colors["fg"]))
        on = self.speak_toggle.isChecked()
        self.speak_toggle.setIcon(icons.speaker(colors["on"] if on else colors["fg"], on))
        self.hands_free_button.setIcon(icons.headphones(colors["on"] if self._hands_free else colors["fg"]))
        document = self.messages.document()
        size = QSize(32, 32)
        document.addResource(QTextDocument.ImageResource, QUrl(SPEAK_ICON),
                             icons.speaker(colors["on"], True).pixmap(size).toImage())
        document.addResource(QTextDocument.ImageResource, QUrl(SPEAK_ICON_STOP),
                             icons.stop(colors["rec"]).pixmap(size).toImage())

    def _show_speak_toggle(self) -> None:
        on = self.speak_toggle.isChecked()
        self._refresh_voice_icons()
        self.speak_toggle.setToolTip(
            tr("Ответы озвучиваются голосом Windows — нажмите, чтобы выключить")
            if on
            else tr("Озвучивать ответы голосом Windows")
        )

    def _on_speak_answers_toggled(self, checked: bool) -> None:
        self.qsettings.setValue("voice_speak_answers", int(checked))
        self._show_speak_toggle()
        box = getattr(self, "voice_speak_answers", None)
        if box is not None and box.isChecked() != checked:
            box.setChecked(checked)
        if not checked:
            self._stop_speaking()

    def _speak(self, markdown: str, index: int) -> None:
        if self._speaker is None:
            from voice.tts import Speaker

            self._speaker = Speaker()
        if not self._speaker.speak(markdown):
            if self._speaker.error:
                self._append_banner(self._speaker.error)
            return
        self._speaking_index = index
        self._speech_timer.start()
        self._render_messages()

    def _stop_speaking(self) -> None:
        if self._speaker is not None:
            self._speaker.stop()
        if self._speaking_index != -1:
            self._speaking_index = -1
            self._speech_timer.stop()
            self._render_messages()

    def _toggle_speak_answer(self, value: str) -> None:
        """"Озвучить" under an answer: read it aloud, or stop it when it is being read."""
        try:
            index = int(value)
        except ValueError:
            return
        if index == self._speaking_index:
            self._stop_speaking()
        elif 0 <= index < len(self._messages):
            self._speak(self._messages[index]["text"], index)

    def _on_speech_tick(self) -> None:
        if self._speaker is None or not self._speaker.speaking():
            self._speaking_index = -1
            self._speech_timer.stop()
            self._render_messages()

    def _notify_answer(self, spoken: str | None = None) -> None:
        """Play the "answer ready" sound and read the answer aloud (*spoken*, by default
        the last message), unless switched off or the user pressed Stop."""
        if self._stop_requested:
            return
        if message_sound_enabled(self.qsettings):
            from .sound import play_message_sound

            play_message_sound()
        if (self.speak_toggle.isChecked() or self._hands_free) and self._messages:
            index = len(self._messages) - 1
            self._speak(spoken if spoken is not None else self._messages[index]["text"], index)

    def stop_generation(self) -> None:
        self._stop_speaking()
        if getattr(self, "laser_job", None) is not None:
            self.laser_job.cancel()  # DXFka stops after the current step
        if self.generating:
            self._stop_requested = True
        if self.worker is not None and self.generating:
            self.worker.stop()
        if self.agent_worker is not None and self.generating:
            self.agent_worker.stop()

    def _write_access(self) -> bool:
        """Tell whether the agent may change files: Pro licence or trial edits left."""
        from licensing import feature_enabled, trial_remaining

        return bool(feature_enabled("agent_write")) or trial_remaining() > 0

    def _agent_write_allowed(self) -> bool:
        """Write access of the next agent run: the chip is on and Pro or trial allows it."""
        return self.chat_agent_files.isChecked() and self._write_access()

    def _refresh_agent_chip(self) -> None:
        """Update the tooltip of the agent chip and its Pro/trial banner."""
        from licensing import feature_enabled, trial_remaining

        base = tr("Сообщение выполнит агент с инструментами проекта. ")
        checked = self.chat_agent_files.isChecked()
        if feature_enabled("agent_write"):
            self.chat_agent_files.setToolTip(base + tr("Запись файлов разрешена (Pro)."))
            self.banners.dismiss_key("agent_write")
            return
        remaining = trial_remaining()
        if remaining > 0:
            note = (
                tr("Pro или триал: осталось {remaining} пробных правок — "
                "каждая с подтверждением.", remaining=remaining)
            )
            self.chat_agent_files.setToolTip(base + note)
            if checked:
                self.banners.show_warning(note, tr("Лицензия…"), self._open_license_dialog, key="agent_write")
            else:
                self.banners.dismiss_key("agent_write")
            return
        self.chat_agent_files.setToolTip(
            base + tr("Только чтение: запись файлов — Pro-функция, пробные правки израсходованы.")
        )
        if checked:
            self.banners.show_warning(
                tr("Запись файлов — Pro-функция: агент работает только на чтение. "
                "Активируйте лицензию: меню Помощь → Лицензия…"),
                tr("Лицензия…"),
                self._open_license_dialog,
                key="agent_write",
            )
        else:
            self.banners.dismiss_key("agent_write")

    def _on_trial_changed(self) -> None:
        """A trial edit was confirmed or declined: refresh the labels that count them."""
        self._refresh_license_badge()

    def _on_chat_agent_files_toggled(self, checked: bool) -> None:
        if checked:
            if self.agent_factory is None:
                self.chat_agent_files.setChecked(False)
                self._append_message("assistant", tr("Движок не подключён. Запустите приложение через python -m desktop_browser."))
                return
            self.prompt.setPlaceholderText(tr("Задача для агента: например, прочитай main.py и исправь баг..."))
        else:
            self.prompt.setPlaceholderText(tr("Напишите сообщение..."))
        self._refresh_agent_chip()

    def _send_agent_message(self, task: str, plan: bool = False, shown: str | None = None) -> None:
        """Run *task* through the agent inside the chat.

        *plan* asks for a numbered plan with file writes off; *shown* replaces
        the text displayed as the user's message.
        """
        visible = shown if shown is not None else task
        self._last_query = visible
        self._web_opened_for_current = False
        if self._project_forgotten():
            self._append_message("user", visible)
            self._append_message("assistant", tr("Проект забыт — агенту не в чем работать. Выберите папку проекта: "
                                                 "меню «Проект» → «Выбрать папку…»."))
            return
        self._append_message("user", visible)
        self.history.append({"role": "user", "content": visible})
        self._append_message("assistant", "")
        self._plan_pending = plan
        self._plan_entry = None
        self._start_thinking()

        self._set_generating(True)
        self.agent_worker = AgentWorker(
            self.agent_factory,
            f"{PLAN_TASK_PREFIX}\n\n{task}" if plan else task,
            False if plan else self._agent_write_allowed(),
            self._agent_max_iterations(),
            self,
        )
        self.agent_worker.step.connect(self._on_chat_agent_step)
        self.agent_worker.done.connect(self._on_chat_agent_done)
        self.agent_worker.failed.connect(self._on_chat_agent_failed)
        self.agent_worker.finished.connect(self.agent_worker.deleteLater)
        self.agent_worker.start()

    def run_plan(self) -> None:
        """Run the agent on the plan saved by the last plan-mode answer."""
        if not self._last_plan or self.generating or self.agent_running:
            return
        if self.agent_factory is None:
            return
        self._send_agent_message(PLAN_RUN_PREFIX + self._last_plan, shown=tr("Выполнить план"))

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
            fragments.append(tr("(остановлено пользователем)"))
        answer = result.get("final_answer") or ""
        if answer:
            if fragments:
                fragments.append("")  # blank line between the steps and the answer
            fragments.append(answer)
            self.history.append({"role": "assistant", "content": answer})
        entry["text"] = "\n".join(fragments) or tr("(пустой ответ)")
        if answer:
            self._notify_answer(answer)
        meta = [tr("агент"), tr("{value} итер.", value=result.get('iterations_used', 0))]
        verification = VERIFICATION_LABELS.get(result.get("verification", ""))
        if verification:
            meta.append(verification)
        entry["meta"] = "[" + " | ".join(meta) + "]"
        if self._plan_pending and answer and result.get("success") and not result.get("cancelled"):
            self._last_plan = answer
            self._plan_entry = entry
        self._plan_pending = False
        self._set_generating(False)
        self.agent_worker = None
        self._render_messages()
        self._save_memory()

    def _on_chat_agent_failed(self, message: str) -> None:
        entry = self._messages[-1]
        entry["text"] = (entry["text"] + "\n" if entry["text"] else "") + message
        self._plan_pending = False
        self._set_generating(False)
        self.agent_worker = None
        self._render_messages()
        self._save_memory()

    def _set_generating(self, active: bool) -> None:
        if active:
            self._stop_requested = False
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
                tr("Задача для агента: например, прочитай main.py и исправь баг...")
                if agent_hint
                else tr("Напишите сообщение...")
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
        citations = getattr(result, "citations", None) or []
        if citations and self._messages:
            self._messages[-1]["sources"] = list(citations)  # shown under the answer
        parts = []
        strategy = getattr(getattr(result, "strategy", None), "name", "")
        if strategy:
            parts.append(strategy.lower())
        if getattr(result, "rag_used", False):
            parts.append(tr("RAG: {value} источн.", value=len(getattr(result, 'rag_sources', []) or [])))
        if getattr(result, "web_used", False):
            parts.append(tr("web: {value} источн.", value=len(getattr(result, 'web_sources', []) or [])))
            tier = getattr(getattr(self, "web_search", None), "provider_tier", "")
            if tier == "basic":
                parts.append(tr("поиск: базовый (один движок)"))
            elif tier == "enhanced":
                parts.append(tr("поиск: расширенный (SearXNG)"))
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
                tr("Веб-поиск: страницы показаны краткой выжимкой из результатов поиска."),
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
            self._messages[-1]["text"] += tr("(пустой ответ)")
        else:
            self.history.append({"role": "assistant", "content": text})
            self._notify_answer()
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
            content = WELCOME_HTML
        else:
            parts = []
            for index, entry in enumerate(self._messages):
                if entry["role"] == "banner":
                    parts.append(f"<p><i>⚠ {html.escape(entry['text'])}</i></p>")
                    continue
                who = tr("Вы") if entry["role"] == "user" else "Fluxion"
                parts.append(f"<p><b>{who}</b></p>")
                if entry["meta"]:
                    parts.append(f"<p><i>{html.escape(entry['meta'])}</i></p>")
                if entry.get("thinking"):
                    spinner = "◐◓◑◒"[self._think_frame % 4]
                    parts.append(tr("<p><i>{spinner} Думаю…</i></p>", spinner=spinner))
                    continue
                from orchestrator.presentation import render_chat_html

                body = render_chat_html(entry["text"]) or "…"
                parts.append(f"<div>{body}</div>")
                if entry.get("sources") and not (index == len(self._messages) - 1 and self.generating):
                    links = "<br>".join(
                        (f'[{s["n"]}] ' if "n" in s else "• ")
                        + f'<a href="{QUrl.fromLocalFile(s["file"]).toString()}">{html.escape(s["label"])}</a>'
                        for s in entry["sources"]
                    )
                    title = html.escape(entry.get("sources_title", tr("Источники")))
                    parts.append(f"<p><small><b>{title}:</b><br>{links}</small></p>")
                last = index == len(self._messages) - 1
                if entry["role"] != "user" and entry["text"].strip() and not (last and self.generating):
                    if index == self._speaking_index:
                        icon, label = SPEAK_ICON_STOP, tr("Остановить озвучку")
                    else:
                        icon, label = SPEAK_ICON, tr("Озвучить")
                    parts.append(
                        f'<p><a href="{SPEAK_URL}{index}"><img src="{icon}" width="16" height="16"'
                        f' style="vertical-align: middle"> {label}</a></p>'
                    )
                if entry is self._plan_entry and not self.generating:
                    parts.append(tr('<p><a href="{PLAN_RUN_URL}">▶ Выполнить план</a></p>', PLAN_RUN_URL=PLAN_RUN_URL))
            content = "".join(parts)
        self._set_html_keeping_scroll(self.messages, content)

    # ── license ──────────────────────────────────────────────────────────

    def _refresh_license_badge(self) -> None:
        lic = load_activation()
        is_pro = lic is not None and lic.is_pro
        act = getattr(self, "act_license", None)
        if act is not None:
            act.setText(tr("Лицензия: PRO") if is_pro else tr("Лицензия: FREE"))
            act.setToolTip(lic.email if lic is not None else "")
        btn = getattr(self, "license_button", None)
        if btn is not None:
            btn.setText("PRO" if is_pro else "FREE")
            btn.setToolTip((tr("Лицензия PRO: ") + lic.email) if is_pro and lic.email else tr("Лицензия: FREE"))
            btn.setProperty("plan", "pro" if is_pro else "free")
            self.style().unpolish(btn)
            self.style().polish(btn)
        self._refresh_training_gate()
        self._refresh_training_env()
        self._refresh_agent_settings()
        self._refresh_agent_chip()
        if getattr(self, "_model_buttons", None):
            self._refresh_models()  # Pro models become downloadable after activation
        self._refresh_cloud_card()

    def _open_license_dialog(self) -> None:
        from .license_dialog import LicenseDialog

        dialog = LicenseDialog(self)
        dialog.exec()
        self._refresh_license_badge()

    # ── updates ──────────────────────────────────────────────────────────

    UPDATES_AUTO_KEY = "updates/auto"  # the automatic check at start, on by default
    UPDATES_LAST_KEY = "updates/last_check"  # time.time() of the last automatic check
    UPDATES_SKIP_KEY = "updates/skip_version"  # «Пропустить эту версию»

    def _check_updates(self, auto: bool = False) -> None:
        """Menu Помощь → Проверить обновления; *auto*: the background check at start, silent
        without an update, without a connection and for a skipped version."""
        if not auto:
            self.statusBar().showMessage(tr("Проверка обновлений…"))
        self._update_worker = UpdateWorker(APP_VERSION)
        self._update_worker.done.connect(lambda info: self._on_update_result(info, auto=auto))
        self._update_worker.finished.connect(self._update_worker.deleteLater)
        self._update_worker.start()

    def _auto_updates_enabled(self) -> bool:
        return str(self.qsettings.value(self.UPDATES_AUTO_KEY, True)).lower() not in ("false", "0")

    def auto_check_updates(self) -> bool:
        """At start (main): the background check, at most once a day. Returns whether it started."""
        from core.update import should_auto_check

        if os.environ.get("FLUXION_NO_UPDATE_CHECK") == "1":
            return False
        if not should_auto_check(self.qsettings.value(self.UPDATES_LAST_KEY, 0), enabled=self._auto_updates_enabled()):
            return False
        self.qsettings.setValue(self.UPDATES_LAST_KEY, time.time())
        self._check_updates(auto=True)
        return True

    def _on_update_result(self, info, auto: bool = False) -> None:
        if not auto:
            self.statusBar().showMessage(tr("Готово"), 3000)
        if info is None:
            if not auto:
                QMessageBox.warning(
                    self, tr("Обновления"), tr("Не удалось проверить обновления: нет соединения с GitHub.")
                )
            return
        if not info.is_newer:
            if not auto:
                QMessageBox.information(
                    self, tr("Обновления"), tr("У вас последняя версия — {current_version}.", current_version=info.current_version)
                )
            return
        if auto and str(self.qsettings.value(self.UPDATES_SKIP_KEY, "") or "") == info.latest_version:
            return
        self._show_update_dialog(info)

    def _can_update_in_place(self, info) -> bool:
        """«Обновить сейчас»: a copy from the installer and a release with the installer and its checksum."""
        from core.app_paths import is_installed

        return bool(info.installer_url and info.sha256_url) and is_installed()

    def _show_update_dialog(self, info) -> None:
        in_place = self._can_update_in_place(info)
        title = tr("Fluxion {latest_version} (у вас {current_version}).",
                   latest_version=info.latest_version, current_version=info.current_version)
        parts = [f"<b>{html.escape(title)}</b>"]
        notes = info.release_notes.strip()
        if notes:
            parts.append(f"<p><b>{html.escape(tr('Что нового:'))}</b><br>"
                         + html.escape(notes[:1500]).replace("\n", "<br>") + "</p>")
        # the site gives FluxionSetup.exe; the GitHub release page stays as a second way
        links = [f'<a href="{SITE_URL}#download">{html.escape(tr("Скачать с сайта"))}</a>']
        if info.release_url:
            links.append(f'<a href="{html.escape(info.release_url)}">{html.escape(tr("Страница на GitHub"))}</a>')
        parts.append("<p>" + " · ".join(links) + "</p>")
        if not in_place:
            parts.append("<p>" + html.escape(
                tr("Эта копия Fluxion не установлена установщиком: скачайте новую версию с сайта.")) + "</p>")

        box = QMessageBox(self)
        box.setWindowTitle(tr("Доступна новая версия"))
        box.setTextFormat(Qt.RichText)
        box.setText("".join(parts))
        label = box.findChild(QLabel, "qt_msgbox_label")
        if label is not None:
            label.setOpenExternalLinks(True)
            label.setTextInteractionFlags(Qt.TextBrowserInteraction)
        auto_box = QCheckBox(tr("Проверять обновления автоматически"))
        auto_box.setChecked(self._auto_updates_enabled())
        box.setCheckBox(auto_box)
        main_btn = box.addButton(tr("Обновить сейчас") if in_place else tr("Скачать с сайта"), QMessageBox.AcceptRole)
        skip_btn = box.addButton(tr("Пропустить эту версию"), QMessageBox.ActionRole)
        box.addButton(tr("Позже"), QMessageBox.RejectRole)
        box.exec()
        self.qsettings.setValue(self.UPDATES_AUTO_KEY, auto_box.isChecked())
        clicked = box.clickedButton()
        if clicked is skip_btn:
            self.qsettings.setValue(self.UPDATES_SKIP_KEY, info.latest_version)
        elif clicked is main_btn:
            if in_place:
                self._start_update(info)
            else:
                QDesktopServices.openUrl(QUrl(SITE_URL + "#download"))

    def _start_update(self, info) -> None:
        """«Обновить сейчас»: download and check the installer, then run it and quit."""
        from PySide6.QtWidgets import QProgressDialog

        dialog = QProgressDialog(tr("Загрузка обновления…"), tr("Отмена"), 0, 100, self)
        dialog.setWindowTitle(tr("Обновления"))
        dialog.setWindowModality(Qt.WindowModal)
        dialog.setMinimumDuration(0)
        dialog.setValue(0)
        worker = InstallerDownloadWorker(info, self)
        self._installer_worker = worker

        def on_progress(done: int, total: int) -> None:
            if total:
                dialog.setMaximum(100)
                dialog.setValue(min(99, int(done * 100 / total)))
            else:
                dialog.setMaximum(0)  # size unknown: a busy bar

        worker.progress.connect(on_progress)
        dialog.canceled.connect(worker.cancel)
        worker.failed.connect(lambda kind: self._on_update_failed(kind, dialog))
        worker.done.connect(lambda path: self._on_installer_ready(path, dialog))
        worker.finished.connect(worker.deleteLater)
        worker.start()

    def _on_update_failed(self, kind: str, dialog=None) -> None:
        if dialog is not None:
            dialog.reset()
        if kind == "cancelled":
            return
        if kind == "checksum_mismatch":
            text = tr("Контрольная сумма установщика не совпала — файл повреждён или подменён. Обновление отменено.")
        elif kind == "checksum_missing":
            text = tr("В выпуске нет контрольной суммы установщика — обновление отменено. Скачайте новую версию с сайта.")
        else:
            text = tr("Не удалось загрузить обновление: {error}", error=kind.partition(":")[2] or kind)
        QMessageBox.warning(self, tr("Обновления"), text)

    def _on_installer_ready(self, path: str, dialog=None) -> None:
        """The checked installer: run it silently (it closes Fluxion and starts the new one) and quit."""
        from core.update import launch_installer

        if dialog is not None:
            dialog.reset()
        try:
            launch_installer(path)
        except OSError as exc:
            QMessageBox.warning(self, tr("Обновления"), tr("Не удалось запустить установщик: {error}", error=exc))
            return
        self.close()
        QApplication.quit()

    # ── theme ────────────────────────────────────────────────────────────

    def choose_language(self, code: str) -> None:
        """Menu Вид → Язык / Language: remember the language, offer a restart to apply it."""
        for other, act in self.language_actions.items():
            act.setChecked(other == code)
        self.qsettings.setValue(LANGUAGE_SETTING, code)
        if code == language():
            return
        # the new language speaks in the question: the window itself is still in the old one
        catalog = load_catalog(code)
        title = catalog.get("Язык интерфейса", "Язык интерфейса")
        text = catalog.get("Язык сменится после перезапуска Fluxion. Перезапустить сейчас?",
                           "Язык сменится после перезапуска Fluxion. Перезапустить сейчас?")
        answer = QMessageBox.question(self, title, text, QMessageBox.Yes | QMessageBox.No, QMessageBox.Yes)
        if answer == QMessageBox.Yes:
            self.restart_app()

    def restart_app(self) -> None:
        """Start a new Fluxion and close this one."""
        from PySide6.QtCore import QProcess

        if getattr(sys, "frozen", False):
            program, args = sys.executable, sys.argv[1:]
        else:
            program, args = sys.executable, ["-m", "desktop_browser", *sys.argv[1:]]
        started = QProcess.startDetached(program, args, str(APP_ROOT))
        if started[0] if isinstance(started, tuple) else started:
            self.close()
            QApplication.quit()

    def toggle_theme(self) -> None:
        self.apply_theme(LIGHT if self.theme.name == "dark" else DARK)

    def apply_theme(self, theme: Theme) -> None:
        self.theme = theme
        self.qsettings.setValue("theme", theme.name)
        if hasattr(self, "act_theme"):
            self.act_theme.setText(tr("Светлая тема") if theme.name == "dark" else tr("Тёмная тема"))

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
        ok = "#B4FF39" if theme.name == "dark" else "#2E7D00"  # lime is unreadable on white
        err = "#FF5C5C"
        self._icon_colors = {"fg": text2, "on": accent, "rec": err}

        self.setStyleSheet(f"""
            QMainWindow, QWidget#page {{ background: {bg0}; color: {text1}; font-family: {ui_font}; font-size: 14px; }}

            /* Диалоги: окна инструментов наследуют этот стиль, но не фон окна — без этого
               светлые кнопки тёмной темы терялись на светлом системном фоне */
            QDialog {{ background: {bg0}; color: {text1}; font-family: {ui_font}; }}
            QDialog QLabel, QDialog QCheckBox, QDialog QRadioButton, QDialog QGroupBox {{ color: {text1}; }}
            QDialog QPlainTextEdit, QDialog QListWidget, QDialog QTableWidget, QDialog QTreeWidget {{ background: {bg1}; color: {text1}; border: 1px solid {border}; border-radius: 6px; selection-background-color: {accent}; }}
            QDialog QScrollArea, QDialog QScrollArea > QWidget > QWidget {{ background: transparent; }}
            QDialog QTabWidget::pane {{ border: 1px solid {border}; background: {bg0}; }}
            QDialog QTabBar::tab {{ background: {bg2}; color: {text2}; padding: 6px 14px; border: 1px solid {border}; }}
            QDialog QTabBar::tab:selected {{ background: {bg0}; color: {text1}; }}
            QDialog QPushButton {{ background: {bg2}; border: 1px solid {border}; }}
            QDialog QPushButton:hover {{ border-color: {accent}; }}
            QDialog QPushButton:disabled {{ color: {text2}; }}

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
            QFrame#composer QPlainTextEdit {{ background: transparent; color: {text1}; border: none; padding: 2px 4px; font-family: {ui_font}; selection-background-color: {accent}; }}
            QToolButton#composerIcon {{ background: transparent; border: none; border-radius: 6px; padding: 5px; }}
            QToolButton#composerIcon:hover {{ background: {bg2}; }}
            QToolButton#composerIcon[recording="true"] {{ background: {bg2}; border: 1px solid {err}; }}
            QProgressBar#voiceLevel {{ background: {border}; border: none; border-radius: 3px; }}
            QProgressBar#voiceLevel::chunk {{ background: {accent}; border-radius: 3px; }}

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
            QFrame#advancedBox {{ background: {bg2}; border: 1px solid {border}; border-radius: 6px; }}
            QCheckBox#modeChip {{ background: transparent; color: {text2}; border: 1px solid transparent; border-radius: 11px; padding: 2px 10px; font-size: 12px; spacing: 0px; }}
            QCheckBox#modeChip::indicator {{ width: 0px; height: 0px; }}
            QCheckBox#modeChip:hover {{ border-color: {border}; color: {text1}; }}
            QCheckBox#modeChip:checked {{ background: {bg2}; border-color: {accent}; color: {accent}; }}
            QLabel#modeHint {{ color: {text2}; font-size: 11px; }}
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
        self._refresh_voice_icons()

    # ── shutdown ─────────────────────────────────────────────────────────

    def closeEvent(self, event) -> None:
        self._save_memory()
        if getattr(self, "report_worker", None) is not None:
            self.report_worker.wait(120000)  # KOMPAS reads a big assembly for a while
        if getattr(self, "acad_worker", None) is not None:
            self.acad_worker.wait(120000)  # AutoCAD starts and saves the DXF
        if getattr(self, "laser_job", None) is not None:
            self.laser_job.cancel()
            if self.laser_job.worker is not None:
                self.laser_job.worker.join(60)
        self._stop_speaking()
        self._hands_free = False
        self._hands_free_timer.stop()
        if self.voice_worker is not None:
            self.voice_worker.stop()
            self.voice_worker.wait(30000)  # recognition of a long phrase on a slow CPU
        if self._speech_download_worker is not None:
            self._speech_download_worker.wait(10000)
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
        if getattr(self, "_catalog_worker", None) is not None:
            self._catalog_worker.wait(12000)  # the request times out at 10 s
        if getattr(self, "_cloud_worker", None) is not None:
            self._cloud_worker.wait(16000)  # the request times out at 15 s
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

    from .crash import enable_native_crash_log, install_excepthook

    install_excepthook()
    enable_native_crash_log()

    try:
        try:
            assistant, backend, settings, rag_service, web_search = build_engine()

            from .searxng import searxng_enabled

            # Docker/SearXNG only if the user switched extended search on
            # (menu Правка); by default the built-in search needs no Docker.
            if (
                web_search is not None
                and searxng_enabled()
                and os.environ.get("FLUXION_DESKTOP_SMOKE") != "1"
            ):
                from threading import Thread

                from .searxng import ensure_searxng

                Thread(
                    target=ensure_searxng,
                    args=(settings.web.searxng_url,),
                    daemon=True,
                    name="searxng-autostart",
                ).start()

            from licensing import feature_enabled

            from .trial_gate import TrialWriteGate

            trial_gate = TrialWriteGate()

            def agent_factory(task: str, allow_write: bool, max_iter: int):
                qs = QSettings("Fluxion", "Fluxion")
                picked = str(qs.value("project_path", "") or "")
                paid = bool(feature_enabled("agent_write"))
                return CodingAgent(
                    backend=window.backend,  # the cloud section can swap the engine
                    project_root=resolve_project_root(picked, settings),
                    rag_service=rag_service,
                    web_search=web_search,
                    max_iterations=max_iter,
                    allow_write=allow_write,
                    git_enabled=git_tools_enabled(qs),
                    lang=getattr(settings, "language", "auto"),
                    write_confirm=None if paid else trial_gate.confirm,
                )

            window = FluxionWindow(
                assistant=assistant,
                backend=backend,
                settings=settings,
                agent_factory=agent_factory,
                rag_service=rag_service,
                web_search=web_search,
            )
            trial_gate.attach(window)
            trial_gate.changed.connect(window._on_trial_changed)
        except Exception as exc:
            rag_service = None
            window = FluxionWindow()
            window.status_label.setText(tr("Ошибка движка: {exc}", exc=exc))
        window.show()

        if (
            os.environ.get("FLUXION_DESKTOP_SMOKE") != "1"
            and getattr(sys, "frozen", False)
            and is_temp_run(sys.executable)
        ):
            window.banners.show_warning(TEMP_RUN_WARNING, key="temp_run")

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
            if os.environ.get("FLUXION_SMOKE_CAD"):
                result = result or _cad_smoke_check(os.environ["FLUXION_SMOKE_CAD"])
            if os.environ.get("FLUXION_SMOKE_VOICE"):
                result = result or _voice_smoke_check(os.environ["FLUXION_SMOKE_VOICE"])
        else:
            # updates: in the background a little after start, at most once a day (Помощь → Проверить обновления)
            if getattr(sys, "frozen", False):
                QTimer.singleShot(15000, window.auto_check_updates)
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


def _cad_smoke_check(folder: str) -> int:
    """Build the sample bent angle in KOMPAS-3D into *folder* (FLUXION_SMOKE_CAD):
    checks that the built exe carries the cad package, its materials catalog
    and pywin32; returns process exit code."""
    try:
        from cad import kompas, materials, spec

        tpl = spec.TEMPLATES["sheet_angle"]
        part, _ = spec.build("sheet_angle", tpl.example[1], material=materials.default("sheet"))
        result = kompas.build_part(part, kompas.part_path("smoke " + tpl.title, folder), log=lambda line: None)
    except Exception as exc:
        print(f"[cad-smoke] FAIL: {type(exc).__name__}: {exc}")
        return 6
    print(f"[cad-smoke] materials: {len(materials.load())}, part: {result.path}, mass: {result.mass_text()}")
    print("[cad-smoke] flat: {:.2f} x {:.2f} x {:g}".format(*result.flat_size) + f", dxf: {result.dxf_path}")
    print("[cad-smoke] OK")
    return 0


def _voice_smoke_check(folder: str) -> int:
    """FLUXION_SMOKE_VOICE=<folder>: a Windows voice says a phrase into a WAV and the
    downloaded speech model recognises it — checks that the built exe carries
    faster-whisper, CTranslate2, sounddevice and the SAPI voice; returns exit code."""
    try:
        import sounddevice  # noqa: F401  (PortAudio library of the microphone)
        import win32com.client

        from voice import models, stt, tts

        installed = models.installed_models()
        if not installed:
            print("[voice-smoke] FAIL: no speech model downloaded")
            return 7
        speaker = tts.Speaker()
        if not speaker.available():
            print(f"[voice-smoke] FAIL: {speaker.error}")
            return 7
        voice = speaker._voice
        fmt = win32com.client.Dispatch("SAPI.SpAudioFormat")
        fmt.Type = 18  # 16 kHz 16 bit mono
        stream = win32com.client.Dispatch("SAPI.SpFileStream")
        stream.Format = fmt
        path = str(Path(folder) / "voice-smoke.wav")
        stream.Open(path, 3, False)
        voice.AudioOutputStream = stream
        voice.Speak("Построй втулку из бронзы.")
        stream.Close()
        text = stt.transcribe(stt.read_wav(path), models.model_path(installed[0].model_id))
    except Exception as exc:
        print(f"[voice-smoke] FAIL: {type(exc).__name__}: {exc}")
        return 7
    print(f"[voice-smoke] voice: {speaker.voice_name()}, model: {installed[0].name}, heard: {text}")
    ok = "втулк" in text.lower()
    print("[voice-smoke] OK" if ok else "[voice-smoke] FAIL: phrase not recognised")
    return 0 if ok else 7


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
