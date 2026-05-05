"""
VW / Audi / Skoda / Seat — WeConnect ID Direct API Provider
Nutzt die inoffizielle WeConnect ID API (gleiche Plattform für alle VAG-Marken).
Bibliothek: weconnect (pip install weconnect)
"""
import logging
from .base import BaseProvider, ProviderCapabilities, VehicleState

log = logging.getLogger(__name__)

try:
    from weconnect import weconnect
    from weconnect.elements.vehicle import Vehicle
    HAS_WECONNECT = True
except ImportError:
    HAS_WECONNECT = False


class VWProvider(BaseProvider):

    PROVIDER_ID   = "vw"
    PROVIDER_NAME = "VW / Audi / Skoda / Seat (WeConnect ID)"
    CAPABILITIES  = ProviderCapabilities(
        charging_state = True,
        soc            = True,
        odometer       = True,
        charge_power   = True,
        location       = True,
        charge_type    = False,
        notes          = [
            "AC/DC Erkennung nicht direkt verfügbar — wird via Leistungsschwelle berechnet",
            "Standort liefert nur home/nicht-home — keine genaue Position",
            "API ist inoffiziell — kann sich ohne Vorwarnung ändern",
        ]
    )

    def __init__(self, config: dict):
        super().__init__(config)
        self._wc = None

    def _get_client(self):
        if not HAS_WECONNECT:
            raise RuntimeError("weconnect Bibliothek nicht installiert — pip install weconnect")
        if self._wc is None:
            self._wc = weconnect.WeConnect(
                username=self.config["vw_username"],
                password=self.config["vw_password"],
                updateAfterLogin=False,
                loginOnError=True,
            )
            self._wc.login()
        return self._wc

    def _get_vehicle(self):
        wc  = self._get_client()
        wc.update()
        vin = self.config.get("vw_vin","").strip().upper()
        for vehicle in wc.vehicles.values():
            if not vin or vehicle.vin.value == vin:
                return vehicle
        raise RuntimeError(f"Fahrzeug nicht gefunden (VIN: {vin or 'beliebig'})")

    def get_state(self) -> VehicleState:
        if not HAS_WECONNECT:
            return VehicleState(error="weconnect nicht installiert")
        try:
            v = self._get_vehicle()

            # Charging state
            charging = False
            soc      = None
            power_kw = None
            try:
                cs = v.domains["charging"]["chargingStatus"]
                charging = str(cs.chargingState.value).lower() in ("charging","conserving")
                soc      = float(cs.currentSOC_pct.value) if cs.currentSOC_pct else None
                power_kw = float(cs.chargePower_kW.value) if hasattr(cs,"chargePower_kW") and cs.chargePower_kW else None
            except (KeyError, AttributeError): pass

            # Odometer
            odo = None
            try:
                odo = float(v.domains["measurements"]["odometerStatus"].odometer.value)
            except (KeyError, AttributeError): pass

            # Location (parking position — home if close to home coords)
            location = "unknown"
            try:
                home_lat = float(self.config.get("home_lat", 0))
                home_lon = float(self.config.get("home_lon", 0))
                if home_lat and home_lon:
                    pos  = v.domains["parking"]["parkingPosition"]
                    vlat = float(pos.lat.value); vlon = float(pos.lon.value)
                    dist = ((vlat-home_lat)**2 + (vlon-home_lon)**2)**0.5 * 111000  # approx meters
                    location = "home" if dist < float(self.config.get("home_radius_m",200)) else "extern"
            except (KeyError, AttributeError): pass

            # AC/DC via power threshold
            chg_type = "unknown"
            if power_kw is not None:
                chg_type = "dc" if power_kw > float(self.config.get("dc_threshold_kw",22.0)) else "ac"

            return VehicleState(
                charging=charging, soc=soc, odometer=odo,
                charge_power=power_kw, location=location, charge_type=chg_type
            )
        except Exception as e:
            log.warning("VW Provider error: %s", e)
            self._wc = None  # reset on error
            return VehicleState(error=str(e))

    def test_connection(self) -> dict:
        if not HAS_WECONNECT:
            return {"ok": False, "message": "weconnect Bibliothek fehlt — requirements.txt prüfen"}
        try:
            v = self._get_vehicle()
            return {"ok": True, "message": f"✅ Verbunden · Fahrzeug: {v.nickname.value or v.vin.value}"}
        except Exception as e:
            return {"ok": False, "message": f"❌ {e}"}

    @classmethod
    def get_config_fields(cls) -> list[dict]:
        return [
            {"id":"vw_username",    "label":"WeConnect ID Email",       "type":"text",     "placeholder":"email@example.com", "required":True},
            {"id":"vw_password",    "label":"WeConnect ID Passwort",    "type":"password", "placeholder":"",                  "required":True},
            {"id":"vw_vin",         "label":"Fahrzeug VIN (optional)",  "type":"text",     "placeholder":"WVWZZZE1ZME000000","required":False,
             "hint":"Leer lassen wenn nur ein Fahrzeug im Account"},
            {"id":"home_lat",       "label":"Heimat Breitengrad",       "type":"text",     "placeholder":"51.5074",           "required":False,
             "hint":"Für Standort-Erkennung (home/extern)"},
            {"id":"home_lon",       "label":"Heimat Längengrad",        "type":"text",     "placeholder":"7.4653",            "required":False},
            {"id":"home_radius_m",  "label":"Heimat-Radius (Meter)",    "type":"number",   "placeholder":"200",               "required":False},
            {"id":"dc_threshold_kw","label":"DC-Schwellwert (kW)",      "type":"number",   "placeholder":"22",                "required":False,
             "hint":"Ladeleistung ab der DC erkannt wird"},
        ]
