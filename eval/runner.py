"""Evaluation runner: HumanEval / MBPP / custom pass@k metrics.

Usage:
    python -m eval.runner --benchmark humaneval --samples 1
    python -m eval.runner --benchmark mbpp --samples 5 --temperature 0.8
    python -m eval.runner --benchmark custom --tasks-path eval/tasks.sample.json
"""
from __future__ import annotations

import json
import logging
import re
import textwrap
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

logger = logging.getLogger(__name__)


@dataclass
class EvalTask:
    task_id: str
    prompt: str
    canonical_solution: str = ""
    test: str = ""
    entry_point: str = ""
    imports: str = ""


@dataclass
class EvalSample:
    task_id: str
    completion: str
    passed: bool = False
    error: str = ""


@dataclass
class EvalResult:
    benchmark: str
    total: int = 0
    passed: int = 0
    failed: int = 0
    pass_at_1: float = 0.0
    pass_at_k: float = 0.0
    k: int = 1
    samples: list[EvalSample] = field(default_factory=list)

    @property
    def accuracy(self) -> float:
        if self.total == 0:
            return 0.0
        return self.passed / self.total


def extract_code(text: str) -> str:
    """Extract Python code from an LLM response.

    Handles fenced code blocks and raw code.
    """
    fenced = re.findall(r"```(?:python)?\s*\n(.*?)```", text, re.DOTALL)
    if fenced:
        return "\n\n".join(fenced)

    lines: list[str] = []
    in_code = False
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("def ") or stripped.startswith("class ") or stripped.startswith("import ") or stripped.startswith("from "):
            in_code = True
        if in_code:
            lines.append(line)
    if lines:
        return "\n".join(lines)

    return text


def run_code_safely(code: str, test_code: str, entry_point: str, timeout: int = 10) -> tuple[bool, str]:
    """Execute generated code + tests in a subprocess-like sandbox.

    Returns (passed, error_message).
    """
    full_code = code.strip()
    if not full_code:
        return False, "Empty code"

    if test_code and entry_point:
        full_code += "\n\n" + test_code.strip()
        try:
            if entry_point:
                full_code += f"\n\n_check = {entry_point}"
        except Exception:
            pass

    import subprocess
    import sys
    import tempfile

    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".py", delete=False, encoding="utf-8"
    ) as f:
        f.write(full_code)
        temp_path = f.name

    try:
        result = subprocess.run(
            [sys.executable, temp_path],
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        if result.returncode == 0:
            return True, ""
        return False, result.stderr.strip()[:500] if result.stderr else "Runtime error"
    except subprocess.TimeoutExpired:
        return False, f"Timeout ({timeout}s)"
    except Exception as exc:
        return False, str(exc)
    finally:
        Path(temp_path).unlink(missing_ok=True)


def compute_pass_at_k(results: list[bool], k: int) -> float:
    """Compute pass@k from a list of per-task pass/fail results.

    For multiple samples per task, results should be a flat list where
    each task contributes multiple entries. This simplified version
    treats each entry as an independent task.
    """
    if not results:
        return 0.0
    n_correct = sum(results)
    return n_correct / len(results)


def load_humaneval(limit: int | None = None) -> list[EvalTask]:
    """Load HumanEval tasks from HuggingFace datasets (streaming)."""
    try:
        from datasets import load_dataset

        ds = load_dataset("openai/openai_humaneval", split="test", streaming=True)
        tasks: list[EvalTask] = []
        for item in ds:
            tasks.append(EvalTask(
                task_id=item.get("task_id", ""),
                prompt=item.get("prompt", ""),
                canonical_solution=item.get("canonical_solution", ""),
                test=item.get("test", ""),
                entry_point=item.get("entry_point", ""),
            ))
            if limit and len(tasks) >= limit:
                break
        return tasks
    except Exception as exc:
        logger.error("Failed to load HumanEval: %s", exc)
        return []


def load_mbpp(limit: int | None = None) -> list[EvalTask]:
    """Load MBPP tasks from HuggingFace datasets (streaming)."""
    try:
        from datasets import load_dataset

        ds = load_dataset("google-research-datasets/mbpp", "sanitized", split="test", streaming=True)
        tasks: list[EvalTask] = []
        for item in ds:
            code = item.get("code", "")
            test_imports = item.get("test_imports", [])
            test_list = item.get("test_list", [])
            parts = list(test_imports or []) + list(test_list or [])
            test_code = "\n".join(parts) if parts else ""

            prompt = item.get("text", "")
            if not prompt:
                prompt = item.get("prompt", "")

            task_id = str(item.get("task_id", ""))
            tasks.append(EvalTask(
                task_id=task_id,
                prompt=prompt,
                canonical_solution=code,
                test=test_code,
                entry_point="",
            ))
            if limit and len(tasks) >= limit:
                break
        return tasks
    except Exception as exc:
        logger.error("Failed to load MBPP: %s", exc)
        return []


def load_custom_tasks(path: str | Path, limit: int | None = None) -> list[EvalTask]:
    """Load custom project-specific eval tasks from a JSON file.

    The file must contain either a JSON array of task objects or an object
    with a "tasks" key holding such an array. Each task requires "task_id"
    and "prompt"; optional fields: "canonical_solution", "test",
    "entry_point", "imports" (string or list of strings).

    Raises:
        ValueError: If the file is missing, malformed, or a task is invalid.
    """
    file_path = Path(path)
    if not file_path.is_file():
        raise ValueError(f"Tasks file not found: {file_path}")

    try:
        data = json.loads(file_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"Invalid JSON in {file_path}: {exc}") from exc

    if isinstance(data, dict):
        data = data.get("tasks")
    if not isinstance(data, list):
        raise ValueError(
            "Tasks file must contain a JSON array of tasks "
            "or an object with a 'tasks' array"
        )

    tasks: list[EvalTask] = []
    for index, item in enumerate(data):
        if not isinstance(item, dict):
            raise ValueError(f"Task #{index} must be a JSON object")

        task_id = str(item.get("task_id", "")).strip()
        prompt = str(item.get("prompt", "")).strip()
        if not task_id:
            raise ValueError(f"Task #{index} is missing 'task_id'")
        if not prompt:
            raise ValueError(f"Task {task_id!r} is missing 'prompt'")

        raw_imports = item.get("imports", "")
        if isinstance(raw_imports, list):
            imports = "\n".join(str(line) for line in raw_imports)
        else:
            imports = str(raw_imports)

        tasks.append(EvalTask(
            task_id=task_id,
            prompt=prompt,
            canonical_solution=str(item.get("canonical_solution", "")),
            test=str(item.get("test", "")),
            entry_point=str(item.get("entry_point", "")),
            imports=imports,
        ))
        if limit is not None and len(tasks) >= limit:
            break

    if not tasks:
        raise ValueError(f"No tasks found in {file_path}")

    return tasks


def run_evaluation(
    backend,
    benchmark: str = "humaneval",
    limit: int | None = None,
    samples_per_task: int = 1,
    temperature: float = 0.2,
    tasks_path: str | Path | None = None,
) -> EvalResult:
    """Run a full evaluation pass.

    Args:
        backend: ModelBackend instance.
        benchmark: "humaneval", "mbpp" or "custom".
        limit: Max number of tasks.
        samples_per_task: Number of samples per task for pass@k.
        temperature: Generation temperature.
        tasks_path: Path to a JSON tasks file (required for "custom").

    Returns:
        EvalResult with aggregated metrics.
    """
    if benchmark == "humaneval":
        tasks = load_humaneval(limit)
    elif benchmark == "mbpp":
        tasks = load_mbpp(limit)
    elif benchmark == "custom":
        if not tasks_path:
            logger.error("benchmark 'custom' requires --tasks-path")
            return EvalResult(benchmark=benchmark)
        try:
            tasks = load_custom_tasks(tasks_path, limit)
        except ValueError as exc:
            logger.error("Failed to load custom tasks: %s", exc)
            return EvalResult(benchmark=benchmark)
    else:
        logger.error("Unknown benchmark: %s", benchmark)
        return EvalResult(benchmark=benchmark)

    if not tasks:
        logger.error("No tasks loaded for benchmark: %s", benchmark)
        return EvalResult(benchmark=benchmark)

    from core.config import GenerationSettings

    gen = GenerationSettings(temperature=temperature, max_tokens=1024)

    result = EvalResult(benchmark=benchmark, total=len(tasks), k=samples_per_task)

    for i, task in enumerate(tasks):
        if benchmark in ("mbpp", "custom") and task.test:
            user_content = (
                f"{task.prompt}\n\nYour code should pass these tests:\n\n{task.test}"
            )
        else:
            user_content = task.prompt
        messages = [
            {"role": "system", "content": "You are Fluxion, an expert Python programmer. Complete the function."},
            {"role": "user", "content": user_content},
        ]

        combined_test = task.test
        if task.imports:
            combined_test = f"{task.imports}\n{task.test}"

        task_passed = False
        for _ in range(samples_per_task):
            try:
                raw = backend.generate(messages, gen=gen)
            except Exception as exc:
                logger.warning("Generation failed for %s: %s", task.task_id, exc)
                continue

            code = extract_code(raw)
            sample = EvalSample(task_id=task.task_id, completion=code)

            if benchmark == "humaneval":
                code_to_run = task.prompt + "\n" + code
            else:
                code_to_run = code

            passed, error = run_code_safely(
                code=code_to_run,
                test_code=combined_test,
                entry_point=task.entry_point,
            )

            sample.passed = passed
            sample.error = error
            result.samples.append(sample)

            if passed:
                task_passed = True

        if task_passed:
            result.passed += 1
        else:
            result.failed += 1

        if (i + 1) % 10 == 0:
            logger.info(
                "Progress: %d/%d  passed=%d  (%.1f%%)",
                i + 1, len(tasks), result.passed,
                result.passed / (i + 1) * 100,
            )

    all_passes = [s.passed for s in result.samples]
    result.pass_at_1 = compute_pass_at_k(all_passes, 1)
    result.pass_at_k = result.passed / result.total if result.total else 0.0

    logger.info(
        "Eval complete: %s  pass@1=%.1f%%  pass@%d=%.1f%%",
        benchmark, result.pass_at_1 * 100,
        result.k, result.pass_at_k * 100,
    )

    return result


def main():
    import argparse
    import sys

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%H:%M:%S",
    )
    parser = argparse.ArgumentParser(description="Fluxion — Model evaluation (HumanEval/MBPP/custom)")
    parser.add_argument("--benchmark", default="humaneval", choices=["humaneval", "mbpp", "custom"])
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--samples", type=int, default=1)
    parser.add_argument("--temperature", type=float, default=0.2)
    parser.add_argument("--output", default=None)
    parser.add_argument(
        "--tasks-path",
        default=None,
        help="Path to a JSON tasks file (required for --benchmark custom)",
    )
    args = parser.parse_args()

    from core.config import Settings
    from core.inference import OllamaBackend

    settings = Settings.load()
    backend = OllamaBackend(settings)

    if not backend.is_available():
        logger.error("Backend not available. Start Ollama and pull the model.")
        sys.exit(1)

    try:
        result = run_evaluation(
            backend=backend,
            benchmark=args.benchmark,
            limit=args.limit,
            samples_per_task=args.samples,
            temperature=args.temperature,
            tasks_path=args.tasks_path,
        )
    except ValueError as exc:
        logger.error("%s", exc)
        sys.exit(2)

    print(f"\n{'='*50}")
    print(f"Benchmark:  {result.benchmark}")
    print(f"Total:      {result.total}")
    print(f"Passed:     {result.passed}")
    print(f"Failed:     {result.failed}")
    print(f"pass@1:     {result.pass_at_1:.1%}")
    print(f"pass@{result.k}:      {result.pass_at_k:.1%}")
    print(f"{'='*50}")

    if args.output:
        out = Path(args.output)
        out.parent.mkdir(parents=True, exist_ok=True)
        data = {
            "benchmark": result.benchmark,
            "total": result.total,
            "passed": result.passed,
            "failed": result.failed,
            "pass_at_1": result.pass_at_1,
            "pass_at_k": result.pass_at_k,
            "k": result.k,
        }
        out.write_text(json.dumps(data, indent=2), encoding="utf-8")
        logger.info("Results saved to %s", out)


if __name__ == "__main__":
    main()
