"""«Мой стиль письма»: the user's samples -> the profile of their manner of writing
(orchestrator/writing_style.py does the work, this is the window)."""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QThread, Signal
from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QDialog,
    QFileDialog,
    QHBoxLayout,
    QHeaderView,
    QInputDialog,
    QLabel,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
)

from orchestrator import writing_style

FILE_FILTER = "Документы (*.pdf *.docx *.txt *.md);;Все файлы (*.*)"


class StyleWorker(QThread):
    done = Signal(str)
    failed = Signal(str)

    def __init__(self, samples, backend, parent=None):
        super().__init__(parent)
        self.samples = samples
        self.backend = backend

    def run(self) -> None:
        try:
            self.done.emit(writing_style.analyze(self.samples, self.backend))
        except writing_style.WritingStyleError as exc:
            self.failed.emit(str(exc))
        except Exception as exc:
            self.failed.emit(f"Ошибка: {exc}")


class WritingStyleDialog(QDialog):
    """Samples (kind, name, beginning) -> «Составить описание» -> the profile, editable -> saved."""

    def __init__(self, backend, data_dir: str | Path, parent=None):
        super().__init__(parent)
        self.backend = backend
        self.data_dir = Path(data_dir)
        self.style = writing_style.load(self.data_dir)
        self.worker: StyleWorker | None = None
        self.setWindowTitle("Мой стиль письма")
        self.resize(980, 720)
        box = QVBoxLayout(self)

        intro = QLabel(
            "Добавьте 3–10 своих текстов: служебные и пояснительные записки, письма заказчикам. "
            "Fluxion опишет вашу манеру — обращения, обороты, построение, оформление — и с включённым "
            "«✍ Мой стиль» в чате будет писать так же. Описание можно поправить руками."
        )
        intro.setWordWrap(True)
        intro.setObjectName("subtitle")
        box.addWidget(intro)

        self.table = QTableWidget(0, 3)
        self.table.setHorizontalHeaderLabels(["Вид текста", "Образец", "Начало"])
        self.table.verticalHeader().setVisible(False)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        header = self.table.horizontalHeader()
        self.table.setColumnWidth(0, 190)
        self.table.setColumnWidth(1, 240)
        header.setStretchLastSection(True)
        box.addWidget(self.table, 1)

        sample_row = QHBoxLayout()
        add_files = QPushButton("Добавить файлы…")
        add_files.clicked.connect(self._add_files)
        sample_row.addWidget(add_files)
        paste = QPushButton("Вставить текст…")
        paste.clicked.connect(self._paste)
        sample_row.addWidget(paste)
        remove = QPushButton("Удалить")
        remove.clicked.connect(self._remove)
        sample_row.addWidget(remove)
        sample_row.addStretch(1)
        self.analyze_button = QPushButton("Составить описание стиля")
        self.analyze_button.setObjectName("sendButton")
        self.analyze_button.clicked.connect(self._analyze)
        sample_row.addWidget(self.analyze_button)
        box.addLayout(sample_row)

        self.status = QLabel()
        self.status.setWordWrap(True)
        self.status.setObjectName("subtitle")
        box.addWidget(self.status)
        box.addWidget(QLabel("Описание вашего стиля:"))
        self.profile = QPlainTextEdit()
        self.profile.setPlaceholderText("Появится после «Составить описание стиля». Можно написать и самому.")
        self.profile.setPlainText(self.style.profile)
        box.addWidget(self.profile, 1)

        bottom = QHBoxLayout()
        bottom.addStretch(1)
        close = QPushButton("Закрыть")
        close.clicked.connect(self.reject)
        bottom.addWidget(close)
        self.save_button = QPushButton("Сохранить")
        self.save_button.setObjectName("sendButton")
        self.save_button.clicked.connect(self._save)
        bottom.addWidget(self.save_button)
        box.addLayout(bottom)

        for sample in self.style.samples:
            self._show(sample)

    # ── samples ──────────────────────────────────────────────────────────
    def _add_row(self, sample: writing_style.Sample) -> None:
        self.style.samples.append(sample)
        self._show(sample)

    def _show(self, sample: writing_style.Sample) -> None:
        row = self.table.rowCount()
        self.table.insertRow(row)
        kind = QComboBox()
        kind.addItems(writing_style.KINDS)
        kind.setCurrentText(sample.kind)
        self.table.setCellWidget(row, 0, kind)
        self.table.setItem(row, 1, QTableWidgetItem(sample.name))
        beginning = " ".join(sample.text.split())[:200]
        item = QTableWidgetItem(beginning)
        item.setToolTip(sample.text[:1500])
        self.table.setItem(row, 2, item)

    def samples(self) -> list[writing_style.Sample]:
        """The samples with the kinds as chosen in the table."""
        out = []
        for row, sample in enumerate(self.style.samples):
            kind = self.table.cellWidget(row, 0).currentText()
            out.append(writing_style.Sample(sample.name, kind, sample.text))
        return out

    def _add_files(self) -> None:
        paths, _ = QFileDialog.getOpenFileNames(self, "Ваши тексты", str(Path.home()), FILE_FILTER)
        failed = []
        for path in paths:
            try:
                sample = writing_style.read_sample(path)
            except Exception as exc:  # rag.documents.DocumentError, a locked file
                failed.append(f"{Path(path).name}: {exc}")
                continue
            if len(sample.text) < writing_style.MIN_SAMPLE_CHARS:
                failed.append(f"{Path(path).name}: текста почти нет (скан без распознавания?)")
                continue
            self._add_row(sample)
        if failed:
            self.status.setText("Не прочитаны:\n" + "\n".join(failed))

    def _paste(self) -> None:
        text, ok = QInputDialog.getMultiLineText(self, "Вставить текст", "Текст письма или записки:")
        if ok and text.strip():
            self._add_row(writing_style.Sample("Вставленный текст", writing_style.detect_kind(text[:400]),
                                               text.strip()))

    def _remove(self) -> None:
        rows = sorted({i.row() for i in self.table.selectedIndexes()}, reverse=True)
        for row in rows:
            self.table.removeRow(row)
            del self.style.samples[row]

    # ── the profile ──────────────────────────────────────────────────────
    def _analyze(self) -> None:
        if self.backend is None:
            QMessageBox.warning(self, "Мой стиль письма", "Модель не подключена: выберите модель в чате.")
            return
        if not self.style.samples:
            QMessageBox.information(self, "Мой стиль письма", "Сначала добавьте свои тексты.")
            return
        self.analyze_button.setEnabled(False)
        self.status.setText("Модель читает образцы… Это займёт от нескольких секунд до пары минут.")
        self.worker = StyleWorker(self.samples(), self.backend, self)
        self.worker.done.connect(self._done)
        self.worker.failed.connect(self._failed)
        self.worker.finished.connect(lambda: self.analyze_button.setEnabled(True))
        self.worker.start()

    def _done(self, profile: str) -> None:
        self.profile.setPlainText(profile)
        self.status.setText("Готово. Проверьте описание, поправьте, если нужно, и нажмите «Сохранить».")

    def _failed(self, message: str) -> None:
        self.status.setText(message)

    def _save(self) -> None:
        style = writing_style.StyleProfile(self.profile.toPlainText().strip(), self.samples())
        writing_style.save(self.data_dir, style)
        self.style = style
        self.accept()

    def reject(self) -> None:
        if self.worker is not None and self.worker.isRunning():
            # the model is still answering: let the thread finish on its own, its result is dropped
            self.worker.done.disconnect()
            self.worker.failed.disconnect()
            self.worker.finished.disconnect()
            self.worker.setParent(self.parent())
        super().reject()
