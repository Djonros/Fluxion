import pytest

from core.language import detect_language, matches_language, resolve_target
from orchestrator.agent import CodingAgent


@pytest.fixture
def stub_backend_switches_on_correction():
    class StubBackend:
        def generate(self, messages):
            corrected = any(
                "[language gate]" in m.get("content", "") for m in messages
            )
            if corrected:
                return "Thought: Готово\nAction: finish Готово! Файл описан выше, точка входа."
            return "Thought: done\nAction: finish The main file contains the entry point."

    return StubBackend()


def test_detect_skips_code():
    assert detect_language("```python\ndef foo(): pass\n```\nГотово!") == "ru"


def test_detect_mixed_is_none():
    assert detect_language("The function returns значение") is None


def test_agent_language_gate(tmp_path, stub_backend_switches_on_correction):
    agent = CodingAgent(
        backend=stub_backend_switches_on_correction,
        project_root=str(tmp_path),
        lang="ru",
    )
    result = agent.run("объясни main.py")
    assert result.success
    assert detect_language(result.final_answer) == "ru"
    assert matches_language(result.final_answer, "ru")
    assert resolve_target("auto", "ответь по-русски") == "ru"
