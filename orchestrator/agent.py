"""ReAct CodingAgent: multi-step tool-use loop.

Tools available:
  - read_file(path)       → file contents (truncated)
  - grep(pattern, path)   → matching lines with file:line
  - run_tests(args)       → pytest output (truncated)
  - web_search(query)     → web context snippets

Loop: Thought → Action → Observation → ... → Final Answer
"""
from __future__ import annotations

import logging
import re
import shutil
import subprocess
import sys
from collections.abc import Callable, Generator
from dataclasses import dataclass, field
from pathlib import Path

from core.inference import ModelBackend
from core.language import LANG_NAMES, matches_language, resolve_target

logger = logging.getLogger(__name__)

_MAX_ITERATIONS = 8
_TOOL_OUTPUT_LIMIT = 3000
_MAX_WRITE_FILE_SIZE = 1_048_576  # 1 MB

_BLOCKED_WRITE_DIRS = frozenset({
    ".git",
    "__pycache__",
    ".venv",
    "venv",
    "node_modules",
    ".fluxion-backup",
    "__chroma_db__",
})

_DELIM_OLD = "---OLD---"
_DELIM_NEW = "---NEW---"

_GIT_LINE_RE = re.compile(r"^.*git_(?:status|diff|commit).*\n?", re.MULTILINE)

_NOT_A_REPO = (
    "Not a git repository. Git tools are unavailable here — "
    "do not call them again; continue with other tools or finish."
)

_REPEAT_OBSERVATION = (
    "This exact action was already executed with identical arguments and "
    "returned the same result. Do not repeat it: choose a different tool "
    "or finish with your best answer."
)

_VERIFY_BUDGET = 2

_NO_TESTS_MARKERS = (
    "no tests ran",
    "collected 0 items",
    "not found: tests",
    "no module named pytest",
)

REACT_SYSTEM_RO = """\
You are Fluxion Agent, an autonomous coding assistant with tool access.

You solve problems step by step using the ReAct pattern:

Thought: <your reasoning about what to do next>
Action: <tool_name> <arguments>

Available tools:
  read_file <path>          - Read a file from disk (first 3000 chars)
  grep <pattern> [<path>]   - Search file contents (regex); path defaults to '.'
  run_tests [<args>]        - Run pytest with optional args
  web_search <query>        - Search the web for information
  git_status                - Show git status (modified/untracked files)
  git_diff                  - Show unstaged changes
  finish <answer>           - Provide final answer and stop

After each Action, you will see an Observation with the tool result.
Continue until you have enough information, then use 'finish'.

Rules:
- One Action per turn.
- Be concise in Thought.
- Use tools proactively to explore the codebase.
- If the user's message is a greeting, small talk, or a question you can
  answer from general knowledge, answer directly with 'finish' and NO tools.
"""

REACT_SYSTEM_RW = """\
You are Fluxion Agent, an autonomous coding assistant with tool access.

You solve problems step by step using the ReAct pattern:

Thought: <your reasoning about what to do next>
Action: <tool_name> <arguments>

Available tools:
  read_file <path>          - Read a file from disk (first 3000 chars)
  write_file <path>         - Create or overwrite a file (path on first line,
                              content on all following lines)
  edit_file <path>          - Edit part of a file using ---OLD--- / ---NEW---
                              sentinels (see below)
  grep <pattern> [<path>]   - Search file contents (regex); path defaults to '.'
  run_tests [<args>]        - Run pytest with optional args
  web_search <query>        - Search the web for information
  git_status                - Show git status (modified/untracked files)
  git_diff                  - Show unstaged changes
  git_commit <message>      - Stage all changes and commit with message
  finish <answer>           - Provide final answer and stop

edit_file format:
  Action: edit_file <path>
  ---OLD---
  <exact text to find>
  ---NEW---
  <replacement text>

Rules:
- One Action per turn.
- Be concise in Thought.
- Use tools proactively to explore and modify the codebase.
- If the user's message is a greeting, small talk, or a question you can
  answer from general knowledge, answer directly with 'finish' and NO tools.
- Prefer edit_file over write_file for existing files.
- After write_file or edit_file, run run_tests before finish. If you forget,
  the harness auto-runs tests and returns the result as an Observation.
- The ---OLD--- text must match exactly and appear only once in the file.
- Use git_status and git_diff to review your changes before committing.
"""


@dataclass
class ToolResult:
    tool: str
    args: str
    output: str
    success: bool = True
    error: str = ""


@dataclass
class AgentStep:
    iteration: int
    thought: str = ""
    action: str = ""
    tool_name: str = ""
    tool_args: str = ""
    observation: str = ""
    is_final: bool = False
    final_answer: str = ""


@dataclass
class AgentResult:
    steps: list[AgentStep] = field(default_factory=list)
    final_answer: str = ""
    iterations_used: int = 0
    success: bool = False
    verification: str = ""  # "not_required" | "passed" | "failed"


class CodingAgent:
    """ReAct-style agent that iteratively uses tools to solve problems."""

    def __init__(
        self,
        backend: ModelBackend,
        project_root: str = ".",
        rag_service=None,
        web_search=None,
        max_iterations: int = _MAX_ITERATIONS,
        allow_write: bool = False,
        git_enabled: bool = True,
        verify_after_write: bool = True,
        verify_budget: int = _VERIFY_BUDGET,
        lang: str = "auto",
    ):
        self.backend = backend
        self.project_root = Path(project_root).resolve()
        self.rag_service = rag_service
        self.web_search = web_search
        self.max_iterations = max_iterations
        self.allow_write = allow_write
        if git_enabled:
            from .git_helper import is_repo

            git_enabled = is_repo(self.project_root)
        self.git_enabled = git_enabled
        self.verify_after_write = verify_after_write
        self.verify_budget = verify_budget
        self.lang = (lang or "auto").strip().lower()
        self._lang_target: str | None = None
        self._lang_fixes = 0
        self._dirty_files: set[str] = set()
        self._ever_dirty = False
        self._tests_ok = False
        self._verify_used = 0
        self._checkpointed = False
        self.tools: dict[str, Callable[[str], ToolResult]] = {
            "read_file": self._tool_read_file,
            "grep": self._tool_grep,
            "run_tests": self._tool_run_tests,
            "web_search": self._tool_web_search,
            "write_file": self._tool_write_file,
            "edit_file": self._tool_edit_file,
            "git_status": self._tool_git_status,
            "git_diff": self._tool_git_diff,
            "git_commit": self._tool_git_commit,
            "finish": self._tool_finish,
        }
        if not git_enabled:
            for name in ("git_status", "git_diff", "git_commit"):
                self.tools.pop(name, None)
        self.last_result: AgentResult | None = None

    @property
    def system_prompt(self) -> str:
        prompt = REACT_SYSTEM_RW if self.allow_write else REACT_SYSTEM_RO
        if not self.git_enabled:
            prompt = _GIT_LINE_RE.sub("", prompt)
        return prompt

    def run(self, task: str, context: str = "") -> AgentResult:
        """Execute the ReAct loop for *task*. Returns AgentResult."""
        gen = self.run_iter(task, context)
        result = None
        while True:
            try:
                next(gen)
            except StopIteration as stop:
                result = stop.value
                break
        return result or AgentResult()

    def run_iter(self, task: str, context: str = "") -> Generator[AgentStep, None, AgentResult]:
        """Generator variant of :meth:`run` that yields each step as it completes.

        Useful for CLI / UI consumers that want to show progress incrementally.
        Returns the final :class:`AgentResult` (also stored in ``self.last_result``).
        """
        result = AgentResult()
        seen_actions: set[tuple[str, str]] = set()
        no_action_streak = 0
        messages: list[dict[str, str]] = [
            {"role": "system", "content": self.system_prompt},
        ]
        if context:
            messages.append({"role": "user", "content": f"Context:\n{context}\n\nTask:\n{task}"})
        else:
            messages.append({"role": "user", "content": task})

        self._lang_target = resolve_target(self.lang, task)
        self._lang_fixes = 0

        extra = self.verify_budget if self.verify_after_write else 0
        for iteration in range(1, self.max_iterations + extra + 1):
            step = AgentStep(iteration=iteration)

            try:
                raw_response = self.backend.generate(messages)
            except Exception as exc:
                step.observation = f"Backend error: {exc}"
                result.steps.append(step)
                yield step
                break

            step.thought, step.action = self._parse_response(raw_response)

            if not step.action:
                messages.append({"role": "assistant", "content": raw_response})
                no_action_streak += 1
                if no_action_streak >= 2 and raw_response.strip():
                    step.is_final = True
                    step.final_answer = raw_response.strip()
                    step.observation = "Task complete."
                    result.final_answer = step.final_answer
                    result.success = True
                    result.steps.append(step)
                    yield step
                    break
                messages.append({"role": "user", "content": "Please use a tool with 'Action: <tool> <args>' or 'Action: finish <answer>'."})
                result.steps.append(step)
                yield step
                continue

            no_action_streak = 0

            tool_name, tool_args = self._parse_action(step.action)
            step.tool_name = tool_name
            step.tool_args = tool_args

            messages.append({"role": "assistant", "content": raw_response})

            if tool_name == "finish":
                gate_note = self._try_verify_gate()
                if gate_note is not None:
                    step.tool_name = "run_tests"
                    step.tool_args = "(auto-verify)"
                    step.observation = gate_note
                    messages.append({"role": "user", "content": f"Observation:\n{gate_note}"})
                    no_action_streak = 0
                    result.steps.append(step)
                    yield step
                    continue
                if (
                    self._lang_target
                    and self._lang_fixes < 1
                    and not matches_language(tool_args, self._lang_target)
                ):
                    self._lang_fixes += 1
                    note = (
                        "[language gate] Ответ получен на другом языке. Перепиши финальный "
                        f"ответ строго на {LANG_NAMES[self._lang_target]}, сохранив смысл и код."
                    )
                    step.tool_name = ""
                    step.observation = note
                    messages.append({"role": "user", "content": note})
                    result.steps.append(step)
                    yield step
                    continue
                step.is_final = True
                step.final_answer = tool_args
                step.observation = "Task complete."
                result.final_answer = tool_args
                result.success = True
                result.steps.append(step)
                yield step
                break

            action_key = (tool_name, tool_args)
            if action_key in seen_actions:
                step.observation = _REPEAT_OBSERVATION
            else:
                seen_actions.add(action_key)
                tool_result = self._dispatch_tool(tool_name, tool_args)
                self._note_effect(tool_name, tool_args, tool_result)
                step.observation = tool_result.output if tool_result.success else f"Error: {tool_result.error}"

            messages.append({
                "role": "user",
                "content": f"Observation:\n{step.observation}",
            })

            result.steps.append(step)
            result.iterations_used = iteration
            yield step

        result.iterations_used = len(result.steps)
        if not result.success and result.steps:
            last = result.steps[-1]
            result.final_answer = last.observation or "Agent ran out of iterations."
        if not self.verify_after_write or not self._ever_dirty:
            result.verification = "not_required"
        elif self._tests_ok:
            result.verification = "passed"
        else:
            result.verification = "failed"

        self.last_result = result
        return result

    # ── verification gate ────────────────────────────────────────────

    def _note_effect(self, tool_name: str, tool_args: str, result: ToolResult) -> None:
        if not result.success:
            return
        if tool_name in ("write_file", "edit_file"):
            path = tool_args.lstrip("\n").split("\n", 1)[0].strip()
            self._dirty_files.add(path)
            self._ever_dirty = True
            self._tests_ok = False
        elif tool_name == "run_tests" and self._dirty_files:
            self._tests_ok = True
            self._dirty_files.clear()

    def _try_verify_gate(self) -> str | None:
        """Финал с непроверенными правками невозможен: авто-прогон тестов."""
        if not self.verify_after_write or not self._dirty_files or self._tests_ok:
            return None
        if self._verify_used >= self.verify_budget:
            return None
        self._verify_used += 1
        passed, note = self._auto_verify()
        if passed:
            self._tests_ok = True
            self._dirty_files.clear()
        return note

    def _auto_verify(self) -> tuple[bool, str]:
        files = ", ".join(sorted(self._dirty_files))
        result = self._dispatch_tool("run_tests", "")
        tail = (result.output or result.error)[-1200:]
        if result.success:
            return True, (
                f"[verification gate] Изменены: {files}. Авто-прогон run_tests: PASSED. "
                f"Можно завершать.\n{tail}"
            )
        low = result.output.lower()
        if any(marker in low for marker in _NO_TESTS_MARKERS):
            errors = self._syntax_errors()
            if not errors:
                return True, (
                    f"[verification gate] Изменены: {files}. pytest-тестов в проекте нет; "
                    "syntax check: PASSED. Можно завершать."
                )
            return False, (
                f"[verification gate] Изменены: {files}. Тестов нет, syntax check FAILED:\n"
                + "\n".join(errors) + "\nИсправь код перед завершением."
            )
        return False, (
            f"[verification gate] Изменены: {files}. Авто-прогон run_tests: FAILED:\n{tail}\n"
            "Исправь код и запусти run_tests перед завершением."
        )

    def _syntax_errors(self) -> list[str]:
        errors: list[str] = []
        for raw in sorted(self._dirty_files):
            fpath = self._validate_write_path(raw)
            if fpath is None or fpath.suffix != ".py" or not fpath.exists():
                continue
            try:
                compile(fpath.read_text(encoding="utf-8", errors="replace"), str(fpath), "exec")
            except SyntaxError as exc:
                errors.append(f"{raw}:{exc.lineno}: {exc.msg}")
        return errors

    @staticmethod
    def _syntax_report(fpath: Path) -> str:
        if fpath.suffix != ".py":
            return ""
        try:
            compile(fpath.read_text(encoding="utf-8", errors="replace"), str(fpath), "exec")
        except SyntaxError as exc:
            return f"\nSYNTAX ERROR: line {exc.lineno}: {exc.msg}"
        return "\nsyntax: OK"

    # ── response parsing ──────────────────────────────────────────────────

    @staticmethod
    def _parse_response(response: str) -> tuple[str, str]:
        """Extract Thought and Action from the LLM response.

        Robustly handles common LLM deviations:
        - ``Finish: <answer>``  → normalised to ``finish <answer>``
        - ``Action: <tool>``    → used as-is
        - ``write_file`` / ``edit_file`` → captures the full multiline body
          (path + file content) that follows the Action line.
        """
        thought = ""
        action = ""

        thought_match = re.search(r"Thought:\s*(.*?)(?=\n(?:Action|Finish)\s*:|$)", response, re.DOTALL)
        if thought_match:
            thought = thought_match.group(1).strip()

        multi_match = re.search(
            r"Action:\s*(write_file|edit_file)\s+(.*)",
            response,
            re.DOTALL,
        )
        if multi_match:
            action = f"{multi_match.group(1)} {multi_match.group(2).strip()}"
            return thought, action

        action_match = re.search(r"Action:\s*(.*)", response)
        if action_match:
            action = action_match.group(1).strip()
        else:
            finish_match = re.search(r"Finish:\s*(.*)", response, re.IGNORECASE)
            if finish_match:
                answer = finish_match.group(1).strip()
                action = f"finish {answer}" if answer else "finish"

        return thought, action

    @staticmethod
    def _parse_action(action: str) -> tuple[str, str]:
        """Split 'tool_name arguments' → (tool_name, arguments)."""
        action = action.strip()
        parts = action.split(None, 1)
        tool_name = parts[0] if parts else ""
        args = parts[1] if len(parts) > 1 else ""
        return tool_name, args

    # ── tool dispatch ─────────────────────────────────────────────────────

    def _dispatch_tool(self, name: str, args: str) -> ToolResult:
        handler = self.tools.get(name)
        if handler is None:
            return ToolResult(tool=name, args=args, output="", success=False, error=f"Unknown tool: {name}")
        try:
            return handler(args)
        except Exception as exc:
            return ToolResult(tool=name, args=args, output="", success=False, error=str(exc))

    # ── individual tools ──────────────────────────────────────────────────

    def _tool_read_file(self, args: str) -> ToolResult:
        path = args.strip().strip('"').strip("'")
        if not path:
            return ToolResult("read_file", args, "", False, "No path provided")

        full_path = self.project_root / path if not Path(path).is_absolute() else Path(path)
        if not full_path.exists():
            return ToolResult("read_file", args, "", False, f"File not found: {path}")

        try:
            content = full_path.read_text(encoding="utf-8", errors="replace")
        except Exception as exc:
            return ToolResult("read_file", args, "", False, str(exc))

        if len(content) > _TOOL_OUTPUT_LIMIT:
            content = content[:_TOOL_OUTPUT_LIMIT] + f"\n... ({len(content)} chars total, truncated)"

        return ToolResult("read_file", args, content)

    def _tool_grep(self, args: str) -> ToolResult:
        args = args.strip()
        if not args:
            return ToolResult("grep", args, "", False, "Usage: grep <pattern> [<path>]")

        parts = args.rsplit(None, 1)
        search_path = "."
        pattern = args

        if len(parts) == 2:
            candidate_path = parts[1].strip('"').strip("'")
            candidate_full = (
                self.project_root / candidate_path
                if not Path(candidate_path).is_absolute()
                else Path(candidate_path)
            )
            if candidate_full.exists():
                pattern = parts[0]
                search_path = candidate_path

        full_path = (
            self.project_root / search_path
            if not Path(search_path).is_absolute()
            else Path(search_path)
        )

        if not full_path.exists():
            return ToolResult("grep", args, "", False, f"Path not found: {search_path}")

        try:
            regex = re.compile(pattern)
        except re.error as exc:
            return ToolResult("grep", args, "", False, f"Invalid regex: {exc}")

        matches: list[str] = []
        files_to_search = list(full_path.rglob("*.py")) if full_path.is_dir() else [full_path]

        for fpath in files_to_search:
            if not fpath.is_file():
                continue
            try:
                lines = fpath.read_text(encoding="utf-8", errors="replace").splitlines()
            except Exception:
                continue
            for lineno, line in enumerate(lines, 1):
                if regex.search(line):
                    rel = fpath.relative_to(self.project_root) if self.project_root in fpath.parents or fpath == self.project_root else fpath
                    matches.append(f"{rel}:{lineno}: {line.strip()}")
                    if len(matches) >= 50:
                        matches.append("... (50 matches, truncated)")
                        break
            if len(matches) >= 50:
                break

        if not matches:
            return ToolResult("grep", args, "No matches found.")

        output = "\n".join(matches)
        if len(output) > _TOOL_OUTPUT_LIMIT:
            output = output[:_TOOL_OUTPUT_LIMIT] + "\n... (truncated)"

        return ToolResult("grep", args, output)

    @staticmethod
    def _python_executable() -> str | None:
        """Interpreter for subprocesses; frozen sys.executable would relaunch the app."""
        if not getattr(sys, "frozen", False):
            return sys.executable
        exe_dir = Path(sys.executable).resolve().parent
        bundled = exe_dir / "data" / "training_env" / "Scripts" / "python.exe"
        if bundled.is_file():
            return str(bundled)
        return shutil.which("python") or shutil.which("py")

    def _tool_run_tests(self, args: str) -> ToolResult:
        test_args = args.strip()
        exe = self._python_executable()
        if exe is None:
            return ToolResult("run_tests", args, "", False, "Python interpreter not found")
        cmd = [exe, "-m", "pytest"]
        if test_args:
            cmd.extend(test_args.split())
        cmd.extend(["-v", "--tb=short", "-q"])

        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=60,
                cwd=str(self.project_root),
            )
        except subprocess.TimeoutExpired:
            return ToolResult("run_tests", args, "", False, "Tests timed out (60s)")
        except FileNotFoundError:
            return ToolResult("run_tests", args, "", False, "pytest not available")

        output = result.stdout + "\n" + result.stderr
        if len(output) > _TOOL_OUTPUT_LIMIT:
            output = output[:_TOOL_OUTPUT_LIMIT] + "\n... (truncated)"

        return ToolResult("run_tests", args, output, success=result.returncode == 0)

    def _tool_web_search(self, args: str) -> ToolResult:
        query = args.strip()
        if not query:
            return ToolResult("web_search", args, "", False, "No query provided")

        if self.web_search is None:
            return ToolResult("web_search", args, "", False, "Web search not configured")

        client = getattr(self.web_search, "client", None)
        if client is not None and hasattr(client, "is_alive") and not client.is_alive():
            base_url = getattr(client, "base_url", "SearXNG")
            return ToolResult(
                "web_search",
                args,
                "",
                False,
                f"SearXNG is not reachable at {base_url}. "
                "Start Docker Desktop (the container starts automatically) "
                "or use the Status dashboard to fix it.",
            )

        try:
            contexts = self.web_search.run(query)
        except Exception as exc:
            return ToolResult("web_search", args, "", False, str(exc))

        if not contexts:
            return ToolResult("web_search", args, "No results found.")

        from web.pipeline import WebSearch as WS
        output = WS.build_context(contexts, max_chars=_TOOL_OUTPUT_LIMIT)
        return ToolResult("web_search", args, output)

    # ── write / edit tools ─────────────────────────────────────────────────

    def _validate_write_path(self, raw_path: str) -> Path | None:
        """Resolve *raw_path* relative to project root; return None if unsafe."""
        clean = raw_path.strip().strip('"').strip("'")
        if not clean:
            return None

        candidate = (self.project_root / clean).resolve() if not Path(clean).is_absolute() else Path(clean).resolve()

        try:
            candidate.relative_to(self.project_root)
        except ValueError:
            return None

        for part in candidate.parts:
            if part in _BLOCKED_WRITE_DIRS:
                return None

        return candidate

    @staticmethod
    def _backup_file(fpath: Path) -> None:
        """Copy *fpath* to ``.fluxion-backup/`` if it exists."""
        if not fpath.exists() or not fpath.is_file():
            return
        import shutil

        backup_dir = fpath.parent / ".fluxion-backup"
        backup_dir.mkdir(exist_ok=True)
        backup_path = backup_dir / fpath.name
        shutil.copy2(fpath, backup_path)

    def _tool_write_file(self, args: str) -> ToolResult:
        """Create or overwrite a file.

        Expected *args* format (from the LLM)::

            <path>\n<full file content>

        The first line is the path; everything after the first ``\\n`` is the
        content.  When the file already exists, a backup is saved.
        """
        if not self.allow_write:
            return ToolResult(
                "write_file", args, "", False,
                "Write operations disabled. Start agent with --write or allow_write=True.",
            )

        stripped = args.lstrip("\n")
        newline_idx = stripped.find("\n")
        if newline_idx == -1:
            return ToolResult("write_file", args, "", False, "Usage: write_file <path>\\n<content>")

        raw_path = stripped[:newline_idx]
        content = stripped[newline_idx + 1:]

        fpath = self._validate_write_path(raw_path)
        if fpath is None:
            return ToolResult("write_file", args, "", False, f"Unsafe or invalid path: {raw_path}")

        if len(content.encode("utf-8")) > _MAX_WRITE_FILE_SIZE:
            return ToolResult("write_file", args, "", False, f"Content exceeds {_MAX_WRITE_FILE_SIZE} bytes")

        try:
            self._ensure_checkpoint()
            self._backup_file(fpath)
            fpath.parent.mkdir(parents=True, exist_ok=True)
            fpath.write_text(content, encoding="utf-8")
        except Exception as exc:
            return ToolResult("write_file", args, "", False, str(exc))

        line_count = content.count("\n") + (1 if content and not content.endswith("\n") else 0)
        return ToolResult(
            "write_file", args,
            f"Wrote {fpath.relative_to(self.project_root)} ({line_count} lines, {len(content)} bytes)."
            + self._syntax_report(fpath),
        )

    def _tool_edit_file(self, args: str) -> ToolResult:
        """Replace a unique snippet in an existing file.

        Expected *args* format::

            <path>
            ---OLD---
            <exact text to find>
            ---NEW---
            <replacement text>
        """
        if not self.allow_write:
            return ToolResult(
                "edit_file", args, "", False,
                "Write operations disabled. Start agent with --write or allow_write=True.",
            )

        if _DELIM_OLD not in args or _DELIM_NEW not in args:
            return ToolResult(
                "edit_file", args, "", False,
                f"Usage: edit_file <path>\\n{_DELIM_OLD}\\n<old>\\n{_DELIM_NEW}\\n<new>",
            )

        pre_old, rest = args.split(_DELIM_OLD, 1)
        old_text, new_text = rest.split(_DELIM_NEW, 1)

        raw_path = pre_old.strip()
        old_text = old_text.strip("\n")
        new_text = new_text.strip("\n")

        if old_text == new_text:
            return ToolResult("edit_file", args, "", False, "old_string and new_string are identical")

        fpath = self._validate_write_path(raw_path)
        if fpath is None:
            return ToolResult("edit_file", args, "", False, f"Unsafe or invalid path: {raw_path}")

        if not fpath.exists():
            return ToolResult("edit_file", args, "", False, f"File not found: {raw_path}")

        try:
            original = fpath.read_text(encoding="utf-8", errors="replace")
        except Exception as exc:
            return ToolResult("edit_file", args, "", False, str(exc))

        occurrences = original.count(old_text)
        if occurrences == 0:
            return ToolResult("edit_file", args, "", False, "old_string not found in file")
        if occurrences > 1:
            return ToolResult(
                "edit_file", args, "", False,
                f"old_string is ambiguous ({occurrences} occurrences). Provide more context.",
            )

        updated = original.replace(old_text, new_text, 1)

        if len(updated.encode("utf-8")) > _MAX_WRITE_FILE_SIZE:
            return ToolResult("edit_file", args, "", False, f"Resulting file would exceed {_MAX_WRITE_FILE_SIZE} bytes")

        try:
            self._ensure_checkpoint()
            self._backup_file(fpath)
            fpath.write_text(updated, encoding="utf-8")
        except Exception as exc:
            return ToolResult("edit_file", args, "", False, str(exc))

        return ToolResult(
            "edit_file", args,
            f"Edited {fpath.relative_to(self.project_root)} (1 replacement)."
            + self._syntax_report(fpath),
        )

    # ── git tools ─────────────────────────────────────────────────────────

    def _ensure_checkpoint(self) -> None:
        """Auto-stash once before the first write in an agent session."""
        if self._checkpointed:
            return
        if not self.git_enabled:
            self._checkpointed = True
            return
        from .git_helper import checkpoint
        self._checkpoint_result = checkpoint(self.project_root)
        self._checkpointed = True

    def _tool_git_status(self, _args: str) -> ToolResult:
        from .git_helper import is_repo, status as git_status
        if not is_repo(self.project_root):
            return ToolResult("git_status", _args, "", False, _NOT_A_REPO)
        out = git_status(self.project_root)
        if len(out) > _TOOL_OUTPUT_LIMIT:
            out = out[:_TOOL_OUTPUT_LIMIT] + "\n... (truncated)"
        return ToolResult("git_status", _args, out)

    def _tool_git_diff(self, _args: str) -> ToolResult:
        from .git_helper import diff as git_diff, is_repo
        if not is_repo(self.project_root):
            return ToolResult("git_diff", _args, "", False, _NOT_A_REPO)
        out = git_diff(self.project_root)
        if len(out) > _TOOL_OUTPUT_LIMIT:
            out = out[:_TOOL_OUTPUT_LIMIT] + "\n... (truncated)"
        return ToolResult("git_diff", _args, out)

    def _tool_git_commit(self, args: str) -> ToolResult:
        if not self.allow_write:
            return ToolResult(
                "git_commit", args, "", False,
                "Write operations disabled. Start agent with --write or allow_write=True.",
            )
        from .git_helper import commit as git_commit, is_repo
        if not is_repo(self.project_root):
            return ToolResult("git_commit", args, "", False, _NOT_A_REPO)
        message = args.strip() or "Fluxion auto-commit"
        out = git_commit(self.project_root, message)
        return ToolResult("git_commit", args, out)

    @staticmethod
    def _tool_finish(args: str) -> ToolResult:
        return ToolResult("finish", args, args, success=True)
