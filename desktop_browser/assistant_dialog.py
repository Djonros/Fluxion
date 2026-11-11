"""«Личный помощник» (Связь, Pro): what Fluxion may watch on this computer (package assistant).

Three tabs: «Источники» — the connectors, each with what it reads, what it writes and
what it never does, its switch and its settings; «Уведомления» — the rules that turn an
event into a notice in the tray or in the bots; «Что знает Fluxion» — the journal itself,
searched, and deleted by the day or as a whole.

``AssistantHost`` carries the notices of the connectors' threads to the window.
"""
from __future__ import annotations

import time
from datetime import datetime

from PySide6.QtCore import QObject, Signal
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFileDialog, QFormLayout, QGroupBox,
                               QHBoxLayout, QHeaderView, QLabel, QLineEdit, QListWidget, QMessageBox, QPushButton,
                               QSpinBox, QTableWidget, QTableWidgetItem, QTabWidget, QVBoxLayout, QWidget)

from assistant import config
from assistant.activity import DEFAULT_EXCLUDE as ACTIVITY_EXCLUDE
from assistant.folders import DEFAULT_EXCLUDE as FOLDER_EXCLUDE
from assistant.hub import CONNECTORS, duration_text
from assistant.journal import start_of_day

from .bots_dialog import autostart_available, autostart_enabled, set_autostart
from .i18n import tr
from .manual import help_button

PAGE = "lichnyi-pomoshchnik"


class AssistantHost(QObject):
    notice = Signal(str, bool)  # text, to the bots too


def connector_texts() -> dict[str, dict]:
    """What the window says about each connector, in the language of the interface."""
    return {
        "folders": {
            "title": tr("Папки"),
            "about": tr("Что появилось и изменилось в выбранных папках: новые модели, чертежи, документы."),
            "reads": tr("имена, размер и время изменения файлов в выбранных папках"),
            "records": tr("путь файла, что с ним случилось и когда"),
            "never": tr("не открывает и не читает содержимое файлов; ничего не удаляет и не перемещает"),
        },
        "activity": {
            "title": tr("Активность"),
            "about": tr("В какой программе и над каким документом вы работали и сколько: «что я делал вчера», "
                        "часы по изделию."),
            "reads": tr("какое окно впереди (программа и заголовок) и сколько не трогали мышь и клавиатуру"),
            "records": tr("программа, заголовок окна, начало и длительность"),
            "never": tr("не записывает нажатия клавиш, не делает снимков экрана, не читает текст окон и адреса "
                        "в браузере, не пишет приватные окна браузера"),
        },
    }


def kind_titles() -> dict[str, str]:
    return {"": tr("любое событие"), "file.created": tr("новый файл"), "file.modified": tr("изменён файл"),
            "file.moved": tr("переименован файл"), "file.deleted": tr("удалён файл"),
            "app.focus": tr("работа в программе")}


def action_titles() -> dict[str, str]:
    return {"notify": tr("уведомление на компьютере"), "bots": tr("уведомление и сообщение в боты")}


class ConnectorBox(QGroupBox):
    def __init__(self, cid: str, cc: dict, state: tuple[str, bool] | None, parent=None):
        texts = connector_texts().get(cid) or {"title": cid, "about": "", "reads": "", "records": "", "never": ""}
        super().__init__(texts["title"], parent)
        self.cid = cid
        box = QVBoxLayout(self)
        self.enabled = QCheckBox(tr("Включить"))
        self.enabled.setChecked(cc["enabled"])
        box.addWidget(self.enabled)
        about = QLabel(f"{texts['about']}<br><b>{tr('Читает:')}</b> {texts['reads']}<br>"
                       f"<b>{tr('Записывает в журнал:')}</b> {texts['records']}<br>"
                       f"<b>{tr('Никогда:')}</b> {texts['never']}")
        about.setWordWrap(True)
        box.addWidget(about)
        self.cloud = QCheckBox(tr("Облачной модели можно видеть эти записи"))
        self.cloud.setToolTip(tr("Выключено — записи этого источника получает только модель на вашем компьютере"))
        self.cloud.setChecked(cc["cloud"])
        box.addWidget(self.cloud)
        settings = cc["settings"]
        form = QFormLayout()
        if cid == "folders":
            self.folders = QListWidget()
            self.folders.addItems([str(f) for f in settings.get("folders") or []])
            self.folders.setMaximumHeight(90)
            box.addWidget(self.folders)
            row = QHBoxLayout()
            add = QPushButton(tr("Добавить папку…"))
            add.clicked.connect(self._add_folder)
            drop = QPushButton(tr("Убрать папку"))
            drop.clicked.connect(lambda: [self.folders.takeItem(self.folders.row(i))
                                          for i in self.folders.selectedItems()])
            row.addWidget(add)
            row.addWidget(drop)
            row.addStretch(1)
            box.addLayout(row)
            self.mask = QLineEdit(str(settings.get("mask", "")))
            self.mask.setPlaceholderText(tr("все файлы; например *.a3d;*.cdw;*.pdf"))
            form.addRow(tr("Какие файлы:"), self.mask)
            self.exclude = QLineEdit(str(settings.get("exclude", FOLDER_EXCLUDE)))
            form.addRow(tr("Не следить за:"), self.exclude)
        elif cid == "activity":
            self.titles = QCheckBox(tr("Записывать заголовки окон (имя документа); выключено — только программу"))
            self.titles.setChecked(settings.get("titles", True) is not False)
            box.addWidget(self.titles)
            self.exclude = QLineEdit(str(settings.get("exclude", ACTIVITY_EXCLUDE)))
            self.exclude.setToolTip(tr("Эти программы не записываются совсем, через точку с запятой"))
            form.addRow(tr("Не записывать программы:"), self.exclude)
        box.addLayout(form)
        if state is not None:
            text, ok = state
            label = QLabel(tr("Работает") if ok else tr("Ошибка: {text}", text=text))
            label.setObjectName("hint")
            box.addWidget(label)

    def _add_folder(self) -> None:
        folder = QFileDialog.getExistingDirectory(self, tr("Папка, за которой следить"))
        if folder and not self.folders.findItems(folder, 0):
            self.folders.addItem(folder)

    def value(self) -> dict:
        settings: dict = {}
        if self.cid == "folders":
            settings = {"folders": [self.folders.item(i).text() for i in range(self.folders.count())],
                        "mask": self.mask.text().strip(), "exclude": self.exclude.text().strip()}
        elif self.cid == "activity":
            settings = {"titles": self.titles.isChecked(), "exclude": self.exclude.text().strip()}
        return {"enabled": self.enabled.isChecked(), "cloud": self.cloud.isChecked(), "settings": settings}


class AssistantDialog(QDialog):
    def __init__(self, parent=None, hub=None):
        super().__init__(parent)
        self.hub = hub
        self.setWindowTitle(tr("Личный помощник"))
        self.resize(820, 700)
        self.data = config.load()
        box = QVBoxLayout(self)
        intro = QLabel(tr(
            "Fluxion следит за тем, что вы разрешили, и отвечает в чате и в ботах: «что я делал вчера», «какие "
            "модели менялись за неделю». Всё хранится только на этом компьютере. Каждый источник включается "
            "отдельно, и видно, что он читает и чего не делает никогда."))
        intro.setWordWrap(True)
        box.addWidget(intro)
        top = QHBoxLayout()
        self.enabled = QCheckBox(tr("Включить личного помощника"))
        self.enabled.setChecked(self.data["enabled"])
        top.addWidget(self.enabled, 1)
        self.pause_button = QPushButton()
        self.pause_button.clicked.connect(self._toggle_pause)
        top.addWidget(self.pause_button)
        box.addLayout(top)
        self._refresh_pause()

        self.tabs = QTabWidget()
        box.addWidget(self.tabs, 1)
        self.tabs.addTab(self._sources_tab(), tr("Источники"))
        self.tabs.addTab(self._rules_tab(), tr("Уведомления"))
        self.tabs.addTab(self._journal_tab(), tr("Что знает Fluxion"))

        bottom = QHBoxLayout()
        bottom.addWidget(help_button(self, PAGE))
        bottom.addStretch(1)
        buttons = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        bottom.addWidget(buttons)
        box.addLayout(bottom)

    # ── Источники ──
    def _sources_tab(self) -> QWidget:
        page = QWidget()
        box = QVBoxLayout(page)
        status = self.hub.status() if self.hub is not None else {}
        self.boxes = []
        for cid in CONNECTORS:
            item = ConnectorBox(cid, config.connector(self.data, cid), status.get(cid), page)
            self.boxes.append(item)
            box.addWidget(item)
        self.autostart = QCheckBox(tr("Запускать Fluxion свёрнутым при входе в Windows — помощник работает сразу"))
        if autostart_available():
            self.autostart.setChecked(autostart_enabled())
        else:
            self.autostart.setEnabled(False)
            self.autostart.setToolTip(tr("Только у установленной программы"))
        box.addWidget(self.autostart)
        hint = QLabel(tr("Пока помощник включён, крестик окна сворачивает Fluxion в значок у часов; выйти — "
                         "правой кнопкой по значку → «Выход»."))
        hint.setWordWrap(True)
        hint.setObjectName("hint")
        box.addWidget(hint)
        box.addStretch(1)
        return page

    # ── Уведомления ──
    def _rules_tab(self) -> QWidget:
        page = QWidget()
        box = QVBoxLayout(page)
        intro = QLabel(tr("Когда случится событие — Fluxion покажет уведомление у часов или напишет в боты "
                          "(Связь → «Боты в мессенджерах», команда /уведомления). Маска — часть имени файла или "
                          "программы: *.a3d, 792720, kompas."))
        intro.setWordWrap(True)
        box.addWidget(intro)
        self.rules = QTableWidget(0, 4)
        self.rules.setHorizontalHeaderLabels([tr("Источник"), tr("Событие"), tr("Маска"), tr("Что сделать")])
        self.rules.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        for rule in self.data["rules"]:
            self._add_rule(rule)
        box.addWidget(self.rules, 1)
        row = QHBoxLayout()
        add = QPushButton(tr("Добавить правило"))
        add.clicked.connect(lambda: self._add_rule({"connector": "folders", "kind": "file.created",
                                                     "mask": "", "action": "notify"}))
        drop = QPushButton(tr("Убрать правило"))
        drop.clicked.connect(lambda: self.rules.removeRow(self.rules.currentRow()))
        row.addWidget(add)
        row.addWidget(drop)
        row.addStretch(1)
        box.addLayout(row)
        self.notify_tray = QCheckBox(tr("Показывать уведомления у часов"))
        self.notify_tray.setChecked(self.data["notify_tray"])
        box.addWidget(self.notify_tray)
        return page

    def _add_rule(self, rule: dict) -> None:
        r = self.rules.rowCount()
        self.rules.insertRow(r)
        source = QComboBox()
        for cid in CONNECTORS:
            source.addItem(connector_texts()[cid]["title"], cid)
        source.setCurrentIndex(max(source.findData(rule["connector"]), 0))
        kind = QComboBox()
        for key, title in kind_titles().items():
            kind.addItem(title, key)
        kind.setCurrentIndex(max(kind.findData(rule["kind"]), 0))
        action = QComboBox()
        for key, title in action_titles().items():
            action.addItem(title, key)
        action.setCurrentIndex(max(action.findData(rule["action"]), 0))
        self.rules.setCellWidget(r, 0, source)
        self.rules.setCellWidget(r, 1, kind)
        self.rules.setItem(r, 2, QTableWidgetItem(rule["mask"]))
        self.rules.setCellWidget(r, 3, action)

    def rule_values(self) -> list[dict]:
        out = []
        for r in range(self.rules.rowCount()):
            mask = self.rules.item(r, 2)
            out.append({"connector": self.rules.cellWidget(r, 0).currentData(),
                        "kind": self.rules.cellWidget(r, 1).currentData(),
                        "mask": mask.text().strip() if mask is not None else "",
                        "action": self.rules.cellWidget(r, 3).currentData()})
        return out

    # ── Что знает Fluxion ──
    def _journal_tab(self) -> QWidget:
        page = QWidget()
        box = QVBoxLayout(page)
        row = QHBoxLayout()
        self.search = QLineEdit()
        self.search.setPlaceholderText(tr("Найти в журнале: имя файла, программа, изделие"))
        self.search.returnPressed.connect(self.fill_journal)
        row.addWidget(self.search, 1)
        self.period = QComboBox()
        for days, title in ((0, tr("Сегодня")), (1, tr("Вчера")), (7, tr("7 дней")), (-1, tr("Всё"))):
            self.period.addItem(title, days)
        self.period.currentIndexChanged.connect(self.fill_journal)
        row.addWidget(self.period)
        find = QPushButton(tr("Найти"))
        find.clicked.connect(self.fill_journal)
        row.addWidget(find)
        box.addLayout(row)
        self.table = QTableWidget(0, 4)
        self.table.setHorizontalHeaderLabels([tr("Когда"), tr("Источник"), tr("Что"), tr("Сколько")])
        self.table.horizontalHeader().setSectionResizeMode(2, QHeaderView.Stretch)
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        box.addWidget(self.table, 1)
        self.journal_label = QLabel()
        self.journal_label.setObjectName("hint")
        box.addWidget(self.journal_label)
        row = QHBoxLayout()
        row.addWidget(QLabel(tr("Хранить записи, дней:")))
        self.retention = QSpinBox()
        self.retention.setRange(*config.RETENTION_LIMITS)
        self.retention.setValue(self.data["retention_days"])
        row.addWidget(self.retention)
        row.addStretch(1)
        today = QPushButton(tr("Удалить за сегодня"))
        today.clicked.connect(lambda: self._delete(start_of_day()))
        row.addWidget(today)
        everything = QPushButton(tr("Удалить всё"))
        everything.clicked.connect(lambda: self._delete(None))
        row.addWidget(everything)
        box.addLayout(row)
        self.fill_journal()
        return page

    def _range(self) -> tuple[float | None, float | None]:
        days = self.period.currentData()
        if days == 0:
            return start_of_day(), None
        if days == 1:
            return start_of_day(days_back=1), start_of_day()
        if days == 7:
            return start_of_day(days_back=6), None
        return None, None

    def fill_journal(self) -> None:
        self.table.setRowCount(0)
        if self.hub is None:
            return
        since, until = self._range()
        try:
            events = self.hub.journal.events(since, until, query=self.search.text().strip(), limit=1000)
            total = self.hub.journal.count()
        except Exception as exc:
            self.journal_label.setText(tr("Журнал не открылся: {exc}", exc=exc))
            return
        titles = {cid: t["title"] for cid, t in connector_texts().items()}
        for e in events:
            r = self.table.rowCount()
            self.table.insertRow(r)
            cells = (datetime.fromtimestamp(e.time).strftime("%d.%m %H:%M"), titles.get(e.connector, e.connector),
                     e.title, duration_text(e.duration) if e.duration else "")
            for c, text in enumerate(cells):
                item = QTableWidgetItem(text)
                item.setToolTip(f"{e.ref}\n{e.detail}".strip())
                self.table.setItem(r, c, item)
        self.table.resizeColumnToContents(0)
        self.journal_label.setText(tr("Показано {shown}, всего в журнале {total}", shown=len(events), total=total))

    def _delete(self, since: float | None) -> None:
        if self.hub is None:
            return
        text = tr("Удалить записи журнала за сегодня?") if since else tr("Удалить весь журнал помощника?")
        if QMessageBox.question(self, tr("Личный помощник"), text) != QMessageBox.Yes:
            return
        self.hub.journal.delete(since=since)
        self.fill_journal()

    # ── pause ──
    def _refresh_pause(self) -> None:
        if config.paused(self.data):
            until = datetime.fromtimestamp(self.data["paused_until"]).strftime("%H:%M")
            self.pause_button.setText(tr("Продолжить (пауза до {time})", time=until))
        else:
            self.pause_button.setText(tr("Пауза на 1 час"))

    def _toggle_pause(self) -> None:
        self.data["paused_until"] = 0 if config.paused(self.data) else time.time() + 3600
        if self.hub is not None:
            self.hub.pause(60 if self.data["paused_until"] else 0)
        self._refresh_pause()

    # ── saving ──
    def value(self) -> dict:
        data = dict(self.data)
        data["enabled"] = self.enabled.isChecked()
        data["connectors"] = dict(data["connectors"])
        for item in self.boxes:
            data["connectors"][item.cid] = item.value()
        data["rules"] = self.rule_values()
        data["notify_tray"] = self.notify_tray.isChecked()
        data["retention_days"] = self.retention.value()
        return data

    def save(self) -> None:
        data = config.load()
        data.update({k: v for k, v in self.value().items() if k != "paused_until"})
        config.save(data)
        if self.autostart.isEnabled() and self.autostart.isChecked() != autostart_enabled():
            try:
                set_autostart(self.autostart.isChecked())
            except OSError:
                pass
