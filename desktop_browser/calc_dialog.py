"""«Инженерный калькулятор»: a tab for each calculation of the package calc — the fields,
«Рассчитать», the result with the formulas and the verdict, the beam diagrams, the report.

The fields are built from ``FIELDS`` of each calculation; the calculation itself
is instant and runs here, in the window.  Saving the report (Excel, PDF) is a
feature of Fluxion Pro.
"""
from __future__ import annotations

import tempfile
from pathlib import Path

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QFont, QImage, QPainter, QPen, QPolygonF
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSplitter,
    QTabWidget,
    QTextBrowser,
    QVBoxLayout,
    QWidget,
)

import calc
from calc import fields as F
from calc import report
from calc import strength as strength_mod
from calc.core import CalcError

MANUAL_PAGE = "inzhenernyi-kalkulyator"
PROFILE_EXAMPLES = ("швеллер 16", "двутавр 20", "труба 80х40х3", "труба 60х60х4", "труба 57х3,5",
                    "гнутый швеллер 100х50х4", "полоса 100х10", "круг 40")


def draw_frame(result: calc.Result, width: int = 760) -> QImage:
    """A trailer frame from above: the members coloured by how loaded they are, the axles, the loads."""
    data = result.diagram or {}
    members = data.get("members") or []
    xs = [m[0] for m in members] + [m[2] for m in members]
    ys = [m[1] for m in members] + [m[3] for m in members]
    span_x, span_y = (max(xs) - min(xs)) or 1.0, (max(ys) - min(ys)) or 1.0
    scale = min((width - 80) / span_x, 220 / span_y)
    height = int(span_y * scale) + 110
    image = QImage(width, height, QImage.Format_ARGB32)
    image.fill(QColor("white"))
    painter = QPainter(image)
    painter.setRenderHint(QPainter.Antialiasing)
    painter.setFont(QFont("Segoe UI", 8))

    def pt(x: float, y: float) -> QPointF:
        return QPointF(40 + (x - min(xs)) * scale, 40 + (y - min(ys)) * scale)

    for x1, y1, x2, y2, ratio in members:
        color = "#1565C0" if ratio <= 1 / 3 else "#2E7D32" if ratio <= 1 else "#B45309" if ratio <= 1.5 else "#C62828"
        painter.setPen(QPen(QColor(color), 4, Qt.SolidLine, Qt.RoundCap))
        painter.drawLine(pt(x1, y1), pt(x2, y2))
    painter.setPen(QPen(QColor("#333"), 1.5))
    painter.setBrush(QColor("#ddd"))
    for x, y in data.get("supports", []):
        c = pt(x, y)
        painter.drawEllipse(c, 7, 7)
    painter.setBrush(QColor("#C62828"))
    for x, y in data.get("loads", []):
        c = pt(x, y)
        painter.drawPolygon(QPolygonF([c, QPointF(c.x() - 5, c.y() - 10), QPointF(c.x() + 5, c.y() - 10)]))
    painter.setPen(QColor("#222"))
    legend = [("#1565C0", "с большим запасом"), ("#2E7D32", "проходит"), ("#B45309", "мало"), ("#C62828", "не проходит")]
    x = 40
    for color, text in legend:
        painter.setPen(QPen(QColor(color), 4))
        painter.drawLine(QPointF(x, height - 18), QPointF(x + 18, height - 18))
        painter.setPen(QColor("#222"))
        painter.drawText(QPointF(x + 24, height - 14), text)
        x += 150
    painter.drawText(QPointF(40, 18), "Вид сверху: кружки — опоры (оси, сцепка), треугольники — грузы; спереди слева")
    painter.end()
    return image


def draw_diagram(result: calc.Result, width: int = 760, height: int = 330) -> QImage:
    """The beam with its supports, the bending moment and the deflection, one under another."""
    data = result.diagram or {}
    if data.get("type") == "frame":
        return draw_frame(result, width)
    image = QImage(width, height, QImage.Format_ARGB32)
    image.fill(QColor("white"))
    xs = data.get("x") or []
    if len(xs) < 2:
        return image
    painter = QPainter(image)
    painter.setRenderHint(QPainter.Antialiasing)
    painter.setFont(QFont("Segoe UI", 8))
    left, right = 50, width - 20
    length = xs[-1] or 1.0

    def px(x: float) -> float:
        return left + (right - left) * x / length

    def plot(values, top: float, band: float, color: str, title: str, unit: str, down_positive: bool) -> None:
        peak = max((abs(v) for v in values), default=0) or 1.0
        zero = top + band / 2
        painter.setPen(QPen(QColor("#999"), 1))
        painter.drawLine(QPointF(left, zero), QPointF(right, zero))
        sign = 1 if down_positive else -1
        points = [QPointF(px(x), zero + sign * v / peak * band / 2) for x, v in zip(xs, values)]
        poly = QPolygonF([QPointF(px(xs[0]), zero)] + points + [QPointF(px(xs[-1]), zero)])
        fill = QColor(color)
        fill.setAlpha(50)
        painter.setBrush(fill)
        painter.setPen(QPen(QColor(color), 1.5))
        painter.drawPolygon(poly)
        i = max(range(len(values)), key=lambda k: abs(values[k]))
        painter.setPen(QColor("#222"))
        painter.drawText(QPointF(4, top + 12), title)
        label = f"{calc.fmt(abs(values[i]))} {unit}"
        x = min(max(px(xs[i]) - 20, left), right - 70)
        painter.drawText(QPointF(x, zero + sign * (values[i] / peak) * band / 2 + (12 if sign * values[i] > 0 else -4)),
                         label)

    # the beam and its supports
    beam_y = 30
    painter.setPen(QPen(QColor("#333"), 3))
    painter.drawLine(QPointF(left, beam_y), QPointF(right, beam_y))
    painter.setPen(QPen(QColor("#333"), 1.5))
    painter.setBrush(QColor("#ddd"))
    for position, kind in data.get("supports", []):
        x = px(position)
        if kind == "заделка":
            painter.drawRect(QRectF(x - 4, beam_y - 14, 8, 28))
        else:
            painter.drawPolygon(QPolygonF([QPointF(x, beam_y + 2), QPointF(x - 8, beam_y + 16),
                                           QPointF(x + 8, beam_y + 16)]))
        painter.drawText(QPointF(x - 12, beam_y + 30), calc.fmt(position))
    painter.drawText(QPointF(4, beam_y + 4), "Балка")
    band = (height - 80) / 2
    plot(data["M"], 80, band - 14, "#1565C0", "M", "кН·м", down_positive=True)
    plot(data["f"], 80 + band, band - 14, "#B45309", "Прогиб", "мм", down_positive=True)
    painter.end()
    return image


class CalcTab(QWidget):
    """The fields of one calculation and its result."""

    _runs = 0

    def __init__(self, kind: str, owner: "CalcDialog"):
        super().__init__()
        self.kind = kind
        self.owner = owner
        self.module = calc.module(kind)
        self.widgets: dict[str, QWidget] = {}
        self.result: calc.Result | None = None
        layout = QHBoxLayout(self)
        splitter = QSplitter(Qt.Horizontal)
        layout.addWidget(splitter)
        form_box = QWidget()
        form = QFormLayout(form_box)
        form.setFieldGrowthPolicy(QFormLayout.AllNonFixedFieldsGrow)
        self.more = QCheckBox("Ещё параметры")
        advanced_rows = []
        for f in self.module.FIELDS:
            widget = self._widget(f)
            self.widgets[f.name] = widget
            title = f.title + (f", {f.unit}" if f.unit else "") + (" *" if f.required else "")
            label = QLabel(title)
            label.setWordWrap(True)
            if f.hint:
                label.setToolTip(f.hint)
                widget.setToolTip(f.hint)
            form.addRow(label, widget)
            if f.advanced:
                advanced_rows.append((label, widget))
        form.addRow(self.more)
        for label, widget in advanced_rows:
            label.setVisible(False)
            widget.setVisible(False)
        self.more.toggled.connect(lambda on: [(l.setVisible(on), w.setVisible(on)) for l, w in advanced_rows])
        button = QPushButton("Рассчитать")
        button.setDefault(True)
        button.clicked.connect(self.run)
        form.addRow(button)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(form_box)
        splitter.addWidget(scroll)
        self.view = QTextBrowser()
        self.view.setOpenExternalLinks(False)
        self.view.setPlaceholderText("Заполните поля со звёздочкой и нажмите «Рассчитать».")
        splitter.addWidget(self.view)
        splitter.setSizes([380, 620])
        self._diagram = ""

    def _widget(self, f: F.Field) -> QWidget:
        if f.kind == F.CHOICE:
            box = QComboBox()
            box.addItems(f.choices)
            box.setCurrentText(str(f.default))
            return box
        if f.kind == F.BOOL:
            check = QCheckBox()
            check.setChecked(bool(f.default))
            return check
        if f.kind == F.MATERIAL:
            box = QComboBox()
            box.setEditable(True)
            if not f.required and not f.default:
                box.addItem("")
            box.addItems([m.short for m in strength_mod.with_strength(f.material_kinds or None)])
            if f.default:
                box.setCurrentText(str(f.default))
            return box
        if f.kind == F.PROFILE:
            box = QComboBox()
            box.setEditable(True)
            box.addItems(PROFILE_EXAMPLES)
            box.setCurrentText("")
            return box
        edit = QLineEdit()
        if f.default not in (None, "", 0, 0.0):
            edit.setPlaceholderText(F.shown_value(f, f.default))
        elif f.hint and f.kind == F.TEXT:
            edit.setPlaceholderText(f.hint.split(":")[-1].strip()[:60])
        return edit

    def values(self) -> dict:
        raw = {}
        for name, widget in self.widgets.items():
            if isinstance(widget, QComboBox):
                raw[name] = widget.currentText()
            elif isinstance(widget, QCheckBox):
                raw[name] = widget.isChecked()
            else:
                raw[name] = widget.text()
        return raw

    def set_values(self, raw: dict) -> None:
        for name, value in raw.items():
            widget = self.widgets.get(name)
            if widget is None or value in (None, ""):
                continue
            if isinstance(widget, QComboBox):
                if isinstance(value, str) and widget.findText(value) < 0 and not widget.isEditable():
                    try:
                        value = F.choice(next(f for f in self.module.FIELDS if f.name == name), value)
                    except CalcError:
                        continue
                widget.setCurrentText(str(value))
            elif isinstance(widget, QCheckBox):
                try:
                    widget.setChecked(F.to_bool(value))
                except CalcError:
                    pass
            else:
                widget.setText(str(value))

    def run(self) -> None:
        try:
            self.result = self.module.calculate(self.values())
        except CalcError as exc:
            self.result = None
            self.view.setHtml(f"<p style='color:#C62828'><b>Не получается посчитать:</b> {report._e(exc)}</p>")
            self.owner.update_buttons()
            return
        self._diagram = ""
        if self.result.diagram:
            CalcTab._runs += 1   # a new file each time: QTextBrowser keeps images by their address
            path = Path(tempfile.gettempdir()) / f"fluxion_calc_{CalcTab._runs}.png"
            width = max(480, self.view.viewport().width() - 40)
            draw_diagram(self.result, width).save(str(path))
            self._diagram = path.as_uri()
        self.view.setHtml(report.to_html(self.result, self._diagram))
        self.owner.update_buttons()


class CalcDialog(QDialog):
    def __init__(self, parent=None, start: tuple[str, dict] | None = None):
        super().__init__(parent)
        self.setWindowTitle("Инженерный калькулятор")
        self.resize(1100, 720)
        box = QVBoxLayout(self)
        intro = QLabel("Расчёт по формулам: сварные швы, болты, балки, рама прицепа целиком, сжатые стойки, гибка листа, "
                       "валы. В чате — «/расчёт» и вопрос своими словами. Нагрузки можно в кгс и тс. Не считаются: "
                       "стеснённое кручение швеллеров, пространственная работа кузова, усталость сварных узлов.")
        intro.setWordWrap(True)
        intro.setObjectName("subtitle")
        box.addWidget(intro)
        self.tabs = QTabWidget()
        self.pages: dict[str, CalcTab] = {}
        for kind, (title, _) in calc.CALCS.items():
            tab = CalcTab(kind, self)
            self.pages[kind] = tab
            self.tabs.addTab(tab, title)
        self.tabs.currentChanged.connect(lambda _: self.update_buttons())
        box.addWidget(self.tabs)
        row = QHBoxLayout()
        from .manual import help_button

        row.addWidget(help_button(self, MANUAL_PAGE))
        row.addStretch(1)
        self.excel = QPushButton("Отчёт в Excel (Pro)…")
        self.excel.clicked.connect(lambda: self.save("xlsx"))
        self.pdf = QPushButton("Отчёт в PDF (Pro)…")
        self.pdf.clicked.connect(lambda: self.save("pdf"))
        close = QPushButton("Закрыть")
        close.clicked.connect(self.accept)
        for b in (self.excel, self.pdf, close):
            row.addWidget(b)
        box.addLayout(row)
        if start:
            kind, raw = start
            if kind in self.pages:
                self.tabs.setCurrentWidget(self.pages[kind])
                self.pages[kind].set_values(raw)
                self.pages[kind].run()
        self.update_buttons()

    @property
    def current(self) -> CalcTab:
        return self.tabs.currentWidget()

    def update_buttons(self) -> None:
        ready = self.current.result is not None
        self.excel.setEnabled(ready)
        self.pdf.setEnabled(ready)

    def save(self, fmt: str) -> None:
        from licensing import feature_enabled

        result = self.current.result
        if result is None:
            return
        if not feature_enabled(calc.PRO_FEATURE):
            QMessageBox.information(self, "Отчёт расчёта",
                                    "Отчёт в Excel и PDF — функция Pro: исходные данные, формулы с подстановкой, "
                                    "проверки, вывод и эпюры для балок. Сами расчёты бесплатны.\n"
                                    "Активировать: меню «Помощь» → «Лицензия…».")
            return
        name = result.title.split(":")[0] + (".xlsx" if fmt == "xlsx" else ".pdf")
        flt = "Excel (*.xlsx)" if fmt == "xlsx" else "PDF (*.pdf)"
        path, _ = QFileDialog.getSaveFileName(self, "Сохранить отчёт", name, flt)
        if not path:
            return
        try:
            if fmt == "xlsx":
                report.save_xlsx(result, Path(path))
            else:
                report.save_pdf(result, Path(path), self.current._diagram)
        except OSError as exc:
            QMessageBox.warning(self, "Отчёт расчёта", f"Не удалось сохранить: {exc}")
            return
        from PySide6.QtCore import QUrl
        from PySide6.QtGui import QDesktopServices

        QDesktopServices.openUrl(QUrl.fromLocalFile(path))
