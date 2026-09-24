"""License issuance with the owner-only Ed25519 private key."""
from __future__ import annotations

import argparse
import json
import secrets
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from .models import PLANS, PRO, License, LicenseError, encode_key, normalize_device, parse_datetime

DEFAULT_PRIVATE_KEY_PATH = Path(__file__).resolve().parents[1] / "keys" / "private.pem"
DEFAULT_REGISTRY_PATH = Path(__file__).resolve().parents[1] / "keys" / "issued" / "registry.jsonl"


def load_private_key(path: Path = DEFAULT_PRIVATE_KEY_PATH) -> Ed25519PrivateKey:
    try:
        pem = Path(path).read_bytes()
        key = serialization.load_pem_private_key(pem, password=None)
    except (OSError, ValueError, TypeError) as exc:
        raise LicenseError(f"cannot load private key {path}: {exc}") from exc
    if not isinstance(key, Ed25519PrivateKey):
        raise LicenseError(f"private key {path} is not an Ed25519 key")
    return key


def issue_license(
    email: str,
    plan: str = PRO,
    expires_at: datetime | None = None,
    private_key: Ed25519PrivateKey | None = None,
    private_key_path: Path | None = None,
    device: str | None = None,
    key_id: str | None = None,
) -> tuple[License, str]:
    license_obj = License(
        email=email,
        plan=plan,
        expires_at=expires_at,
        issued_at=datetime.now(timezone.utc),
        key_id=key_id or secrets.token_hex(8),
        device=normalize_device(device) if device else None,
    )
    payload_bytes = json.dumps(
        license_obj.to_payload(), separators=(",", ":"), sort_keys=True
    ).encode("utf-8")
    if private_key is None:
        private_key = load_private_key(private_key_path or DEFAULT_PRIVATE_KEY_PATH)
    signature = private_key.sign(payload_bytes)
    return license_obj, encode_key(payload_bytes, signature)


def record_issuance(
    license_obj: License,
    registry_path: Path = DEFAULT_REGISTRY_PATH,
) -> None:
    """Append issued key metadata to the local journal (best effort)."""
    try:
        registry_path.parent.mkdir(parents=True, exist_ok=True)
        entry = {
            "kid": license_obj.key_id,
            "email": license_obj.email,
            "plan": license_obj.plan,
            "expires": license_obj.expires_at.isoformat() if license_obj.expires_at else None,
            "issued_at": license_obj.issued_at.isoformat() if license_obj.issued_at else None,
            "device": license_obj.device,
        }
        with registry_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except OSError:
        pass


def _expiry_from_args(args) -> datetime | None:
    if args.expires:
        return parse_datetime(args.expires)
    days = args.days or (args.months * 30 if args.months else 0) or (args.years * 365 if args.years else 0)
    if days:
        base = datetime.now(timezone.utc)
        return (base + timedelta(days=days)).replace(microsecond=0)
    return None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m licensing.issuer",
        description="Issue a Fluxion license key (owner-only tool)",
    )
    parser.add_argument("--email", required=True)
    parser.add_argument("--plan", choices=PLANS, default=PRO)
    expiry = parser.add_mutually_exclusive_group()
    expiry.add_argument("--days", type=int, default=None, help="validity in days from now")
    expiry.add_argument("--months", type=int, default=None, help="validity in ~30-day months from now")
    expiry.add_argument("--years", type=int, default=None, help="validity in ~365-day years from now")
    expiry.add_argument(
        "--expires",
        default=None,
        help="ISO 8601 expiry timestamp; omit for a perpetual license",
    )
    parser.add_argument(
        "--device",
        default=None,
        help="bind the key to a device code (XXXX-XXXX-XXXX-XXXX); omit for a portable key",
    )
    parser.add_argument("--key-path", type=Path, default=DEFAULT_PRIVATE_KEY_PATH)
    parser.add_argument("--registry", type=Path, default=DEFAULT_REGISTRY_PATH)
    args = parser.parse_args(argv)
    try:
        expires_at = _expiry_from_args(args)
        license_obj, key = issue_license(
            args.email,
            plan=args.plan,
            expires_at=expires_at,
            private_key_path=args.key_path,
            device=args.device,
        )
        record_issuance(license_obj, args.registry)
    except LicenseError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    print(key)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
