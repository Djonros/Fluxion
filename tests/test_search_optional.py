"""SearXNG (Docker) is opt-in: built-in web search works without it."""
from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

from web.search_backend import OptionalSearxngProvider, SearchProvider
from web.searxng import SearchResult

ROOT = Path(__file__).resolve().parents[1]


def _result(tag):
    return SearchResult(title=tag, url=f"https://{tag}.example", snippet=tag)


class FakeClient:
    base_url = "http://localhost:8080"

    def __init__(self, alive=True, results=None, error=None):
        self.alive, self.results, self.error = alive, results, error
        self.probes = 0
        self.queries = 0

    def is_alive(self):
        self.probes += 1
        return self.alive

    def search(self, query, max_results=10):
        self.queries += 1
        if self.error:
            raise self.error
        return self.results if self.results is not None else [_result("searx")]


class Builtin(SearchProvider):
    name = "builtin"
    tier = "basic"

    def search(self, query, max_results=10):
        return [_result("builtin")]


def test_off_by_default_never_touches_searxng():
    client = FakeClient()
    provider = OptionalSearxngProvider(client, Builtin())
    assert provider.search("q")[0].title == "builtin"
    assert provider.tier == "basic" and provider.name == "builtin"
    assert client.probes == 0 and client.queries == 0      # no network, no Docker


def test_enabled_and_alive_uses_searxng():
    client = FakeClient()
    provider = OptionalSearxngProvider(client, Builtin(), enabled=True)
    assert provider.tier == "enhanced"
    assert provider.search("q")[0].title == "searx"


def test_switch_on_at_runtime_without_restart():
    client = FakeClient(alive=False)
    provider = OptionalSearxngProvider(client, Builtin())
    provider.set_enabled(True)
    assert provider.search("q")[0].title == "builtin"      # container still starting
    client.alive = True
    provider.set_enabled(True)                             # re-probe (cache reset)
    assert provider.search("q")[0].title == "searx"
    provider.set_enabled(False)
    assert provider.search("q")[0].title == "builtin"


def test_searxng_failure_falls_back_per_query():
    client = FakeClient(error=RuntimeError("boom"))
    provider = OptionalSearxngProvider(client, Builtin(), enabled=True)
    assert provider.search("q")[0].title == "builtin"
    client.results = []
    client.error = None
    provider.set_enabled(True)
    assert provider.search("q")[0].title == "builtin"      # empty SearXNG answer


def test_liveness_is_cached():
    client = FakeClient()
    provider = OptionalSearxngProvider(client, Builtin(), enabled=True, probe_ttl=60)
    for _ in range(5):
        provider.search("q")
        _ = provider.tier
    assert client.probes == 1


def test_pipeline_keeps_optional_provider():
    """web.pipeline used to replace a failing enhanced provider for good."""
    from web.pipeline import WebSearch

    client = FakeClient(results=[])
    provider = OptionalSearxngProvider(client, Builtin(), enabled=True)
    provider.fallback = MagicMock(tier="basic", search=MagicMock(return_value=[]))
    ws = WebSearch(client=client, fetcher=MagicMock(), cache=MagicMock(), provider=provider)
    ws._fallback = MagicMock(search=MagicMock(return_value=[_result("ddg")]))
    ws.run("q")
    assert ws.provider is provider


def test_agent_web_search_works_without_searxng(tmp_path):
    from orchestrator import CodingAgent

    provider = OptionalSearxngProvider(FakeClient(alive=False), Builtin())
    web = MagicMock()
    web.client = FakeClient(alive=False)
    web.provider = provider
    web.run.return_value = []
    agent = CodingAgent(backend=MagicMock(), project_root=str(tmp_path), web_search=web,
                        git_enabled=False, lang="off")
    result = agent._tool_web_search("python dataclasses")
    assert "not reachable" not in (result.error or "")
    web.run.assert_called_once()


def test_agent_still_reports_missing_searxng_for_plain_client(tmp_path):
    from orchestrator import CodingAgent

    web = MagicMock()
    web.client = FakeClient(alive=False)
    agent = CodingAgent(backend=MagicMock(), project_root=str(tmp_path), web_search=web,
                        git_enabled=False, lang="off")
    assert "not reachable" in agent._tool_web_search("q").error


def test_app_starts_docker_only_when_enabled():
    source = (ROOT / "desktop_browser" / "app.py").read_text(encoding="utf-8")
    start = source.index("# Docker/SearXNG only if the user switched")
    block = source[start:start + 600]
    assert "and searxng_enabled()" in block and "ensure_searxng" in block
