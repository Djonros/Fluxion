"""LoRA adapter marketplace: local registry of fine-tuned adapters.

Each adapter produced by the finetune pipeline (QLoRA -> merge -> GGUF ->
`ollama create`) is described by an AdapterInfo record. Records live in a
JSON index (default: data/lora_registry/index.json) that backs the
`/adapters` CLI commands: list / info / register / delete / switch.
"""
from __future__ import annotations

import json
import re
import shutil
from dataclasses import asdict, dataclass, fields
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING

from .qlora_config import QLoRASettings

if TYPE_CHECKING:
    from core.config import Settings
    from core.inference import OllamaBackend

DEFAULT_REGISTRY_DIR = "data/lora_registry"
INDEX_FILENAME = "index.json"
INDEX_VERSION = 1
MAX_NAME_LENGTH = 64
_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9._-]*$")


class AdapterError(Exception):
    """Base error for adapter registry operations."""


class AdapterNotFoundError(AdapterError):
    """Requested adapter is not in the registry."""


class DuplicateAdapterError(AdapterError):
    """An adapter with the same name is already registered."""


class InvalidAdapterError(AdapterError):
    """Adapter metadata failed validation."""


@dataclass
class AdapterInfo:
    name: str
    base_model: str = "Qwen/Qwen2.5-Coder-7B-Instruct"
    path: str = "data/lora_output"
    ollama_model: str = "fluxion-coder-python"
    quantize: str = "q4_K_M"
    created_at: str = ""
    description: str = ""

    def __post_init__(self) -> None:
        if not self.created_at:
            self.created_at = datetime.now(timezone.utc).isoformat()

    @classmethod
    def from_training(
        cls, settings: QLoRASettings, name: str, description: str = ""
    ) -> "AdapterInfo":
        """Build an entry from a (possibly customized) QLoRASettings."""
        return cls(
            name=name,
            base_model=settings.base_model,
            path=settings.output_dir,
            ollama_model=settings.ollama_model_name,
            quantize=settings.gguf_quantize,
            description=description,
        )

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "AdapterInfo":
        allowed = {f.name for f in fields(cls)}
        return cls(**{k: v for k, v in data.items() if k in allowed})

    def validate(self) -> None:
        if (
            not self.name
            or len(self.name) > MAX_NAME_LENGTH
            or not _NAME_RE.match(self.name)
        ):
            raise InvalidAdapterError(
                f"invalid adapter name {self.name!r}: expected lowercase slug "
                f"matching {_NAME_RE.pattern} (max {MAX_NAME_LENGTH} chars)"
            )
        if not self.base_model:
            raise InvalidAdapterError("base_model must not be empty")
        if not self.ollama_model:
            raise InvalidAdapterError("ollama_model must not be empty")


class AdapterRegistry:
    """JSON-backed local registry of LoRA adapters."""

    def __init__(self, registry_dir: str | Path = DEFAULT_REGISTRY_DIR) -> None:
        self.registry_dir = Path(registry_dir)
        self.index_path = self.registry_dir / INDEX_FILENAME

    # -- index io ---------------------------------------------------------

    def _read_index(self) -> dict:
        if not self.index_path.exists():
            return {"version": INDEX_VERSION, "adapters": {}}
        try:
            data = json.loads(self.index_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise AdapterError(
                f"corrupt adapter index {self.index_path}: {exc}"
            ) from exc
        if not isinstance(data, dict) or not isinstance(
            data.get("adapters", {}), dict
        ):
            raise AdapterError(f"corrupt adapter index {self.index_path}")
        data.setdefault("version", INDEX_VERSION)
        data.setdefault("adapters", {})
        return data

    def _write_index(self, data: dict) -> None:
        self.registry_dir.mkdir(parents=True, exist_ok=True)
        tmp = self.index_path.with_suffix(".json.tmp")
        tmp.write_text(
            json.dumps(data, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        tmp.replace(self.index_path)

    # -- operations -------------------------------------------------------

    def register(
        self, adapter: AdapterInfo, *, exist_ok: bool = False
    ) -> AdapterInfo:
        adapter.validate()
        data = self._read_index()
        adapters = data["adapters"]
        if adapter.name in adapters and not exist_ok:
            raise DuplicateAdapterError(
                f"adapter '{adapter.name}' already registered"
            )
        adapters[adapter.name] = adapter.to_dict()
        self._write_index(data)
        return adapter

    def list(self) -> list[AdapterInfo]:
        data = self._read_index()
        return [
            AdapterInfo.from_dict(entry)
            for _, entry in sorted(data["adapters"].items())
        ]

    def get(self, name: str) -> AdapterInfo | None:
        entry = self._read_index()["adapters"].get(name)
        return AdapterInfo.from_dict(entry) if entry else None

    def find_by_model(self, ollama_model: str) -> AdapterInfo | None:
        for adapter in self.list():
            if adapter.ollama_model == ollama_model:
                return adapter
        return None

    def delete(self, name: str, *, remove_files: bool = False) -> bool:
        data = self._read_index()
        entry = data["adapters"].pop(name, None)
        if entry is None:
            return False
        self._write_index(data)
        if remove_files:
            path = Path(entry.get("path", ""))
            if path.is_dir():
                shutil.rmtree(path)
        return True

    def switch(
        self,
        name: str,
        settings: "Settings",
        backend: "OllamaBackend | None" = None,
    ) -> AdapterInfo:
        """Point settings (and backend) at the adapter's Ollama model."""
        adapter = self.get(name)
        if adapter is None:
            raise AdapterNotFoundError(f"adapter '{name}' not found")
        settings.model = adapter.ollama_model
        if backend is not None:
            backend.settings = settings
            backend.client.model = adapter.ollama_model
        return adapter
