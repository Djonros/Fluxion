"""The log of the program: ``%LOCALAPPDATA%\\Fluxion\\logs\\fluxion.log``, next to dxfka.log.

Before it, ``logger.*`` of the built program went nowhere: a problem on a
user's computer left no trace but the crash reports. The file turns over at
2 MB, three old ones are kept. httpx and httpcore write only warnings: at the
INFO level they log every URL, and the URL of the Telegram API holds the bot
token. Whatever still looks like a bot token is masked before it is written.
"""
from __future__ import annotations

import logging
import logging.handlers
import re
from pathlib import Path

LOG_NAME = "fluxion.log"
MAX_BYTES = 2_000_000
BACKUPS = 3
QUIET = ("httpx", "httpcore", "urllib3", "chromadb", "PIL", "matplotlib")
_TOKEN = re.compile(r"(bot)?\d{6,}:[A-Za-z0-9_-]{30,}")


def logs_dir() -> Path:
    """%LOCALAPPDATA%\\Fluxion\\logs (a portable copy: logs next to the exe)."""
    from . import app_paths

    if app_paths.is_frozen() and app_paths.is_portable():
        return app_paths.exe_dir() / "logs"
    return app_paths.user_root() / "logs"


class _MaskTokens(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        try:
            text = record.getMessage()
        except Exception:
            return True
        if _TOKEN.search(text):
            record.msg, record.args = _TOKEN.sub("***", text), None
        return True


def setup_logging(level: int = logging.INFO) -> Path | None:
    """Write the log of the program to a file; once per process. The file, or None if it cannot be made."""
    root = logging.getLogger()
    for handler in root.handlers:
        if getattr(handler, "_fluxion", False):
            return Path(handler.baseFilename)
    try:
        folder = logs_dir()
        folder.mkdir(parents=True, exist_ok=True)
        handler = logging.handlers.RotatingFileHandler(folder / LOG_NAME, maxBytes=MAX_BYTES,
                                                       backupCount=BACKUPS, encoding="utf-8", delay=True)
    except OSError:
        return None
    handler._fluxion = True  # type: ignore[attr-defined]
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)-7s %(name)s: %(message)s"))
    handler.addFilter(_MaskTokens())
    root.addHandler(handler)
    if root.level == logging.NOTSET or root.level > level:
        root.setLevel(level)
    for name in QUIET:
        logging.getLogger(name).setLevel(logging.WARNING)
    return Path(handler.baseFilename)
