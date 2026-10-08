"""QLoRA configuration: hyperparameters, target modules, paths.

Two presets:
  - LOW_VRAM (6-8 GB, unsloth): r=32, alpha=64, 4 target modules, seq 1024, no dropout
  - STANDARD (8-12 GB, HF):  r=64, alpha=128, 7 target modules, seq 2048-4096
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path


class VRAMPreset(Enum):
    LOW = "low"          # 6 GB, unsloth
    STANDARD = "standard"  # 8-12 GB, HF peft+trl


# ChatML token constants
IM_START = "<|im_start|>"
IM_END = "<|im_end|>"
SYSTEM_TOKEN = "<|im_start|>system"
USER_TOKEN = "<|im_start|>user"
ASSISTANT_TOKEN = "<|im_start|>assistant"

STOP_TOKENS = [IM_END]
# Packing works on characters: ~3 characters per token for code and Russian
# text keeps a packed sample within max_seq_length (4 overflowed and was cut).
PACK_CHARS_PER_TOKEN = 3

DEFAULT_TARGET_MODULES_LOW = ["q_proj", "k_proj", "v_proj", "o_proj"]
DEFAULT_TARGET_MODULES_STD = [
    "q_proj", "k_proj", "v_proj", "o_proj",
    "gate_proj", "up_proj", "down_proj",
]


@dataclass
class QLoRASettings:
    """All hyperparameters for QLoRA fine-tuning."""
    # ── Model ──
    base_model: str = "Qwen/Qwen2.5-Coder-7B-Instruct"

    # ── LoRA ──
    lora_r: int = 32
    lora_alpha: int = 64
    lora_dropout: float = 0.05
    target_modules: list[str] = field(default_factory=lambda: list(DEFAULT_TARGET_MODULES_LOW))
    bias: str = "none"
    task_type: str = "CAUSAL_LM"

    # ── Quantization ──
    load_in_4bit: bool = True
    bnb_4bit_quant_type: str = "nf4"
    bnb_4bit_use_double_quant: bool = True
    bnb_4bit_compute_dtype: str = "bfloat16"

    # ── Training ──
    max_seq_length: int = 2048
    num_train_epochs: int = 3
    per_device_train_batch_size: int = 1
    gradient_accumulation_steps: int = 16
    learning_rate: float = 2e-4
    lr_scheduler_type: str = "cosine"
    warmup_ratio: float = 0.03
    weight_decay: float = 0.01
    max_grad_norm: float = 0.3

    # ── Optimization ──
    gradient_checkpointing: bool = True
    use_flash_attention_2: bool = True
    packing: bool = True

    # ── Paths ──
    output_dir: str = "data/lora_output"
    merged_dir: str = "data/merged_model"
    gguf_dir: str = "data/gguf"
    dataset_path: str = "data/instruction_train.jsonl"

    # ── Export ──
    gguf_quantize: str = "q4_K_M"
    ollama_model_name: str = "fluxion-coder-python"

    # ── Logging ──
    logging_steps: int = 10
    save_steps: int = 200
    save_total_limit: int = 3

    @classmethod
    def from_preset(cls, preset: VRAMPreset | str) -> "QLoRASettings":
        """Create settings for a given VRAM preset."""
        preset = VRAMPreset(preset) if isinstance(preset, str) else preset

        if preset == VRAMPreset.LOW:
            # 1024 tokens and no dropout: a 7B model at 2048 ran out of memory
            # on an 8 GB laptop card ("No or negligible GPU memory available"),
            # and dropout 0 keeps unsloth's fused, leaner LoRA kernels.
            return cls(
                lora_r=32,
                lora_alpha=64,
                lora_dropout=0.0,
                target_modules=list(DEFAULT_TARGET_MODULES_LOW),
                max_seq_length=1024,
                per_device_train_batch_size=1,
                gradient_accumulation_steps=16,
                learning_rate=2e-4,
            )
        return cls(
            lora_r=64,
            lora_alpha=128,
            target_modules=list(DEFAULT_TARGET_MODULES_STD),
            max_seq_length=4096,
            per_device_train_batch_size=1,
            gradient_accumulation_steps=8,
            learning_rate=2e-4,
        )

    def to_peft_config(self) -> dict:
        """Convert to LoraConfig kwargs for PEFT."""
        return {
            "r": self.lora_r,
            "lora_alpha": self.lora_alpha,
            "lora_dropout": self.lora_dropout,
            "target_modules": self.target_modules,
            "bias": self.bias,
            "task_type": self.task_type,
        }

    def to_bnb_config(self) -> dict:
        """Convert to BitsAndBytesConfig kwargs."""
        return {
            "load_in_4bit": self.load_in_4bit,
            "bnb_4bit_quant_type": self.bnb_4bit_quant_type,
            "bnb_4bit_use_double_quant": self.bnb_4bit_use_double_quant,
            "bnb_4bit_compute_dtype": self.bnb_4bit_compute_dtype,
        }

    def to_training_args(self) -> dict:
        """Convert to transformers TrainingArguments kwargs."""
        return {
            "output_dir": self.output_dir,
            "num_train_epochs": self.num_train_epochs,
            "per_device_train_batch_size": self.per_device_train_batch_size,
            "gradient_accumulation_steps": self.gradient_accumulation_steps,
            "learning_rate": self.learning_rate,
            "lr_scheduler_type": self.lr_scheduler_type,
            "warmup_ratio": self.warmup_ratio,
            "weight_decay": self.weight_decay,
            "max_grad_norm": self.max_grad_norm,
            "logging_steps": self.logging_steps,
            "save_steps": self.save_steps,
            "save_total_limit": self.save_total_limit,
            "gradient_checkpointing": self.gradient_checkpointing,
            "report_to": "none",
            "bf16": True,
        }
