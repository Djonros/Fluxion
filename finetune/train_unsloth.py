"""QLoRA training via unsloth (preferred for 6 GB VRAM).

Usage (in Python 3.11/3.12 venv):
    python -m finetune.train_unsloth --dataset data/instruction_train.jsonl

Requires: unsloth, torch (cu121), bitsandbytes
"""
from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from .dataset_loader import pack_sequences, prepare_dataset, train_val_split
from .qlora_config import QLoRASettings, VRAMPreset

logger = logging.getLogger(__name__)


def train(
    settings: QLoRASettings,
    dataset_path: str | None = None,
    max_samples: int | None = None,
) -> str:
    """Run QLoRA training with unsloth. Returns path to adapter."""
    from unsloth import FastLanguageModel
    from trl import SFTTrainer
    from transformers import TrainingArguments

    ds_path = dataset_path or settings.dataset_path
    if not Path(ds_path).exists():
        logger.error("Dataset not found: %s", ds_path)
        sys.exit(1)

    # ── Load model ──
    logger.info("Loading model: %s", settings.base_model)
    model, tokenizer = FastLanguageModel.from_pretrained(
        model_name=settings.base_model,
        max_seq_length=settings.max_seq_length,
        load_in_4bit=settings.load_in_4bit,
        dtype=None,
    )

    # ── Add LoRA adapters ──
    model = FastLanguageModel.get_peft_model(
        model,
        r=settings.lora_r,
        target_modules=settings.target_modules,
        lora_alpha=settings.lora_alpha,
        lora_dropout=settings.lora_dropout,
        bias=settings.bias,
        use_gradient_checkpointing="unsloth",
        random_state=42,
    )

    # ── Prepare dataset ──
    texts = prepare_dataset(ds_path, max_samples=max_samples)
    if settings.packing:
        texts = pack_sequences(texts, max_length=settings.max_seq_length * 4)
    train_texts, val_texts = train_val_split(texts)

    from datasets import Dataset
    train_ds = Dataset.from_dict({"text": train_texts})
    val_ds = Dataset.from_dict({"text": val_texts})

    # ── ChatML formatting ──
    chatml_template = (
        "{% for message in messages %}"
        "{{'<|im_start|>' + message['role'] + '\n' + message['content'] + '<|im_end|>' + '\n'}}"
        "{% endfor %}"
    )
    if hasattr(tokenizer, "chat_template"):
        tokenizer.chat_template = chatml_template

    # ── Trainer ──
    Path(settings.output_dir).mkdir(parents=True, exist_ok=True)

    training_args = TrainingArguments(
        **settings.to_training_args(),
        eval_strategy="steps" if val_texts else "no",
        eval_steps=settings.save_steps if val_texts else None,
    )

    trainer = SFTTrainer(
        model=model,
        tokenizer=tokenizer,
        train_dataset=train_ds,
        eval_dataset=val_ds if val_texts else None,
        args=training_args,
        max_seq_length=settings.max_seq_length,
        dataset_text_field="text",
        packing=settings.packing,
    )

    logger.info("Starting training (unsloth)...")
    trainer.train()

    # ── Save adapter ──
    adapter_path = str(Path(settings.output_dir) / "adapter")
    model.save_pretrained(adapter_path)
    tokenizer.save_pretrained(adapter_path)
    logger.info("Adapter saved to %s", adapter_path)

    return adapter_path


def main():
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%H:%M:%S",
    )
    from licensing import ProRequiredError, ensure_pro

    try:
        ensure_pro("qlora")
    except ProRequiredError as exc:
        logger.error("%s", exc)
        raise SystemExit(1)
    parser = argparse.ArgumentParser(description="QLoRA training via unsloth")
    parser.add_argument("--preset", default="low", choices=["low", "standard"])
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
