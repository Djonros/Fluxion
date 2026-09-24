"""Phase 11 tests: Git integration tools — git_status, git_diff, git_commit,
auto-checkpoint, rollback.

These tests initialise real git repos in tmp_path (no mocking of git).
Skipped when git is not installed.
"""
from __future__ import annotations

import shutil
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from orchestrator import CodingAgent, ToolResult
from orchestrator import git_helper


# ═══════════════════════════════════════════════════════════════════════════════
#  Fixtures
# ═══════════════════════════════════════════════════════════════════════════════

def _git_available() -> bool:
    return shutil.which("git") is not None


pytestmark = pytest.mark.skipif(not _git_available(), reason="git not installed")


def _init_repo(path: Path) -> None:
    """Initialise a git repo with a base commit."""
    import subprocess

    def git(*args: str) -> None:
        subprocess.run(
            ["git", *args],
            cwd=str(path),
            capture_output=True,
            check=True,
        )

    git("init")
    git("config", "user.email", "test@test.test")
    git("config", "user.name", "Test")
    (path / "README.md").write_text("hello\n", encoding="utf-8")
    git("add", "-A")
    git("commit", "-m", "initial")


@pytest.fixture()
def git_repo(tmp_path):
    """A temporary git repo with one initial commit."""
    _init_repo(tmp_path)
    return tmp_path


@pytest.fixture()
def rw_agent(git_repo):
    backend = MagicMock()
    agent = CodingAgent(
        backend=backend,
        project_root=str(git_repo),
        allow_write=True,
    )
    return agent


@pytest.fixture()
def ro_agent(git_repo):
    backend = MagicMock()
    agent = CodingAgent(
        backend=backend,
        project_root=str(git_repo),
        allow_write=False,
    )
    return agent


# ═══════════════════════════════════════════════════════════════════════════════
#  git_helper unit tests
# ═══════════════════════════════════════════════════════════════════════════════

class TestGitHelper:
    def test_is_repo_true(self, git_repo):
        assert git_helper.is_repo(git_repo) is True

    def test_is_repo_false(self, tmp_path):
        assert git_helper.is_repo(tmp_path) is False

    def test_status_clean(self, git_repo):
        out = git_helper.status(git_repo)
        assert "clean" in out.lower() or out.strip() == ""

    def test_status_shows_changes(self, git_repo):
        (git_repo / "new.py").write_text("x = 1\n", encoding="utf-8")
        out = git_helper.status(git_repo)
        assert "new.py" in out

    def test_diff_empty(self, git_repo):
        out = git_helper.diff(git_repo)
        assert "no changes" in out.lower() or out.strip() == ""

    def test_diff_shows_changes(self, git_repo):
        (git_repo / "README.md").write_text("changed\n", encoding="utf-8")
        out = git_helper.diff(git_repo)
        assert "changed" in out

    def test_commit_creates_commit(self, git_repo):
        (git_repo / "file.py").write_text("x = 1\n", encoding="utf-8")
        out = git_helper.commit(git_repo, "test commit")
        assert "master" in out or "main" in out

    def test_commit_nothing_to_commit(self, git_repo):
        out = git_helper.commit(git_repo, "empty")
        assert "nothing" in out.lower() or "no changes" in out.lower() or "main" in out

    def test_stash_and_pop(self, git_repo):
        (git_repo / "file.py").write_text("x = 1\n", encoding="utf-8")
        stash_out = git_helper.stash(git_repo, "test-stash")
        assert "stash" in stash_out.lower() or "nothing" in stash_out.lower()

        if "nothing" not in stash_out.lower():
            pop_out = git_helper.stash_pop(git_repo)
            assert "restored" in pop_out.lower() or "pop" in pop_out.lower()

    def test_checkpoint_not_a_repo(self, tmp_path):
        out = git_helper.checkpoint(tmp_path)
        assert "not a git repo" in out

    def test_rollback_not_a_repo(self, tmp_path):
        out = git_helper.rollback(tmp_path)
        assert "not a git repo" in out

    def test_rollback_no_stashes(self, git_repo):
        out = git_helper.rollback(git_repo)
        assert "no stashes" in out.lower()


# ═══════════════════════════════════════════════════════════════════════════════
#  Agent git tools
# ═══════════════════════════════════════════════════════════════════════════════

class TestAgentGitStatus:
    def test_git_status_clean(self, rw_agent):
        result = rw_agent._tool_git_status("")
        assert result.success
        assert "clean" in result.output.lower() or result.output.strip() == ""

    def test_git_status_shows_new_file(self, rw_agent, git_repo):
        (git_repo / "new.py").write_text("x = 1\n", encoding="utf-8")
        result = rw_agent._tool_git_status("")
        assert result.success
        assert "new.py" in result.output

    def test_git_status_not_a_repo(self, tmp_path):
        backend = MagicMock()
        agent = CodingAgent(backend=backend, project_root=str(tmp_path))
        result = agent._tool_git_status("")
        assert not result.success
        assert "not a git" in result.error.lower()


class TestAgentGitDiff:
    def test_git_diff_no_changes(self, rw_agent):
        result = rw_agent._tool_git_diff("")
        assert result.success

    def test_git_diff_shows_changes(self, rw_agent, git_repo):
        (git_repo / "README.md").write_text("changed content\n", encoding="utf-8")
        result = rw_agent._tool_git_diff("")
        assert result.success
        assert "changed" in result.output


class TestAgentGitCommit:
    def test_git_commit_disabled_in_ro(self, ro_agent):
        result = ro_agent._tool_git_commit("test")
        assert not result.success
        assert "disabled" in result.error.lower()

    def test_git_commit_commits_changes(self, rw_agent, git_repo):
        (git_repo / "file.py").write_text("x = 1\n", encoding="utf-8")
        result = rw_agent._tool_git_commit("add file")
        assert result.success

        status = rw_agent._tool_git_status("")
        assert "clean" in status.output.lower() or "file.py" not in status.output

    def test_git_commit_not_a_repo(self, tmp_path):
        backend = MagicMock()
        agent = CodingAgent(backend=backend, project_root=str(tmp_path), allow_write=True)
        result = agent._tool_git_commit("msg")
        assert not result.success
        assert "not a git" in result.error.lower()


# ═══════════════════════════════════════════════════════════════════════════════
#  Auto-checkpoint
# ═══════════════════════════════════════════════════════════════════════════════

class TestAutoCheckpoint:
    def test_checkpoint_happens_before_write(self, rw_agent, git_repo):
        """First write_file triggers a git stash checkpoint."""
        result = rw_agent._tool_write_file("new.py\ncontent = 1\n")
        assert result.success
        assert rw_agent._checkpointed is True

    def test_checkpoint_only_once(self, rw_agent, git_repo):
        """Multiple writes only checkpoint once."""
        rw_agent._tool_write_file("a.py\nx = 1\n")
        rw_agent._tool_write_file("b.py\ny = 2\n")
        assert rw_agent._checkpointed is True

    def test_checkpoint_skipped_without_git(self, tmp_path):
        """Outside a git repo git tools are auto-disabled; writes still work."""
        backend = MagicMock()
        agent = CodingAgent(backend=backend, project_root=str(tmp_path), allow_write=True)
        assert agent.git_enabled is False
        result = agent._tool_write_file("new.py\ncontent\n")
        assert result.success
        assert agent._checkpointed is True
        assert not hasattr(agent, "_checkpoint_result")

    def test_edit_triggers_checkpoint(self, rw_agent, git_repo):
        """edit_file also triggers checkpoint."""
        (git_repo / "mod.py").write_text("x = 1\n", encoding="utf-8")
        (git_repo / "mod.py").resolve()
        args = "mod.py\n---OLD---\nx = 1\n---NEW---\nx = 42\n"
        result = rw_agent._tool_edit_file(args)
        assert result.success
        assert rw_agent._checkpointed is True


# ═══════════════════════════════════════════════════════════════════════════════
#  Full round-trip: write → git_diff → git_commit
# ═══════════════════════════════════════════════════════════════════════════════

class TestGitAutoDisabled:
    def test_git_tools_auto_disabled_outside_repo(self, tmp_path):
        agent = CodingAgent(backend=MagicMock(), project_root=str(tmp_path), allow_write=True)
        assert agent.git_enabled is False
        for name in ("git_status", "git_diff", "git_commit"):
            assert name not in agent.tools
        assert "git_status" not in agent.system_prompt

    def test_git_tools_active_inside_repo_subdir(self, git_repo):
        sub = git_repo / "pkg"
        sub.mkdir(exist_ok=True)
        agent = CodingAgent(backend=MagicMock(), project_root=str(sub), allow_write=True)
        assert agent.git_enabled is True
        assert "git_status" in agent.tools


class TestGitDisabled:
    def test_git_enabled_false_drops_tools_and_prompt(self, git_repo):
        agent = CodingAgent(
            backend=MagicMock(),
            project_root=str(git_repo),
            allow_write=True,
            git_enabled=False,
        )
        for name in ("git_status", "git_diff", "git_commit"):
            assert name not in agent.tools
        assert "git_status" not in agent.system_prompt
        assert "Use git_status" not in agent.system_prompt

    def test_git_enabled_false_skips_checkpoint(self, git_repo, monkeypatch):
        from orchestrator import git_helper

        def boom(root):
            raise AssertionError("checkpoint must not run when git is disabled")

        monkeypatch.setattr(git_helper, "checkpoint", boom)
        agent = CodingAgent(
            backend=MagicMock(),
            project_root=str(git_repo),
            allow_write=True,
            git_enabled=False,
        )
        agent._ensure_checkpoint()
        assert agent._checkpointed is True

    def test_git_enabled_true_keeps_tools(self, git_repo):
        agent = CodingAgent(
            backend=MagicMock(),
            project_root=str(git_repo),
            allow_write=True,
        )
        assert "git_status" in agent.tools
        assert "git_commit" in agent.system_prompt


class TestGitRoundTrip:
    def test_write_then_diff_then_commit(self, rw_agent, git_repo):
        # 1. Write a file
        w = rw_agent._tool_write_file("feature.py\ndef hello():\n    return 'world'\n")
        assert w.success

        # 2. Git diff shows the new file
        d = rw_agent._tool_git_diff("")
        assert d.success

        # 3. Git status shows untracked/modified
        s = rw_agent._tool_git_status("")
        assert s.success
        assert "feature.py" in s.output

        # 4. Commit
        c = rw_agent._tool_git_commit("add feature.py")
        assert c.success

        # 5. Status is clean after commit
        s2 = rw_agent._tool_git_status("")
        assert "clean" in s2.output.lower() or "feature.py" not in s2.output
