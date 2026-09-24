"""Loaded-model registry for the embedded engine (roadmap 18.4).

Tracks in-process llama.cpp models (chat + embeddings), enforces a VRAM
budget with LRU eviction (never evicting the model mid-generation),
supports idle unloading, and reports the CPU-mode notice when nothing
is offloaded to the GPU.
"""
from __future__ import annotations

import logging
import os
import threading
import time
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)


@dataclass
class LoadedModel:
    key: str
    llm: object
    vram_gb: float = 0.0
    n_gpu_layers: int = 0
    task: str = "chat"  # "chat" | "embedding"
    protected: bool = False
    last_used: float = field(default_factory=time.monotonic)


class ModelRuntime:
    """Thread-safe registry of loaded models with a VRAM budget."""

    def __init__(self, vram_budget_gb: float | None = None):
        if vram_budget_gb is None:
            env = os.environ.get("FLUXION_VRAM_BUDGET_GB", "").strip()
            try:
                vram_budget_gb = float(env) if env else 0.0
            except ValueError:
                vram_budget_gb = 0.0
        self.vram_budget_gb = float(vram_budget_gb)  # 0 = unlimited
        self._models: dict[str, LoadedModel] = {}
        self._lock = threading.Lock()

    # ── registry ───────────────────────────────────────────────────────

    def register(
        self,
        key: str,
        llm: object,
        vram_gb: float = 0.0,
        n_gpu_layers: int = 0,
        task: str = "chat",
    ) -> None:
        with self._lock:
            self._models[key] = LoadedModel(key, llm, vram_gb, n_gpu_layers, task)
        logger.info("Model loaded: %s (%.1f GB, gpu_layers=%d)", key, vram_gb, n_gpu_layers)

    def unregister(self, key: str) -> bool:
        with self._lock:
            return self._models.pop(key, None) is not None

    def get(self, key: str):
        """Touch LRU and return the underlying model (or None)."""
        with self._lock:
            entry = self._models.get(key)
            if entry is None:
                return None
            entry.last_used = time.monotonic()
            return entry.llm

    def keys(self) -> list[str]:
        with self._lock:
            return list(self._models)

    def protect(self, key: str, protected: bool = True) -> None:
        with self._lock:
            entry = self._models.get(key)
            if entry is not None:
                entry.protected = protected

    # ── memory management ──────────────────────────────────────────────

    @property
    def total_vram_gb(self) -> float:
        with self._lock:
            return float(sum(m.vram_gb for m in self._models.values()))

    def ensure_capacity(self, needed_gb: float) -> list[str]:
        """Evict LRU models until *needed_gb* fits the budget (0 = unlimited).

        Protected models are never evicted. Returns the evicted keys.
        """
        evicted: list[str] = []
        if self.vram_budget_gb <= 0:
            return evicted
        with self._lock:
            used = sum(m.vram_gb for m in self._models.values())
            while used + needed_gb > self.vram_budget_gb:
                candidates = [m for m in self._models.values() if not m.protected]
                if not candidates:
                    break
                victim = min(candidates, key=lambda m: m.last_used)
                self._models.pop(victim.key, None)
                used -= victim.vram_gb
                evicted.append(victim.key)
                logger.info("Evicted model (VRAM budget): %s", victim.key)
        return evicted

    def unload_idle(self, max_idle_seconds: float) -> list[str]:
        """Unload models idle for longer than *max_idle_seconds*."""
        evicted: list[str] = []
        now = time.monotonic()
        with self._lock:
            for key in [
                m.key for m in self._models.values()
                if not m.protected and now - m.last_used > max_idle_seconds
            ]:
                self._models.pop(key, None)
                evicted.append(key)
        return evicted

    # ── notices ────────────────────────────────────────────────────────

    def cpu_mode(self) -> bool:
        """True when models are loaded but none uses GPU layers."""
        with self._lock:
            return bool(self._models) and all(
                m.n_gpu_layers == 0 for m in self._models.values()
            )

    def cpu_mode_note(self) -> str:
        if not self.cpu_mode():
            return ""
        return (
            "GPU-пак не найден: инференс на CPU — работает, но медленнее. "
            "[Установить GPU-пак]"
        )


_SHARED: ModelRuntime | None = None


def get_shared_runtime() -> ModelRuntime:
    """Process-wide runtime shared by the chat backend and the embedder."""
    global _SHARED
    if _SHARED is None:
        _SHARED = ModelRuntime()
    return _SHARED
