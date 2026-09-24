"""Indexer: walks a directory, chunks files, embeds, and stores in ChromaDB."""
from __future__ import annotations

import hashlib
import logging
from pathlib import Path

from .chunker import Chunk, TreeSitterChunker
from .embedder import Embedder
from .store import ChromaStore

logger = logging.getLogger(__name__)

SUPPORTED_EXTENSIONS = {
    ".py", ".js", ".ts", ".tsx", ".jsx",
    ".md", ".rst", ".txt",
    ".java", ".go", ".rs", ".c", ".cpp", ".h", ".hpp",
    ".yaml", ".yml", ".toml", ".cfg", ".ini",
    ".sh", ".sql",
}

SKIP_DIRS = {
    ".git", "__pycache__", ".venv", "venv", "node_modules",
    ".mypy_cache", ".pytest_cache", ".ruff_cache", "dist", "build",
    ".eggs", ".tox", "htmlcov", ".idea", ".vscode",
}


class Indexer:
    """Incrementally indexes directories into ChromaDB."""

    def __init__(
        self,
        chunker: TreeSitterChunker,
        embedder: Embedder,
        store: ChromaStore,
    ):
        self.chunker = chunker
        self.embedder = embedder
        self.store = store

    def index_path(self, root: str | Path) -> int:
        """Index all supported files under *root*. Returns number of chunks stored."""
        root_path = Path(root)
        if not root_path.exists():
            raise FileNotFoundError(f"Path not found: {root_path}")

        files = self._collect_files(root_path)
        total_chunks = 0

        for fpath in files:
            try:
                chunks = self.chunker.chunk_file(fpath)
            except Exception as exc:
                logger.warning("Failed to chunk %s: %s", fpath, exc)
                continue
            if not chunks:
                continue

            documents = [c.content for c in chunks]
            embeddings = self.embedder.embed(documents)
            ids = [self._chunk_id(fpath, i, c) for i, c in enumerate(chunks)]
            metadatas = [self._chunk_meta(fpath, c) for c in chunks]

            self.store.add(ids, documents, embeddings, metadatas)
            total_chunks += len(chunks)
            logger.info("Indexed %s → %d chunks", fpath, len(chunks))

        logger.info("Total: %d chunks from %d files", total_chunks, len(files))
        return total_chunks

    def index_file(self, file_path: str | Path) -> int:
        """Index a single file. Returns number of chunks stored."""
        fpath = Path(file_path)
        chunks = self.chunker.chunk_file(fpath)
        if not chunks:
            return 0
        documents = [c.content for c in chunks]
        embeddings = self.embedder.embed(documents)
        ids = [self._chunk_id(fpath, i, c) for i, c in enumerate(chunks)]
        metadatas = [self._chunk_meta(fpath, c) for c in chunks]
        self.store.add(ids, documents, embeddings, metadatas)
        return len(chunks)

    def clear(self) -> None:
        self.store.delete_all()

    # -- Internals ----------------------------------------------------------

    def _collect_files(self, root: Path) -> list[Path]:
        files: list[Path] = []
        for path in root.rglob("*"):
            if path.is_dir():
                continue
            if any(part in SKIP_DIRS for part in path.parts):
                continue
            if path.suffix.lower() in SUPPORTED_EXTENSIONS:
                files.append(path)
        return sorted(files)

    @staticmethod
    def _chunk_id(file_path: Path, index: int, chunk: Chunk) -> str:
        h = hashlib.md5(
            f"{file_path}:{chunk.start_line}:{chunk.end_line}:{chunk.name}".encode()
        ).hexdigest()[:12]
        return f"{index}_{h}"

    @staticmethod
    def _chunk_meta(file_path: Path, chunk: Chunk) -> dict:
        return {
            "file_path": str(file_path),
            "start_line": chunk.start_line,
            "end_line": chunk.end_line,
            "node_type": chunk.node_type,
            "name": chunk.name,
            "language": chunk.language,
        }
