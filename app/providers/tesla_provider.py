"""
Tesla Direct API Provider
Nutzt die inoffizielle Tesla Fleet API via tesla-api Bibliothek.
"""
import logging
from .base import BaseProvider, ProviderCapabilities, VehicleState

log = logging.getLogger(__name__)

try:
    import teslapy
    HAS_TESLA = True
except ImportError:
    HAS_TESLA = False


class TeslaProvider(BaseProvider):

    PROVIDER_ID   = "tesla"
    PROVIDER_NAME = "Tesla"
    CAPABILITIES  = ProviderCapabilities(
        charging_state = True,
        soc            = True,
        odometer       = True,
        charge_power   = True,
        location       = True,
        charge_type    = True,
        notes          = ["Beste API-Abdeckung · Alle Features verfügbar"]
    )

    def __init__(self, config: dict):
        super().__init__(config)
        self._tesla = None
        self._vehicle = None

    def _get_vehicle(self):
        if not HAS_TESLA:
            raise RuntimeError("teslapy nicht installiert")
        if self._tesla is None:
            self._tesla = teslapy.Tesla(self.config["tesla_email"])
            if not self._tesla.authorized:
                raise RuntimeError("Tesla nicht autorisiert — Token fehlt. Bitte einmalig per CLI authorisieren.")
        vehicles = self._tesla.vehicle_list()
        if not vehicles:
            raise RuntimeError("Kein Fahrzeug im Tesla Account gefunden")
        vin = self.config.get("tesla_vin","").strip()
        if vin:
            for v in vehicles:
                if v["vin"] == vin: return v
        return vehicles[0]

    def get_state(self) -> VehicleState:
        if not HAS_TESLA:
            return VehicleState(error="teslapy nicht installiert")
        try:
            v = self._get_vehicle()
            try:
                v.sync_wake_up()
            except: pass
            data = v.get_vehicle_data()

            charge = data.get("charge_state", {})
            drive  = data.get("drive_state", {})
            vehicle= data.get("vehicle_state", {})

            charging = charge.get("charging_state","").lower() in ("charging","complete")
            soc      = charge.get("battery_level")
            power_kw = charge.get("charger_power")
            odo      = vehicle.get("odometer")  # miles
            if odo: odo = round(odo * 1.60934, 1)  # → km

            # Location
            location = "unknown"
            lat = drive.get("latitude"); lon = drive.get("longitude")
            if lat and lon:
                home_lat = float(self.config.get("home_lat",0))
                home_lon = float(self.config.get("home_lon",0))
                if home_lat and home_lon:
                    dist = ((lat-home_lat)**2 + (lon-home_lon)**2)**0.5 * 111000
                    location = "home" if dist < float(self.config.get("home_radius_m",200)) else "extern"

            # AC/DC
            conn_type = charge.get("conn_charge_cable","")
            if "CCS" in conn_type or "CHAdeMO" in conn_type:
                chg_type = "dc"
            elif power_kw and power_kw > float(self.config.get("dc_threshold_kw",22)):
                chg_type = "dc"
            elif power_kw:
                chg_type = "ac"
            else:
                chg_type = "unknown"

            return VehicleState(
                charging=charging, soc=soc, odometer=odo,
                charge_power=power_kw, location=location, charge_type=chg_type
            )
        except Exception as e:
            log.warning("Tesla error: %s", e)
            self._vehicle = None
            return VehicleState(error=str(e))

    def test_connection(self) -> dict:
        if not HAS_TESLA:
            return {"ok": False, "message": "teslapy nicht installiert"}
        try:
            v = self._get_vehicle()
            return {"ok": True, "message": f"✅ {v.get('display_name', v['vin'])}"}
        except Exception as e:
            return {"ok": False, "message": f"❌ {e}"}

    @classmethod
    def get_config_fields(cls) -> list[dict]:
        return [
            {"id":"tesla_email",    "label":"Tesla Account Email",      "type":"text",   "placeholder":"email@example.com", "required":True,
             "hint":"Einmalige Autorisierung per QR-Code beim ersten Start nötig"},
            {"id":"tesla_vin",      "label":"Fahrzeug VIN (optional)",  "type":"text",   "placeholder":"5YJ3E1EA1JF000000", "required":False,
             "hint":"Leer lassen wenn nur ein Fahrzeug im Account"},
            {"id":"home_lat",       "label":"Heimat Breitengrad",       "type":"text",   "placeholder":"51.5074",           "required":False},
            {"id":"home_lon",       "label":"Heimat Längengrad",        "type":"text",   "placeholder":"7.4653",            "required":False},
            {"id":"home_radius_m",  "label":"Heimat-Radius (Meter)",    "type":"number", "placeholder":"200",               "required":False},
            {"id":"dc_threshold_kw","label":"DC-Schwellwert (kW)",      "type":"number", "placeholder":"22",                "required":False},
        ]
