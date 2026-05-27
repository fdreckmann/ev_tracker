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
            self._store_entity_debug(entity_id, reachable=False, error="ha_url nicht konfiguriert")
            return None
        if not token:
            self._store_entity_debug(entity_id, reachable=False, error="ha_token nicht konfiguriert")
            return None
        try:
            r = requests.get(f"{url}/api/states/{entity_id}",
                             headers=self._headers(), timeout=10)
            if r.status_code == 401:
                self._store_entity_debug(entity_id, http_status=401, reachable=True, error="Token ungültig oder keine Berechtigung")
                return None
            if r.status_code == 403:
                self._store_entity_debug(entity_id, http_status=403, reachable=True, error="Token ungültig oder keine Berechtigung")
                return None
            if r.status_code == 404:
                self._store_entity_debug(entity_id, http_status=404, reachable=True, error="Entity nicht gefunden")
                return None
            r.raise_for_status()
            data = r.json()
            state = data.get("state","")
            unit  = data.get("attributes",{}).get("unit_of_measurement","")
            unavailable = state.lower() in ("unavailable","unknown")
            self._store_entity_debug(entity_id, http_status=200, reachable=True,
                                     state=state, unit=unit,
                                     error="Sensor unavailable" if unavailable else None)
            return data
        except requests.exceptions.Timeout:
            self._store_entity_debug(entity_id, reachable=False, error="Timeout")
            return None
        except requests.exceptions.ConnectionError as e:
            self._store_entity_debug(entity_id, reachable=False, error=f"Verbindung fehlgeschlagen: {e}")
            return None
        except Exception as e:
            self._store_entity_debug(entity_id, reachable=False, error=str(e))
            return None

    def _store_entity_debug(self, entity_id: str, *, reachable: bool = True,
                            http_status: int | None = None, state: str | None = None,
                            unit: str | None = None, error: str | None = None):
        if not hasattr(self, "_entity_debug"):
            self._entity_debug = {}
        self._entity_debug[entity_id] = {
            "entity_id": entity_id,
            "reachable": reachable,
            "http_status": http_status,
            "state": state,
            "unit": unit,
            "error": error,
        }

    _UNAVAILABLE_STATES = {"unknown", "unavailable", "none", ""}

    def _float(self, entity_id: str) -> float | None:
        data = self._get_entity(entity_id)
        if not data: return None
        raw = str(data["state"]).strip()
        if raw.lower() in self._UNAVAILABLE_STATES: return None
        try: return float(raw.replace(",", "."))
        except: return None

    _CHARGING_STATES = {
        "charging", "charging_active", "connected_charging", "active_charging",
        "laden", "ladend", "true", "on", "1", "conserving", "lading", "opladen",
    }
    _CONNECTED_ONLY_STATES = {
        "connected", "plugged_in", "plugged", "cable_connected",
    }
    _NOT_CHARGING_STATES = {
        "ready", "idle", "not_charging", "complete", "completed", "finished",
        "off", "false", "0", "unavailable", "unknown", "disconnected",
    }

    def _bool_charging(self, entity_id: str) -> bool | None:
        data = self._get_entity(entity_id)
        if not data: return None
        s = data["state"].lower()
        if s in self._CONNECTED_ONLY_STATES:
            return bool(self.config.get("ha_connected_means_charging", False))
        return s in self._CHARGING_STATES

    @staticmethod
    def _normalize_location(state: str) -> str:
        """Normalize location state to canonical 'home' or 'extern'."""
        from core.location import normalize_location
        norm = normalize_location(state)
        if norm != "unknown":
            return norm
        return state.lower().strip()  # caller checks against home_states list

    def _location(self) -> str:
        sensor = self.config.get("location_sensor","").strip()
        if not sensor: return "unknown"
        data = self._get_entity(sensor)
        if not data: return "unknown"
        raw = data["state"].lower().strip()
        from core.location import normalize_location, _SKIP_VALUES
        canonical = normalize_location(raw)
        if canonical == "home":
            return "home"
        if canonical == "extern":
            return "extern"
        # Check user-defined home_states first
        home_states = [s.strip().lower() for s in self.config.get("home_states","home").split(",")]
        if raw in home_states:
            return "home"
        # device_tracker zones that are not in home_states and not skip values → extern
        # (e.g. "not_home", "work", "parking", "office" all mean the car is away)
        if raw not in _SKIP_VALUES:
            return "extern"
        return "unknown"

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
        debug = {}
        try:
            url   = self.config.get("ha_url","").rstrip("/")
            token = self.config.get("ha_token","")
            if not url or not token:
                err = "HA URL oder Token nicht konfiguriert"
                setattr(self, "_last_debug", {"ha_reachable": False, "error": err})
                return VehicleState(error=err)

            # charging sensor — required for useful data
            chg_id = self.config.get("charging_sensor","")
            charging = None
            if chg_id:
                chg_data = self._get_entity(chg_id)
                if chg_data:
                    s = chg_data["state"].lower()
                    if s in self._CONNECTED_ONLY_STATES:
                        charging = bool(self.config.get("ha_connected_means_charging", False))
                    else:
                        charging = s in self._CHARGING_STATES
                    debug["charging_sensor"] = {"entity_id": chg_id, "ok": True, "state": chg_data["state"], "resolved_charging": charging}
                else:
                    debug["charging_sensor"] = {"entity_id": chg_id, "ok": False, "error": "Nicht gefunden oder nicht erreichbar"}
            else:
                debug["charging_sensor"] = {"entity_id": "", "ok": False, "error": "Nicht konfiguriert"}

            # soc sensor
            soc_id = self.config.get("soc_sensor","")
            soc = None
            if soc_id:
                soc = self._float(soc_id)
                debug["soc_sensor"] = {"entity_id": soc_id, "ok": soc is not None, "state": str(soc) if soc is not None else None}
            else:
                debug["soc_sensor"] = {"entity_id": "", "ok": False, "error": "Nicht konfiguriert"}

            # odo sensor
            odo_id = self.config.get("odo_sensor","")
            odo = None
            if odo_id:
                odo = self._float(odo_id)
                debug["odo_sensor"] = {"entity_id": odo_id, "ok": odo is not None}

            # power sensor
            pwr_entity = (self.config.get("charge_speed_sensor","").strip() or
                          self.config.get("power_sensor","").strip())
            power_kw = None
            if pwr_entity:
                power_kw = self._float(pwr_entity)
                debug["power_sensor"] = {"entity_id": pwr_entity, "ok": power_kw is not None}

            # location
            location = self._location()
            debug["location_sensor"] = {"entity_id": self.config.get("location_sensor",""), "ok": location != "unknown"}

            chg_type = self._charge_type(power_kw)

            # vehicle image URL from optional image entity
            image_url = None
            image_source = None
            img_entity_id = self.config.get("vehicle_image_entity", "").strip()
            if img_entity_id:
                img_data = self._get_entity(img_entity_id)
                if img_data:
                    attrs = img_data.get("attributes", {})
                    raw_url = (
                        attrs.get("entity_picture")
                        or attrs.get("entity_picture_local")
                        or attrs.get("picture")
                        or attrs.get("image")
                        or attrs.get("url")
                    )
                    if not raw_url:
                        state_val = img_data.get("state", "")
                        if isinstance(state_val, str) and state_val.startswith("http"):
                            raw_url = state_val
                    if raw_url and isinstance(raw_url, str):
                        if raw_url.startswith("/"):
                            ha_base = self.config.get("ha_url", "").rstrip("/")
                            raw_url = ha_base + raw_url
                        if raw_url.startswith("http"):
                            image_url = raw_url
                            image_source = "ha"

            debug["ha_reachable"] = True
            debug["token_valid"] = True
            setattr(self, "_last_debug", debug)

            return VehicleState(
                charging=charging, soc=soc, odometer=odo,
                charge_power=power_kw, location=location, charge_type=chg_type,
                image_url=image_url, image_source=image_source,
            )
        except Exception as e:
            setattr(self, "_last_debug", {"ha_reachable": False, "error": str(e)})
            return VehicleState(error=str(e))

    def get_debug(self) -> dict:
        d = dict(getattr(self, "_last_debug", {}))
        if hasattr(self, "_entity_debug"):
            d["entities"] = dict(self._entity_debug)
        return d

    def test_connection(self) -> dict:
        url   = self.config.get("ha_url","").rstrip("/")
        token = self.config.get("ha_token","")
        if not url:
            return {"ok": False, "message": "❌ Keine Home Assistant URL konfiguriert"}
        if not token:
            return {"ok": False, "message": "❌ Kein Access Token konfiguriert"}

        # ── Basic reachability check ─────────────────────────────────────────
        try:
            r = requests.get(f"{url}/api/", headers=self._headers(), timeout=10)
            if r.status_code == 401:
                return {"ok": False, "message": "❌ Authentifizierung fehlgeschlagen (401) — Token prüfen"}
            if r.status_code == 403:
                return {"ok": False, "message": "❌ Zugriff verweigert (403) — Token-Berechtigungen prüfen"}
            if not r.ok:
                return {"ok": False, "message": f"❌ HA antwortet mit HTTP {r.status_code}"}
        except Exception as e:
            return {"ok": False, "message": f"❌ Verbindung zu {url} fehlgeschlagen: {e}"}

        chg_sensor = self.config.get("charging_sensor", "").strip()
        soc_sensor = self.config.get("soc_sensor", "").strip()
        parts: list[str] = []
        overall_ok = True

        # ── Charging sensor (primary) ─────────────────────────────────────────
        if chg_sensor:
            data = self._get_entity(chg_sensor)
            if data:
                state = data.get("state", "?")
                name  = data.get("attributes", {}).get("friendly_name", chg_sensor)
                parts.append(f"✅ Ladestatus: {name} = '{state}'")
            else:
                parts.append(f"⚠ Ladestatus-Sensor '{chg_sensor}' nicht gefunden")
                overall_ok = False
        elif soc_sensor:
            # No charging sensor but SOC sensor set — test SOC as fallback
            soc = self._float(soc_sensor)
            if soc is not None:
                parts.append(f"✅ SOC-Sensor: {soc}% (kein Ladestatus-Sensor konfiguriert)")
            else:
                parts.append(f"⚠ SOC-Sensor '{soc_sensor}' nicht gefunden — Entity-ID prüfen")
                overall_ok = False
        else:
            parts.append("⚠ Kein Ladestatus-Sensor und kein SOC-Sensor konfiguriert")

        # ── SOC sensor ────────────────────────────────────────────────────────
        if soc_sensor and chg_sensor:
            soc = self._float(soc_sensor)
            parts.append(f"✅ SOC: {soc}%" if soc is not None else f"⚠ SOC-Sensor '{soc_sensor}' nicht gefunden")

        # ── Optional sensors ──────────────────────────────────────────────────
        if self.config.get("location_sensor"):
            loc = self._get_entity(self.config["location_sensor"])
            parts.append(f"✅ Standort: '{loc['state']}'" if loc else f"⚠ Standort-Sensor '{self.config['location_sensor']}' nicht gefunden")

        pwr_entity = (self.config.get("charge_speed_sensor", "") or self.config.get("power_sensor", "")).strip()
        if pwr_entity:
            pwr = self._get_entity(pwr_entity)
            parts.append(f"✅ Leistung: {pwr['state']} kW" if pwr else f"⚠ Leistungs-Sensor '{pwr_entity}' nicht gefunden")

        prefix = "✅ HA verbunden" if overall_ok else "⚠ HA verbunden, Sensor-Fehler"
        return {"ok": overall_ok, "message": f"{prefix} · " + " · ".join(parts),
                "sensors": parts}

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
            {"id":"dc_threshold_kw",           "label":"DC-Schwellwert (kW)",                  "type":"number",   "placeholder":"22",    "required":False,
             "hint":"Ladeleistung ab der DC erkannt wird (Standard: 22 kW)"},
            {"id":"ha_connected_means_charging","label":"'connected'/'plugged_in' = Laden",     "type":"checkbox", "required":False,
             "hint":"Standard: nein — angesteckt gilt nicht automatisch als Ladevorgang"},
            {"id":"vehicle_image_entity",       "label":"Fahrzeugbild Entity (optional)",     "type":"text",     "placeholder":"image.mein_auto", "required":False,
             "hint":"Entity-ID einer HA image- oder camera-Entity — Bild wird automatisch übernommen"},
        ]
