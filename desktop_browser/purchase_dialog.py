"""«Заявка на закупку» (Инструменты, Pro): several products, each in its quantity —
assemblies (.a3d, opened read-only) or snapshots of their reports (.json) — and what
to buy for all of them in an Excel workbook (cad/purchase.py)."""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt, QThread, Signal
from PySide6.QtWidgets import (QDialog, QFileDialog, QHBoxLayout, QHeaderView, QLabel, QPushButton, QSpinBox,
                               QTableWidget, QTableWidgetItem, QVBoxLayout)

from .i18n import tr


class PurchaseDialog(QDialog):
    def __init__(self, parent=None, start: str = ""):
        super().__init__(parent)
        self.setWindowTitle(tr("Заявка на закупку"))
        self.resize(760, 380)
        self.start = start
        box = QVBoxLayout(self)
        intro = QLabel(tr("Изделия заявки и их количество. Каждое — сборка КОМПАС-3D (.a3d, открывается только для "
                          "чтения) или снимок её отчёта (.json, лежит рядом с книгой отчёта). Отход и форматы листа — "
                          "из «Норм и цен завода»."))
        intro.setWordWrap(True)
        box.addWidget(intro)
        self.table = QTableWidget(0, 2)
        self.table.setHorizontalHeaderLabels([tr("Изделие (файл)"), tr("Количество, шт.")])
        self.table.verticalHeader().setVisible(False)
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        box.addWidget(self.table, 1)
        buttons = QHBoxLayout()
        add = QPushButton(tr("Добавить…"))
        add.clicked.connect(self._pick)
        remove = QPushButton(tr("Удалить"))
        remove.clicked.connect(self._remove)
        buttons.addWidget(add)
        buttons.addWidget(remove)
        buttons.addStretch(1)
        cancel = QPushButton(tr("Отмена"))
        cancel.clicked.connect(self.reject)
        self.ok = QPushButton(tr("Составить заявку"))
        self.ok.setObjectName("sendButton")
        self.ok.clicked.connect(self.accept)
        buttons.addWidget(cancel)
        buttons.addWidget(self.ok)
        box.addLayout(buttons)
        self._update()

    def add(self, path: str, qty: int = 1) -> None:
        row = self.table.rowCount()
        self.table.insertRow(row)
        item = QTableWidgetItem(Path(path).name)
        item.setData(Qt.UserRole, path)
        item.setToolTip(path)
        item.setFlags(Qt.ItemIsEnabled | Qt.ItemIsSelectable)
        self.table.setItem(row, 0, item)
        spin = QSpinBox()
        spin.setRange(1, 100000)
        spin.setValue(qty)
        self.table.setCellWidget(row, 1, spin)
        self._update()

    def _pick(self) -> None:
        paths, _ = QFileDialog.getOpenFileNames(
            self, tr("Изделия заявки"), self.start,
            tr("Сборки и снимки отчётов (*.a3d *.json);;Сборки КОМПАС-3D (*.a3d);;Снимки отчётов (*.json)"))
        for path in paths:
            self.add(path)
            self.start = str(Path(path).parent)

    def _remove(self) -> None:
        row = self.table.currentRow()
        if row >= 0:
            self.table.removeRow(row)
        self._update()

    def products(self) -> list[tuple[str, int]]:
        return [(self.table.item(r, 0).data(Qt.UserRole), self.table.cellWidget(r, 1).value())
                for r in range(self.table.rowCount())]

    def _update(self) -> None:
        self.ok.setEnabled(self.table.rowCount() > 0)


class PurchaseWorker(QThread):
    progress = Signal(str)
    ready = Signal(object, str)  # cad.purchase.Order, the workbook
    failed = Signal(str)

    def __init__(self, products: list[tuple[str, int]], parent=None):
        super().__init__(parent)
        self.products = products

    def run(self) -> None:
        from cad import assembly, purchase

        from cad.compare import version_snapshot

        try:
            import pythoncom

            pythoncom.CoInitialize()  # COM in a background thread
        except ImportError:
            pythoncom = None
        try:
            snapshots = []
            for path, qty in self.products:
                self.progress.emit(tr("{value} × {qty}", value=Path(path).name, qty=qty))
                snapshots.append((version_snapshot(path, self.progress.emit), qty))
            order = purchase.build(snapshots)
            path = purchase.save_xlsx(order, purchase.default_path())
        except (assembly.AssemblyError, ValueError) as exc:
            self.failed.emit(str(exc))
            return
        except Exception as exc:  # COM errors carry the KOMPAS message
            self.failed.emit(tr("Не получилось составить заявку: {exc}", exc=exc))
            return
        finally:
            if pythoncom is not None:
                pythoncom.CoUninitialize()
        self.ready.emit(order, str(path))
