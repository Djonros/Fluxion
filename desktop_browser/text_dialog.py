"""A multi-line text question that fits the screen.

QInputDialog.getMultiLineText lays its label out on one line: the long
explanation of «Деталь в КОМПАС» made the window wider and taller than the
screen.  Here the explanation wraps, the text field takes what is left, and
the window is at most 80 % of the screen it opens on.
"""
from __future__ import annotations

from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import QDialog, QDialogButtonBox, QLabel, QPlainTextEdit, QScrollArea, QVBoxLayout

from .i18n import tr

SCREEN_SHARE = 0.8


class TextDialog(QDialog):
    def __init__(self, title: str, label: str, text: str = "", placeholder: str = "", parent=None):
        super().__init__(parent)
        self.setWindowTitle(title)
        box = QVBoxLayout(self)
        note = QLabel(label)
        note.setWordWrap(True)
        scroll = QScrollArea()   # a long explanation scrolls instead of pushing the window off the screen
        scroll.setWidget(note)
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.NoFrame)
        scroll.setMaximumHeight(160)
        box.addWidget(scroll)
        self.editor = QPlainTextEdit(text)
        self.editor.setPlaceholderText(placeholder)
        box.addWidget(self.editor, 1)
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.button(QDialogButtonBox.Ok).setText(tr("Готово"))
        buttons.button(QDialogButtonBox.Cancel).setText(tr("Отмена"))
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        box.addWidget(buttons)
        self._fit_screen(parent)
        self.editor.setFocus()

    def _fit_screen(self, parent) -> None:
        screen = (parent.screen() if parent is not None else None) or QGuiApplication.primaryScreen()
        area = screen.availableGeometry() if screen is not None else None
        width, height = 620, 380
        if area is not None:
            width = min(width, int(area.width() * SCREEN_SHARE))
            height = min(height, int(area.height() * SCREEN_SHARE))
            self.setMaximumSize(int(area.width() * SCREEN_SHARE), int(area.height() * SCREEN_SHARE))
        self.resize(width, height)

    def text(self) -> str:
        return self.editor.toPlainText()


def ask_text(parent, title: str, label: str, text: str = "", placeholder: str = "") -> tuple[str, bool]:
    """Like QInputDialog.getMultiLineText: (text, accepted)."""
    dialog = TextDialog(title, label, text, placeholder, parent)
    accepted = dialog.exec() == QDialog.Accepted
    return dialog.text(), accepted
