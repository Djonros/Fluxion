"""Presentation helpers for agent steps and chat text (no Qt imports).

Used by the desktop apps to show what the agent is doing without duplicating
the final answer, and to render ``` code blocks in chat messages.
"""
from __future__ import annotations

import html
import re
from pathlib import PurePosixPath

_URL_RE = re.compile(r"https?://[^\s<>\"']+")
_FENCE_RE = re.compile(r"```[ \t]*([\w.+#-]*)[ \t]*\n(.*?)(?:\n?```|\Z)", re.S)
_INLINE_CODE_RE = re.compile(r"`([^`\n]+)`")

_CODE_BLOCK_STYLE = (
    "background-color:#0B1220; color:#E2E8F0; border:1px solid #1F2937; "
    "padding:8px; margin:6px 0; "
    "font-family:'Cascadia Mono',Consolas,'Courier New',monospace; font-size:13px; "
    "white-space:pre-wrap;"
)
_INLINE_CODE_STYLE = (
    "background-color:#1F2937; font-family:'Cascadia Mono',Consolas,monospace;"
)

_LANG_BY_SUFFIX = {
    ".py": "python", ".js": "javascript", ".ts": "typescript", ".json": "json",
    ".yaml": "yaml", ".yml": "yaml", ".md": "markdown", ".sh": "bash",
    ".bat": "bat", ".ps1": "powershell", ".html": "html", ".css": "css",
    ".sql": "sql", ".toml": "toml", ".ini": "ini", ".txt": "",
}

MAX_CODE_LINES = 80
_THOUGHT_CHARS = 300
_ARGS_CHARS = 200


# ── chat text → HTML ─────────────────────────────────────────────────────────

def _linkify(text: str) -> str:
    parts: list[str] = []
    pos = 0
    for match in _URL_RE.finditer(text):
        url = match.group(0).rstrip(".,);]")
        parts.append(html.escape(text[pos:match.start()]))
        parts.append(f'<a href="{html.escape(url, quote=True)}">{html.escape(url)}</a>')
        pos = match.start() + len(url)
    parts.append(html.escape(text[pos:]))
    return "".join(parts)


def _prose_html(text: str) -> str:
    out: list[str] = []
    pos = 0
    for match in _INLINE_CODE_RE.finditer(text):
        out.append(_linkify(text[pos:match.start()]))
        out.append(f'<code style="{_INLINE_CODE_STYLE}">{html.escape(match.group(1))}</code>')
        pos = match.end()
    out.append(_linkify(text[pos:]))
    return "".join(out).replace("\n", "<br>")


def render_chat_html(text: str) -> str:
    """Plain chat text → HTML: ``` blocks become monospace code boxes,
    `inline code` is highlighted, URLs become links, everything else is
    escaped.  An unclosed fence (text still streaming) is rendered as code."""
    if not text:
        return ""
    out: list[str] = []
    pos = 0
    for match in _FENCE_RE.finditer(text):
        before = text[pos:match.start()]
        out.append(_prose_html(before.rstrip("\n")))
        code = match.group(2).rstrip("\n")
        out.append(f'<pre style="{_CODE_BLOCK_STYLE}">{html.escape(code)}</pre>')
        pos = match.end()
        if text[pos:pos + 1] == "\n":
            pos += 1
    out.append(_prose_html(text[pos:]))
    return "".join(out)


# ── agent steps → chat text ──────────────────────────────────────────────────

def _fence(code: str, lang: str = "") -> str:
    lines = code.rstrip("\n").split("\n")
    if len(lines) > MAX_CODE_LINES:
        hidden = len(lines) - MAX_CODE_LINES
        lines = lines[:MAX_CODE_LINES] + [f"# … ещё {hidden} строк"]
    body = "\n".join(lines).replace("```", "ʼʼʼ")  # never break our own fence
    return f"```{lang}\n{body}\n```"


def _lang_for(path: str) -> str:
    return _LANG_BY_SUFFIX.get(PurePosixPath(path.replace("\\", "/")).suffix.lower(), "")


def _plural_lines(n: int) -> str:
    if n % 10 == 1 and n % 100 != 11:
        return f"{n} строка"
    if n % 10 in (2, 3, 4) and n % 100 not in (12, 13, 14):
        return f"{n} строки"
    return f"{n} строк"


def _verify_status(observation: str) -> str:
    low = observation.lower()
    if "failed" in low:
        return "есть ошибки — агент исправляет"
    if "проверен только синтаксис" in low:
        return "тестов нет, проверен только синтаксис"
    if "passed" in low:
        return "тесты пройдены"
    return "выполнена"


def _tests_status(observation: str) -> str:
    low = observation.lower()
    if low.startswith("error: pytest is not installed"):
        return "pytest не установлен"
    if low.startswith("error: no tests were collected"):
        return "тестов нет"
    if low.startswith("error: running tests is disabled"):
        return "запуск тестов отключён"
    if low.startswith("error:"):
        return "есть падения"
    return "пройдены"


def format_tool(step) -> str:
    """What the agent did in *step*, as chat text (may contain ``` blocks).

    The ``finish`` action is intentionally empty: its text is the final
    answer, which the UI shows once, separately."""
    tool = getattr(step, "tool_name", "") or ""
    args = getattr(step, "tool_args", "") or ""
    observation = getattr(step, "observation", "") or ""

    if not tool:
        if observation.startswith("[language gate]"):
            return "↻ Перевожу ответ на язык вопроса…"
        return ""
    if tool == "finish":
        return ""
    if tool == "write_file":
        from .agent import _strip_code_fence

        path, _, content = args.lstrip("\n").partition("\n")
        content = _strip_code_fence(content)
        n = content.count("\n") + (0 if content.endswith("\n") or not content else 1)
        failed = observation.startswith("Error:")
        head = f"→ write_file {path.strip()} ({_plural_lines(n)})"
        if failed:
            return head + f" — не удалось: {observation[6:].strip()[:_ARGS_CHARS]}"
        return head + "\n" + _fence(content, _lang_for(path))
    if tool == "edit_file":
        from .agent import _DELIM_NEW, _DELIM_OLD, _strip_code_fence

        path, _, rest = args.partition(_DELIM_OLD)
        old, _, new = rest.partition(_DELIM_NEW)
        old = _strip_code_fence(old.strip("\n")).strip("\n")
        new = _strip_code_fence(new.strip("\n")).strip("\n")
        head = f"→ edit_file {path.strip()}"
        if observation.startswith("Error:"):
            return head + f" — не удалось: {observation[6:].strip()[:_ARGS_CHARS]}"
        diff = "\n".join(
            [f"- {line}" for line in old.split("\n")] + [f"+ {line}" for line in new.split("\n")]
        )
        return head + "\n" + _fence(diff, "diff")
    if tool == "run_tests":
        if args == "(auto-verify)":
            return f"→ автопроверка: {_verify_status(observation)}"
        label = f"→ run_tests {args}".rstrip()
        return f"{label}: {_tests_status(observation)}"
    one_line = " ".join(args.split())
    if len(one_line) > _ARGS_CHARS:
        one_line = one_line[:_ARGS_CHARS] + "…"
    return f"→ {tool} {one_line}".rstrip()


def format_agent_step(step) -> str:
    """Thought + action of one agent step for the chat view."""
    observation = getattr(step, "observation", "") or ""
    tool_text = format_tool(step)
    if observation.startswith("[language gate]"):
        # The thought here is the answer in the wrong language: don't show it.
        return tool_text
    parts: list[str] = []
    thought = (getattr(step, "thought", "") or "").strip()
    if thought:
        if len(thought) > _THOUGHT_CHARS:
            thought = thought[:_THOUGHT_CHARS] + "…"
        parts.append(thought)
    if tool_text:
        parts.append(tool_text)
    return "\n".join(parts)
