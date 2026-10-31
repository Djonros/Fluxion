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


SETUP_MUTEX = "FluxionSetupRunning"  # SetupMutex of installer/fluxion.iss
SETUP_WAIT_S = 180


def setup_running() -> bool:
    """FluxionSetup.exe is installing: its files may be half-copied."""
    if sys.platform != "win32":
        return False
    try:
        import ctypes

        kernel32 = ctypes.windll.kernel32
        for name in (SETUP_MUTEX, "Global\\" + SETUP_MUTEX):
            handle = kernel32.OpenMutexW(0x00100000, False, name)  # SYNCHRONIZE
            if handle:
                kernel32.CloseHandle(handle)
                return True
    except Exception:
        pass
    return False


def wait_for_setup(timeout: float = SETUP_WAIT_S, step: float = 0.5, running=setup_running, sleep=None) -> bool:
    """Wait until the installer is done (the restart after a one-click update starts
    while Setup is finishing; a shortcut can be clicked mid-update). True: free to start."""
    import time

    sleep = sleep or time.sleep
    waited = 0.0
    while running():
        if waited >= timeout:
            return False
        sleep(step)
        waited += step
    return True


def main() -> int:
    if not wait_for_setup():
        if sys.platform == "win32":
            import ctypes

            ctypes.windll.user32.MessageBoxW(
                None, "Fluxion обновляется. Дождитесь конца установки и запустите программу снова.\n\n"
                      "Fluxion is being updated. Wait for the installation to finish and start it again.",
                "Fluxion", 0x40)
        return 1
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
