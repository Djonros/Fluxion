"""The project index by changes: unchanged files skipped, changed and removed files lose their old
chunks, skipped folders not entered, «Стоп» between files, an index from before the manifest."""
from __future__ import annotations

import os
import time

import numpy as np
import pytest

from rag.chunker import TreeSitterChunker
from rag.indexer import Indexer

chromadb = pytest.importorskip("chromadb")

from rag.store import ChromaStore  # noqa: E402


class FakeEmbedder:
    """Vectors without a model: counts what it was asked to embed."""

    def __init__(self, backend_id: str = "fake:1"):
        self.backend_id = backend_id
        self.embedded = 0

    def embed(self, texts):
        self.embedded += len(texts)
        return np.array([[float(len(t) % 7 + 1), 1.0, float(i % 3)] for i, t in enumerate(texts)])


@pytest.fixture
def project(tmp_path):
    root = tmp_path / "project"
    root.mkdir()
    (root / "a.py").write_text("def a():\n    return 1\n", encoding="utf-8")
    (root / "b.md").write_text("# Надстройка\n\nЛист 3 мм, сталь С235.\n", encoding="utf-8")
    for skipped in (".git", "node_modules", "venv", "pkg.egg-info"):
        (root / skipped).mkdir()
        (root / skipped / "inside.py").write_text("x = 1\n", encoding="utf-8")
    return root


@pytest.fixture
def make(tmp_path):
    stores = []

    def build(embedder=None):
        store = ChromaStore(persist_dir=str(tmp_path / "chroma"))
        stores.append(store)
        return Indexer(TreeSitterChunker(), embedder or FakeEmbedder(), store), store
    return build


def _touch(path, text):
    path.write_text(text, encoding="utf-8")
    later = time.time() + 5
    os.utime(path, (later, later))


def test_skipped_folders_are_not_entered(project):
    names = {p.relative_to(project).as_posix() for p in Indexer(None, None, None)._collect_files(project)}
    assert names == {"a.py", "b.md"}


def test_unchanged_files_are_skipped(project, make):
    indexer, store = make()
    first = indexer.index_path(project)
    assert first > 0 and store.count() == first
    assert indexer.last_stats["indexed"] == 2 and indexer.last_stats["full"] == 1
    embedded = indexer.embedder.embedded
    again, store2 = make(indexer.embedder)  # another start of the program, same model
    assert again.index_path(project) == 0
    assert again.last_stats["unchanged"] == 2 and again.last_stats["indexed"] == 0
    assert indexer.embedder.embedded == embedded and store2.count() == first


def test_a_changed_file_replaces_its_chunks(project, make):
    indexer, store = make()
    indexer.index_path(project)
    _touch(project / "a.py", "def a():\n    return 1\n\n\ndef b():\n    return 2\n\n\ndef c():\n    return 3\n")
    indexer.index_path(project)
    assert indexer.last_stats["indexed"] == 1 and indexer.last_stats["unchanged"] == 1
    docs = store._collection.get(where={"file_path": str(project / "a.py")}, include=["documents"])["documents"]
    assert any("def c" in d for d in docs)
    assert len(docs) == len(set(docs)), "no stale or doubled chunks"


def test_a_removed_file_leaves_the_index(project, make):
    indexer, store = make()
    indexer.index_path(project)
    (project / "b.md").unlink()
    indexer.index_path(project)
    assert indexer.last_stats["removed"] == 1
    assert str(project / "b.md") not in store.get_all_sources()
    assert str(project / "a.py") in store.get_all_sources()


def test_another_embedding_model_indexes_everything_again(project, make):
    indexer, _ = make()
    indexer.index_path(project)
    other, _ = make(FakeEmbedder("fake:2"))
    other.index_path(project)
    assert other.last_stats["indexed"] == 2 and other.last_stats["full"] == 1


def test_an_index_from_before_the_manifest_is_rebuilt_without_doubles(project, make, tmp_path):
    indexer, store = make()
    indexer.index_path(project)
    count = store.count()
    old_gone = str(project / "old.md")  # a file the old index had and that is gone now
    store.add(["old_1"], ["старое"], np.array([[1.0, 1.0, 1.0]]), [{"file_path": old_gone}])
    indexer._manifest_path().unlink()  # as made by 0.29.0
    again, _ = make()
    again.index_path(project)
    assert again.last_stats["full"] == 1 and again.last_stats["indexed"] == 2
    assert old_gone not in store.get_all_sources()
    assert store.count() == count


def test_stop_between_files_keeps_what_is_done(project, make):
    for i in range(5):
        (project / f"n{i}.md").write_text(f"# Заметка {i}\n", encoding="utf-8")
    indexer, store = make()
    seen: list[tuple[int, int, str]] = []
    indexer.index_path(project, progress=lambda d, t, n: seen.append((d, t, n)), should_stop=lambda: len(seen) >= 3)
    assert indexer.stopped and indexer.last_stats["indexed"] == 3
    assert seen[0][1] == 7
    again, _ = make()
    again.index_path(project)
    assert again.last_stats["unchanged"] == 3 and again.last_stats["indexed"] == 4 and not again.stopped


def test_progress_reaches_the_end(project, make):
    indexer, _ = make()
    seen: list[tuple[int, int, str]] = []
    indexer.index_path(project, progress=lambda d, t, n: seen.append((d, t, n)))
    assert seen[-1] == (2, 2, "")


def test_clear_forgets_the_manifest(project, make):
    indexer, store = make()
    indexer.index_path(project)
    indexer.clear()
    assert store.count() == 0 and not indexer._manifest_path().exists()
    indexer.index_path(project)
    assert indexer.last_stats["indexed"] == 2
