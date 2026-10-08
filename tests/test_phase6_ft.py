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
        assert s.max_seq_length == 1024  # 2048 ran out of memory on 8 GB
        assert s.lora_dropout == 0.0
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


# ── attention and log noise of the HF trainer ────────────────────────────────


def test_flash_attention_used_only_when_installed(monkeypatch):
    import importlib.util

    import finetune.train_hf as train_hf

    real_find_spec = importlib.util.find_spec
    monkeypatch.setattr(
        train_hf.importlib.util, "find_spec",
        lambda name, *a: None if name == "flash_attn" else real_find_spec(name, *a),
    )
    assert train_hf.attention_implementation(True) == "sdpa"
    assert train_hf.attention_implementation(False) == "sdpa"

    monkeypatch.setattr(
        train_hf.importlib.util, "find_spec",
        lambda name, *a: object() if name == "flash_attn" else real_find_spec(name, *a),
    )
    assert train_hf.attention_implementation(True) == "flash_attention_2"
    assert train_hf.attention_implementation(False) == "sdpa"


def test_download_http_lines_are_silenced():
    import logging

    from finetune.train_hf import NOISY_LOGGERS, quiet_noisy_loggers

    quiet_noisy_loggers()
    for name in NOISY_LOGGERS:
        assert logging.getLogger(name).getEffectiveLevel() >= logging.WARNING



# ═══════════════════════════════════════════════════════════════════════════════
#  Dataset formats other than ChatML
# ═══════════════════════════════════════════════════════════════════════════════


def _write_jsonl(path, records, encoding="utf-8"):
    path.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in records), encoding=encoding)
    return path


@pytest.mark.parametrize(
    "record, user, answer",
    [
        ({"instruction": "Сложи", "input": "2 и 2", "output": "4"}, "Сложи\n\n2 и 2", "4"),
        ({"instruction": "Привет", "output": "Здравствуйте"}, "Привет", "Здравствуйте"),
        (
            {"conversations": [{"from": "human", "value": "q"}, {"from": "gpt", "value": "a"}]},
            "q", "a",
        ),
        ({"prompt": "q", "completion": "a"}, "q", "a"),
        ({"prompt": "q", "response": "a"}, "q", "a"),
        ({"question": "q", "answer": "a"}, "q", "a"),
        ({"input": "q", "output": "a"}, "q", "a"),
    ],
)
def test_other_formats_become_chat_messages(tmp_path, record, user, answer):
    samples = load_jsonl(_write_jsonl(tmp_path / "d.jsonl", [record]))
    messages = samples[0]["messages"]
    assert messages[-2] == {"role": "user", "content": user}
    assert messages[-1] == {"role": "assistant", "content": answer}
    assert "<|im_start|>assistant" in prepare_dataset(tmp_path / "d.jsonl")[0]


def test_system_field_is_kept(tmp_path):
    record = {"system": "Ты помощник", "instruction": "q", "output": "a"}
    messages = load_jsonl(_write_jsonl(tmp_path / "d.jsonl", [record]))[0]["messages"]
    assert messages[0] == {"role": "system", "content": "Ты помощник"}


def test_unknown_format_is_one_clear_error(tmp_path, caplog):
    """Regression: 15 000 lines of "no 'messages' key", then training went on."""
    from finetune import DatasetFormatError

    path = _write_jsonl(tmp_path / "d.jsonl", [{"text": f"t{i}", "meta": 1} for i in range(50)])
    with caplog.at_level("WARNING"), pytest.raises(DatasetFormatError) as error:
        load_jsonl(path)
    assert "meta, text" in str(error.value)
    assert "Alpaca" in str(error.value)
    assert len([r for r in caplog.records if r.levelname == "WARNING"]) <= 4


def test_bom_at_file_start_is_accepted(tmp_path):
    path = _write_jsonl(tmp_path / "d.jsonl", [{"question": "q", "answer": "a"}], encoding="utf-8-sig")
    assert len(load_jsonl(path)) == 1


def test_check_dataset(tmp_path):
    from finetune import check_dataset

    good = _write_jsonl(tmp_path / "good.jsonl", [{"question": "q", "answer": "a"}])
    assert check_dataset(good) == ""
    bad = _write_jsonl(tmp_path / "bad.jsonl", [{"text": "t"}])
    assert "Формат датасета не распознан" in check_dataset(bad)
    cp1251 = tmp_path / "cp.jsonl"
    cp1251.write_bytes('{"question": "вопрос", "answer": "ответ"}\n'.encode("cp1251"))
    assert "UTF-8" in check_dataset(cp1251)
    empty = tmp_path / "empty.jsonl"
    empty.write_text("\n", encoding="utf-8")
    assert "пуст" in check_dataset(empty)


def test_russian_and_problem_solution_pairs(tmp_path):
    path = _write_jsonl(tmp_path / "d.jsonl", [
        {"index": 4, "вопрос": "Как?", "ответ": "Так."},
        {"problem": "p", "solution": "s", "lang": "cpp"},
    ])
    samples = load_jsonl(path)
    assert samples[0]["messages"][-1] == {"role": "assistant", "content": "Так."}
    assert samples[1]["messages"][0] == {"role": "user", "content": "p"}


def test_max_samples_stops_reading_early(tmp_path):
    path = _write_jsonl(tmp_path / "d.jsonl", [{"question": f"q{i}", "answer": "a"} for i in range(50)])
    with path.open("a", encoding="utf-8") as f:
        f.write("not json at the end\n")
    assert len(load_jsonl(path, limit=5)) == 5
    assert len(prepare_dataset(path, max_samples=5)) == 5


def test_estimate_samples(tmp_path):
    from finetune.dataset_loader import estimate_samples

    path = _write_jsonl(tmp_path / "d.jsonl", [{"question": "q" * 40, "answer": "a"}] * 1000)
    assert 950 <= estimate_samples(path) <= 1050
    small = _write_jsonl(tmp_path / "s.jsonl", [{"question": "q", "answer": "a"}] * 3)
    assert estimate_samples(small) == 3
    assert estimate_samples(tmp_path / "missing.jsonl") == 0


# ═══════════════════════════════════════════════════════════════════════════════
#  trl compatibility (SFTTrainer API changed in trl 0.12+ / 1.x)
# ═══════════════════════════════════════════════════════════════════════════════


def test_sft_config_kwargs_follow_installed_fields():
    from finetune.sft_compat import sft_config_kwargs

    new = sft_config_kwargs(
        {"output_dir", "max_length", "dataset_text_field", "packing"},
        {"output_dir": "o", "no_such_option": 1}, max_seq_length=1024, packing=True,
    )
    assert new == {"output_dir": "o", "max_length": 1024, "dataset_text_field": "text", "packing": True}
    old = sft_config_kwargs(
        {"output_dir", "max_seq_length", "dataset_text_field", "packing"},
        {"output_dir": "o"}, max_seq_length=512, packing=False,
    )
    assert old["max_seq_length"] == 512 and "max_length" not in old


def _fake_trl(monkeypatch, trainer_init):
    import sys
    import types

    calls = {}

    class SFTConfig:
        def __init__(self, output_dir=None, max_length=None, dataset_text_field=None, packing=False,
                     num_train_epochs=1):
            calls["config"] = dict(output_dir=output_dir, max_length=max_length,
                                   dataset_text_field=dataset_text_field, packing=packing)

    class SFTTrainer:
        __init__ = trainer_init

    SFTTrainer.calls = calls
    module = types.ModuleType("trl")
    module.SFTConfig = SFTConfig
    module.SFTTrainer = SFTTrainer
    monkeypatch.setitem(sys.modules, "trl", module)
    return calls


def test_build_sft_trainer_uses_current_trl_api(monkeypatch):
    """Regression: trl 1.x rejected tokenizer=/max_seq_length=/dataset_text_field= right
    after the base model had been loaded."""
    from finetune.sft_compat import build_sft_trainer

    def init(self, model, args=None, train_dataset=None, eval_dataset=None, processing_class=None):
        type(self).calls["trainer"] = dict(model=model, processing_class=processing_class)

    calls = _fake_trl(monkeypatch, init)
    build_sft_trainer(
        model="m", tokenizer="tok", train_dataset=[], eval_dataset=None,
        training_args={"output_dir": "o", "num_train_epochs": 2, "report_to": "none"},
        max_seq_length=2048, packing=True,
    )
    assert calls["config"] == {"output_dir": "o", "max_length": 2048,
                               "dataset_text_field": "text", "packing": True}
    assert calls["trainer"] == {"model": "m", "processing_class": "tok"}


def test_build_sft_trainer_keeps_tokenizer_for_older_trl(monkeypatch):
    from finetune.sft_compat import build_sft_trainer

    def init(self, model, args=None, train_dataset=None, eval_dataset=None, tokenizer=None):
        type(self).calls["tokenizer"] = tokenizer

    calls = _fake_trl(monkeypatch, init)
    build_sft_trainer(
        model="m", tokenizer="tok", train_dataset=[], eval_dataset=None,
        training_args={"output_dir": "o"}, max_seq_length=1024, packing=False,
    )
    assert calls["tokenizer"] == "tok"


def test_trainers_do_not_pass_removed_sft_arguments():
    root = Path(__file__).resolve().parents[1] / "finetune"
    for name in ("train_unsloth.py", "train_hf.py"):
        source = (root / name).read_text(encoding="utf-8")
        assert "build_sft_trainer(" in source, name
        assert "SFTTrainer(" not in source, name


def test_warmup_ratio_becomes_warmup_steps_on_transformers_5():
    from finetune.sft_compat import sft_config_kwargs

    params = {"output_dir", "warmup_steps", "max_length", "dataset_text_field", "packing"}
    kwargs = sft_config_kwargs(params, {"output_dir": "o", "warmup_ratio": 0.03},
                               max_seq_length=1024, packing=False)
    assert kwargs["warmup_steps"] == 0.03 and "warmup_ratio" not in kwargs
    old = sft_config_kwargs(params | {"warmup_ratio"}, {"output_dir": "o", "warmup_ratio": 0.03},
                            max_seq_length=1024, packing=False)
    assert old["warmup_ratio"] == 0.03 and "warmup_steps" not in old
