"""Dataset loader: reads chat JSONL (ChatML, ShareGPT, Alpaca, Q/A), applies ChatML, packs sequences."""
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


class DatasetFormatError(ValueError):
    """The dataset has no line in a supported format."""


SUPPORTED_FORMATS = (
    '{"messages": [{"role": "user", "content": …}, {"role": "assistant", …}]} (ChatML)',
    '{"conversations": [{"from": "human", "value": …}, {"from": "gpt", …}]} (ShareGPT)',
    '{"instruction": …, "input": …, "output": …} (Alpaca)',
    '{"prompt": …, "completion"/"response": …}',
    '{"question": …, "answer": …}',
    '{"input": …, "output": …}',
    '{"problem": …, "solution": …}',
    '{"вопрос": …, "ответ": …}',
)
_ROLE_ALIASES = {
    "human": "user", "user": "user", "prompter": "user",
    "gpt": "assistant", "assistant": "assistant", "bot": "assistant",
    "model": "assistant", "chatgpt": "assistant",
    "system": "system",
}
_PAIR_KEYS = (
    ("prompt", "completion"),
    ("prompt", "response"),
    ("question", "answer"),
    ("query", "response"),
    ("input", "output"),
    ("user", "assistant"),
    ("problem", "solution"),
    ("вопрос", "ответ"),
)
_DETAILED_SKIPS = 3


def _turns(items) -> list[dict[str, str]] | None:
    """Normalize a list of chat turns (ChatML or ShareGPT keys) to role/content."""
    if not isinstance(items, list):
        return None
    messages = []
    for item in items:
        if not isinstance(item, dict):
            return None
        role = str(item.get("role", item.get("from", "user"))).strip().lower()
        content = item.get("content", item.get("value", ""))
        if not isinstance(content, str):
            return None
        messages.append({"role": _ROLE_ALIASES.get(role, role), "content": content})
    return messages


def _text(value) -> str:
    return value.strip() if isinstance(value, str) else ""


def to_messages(data) -> tuple[list[dict[str, str]] | None, str]:
    """Convert one dataset record to chat messages; returns (messages, format name)."""
    if not isinstance(data, dict):
        return None, ""
    for key, name in (("messages", "ChatML"), ("conversations", "ShareGPT")):
        if key in data:
            messages = _turns(data[key])
            if messages:
                return messages, name
            return None, ""
    system = _text(data.get("system"))
    prefix = [{"role": "system", "content": system}] if system else []
    if _text(data.get("instruction")) and _text(data.get("output")):
        prompt = _text(data["instruction"])
        extra = _text(data.get("input"))
        if extra:
            prompt += "\n\n" + extra
        return prefix + [
            {"role": "user", "content": prompt},
            {"role": "assistant", "content": _text(data["output"])},
        ], "Alpaca"
    for user_key, answer_key in _PAIR_KEYS:
        if _text(data.get(user_key)) and _text(data.get(answer_key)):
            return prefix + [
                {"role": "user", "content": _text(data[user_key])},
                {"role": "assistant", "content": _text(data[answer_key])},
            ], f"{user_key}/{answer_key}"
    return None, ""


def _format_help(keys: list[str]) -> str:
    found = ", ".join(keys) if keys else "нет полей"
    lines = "\n".join(f"  • {item}" for item in SUPPORTED_FORMATS)
    return (
        f"Формат датасета не распознан: в строках есть поля {found}. "
        f"Поддерживаются строки JSONL вида:\n{lines}"
    )


def load_jsonl(path: str | Path, limit: int | None = None) -> list[dict]:
    """Load a JSONL dataset; every sample is returned as ``{"messages": [...]}``.

    Besides ChatML, ShareGPT, Alpaca and simple question/answer records are
    converted. Skipped lines are reported once with a summary (a dataset of
    another format used to print one warning per line). Raises
    :class:`DatasetFormatError` when no line can be used. With *limit*
    reading stops after that many samples (a 250 MB file is not parsed whole
    for a short run).
    """
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f"Dataset not found: {path}")

    samples: list[dict] = []
    skipped = 0
    formats: dict[str, int] = {}
    first_keys: list[str] = []
    with path.open("r", encoding="utf-8-sig") as f:
        for lineno, line in enumerate(f, 1):
            line = line.strip()
            if not line:
                continue
            try:
                data = json.loads(line)
            except json.JSONDecodeError as exc:
                skipped += 1
                if skipped <= _DETAILED_SKIPS:
                    logger.warning("Skipping invalid JSON at line %d: %s", lineno, exc)
                continue
            messages, name = to_messages(data)
            if messages is None:
                skipped += 1
                keys = sorted(data) if isinstance(data, dict) else []
                if not first_keys:
                    first_keys = keys
                if skipped <= _DETAILED_SKIPS:
                    logger.warning(
                        "Skipping line %d: unsupported record (fields: %s)",
                        lineno, ", ".join(keys) or type(data).__name__,
                    )
                continue
            formats[name] = formats.get(name, 0) + 1
            samples.append({**data, "messages": messages} if isinstance(data, dict) else {"messages": messages})
            if limit and len(samples) >= limit:
                break
    if skipped > _DETAILED_SKIPS:
        logger.warning("Skipped %d lines in total", skipped)
    if not samples and skipped:
        raise DatasetFormatError(_format_help(first_keys))
    described = ", ".join(f"{name}: {count}" for name, count in formats.items())
    logger.info("Loaded %d samples from %s (%s)", len(samples), path, described or "empty")
    return samples


def estimate_samples(path: str | Path, probe: int = 200) -> int:
    """Rough number of records: file size / average length of the first lines."""
    path = Path(path)
    lengths: list[int] = []
    try:
        with path.open("rb") as f:
            for raw in f:
                if raw.strip():
                    lengths.append(len(raw))
                if len(lengths) >= probe:
                    break
        size = path.stat().st_size
    except OSError:
        return 0
    if not lengths:
        return 0
    if len(lengths) < probe:
        return len(lengths)
    return int(size / (sum(lengths) / len(lengths)))


def check_dataset(path: str | Path, limit: int = 200) -> str:
    """Quick format check of the first *limit* records; returns a problem or ''."""
    usable = 0
    keys: list[str] = []
    try:
        with Path(path).open("r", encoding="utf-8-sig") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                limit -= 1
                if limit < 0:
                    break
                try:
                    data = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if to_messages(data)[0] is not None:
                    usable += 1
                elif not keys and isinstance(data, dict):
                    keys = sorted(data)
    except UnicodeDecodeError:
        return "Датасет должен быть в кодировке UTF-8."
    except OSError as exc:
        return f"Датасет не читается: {exc}"
    if usable:
        return ""
    if limit >= 0 and not keys:
        return "Датасет пуст или не содержит строк JSON."
    return _format_help(keys)


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
    samples = load_jsonl(path, limit=max_samples or None)
    if max_samples:
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
