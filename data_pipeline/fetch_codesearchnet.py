"""CodeSearchNet fetcher: streams Python (docstring, code) pairs from HF datasets."""
from __future__ import annotations

import logging
from collections.abc import Iterator
from dataclasses import dataclass

logger = logging.getLogger(__name__)

_DATASET_ID = "code-search-net/code_search_net"
_SPLITS = ("train", "validation", "test")


@dataclass
class CodePair:
    code: str
    docstring: str
    repo: str = ""
    path: str = ""
    language: str = "python"


class CodeSearchNetFetcher:
    """Streams Python code/docstring pairs from CodeSearchNet via HF datasets."""

    def __init__(self, split: str = "train", streaming: bool = True):
        self.split = split
        self.streaming = streaming

    def stream(self, limit: int | None = None) -> Iterator[CodePair]:
        """Yield Python CodePair items from the dataset."""
        from datasets import load_dataset

        ds = load_dataset(_DATASET_ID, split=self.split, streaming=self.streaming)

        count = 0
        for item in ds:
            if limit is not None and count >= limit:
                break
            lang = item.get("language", "")
            if lang and lang.lower() != "python":
                continue

            code = item.get("func_code_tokens", item.get("whole_func_string", ""))
            if isinstance(code, list):
                code = " ".join(code)

            func_code = item.get("func_code_string", "")
            if func_code:
                code = func_code

            docstring = item.get("func_documentation_string", item.get("docstring", ""))

            if not code or not docstring:
                continue

            yield CodePair(
                code=code,
                docstring=docstring,
                repo=item.get("repository_name", ""),
                path=item.get("func_path", item.get("path", "")),
                language="python",
            )
            count += 1

        logger.info("CodeSearchNet: yielded %d Python pairs", count)
