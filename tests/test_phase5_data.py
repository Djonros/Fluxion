"""Phase 5 tests: filters, dedup, formatter, repo cloner.

All tests run offline (no HF dataset downloads needed).
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from data_pipeline import (
    Filters,
    InstructionFormatter,
    InstructionPair,
    MinHashDeduper,
    RepoCloner,
)


# ═══════════════════════════════════════════════════════════════════════════════
#  Filters
# ═══════════════════════════════════════════════════════════════════════════════

class TestFilters:
    @pytest.fixture()
    def flt(self):
        return Filters()

    def test_good_code_passes(self, flt):
        code = '''\
def fibonacci(n):
    """Compute the nth Fibonacci number."""
    if n <= 1:
        return n
    a, b = 0, 1
    for _ in range(2, n + 1):
        a, b = b, a + b
    return b
'''
        assert flt.keep(code)

    def test_too_short_rejected(self, flt):
        assert not flt.check_size("x = 1")

    def test_syntax_error_rejected(self, flt):
        code = "def broken(:\n    pass"
        assert not flt.check_syntax_ok(code)

    def test_secret_detected(self, flt):
        code = 'api_key = "sk-1234567890abcdef1234567890abcdef"\n'
        assert not flt.check_no_secrets(code)

    def test_no_secret_in_clean_code(self, flt):
        code = "def add(a, b):\n    return a + b\n"
        assert flt.check_no_secrets(code)

    def test_license_accepted(self, flt):
        assert flt.check_license("MIT")
        assert flt.check_license("Apache-2.0")
        assert flt.check_license("BSD-3-Clause")

    def test_no_license_accepted(self, flt):
        assert flt.check_license("")
        assert flt.check_license("unknown")

    def test_comment_ratio_in_range(self, flt):
        code = "# comment\ndef f():\n    pass\n# another\n"
        assert flt.check_comment_ratio(code)

    def test_excessive_comments_rejected(self):
        flt = Filters(max_comment_ratio=0.1)
        code = "\n".join(["# comment"] * 50 + ["def f():", "    pass"])
        assert not flt.check_comment_ratio(code)

    def test_aws_key_detected(self, flt):
        code = 'aws_secret_access_key = "abcdefghijklmnopqrstuvwxyz0123456789ABCD"\n'
        assert not flt.check_no_secrets(code)

    def test_private_key_detected(self, flt):
        code = 'key = "-----BEGIN RSA PRIVATE KEY-----\\nMII..."\n'
        assert not flt.check_no_secrets(code)


# ═══════════════════════════════════════════════════════════════════════════════
#  MinHashDeduper
# ═══════════════════════════════════════════════════════════════════════════════

class TestMinHashDeduper:
    @pytest.fixture()
    def deduper(self):
        return MinHashDeduper(threshold=0.85, num_perm=64)

    def test_unique_items_kept(self, deduper):
        items = [
            ("1", "def add(a, b):\n    return a + b"),
            ("2", "def multiply(a, b):\n    return a * b"),
            ("3", "class Animal:\n    def speak(self):\n        pass"),
        ]
        result = deduper.dedup(items)
        assert len(result) == 3

    def test_exact_duplicate_removed(self, deduper):
        code = "def add(a, b):\n    return a + b"
        items = [("1", code), ("2", code)]
        result = deduper.dedup(items)
        assert len(result) == 1

    def test_near_duplicate_removed(self, deduper):
        original = (
            "def fibonacci(n):\n"
            "    if n <= 1:\n"
            "        return n\n"
            "    a, b = 0, 1\n"
            "    for _ in range(2, n + 1):\n"
            "        a, b = b, a + b\n"
            "    return b"
        )
        near_dup = (
            "# Compute Fibonacci\n"
            "def fibonacci(n):\n"
            "    if n <= 1:\n"
            "        return n\n"
            "    a, b = 0, 1\n"
            "    for _ in range(2, n + 1):\n"
            "        a, b = b, a + b\n"
            "    return b"
        )
        items = [("1", original), ("2", near_dup)]
        result = deduper.dedup(items)
        assert len(result) == 1

    def test_empty_list(self, deduper):
        assert deduper.dedup([]) == []

    def test_is_duplicate(self, deduper):
        code = "def f():\n    return 42"
        assert deduper.is_duplicate(code, [code])

    def test_not_duplicate(self, deduper):
        code1 = "def f():\n    return 42"
        code2 = "class MyModel:\n    def save(self):\n        pass"
        assert not deduper.is_duplicate(code1, [code2])

    def test_dedup_strings(self, deduper):
        texts = ["def f(): pass", "def f(): pass", "class A: pass"]
        result = deduper.dedup_strings(texts)
        assert len(result) == 2


# ═══════════════════════════════════════════════════════════════════════════════
#  InstructionFormatter
# ═══════════════════════════════════════════════════════════════════════════════

class TestInstructionFormatter:
    @pytest.fixture()
    def fmt(self):
        return InstructionFormatter()

    def test_to_chatml_has_messages(self, fmt):
        pair = InstructionPair(instruction="Write a function", output="def f(): pass")
        result = fmt.to_chatml(pair)
        assert "messages" in result
        msgs = result["messages"]
        assert msgs[0]["role"] == "system"
        assert msgs[1]["role"] == "user"
        assert msgs[2]["role"] == "assistant"
        assert msgs[2]["content"] == "def f(): pass"

    def test_to_jsonl_line_valid(self, fmt):
        pair = InstructionPair(instruction="Test", output="x = 1")
        line = fmt.to_jsonl_line(pair)
        parsed = json.loads(line)
        assert parsed["messages"][1]["content"] == "Test"

    def test_format_batch_writes_file(self, fmt, tmp_path):
        pairs = [
            InstructionPair(instruction=f"Q{i}", output=f"A{i}", source="test")
            for i in range(5)
        ]
        out = tmp_path / "train.jsonl"
        count = fmt.format_batch(pairs, out)
        assert count == 5
        lines = out.read_text(encoding="utf-8").strip().split("\n")
        assert len(lines) == 5
        for line in lines:
            data = json.loads(line)
            assert "messages" in data

    def test_from_docstring_code(self, fmt):
        pair = fmt.from_docstring_code("Add two numbers.", "def add(a,b): return a+b")
        assert "docstring" in pair.instruction.lower() or "Add two numbers" in pair.instruction
        assert pair.source == "codesearchnet"

    def test_append_pair(self, fmt, tmp_path):
        out = tmp_path / "train.jsonl"
        p1 = InstructionPair(instruction="Q1", output="A1")
        p2 = InstructionPair(instruction="Q2", output="A2")
        fmt.append_pair(p1, out)
        fmt.append_pair(p2, out)
        lines = out.read_text(encoding="utf-8").strip().split("\n")
        assert len(lines) == 2


# ═══════════════════════════════════════════════════════════════════════════════
#  RepoCloner
# ═══════════════════════════════════════════════════════════════════════════════

class TestRepoCloner:
    def test_extract_name(self):
        assert RepoCloner._extract_name("https://github.com/user/myrepo") == "myrepo"
        assert RepoCloner._extract_name("https://github.com/user/myrepo.git") == "myrepo"
        assert RepoCloner._extract_name("https://github.com/user/myrepo/") == "myrepo"

    def test_clone_creates_target_dir(self, tmp_path):
        cloner = RepoCloner(target_dir=str(tmp_path / "repos"))
        assert (tmp_path / "repos").exists()

    def test_clone_skips_existing(self, tmp_path):
        target = tmp_path / "repos" / "existing-repo"
        target.mkdir(parents=True)
        (target / ".gitkeep").write_text("")

        cloner = RepoCloner(target_dir=str(tmp_path / "repos"))
        result = cloner.clone("https://github.com/test/existing-repo")
        assert result.success
        assert "existing-repo" in result.local_path

    def test_clone_all_returns_results(self, tmp_path):
        cloner = RepoCloner(target_dir=str(tmp_path / "repos"))
        results = cloner.clone_all([
            "https://github.com/nonexistent/fake-repo-12345",
        ])
        assert len(results) == 1


# ═══════════════════════════════════════════════════════════════════════════════
#  Integration: filter → dedup → format
# ═══════════════════════════════════════════════════════════════════════════════

class TestPipelineIntegration:
    def test_filter_dedup_format(self, tmp_path):
        flt = Filters()
        deduper = MinHashDeduper(threshold=0.85, num_perm=64)
        fmt = InstructionFormatter()

        raw_pairs = [
            InstructionPair(
                instruction="Write an add function",
                output=(
                    "def add(a: int, b: int) -> int:\n"
                    "    \"\"\"Return the sum of a and b.\"\"\"\n"
                    "    return a + b\n"
                ),
                source="test",
            ),
            InstructionPair(
                instruction="Write add",
                output=(
                    "def add(a: int, b: int) -> int:\n"
                    "    \"\"\"Return the sum of a and b.\"\"\"\n"
                    "    return a + b\n"
                ),
                source="test",
            ),
            InstructionPair(
                instruction="Write a Dog class",
                output=(
                    "class Dog:\n"
                    "    def __init__(self, name: str):\n"
                    "        self.name = name\n\n"
                    "    def bark(self) -> str:\n"
                    "        \"\"\"Return the bark sound.\"\"\"\n"
                    "        return f\"{self.name} says woof\"\n"
                ),
                source="test",
            ),
        ]

        filtered = [p for p in raw_pairs if flt.keep(p.output)]
        assert len(filtered) == 3

        items = [(str(i), p.output) for i, p in enumerate(filtered)]
        deduped = deduper.dedup(items)
        assert len(deduped) == 2  # one duplicate removed

        final_pairs = [filtered[int(pid)] for pid, _ in deduped]
        out = tmp_path / "train.jsonl"
        count = fmt.format_batch(final_pairs, out)
        assert count == 2
        lines = out.read_text(encoding="utf-8").strip().split("\n")
        for line in lines:
            data = json.loads(line)
            assert "messages" in data
            assert len(data["messages"]) == 3
