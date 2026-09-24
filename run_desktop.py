"""PyInstaller entry point for the Fluxion desktop app."""
from __future__ import annotations

import os
import sys


def _prepare_config() -> None:
    """Use the bundled config when no local config/ exists (frozen builds)."""
    if os.environ.get("AI_AGENT_CONFIG"):
        return
    if os.path.exists(os.path.join("config", "config.yaml")):
        return
    if getattr(sys, "frozen", False):
        base = getattr(sys, "_MEIPASS", os.path.dirname(sys.executable))
        bundled = os.path.join(base, "config", "config.yaml")
        if os.path.exists(bundled):
            os.environ["AI_AGENT_CONFIG"] = bundled


def main() -> int:
    _prepare_config()

    from desktop.app import main as run_app

    return run_app()


if __name__ == "__main__":
    raise SystemExit(main())
