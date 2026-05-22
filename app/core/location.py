"""
Session location resolution helpers.
"""
from __future__ import annotations

_VALID = {"home", "extern", "unknown"}


def effective_session_location(state_location: str | None,
                               location_status: str | None) -> str:
    """Return the best known location for a session.

    Priority: location_status (UniFi/combined detection) > state_location (provider value).
    Always returns one of: 'home', 'extern', 'unknown'.
    """
    for candidate in (location_status, state_location):
        if candidate and candidate in _VALID:
            return candidate
    return "unknown"
