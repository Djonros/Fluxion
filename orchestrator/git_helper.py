"""Git helper: thin subprocess wrapper for agent tools.

All commands run inside *project_root*.  Paths are never absolute —
we rely on git's own working-directory behaviour.
"""
from __future__ import annotations

import logging
import subprocess
from pathlib import Path

logger = logging.getLogger(__name__)

_GIT_TIMEOUT = 15


def _run_git(project_root: Path, args: list[str]) -> tuple[int, str]:
    """Run ``git`` inside *project_root*; return (returncode, combined output)."""
    cmd = ["git"] + args
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=_GIT_TIMEOUT,
            cwd=str(project_root),
        )
        output = result.stdout
        if result.stderr:
            output = (output + "\n" + result.stderr).strip() if output else result.stderr.strip()
        return result.returncode, output
    except FileNotFoundError:
        return -1, "git executable not found"
    except subprocess.TimeoutExpired:
        return -1, f"git timed out after {_GIT_TIMEOUT}s"


def is_repo(project_root: Path) -> bool:
    """True when *project_root* is inside a git repository."""
    code, _ = _run_git(project_root, ["rev-parse", "--is-inside-work-tree"])
    return code == 0


def status(project_root: Path) -> str:
    """Return ``git status --short`` output."""
    _, out = _run_git(project_root, ["status", "--short"])
    return out or "(clean)"


def diff(project_root: Path, staged: bool = False) -> str:
    """Return ``git diff`` output."""
    args = ["diff"]
    if staged:
        args.append("--cached")
    _, out = _run_git(project_root, args)
    return out or "(no changes)"


def commit(project_root: Path, message: str) -> str:
    """Stage all tracked changes and commit. Returns git output."""
    if not message.strip():
        message = "Fluxion auto-commit"
    _run_git(project_root, ["add", "-A"])
    code, out = _run_git(project_root, ["commit", "-m", message])
    if code != 0 and "nothing to commit" not in out and "no changes" not in out:
        return f"commit failed: {out}"
    lines = [ln.strip() for ln in out.strip().splitlines() if ln.strip()]
    for line in lines:
        if "nothing to commit" in line or "no changes" in line:
            return line
    return lines[0] if lines else "committed"


def stash(project_root: Path, message: str = "fluxion-checkpoint") -> str:
    """Create a stash checkpoint. Returns stash confirmation."""
    code, out = _run_git(project_root, ["stash", "push", "-u", "-m", message])
    if code != 0:
        return f"stash failed: {out}"
    if "No local changes" in out or "Cannot stash" in out:
        return "(nothing to stash)"
    return out.strip().split("\n")[0] if out else "stashed"


def stash_pop(project_root: Path) -> str:
    """Pop the most recent stash."""
    code, out = _run_git(project_root, ["stash", "pop"])
    if code != 0:
        return f"stash pop failed: {out}"
    lines = [ln.strip() for ln in out.strip().splitlines() if ln.strip()]
    for line in lines:
        if "Dropped" in line or "dropped" in line:
            return f"restored: {line}"
    return lines[0] if lines else "restored"


def stash_list(project_root: Path) -> list[str]:
    """Return list of stash entries."""
    _, out = _run_git(project_root, ["stash", "list"])
    if not out or out == "(clean)":
        return []
    return [line.strip() for line in out.strip().splitlines() if line.strip()]


def checkpoint(project_root: Path) -> str:
    """Create an auto-checkpoint stash before agent writes files.

    Returns a human-readable status.  When there is nothing to stash
    the string starts with ``(nothing`` so callers can detect no-ops.
    """
    if not is_repo(project_root):
        return "(not a git repo)"
    return stash(project_root, "fluxion-checkpoint")


def rollback(project_root: Path) -> str:
    """Pop the most recent fluxion-checkpoint stash (undo last write session)."""
    if not is_repo(project_root):
        return "(not a git repo)"
    entries = stash_list(project_root)
    if not entries:
        return "(no stashes to restore)"
    return stash_pop(project_root)
