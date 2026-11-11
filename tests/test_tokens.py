"""Counting tokens without a tokenizer: Russian is not undercounted; tiktoken failing is not an error."""
from __future__ import annotations

import sys
import types

import core.tokenizer_util as tokens

RU = ("Пакет на лазер: развёртки листовых деталей сборки раскладываются по папкам толщин. "
      "Если толщину из модели получить не удалось, деталь обрабатывается вручную.")
EN = "The laser package puts the flat patterns of the sheet parts of an assembly into thickness folders."
CODE = "def flat_length(t: float, r: float, k: float) -> float:\n    return (r + k * t) * math.pi / 2\n"

# Real token counts of these texts by the Qwen2.5-7B tokenizer (llama.cpp, measured 2026-11-11)
QWEN = {RU: 50, EN: 18, CODE: 34}


def test_the_estimate_is_on_the_safe_side_of_the_real_count():
    for text in (RU, EN):
        est, real = tokens.estimate_tokens(text), QWEN[text]
        assert real <= est <= real * 1.35, (text[:20], est, real)
    # a two-line snippet with one-letter names is the worst case: still within 20 %
    assert tokens.estimate_tokens(CODE) >= 0.8 * QWEN[CODE]


def test_russian_is_counted_about_twice_the_old_four_characters_per_token():
    assert tokens.estimate_tokens(RU) >= 1.5 * (len(RU) // 4)


def test_empty_and_tiny_texts():
    assert tokens.estimate_tokens("") == 0
    assert tokens.estimate_tokens("а") == 1


def test_truncate_keeps_the_estimate_within_the_limit():
    text = RU * 50
    cut = tokens.truncate_to_tokens(text, 100)
    assert text.startswith(cut) and 0 < tokens.estimate_tokens(cut) <= 100
    assert tokens.truncate_to_tokens(RU, 10_000) == RU
    assert tokens.truncate_to_tokens(RU, 0) == ""


def test_tiktoken_that_cannot_load_its_table_falls_back(monkeypatch):
    broken = types.ModuleType("tiktoken")

    def get_encoding(name):
        raise ConnectionError("no internet: cannot download cl100k_base")
    broken.get_encoding = get_encoding
    monkeypatch.setitem(sys.modules, "tiktoken", broken)
    monkeypatch.undo()  # the conftest fixture replaced _encoding: use the real one here
    monkeypatch.setitem(sys.modules, "tiktoken", broken)
    monkeypatch.setattr(tokens, "_enc", None)
    monkeypatch.setattr(tokens, "_enc_failed", False)
    assert tokens.count_tokens(RU) == tokens.estimate_tokens(RU)
    assert tokens.has_tiktoken() is False
    assert tokens.truncate_to_tokens(RU * 10, 20)
