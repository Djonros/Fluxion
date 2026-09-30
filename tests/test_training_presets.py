"""Training presets as files: strict loading, safe base models, pipeline wiring."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from finetune.presets import (
    MAX_PRESET_BYTES,
    SUPPORTED_BASE_MODELS,
    PresetError,
    TrainingPreset,
    load_preset,
)
from finetune.qlora_config import QLoRASettings, VRAMPreset

ROOT = Path(__file__).resolve().parents[1]
PRESET_FILES = sorted((ROOT / "presets" / "training").glob("*.json"))


def _preset(**overrides):
    data = {
        "format": "fluxion-training-preset", "version": 1, "id": "t", "name": "Тест",
        "trainer": "low", "min_vram_gb": 6,
        "settings": {"lora_r": 16, "target_modules": ["q_proj", "v_proj"]},
    }
    data.update(overrides)
    return data


class TestShippedPresets:
    def test_files_exist(self):
        assert {p.stem for p in PRESET_FILES} >= {"economy", "standard", "quick-check"}

    @pytest.mark.parametrize("path", PRESET_FILES, ids=[p.name for p in PRESET_FILES])
    def test_every_file_loads(self, path):
        preset = load_preset(path)
        assert preset.id == path.stem
        assert preset.base_model in (None, *SUPPORTED_BASE_MODELS)

    @pytest.mark.parametrize("pid,trainer", [("economy", "low"), ("standard", "standard")])
    def test_builtin_equivalents_match_code(self, pid, trainer):
        """The website's economy/standard files must equal the built-in presets."""
        builtin = QLoRASettings.from_preset(VRAMPreset(trainer))
        applied = load_preset(ROOT / "presets" / "training" / f"{pid}.json").apply(builtin)
        assert applied == builtin

    def test_quick_check_is_short(self):
        preset = load_preset(ROOT / "presets" / "training" / "quick-check.json")
        assert preset.settings["num_train_epochs"] == 1 and preset.max_samples


class TestValidation:
    def test_valid_minimal(self):
        preset = TrainingPreset.from_dict(_preset())
        settings = preset.apply(QLoRASettings.from_preset("low"))
        assert settings.lora_r == 16 and settings.target_modules == ["q_proj", "v_proj"]

    def test_apply_does_not_mutate_input(self):
        base = QLoRASettings.from_preset("low")
        TrainingPreset.from_dict(_preset()).apply(base)
        assert base.lora_r == 32

    @pytest.mark.parametrize("model", ["attacker/evil-model", "Qwen/Qwen2.5-Coder-7B-Instruct/../x", ""])
    def test_untrusted_base_model_rejected(self, model):
        # trainers load the base model with trust_remote_code=True
        with pytest.raises(PresetError, match="не поддерживается"):
            TrainingPreset.from_dict(_preset(base_model=model))

    @pytest.mark.parametrize("bad", [
        {"format": "other"},
        {"version": 2},
        {"trainer": "gpu"},
        {"id": "Bad Id"},
        {"name": ""},
        {"min_vram_gb": True},
        {"max_samples": 0},
        {"extra_field": 1},
        {"settings": {"lora_r": 100000}},
        {"settings": {"lora_r": True}},
        {"settings": {"learning_rate": "0.1"}},
        {"settings": {"target_modules": ["q_proj", "rm -rf"]}},
        {"settings": {"target_modules": []}},
        {"settings": {"output_dir": "C:/Windows"}},
        {"settings": {"lr_scheduler_type": "exotic"}},
    ])
    def test_invalid_rejected(self, bad):
        with pytest.raises(PresetError):
            TrainingPreset.from_dict(_preset(**bad))

    def test_not_an_object(self):
        with pytest.raises(PresetError):
            TrainingPreset.from_dict(["x"])

    def test_bad_json_and_huge_file(self, tmp_path):
        broken = tmp_path / "b.json"
        broken.write_text("{not json", encoding="utf-8")
        with pytest.raises(PresetError, match="JSON"):
            load_preset(broken)
        huge = tmp_path / "h.json"
        huge.write_text(" " * (MAX_PRESET_BYTES + 1), encoding="utf-8")
        with pytest.raises(PresetError, match="большой"):
            load_preset(huge)
        with pytest.raises(PresetError, match="не найден"):
            load_preset(tmp_path / "missing.json")

    def test_roundtrip_and_bom(self, tmp_path):
        preset = TrainingPreset.from_dict(_preset(max_samples=50, base_model=SUPPORTED_BASE_MODELS[0]))
        path = tmp_path / "p.json"
        path.write_text("\ufeff" + json.dumps(preset.to_dict(), ensure_ascii=False), encoding="utf-8")
        assert load_preset(path) == preset


class TestPipeline:
    def test_preset_file_reaches_trainer_subprocess(self, tmp_path, monkeypatch):
        import licensing

        import desktop_browser.training as training

        monkeypatch.setattr(licensing, "ensure_pro", lambda feature: None)
        dataset = tmp_path / "ds.jsonl"
        dataset.write_text('{"instruction": "a", "output": "b"}\n', encoding="utf-8")
        preset_path = ROOT / "presets" / "training" / "standard.json"
        env_py = tmp_path / "python.exe"
        env_py.write_text("", encoding="utf-8")
        commands = []

        def fake_run_cmd(cmd, log, stop_requested, extra_env=None):
            commands.append([str(c) for c in cmd])
            raise training.TrainingError("stop after trainer")  # enough for this test

        monkeypatch.setattr(training, "_run_cmd", fake_run_cmd)
        monkeypatch.setattr(training, "_detect_trainer",
                            lambda log, env=None: (lambda *a: None, "venv", str(env_py)))
        params = training.TrainingParams(dataset=str(dataset), preset="low",
                                         preset_file=str(preset_path))
        with pytest.raises(training.TrainingError, match="stop after trainer"):
            training.run_pipeline(params, log=lambda m: None, stop_requested=lambda: False,
                                  data_dir=str(tmp_path))
        cmd = commands[0]
        assert "finetune.train_hf" in cmd                     # trainer taken from the file
        assert cmd[cmd.index("--preset") + 1] == "standard"
        assert cmd[cmd.index("--settings-file") + 1] == str(preset_path.resolve())

    def test_invalid_preset_file_is_a_training_error(self, tmp_path, monkeypatch):
        import licensing

        import desktop_browser.training as training

        monkeypatch.setattr(licensing, "ensure_pro", lambda feature: None)
        dataset = tmp_path / "ds.jsonl"
        dataset.write_text("{}\n", encoding="utf-8")
        bad = tmp_path / "bad.json"
        bad.write_text(json.dumps(_preset(base_model="attacker/evil")), encoding="utf-8")
        params = training.TrainingParams(dataset=str(dataset), preset_file=str(bad))
        with pytest.raises(training.TrainingError, match="Пресет не загружен"):
            training.run_pipeline(params, log=lambda m: None, stop_requested=lambda: False,
                                  data_dir=str(tmp_path))

    @pytest.mark.parametrize("script", ["train_unsloth.py", "train_hf.py"])
    def test_trainers_accept_settings_file(self, script):
        source = (ROOT / "finetune" / script).read_text(encoding="utf-8")
        assert '"--settings-file"' in source and "load_preset(args.settings_file)" in source
