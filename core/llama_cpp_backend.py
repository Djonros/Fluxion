"""LlamaCppBackend: direct GGUF inference via llama-cpp-python.

No external server process needed — loads the model directly in-process.
Install with:  pip install llama-cpp-python

Set environment variables:
    FLUXION_GGUF_PATH  — path to the .gguf model file (required)
    FLUXION_N_GPU_LAYERS — number of layers to offload to GPU (default: 0)
"""
from __future__ import annotations

import logging
import os
from collections.abc import Iterator

from .config import GenerationSettings, Settings
from .inference import ModelBackend

logger = logging.getLogger(__name__)


class LlamaCppBackend(ModelBackend):
    """Direct GGUF inference backend using llama-cpp-python."""

    # llama-cpp-python compiles a JSON schema into a GBNF grammar, so the
    # model cannot produce a malformed tool call.
    supports_json_schema = True

    def __init__(
        self,
        model_path: str,
        n_ctx: int = 32768,
        n_gpu_layers: int = 0,
        verbose: bool = False,
        runtime=None,
        runtime_key: str = "chat",
        generation: GenerationSettings | None = None,
    ):
        try:
            from llama_cpp import Llama
        except ImportError as exc:
            raise ImportError(
                "llama-cpp-python not installed. Install with: pip install llama-cpp-python"
            ) from exc

        self.model_path = model_path
        # Defaults for generate()/stream() when the caller passes no settings.
        # Previously a bare GenerationSettings() was used, silently ignoring
        # temperature/max_tokens from config.yaml.
        self.generation = generation or GenerationSettings(num_ctx=n_ctx)
        from .model_runtime import get_shared_runtime

        self._runtime = runtime or get_shared_runtime()
        self._runtime_key = runtime_key
        self._runtime.ensure_capacity(0.0)  # apply budget policy before load
        self._llm = Llama(
            model_path=model_path,
            n_ctx=n_ctx,
            n_gpu_layers=n_gpu_layers,
            verbose=verbose,
        )
        self._runtime.register(
            runtime_key, self._llm, n_gpu_layers=n_gpu_layers, task="chat"
        )

    def unload(self) -> None:
        """Drop the model from the registry (frees VRAM on GC)."""
        self._runtime.unregister(self._runtime_key)

    @property
    def cpu_mode_note(self) -> str:
        return self._runtime.cpu_mode_note()

    @classmethod
    def from_settings(cls, settings: Settings) -> LlamaCppBackend:
        model_path = str(getattr(settings, "gguf_path", "") or "").strip()
        if not model_path:
            model_path = os.environ.get("FLUXION_GGUF_PATH", "")
        if not model_path:
            raise RuntimeError(
                "Путь к GGUF-модели не задан: укажите gguf_path в config.yaml "
                "или переменную окружения FLUXION_GGUF_PATH."
            )
        n_gpu = int(getattr(settings, "n_gpu_layers", 0) or 0)
        if not n_gpu:
            n_gpu = int(os.environ.get("FLUXION_N_GPU_LAYERS", "0"))
        return cls(
            model_path=model_path,
            n_ctx=settings.generation.num_ctx,
            n_gpu_layers=n_gpu,
            generation=settings.generation,
        )

    def _params(self, gen: GenerationSettings | None) -> dict:
        g = gen or self.generation
        params = {
            "temperature": g.temperature,
            "top_p": g.top_p,
            "max_tokens": g.max_tokens,
        }
        if getattr(g, "stop", None):
            params["stop"] = list(g.stop)
        if getattr(g, "json_schema", None):
            params["response_format"] = {"type": "json_object", "schema": g.json_schema}
        return params

    def generate(
        self,
        messages: list[dict[str, str]],
        gen: GenerationSettings | None = None,
    ) -> str:
        result = self._llm.create_chat_completion(
            messages=messages,
            stream=False,
            **self._params(gen),
        )
        return result["choices"][0]["message"]["content"]

    def stream(
        self,
        messages: list[dict[str, str]],
        gen: GenerationSettings | None = None,
    ) -> Iterator[str]:
        for chunk in self._llm.create_chat_completion(
            messages=messages,
            stream=True,
            **self._params(gen),
        ):
            delta = chunk.get("choices", [{}])[0].get("delta", {}).get("content", "")
            if delta:
                yield delta

    def is_available(self) -> bool:
        return bool(self.model_path) and os.path.isfile(self.model_path)


class ModelNotReadyError(RuntimeError):
    """The GGUF model is not downloaded yet (message is user-facing)."""


def default_gguf_path() -> str:
    """Where the app keeps the default chat model (the "Models" page)."""
    from .model_manager import MODEL_CATALOG, _default_models_dir

    entry = next((m for m in MODEL_CATALOG if m.task == "chat" and m.default), None)
    return str(_default_models_dir() / entry.filename) if entry else ""


def resolve_gguf_path(settings) -> str:
    """The chat model file to use.

    An explicit path (``gguf_path`` / ``FLUXION_GGUF_PATH``) wins when the
    file exists.  If it does not — e.g. a path from the machine the exe was
    built on — but the model is in the app's models folder (where the wizard
    and the "Models" page download it), that one is used instead of failing.
    """
    configured = str(getattr(settings, "gguf_path", "") or "").strip() or os.environ.get(
        "FLUXION_GGUF_PATH", ""
    ).strip()
    if configured and os.path.isfile(configured):
        return configured
    default = default_gguf_path()
    if default and os.path.isfile(default):
        return default
    return configured or default


class LazyLlamaCppBackend(ModelBackend):
    """Embedded llama.cpp engine that loads the model on first use.

    On a clean machine the model is not downloaded yet: creating
    :class:`LlamaCppBackend` would fail at startup and the app could not even
    open its first-run wizard to download the model.  This wrapper starts
    without a model, reports ``is_available() == False`` until the file
    exists, and loads it on the first request — so a model downloaded while
    the app runs is picked up without a restart.  If ``settings.gguf_path``
    changes (another model chosen), the next request loads that model.
    """

    supports_json_schema = True

    def __init__(self, settings: Settings):
        import threading

        self.settings = settings
        self.generation = settings.generation
        self._backend: LlamaCppBackend | None = None
        self._loaded_path = ""
        self._lock = threading.Lock()

    @classmethod
    def from_settings(cls, settings: Settings) -> "LazyLlamaCppBackend":
        return cls(settings)

    @property
    def model_path(self) -> str:
        return resolve_gguf_path(self.settings)

    def is_available(self) -> bool:
        path = self.model_path
        if not path or not os.path.isfile(path):
            return False
        from .backend_factory import _llama_cpp_installed

        return _llama_cpp_installed()

    def _engine(self) -> LlamaCppBackend:
        path = self.model_path
        with self._lock:
            if self._backend is not None and self._loaded_path == path:
                return self._backend
            if not path or not os.path.isfile(path):
                raise ModelNotReadyError(
                    "Модель не скачана. Откройте страницу «Модели» и скачайте "
                    "модель — после загрузки она подключится без перезапуска."
                )
            if self._backend is not None:
                self._backend.unload()
                self._backend = None
            n_gpu = int(getattr(self.settings, "n_gpu_layers", 0) or 0)
            if not n_gpu:
                n_gpu = int(os.environ.get("FLUXION_N_GPU_LAYERS", "0") or 0)
            logger.info("loading GGUF model %s", path)
            self._backend = LlamaCppBackend(
                model_path=path,
                n_ctx=self.settings.generation.num_ctx,
                n_gpu_layers=n_gpu,
                generation=self.settings.generation,
            )
            self._loaded_path = path
            return self._backend

    def generate(self, messages, gen: GenerationSettings | None = None) -> str:
        return self._engine().generate(messages, gen)

    def stream(self, messages, gen: GenerationSettings | None = None) -> Iterator[str]:
        yield from self._engine().stream(messages, gen)

    def unload(self) -> None:
        with self._lock:
            if self._backend is not None:
                self._backend.unload()
            self._backend = None
            self._loaded_path = ""

    def cpu_mode_note(self) -> str:
        return self._backend.cpu_mode_note() if self._backend is not None else ""
