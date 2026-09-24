"""Stable per-machine device code for optional hardware-bound licenses.

The code is derived from an OS-level machine identifier (Windows MachineGuid,
Linux machine-id, macOS IOPlatformUUID; MAC fallback) and hashed to a short
readable string the customer can send to the key issuer.
"""
from __future__ import annotations

import hashlib
import os
import subprocess
import sys
import uuid
from pathlib import Path


def _raw_identifier() -> str:
    if os.name == "nt":
        try:
            import winreg

            with winreg.OpenKey(
                winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Cryptography"
            ) as key:
                value, _ = winreg.QueryValueEx(key, "MachineGuid")
                if value:
                    return str(value)
        except OSError:
            pass
    for candidate in ("/etc/machine-id", "/var/lib/dbus/machine-id"):
        path = Path(candidate)
        if path.is_file():
            text = path.read_text(encoding="utf-8").strip()
            if text:
                return text
    if sys.platform == "darwin":
        try:
            out = subprocess.run(
                ["ioreg", "-rd1", "-c", "IOPlatformExpertDevice"],
                capture_output=True,
                text=True,
                timeout=5,
            )
            for line in out.stdout.splitlines():
                if "IOPlatformUUID" in line:
                    parts = line.split('"')
                    if len(parts) >= 2 and parts[-2]:
                        return parts[-2]
        except (OSError, subprocess.SubprocessError):
            pass
    return f"mac-{uuid.getnode():012x}"


def device_code() -> str:
    """Return the stable device code, formatted XXXX-XXXX-XXXX-XXXX."""
    digest = hashlib.sha256(_raw_identifier().encode("utf-8")).hexdigest()
    return "-".join(digest[i : i + 4] for i in range(0, 16, 4)).upper()


if __name__ == "__main__":
    print(device_code())
