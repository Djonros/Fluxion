"""Norm control of an AutoCAD drawing in the desktop app (cad/acad_check.py):
the worker reads the drawing in the background, the window lists the remarks,
each one is marked before the report is made."""
from __future__ import annotations

import html
from pathlib import Path

from PySide6.QtCore import Qt, QThread, QUrl, Signal
from PySide6.QtGui import QColor, QDesktopServices
from PySide6.QtWidgets import QDialog, QHBoxLayout, QLabel, QPushButton, QVBoxLayout
from .i18n import tr

ACAD_FILTER = tr("Чертежи AutoCAD (*.dwg *.dxf)")


class AcadCheckWorker(QThread):
    """The drawing is opened through AutoCAD (a copy, read-only) or read as DXF, then checked."""

    progress = Signal(str)
    ready = Signal(object)  # cad.acad_check.Result
    failed = Signal(str)

    def __init__(self, path: str, parent=None):
        super().__init__(parent)
        self.path = path

    def run(self) -> None:
        from cad import acad, acad_check, report
        from cad.drawing import DrawingError

        try:
            import pythoncom

            pythoncom.CoInitialize()  # COM in a background thread
        except ImportError:  # not Windows: DXF only
            pythoncom = None
        try:
            drawing = acad.open_drawing(self.path, log=self.progress.emit)
            self.progress.emit(tr("Проверяю штамп, шрифты, слои, размеры и масштабы…"))
            result = acad_check.check(drawing, report.load_decisions(drawing.file))
        except DrawingError as exc:
            self.failed.emit(str(exc))
            return
        except Exception as exc:  # COM errors carry the AutoCAD message
            self.failed.emit(tr("Не получилось проверить чертёж: {exc}", exc=exc))
            return
        finally:
            if pythoncom is not None:
                pythoncom.CoUninitialize()
        self.ready.emit(result)


class AcadWarningsDialog(QDialog):
    """The remarks of a drawing, one row each: the designer marks every one
    ("Проверено") and only then the report can be made."""

    def __init__(self, result, parent=None):
        super().__init__(parent)
        from PySide6.QtWidgets import QAbstractItemView, QHeaderView, QTableWidget, QTableWidgetItem

        self.result = result
        self.setWindowTitle(tr("Нормоконтроль чертежа"))
        self.resize(1100, 560)
        box = QVBoxLayout(self)
        intro = QLabel(
            tr("<b>{value}</b> ({norm}): замечаний — {n_remarks}. "
            "Отметьте каждое: отчёт формируется, только когда отмечено всё. "
            "Отметки запоминаются для этого чертежа. Двойной щелчок по строке открывает чертёж.", value=html.escape(result.title), norm=result.norm, n_remarks=len(result.remarks))
        )
        intro.setWordWrap(True)
        intro.setTextFormat(Qt.RichText)
        box.addWidget(intro)
        headers = [tr("Проверено"), tr("Уровень"), tr("Замечание"), tr("Что"), tr("Где"), tr("Что будет и что сделать")]
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
            for col, text in enumerate((r["level"], r["problem"], r["title"], r["where"], r["message"]), 1):
                item = QTableWidgetItem(text)
                item.setFlags(Qt.ItemIsEnabled)
                if col == 1 and r["level"] == "Внимание":
                    item.setForeground(QColor("#B45309"))
                self.table.setItem(row, col, item)
        header = self.table.horizontalHeader()
        for col in (0, 1):
            header.setSectionResizeMode(col, QHeaderView.ResizeToContents)
        for col, width in ((2, 220), (3, 230), (4, 150), (5, 330)):
            self.table.setColumnWidth(col, width)
        header.setStretchLastSection(True)
        self.table.resizeRowsToContents()
        self.table.itemChanged.connect(self._update)
        self.table.cellDoubleClicked.connect(self.open_drawing)
        box.addWidget(self.table, 1)
        buttons = QHBoxLayout()
        from .manual import help_button

        buttons.addWidget(help_button(self, "normokontrol-autocad"))
        self.counter = QLabel()
        buttons.addWidget(self.counter, 1)
        cancel = QPushButton(tr("Отмена — исправлю чертёж"))
        cancel.clicked.connect(self.reject)
        buttons.addWidget(cancel)
        self.make_button = QPushButton(tr("Сформировать отчёт"))
        self.make_button.setObjectName("sendButton")
        self.make_button.clicked.connect(self.accept)
        buttons.addWidget(self.make_button)
        box.addLayout(buttons)
        self._update()

    def open_drawing(self, *_args) -> str:
        file = self.result.drawing.file
        if file:
            QDesktopServices.openUrl(QUrl.fromLocalFile(file))
        return file

    def marks(self) -> dict:
        return {r["id"]: {"checked": self.table.item(row, 0).checkState() == Qt.Checked}
                for row, r in enumerate(self.result.remarks)}

    def _update(self, *_args) -> None:
        marks = self.marks()
        done = sum(1 for m in marks.values() if m["checked"])
        self.counter.setText(tr("Отмечено {done} из {n_marks}", done=done, n_marks=len(marks)))
        self.make_button.setEnabled(done == len(marks))


def report_links(result, path=None) -> list[dict]:
    """Links for the chat under the report: the workbook and the drawing."""
    links = []
    if path:
        links.append({"file": str(path), "label": tr("Отчёт: {value}", value=Path(path).name)})
    if result.drawing.file:
        links.append({"file": result.drawing.file, "label": tr("Чертёж: {title}", title=result.title)})
    return links
