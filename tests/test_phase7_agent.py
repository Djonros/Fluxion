"""Phase 7 tests: ReAct agent tools, response parsing, eval utilities.

Agent LLM-dependent tests are skipped when Ollama is unavailable.
All tool tests use local files / mocks — no LLM calls needed.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from orchestrator import AgentResult, AgentStep, CodingAgent, ToolResult


# ═══════════════════════════════════════════════════════════════════════════════
#  Response parsing
# ═══════════════════════════════════════════════════════════════════════════════

class TestResponseParsing:
    def test_parse_thought_and_action(self):
        response = """Thought: I need to read the config file.
Action: read_file config/config.yaml"""

        thought, action = CodingAgent._parse_response(response)
        assert "config file" in thought.lower()
        assert action == "read_file config/config.yaml"

    def test_parse_only_thought(self):
        response = "Thought: Let me think about this."
        thought, action = CodingAgent._parse_response(response)
        assert "think" in thought.lower()
        assert action == ""

    def test_parse_no_thought_no_action(self):
        thought, action = CodingAgent._parse_response("Just some text")
        assert thought == ""
        assert action == ""

    def test_parse_action_with_args(self):
        response = "Thought: x\nAction: grep def.*config"
        _, action = CodingAgent._parse_response(response)
        assert action == "grep def.*config"

    def test_parse_action_split(self):
        tool, args = CodingAgent._parse_action("read_file config.yaml")
        assert tool == "read_file"
        assert args == "config.yaml"

    def test_parse_action_no_args(self):
        tool, args = CodingAgent._parse_action("finish")
        assert tool == "finish"
        assert args == ""

    def test_parse_action_with_quotes(self):
        tool, args = CodingAgent._parse_action('read_file "path/to file.py"')
        assert tool == "read_file"
        assert "path/to file.py" in args

    def test_parse_finish_prefix(self):
        """LLM sometimes writes 'Finish:' instead of 'Action: finish'."""
        response = (
            "Thought: I have the answer.\n"
            "Finish: The function returns a greeting."
        )
        thought, action = CodingAgent._parse_response(response)
        assert "answer" in thought.lower()
        assert action.startswith("finish")
        assert "greeting" in action.lower()

    def test_parse_action_takes_precedence_over_finish(self):
        """When both Action and Finish are present, Action wins."""
        response = "Thought: x\nAction: read_file foo.py\nFinish: done"
        _, action = CodingAgent._parse_response(response)
        assert action == "read_file foo.py"

    def test_parse_multiline_write_file(self):
        response = (
            "Thought: I'll create a new file.\n"
            "Action: write_file src/new.py\n"
            "def hello():\n"
            "    return 'world'\n"
        )
        _, action = CodingAgent._parse_response(response)
        assert action.startswith("write_file src/new.py")
        assert "def hello():" in action
        assert "return 'world'" in action

    def test_parse_multiline_edit_file(self):
        response = (
            "Thought: I'll edit the file.\n"
            "Action: edit_file src/mod.py\n"
            "---OLD---\nx = 1\n"
            "---NEW---\nx = 42\n"
        )
        _, action = CodingAgent._parse_response(response)
        assert action.startswith("edit_file src/mod.py")
        assert "---OLD---" in action
        assert "---NEW---" in action


# ═══════════════════════════════════════════════════════════════════════════════
#  Tools (local, no LLM)
# ═══════════════════════════════════════════════════════════════════════════════

class TestAgentTools:
    @pytest.fixture()
    def agent(self, tmp_path):
        """Create an agent with a temp project root."""
        backend = MagicMock()
        return CodingAgent(backend=backend, project_root=str(tmp_path))

    def test_read_file_success(self, agent, tmp_path):
        fpath = tmp_path / "test.py"
        fpath.write_text("def hello():\n    return 'world'\n", encoding="utf-8")

        result = agent._tool_read_file("test.py")
        assert result.success
        assert "hello" in result.output

    def test_read_file_not_found(self, agent):
        result = agent._tool_read_file("nonexistent.py")
        assert not result.success
        assert "not found" in result.error.lower()

    def test_read_file_no_path(self, agent):
        result = agent._tool_read_file("")
        assert not result.success

    def test_read_file_truncates_long(self, agent, tmp_path):
        fpath = tmp_path / "big.py"
        fpath.write_text("x" * 20000, encoding="utf-8")
        result = agent._tool_read_file("big.py")
        assert result.success
        assert "truncated" in result.output

    def test_grep_finds_matches(self, agent, tmp_path):
        fpath = tmp_path / "mod.py"
        fpath.write_text("def foo():\n    pass\n\ndef bar():\n    pass\n", encoding="utf-8")

        result = agent._tool_grep("def foo")
        assert result.success
        assert "foo" in result.output

    def test_grep_no_matches(self, agent, tmp_path):
        fpath = tmp_path / "mod.py"
        fpath.write_text("x = 1\n", encoding="utf-8")

        result = agent._tool_grep("nonexistent_pattern")
        assert result.success
        assert "No matches" in result.output

    def test_grep_invalid_regex(self, agent, tmp_path):
        result = agent._tool_grep("[invalid(")
        assert not result.success
        assert "regex" in result.error.lower()

    def test_grep_empty_args(self, agent):
        result = agent._tool_grep("")
        assert not result.success

    def test_web_search_not_configured(self, agent):
        result = agent._tool_web_search("test query")
        assert not result.success
        assert "not configured" in result.error

    def test_finish_tool(self, agent):
        result = agent._tool_finish("The answer is 42")
        assert result.success
        assert result.output == "The answer is 42"

    def test_unknown_tool(self, agent):
        result = agent._dispatch_tool("nonexistent_tool", "args")
        assert not result.success
        assert "Unknown tool" in result.error

    def test_python_executable_uses_sys_executable_unfrozen(self):
        assert CodingAgent._python_executable() == sys.executable

    def test_python_executable_prefers_bundled_venv_when_frozen(self, tmp_path, monkeypatch):
        exe = tmp_path / "FluxionBrowser.exe"
        exe.write_bytes(b"")
        bundled = tmp_path / "data" / "training_env" / "Scripts" / "python.exe"
        bundled.parent.mkdir(parents=True)
        bundled.write_bytes(b"")
        monkeypatch.setattr(sys, "frozen", True, raising=False)
        monkeypatch.setattr(sys, "executable", str(exe))
        assert CodingAgent._python_executable() == str(bundled)

    def test_python_executable_falls_back_to_path_when_frozen(self, tmp_path, monkeypatch):
        exe = tmp_path / "FluxionBrowser.exe"
        exe.write_bytes(b"")
        monkeypatch.setattr(sys, "frozen", True, raising=False)
        monkeypatch.setattr(sys, "executable", str(exe))
        monkeypatch.setattr(
            "orchestrator.agent.shutil.which", lambda name: "C:/Python314/python.exe"
        )
        assert CodingAgent._python_executable() == "C:/Python314/python.exe"

    def test_run_tests_never_relaunches_frozen_app(self, tmp_path, monkeypatch):
        exe = tmp_path / "FluxionBrowser.exe"
        exe.write_bytes(b"")
        monkeypatch.setattr(sys, "frozen", True, raising=False)
        monkeypatch.setattr(sys, "executable", str(exe))
        monkeypatch.setattr(
            "orchestrator.agent.shutil.which", lambda name: "C:/Python314/python.exe"
        )
        calls = {}

        def fake_run(cmd, **kwargs):
            calls["cmd"] = cmd
            return subprocess.CompletedProcess(cmd, 0, stdout="1 passed", stderr="")

        monkeypatch.setattr("orchestrator.agent.subprocess.run", fake_run)
        agent = CodingAgent(backend=MagicMock(), project_root=str(tmp_path))
        result = agent._tool_run_tests("tests")
        assert result.success
        assert calls["cmd"][0] == "C:/Python314/python.exe"


# ═══════════════════════════════════════════════════════════════════════════════
#  Write / Edit tools (local, no LLM)
# ═══════════════════════════════════════════════════════════════════════════════

class TestWriteFileTool:
    @pytest.fixture()
    def rw_agent(self, tmp_path):
        backend = MagicMock()
        return CodingAgent(backend=backend, project_root=str(tmp_path), allow_write=True)

    @pytest.fixture()
    def ro_agent(self, tmp_path):
        backend = MagicMock()
        return CodingAgent(backend=backend, project_root=str(tmp_path), allow_write=False)

    def test_write_creates_new_file(self, rw_agent, tmp_path):
        fpath = tmp_path / "new.py"
        content = "def hello():\n    return 'world'\n"
        result = rw_agent._tool_write_file(f"new.py\n{content}")

        assert result.success
        assert fpath.exists()
        assert fpath.read_text(encoding="utf-8") == content

    def test_write_overwrites_existing_with_backup(self, rw_agent, tmp_path):
        fpath = tmp_path / "mod.py"
        fpath.write_text("OLD", encoding="utf-8")
        result = rw_agent._tool_write_file("mod.py\nNEW")

        assert result.success
        assert fpath.read_text(encoding="utf-8") == "NEW"
        backup = fpath.parent / ".fluxion-backup" / "mod.py"
        assert backup.exists()
        assert backup.read_text(encoding="utf-8") == "OLD"

    def test_write_disabled_by_default(self, ro_agent):
        result = ro_agent._tool_write_file("test.py\ncontent")
        assert not result.success
        assert "disabled" in result.error.lower()

    def test_write_rejects_path_traversal(self, rw_agent):
        result = rw_agent._tool_write_file("../../etc/passwd\nmalicious")
        assert not result.success
        assert "unsafe" in result.error.lower() or "invalid" in result.error.lower()

    def test_write_rejects_blocked_dir(self, rw_agent):
        result = rw_agent._tool_write_file(".git/config\nmalicious")
        assert not result.success
        assert "unsafe" in result.error.lower() or "invalid" in result.error.lower()

    def test_write_rejects_no_content(self, rw_agent):
        result = rw_agent._tool_write_file("test.py")
        assert not result.success
        assert "usage" in result.error.lower()

    def test_write_creates_parent_dirs(self, rw_agent, tmp_path):
        nested = tmp_path / "sub" / "dir"
        result = rw_agent._tool_write_file("sub/dir/mod.py\ncontent")
        assert result.success
        assert (nested / "mod.py").exists()


class TestEditFileTool:
    @pytest.fixture()
    def rw_agent(self, tmp_path):
        backend = MagicMock()
        return CodingAgent(backend=backend, project_root=str(tmp_path), allow_write=True)

    @pytest.fixture()
    def ro_agent(self, tmp_path):
        backend = MagicMock()
        return CodingAgent(backend=backend, project_root=str(tmp_path), allow_write=False)

    def test_edit_replaces_unique_snippet(self, rw_agent, tmp_path):
        fpath = tmp_path / "code.py"
        fpath.write_text("x = 1\ny = 2\nz = 3\n", encoding="utf-8")

        args = "code.py\n---OLD---\ny = 2\n---NEW---\ny = 42\n"
        result = rw_agent._tool_edit_file(args)

        assert result.success
        assert "y = 42" in fpath.read_text(encoding="utf-8")
        assert "x = 1" in fpath.read_text(encoding="utf-8")

    def test_edit_creates_backup(self, rw_agent, tmp_path):
        fpath = tmp_path / "mod.py"
        fpath.write_text("hello world", encoding="utf-8")

        args = "mod.py\n---OLD---\nhello\n---NEW---\ngoodbye\n"
        rw_agent._tool_edit_file(args)

        backup = fpath.parent / ".fluxion-backup" / "mod.py"
        assert backup.exists()
        assert backup.read_text(encoding="utf-8") == "hello world"

    def test_edit_disabled_by_default(self, ro_agent):
        result = ro_agent._tool_edit_file("mod.py\n---OLD---\nx\n---NEW---\ny\n")
        assert not result.success
        assert "disabled" in result.error.lower()

    def test_edit_file_not_found(self, rw_agent):
        args = "nonexist.py\n---OLD---\nx\n---NEW---\ny\n"
        result = rw_agent._tool_edit_file(args)
        assert not result.success
        assert "not found" in result.error.lower()

    def test_edit_old_not_found(self, rw_agent, tmp_path):
        fpath = tmp_path / "code.py"
        fpath.write_text("unchanged", encoding="utf-8")
        args = "code.py\n---OLD---\nmissing\n---NEW---\nreplaced\n"
        result = rw_agent._tool_edit_file(args)
        assert not result.success
        assert "not found" in result.error.lower()

    def test_edit_ambiguous_match(self, rw_agent, tmp_path):
        fpath = tmp_path / "dup.py"
        fpath.write_text("foo\nfoo\n", encoding="utf-8")
        args = "dup.py\n---OLD---\nfoo\n---NEW---\nbar\n"
        result = rw_agent._tool_edit_file(args)
        assert not result.success
        assert "ambiguous" in result.error.lower()

    def test_edit_identical_old_new(self, rw_agent, tmp_path):
        fpath = tmp_path / "code.py"
        fpath.write_text("same\n", encoding="utf-8")
        args = "code.py\n---OLD---\nsame\n---NEW---\nsame\n"
        result = rw_agent._tool_edit_file(args)
        assert not result.success
        assert "identical" in result.error.lower()

    def test_edit_rejects_path_traversal(self, rw_agent):
        args = "../../etc/passwd\n---OLD---\nx\n---NEW---\ny\n"
        result = rw_agent._tool_edit_file(args)
        assert not result.success
        assert "unsafe" in result.error.lower() or "invalid" in result.error.lower()


# ═══════════════════════════════════════════════════════════════════════════════
#  Agent run (mocked backend)
# ═══════════════════════════════════════════════════════════════════════════════

class TestAgentRun:
    def test_agent_finishes_immediately(self, tmp_path):
        """Agent calls 'finish' on the first turn."""
        backend = MagicMock()
        backend.generate.return_value = (
            "Thought: I can answer directly.\n"
            "Action: finish 2 plus 2 equals 4"
        )
        agent = CodingAgent(backend=backend, project_root=str(tmp_path))
        result = agent.run("What is 2+2?")

        assert result.success
        assert "4" in result.final_answer
        assert len(result.steps) == 1
        assert result.steps[0].is_final

    def test_agent_uses_tool_then_finishes(self, tmp_path):
        """Agent reads a file, then finishes."""
        fpath = tmp_path / "config.py"
        fpath.write_text("VERSION = '1.0'\n", encoding="utf-8")

        backend = MagicMock()
        responses = [
            "Thought: Let me check the version.\nAction: read_file config.py",
            "Thought: Found it.\nAction: finish The version is 1.0",
        ]
        backend.generate.side_effect = responses
        agent = CodingAgent(backend=backend, project_root=str(tmp_path))
        result = agent.run("What version is this project?")

        assert result.success
        assert "1.0" in result.final_answer
        assert len(result.steps) == 2

    def test_agent_max_iterations(self, tmp_path):
        """Agent never calls 'finish' — hits iteration limit."""
        backend = MagicMock()
        backend.generate.return_value = (
            "Thought: Let me check something.\n"
            "Action: read_file nonexist.py"
        )
        agent = CodingAgent(
            backend=backend, project_root=str(tmp_path), max_iterations=3, verify_after_write=False
        )
        result = agent.run("Keep going")

        assert not result.success
        assert len(result.steps) <= 4

    def test_agent_retries_on_no_action(self, tmp_path):
        """Agent gives no Action → gets prompted to use tools."""
        responses = [
            "Thought: Hmm, let me think.\n",  # no action
            "Thought: Now I'll finish.\nAction: finish Done!",
        ]
        backend = MagicMock()
        backend.generate.side_effect = responses
        agent = CodingAgent(backend=backend, project_root=str(tmp_path))
        result = agent.run("Test")

        assert result.success
        assert result.final_answer == "Done!"

    def test_agent_repeated_identical_action_not_reexecuted(self, tmp_path):
        """Identical tool call repeated → executed once, warning injected."""
        fpath = tmp_path / "config.py"
        fpath.write_text("X = 1\n", encoding="utf-8")

        responses = [
            "Thought: check.\nAction: read_file config.py",
            "Thought: again.\nAction: read_file config.py",
            "Thought: stop.\nAction: finish Done",
        ]
        backend = MagicMock()
        backend.generate.side_effect = responses
        agent = CodingAgent(backend=backend, project_root=str(tmp_path))
        original = agent._dispatch_tool
        calls: list[str] = []

        def spy(name, args):
            calls.append(name)
            return original(name, args)

        agent._dispatch_tool = spy
        result = agent.run("Test")

        assert calls == ["read_file"]
        assert "already executed" in result.steps[1].observation
        assert result.success

    def test_system_prompts_forbid_tools_for_greetings(self, tmp_path):
        """Prompts must steer greetings/small talk straight to finish."""
        from orchestrator.agent import REACT_SYSTEM_RO, REACT_SYSTEM_RW

        for prompt in (REACT_SYSTEM_RO, REACT_SYSTEM_RW):
            assert "greeting" in prompt
            assert "NO tools" in prompt

    def test_agent_plain_text_reply_accepted_as_final(self, tmp_path):
        """Model answers a greeting with plain text (no Action) twice → final."""
        responses = [
            "Привет! Я Fluxion, ассистент для работы с кодом.",
            "Я Fluxion: могу читать файлы, искать и запускать тесты.",
        ]
        backend = MagicMock()
        backend.generate.side_effect = responses
        agent = CodingAgent(backend=backend, project_root=str(tmp_path))
        result = agent.run("привет")

        assert result.success
        assert "Fluxion" in result.final_answer
        assert result.steps[-1].is_final

    def test_agent_iter_plain_text_reply_accepted_as_final(self, tmp_path):
        """Same via the streaming run_iter() variant."""
        responses = [
            "Привет! Я Fluxion.",
            "Я Fluxion. Задайте задачу по коду.",
        ]
        backend = MagicMock()
        backend.generate.side_effect = responses
        agent = CodingAgent(backend=backend, project_root=str(tmp_path))
        result = agent.run("привет")

        assert result.success
        assert "Fluxion" in result.final_answer

    # ── verification gate (auto run_tests before finish) ─────────────────

    def test_gate_blocks_unverified_finish(self, tmp_path):
        """Write → finish without tests: gate auto-runs run_tests."""
        responses = [
            "Thought: create\nAction: write_file utils.py\nX = 1\n",
            "Thought: done\nAction: finish Готово",
            "Thought: verified\nAction: finish Готово",
        ]
        backend = MagicMock()
        backend.generate.side_effect = responses
        agent = CodingAgent(
            backend=backend, project_root=str(tmp_path), allow_write=True, git_enabled=False
        )
        result = agent.run("добавь функцию")

        tools = [s.tool_name for s in result.steps]
        assert tools[-1] == "finish"
        assert "run_tests" in tools
        assert result.verification in ("passed", "syntax_only", "failed")

    def test_gate_syntax_fallback_without_tests(self, tmp_path):
        """Project without pytest: syntax check decides; broken code blocked."""
        responses = [
            "Thought: bad\nAction: write_file utils.py\ndef f(:\n",
            "Thought: done\nAction: finish Готово",
            "Thought: fix\nAction: write_file utils.py\ndef f():\n    return 1\n",
            "Thought: done\nAction: finish Готово",
            "Thought: done\nAction: finish Готово",
        ]
        backend = MagicMock()
        backend.generate.side_effect = responses
        agent = CodingAgent(
            backend=backend, project_root=str(tmp_path), allow_write=True, git_enabled=False
        )
        result = agent.run("добавь функцию")

        gate_steps = [s for s in result.steps if s.tool_args == "(auto-verify)"]
        assert gate_steps
        assert "syntax check FAILED" in gate_steps[0].observation
        # No tests in the project: only syntax was checked — reported honestly.
        assert result.verification == "syntax_only"

    def test_gate_budget_exhausted_marks_failed(self, tmp_path, monkeypatch):
        """Always-failing verification: budget exhausted → finish allowed, failed."""
        responses = [
            "Thought: w\nAction: write_file utils.py\nX = 1\n",
            "Thought: d\nAction: finish Готово",
            "Thought: d\nAction: finish Готово",
            "Thought: d\nAction: finish Готово",
        ]
        backend = MagicMock()
        backend.generate.side_effect = responses
        agent = CodingAgent(
            backend=backend, project_root=str(tmp_path), allow_write=True, git_enabled=False
        )
        monkeypatch.setattr(
            agent, "_auto_verify", lambda: (False, "[verification gate] FAILED")
        )
        result = agent.run("t")

        assert result.verification == "failed"
        assert sum(s.tool_args == "(auto-verify)" for s in result.steps) <= 2
        assert result.steps[-1].is_final


# ═══════════════════════════════════════════════════════════════════════════════
#  Agent with Ollama (integration, optional)
# ═══════════════════════════════════════════════════════════════════════════════

class TestAgentOllama:
    @pytest.fixture(scope="module")
    def backend(self):
        from core.config import Settings
        from core.inference import OllamaBackend

        b = OllamaBackend(Settings.load())
        if not b.is_available():
            pytest.skip("Ollama not available")
        return b

    def test_agent_reads_file(self, backend, tmp_path):
        fpath = tmp_path / "hello.py"
        fpath.write_text("def greet(name):\n    return f'Hello, {name}!'\n", encoding="utf-8")

        agent = CodingAgent(backend=backend, project_root=str(tmp_path), max_iterations=4)
        result = agent.run(f"Read the file hello.py and tell me what the greet function does.")

        assert result.success
        assert len(result.steps) > 0


# ═══════════════════════════════════════════════════════════════════════════════
#  Eval utilities
# ═══════════════════════════════════════════════════════════════════════════════

class TestEvalUtilities:
    def test_extract_code_from_fenced(self):
        text = "Here is the code:\n```python\ndef add(a, b):\n    return a + b\n```\nDone."
        code = __import__("eval").extract_code(text)
        assert "def add" in code
        assert "return a + b" in code

    def test_extract_code_plain(self):
        text = "def square(x):\n    return x ** 2\n"
        code = __import__("eval").extract_code(text)
        assert "def square" in code

    def test_extract_code_from_multiple_blocks(self):
        text = "```python\nx = 1\n```\nMore text\n```python\ny = 2\n```"
        code = __import__("eval").extract_code(text)
        assert "x = 1" in code
        assert "y = 2" in code

    def test_extract_code_empty(self):
        assert __import__("eval").extract_code("") == ""

    def test_run_code_safely_passes(self):
        code = "x = 2 + 2\nassert x == 4"
        passed, error = __import__("eval").run_code_safely(code, "", "")
        assert passed

    def test_run_code_safely_fails(self):
        code = "assert False"
        passed, error = __import__("eval").run_code_safely(code, "", "")
        assert not passed
        assert error

    def test_run_code_safely_syntax_error(self):
        code = "def broken(:"
        passed, error = __import__("eval").run_code_safely(code, "", "")
        assert not passed

    def test_compute_pass_at_k_all_pass(self):
        from eval import compute_pass_at_k
        assert compute_pass_at_k([True, True, True], 1) == 1.0

    def test_compute_pass_at_k_mixed(self):
        from eval import compute_pass_at_k
        result = compute_pass_at_k([True, False, True, False], 1)
        assert result == 0.5

    def test_compute_pass_at_k_empty(self):
        from eval import compute_pass_at_k
        assert compute_pass_at_k([], 1) == 0.0

    def test_eval_result_accuracy(self):
        from eval import EvalResult
        r = EvalResult(benchmark="test", total=10, passed=7, failed=3)
        assert r.accuracy == 0.7
