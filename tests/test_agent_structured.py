"""Structured (JSON-schema constrained) tool calls and semantic code search."""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from core.config import GenerationSettings
from orchestrator import CodingAgent
from orchestrator.agent import (
    TOOL_SPECS,
    build_action_schema,
    build_json_system_prompt,
    normalize_action,
)


# ── a tiny validator for the schema subset we emit ───────────────────────────

def _valid(instance, schema) -> bool:
    if "anyOf" in schema:
        return sum(_valid(instance, s) for s in schema["anyOf"]) == 1
    if "const" in schema:
        return instance == schema["const"]
    t = schema.get("type")
    if t == "string":
        return isinstance(instance, str)
    if t == "integer":
        return isinstance(instance, int) and not isinstance(instance, bool)
    if t == "object":
        if not isinstance(instance, dict):
            return False
        props = schema.get("properties", {})
        if any(k not in instance for k in schema.get("required", [])):
            return False
        if schema.get("additionalProperties") is False and set(instance) - set(props):
            return False
        return all(_valid(v, props[k]) for k, v in instance.items() if k in props)
    return True


class JsonBackend:
    """Backend that declares grammar support and records settings."""

    supports_json_schema = True

    def __init__(self, replies, fail_on_schema=False):
        self.replies = list(replies)
        self.calls: list[GenerationSettings | None] = []
        self.fail_on_schema = fail_on_schema
        self.generation = GenerationSettings()

    def generate(self, messages, gen=None):
        self.calls.append(gen)
        self.last_messages = [dict(m) for m in messages]
        if self.fail_on_schema and gen is not None and gen.json_schema:
            raise RuntimeError("400: format not supported")
        return self.replies.pop(0)

    def stream(self, messages, gen=None):
        yield ""

    def is_available(self):
        return True


def J(**kw) -> str:
    return json.dumps(kw, ensure_ascii=False)


def _agent(tmp_path, backend, **kw):
    kw.setdefault("git_enabled", False)
    kw.setdefault("lang", "off")
    return CodingAgent(backend=backend, project_root=str(tmp_path), **kw)


# ── schema & prompt ─────────────────────────────────────────────────────────

class TestSchema:
    def test_prompt_examples_match_schema(self):
        names = list(TOOL_SPECS)
        schema = build_action_schema(names)
        prompt = build_json_system_prompt(names, allow_write=True)
        section = prompt.split("Examples:", 1)[1].split("Rules:", 1)[0]
        examples = [ln for ln in section.splitlines() if ln.startswith("{")]
        assert len(examples) >= 3
        for ex in examples:
            assert _valid(json.loads(ex), schema), ex

    def test_required_fields_enforced(self):
        schema = build_action_schema(list(TOOL_SPECS))
        assert not _valid({"thought": "", "tool": "read_file"}, schema)          # no path
        assert not _valid({"thought": "", "tool": "rm_rf", "path": "/"}, schema)   # unknown tool
        assert not _valid({"thought": "", "tool": "finish", "answer": "x", "evil": 1}, schema)
        assert _valid({"thought": "", "tool": "read_file", "path": "a.py", "start_line": 3}, schema)

    def test_tools_follow_configuration(self, tmp_path):
        ro = _agent(tmp_path, JsonBackend([]))
        names = ro.prompt_tool_names()
        assert "write_file" not in names and "search_code" not in names
        assert "web_search" not in names  # not configured
        rw = _agent(tmp_path, JsonBackend([]), allow_write=True, rag_service=MagicMock(),
                    allow_exec=False)
        names = rw.prompt_tool_names()
        assert {"write_file", "edit_file", "search_code"} <= set(names)
        assert "run_tests" not in names

    def test_format_selection(self, tmp_path, monkeypatch):
        assert _agent(tmp_path, JsonBackend([])).action_format == "json"
        assert _agent(tmp_path, MagicMock()).action_format == "text"   # MagicMock ≠ declaration
        assert _agent(tmp_path, JsonBackend([]), action_format="text").action_format == "text"
        monkeypatch.setenv("FLUXION_AGENT_FORMAT", "text")
        assert _agent(tmp_path, JsonBackend([])).action_format == "text"

    def test_text_prompt_hides_search_code_without_rag(self, tmp_path):
        agent = _agent(tmp_path, MagicMock())
        assert "search_code" not in agent.system_prompt
        agent = _agent(tmp_path, MagicMock(), rag_service=MagicMock())
        assert "search_code" in agent.system_prompt


# ── JSON loop ───────────────────────────────────────────────────────────────

class TestJsonLoop:
    def test_schema_sent_and_no_stop_sequences(self, tmp_path):
        backend = JsonBackend([J(thought="hi", tool="finish", answer="ok")])
        _agent(tmp_path, backend).run("t")
        gen = backend.calls[0]
        assert gen.json_schema == build_action_schema(_agent(tmp_path, backend).prompt_tool_names())
        assert not gen.stop

    def test_full_edit_cycle_with_multiline_content(self, tmp_path):
        code = 'def add(a, b):\n    """Sum."""\n    return a - b\n'
        backend = JsonBackend([
            J(thought="create", tool="write_file", path="calc.py", content=code),
            J(thought="fix", tool="edit_file", path="calc.py",
              old="    return a - b", new="    return a + b"),
            J(thought="done", tool="finish", answer="Готово:\n```python\nadd(1, 2)\n```"),
        ])
        agent = _agent(tmp_path, backend, allow_write=True, verify_after_write=False)
        result = agent.run("t")
        assert (tmp_path / "calc.py").read_text(encoding="utf-8") == code.replace("a - b", "a + b")
        assert result.final_answer == "Готово:\n```python\nadd(1, 2)\n```"
        assert [s.tool_name for s in result.steps] == ["write_file", "edit_file", "finish"]

    def test_grep_pattern_with_spaces_is_not_split(self, tmp_path):
        (tmp_path / "src").mkdir()
        (tmp_path / "m.py").write_text("x = compute total\n", encoding="utf-8")
        backend = JsonBackend([
            J(thought="", tool="grep", pattern="compute total"),
            J(thought="", tool="finish", answer="ok"),
        ])
        result = _agent(tmp_path, backend).run("t")
        assert "m.py:1" in result.steps[0].observation

    def test_read_range(self, tmp_path):
        (tmp_path / "f.py").write_text("\n".join(f"L{i}" for i in range(1, 50)), encoding="utf-8")
        backend = JsonBackend([
            J(thought="", tool="read_file", path="f.py", start_line=10, end_line=12),
            J(thought="", tool="finish", answer="ok"),
        ])
        obs = _agent(tmp_path, backend).run("t").steps[0].observation
        assert "L10" in obs and "L12" in obs and "L13" not in obs

    @pytest.mark.parametrize("reply", [
        '```json\n{"thought": "x", "tool": "finish", "answer": "ok"}\n```',
        'Sure! {"thought": "x", "tool": "finish", "answer": "ok"} hope it helps',
        '{"name": "finish", "arguments": {"answer": "ok"}}',
        '{"action": "finish", "args": "{\\"answer\\": \\"ok\\"}"}',
    ])
    def test_lenient_json_parsing(self, tmp_path, reply):
        assert _agent(tmp_path, JsonBackend([reply])).run("t").final_answer == "ok"

    def test_text_reply_accepted_in_json_mode(self, tmp_path):
        backend = JsonBackend(["Thought: done\nAction: finish ok\nsecond line"])
        assert _agent(tmp_path, backend).run("t").final_answer == "ok\nsecond line"

    def test_json_reply_accepted_in_text_mode(self, tmp_path):
        backend = MagicMock()
        backend.generate.side_effect = [J(thought="", tool="finish", answer="ok")]
        agent = _agent(tmp_path, backend)
        assert agent.action_format == "text"
        assert agent.run("t").final_answer == "ok"

    def test_unknown_tool_reported(self, tmp_path):
        backend = JsonBackend([J(thought="", tool="delete_all"), J(thought="", tool="finish", answer="ok")])
        obs = _agent(tmp_path, backend).run("t").steps[0].observation
        assert "Unknown tool" in obs

    def test_falls_back_to_text_when_schema_rejected(self, tmp_path):
        backend = JsonBackend(["Thought: ok\nAction: finish fallback works"], fail_on_schema=True)
        agent = _agent(tmp_path, backend)
        result = agent.run("t")
        assert result.final_answer == "fallback works"
        assert agent.action_format == "text"
        assert "Action:" in backend.last_messages[0]["content"]   # text prompt in use
        assert backend.calls[-1].json_schema is None

    def test_backend_error_after_start_is_not_masked(self, tmp_path):
        class Flaky(JsonBackend):
            def generate(self, messages, gen=None):
                if self.replies:
                    return super().generate(messages, gen)
                raise RuntimeError("connection lost")

        backend = Flaky([J(thought="", tool="list_files")])
        agent = _agent(tmp_path, backend)
        result = agent.run("t")
        assert "connection lost" in result.steps[-1].observation
        assert agent.action_format == "json"

    def test_cut_off_json_is_not_a_final_answer(self, tmp_path):
        cut = '{"thought": "big file", "tool": "write_file", "path": "a.py", "content": "x = 1\\n'
        backend = JsonBackend([cut, cut, J(thought="", tool="finish", answer="ok")])
        result = _agent(tmp_path, backend, allow_write=True).run("t")
        assert result.final_answer == "ok"
        assert "cut off" in result.steps[0].observation
        assert not (tmp_path / "a.py").exists()

    def test_normalize_action_rejects_garbage(self):
        assert normalize_action({"foo": 1}) is None
        assert normalize_action({"tool": ""}) is None


# ── search_code ─────────────────────────────────────────────────────────────

@dataclass
class R:
    content: str
    file_path: str
    start_line: int
    end_line: int
    node_type: str = "function"
    name: str = ""
    score: float = 0.9


class FakeRag:
    def __init__(self, results, ready=True, error=None):
        self.results, self.ready, self.error = results, ready, error
        self.queries = []

    @property
    def is_ready(self):
        return self.ready

    def search(self, query, top_k=None):
        self.queries.append(query)
        if self.error:
            raise self.error
        return self.results


class TestSearchCode:
    def test_returns_project_hits_only(self, tmp_path):
        (tmp_path / "auth.py").write_text("def login(): ...\n", encoding="utf-8")
        outside = tmp_path.parent / f"{tmp_path.name}-other.py"
        outside.write_text("secret()\n", encoding="utf-8")
        rag = FakeRag([
            R("def login(): ...", str(tmp_path / "auth.py"), 1, 1, name="login"),
            R("secret()", str(outside), 1, 1),
            R("def login(): ...", str(tmp_path / "auth.py"), 1, 1),  # duplicate
        ])
        agent = _agent(tmp_path, MagicMock(), rag_service=rag)
        res = agent._tool_search_code("where is login handled")
        assert res.success
        assert "auth.py:1-1  [login]" in res.output
        assert "secret()" not in res.output
        assert res.output.count("auth.py:1-1") == 1
        assert rag.queries == ["where is login handled"]

    def test_relative_index_paths(self, tmp_path):
        (tmp_path / "pkg").mkdir()
        (tmp_path / "pkg" / "a.py").write_text("x = 1\n", encoding="utf-8")
        agent = _agent(tmp_path, MagicMock(), rag_service=FakeRag([R("x = 1", "pkg/a.py", 1, 1)]))
        assert "pkg/a.py:1-1" in agent._tool_search_code("x").output

    def test_not_indexed(self, tmp_path):
        agent = _agent(tmp_path, MagicMock(), rag_service=FakeRag([], ready=False))
        res = agent._tool_search_code("x")
        assert not res.success and "not indexed" in res.error

    def test_index_error_suggests_grep(self, tmp_path):
        agent = _agent(tmp_path, MagicMock(), rag_service=FakeRag([], error=RuntimeError("bad index")))
        res = agent._tool_search_code("x")
        assert not res.success and "grep" in res.error

    def test_no_rag_configured(self, tmp_path):
        res = _agent(tmp_path, MagicMock())._tool_search_code("x")
        assert not res.success and "grep" in res.error

    def test_other_project_index_hint(self, tmp_path):
        other = tmp_path.parent / f"{tmp_path.name}-elsewhere.py"
        other.write_text("y\n", encoding="utf-8")
        agent = _agent(tmp_path, MagicMock(), rag_service=FakeRag([R("y", str(other), 1, 1)]))
        assert "re-index" in agent._tool_search_code("y").output

    def test_search_code_in_json_loop(self, tmp_path):
        (tmp_path / "auth.py").write_text("def login(): ...\n", encoding="utf-8")
        rag = FakeRag([R("def login(): ...", "auth.py", 1, 1)])
        backend = JsonBackend([
            J(thought="", tool="search_code", query="login"),
            J(thought="", tool="finish", answer="auth.py"),
        ])
        result = _agent(tmp_path, backend, rag_service=rag).run("where is login")
        assert "auth.py:1-1" in result.steps[0].observation


# ── backends ────────────────────────────────────────────────────────────────

class TestBackendsStructured:
    def test_llama_response_format(self):
        from core.llama_cpp_backend import LlamaCppBackend

        b = object.__new__(LlamaCppBackend)
        b.generation = GenerationSettings()
        schema = {"type": "object"}
        params = b._params(GenerationSettings(json_schema=schema))
        assert params["response_format"] == {"type": "json_object", "schema": schema}
        assert "response_format" not in b._params(None)
        assert LlamaCppBackend.supports_json_schema is True

    def test_ollama_passes_format(self):
        from core.config import Settings
        from core.inference import OllamaBackend

        b = OllamaBackend(Settings())
        b.client = MagicMock()
        b.client.chat.return_value = "x"
        b.generate([], GenerationSettings(json_schema={"type": "object"}))
        assert b.client.chat.call_args.kwargs["format"] == {"type": "object"}
        b.generate([], GenerationSettings())
        assert "format" not in b.client.chat.call_args.kwargs

    def test_api_structured_is_opt_in(self, monkeypatch):
        from core.api_backend import APIBackend

        monkeypatch.delenv("FLUXION_API_STRUCTURED", raising=False)
        b = APIBackend(api_key="k")
        gen = GenerationSettings(json_schema={"type": "object"})
        assert b.supports_json_schema is False
        assert "response_format" not in b._payload([], gen, stream=False)
        monkeypatch.setenv("FLUXION_API_STRUCTURED", "1")
        b = APIBackend(api_key="k")
        assert b._payload([], gen, stream=False)["response_format"]["type"] == "json_schema"
