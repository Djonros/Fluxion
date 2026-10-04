"""First start on a clean machine: embedded engine shipped, no model, no Ollama.

Regression for a report from testing on a clean PC: the app did not start
until Ollama was installed.  Two causes:
  * the default backend required an already downloaded model, so a fresh
    install fell back to Ollama;
  * the llama.cpp engine loaded the model at startup, so without a model it
    failed and the first-run wizard (which downloads the model) never opened.
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest

import core.llama_cpp_backend as lcb
from core.backend_factory import BackendFactory, _detect_backend_type
from core.config import GenerationSettings, Settings
from core.llama_cpp_backend import LazyLlamaCppBackend, ModelNotReadyError


class FakeLlama:
    """Stands in for LlamaCppBackend: records loads, answers with the path."""

    loads: list[str] = []
    unloads: list[str] = []

    def __init__(self, model_path, n_ctx=0, n_gpu_layers=0, generation=None, **kw):
        if not Path(model_path).is_file():
            raise ValueError(f"Model path does not exist: {model_path}")
        self.model_path = model_path
        self.n_gpu_layers = n_gpu_layers
        FakeLlama.loads.append(model_path)

    def generate(self, messages, gen=None):
        return f"answer from {Path(self.model_path).name}"

    def stream(self, messages, gen=None):
        yield "tok"

    def unload(self):
        FakeLlama.unloads.append(self.model_path)

    def cpu_mode_note(self):
        return "cpu"


@pytest.fixture()
def clean_machine(tmp_path, monkeypatch):
    for key in ("FLUXION_BACKEND", "FLUXION_API_KEY", "FLUXION_GGUF_PATH", "FLUXION_MODELS_DIR"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    monkeypatch.setattr("core.backend_factory._llama_cpp_installed", lambda: True)
    monkeypatch.setattr(lcb, "LlamaCppBackend", FakeLlama)
    FakeLlama.loads, FakeLlama.unloads = [], []
    return tmp_path / "Fluxion" / "models"


def _download_default_model(models_dir: Path) -> Path:
    models_dir.mkdir(parents=True, exist_ok=True)
    path = models_dir / "qwen2.5-coder-7b-instruct-q4_k_m.gguf"
    path.write_bytes(b"GGUF")
    return path


def test_embedded_engine_is_chosen_without_model_or_ollama(clean_machine):
    assert _detect_backend_type(Settings()) == "llama_cpp"


def test_startup_does_not_fail_without_model(clean_machine):
    backend = BackendFactory.create_primary(Settings())   # used by the app at startup
    assert isinstance(backend, LazyLlamaCppBackend)
    assert backend.is_available() is False                 # the wizard offers the download
    assert FakeLlama.loads == []                            # nothing loaded at startup


def test_request_before_download_explains_what_to_do(clean_machine):
    backend = BackendFactory.create_primary(Settings())
    with pytest.raises(ModelNotReadyError, match="«Модели»"):
        backend.generate([{"role": "user", "content": "hi"}])


def test_model_downloaded_while_running_is_used_without_restart(clean_machine):
    settings = Settings()
    backend = BackendFactory.create_primary(settings)
    with pytest.raises(ModelNotReadyError):
        backend.generate([])
    model = _download_default_model(clean_machine)          # wizard / "Models" page
    assert backend.is_available()
    assert backend.generate([]) == f"answer from {model.name}"
    assert backend.generate([]) == f"answer from {model.name}"
    assert FakeLlama.loads == [str(model)]                   # loaded once, then reused


def test_switching_model_reloads(clean_machine, tmp_path):
    settings = Settings()
    backend = LazyLlamaCppBackend(settings)
    first = _download_default_model(clean_machine)
    backend.generate([])
    other = tmp_path / "other.gguf"
    other.write_bytes(b"GGUF")
    settings.gguf_path = str(other)                          # chosen on the "Models" page
    assert backend.generate([]) == "answer from other.gguf"
    assert FakeLlama.unloads == [str(first)]


def test_explicit_path_and_env_win(clean_machine, tmp_path, monkeypatch):
    explicit = tmp_path / "mine.gguf"
    assert LazyLlamaCppBackend(Settings(gguf_path=str(explicit))).model_path == str(explicit)
    monkeypatch.setenv("FLUXION_GGUF_PATH", str(tmp_path / "env.gguf"))
    assert LazyLlamaCppBackend(Settings()).model_path == str(tmp_path / "env.gguf")


def test_default_path_is_the_models_page_folder(clean_machine):
    expected = clean_machine / "qwen2.5-coder-7b-instruct-q4_k_m.gguf"
    assert LazyLlamaCppBackend(Settings()).model_path == str(expected)


def test_lazy_backend_supports_agent_features(clean_machine):
    settings = Settings(generation=GenerationSettings(temperature=0.3))
    backend = LazyLlamaCppBackend(settings)
    assert backend.supports_json_schema is True             # structured tool calls
    assert backend.generation.temperature == 0.3            # agent reads backend.generation
    assert backend.cpu_mode_note() == ""
    backend.unload()                                        # safe before anything is loaded


def test_gpu_layers_passed_on_load(clean_machine):
    _download_default_model(clean_machine)
    backend = LazyLlamaCppBackend(Settings(n_gpu_layers=-1))
    backend.generate([])
    assert backend._backend.n_gpu_layers == -1


def test_frozen_build_detection_falls_back_to_import(monkeypatch):
    """In a PyInstaller exe find_spec may not see the bundled package."""
    import sys
    import types

    import core.backend_factory as factory

    monkeypatch.undo()  # drop the conftest default, test the real function
    real = factory._llama_cpp_installed
    monkeypatch.setattr(factory.importlib.util, "find_spec", lambda name: None)
    monkeypatch.setitem(sys.modules, "llama_cpp", types.ModuleType("llama_cpp"))
    assert real() is True


def test_path_from_build_machine_falls_back_to_downloaded_model(clean_machine):
    """The exe bundles the builder's config: its gguf_path may not exist here."""
    foreign = "C:/Users/builder/AppData/Local/Fluxion/models/qwen.gguf"
    settings = Settings(gguf_path=foreign)
    backend = LazyLlamaCppBackend(settings)
    assert backend.model_path == foreign                   # nothing downloaded yet
    model = _download_default_model(clean_machine)
    assert backend.model_path == str(model)
    assert backend.generate([]) == f"answer from {model.name}"


def test_wizard_model_check_sees_downloaded_model_despite_foreign_path(clean_machine):
    from desktop_browser.health import check_gguf_model

    settings = Settings(gguf_path="C:/Users/builder/qwen.gguf")
    assert check_gguf_model(settings).available is False
    _download_default_model(clean_machine)
    assert check_gguf_model(settings).available is True
