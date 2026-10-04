"""License data model, plan constants and key wire format."""
from __future__ import annotations

import base64
import binascii
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

FREE = "free"
PRO = "pro"
PLANS = (FREE, PRO)

PRO_FEATURES = frozenset({"agent_write", "qlora", "multi_model", "model_catalog"})

_KEY_ID_RE = re.compile(r"^[0-9a-f]{16,64}$")


class LicenseError(ValueError):
    pass


def normalize_device(device: str) -> str:
    """Normalize a device code for comparison: uppercase, no dashes/spaces."""
    if not isinstance(device, str) or not device.strip():
        raise LicenseError("device code must be a non-empty string")
    return re.sub(r"[\s-]+", "", device.strip().upper())


def validate_key_id(key_id: str) -> str:
    if not isinstance(key_id, str) or not _KEY_ID_RE.match(key_id):
        raise LicenseError(f"invalid key id: {key_id!r}")
    return key_id


def _b64encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _b64decode(segment: str) -> bytes:
    standard = segment.translate(str.maketrans("-_", "+/"))
    padding = "=" * (-len(standard) % 4)
    try:
        return base64.b64decode(standard + padding, validate=True)
    except (binascii.Error, ValueError):
        raise LicenseError("malformed license key") from None


def encode_key(payload: bytes, signature: bytes) -> str:
    return f"{_b64encode(payload)}.{_b64encode(signature)}"


def decode_key(key: str) -> tuple[bytes, bytes]:
    if not isinstance(key, str):
        raise LicenseError("license key must be a string")
    parts = key.strip().split(".")
    if len(parts) != 2 or not parts[0] or not parts[1]:
        raise LicenseError("malformed license key")
    payload = _b64decode(parts[0])
    signature = _b64decode(parts[1])
    return payload, signature


def parse_datetime(value: Any) -> datetime:
    if not isinstance(value, str) or not value:
        raise LicenseError(f"invalid datetime: {value!r}")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        raise LicenseError(f"invalid datetime: {value!r}") from None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


@dataclass(frozen=True)
class License:
    email: str
    plan: str
    expires_at: datetime | None = None
    issued_at: datetime | None = None
    key_id: str = ""
    device: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.email, str) or not self.email.strip():
            raise LicenseError("license email must be a non-empty string")
        if self.plan not in PLANS:
            raise LicenseError(f"unknown plan: {self.plan!r}")
        if self.expires_at is not None and self.expires_at.tzinfo is None:
            object.__setattr__(self, "expires_at", self.expires_at.replace(tzinfo=timezone.utc))
        if self.issued_at is not None and self.issued_at.tzinfo is None:
            object.__setattr__(self, "issued_at", self.issued_at.replace(tzinfo=timezone.utc))
        if self.key_id:
            validate_key_id(self.key_id)
        if self.device is not None:
            object.__setattr__(self, "device", normalize_device(self.device))

    @property
    def is_expired(self) -> bool:
        return self.expires_at is not None and datetime.now(timezone.utc) >= self.expires_at

    @property
    def is_pro(self) -> bool:
        return self.plan == PRO and not self.is_expired

    def feature_enabled(self, feature: str) -> bool:
        if feature not in PRO_FEATURES:
            return True
        return self.is_pro

    def to_payload(self) -> dict[str, Any]:
        payload: dict[str, Any] = {"email": self.email, "plan": self.plan}
        if self.expires_at is not None:
            payload["expires"] = self.expires_at.isoformat()
        if self.issued_at is not None:
            payload["issued_at"] = self.issued_at.isoformat()
        if self.key_id:
            payload["kid"] = self.key_id
        if self.device is not None:
            payload["dev"] = self.device
        return payload

    @classmethod
    def from_payload(cls, payload: Any) -> License:
        if not isinstance(payload, dict):
            raise LicenseError("license payload must be a JSON object")
        expires_at = None
        if payload.get("expires") is not None:
            expires_at = parse_datetime(payload["expires"])
        issued_at = None
        if payload.get("issued_at") is not None:
            issued_at = parse_datetime(payload["issued_at"])
        return cls(
            email=payload.get("email"),
            plan=payload.get("plan"),
            expires_at=expires_at,
            issued_at=issued_at,
            key_id=str(payload.get("kid") or ""),
            device=payload.get("dev"),
        )
