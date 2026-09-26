"""The agent benchmark must itself be trustworthy.

* every task is solvable and its checks reject the untouched fixture;
* an oracle "model" that knows the reference solutions scores 100% through the
  real agent loop in every mode — proving harness, agents and checks are wired
  correctly (it says nothing about model quality);
* results resume after interruption and the report is produced.
"""
from __future__ import annotations

import json

import pytest

from eval.agent_bench import TASKS, build_report, main, run_one, validate_tasks


class OracleBackend:
    """Replays the reference solution of whichever task it is asked about.

    Stateless: the step index is the number of assistant turns so far, and the
    protocol (JSON vs ReAct text) is read from the system prompt."""

    def __init__(self, tasks):
        self.by_prompt = {t.prompt: t for t in tasks}

    def generate(self, messages, gen=None):
        user = messages[1]["content"]
        task = next(t for p, t in self.by_prompt.items() if p in user)
        json_mode = "exactly ONE JSON object" in messages[0]["content"]
        answer = task.solution.get("answer") or ("Done." if task.prompt.isascii() else "Готово.")
        script = [("write_file", {"path": p, "content": c}) for p, c in task.solution.get("files", {}).items()]
        script.append(("finish", {"answer": answer}))
        turn = sum(1 for m in messages if m["role"] == "assistant")
        tool, fields = script[min(turn, len(script) - 1)]
        if json_mode:
            return json.dumps({"thought": "oracle", "tool": tool, **fields}, ensure_ascii=False)
        if tool == "write_file":
            return f"Thought: oracle\nAction: write_file {fields['path']}\n{fields['content']}"
        return f"Thought: oracle\nAction: finish {fields['answer']}"

    def stream(self, messages, gen=None):
        yield ""

    def is_available(self):
        return True


def test_task_suite_is_valid():
    assert validate_tasks(TASKS) == []


def test_task_ids_unique_and_categories_covered():
    ids = [t.id for t in TASKS]
    assert len(ids) == len(set(ids))
    assert {t.category for t in TASKS} >= {"qa", "chat", "create", "fix", "refactor", "safety"}


@pytest.mark.parametrize("mode", ["baseline", "text", "json"])
def test_oracle_solves_everything(mode):
    backend = OracleBackend(TASKS)
    failed = []
    for task in TASKS:
        rec = run_one(task, mode, backend, max_iter=15)
        if not rec["passed"]:
            failed.append((task.id, rec["failed_checks"], rec.get("error")))
    assert failed == []


def test_run_resume_and_report(tmp_path):
    out = tmp_path / "run"
    subset = "create-calc,qa-long-file,chat-greeting"
    argv = ["--tasks", subset, "--modes", "text,json", "--out", str(out)]
    assert main(argv, backend=OracleBackend(TASKS)) == 0
    lines = (out / "results.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(lines) == 6
    assert main(argv, backend=OracleBackend(TASKS)) == 0          # resume: nothing re-run
    assert len((out / "results.jsonl").read_text(encoding="utf-8").splitlines()) == 6
    report = (out / "report.md").read_text(encoding="utf-8")
    assert "| text | 3/3 (100%)" in report and "| json | 3/3 (100%)" in report


def test_report_flags_differences():
    recs = [
        {"task": "a", "category": "fix", "mode": "baseline", "passed": False, "steps": 8, "seconds": 9,
         "tool_errors": 2, "no_action": 1, "repeats": 3, "failed_checks": ["x"], "tools": []},
        {"task": "a", "category": "fix", "mode": "json", "passed": True, "steps": 3, "seconds": 5,
         "tool_errors": 0, "no_action": 0, "repeats": 0, "failed_checks": [], "tools": [],
         "final_format": "text"},
    ]
    report = build_report(recs)
    assert "only json: 1 ['a']" in report
    assert "fell back to text" in report
