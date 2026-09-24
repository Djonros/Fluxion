"""Merge LoRA adapter into base model → full merged weights.

Usage (in Python 3.11/3.12 venv):
    python -m finetune.merge --adapter data/lora_output/adapter

Requires: torch, transformers, peft
"""
from __future__ import annotations

import argparse
import logging
from pathlib import Path

from .qlora_config import QLoRASettings

logger = logging.getLogger(__name__)


def merge_lora(
    adapter_path: str,
    settings: QLoRASettings | None = None,
    output_dir: str | None = None,
) -> str:
    """Merge LoRA adapter into base model and save full weights.

    Args:
        adapter_path: Path to the saved LoRA adapter.
        settings: QLoRA settings (for base model name and output path).
        output_dir: Override output directory.

    Returns:
        Path to the merged model.
    """
    import torch
    from peft import PeftModel
    from transformers import AutoModelForCausalLM, AutoTokenizer

    settings = settings or QLoRASettings()
    merged_dir = output_dir or settings.merged_dir
    Path(merged_dir).mkdir(parents=True, exist_ok=True)

    logger.info("Loading base model: %s", settings.base_model)
    base_model = AutoModelForCausalLM.from_pretrained(
        settings.base_model,
        torch_dtype=torch.float16,
        device_map="cpu",
        trust_remote_code=True,
    )

    logger.info("Loading adapter: %s", adapter_path)
    model = PeftModel.from_pretrained(base_model, adapter_path)

    logger.info("Merging adapter weights...")
    model = model.merge_and_unload()

    logger.info("Saving merged model to: %s", merged_dir)
    model.save_pretrained(merged_dir, safe_serialization=True)

    tokenizer = AutoTokenizer.from_pretrained(settings.base_model, trust_remote_code=True)
    tokenizer.save_pretrained(merged_dir)

    logger.info("Merge complete: %s", merged_dir)
    return merged_dir


def main():
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%H:%M:%S",
    )
    parser = argparse.ArgumentParser(description="Merge LoRA adapter into base model")
    parser.add_argument("--adapter", required=True, help="Path to LoRA adapter")
    parser.add_argument("--output", default=None, help="Output directory")
    parser.add_argument("--base-model", default=None, help="Override base model")
    args = parser.parse_args()

    settings = QLoRASettings()
    if args.base_model:
        settings.base_model = args.base_model

    merge_lora(args.adapter, settings, args.output)


if __name__ == "__main__":
    main()
