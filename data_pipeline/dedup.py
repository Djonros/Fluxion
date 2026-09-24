"""MinHash deduplication using datasketch."""
from __future__ import annotations

import hashlib
import logging
import re
from dataclasses import dataclass

logger = logging.getLogger(__name__)

_DEFAULT_THRESHOLD = 0.9
_DEFAULT_NUM_PERM = 128
_NGRAM_SIZE = 5

_TOKEN_RE = re.compile(r"[a-zA-Z_]\w*|[^\s\w]")


@dataclass
class DedupResult:
    total: int
    unique: int
    duplicates: int


def _normalize(text: str) -> str:
    text = re.sub(r"#.*$", "", text, flags=re.MULTILINE)
    text = re.sub(r'""".*?"""', "", text, flags=re.DOTALL)
    text = re.sub(r"'''.*?'''", "", text, flags=re.DOTALL)
    text = re.sub(r"\s+", " ", text).strip()
    return text.lower()


def _tokens(text: str) -> list[str]:
    return _TOKEN_RE.findall(_normalize(text))


def _ngrams(tokens: list[str], n: int = _NGRAM_SIZE) -> list[tuple[str, ...]]:
    if len(tokens) < n:
        return [tuple(tokens)]
    return [tuple(tokens[i : i + n]) for i in range(len(tokens) - n + 1)]


def _minhash_for_text(text: str, num_perm: int) -> "MinHash":
    from datasketch import MinHash

    mh = MinHash(num_perm=num_perm)
    for gram in _ngrams(_tokens(text)):
        mh.update("".join(gram).encode("utf-8"))
    return mh


class MinHashDeduper:
    """Near-duplicate removal using MinHash LSH."""

    def __init__(
        self,
        threshold: float = _DEFAULT_THRESHOLD,
        num_perm: int = _DEFAULT_NUM_PERM,
    ):
        self.threshold = threshold
        self.num_perm = num_perm

    def dedup(self, items: list[tuple[str, str]]) -> list[tuple[str, str]]:
        """Remove near-duplicate items.

        Args:
            items: list of (id, text) pairs.

        Returns:
            Filtered list of (id, text) pairs with duplicates removed.
        """
        from datasketch import MinHash, MinHashLSH

        if not items:
            return []

        lsh = MinHashLSH(threshold=self.threshold, num_perm=self.num_perm)
        hashes: dict[str, MinHash] = {}
        kept: list[tuple[str, str]] = []

        for item_id, text in items:
            mh = _minhash_for_text(text, self.num_perm)
            hashes[item_id] = mh

            dupes = lsh.query(mh)
            if dupes:
                logger.debug("Dedup: %s is duplicate of %s", item_id, dupes[:3])
                continue

            lsh.insert(item_id, mh)
            kept.append((item_id, text))

        return kept

    def dedup_strings(self, texts: list[str]) -> list[str]:
        """Dedup plain strings. Returns unique texts in original order."""
        items = [(str(i), t) for i, t in enumerate(texts)]
        result = self.dedup(items)
        id_to_text = {item_id: t for item_id, t in result}
        return [id_to_text[str(i)] for i in range(len(texts)) if str(i) in id_to_text]

    def is_duplicate(self, text: str, existing: list[str]) -> bool:
        """Check if *text* is a near-duplicate of any text in *existing*."""
        if not existing:
            return False
        mh_new = _minhash_for_text(text, self.num_perm)
        for ex in existing:
            mh_ex = _minhash_for_text(ex, self.num_perm)
            if mh_new.jaccard(mh_ex) >= self.threshold:
                return True
        return False
