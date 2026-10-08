"""Crash logging + opt-in report dialog (Phase 14.4).

Uncaught exceptions are written to ``data/logs/crash-*.log`` and shown to the
user with a report dialog (copy / email). Sending is strictly opt-in: no
network calls happen without the user pressing a button.
"""
from __future__ import annotations

import os
import sys
import traceback
import urllib.parse
from datetime import datetime
from pathlib import Path

from . import APP_VERSION

REPORT_EMAIL = "djonros@gmail.com"

_last_crash_log: Path | None = None
_native_log = None  # kept open: faulthandler writes to it when the process dies


def _base_dir() -> Path:
    if getattr(sys, "frozen", False):
        base = Path(sys.executable).resolve().parent
    else:
        base = Path(__file__).resolve().parent.parent
    return base / "data"


def crash_dir() -> Path:
    path = _base_dir() / "logs"
    path.mkdir(parents=True, exist_ok=True)
    return path


def app_context() -> dict[str, str]:
    return {
        "app": "Fluxion",
        "version": f"desktop-{APP_VERSION}",
        "frozen": str(bool(getattr(sys, "frozen", False))),
        "python": sys.version.split()[0],
        "os": f"{sys.platform} {os.name}",
        "time": datetime.now().isoformat(timespec="seconds"),
    }


def write_crash(exc_type, exc_value, exc_tb) -> Path:
    """Write a crash log; returns its path. Never raises."""
    global _last_crash_log
    path = crash_dir() / f"crash-{datetime.now().strftime('%Y%m%d-%H%M%S')}.log"
    try:
        context = app_context()
        header = "\n".join(f"{k}: {v}" for k, v in context.items())
        body = "".join(traceback.format_exception(exc_type, exc_value, exc_tb))
        path.write_text(f"{header}\n\n{body}", encoding="utf-8")
    except OSError:
        pass
    _last_crash_log = path
    return path


def last_crash_log() -> Path | None:
    return _last_crash_log


def install_excepthook() -> None:
    """Log uncaught exceptions; keep default stderr behaviour."""
    previous = sys.excepthook

    def hook(exc_type, exc_value, exc_tb):
        write_crash(exc_type, exc_value, exc_tb)
        previous(exc_type, exc_value, exc_tb)

    sys.excepthook = hook


def enable_native_crash_log() -> Path | None:
    """Record native crashes (access violation in Qt, a driver…) to a file.

    Such a crash closes the window without a Python traceback, so neither
    the excepthook nor the training log on screen keeps anything.
    """
    global _native_log
    import faulthandler

    path = crash_dir() / "native-crash.log"
    try:
        _native_log = path.open("a", encoding="utf-8")
        _native_log.write(f"\n=== {app_context()['version']} started {app_context()['time']} ===\n")
        _native_log.flush()
        faulthandler.enable(file=_native_log, all_threads=True)
    except (OSError, RuntimeError, ValueError):
        return None
    return path


def show_crash_dialog(log_path: Path, parent=None) -> None:
    """Modal dialog: copy report / email it (opt-in) / open logs folder."""
    from PySide6.QtWidgets import QApplication, QMessageBox

    try:
        text = log_path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        text = "(отчёт недоступен)"

    box = QMessageBox(parent)
    box.setWindowTitle("Fluxion — неожиданная ошибка")
    box.setIcon(QMessageBox.Critical)
    box.setText(
        "Произошла ошибка. Отчёт сохранён локально:\n"
        f"{log_path}\n\n"
        "Отправка отчёта — только по вашему действию."
    )
    copy_btn = box.addButton("Копировать отчёт", QMessageBox.ActionRole)
    mail_btn = box.addButton("Отправить по email", QMessageBox.ActionRole)
    folder_btn = box.addButton("Открыть папку логов", QMessageBox.ActionRole)
    box.addButton("Закрыть", QMessageBox.RejectRole)
    box.exec()

    clicked = box.clickedButton()
    if clicked is copy_btn:
        QApplication.clipboard().setText(text)
    elif clicked is mail_btn:
        _open_mailto(text)
    elif clicked is folder_btn:
        _open_folder(log_path)


def _open_mailto(text: str) -> None:
    from PySide6.QtGui import QDesktopServices
    from PySide6.QtCore import QUrl

    body = urllib.parse.quote(text[:1500])
    url = QUrl(f"mailto:{REPORT_EMAIL}?subject=Fluxion%20crash%20report&body={body}")
    QDesktopServices.openUrl(url)


def _open_folder(log_path: Path) -> None:
    from PySide6.QtCore import QUrl
    from PySide6.QtGui import QDesktopServices

    QDesktopServices.openUrl(QUrl.fromLocalFile(str(log_path.parent)))
