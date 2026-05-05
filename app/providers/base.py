"""
EV Tracker — Provider Interface

Jeder Provider implementiert diese Basisklasse.
Nicht verfügbare Features geben None zurück und werden
in der UI als "nicht verfügbar" markiert.
"""
from dataclasses import dataclass, field
from typing import Optional
import logging

log = logging.getLogger(__name__)


@dataclass
class ProviderCapabilities:
    """Beschreibt was ein Provider liefern kann."""
    charging_state: bool = False   # Pflicht: lädt gerade?
    soc:            bool = False   # Pflicht: State of Charge %
    odometer:       bool = False   # Optional: Kilometerstand
    charge_power:   bool = False   # Optional: Ladeleistung kW
    location:       bool = False   # Optional: Standort home/extern
    charge_type:    bool = False   # Optional: AC/DC direkt
    notes:          list = field(default_factory=list)  # Hinweise zur Einschränkung


@dataclass
class VehicleState:
    """Aktueller Fahrzeugzustand — None = nicht verfügbar."""
    charging:     Optional[bool]  = None
    soc:          Optional[float] = None
    odometer:     Optional[float] = None
    charge_power: Optional[float] = None
    location:     Optional[str]   = None   # "home" | "extern" | "unknown"
    charge_type:  Optional[str]   = None   # "ac" | "dc" | "unknown"
    error:        Optional[str]   = None   # Fehlermeldung wenn Abruf fehlschlägt


class BaseProvider:
    """Basisklasse für alle EV-Datenprovider."""

    PROVIDER_ID   = "base"
    PROVIDER_NAME = "Base Provider"
    CAPABILITIES  = ProviderCapabilities()

    def __init__(self, config: dict):
        self.config = config

    def get_state(self) -> VehicleState:
        """Aktuellen Fahrzeugzustand abrufen."""
        raise NotImplementedError

    def test_connection(self) -> dict:
        """Verbindung testen — gibt {"ok": bool, "message": str} zurück."""
        raise NotImplementedError

    @classmethod
    def get_config_fields(cls) -> list[dict]:
        """
        Gibt die Konfigurationsfelder zurück die dieser Provider benötigt.
        Format: [{"id": "field_id", "label": "Label", "type": "text|password|select",
                  "placeholder": "...", "hint": "...", "required": True}]
        """
        return []

    @classmethod
    def capability_summary(cls) -> dict:
        """Zusammenfassung der Fähigkeiten für die UI."""
        cap = cls.CAPABILITIES
        return {
            "provider_id":    cls.PROVIDER_ID,
            "provider_name":  cls.PROVIDER_NAME,
            "charging_state": cap.charging_state,
            "soc":            cap.soc,
            "odometer":       cap.odometer,
            "charge_power":   cap.charge_power,
            "location":       cap.location,
            "charge_type":    cap.charge_type,
            "notes":          cap.notes,
        }
