"""Phase 13.2 tests: LoRA adapter marketplace registry.

Covers AdapterInfo (defaults, from_training, serialization, validation) and
AdapterRegistry (register/list/get/delete/switch, JSON index persistence).
"""
from __future__ import annotations

import json

import pytest

from core.config import Settings
from core.inference import OllamaBackend
from finetune import QLoRASettings
from finetune.marketplace import (
    AdapterError,
    AdapterInfo,
    AdapterNotFoundError,
    AdapterRegistry,
    DuplicateAdapterError,
    InvalidAdapterError,
)


@pytest.fixture()
def registry(tmp_path):
    return AdapterRegistry(tmp_path / "registry")


def make_adapter(name: str = "fluxion-coder-python", **overrides) -> AdapterInfo:
    kwargs = {
        "base_model": "Qwen/Qwen2.5-Coder-7B-Instruct",
        "path": "data/lora_output",
        "ollama_model": "fluxion-coder-python",
        "quantize": "q4_K_M",
        "description": "base adapter",
    }
    kwargs.update(overrides)
    return AdapterInfo(name=name, **kwargs)


# ═══════════════════════════════════════════════════════════════════════════════
#  AdapterInfo
# ═══════════════════════════════════════════════════════════════════════════════

class TestAdapterInfo:
    def test_defaults_match_qlora_settings(self):
        a = AdapterInfo(name="x")
        q = QLoRASettings()
        assert a.base_model == q.base_model
        assert a.path == q.output_dir
        assert a.ollama_model == q.ollama_model_name
        assert a.quantize == q.gguf_quantize

    def test_created_at_autofilled(self):
        a = AdapterInfo(name="x")
        assert a.created_at
        assert "T" in a.created_at

    def test_created_at_preserved(self):
        a = AdapterInfo(name="x", created_at="2026-01-01T00:00:00+00:00")
        assert a.created_at == "2026-01-01T00:00:00+00:00"

    def test_from_training(self):
        q = QLoRASettings(
            base_model="Qwen/Qwen2.5-Coder-1.5B-Instruct",
            output_dir="data/lora_a",
            ollama_model_name="fluxion-mini",
            gguf_quantize="q5_K_M",
        )
        a = AdapterInfo.from_training(q, "mini", description="small")
        assert a.name == "mini"
        assert a.base_model == "Qwen/Qwen2.5-Coder-1.5B-Instruct"
        assert a.path == "data/lora_a"
        assert a.ollama_model == "fluxion-mini"
        assert a.quantize == "q5_K_M"

    def test_to_from_dict_roundtrip(self):
        a = make_adapter("adp.v2_rc1", description="round trip")
        restored = AdapterInfo.from_dict(a.to_dict())
        assert restored == a

    def test_from_dict_ignores_unknown_keys(self):
        a = AdapterInfo.from_dict({"name": "x", "future_field": 1})
        assert a.name == "x"

    def test_validate_accepts_slug(self):
        make_adapter("fluxion-coder-python").validate()
        make_adapter("adp.v2_rc1").validate()

    @pytest.mark.parametrize("bad_name", ["", "UPPER", "with space", "-lead", "a" * 65])
    def test_validate_rejects_bad_names(self, bad_name):
        with pytest.raises(InvalidAdapterError):
            make_adapter(bad_name).validate()

    def test_validate_rejects_empty_model_fields(self):
        with pytest.raises(InvalidAdapterError):
            make_adapter("x", ollama_model="").validate()
        with pytest.raises(InvalidAdapterError):
            make_adapter("x", base_model="").validate()


# ═══════════════════════════════════════════════════════════════════════════════
#  Registry: register / list / get
# ═══════════════════════════════════════════════════════════════════════════════

class TestRegistryCrud:
    def test_register_creates_index_file(self, registry):
        registry.register(make_adapter("one"))
        assert registry.index_path.exists()
        data = json.loads(registry.index_path.read_text(encoding="utf-8"))
        assert data["version"] == 1
        assert "one" in data["adapters"]
        assert data["adapters"]["one"]["ollama_model"] == "fluxion-coder-python"

    def test_register_invalid_name_raises(self, registry):
        with pytest.raises(InvalidAdapterError):
            registry.register(make_adapter("Bad Name"))
        assert not registry.index_path.exists()

    def test_register_duplicate_raises(self, registry):
        registry.register(make_adapter("one"))
        with pytest.raises(DuplicateAdapterError):
            registry.register(make_adapter("one", ollama_model="other"))

    def test_register_exist_ok_overwrites(self, registry):
        registry.register(make_adapter("one", ollama_model="v1"))
        registry.register(make_adapter("one", ollama_model="v2"), exist_ok=True)
        assert registry.get("one").ollama_model == "v2"

    def test_list_sorted_by_name(self, registry):
        for name in ("zeta", "alpha", "mid"):
            registry.register(make_adapter(name))
        assert [a.name for a in registry.list()] == ["alpha", "mid", "zeta"]

    def test_get_missing_returns_none(self, registry):
        assert registry.get("nope") is None

    def test_persistence_across_instances(self, tmp_path):
        dir_ = tmp_path / "registry"
        AdapterRegistry(dir_).register(make_adapter("one"))
        again = AdapterRegistry(dir_)
        assert [a.name for a in again.list()] == ["one"]

    def test_find_by_model(self, registry):
        registry.register(make_adapter("one", ollama_model="fluxion-a"))
        registry.register(make_adapter("two", ollama_model="fluxion-b"))
        assert registry.find_by_model("fluxion-b").name == "two"
        assert registry.find_by_model("missing") is None

    def test_corrupt_index_raises(self, registry):
        registry.index_path.parent.mkdir(parents=True)
        registry.index_path.write_text("{not json", encoding="utf-8")
        with pytest.raises(AdapterError):
            registry.list()

    def test_empty_registry_list(self, registry):
        assert registry.list() == []
        assert registry.get("x") is None


# ═══════════════════════════════════════════════════════════════════════════════
#  Registry: delete
# ═══════════════════════════════════════════════════════════════════════════════

class TestRegistryDelete:
    def test_delete_removes_entry(self, registry):
        registry.register(make_adapter("one"))
        assert registry.delete("one") is True
        assert registry.get("one") is None

    def test_delete_missing_returns_false(self, registry):
        assert registry.delete("nope") is False

    def test_delete_remove_files(self, registry, tmp_path):
        lora_dir = tmp_path / "lora_one"
        lora_dir.mkdir()
        (lora_dir / "adapter_model.safetensors").write_text("x", encoding="utf-8")
        registry.register(make_adapter("one", path=str(lora_dir)))
        assert registry.delete("one", remove_files=True) is True
        assert not lora_dir.exists()

    def test_delete_remove_files_missing_dir_is_safe(self, registry):
        registry.register(make_adapter("one", path="data/does/not/exist"))
        assert registry.delete("one", remove_files=True) is True


# ═══════════════════════════════════════════════════════════════════════════════
#  Registry: switch
# ═══════════════════════════════════════════════════════════════════════════════

class TestRegistrySwitch:
    def test_switch_updates_settings_and_backend(self, registry):
        registry.register(make_adapter("one", ollama_model="fluxion-a"))
        settings = Settings()
        backend = OllamaBackend(settings)
        original = settings.model
        adapter = registry.switch("one", settings, backend)
        assert adapter.name == "one"
        assert settings.model == "fluxion-a"
        assert backend.client.model == "fluxion-a"
        assert settings.model != original or original == "fluxion-a"

    def test_switch_without_backend(self, registry):
        registry.register(make_adapter("one", ollama_model="fluxion-a"))
        settings = Settings()
        registry.switch("one", settings)
        assert settings.model == "fluxion-a"

    def test_switch_missing_raises(self, registry):
        with pytest.raises(AdapterNotFoundError):
            registry.switch("nope", Settings())
