"""
Provider Registry — zentrale Verwaltung aller Provider.
Neue Provider hier registrieren.
"""
import logging
log = logging.getLogger(__name__)

from .base import BaseProvider, VehicleState

# Graceful imports — fehlende Bibliotheken oder Syntaxfehler in einem Provider
# sollen den Start der gesamten App nicht blockieren.
try:
    from .ha_provider import HomeAssistantProvider
except Exception as _e:
    log.error("ha_provider nicht geladen: %s", _e); HomeAssistantProvider = None  # type: ignore[assignment,misc]

try:
    from .vw_provider import VWProvider
except Exception as _e:
    log.error("vw_provider nicht geladen: %s", _e); VWProvider = None  # type: ignore[assignment,misc]

try:
    from .tesla_provider import TeslaProvider
except Exception as _e:
    log.error("tesla_provider nicht geladen: %s", _e); TeslaProvider = None  # type: ignore[assignment,misc]

try:
    from .other_providers import VolvoProvider, BMWProvider, MercedesProvider
except Exception as _e:
    log.error("other_providers nicht geladen: %s", _e)
    VolvoProvider = BMWProvider = MercedesProvider = None  # type: ignore[assignment,misc]

try:
    from .new_providers import HyundaiKiaProvider, RenaultProvider, PolestarProvider, AudiProvider
except Exception as _e:
    log.error("new_providers nicht geladen: %s", _e)
    HyundaiKiaProvider = RenaultProvider = PolestarProvider = AudiProvider = None  # type: ignore[assignment,misc]

try:
    from .extended_providers import (
        StellantisProvider, FordProvider, MGSAICProvider,
        ToyotaLexusProvider, NissanProvider, PorscheProvider,
        JLRProvider, XPengProvider, BYDProvider,
        TronityProvider, EnodeProvider, SmartcarProvider,
    )
except Exception as _e:
    log.error("extended_providers nicht geladen: %s", _e)
    StellantisProvider = FordProvider = MGSAICProvider = None  # type: ignore[assignment,misc]
    ToyotaLexusProvider = NissanProvider = PorscheProvider = None  # type: ignore[assignment,misc]
    JLRProvider = XPengProvider = BYDProvider = None  # type: ignore[assignment,misc]
    TronityProvider = EnodeProvider = SmartcarProvider = None  # type: ignore[assignment,misc]

# Alle verfügbaren Provider — None-Einträge (fehlgeschlagene Imports) werden gefiltert
PROVIDERS: dict[str, type[BaseProvider]] = {k: v for k, v in {
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
    "stellantis":  StellantisProvider,
    "ford":        FordProvider,
    "mg_saic":     MGSAICProvider,
    "toyota":      ToyotaLexusProvider,
    "nissan":      NissanProvider,
    "porsche":     PorscheProvider,
    "jlr":         JLRProvider,
    "xpeng":       XPengProvider,
    "byd":         BYDProvider,
    "tronity":     TronityProvider,
    "enode":       EnodeProvider,
    "smartcar":    SmartcarProvider,
}.items() if v is not None}


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
