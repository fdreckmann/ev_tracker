"""
Provider Registry — zentrale Verwaltung aller Provider.
Neue Provider hier registrieren.
"""
from .base import BaseProvider, VehicleState
from .ha_provider import HomeAssistantProvider
from .vw_provider import VWProvider
from .tesla_provider import TeslaProvider
from .other_providers import VolvoProvider, BMWProvider, MercedesProvider
from .new_providers import HyundaiKiaProvider, RenaultProvider, PolestarProvider, AudiProvider
from .extended_providers import (
    StellantisProvider, FordProvider, MGSAICProvider,
    ToyotaLexusProvider, NissanProvider, PorscheProvider,
    JLRProvider, XPengProvider, BYDProvider,
    TronityProvider, EnodeProvider, SmartcarProvider,
)

# Alle verfügbaren Provider
PROVIDERS: dict[str, type[BaseProvider]] = {
    "ha":          HomeAssistantProvider,
    "vw":          VWProvider,
    "tesla":       TeslaProvider,
    "volvo":       VolvoProvider,
    "bmw":         BMWProvider,
    "mercedes":    MercedesProvider,
    "hyundai_kia": HyundaiKiaProvider,
    "renault":     RenaultProvider,
    "polestar":    PolestarProvider,
    "audi":        AudiProvider,
    "stellantis": StellantisProvider,
    "ford":       FordProvider,
    "mg_saic":    MGSAICProvider,
    "toyota":     ToyotaLexusProvider,
    "nissan":     NissanProvider,
    "porsche":    PorscheProvider,
    "jlr":        JLRProvider,
    "xpeng":      XPengProvider,
    "byd":        BYDProvider,
    "tronity":    TronityProvider,
    "enode":      EnodeProvider,
    "smartcar":   SmartcarProvider,
}


def get_provider(provider_id: str, config: dict) -> BaseProvider:
    """Provider-Instanz für gegebene ID erstellen."""
    cls = PROVIDERS.get(provider_id)
    if not cls:
        raise ValueError(f"Unbekannter Provider: {provider_id}")
    return cls(config)


def get_all_capabilities() -> list[dict]:
    """Fähigkeiten aller Provider für die UI."""
    return [cls.capability_summary() for cls in PROVIDERS.values()]


def get_config_fields(provider_id: str) -> list[dict]:
    """Konfigurationsfelder für einen Provider."""
    cls = PROVIDERS.get(provider_id)
    if not cls:
        return []
    return cls.get_config_fields()
