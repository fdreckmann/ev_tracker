"""
Volvo Cars API Provider — Offizielle API
Benötigt API Key von developer.volvocars.com
"""
import logging
import requests
from .base import BaseProvider, ProviderCapabilities, VehicleState

log = logging.getLogger(__name__)


class VolvoProvider(BaseProvider):

    PROVIDER_ID   = "volvo"
    PROVIDER_NAME = "Volvo"
    CAPABILITIES  = ProviderCapabilities(
        charging_state = True,
        soc            = True,
        odometer       = False,
        charge_power   = False,
        location       = False,
        charge_type    = False,
        notes          = [
            "Offizielle API — stabil und zuverlässig",
            "Kilometerstand nicht verfügbar via API",
            "Ladeleistung nicht verfügbar — AC/DC Erkennung nicht möglich",
            "Standort nicht verfügbar via API",
        ]
    )

    BASE_URL = "https://api.volvocars.com/energy/v1"

    def _headers(self):
        return {
            "Authorization":      f"Bearer {self.config.get('volvo_access_token','')}",
            "vcc-api-key":        self.config.get("volvo_api_key",""),
            "Content-Type":       "application/json",
        }

    def _get_vin(self):
        vin = self.config.get("volvo_vin","").strip()
        if not vin:
            r = requests.get("https://api.volvocars.com/connected-vehicle/v2/vehicles",
                            headers=self._headers(), timeout=10)
            r.raise_for_status()
            vehicles = r.json().get("data",[])
            if not vehicles: raise RuntimeError("Kein Fahrzeug gefunden")
            vin = vehicles[0]["id"]
        return vin

    def get_state(self) -> VehicleState:
        try:
            vin = self._get_vin()
            r   = requests.get(f"{self.BASE_URL}/vehicles/{vin}/recharge-status",
                               headers=self._headers(), timeout=10)
            r.raise_for_status()
            data = r.json().get("data",{})
            soc      = data.get("batteryChargeLevel",{}).get("value")
            charging = data.get("chargingSystemStatus",{}).get("value","").lower() in \
                       ("chargingsystemcharging","charging")
            return VehicleState(charging=charging, soc=soc)
        except Exception as e:
            return VehicleState(error=str(e))

    def test_connection(self) -> dict:
        try:
            vin = self._get_vin()
            return {"ok": True, "message": f"✅ Verbunden · VIN: {vin}"}
        except Exception as e:
            return {"ok": False, "message": f"❌ {e}"}

    @classmethod
    def get_config_fields(cls) -> list[dict]:
        return [
            {"id":"volvo_api_key",      "label":"Volvo API Key",           "type":"password","placeholder":"",               "required":True,
             "hint":"Kostenlos unter developer.volvocars.com registrieren"},
            {"id":"volvo_access_token", "label":"Access Token",            "type":"password","placeholder":"",               "required":True,
             "hint":"OAuth2 Token — siehe Volvo Developer Portal"},
            {"id":"volvo_vin",          "label":"Fahrzeug VIN (optional)", "type":"text",    "placeholder":"YV1XXXXXXXXXXX", "required":False},
        ]


# ─────────────────────────────────────────────────────────────────────────────

class BMWProvider(BaseProvider):

    PROVIDER_ID   = "bmw"
    PROVIDER_NAME = "BMW / Mini"
    CAPABILITIES  = ProviderCapabilities(
        charging_state = True,
        soc            = True,
        odometer       = True,
        charge_power   = False,
        location       = True,
        charge_type    = False,
        notes          = [
            "Inoffizielle API via bimmer-connected Bibliothek",
            "Ladeleistung nicht verfügbar — AC/DC Erkennung nicht möglich",
        ]
    )

    def __init__(self, config: dict):
        super().__init__(config)
        self._account = None

    def _get_account(self):
        try:
            from bimmer_connected.account import MyBMWAccount
            from bimmer_connected.api.regions import get_region_from_name
        except ImportError:
            raise RuntimeError("bimmer-connected nicht installiert")
        if self._account is None:
            region = get_region_from_name(self.config.get("bmw_region","rest_of_world"))
            self._account = MyBMWAccount(
                self.config["bmw_username"],
                self.config["bmw_password"],
                region
            )
            import asyncio
            asyncio.run(self._account.get_vehicles())
        return self._account

    def _get_vehicle(self):
        account = self._get_account()
        vin = self.config.get("bmw_vin","").strip()
        for v in account.vehicles:
            if not vin or v.vin == vin: return v
        raise RuntimeError("Kein BMW gefunden")

    def get_state(self) -> VehicleState:
        try:
            v        = self._get_vehicle()
            charging = v.fuel_and_battery.charging_status.value.lower() in ("charging","plugged_in")
            soc      = v.fuel_and_battery.remaining_battery_percent
            odo      = v.mileage[0] if v.mileage else None

            location = "unknown"
            home_lat = float(self.config.get("home_lat",0))
            home_lon = float(self.config.get("home_lon",0))
            if home_lat and home_lon and v.vehicle_location:
                lat = v.vehicle_location.gps.latitude
                lon = v.vehicle_location.gps.longitude
                dist = ((lat-home_lat)**2+(lon-home_lon)**2)**0.5*111000
                location = "home" if dist < float(self.config.get("home_radius_m",200)) else "extern"

            return VehicleState(charging=charging, soc=soc, odometer=odo, location=location)
        except Exception as e:
            self._account = None
            return VehicleState(error=str(e))

    def test_connection(self) -> dict:
        try:
            v = self._get_vehicle()
            return {"ok": True, "message": f"✅ {v.name} ({v.vin})"}
        except Exception as e:
            return {"ok": False, "message": f"❌ {e}"}

    @classmethod
    def get_config_fields(cls) -> list[dict]:
        return [
            {"id":"bmw_username",   "label":"BMW Account Email",        "type":"text",     "placeholder":"email@example.com","required":True},
            {"id":"bmw_password",   "label":"BMW Account Passwort",     "type":"password", "placeholder":"",                 "required":True},
            {"id":"bmw_vin",        "label":"Fahrzeug VIN (optional)",  "type":"text",     "placeholder":"WBA...",           "required":False},
            {"id":"bmw_region",     "label":"Region",                   "type":"select",   "options":["rest_of_world","china","north_america"], "required":False},
            {"id":"home_lat",       "label":"Heimat Breitengrad",       "type":"text",     "placeholder":"51.5074",          "required":False},
            {"id":"home_lon",       "label":"Heimat Längengrad",        "type":"text",     "placeholder":"7.4653",           "required":False},
            {"id":"home_radius_m",  "label":"Heimat-Radius (Meter)",    "type":"number",   "placeholder":"200",              "required":False},
        ]


# ─────────────────────────────────────────────────────────────────────────────

class MercedesProvider(BaseProvider):

    PROVIDER_ID   = "mercedes"
    PROVIDER_NAME = "Mercedes-Benz"
    CAPABILITIES  = ProviderCapabilities(
        charging_state = True,
        soc            = True,
        odometer       = True,
        charge_power   = False,
        location       = False,
        charge_type    = False,
        notes          = [
            "Offizielle Mercedes API — benötigt Developer Account",
            "Ladeleistung und Standort nicht verfügbar",
            "Kostenloser Developer-Zugang unter developer.mercedes-benz.com",
        ]
    )

    BASE_URL = "https://api.mercedes-benz.com/vehicledata/v2"

    def _headers(self):
        return {"Authorization": f"Bearer {self.config.get('mercedes_token','')}"}

    def _get_vin(self):
        return self.config.get("mercedes_vin","").strip()

    def get_state(self) -> VehicleState:
        vin = self._get_vin()
        if not vin: return VehicleState(error="Keine VIN konfiguriert")
        try:
            r = requests.get(f"{self.BASE_URL}/vehicles/{vin}/resources/soc",
                            headers=self._headers(), timeout=10)
            r.raise_for_status()
            soc_data = r.json()
            soc = float(soc_data[0]["value"]) if soc_data else None

            r2 = requests.get(f"{self.BASE_URL}/vehicles/{vin}/resources/chargingstatus",
                             headers=self._headers(), timeout=10)
            r2.raise_for_status()
            chg_data = r2.json()
            charging = str(chg_data[0]["value"]).lower() in ("charging","1","true") if chg_data else False

            r3 = requests.get(f"{self.BASE_URL}/vehicles/{vin}/resources/odo",
                             headers=self._headers(), timeout=10)
            r3.raise_for_status()
            odo_data = r3.json()
            odo = float(odo_data[0]["value"]) if odo_data else None

            return VehicleState(charging=charging, soc=soc, odometer=odo)
        except Exception as e:
            return VehicleState(error=str(e))

    def test_connection(self) -> dict:
        vin = self._get_vin()
        if not vin: return {"ok": False, "message": "Keine VIN konfiguriert"}
        try:
            r = requests.get(f"{self.BASE_URL}/vehicles/{vin}/resources/soc",
                            headers=self._headers(), timeout=10)
            r.raise_for_status()
            return {"ok": True, "message": f"✅ Verbunden · VIN: {vin}"}
        except Exception as e:
            return {"ok": False, "message": f"❌ {e}"}

    @classmethod
    def get_config_fields(cls) -> list[dict]:
        return [
            {"id":"mercedes_token", "label":"Mercedes API Token",       "type":"password","placeholder":"",              "required":True,
             "hint":"OAuth2 Token von developer.mercedes-benz.com"},
            {"id":"mercedes_vin",   "label":"Fahrzeug VIN",             "type":"text",    "placeholder":"WDB...",        "required":True},
        ]
