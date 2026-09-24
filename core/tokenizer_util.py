"""Token counting for context-budget management.

Uses tiktoken (cl100k_base) as a fast approximation when available;
falls back to a character-based heuristic (4 chars ~= 1 token) otherwise.
"""
from __future__ import annotations

_CHARS_PER_TOKEN = 4
_tiktoken_enc = None

try:
    import tiktoken as _tiktoken  # type: ignore

    def _get_enc():
        global _tiktoken_enc
        if _tiktoken_enc is None:
            _tiktoken_enc = _tiktoken.get_encoding("cl100k_base")
        return _tiktoken_enc

    def count_tokens(text: str) -> int:
        return len(_get_enc().encode(text))

    def truncate_to_tokens(text: str, max_tokens: int) -> str:
        enc = _get_enc()
        tokens = enc.encode(text)
        if len(tokens) <= max_tokens:
            return text
        return enc.decode(tokens[:max_tokens])

    HAS_TIKTOKEN = True

except Exception:
    HAS_TIKTOKEN = False

    def count_tokens(text: str) -> int:  # type: ignore[misc]
        return max(1, len(text) // _CHARS_PER_TOKEN)

    def truncate_to_tokens(text: str, max_tokens: int) -> str:  # type: ignore[misc]
        limit = max_tokens * _CHARS_PER_TOKEN
        return text if len(text) <= limit else text[:limit]
