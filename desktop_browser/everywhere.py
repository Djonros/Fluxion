"""«Fluxion везде»: the selected text of any program → Ctrl+Alt+Пробел → a small window of Fluxion.

The hotkey is registered with Windows (RegisterHotKey) while Fluxion is open, minimized
too. Pressed, Fluxion copies the selection of the program in front (Ctrl+C sent to it)
and gives the clipboard back as it was; the window shows the text and quick actions —
Переписать, Мой стиль, Короче, Перевести, Объяснить, Ответить, or the user's own words.
The answer streams from the chat model of Fluxion; «Вставить вместо выделенного» puts
it in place of the selection (Ctrl+V to the same program), the clipboard again given back.

A program run as administrator does not take keys from Fluxion: the selection cannot
be copied, the window opens empty and says so — the text can be pasted into it.
"""
from __future__ import annotations

import sys
import time
from functools import partial

from PySide6.QtCore import QAbstractNativeEventFilter, QMimeData, QObject, Qt, QThread, QTimer, Signal
from PySide6.QtGui import QCursor, QGuiApplication, QKeySequence
from PySide6.QtWidgets import (QApplication, QFrame, QHBoxLayout, QLabel, QLineEdit, QMenu, QPlainTextEdit,
                               QPushButton, QTextBrowser, QToolButton, QVBoxLayout)

from .i18n import tr

DEFAULT_HOTKEY = "Ctrl+Alt+Space"
SPARE_HOTKEY = "Ctrl+Alt+Q"  # when another program holds the default one (the Claude app takes Ctrl+Alt+Space)
HOTKEY_SETTING = "everywhere/hotkey"
ENABLED_SETTING = "everywhere/enabled"
CAPTURE_TIMEOUT = 0.6  # s: the program in front has this long to copy its selection
MAX_TEXT = 12000  # characters of the selection sent to the model

# what each button asks of the model; the answer only, in the language of the text
PROMPTS = {
    "rewrite": "Перепиши текст грамотно, ясно и аккуратно, сохрани смысл и язык текста.",
    "style": "Перепиши текст в манере пользователя (описана ниже), сохрани смысл и язык текста.",
    "shorter": "Сократи текст примерно вдвое, сохрани главное и язык текста.",
    "explain": "Объясни простыми словами, что значит этот текст и что из него следует. Отвечай на языке текста.",
    "reply": "Напиши вежливый деловой ответ на это сообщение от имени пользователя, на языке сообщения.",
    "translate": "Переведи текст на {language}. Термины переводи точно, формат сохрани.",
}
ANSWER_ONLY = "Выведи только готовый текст, без пояснений, кавычек и вступлений."
LANGUAGES = (("русский", "Русский"), ("английский", "English"), ("немецкий", "Deutsch"),
             ("китайский", "中文"), ("казахский", "Қазақша"))


def build_messages(action: str, text: str, own: str = "", language: str = "", style: str = "") -> list[dict]:
    """The request to the chat model for a button (*action*) or the user's own words (*own*)."""
    if own:
        task = f"Сделай с текстом вот что: {own}"
    else:
        task = PROMPTS[action].format(language=language or "английский")
    system = task + ("" if action == "explain" and not own else " " + ANSWER_ONLY)
    if style:
        system += "\n\n" + style
    return [{"role": "system", "content": system}, {"role": "user", "content": text[:MAX_TEXT]}]


# ── the hotkey (Windows) ─────────────────────────────────────────────────────

_MODIFIERS = {"ctrl": 0x2, "alt": 0x1, "shift": 0x4, "meta": 0x8, "win": 0x8}
_MOD_NOREPEAT = 0x4000
_WM_HOTKEY = 0x0312
_KEYS = {"space": 0x20, "enter": 0x0D, "return": 0x0D, "tab": 0x09, "insert": 0x2D, "ins": 0x2D}


def parse_hotkey(text: str) -> tuple[int, int] | None:
    """(the modifiers, the virtual key) of «Ctrl+Alt+Space» for RegisterHotKey; None when not understood
    or without a modifier (a bare key would be taken from every program)."""
    parts = [p.strip().lower() for p in (text or "").split("+") if p.strip()]
    if len(parts) < 2:
        return None
    mods = 0
    for part in parts[:-1]:
        if part not in _MODIFIERS:
            return None
        mods |= _MODIFIERS[part]
    key = parts[-1]
    if key in _KEYS:
        vk = _KEYS[key]
    elif len(key) == 1 and key.isalnum() and key.isascii():
        vk = ord(key.upper())
    elif key.startswith("f") and key[1:].isdigit() and 1 <= int(key[1:]) <= 12:
        vk = 0x70 + int(key[1:]) - 1
    else:
        return None
    return mods, vk


class _HotkeyFilter(QAbstractNativeEventFilter):
    def __init__(self, hotkey: GlobalHotkey):
        super().__init__()
        self.hotkey = hotkey

    def nativeEventFilter(self, event_type, message):  # noqa: N802 (Qt API)
        if event_type in (b"windows_generic_MSG", "windows_generic_MSG"):
            from ctypes import wintypes

            msg = wintypes.MSG.from_address(int(message))
            if msg.message == _WM_HOTKEY and msg.wParam == GlobalHotkey.ID:
                QTimer.singleShot(0, self.hotkey.pressed.emit)  # out of the native call
                return True, 0
        return False, 0


class GlobalHotkey(QObject):
    """A hotkey Windows gives to Fluxion over every program; nothing on other systems."""

    ID = 0x464C  # "FL"
    pressed = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.keys = ""
        self._filter = None

    def register(self, keys: str) -> bool:
        """True when Windows gave the hotkey; False — another program holds it, or not Windows."""
        self.unregister()
        parsed = parse_hotkey(keys)
        if parsed is None or sys.platform != "win32":
            return False
        import ctypes

        mods, vk = parsed
        if not ctypes.windll.user32.RegisterHotKey(None, self.ID, mods | _MOD_NOREPEAT, vk):
            return False
        self._filter = _HotkeyFilter(self)
        QApplication.instance().installNativeEventFilter(self._filter)
        self.keys = keys
        return True

    def unregister(self) -> None:
        if self._filter is None:
            return
        import ctypes

        ctypes.windll.user32.UnregisterHotKey(None, self.ID)
        QApplication.instance().removeNativeEventFilter(self._filter)
        self._filter = None
        self.keys = ""


# ── the selection of another program: Ctrl+C in, Ctrl+V out ─────────────────

_VK_CONTROL, _VK_MENU, _VK_SHIFT, _VK_LWIN, _VK_SPACE = 0x11, 0x12, 0x10, 0x5B, 0x20
_KEYUP = 0x2


def _key(vk: int, up: bool = False) -> None:
    import ctypes

    ctypes.windll.user32.keybd_event(vk, 0, _KEYUP if up else 0, 0)


def _send_ctrl(letter: str) -> None:
    # the keys of the hotkey are still down: let them go, or the program gets Ctrl+Alt+C
    for vk in (_VK_MENU, _VK_SHIFT, _VK_LWIN, _VK_SPACE):
        _key(vk, up=True)
    _key(_VK_CONTROL)
    _key(ord(letter))
    _key(ord(letter), up=True)
    _key(_VK_CONTROL, up=True)


def _clipboard_number() -> int:
    if sys.platform != "win32":
        return 0
    import ctypes

    return int(ctypes.windll.user32.GetClipboardSequenceNumber())


def save_clipboard() -> QMimeData:
    """A copy of what is in the clipboard now, every format of it."""
    copy = QMimeData()
    current = QGuiApplication.clipboard().mimeData()
    if current is not None:
        for fmt in current.formats():
            copy.setData(fmt, current.data(fmt))
    return copy


def restore_clipboard(saved: QMimeData) -> None:
    QGuiApplication.clipboard().setMimeData(saved)


def foreground_window() -> int:
    if sys.platform != "win32":
        return 0
    import ctypes

    return int(ctypes.windll.user32.GetForegroundWindow())


def capture_selection(timeout: float = CAPTURE_TIMEOUT) -> str:
    """The text selected in the program in front, "" when it gave nothing (nothing selected,
    or a program run as administrator). The clipboard is given back as it was."""
    if sys.platform != "win32":
        return ""
    saved = save_clipboard()
    number = _clipboard_number()
    _send_ctrl("C")
    end = time.monotonic() + timeout
    text = ""
    while time.monotonic() < end:
        QApplication.processEvents()
        if _clipboard_number() != number:
            time.sleep(0.03)  # a program may put the text in a few steps
            text = QGuiApplication.clipboard().text()
            break
        time.sleep(0.02)
    restore_clipboard(saved)
    return text


def paste_into(window: int, text: str) -> None:
    """Put *text* in place of the selection of *window*; the clipboard is given back afterwards."""
    saved = save_clipboard()
    QGuiApplication.clipboard().setText(text)
    if sys.platform == "win32":
        import ctypes

        ctypes.windll.user32.SetForegroundWindow(window)
        time.sleep(0.12)
        _send_ctrl("V")
    QTimer.singleShot(800, partial(restore_clipboard, saved))


# ── the window ───────────────────────────────────────────────────────────────

class AnswerWorker(QThread):
    token = Signal(str)
    failed = Signal(str)

    def __init__(self, backend, messages: list[dict], parent=None):
        super().__init__(parent)
        self.backend = backend
        self.messages = messages
        self._stop = False

    def stop(self) -> None:
        self._stop = True

    def run(self) -> None:
        from core.config import GenerationSettings

        try:
            for chunk in self.backend.stream(self.messages, GenerationSettings(temperature=0.3, max_tokens=1500)):
                if self._stop:
                    break
                self.token.emit(chunk)
        except Exception as exc:
            self.failed.emit(str(exc) or type(exc).__name__)


class QuickWindow(QFrame):
    """The small window over the program: the text, the buttons, the answer."""

    to_chat = Signal(str)

    def __init__(self, backend_of, style_of, colors: dict, parent=None):
        super().__init__(parent, Qt.Tool | Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint)
        self.backend_of = backend_of  # () -> the chat backend of the window, or None
        self.style_of = style_of  # (text) -> the «Мой стиль» block, "" without Pro or samples
        self.target = 0
        self.worker: AnswerWorker | None = None
        self.answer = ""
        self.setObjectName("quickWindow")
        self.setFixedWidth(540)
        self.setStyleSheet(self._css(colors))
        box = QVBoxLayout(self)
        box.setContentsMargins(14, 12, 14, 12)
        box.setSpacing(8)
        head = QHBoxLayout()
        name = QLabel("FLUXION")
        name.setObjectName("quickName")
        head.addWidget(name)
        head.addStretch(1)
        self.keys_label = QLabel("")
        self.keys_label.setObjectName("quickHint")
        head.addWidget(self.keys_label)
        close = QToolButton()
        close.setText("Esc")
        close.setObjectName("quickHint")
        close.clicked.connect(self.hide)
        head.addWidget(close)
        box.addLayout(head)
        self.source = QPlainTextEdit()
        self.source.setObjectName("quickSource")
        self.source.setFixedHeight(70)
        box.addWidget(self.source)
        self.note = QLabel("")
        self.note.setObjectName("quickNote")
        self.note.setWordWrap(True)
        self.note.setVisible(False)
        box.addWidget(self.note)
        chips, more = QHBoxLayout(), QHBoxLayout()  # two rows: the longer words of other languages fit
        chips.setSpacing(6)
        more.setSpacing(6)
        self.buttons: dict[str, QPushButton] = {}
        for action, title in (("rewrite", tr("Переписать")), ("style", tr("Мой стиль") + "  PRO"),
                              ("shorter", tr("Короче")), ("explain", tr("Объяснить")), ("reply", tr("Ответить"))):
            button = QPushButton(title)
            button.setObjectName("quickChip")
            button.setProperty("action", action)
            button.clicked.connect(self._on_chip)
            (chips if action in ("rewrite", "style", "shorter") else more).addWidget(button)
            self.buttons[action] = button
        translate = QToolButton()
        translate.setText(tr("Перевести") + " ▾")
        translate.setObjectName("quickChip")
        translate.setPopupMode(QToolButton.InstantPopup)
        menu = QMenu(translate)
        for language, title in LANGUAGES:
            action = menu.addAction(title)
            action.setData(language)
        menu.triggered.connect(self._on_translate)
        translate.setMenu(menu)
        more.insertWidget(0, translate)
        chips.addStretch(1)
        more.addStretch(1)
        box.addLayout(chips)
        box.addLayout(more)
        ask = QHBoxLayout()
        self.own = QLineEdit()
        self.own.setPlaceholderText(tr("Или своими словами: «вежливее», «добавь сроки»…"))
        self.own.returnPressed.connect(self._on_own)
        ask.addWidget(self.own, 1)
        send = QPushButton("↑")
        send.setObjectName("quickSend")
        send.clicked.connect(self._on_own)
        ask.addWidget(send)
        box.addLayout(ask)
        self.result = QTextBrowser()
        self.result.setObjectName("quickResult")
        self.result.setVisible(False)
        self.result.setMinimumHeight(110)
        box.addWidget(self.result)
        actions = QHBoxLayout()
        actions.addStretch(1)
        self.chat_button = QPushButton(tr("В чат"))
        self.chat_button.clicked.connect(self._on_chat)
        self.copy_button = QPushButton(tr("Копировать"))
        self.copy_button.clicked.connect(self._on_copy)
        self.paste_button = QPushButton(tr("Вставить вместо выделенного"))
        self.paste_button.setObjectName("quickPaste")
        self.paste_button.clicked.connect(self._on_paste)
        for button in (self.chat_button, self.copy_button, self.paste_button):
            button.setEnabled(False)
            actions.addWidget(button)
        box.addLayout(actions)

    def set_colors(self, colors: dict) -> None:
        self.setStyleSheet(self._css(colors))

    @staticmethod
    def _css(c: dict) -> str:
        return f"""
            QFrame#quickWindow {{ background: {c['bg1']}; border: 1px solid {c['border']}; border-radius: 14px; }}
            QFrame#quickWindow QLabel {{ color: {c['text1']}; background: transparent; }}
            QFrame#quickWindow QPushButton, QFrame#quickWindow QToolButton {{ color: {c['text1']}; background: transparent; border: 1px solid {c['border']}; border-radius: 8px; padding: 6px 12px; }}
            QFrame#quickWindow QPushButton:disabled {{ color: {c['text2']}; }}
            QFrame#quickWindow QLabel#quickName {{ font-weight: 600; letter-spacing: 3px; }}
            QFrame#quickWindow QLabel#quickHint {{ color: {c['text2']}; border: 1px solid {c['border']}; border-radius: 6px; padding: 1px 6px; }}
            QFrame#quickWindow QToolButton#quickHint {{ color: {c['text2']}; border-radius: 6px; padding: 1px 6px; }}
            QFrame#quickWindow QLabel#quickNote {{ color: {c['text2']}; }}
            QFrame#quickWindow QPlainTextEdit#quickSource {{ background: transparent; color: {c['text2']}; border: none; border-left: 2px solid {c['border']}; padding-left: 8px; }}
            QFrame#quickWindow QPushButton#quickChip, QFrame#quickWindow QToolButton#quickChip {{ border-radius: 12px; padding: 4px 11px; }}
            QFrame#quickWindow QPushButton#quickChip:hover, QFrame#quickWindow QToolButton#quickChip:hover {{ border-color: {c['accent']}; color: {c['accent']}; }}
            QFrame#quickWindow QToolButton#quickChip::menu-indicator {{ image: none; width: 0px; }}
            QFrame#quickWindow QLineEdit {{ background: {c['bg2']}; color: {c['text1']}; border: 1px solid {c['border']}; border-radius: 8px; padding: 6px 8px; }}
            QFrame#quickWindow QPushButton#quickSend {{ background: {c['primary']}; color: white; border: none; border-radius: 14px; padding: 0px; min-width: 28px; max-width: 28px; min-height: 28px; max-height: 28px; }}
            QFrame#quickWindow QTextBrowser#quickResult {{ background: {c['bg2']}; color: {c['text1']}; border: none; border-radius: 10px; padding: 8px; }}
            QFrame#quickWindow QPushButton#quickPaste {{ background: {c['primary']}; border-color: {c['primary']}; color: white; }}
        """

    # ── showing ──────────────────────────────────────────────────────────

    def open_with(self, text: str, target: int, keys: str = "") -> None:
        """Show the window next to the mouse with the selected *text* of the window *target*."""
        self._stop_worker()
        self.target = target
        self.keys_label.setText(keys.replace("Space", tr("Пробел")))
        self.source.setPlainText(text)
        self.note.setVisible(not text)
        if not text:
            self.note.setText(tr("Выделенный текст не получен: ничего не выделено или программа запущена "
                                 "от имени администратора. Вставьте текст сюда (Ctrl+V)."))
        self.answer = ""
        self.result.clear()
        self.result.setVisible(False)
        self.own.clear()
        for button in (self.chat_button, self.copy_button, self.paste_button):
            button.setEnabled(False)
        self.adjustSize()
        pos = QCursor.pos()
        screen = QGuiApplication.screenAt(pos) or QGuiApplication.primaryScreen()
        area = screen.availableGeometry()
        x = min(max(area.left(), pos.x() + 12), area.right() - self.width())
        y = min(max(area.top(), pos.y() + 16), area.bottom() - self.sizeHint().height() - 140)
        self.move(x, y)
        self.show()
        self.raise_()
        self.activateWindow()
        (self.own if text else self.source).setFocus()

    def keyPressEvent(self, event) -> None:  # noqa: N802 (Qt API)
        if event.key() == Qt.Key_Escape:
            self.hide()
            return
        super().keyPressEvent(event)

    def hideEvent(self, event) -> None:  # noqa: N802 (Qt API)
        self._stop_worker()
        super().hideEvent(event)

    # ── asking ───────────────────────────────────────────────────────────

    def _on_chip(self) -> None:
        self.run(self.sender().property("action"))

    def _on_translate(self, action) -> None:
        self.run("translate", language=action.data())

    def _on_own(self) -> None:
        words = self.own.text().strip()
        if words:
            self.run("rewrite", own=words)

    def run(self, action: str, own: str = "", language: str = "") -> bool:
        text = self.source.toPlainText().strip()
        if not text:
            self.note.setVisible(True)
            self.note.setText(tr("Нет текста: вставьте его сюда (Ctrl+V)."))
            return False
        style = ""
        if action == "style":
            style = self.style_of(text)
            if not style:
                self._show_error(tr("«Мой стиль» — функция Pro: нужны лицензия Pro и образцы ваших текстов "
                                    "(Связь → «Мой стиль письма…»)."))
                return False
        backend = self.backend_of()
        if backend is None:
            self._show_error(tr("Модель не подключена: выберите модель в главном окне Fluxion."))
            return False
        self._stop_worker()
        self.answer = ""
        self.result.setVisible(True)
        self.result.setPlainText(tr("Думаю…"))
        for button in (self.chat_button, self.copy_button, self.paste_button):
            button.setEnabled(False)
        self.worker = AnswerWorker(backend, build_messages(action, text, own, language, style), self)
        self.worker.token.connect(self._on_token)
        self.worker.failed.connect(self._show_error)
        self.worker.finished.connect(self._on_finished)
        self.worker.start()
        return True

    def _on_token(self, chunk: str) -> None:
        self.answer += chunk
        self.result.setPlainText(self.answer)

    def _on_finished(self) -> None:
        worker, self.worker = self.worker, None
        if worker is not None:
            worker.deleteLater()
        ready = bool(self.answer.strip())
        for button in (self.chat_button, self.copy_button, self.paste_button):
            button.setEnabled(ready)
        self.paste_button.setEnabled(ready and bool(self.target))

    def _show_error(self, text: str) -> None:
        self.result.setVisible(True)
        self.result.setPlainText("✖ " + text)

    def _stop_worker(self) -> None:
        if self.worker is not None:
            self.worker.stop()
            self.worker.wait(3000)

    # ── the answer out ───────────────────────────────────────────────────

    def _on_copy(self) -> None:
        QGuiApplication.clipboard().setText(self.answer.strip())

    def _on_chat(self) -> None:
        self.to_chat.emit(self.source.toPlainText().strip())
        self.hide()

    def _on_paste(self) -> None:
        text, target = self.answer.strip(), self.target
        self.hide()
        paste_into(target, text)


class Everywhere(QObject):
    """The hotkey and the window together; the main window owns it."""

    def __init__(self, backend_of, style_of, colors: dict, parent=None):
        super().__init__(parent)
        self.hotkey = GlobalHotkey(self)
        self.hotkey.pressed.connect(self._on_hotkey)
        self.window = QuickWindow(backend_of, style_of, colors)
        self.keys = ""

    def start(self, keys: str) -> bool:
        self.keys = keys
        return self.hotkey.register(keys)

    def start_spare(self) -> bool:
        """The spare hotkey when another program holds the chosen one."""
        return self.start(SPARE_HOTKEY)

    def stop(self) -> None:
        self.hotkey.unregister()
        self.window.hide()

    def _on_hotkey(self) -> None:
        target = foreground_window()
        if self.window.isVisible() and target == int(self.window.winId()):
            self.window.hide()
            return
        text = capture_selection()
        self.window.open_with(text, target, self.keys)


def hotkey_text(sequence: QKeySequence) -> str:
    """«Ctrl+Alt+Space» of a key sequence edited in the settings."""
    return sequence.toString(QKeySequence.PortableText)

