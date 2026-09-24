"""Offline license verification against an embedded Ed25519 public key."""
from __future__ import annotations

import base64
import json

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from .models import License, LicenseError, decode_key

PUBLIC_KEY_B64 = "MCowBQYDK2VwAyEAWPa9DdaU374NyFPeB338lvhJ/YWaXkyY4qUnTmctBhI="


def load_public_key(public_key_b64: str = PUBLIC_KEY_B64) -> Ed25519PublicKey:
    try:
        der = base64.b64decode(public_key_b64, validate=True)
        key = serialization.load_der_public_key(der)
    except (ValueError, TypeError) as exc:
        raise LicenseError("invalid public key") from exc
    if not isinstance(key, Ed25519PublicKey):
        raise LicenseError("public key is not an Ed25519 key")
    return key


def verify_license(key: str, public_key_b64: str | None = None) -> License:
    if public_key_b64 is None:
        public_key_b64 = PUBLIC_KEY_B64
    payload_bytes, signature = decode_key(key)
    public_key = load_public_key(public_key_b64)
    try:
        public_key.verify(signature, payload_bytes)
    except InvalidSignature:
        raise LicenseError("license signature verification failed") from None
    try:
        payload = json.loads(payload_bytes.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise LicenseError("license payload is not valid JSON") from None
    license_obj = License.from_payload(payload)
    if license_obj.is_expired:
        raise LicenseError("license expired")
    if license_obj.device is not None:
        from .fingerprint import device_code

        from .models import normalize_device

        if license_obj.device != normalize_device(device_code()):
            raise LicenseError(
                "license is bound to another computer (device code mismatch)"
            )
    return license_obj
