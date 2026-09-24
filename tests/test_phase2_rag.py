"""Phase 2 milestone tests: chunking, indexing, and retrieval.

Tests are organised so that the fast chunker tests run first,
and the slow embedding-model tests are skipped if the model
cannot be loaded.
"""
from __future__ import annotations

import os
import shutil
import tempfile
from pathlib import Path

import httpx
import numpy as np
import pytest

from rag import Chunk, TreeSitterChunker, Embedder, ChromaStore, Indexer, Retriever
from rag.embedder import _ollama_model_name
from rag.service import RAGConfig

# -- Sample code used across tests -------------------------------------------

_SAMPLE_PY = '''\
"""A sample module for testing the chunker."""

import os
from pathlib import Path

MAX_RETRIES = 5


def fetch_user(user_id: int) -> dict:
    """Return a user by id."""
    return {"id": user_id, "name": "Alice"}


class UserRepo:
    """Repository for managing users in the database."""

    def __init__(self, connection):
        self.conn = connection

    def save(self, user: dict) -> None:
        """Persist a user record."""
        self.conn.execute("INSERT ...", user)


def calculate_checksum(data: bytes) -> str:
    """Compute a SHA-256 checksum."""
    import hashlib
    return hashlib.sha256(data).hexdigest()
'''

_SAMPLE_MD = '''\
# API Reference

## fetch_user

Fetches a user by their numeric id from the database.

## UserRepo

A repository class for CRUD operations on the users table.
'''


# ─── Chunker tests (fast, no model) ──────────────────────────────────────────


class TestChunker:
    @pytest.fixture()
    def chunker(self):
        return TreeSitterChunker(max_chars=1500, overlap=200)

    def test_python_chunks_contain_function_and_class(self, chunker):
        chunks = chunker.chunk_text(_SAMPLE_PY, file_path="sample.py")
        names = {c.name for c in chunks if c.node_type in ("function", "class")}
        assert "fetch_user" in names
        assert "UserRepo" in names
        assert "calculate_checksum" in names

    def test_python_chunk_has_correct_line_numbers(self, chunker):
        chunks = chunker.chunk_text(_SAMPLE_PY, file_path="sample.py")
        fn = next(c for c in chunks if c.name == "fetch_user")
        assert fn.start_line >= 7  # first line of the function
        assert fn.node_type == "function"
        assert fn.language == "python"
        assert "def fetch_user" in fn.content

    def test_python_class_chunk_includes_methods(self, chunker):
        chunks = chunker.chunk_text(_SAMPLE_PY, file_path="sample.py")
        cls = next(c for c in chunks if c.name == "UserRepo")
        assert cls.node_type == "class"
        assert "def save" in cls.content

    def test_module_level_code_captured(self, chunker):
        chunks = chunker.chunk_text(_SAMPLE_PY, file_path="sample.py")
        module_chunks = [c for c in chunks if c.node_type == "module"]
        combined = "\n".join(c.content for c in module_chunks)
        assert "import os" in combined
        assert "MAX_RETRIES" in combined

    def test_text_fallback_for_markdown(self, chunker):
        chunks = chunker.chunk_text(_SAMPLE_MD, file_path="api.md", language="markdown")
        assert len(chunks) >= 1
        assert all(c.node_type == "text" for c in chunks)
        combined = "\n".join(c.content for c in chunks)
        assert "API Reference" in combined

    def test_chunk_file_reads_from_disk(self, chunker, tmp_path):
        fpath = tmp_path / "mod.py"
        fpath.write_text(_SAMPLE_PY, encoding="utf-8")
        chunks = chunker.chunk_file(fpath)
        assert len(chunks) >= 3
        assert any(c.name == "fetch_user" for c in chunks)


# ─── Ollama embedder unit tests (mocked HTTP) ────────────────────────────────


class _FakeResponse:
    def __init__(self, status_code=200, payloads=None, text=""):
        self.status_code = status_code
        self._payloads = payloads or []
        self.text = text

    def json(self):
        return {"embeddings": self._payloads}


class TestOllamaEmbedderUnit:
    @pytest.fixture()
    def calls(self, monkeypatch):
        recorded: list[dict] = []

        def fake_post(url, json=None, timeout=None):
            recorded.append({"url": url, "json": json})
            n = len(json["input"])
            return _FakeResponse(payloads=[[0.1, 0.2] for _ in range(n)])

        monkeypatch.setattr("rag.embedder.httpx.post", fake_post)
        return recorded

    def test_hf_name_mapped_to_ollama_tag(self):
        assert _ollama_model_name("BAAI/bge-m3") == "bge-m3"
        assert _ollama_model_name("bge-m3") == "bge-m3"
        assert _ollama_model_name("hf.co/BAAI/bge-m3-GGUF") == "hf.co/BAAI/bge-m3-GGUF"
        assert _ollama_model_name("mxbai-embed-large:latest") == "mxbai-embed-large:latest"

    def test_embed_posts_to_embed_endpoint(self, calls):
        emb = Embedder(host="http://localhost:11434/", model_name="BAAI/bge-m3")
        out = emb.embed(["hello"])
        assert calls[0]["url"] == "http://localhost:11434/api/embed"
        assert calls[0]["json"]["model"] == "bge-m3"
        assert calls[0]["json"]["input"] == ["hello"]
        assert out.shape == (1, 2)
        assert out.dtype == np.float32

    def test_embed_normalizes_vectors(self, monkeypatch):
        def fake_post(url, json=None, timeout=None):
            n = len(json["input"])
            return _FakeResponse(payloads=[[3.0, 4.0] for _ in range(n)])

        monkeypatch.setattr("rag.embedder.httpx.post", fake_post)
        emb = Embedder()
        out = emb.embed(["a", "b"])
        norms = np.linalg.norm(out, axis=1)
        assert np.allclose(norms, 1.0)

    def test_embed_batches_requests(self, calls):
        emb = Embedder(batch_size=2)
        emb.embed(["t1", "t2", "t3", "t4", "t5"])
        assert len(calls) == 3
        assert [len(c["json"]["input"]) for c in calls] == [2, 2, 1]

    def test_missing_model_hint(self, monkeypatch):
        monkeypatch.setattr(
            "rag.embedder.httpx.post",
            lambda url, json=None, timeout=None: _FakeResponse(
                status_code=404, text='{"error":"model not found"}'
            ),
        )
        emb = Embedder(model_name="bge-m3")
        with pytest.raises(RuntimeError, match=r"ollama pull bge-m3"):
            emb.embed(["x"])

    def test_unreachable_server_hint(self, monkeypatch):
        def raise_connect(url, json=None, timeout=None):
            raise httpx.ConnectError("refused")

        monkeypatch.setattr("rag.embedder.httpx.post", raise_connect)
        emb = Embedder(host="http://localhost:11434")
        with pytest.raises(RuntimeError, match="недоступна"):
            emb.embed(["x"])

    def test_empty_input_returns_empty_array(self, calls):
        emb = Embedder()
        out = emb.embed([])
        assert out.shape[0] == 0
        assert calls == []


class TestRAGConfigHost:
    def _settings(self):
        from core.config import RagSettings

        return RagSettings()

    def test_from_settings_uses_explicit_host(self):
        cfg = RAGConfig.from_settings(self._settings(), ollama_host="http://gpu-box:11434")
        assert cfg.ollama_host == "http://gpu-box:11434"

    def test_from_settings_falls_back_to_env(self, monkeypatch):
        monkeypatch.setenv("OLLAMA_HOST", "http://env-host:11434")
        cfg = RAGConfig.from_settings(self._settings())
        assert cfg.ollama_host == "http://env-host:11434"

    def test_service_wires_host_into_embedder(self, monkeypatch):
        from rag.service import RAGService

        monkeypatch.setenv("OLLAMA_HOST", "http://wired:11434")
        monkeypatch.setattr("rag.service.ChromaStore", _FakeStore)
        svc = RAGService(RAGConfig.from_settings(self._settings()))
        svc._ensure_init()
        assert svc.embedder.host == "http://wired:11434"
        assert svc.embedder.model_name == "bge-m3"


class _FakeStore:
    def __init__(self, persist_dir):
        self.persist_dir = persist_dir


# ─── Full pipeline tests (slow: requires live Ollama + bge-m3) ────────────────

@pytest.fixture(scope="module")
def embedder():
    emb = Embedder(
        host=os.environ.get("OLLAMA_HOST", "http://localhost:11434"),
        model_name="bge-m3",
    )
    try:
        _ = emb.dimension  # probes the live Ollama server / model
    except Exception as exc:
        pytest.skip(f"Ollama embeddings not available: {exc}")
    return emb


@pytest.fixture()
def temp_store(tmp_path):
    store = ChromaStore(persist_dir=str(tmp_path / "chroma"))
    yield store
    store.delete_all()


@pytest.fixture()
def temp_project(tmp_path):
    """Create a small project tree to index."""
    root = tmp_path / "project"
    root.mkdir()
    (root / "users.py").write_text(_SAMPLE_PY, encoding="utf-8")
    (root / "api.md").write_text(_SAMPLE_MD, encoding="utf-8")
    return root


class TestIndexing:
    def test_index_returns_nonzero_chunks(self, embedder, temp_store, temp_project):
        indexer = Indexer(TreeSitterChunker(), embedder, temp_store)
        count = indexer.index_path(temp_project)
        assert count > 0, "expected at least one chunk indexed"
        assert temp_store.count() == count

    def test_index_captures_function_names(self, embedder, temp_store, temp_project):
        indexer = Indexer(TreeSitterChunker(), embedder, temp_store)
        indexer.index_path(temp_project)
        sources = temp_store.get_all_sources()
        assert any("users.py" in s for s in sources)


class TestRetrieval:
    def test_retrieve_user_function(self, embedder, temp_store, temp_project):
        indexer = Indexer(TreeSitterChunker(), embedder, temp_store)
        indexer.index_path(temp_project)
        retriever = Retriever(embedder, temp_store, use_rerank=False, top_k=5)
        results = retriever.retrieve("fetch a user by id")
        assert len(results) > 0
        top = results[0]
        assert "user" in top.content.lower()
        assert results[0].score >= 0.0

    def test_retrieve_returns_correct_metadata(self, embedder, temp_store, temp_project):
        indexer = Indexer(TreeSitterChunker(), embedder, temp_store)
        indexer.index_path(temp_project)
        retriever = Retriever(embedder, temp_store, use_rerank=False, top_k=5)
        results = retriever.retrieve("User repository class")
        assert len(results) > 0
        assert all(isinstance(r.file_path, str) for r in results)
        assert all(r.start_line >= 0 for r in results)
        assert all(r.end_line >= r.start_line for r in results)

    def test_retrieve_checksum(self, embedder, temp_store, temp_project):
        indexer = Indexer(TreeSitterChunker(), embedder, temp_store)
        indexer.index_path(temp_project)
        retriever = Retriever(embedder, temp_store, use_rerank=False, top_k=5)
        results = retriever.retrieve("calculate SHA-256 checksum")
        assert len(results) > 0
        combined = " ".join(r.content for r in results[:3])
        assert "checksum" in combined.lower() or "hashlib" in combined.lower()
