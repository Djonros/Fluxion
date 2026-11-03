"""The chat input: several lines that grow with the text, Enter sends, Shift+Enter breaks the line.

It replaced a one-line QLineEdit, which kept a long message on one line out of
sight and cut a pasted text at 32 767 characters.  The methods of the old field
the window uses — ``text()``, ``setText()``, ``returnPressed`` — are kept.

Files dropped on the field or pasted with Ctrl+V, and a picture copied to the
clipboard (a screenshot), are not inserted as text: ``filesAttached`` gives their
paths to the window, which attaches them to the message.
"""
from __future__ import annotations

import tempfile
import time
from pathlib import Path

from PySide6.QtCore import QMimeData, Qt, Signal
from PySide6.QtGui import QImage, QKeyEvent, QTextOption
from PySide6.QtWidgets import QFrame, QPlainTextEdit, QSizePolicy


def pasted_image_path(image: QImage) -> str:
    """Save a picture from the clipboard to a PNG in the temp folder; its path, or "" when it fails."""
    folder = Path(tempfile.gettempdir()) / "fluxion-paste"
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"скриншот-{time.strftime('%Y%m%d-%H%M%S')}.png"  # Cyrillic in strftime fails on Windows
    return str(path) if image.save(str(path), "PNG") else ""


class ChatInput(QPlainTextEdit):
    returnPressed = Signal()        # Enter: send
    agentRequested = Signal()       # Ctrl+Enter: run this message through the agent
    filesAttached = Signal(list)    # paths of files dropped or pasted, of a pasted picture

    MIN_LINES = 1
    MAX_LINES = 8

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("chatInput")
        self.setFrameShape(QFrame.NoFrame)
        self.setTabChangesFocus(True)
        self.setWordWrapMode(QTextOption.WrapAtWordBoundaryOrAnywhere)
        self.setLineWrapMode(QPlainTextEdit.WidgetWidth)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        # a bound method, not a lambda: Qt drops the connection with the widget, while a lambda
        # would be called by a document still emitting during teardown, on a deleted widget
        self.document().documentLayout().documentSizeChanged.connect(self._on_document_size)
        self._fit()

    # ── the QLineEdit methods the window uses ─────────────────────────────

    def text(self) -> str:
        return self.toPlainText()

    def setText(self, text: str) -> None:
        self.setPlainText(text or "")
        self.moveCursor(self.textCursor().MoveOperation.End)

    # ── keys ──────────────────────────────────────────────────────────────

    def keyPressEvent(self, event: QKeyEvent) -> None:
        if event.key() in (Qt.Key_Return, Qt.Key_Enter):
            modifiers = event.modifiers()
            if modifiers & Qt.ShiftModifier:
                self.insertPlainText("\n")
                return
            if modifiers & Qt.ControlModifier:
                self.agentRequested.emit()
                return
            self.returnPressed.emit()
            return
        super().keyPressEvent(event)

    # ── files and pictures: dropped or pasted ──────────────────────────────

    @staticmethod
    def _local_files(source: QMimeData) -> list[str]:
        if not source.hasUrls():
            return []
        return [url.toLocalFile() for url in source.urls() if url.isLocalFile() and Path(url.toLocalFile()).is_file()]

    def canInsertFromMimeData(self, source: QMimeData) -> bool:  # noqa: N802 (Qt API)
        return bool(self._local_files(source)) or source.hasImage() or super().canInsertFromMimeData(source)

    def insertFromMimeData(self, source: QMimeData) -> None:  # noqa: N802 (Qt API)
        files = self._local_files(source)
        if files:
            self.filesAttached.emit(files)
            return
        if source.hasImage() and not source.hasText():
            path = pasted_image_path(QImage(source.imageData()))
            if path:
                self.filesAttached.emit([path])
            return
        super().insertFromMimeData(source)

    # ── height: one line empty, up to MAX_LINES, then a scroll bar ────────

    def _on_document_size(self, _size) -> None:
        self._fit()

    def _line_height(self) -> int:
        return self.fontMetrics().lineSpacing()

    def _fit(self) -> None:
        lines = max(self.MIN_LINES, min(self.MAX_LINES, self._visual_lines()))
        margins = self.contentsMargins()
        doc_margin = int(self.document().documentMargin() * 2)
        height = lines * self._line_height() + doc_margin + margins.top() + margins.bottom() + 2
        if self.height() != height:
            self.setFixedHeight(height)
        self.setVerticalScrollBarPolicy(
            Qt.ScrollBarAsNeeded if self._visual_lines() > self.MAX_LINES else Qt.ScrollBarAlwaysOff
        )

    def _visual_lines(self) -> int:
        """Lines on the screen, wrapped ones included."""
        count = 0
        block = self.document().firstBlock()
        while block.isValid():
            layout = block.layout()
            count += max(1, layout.lineCount() if layout is not None else 1)
            block = block.next()
        return count

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._fit()
