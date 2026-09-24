"""Model manager (roadmap 18.2): curated catalog + GGUF download/import.

Storage: %LOCALAPPDATA%/Fluxion/models (override: FLUXION_MODELS_DIR).
Every installed model gets a .json sidecar with source metadata.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import shutil
from dataclasses import dataclass, asdict
from pathlib import Path

import httpx

logger = logging.getLogger(__name__)

_GGUF_MAGIC = b"GGUF"
_CHUNK = 1024 * 512


def _default_models_dir() -> Path:
    override = os.environ.get("FLUXION_MODELS_DIR", "").strip()
    if override:
        return Path(override)
    base = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
    return Path(base) / "Fluxion" / "models"


def _ollama_models_dir() -> Path:
    override = os.environ.get("OLLAMA_MODELS", "").strip()
    if override:
        return Path(override)
    base = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
    return Path(base) / "ollama" / "models"


@dataclass
class ModelInfo:
    model_id: str
    name: str
    repo_id: str  # HuggingFace repo
    filename: str
    size_bytes: int
    task: str = "chat"  # "chat" | "embedding"
    vram_gb: float = 0.0
    sha256: str = ""  # optional integrity pin
    default: bool = False


MODEL_CATALOG: list[ModelInfo] = [
    ModelInfo(
        model_id="qwen2.5-coder-7b-q4km",
        name="Qwen2.5 Coder 7B Instruct (Q4_K_M)",
        repo_id="Qwen/Qwen2.5-Coder-7B-Instruct-GGUF",
        filename="qwen2.5-coder-7b-instruct-q4_k_m.gguf",
        size_bytes=4_681_000_000,
        vram_gb=6.0,
        default=True,
    ),
    ModelInfo(
        model_id="qwen2.5-coder-3b-q4km",
        name="Qwen2.5 Coder 3B Instruct (Q4_K_M) — для слабых машин",
        repo_id="Qwen/Qwen2.5-Coder-3B-Instruct-GGUF",
        filename="qwen2.5-coder-3b-instruct-q4_k_m.gguf",
        size_bytes=2_200_000_000,
        vram_gb=3.0,
    ),
    ModelInfo(
        model_id="bge-m3-gguf",
        name="BGE-M3 (эмбеддинги, GGUF)",
        repo_id="gpustack/bge-m3-GGUF",
        filename="bge-m3-Q8_0.gguf",
        size_bytes=1_300_000_000,
        task="embedding",
    ),
]


def hf_download_url(repo_id: str, filename: str) -> str:
    return f"https://huggingface.co/{repo_id}/resolve/main/{filename}"


@dataclass
class InstalledModel:
    filename: str
    path: Path
    size_bytes: int
    source: str = ""  # catalog model_id | "disk" | "ollama:<name>"
    task: str = "chat"


class ModelManager:
    """Downloads/imports GGUF models into the local Fluxion storage."""

    def __init__(self, models_dir: Path | str | None = None):
        self.models_dir = Path(models_dir) if models_dir else _default_models_dir()
        self.models_dir.mkdir(parents=True, exist_ok=True)

    # ── catalog / listing ─────────────────────────────────────────────

    @staticmethod
    def catalog() -> list[ModelInfo]:
        return list(MODEL_CATALOG)

    def catalog_entry(self, model_id: str) -> ModelInfo | None:
        return next((m for m in MODEL_CATALOG if m.model_id == model_id), None)

    def list_installed(self) -> list[InstalledModel]:
        installed: list[InstalledModel] = []
        for gguf in sorted(self.models_dir.glob("*.gguf")):
            sidecar = self._sidecar(gguf)
            installed.append(
                InstalledModel(
                    filename=gguf.name,
                    path=gguf,
                    size_bytes=gguf.stat().st_size,
                    source=str(sidecar.get("source", "")),
                    task=str(sidecar.get("task", "chat")),
                )
            )
        return installed

    def is_installed(self, model_id: str) -> bool:
        entry = self.catalog_entry(model_id)
        return entry is not None and (self.models_dir / entry.filename).is_file()

    def path_for(self, model_id: str) -> str:
        entry = self.catalog_entry(model_id)
        if entry is None:
            return ""
        return str(self.models_dir / entry.filename)

    # ── download ──────────────────────────────────────────────────────

    def download(
        self,
        model_id: str,
        on_progress=None,
        timeout: float = 60.0,
    ) -> Path:
        """Download a catalog model with resume + optional sha256 check."""
        entry = self.catalog_entry(model_id)
        if entry is None:
            raise KeyError(f"Unknown model id: {model_id}")
        target = self.models_dir / entry.filename
        if target.is_file():
            return target
        url = hf_download_url(entry.repo_id, entry.filename)
        part = target.with_suffix(target.suffix + ".part")
        start = part.stat().st_size if part.exists() else 0

        headers = {}
        if start:
            headers["Range"] = f"bytes={start}-"
        with httpx.stream("GET", url, headers=headers, timeout=timeout, follow_redirects=True) as resp:
            if start and resp.status_code != 206:
                start = 0  # server ignored Range — restart from scratch
                part.unlink(missing_ok=True)
            resp.raise_for_status()
            total = int(resp.headers.get("content-length", 0)) + start
            mode = "ab" if start else "wb"
            done = start
            with open(part, mode) as fh:
                for chunk in resp.iter_bytes(chunk_size=_CHUNK):
                    fh.write(chunk)
                    done += len(chunk)
                    if on_progress is not None:
                        try:
                            on_progress(done, total)
                        except Exception:  # progress must never break download
                            pass

        if entry.sha256:
            digest = _file_sha256(part)
            if digest != entry.sha256:
                part.unlink(missing_ok=True)
                raise RuntimeError(
                    f"SHA256 mismatch for {entry.filename}: expected {entry.sha256}, got {digest}"
                )
        part.replace(target)
        self._write_sidecar(
            target,
            {
                "source": entry.model_id,
                "task": entry.task,
                "repo_id": entry.repo_id,
                "url": url,
                "size_bytes": target.stat().st_size,
            },
        )
        return target

    # ── import / delete ───────────────────────────────────────────────

    def import_from_disk(self, src: Path | str, task: str = "chat") -> InstalledModel:
        src = Path(src)
        if not src.is_file() or src.suffix.lower() != ".gguf":
            raise ValueError(f"Не GGUF-файл: {src}")
        target = self.models_dir / src.name
        shutil.copy2(src, target)
        self._write_sidecar(target, {"source": "disk", "task": task})
        return InstalledModel(
            filename=target.name, path=target, size_bytes=target.stat().st_size,
            source="disk", task=task,
        )

    def import_from_ollama(self, name_filter: str | None = None) -> list[InstalledModel]:
        """Reuse existing Ollama blobs (same GGUFs) — no re-download."""
        root = _ollama_models_dir()
        manifests = root / "manifests"
        blobs = root / "blobs"
        if not manifests.is_dir() or not blobs.is_dir():
            return []
        imported: list[InstalledModel] = []
        for manifest in sorted(p for p in manifests.rglob("*") if p.is_file()):
            try:
                data = json.loads(manifest.read_text(encoding="utf-8"))
            except Exception:
                continue
            if not isinstance(data, dict) or "layers" not in data:
                continue
            model_name = self._ollama_name(manifest, manifests)
            if name_filter and model_name != name_filter:
                continue
            layer = next(
                (l for l in data.get("layers", [])
                 if str(l.get("mediaType", "")).endswith("image.model")),
                None,
            )
            if layer is None:
                continue
            digest = str(layer.get("digest", ""))
            if not digest.startswith("sha256:"):
                continue
            blob = blobs / ("sha256-" + digest.split(":", 1)[1])
            if not blob.is_file() or not self._is_gguf(blob):
                continue
            safe_name = model_name.replace(":", "_").replace("/", "_")
            target = self.models_dir / f"{safe_name}.gguf"
            if not target.exists():
                shutil.copy2(blob, target)
            self._write_sidecar(target, {"source": f"ollama:{model_name}", "task": "chat"})
            imported.append(
                InstalledModel(
                    filename=target.name, path=target, size_bytes=target.stat().st_size,
                    source=f"ollama:{model_name}", task="chat",
                )
            )
        return imported

    def delete(self, filename: str) -> bool:
        target = self.models_dir / filename
        if not target.is_file():
            return False
        target.unlink()
        self._sidecar_path(target).unlink(missing_ok=True)
        return True

    # ── internals ─────────────────────────────────────────────────────

    @staticmethod
    def _ollama_name(manifest: Path, manifests_root: Path) -> str:
        rel = manifest.relative_to(manifests_root)
        parts = list(rel.parts)
        if len(parts) >= 3 and parts[0] == "registry.ollama.ai":
            parts = parts[1:]  # drop host
        tag = parts[-1]
        stem = parts[-2] if len(parts) >= 2 else tag
        return f"{stem}:{tag}" if "." not in tag and ":" not in tag else stem

    @staticmethod
    def _is_gguf(path: Path) -> bool:
        try:
            with open(path, "rb") as fh:
                return fh.read(4) == _GGUF_MAGIC
        except OSError:
            return False

    @staticmethod
    def _sidecar_path(gguf: Path) -> Path:
        return gguf.with_suffix(gguf.suffix + ".json")

    def _sidecar(self, gguf: Path) -> dict:
        sp = self._sidecar_path(gguf)
        if not sp.is_file():
            return {}
        try:
            return json.loads(sp.read_text(encoding="utf-8"))
        except Exception:
            return {}

    def _write_sidecar(self, gguf: Path, payload: dict) -> None:
        self._sidecar_path(gguf).write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
        )


def _file_sha256(path: Path, chunk: int = _CHUNK) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        while True:
            block = fh.read(chunk)
            if not block:
                break
            digest.update(block)
    return digest.hexdigest()
