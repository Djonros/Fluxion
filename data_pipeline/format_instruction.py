"""Instruction formatter: converts (prompt, code) pairs into ChatML JSONL."""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)


@dataclass
class InstructionPair:
    """A single instruction sample: user prompt + assistant response."""
    instruction: str
    output: str
    source: str = "unknown"
    quality_score: float = 1.0


class InstructionFormatter:
    """Formats instruction pairs into ChatML JSONL for QLoRA training."""

    def __init__(
        self,
        system_prompt: str = (
            "You are Fluxion, an expert Python programming assistant.\n"
            "Provide concise, correct, well-structured code with explanations.\n"
            "Reply in the same language as the instruction."
        ),
    ):
        self.system_prompt = system_prompt

    def to_chatml(self, pair: InstructionPair) -> dict:
        """Convert one instruction pair to ChatML-format dict."""
        return {
            "messages": [
                {"role": "system", "content": self.system_prompt},
                {"role": "user", "content": pair.instruction},
                {"role": "assistant", "content": pair.output},
            ],
            "source": pair.source,
            "quality_score": pair.quality_score,
        }

    def to_jsonl_line(self, pair: InstructionPair) -> str:
        """Convert to a single JSONL line string."""
        return json.dumps(self.to_chatml(pair), ensure_ascii=False)

    def format_batch(
        self,
        pairs: list[InstructionPair],
        output_path: str | Path,
    ) -> int:
        """Write all pairs to *output_path* in ChatML JSONL format.

        Returns number of lines written.
        """
        output_path = Path(output_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        count = 0
        with output_path.open("w", encoding="utf-8") as f:
            for pair in pairs:
                f.write(self.to_jsonl_line(pair) + "\n")
                count += 1
        logger.info("Wrote %d instruction pairs to %s", count, output_path)
        return count

    def append_pair(
        self,
        pair: InstructionPair,
        output_path: str | Path,
    ) -> None:
        """Append a single pair to an existing JSONL file."""
        output_path = Path(output_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        with output_path.open("a", encoding="utf-8") as f:
            f.write(self.to_jsonl_line(pair) + "\n")

    @staticmethod
    def from_docstring_code(
        docstring: str,
        code: str,
        source: str = "codesearchnet",
    ) -> InstructionPair:
        """Create an instruction pair from a docstring + code pair."""
        instruction = (
            f"Implement a function that satisfies this docstring:\n\n"
            f"{docstring}"
        )
        return InstructionPair(
            instruction=instruction,
            output=code,
            source=source,
        )

    @staticmethod
    def from_code_explain(
        code: str,
        explanation: str,
        source: str = "stack",
    ) -> InstructionPair:
        """Create a pair: given code, explain what it does."""
        instruction = f"Explain what this Python code does:\n\n```python\n{code}\n```"
        return InstructionPair(
            instruction=instruction,
            output=explanation,
            source=source,
        )
