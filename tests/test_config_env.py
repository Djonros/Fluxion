"""Documented environment overrides actually work."""
from __future__ import annotations

from core.config import Settings


def test_searxng_url_env_overrides_config(tmp_path, monkeypatch):
    cfg = tmp_path / "config.yaml"
    cfg.write_text("web:\n  searxng_url: http://localhost:8080\n", encoding="utf-8")
    monkeypatch.setenv("SEARXNG_URL", "http://search.local:9000")
    assert Settings.load(str(cfg)).web.searxng_url == "http://search.local:9000"
    monkeypatch.delenv("SEARXNG_URL")
    assert Settings.load(str(cfg)).web.searxng_url == "http://localhost:8080"


def test_env_file_next_to_config_is_loaded(tmp_path, monkeypatch):
    """config/.env (as documented) used to be ignored."""
    cfg_dir = tmp_path / "config"
    cfg_dir.mkdir()
    (cfg_dir / "config.yaml").write_text("{}\n", encoding="utf-8")
    (cfg_dir / ".env").write_text("HF_TOKEN=hf_from_config_env\n", encoding="utf-8")
    monkeypatch.delenv("HF_TOKEN", raising=False)
    assert Settings.load(str(cfg_dir / "config.yaml")).hf_token == "hf_from_config_env"
    monkeypatch.setenv("HF_TOKEN", "hf_real_env")              # real env wins
    assert Settings.load(str(cfg_dir / "config.yaml")).hf_token == "hf_real_env"
