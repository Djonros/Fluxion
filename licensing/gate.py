"""Feature gating bound to the local activation state."""
from __future__ import annotations

from .models import FREE, License, LicenseError
from .store import load_activation


class ProRequiredError(LicenseError):
    """Raised when a Pro feature is used without an active Pro license."""

    def __init__(self, feature: str) -> None:
        self.feature = feature
        super().__init__(
            f"'{feature}' requires a Pro license. "
            "Activate one with: /license activate <key>"
        )


def current_license() -> License:
    """The locally activated license, or a synthetic free license."""
    activated = load_activation()
    if activated is not None:
        return activated
    return License(email="free@local", plan=FREE)


def feature_enabled(feature: str) -> bool:
    return current_license().feature_enabled(feature)


def ensure_pro(feature: str) -> None:
    if not feature_enabled(feature):
        raise ProRequiredError(feature)
