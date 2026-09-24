"""Phase 6 tests: QLoRA config, dataset loader, ChatML formatting.

Training/merge/export modules are tested only for import-ability and CLI arg
parsing — actual training requires Python 3.11/3.12 venv with torch+peft.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from finetune import (
    QLoRASettings,
    VRAMPreset,
    format_chatml,
    load_jsonl,
    pack_sequences,
    prepare_dataset,
    train_val_split,
)


# ═══════════════════════════════════════════════════════════════════════════════
#  QLoRASettings
# ═══════════════════════════════════════════════════════════════════════════════

class TestQLoRASettings:
    def test_default_settings(self):
        s = QLoRASettings()
        assert s.lora_r == 32
        assert s.lora_alpha == 64
        assert s.base_model.startswith("Qwen")
        assert s.load_in_4bit is True
        assert s.num_train_epochs == 3

    def test_low_vram_preset(self):
        s = QLoRASettings.from_preset(VRAMPreset.LOW)
        assert s.lora_r == 32
        assert s.lora_alpha == 64
        assert len(s.target_modules) == 4
        assert s.max_seq_length == 2048
        assert s.gradient_accumulation_steps == 16

    def test_standard_preset(self):
        s = QLoRASettings.from_preset(VRAMPreset.STANDARD)
        assert s.lora_r == 64
        assert s.lora_alpha == 128
        assert len(s.target_modules) == 7
        assert s.max_seq_length == 4096
        assert s.gradient_accumulation_steps == 8

    def test_preset_from_string(self):
        s = QLoRASettings.from_preset("low")
        assert s.lora_r == 32
        s2 = QLoRASettings.from_preset("standard")
        assert s2.lora_r == 64

    def test_to_peft_config(self):
        s = QLoRASettings()
        cfg = s.to_peft_config()
        assert cfg["r"] == s.lora_r
        assert cfg["lora_alpha"] == s.lora_alpha
        assert "q_proj" in cfg["target_modules"]
        assert cfg["task_type"] == "CAUSAL_LM"

    def test_to_bnb_config(self):
        s = QLoRASettings()
        cfg = s.to_bnb_config()
        assert cfg["load_in_4bit"] is True
        assert cfg["bnb_4bit_quant_type"] == "nf4"

    def test_to_training_args(self):
        s = QLoRASettings()
        args = s.to_training_args()
        assert args["num_train_epochs"] == 3
        assert args["learning_rate"] == 2e-4
        assert "output_dir" in args
        assert args["bf16"] is True


# ═══════════════════════════════════════════════════════════════════════════════
#  ChatML formatting
# ═══════════════════════════════════════════════════════════════════════════════

class TestFormatChatML:
    def test_basic_formatting(self):
        messages = [
            {"role": "system", "content": "You are helpful."},
            {"role": "user", "content": "Hello"},
            {"role": "assistant", "content": "Hi there!"},
        ]
        result = format_chatml(messages)
        assert "<|im_start|>system" in result
        assert "You are helpful." in result
        assert "<|im_start|>user" in result
        assert "Hello" in result
        assert "<|im_start|>assistant" in result
        assert "Hi there!" in result
        assert "<|im_end|>" in result

    def test_empty_messages(self):
        result = format_chatml([])
        assert result == ""

    def test_single_message(self):
        messages = [{"role": "user", "content": "Test"}]
        result = format_chatml(messages)
        assert "<|im_start|>user\nTest\n<|im_end|>" == result


# ═══════════════════════════════════════════════════════════════════════════════
#  Dataset loading
# ═══════════════════════════════════════════════════════════════════════════════

class TestDatasetLoader:
    @pytest.fixture()
    def sample_jsonl(self, tmp_path):
        """Create a small ChatML JSONL file."""
        path = tmp_path / "train.jsonl"
        lines: list[str] = []
        for i in range(10):
            sample = {
                "messages": [
                    {"role": "system", "content": "You are a Python assistant."},
                    {"role": "user", "content": f"Write function number {i}"},
                    {"role": "assistant", "content": f"def func_{i}():\n    return {i}"},
                ],
                "source": "test",
                "quality_score": 1.0,
            }
            lines.append(json.dumps(sample))
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        return path

    def test_load_jsonl(self, sample_jsonl):
        samples = load_jsonl(sample_jsonl)
        assert len(samples) == 10
        assert "messages" in samples[0]

    def test_load_jsonl_missing_file(self, tmp_path):
        with pytest.raises(FileNotFoundError):
            load_jsonl(tmp_path / "nonexistent.jsonl")

    def test_load_jsonl_skips_invalid(self, tmp_path):
        path = tmp_path / "bad.jsonl"
        path.write_text(
            json.dumps({"messages": [{"role": "user", "content": "ok"}]}) + "\n"
            "not valid json\n"
            '{"no_messages": true}\n',
            encoding="utf-8",
        )
        samples = load_jsonl(path)
        assert len(samples) == 1

    def test_prepare_dataset(self, sample_jsonl):
        texts = prepare_dataset(sample_jsonl)
        assert len(texts) == 10
        assert "<|im_start|>" in texts[0]
        assert "<|endoftext|>" in texts[0]

    def test_prepare_dataset_max_samples(self, sample_jsonl):
        texts = prepare_dataset(sample_jsonl, max_samples=3)
        assert len(texts) == 3

    def test_prepare_dataset_no_eos(self, sample_jsonl):
        texts = prepare_dataset(sample_jsonl, add_eos=False)
        assert "<|endoftext|>" not in texts[0]


# ═══════════════════════════════════════════════════════════════════════════════
#  Packing
# ═══════════════════════════════════════════════════════════════════════════════

class TestPacking:
    def test_pack_short_sequences(self):
        texts = ["AAAA", "BBBB", "CCCC"]
        packed = pack_sequences(texts, max_length=20)
        # All 3 should fit in one packed sequence (4+4+4+separators < 20)
        assert len(packed) <= 2

    def test_pack_large_sequence_splits(self):
        texts = ["A" * 100, "B" * 100, "C" * 100]
        packed = pack_sequences(texts, max_length=150)
        assert len(packed) >= 2

    def test_pack_empty(self):
        assert pack_sequences([]) == []

    def test_pack_single(self):
        texts = ["Only one"]
        packed = pack_sequences(texts, max_length=1000)
        assert len(packed) == 1
        assert packed[0] == "Only one"


# ═══════════════════════════════════════════════════════════════════════════════
#  Train/val split
# ═══════════════════════════════════════════════════════════════════════════════

class TestTrainValSplit:
    def test_split_returns_correct_sizes(self):
        texts = [f"text_{i}" for i in range(100)]
        train, val = train_val_split(texts, val_ratio=0.1)
        assert len(val) == 10
        assert len(train) == 90

    def test_split_is_disjoint(self):
        texts = [f"text_{i}" for i in range(20)]
        train, val = train_val_split(texts, val_ratio=0.2)
        train_set = set(train)
        val_set = set(val)
        assert train_set.isdisjoint(val_set)
        assert len(train_set) + len(val_set) == 20

    def test_split_reproducible(self):
        texts = [f"text_{i}" for i in range(50)]
        train1, val1 = train_val_split(texts, seed=42)
        train2, val2 = train_val_split(texts, seed=42)
        assert train1 == train2
        assert val1 == val2

    def test_min_val_size(self):
        texts = ["a", "b", "c"]
        train, val = train_val_split(texts, val_ratio=0.05)
        assert len(val) >= 1


# ═══════════════════════════════════════════════════════════════════════════════
#  Modelfile generation (export_gguf internal)
# ═══════════════════════════════════════════════════════════════════════════════

class TestModelfile:
    def test_modelfile_generation(self):
        from finetune.export_gguf import _generate_modelfile

        settings = QLoRASettings()
        content = _generate_modelfile("model.gguf", settings)
        assert "FROM ./model.gguf" in content
        assert "PARAMETER temperature" in content
        assert "PARAMETER stop" in content
        assert "<|im_end|>" in content
