"""«Сравнить версии сборки» (Производство, Pro): two versions of a product — assemblies
(.a3d, opened read-only) or snapshots of earlier reports (.json) — and what changed
between them in an Excel workbook (cad/compare.py)."""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QThread, Signal
from PySide6.QtWidgets import (QDialog, QDialogButtonBox, QFileDialog, QGridLayout, QLabel, QLineEdit, QPushButton,
                               QVBoxLayout)

from cad.compare import version_snapshot

from .i18n import tr


class CompareDialog(QDialog):
    def __init__(self, parent=None, start: str = ""):
        super().__init__(parent)
        self.setWindowTitle(tr("Сравнить версии сборки"))
        self.resize(720, 220)
        self.start = start
        box = QVBoxLayout(self)
        intro = QLabel(tr("«Было» и «Стало» — сборки КОМПАС-3D (.a3d) или снимки прошлых отчётов (.json: отчёт по "
                          "сборке сохраняет его рядом с книгой Excel). Сборки открываются только для чтения."))
        intro.setWordWrap(True)
        box.addWidget(intro)
        grid = QGridLayout()
        self.old = QLineEdit()
        self.new = QLineEdit()
        picks = (self._pick_old, self._pick_new)  # bound methods: Qt drops them with the dialog
        for row, (title, field) in enumerate(((tr("Было:"), self.old), (tr("Стало:"), self.new))):
            grid.addWidget(QLabel(title), row, 0)
            grid.addWidget(field, row, 1)
            pick = QPushButton(tr("Выбрать…"))
            pick.clicked.connect(picks[row])
            grid.addWidget(pick, row, 2)
        box.addLayout(grid)
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        self.ok = buttons.button(QDialogButtonBox.Ok)
        self.ok.setText(tr("Сравнить"))
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        box.addWidget(buttons)
        self.old.textChanged.connect(self._update)
        self.new.textChanged.connect(self._update)
        self._update()

    def _pick_old(self) -> None:
        self._pick(self.old)

    def _pick_new(self) -> None:
        self._pick(self.new)

    def _pick(self, field: QLineEdit) -> None:
        start = field.text() or self.start
        path, _ = QFileDialog.getOpenFileName(
            self, tr("Версия сборки"), start,
            tr("Сборки и снимки отчётов (*.a3d *.json);;Сборки КОМПАС-3D (*.a3d);;Снимки отчётов (*.json)"))
        if path:
            field.setText(path)

    def paths(self) -> tuple[str, str]:
        return self.old.text().strip(), self.new.text().strip()

    def _update(self, *_args) -> None:
        old, new = self.paths()
        ok = all(p and Path(p).is_file() and Path(p).suffix.lower() in (".a3d", ".json") for p in (old, new))
        self.ok.setEnabled(ok and Path(old).resolve() != Path(new).resolve())


class CompareWorker(QThread):
    progress = Signal(str)
    ready = Signal(object, str)  # cad.compare.Diff, the workbook
    failed = Signal(str)

    def __init__(self, old: str, new: str, parent=None):
        super().__init__(parent)
        self.old, self.new = old, new

    def run(self) -> None:
        from cad import assembly, compare

        try:
            import pythoncom

            pythoncom.CoInitialize()  # COM in a background thread
        except ImportError:
            pythoncom = None
        try:
            self.progress.emit(tr("Было: {value}", value=Path(self.old).name))
            old = version_snapshot(self.old, self.progress.emit)
            self.progress.emit(tr("Стало: {value}", value=Path(self.new).name))
            new = version_snapshot(self.new, self.progress.emit)
            diff = compare.compare(old, new)
            path = compare.save_xlsx(diff, compare.default_path(diff))
        except (assembly.AssemblyError, ValueError) as exc:
            self.failed.emit(str(exc))
            return
        except Exception as exc:  # COM errors carry the KOMPAS message
            self.failed.emit(tr("Не получилось сравнить: {exc}", exc=exc))
            return
        finally:
            if pythoncom is not None:
                pythoncom.CoUninitialize()
        self.ready.emit(diff, str(path))
