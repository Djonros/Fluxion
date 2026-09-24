"""Instruction dataset fetcher: streams ready-made instruction data (Evol-Instruct-Code)."""
from __future__ import annotations

import logging
from collections.abc import Iterator
from dataclasses import dataclass

from .format_instruction import InstructionPair

logger = logging.getLogger(__name__)

_DATASETS = {
    "evol_instruct_code": "mistral/Mistral-7B-Instruct-v0.2-code-alpaca",
    "code_alpaca": "sahil2801/code_alpaca",
    "python_alpaca": "Vezora/Tested-143k-Python-Alpaca",
}


@dataclass
class InstructionDatasetConfig:
    name: str
    dataset_id: str
    instruction_field: str = "instruction"
    output_field: str = "output"
    split: str = "train"


class InstructionFetcher:
    """Streams instruction-tuning data from HuggingFace datasets."""

    def __init__(self, config: InstructionDatasetConfig | None = None):
        self.config = config or InstructionDatasetConfig(
            name="code_alpaca",
            dataset_id=_DATASETS["code_alpaca"],
        )

    def stream(self, limit: int | None = None) -> Iterator[InstructionPair]:
        """Yield InstructionPair items from the configured dataset."""
        from datasets import load_dataset

        ds = load_dataset(
            self.config.dataset_id,
            split=self.config.split,
            streaming=True,
        )

        count = 0
        for item in ds:
            if limit is not None and count >= limit:
                break

            instruction = item.get(self.config.instruction_field, "")
            output = item.get(self.config.output_field, "")

            if not instruction or not output:
                continue

            yield InstructionPair(
                instruction=instruction,
                output=output,
                source=self.config.name,
            )
            count += 1

        logger.info("InstructionFetcher: yielded %d pairs", count)

    @staticmethod
    def available_datasets() -> dict[str, str]:
        return dict(_DATASETS)
