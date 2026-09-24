"""The Stack v2 fetcher: streams Python files from bigcode/the-stack-v2."""
from __future__ import annotations

import logging
from collections.abc import Iterator
from dataclasses import dataclass

logger = logging.getLogger(__name__)

_DATASET_ID = "bigcode/the-stack-v2"
_CONFIG = "Python"


@dataclass
class StackSample:
    code: str
    repo: str = ""
    path: str = ""
    size: int = 0
    license: str = ""


class StackFetcher:
    """Streams Python source files from The Stack v2 via HF datasets.

    Requires:
      - ``huggingface-cli login`` with a valid token
      - Accepted the license at https://huggingface.co/datasets/bigcode/the-stack-v2
    """

    def __init__(self, split: str = "train", streaming: bool = True):
        self.split = split
        self.streaming = streaming

    def stream(self, limit: int | None = None) -> Iterator[StackSample]:
        """Yield Python source files from The Stack v2."""
        from datasets import load_dataset

        ds = load_dataset(
            _DATASET_ID,
            _CONFIG,
            split=self.split,
            streaming=self.streaming,
        )

        count = 0
        for item in ds:
            if limit is not None and count >= limit:
                break

            code = item.get("content", "")
            if not code or not code.strip():
                continue

            yield StackSample(
                code=code,
                repo=item.get("repository", item.get("repo_name", "")),
                path=item.get("files", item.get("path", "")),
                size=item.get("size", len(code)),
                license=item.get("license", ""),
            )
            count += 1

        logger.info("The Stack v2: yielded %d Python files", count)
