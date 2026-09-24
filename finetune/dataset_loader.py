"""Dataset loader: reads ChatML JSONL, applies ChatML template, packs sequences."""
from __future__ import annotations

import json
import logging
from pathlib import Path

from .qlora_config import (
    ASSISTANT_TOKEN,
    IM_END,
    IM_START,
    SYSTEM_TOKEN,
    USER_TOKEN,
)

logger = logging.getLogger(__name__)


def format_chatml(messages: list[dict[str, str]]) -> str:
    """Render a list of messages as a ChatML-formatted string.

    Args:
        messages: [{"role": "system", "content": "..."},
                   {"role": "user", "content": "..."},
                   {"role": "assistant", "content": "..."}]

    Returns:
        ChatML text with special tokens.
    """
    parts: list[str] = []
    for msg in messages:
        role = msg.get("role", "user")
        content = msg.get("content", "")
        parts.append(f"{IM_START}{role}\n{content}\n{IM_END}")
    return "\n".join(parts)


def load_jsonl(path: str | Path) -> list[dict]:
    """Load a JSONL file. Returns list of dicts with 'messages' key."""
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f"Dataset not found: {path}")

    samples: list[dict] = []
    with path.open("r", encoding="utf-8") as f:
        for lineno, line in enumerate(f, 1):
            line = line.strip()
            if not line:
                continue
            try:
                data = json.loads(line)
            except json.JSONDecodeError as exc:
                logger.warning("Skipping invalid JSON at line %d: %s", lineno, exc)
                continue
            if "messages" not in data:
                logger.warning("Skipping line %d: no 'messages' key", lineno)
                continue
            samples.append(data)
    logger.info("Loaded %d samples from %s", len(samples), path)
    return samples


def prepare_dataset(
    path: str | Path,
    max_samples: int | None = None,
    add_eos: bool = True,
) -> list[str]:
    """Load JSONL and convert each sample to ChatML text.

    Args:
        path: Path to ChatML JSONL file.
        max_samples: Optional limit on number of samples.
        add_eos: Whether to append EOS token at the end.

    Returns:
        List of formatted ChatML strings.
    """
    samples = load_jsonl(path)
    if max_samples is not None:
        samples = samples[:max_samples]

    texts: list[str] = []
    for sample in samples:
        messages = sample.get("messages", [])
        text = format_chatml(messages)
        if add_eos:
            text += "\n<|endoftext|>"
        texts.append(text)
    logger.info("Prepared %d ChatML texts", len(texts))
    return texts


def pack_sequences(
    texts: list[str],
    max_length: int = 2048,
    separator: str = "\n\n",
) -> list[str]:
    """Pack short sequences together to fill the context window.

    Args:
        texts: List of pre-tokenized text strings.
        max_length: Target sequence length in characters (approximate).
        separator: String inserted between packed sequences.

    Returns:
        Packed sequences.
    """
    packed: list[str] = []
    current: list[str] = []
    current_len = 0

    for text in texts:
        text_len = len(text)
        if current_len + text_len + len(separator) > max_length and current:
            packed.append(separator.join(current))
            current = [text]
            current_len = text_len
        else:
            current.append(text)
            current_len += text_len + len(separator)

    if current:
        packed.append(separator.join(current))

    logger.info(
        "Packed %d sequences into %d (avg %.0f chars each)",
        len(texts), len(packed),
        sum(len(p) for p in packed) / max(1, len(packed)),
    )
    return packed


def train_val_split(
    texts: list[str],
    val_ratio: float = 0.05,
    seed: int = 42,
) -> tuple[list[str], list[str]]:
    """Shuffle and split texts into train/validation sets."""
    import random

    rng = random.Random(seed)
    indices = list(range(len(texts)))
    rng.shuffle(indices)

    val_size = max(1, int(len(texts) * val_ratio))
    val_indices = set(indices[:val_size])

    train = [texts[i] for i in range(len(texts)) if i not in val_indices]
    val = [texts[i] for i in sorted(val_indices)]
    return train, val
