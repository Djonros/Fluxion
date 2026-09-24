"""RepoCloner: shallow-clone GitHub repos for RAG indexing."""
from __future__ import annotations

import logging
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

logger = logging.getLogger(__name__)


@dataclass
class CloneResult:
    repo_url: str
    local_path: str
    success: bool = True
    error: str = ""


class RepoCloner:
    """Clones GitHub repositories with --depth 1 for RAG indexing."""

    def __init__(self, target_dir: str = "data/repos", depth: int = 1):
        self.target_dir = Path(target_dir)
        self.target_dir.mkdir(parents=True, exist_ok=True)
        self.depth = depth

    def clone(self, repo_url: str) -> CloneResult:
        """Clone a single repository. Returns CloneResult."""
        repo_name = self._extract_name(repo_url)
        local_path = self.target_dir / repo_name

        if local_path.exists() and any(local_path.iterdir()):
            logger.info("Repo already exists: %s", local_path)
            return CloneResult(
                repo_url=repo_url,
                local_path=str(local_path),
                success=True,
            )

        try:
            cmd = [
                "git", "clone",
                "--depth", str(self.depth),
                repo_url,
                str(local_path),
            ]
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=120,
            )
            if result.returncode != 0:
                return CloneResult(
                    repo_url=repo_url,
                    local_path=str(local_path),
                    success=False,
                    error=result.stderr.strip(),
                )
        except FileNotFoundError:
            return CloneResult(
                repo_url=repo_url,
                local_path=str(local_path),
                success=False,
                error="git not found on PATH",
            )
        except subprocess.TimeoutExpired:
            return CloneResult(
                repo_url=repo_url,
                local_path=str(local_path),
                success=False,
                error="git clone timed out",
            )
        except Exception as exc:
            return CloneResult(
                repo_url=repo_url,
                local_path=str(local_path),
                success=False,
                error=str(exc),
            )

        return CloneResult(repo_url=repo_url, local_path=str(local_path))

    def clone_all(self, repo_urls: list[str]) -> list[CloneResult]:
        """Clone multiple repositories."""
        results: list[CloneResult] = []
        for url in repo_urls:
            logger.info("Cloning %s ...", url)
            result = self.clone(url)
            results.append(result)
            if not result.success:
                logger.warning("Failed to clone %s: %s", url, result.error)
        return results

    @staticmethod
    def _extract_name(repo_url: str) -> str:
        name = repo_url.rstrip("/").split("/")[-1]
        if name.endswith(".git"):
            name = name[:-4]
        return name
