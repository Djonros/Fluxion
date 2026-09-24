"""Pipeline orchestrator: fetch → filter → dedup → format → JSONL.

Usage:
    python -m data_pipeline.run --source codesearchnet --limit 5000 \
        --output data/instruction_train.jsonl
"""
from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

import yaml

from .dedup import MinHashDeduper
from .filters import Filters
from .format_instruction import InstructionFormatter, InstructionPair

logger = logging.getLogger(__name__)


def run_codesearchnet(
    output_path: str,
    limit: int | None = None,
    dedup: bool = True,
    filter: bool = True,
) -> int:
    """Fetch CodeSearchNet Python pairs → filter → dedup → ChatML JSONL."""
    from .fetch_codesearchnet import CodeSearchNetFetcher

    fetcher = CodeSearchNetFetcher(split="train", streaming=True)
    fmt = InstructionFormatter()
    flt = Filters()
    deduper = MinHashDeduper() if dedup else None

    pairs: list[InstructionPair] = []
    seen_texts: list[str] = []

    for pair in fetcher.stream(limit=limit):
        if filter and not flt.keep(pair.code):
            continue

        instruction_pair = fmt.from_docstring_code(
            docstring=pair.docstring, code=pair.code, source="codesearchnet"
        )

        if deduper and deduper.is_duplicate(instruction_pair.output, seen_texts):
            continue

        seen_texts.append(instruction_pair.output)
        pairs.append(instruction_pair)
        logger.info("Collected %d pairs", len(pairs))

    count = fmt.format_batch(pairs, output_path)
    logger.info("Wrote %d instruction pairs to %s", count, output_path)
    return count


def run_instruction(
    dataset_name: str,
    output_path: str,
    limit: int | None = None,
    dedup: bool = True,
) -> int:
    """Fetch instruction dataset → filter → dedup → ChatML JSONL."""
    from .fetch_instruction import InstructionDatasetConfig, InstructionFetcher

    available = InstructionFetcher.available_datasets()
    if dataset_name not in available:
        logger.error("Unknown dataset: %s. Available: %s", dataset_name, list(available))
        return 0

    config = InstructionDatasetConfig(
        name=dataset_name,
        dataset_id=available[dataset_name],
    )
    fetcher = InstructionFetcher(config)
    fmt = InstructionFormatter()
    deduper = MinHashDeduper() if dedup else None

    pairs: list[InstructionPair] = []
    seen_texts: list[str] = []

    for pair in fetcher.stream(limit=limit):
        if deduper and deduper.is_duplicate(pair.output, seen_texts):
            continue
        seen_texts.append(pair.output)
        pairs.append(pair)

    count = fmt.format_batch(pairs, output_path)
    logger.info("Wrote %d instruction pairs to %s", count, output_path)
    return count


def run_clone_and_index(
    repos_yaml: str = "config/repos.yaml",
    repos_dir: str = "data/repos",
    chroma_dir: str = "data/chroma",
) -> int:
    """Clone repos from YAML config and index into ChromaDB for RAG."""
    from .repo_clone import RepoCloner
    from rag.service import RAGConfig, RAGService
    from core.config import Settings

    with open(repos_yaml, encoding="utf-8") as f:
        config = yaml.safe_load(f) or {}

    repo_urls = config.get("repos", [])
    if not repo_urls:
        logger.warning("No repos listed in %s", repos_yaml)
        return 0

    cloner = RepoCloner(target_dir=repos_dir)
    clone_results = cloner.clone_all(repo_urls)

    settings = Settings.load()
    rag = RAGService(RAGConfig.from_settings(settings.rag))

    total_chunks = 0
    for result in clone_results:
        if not result.success:
            continue
        count = rag.index(result.local_path)
        total_chunks += count
        logger.info("Indexed %s → %d chunks", result.local_path, count)

    logger.info("Total: %d chunks from %d repos", total_chunks, len(clone_results))
    return total_chunks


def main():
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%H:%M:%S",
    )

    parser = argparse.ArgumentParser(description="Data pipeline for Fluxion")
    sub = parser.add_subparsers(dest="command")

    p_csn = sub.add_parser("codesearchnet", help="Fetch CodeSearchNet → JSONL")
    p_csn.add_argument("--output", default="data/instruction_train.jsonl")
    p_csn.add_argument("--limit", type=int, default=None)
    p_csn.add_argument("--no-dedup", action="store_true")
    p_csn.add_argument("--no-filter", action="store_true")

    p_inst = sub.add_parser("instruction", help="Fetch instruction dataset → JSONL")
    p_inst.add_argument("--dataset", default="code_alpaca")
    p_inst.add_argument("--output", default="data/instruction_train.jsonl")
    p_inst.add_argument("--limit", type=int, default=None)
    p_inst.add_argument("--no-dedup", action="store_true")

    p_clone = sub.add_parser("clone", help="Clone repos and index for RAG")
    p_clone.add_argument("--repos", default="config/repos.yaml")

    args = parser.parse_args()

    if args.command == "codesearchnet":
        run_codesearchnet(
            output_path=args.output,
            limit=args.limit,
            dedup=not args.no_dedup,
            filter=not args.no_filter,
        )
    elif args.command == "instruction":
        run_instruction(
            dataset_name=args.dataset,
            output_path=args.output,
            limit=args.limit,
            dedup=not args.no_dedup,
        )
    elif args.command == "clone":
        run_clone_and_index(repos_yaml=args.repos)
    else:
        parser.print_help()
        sys.exit(1)


if __name__ == "__main__":
    main()
