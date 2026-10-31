"""«Нормы и цены завода» (Инструменты): the norms and prices the «Смета» of an assembly
report is built on (cad/estimate.py). Fluxion starts from recommended ones; every
value can be corrected, a corrected cell is highlighted, «Вернуть рекомендуемые»
puts a row or everything back."""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (QDialog, QDoubleSpinBox, QFileDialog, QFormLayout, QHBoxLayout, QHeaderView, QLabel,
                               QMenu, QMessageBox, QPushButton, QTableWidget, QTableWidgetItem, QTabWidget,
                               QVBoxLayout, QWidget)

from cad import estimate

from .i18n import tr

EDITED = QColor("#B45309")  # a value that differs from the recommended one


def _number(text: str) -> float:
    try:
        return float(str(text).replace(",", ".").replace(" ", "")) if str(text).strip() else 0.0
    except ValueError:
        return 0.0


def _fmt(value: float) -> str:
    return f"{value:g}"


class NormsDialog(QDialog):
    def __init__(self, parent=None, norms: dict | None = None):
        super().__init__(parent)
        self.setWindowTitle(tr("Нормы и цены завода"))
        self.resize(900, 560)
        self.recommended = estimate.recommended()
        self.norms = estimate.normalize(norms) if norms is not None else estimate.load_norms()
        self._filling = False
        box = QVBoxLayout(self)
        intro = QLabel(tr("Смета отчёта по сборке считается по этим нормам и ценам. {basis} "
                          "Исправленные значения выделены цветом; правая кнопка по строке — «Вернуть рекомендуемое».",
                          basis=tr(estimate.BASIS)))
        intro.setWordWrap(True)
        box.addWidget(intro)
        self.state = QLabel()
        box.addWidget(self.state)

        self.tabs = QTabWidget()
        self.metal = self._table([tr("Правило (что искать в материале)"), tr("Название"), tr("Цена, ₽/кг"),
                                  tr("Отход, %")])
        self.work = self._table([tr("Операция"), tr("Единица"), tr("Часов на единицу"), tr("Ставка, ₽/ч (0 — общая)")])
        self.items = self._table([tr("Наименование (часть)"), tr("Цена, ₽/шт")])
        self.tabs.addTab(self._with_rows(self.metal, self._add_metal), tr("Металл"))
        self.tabs.addTab(self.work, tr("Работы"))
        self.tabs.addTab(self._with_rows(self.items, self._add_item), tr("Покупные и крепёж"))
        general = QWidget()
        form = QFormLayout(general)
        self.general = {}
        for key, title in estimate.GENERAL:
            spin = QDoubleSpinBox()
            spin.setRange(0, 10_000_000)
            spin.setDecimals(2)
            spin.valueChanged.connect(self._refresh)
            form.addRow(tr(title), spin)
            self.general[key] = spin
        self.tabs.addTab(general, tr("Общее"))
        box.addWidget(self.tabs, 1)

        buttons = QHBoxLayout()
        from .manual import help_button

        buttons.addWidget(help_button(self, "smeta"))
        reset = QPushButton(tr("Вернуть рекомендуемые"))
        reset.setToolTip(tr("Все нормы и цены — снова рекомендуемые"))
        reset.clicked.connect(self.reset_all)
        buttons.addWidget(reset)
        export = QPushButton(tr("Экспорт в Excel…"))
        export.clicked.connect(self._export)
        buttons.addWidget(export)
        imp = QPushButton(tr("Импорт из Excel…"))
        imp.clicked.connect(self._import)
        buttons.addWidget(imp)
        buttons.addStretch(1)
        cancel = QPushButton(tr("Отмена"))
        cancel.clicked.connect(self.reject)
        buttons.addWidget(cancel)
        save = QPushButton(tr("Сохранить"))
        save.setObjectName("sendButton")
        save.clicked.connect(self.save)
        buttons.addWidget(save)
        box.addLayout(buttons)
        self.fill(self.norms)

    # ── tables ───────────────────────────────────────────────────────────

    def _table(self, headers: list[str]) -> QTableWidget:
        table = QTableWidget(0, len(headers))
        table.setHorizontalHeaderLabels(headers)
        table.verticalHeader().setVisible(False)
        table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        table.itemChanged.connect(self._refresh)
        table.setContextMenuPolicy(Qt.CustomContextMenu)
        table.customContextMenuRequested.connect(lambda pos, t=table: self._row_menu(t, pos))
        return table

    def _with_rows(self, table: QTableWidget, add) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(table, 1)
        row = QHBoxLayout()
        plus = QPushButton(tr("Добавить строку"))
        plus.clicked.connect(add)
        minus = QPushButton(tr("Удалить строку"))
        minus.clicked.connect(lambda: table.removeRow(table.currentRow()) if table.currentRow() >= 0 else None)
        minus.clicked.connect(self._refresh)
        row.addWidget(plus)
        row.addWidget(minus)
        row.addStretch(1)
        layout.addLayout(row)
        return page

    @staticmethod
    def _cell(text: str, editable: bool = True) -> QTableWidgetItem:
        item = QTableWidgetItem(text)
        if not editable:
            item.setFlags(Qt.ItemIsEnabled | Qt.ItemIsSelectable)
        return item

    def _add_metal(self) -> None:
        row = self.metal.rowCount()
        self.metal.insertRow(row)
        for col, text in enumerate(("", "", "0", "0")):
            self.metal.setItem(row, col, self._cell(text))
        self.metal.setCurrentCell(row, 0)

    def _add_item(self) -> None:
        row = self.items.rowCount()
        self.items.insertRow(row)
        self.items.setItem(row, 0, self._cell(""))
        self.items.setItem(row, 1, self._cell("0"))
        self.items.setCurrentCell(row, 0)

    def fill(self, norms: dict) -> None:
        self._filling = True
        norms = estimate.normalize(norms)
        self.metal.setRowCount(0)
        for m in norms["metal"]:
            row = self.metal.rowCount()
            self.metal.insertRow(row)
            for col, text in enumerate((m["match"], m["name"], _fmt(m["price_kg"]), _fmt(m["waste_pct"]))):
                self.metal.setItem(row, col, self._cell(text))
        names = {key: (name, unit) for key, name, unit in estimate.OPERATIONS}
        self.work.setRowCount(0)
        for o in norms["operations"]:
            row = self.work.rowCount()
            self.work.insertRow(row)
            name, unit = names[o["key"]]
            self.work.setItem(row, 0, self._cell(tr(name), False))
            self.work.item(row, 0).setData(Qt.UserRole, o["key"])
            self.work.setItem(row, 1, self._cell(tr(unit), False))
            self.work.setItem(row, 2, self._cell(_fmt(o["hours"])))
            self.work.setItem(row, 3, self._cell(_fmt(o["rate"])))
        self.items.setRowCount(0)
        for i in norms["items"]:
            row = self.items.rowCount()
            self.items.insertRow(row)
            self.items.setItem(row, 0, self._cell(i["match"]))
            self.items.setItem(row, 1, self._cell(_fmt(i["price"])))
        for key, spin in self.general.items():
            spin.setValue(norms[key])
        self._filling = False
        self._refresh()

    def collect(self) -> dict:
        """The norms as typed in the window."""
        def text(table, row, col) -> str:
            item = table.item(row, col)
            return item.text().strip() if item is not None else ""

        norms = {key: spin.value() for key, spin in self.general.items()}
        norms["metal"] = [{"match": text(self.metal, r, 0), "name": text(self.metal, r, 1),
                           "price_kg": _number(text(self.metal, r, 2)), "waste_pct": _number(text(self.metal, r, 3))}
                          for r in range(self.metal.rowCount())]
        norms["operations"] = [{"key": self.work.item(r, 0).data(Qt.UserRole), "hours": _number(text(self.work, r, 2)),
                                "rate": _number(text(self.work, r, 3))} for r in range(self.work.rowCount())]
        norms["items"] = [{"match": text(self.items, r, 0), "price": _number(text(self.items, r, 1))}
                          for r in range(self.items.rowCount()) if text(self.items, r, 0)]
        return estimate.normalize(norms)

    # ── what differs from the recommended norms ──────────────────────────

    def _recommended_metal(self, row: int) -> dict | None:
        rec = self.recommended["metal"]
        return rec[row] if row < len(rec) else None

    def _refresh(self, *_args) -> None:
        if self._filling:
            return
        self._filling = True
        norms = self.collect()
        for row, m in enumerate(norms["metal"]):
            rec = self._recommended_metal(row)
            values = (m["match"], m["name"], m["price_kg"], m["waste_pct"])
            recs = (rec["match"], rec["name"], rec["price_kg"], rec["waste_pct"]) if rec else (None,) * 4
            for col, (value, rec_value) in enumerate(zip(values, recs)):
                self._mark(self.metal.item(row, col), value != rec_value)
        for row, (o, rec) in enumerate(zip(norms["operations"], self.recommended["operations"])):
            self._mark(self.work.item(row, 2), o["hours"] != rec["hours"])
            self._mark(self.work.item(row, 3), o["rate"] != rec["rate"])
        for row in range(self.items.rowCount()):
            for col in (0, 1):
                self._mark(self.items.item(row, col), True)  # the recommended list is empty: every price is the plant's
        for key, spin in self.general.items():
            edited = norms[key] != self.recommended[key]
            spin.setStyleSheet(f"color: {EDITED.name()}; font-weight: 600;" if edited else "")
        changed = estimate.changed(norms)
        self.state.setText(tr("Сейчас: рекомендуемые нормы.") if not changed
                           else tr("Сейчас: нормы завода — исправлено {n}: {what}.", n=len(changed),
                                   what=", ".join(changed[:6]) + ("…" if len(changed) > 6 else "")))
        self._filling = False

    @staticmethod
    def _mark(item, edited: bool) -> None:
        if item is None:
            return
        font = item.font()
        font.setBold(edited)
        item.setFont(font)
        if edited:
            item.setForeground(EDITED)
            item.setToolTip(tr("Исправлено: не как в рекомендуемых нормах"))
        else:
            item.setData(Qt.ForegroundRole, None)
            item.setToolTip("")

    def _row_menu(self, table: QTableWidget, pos) -> None:
        row = table.rowAt(pos.y())
        if row < 0:
            return
        menu = QMenu(table)
        action = menu.addAction(tr("Вернуть рекомендуемое для строки"), lambda: self.reset_row(table, row))
        action.setEnabled(table is not self.items and (table is self.work or self._recommended_metal(row) is not None))
        menu.exec(table.viewport().mapToGlobal(pos))

    def reset_row(self, table: QTableWidget, row: int) -> None:
        if table is self.work:
            rec = self.recommended["operations"][row]
            table.item(row, 2).setText(_fmt(rec["hours"]))
            table.item(row, 3).setText(_fmt(rec["rate"]))
        elif table is self.metal and self._recommended_metal(row) is not None:
            rec = self._recommended_metal(row)
            for col, value in enumerate((rec["match"], rec["name"], _fmt(rec["price_kg"]), _fmt(rec["waste_pct"]))):
                table.item(row, col).setText(value)
        self._refresh()

    def reset_all(self) -> None:
        answer = QMessageBox.question(self, tr("Вернуть рекомендуемые"),
                                      tr("Вернуть все нормы и цены к рекомендуемым? Ваши исправления и цены покупных "
                                         "в окне пропадут (файл изменится, только когда нажмёте «Сохранить»)."),
                                      QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
        if answer == QMessageBox.Yes:
            self.fill(self.recommended)

    # ── files ────────────────────────────────────────────────────────────

    def _export(self) -> None:
        path, _ = QFileDialog.getSaveFileName(self, tr("Экспорт норм в Excel"), "Нормы и цены завода.xlsx",
                                              "Excel (*.xlsx)")
        if not path:
            return
        try:
            estimate.export_xlsx(self.collect(), path)
        except Exception as exc:
            QMessageBox.warning(self, tr("Экспорт в Excel"), tr("Не удалось сохранить: {exc}", exc=exc))

    def _import(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, tr("Импорт норм из Excel"), "", "Excel (*.xlsx)")
        if not path:
            return
        try:
            self.fill(estimate.import_xlsx(path, self.collect()))
        except Exception as exc:
            QMessageBox.warning(self, tr("Импорт из Excel"), tr("Не удалось прочитать: {exc}", exc=exc))

    def save(self) -> None:
        try:
            estimate.save_norms(self.collect())
        except OSError as exc:
            QMessageBox.warning(self, tr("Нормы и цены завода"), tr("Не удалось сохранить: {exc}", exc=exc))
            return
        self.accept()
