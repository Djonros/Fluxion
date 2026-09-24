"""Phase 13.4 tests: custom project-specific evals.

Covers load_custom_tasks (JSON parsing, validation, limits) and the eval
harness primitives (extract_code, run_code_safely, compute_pass_at_k) on
fixture tasks — no model required.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from eval import (
    compute_pass_at_k,
    extract_code,
    load_custom_tasks,
    run_code_safely,
    run_evaluation,
)

TASKS = [
    {
        "task_id": "custom-add",
        "prompt": "Write a function `add(a, b)` that returns the sum of two integers.",
        "canonical_solution": "def add(a, b):\n    return a + b\n",
        "test": "assert add(2, 3) == 5\nassert add(-1, 1) == 0\n",
        "entry_point": "add",
    },
    {
        "task_id": "custom-reverse",
        "prompt": "Write a function `rev(s)` that reverses a string.",
        "canonical_solution": "def rev(s):\n    return s[::-1]\n",
        "test": "assert rev('ab') == 'ba'\n",
        "entry_point": "rev",
        "imports": ["import math", "import re"],
    },
]


def write_tasks(tmp_path, payload, name="tasks.json"):
    path = tmp_path / name
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


# ═══════════════════════════════════════════════════════════════════════════════
#  load_custom_tasks
# ═══════════════════════════════════════════════════════════════════════════════

class TestLoadCustomTasks:
    def test_loads_array_format(self, tmp_path):
        path = write_tasks(tmp_path, TASKS)
        tasks = load_custom_tasks(path)
        assert [t.task_id for t in tasks] == ["custom-add", "custom-reverse"]

    def test_loads_tasks_key_format(self, tmp_path):
        path = write_tasks(tmp_path, {"tasks": TASKS})
        tasks = load_custom_tasks(path)
        assert len(tasks) == 2
        assert tasks[0].task_id == "custom-add"

    def test_imports_list_joined_with_newlines(self, tmp_path):
        path = write_tasks(tmp_path, TASKS)
        tasks = load_custom_tasks(path)
        assert tasks[1].imports == "import math\nimport re"

    def test_imports_string_kept_verbatim(self, tmp_path):
        payload = [dict(TASKS[0], imports="import json")]
        tasks = load_custom_tasks(write_tasks(tmp_path, payload))
        assert tasks[0].imports == "import json"

    def test_optional_fields_default_to_empty(self, tmp_path):
        payload = [{"task_id": "minimal", "prompt": "Do something."}]
        tasks = load_custom_tasks(write_tasks(tmp_path, payload))
        task = tasks[0]
        assert task.canonical_solution == ""
        assert task.test == ""
        assert task.entry_point == ""
        assert task.imports == ""

    def test_limit(self, tmp_path):
        path = write_tasks(tmp_path, TASKS)
        tasks = load_custom_tasks(path, limit=1)
        assert len(tasks) == 1
        assert tasks[0].task_id == "custom-add"

    def test_accepts_str_path(self, tmp_path):
        path = write_tasks(tmp_path, TASKS)
        tasks = load_custom_tasks(str(path))
        assert len(tasks) == 2

    def test_missing_file_raises(self, tmp_path):
        with pytest.raises(ValueError, match="not found"):
            load_custom_tasks(tmp_path / "nope.json")

    def test_invalid_json_raises(self, tmp_path):
        path = tmp_path / "bad.json"
        path.write_text("{not json", encoding="utf-8")
        with pytest.raises(ValueError, match="Invalid JSON"):
            load_custom_tasks(path)

    def test_non_array_payload_raises(self, tmp_path):
        with pytest.raises(ValueError, match="array"):
            load_custom_tasks(write_tasks(tmp_path, {"tasks": {"a": 1}}))

    def test_non_object_task_raises(self, tmp_path):
        with pytest.raises(ValueError, match="must be a JSON object"):
            load_custom_tasks(write_tasks(tmp_path, ["just a string"]))

    def test_missing_task_id_raises(self, tmp_path):
        payload = [{"prompt": "No id here."}]
        with pytest.raises(ValueError, match="task_id"):
            load_custom_tasks(write_tasks(tmp_path, payload))

    def test_blank_task_id_raises(self, tmp_path):
        payload = [{"task_id": "   ", "prompt": "Blank id."}]
        with pytest.raises(ValueError, match="task_id"):
            load_custom_tasks(write_tasks(tmp_path, payload))

    def test_missing_prompt_raises(self, tmp_path):
        payload = [{"task_id": "custom-x"}]
        with pytest.raises(ValueError, match="prompt"):
            load_custom_tasks(write_tasks(tmp_path, payload))

    def test_empty_tasks_array_raises(self, tmp_path):
        with pytest.raises(ValueError, match="No tasks"):
            load_custom_tasks(write_tasks(tmp_path, []))


# ═══════════════════════════════════════════════════════════════════════════════
#  Harness smoke (no model)
# ═══════════════════════════════════════════════════════════════════════════════

class TestHarnessSmoke:
    def test_canonical_solutions_pass_their_tests(self, tmp_path):
        tasks = load_custom_tasks(write_tasks(tmp_path, TASKS))
        for task in tasks:
            passed, error = run_code_safely(
                code=task.canonical_solution,
                test_code=task.test,
                entry_point=task.entry_point,
            )
            assert passed, f"{task.task_id} should pass: {error}"

    def test_broken_solution_fails(self):
        code = "def add(a, b):\n    return a - b\n"
        passed, error = run_code_safely(
            code=code,
            test_code="assert add(2, 3) == 5\n",
            entry_point="add",
        )
        assert not passed
        assert "AssertionError" in error

    def test_sample_file_loads_and_passes(self):
        sample = Path(__file__).resolve().parent.parent / "eval" / "tasks.sample.json"
        tasks = load_custom_tasks(sample)
        assert len(tasks) == 2
        for task in tasks:
            passed, error = run_code_safely(
                code=task.canonical_solution,
                test_code=task.test,
                entry_point=task.entry_point,
            )
            assert passed, f"{task.task_id} should pass: {error}"

    def test_extract_code_prefers_fenced_block(self):
        text = "Sure!\n```python\ndef add(a, b):\n    return a + b\n```\nDone."
        assert "def add" in extract_code(text)
        assert "Sure" not in extract_code(text)

    def test_compute_pass_at_k_empty(self):
        assert compute_pass_at_k([], 1) == 0.0

    def test_compute_pass_at_k_ratio(self):
        assert compute_pass_at_k([True, False, True, False], 1) == 0.5
        assert compute_pass_at_k([True, True], 1) == 1.0


# ═══════════════════════════════════════════════════════════════════════════════
#  run_evaluation wiring (no model: generation errors are tolerated)
# ═══════════════════════════════════════════════════════════════════════════════

class TestRunEvaluationCustom:
    def test_custom_without_tasks_path_returns_empty_result(self):
        result = run_evaluation(backend=None, benchmark="custom")
        assert result.benchmark == "custom"
        assert result.total == 0

    def test_custom_with_invalid_tasks_file_returns_empty_result(self, tmp_path):
        missing = tmp_path / "missing.json"
        result = run_evaluation(backend=None, benchmark="custom", tasks_path=missing)
        assert result.total == 0

    def test_custom_scores_zero_without_model(self, tmp_path):
        result = run_evaluation(
            backend=None,
            benchmark="custom",
            tasks_path=write_tasks(tmp_path, TASKS),
        )
        assert result.total == 2
        assert result.passed == 0
        assert result.failed == 2
        assert result.pass_at_1 == 0.0
