"""
Session location resolution helpers.
"""
from __future__ import annotations

_HOME_VALUES  = {"home", "zuhause", "at_home", "home_charging"}
_EXTERN_VALUES = {"extern", "external", "not_home", "away", "unterwegs", "extern_charging"}
_SKIP_VALUES  = {"unknown", "unavailable", "disabled", "none", ""}


def normalize_location(value: str | None) -> str:
    """Map any location string to canonical 'home', 'extern', or 'unknown'."""
    if not value:
        return "unknown"
    v = str(value).strip().lower()
    if v in _HOME_VALUES:
        return "home"
    if v in _EXTERN_VALUES:
        return "extern"
    return "unknown"


def effective_session_location(state_location: str | None,
                               location_status: str | None) -> str:
    """Return the best known location for a session.

    Priority: location_status (UniFi/combined detection) > state_location (provider value).
    Normalizes all inputs — 'external' becomes 'extern'.
    Always returns one of: 'home', 'extern', 'unknown'.
    """
    for candidate in (location_status, state_location):
        norm = normalize_location(candidate)
        if norm != "unknown":
            return norm
    return "unknown"
