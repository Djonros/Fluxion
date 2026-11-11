"""Keys and passwords of «Личный помощник» in the Windows Credential Manager, not in json.

    set_secret("app:crm", "ключ API")  →  «Fluxion:app:crm» in «Диспетчер учётных данных»

Off Windows (and when pywin32 is missing) they live in memory only, until Fluxion closes.
"""
from __future__ import annotations

import sys

PREFIX = "Fluxion:"
_memory: dict[str, str] = {}


def _cred():
    if sys.platform != "win32":
        return None
    try:
        import win32cred

        return win32cred
    except ImportError:
        return None


def set_secret(name: str, value: str) -> None:
    cred = _cred()
    if cred is None:
        _memory[name] = value
        return
    cred.CredWrite({"Type": cred.CRED_TYPE_GENERIC, "TargetName": PREFIX + name, "UserName": "Fluxion",
                    "CredentialBlob": value, "Persist": cred.CRED_PERSIST_LOCAL_MACHINE}, 0)


def get_secret(name: str) -> str:
    cred = _cred()
    if cred is None:
        return _memory.get(name, "")
    try:
        blob = cred.CredRead(PREFIX + name, cred.CRED_TYPE_GENERIC)["CredentialBlob"]
    except Exception:
        return ""
    if isinstance(blob, bytes):
        try:
            return blob.decode("utf-16-le")
        except UnicodeDecodeError:
            return blob.decode("utf-8", "replace")
    return str(blob or "")


def delete_secret(name: str) -> None:
    cred = _cred()
    if cred is None:
        _memory.pop(name, None)
        return
    try:
        cred.CredDelete(PREFIX + name, cred.CRED_TYPE_GENERIC)
    except Exception:
        pass
