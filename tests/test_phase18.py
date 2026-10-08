"""Phase 18.1: inference swap — config fields, backend selection, free llama.cpp."""
from __future__ import annotations

import pytest
from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QApplication

from core.backend_factory import _detect_backend_type
from core.config import Settings


@pytest.fixture(scope="module", autouse=True)
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app


@pytest.fixture()
def make_window(tmp_path, monkeypatch):
    monkeypatch.setenv("FLUXION_DISABLE_WEBENGINE", "1")

    def factory(**kwargs):
        from desktop_browser.app import FluxionWindow

        ini = tmp_path / f"settings_{len(list(tmp_path.iterdir()))}.ini"
        return FluxionWindow(qsettings=QSettings(str(ini), QSettings.IniFormat), **kwargs)

    return factory


# ── Settings: backend / gguf fields ───────────────────────────────────────


def test_settings_backend_defaults():
    s = Settings()
    assert s.backend == ""  # "" → auto (env / api-key / ollama)
    assert s.gguf_path == ""
    assert s.n_gpu_layers == 0


def test_settings_backend_from_yaml(tmp_path, monkeypatch):
    cfg = tmp_path / "config.yaml"
    cfg.write_text(
        "backend: llamacpp\ngguf_path: C:/models/qwen.gguf\nn_gpu_layers: 24\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("AI_AGENT_CONFIG", str(cfg))
    s = Settings.load()
    assert s.backend == "llamacpp"
    assert s.gguf_path == "C:/models/qwen.gguf"
    assert s.n_gpu_layers == 24


# ── backend selection ─────────────────────────────────────────────────────


def test_detect_env_overrides_config(monkeypatch):
    monkeypatch.setenv("FLUXION_BACKEND", "llamacpp")
    monkeypatch.delenv("FLUXION_API_KEY", raising=False)
    assert _detect_backend_type(Settings(backend="ollama")) == "llama_cpp"


def test_detect_config_backend(monkeypatch):
    monkeypatch.delenv("FLUXION_BACKEND", raising=False)
    monkeypatch.delenv("FLUXION_API_KEY", raising=False)
    assert _detect_backend_type(Settings(backend="llamacpp")) == "llama_cpp"
    assert _detect_backend_type(Settings(backend="llama_cpp")) == "llama_cpp"
    assert _detect_backend_type(Settings(backend="api")) == "api"
    assert _detect_backend_type(Settings(backend="ollama")) == "ollama"


def test_detect_api_key_only_without_explicit_backend(monkeypatch):
    monkeypatch.delenv("FLUXION_BACKEND", raising=False)
    monkeypatch.setenv("FLUXION_API_KEY", "sk-x")
    assert _detect_backend_type(Settings()) == "api"
    assert _detect_backend_type(Settings(backend="ollama")) == "ollama"


# ── LlamaCppBackend.from_settings: config wins over env ───────────────────


def test_from_settings_prefers_config_over_env(monkeypatch):
    import core.llama_cpp_backend as lcb

    captured: dict = {}

    def fake_init(self, **kwargs):
        captured.update(kwargs)

    monkeypatch.setattr(lcb.LlamaCppBackend, "__init__", fake_init)
    monkeypatch.setenv("FLUXION_GGUF_PATH", "C:/env.gguf")
    monkeypatch.setenv("FLUXION_N_GPU_LAYERS", "7")

    lcb.LlamaCppBackend.from_settings(Settings(gguf_path="C:/cfg.gguf", n_gpu_layers=33))
    assert captured["model_path"] == "C:/cfg.gguf"
    assert captured["n_gpu_layers"] == 33
    assert captured["n_ctx"] == Settings().generation.num_ctx

    captured.clear()
    lcb.LlamaCppBackend.from_settings(Settings())
    assert captured["model_path"] == "C:/env.gguf"
    assert captured["n_gpu_layers"] == 7


# ── 18.3: embeddings via llama.cpp ────────────────────────────────────────


def test_rag_settings_embedding_fields_from_yaml(tmp_path, monkeypatch):
    cfg = tmp_path / "config.yaml"
    cfg.write_text(
        "rag:\n  embedding_backend: llama_cpp\n  embedding_gguf: C:/models/bge-m3.gguf\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("AI_AGENT_CONFIG", str(cfg))
    rag = Settings.load().rag
    assert rag.embedding_backend == "llama_cpp"
    assert rag.embedding_gguf == "C:/models/bge-m3.gguf"


def test_rag_config_from_settings_passes_backend():
    from rag.service import RAGConfig

    rag = Settings().rag
    rag.embedding_backend = "llama_cpp"
    rag.embedding_gguf = "C:/m.gguf"
    cfg = RAGConfig.from_settings(rag)
    assert cfg.embedding_backend == "llama_cpp"
    assert cfg.embedding_gguf == "C:/m.gguf"


def test_rag_service_resolves_backend(tmp_path, monkeypatch):
    from rag.service import RAGConfig, RAGService

    # явный выбор сильнее авто-проб
    svc = RAGService(RAGConfig(embedding_backend="ollama", embedding_gguf="C:/no.gguf"))
    assert svc._resolve_backend() == "ollama"
    assert svc.embedding_backend_id == "ollama:bge-m3"

    # авто: gguf отсутствует → ollama
    svc = RAGService(RAGConfig(embedding_gguf="C:/missing.gguf"))
    assert svc._resolve_backend() == "ollama"

    # авто: gguf есть, но llama_cpp не установлен → ollama
    gguf = tmp_path / "bge.gguf"
    gguf.write_bytes(b"x")
    monkeypatch.setitem(__import__("sys").modules, "llama_cpp", None)
    svc = RAGService(RAGConfig(embedding_gguf=str(gguf)))
    assert svc._resolve_backend() == "ollama"

    # явный llama_cpp → llama_cpp (без тяжёлой инициализации)
    svc = RAGService(RAGConfig(embedding_backend="llama_cpp"))
    assert svc.embedding_backend_id == "llama_cpp:bge-m3"


class _FakeLlama:
    def __init__(self):
        self.calls = []

    def create_embedding(self, input):
        self.calls.append(list(input))
        return {"data": [{"embedding": [3.0, 4.0]} for _ in input]}


def test_llamacpp_embedder_normalises_vectors():
    import numpy as np

    from rag.embedder import LlamaCppEmbedder

    fake = _FakeLlama()
    emb = LlamaCppEmbedder(model_path="x.gguf", model_name="bge-m3", llm=fake)
    out = emb.embed(["a", "b"])
    assert out.shape == (2, 2)
    assert abs(float(np.linalg.norm(out[0])) - 1.0) < 1e-6  # [3,4]/5 = [0.6,0.8]
    assert emb.backend_id == "llama_cpp:bge-m3"
    assert emb.embed_query("q").shape == (2,)


def test_llamacpp_embedder_requires_path():
    import pytest

    from rag.embedder import LlamaCppEmbedder

    with pytest.raises(RuntimeError, match="embedding_gguf"):
        LlamaCppEmbedder.from_config(type("C", (), {"embedding_gguf": "", "embedding_model": "bge-m3"})())


# ── 18.2: model manager ───────────────────────────────────────────────────


class _FakeStream:
    def __init__(self, chunks, headers=None, status=200):
        self._chunks = chunks
        self.headers = headers or {}
        self.status_code = status

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def iter_bytes(self, chunk_size=None):
        yield from self._chunks

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


def _fake_llama_blob(tmp_path, marker=b"hello-model"):
    blob = tmp_path / "fake.gguf"
    blob.write_bytes(b"GGUF" + marker)
    return blob


def test_catalog_and_installed_listing(tmp_path):
    import json as json_mod

    from core.model_manager import ModelManager

    mm = ModelManager(models_dir=tmp_path / "models")
    ids = {m.model_id for m in mm.catalog()}
    assert "qwen2.5-coder-7b-q4km" in ids
    assert "bge-m3-gguf" in ids
    assert not mm.list_installed()

    gguf = mm.models_dir / "custom.gguf"
    gguf.write_bytes(b"GGUF-data")
    (mm.models_dir / "custom.gguf.json").write_text(
        json_mod.dumps({"source": "disk", "task": "chat"}), encoding="utf-8"
    )
    installed = mm.list_installed()
    assert len(installed) == 1
    assert installed[0].source == "disk"
    assert installed[0].size_bytes == gguf.stat().st_size


def test_download_with_progress_and_resume(tmp_path, monkeypatch):
    import hashlib

    import core.model_manager as mm_mod
    from core.model_manager import ModelInfo, ModelManager

    data = b"GGUF" + b"x" * 996
    sha = hashlib.sha256(data).hexdigest()
    mm = ModelManager(models_dir=tmp_path / "models")
    calls = []

    monkeypatch.setattr(
        mm_mod, "MODEL_CATALOG",
        [ModelInfo(model_id="tiny", name="Tiny", repo_id="t/tiny",
                   filename="tiny.gguf", size_bytes=len(data), sha256=sha)],
    )

    def fake_stream(method, url, **kwargs):
        assert method == "GET"
        return _FakeStream([data], headers={"content-length": str(len(data))})

    monkeypatch.setattr(mm_mod.httpx, "stream", fake_stream)
    path = mm.download("tiny", on_progress=lambda done, total: calls.append((done, total)))
    assert path.is_file()
    assert path.read_bytes() == data
    assert calls[-1][0] == len(data)

    # resume: частичный .part продолжает с offset (Range: bytes=400-)
    monkeypatch.setattr(
        mm_mod, "MODEL_CATALOG",
        [ModelInfo(model_id="tiny", name="Tiny", repo_id="t/tiny",
                   filename="tiny2.gguf", size_bytes=len(data), sha256=sha)],
    )
    part = mm.models_dir / "tiny2.gguf.part"
    part.write_bytes(data[:400])
    seen_ranges = []

    def fake_stream2(method, url, **kwargs):
        headers = kwargs.get("headers") or {}
        rng = headers.get("Range", "")
        seen_ranges.append(rng)
        start = int(rng.split("=")[1].split("-")[0]) if rng else 0
        return _FakeStream(
            [data[start:]],
            headers={"content-length": str(len(data) - start)},
            status=206,
        )

    monkeypatch.setattr(mm_mod.httpx, "stream", fake_stream2)
    path2 = mm.download("tiny")
    assert path2.read_bytes() == data
    assert seen_ranges == ["bytes=400-"]


def test_download_sha_mismatch_cleans_up(tmp_path, monkeypatch):
    import core.model_manager as mm_mod
    from core.model_manager import ModelInfo, ModelManager

    mm = ModelManager(models_dir=tmp_path / "models")
    monkeypatch.setattr(
        mm_mod, "MODEL_CATALOG",
        [ModelInfo(model_id="bad", name="Bad", repo_id="b/b",
                   filename="bad.gguf", size_bytes=10, sha256="0" * 64)],
    )
    monkeypatch.setattr(
        mm_mod.httpx, "stream",
        lambda method, url, **kw: _FakeStream([b"GGUF-corrupted-payload"]),
    )
    import pytest

    with pytest.raises(RuntimeError, match="SHA256 mismatch"):
        mm.download("bad")
    assert not (mm.models_dir / "bad.gguf").exists()
    assert not (mm.models_dir / "bad.gguf.part").exists()


def test_import_from_disk_and_delete(tmp_path):
    from core.model_manager import ModelManager

    src = _fake_llama_blob(tmp_path)
    mm = ModelManager(models_dir=tmp_path / "models")
    installed = mm.import_from_disk(src, task="embedding")
    assert installed.source == "disk"
    assert installed.task == "embedding"
    assert (mm.models_dir / "fake.gguf").read_bytes().startswith(b"GGUF")
    assert mm.delete("fake.gguf") is True
    assert not (mm.models_dir / "fake.gguf").exists()


def test_import_from_ollama_reuses_blobs(tmp_path, monkeypatch):
    import json as json_mod

    from core.model_manager import ModelManager

    ollama_root = tmp_path / "ollama"
    manifests = ollama_root / "manifests" / "registry.ollama.ai" / "library" / "qwen2.5-coder"
    manifests.mkdir(parents=True)
    blob = _fake_llama_blob(tmp_path, marker=b"qwen-blob")
    digest = "abc123"
    (ollama_root / "blobs").mkdir()
    shutil_copy = (ollama_root / "blobs" / f"sha256-{digest}")
    shutil_copy.write_bytes(blob.read_bytes())
    (manifests / "latest").write_text(
        json_mod.dumps(
            {"layers": [{"mediaType": "application/vnd.ollama.image.model", "digest": f"sha256:{digest}"}]}
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("OLLAMA_MODELS", str(ollama_root))

    mm = ModelManager(models_dir=tmp_path / "models")
    imported = mm.import_from_ollama()
    assert len(imported) == 1
    assert imported[0].source == "ollama:qwen2.5-coder:latest"
    assert (mm.models_dir / "qwen2.5-coder_latest.gguf").read_bytes().startswith(b"GGUF")


# ── 18.2: engine wiring + UI page ─────────────────────────────────────────


def test_wire_gguf_paths_resolves_catalog(tmp_path, monkeypatch):
    import core.model_manager as mm_mod
    from desktop_browser.engine import _wire_gguf_paths

    models = tmp_path / "models"
    models.mkdir()
    (models / "qwen2.5-coder-7b-instruct-q4_k_m.gguf").write_bytes(b"GGUF-chat")
    (models / "bge-m3-Q8_0.gguf").write_bytes(b"GGUF-emb")
    monkeypatch.setenv("FLUXION_MODELS_DIR", str(models))

    s = Settings(backend="llamacpp")
    _wire_gguf_paths(s)
    assert s.gguf_path.endswith("qwen2.5-coder-7b-instruct-q4_k_m.gguf")
    assert s.rag.embedding_gguf.endswith("bge-m3-Q8_0.gguf")

    # ollama-бэкенд: пути не трогаем
    s2 = Settings()
    _wire_gguf_paths(s2)
    assert s2.gguf_path == ""


def test_models_page_builds_with_catalog(make_window):
    window = make_window()
    assert window.pages.count() == 4
    buttons = window._model_buttons
    assert set(buttons) >= {"qwen2.5-coder-7b-q4km", "bge-m3-gguf"}
    for button in buttons.values():
        assert button.text().startswith(("Скачать", "Удалить", "Доступно в Pro"))


def test_models_page_pro_models_follow_licence(make_window, monkeypatch, tmp_path):
    import licensing

    monkeypatch.setenv("FLUXION_MODELS_DIR", str(tmp_path / "models"))
    window = make_window()
    from core.model_manager import ModelManager

    window.model_mgr = ModelManager(tmp_path / "models")
    pro_id = "qwen2.5-coder-14b-q4km"

    monkeypatch.setattr(licensing, "feature_enabled", lambda feature: False)
    window._refresh_models()
    assert window._model_buttons[pro_id].text() == "Доступно в Pro"
    window._on_model_button(pro_id)
    assert "Pro" in window.models_status.text()
    assert getattr(window, "_model_download_worker", None) is None   # nothing started

    monkeypatch.setattr(licensing, "feature_enabled", lambda feature: True)
    window._refresh_models()
    assert window._model_buttons[pro_id].text().startswith("Скачать")
    # free models never show the Pro label
    monkeypatch.setattr(licensing, "feature_enabled", lambda feature: False)
    window._refresh_models()
    assert window._model_buttons["qwen2.5-coder-7b-q4km"].text() != "Доступно в Pro"


# ── 18.6: wizard/health under llama_cpp backend ──────────────────────────


def test_check_all_llamacpp_backend(monkeypatch, tmp_path):
    from types import SimpleNamespace

    import desktop_browser.health as health

    monkeypatch.delenv("FLUXION_BACKEND", raising=False)
    monkeypatch.delenv("FLUXION_API_KEY", raising=False)
    monkeypatch.setenv("FLUXION_MODELS_DIR", str(tmp_path / "models"))
    settings = SimpleNamespace(backend="llamacpp", gguf_path="")

    statuses = health.check_all(settings, web_search=None, rag_service=None)
    keys = [s.key for s in statuses]
    assert "llamacpp" in keys and "model" in keys
    assert "ollama" not in keys
    model = next(s for s in statuses if s.key == "model")
    assert not model.available
    assert model.fix == "wizard"

    from core.model_manager import ModelManager

    mm = ModelManager()
    (mm.models_dir / "qwen2.5-coder-7b-instruct-q4_k_m.gguf").write_bytes(b"GGUF")
    statuses = health.check_all(settings, web_search=None, rag_service=None)
    model = next(s for s in statuses if s.key == "model")
    assert model.available


def test_check_gguf_model_explicit_path(tmp_path):
    from types import SimpleNamespace

    import desktop_browser.health as health

    gguf = tmp_path / "m.gguf"
    gguf.write_bytes(b"GGUF")
    assert health.check_gguf_model(SimpleNamespace(gguf_path=str(gguf))).available
    status = health.check_gguf_model(SimpleNamespace(gguf_path=str(tmp_path / "no.gguf")))
    assert not status.available
    assert status.fix == "wizard"


def test_gguf_download_worker(monkeypatch):
    import core.model_manager as mm_mod
    from desktop_browser.wizard import GgufDownloadWorker

    class FakeMM:
        def download(self, model_id, on_progress=None):
            if on_progress:
                on_progress(5, 10)
            return "C:/x.gguf"

    monkeypatch.setattr(mm_mod, "ModelManager", FakeMM)
    worker = GgufDownloadWorker()
    got = {"progress": [], "failed": [], "done": 0}
    worker.progress.connect(lambda s, p: got["progress"].append((s, p)))
    worker.failed.connect(lambda m: got["failed"].append(m))
    worker.done.connect(lambda: got.__setitem__("done", got["done"] + 1))
    worker.run()
    assert got["progress"] == [("Скачиваем модель…", 50)]
    assert got["done"] == 1
    assert not got["failed"]


# ── 18.4: model runtime (VRAM budget / lifecycle) ─────────────────────────


def test_runtime_register_get_protect():
    from core.model_runtime import ModelRuntime

    rt = ModelRuntime(vram_budget_gb=0)
    llm_chat, llm_emb = object(), object()
    rt.register("chat", llm_chat, vram_gb=5.0, n_gpu_layers=24)
    rt.register("embedding", llm_emb, vram_gb=1.0, task="embedding")
    assert rt.get("chat") is llm_chat
    assert set(rt.keys()) == {"chat", "embedding"}
    assert rt.total_vram_gb == 6.0
    rt.protect("chat")
    assert rt.unregister("embedding") is True
    assert rt.get("embedding") is None


def test_runtime_ensure_capacity_evicts_lru():
    from core.model_runtime import ModelRuntime

    rt = ModelRuntime(vram_budget_gb=8.0)
    rt.register("old", object(), vram_gb=5.0)
    rt.get("old")  # touch
    import time as _t

    _t.sleep(0.01)
    rt.register("new", object(), vram_gb=3.0)
    # chat protected, старый не защищён → вытесняется первым
    evicted = rt.ensure_capacity(4.0)
    assert evicted == ["old"]
    assert set(rt.keys()) == {"new"}
    assert rt.total_vram_gb == 3.0


def test_runtime_never_evicts_protected():
    from core.model_runtime import ModelRuntime

    rt = ModelRuntime(vram_budget_gb=6.0)
    rt.register("chat", object(), vram_gb=6.0)
    rt.protect("chat")
    assert rt.ensure_capacity(4.0) == []
    assert set(rt.keys()) == {"chat"}


def test_runtime_unlimited_budget_no_eviction():
    from core.model_runtime import ModelRuntime

    rt = ModelRuntime(vram_budget_gb=0)
    rt.register("a", object(), vram_gb=100.0)
    assert rt.ensure_capacity(50.0) == []


def test_runtime_unload_idle_and_cpu_note():
    import time as _t

    from core.model_runtime import ModelRuntime

    rt = ModelRuntime()
    rt.register("chat", object(), vram_gb=5.0, n_gpu_layers=0)
    assert rt.cpu_mode() is True
    assert "GPU-пак" in rt.cpu_mode_note()
    _t.sleep(0.02)
    assert rt.unload_idle(0.01) == ["chat"]
    assert rt.keys() == []

    rt.register("chat", object(), vram_gb=5.0, n_gpu_layers=30)
    assert rt.cpu_mode() is False
    assert rt.cpu_mode_note() == ""


def test_embedder_registers_in_runtime():
    from core.model_runtime import ModelRuntime
    from rag.embedder import LlamaCppEmbedder

    rt = ModelRuntime()
    emb = LlamaCppEmbedder(
        model_path="x.gguf", model_name="bge-m3", llm=_FakeLlama(), runtime=rt
    )
    assert "embedding" in rt.keys()
    assert rt.get("embedding") is emb._llm
    emb.unload()
    assert rt.keys() == []


# ── desktop app: own embedding model instead of Ollama ───────────────────────


def test_desktop_rag_finds_model_downloaded_after_start(tmp_path, monkeypatch):
    import sys
    import types

    from rag.service import RAGConfig, RAGService

    models = tmp_path / "models"
    models.mkdir()
    monkeypatch.setenv("FLUXION_MODELS_DIR", str(models))
    monkeypatch.setitem(sys.modules, "llama_cpp", types.ModuleType("llama_cpp"))
    svc = RAGService(RAGConfig(prefer_local_models=True))
    assert svc._resolve_backend() == "llama_cpp"
    assert svc._local_embedding_gguf() == ""

    (models / "bge-m3-Q8_0.gguf").write_bytes(b"GGUF")
    assert svc._local_embedding_gguf().endswith("bge-m3-Q8_0.gguf")
    assert svc.config.embedding_gguf.endswith("bge-m3-Q8_0.gguf")


def test_desktop_rag_without_model_says_what_to_download(tmp_path, monkeypatch):
    import sys
    import types

    from rag.service import MISSING_EMBEDDING_MODEL, RAGConfig, RAGService

    monkeypatch.setenv("FLUXION_MODELS_DIR", str(tmp_path / "models"))
    monkeypatch.setitem(sys.modules, "llama_cpp", types.ModuleType("llama_cpp"))
    svc = RAGService(RAGConfig(prefer_local_models=True, chroma_dir=str(tmp_path / "chroma")))
    with pytest.raises(RuntimeError) as error:
        svc.index(tmp_path)
    assert str(error.value) == MISSING_EMBEDDING_MODEL
    assert "Ollama" in MISSING_EMBEDDING_MODEL and "BGE-M3" in MISSING_EMBEDDING_MODEL


def test_desktop_rag_falls_back_to_ollama_without_engine(tmp_path, monkeypatch):
    import sys

    from rag.service import RAGConfig, RAGService

    monkeypatch.setenv("FLUXION_MODELS_DIR", str(tmp_path / "models"))
    monkeypatch.setitem(sys.modules, "llama_cpp", None)
    svc = RAGService(RAGConfig(prefer_local_models=True))
    assert svc._resolve_backend() == "ollama"


def test_build_engine_marks_rag_as_desktop():
    from pathlib import Path

    source = (Path(__file__).resolve().parents[1] / "desktop_browser" / "engine.py").read_text(encoding="utf-8")
    assert "prefer_local_models = True" in source

