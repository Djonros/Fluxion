"""Fluxion licensing: offline Ed25519-signed license keys with free/pro plans."""
from typing import Any

from .models import (
    FREE,
    PLANS,
    PRO,
    PRO_FEATURES,
    License,
    LicenseError,
    decode_key,
    encode_key,
    normalize_device,
    parse_datetime,
    validate_key_id,
)
from .release import RELEASE_DATE, UPDATE_PERIOD_DAYS, release_date
from .verifier import PUBLIC_KEY_B64, verify_license
from .store import (
    DEFAULT_LICENSE_PATH,
    activate,
    activate_file,
    activate_text,
    clear_activation,
    extract_key,
    find_key_files,
    load_activation,
)
from .gate import (
    ProRequiredError,
    current_license,
    ensure_pro,
    feature_enabled,
)
from .trial import TRIAL_WRITES, trial_consume, trial_remaining, trial_reset

_ISSUER_EXPORTS = frozenset(
    {"issue_license", "load_private_key", "record_issuance", "DEFAULT_PRIVATE_KEY_PATH"}
)


def __getattr__(name: str) -> Any:
    if name in _ISSUER_EXPORTS:
        from . import issuer

        return getattr(issuer, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = [
    "DEFAULT_LICENSE_PATH",
    "activate_file",
    "activate_text",
    "extract_key",
    "find_key_files",
    "DEFAULT_PRIVATE_KEY_PATH",
    "PUBLIC_KEY_B64",
    "FREE",
    "PLANS",
    "PRO",
    "PRO_FEATURES",
    "License",
    "LicenseError",
    "decode_key",
    "encode_key",
    "normalize_device",
    "parse_datetime",
    "validate_key_id",
    "issue_license",
    "load_private_key",
    "record_issuance",
    "activate",
    "clear_activation",
    "load_activation",
    "verify_license",
    "ProRequiredError",
    "current_license",
    "ensure_pro",
    "feature_enabled",
    "RELEASE_DATE",
    "UPDATE_PERIOD_DAYS",
    "release_date",
    "TRIAL_WRITES",
    "trial_consume",
    "trial_remaining",
    "trial_reset",
]
