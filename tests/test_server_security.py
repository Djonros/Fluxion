"""Security policy of the server (hosted vs local mode) and backend hardening."""
from __future__ import annotations

import importlib

import pytest

pytest.importorskip("fastapi")

from server import security  # noqa: E402


class TestServerMode:
    def test_default_is_local(self, monkeypatch):
        monkeypatch.delenv("FLUXION_SERVER_MODE", raising=False)
        assert security.server_mode() == "local"

    def test_saas_confines_paths(self, tmp_path, monkeypatch):
        monkeypatch.setenv("FLUXION_SERVER_MODE", "saas")
        monkeypatch.setenv("FLUXION_WORKSPACES_DIR", str(tmp_path))
        ws = security.user_workspace(42)
        assert security.resolve_user_path(42, "proj") == (ws / "proj").resolve()
        for bad in ("/etc", "../41", str(tmp_path)):
            with pytest.raises(Exception):
                security.resolve_user_path(42, bad)

    def test_local_mode_keeps_paths(self, tmp_path, monkeypatch):
        monkeypatch.setenv("FLUXION_SERVER_MODE", "local")
        assert security.resolve_user_path(1, str(tmp_path)) == tmp_path.resolve()

    def test_cors_no_wildcard_by_default(self, monkeypatch):
        monkeypatch.delenv("FLUXION_CORS_ORIGINS", raising=False)
        opts = security.cors_options()
        assert "allow_origins" not in opts
        assert "localhost" in opts["allow_origin_regex"]

    def test_cors_explicit_origins(self, monkeypatch):
        monkeypatch.setenv("FLUXION_CORS_ORIGINS", "https://a.example, https://b.example")
        assert security.cors_options()["allow_origins"] == ["https://a.example", "https://b.example"]


class TestJwtSecret:
    def _reload(self):
        import server.auth.jwt_handler as jh
        return importlib.reload(jh)

    def test_production_refuses_default_secret(self, monkeypatch):
        monkeypatch.setenv("FLUXION_ENV", "production")
        monkeypatch.setenv("FLUXION_JWT_SECRET", "fluxion-dev-secret-change-in-production")
        with pytest.raises(RuntimeError):
            self._reload()
        monkeypatch.delenv("FLUXION_ENV")
        monkeypatch.delenv("FLUXION_JWT_SECRET")
        self._reload()

    def test_dev_uses_random_secret_not_public_default(self, monkeypatch):
        monkeypatch.delenv("FLUXION_ENV", raising=False)
        monkeypatch.delenv("FLUXION_SERVER_MODE", raising=False)
        monkeypatch.setenv("FLUXION_JWT_SECRET", "fluxion-dev-secret-change-in-production")
        jh = self._reload()
        assert jh._SECRET_KEY != "fluxion-dev-secret-change-in-production"
        assert len(jh._SECRET_KEY) >= 32
        monkeypatch.delenv("FLUXION_JWT_SECRET")
        self._reload()


class TestBackends:
    def test_api_backend_never_uses_hf_token(self, monkeypatch):
        from core.api_backend import APIBackend
        from core.config import Settings

        monkeypatch.delenv("FLUXION_API_KEY", raising=False)
        backend = APIBackend.from_settings(Settings(hf_token="hf_secret"))
        assert backend.api_key == ""

    def test_api_backend_sends_stop(self):
        from core.api_backend import APIBackend
        from core.config import GenerationSettings

        b = APIBackend(api_key="k")
        payload = b._payload([], GenerationSettings(stop=["\nObservation:"]), stream=False)
        assert payload["stop"] == ["\nObservation:"]

    def test_llama_backend_uses_configured_generation(self):
        from core.config import GenerationSettings
        from core.llama_cpp_backend import LlamaCppBackend

        b = object.__new__(LlamaCppBackend)  # no model load
        b.generation = GenerationSettings(temperature=0.55, max_tokens=77)
        params = b._params(None)
        assert params["temperature"] == 0.55 and params["max_tokens"] == 77
        assert b._params(GenerationSettings(stop=["X"]))["stop"] == ["X"]

    def test_ollama_backend_sends_stop(self):
        from core.config import GenerationSettings, Settings
        from core.inference import OllamaBackend

        b = OllamaBackend(Settings())
        assert b._options(GenerationSettings(stop=["\nObservation:"]))["stop"] == ["\nObservation:"]
