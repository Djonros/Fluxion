"""Git helper: thin subprocess wrapper for agent tools.

All commands run inside *project_root*.  Paths are never absolute —
we rely on git's own working-directory behaviour.
"""
from __future__ import annotations

import fnmatch
import logging
import os
import subprocess
import tempfile
import time
from pathlib import Path

from core.proc import no_window

logger = logging.getLogger(__name__)

_GIT_TIMEOUT = 60


def _run_git(
    project_root: Path, args: list[str], env: dict[str, str] | None = None
) -> tuple[int, str]:
    """Run ``git`` inside *project_root*; return (returncode, combined output)."""
    cmd = ["git"] + args
    full_env = None
    if env:
        full_env = dict(os.environ)
        full_env.update(env)
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=_GIT_TIMEOUT,
            cwd=str(project_root),
            env=full_env,
            encoding="utf-8",
            errors="replace",
            **no_window(),
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


# Files that must never be committed automatically, even if the agent wrote them.
_SECRET_PATTERNS = (".env", ".env.*", "*.pem", "*.key", "*.p12", "*.pfx", "id_rsa*", "id_ed25519*")


_SAFE_SUFFIXES = (".example", ".sample", ".template", ".dist")


def is_secret_path(path: str) -> bool:
    """True for files that typically hold credentials (.env, keys, certs)."""
    name = Path(path).name
    if name.lower().endswith(_SAFE_SUFFIXES):
        return False
    return any(fnmatch.fnmatch(name, pat) for pat in _SECRET_PATTERNS)


def changed_paths(project_root: Path) -> list[str]:
    """Uncommitted paths (modified, deleted, untracked) under *project_root*,
    relative to *project_root* (``git ls-files`` is cwd-relative, unlike
    ``git status --porcelain``)."""
    code, out = _run_git(
        project_root, ["ls-files", "-z", "-m", "-d", "-o", "--exclude-standard"]
    )
    if code != 0:
        return []
    seen: dict[str, None] = {}
    for path in out.split("\0"):
        if path:
            seen.setdefault(path, None)
    return list(seen)


def commit(project_root: Path, message: str, paths: list[str] | None = None) -> str:
    """Commit changes. Returns git output.

    With *paths*, only those files are staged and committed (the agent passes
    the files it wrote itself).  Without *paths* every change is staged
    (``git add -A``) — kept for explicit, user-initiated commits only.
    Secret-looking files (.env, *.pem, ...) are never staged.
    """
    if not message.strip():
        message = "Fluxion auto-commit"
    if paths is not None:
        safe = [p for p in paths if p and not is_secret_path(p)]
        if not safe:
            return "nothing to commit (no eligible files)"
        _run_git(project_root, ["add", "-A", "--", *safe])
        code, out = _run_git(project_root, ["commit", "-m", message, "--", *safe])
    else:
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


_CHECKPOINT_REF_PREFIX = "refs/fluxion/checkpoints/"


def _snapshot_tree(project_root: Path) -> str | None:
    """Write the full working tree (tracked + untracked, minus ignored) to a
    tree object using a throw-away index.  Neither the working tree nor the
    user's real index is touched."""
    fd, tmp_index = tempfile.mkstemp(prefix="fluxion-index-")
    os.close(fd)
    os.unlink(tmp_index)  # git wants to create it itself
    # Seed from the real index so git can reuse its stat cache (fast on big repos).
    code, real_index = _run_git(project_root, ["rev-parse", "--git-path", "index"])
    if code == 0:
        src = Path(real_index.strip())
        if not src.is_absolute():
            src = project_root / src
        if src.is_file():
            import shutil
            # copy2, not copyfile: the copy must keep the index's mtime.  Git
            # re-hashes "racily clean" entries (file mtime >= index mtime);
            # a fresh mtime on the copy disables that check, and a file edited
            # in the same second as the index (same size) is then taken from
            # the stale stat cache — the snapshot silently records old content.
            shutil.copy2(src, tmp_index)
    env = {"GIT_INDEX_FILE": tmp_index}
    try:
        code, out = _run_git(project_root, ["add", "-A", "."], env=env)
        if code != 0:
            logger.warning("snapshot: git add failed: %s", out)
            return None
        code, tree = _run_git(project_root, ["write-tree"], env=env)
        if code != 0:
            logger.warning("snapshot: write-tree failed: %s", tree)
            return None
        return tree.strip()
    finally:
        try:
            os.unlink(tmp_index)
        except OSError:
            pass


def checkpoint(project_root: Path) -> str:
    """Snapshot the working tree before the agent writes files.

    Unlike the previous ``git stash push -u`` implementation this does NOT
    remove the user's uncommitted work from disk.  The snapshot is stored as a
    commit under ``refs/fluxion/checkpoints/`` and can be restored with
    :func:`rollback`.

    Returns a human-readable status; failures start with ``checkpoint failed``.
    """
    if not is_repo(project_root):
        return "(not a git repo)"
    tree = _snapshot_tree(project_root)
    if not tree:
        return "checkpoint failed: could not snapshot working tree"
    args = ["commit-tree", tree, "-m", "fluxion-checkpoint"]
    code, head = _run_git(project_root, ["rev-parse", "--verify", "-q", "HEAD"])
    if code == 0 and head.strip():
        args[2:2] = ["-p", head.strip()]
    env = {
        "GIT_AUTHOR_NAME": "Fluxion", "GIT_AUTHOR_EMAIL": "fluxion@localhost",
        "GIT_COMMITTER_NAME": "Fluxion", "GIT_COMMITTER_EMAIL": "fluxion@localhost",
    }
    code, sha = _run_git(project_root, args, env=env)
    if code != 0:
        return f"checkpoint failed: {sha}"
    sha = sha.strip()
    ref = f"{_CHECKPOINT_REF_PREFIX}{int(time.time() * 1000)}"
    code, out = _run_git(project_root, ["update-ref", ref, sha])
    if code != 0:
        return f"checkpoint failed: {out}"
    return f"checkpoint {sha[:10]} ({ref})"


def list_checkpoints(project_root: Path) -> list[str]:
    """Checkpoint refs, newest first."""
    code, out = _run_git(
        project_root,
        ["for-each-ref", "--sort=-refname", "--format=%(refname)", _CHECKPOINT_REF_PREFIX],
    )
    if code != 0:
        return []
    return [ln.strip() for ln in out.splitlines() if ln.strip()]


def _restore_snapshot(project_root: Path, ref: str) -> str:
    """Make the working tree match the snapshot *ref* (index untouched)."""
    current = _snapshot_tree(project_root)
    if not current:
        return "rollback failed: could not snapshot current state"
    code, out = _run_git(
        project_root, ["diff", "--name-status", "--no-renames", "-z", f"{ref}^{{tree}}", current]
    )
    if code != 0:
        return f"rollback failed: {out}"
    parts = [p for p in out.split("\0") if p]
    added: list[str] = []
    restore: list[str] = []
    for status_, path in zip(parts[0::2], parts[1::2]):
        if status_.startswith("A"):
            added.append(path)          # created after the checkpoint → remove
        else:
            restore.append(path)        # modified / deleted → bring back
    for path in added:
        try:
            (project_root / path).unlink()
        except OSError as exc:
            logger.warning("rollback: cannot remove %s: %s", path, exc)
    if restore:
        code, out = _run_git(
            project_root, ["restore", f"--source={ref}", "--worktree", "--", *restore]
        )
        if code != 0:
            return f"rollback failed: {out}"
    return f"restored {len(restore)} file(s), removed {len(added)} new file(s)"


def rollback(project_root: Path) -> str:
    """Undo the last agent write session by restoring its checkpoint.

    Falls back to legacy ``fluxion-checkpoint`` stashes created by older
    versions (which removed user work from disk — this brings it back).
    """
    if not is_repo(project_root):
        return "(not a git repo)"
    refs = list_checkpoints(project_root)
    if refs:
        ref = refs[0]
        result = _restore_snapshot(project_root, ref)
        if not result.startswith("rollback failed"):
            _run_git(project_root, ["update-ref", "-d", ref])
        return result
    for idx, entry in enumerate(stash_list(project_root)):
        if "fluxion-checkpoint" in entry:
            code, out = _run_git(project_root, ["stash", "pop", f"stash@{{{idx}}}"])
            if code != 0:
                return f"stash pop failed: {out}"
            return f"restored legacy checkpoint: {entry}"
    return "(no stashes or checkpoints to restore)"
