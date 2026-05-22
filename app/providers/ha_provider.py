"""Home Assistant Provider — via Long-Lived Token + REST API."""
import requests
import logging
from .base import BaseProvider, ProviderCapabilities, VehicleState

log = logging.getLogger(__name__)


class HomeAssistantProvider(BaseProvider):

    PROVIDER_ID   = "ha"
    PROVIDER_NAME = "Home Assistant"
    CAPABILITIES  = ProviderCapabilities(
        charging_state = True,
        soc            = True,
        odometer       = True,
        charge_power   = True,
        location       = True,
        charge_type    = True,
        notes          = ["Alle Funktionen verfügbar je nach konfigurierten Sensoren"]
    )

    def _headers(self):
        return {
            "Authorization": f"Bearer {self.config.get('ha_token','')}",
            "Content-Type":  "application/json",
        }

    def _get_entity(self, entity_id: str) -> dict | None:
        if not entity_id:
            return None
        url = self.config.get("ha_url","").rstrip("/")
        token = self.config.get("ha_token","")
        if not url:
            log.warning("HA: ha_url nicht konfiguriert")
            return None
        if not token:
            log.warning("HA: ha_token nicht konfiguriert")
            return None
        try:
            r = requests.get(f"{url}/api/states/{entity_id}",
                             headers=self._headers(), timeout=10)
            if r.status_code == 401:
                log.warning("HA: Authentifizierung fehlgeschlagen (401) — Token prüfen")
                return None
            if r.status_code == 403:
                log.warning("HA: Zugriff verweigert (403) — Token-Berechtigungen prüfen")
                return None
            r.raise_for_status()
            return r.json()
        except requests.exceptions.ConnectionError as e:
            log.warning("HA: Verbindung zu %s fehlgeschlagen: %s", url, e)
            return None
        except Exception as e:
            log.warning("HA entity %s error: %s", entity_id, e)
            return None

    def _float(self, entity_id: str) -> float | None:
        data = self._get_entity(entity_id)
        if not data: return None
        try: return float(data["state"])
        except: return None

    def _bool_charging(self, entity_id: str) -> bool | None:
        data = self._get_entity(entity_id)
        if not data: return None
        return data["state"].lower() in (
            "charging", "laden", "true", "on", "1", "conserving",
            "plugged_in", "connected", "active", "lading", "opladen",
        )

    @staticmethod
    def _normalize_location(state: str) -> str:
        """Normalize location state to canonical 'home' or 'extern'."""
        s = state.lower().strip()
        extern_states = {"not_home", "away", "extern", "external", "unterwegs", "außer_haus"}
        if s in extern_states:
            return "extern"
        return s  # caller checks against home_states list

    def _location(self) -> str:
        sensor = self.config.get("location_sensor","").strip()
        if not sensor: return "unknown"
        data = self._get_entity(sensor)
        if not data: return "unknown"
        raw         = data["state"].lower().strip()
        normalized  = self._normalize_location(raw)
        home_states = [s.strip().lower() for s in self.config.get("home_states","home").split(",")]
        return "home" if normalized in home_states else "extern"

    def _charge_type(self, power_kw: float | None) -> str:
        # 1. Dedicated type sensor
        type_sensor = self.config.get("charge_type_sensor","").strip()
        if type_sensor:
            data = self._get_entity(type_sensor)
            if data:
                s = data["state"].lower()
                if "dc" in s: return "dc"
                if "ac" in s: return "ac"
        # 2. Power threshold fallback
        if power_kw is None: return "unknown"
        return "dc" if power_kw > float(self.config.get("dc_threshold_kw", 22.0)) else "ac"

    def get_state(self) -> VehicleState:
        try:
            charging = self._bool_charging(self.config.get("charging_sensor",""))
            soc      = self._float(self.config.get("soc_sensor",""))
            odo      = self._float(self.config.get("odo_sensor",""))
            # speed sensor has priority over power sensor
            pwr_entity = self.config.get("charge_speed_sensor","").strip() or \
                         self.config.get("power_sensor","").strip()
            power_kw   = self._float(pwr_entity) if pwr_entity else None
            location   = self._location()
            chg_type   = self._charge_type(power_kw)
            return VehicleState(
                charging=charging, soc=soc, odometer=odo,
                charge_power=power_kw, location=location, charge_type=chg_type
            )
        except Exception as e:
            return VehicleState(error=str(e))

    def test_connection(self) -> dict:
        url   = self.config.get("ha_url","").rstrip("/")
        token = self.config.get("ha_token","")
        if not url:
            return {"ok": False, "message": "❌ Keine Home Assistant URL konfiguriert"}
        if not token:
            return {"ok": False, "message": "❌ Kein Access Token konfiguriert"}

        chg_sensor = self.config.get("charging_sensor","")
        if not chg_sensor:
            # At minimum test basic API connectivity
            try:
                r = requests.get(f"{url}/api/", headers=self._headers(), timeout=10)
                if r.status_code == 401:
                    return {"ok": False, "message": "❌ Authentifizierung fehlgeschlagen (401) — Token prüfen"}
                if r.status_code == 403:
                    return {"ok": False, "message": "❌ Zugriff verweigert (403) — Token-Berechtigungen prüfen"}
                if r.ok:
                    return {"ok": True, "message": f"✅ HA erreichbar unter {url} — kein Ladestatus-Sensor konfiguriert"}
            except Exception as e:
                return {"ok": False, "message": f"❌ Verbindung fehlgeschlagen: {e}"}
            return {"ok": False, "message": "❌ HA nicht erreichbar — URL prüfen"}

        data = self._get_entity(chg_sensor)
        if not data:
            # Give specific error based on what's likely wrong
            try:
                r = requests.get(f"{url}/api/", headers=self._headers(), timeout=10)
                if r.status_code == 401:
                    return {"ok": False, "message": "❌ Authentifizierung fehlgeschlagen (401) — Token prüfen"}
                if r.status_code == 403:
                    return {"ok": False, "message": "❌ Zugriff verweigert (403) — Token-Berechtigungen prüfen"}
                if r.ok:
                    return {"ok": False, "message": f"❌ Sensor '{chg_sensor}' nicht gefunden — Entity-ID prüfen"}
            except Exception as e:
                return {"ok": False, "message": f"❌ Verbindung zu {url} fehlgeschlagen: {e}"}
            return {"ok": False, "message": "❌ Verbindung fehlgeschlagen — URL oder Token prüfen"}

        state = data.get("state","?")
        name  = data.get("attributes",{}).get("friendly_name", chg_sensor)
        parts = [f"✅ {name} · Zustand: '{state}'"]

        if self.config.get("soc_sensor"):
            soc = self._float(self.config["soc_sensor"])
            parts.append(f"SOC: {soc}%" if soc is not None else "⚠ SOC-Sensor nicht gefunden")

        if self.config.get("location_sensor"):
            loc = self._get_entity(self.config["location_sensor"])
            parts.append(f"Standort: '{loc['state']}'" if loc else "⚠ Standort-Sensor nicht gefunden")

        pwr_entity = (self.config.get("charge_speed_sensor","") or self.config.get("power_sensor","")).strip()
        if pwr_entity:
            pwr = self._get_entity(pwr_entity)
            parts.append(f"Leistung: {pwr['state']} kW" if pwr else "⚠ Leistungs-Sensor nicht gefunden")

        return {"ok": True, "message": " · ".join(parts)}

    @classmethod
    def get_config_fields(cls) -> list[dict]:
        return [
            {"id":"ha_url",             "label":"Home Assistant URL",           "type":"text",     "placeholder":"http://192.168.x.x:8123", "required":True},
            {"id":"ha_token",           "label":"Long-Lived Access Token",      "type":"password", "placeholder":"eyJ0eXAi…",              "required":True,
             "hint":"HA → Profil → Langlebige Zugriffstoken"},
            {"id":"charging_sensor",    "label":"Ladestatus Sensor / Switch",   "type":"text",     "placeholder":"sensor.volkswagen_id_id_7_charging_state", "required":True,
             "hint":"state = charging / on wenn Laden aktiv"},
            {"id":"soc_sensor",         "label":"SOC Sensor (%)",               "type":"text",     "placeholder":"sensor.volkswagen_id_id_7_state_of_charge","required":True},
            {"id":"odo_sensor",         "label":"Kilometerstand Sensor",        "type":"text",     "placeholder":"sensor.volkswagen_id_id_7_mileage",         "required":False},
            {"id":"power_sensor",       "label":"Ladeleistung Sensor (kW)",     "type":"text",     "placeholder":"sensor.volkswagen_id_id_7_charge_power",    "required":False},
            {"id":"charge_speed_sensor","label":"Ladegeschwindigkeit Sensor",   "type":"text",     "placeholder":"",                                           "required":False,
             "hint":"Hat Vorrang vor Ladeleistungs-Sensor wenn gesetzt"},
            {"id":"charge_type_sensor", "label":"Ladetyp Sensor (AC/DC)",       "type":"text",     "placeholder":"sensor.volkswagen_id_id_7_charge_type",     "required":False,
             "hint":"state muss 'ac' oder 'dc' enthalten"},
            {"id":"location_sensor",    "label":"Standort Sensor / Device Tracker","type":"text",  "placeholder":"device_tracker.mein_auto_position", "required":False},
            {"id":"home_states",        "label":"'Zuhause' States",             "type":"text",     "placeholder":"home,zuhause",                               "required":False,
             "hint":"Kommagetrennte Werte die als Zuhause gelten"},
            {"id":"dc_threshold_kw",    "label":"DC-Schwellwert (kW)",          "type":"number",   "placeholder":"22",                                         "required":False,
             "hint":"Ladeleistung ab der DC erkannt wird (Standard: 22 kW)"},
        ]
