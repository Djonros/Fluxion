"""PyInstaller entry point for the Fluxion Browser desktop app (Fluxion + web browser)."""
from __future__ import annotations

import os
import sys


def _prepare_config() -> None:
    """Pick config.yaml: the local config/, the user's one, else the bundled one.

    A frozen build looks for the user's config in the data folder
    (%LOCALAPPDATA%\\Fluxion\\config, or next to the exe in portable mode).
    """
    if os.environ.get("AI_AGENT_CONFIG"):
        return
    if os.path.exists(os.path.join("config", "config.yaml")):
        return
    from core.app_paths import user_config_path

    user_config = user_config_path()
    if user_config is not None:
        os.environ["AI_AGENT_CONFIG"] = str(user_config)
        return
    if getattr(sys, "frozen", False):
        base = getattr(sys, "_MEIPASS", os.path.dirname(sys.executable))
        bundled = os.path.join(base, "config", "config.yaml")
        if os.path.exists(bundled):
            os.environ["AI_AGENT_CONFIG"] = bundled


def main() -> int:
    from core.app_paths import migrate_legacy_data

    # data of an earlier build next to the exe -> %LOCALAPPDATA%\Fluxion (once)
    migrate_legacy_data()
    _prepare_config()

    from desktop_browser.i18n import load_saved_language

    load_saved_language()  # before the window module: its texts are read on import

    from desktop_browser.app import main as run_app

    return run_app()


if __name__ == "__main__":
    raise SystemExit(main())
