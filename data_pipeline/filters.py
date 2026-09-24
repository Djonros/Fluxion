"""Filters: quality gates for collected Python code.

Each filter checks a single criterion and returns True/False.
``Filters.keep()`` combines them all.
"""
from __future__ import annotations

import ast
import logging
import re

logger = logging.getLogger(__name__)

# ── Limits ───────────────────────────────────────────────────────────────────

MIN_CHARS = 50
MAX_CHARS = 50_000
MIN_LINES = 3
MAX_LINES = 2_000
MIN_COMMENT_RATIO = 0.0
MAX_COMMENT_RATIO = 0.6
MIN_TOKENS_EST = 15
MAX_TOKENS_EST = 8_000

# ── License keywords ─────────────────────────────────────────────────────────

LICENSE_KEYWORDS = {
    "mit", "apache", "apache-2.0", "bsd", "bsd-2-clause", "bsd-3-clause",
    "isc", "unlicense", "mozilla", "lgpl", "gpl-3.0",
}

# ── Secret patterns ──────────────────────────────────────────────────────────

_SECRET_PATTERNS = [
    re.compile(r"(?i)(api[_-]?key|secret|password|passwd|token|auth)\s*[:=]\s*['\"][A-Za-z0-9+/=_-]{16,}['\"]"),
    re.compile(r"(?i)aws_access_key_id\s*[:=]\s*['\"]?AKIA[0-9A-Z]{16}['\"]?"),
    re.compile(r"(?i)aws_secret_access_key\s*[:=]\s*['\"][A-Za-z0-9/+]{40}['\"]"),
    re.compile(r"(?i)-----BEGIN (RSA |EC )?PRIVATE KEY-----"),
    re.compile(r"(?i)gh[pousr]_[A-Za-z0-9]{36,}"),
    re.compile(r"(?i)xox[baprs]-[A-Za-z0-9-]+"),
]

# ── Banned content ───────────────────────────────────────────────────────────

_BANNED_FRAGMENTS = (
    "# !/usr/bin/env python",
    "if __name__ ==",
    "input(",
    "sys.exit(",
    "os.system(",
)


class Filters:
    """Composable quality filters for Python source code."""

    def __init__(
        self,
        min_chars: int = MIN_CHARS,
        max_chars: int = MAX_CHARS,
        min_lines: int = MIN_LINES,
        max_lines: int = MAX_LINES,
        min_comment_ratio: float = MIN_COMMENT_RATIO,
        max_comment_ratio: float = MAX_COMMENT_RATIO,
        check_syntax: bool = True,
        check_secrets: bool = True,
    ):
        self.min_chars = min_chars
        self.max_chars = max_chars
        self.min_lines = min_lines
        self.max_lines = max_lines
        self.min_comment_ratio = min_comment_ratio
        self.max_comment_ratio = max_comment_ratio
        self.check_syntax = check_syntax
        self.check_secrets = check_secrets

    def keep(self, code: str, license: str = "") -> bool:
        """Return True if *code* passes all filters."""
        return (
            self.check_size(code)
            and self.check_lines(code)
            and (self.check_syntax_ok(code) if self.check_syntax else True)
            and self.check_comment_ratio(code)
            and (self.check_no_secrets(code) if self.check_secrets else True)
            and self.check_license(license)
        )

    # ── individual filters ─────────────────────────────────────────────────

    def check_size(self, code: str) -> bool:
        return self.min_chars <= len(code) <= self.max_chars

    def check_lines(self, code: str) -> bool:
        lines = code.strip().count("\n") + 1
        return self.min_lines <= lines <= self.max_lines

    def check_syntax_ok(self, code: str) -> bool:
        try:
            ast.parse(code)
            return True
        except SyntaxError:
            return False

    def check_comment_ratio(self, code: str) -> bool:
        lines = code.strip().splitlines()
        if not lines:
            return False
        comment_lines = sum(
            1 for ln in lines if ln.strip().startswith("#")
        )
        ratio = comment_lines / len(lines)
        return self.min_comment_ratio <= ratio <= self.max_comment_ratio

    def check_no_secrets(self, code: str) -> bool:
        for pattern in _SECRET_PATTERNS:
            if pattern.search(code):
                return False
        return True

    def check_license(self, license: str) -> bool:
        if not license:
            return True
        low = license.lower().strip()
        if low in ("unknown", "none", "n/a", ""):
            return True
        return any(kw in low for kw in LICENSE_KEYWORDS)
