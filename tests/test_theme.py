from types import SimpleNamespace

import pytest

from cli.render import Renderer
from cli.theme import DARK, LIGHT, THEMES, load_theme, save_theme


def _settings(tmp_path):
    return SimpleNamespace(paths=SimpleNamespace(data_dir=str(tmp_path)))


def test_default_themes():
    assert DARK.name == "dark"
    assert LIGHT.name == "light"
    assert set(THEMES) == {"dark", "light"}
    assert DARK.accent == LIGHT.accent == "#7B2CBF"
    assert DARK.accent2 == LIGHT.accent2 == "#00B4D8"


def test_load_theme_default(monkeypatch, tmp_path):
    monkeypatch.delenv("FLUXION_THEME", raising=False)
    assert load_theme(_settings(tmp_path)) == DARK


def test_load_theme_env(monkeypatch, tmp_path):
    monkeypatch.setenv("FLUXION_THEME", "light")
    assert load_theme(_settings(tmp_path)) == LIGHT


def test_load_theme_env_invalid(monkeypatch, tmp_path):
    monkeypatch.setenv("FLUXION_THEME", "neon")
    assert load_theme(_settings(tmp_path)) == DARK


def test_save_and_load_theme(tmp_path):
    settings = _settings(tmp_path)
    save_theme(settings, LIGHT)
    assert load_theme(settings) == LIGHT
    save_theme(settings, DARK)
    assert load_theme(settings) == DARK


def test_renderer_takes_theme():
    renderer = Renderer(theme=LIGHT)
    assert renderer.theme == LIGHT
    assert renderer.pt_style is not None


def test_theme_command_switches_and_persists(tmp_path, monkeypatch):
    monkeypatch.delenv("FLUXION_THEME", raising=False)
    from unittest import mock

    from core.config import Settings

    import cli.app as cli_app

    settings = Settings.load()
    settings.paths.data_dir = str(tmp_path)

    class FakeREPL:
        pass

    repl = FakeREPL()
    renderer = Renderer(theme=DARK)
    repl.renderer = renderer

    reg = cli_app.build_registry(repl, mock.MagicMock(), settings)

    reg.get("theme").handler(["light"])
    assert renderer.theme == LIGHT
    assert load_theme(settings) == LIGHT

    reg.get("theme").handler([])

    reg.get("theme").handler(["dark"])
    assert renderer.theme == DARK
    assert load_theme(settings) == DARK


def test_theme_command_unknown_theme(tmp_path, monkeypatch):
    monkeypatch.delenv("FLUXION_THEME", raising=False)
    from unittest import mock

    from core.config import Settings

    import cli.app as cli_app

    settings = Settings.load()
    settings.paths.data_dir = str(tmp_path)

    class FakeREPL:
        pass

    repl = FakeREPL()
    renderer = Renderer(theme=DARK)
    repl.renderer = renderer

    reg = cli_app.build_registry(repl, mock.MagicMock(), settings)
    renderer.theme = DARK
    reg.get("theme").handler(["neon"])
    assert renderer.theme == DARK
    assert load_theme(settings) == DARK
