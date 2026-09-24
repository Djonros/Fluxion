"""Assemble a portable training package (zip) for the GPU machine.

Usage:
    python scripts/export_training_package.py
    python scripts/export_training_package.py --output dist/fluxion-training.zip
"""
from __future__ import annotations

import argparse
import logging
import zipfile
from pathlib import Path

logger = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parent.parent

INCLUDES: list[tuple[Path, str]] = [
    (ROOT / "finetune", "finetune"),
    (ROOT / "data_pipeline", "data_pipeline"),
    (ROOT / "requirements-train.txt", "requirements-train.txt"),
    (ROOT / "README-TRAIN.md", "README-TRAIN.md"),
]

EXCLUDE_SUFFIXES = {".pyc", ".log"}


def build_package(output: Path) -> int:
    output.parent.mkdir(parents=True, exist_ok=True)
    count = 0
    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as zf:
        for source, arc_root in INCLUDES:
            if not source.exists():
                logger.warning("Missing: %s", source)
                continue
            files = (
                sorted(source.rglob("*"))
                if source.is_dir()
                else [source]
            )
            for item in files:
                if not item.is_file() or item.suffix in EXCLUDE_SUFFIXES:
                    continue
                if "__pycache__" in item.parts:
                    continue
                arcname = (
                    f"{arc_root}/{item.relative_to(source).as_posix()}"
                    if source.is_dir()
                    else arc_root
                )
                zf.write(item, arcname)
                count += 1
                logger.info("+ %s", arcname)
    logger.info("Wrote %d files to %s (%.1f KB)", count, output, output.stat().st_size / 1024)
    return count


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    parser = argparse.ArgumentParser(description="Export portable training package")
    parser.add_argument("--output", default="dist/fluxion-training.zip")
    args = parser.parse_args()
    build_package(ROOT / args.output)


if __name__ == "__main__":
    main()
