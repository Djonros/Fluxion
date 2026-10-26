"""«Архив КД»: the folders of the design archive and the update of its search index
(rag/archive.py does the work, this is the window). Questions go to the chat:
«/архив …»."""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QThread, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QProgressBar,
    QPushButton,
    QVBoxLayout,
)

from rag import archive


class ArchiveUpdateWorker(QThread):
    progress = Signal(int, int, str)
    done = Signal(object)  # archive.UpdateStats
    failed = Signal(str)

    def __init__(self, data_dir: str | Path, parent=None):
        super().__init__(parent)
        self.data_dir = data_dir
        self._stop = False

    def stop(self) -> None:
        self._stop = True

    def run(self) -> None:
        import logging

        logging.getLogger("pypdf").setLevel(logging.ERROR)  # exported drawings have odd fonts
        try:
            index = archive.ArchiveIndex(self.data_dir)
            try:
                stats = index.update(progress=self.progress.emit, stop_requested=lambda: self._stop)
            finally:
                index.close()
        except Exception as exc:
            self.failed.emit(f"Ошибка: {exc}")
            return
        self.done.emit(stats)


class ArchiveDialog(QDialog):
    """The archive folders, the state of the index, «Обновить индекс» with progress and «Стоп»."""

    def __init__(self, data_dir: str | Path, parent=None):
        super().__init__(parent)
        self.data_dir = Path(data_dir)
        self.worker: ArchiveUpdateWorker | None = None
        index = archive.ArchiveIndex(self.data_dir)
        roots = index.roots
        ocr_on = index.ocr
        self._count, self._updated = index.file_count, index.updated
        index.close()
        self.setWindowTitle("Архив КД")
        self.resize(760, 520)
        box = QVBoxLayout(self)
        intro = QLabel(
            "Fluxion читает файлы архива — КОМПАС-3D (чертежи, спецификации, модели), PDF, Word, Excel — "
            "и ищет по ним: обозначения, наименования, материалы, массы, состав. Файлы только читаются, "
            "КОМПАС не запускается. Обновление читает только новые и изменённые файлы; его можно "
            "остановить и продолжить позже.\nСпрашивайте в чате: «/архив материал бокового лонжерона 714094», "
            "«/архив где применяли 09Г2С после 2020»."
        )
        intro.setWordWrap(True)
        intro.setObjectName("subtitle")
        box.addWidget(intro)
        box.addWidget(QLabel("Папки архива:"))
        row = QHBoxLayout()
        self.folders = QListWidget()
        self.folders.addItems(roots)
        row.addWidget(self.folders, 1)
        buttons = QVBoxLayout()
        add = QPushButton("Добавить папку…")
        add.clicked.connect(self._add_folder)
        buttons.addWidget(add)
        remove = QPushButton("Убрать")
        remove.clicked.connect(self._remove_folder)
        buttons.addWidget(remove)
        buttons.addStretch(1)
        row.addLayout(buttons)
        box.addLayout(row, 1)
        from rag.ocr import available

        self.ocr = QCheckBox("Распознавать сканы (картинки и PDF без текста) — встроенным распознаванием Windows")
        self.ocr.setChecked(ocr_on and available())
        if not available():
            self.ocr.setEnabled(False)
            self.ocr.setToolTip("Нужен русский языковой пакет Windows: Параметры → Время и язык → Язык")
        box.addWidget(self.ocr)
        self.state = QLabel()
        self.state.setWordWrap(True)
        box.addWidget(self.state)
        self.bar = QProgressBar()
        self.bar.setVisible(False)
        box.addWidget(self.bar)
        bottom = QHBoxLayout()
        bottom.addStretch(1)
        self.stop_button = QPushButton("Стоп")
        self.stop_button.setEnabled(False)
        self.stop_button.clicked.connect(self._stop)
        bottom.addWidget(self.stop_button)
        self.update_button = QPushButton("Обновить индекс")
        self.update_button.setObjectName("sendButton")
        self.update_button.clicked.connect(self.start_update)
        bottom.addWidget(self.update_button)
        close = QPushButton("Закрыть")
        close.clicked.connect(self.reject)
        bottom.addWidget(close)
        box.addLayout(bottom)
        self._show_state()

    def roots(self) -> list[str]:
        return [self.folders.item(i).text() for i in range(self.folders.count())]

    def _show_state(self, extra: str = "") -> None:
        text = (f"В индексе файлов: {self._count}. Обновлён: {self._updated}." if self._updated
                else "Индекс ещё не создан: добавьте папки архива и нажмите «Обновить индекс».")
        self.state.setText(text + (f"\n{extra}" if extra else ""))

    def _add_folder(self) -> None:
        path = QFileDialog.getExistingDirectory(self, "Папка архива", str(Path.home()))
        if path and path not in self.roots():
            self.folders.addItem(path)

    def _remove_folder(self) -> None:
        for item in self.folders.selectedItems():
            self.folders.takeItem(self.folders.row(item))

    def start_update(self) -> None:
        if self.worker is not None:
            return
        index = archive.ArchiveIndex(self.data_dir)
        index.roots = self.roots()
        index.ocr = self.ocr.isChecked()
        index.close()
        self.worker = ArchiveUpdateWorker(self.data_dir, self)
        self.worker.progress.connect(self._progress)
        self.worker.done.connect(self._done)
        self.worker.failed.connect(self._failed)
        self.worker.finished.connect(self._finished)
        self.update_button.setEnabled(False)
        self.stop_button.setEnabled(True)
        self.bar.setVisible(True)
        self.bar.setRange(0, 0)
        self.state.setText("Ищу новые и изменённые файлы…")
        self.worker.start()

    def _progress(self, done: int, total: int, name: str) -> None:
        self.bar.setRange(0, total)
        self.bar.setValue(done)
        self.state.setText(f"Читаю {done} из {total}: {name}")

    def _done(self, stats) -> None:
        index = archive.ArchiveIndex(self.data_dir)
        self._count, self._updated = index.file_count, index.updated
        index.close()
        lines = [f"Прочитано файлов: {stats.indexed}, без изменений: {stats.unchanged}, "
                 f"убрано из индекса: {stats.removed}. Время: {stats.seconds / 60:.1f} мин."]
        if stats.stopped:
            lines.append("Остановлено: «Обновить индекс» продолжит с этого места.")
        if stats.failed:
            lines.append(f"Не прочитано: {len(stats.failed)} (например, {stats.failed[0]}).")
        self._show_state("\n".join(lines))

    def _failed(self, message: str) -> None:
        self._show_state(message)

    def _finished(self) -> None:
        self.worker = None
        self.update_button.setEnabled(True)
        self.stop_button.setEnabled(False)
        self.bar.setVisible(False)

    def _stop(self) -> None:
        if self.worker is not None:
            self.worker.stop()
            self.stop_button.setEnabled(False)
            self.state.setText("Останавливаю…")

    def reject(self) -> None:
        if self.worker is not None:  # the work so far is kept: the next update goes on from there
            self.worker.stop()
            self.worker.wait()
        index = archive.ArchiveIndex(self.data_dir)
        index.roots = self.roots()
        index.close()
        super().reject()
