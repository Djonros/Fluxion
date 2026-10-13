"""Model catalog: bundled JSON file, website catalog without a release, page filters."""
from __future__ import annotations

import json

import httpx
import pytest

from core import model_manager as mm
from core.model_manager import (
    MODEL_CATALOG,
    REMOTE_CACHE_NAME,
    ModelManager,
    catalog_url,
    entry_from_dict,
    merge_catalog,
    parse_catalog,
)


def _rec(**kwargs) -> dict:
    base = dict(
        id="new-model", name="New Model 3B", repo_id="someone/New-GGUF",
        filename="new-3b-Q4_K_M.gguf", size_bytes=2_000_000_000, task="chat",
        vram_gb=3, tags=["Chat", "text"], summary="Тексты",
    )
    base.update(kwargs)
    return base


# ── bundled file ──────────────────────────────────────────────────────────

def test_bundled_catalog_is_valid():
    raw = json.loads(mm.CATALOG_FILE.read_text(encoding="utf-8"))["models"]
    assert len(MODEL_CATALOG) == len(raw)          # no record silently dropped
    assert [m.model_id for m in MODEL_CATALOG if m.default] == ["qwen2.5-coder-7b-q4km"]
    assert len({m.filename.lower() for m in MODEL_CATALOG}) == len(MODEL_CATALOG)
    text = [m for m in MODEL_CATALOG if "text" in m.tags]
    assert text and any(not m.pro for m in text) and all(m.task == "chat" for m in text)
    assert any(m.task == "embedding" for m in MODEL_CATALOG)


# ── record validation ─────────────────────────────────────────────────────

def test_valid_record():
    entry = entry_from_dict(_rec())
    assert entry.model_id == "new-model" and entry.tags == ["chat", "text"]
    assert entry_from_dict(_rec(id="", model_id="old-key")).model_id == "old-key"


@pytest.mark.parametrize("bad", [
    {"repo_id": "https://evil.example/x"},
    {"repo_id": "a/../b"},
    {"repo_id": "no-slash"},
    {"filename": "../outside.gguf"},
    {"filename": "dir/model.gguf"},
    {"filename": "dir\\model.gguf"},
    {"filename": "model.bin"},
    {"filename": "big-00001-of-00003.gguf"},
    {"task": "vision"},
    {"sha256": "abc"},
    {"size_bytes": 0},
    {"size_bytes": "lots"},
    {"name": ""},
    {"id": ""},
])
def test_bad_records_are_rejected(bad):
    assert entry_from_dict(_rec(**bad)) is None


def test_parse_catalog_skips_bad_and_duplicate_records():
    data = {"models": [_rec(), _rec(), "junk", _rec(id="x", filename="a.txt"), _rec(id="y", filename="y.gguf")]}
    assert [m.model_id for m in parse_catalog(data)] == ["new-model", "y"]
    assert parse_catalog([1, 2]) == [] and parse_catalog({"models": "no"}) == []


# ── merge with the website catalog ────────────────────────────────────────

def test_merge_updates_known_and_appends_new():
    default = next(m for m in MODEL_CATALOG if m.default)
    remote = parse_catalog({"models": [
        _rec(id=default.model_id, name="Renamed", repo_id=default.repo_id, filename=default.filename,
             default=False, task="embedding", summary="new text"),
        _rec(default=True),
    ]})
    merged = merge_catalog(list(MODEL_CATALOG), remote, "0.13.0")
    assert [m.model_id for m in merged[: len(MODEL_CATALOG)]] == [m.model_id for m in MODEL_CATALOG]
    updated = next(m for m in merged if m.model_id == default.model_id)
    assert updated.name == "Renamed" and updated.summary == "new text"
    assert updated.default and updated.task == "chat"     # never changed remotely
    assert merged[-1].model_id == "new-model" and not merged[-1].default


def test_merge_skips_taken_file_names_and_newer_app_versions():
    taken = MODEL_CATALOG[0].filename.upper()
    remote = parse_catalog({"models": [
        _rec(id="clash", filename=taken),
        _rec(id="future", filename="f.gguf", min_app_version="0.20.0"),
        _rec(id="now", filename="n.gguf", min_app_version="0.13.0"),
    ]})
    ids = [m.model_id for m in merge_catalog(list(MODEL_CATALOG), remote, "0.13.0")]
    assert "clash" not in ids and "future" not in ids and "now" in ids


def test_catalog_url(monkeypatch):
    monkeypatch.delenv("FLUXION_CATALOG_URL")
    assert catalog_url() == mm.DEFAULT_CATALOG_URL
    for value in ("off", "", "0", " NO "):
        monkeypatch.setenv("FLUXION_CATALOG_URL", value)
        assert catalog_url() == ""
    monkeypatch.setenv("FLUXION_CATALOG_URL", "https://example.org/c.json")
    assert catalog_url() == "https://example.org/c.json"


# ── fetching ──────────────────────────────────────────────────────────────

class _Resp:
    def __init__(self, status, payload=None):
        self.status_code = status
        self._payload = payload

    def json(self):
        if self._payload is None:
            raise ValueError("no json")
        return self._payload


def test_fetch_caches_and_catalog_uses_it(tmp_path, monkeypatch):
    seen = {}

    def fake_get(url, **kwargs):
        seen["url"] = url
        return _Resp(200, {"app_version": "0.13.0", "models": [_rec(), {"id": "broken"}]})

    monkeypatch.setattr(mm.httpx, "get", fake_get)
    manager = ModelManager(tmp_path / "models")
    assert manager.catalog() == list(MODEL_CATALOG)
    assert manager.fetch_remote_catalog("https://example.org/c.json") == 1
    assert seen["url"] == "https://example.org/c.json"
    cached = json.loads((tmp_path / "models" / REMOTE_CACHE_NAME).read_text(encoding="utf-8"))
    assert [m["id"] for m in cached["models"]] == ["new-model"]
    assert manager.catalog_entry("new-model").repo_id == "someone/New-GGUF"
    assert len(manager.catalog()) == len(MODEL_CATALOG) + 1


@pytest.mark.parametrize("response, words", [
    (_Resp(404), "404"),
    (_Resp(200), "повреждён"),
    (_Resp(200, {"models": [{"id": "x"}]}), "пуст"),
])
def test_fetch_errors_keep_the_old_cache(tmp_path, monkeypatch, response, words):
    manager = ModelManager(tmp_path / "models")
    monkeypatch.setattr(mm.httpx, "get", lambda url, **k: _Resp(200, {"models": [_rec()]}))
    manager.fetch_remote_catalog("https://example.org/c.json")
    monkeypatch.setattr(mm.httpx, "get", lambda url, **k: response)
    with pytest.raises(RuntimeError, match=words):
        manager.fetch_remote_catalog("https://example.org/c.json")
    assert manager.catalog_entry("new-model") is not None


def test_fetch_offline_and_switched_off(tmp_path, monkeypatch):
    manager = ModelManager(tmp_path / "models")

    def fail(*a, **k):
        raise httpx.ConnectError("offline")

    monkeypatch.setattr(mm.httpx, "get", fail)
    with pytest.raises(RuntimeError, match="Нет связи"):
        manager.fetch_remote_catalog("https://example.org/c.json")
    with pytest.raises(RuntimeError, match="выключено"):
        manager.fetch_remote_catalog()          # FLUXION_CATALOG_URL=off in tests


def test_gpu_memory_from_nvidia_smi(monkeypatch):
    import subprocess
    from types import SimpleNamespace

    monkeypatch.setattr(mm.shutil, "which", lambda name: "nvidia-smi")
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: SimpleNamespace(returncode=0, stdout="8192\n4096\n"))
    assert mm.detect_gpu_memory_gb() == 8.0

    def missing(*a, **k):
        raise FileNotFoundError("nvidia-smi")

    monkeypatch.setattr(subprocess, "run", missing)
    assert mm.detect_gpu_memory_gb() == 0.0
    monkeypatch.setattr(mm.shutil, "which", lambda name: None)
    assert mm.detect_gpu_memory_gb() == 0.0


# ── "Модели" page ─────────────────────────────────────────────────────────

@pytest.fixture()
def window(tmp_path, monkeypatch):
    monkeypatch.setenv("FLUXION_DISABLE_WEBENGINE", "1")
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtCore import QSettings
    from PySide6.QtWidgets import QApplication

    QApplication.instance() or QApplication([])
    from desktop_browser.app import FluxionWindow

    win = FluxionWindow(qsettings=QSettings(str(tmp_path / "s.ini"), QSettings.IniFormat))
    yield win
    worker = getattr(win, "_catalog_worker", None)
    if worker is not None:
        worker.wait(5000)


def _shown(win) -> set[str]:
    return {mid for mid, (card, _e) in win._model_cards.items() if not card.isHidden()}


def _pick(combo, data):
    combo.setCurrentIndex(combo.findData(data))


def test_page_filters(window):
    every = {m.model_id for m in MODEL_CATALOG}
    assert _shown(window) == every

    _pick(window.models_kind, "text")
    text = _shown(window)
    assert text and "qwen2.5-7b-q4km" in text and "qwen2.5-coder-7b-q4km" not in text

    _pick(window.models_kind, "rag")
    assert _shown(window) == {"bge-m3-gguf"}

    _pick(window.models_kind, "pro")
    assert _shown(window) == {m.model_id for m in MODEL_CATALOG if m.pro}

    _pick(window.models_kind, "")
    window.models_search.setText("coder 14b")
    assert _shown(window) == {"qwen2.5-coder-14b-q4km"}
    assert window.models_empty.isHidden()

    window.models_search.setText("нет такой модели")
    assert _shown(window) == set() and not window.models_empty.isHidden()

    window.models_search.setText("")
    _pick(window.models_vram, 4.0)
    assert _shown(window) == {m.model_id for m in MODEL_CATALOG if m.vram_gb <= 4}


def test_page_my_video_card_and_new_models(window, monkeypatch):
    window._on_catalog_fetched(0, "Нет связи", 6.0)
    assert window.models_vram.itemText(1) == "Моя видеокарта (6 ГБ)"
    _pick(window.models_vram, -1.0)
    assert _shown(window) == {m.model_id for m in MODEL_CATALOG if m.vram_gb <= 6}
    assert "Нет связи" in window.models_refresh_button.toolTip()

    monkeypatch.setattr(mm.httpx, "get", lambda url, **k: _Resp(200, {"models": [_rec(vram_gb=3)]}))
    window.model_mgr.fetch_remote_catalog("https://example.org/c.json")
    window._on_catalog_fetched(1, "", 6.0)
    labels = [window.models_vram.itemText(i) for i in range(window.models_vram.count())]
    assert sum(label.startswith("Моя видеокарта") for label in labels) == 1
    assert "new-model" in window._model_cards and "new-model" in window._model_buttons
    assert window._model_buttons["new-model"].text().startswith("Скачать")


# ── "⚙ Настройки чата" → «Модель» with the embedded engine ───────────────

def test_chat_model_choice_with_embedded_engine(tmp_path, monkeypatch):
    monkeypatch.setenv("FLUXION_DISABLE_WEBENGINE", "1")
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtCore import QSettings
    from PySide6.QtWidgets import QApplication

    from core.config import Settings
    from core.llama_cpp_backend import LazyLlamaCppBackend
    from desktop_browser.app import FluxionWindow

    QApplication.instance() or QApplication([])
    models = tmp_path / "models"
    manager = ModelManager(models)
    for model_id in ("qwen2.5-coder-7b-q4km", "qwen2.5-7b-q4km", "bge-m3-gguf"):
        entry = manager.catalog_entry(model_id)
        (models / entry.filename).write_bytes(b"GGUF")
        manager._write_sidecar(models / entry.filename, {"source": model_id, "task": entry.task})

    settings = Settings()
    backend = LazyLlamaCppBackend(settings)
    ini = str(tmp_path / "s.ini")
    win = FluxionWindow(settings=settings, backend=backend, qsettings=QSettings(ini, QSettings.IniFormat))
    combo = win.model_combo
    names = [combo.itemText(i) for i in range(combo.count())]
    assert combo.isEnabled()
    assert set(names) == {manager.catalog_entry(i).name for i in ("qwen2.5-7b-q4km", "qwen2.5-coder-7b-q4km")}
    assert combo.currentText() == manager.catalog_entry("qwen2.5-coder-7b-q4km").name   # the default

    text_model = manager.catalog_entry("qwen2.5-7b-q4km")
    combo.setCurrentIndex(combo.findText(text_model.name))
    win._on_model_selected(combo.currentText())
    assert backend.model_path == str(models / text_model.filename)
    assert text_model.filename in win._status_model_name()

    # the choice survives a restart
    settings2 = Settings()
    backend2 = LazyLlamaCppBackend(settings2)
    win2 = FluxionWindow(settings=settings2, backend=backend2, qsettings=QSettings(ini, QSettings.IniFormat))
    assert backend2.model_path == str(models / text_model.filename)
    assert win2.model_combo.currentText() == text_model.name


# ── "Скачать по ссылке…": any GGUF from Hugging Face ──────────────────────

@pytest.mark.parametrize("link, expected", [
    ("bartowski/Qwen2.5-7B-Instruct-GGUF", ("bartowski/Qwen2.5-7B-Instruct-GGUF", "", "")),
    ("https://huggingface.co/bartowski/Qwen2.5-7B-Instruct-GGUF", ("bartowski/Qwen2.5-7B-Instruct-GGUF", "", "")),
    ("https://huggingface.co/a/b-GGUF/blob/main/b-Q4_K_M.gguf?download=true", ("a/b-GGUF", "b-Q4_K_M.gguf", "")),
    ("huggingface.co/a/b/resolve/main/sub/x.gguf", ("a/b", "sub/x.gguf", "")),
    ("hf.co/unsloth/gemma-3-4b-it-GGUF:Q4_K_M", ("unsloth/gemma-3-4b-it-GGUF", "", "Q4_K_M")),
    ("https://huggingface.co/a/b/tree/main", ("a/b", "", "")),
])
def test_parse_hf_link(link, expected):
    assert mm.parse_hf_link(link) == expected


@pytest.mark.parametrize("link", ["", "qwen", "https://example.org/a/b", "ftp://huggingface.co/a/b",
                                  "https://huggingface.co/a/b/resolve/main/../x.gguf"])
def test_parse_hf_link_rejects(link):
    with pytest.raises(ValueError):
        mm.parse_hf_link(link)


SHA = "a" * 64
TREE = [
    {"type": "directory", "path": "sub"},
    {"type": "file", "path": "README.md", "size": 10},
    {"type": "file", "path": "m-Q8_0.gguf", "size": 8_000, "lfs": {"oid": SHA, "size": 8_000_000_000}},
    {"type": "file", "path": "m-Q4_K_M.gguf", "size": 4_000, "lfs": {"oid": SHA, "size": 4_000_000_000}},
    {"type": "file", "path": "big/m-Q8_0-00001-of-00002.gguf", "lfs": {"size": 9}},
    {"type": "file", "path": "mmproj-f16.gguf", "lfs": {"size": 1}},
]


def test_list_hf_gguf_files(monkeypatch):
    seen = {}

    def fake_get(url, **kwargs):
        seen["url"] = url
        return _Resp(200, TREE)

    monkeypatch.setattr(mm.httpx, "get", fake_get)
    files = mm.list_hf_gguf_files("a/m-GGUF")
    assert seen["url"] == "https://huggingface.co/api/models/a/m-GGUF/tree/main?recursive=1"
    assert [f.path for f in files][:2] == ["m-Q4_K_M.gguf", "m-Q8_0.gguf"]       # usable first, small first
    assert files[0].size_bytes == 4_000_000_000 and files[0].sha256 == SHA
    assert {mm.hf_file_problem(f.path) != "" for f in files[2:]} == {True}


@pytest.mark.parametrize("status, words", [(401, "закрытая"), (404, "не найдена"), (500, "500")])
def test_list_hf_gguf_files_errors(monkeypatch, status, words):
    monkeypatch.setattr(mm.httpx, "get", lambda url, **k: _Resp(status, []))
    with pytest.raises(mm.ModelDownloadError, match=words):
        mm.list_hf_gguf_files("a/b")


class _Stream:
    def __init__(self, body: bytes):
        self.status_code = 200
        self.headers = {"content-length": str(len(body))}
        self._body = body

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def iter_bytes(self, chunk_size=0):
        yield self._body


def test_download_hf_file(tmp_path, monkeypatch):
    import hashlib

    body = b"GGUF" + b"\0" * 60
    seen = {}

    def fake_stream(method, url, **kwargs):
        seen["url"] = url
        return _Stream(body)

    monkeypatch.setattr(mm.httpx, "stream", fake_stream)
    manager = ModelManager(tmp_path / "models")
    file = mm.HfFile("a/m-GGUF", "sub/m-Q4_K_M.gguf", len(body), hashlib.sha256(body).hexdigest())
    path = manager.download_hf_file(file)
    assert seen["url"] == "https://huggingface.co/a/m-GGUF/resolve/main/sub/m-Q4_K_M.gguf"
    assert path == tmp_path / "models" / "m-Q4_K_M.gguf" and path.read_bytes() == body
    installed = {m.filename: m for m in manager.list_installed()}
    assert installed["m-Q4_K_M.gguf"].source == "hf:a/m-GGUF/sub/m-Q4_K_M.gguf"
    assert installed["m-Q4_K_M.gguf"].task == "chat"

    with pytest.raises(mm.ModelDownloadError):
        manager.download_hf_file(mm.HfFile("a/m-GGUF", "m-00001-of-00002.gguf"))
    bad = mm.HfFile("a/m-GGUF", "other.gguf", len(body), "b" * 64)
    with pytest.raises(RuntimeError, match="SHA256"):
        manager.download_hf_file(bad)
    assert not (tmp_path / "models" / "other.gguf").exists()


def test_link_dialog_lists_and_picks(window, monkeypatch):
    from desktop_browser.app import HfDownloadDialog

    dialog = HfDownloadDialog(window, installed={"m-Q8_0.gguf"})
    dialog.link.setText("https://example.org/x/y")
    dialog.find_files()
    assert "huggingface.co" in dialog.status.text() and dialog._worker is None

    files = [mm.HfFile("a/m", t["path"], (t.get("lfs") or {}).get("size", 0)) for t in TREE if t["type"] == "file"
             and t["path"].endswith(".gguf")]
    dialog._wanted = ("", "Q8_0")
    dialog.show_files(files)
    texts = [dialog.files.item(i).text() for i in range(dialog.files.count())]
    assert any("уже скачана" in t for t in texts) and any("разбита на части" in t for t in texts)
    assert dialog.files.currentItem().data(0x0100).path == "m-Q8_0.gguf"      # the quant from the link
    assert dialog.download_button.isEnabled()
    dialog._wanted = ("", "")
    dialog.show_files(files)
    assert dialog.files.currentItem().data(0x0100).path == "m-Q4_K_M.gguf"    # Q4_K_M by default
    dialog._accept_selected()
    assert dialog.selected_file.path == "m-Q4_K_M.gguf"
    from PySide6.QtCore import Qt

    mmproj = dialog.files.item(texts.index(next(t for t in texts if "mmproj" in t)))
    assert not mmproj.flags() & Qt.ItemIsEnabled


def test_custom_download_becomes_the_chat_model(window, tmp_path, monkeypatch):
    models = window.model_mgr.models_dir
    gguf = models / "custom-Q4_K_M.gguf"
    gguf.write_bytes(b"GGUF")
    window.model_mgr._write_sidecar(gguf, {"source": "hf:a/b/custom-Q4_K_M.gguf", "task": "chat"})
    import core.backend_factory as factory

    monkeypatch.setattr(factory, "_detect_backend_type", lambda settings: "llama_cpp")
    from core.config import Settings

    window.settings = Settings()
    window._on_model_download_done(str(gguf))
    assert window.settings.gguf_path == str(gguf)
