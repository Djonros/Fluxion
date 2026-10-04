"""Pro model catalog: larger/precise models downloadable with a Pro licence."""
from __future__ import annotations

import re

import pytest

import core.model_manager as mm_module
from core.model_manager import MODEL_CATALOG, PRO_FEATURE, ModelManager, model_allowed


def _licensed(monkeypatch, value: bool):
    import licensing

    monkeypatch.setattr(licensing, "feature_enabled", lambda feature: value and feature == PRO_FEATURE)


class TestCatalog:
    def test_ids_and_files_unique(self):
        ids = [m.model_id for m in MODEL_CATALOG]
        files = [m.filename.lower() for m in MODEL_CATALOG]
        assert len(ids) == len(set(ids)) and len(files) == len(set(files))

    def test_free_catalog_unchanged(self):
        free = {m.model_id for m in MODEL_CATALOG if not m.pro}
        assert free == {"qwen2.5-coder-7b-q4km", "qwen2.5-coder-3b-q4km", "bge-m3-gguf"}
        assert [m.model_id for m in MODEL_CATALOG if m.default] == ["qwen2.5-coder-7b-q4km"]
        assert all(not m.default for m in MODEL_CATALOG if m.pro)

    def test_pro_entries_are_well_formed(self):
        pro = [m for m in MODEL_CATALOG if m.pro]
        assert {m.model_id for m in pro} == {
            "qwen2.5-coder-7b-q8", "qwen2.5-coder-14b-q4km", "qwen2.5-coder-32b-q4km",
        }
        for m in pro:
            assert m.task == "chat" and m.summary and m.vram_gb > 0
            assert re.fullmatch(r"[\w.-]+/[\w.-]+", m.repo_id)
            assert m.filename.endswith(".gguf")
            # the downloader fetches one file: no split ("-00001-of-0000N") GGUFs
            assert not re.search(r"-\d{5}-of-\d{5}", m.filename)

    def test_every_entry_has_summary(self):
        assert all(m.summary for m in MODEL_CATALOG)

    def test_feature_is_a_pro_feature(self):
        from licensing import PRO_FEATURES

        assert PRO_FEATURE in PRO_FEATURES


class TestGate:
    def test_free_models_always_allowed(self, monkeypatch):
        _licensed(monkeypatch, False)
        assert all(model_allowed(m) for m in MODEL_CATALOG if not m.pro)

    def test_pro_models_follow_licence(self, monkeypatch):
        pro = next(m for m in MODEL_CATALOG if m.pro)
        _licensed(monkeypatch, False)
        assert not model_allowed(pro)
        _licensed(monkeypatch, True)
        assert model_allowed(pro)

    def test_download_refused_before_any_network(self, tmp_path, monkeypatch):
        from licensing import ProRequiredError

        _licensed(monkeypatch, False)

        def no_network(*a, **k):
            raise AssertionError("network must not be touched without a licence")

        monkeypatch.setattr(mm_module.httpx, "stream", no_network, raising=False)
        manager = ModelManager(tmp_path)
        with pytest.raises(ProRequiredError, match="Pro"):
            manager.download("qwen2.5-coder-14b-q4km")
        assert list(tmp_path.iterdir()) == []

    def test_download_proceeds_with_licence(self, tmp_path, monkeypatch):
        _licensed(monkeypatch, True)

        class Reached(Exception):
            pass

        def fake_stream(method, url, **kw):
            assert url.endswith("/Qwen2.5-Coder-14B-Instruct-Q4_K_M.gguf")
            raise Reached

        monkeypatch.setattr(mm_module.httpx, "stream", fake_stream, raising=False)
        with pytest.raises(Reached):
            ModelManager(tmp_path).download("qwen2.5-coder-14b-q4km")

    def test_free_download_unaffected(self, tmp_path, monkeypatch):
        _licensed(monkeypatch, False)

        class Reached(Exception):
            pass

        monkeypatch.setattr(mm_module.httpx, "stream", lambda *a, **k: (_ for _ in ()).throw(Reached()),
                            raising=False)
        with pytest.raises(Reached):
            ModelManager(tmp_path).download("qwen2.5-coder-7b-q4km")
