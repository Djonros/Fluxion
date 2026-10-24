"""«Датасет из документов»: the chat model writes question-answer pairs on the
user's documents, the user reviews them and saves a JSONL for training
(finetune/doc_dataset.py does the work, this is the window)."""
from __future__ import annotations

from datetime import datetime
from pathlib import Path

from PySide6.QtCore import Qt, QThread, Signal
from PySide6.QtWidgets import (
    QAbstractItemView,
    QDialog,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QListWidget,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QSpinBox,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
)

from finetune import doc_dataset

FILE_FILTER = "Документы (*.pdf *.docx *.xlsx *.pptx *.txt *.md);;Все файлы (*.*)"
DRAFT_NAME = "doc_dataset_draft.json"


class DocDatasetWorker(QThread):
    log = Signal(str)
    progress = Signal(int, int)
    done = Signal(object)  # list[QAPair]
    failed = Signal(str)

    def __init__(self, files, backend, per_fragment: int, max_fragments: int, parent=None):
        super().__init__(parent)
        self.files = files
        self.backend = backend
        self.per_fragment = per_fragment
        self.max_fragments = max_fragments
        self._stop = False

    def stop(self) -> None:
        self._stop = True

    def run(self) -> None:
        try:
            pairs = doc_dataset.build_pairs(
                self.files,
                self.backend,
                per_fragment=self.per_fragment,
                max_fragments=self.max_fragments,
                log=self.log.emit,
                progress=self.progress.emit,
                stop_requested=lambda: self._stop,
            )
        except doc_dataset.DocDatasetError as exc:
            self.failed.emit(str(exc))
            return
        except Exception as exc:
            self.failed.emit(f"Ошибка: {exc}")
            return
        self.done.emit(pairs)


class DocDatasetDialog(QDialog):
    """Files -> generated pairs -> review (edit, untick) -> saved JSONL in ``self.saved_path``."""

    def __init__(self, backend, data_dir: str | Path, parent=None):
        super().__init__(parent)
        self.backend = backend
        self.data_dir = Path(data_dir)
        self.pairs: list[doc_dataset.QAPair] = []
        self.saved_path = ""
        self.worker: DocDatasetWorker | None = None
        self.setWindowTitle("Датасет из документов")
        self.resize(1100, 720)
        box = QVBoxLayout(self)

        intro = QLabel(
            "Fluxion прочитает ваши документы (PDF, Word, Excel, PowerPoint, текст) и составит по ним "
            "пары «вопрос — ответ» для обучения. Просмотрите их: ответ можно исправить прямо в таблице, "
            "неудачную пару — снять галочкой. Сохраняются только отмеченные."
        )
        intro.setWordWrap(True)
        intro.setObjectName("subtitle")
        box.addWidget(intro)

        files_row = QHBoxLayout()
        self.file_list = QListWidget()
        self.file_list.setMaximumHeight(110)
        files_row.addWidget(self.file_list, 1)
        file_buttons = QVBoxLayout()
        add_files = QPushButton("Добавить файлы…")
        add_files.clicked.connect(self._add_files)
        file_buttons.addWidget(add_files)
        add_folder = QPushButton("Добавить папку…")
        add_folder.clicked.connect(self._add_folder)
        file_buttons.addWidget(add_folder)
        clear = QPushButton("Очистить")
        clear.clicked.connect(self.file_list.clear)
        file_buttons.addWidget(clear)
        file_buttons.addStretch(1)
        files_row.addLayout(file_buttons)
        box.addLayout(files_row)

        form = QFormLayout()
        self.per_fragment = QSpinBox()
        self.per_fragment.setRange(1, 6)
        self.per_fragment.setValue(doc_dataset.DEFAULT_PER_FRAGMENT)
        self.per_fragment.setToolTip(f"Фрагмент — около {doc_dataset.FRAGMENT_CHARS} знаков текста")
        form.addRow("Вопросов на фрагмент:", self.per_fragment)
        self.max_fragments = QSpinBox()
        self.max_fragments.setRange(0, 100_000)
        self.max_fragments.setSpecialValueText("все")
        self.max_fragments.setToolTip("Для пробы возьмите 10–20: на один фрагмент уходит от нескольких секунд до минуты")
        form.addRow("Фрагментов не больше:", self.max_fragments)
        box.addLayout(form)

        run_row = QHBoxLayout()
        self.run_button = QPushButton("Составить вопросы")
        self.run_button.setObjectName("sendButton")
        self.run_button.clicked.connect(self._start)
        run_row.addWidget(self.run_button)
        self.stop_button = QPushButton("Остановить")
        self.stop_button.setEnabled(False)
        self.stop_button.clicked.connect(self._stop)
        run_row.addWidget(self.stop_button)
        self.progress_bar = QProgressBar()
        self.progress_bar.setVisible(False)
        run_row.addWidget(self.progress_bar, 1)
        box.addLayout(run_row)
        self.status = QLabel()
        self.status.setWordWrap(True)
        self.status.setObjectName("subtitle")
        box.addWidget(self.status)

        self.table = QTableWidget(0, 4)
        self.table.setHorizontalHeaderLabels(["✓", "Вопрос", "Ответ", "Источник"])
        self.table.verticalHeader().setVisible(False)
        self.table.setWordWrap(True)
        self.table.setSelectionMode(QAbstractItemView.NoSelection)
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeToContents)
        self.table.setColumnWidth(1, 330)
        self.table.setColumnWidth(2, 480)
        header.setStretchLastSection(True)
        self.table.itemChanged.connect(self._item_changed)
        box.addWidget(self.table, 1)

        bottom = QHBoxLayout()
        self.counter = QLabel()
        bottom.addWidget(self.counter, 1)
        close = QPushButton("Закрыть")
        close.clicked.connect(self.reject)
        bottom.addWidget(close)
        self.save_button = QPushButton("Сохранить датасет…")
        self.save_button.setObjectName("sendButton")
        self.save_button.clicked.connect(self._save)
        bottom.addWidget(self.save_button)
        box.addLayout(bottom)

        draft = self._draft_path()
        if draft.is_file():
            try:
                self._show_pairs(doc_dataset.load_draft(draft))
                self.status.setText("Открыт прошлый незаконченный просмотр. Новые вопросы заменят его.")
            except (OSError, ValueError, TypeError):
                pass
        self._update()

    # ── files ────────────────────────────────────────────────────────────
    def _paths(self) -> list[str]:
        return [self.file_list.item(i).text() for i in range(self.file_list.count())]

    def _add(self, paths) -> None:
        present = set(self._paths())
        for path in paths:
            if path and path not in present:
                self.file_list.addItem(path)
                present.add(path)
        self._update()

    def _add_files(self) -> None:
        paths, _ = QFileDialog.getOpenFileNames(self, "Документы для датасета", str(Path.home()), FILE_FILTER)
        self._add(paths)

    def _add_folder(self) -> None:
        path = QFileDialog.getExistingDirectory(self, "Папка с документами", str(Path.home()))
        self._add([path])

    # ── generation ───────────────────────────────────────────────────────
    def _start(self) -> None:
        if self.backend is None:
            QMessageBox.warning(self, "Датасет из документов", "Модель не подключена: выберите модель в чате.")
            return
        files = doc_dataset.collect_files(self._paths())
        if not files:
            QMessageBox.information(self, "Датасет из документов", "Добавьте файлы или папку с документами.")
            return
        if self.pairs and QMessageBox.question(
            self, "Датасет из документов", "Уже составленные пары будут заменены. Продолжить?"
        ) != QMessageBox.Yes:
            return
        self.status.setText(f"Документов: {len(files)}. Читаю…")
        self.progress_bar.setRange(0, 0)
        self.progress_bar.setVisible(True)
        self.run_button.setEnabled(False)
        self.stop_button.setEnabled(True)
        self.worker = DocDatasetWorker(files, self.backend, self.per_fragment.value(),
                                       self.max_fragments.value(), self)
        self.worker.log.connect(self.status.setText)
        self.worker.progress.connect(self._progress)
        self.worker.done.connect(self._done)
        self.worker.failed.connect(self._failed)
        self.worker.finished.connect(self._finished)
        self.worker.start()

    def _stop(self) -> None:
        if self.worker is not None:
            self.worker.stop()
            self.stop_button.setEnabled(False)
            self.status.setText("Останавливаю после текущего фрагмента…")

    def _progress(self, done: int, total: int) -> None:
        self.progress_bar.setRange(0, max(total, 1))
        self.progress_bar.setValue(done)
        self.progress_bar.setFormat(f"фрагмент {done} из {total}")

    def _done(self, pairs) -> None:
        self._show_pairs(pairs)
        try:
            doc_dataset.save_draft(pairs, self._draft_path())
        except OSError:
            pass
        self.status.setText(f"Готово: пар {len(pairs)}. Просмотрите их перед сохранением.")

    def _failed(self, message: str) -> None:
        self.status.setText(message)
        QMessageBox.warning(self, "Датасет из документов", message)

    def _finished(self) -> None:
        self.progress_bar.setVisible(False)
        self.run_button.setEnabled(True)
        self.stop_button.setEnabled(False)
        self.worker = None

    # ── review ───────────────────────────────────────────────────────────
    def _show_pairs(self, pairs) -> None:
        self.pairs = list(pairs)
        self.table.blockSignals(True)
        self.table.setRowCount(len(self.pairs))
        for row, pair in enumerate(self.pairs):
            check = QTableWidgetItem()
            check.setFlags(Qt.ItemIsUserCheckable | Qt.ItemIsEnabled)
            check.setCheckState(Qt.Checked if pair.keep else Qt.Unchecked)
            self.table.setItem(row, 0, check)
            self.table.setItem(row, 1, QTableWidgetItem(pair.question))
            self.table.setItem(row, 2, QTableWidgetItem(pair.answer))
            source = QTableWidgetItem(pair.source)
            source.setFlags(Qt.ItemIsEnabled)
            self.table.setItem(row, 3, source)
        self.table.resizeRowsToContents()
        self.table.blockSignals(False)
        self._update()

    def _item_changed(self, item: QTableWidgetItem) -> None:
        pair = self.pairs[item.row()]
        if item.column() == 0:
            pair.keep = item.checkState() == Qt.Checked
        elif item.column() == 1:
            pair.question = item.text()
        elif item.column() == 2:
            pair.answer = item.text()
        self._update()

    def _update(self) -> None:
        kept = sum(1 for p in self.pairs if p.keep)
        self.counter.setText(f"Пар: {len(self.pairs)}, в датасет войдут {kept}" if self.pairs else "")
        self.save_button.setEnabled(kept > 0)

    def _draft_path(self) -> Path:
        return self.data_dir / "datasets" / DRAFT_NAME

    def _save(self) -> None:
        folder = self.data_dir / "datasets"
        folder.mkdir(parents=True, exist_ok=True)
        default = folder / f"docs-{datetime.now():%Y%m%d-%H%M}.jsonl"
        path, _ = QFileDialog.getSaveFileName(self, "Сохранить датасет", str(default), "JSONL (*.jsonl)")
        if not path:
            return
        try:
            count = doc_dataset.save_jsonl(self.pairs, path)
        except OSError as exc:
            QMessageBox.warning(self, "Сохранить датасет", f"Не удалось сохранить: {exc}")
            return
        try:
            self._draft_path().unlink(missing_ok=True)
        except OSError:
            pass
        self.saved_path = path
        QMessageBox.information(self, "Датасет сохранён", f"Сохранено пар: {count}\n{path}")
        self.accept()

    def reject(self) -> None:
        if self.worker is not None:  # a running thread must not outlive its window
            self._stop()
            self.status.setText("Останавливаю: окно закроется после текущего фрагмента…")
            self.worker.finished.connect(self.reject)
            return
        if self.pairs:  # keep the edits for the next time the window is opened
            try:
                doc_dataset.save_draft(self.pairs, self._draft_path())
            except OSError:
                pass
        super().reject()
