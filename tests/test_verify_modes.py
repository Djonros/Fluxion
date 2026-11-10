"""«Проверять правки тестами»: auto / always / never, the baseline of failing tests, no circles."""
from __future__ import annotations

from types import SimpleNamespace

from orchestrator.agent import CodingAgent, ToolResult, failed_tests


def agent(tmp_path, mode, tests=True):
    if tests:
        (tmp_path / "tests").mkdir()
        (tmp_path / "tests" / "test_a.py").write_text("def test_a():\n    assert True\n", encoding="utf-8")
    a = CodingAgent(backend=SimpleNamespace(), project_root=str(tmp_path), allow_write=True,
                    git_enabled=False, action_format="text", verify_mode=mode)
    return a


def fake_runs(a, outputs):
    """run_tests answers with *outputs* in turn: "" — passed, else the pytest output of a failure."""
    calls = []

    def run(args):
        out = outputs[min(len(calls), len(outputs) - 1)]
        calls.append(args)
        if not out:
            return ToolResult("run_tests", args, "1 passed", exit_code=0)
        return ToolResult("run_tests", args, out, False, "tests failed", exit_code=1)

    a._tool_run_tests = run
    a.tools["run_tests"] = run
    return calls


def write(a, name="mod.py", body="X = 1\n"):
    result = a._dispatch_tool("write_file", f"{name}\n{body}")
    a._note_effect("write_file", f"{name}\n{body}", result)
    return result


def test_failed_tests_are_read_from_pytest_output():
    out = "FAILED tests/test_a.py::test_x - assert 1 == 2\nERROR tests/test_b.py::test_y - boom\n1 failed"
    assert failed_tests(out) == {"tests/test_a.py::test_x", "tests/test_b.py::test_y"}


def test_auto_without_tests_checks_the_syntax_quietly(tmp_path):
    a = agent(tmp_path, "auto", tests=False)
    calls = fake_runs(a, [""])
    write(a)
    assert a._try_verify_gate() is None and calls == []  # no test run at all
    assert a._quiet_verify and a._tests_ok


def test_never_does_not_check(tmp_path):
    a = agent(tmp_path, "never")
    calls = fake_runs(a, [""])
    write(a)
    assert a._try_verify_gate() is None and calls == []


def test_old_failures_are_not_the_agents_doing(tmp_path):
    old = "FAILED tests/test_a.py::test_old - broken before\n1 failed"
    a = agent(tmp_path, "auto")
    calls = fake_runs(a, [old, old])  # the baseline, then the check: the same old failure
    write(a)
    assert calls == [""]  # the baseline ran before the first change
    note = a._try_verify_gate()
    assert "Новых падений нет" in note and "test_old" in note and a._tests_ok


def test_new_failures_must_be_fixed_and_the_same_result_twice_stops(tmp_path):
    new = "FAILED tests/test_a.py::test_new - broken by the change\n1 failed"
    a = agent(tmp_path, "always")
    a.verify_budget = 5
    fake_runs(a, ["", new, new])  # baseline passes; two checks fail the same way
    write(a)
    first = a._try_verify_gate()
    assert "Новые падения после твоих правок: tests/test_a.py::test_new" in first
    second = a._try_verify_gate()
    assert "Второй прогон подряд" in second and "Не повторяй" in second
    assert a._try_verify_gate() is None  # no third round


def test_a_quick_same_size_edit_is_not_run_from_stale_bytecode(tmp_path):
    """The baseline imports the module; an edit within the same second to the same size must still count."""
    (tmp_path / "tests").mkdir()
    (tmp_path / "calc.py").write_text("def f():\n    return 1\n", encoding="utf-8")
    (tmp_path / "tests" / "test_calc.py").write_text(
        "import sys, os\nsys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))\n"
        "from calc import f\n\n\ndef test_f():\n    assert f() == 2\n", encoding="utf-8")
    a = CodingAgent(backend=SimpleNamespace(), project_root=str(tmp_path), allow_write=True,
                    git_enabled=False, action_format="text", verify_mode="always")
    a._take_test_baseline()
    assert a._baseline_failures == {"tests/test_calc.py::test_f"}
    write(a, "calc.py", "def f():\n    return 2\n")  # the same size, at once
    assert a._try_verify_gate() is None or a._tests_ok
    assert a._tests_ok
    assert not (tmp_path / "__pycache__").exists()
