"""2026-11-14: after an edit the chat showed «→ run_tests: пройдены» while the model read «blocked», then
it read the same test file over and over until the run was stopped."""
from __future__ import annotations

import subprocess
from types import SimpleNamespace
from unittest.mock import MagicMock

from orchestrator import CodingAgent
from orchestrator.presentation import _tests_status, format_tool


def _agent(tmp_path, replies, **kw):
    backend = MagicMock()
    backend.generate.side_effect = list(replies)
    kw.setdefault("git_enabled", False)
    kw.setdefault("lang", "off")
    return CodingAgent(backend=backend, project_root=str(tmp_path), **kw), backend


def _fake_pytest(monkeypatch, codes):
    calls = []
    pending = list(codes)

    def fake_run(cmd, **kwargs):
        calls.append(cmd)
        code = pending.pop(0)
        text = "1 passed" if code == 0 else "FAILED tests/test_a.py::test_a - AssertionError\n1 failed"
        return subprocess.CompletedProcess(cmd, code, stdout=text, stderr="")

    monkeypatch.setattr("orchestrator.agent.subprocess.run", fake_run)
    return calls


def test_a_fix_unblocks_run_tests(tmp_path, monkeypatch):
    """Two failures block run_tests; an edit is new code: its tests run (they used to stay blocked)."""
    calls = _fake_pytest(monkeypatch, [1, 1, 0])
    replies = [
        "Thought: create\nAction: write_file mod.py\nx = 1\n",
        "Thought: t\nAction: run_tests tests/a.py",
        "Thought: t\nAction: run_tests tests/b.py",
        "Thought: fix\nAction: write_file mod.py\nx = 2\n",
        "Thought: t\nAction: run_tests tests/a.py",
        "Thought: done\nAction: finish fixed",
    ]
    agent, _ = _agent(tmp_path, replies, allow_write=True, verify_after_write=False)
    result = agent.run("task")
    assert len(calls) == 3
    assert "1 passed" in result.steps[4].observation
    assert format_tool(result.steps[4]) == "→ run_tests tests/a.py: пройдены"
    assert result.final_answer == "fixed"


def test_the_chat_says_what_the_model_got(tmp_path, monkeypatch):
    _fake_pytest(monkeypatch, [1, 1])
    replies = [
        "Thought: create\nAction: write_file mod.py\nx = 1\n",
        "Thought: t\nAction: run_tests tests/a.py",
        "Thought: t\nAction: run_tests tests/b.py",
        "Thought: t\nAction: run_tests tests/c.py",  # blocked
        "Thought: done\nAction: finish gave up",
    ]
    agent, _ = _agent(tmp_path, replies, allow_write=True, verify_after_write=False)
    result = agent.run("task")
    shown = [format_tool(s) for s in result.steps]
    assert shown[1] == "→ run_tests tests/a.py: упали 1"
    assert "блокир" in result.steps[3].observation or "blocked" in result.steps[3].observation
    assert "пройдены" not in shown[3] and "заблокированы" in shown[3]


def test_a_repeated_read_says_what_to_do_next(tmp_path):
    (tmp_path / "t.py").write_text("assert 1\n", encoding="utf-8")
    replies = ["Thought: look\nAction: read_file t.py", "Thought: again\nAction: read_file t.py",
               "Thought: done\nAction: finish ok"]
    agent, _ = _agent(tmp_path, replies)
    result = agent.run("task")
    second = result.steps[1].observation
    assert "Nothing has changed since" in second and "edit_file" in second and "finish" in second
    assert format_tool(result.steps[1]).endswith("— повтор, ничего не изменилось")


def test_a_repeated_test_run_says_fix_first(tmp_path, monkeypatch):
    _fake_pytest(monkeypatch, [1])
    replies = ["Thought: create\nAction: write_file mod.py\nx = 1\n",
               "Thought: t\nAction: run_tests tests/a.py", "Thought: t\nAction: run_tests tests/a.py",
               "Thought: done\nAction: finish ok"]
    agent, _ = _agent(tmp_path, replies, allow_write=True, verify_after_write=False)
    result = agent.run("task")
    assert "no file has changed since" in result.steps[2].observation
    assert _tests_status(result.steps[2].observation).startswith("не запущены")


def test_test_statuses():
    assert _tests_status("Error: 2 failed, 3 passed in 0.1s") == "упали 2"
    assert _tests_status("Error: ERROR collecting tests/test_x.py\n1 error in 0.2s") == "ошибка при сборе тестов"
    assert _tests_status("Error: No tests were collected (pytest exit code 5)") == "тестов нет"
    assert _tests_status("5 passed in 0.1s") == "пройдены"
    assert format_tool(SimpleNamespace(tool_name="read_file", tool_args="a.py",
                                       observation="[futility breaker] read_file is blocked")).endswith(
        "— заблокировано (два провала подряд)")


def test_an_edit_inside_an_indented_body_keeps_the_indentation(tmp_path):
    """The live run: old «assert …» without its indentation, new two lines from column 0 — the second
    line fell out of the test function."""
    (tmp_path / "t.py").write_text("import os\n\n\ndef test_doc():\n    assert 'docs/a.md' in os.listdir('.')\n",
                                   encoding="utf-8")
    agent = CodingAgent(backend=MagicMock(), project_root=str(tmp_path), allow_write=True, git_enabled=False,
                        lang="off", verify_after_write=False)
    done = agent._tool_edit_file("t.py\n---OLD---\nassert 'docs/a.md' in os.listdir('.')\n---NEW---\n"
                                 "import os\nassert os.path.exists('docs/a.md')")
    assert done.success
    assert (tmp_path / "t.py").read_text(encoding="utf-8").endswith(
        "def test_doc():\n    import os\n    assert os.path.exists('docs/a.md')\n")
    # already indented new lines are left as they are
    agent._tool_edit_file("t.py\n---OLD---\nassert os.path.exists('docs/a.md')\n---NEW---\n"
                          "x = 1\n    assert x")
    assert (tmp_path / "t.py").read_text(encoding="utf-8").endswith("    x = 1\n    assert x\n")


def test_the_same_file_written_again_is_no_change_and_stops_the_loop(tmp_path, monkeypatch):
    """The FastAPI run of 2026-11-14: write_file docs/data_sources.md with the same text thirty times."""
    _fake_pytest(monkeypatch, [1] * 10)
    doc = "Thought: create\nAction: write_file docs/data_sources.md\n# Data Sources\n"
    replies = [doc] + [doc] * 10 + ["Thought: done\nAction: finish ok"]
    agent, backend = _agent(tmp_path, replies, allow_write=True, verify_after_write=False)
    result = agent.run("task")
    observations = [s.observation for s in result.steps]
    assert observations[0].startswith("Wrote docs/data_sources.md")
    assert "already has exactly this content" in observations[1]
    assert backend.generate.call_count <= 6  # stopped as idle, not after the iteration budget
    assert "Агент остановлен" in observations[-1]


def test_failures_from_before_the_changes_are_not_the_task(tmp_path, monkeypatch):
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_old.py").write_text("def test_old():\n    assert False\n", encoding="utf-8")
    outputs = iter(["FAILED tests/test_old.py::test_old - assert False\n1 failed"] * 3)

    def fake_run(cmd, **kwargs):
        return subprocess.CompletedProcess(cmd, 1, stdout=next(outputs), stderr="")

    monkeypatch.setattr("orchestrator.agent.subprocess.run", fake_run)
    replies = ["Thought: create\nAction: write_file backend/app/main.py\napp = 1\n",
               "Thought: t\nAction: run_tests", "Thought: done\nAction: finish ok"]
    agent, _ = _agent(tmp_path, replies, allow_write=True, verify_after_write=True, verify_mode="auto")
    result = agent.run("task")
    run = result.steps[1].observation
    assert "already failing BEFORE your changes" in run and "tests/test_old.py::test_old" in run
    assert _tests_status(run) == "падают только старые тесты — они падали ещё до правок агента"

