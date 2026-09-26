"""Settings: loads config.yaml merged with environment variables (.env)."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml
from dotenv import load_dotenv

_DEFAULT_CONFIG = "config/config.yaml"


@dataclass
class GenerationSettings:
    temperature: float = 0.2
    top_p: float = 0.9
    max_tokens: int = 2048
    num_ctx: int = 32768
    # Stop sequences: generation halts before any of these strings.  The
    # ReAct agent sets them so the model cannot invent its own "Observation:".
    stop: list[str] = field(default_factory=list)
    # JSON schema the reply must conform to (constrained decoding).  Backends
    # with ``supports_json_schema = True`` enforce it with a grammar.
    json_schema: dict | None = None


@dataclass
class RagSettings:
    enabled: bool = False
    embedding_model: str = "bge-m3"
    embedding_device: str = "cpu"
    embedding_backend: str = ""  # "" (auto) | ollama | llama_cpp
    embedding_gguf: str = ""  # путь к GGUF-модели эмбеддингов (для llama_cpp)
    chroma_dir: str = "data/chroma"
    chunk_max_chars: int = 1500
    chunk_overlap: int = 200
    top_k: int = 8
    rerank: bool = True


@dataclass
class WebSettings:
    enabled: bool = False
    searxng_url: str = "http://localhost:8080"
    max_pages: int = 3
    timeout: int = 10
    cache_ttl_hours: int = 24
    searxng_autostart: bool = True


@dataclass
class PathsSettings:
    data_dir: str = "data"
    repos_dir: str = "data/repos"


@dataclass
class Settings:
    backend: str = ""  # ollama | llama_cpp (alias: llamacpp) | api; "" → auto
    ollama_host: str = "http://localhost:11434"
    model: str = "qwen2.5-coder:7b-instruct"
    gguf_path: str = ""
    n_gpu_layers: int = 0
    generation: GenerationSettings = field(default_factory=GenerationSettings)
    rag: RagSettings = field(default_factory=RagSettings)
    web: WebSettings = field(default_factory=WebSettings)
    paths: PathsSettings = field(default_factory=PathsSettings)
    hf_token: str = ""
    language: str = "auto"  # auto | ru | en
    config_path: str = ""

    @classmethod
    def load(cls, config_path: str | None = None) -> "Settings":
        """Load settings from YAML, with .env and environment overrides."""
        load_dotenv()
        path = Path(config_path or os.environ.get("AI_AGENT_CONFIG", _DEFAULT_CONFIG))
        data: dict[str, Any] = {}
        if path.exists():
            with path.open("r", encoding="utf-8") as fh:
                data = yaml.safe_load(fh) or {}
        gen = data.get("generation") or {}
        rag = data.get("rag") or {}
        web = data.get("web") or {}
        paths = data.get("paths") or {}
        return cls(
            backend=str(data.get("backend", "")).strip().lower(),
            ollama_host=data.get(
                "ollama_host", os.environ.get("OLLAMA_HOST", "http://localhost:11434")
            ),
            model=data.get("model", os.environ.get("MODEL", "qwen2.5-coder:7b-instruct")),
            gguf_path=str(data.get("gguf_path", "") or ""),
            n_gpu_layers=int(data.get("n_gpu_layers", 0) or 0),
            generation=GenerationSettings(**gen),
            rag=RagSettings(**rag),
            web=WebSettings(**web),
            paths=PathsSettings(**paths),
            hf_token=os.environ.get("HF_TOKEN", ""),
            language=str(data.get("language", "auto") or "auto").strip().lower(),
            config_path=str(path),
        )

    def project_root(self) -> Path:
        """Best-effort project root: parent of the config dir."""
        p = Path(self.config_path)
        return p.parent.parent if p.parent.name == "config" else p.parent
