"""Token counting for context-budget management.

With tiktoken (cl100k_base) when it is there and its tables load; it is within
-1…+18 % of the Qwen2.5 tokenizer on Russian, English and code.

The built program has no tiktoken, and from the sources the first
``get_encoding`` downloads its table: without the internet it fails, and the
count falls back to an estimate instead of breaking the chat. The estimate
used to be «4 characters per token», which for Russian is about 1.6 times too
few: measured on Qwen2.5-7B (the tokenizer of the built-in models) a Russian
text has 2.65 characters per token, English 4.2, code 4.0 — so a long Russian
chat could overflow the model's window. Now each kind of character has its
weight, fitted on 31 texts of this project (Russian, English, code) against the
Qwen2.5 tokenizer: the estimate is 97–128 % of the real count on texts of a
page or more (110 % on average, the safe side), 85 % on a two-line snippet of
code with one-letter names.
"""
from __future__ import annotations

import math
import re

# tokens per character, by kind (fitted against Qwen2.5, see above)
WEIGHTS = {"cyrillic": 0.42, "letter": 0.21, "digit": 1.0, "punct": 0.75, "space": 0.16, "other": 0.5}
_CYRILLIC = re.compile(r"[\u0400-\u04ff]")
_LETTERS = re.compile(r"[A-Za-z]")
_DIGITS = re.compile(r"[0-9]")
_SPACES = re.compile(r"[ \t\r\n]")

_enc = None
_enc_failed = False


def estimate_tokens(text: str) -> int:
    """Tokens of *text* without a tokenizer, a little over the real count for Russian, English, code."""
    if not text:
        return 0
    cyrillic = len(_CYRILLIC.findall(text))
    letters = len(_LETTERS.findall(text))
    digits = len(_DIGITS.findall(text))
    spaces = len(_SPACES.findall(text))
    ascii_chars = len(text.encode("ascii", "ignore"))
    punct = ascii_chars - letters - digits - spaces
    other = len(text) - cyrillic - ascii_chars
    w = WEIGHTS
    return max(1, math.ceil(cyrillic * w["cyrillic"] + letters * w["letter"] + digits * w["digit"]
                            + punct * w["punct"] + spaces * w["space"] + other * w["other"]))


def _encoding():
    """The tiktoken encoding, or None: not installed, or its table cannot be loaded (no internet)."""
    global _enc, _enc_failed
    if _enc is None and not _enc_failed:
        try:
            import tiktoken  # type: ignore

            _enc = tiktoken.get_encoding("cl100k_base")
        except Exception:
            _enc_failed = True
    return _enc


def count_tokens(text: str) -> int:
    enc = _encoding()
    if enc is not None:
        try:
            return len(enc.encode(text))
        except Exception:
            pass
    return estimate_tokens(text)


def truncate_to_tokens(text: str, max_tokens: int) -> str:
    enc = _encoding()
    if enc is not None:
        try:
            tokens = enc.encode(text)
            if len(tokens) <= max_tokens:
                return text
            return enc.decode(tokens[:max_tokens])
        except Exception:
            pass
    if max_tokens <= 0:
        return ""
    total = estimate_tokens(text)
    if total <= max_tokens:
        return text
    cut = int(len(text) * max_tokens / total)
    while cut > 0 and estimate_tokens(text[:cut]) > max_tokens:
        cut = int(cut * 0.95)
    return text[:cut]


def has_tiktoken() -> bool:
    return _encoding() is not None
