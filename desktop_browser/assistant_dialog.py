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

from PySide6.QtCore import QObject, QThread, Signal
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFileDialog, QFormLayout, QGroupBox,
                               QHBoxLayout, QHeaderView, QLabel, QLineEdit, QListWidget, QMessageBox, QPushButton,
                               QSpinBox, QTableWidget, QTableWidgetItem, QTabWidget, QVBoxLayout, QWidget)

from assistant import apps as app_kinds
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


class HelperWorker(QThread):
    """«/помощник …»: the agent of the assistant calls the tools of the apps in a background thread.
    A tool that needs the user's «Да» asks the window (``ask``, a blocking queued signal)."""

    step = Signal(str)
    done = Signal(str)
    failed = Signal(str)
    ask = Signal(str, str)  # what the tool is, its arguments → the window sets ``answer``

    def __init__(self, hub, backend, task: str, context: str = "", cloud: bool = False, parent=None):
        super().__init__(parent)
        self.hub, self.backend, self.task, self.context, self.cloud = hub, backend, task, context, cloud
        self.answer = False
        self.agent = None

    def confirm(self, tool, args: dict) -> bool:
        import json

        self.answer = False
        self.ask.emit(f"{tool.app_name}: {tool.name}", json.dumps(args, ensure_ascii=False, indent=1)[:1500])
        return self.answer

    def stop(self) -> None:
        if self.agent is not None:
            self.agent.stopped = True

    def run(self) -> None:
        from assistant.agent import HelperAgent

        try:
            tools = self.hub.agent_tools(self.cloud)
            self.agent = HelperAgent(self.backend, tools, self.confirm, self.step.emit)
            text = self.agent.run(self.task, self.context)
        except Exception as exc:
            self.failed.emit(tr("Помощник не справился: {exc}", exc=exc))
            return
        errors = self.hub.app_errors()
        if errors:
            text += "\n\n" + tr("Не подключились: {apps}", apps="; ".join(f"{k} — {v}" for k, v in errors.items()))
        self.done.emit(text)


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
        "mail": {
            "title": tr("Почта"),
            "about": tr("Новые письма любого ящика: «что пришло на почту», поиск, чтение и черновики ответов по "
                        "вашей просьбе."),
            "reads": tr("отправителя, тему и время новых писем; текст письма — только когда вы просите помощника"),
            "records": tr("отправитель, тема, время, есть ли вложения"),
            "never": tr("не отправляет письма — только сохраняет черновики после вашего «Да»; не отмечает письма "
                        "прочитанными; ничего не удаляет"),
        },
    }


def kind_titles() -> dict[str, str]:
    return {"": tr("любое событие"), "file.created": tr("новый файл"), "file.modified": tr("изменён файл"),
            "file.moved": tr("переименован файл"), "file.deleted": tr("удалён файл"),
            "app.focus": tr("работа в программе"), "mail.new": tr("новое письмо")}


def action_titles() -> dict[str, str]:
    return {"notify": tr("уведомление на компьютере"), "bots": tr("уведомление и сообщение в боты")}


def policy_titles() -> dict[str, str]:
    return {"auto": tr("сам"), "ask": tr("спросить меня"), "off": tr("выключен")}


def kind_names() -> dict[str, str]:
    return {"mcp": tr("MCP-сервер"), "cli": tr("Командная строка"), "http": tr("Веб-API")}


def preset_names() -> list[str]:
    return [tr("Файлы в папке"), tr("Git-репозиторий"), tr("База SQLite"), tr("Память (заметки)"),
            tr("Свой MCP-сервер"), tr("Программа с командной строкой"), tr("Веб-API")]


class AppDialog(QDialog):
    """«Добавить приложение»: an MCP server, a program with a command line or a web API, its tools."""

    def __init__(self, parent=None, app: dict | None = None, taken: set[str] = frozenset()):
        super().__init__(parent)
        self.setWindowTitle(tr("Приложение для помощника"))
        self.resize(700, 600)
        self.app = dict(app) if app else None
        self.taken = set(taken)
        box = QVBoxLayout(self)
        form = QFormLayout()
        self.preset = QComboBox()
        self.preset.addItem(tr("— выбрать готовое —"), -1)
        for i, name in enumerate(preset_names()):
            self.preset.addItem(name, i)
        self.preset.activated.connect(self._use_preset)
        if app is None:
            form.addRow(tr("Готовое:"), self.preset)
        self.name = QLineEdit()
        form.addRow(tr("Название:"), self.name)
        self.kind = QComboBox()
        for key, title in kind_names().items():
            self.kind.addItem(title, key)
        self.kind.currentIndexChanged.connect(self._kind_changed)
        form.addRow(tr("Тип:"), self.kind)
        self.command = QLineEdit()
        form.addRow(tr("Команда:"), self.command)
        self.args = QLineEdit()
        self.args.setPlaceholderText(tr("через пробел; путь с пробелами — в кавычках"))
        form.addRow(tr("Аргументы:"), self.args)
        self.url = QLineEdit()
        self.url.setPlaceholderText("https://example.local/api/find?q={input}")
        form.addRow(tr("Адрес:"), self.url)
        self.method = QComboBox()
        self.method.addItems(["GET", "POST"])
        form.addRow(tr("Метод:"), self.method)
        self.header = QLineEdit()
        self.header.setPlaceholderText("Authorization")
        form.addRow(tr("Заголовок ключа:"), self.header)
        self.key = QLineEdit()
        self.key.setEchoMode(QLineEdit.Password)
        self.key.setPlaceholderText(tr("хранится в диспетчере учётных данных Windows"))
        form.addRow(tr("Ключ:"), self.key)
        self.description = QLineEdit()
        self.description.setPlaceholderText(tr("что умеет: помощник читает это, чтобы понять, когда звать"))
        form.addRow(tr("Описание:"), self.description)
        box.addLayout(form)
        self.hint = QLabel()
        self.hint.setWordWrap(True)
        self.hint.setObjectName("hint")
        box.addWidget(self.hint)
        self.cloud = QCheckBox(tr("Облачной модели можно пользоваться этим приложением"))
        box.addWidget(self.cloud)
        row = QHBoxLayout()
        check = QPushButton(tr("Проверить"))
        check.clicked.connect(self.check)
        row.addWidget(check)
        self.check_label = QLabel()
        self.check_label.setWordWrap(True)
        row.addWidget(self.check_label, 1)
        box.addLayout(row)
        self.tools = QTableWidget(0, 3)
        self.tools.setHorizontalHeaderLabels([tr("Инструмент"), tr("Что делает"), tr("Запускать")])
        self.tools.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        box.addWidget(self.tools, 1)
        buttons = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        box.addWidget(buttons)
        self._policies = dict((app or {}).get("tools") or {})
        if app:
            self.name.setText(app["name"])
            self.kind.setCurrentIndex(max(self.kind.findData(app["kind"]), 0))
            self.command.setText(app["command"])
            self.args.setText(" ".join(f'"{a}"' if " " in a else a for a in app["args"]))
            self.url.setText(app["url"])
            self.method.setCurrentText(app["method"])
            self.header.setText(app["header"])
            self.description.setText(app["description"])
            self.cloud.setChecked(app["cloud"])
        self._kind_changed()

    def _use_preset(self) -> None:
        index = self.preset.currentData()
        if index is None or index < 0:
            return
        preset = app_kinds.PRESETS[index]
        args = list(preset.get("args") or [])
        if "{folder}" in args:
            folder = QFileDialog.getExistingDirectory(self, tr("Папка для приложения"))
            if not folder:
                return
            args = [folder if a == "{folder}" else a for a in args]
        if "{file}" in args:
            file, _ = QFileDialog.getOpenFileName(self, tr("Файл базы"))
            if not file:
                return
            args = [file if a == "{file}" else a for a in args]
        self.name.setText(preset_names()[index])
        self.kind.setCurrentIndex(max(self.kind.findData(preset["kind"]), 0))
        self.command.setText(preset.get("command", ""))
        self.args.setText(" ".join(f'"{a}"' if " " in a else a for a in args))
        self.url.setText(preset.get("url", ""))

    def _kind_changed(self) -> None:
        kind = self.kind.currentData()
        for widget, kinds in ((self.args, ("mcp",)), (self.url, ("http",)), (self.method, ("http",)),
                              (self.header, ("http",)), (self.key, ("http",)),
                              (self.command, ("mcp", "cli")), (self.description, ("cli", "http"))):
            widget.setEnabled(kind in kinds)
        self.hint.setText({
            "mcp": tr("MCP-сервер — программа, которая отдаёт свои инструменты помощнику. Готовые серверы есть для "
                      "файлов, Git, баз данных, 1С, Битрикс24, Jira и многих других; часто их запускают через npx "
                      "(нужен Node.js) или uvx (нужен uv)."),
            "cli": tr("Команда целиком, {input} заменится тем, что передаст помощник. Например: "
                      "\"C:\\Программы\\поиск.exe\" --текст {input}"),
            "http": tr("Адрес с {input}: для GET он подставится в адрес, для POST уйдёт телом {\"input\": …}."),
        }[kind])

    def value(self) -> dict:
        import shlex

        try:
            args = [a[1:-1] if len(a) >= 2 and a[0] == a[-1] == '"' else a
                    for a in shlex.split(self.args.text(), posix=False)]
        except ValueError:
            args = self.args.text().split()
        app_id = (self.app or {}).get("id") or app_kinds.slug(self.name.text(), self.taken)
        return app_kinds.normalize_app({
            "id": app_id, "name": self.name.text().strip(), "kind": self.kind.currentData(),
            "enabled": (self.app or {}).get("enabled", True), "cloud": self.cloud.isChecked(),
            "command": self.command.text().strip(), "args": args, "env": (self.app or {}).get("env") or {},
            "url": self.url.text().strip(), "method": self.method.currentText(), "header": self.header.text().strip(),
            "description": self.description.text().strip(), "tools": self.policy_values()})

    def policy_values(self) -> dict:
        out = dict(self._policies)
        for r in range(self.tools.rowCount()):
            out[self.tools.item(r, 0).text()] = self.tools.cellWidget(r, 2).currentData()
        return out

    def check(self) -> None:
        from PySide6.QtWidgets import QApplication

        app = self.value()
        self.save_key(app)
        self.check_label.setText(tr("Проверяю… первый запуск через npx может занять минуту."))
        QApplication.processEvents()
        tools, error = app_kinds.check(app)
        if error:
            self.check_label.setText(tr("Не работает: {error}", error=error))
            return
        self.check_label.setText(tr("Работает. Инструментов: {count}", count=len(tools)))
        self.show_tools(tools)

    def show_tools(self, tools) -> None:
        self.tools.setRowCount(0)
        for tool in tools:
            r = self.tools.rowCount()
            self.tools.insertRow(r)
            self.tools.setItem(r, 0, QTableWidgetItem(tool.name))
            self.tools.setItem(r, 1, QTableWidgetItem(tool.description.splitlines()[0] if tool.description else ""))
            combo = QComboBox()
            for key, title in policy_titles().items():
                combo.addItem(title, key)
            combo.setCurrentIndex(max(combo.findData(self._policies.get(tool.name, tool.policy)), 0))
            self.tools.setCellWidget(r, 2, combo)

    def save_key(self, app: dict) -> None:
        if app["kind"] == "http" and self.key.text():
            from assistant.secrets import set_secret

            set_secret(app_kinds.secret_name(app), self.key.text())


class MailAccountDialog(QDialog):
    """A mailbox of «Почта»: any IMAP mail (servers found by the address) or the Outlook of this computer."""

    def __init__(self, parent=None, account: dict | None = None, taken: set[str] = frozenset()):
        super().__init__(parent)
        self.setWindowTitle(tr("Почтовый ящик"))
        self.resize(560, 420)
        self.account = dict(account or {})
        self.taken = set(taken)
        box = QVBoxLayout(self)
        form = QFormLayout()
        self.kind = QComboBox()
        self.kind.addItem(tr("Любая почта по IMAP"), "imap")
        self.kind.addItem(tr("Outlook на этом компьютере"), "outlook")
        self.kind.setCurrentIndex(max(self.kind.findData(self.account.get("kind", "imap")), 0))
        self.kind.currentIndexChanged.connect(self._kind_changed)
        form.addRow(tr("Тип:"), self.kind)
        self.address = QLineEdit(self.account.get("address", ""))
        self.address.setPlaceholderText("ivanov@zavod.ru")
        self.address.editingFinished.connect(self._autofill)
        form.addRow(tr("Адрес:"), self.address)
        self.password = QLineEdit()
        self.password.setEchoMode(QLineEdit.Password)
        self.password.setPlaceholderText(tr("пароль приложения; хранится в диспетчере учётных данных Windows"))
        form.addRow(tr("Пароль:"), self.password)
        self.host = QLineEdit(self.account.get("imap_host", ""))
        form.addRow(tr("Сервер IMAP:"), self.host)
        self.port = QSpinBox()
        self.port.setRange(1, 65535)
        self.port.setValue(int(self.account.get("imap_port") or 993))
        form.addRow(tr("Порт:"), self.port)
        self.login = QLineEdit(self.account.get("login", ""))
        self.login.setPlaceholderText(tr("обычно совпадает с адресом"))
        form.addRow(tr("Логин:"), self.login)
        box.addLayout(form)
        row = QHBoxLayout()
        find = QPushButton(tr("Найти настройки"))
        find.clicked.connect(lambda: self._autofill(force=True))
        row.addWidget(find)
        check = QPushButton(tr("Проверить"))
        check.clicked.connect(self.check)
        row.addWidget(check)
        row.addStretch(1)
        box.addLayout(row)
        self.hint = QLabel()
        self.hint.setWordWrap(True)
        self.hint.setObjectName("hint")
        box.addWidget(self.hint, 1)
        buttons = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        box.addWidget(buttons)
        self._kind_changed()

    def _kind_changed(self) -> None:
        imap = self.kind.currentData() == "imap"
        for widget in (self.password, self.host, self.port, self.login, self.address):
            widget.setEnabled(imap)
        if not imap:
            self.hint.setText(tr("Fluxion читает письма из Outlook, установленного на этом компьютере: подходит для "
                                 "рабочей почты Exchange и Microsoft 365. Пароль не нужен."))

    def _autofill(self, force: bool = False) -> None:
        from assistant.mail import MailError, autoconfig

        address = self.address.text().strip()
        if "@" not in address or (self.host.text() and not force):
            return
        try:
            found = autoconfig(address)
        except MailError as exc:
            self.hint.setText(str(exc))
            return
        self.host.setText(found["imap_host"])
        self.port.setValue(int(found["imap_port"]))
        if not self.login.text():
            self.login.setText(address)
        self.hint.setText(found.get("hint") or tr("Настройки найдены. Нажмите «Проверить»."))

    def value(self) -> dict:
        from assistant.apps import slug

        address = self.address.text().strip()
        key = self.account.get("id") or slug(address.split("@")[0] if address else "outlook", self.taken)
        if self.kind.currentData() == "outlook":
            return {"id": key, "kind": "outlook", "address": ""}
        return {"id": key, "kind": "imap", "address": address, "login": self.login.text().strip() or address,
                "imap_host": self.host.text().strip(), "imap_port": self.port.value()}

    def save_password(self, account: dict) -> None:
        if account["kind"] == "imap" and self.password.text():
            from assistant.mail import secret_name
            from assistant.secrets import set_secret

            set_secret(secret_name(account), self.password.text())

    def check(self) -> None:
        from PySide6.QtWidgets import QApplication

        from assistant.mail import open_box

        account = self.value()
        self.save_password(account)
        self.hint.setText(tr("Проверяю…"))
        QApplication.processEvents()
        try:
            with open_box(account) as mailbox:
                count = mailbox.unseen()
        except Exception as exc:
            self.hint.setText(tr("Не подключился: {error}", error=exc))
            return
        self.hint.setText(tr("Подключился. Непрочитанных писем: {count}", count=count))


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
        elif cid == "mail":
            self.accounts = [dict(a) for a in settings.get("accounts") or [] if isinstance(a, dict)]
            self.account_list = QListWidget()
            self.account_list.setMaximumHeight(80)
            box.addWidget(self.account_list)
            self._fill_accounts()
            row = QHBoxLayout()
            for title, slot in ((tr("Добавить ящик…"), self._add_account), (tr("Изменить…"), self._edit_account),
                                (tr("Убрать ящик"), self._drop_account)):
                button = QPushButton(title)
                button.clicked.connect(slot)
                row.addWidget(button)
            row.addStretch(1)
            box.addLayout(row)
            self.interval = QSpinBox()
            self.interval.setRange(1, 120)
            self.interval.setValue(max(int(settings.get("interval") or 120) // 60, 1))
            form.addRow(tr("Проверять почту, раз в минут:"), self.interval)
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

    def _fill_accounts(self) -> None:
        from assistant.mail import account_title

        self.account_list.clear()
        self.account_list.addItems([account_title(a) for a in self.accounts])

    def _add_account(self) -> None:
        dialog = MailAccountDialog(self, taken={a.get("id", "") for a in self.accounts})
        if dialog.exec() == QDialog.Accepted:
            account = dialog.value()
            dialog.save_password(account)
            self.accounts.append(account)
            self._fill_accounts()
        dialog.deleteLater()

    def _edit_account(self) -> None:
        r = self.account_list.currentRow()
        if not 0 <= r < len(self.accounts):
            return
        dialog = MailAccountDialog(self, self.accounts[r])
        if dialog.exec() == QDialog.Accepted:
            account = dialog.value()
            dialog.save_password(account)
            self.accounts[r] = account
            self._fill_accounts()
        dialog.deleteLater()

    def _drop_account(self) -> None:
        r = self.account_list.currentRow()
        if 0 <= r < len(self.accounts):
            from assistant.mail import secret_name
            from assistant.secrets import delete_secret

            delete_secret(secret_name(self.accounts.pop(r)))
            self._fill_accounts()

    def _add_folder(self) -> None:
        folder = QFileDialog.getExistingDirectory(self, tr("Папка, за которой следить"))
        if folder and not self.folders.findItems(folder, 0):
            self.folders.addItem(folder)

    def value(self) -> dict:
        settings: dict = {}
        if self.cid == "folders":
            settings = {"folders": [self.folders.item(i).text() for i in range(self.folders.count())],
                        "mask": self.mask.text().strip(), "exclude": self.exclude.text().strip()}
        elif self.cid == "mail":
            settings = {"accounts": self.accounts, "interval": self.interval.value() * 60}
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
        self.tabs.addTab(self._apps_tab(), tr("Приложения"))
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
        from PySide6.QtWidgets import QScrollArea

        scroll = QScrollArea()  # three sources and more: the window stays the same size
        scroll.setWidgetResizable(True)
        scroll.setWidget(page)
        return scroll

    # ── Приложения ──
    def _apps_tab(self) -> QWidget:
        page = QWidget()
        box = QVBoxLayout(page)
        intro = QLabel(tr("Любая программа становится инструментом помощника: MCP-сервер, программа с командной "
                          "строкой или веб-API. В чате: /помощник и задача словами. Действие, которое может что-то "
                          "изменить, выполняется только после вашего «Да»."))
        intro.setWordWrap(True)
        box.addWidget(intro)
        self.apps_table = QTableWidget(0, 3)
        self.apps_table.setHorizontalHeaderLabels([tr("Приложение"), tr("Тип"), tr("Включено")])
        self.apps_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        self.apps_table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.apps_table.setSelectionBehavior(QTableWidget.SelectRows)
        self.apps_table.doubleClicked.connect(lambda _i: self._edit_app())
        box.addWidget(self.apps_table, 1)
        self.app_list = [dict(a) for a in self.data["apps"]]
        self._fill_apps()
        row = QHBoxLayout()
        for title, slot in ((tr("Добавить приложение…"), self._add_app), (tr("Изменить…"), self._edit_app),
                            (tr("Включить / выключить"), self._toggle_app), (tr("Убрать"), self._drop_app)):
            button = QPushButton(title)
            button.clicked.connect(slot)
            row.addWidget(button)
        row.addStretch(1)
        box.addLayout(row)
        errors = self.hub.app_errors() if self.hub is not None else {}
        if errors:
            label = QLabel(tr("Не подключились: {apps}", apps="; ".join(f"{k} — {v}" for k, v in errors.items())))
            label.setWordWrap(True)
            box.addWidget(label)
        return page

    def _fill_apps(self) -> None:
        self.apps_table.setRowCount(0)
        names = kind_names()
        for app in self.app_list:
            r = self.apps_table.rowCount()
            self.apps_table.insertRow(r)
            for c, text in enumerate((app["name"], names.get(app["kind"], app["kind"]),
                                      tr("да") if app["enabled"] else tr("нет"))):
                self.apps_table.setItem(r, c, QTableWidgetItem(text))

    def _add_app(self) -> None:
        dialog = AppDialog(self, taken={a["id"] for a in self.app_list})
        if dialog.exec() == QDialog.Accepted:
            app = dialog.value()
            dialog.save_key(app)
            if app is not None:
                self.app_list.append(app)
                self._fill_apps()
        dialog.deleteLater()

    def _edit_app(self) -> None:
        r = self.apps_table.currentRow()
        if not 0 <= r < len(self.app_list):
            return
        dialog = AppDialog(self, self.app_list[r])
        if dialog.exec() == QDialog.Accepted:
            app = dialog.value()
            dialog.save_key(app)
            if app is not None:
                self.app_list[r] = app
                self._fill_apps()
        dialog.deleteLater()

    def _toggle_app(self) -> None:
        r = self.apps_table.currentRow()
        if 0 <= r < len(self.app_list):
            self.app_list[r]["enabled"] = not self.app_list[r]["enabled"]
            self._fill_apps()
            self.apps_table.selectRow(r)

    def _drop_app(self) -> None:
        r = self.apps_table.currentRow()
        if 0 <= r < len(self.app_list):
            from assistant.secrets import delete_secret

            delete_secret(app_kinds.secret_name(self.app_list.pop(r)))
            self._fill_apps()

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
        data["apps"] = self.app_list
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
