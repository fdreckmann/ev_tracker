"""
Charging session import service.

Provides a dedup key function and a base profile class for external charge
import providers (LogPay, EnBW, Tesla, Elli, generic CSV).

Dedup key: provider + start_ts + energy_kwh + cost + card_reference
"""
from __future__ import annotations

import hashlib
from abc import ABC, abstractmethod
from typing import Optional


def make_dedup_key(provider: str, start_ts: str, energy_kwh: float,
                   cost: Optional[float] = None,
                   card_reference: Optional[str] = None) -> str:
    """Return a stable SHA-256 dedup key for an imported session."""
    parts = [
        provider.strip().lower(),
        start_ts.strip(),
        f"{energy_kwh:.3f}",
        f"{cost:.2f}" if cost is not None else "",
        (card_reference or "").strip().lower(),
    ]
    raw = "|".join(parts)
    return hashlib.sha256(raw.encode()).hexdigest()


class BaseImportProfile(ABC):
    """Abstract base for charge import profiles."""

    provider_id: str  # e.g. "logpay", "enbw", "tesla", "elli", "csv"

    @abstractmethod
    def parse_row(self, row: dict) -> Optional[dict]:
        """Parse one source row into a normalised session dict.

        Returns None to skip the row (unknown format, header row, etc.).

        Required keys in returned dict:
          start_ts (ISO str), kwh_charged (float)
        Optional keys mirror the sessions table columns.
        """

    def dedup_key(self, session: dict) -> str:
        return make_dedup_key(
            provider=self.provider_id,
            start_ts=session.get("start_ts", ""),
            energy_kwh=float(session.get("kwh_charged", 0)),
            cost=session.get("cost_eur"),
            card_reference=session.get("card_reference"),
        )


# ---------------------------------------------------------------------------
# TODO: provider implementations
# ---------------------------------------------------------------------------

# class LogPayImportProfile(BaseImportProfile):
#     provider_id = "logpay"
#     def parse_row(self, row): ...

# class EnBWImportProfile(BaseImportProfile):
#     provider_id = "enbw"
#     def parse_row(self, row): ...

# class TeslaImportProfile(BaseImportProfile):
#     provider_id = "tesla"
#     def parse_row(self, row): ...

# class ElliImportProfile(BaseImportProfile):
#     provider_id = "elli"
#     def parse_row(self, row): ...

# class GenericCsvImportProfile(BaseImportProfile):
#     provider_id = "csv"
#     def parse_row(self, row): ...
