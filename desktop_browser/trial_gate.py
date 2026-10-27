"""Confirmation of trial file edits: asked in the GUI thread, awaited by the agent."""
from __future__ import annotations

import threading

from PySide6.QtCore import Q_ARG, QMetaObject, QObject, Qt, QThread, Signal, Slot
from PySide6.QtWidgets import QMessageBox, QWidget

from licensing import trial_consume, trial_remaining
from .i18n import tr

KIND_LABELS = {"write": tr("запись файла"), "edit": tr("правка файла")}


def ask_trial_write(parent: QWidget | None, path: str, kind: str, remaining: int) -> bool:
    """Show the modal question about one trial edit; return True when it is allowed."""
    box = QMessageBox(parent)
    box.setWindowTitle(tr("Пробная правка (Pro)"))
    box.setIcon(QMessageBox.Question)
    box.setText(tr("Пробная запись: {path}. Разрешить? Осталось попыток: {remaining}", path=path, remaining=remaining))
    box.setInformativeText(
        tr("Агент просит разрешение: {value}. "
        "Без лицензии Pro доступны несколько пробных правок.", value=KIND_LABELS.get(kind, kind))
    )
    allow = box.addButton(tr("Разрешить"), QMessageBox.AcceptRole)
    box.addButton(tr("Отменить"), QMessageBox.RejectRole)
    box.exec()
    return box.clickedButton() is allow


class TrialWriteGate(QObject):
    """``write_confirm`` callback of the agent for users without a Pro licence."""

    changed = Signal()

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._window: QWidget | None = None
        self._answer = False
        self._answered = threading.Event()

    def attach(self, window: QWidget) -> None:
        """Use *window* as the parent of the confirmation dialog."""
        self._window = window

    @Slot(str, str)
    def _ask(self, path: str, kind: str) -> None:
        """GUI-thread slot: ask the user and spend one trial edit on consent."""
        allowed = False
        try:
            remaining = trial_remaining()
            if remaining > 0 and ask_trial_write(self._window, path, kind, remaining):
                allowed = trial_consume()
        finally:
            self._answer = allowed
            self._answered.set()
            self.changed.emit()

    def confirm(self, path: str, kind: str) -> bool:
        """Return True when the user allowed changing *path*; safe from any thread."""
        self._answer = False
        self._answered.clear()
        if QThread.currentThread() == self.thread():
            self._ask(path, kind)
        else:
            QMetaObject.invokeMethod(
                self, "_ask", Qt.QueuedConnection, Q_ARG(str, path), Q_ARG(str, kind)
            )
            self._answered.wait()
        return self._answer
