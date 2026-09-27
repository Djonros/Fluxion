"""Agent benchmark: run the task suite against a real model in several modes.

Modes
  baseline — frozen copy of the agent before the review fixes
  text     — current agent, classic ReAct text protocol
  json     — current agent, JSON-schema constrained tool calls

Usage
  python -m eval.agent_bench --validate                 # check the tasks themselves
  python -m eval.agent_bench --modes baseline,text,json --repeats 3
  python -m eval.agent_bench --report results/agent_bench/<run>

Results are appended to ``results.jsonl`` after every task, so an interrupted
run resumes where it stopped when started again with the same ``--out``.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import statistics
import subprocess
import sys
import tempfile
import time
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path

from .tasks import TASKS, Task

MODES = ("baseline", "text", "json")
_CHECK_TIMEOUT = 60
_REPEAT_MARK = "already executed"


# ── fixtures ─────────────────────────────────────────────────────────────────

def materialize(files: dict[str, str], root: Path) -> None:
    for rel, content in files.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", encoding="utf-8", newline="") as fh:  # keep CRLF fixtures
            fh.write(content)


# ── checks ───────────────────────────────────────────────────────────────────

@dataclass
class CheckOutcome:
    ok: bool
    detail: str = ""


def run_checks(task: Task, root: Path, answer: str, tool_calls: int) -> list[CheckOutcome]:
    outcomes: list[CheckOutcome] = []
    low = (answer or "").lower()
    for check in task.checks:
        kind = check["type"]
        if kind == "python":
            env = dict(os.environ, PYTHONPATH=str(root), PYTHONDONTWRITEBYTECODE="1")
            try:
                proc = subprocess.run(
                    [sys.executable, "-c", check["code"]], cwd=root, env=env,
                    capture_output=True, text=True, timeout=_CHECK_TIMEOUT,
                    encoding="utf-8", errors="replace",
                )
                ok = proc.returncode == 0
                detail = "" if ok else (proc.stderr or proc.stdout).strip().splitlines()[-1:]
                outcomes.append(CheckOutcome(ok, " ".join(detail) if detail else ""))
            except subprocess.TimeoutExpired:
                outcomes.append(CheckOutcome(False, "check timed out"))
        elif kind == "answer":
            any_ = check.get("any") or []
            all_ = check.get("all") or []
            none_ = check.get("none") or []
            ok = bool(answer.strip())
            if any_:
                ok = ok and any(s.lower() in low for s in any_)
            ok = ok and all(s.lower() in low for s in all_)
            ok = ok and not any(s.lower() in low for s in none_)
            outcomes.append(CheckOutcome(ok, "" if ok else f"answer mismatch: {answer[:120]!r}"))
        elif kind == "unchanged":
            changed = []
            for rel in check["paths"]:
                path = root / rel
                expected = task.files[rel].encode("utf-8")
                if not path.is_file() or path.read_bytes() != expected:
                    changed.append(rel)
            outcomes.append(CheckOutcome(not changed, f"modified: {changed}" if changed else ""))
        elif kind == "no_tools":
            ok = tool_calls == 0 and bool(answer.strip())
            outcomes.append(CheckOutcome(ok, "" if ok else f"{tool_calls} tool call(s)"))
        elif kind == "lang":
            got = _dominant_script(answer)
            ok = got == check["lang"]
            outcomes.append(CheckOutcome(ok, "" if ok else f"language={got}"))
        else:
            outcomes.append(CheckOutcome(False, f"unknown check {kind}"))
    return outcomes


def _dominant_script(text: str) -> str | None:
    """"ru"/"en" by majority of letters outside code — robust for short
    replies that mix scripts ("Я Fluxion, ассистент…"), unlike the product's
    detector which abstains on short mixed text."""
    from core.language import _natural_text

    natural = _natural_text(text)
    cyr = sum(1 for ch in natural if "\u0400" <= ch <= "\u04FF")
    lat = sum(1 for ch in natural if ch.isascii() and ch.isalpha())
    if cyr + lat == 0:
        return None
    return "ru" if cyr >= lat else "en"


# ── agent construction ──────────────────────────────────────────────────────

def make_agent(mode: str, backend, root: Path, task: Task, max_iter: int):
    common = dict(
        backend=backend, project_root=str(root), max_iterations=max_iter,
        allow_write=task.allow_write, git_enabled=False, lang="auto",
    )
    if mode == "baseline":
        from .baseline_agent import CodingAgent as BaselineAgent

        return BaselineAgent(**common)
    from orchestrator.agent import CodingAgent

    return CodingAgent(**common, action_format=mode)


def run_one(
    task: Task,
    mode: str,
    backend,
    max_iter: int,
    keep: bool = False,
    timeout: float | None = None,
    progress=None,
) -> dict:
    """Run *task* once.  *timeout* (seconds) is checked after every agent
    step — a model call in progress cannot be interrupted, but each call is
    bounded by max_tokens.  *progress(step, elapsed)* is called per step."""
    root = Path(tempfile.mkdtemp(prefix=f"fxbench-{task.id}-"))
    materialize(task.files, root)
    record: dict = {"task": task.id, "category": task.category, "mode": mode}
    started = time.perf_counter()
    steps: list = []
    answer = ""
    timed_out = False
    try:
        agent = make_agent(mode, backend, root, task, max_iter)
        gen = agent.run_iter(task.prompt)
        result = None
        while True:
            try:
                step = next(gen)
            except StopIteration as stop:
                result = stop.value
                break
            steps.append(step)
            elapsed = time.perf_counter() - started
            if progress is not None:
                progress(step, elapsed)
            if timeout and elapsed > timeout and not step.is_final:
                timed_out = True
                gen.close()
                break
        if result is not None and result.success:
            answer = result.final_answer
        record.update(
            agent_success=bool(result is not None and result.success),
            verification=getattr(result, "verification", "") if result is not None else "",
            final_format=getattr(agent, "action_format", "text"),
            timed_out=timed_out,
        )
    except Exception as exc:  # a crash is a failed task, not a failed benchmark
        record.update(agent_success=False, error=f"{type(exc).__name__}: {exc}", timed_out=False)
    record["seconds"] = round(time.perf_counter() - started, 1)

    tool_steps = [s for s in steps if s.tool_name and s.tool_name != "finish"]
    record.update(
        steps=len(steps),
        tool_calls=len(tool_steps),
        tool_errors=sum(1 for s in tool_steps if (s.observation or "").startswith("Error:")),
        no_action=sum(1 for s in steps if not s.tool_name and not s.is_final
                      and not (s.observation or "").startswith(("[language gate]", "Backend error"))),
        repeats=sum(1 for s in steps if _REPEAT_MARK in (s.observation or "")),
        backend_error=any((s.observation or "").startswith("Backend error") for s in steps),
        tools=[s.tool_name for s in tool_steps],
    )
    outcomes = run_checks(task, root, answer, len(tool_steps))
    record["passed"] = all(o.ok for o in outcomes) and not timed_out
    record["failed_checks"] = [o.detail or task.checks[i]["type"]
                               for i, o in enumerate(outcomes) if not o.ok]
    if timed_out:
        record["failed_checks"].insert(0, f"timeout after {record['seconds']:.0f}s")
    record["answer"] = (answer or "")[:400]
    if keep:
        record["workdir"] = str(root)
    else:
        shutil.rmtree(root, ignore_errors=True)
    return record


# ── validation of the task suite itself ────────────────────────────────────

def validate_tasks(tasks: list[Task]) -> list[str]:
    """Every task: the untouched fixture must FAIL, the reference solution must PASS."""
    problems: list[str] = []
    for task in tasks:
        root = Path(tempfile.mkdtemp(prefix=f"fxval-{task.id}-"))
        try:
            materialize(task.files, root)
            before = run_checks(task, root, "", tool_calls=0)
            if all(o.ok for o in before):
                problems.append(f"{task.id}: checks pass WITHOUT any work (task is trivial)")
            materialize(task.solution.get("files", {}), root)
            after = run_checks(task, root, task.solution.get("answer", ""), tool_calls=0)
            bad = [o.detail or task.checks[i]["type"] for i, o in enumerate(after) if not o.ok]
            if bad:
                problems.append(f"{task.id}: reference solution fails: {bad}")
        finally:
            shutil.rmtree(root, ignore_errors=True)
    return problems


# ── reporting ────────────────────────────────────────────────────────────────

def load_records(out: Path) -> list[dict]:
    path = out / "results.jsonl"
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _pct(n: int, d: int) -> str:
    return f"{100 * n / d:.0f}%" if d else "—"


def build_report(records: list[dict], meta: dict | None = None) -> str:
    modes = [m for m in MODES if any(r["mode"] == m for r in records)]
    by_mode: dict[str, list[dict]] = defaultdict(list)
    for r in records:
        by_mode[r["mode"]].append(r)

    lines = ["# Fluxion agent benchmark", ""]
    if meta:
        lines += [f"- {k}: {v}" for k, v in meta.items()] + [""]
    n_tasks = len({r["task"] for r in records})
    lines += [
        f"Tasks: {n_tasks}. Runs: {len(records)}. A task counts as solved when all its checks pass.",
        "",
        "| Mode | Solved | Avg steps | Median time, s | Tool errors / run | Format errors / run | Repeat blocks / run | Crashes | Timeouts |",
        "|------|--------|-----------|----------------|-------------------|---------------------|---------------------|---------|----------|",
    ]
    for m in modes:
        rs = by_mode[m]
        solved = sum(r["passed"] for r in rs)
        lines.append(
            f"| {m} | {solved}/{len(rs)} ({_pct(solved, len(rs))}) "
            f"| {statistics.mean(r['steps'] for r in rs):.1f} "
            f"| {statistics.median(r['seconds'] for r in rs):.0f} "
            f"| {statistics.mean(r['tool_errors'] for r in rs):.2f} "
            f"| {statistics.mean(r['no_action'] for r in rs):.2f} "
            f"| {statistics.mean(r['repeats'] for r in rs):.2f} "
            f"| {sum(1 for r in rs if r.get('error') or r.get('backend_error'))} "
            f"| {sum(1 for r in rs if r.get('timed_out'))} |"
        )
    fallback = [r for r in by_mode.get("json", []) if r.get("final_format") == "text"]
    if fallback:
        lines += ["", f"⚠ json mode fell back to text in {len(fallback)} run(s): the backend "
                      "rejected the schema, so those runs measure the text protocol."]

    cats = sorted({r["category"] for r in records})
    lines += ["", "## By category", "",
              "| Category | " + " | ".join(modes) + " |",
              "|---|" + "---|" * len(modes)]
    for c in cats:
        cells = []
        for m in modes:
            rs = [r for r in by_mode[m] if r["category"] == c]
            cells.append(f"{sum(r['passed'] for r in rs)}/{len(rs)}")
        lines.append(f"| {c} | " + " | ".join(cells) + " |")

    # per-task majority vote across repeats
    solved_by: dict[tuple[str, str], float] = {}
    for m in modes:
        per_task: dict[str, list[bool]] = defaultdict(list)
        for r in by_mode[m]:
            per_task[r["task"]].append(r["passed"])
        for t, vals in per_task.items():
            solved_by[(t, m)] = sum(vals) / len(vals)

    lines += ["", "## Pairwise (tasks solved in the majority of repeats)", ""]
    for i, a in enumerate(modes):
        for b in modes[i + 1:]:
            tasks = sorted({t for (t, m) in solved_by if m in (a, b)})
            wa = [t for t in tasks if solved_by.get((t, a), 0) > 0.5 >= solved_by.get((t, b), 0)]
            wb = [t for t in tasks if solved_by.get((t, b), 0) > 0.5 >= solved_by.get((t, a), 0)]
            lines.append(f"- **{a} vs {b}**: only {a}: {len(wa)} {wa}; only {b}: {len(wb)} {wb}")
    lines += [
        "",
        "With ~35 tasks a difference of a few tasks can be noise; trust differences that "
        "hold across repeats and show up in the pairwise lists.",
        "",
        "## Per task",
        "",
        "| Task | Category | " + " | ".join(modes) + " |",
        "|---|---|" + "---|" * len(modes),
    ]
    task_cat = {r["task"]: r["category"] for r in records}
    for t in sorted(task_cat, key=lambda x: (task_cat[x], x)):
        cells = []
        for m in modes:
            rs = [r for r in by_mode[m] if r["task"] == t]
            if not rs:
                cells.append("")
                continue
            ok = sum(r["passed"] for r in rs)
            mark = "✅" if ok == len(rs) else ("❌" if ok == 0 else "⚠️")
            cells.append(f"{mark} {ok}/{len(rs)}")
        lines.append(f"| {t} | {task_cat[t]} | " + " | ".join(cells) + " |")

    failures = [r for r in records if not r["passed"]]
    if failures:
        lines += ["", "## Failure details", ""]
        for r in failures:
            why = "; ".join(r.get("failed_checks") or []) or r.get("error", "")
            lines.append(f"- `{r['task']}` [{r['mode']}] steps={r['steps']} tools={r.get('tools')} — {why[:200]}")
    return "\n".join(lines) + "\n"


# ── main ─────────────────────────────────────────────────────────────────────

def _select(tasks: list[Task], ids: str, categories: str, limit: int | None) -> list[Task]:
    if ids:
        wanted = {x.strip() for x in ids.split(",")}
        tasks = [t for t in tasks if t.id in wanted]
    if categories:
        cats = {x.strip() for x in categories.split(",")}
        tasks = [t for t in tasks if t.category in cats]
    return tasks[:limit] if limit else tasks


def main(argv: list[str] | None = None, backend=None) -> int:
    ap = argparse.ArgumentParser(prog="python -m eval.agent_bench", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--modes", default="baseline,text,json")
    ap.add_argument("--repeats", type=int, default=1)
    ap.add_argument("--tasks", default="", help="comma-separated task ids")
    ap.add_argument("--category", default="", help="qa,chat,create,fix,refactor,safety")
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--max-iter", type=int, default=15)
    ap.add_argument("--task-timeout", type=float, default=900,
                    help="seconds per task, checked after each step (0 = no limit); "
                         "a timed-out task counts as failed")
    ap.add_argument("--config", default=None, help="config.yaml (default: AI_AGENT_CONFIG or config/config.yaml)")
    ap.add_argument("--out", default=None, help="results dir (reuse to resume)")
    ap.add_argument("--keep", action="store_true", help="keep task work dirs for inspection")
    ap.add_argument("--validate", action="store_true", help="check the task suite, no model needed")
    ap.add_argument("--report", default=None, help="only rebuild report.md for a results dir")
    ap.add_argument("--list", action="store_true", help="list tasks")
    args = ap.parse_args(argv)

    tasks = _select(TASKS, args.tasks, args.category, args.limit)

    if args.list:
        for t in tasks:
            print(f"{t.id:28} {t.category:9} {'W' if t.allow_write else 'R'}  {t.prompt[:70]}")
        return 0

    if args.validate:
        problems = validate_tasks(tasks)
        for p in problems:
            print("✗", p)
        print(f"{len(tasks) - len({p.split(':')[0] for p in problems})}/{len(tasks)} tasks valid")
        return 1 if problems else 0

    if args.report:
        out = Path(args.report)
        report = build_report(load_records(out), _load_meta(out))
        (out / "report.md").write_text(report, encoding="utf-8")
        print(report)
        return 0

    modes = [m.strip() for m in args.modes.split(",") if m.strip()]
    unknown = set(modes) - set(MODES)
    if unknown:
        ap.error(f"unknown modes: {sorted(unknown)}")

    out = Path(args.out or f"results/agent_bench/{time.strftime('%Y%m%d-%H%M%S')}")
    out.mkdir(parents=True, exist_ok=True)

    if backend is None:
        from core.backend_factory import BackendFactory
        from core.config import Settings

        settings = Settings.load(args.config)
        # Same model resolution as the desktop app: with the llama.cpp backend
        # and no gguf_path, use the model installed via the "Models" page.
        try:
            from desktop_browser.engine import _wire_gguf_paths

            _wire_gguf_paths(settings)
        except Exception as exc:  # optional: desktop package may be absent
            print(f"(model catalog lookup skipped: {exc})")
        try:
            backend = BackendFactory.create(settings)
        except RuntimeError as exc:
            print(f"\n[ОШИБКА] Модель недоступна.\n{exc}\n")
            print(
                "Проверьте config/config.yaml: для встроенного движка нужна строка\n"
                "  backend: llama_cpp\n"
                "и модель, скачанная на странице «Модели» приложения, либо\n"
                "  gguf_path: C:/путь/к/модели.gguf\n"
                "Для Ollama: backend: ollama и запущенный Ollama."
            )
            return 3
        print(f"Движок: {type(backend).__name__}  модель: "
              f"{getattr(backend, 'model_path', '') or getattr(settings, 'model', '')}")
        meta = {
            "backend": type(backend).__name__,
            "model": getattr(backend, "model_path", "") or getattr(settings, "model", ""),
            "generation": vars(settings.generation),
        }
    else:
        meta = {"backend": type(backend).__name__}
    meta.update(modes=modes, repeats=args.repeats, max_iter=args.max_iter,
                task_timeout=args.task_timeout,
                started=time.strftime("%Y-%m-%d %H:%M"))
    meta_path = out / "meta.json"
    if not meta_path.exists():
        meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2, default=str), encoding="utf-8")

    done = {(r["task"], r["mode"], r.get("repeat", 0)) for r in load_records(out)}
    plan = [(t, m, k) for k in range(args.repeats) for t in tasks for m in modes
            if (t.id, m, k) not in done]
    total = len(plan)
    print(f"{len(tasks)} tasks × {len(modes)} modes × {args.repeats} repeats; "
          f"{len(done)} done, {total} to run. Results: {out}")

    durations: list[float] = []
    with (out / "results.jsonl").open("a", encoding="utf-8") as fh:
        for i, (task, mode, rep) in enumerate(plan, 1):
            print(f"[{i}/{total}] {task.id} · {mode} ...", flush=True)
            rec = run_one(task, mode, backend, args.max_iter, keep=args.keep,
                          timeout=args.task_timeout or None, progress=_print_step)
            rec["repeat"] = rep
            fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
            fh.flush()
            durations.append(rec["seconds"])
            eta = statistics.mean(durations) * (total - i) / 60
            mark = "✓" if rec["passed"] else "✗"
            print(f"        {mark} {task.id:26} {mode:8} {rec['steps']:2} steps "
                  f"{rec['seconds']:6.1f}s  ETA {eta:5.1f} min"
                  + ("" if rec["passed"] else f"  ({'; '.join(rec['failed_checks'])[:80]})"))

    report = build_report(load_records(out), _load_meta(out))
    (out / "report.md").write_text(report, encoding="utf-8")
    print("\n" + report.split("## By category")[0])
    print(f"Full report: {out / 'report.md'}")
    return 0


def _print_step(step, elapsed: float) -> None:
    """One line per agent step, so a long task visibly makes progress."""
    what = step.tool_name or ("ответ" if step.is_final else "без действия")
    if step.is_final:
        what = "finish"
    print(f"        шаг {step.iteration:2}: {what:<12} {elapsed:6.0f}s", flush=True)


def _load_meta(out: Path) -> dict | None:
    path = out / "meta.json"
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else None
