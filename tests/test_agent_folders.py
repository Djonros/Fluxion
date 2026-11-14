"""The agent makes folders: «make_dir», and «write_file frontend/» with no content (2026-11-14: the model
asked for backend/, frontend/, infra/… with write_file, every call failed with «content is empty» and the
futility breaker stopped the run)."""
from __future__ import annotations

from unittest.mock import MagicMock

from orchestrator import CodingAgent
from orchestrator.agent import TOOL_SPECS, build_json_system_prompt

from tests.test_agent_structured import J, JsonBackend


def _agent(tmp_path, backend, **kw):
    kw.setdefault("git_enabled", False)
    kw.setdefault("lang", "off")
    kw.setdefault("verify_after_write", False)
    return CodingAgent(backend=backend, project_root=str(tmp_path), **kw)


def test_the_skeleton_of_a_project(tmp_path):
    """The task of the screenshot: folders, .gitignore, README, docs/architecture.md."""
    backend = JsonBackend([
        J(thought="", tool="make_dir", path="backend"),
        J(thought="", tool="write_file", path="frontend/", content=""),  # what the model did
        J(thought="", tool="make_dir", path="infra/"),
        J(thought="", tool="make_dir", path="scripts"),
        J(thought="", tool="write_file", path="docs/architecture.md", content="# Архитектура\n"),
        J(thought="", tool="write_file", path=".gitignore", content="node_modules/\n.env\n"),
        J(thought="", tool="finish", answer="Готово"),
    ])
    result = _agent(tmp_path, backend, allow_write=True).run("Создай структуру проекта")
    assert result.final_answer == "Готово"
    for folder in ("backend", "frontend", "infra", "scripts", "docs"):
        assert (tmp_path / folder).is_dir(), folder
    assert all(s.observation.startswith(("Created folder", "Wrote")) for s in result.steps[:-1]), \
        [s.observation for s in result.steps]
    assert "git does not keep empty folders" in result.steps[0].observation


def test_text_format_make_dir_and_write_file_of_a_folder(tmp_path):
    agent = _agent(tmp_path, MagicMock(), allow_write=True)
    assert agent._tool_make_dir("src/app").success and (tmp_path / "src" / "app").is_dir()
    assert agent._tool_make_dir("src/app").output == "Already exist: src/app/."
    made = agent._tool_write_file("frontend/")  # no newline at all
    assert made.success and (tmp_path / "frontend").is_dir()


def test_an_empty_new_file_is_made_but_an_existing_file_is_not_emptied(tmp_path):
    agent = _agent(tmp_path, MagicMock(), allow_write=True)
    assert agent._tool_write_file("pkg/__init__.py\n").success
    assert (tmp_path / "pkg" / "__init__.py").read_text(encoding="utf-8") == ""
    (tmp_path / "main.py").write_text("print(1)\n", encoding="utf-8")
    kept = agent._tool_write_file("main.py\n")
    assert not kept.success and "left as it is" in kept.error
    assert (tmp_path / "main.py").read_text(encoding="utf-8") == "print(1)\n"


def test_make_dir_is_refused_where_writing_is(tmp_path):
    (tmp_path / "notes.txt").write_text("x", encoding="utf-8")
    agent = _agent(tmp_path, MagicMock(), allow_write=True)
    assert not agent._tool_make_dir("../outside").success
    assert not agent._tool_make_dir(".git/hooks2").success
    assert "is a file" in agent._tool_make_dir("notes.txt").error
    assert not _agent(tmp_path, MagicMock(), allow_write=False)._tool_make_dir("x").success
    assert not (tmp_path.parent / "outside").exists()


def test_the_trial_asks_before_a_folder(tmp_path):
    asked = []
    agent = _agent(tmp_path, MagicMock(), allow_write=True,
                   write_confirm=lambda path, kind: asked.append(path) or False)
    result = agent._tool_make_dir("backend")
    assert not result.success and asked == ["backend"] and not (tmp_path / "backend").exists()


def test_make_dir_is_offered_only_with_write(tmp_path):
    assert "make_dir" in _agent(tmp_path, JsonBackend([]), allow_write=True).prompt_tool_names()
    assert "make_dir" not in _agent(tmp_path, JsonBackend([])).prompt_tool_names()
    assert "make_dir" in TOOL_SPECS
    assert "make_dir" in build_json_system_prompt(["make_dir", "finish"], allow_write=True)


def test_several_folders_at_once(tmp_path):
    """The live run on qwen2.5-coder-7b: «make_dir backend frontend infra docs scripts» made one folder
    with that whole name."""
    agent = _agent(tmp_path, MagicMock(), allow_write=True)
    made = agent._tool_make_dir('backend frontend infra/ docs, scripts\n"my folder"')
    assert made.success and made.output.startswith("Created folders backend/, frontend/, infra/, docs/, scripts/, ")
    assert sorted(p.name for p in tmp_path.iterdir()) == ["backend", "docs", "frontend", "infra", "my folder", "scripts"]
    assert not (tmp_path / "backend frontend infra docs scripts").exists()


def test_one_unsafe_folder_makes_none(tmp_path):
    agent = _agent(tmp_path, MagicMock(), allow_write=True)
    assert not agent._tool_make_dir("ok ../outside").success
    assert not (tmp_path / "ok").exists()


def test_the_chat_shows_folders_in_words():
    from types import SimpleNamespace

    from orchestrator.presentation import format_tool

    step = SimpleNamespace(tool_name="write_file", tool_args="frontend/\n", observation="Created folder frontend/ (empty…)")
    assert format_tool(step) == "→ папка frontend/ создана"
    step = SimpleNamespace(tool_name="make_dir", tool_args="backend infra", observation="Created folders backend/, infra/")
    assert format_tool(step) == "→ папки backend/, infra/ — готово"
    step = SimpleNamespace(tool_name="make_dir", tool_args="../x", observation="Error: Unsafe or invalid path: ../x")
    assert format_tool(step).startswith("→ папки ../x/ — не удалось: Unsafe")


def test_no_tests_no_test_runs(tmp_path):
    """Without tests in the project the agent is told not to invent them (the live run wrote run_tests.py
    and a dummy test and reported «tests passed»)."""
    agent = _agent(tmp_path, JsonBackend([]), allow_write=True, verify_after_write=True, verify_mode="auto")
    assert "do not create tests or test runners" in agent._system_message()
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_a.py").write_text("def test_a():\n    pass\n", encoding="utf-8")
    with_tests = _agent(tmp_path, JsonBackend([]), allow_write=True, verify_after_write=True, verify_mode="auto")
    assert "do not create tests or test runners" not in with_tests._system_message()
    asked = _agent(tmp_path, JsonBackend([]), allow_write=True, verify_after_write=True, verify_mode="always")
    assert "do not create tests" not in asked._system_message()
