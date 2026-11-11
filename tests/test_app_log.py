"""The log of the program: a file with rotation, quiet httpx, no bot tokens in it."""
from __future__ import annotations

import logging

import pytest

from core import app_log


@pytest.fixture
def log_file(tmp_path, monkeypatch):
    monkeypatch.setenv("FLUXION_HOME", str(tmp_path / "home"))
    root = logging.getLogger()
    before, level = list(root.handlers), root.level
    levels = {name: logging.getLogger(name).level for name in app_log.QUIET}
    path = app_log.setup_logging()
    yield path
    for handler in root.handlers[:]:
        if handler not in before:
            handler.close()
            root.removeHandler(handler)
    root.setLevel(level)
    for name, lv in levels.items():
        logging.getLogger(name).setLevel(lv)


def test_the_log_goes_to_the_logs_folder_and_turns_over(log_file, tmp_path):
    assert log_file == tmp_path / "home" / "logs" / "fluxion.log"
    handler = next(h for h in logging.getLogger().handlers if getattr(h, "_fluxion", False))
    assert handler.maxBytes == 2_000_000 and handler.backupCount == 3
    logging.getLogger("rag.indexer").info("Индекс: %d файлов", 3)
    handler.flush()
    assert "Индекс: 3 файлов" in log_file.read_text(encoding="utf-8")


def test_set_up_once(log_file):
    assert app_log.setup_logging() == log_file
    assert sum(getattr(h, "_fluxion", False) for h in logging.getLogger().handlers) == 1


def test_httpx_does_not_log_urls_and_tokens_are_masked(log_file):
    assert logging.getLogger("httpx").getEffectiveLevel() == logging.WARNING
    token = "1234567890:AAHdqTcvCH1vGWJxfSeofSAs0K5PALDsawQ"
    logging.getLogger("httpx").info("HTTP Request: GET https://api.telegram.org/bot%s/getUpdates", token)
    logging.getLogger("bots").warning("Telegram: bot%s/getFile failed", token)
    for h in logging.getLogger().handlers:
        h.flush()
    text = log_file.read_text(encoding="utf-8")
    assert token not in text and "getUpdates" not in text
    assert "Telegram: ***/getFile failed" in text
