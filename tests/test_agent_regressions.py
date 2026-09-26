"""Regression tests for agent bugs found in the production-readiness review.

Each test pins one concrete defect so it cannot silently come back.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from core.config import GenerationSettings
from orchestrator import CodingAgent, git_helper
from orchestrator.agent import STOP_SEQUENCES, ToolResult


def _agent(tmp_path, replies, **kw):
    backend = MagicMock()
    backend.generate.side_effect = list(replies)
    kw.setdefault("git_enabled", False)
    kw.setdefault("lang", "off")
    return CodingAgent(backend=backend, project_root=str(tmp_path), **kw), backend


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=repo, capture_output=True, text=True, check=True
    ).stdout


@pytest.fixture()
def repo(tmp_path):
    if subprocess.run(["git", "--version"], capture_output=True).returncode != 0:
        pytest.skip("git not installed")
    _git(tmp_path, "init", "-q")
    _git(tmp_path, "config", "user.email", "t@example.com")
    _git(tmp_path, "config", "user.name", "t")
    (tmp_path / "work.py").write_text("v1\n", encoding="utf-8")
    (tmp_path / "other.py").write_text("x = 1\n", encoding="utf-8")
    _git(tmp_path, "add", ".")
    _git(tmp_path, "commit", "-qm", "init")
    return tmp_path


# ── parsing ──────────────────────────────────────────────────────────────────

class TestParsing:
    def test_multiline_finish_is_not_truncated(self, tmp_path):
        answer = "Вот решение:\n```python\nprint(1)\n```\nГотово."
        agent, _ = _agent(tmp_path, [f"Thought: done\nAction: finish {answer}"])
        result = agent.run("q")
        assert result.final_answer == answer

    def test_multiline_finish_prefix(self):
        _, action = CodingAgent._parse_response("Finish: line1\nline2")
        assert action == "finish line1\nline2"

    def test_hallucinated_observation_not_written_to_file(self, tmp_path):
        reply = (
            "Action: write_file a.py\nx = 1\n"
            "Observation: Wrote a.py\nThought: done\nAction: finish ok"
        )
        agent, _ = _agent(
            tmp_path, [reply, "Action: finish ok"], allow_write=True, verify_after_write=False
        )
        agent.run("w")
        assert (tmp_path / "a.py").read_text(encoding="utf-8") == "x = 1"

    def test_markdown_fence_stripped_from_written_file(self, tmp_path):
        agent, _ = _agent(tmp_path, [], allow_write=True)
        res = agent._tool_write_file("calc.py\n```python\ndef add(a, b):\n    return a + b\n```\n")
        assert res.success
        assert (tmp_path / "calc.py").read_text(encoding="utf-8") == "def add(a, b):\n    return a + b\n"

    def test_backticked_tool_name_is_parsed(self):
        _, action = CodingAgent._parse_response("Action: `read_file` a.py")
        assert action == "read_file a.py"


# ── observations & loop control ─────────────────────────────────────────────

class TestLoop:
    def test_failed_tool_output_reaches_the_model(self):
        tr = ToolResult("run_tests", "", "E   assert 1 == 2", success=False, error="Tests failed")
        obs = CodingAgent._format_observation(tr)
        assert "Tests failed" in obs and "assert 1 == 2" in obs

    def test_rerun_after_edit_is_not_blocked(self, tmp_path):
        (tmp_path / "m.py").write_text("X = 1\n", encoding="utf-8")
        replies = [
            "Action: read_file m.py",
            "Action: edit_file m.py\n---OLD---\nX = 1\n---NEW---\nX = 2",
            "Action: read_file m.py",
            "Action: finish ok",
        ]
        agent, _ = _agent(tmp_path, replies, allow_write=True, verify_after_write=False)
        result = agent.run("t")
        assert "already executed" not in result.steps[2].observation
        assert "X = 2" in result.steps[2].observation

    def test_repeat_still_blocked_without_changes(self, tmp_path):
        (tmp_path / "m.py").write_text("X = 1\n", encoding="utf-8")
        agent, _ = _agent(tmp_path, ["Action: read_file m.py"] * 2 + ["Action: finish ok"])
        result = agent.run("t")
        assert "already executed" in result.steps[1].observation

    def test_stop_sequences_passed_to_backend(self, tmp_path):
        agent, backend = _agent(tmp_path, ["Action: finish ok"])
        agent.run("t")
        gen = backend.generate.call_args[0][1]
        assert isinstance(gen, GenerationSettings)
        assert set(STOP_SEQUENCES) <= set(gen.stop)

    def test_backend_generation_settings_respected(self, tmp_path):
        agent, backend = _agent(tmp_path, ["Action: finish ok"])
        backend.generation = GenerationSettings(temperature=0.7, max_tokens=111)
        agent.run("t")
        gen = backend.generate.call_args[0][1]
        assert gen.temperature == 0.7 and gen.max_tokens == 111

    def test_backend_without_gen_parameter_still_works(self, tmp_path):
        class OldBackend:
            def generate(self, messages):
                return "Action: finish ok"

        agent = CodingAgent(backend=OldBackend(), project_root=str(tmp_path), lang="off")
        assert agent.run("t").final_answer == "ok"

    def test_history_is_compacted(self, tmp_path):
        agent, _ = _agent(tmp_path, [], context_char_budget=2000)
        messages = [{"role": "system", "content": "s"}, {"role": "user", "content": "task"}]
        messages += [{"role": "user", "content": "Observation:\n" + "y" * 3000} for _ in range(6)]
        agent._compact_history(messages)
        assert sum(len(m["content"]) for m in messages) < 3000 * 6
        assert messages[-1]["content"].endswith("y" * 100)  # recent kept intact


# ── read / search tools ─────────────────────────────────────────────────────

class TestReadTools:
    def test_read_outside_project_denied(self, tmp_path):
        outside = tmp_path.parent / f"{tmp_path.name}-secret.txt"
        outside.write_text("TOP SECRET", encoding="utf-8")
        agent, _ = _agent(tmp_path, [])
        for arg in (str(outside), f"../{outside.name}"):
            res = agent._tool_read_file(arg)
            assert not res.success and "TOP SECRET" not in res.output

    def test_read_secret_file_denied(self, tmp_path):
        (tmp_path / ".env").write_text("API_KEY=123", encoding="utf-8")
        (tmp_path / ".env.example").write_text("API_KEY=", encoding="utf-8")
        agent, _ = _agent(tmp_path, [])
        assert not agent._tool_read_file(".env").success
        assert agent._tool_read_file(".env.example").success

    def test_grep_outside_project_denied(self, tmp_path):
        agent, _ = _agent(tmp_path, [])
        res = agent._tool_grep(f"root {Path(sys.executable).parent}")
        assert not res.success

    def test_grep_skips_secret_files(self, tmp_path):
        (tmp_path / ".env").write_text("TOKEN=abc\n", encoding="utf-8")
        agent, _ = _agent(tmp_path, [])
        assert "abc" not in agent._tool_grep("TOKEN").output

    def test_read_file_line_range_and_paging(self, tmp_path):
        lines = [f"line {i} " + "z" * 60 for i in range(1, 501)]
        (tmp_path / "big.py").write_text("\n".join(lines), encoding="utf-8")
        agent, _ = _agent(tmp_path, [])
        first = agent._tool_read_file("big.py")
        assert "truncated" in first.output and "read_file big.py" in first.output
        page = agent._tool_read_file("big.py 400-402")
        assert page.success
        assert "line 400 " in page.output and "line 402 " in page.output
        assert "line 403 " not in page.output

    def test_grep_searches_non_python_files(self, tmp_path):
        (tmp_path / "settings.yaml").write_text("timeout: 30\n", encoding="utf-8")
        agent, _ = _agent(tmp_path, [])
        assert "settings.yaml:1" in agent._tool_grep("timeout").output

    def test_list_files(self, tmp_path):
        (tmp_path / "src").mkdir()
        (tmp_path / "src" / "a.py").write_text("", encoding="utf-8")
        (tmp_path / "node_modules").mkdir()
        (tmp_path / "node_modules" / "junk.js").write_text("", encoding="utf-8")
        agent, _ = _agent(tmp_path, [])
        out = agent._tool_list_files("").output
        assert "src/a.py" in out and "junk.js" not in out
        assert "src/a.py" in agent._tool_list_files("**/*.py").output


# ── write / edit ─────────────────────────────────────────────────────────────

class TestEdit:
    def test_edit_crlf_file(self, tmp_path):
        f = tmp_path / "win.py"
        f.write_bytes(b"def f():\r\n    return 1\r\n")
        agent, _ = _agent(tmp_path, [], allow_write=True)
        res = agent._tool_edit_file("win.py\n---OLD---\n    return 1\n---NEW---\n    return 2\n")
        assert res.success, res.error
        assert f.read_bytes() == b"def f():\r\n    return 2\r\n"

    def test_edit_tolerates_trailing_whitespace(self, tmp_path):
        f = tmp_path / "m.py"
        f.write_text("a = 1   \nb = 2\n", encoding="utf-8")
        agent, _ = _agent(tmp_path, [], allow_write=True)
        res = agent._tool_edit_file("m.py\n---OLD---\na = 1\nb = 2\n---NEW---\na = 10\nb = 20\n")
        assert res.success, res.error
        assert f.read_text(encoding="utf-8") == "a = 10\nb = 20\n"

    def test_backup_keeps_original_version(self, tmp_path):
        f = tmp_path / "m.py"
        f.write_text("ORIGINAL\n", encoding="utf-8")
        agent, _ = _agent(tmp_path, [], allow_write=True)
        agent._tool_write_file("m.py\nSECOND\n")
        agent._tool_write_file("m.py\nTHIRD\n")
        backup = tmp_path / ".fluxion-backup" / "m.py"
        assert backup.read_text(encoding="utf-8") == "ORIGINAL\n"

    def test_writing_secret_files_refused(self, tmp_path):
        agent, _ = _agent(tmp_path, [], allow_write=True)
        assert not agent._tool_write_file(".env\nX=1\n").success


# ── tests / verification ─────────────────────────────────────────────────────

class TestVerification:
    def test_missing_pytest_is_not_reported_as_passed(self, tmp_path, monkeypatch):
        def fake_run(cmd, **kw):
            return subprocess.CompletedProcess(cmd, 1, stdout="", stderr="No module named pytest")

        monkeypatch.setattr("orchestrator.agent.subprocess.run", fake_run)
        replies = [
            "Action: write_file u.py\nX = 1\n",
            "Action: finish done",
            "Action: finish done",
        ]
        agent, _ = _agent(tmp_path, replies, allow_write=True)
        result = agent.run("t")
        assert result.verification == "syntax_only"
        gate = [s for s in result.steps if s.tool_args == "(auto-verify)"][0]
        assert "pytest не установлен" in gate.observation

    def test_failing_tests_return_output(self, tmp_path, monkeypatch):
        def fake_run(cmd, **kw):
            return subprocess.CompletedProcess(cmd, 1, stdout="FAILED test_m.py::test - assert 1 == 2", stderr="")

        monkeypatch.setattr("orchestrator.agent.subprocess.run", fake_run)
        agent, _ = _agent(tmp_path, [])
        res = agent._tool_run_tests("")
        assert not res.success and "assert 1 == 2" in CodingAgent._format_observation(res)

    @pytest.mark.parametrize("bad", ["-p evil", "--rootdir=/", "-c /etc/x.ini", "/etc", "../other"])
    def test_dangerous_pytest_args_rejected(self, tmp_path, bad):
        agent, _ = _agent(tmp_path, [])
        res = agent._tool_run_tests(bad)
        assert not res.success

    def test_exec_disabled(self, tmp_path):
        agent, _ = _agent(tmp_path, [], allow_exec=False)
        assert not agent._tool_run_tests("").success
        assert "run_tests" not in agent.system_prompt


# ── git safety ───────────────────────────────────────────────────────────────

class TestGitSafety:
    def test_checkpoint_keeps_uncommitted_work(self, repo):
        (repo / "work.py").write_text("user WIP\n", encoding="utf-8")
        (repo / "draft.py").write_text("draft\n", encoding="utf-8")
        agent = CodingAgent(MagicMock(), project_root=str(repo), allow_write=True)
        assert agent._tool_edit_file("other.py\n---OLD---\nx = 1\n---NEW---\nx = 2\n").success
        assert (repo / "work.py").read_text(encoding="utf-8") == "user WIP\n"
        assert (repo / "draft.py").exists()
        assert _git(repo, "stash", "list").strip() == ""

    def test_rollback_restores_pre_agent_state(self, repo):
        (repo / "work.py").write_text("user WIP\n", encoding="utf-8")
        agent = CodingAgent(MagicMock(), project_root=str(repo), allow_write=True)
        agent._tool_edit_file("other.py\n---OLD---\nx = 1\n---NEW---\nx = 2\n")
        agent._tool_write_file("new_by_agent.py\nn = 1\n")
        git_helper.rollback(repo)
        assert (repo / "other.py").read_text(encoding="utf-8") == "x = 1\n"
        assert (repo / "work.py").read_text(encoding="utf-8") == "user WIP\n"
        assert not (repo / "new_by_agent.py").exists()

    def test_snapshot_not_fooled_by_git_stat_cache(self, repo):
        """Same-size edit in the same second as the index write ("racy git").

        The snapshot index is a copy of the real one; if the copy got a fresh
        mtime, git trusted the stale stat cache and recorded OLD content —
        rollback then silently failed (seen on Windows, flaky)."""
        import os
        import time

        f = repo / "other.py"
        past = time.time() - 100
        os.utime(f, (past, past))
        _git(repo, "add", "other.py")
        _git(repo, "commit", "-qm", "touch", "--allow-empty")
        index = repo / ".git" / "index"
        os.utime(index, (past, past))            # entry is "racily clean"
        f.write_text("x = 2\n", encoding="utf-8")  # same size as "x = 1\n"
        os.utime(f, (past, past))                # identical stat info
        tree = git_helper._snapshot_tree(repo)
        assert _git(repo, "show", f"{tree}:other.py") == "x = 2\n"

    def test_commit_only_agent_files_never_secrets(self, repo):
        (repo / "work.py").write_text("user WIP\n", encoding="utf-8")
        (repo / ".env").write_text("SECRET=1\n", encoding="utf-8")
        agent = CodingAgent(MagicMock(), project_root=str(repo), allow_write=True)
        replies_edit = "other.py\n---OLD---\nx = 1\n---NEW---\nx = 2\n"
        res = agent._tool_edit_file(replies_edit)
        agent._note_effect("edit_file", replies_edit, res)
        assert agent._tool_git_commit("agent change").success
        committed = _git(repo, "show", "--name-only", "--format=", "HEAD").split()
        assert committed == ["other.py"]
        status = _git(repo, "status", "--short")
        assert "work.py" in status and ".env" in status and ".fluxion-backup" not in status
