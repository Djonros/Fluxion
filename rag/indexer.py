"""Indexer: walks a directory, chunks files, embeds, and stores in ChromaDB.

Indexing is by changes: a manifest next to the store keeps, for every file
indexed, its size and modification time. A file that did not change is
skipped; a changed one loses its old chunks before the new ones are added; a
file that is gone loses its chunks. The manifest also keeps the embedding
model: another model means everything is indexed again (vectors of two models
do not mix). An index made before the manifest existed is indexed again in
full once — each file's old chunks removed first, so nothing is doubled.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
from collections.abc import Callable
from pathlib import Path

from .chunker import Chunk, TreeSitterChunker
from .documents import DOCUMENT_EXTENSIONS
from .embedder import Embedder
from .store import ChromaStore

logger = logging.getLogger(__name__)

SUPPORTED_EXTENSIONS = {
    ".py", ".js", ".ts", ".tsx", ".jsx",
    ".md", ".rst", ".txt",
    ".java", ".go", ".rs", ".c", ".cpp", ".h", ".hpp",
    ".yaml", ".yml", ".toml", ".cfg", ".ini",
    ".sh", ".sql",
    ".csv",
    *DOCUMENT_EXTENSIONS,  # PDF, Word, Excel, PowerPoint (rag/documents.py)
}

SKIP_DIRS = {
    ".git", ".hg", ".svn", "__pycache__", ".venv", "venv", "env", "node_modules",
    ".mypy_cache", ".pytest_cache", ".ruff_cache", "dist", "build",
    ".eggs", ".tox", "htmlcov", ".idea", ".vscode", ".claude",
}

MANIFEST_VERSION = 1

# progress(done, total, file name); should_stop() -> True ends the run between two files
Progress = Callable[[int, int, str], None]


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
        self.stopped = False
        self.last_stats: dict[str, int] = {}

    # -- the manifest -------------------------------------------------------

    def _manifest_path(self) -> Path | None:
        folder = getattr(self.store, "persist_dir", None)
        if folder is None:
            return None
        name = getattr(self.store, "collection_name", "codebase")
        return Path(folder) / f"index_manifest_{name}.json"

    def _embed_id(self) -> str:
        return str(getattr(self.embedder, "backend_id", "") or type(self.embedder).__name__)

    def _load_manifest(self) -> dict | None:
        path = self._manifest_path()
        if path is None or not path.is_file():
            return None
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        if data.get("version") != MANIFEST_VERSION or data.get("embedder") != self._embed_id():
            return None
        return data

    def _save_manifest(self, files: dict[str, list[int]]) -> None:
        path = self._manifest_path()
        if path is None:
            return
        data = {"version": MANIFEST_VERSION, "embedder": self._embed_id(), "files": files}
        tmp = path.with_suffix(".tmp")
        try:
            tmp.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
            os.replace(tmp, path)
        except OSError as exc:
            logger.warning("index manifest not saved: %s", exc)

    # -- indexing -------------------------------------------------------------

    def index_path(
        self,
        root: str | Path,
        progress: Progress | None = None,
        should_stop: Callable[[], bool] | None = None,
    ) -> int:
        """Index the supported files under *root* that changed since the last run.

        Returns the number of chunks stored by this run; ``last_stats`` tells
        how many files were indexed, skipped as unchanged, removed, failed.
        """
        root_path = Path(root)
        if not root_path.exists():
            raise FileNotFoundError(f"Path not found: {root_path}")

        self.stopped = False
        manifest = self._load_manifest()
        known: dict[str, list[int]] = dict(manifest["files"]) if manifest else {}
        fresh_start = manifest is None
        files = self._collect_files(root_path)
        on_disk = {str(f) for f in files}
        stats = {"indexed": 0, "unchanged": 0, "removed": 0, "failed": 0, "full": int(fresh_start)}

        # files of this folder that are gone: their chunks go too
        root_key = str(root_path)
        gone = [p for p in known if _inside(p, root_key) and p not in on_disk]
        if fresh_start:  # an index from before the manifest: what it has of this folder and is gone
            try:
                gone += [p for p in self.store.get_all_sources() if _inside(p, root_key) and p not in on_disk]
            except Exception as exc:
                logger.warning("old index sources not read: %s", exc)
        for path in gone:
            self._forget(path)
            known.pop(path, None)
            stats["removed"] += 1

        total_chunks = 0
        for done, fpath in enumerate(files):
            if should_stop is not None and should_stop():
                self.stopped = True
                break
            if progress is not None:
                progress(done, len(files), fpath.name)
            key = str(fpath)
            try:
                st = fpath.stat()
            except OSError:
                continue
            stamp = [st.st_size, st.st_mtime_ns]
            if not fresh_start and known.get(key, [None, None])[:2] == stamp:
                stats["unchanged"] += 1
                continue
            try:
                stored = self._index_one(fpath, replace=True)
            except Exception as exc:
                logger.warning("Failed to index %s: %s", fpath, exc)
                stats["failed"] += 1
                known.pop(key, None)
                continue
            known[key] = [*stamp, stored]
            total_chunks += stored
            stats["indexed"] += 1
            # a stop or a crash later keeps what is done
            if stats["indexed"] % 20 == 0:
                self._save_manifest(known)
        else:
            if progress is not None:
                progress(len(files), len(files), "")

        self._save_manifest(known)
        self.last_stats = stats
        logger.info("Index %s: %s, %d chunks", root_path, stats, total_chunks)
        return total_chunks

    def index_file(self, file_path: str | Path) -> int:
        """Index a single file (its old chunks replaced). Returns number of chunks stored."""
        fpath = Path(file_path)
        stored = self._index_one(fpath, replace=True)
        manifest = self._load_manifest()
        if manifest is not None:
            try:
                st = fpath.stat()
                manifest["files"][str(fpath)] = [st.st_size, st.st_mtime_ns, stored]
                self._save_manifest(manifest["files"])
            except OSError:
                pass
        return stored

    def clear(self) -> None:
        self.store.delete_all()
        path = self._manifest_path()
        if path is not None:
            try:
                path.unlink(missing_ok=True)
            except OSError:
                pass

    # -- Internals ----------------------------------------------------------

    def _forget(self, path: str) -> None:
        delete = getattr(self.store, "delete_file", None)
        if delete is not None:
            try:
                delete(path)
            except Exception as exc:
                logger.warning("chunks of %s not removed: %s", path, exc)

    def _index_one(self, fpath: Path, replace: bool) -> int:
        chunks = self.chunker.chunk_file(fpath)
        if replace:
            self._forget(str(fpath))
        if not chunks:
            return 0
        documents = [c.content for c in chunks]
        embeddings = self.embedder.embed(documents)
        ids = [self._chunk_id(fpath, i, c) for i, c in enumerate(chunks)]
        metadatas = [self._chunk_meta(fpath, c) for c in chunks]
        self.store.add(ids, documents, embeddings, metadatas)
        logger.info("Indexed %s → %d chunks", fpath, len(chunks))
        return len(chunks)

    def _collect_files(self, root: Path) -> list[Path]:
        """The supported files under *root*; skipped folders are not even entered."""
        root = Path(root)
        if root.is_file():
            return [root] if root.suffix.lower() in SUPPORTED_EXTENSIONS else []
        files: list[Path] = []
        for folder, dirs, names in os.walk(root):
            dirs[:] = [d for d in dirs if d not in SKIP_DIRS and not d.endswith(".egg-info")]
            for name in names:
                if os.path.splitext(name)[1].lower() in SUPPORTED_EXTENSIONS:
                    files.append(Path(folder) / name)
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


def _inside(path: str, root: str) -> bool:
    """*path* is *root* or under it (case-insensitive on Windows)."""
    p, r = os.path.normcase(os.path.abspath(path)), os.path.normcase(os.path.abspath(root))
    return p == r or p.startswith(r.rstrip("\\/") + os.sep)
