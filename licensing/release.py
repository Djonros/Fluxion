"""Release date of this build: the reference point for the update period of a licence.

A Pro licence covers every build released within ``UPDATE_PERIOD_DAYS`` of the
day the key was issued. Those builds keep working forever; a later build asks
for a renewal. The date is bumped together with the version at each release.
"""
from __future__ import annotations

from datetime import datetime, timezone

RELEASE_DATE = "2026-10-08"
UPDATE_PERIOD_DAYS = 365


def release_date() -> datetime:
    """Return the release date of this build as the start of that day in UTC."""
    return datetime.strptime(RELEASE_DATE, "%Y-%m-%d").replace(tzinfo=timezone.utc)
