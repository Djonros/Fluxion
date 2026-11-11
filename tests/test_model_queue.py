"""The built-in model answers one request at a time; the design archive is shared by threads safely."""
from __future__ import annotations

import threading
import time

import pytest

from core.config import GenerationSettings
from core.llama_cpp_backend import LlamaCppBackend


class FakeLlama:
    """Stands for llama_cpp.Llama: counts how many answers run at once."""

    def __init__(self, fail: bool = False):
        self.running = 0
        self.most = 0
        self.fail = fail
        self.guard = threading.Lock()

    def _enter(self):
        with self.guard:
            self.running += 1
            self.most = max(self.most, self.running)

    def _leave(self):
        with self.guard:
            self.running -= 1

    def create_chat_completion(self, messages, stream, **params):
        if not stream:
            self._enter()
            time.sleep(0.05)
            self._leave()
            return {"choices": [{"message": {"content": "ok"}}]}
        return self._stream()

    def _stream(self):
        self._enter()
        try:
            for word in ("один ", "два ", "три"):
                time.sleep(0.02)
                if self.fail:
                    raise RuntimeError("llama: out of memory")
                yield {"choices": [{"delta": {"content": word}}]}
        finally:
            self._leave()


def backend(llm) -> LlamaCppBackend:
    b = LlamaCppBackend.__new__(LlamaCppBackend)
    b._llm = llm
    b._busy = threading.Lock()
    b.generation = GenerationSettings()
    b.model_path = "model.gguf"
    return b


def test_requests_from_several_threads_wait_their_turn():
    llm = FakeLlama()
    b = backend(llm)
    answers: list[str] = []

    def chat():
        answers.append("".join(b.stream([{"role": "user", "content": "?"}])))

    def agent():
        answers.append(b.generate([{"role": "user", "content": "?"}]))

    threads = [threading.Thread(target=f) for f in (chat, agent, chat, agent)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(10)
    assert llm.most == 1
    assert sorted(answers) == ["ok", "ok", "один два три", "один два три"]


def test_stop_frees_the_model_at_once():
    b = backend(FakeLlama())
    stream = b.stream([{"role": "user", "content": "?"}])
    assert next(stream) == "один "
    assert b._busy.locked()
    stream.close()  # «Стоп» in the chat
    assert not b._busy.locked()
    assert b.generate([{"role": "user", "content": "?"}]) == "ok"


def test_a_failed_stream_frees_the_model():
    b = backend(FakeLlama(fail=True))
    with pytest.raises(RuntimeError, match="out of memory"):
        list(b.stream([{"role": "user", "content": "?"}]))
    assert not b._busy.locked()


def test_the_lazy_engine_streams_through_the_same_queue(monkeypatch, tmp_path):
    from core.llama_cpp_backend import LazyLlamaCppBackend

    inner = backend(FakeLlama())
    lazy = LazyLlamaCppBackend.__new__(LazyLlamaCppBackend)
    monkeypatch.setattr(lazy, "_engine", lambda: inner, raising=False)
    stream = lazy.stream([{"role": "user", "content": "?"}])
    next(stream)
    assert inner._busy.locked()
    stream.close()
    assert not inner._busy.locked()


# ── the design archive from several threads ─────────────────────────────────


def test_archive_searched_from_threads_while_settings_change(tmp_path):
    from rag.archive import ArchiveIndex

    index = ArchiveIndex(tmp_path)
    index.db.execute("INSERT INTO files VALUES (?,?,?,?,?,?,?,?,?)",
                     ("a.pdf", str(tmp_path), "562001", 2024, "pdf", 1, 1, 1, ""))
    index.db.executemany("INSERT INTO chunks (text, path, place) VALUES (?,?,?)",
                         [(f"Надстройка рабочая лист {i}", "a.pdf", f"стр. {i}") for i in range(50)])
    index.db.commit()
    errors: list[Exception] = []

    def search():
        try:
            for _ in range(40):
                index.search("надстройка", top_k=3)
        except Exception as exc:  # pragma: no cover - the failure being tested
            errors.append(exc)

    def settings():
        try:
            for i in range(40):
                index.ocr = bool(i % 2)
                _ = index.file_count
        except Exception as exc:  # pragma: no cover
            errors.append(exc)

    threads = [threading.Thread(target=f) for f in (search, search, settings, search)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(20)
    index.close()
    assert not errors


def test_a_closed_archive_finds_nothing_instead_of_crashing(tmp_path):
    from rag.archive import ArchiveIndex

    index = ArchiveIndex(tmp_path)
    index.close()
    assert index.search("надстройка") == []
    index.close()  # twice is fine
