"""«Раскрой листа» (Лазер): an Excel order, the list of remnants of the shop, the folders of
the DXF — the details nested on remnants and sheets (cad/nesting), a DXF and a picture of
every sheet, «Отчёт раскроя.xlsx» and «Остатки — изменения.xlsx»."""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt, QThread, QUrl, Signal
from PySide6.QtGui import QColor, QDesktopServices
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QDialog,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QPushButton,
    QSpinBox,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from .i18n import tr

EXCEL_FILTER = "Excel (*.xlsx *.xlsm)"
ROTATIONS = {"all": (0.0, 90.0, 180.0, 270.0), "half": (0.0, 180.0), "none": (0.0,)}


def _spin(value: float, top: float, step: float = 1.0, decimals: int = 0) -> QDoubleSpinBox:
    spin = QDoubleSpinBox()
    spin.setRange(0, top)
    spin.setDecimals(decimals)
    spin.setSingleStep(step)
    spin.setValue(value)
    return spin


class NestingDialog(QDialog):
    def __init__(self, parent=None, qsettings=None, archive_dir: str = "", order_path: str = ""):
        super().__init__(parent)
        self.setWindowTitle(tr("Раскрой листа"))
        self.resize(940, 800)
        self.qsettings = qsettings
        self.archive_dir = archive_dir
        self.job = None
        box = QVBoxLayout(self)
        intro = QLabel(tr("Детали заявки Excel раскладываются по форме из DXF: сначала на деловые остатки той же "
                          "толщины и марки, потом на целые листы. DXF ищутся по обозначению в папке заявки, в "
                          "указанной папке и в «Архиве КД»."))
        intro.setWordWrap(True)
        box.addWidget(intro)
        form = QFormLayout()
        self.order = QLineEdit(order_path or self._value("nesting_order"))
        form.addRow(tr("Заявка:"), self._picker(self.order, self._pick_order))
        self.products = QSpinBox()
        self.products.setRange(1, 100000)
        self.products.setValue(1)
        form.addRow(tr("Изделий:"), self.products)
        self.stock = QLineEdit(self._value("nesting_stock"))
        self.stock.setPlaceholderText(tr("необязательно"))
        self.use_stock = QCheckBox(tr("Брать деловые остатки"))
        self.use_stock.setChecked(self._value("nesting_use_stock", "1") == "1")
        stock_row = self._picker(self.stock, self._pick_stock)
        stock_row.layout().addWidget(self.use_stock)
        form.addRow(tr("Деловые остатки:"), stock_row)
        self.dxf = QLineEdit(self._value("nesting_dxf"))
        self.dxf.setPlaceholderText(tr("папка заявки и её подпапки"))
        form.addRow(tr("Папка с DXF:"), self._picker(self.dxf, self._pick_dxf))
        self.archive = QCheckBox(tr("Искать DXF и в «Архиве КД»"))
        has_archive = bool(archive_dir) and self._archive_ready()
        self.archive.setEnabled(has_archive)
        self.archive.setChecked(has_archive and self._value("nesting_archive", "1") == "1")
        form.addRow("", self.archive)
        sheet = QWidget()
        sheet_row = QHBoxLayout(sheet)
        sheet_row.setContentsMargins(0, 0, 0, 0)
        self.sheet_l = _spin(float(self._value("nesting_sheet_l", "6000")), 20000, 100)
        self.sheet_w = _spin(float(self._value("nesting_sheet_w", "1500")), 5000, 50)
        sheet_row.addWidget(self.sheet_l)
        sheet_row.addWidget(QLabel("×"))
        sheet_row.addWidget(self.sheet_w)
        sheet_row.addWidget(QLabel(tr("мм")))
        sheet_row.addStretch(1)
        form.addRow(tr("Целый лист:"), sheet)
        gaps = QWidget()
        gaps_row = QHBoxLayout(gaps)
        gaps_row.setContentsMargins(0, 0, 0, 0)
        self.gap = _spin(float(self._value("nesting_gap", "8")), 200, 0.5, 1)
        self.margin = _spin(float(self._value("nesting_margin", "10")), 200, 0.5, 1)
        gaps_row.addWidget(QLabel(tr("между деталями")))
        gaps_row.addWidget(self.gap)
        gaps_row.addWidget(QLabel(tr("от края листа")))
        gaps_row.addWidget(self.margin)
        gaps_row.addWidget(QLabel(tr("мм")))
        gaps_row.addStretch(1)
        form.addRow(tr("Зазоры:"), gaps)
        rest = QWidget()
        rest_row = QHBoxLayout(rest)
        rest_row.setContentsMargins(0, 0, 0, 0)
        self.rest_a = _spin(float(self._value("nesting_rest_a", "300")), 5000, 50)
        self.rest_b = _spin(float(self._value("nesting_rest_b", "300")), 5000, 50)
        rest_row.addWidget(self.rest_a)
        rest_row.addWidget(QLabel("×"))
        rest_row.addWidget(self.rest_b)
        rest_row.addWidget(QLabel(tr("мм, меньше — в отход")))
        rest_row.addStretch(1)
        form.addRow(tr("Новый деловой остаток от:"), rest)
        self.rotations = QComboBox()
        self.rotations.addItem(tr("0°, 90°, 180°, 270°"), "all")
        self.rotations.addItem(tr("0° и 180° (вдоль проката)"), "half")
        self.rotations.addItem(tr("Без поворота"), "none")
        index = self.rotations.findData(self._value("nesting_rotations", "all"))
        self.rotations.setCurrentIndex(max(0, index))
        form.addRow(tr("Повороты деталей:"), self.rotations)
        self._program_rows(form)
        self.out = QLineEdit(self._value("nesting_out"))
        self.out.setPlaceholderText(tr("Документы\\Fluxion CAD\\Раскрой\\<заявка> <дата>"))
        form.addRow(tr("Сохранить в:"), self._picker(self.out, self._pick_out))
        box.addLayout(form)
        self.table = QTableWidget(0, 5)
        self.table.setHorizontalHeaderLabels([tr("Обозначение"), tr("Наименование"), tr("Материал"), tr("Кол-во"),
                                              tr("DXF")])
        self.table.verticalHeader().setVisible(False)
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(1, QHeaderView.Stretch)
        header.setSectionResizeMode(4, QHeaderView.Stretch)
        box.addWidget(self.table, 1)
        self.status = QLabel(tr("Выберите заявку и нажмите «Проверить заявку»."))
        self.status.setWordWrap(True)
        box.addWidget(self.status)
        buttons = QHBoxLayout()
        from .manual import help_button

        buttons.addWidget(help_button(self, "raskroy-lista"))
        check = QPushButton(tr("Проверить заявку"))
        check.clicked.connect(self.check)
        buttons.addWidget(check)
        buttons.addStretch(1)
        cancel = QPushButton(tr("Отмена"))
        cancel.clicked.connect(self.reject)
        self.ok = QPushButton(tr("Разложить"))
        self.ok.setObjectName("sendButton")
        self.ok.clicked.connect(self._accept)
        buttons.addWidget(cancel)
        buttons.addWidget(self.ok)
        box.addLayout(buttons)
        if self.order.text():
            self._order_changed()

    # ── the program of the machine ───────────────────────────────────────
    def _program_rows(self, form: QFormLayout) -> None:
        from cad.nesting.post import load_machines

        machines, self.machine_errors = load_machines()
        self.machines = {m.id: m for m in machines}
        row = QWidget()
        layout = QHBoxLayout(row)
        layout.setContentsMargins(0, 0, 0, 0)
        self.machine = QComboBox()
        self.machine.addItem(tr("Без программы — только DXF"), "")
        for m in machines:
            self.machine.addItem(m.name, m.id)
        self.machine.setCurrentIndex(max(0, self.machine.findData(self._value("nesting_machine", "hasa-aria-maxcut250"))))
        layout.addWidget(self.machine, 1)
        edit = QPushButton(tr("Профиль станка…"))
        edit.setToolTip(tr("Коды программы и таблица технологий (рез, подача, прожиг по толщинам) — файл JSON, "
                           "как настройки станка в ProNest"))
        edit.clicked.connect(self._edit_machine)
        layout.addWidget(edit)
        form.addRow(tr("Станок:"), row)
        tech = QWidget()
        tech_row = QHBoxLayout(tech)
        tech_row.setContentsMargins(0, 0, 0, 0)
        self.kerf = _spin(float(self._value("nesting_kerf", "0")), 10, 0.1, 2)
        self.kerf.setSpecialValueText(tr("из таблицы"))
        self.lead_in = _spin(float(self._value("nesting_lead", "0")), 50, 0.5, 1)
        self.lead_in.setSpecialValueText(tr("из таблицы"))
        self.overcut = _spin(float(self._value("nesting_overrun", "0")), 20, 0.1, 2)
        self.overcut.setSpecialValueText(tr("из таблицы"))
        for label, widget in ((tr("рез"), self.kerf), (tr("подвод"), self.lead_in), (tr("перебег"), self.overcut)):
            tech_row.addWidget(QLabel(label))
            tech_row.addWidget(widget)
        tech_row.addWidget(QLabel(tr("мм")))
        tech_row.addStretch(1)
        form.addRow(tr("Резка:"), tech)
        bridges = QWidget()
        bridges_row = QHBoxLayout(bridges)
        bridges_row.setContentsMargins(0, 0, 0, 0)
        self.bridges = QSpinBox()
        self.bridges.setRange(0, 10)
        self.bridges.setValue(int(self._value("nesting_bridges", "0")))
        self.bridge_width = _spin(float(self._value("nesting_bridge_width", "1")), 20, 0.1, 1)
        self.bridge_min = _spin(float(self._value("nesting_bridge_min", "0")), 10000, 10)
        self.small_hole = _spin(float(self._value("nesting_small_hole", "0")), 100, 0.5, 1)
        for label, widget in ((tr("на контур"), self.bridges), (tr("шириной"), self.bridge_width),
                              (tr("на деталях от"), self.bridge_min)):
            bridges_row.addWidget(QLabel(label))
            bridges_row.addWidget(widget)
        bridges_row.addWidget(QLabel(tr("мм")))
        bridges_row.addStretch(1)
        form.addRow(tr("Мостики:"), bridges)
        holes = QWidget()
        holes_row = QHBoxLayout(holes)
        holes_row.setContentsMargins(0, 0, 0, 0)
        holes_row.addWidget(self.small_hole)
        holes_row.addWidget(QLabel(tr("мм — отверстия меньше не резать (0 — резать все)")))
        holes_row.addStretch(1)
        form.addRow(tr("Мелкие отверстия:"), holes)

    def _edit_machine(self) -> None:
        from cad.nesting.post import copy_to_user

        machine = self.machines.get(self.machine.currentData())
        if machine is None:
            self.status.setText(tr("Выберите станок."))
            return
        path = copy_to_user(machine)
        self.status.setText(tr("Профиль станка: {path}. После правки закройте и откройте это окно заново.", path=path))
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(path)))

    def program_settings(self):
        """(post.Machine or None, cutting.CutSettings) of the window."""
        from cad.nesting.cutting import CutSettings

        machine = self.machines.get(self.machine.currentData())
        cut = CutSettings(kerf=self.kerf.value(), lead_in=self.lead_in.value(), overcut=self.overcut.value(),
                          bridges=self.bridges.value(), bridge_width=self.bridge_width.value(),
                          bridge_min_size=self.bridge_min.value(), small_hole=self.small_hole.value())
        return machine, cut

    # ── settings ─────────────────────────────────────────────────────────
    def _value(self, key: str, default: str = "") -> str:
        if self.qsettings is None:
            return default
        return str(self.qsettings.value(key, default) or default)

    def _save(self) -> None:
        if self.qsettings is None:
            return
        for key, value in (("nesting_order", self.order.text()), ("nesting_stock", self.stock.text()),
                           ("nesting_use_stock", "1" if self.use_stock.isChecked() else "0"),
                           ("nesting_dxf", self.dxf.text()), ("nesting_archive", "1" if self.archive.isChecked() else "0"),
                           ("nesting_sheet_l", f"{self.sheet_l.value():g}"), ("nesting_sheet_w", f"{self.sheet_w.value():g}"),
                           ("nesting_gap", f"{self.gap.value():g}"), ("nesting_margin", f"{self.margin.value():g}"),
                           ("nesting_rest_a", f"{self.rest_a.value():g}"), ("nesting_rest_b", f"{self.rest_b.value():g}"),
                           ("nesting_rotations", self.rotations.currentData()), ("nesting_out", self.out.text()),
                           ("nesting_machine", self.machine.currentData()), ("nesting_kerf", f"{self.kerf.value():g}"),
                           ("nesting_lead", f"{self.lead_in.value():g}"),
                           ("nesting_overrun", f"{self.overcut.value():g}"),
                           ("nesting_bridges", str(self.bridges.value())),
                           ("nesting_bridge_width", f"{self.bridge_width.value():g}"),
                           ("nesting_bridge_min", f"{self.bridge_min.value():g}"),
                           ("nesting_small_hole", f"{self.small_hole.value():g}")):
            self.qsettings.setValue(key, value)

    def _archive_ready(self) -> bool:
        try:
            from rag.archive import DB_NAME

            return (Path(self.archive_dir) / DB_NAME).is_file()
        except Exception:  # no archive module in a stripped build
            return False

    # ── pickers ──────────────────────────────────────────────────────────
    def _picker(self, edit: QLineEdit, slot) -> QWidget:
        row = QWidget()
        layout = QHBoxLayout(row)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(edit, 1)
        browse = QPushButton(tr("Обзор…"))
        browse.clicked.connect(slot)
        layout.addWidget(browse)
        return row

    def _start(self, edit: QLineEdit) -> str:
        text = edit.text() or self.order.text()
        return str(Path(text).parent) if text and Path(text).suffix else text

    def _pick_order(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, tr("Заявка на раскрой"), self._start(self.order), EXCEL_FILTER)
        if path:
            self.order.setText(path)
            self._order_changed()

    def _order_changed(self) -> None:
        from cad.nesting.orders import products_in_name

        self.products.setValue(products_in_name(self.order.text()))
        self.job = None
        self.table.setRowCount(0)

    def _pick_stock(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, tr("Список деловых остатков"), self._start(self.stock), EXCEL_FILTER)
        if path:
            self.stock.setText(path)
            self.use_stock.setChecked(True)
            self.job = None

    def _pick_dxf(self) -> None:
        folder = QFileDialog.getExistingDirectory(self, tr("Папка с DXF"), self._start(self.dxf))
        if folder:
            self.dxf.setText(folder)
            self.job = None

    def _pick_out(self) -> None:
        folder = QFileDialog.getExistingDirectory(self, tr("Куда сохранить раскрой"), self.out.text())
        if folder:
            self.out.setText(folder)

    # ── check ────────────────────────────────────────────────────────────
    def settings(self):
        from cad.nesting import Settings

        small, large = sorted((self.rest_a.value(), self.rest_b.value()))
        return Settings(gap=self.gap.value(), margin=self.margin.value(),
                        rotations=ROTATIONS.get(self.rotations.currentData(), ROTATIONS["all"]),
                        min_remnant=(small, large), use_remnants=self.use_stock.isChecked(),
                        sheet_sizes=((max(self.sheet_l.value(), self.sheet_w.value()),
                                      min(self.sheet_l.value(), self.sheet_w.value())),))

    def check(self) -> bool:
        """Read the order and the remnants, find the DXF; the table shows what was found."""
        from cad.nesting import prepare

        order = self.order.text().strip()
        if not order or not Path(order).is_file():
            self.status.setText(tr("Заявка не выбрана или файла нет."))
            return False
        stock = self.stock.text().strip() if self.use_stock.isChecked() else ""
        if stock and not Path(stock).is_file():
            self.status.setText(tr("Файла деловых остатков нет: {path}", path=stock))
            return False
        QApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            self.job = prepare(order, stock or None, [self.dxf.text().strip()], self.products.value(),
                               self.archive_dir if self.archive.isChecked() else None)
        except Exception as exc:  # a broken workbook, a file open in Excel
            self.job = None
            self.status.setText(tr("Не удалось прочитать: {exc}", exc=exc))
            return False
        finally:
            QApplication.restoreOverrideCursor()
        self._fill(self.job)
        return True

    def _fill(self, job) -> None:
        self.table.setRowCount(0)
        red = QColor("#c62828")
        for part in job.parts:
            item = part.item
            row = self.table.rowCount()
            self.table.insertRow(row)
            material = item.material.label() if item.material else item.material_text
            dxf = part.error or (part.path.name if part.path else "")
            for col, text in enumerate((item.designation, item.name, material, str(item.quantity), dxf)):
                cell = QTableWidgetItem(text)
                if col == 4:
                    cell.setToolTip(str(part.path) if part.path else part.error)
                    if part.error:
                        cell.setForeground(red)
                self.table.setItem(row, col, cell)
        found = sum(1 for p in job.parts if p.shape is not None)
        problems = job.order.problems + (job.stock.problems if job.stock else [])
        remnants = 0
        if job.stock:
            remnants = sum(s.quantity for s in job.stock.sheets if s.remnant)
        text = tr("Деталей в заявке: {total}, DXF найдено и прочитано: {found}.", total=len(job.parts), found=found)
        if job.stock:
            text += " " + tr("Деловых остатков в списке: {count}.", count=remnants)
        if problems:
            text += "\n" + "\n".join(problems[:5])
        self.status.setText(text)

    def _accept(self) -> None:
        if self.job is None and not self.check():
            return
        if not any(p.shape is not None for p in self.job.parts):
            self.status.setText(tr("Ни для одной детали нет DXF — раскладывать нечего."))
            return
        self._save()
        self.accept()


class NestingWorker(QThread):
    progress = Signal(str)
    ready = Signal(object)  # cad.nesting.Job
    failed = Signal(str)

    def __init__(self, job, settings, out: str = "", parent=None, machine=None, cut=None):
        super().__init__(parent)
        self.job, self.settings, self.out = job, settings, out
        self.machine, self.cut = machine, cut

    def run(self) -> None:
        from cad.nesting import run

        try:
            run(self.job, self.settings, self.out or None, self.progress.emit, self.machine, self.cut)
        except Exception as exc:  # a file open in Excel, a folder without rights
            self.failed.emit(tr("Раскрой не получился: {exc}", exc=exc))
            return
        self.ready.emit(self.job)
