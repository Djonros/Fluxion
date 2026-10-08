"""QLoRA training via HuggingFace peft + trl (for 8-12 GB VRAM).

Usage (in Python 3.11/3.12 venv):
    python -m finetune.train_hf --dataset data/instruction_train.jsonl

Requires: torch (cu121), transformers, peft, trl, bitsandbytes, accelerate
"""
from __future__ import annotations

import argparse
import importlib.util
import logging
import sys
from pathlib import Path

from .dataset_loader import pack_sequences, prepare_dataset, train_val_split
from .qlora_config import PACK_CHARS_PER_TOKEN, QLoRASettings, VRAMPreset

logger = logging.getLogger(__name__)
NOISY_LOGGERS = ("httpx", "httpcore", "huggingface_hub", "urllib3")


def attention_implementation(want_flash: bool) -> str:
    """Return ``flash_attention_2`` only when the package is installed, else PyTorch SDPA.

    FlashAttention is rarely available on Windows; asking for it there failed
    the run after the base model had already been downloaded.
    """
    if want_flash and importlib.util.find_spec("flash_attn") is not None:
        return "flash_attention_2"
    if want_flash:
        logger.info("FlashAttention2 is not installed; using PyTorch SDPA attention")
    return "sdpa"


def quiet_noisy_loggers() -> None:
    """Keep per-request HTTP lines of the model download out of the training log."""
    for name in NOISY_LOGGERS:
        logging.getLogger(name).setLevel(logging.WARNING)


def train(
    settings: QLoRASettings,
    dataset_path: str | None = None,
    max_samples: int | None = None,
) -> str:
    """Run QLoRA training with HuggingFace peft + trl. Returns adapter path."""
    import torch
    from datasets import Dataset
    from peft import LoraConfig, get_peft_model, prepare_model_for_kbit_training
    from transformers import (
        AutoModelForCausalLM,
        AutoTokenizer,
        BitsAndBytesConfig,
    )

    from .sft_compat import build_sft_trainer

    ds_path = dataset_path or settings.dataset_path
    if not Path(ds_path).exists():
        logger.error("Dataset not found: %s", ds_path)
        sys.exit(1)

    # ── Quantization config ──
    compute_dtype = getattr(torch, settings.bnb_4bit_compute_dtype, torch.bfloat16)
    bnb_config = BitsAndBytesConfig(
        load_in_4bit=settings.load_in_4bit,
        bnb_4bit_quant_type=settings.bnb_4bit_quant_type,
        bnb_4bit_use_double_quant=settings.bnb_4bit_use_double_quant,
        bnb_4bit_compute_dtype=compute_dtype,
    )

    # ── Load model ──
    logger.info("Loading model: %s", settings.base_model)
    model = AutoModelForCausalLM.from_pretrained(
        settings.base_model,
        quantization_config=bnb_config,
        device_map="auto",
        attn_implementation=attention_implementation(settings.use_flash_attention_2),
    )
    model.config.use_cache = False
    model = prepare_model_for_kbit_training(model)

    # ── Tokenizer ──
    tokenizer = AutoTokenizer.from_pretrained(settings.base_model, trust_remote_code=True)
    if tokenizer.pad_token is None:
        tokenizer.pad_token = tokenizer.eos_token

    # ── LoRA config ──
    lora_config = LoraConfig(**settings.to_peft_config())
    model = get_peft_model(model, lora_config)
    model.print_trainable_parameters()

    # ── Prepare dataset ──
    texts = prepare_dataset(ds_path, max_samples=max_samples)
    if settings.packing:
        texts = pack_sequences(texts, max_length=settings.max_seq_length * PACK_CHARS_PER_TOKEN)
    train_texts, val_texts = train_val_split(texts)

    train_ds = Dataset.from_dict({"text": train_texts})
    val_ds = Dataset.from_dict({"text": val_texts}) if val_texts else None

    # ── Trainer ──
    Path(settings.output_dir).mkdir(parents=True, exist_ok=True)

    trainer = build_sft_trainer(
        model=model,
        tokenizer=tokenizer,
        train_dataset=train_ds,
        eval_dataset=val_ds if val_texts else None,
        training_args={
            **settings.to_training_args(),
            "eval_strategy": "steps" if val_texts else "no",
            "eval_steps": settings.save_steps if val_texts else None,
            "dataloader_pin_memory": True,
        },
        max_seq_length=settings.max_seq_length,
        # Texts are already packed by pack_sequences(); trl 1.x packing would
        # flatten batches (padding-free), which needs FlashAttention.
        packing=False,
    )

    logger.info("Starting training (HF peft + trl)...")
    trainer.train()

    # ── Save adapter ──
    adapter_path = str(Path(settings.output_dir) / "adapter")
    trainer.model.save_pretrained(adapter_path)
    tokenizer.save_pretrained(adapter_path)
    logger.info("Adapter saved to %s", adapter_path)

    return adapter_path


def main():
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%H:%M:%S",
    )
    quiet_noisy_loggers()
    from licensing import ProRequiredError, ensure_pro

    try:
        ensure_pro("qlora")
    except ProRequiredError as exc:
        logger.error("%s", exc)
        raise SystemExit(1)
    parser = argparse.ArgumentParser(description="QLoRA training via HF peft + trl")
    parser.add_argument("--preset", default="standard", choices=["low", "standard"])
    parser.add_argument("--dataset", default=None)
    parser.add_argument("--max-samples", type=int, default=None)
    parser.add_argument("--epochs", type=int, default=None)
    parser.add_argument("--base-model", default=None)
    parser.add_argument(
        "--settings-file", default=None,
        help="training preset JSON (fluxion-training-preset); applied on top of --preset",
    )
    args = parser.parse_args()

    settings = QLoRASettings.from_preset(VRAMPreset(args.preset))
    if args.settings_file:
        from finetune.presets import PresetError, load_preset

        try:
            settings = load_preset(args.settings_file).apply(settings)
        except PresetError as exc:
            logger.error("%s", exc)
            raise SystemExit(2)
    if args.epochs:
        settings.num_train_epochs = args.epochs
    if args.base_model:
        settings.base_model = args.base_model

    train(settings, args.dataset, args.max_samples)


if __name__ == "__main__":
    main()
