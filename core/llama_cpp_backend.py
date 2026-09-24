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

    def __init__(
        self,
        model_path: str,
        n_ctx: int = 32768,
        n_gpu_layers: int = 0,
        verbose: bool = False,
        runtime=None,
        runtime_key: str = "chat",
    ):
        try:
            from llama_cpp import Llama
        except ImportError as exc:
            raise ImportError(
                "llama-cpp-python not installed. Install with: pip install llama-cpp-python"
            ) from exc

        self.model_path = model_path
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
        )

    def _params(self, gen: GenerationSettings | None) -> dict:
        g = gen or GenerationSettings()
        return {
            "temperature": g.temperature,
            "top_p": g.top_p,
            "max_tokens": g.max_tokens,
        }

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
        return self.model_path and os.path.isfile(self.model_path)
