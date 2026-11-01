"""«Боты в мессенджерах» (Инструменты): Telegram, MAX and ВКонтакте answer from this
computer while Fluxion is open (package bots).

``BotHost`` is what the bots ask the window for, from their own threads: the answers
of the chat, the speech model, the chat history (through a queued signal — the
history file is the window's). ``BotsDialog`` holds the tokens, the allow-list, the
folders open to the bots and the start of Fluxion with Windows.
"""
from __future__ import annotations

import sys
from pathlib import Path

from PySide6.QtCore import QObject, QThread, Signal
from PySide6.QtWidgets import (QCheckBox, QDialog, QDialogButtonBox, QFileDialog, QFormLayout, QHBoxLayout, QLabel,
                               QLineEdit, QListWidget, QListWidgetItem, QPushButton, QTabWidget, QVBoxLayout, QWidget)

from bots import config

from .i18n import tr

RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
RUN_NAME = "Fluxion"
MINIMIZED = "--minimized"

HOW_TO = {
    "telegram": "Токен выдаёт @BotFather в Telegram: /newbot, имя, адрес бота — и он пришлёт токен. "
                "Если api.telegram.org не открывается, укажите HTTP-прокси: http://адрес:порт.",
    "max": "Бота в MAX создаёт @MasterBot (пока — для организаций, проверенных на платформе MAX для бизнеса); "
           "токен — в настройках бота. Проверено только на тестовом сервере: «Проверить» скажет, отвечает ли MAX.",
    "vk": "Нужно сообщество ВКонтакте: Управление → Работа с API → «Создать ключ» с доступом к сообщениям и "
          "документам; там же Long Poll API — включить, тип событий «Входящее сообщение»; в «Сообщениях» — включить.",
}


# ── what the bots ask the window for ─────────────────────────────────────────

class BotHost(QObject):
    chat_saved = Signal(dict)
    state_changed = Signal(str, str, bool)  # platform, text, working
    pending_code = Signal(str, str, str)  # platform, name, code

    def __init__(self, window):
        super().__init__(window)
        self.window = window

    def ask(self, text: str, history: list[dict], archive: bool = False) -> str:
        assistant = getattr(self.window, "assistant", None)
        if assistant is None:
            return "Модель не подключена: откройте Fluxion и выберите модель (меню «Модели»)."
        source = None
        if archive:
            from rag.archive import ArchiveIndex

            source = ArchiveIndex(self.window._style_dir())
            if not source.file_count:
                source.close()
                return "Архив КД ещё не проиндексирован: в программе Инструменты → «Архив КД (Pro)…»."
        try:
            extra = {"source": source} if source is not None else {}
            _result, stream = assistant.ask_stream(query=text, history=history, **extra)
            return "".join(stream).strip()
        finally:
            if source is not None:
                source.close()

    def backend(self):
        return self.window.backend

    def transcribe(self, path) -> str:
        from voice import stt
        from voice.models import is_installed, model_path

        from bots.audio import decode

        from .app import voice_model_id

        model_id = voice_model_id(self.window.qsettings)
        if not is_installed(model_id):
            raise RuntimeError("в Fluxion не скачана модель распознавания речи")
        return stt.transcribe(decode(path), model_path(model_id))

    def save_chat(self, session: dict) -> None:
        self.chat_saved.emit(dict(session, renamed=True))  # the messenger mark stays in the title

    def pending(self, platform: str, name: str, code: str) -> None:
        self.pending_code.emit(platform, name, code)

    def status(self) -> str:
        backend = getattr(self.window, "backend", None)
        model = getattr(backend, "model", "") if backend is not None else ""
        return f"Fluxion открыт. Модель: {model or 'не выбрана'}."

    def busy(self) -> bool:
        w = self.window
        return any(getattr(w, name, None) is not None for name in ("report_worker", "laser_job", "acad_worker"))


# ── start with Windows ───────────────────────────────────────────────────────

def autostart_command() -> str:
    """The command of the Run key: the installed Fluxion.exe, minimized."""
    return f'"{sys.executable}" {MINIMIZED}'


def autostart_available() -> bool:
    return sys.platform == "win32" and bool(getattr(sys, "frozen", False))


def autostart_enabled() -> bool:
    if sys.platform != "win32":
        return False
    import winreg

    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as key:
            value, _ = winreg.QueryValueEx(key, RUN_NAME)
    except OSError:
        return False
    return MINIMIZED in str(value)


def set_autostart(on: bool) -> None:
    import winreg

    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as key:
        if on:
            winreg.SetValueEx(key, RUN_NAME, 0, winreg.REG_SZ, autostart_command())
        else:
            try:
                winreg.DeleteValue(key, RUN_NAME)
            except FileNotFoundError:
                pass


# ── the window ───────────────────────────────────────────────────────────────

class CheckWorker(QThread):
    done = Signal(str, bool)

    def __init__(self, platform: str, token: str, proxy: str, parent=None):
        super().__init__(parent)
        self.platform, self.token, self.proxy = platform, token, proxy

    def run(self) -> None:
        from bots.base import BotError
        from bots.runner import adapter_for

        try:
            name = adapter_for(self.platform, self.token, self.proxy).me()
        except BotError as exc:
            self.done.emit(str(exc), False)
            return
        except Exception as exc:
            self.done.emit(f"{type(exc).__name__}: {exc}", False)
            return
        self.done.emit(name, True)


class PlatformTab(QWidget):
    def __init__(self, platform: str, settings: dict, parent=None):
        super().__init__(parent)
        self.platform = platform
        self.worker = None
        form = QFormLayout(self)
        how = QLabel(tr(HOW_TO[platform]))
        how.setWordWrap(True)
        form.addRow(how)
        self.token = QLineEdit(settings["token"])
        self.token.setEchoMode(QLineEdit.Password)
        self.token.setPlaceholderText(tr("токен бота"))
        row = QHBoxLayout()
        row.addWidget(self.token, 1)
        self.show_token = QCheckBox(tr("показать"))
        self.show_token.toggled.connect(self._on_show_token)
        row.addWidget(self.show_token)
        form.addRow(tr("Токен:"), row)
        self.proxy = QLineEdit(settings["proxy"])
        self.proxy.setPlaceholderText(tr("необязательно: http://адрес:порт"))
        form.addRow(tr("Прокси:"), self.proxy)
        check_row = QHBoxLayout()
        self.check_button = QPushButton(tr("Проверить"))
        self.check_button.clicked.connect(self._check)
        check_row.addWidget(self.check_button)
        self.check_label = QLabel("")
        self.check_label.setWordWrap(True)
        check_row.addWidget(self.check_label, 1)
        form.addRow(check_row)
        self.enabled = QCheckBox(tr("Включить бота: отвечать, пока Fluxion открыт"))
        self.enabled.setChecked(settings["enabled"])
        form.addRow(self.enabled)
        self.allowed = QListWidget()
        for user in settings["allowed"]:
            item = QListWidgetItem(f"{user['name'] or user['id']} ({user['id']})")
            item.setData(256, dict(user))
            self.allowed.addItem(item)
        form.addRow(tr("Разрешены:"), self.allowed)
        remove = QPushButton(tr("Убрать из списка"))
        remove.clicked.connect(self._remove)
        form.addRow("", remove)

    def _on_show_token(self, on: bool) -> None:
        self.token.setEchoMode(QLineEdit.Normal if on else QLineEdit.Password)

    def _check(self) -> None:
        if self.worker is not None:
            return
        self.check_label.setText(tr("Проверяю…"))
        self.worker = CheckWorker(self.platform, self.token.text().strip(), self.proxy.text().strip(), self)
        self.worker.done.connect(self._on_checked)
        self.worker.finished.connect(self._on_check_finished)
        self.worker.start()

    def _on_checked(self, text: str, ok: bool) -> None:
        self.check_label.setText(tr("Работает: {name}", name=text) if ok else "✖ " + text)

    def _on_check_finished(self) -> None:
        self.worker.deleteLater()
        self.worker = None

    def _remove(self) -> None:
        for item in self.allowed.selectedItems():
            self.allowed.takeItem(self.allowed.row(item))

    def add_user(self, user: dict) -> None:
        if any(self.allowed.item(i).data(256)["id"] == user["id"] for i in range(self.allowed.count())):
            return
        item = QListWidgetItem(f"{user['name'] or user['id']} ({user['id']})")
        item.setData(256, {"id": user["id"], "name": user["name"]})
        self.allowed.addItem(item)

    def values(self) -> dict:
        return {"token": self.token.text().strip(), "proxy": self.proxy.text().strip(),
                "enabled": self.enabled.isChecked(),
                "allowed": [self.allowed.item(i).data(256) for i in range(self.allowed.count())]}

    def wait(self) -> None:
        if self.worker is not None:
            self.worker.wait(40000)


class BotsDialog(QDialog):
    def __init__(self, parent=None, states: dict | None = None):
        super().__init__(parent)
        self.setWindowTitle(tr("Боты в мессенджерах"))
        self.resize(760, 640)
        data = config.load()
        box = QVBoxLayout(self)
        intro = QLabel(tr(
            "Бот отвечает с этого компьютера, пока Fluxion открыт: вопросы, расчёты, детали в КОМПАС, отчёты по "
            "сборкам. Сервер не нужен. Отвечает только тем, кого вы подтвердили кодом. Сообщения и файлы проходят "
            "через серверы мессенджера — не пересылайте через бота то, что нельзя выносить с завода."))
        intro.setWordWrap(True)
        box.addWidget(intro)
        self.tabs = QTabWidget()
        self.platform_tabs: dict[str, PlatformTab] = {}
        states = states or {}
        for platform in config.PLATFORMS:
            tab = PlatformTab(platform, data[platform], self)
            state = states.get(platform)
            if state:
                tab.check_label.setText(state)
            self.platform_tabs[platform] = tab
            self.tabs.addTab(tab, config.TITLES[platform])
        box.addWidget(self.tabs, 1)

        box.addWidget(QLabel(tr("<b>Подтвердить нового пользователя</b>: он пишет боту и получает код из 6 цифр.")))
        confirm_row = QHBoxLayout()
        self.code = QLineEdit()
        self.code.setPlaceholderText(tr("код из сообщения бота"))
        confirm_row.addWidget(self.code, 1)
        confirm = QPushButton(tr("Подтвердить"))
        confirm.clicked.connect(self._confirm)
        confirm_row.addWidget(confirm)
        box.addLayout(confirm_row)
        self.confirm_label = QLabel(self._pending_text())
        self.confirm_label.setWordWrap(True)
        box.addWidget(self.confirm_label)

        box.addWidget(QLabel(tr("<b>Папки, открытые для ботов</b>: только отсюда бот берёт сборки и файлы.")))
        self.folders = QListWidget()
        self.folders.addItems(data["folders"])
        self.folders.setMaximumHeight(110)
        box.addWidget(self.folders)
        folder_row = QHBoxLayout()
        add = QPushButton(tr("Добавить папку…"))
        add.clicked.connect(self._add_folder)
        folder_row.addWidget(add)
        drop = QPushButton(tr("Убрать папку"))
        drop.clicked.connect(self._drop_folder)
        folder_row.addWidget(drop)
        folder_row.addStretch(1)
        box.addLayout(folder_row)

        self.autostart = QCheckBox(tr("Запускать Fluxion свёрнутым при входе в Windows — боты отвечают сразу"))
        if autostart_available():
            self.autostart.setChecked(autostart_enabled())
        else:
            self.autostart.setEnabled(False)
            self.autostart.setToolTip(tr("Только у установленной программы"))
        box.addWidget(self.autostart)
        buttons = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        box.addWidget(buttons)

    @staticmethod
    def _pending_text() -> str:
        waiting = config.pending()
        if not waiting:
            return ""
        return tr("Ждут подтверждения: ") + "; ".join(
            f"{p['name']} ({config.TITLES[p['platform']]})" for p in waiting)

    def _confirm(self) -> None:
        user = config.confirm(self.code.text())
        if user is None:
            self.confirm_label.setText(tr("Такого кода нет или он устарел: пусть пользователь напишет боту ещё раз."))
            return
        self.code.clear()
        self.platform_tabs[user["platform"]].add_user(user)
        self.tabs.setCurrentWidget(self.platform_tabs[user["platform"]])
        self.confirm_label.setText(tr("Подтверждён: {name}. Теперь бот ему отвечает.", name=user["name"]))

    def _add_folder(self) -> None:
        folder = QFileDialog.getExistingDirectory(self, tr("Папка для ботов"))
        if folder and not self.folders.findItems(folder, 0):
            self.folders.addItem(str(Path(folder)))

    def _drop_folder(self) -> None:
        for item in self.folders.selectedItems():
            self.folders.takeItem(self.folders.row(item))

    def values(self) -> dict:
        data = config.load()  # the users' «/уведомления» are kept
        for platform, tab in self.platform_tabs.items():
            old = {u["id"]: u for u in data[platform]["allowed"]}
            values = tab.values()
            values["allowed"] = [dict(old.get(u["id"], {}), **u) for u in values["allowed"]]
            data[platform] = values
        data["folders"] = [self.folders.item(i).text() for i in range(self.folders.count())]
        return data

    def save(self) -> None:
        config.save(self.values())
        if autostart_available() and self.autostart.isChecked() != autostart_enabled():
            set_autostart(self.autostart.isChecked())

    def done(self, result: int) -> None:
        for tab in self.platform_tabs.values():
            tab.wait()
        super().done(result)
