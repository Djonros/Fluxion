"""Agent step presentation and chat rendering (the "answers twice" report)."""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

from orchestrator import CodingAgent
from orchestrator.presentation import (
    MAX_CODE_LINES,
    format_agent_step,
    format_tool,
    render_chat_html,
)


def step(tool="", args="", thought="", observation="", is_final=False, iteration=1):
    return SimpleNamespace(tool_name=tool, tool_args=args, thought=thought,
                           observation=observation, is_final=is_final, iteration=iteration)


ANSWER = "Скрипт search.py готов. Запуск: python search.py D:\\ .txt"


class TestNoDuplicateAnswer:
    def test_finish_step_does_not_repeat_the_answer(self):
        text = format_agent_step(step("finish", ANSWER, thought="Готово", is_final=True,
                                      observation="Task complete."))
        assert ANSWER not in text
        assert "finish" not in text
        assert text == "Готово"

    def test_language_gate_step_hides_wrong_language_answer(self):
        text = format_agent_step(step("", "", thought="The script is written.",
                                      observation="[language gate] Ответ получен на другом языке."))
        assert "The script is written" not in text
        assert "Перевожу" in text

    def test_whole_screenshot_scenario_shows_answer_once(self):
        """Steps from the user's screenshot, joined the way the chat does it."""
        code = "import os\n\nfor root, _, files in os.walk('.'):\n    print(root)\n"
        steps = [
            step("write_file", f"script.py\n{code}", thought="Создам скрипт."),
            step("run_tests", "(auto-verify)", thought="Скрипт написан.",
                 observation="[verification gate] Изменены: script.py. pytest-тестов в проекте "
                             "нет; проверен только синтаксис: OK"),
            step("", "", thought="The script is written.",
                 observation="[language gate] Ответ получен на другом языке."),
            step("finish", ANSWER, thought="Готово.", is_final=True, observation="Task complete."),
        ]
        chat = "\n".join(t for t in (format_agent_step(s) for s in steps) if t) + "\n\n" + ANSWER
        assert chat.count(ANSWER) == 1
        assert "→ автопроверка: тестов нет, проверен только синтаксис" in chat
        assert "```python\nimport os" in chat


class TestToolLines:
    def test_write_file_shows_code_block_and_line_count(self):
        text = format_tool(step("write_file", "app/calc.py\n```python\ndef add(a, b):\n    return a + b\n```\n",
                                observation="Wrote app/calc.py"))
        assert text.startswith("→ write_file app/calc.py (2 строки)")
        assert "```python\ndef add(a, b):\n    return a + b\n```" in text

    def test_long_file_truncated(self):
        code = "\n".join(f"x{i} = {i}" for i in range(MAX_CODE_LINES + 25))
        text = format_tool(step("write_file", f"big.py\n{code}"))
        assert "ещё 25 строк" in text
        assert f"x{MAX_CODE_LINES + 1} =" not in text

    def test_failed_write_is_short(self):
        text = format_tool(step("write_file", "../x.py\nprint(1)", observation="Error: Unsafe or invalid path: ../x.py"))
        assert "не удалось" in text and "```" not in text

    def test_edit_file_as_diff(self):
        text = format_tool(step("edit_file", "calc.py\n---OLD---\n    return a - b\n---NEW---\n    return a + b\n",
                                observation="Edited calc.py (1 replacement)."))
        assert text.startswith("→ edit_file calc.py")
        assert "```diff\n-     return a - b\n+     return a + b\n```" in text

    def test_run_tests_status(self):
        assert format_tool(step("run_tests", "", observation="3 passed")) == "→ run_tests: пройдены"
        assert format_tool(step("run_tests", "tests/t.py", observation="Error: Tests failed (pytest exit code 1)")) \
            == "→ run_tests tests/t.py: есть падения"
        assert format_tool(step("run_tests", "(auto-verify)", observation="[verification gate] ... FAILED:")) \
            == "→ автопроверка: есть ошибки — агент исправляет"

    def test_other_tools_one_line(self):
        assert format_tool(step("grep", "def  main\n src")) == "→ grep def main src"
        assert len(format_tool(step("web_search", "q" * 500))) < 220

    def test_code_fence_in_content_cannot_break_block(self):
        text = format_tool(step("write_file", "README.md\nUse:\n```\npip install x\n```\n"))
        assert text.count("```") == 2


class TestRenderChatHtml:
    def test_code_block_is_monospace_and_escaped(self):
        out = render_chat_html("Вот код:\n```python\nif a < b:\n    print('<b>')\n```\nГотово")
        assert "<pre" in out and "monospace" in out
        assert "if a &lt; b:" in out and "&lt;b&gt;" in out
        assert "```" not in out
        assert out.endswith("Готово")

    def test_unclosed_fence_while_streaming(self):
        out = render_chat_html("Пишу:\n```python\nx = 1")
        assert "<pre" in out and "x = 1" in out

    def test_inline_code_links_and_newlines(self):
        out = render_chat_html("Запусти `python a.py`\nсм. https://docs.python.org/3/")
        assert "<code" in out and "python a.py" in out
        assert 'href="https://docs.python.org/3/"' in out
        assert "<br>" in out

    def test_plain_text_escaped(self):
        assert render_chat_html("<script>x</script>") == "&lt;script&gt;x&lt;/script&gt;"
        assert render_chat_html("") == ""


class TestAgentLanguageInstruction:
    def test_system_prompt_asks_for_user_language(self, tmp_path):
        backend = MagicMock()
        backend.generate.side_effect = ["Action: finish ок"]
        agent = CodingAgent(backend=backend, project_root=str(tmp_path), git_enabled=False, lang="auto")
        agent.run("нужно написать скрипт, который ищет файлы по расширению")
        system = backend.generate.call_args[0][0][0]["content"]
        assert "Write your thoughts and the final answer in Russian" in system

    def test_no_instruction_when_language_off(self, tmp_path):
        backend = MagicMock()
        backend.generate.side_effect = ["Action: finish ok"]
        agent = CodingAgent(backend=backend, project_root=str(tmp_path), git_enabled=False, lang="off")
        agent.run("write a script")
        assert "Language:" not in backend.generate.call_args[0][0][0]["content"]

    def test_resolve_target_off(self):
        from core.language import resolve_target

        assert resolve_target("off", "привет, напиши скрипт") is None
        assert resolve_target("auto", "привет, напиши скрипт для поиска файлов") == "ru"
        assert resolve_target("en", "привет") == "en"

    def test_prompts_require_useful_final_answer(self, tmp_path):
        for fmt in ("text", "json"):
            agent = CodingAgent(backend=MagicMock(), project_root=str(tmp_path), git_enabled=False,
                                allow_write=True, action_format=fmt)
            assert "how to run" in agent.system_prompt
