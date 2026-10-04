"""Model download: resume, 416 recovery, completeness and GGUF checks."""
from __future__ import annotations

from pathlib import Path

import pytest

import core.model_manager as mm_module
from core.model_manager import ModelDownloadError, ModelManager

MODEL_ID = "qwen2.5-coder-7b-q4km"           # free: no licence involved
CONTENT = b"GGUF" + b"x" * 996                  # a 1000-byte "model"


class FakeResponse:
    def __init__(self, status, body=b"", headers=None, cut=None):
        self.status_code = status
        self._body = body
        self.headers = headers or {}
        self._cut = cut
        self.streamed = False

    def iter_bytes(self, chunk_size=0):
        self.streamed = True
        data = self._body if self._cut is None else self._body[: self._cut]
        for i in range(0, len(data), 300):
            yield data[i:i + 300]

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class FakeServer:
    """Serves CONTENT, honouring Range like Hugging Face's CDN."""

    def __init__(self, content=CONTENT, status=None, cut=None):
        self.content, self.status, self.cut = content, status, cut
        self.requests: list[dict] = []
        self.responses: list[FakeResponse] = []

    def __call__(self, method, url, headers=None, **kw):
        headers = headers or {}
        self.requests.append(dict(headers))
        if self.status is not None:
            resp = FakeResponse(self.status)
        elif "Range" in headers:
            start = int(headers["Range"].split("=")[1].rstrip("-"))
            if start >= len(self.content):
                resp = FakeResponse(416, headers={"content-range": f"bytes */{len(self.content)}"})
            else:
                body = self.content[start:]
                resp = FakeResponse(206, body, {"content-length": str(len(body))}, self.cut)
        else:
            resp = FakeResponse(200, self.content, {"content-length": str(len(self.content))}, self.cut)
        self.responses.append(resp)
        return resp


@pytest.fixture()
def manager(tmp_path):
    return ModelManager(tmp_path)


def _serve(monkeypatch, server):
    monkeypatch.setattr(mm_module.httpx, "stream", server, raising=False)
    return server


def _part(manager) -> Path:
    entry = manager.catalog_entry(MODEL_ID)
    return manager.models_dir / (entry.filename + ".part")


def test_fresh_download(manager, monkeypatch):
    _serve(monkeypatch, FakeServer())
    path = manager.download(MODEL_ID)
    assert path.read_bytes() == CONTENT and not _part(manager).exists()


def test_416_with_complete_part_finishes(manager, monkeypatch):
    """The reported bug: a complete .part made every retry fail with 416."""
    _part(manager).write_bytes(CONTENT)
    server = _serve(monkeypatch, FakeServer())
    path = manager.download(MODEL_ID)
    assert path.read_bytes() == CONTENT
    assert server.requests == [{"Range": "bytes=1000-"}]
    assert not server.responses[0].streamed


def test_416_with_oversized_part_restarts(manager, monkeypatch):
    _part(manager).write_bytes(CONTENT + b"garbage")
    server = _serve(monkeypatch, FakeServer())
    path = manager.download(MODEL_ID)
    assert path.read_bytes() == CONTENT
    assert server.requests == [{"Range": "bytes=1007-"}, {}]


def test_resume_appends(manager, monkeypatch):
    _part(manager).write_bytes(CONTENT[:400])
    server = _serve(monkeypatch, FakeServer())
    assert manager.download(MODEL_ID).read_bytes() == CONTENT
    assert server.requests == [{"Range": "bytes=400-"}]


def test_interrupted_download_keeps_part_for_resume(manager, monkeypatch):
    _serve(monkeypatch, FakeServer(cut=600))
    with pytest.raises(ModelDownloadError, match="прервалась"):
        manager.download(MODEL_ID)
    assert _part(manager).stat().st_size == 600
    _serve(monkeypatch, FakeServer())                     # next click resumes
    assert manager.download(MODEL_ID).read_bytes() == CONTENT


def test_not_a_gguf_is_rejected(manager, monkeypatch):
    _serve(monkeypatch, FakeServer(content=b"<html>error page</html>"))
    with pytest.raises(ModelDownloadError, match="GGUF"):
        manager.download(MODEL_ID)
    assert not _part(manager).exists()
    assert not (manager.models_dir / manager.catalog_entry(MODEL_ID).filename).exists()


@pytest.mark.parametrize("status,needle", [(404, "не найден"), (429, "429"), (503, "503")])
def test_http_errors_are_readable(manager, monkeypatch, status, needle):
    _serve(monkeypatch, FakeServer(status=status))
    with pytest.raises(ModelDownloadError, match=needle) as info:
        manager.download(MODEL_ID)
    assert "http" not in str(info.value).lower()        # no long signed CDN url


def test_network_error_is_readable(manager, monkeypatch):
    class NetError(Exception):
        pass

    monkeypatch.setattr(mm_module.httpx, "HTTPError", NetError, raising=False)

    def broken(*a, **k):
        raise NetError("ConnectError")

    monkeypatch.setattr(mm_module.httpx, "stream", broken, raising=False)
    with pytest.raises(ModelDownloadError, match="интернет"):
        manager.download(MODEL_ID)


def test_import_from_disk_with_target_name(tmp_path):
    src = tmp_path / "out" / "merged.q4_K_M.gguf"
    src.parent.mkdir()
    src.write_bytes(b"GGUF")
    manager = ModelManager(tmp_path / "models")
    installed = manager.import_from_disk(src, target_name="my-coder", source="training")
    assert installed.filename == "my-coder.gguf" and installed.source == "training"
    assert (tmp_path / "models" / "my-coder.gguf").read_bytes() == b"GGUF"
    assert manager.import_from_disk(src).filename == "merged.q4_K_M.gguf"   # default unchanged
