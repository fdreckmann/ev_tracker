"""
Extended Provider set for EV Tracker v1.9.7

Priority 1: Stellantis, Ford, MG/SAIC, Toyota/Lexus, Nissan
Priority 2: Porsche, JLR, XPeng (stub), BYD (stub)
Priority 3: Tronity, Enode, Smartcar (aggregators)

All providers deliver VehicleState with: charging, soc, odometer,
charge_power, location, charge_type, error.
"""
import logging
import requests
from .base import BaseProvider, ProviderCapabilities, VehicleState

log = logging.getLogger(__name__)


def _dist_m(lat1, lon1, lat2, lon2) -> float:
    """Approximate distance in meters between two GPS coords."""
    return ((lat1 - lat2) ** 2 + (lon1 - lon2) ** 2) ** 0.5 * 111000


def _home_location(cfg: dict, lat, lon) -> str:
    try:
        home_lat = float(cfg.get("home_lat", 0) or 0)
        home_lon = float(cfg.get("home_lon", 0) or 0)
        radius   = float(cfg.get("home_radius_m", 200) or 200)
        if home_lat and home_lon and lat is not None and lon is not None:
            return "home" if _dist_m(lat, lon, home_lat, home_lon) < radius else "extern"
    except Exception:
        pass
    return "unknown"


# ─────────────────────────────────────────────────────────────────────────────
# STELLANTIS — Peugeot, Opel, Citroën, DS, Vauxhall, Fiat, Jeep
# ─────────────────────────────────────────────────────────────────────────────

class StellantisProvider(BaseProvider):
    """
    Stellantis Connected Vehicle API (formerly PSA/FCA).
    Covers: Peugeot, Opel/Vauxhall, Citroën, DS, Fiat, Jeep.
    Uses the groupe-psa API with OAuth2 client credentials + user token.
    """

    PROVIDER_ID   = "stellantis"
    PROVIDER_NAME = "Stellantis (Peugeot / Opel / Citroën / DS / Fiat)"
    CAPABILITIES  = ProviderCapabilities(
        charging_state  = True,
        soc             = True,
        odometer        = True,
        charge_power    = True,
        location        = True,
        charge_type     = False,
        notes           = [
            "Inoffizielle PSA/Stellantis API",
            "Marken: Peugeot, Opel, Vauxhall, Citroën, DS, Fiat, Jeep",
            "Stabilität: mittel — API kann sich ändern",
        ],
        official_api    = False,
        requires_oauth  = True,
        requires_password = True,
        stability_level = "medium",
        region_support  = "EU",
    )

    # Brand → (realm, client_id, client_secret, app_name)
    BRANDS = {
        "peugeot":   ("clientsB2CPeugeot",  "1uyO0_NNqLCykSqLnWmJiagdpXBW0psv",  "https://idpcvs.peugeot.com"),
        "opel":      ("clientsB2COpel",      "b2J3SJdEjpN93pJfSvU7ZZOL9N0JO2G9",  "https://idpcvs.opel.com"),
        "citroen":   ("clientsB2CCitroen",   "5Bs-Jz1w6r3i7JU3D-q7HxCmqX2K5YB0",  "https://idpcvs.citroen.com"),
        "ds":        ("clientsB2CDS",        "gJFJqSdI9kEo7Q9eX0H1_r1U6m4P7xRf",  "https://idpcvs.dsautomobiles.com"),
        "vauxhall":  ("clientsB2COpel",      "b2J3SJdEjpN93pJfSvU7ZZOL9N0JO2G9",  "https://idpcvs.opel.com"),
        "fiat":      ("clientsB2CFiat",      "L7R3eYCFT7-M4mOoIEm_sHKgcoxFlFXC",  "https://loginprod.fiat.com"),
        "jeep":      ("clientsB2CJeep",      "gJFJqSdI9kEo7Q9eX0H1_r1U6m4P7xRf",  "https://loginprod.jeep.com"),
    }
    API_BASE = "https://api.groupe-psa.com/connectedcar/v4"

    def _get_token(self) -> str:
        brand  = self.config.get("stellantis_brand", "peugeot").lower()
        realm, client_id, idp_url = self.BRANDS.get(brand, self.BRANDS["peugeot"])
        token_url = f"{idp_url}/am/oauth2/access_token"
        r = requests.post(
            token_url,
            data={
                "grant_type": "password",
                "realm":      realm,
                "username":   self.config.get("stellantis_username", ""),
                "password":   self.config.get("stellantis_password", ""),
                "client_id":  client_id,
                "scope":      "openid profile email",
            },
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            timeout=15,
        )
        if r.status_code != 200:
            raise RuntimeError(f"Stellantis Login fehlgeschlagen (HTTP {r.status_code}) — Zugangsdaten prüfen")
        return r.json()["access_token"]

    def _get_vehicle_id(self, token: str) -> str:
        vid = self.config.get("stellantis_vin", "").strip()
        if vid:
            return vid
        brand = self.config.get("stellantis_brand", "peugeot").lower()
        r = requests.get(
            f"{self.API_BASE}/user/vehicles",
            headers={"Authorization": f"Bearer {token}", "x-introspect-realm": self.BRANDS.get(brand, self.BRANDS["peugeot"])[0]},
            timeout=15,
        )
        r.raise_for_status()
        vehicles = r.json().get("_embedded", {}).get("vehicles", [])
        if not vehicles:
            raise RuntimeError("Kein Fahrzeug im Stellantis-Konto gefunden")
        return vehicles[0]["id"]

    def _get_status(self, token: str, vid: str) -> dict:
        brand = self.config.get("stellantis_brand", "peugeot").lower()
        r = requests.get(
            f"{self.API_BASE}/user/vehicles/{vid}/status",
            headers={"Authorization": f"Bearer {token}", "x-introspect-realm": self.BRANDS.get(brand, self.BRANDS["peugeot"])[0]},
            timeout=15,
        )
        r.raise_for_status()
        return r.json()

    def get_state(self) -> VehicleState:
        try:
            token  = self._get_token()
            vid    = self._get_vehicle_id(token)
            status = self._get_status(token, vid)

            energy = status.get("energy", [{}])
            energy_data = energy[0] if energy else {}
            soc      = energy_data.get("level")
            charging_str = energy_data.get("charging", {}).get("status", "")
            charging = charging_str.lower() in ("inprogress", "charging", "in_progress")
            power    = energy_data.get("charging", {}).get("chargingRate")

            # odometer
            odo = None
            try:
                odo = status.get("lastPosition", {}).get("properties", {}).get("odometer", {}).get("mileage")
                if odo:
                    odo = round(float(odo), 1)
            except Exception:
                pass

            # location
            location = "unknown"
            try:
                coords = status.get("lastPosition", {}).get("geometry", {}).get("coordinates", [])
                if len(coords) >= 2:
                    location = _home_location(self.config, coords[1], coords[0])
            except Exception:
                pass

            return VehicleState(charging=charging, soc=soc, odometer=odo,
                                charge_power=power, location=location)
        except Exception as e:
            return VehicleState(error=str(e))

    def test_connection(self) -> dict:
        try:
            token = self._get_token()
            vid   = self._get_vehicle_id(token)
            return {"ok": True, "message": f"✅ Verbunden · ID: {vid}"}
        except Exception as e:
            return {"ok": False, "message": f"❌ {e}"}

    @classmethod
    def get_config_fields(cls) -> list[dict]:
        return [
            {"id":"stellantis_brand",    "label":"Marke",                   "type":"select",
             "options":["peugeot","opel","citroen","ds","vauxhall","fiat","jeep"], "required":True},
            {"id":"stellantis_username", "label":"App-E-Mail",              "type":"text",     "placeholder":"email@example.com", "required":True},
            {"id":"stellantis_password", "label":"App-Passwort",            "type":"password", "placeholder":"",                  "required":True},
            {"id":"stellantis_vin",      "label":"Fahrzeug VIN (optional)", "type":"text",     "placeholder":"VF3...",            "required":False},
            {"id":"home_lat",            "label":"Heimat Breitengrad",      "type":"text",     "placeholder":"51.5074",           "required":False},
            {"id":"home_lon",            "label":"Heimat Längengrad",       "type":"text",     "placeholder":"7.4653",            "required":False},
            {"id":"home_radius_m",       "label":"Heimat-Radius (Meter)",   "type":"number",   "placeholder":"200",               "required":False},
        ]


# ─────────────────────────────────────────────────────────────────────────────
# FORD — FordPass Connect API
# ─────────────────────────────────────────────────────────────────────────────

class FordProvider(BaseProvider):
    """
    FordPass Connect (FordPass / Ford Remote Connect API).
    For: Mustang Mach-E, F-150 Lightning, Ford Focus EV, Kuga PHEV, Puma PHEV.
    """

    PROVIDER_ID   = "ford"
    PROVIDER_NAME = "Ford (FordPass Connect)"
    CAPABILITIES  = ProviderCapabilities(
        charging_state  = True,
        soc             = True,
        odometer        = True,
        charge_power    = False,
        location        = True,
        charge_type     = False,
        notes           = [
            "FordPass Connect — inoffizielle API",
            "Für: Mustang Mach-E, F-150 Lightning, Puma/Kuga PHEV",
            "Ladeleistung nicht direkt verfügbar",
        ],
        official_api    = False,
        requires_oauth  = False,
        requires_password = True,
        stability_level = "medium",
        region_support  = "global",
    )

    AUTH_URL = "https://fcis.ice.ibmdomain.com/v2.0/endpoint/default/token"
    API_URL  = "https://usapi.cv.ford.com/api"

    def _get_token(self) -> str:
        r = requests.post(
            self.AUTH_URL,
            data={
                "grant_type": "password",
                "client_id":  "9fb503e0-715b-47e8-adfd-ad4b7770f73b",
                "username":   self.config.get("ford_username", ""),
                "password":   self.config.get("ford_password", ""),
            },
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            timeout=15,
        )
        if r.status_code != 200:
            raise RuntimeError(f"Ford Login fehlgeschlagen (HTTP {r.status_code})")
        return r.json().get("access_token", "")

    def _get_vin(self) -> str:
        vin = self.config.get("ford_vin", "").strip()
        if not vin:
            raise RuntimeError("Bitte Ford VIN in der Konfiguration eintragen")
        return vin

    def _headers(self, token: str) -> dict:
        return {
            "Authorization":   f"Bearer {token}",
            "Application-Id":  "71A3AD0A-CF46-4CCF-B473-FC7FE5BC4592",
            "Content-Type":    "application/json",
        }

    def get_state(self) -> VehicleState:
        try:
            token = self._get_token()
            vin   = self._get_vin()
            hdrs  = self._headers(token)

            r = requests.get(f"{self.API_URL}/vehicles/v4/{vin}/status", headers=hdrs, timeout=15)
            r.raise_for_status()
            data = r.json().get("vehiclestatus", {})

            # EV / PHEV battery
            soc      = None
            charging = False
            for key in ["batteryFillLevel", "xevBatteryStateOfCharge"]:
                if key in data and data[key] is not None:
                    val = data[key].get("value") if isinstance(data[key], dict) else data[key]
                    if val is not None:
                        soc = float(val)
                        break
            chg_status = data.get("chargingStatus", {})
            if isinstance(chg_status, dict):
                chg_val = chg_status.get("value", "")
                charging = str(chg_val).upper() in ("CHARGING", "CHRGNG", "IN_PROGRESS")

            # odometer
            odo = None
            odo_raw = data.get("odometer", {})
            if isinstance(odo_raw, dict):
                odo = odo_raw.get("value")

            # location
            location = "unknown"
            try:
                loc = data.get("gps", {})
                if isinstance(loc, dict):
                    lat = loc.get("latitude") or loc.get("lat")
                    lon = loc.get("longitude") or loc.get("lon")
                    location = _home_location(self.config, lat, lon)
            except Exception:
                pass

            return VehicleState(charging=charging, soc=soc, odometer=odo, location=location)
        except Exception as e:
            return VehicleState(error=str(e))

    def test_connection(self) -> dict:
        try:
            token = self._get_token()
            vin   = self._get_vin()
            hdrs  = self._headers(token)
            r = requests.get(f"{self.API_URL}/vehicles/v4/{vin}/status", headers=hdrs, timeout=15)
            r.raise_for_status()
            return {"ok": True, "message": f"✅ Verbunden · VIN: {vin}"}
        except Exception as e:
            return {"ok": False, "message": f"❌ {e}"}

    @classmethod
    def get_config_fields(cls) -> list[dict]:
        return [
            {"id":"ford_username", "label":"FordPass E-Mail",          "type":"text",     "placeholder":"email@example.com", "required":True},
            {"id":"ford_password", "label":"FordPass Passwort",         "type":"password", "placeholder":"",                  "required":True},
            {"id":"ford_vin",      "label":"Fahrzeug VIN",              "type":"text",     "placeholder":"WF0...",            "required":True,
             "hint":"VIN in der FordPass App unter Fahrzeuginformationen"},
            {"id":"home_lat",      "label":"Heimat Breitengrad",        "type":"text",     "placeholder":"51.5074",           "required":False},
            {"id":"home_lon",      "label":"Heimat Längengrad",         "type":"text",     "placeholder":"7.4653",            "required":False},
            {"id":"home_radius_m", "label":"Heimat-Radius (Meter)",     "type":"number",   "placeholder":"200",               "required":False},
        ]


# ─────────────────────────────────────────────────────────────────────────────
# MG / SAIC — iSMART API
# ─────────────────────────────────────────────────────────────────────────────

class MGSAICProvider(BaseProvider):
    """
    MG iSMART / SAIC API.
    Supports: MG4, MG5, MG ZS EV, EHS, Marvel R, Roewe.
    Uses the SAIC iSMART app API (unofficial).
    """

    PROVIDER_ID   = "mg_saic"
    PROVIDER_NAME = "MG / SAIC (iSMART)"
    CAPABILITIES  = ProviderCapabilities(
        charging_state  = True,
        soc             = True,
        odometer        = True,
        charge_power    = True,
        location        = True,
        charge_type     = False,
        notes           = [
            "SAIC iSMART API (inoffiziell) — für MG4, MG5, MG ZS EV, Roewe",
            "Erfordert iSMART-App-Login",
            "Stabilität: fragil — API ändert sich häufig",
        ],
        official_api    = False,
        requires_oauth  = False,
        requires_password = True,
        stability_level = "fragile",
        region_support  = "EU",
    )

    # Regional base URLs
    REGIONS = {
        "eu":   "https://tap-eu.soimt.com",
        "china":"https://tap-cn.soimt.com",
        "uk":   "https://tap-eu.soimt.com",
    }

    def _get_token(self) -> tuple[str, str]:
        """Returns (token, uid)."""
        region = self.config.get("mg_region", "eu").lower()
        base   = self.REGIONS.get(region, self.REGIONS["eu"])
        import hashlib
        pw_hash = hashlib.sha512(self.config.get("mg_password", "").encode()).hexdigest()
        r = requests.post(
            f"{base}/user/login",
            json={
                "loginName": self.config.get("mg_username", ""),
                "password":  pw_hash,
            },
            headers={"Content-Type": "application/json;charset=UTF-8",
                     "User-Agent":   "okhttp/3.14.9"},
            timeout=15,
        )
        r.raise_for_status()
        data = r.json().get("data", {})
        token = data.get("token") or data.get("access_token", "")
        uid   = str(data.get("uid", data.get("userId", "")))
        if not token:
            raise RuntimeError("MG/SAIC Login fehlgeschlagen — Zugangsdaten prüfen")
        return token, uid

    def _get_vin_and_series(self, token: str, uid: str) -> tuple[str, str]:
        vin_cfg = self.config.get("mg_vin", "").strip()
        region  = self.config.get("mg_region", "eu").lower()
        base    = self.REGIONS.get(region, self.REGIONS["eu"])
        r = requests.get(
            f"{base}/vehicle/list/v2",
            headers={"Authorization": f"Bearer {token}", "uid": uid},
            timeout=15,
        )
        r.raise_for_status()
        vehicles = r.json().get("data", {}).get("vinList", [])
        if not vehicles:
            raise RuntimeError("Kein MG/SAIC Fahrzeug gefunden")
        for v in vehicles:
            if not vin_cfg or v.get("vin") == vin_cfg:
                return v["vin"], v.get("series", "")
        return vehicles[0]["vin"], vehicles[0].get("series", "")

    def _get_vehicle_status(self, token: str, uid: str, vin: str) -> dict:
        region = self.config.get("mg_region", "eu").lower()
        base   = self.REGIONS.get(region, self.REGIONS["eu"])
        r = requests.get(
            f"{base}/vehicle/status/v2",
            headers={"Authorization": f"Bearer {token}", "uid": uid},
            params={"vin": vin},
            timeout=15,
        )
        r.raise_for_status()
        return r.json().get("data", {})

    def get_state(self) -> VehicleState:
        try:
            token, uid = self._get_token()
            vin, _     = self._get_vin_and_series(token, uid)
            status     = self._get_vehicle_status(token, uid, vin)

            bms  = status.get("bmsStatus", status)
            soc  = bms.get("bmsPackSOCDsp") or bms.get("fuelRangeElec", {}).get("remainBattery")
            chg  = bms.get("bmsChargeSts", 0)
            charging = int(chg) in (1, 2) if chg is not None else False

            power = None
            try:
                power_raw = bms.get("bmsChargingPower") or status.get("chargingPower")
                if power_raw:
                    power = round(float(power_raw) / 1000, 2)
            except Exception:
                pass

            odo = None
            try:
                odo = float(status.get("odometerStatus", {}).get("mileage") or 0) or None
            except Exception:
                pass

            location = "unknown"
            try:
                gps = status.get("gpsStatus", {})
                lat = gps.get("wayPointLat") or gps.get("latitude")
                lon = gps.get("wayPointLng") or gps.get("longitude")
                if lat and lon:
                    location = _home_location(self.config, float(lat)/1e6 if float(lat)>1000 else float(lat),
                                              float(lon)/1e6 if float(lon)>1000 else float(lon))
            except Exception:
                pass

            return VehicleState(charging=charging, soc=soc, odometer=odo,
                                charge_power=power, location=location)
        except Exception as e:
            return VehicleState(error=str(e))

    def test_connection(self) -> dict:
        try:
            token, uid = self._get_token()
            vin, _     = self._get_vin_and_series(token, uid)
            return {"ok": True, "message": f"✅ Verbunden · VIN: {vin}"}
        except Exception as e:
            return {"ok": False, "message": f"❌ {e}"}

    @classmethod
    def get_config_fields(cls) -> list[dict]:
        return [
            {"id":"mg_username",  "label":"iSMART E-Mail",             "type":"text",     "placeholder":"email@example.com", "required":True},
            {"id":"mg_password",  "label":"iSMART Passwort",           "type":"password", "placeholder":"",                  "required":True},
            {"id":"mg_region",    "label":"Region",                    "type":"select",   "options":["eu","uk","china"],     "required":False},
            {"id":"mg_vin",       "label":"Fahrzeug VIN (optional)",   "type":"text",     "placeholder":"LSJXXXX...",        "required":False},
            {"id":"home_lat",     "label":"Heimat Breitengrad",        "type":"text",     "placeholder":"51.5074",           "required":False},
            {"id":"home_lon",     "label":"Heimat Längengrad",         "type":"text",     "placeholder":"7.4653",            "required":False},
            {"id":"home_radius_m","label":"Heimat-Radius (Meter)",     "type":"number",   "placeholder":"200",               "required":False},
        ]


# ─────────────────────────────────────────────────────────────────────────────
# TOYOTA / LEXUS — Connected Services (MyT)
# ─────────────────────────────────────────────────────────────────────────────

class ToyotaLexusProvider(BaseProvider):
    """
    Toyota/Lexus Connected Services (MyT / Lexus Link).
    Supports: bZ4X, Prius PHEV, RAV4 PHEV, Yaris Cross PHEV, Lexus UX 300e, RZ.
    Uses the mytoyota Python library (unofficial).
    """

    PROVIDER_ID   = "toyota"
    PROVIDER_NAME = "Toyota / Lexus"
    CAPABILITIES  = ProviderCapabilities(
        charging_state  = True,
        soc             = True,
        odometer        = True,
        charge_power    = False,
        location        = True,
        charge_type     = False,
        notes           = [
            "Toyota MyT / Lexus Link API (mytoyota Bibliothek, inoffiziell)",
            "Für: bZ4X, Prius PHEV, RAV4 PHEV, Lexus RZ/UX 300e",
            "Benötigt: pip install mytoyota",
        ],
        official_api    = False,
        requires_oauth  = False,
        requires_password = True,
        stability_level = "medium",
        region_support  = "EU",
    )

    def _get_client(self):
        try:
            from mytoyota import MyT
        except ImportError:
            raise RuntimeError("Bibliothek fehlt: pip install mytoyota")
        import asyncio

        async def _login():
            client = MyT(username=self.config.get("toyota_username", ""),
                         password=self.config.get("toyota_password", ""),
                         locale=self.config.get("toyota_locale", "de-de"),
                         region=self.config.get("toyota_region", "europe"))
            await client.login()
            return client

        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                import concurrent.futures
                with concurrent.futures.ThreadPoolExecutor() as pool:
                    return pool.submit(asyncio.run, _login()).result()
            return loop.run_until_complete(_login())
        except RuntimeError:
            return asyncio.run(_login())

    def _get_vehicle(self):
        import asyncio
        client = self._get_client()

        async def _fetch():
            cars = await client.get_vehicles()
            vin_cfg = self.config.get("toyota_vin", "").strip()
            for car in cars:
                if not vin_cfg or car.vin == vin_cfg:
                    return car
            if cars:
                return cars[0]
            raise RuntimeError("Kein Toyota/Lexus gefunden")

        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                import concurrent.futures
                with concurrent.futures.ThreadPoolExecutor() as pool:
                    return pool.submit(asyncio.run, _fetch()).result()
            return loop.run_until_complete(_fetch())
        except RuntimeError:
            return asyncio.run(_fetch())

    def get_state(self) -> VehicleState:
        try:
            car = self._get_vehicle()
            import asyncio

            async def _status():
                await car.update()
                return car

            try:
                loop = asyncio.get_event_loop()
                if loop.is_running():
                    import concurrent.futures
                    with concurrent.futures.ThreadPoolExecutor() as pool:
                        car2 = pool.submit(asyncio.run, _status()).result()
                else:
                    car2 = loop.run_until_complete(_status())
            except RuntimeError:
                car2 = asyncio.run(_status())

            # EV status
            ev = getattr(car2, "electric_status", None) or {}
            if hasattr(ev, "battery_level"):
                soc = ev.battery_level
            else:
                soc = getattr(car2, "battery_level", None)

            charging = False
            chg_stat = getattr(ev, "charging_status", None) or getattr(car2, "charging_status", None)
            if chg_stat is not None:
                charging = str(chg_stat).lower() in ("charging", "in_progress", "pluggedincharging")

            odo = getattr(car2, "odometer", None)
            if odo is not None:
                try: odo = float(str(odo).split()[0])
                except Exception: odo = None

            location = "unknown"
            try:
                loc = getattr(car2, "location", None)
                if loc:
                    lat = getattr(loc, "latitude", None)
                    lon = getattr(loc, "longitude", None)
                    location = _home_location(self.config, lat, lon)
            except Exception:
                pass

            return VehicleState(charging=charging, soc=soc, odometer=odo, location=location)
        except Exception as e:
            return VehicleState(error=str(e))

    def test_connection(self) -> dict:
        try:
            car = self._get_vehicle()
            return {"ok": True, "message": f"✅ Verbunden · {getattr(car,'alias',car.vin)} ({car.vin})"}
        except Exception as e:
            return {"ok": False, "message": f"❌ {e}"}

    @classmethod
    def get_config_fields(cls) -> list[dict]:
        return [
            {"id":"toyota_username", "label":"MyT / Lexus Link E-Mail",   "type":"text",     "placeholder":"email@example.com", "required":True},
            {"id":"toyota_password", "label":"Passwort",                   "type":"password", "placeholder":"",                  "required":True},
            {"id":"toyota_locale",   "label":"Locale",                     "type":"text",     "placeholder":"de-de",             "required":False,
             "hint":"z.B. de-de, en-gb, fr-fr"},
            {"id":"toyota_region",   "label":"Region",                     "type":"select",   "options":["europe","north_america","asia"],"required":False},
            {"id":"toyota_vin",      "label":"Fahrzeug VIN (optional)",    "type":"text",     "placeholder":"JTDXX...",          "required":False},
            {"id":"home_lat",        "label":"Heimat Breitengrad",         "type":"text",     "placeholder":"51.5074",           "required":False},
            {"id":"home_lon",        "label":"Heimat Längengrad",          "type":"text",     "placeholder":"7.4653",            "required":False},
            {"id":"home_radius_m",   "label":"Heimat-Radius (Meter)",      "type":"number",   "placeholder":"200",               "required":False},
        ]


# ─────────────────────────────────────────────────────────────────────────────
# NISSAN — Ariya & Leaf (NissanConnect Services)
# ─────────────────────────────────────────────────────────────────────────────

class NissanProvider(BaseProvider):
    """
    Nissan Connect EV Services.
    Supports: Nissan Ariya, Leaf (2018+), e-NV200.
    Uses the nissan-connect-ev Python library (unofficial).
    """

    PROVIDER_ID   = "nissan"
    PROVIDER_NAME = "Nissan (Ariya / Leaf)"
    CAPABILITIES  = ProviderCapabilities(
        charging_state  = True,
        soc             = True,
        odometer        = False,
        charge_power    = False,
        location        = False,
        charge_type     = False,
        notes           = [
            "NissanConnect EV Services (inoffiziell)",
            "Für: Nissan Ariya, Leaf (2018+), e-NV200",
            "Kilometerstand/Ladeleistung nicht verfügbar via API",
            "Benötigt: pip install nissan-connect-ev",
        ],
        official_api    = False,
        requires_oauth  = False,
        requires_password = True,
        stability_level = "medium",
        region_support  = "EU",
    )

    # NissanConnect EV API
    REGIONS = {
        "eu":  ("NE", "https://alliance-platform-servicesidentity-prod.apps.eu-b01.kube.connected.toyota/"),
        "us":  ("NNA", "https://alliance-platform-servicesidentity-prod.apps.us-b01.kube.connected.toyota/"),
        "ca":  ("NCI", "https://alliance-platform-servicesidentity-prod.apps.us-b01.kube.connected.toyota/"),
        "au":  ("NMA", "https://alliance-platform-servicesidentity-prod.apps.eu-b01.kube.connected.toyota/"),
    }

    def _get_client(self):
        try:
            from nissanconnect.api import Nissan
        except ImportError:
            # Fallback to direct requests
            return None
        region_code, _ = self.REGIONS.get(self.config.get("nissan_region", "eu"), self.REGIONS["eu"])
        return Nissan(
            username=self.config.get("nissan_username", ""),
            password=self.config.get("nissan_password", ""),
            region=region_code,
        )

    def _direct_auth(self) -> tuple[str, str]:
        """Direct API auth as fallback when nissanconnect not installed."""
        region = self.config.get("nissan_region", "eu")
        _, base_url = self.REGIONS.get(region, self.REGIONS["eu"])
        # Use OpenID Connect
        r = requests.post(
            f"{base_url}v2/auth/openid-connect/token",
            data={
                "client_id":  "a-ncb-nc-android-prod",
                "grant_type": "password",
                "username":   self.config.get("nissan_username", ""),
                "password":   self.config.get("nissan_password", ""),
                "scope":      "openid profile email",
            },
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            timeout=15,
        )
        r.raise_for_status()
        tokens = r.json()
        return tokens.get("access_token", ""), tokens.get("refresh_token", "")

    def get_state(self) -> VehicleState:
        try:
            client = self._get_client()
            if client:
                import asyncio
                async def _fetch():
                    await client.login()
                    vehicles = await client.get_vehicles()
                    vin_cfg = self.config.get("nissan_vin", "").strip()
                    vehicle = next((v for v in vehicles if not vin_cfg or v.vin == vin_cfg), vehicles[0] if vehicles else None)
                    if not vehicle:
                        raise RuntimeError("Kein Nissan gefunden")
                    status = await vehicle.get_battery_status()
                    return status

                try:
                    loop = asyncio.get_event_loop()
                    if loop.is_running():
                        import concurrent.futures
                        with concurrent.futures.ThreadPoolExecutor() as pool:
                            status = pool.submit(asyncio.run, _fetch()).result()
                    else:
                        status = loop.run_until_complete(_fetch())
                except RuntimeError:
                    status = asyncio.run(_fetch())

                soc = getattr(status, "battery_level", None)
                charging = str(getattr(status, "charging_status", "")).lower() in ("charging", "normal_charging", "rapid_charging")
                return VehicleState(charging=charging, soc=soc)
            else:
                raise RuntimeError("Bibliothek nicht installiert: pip install nissan-connect-ev")
        except Exception as e:
            return VehicleState(error=str(e))

    def test_connection(self) -> dict:
        try:
            state = self.get_state()
            if state.error:
                return {"ok": False, "message": f"❌ {state.error}"}
            return {"ok": True, "message": f"✅ Verbunden · SOC: {state.soc}%"}
        except Exception as e:
            return {"ok": False, "message": f"❌ {e}"}

    @classmethod
    def get_config_fields(cls) -> list[dict]:
        return [
            {"id":"nissan_username", "label":"NissanConnect E-Mail",     "type":"text",     "placeholder":"email@example.com", "required":True},
            {"id":"nissan_password", "label":"Passwort",                  "type":"password", "placeholder":"",                  "required":True},
            {"id":"nissan_region",   "label":"Region",                    "type":"select",   "options":["eu","us","ca","au"],   "required":False},
            {"id":"nissan_vin",      "label":"Fahrzeug VIN (optional)",   "type":"text",     "placeholder":"SJNFA...",          "required":False},
        ]


# ─────────────────────────────────────────────────────────────────────────────
# PORSCHE — Porsche Connect / MyPorsche
# ─────────────────────────────────────────────────────────────────────────────

class PorscheProvider(BaseProvider):
    """
    Porsche Connect API (pyporsche library).
    Supports: Taycan, Macan EV, Cayenne E-Hybrid, Panamera E-Hybrid.
    Requires: pip install pyporsche
    """

    PROVIDER_ID   = "porsche"
    PROVIDER_NAME = "Porsche (Connect)"
    CAPABILITIES  = ProviderCapabilities(
        charging_state  = True,
        soc             = True,
        odometer        = True,
        charge_power    = False,
        location        = True,
        charge_type     = False,
        notes           = [
            "Porsche Connect API via pyporsche (inoffiziell)",
            "Für: Taycan, Macan EV, Cayenne/Panamera E-Hybrid",
            "Benötigt: pip install pyporsche",
        ],
        official_api    = False,
        requires_oauth  = True,
        requires_password = True,
        stability_level = "medium",
        region_support  = "global",
    )

    def _get_vehicle(self):
        try:
            import pyporsche
        except ImportError:
            raise RuntimeError("Bibliothek fehlt: pip install pyporsche")
        import asyncio

        async def _fetch():
            client = pyporsche.Porsche(
                email=self.config.get("porsche_username", ""),
                password=self.config.get("porsche_password", ""),
            )
            await client.login()
            vehicles = await client.getVehicles()
            vin_cfg = self.config.get("porsche_vin", "").strip()
            for v in vehicles:
                if not vin_cfg or v.vin == vin_cfg:
                    return v
            if vehicles:
                return vehicles[0]
            raise RuntimeError("Kein Porsche gefunden")

        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                import concurrent.futures
                with concurrent.futures.ThreadPoolExecutor() as pool:
                    return pool.submit(asyncio.run, _fetch()).result()
            return loop.run_until_complete(_fetch())
        except RuntimeError:
            return asyncio.run(_fetch())

    def get_state(self) -> VehicleState:
        try:
            import asyncio
            vehicle = self._get_vehicle()

            async def _status(v):
                data = await v.getCurrentOverview()
                return data

            try:
                loop = asyncio.get_event_loop()
                if loop.is_running():
                    import concurrent.futures
                    with concurrent.futures.ThreadPoolExecutor() as pool:
                        data = pool.submit(asyncio.run, _status(vehicle)).result()
                else:
                    data = loop.run_until_complete(_status(vehicle))
            except RuntimeError:
                data = asyncio.run(_status(vehicle))

            # data is typically a dict or object
            if isinstance(data, dict):
                soc      = data.get("batteryLevel", {}).get("value")
                chg_stat = data.get("chargingState", {}).get("value", "")
                odo_val  = data.get("mileage", {}).get("value")
                loc_lat  = data.get("parkingLight", {}).get("value")  # fallback
            else:
                soc      = getattr(data, "batteryLevel", None)
                chg_stat = str(getattr(data, "chargingState", ""))
                odo_val  = getattr(data, "mileage", None)
                loc_lat  = None

            charging = str(chg_stat).upper() in ("CHARGING", "PLUGGED_IN_CHARGING")
            soc      = float(soc) if soc is not None else None
            odo      = float(odo_val) if odo_val is not None else None

            return VehicleState(charging=charging, soc=soc, odometer=odo)
        except Exception as e:
            return VehicleState(error=str(e))

    def test_connection(self) -> dict:
        try:
            v = self._get_vehicle()
            return {"ok": True, "message": f"✅ Verbunden · VIN: {v.vin}"}
        except Exception as e:
            return {"ok": False, "message": f"❌ {e}"}

    @classmethod
    def get_config_fields(cls) -> list[dict]:
        return [
            {"id":"porsche_username", "label":"My Porsche E-Mail",        "type":"text",     "placeholder":"email@example.com", "required":True},
            {"id":"porsche_password", "label":"Passwort",                  "type":"password", "placeholder":"",                  "required":True},
            {"id":"porsche_vin",      "label":"Fahrzeug VIN (optional)",   "type":"text",     "placeholder":"WP0...",            "required":False},
        ]


# ─────────────────────────────────────────────────────────────────────────────
# JLR — Jaguar Land Rover InControl
# ─────────────────────────────────────────────────────────────────────────────

class JLRProvider(BaseProvider):
    """
    Jaguar Land Rover InControl Remote API (jlrpy).
    Supports: Jaguar I-Pace, XJ90, Range Rover PHEV, Defender PHEV.
    Requires: pip install jlrpy
    """

    PROVIDER_ID   = "jlr"
    PROVIDER_NAME = "Jaguar / Land Rover (InControl)"
    CAPABILITIES  = ProviderCapabilities(
        charging_state  = True,
        soc             = True,
        odometer        = True,
        charge_power    = False,
        location        = True,
        charge_type     = False,
        notes           = [
            "JLR InControl Remote API via jlrpy (inoffiziell)",
            "Für: Jaguar I-Pace, Range Rover PHEV, Defender PHEV",
            "Benötigt: pip install jlrpy",
        ],
        official_api    = False,
        requires_oauth  = False,
        requires_password = True,
        stability_level = "medium",
        region_support  = "global",
    )

    def _get_vehicle(self):
        try:
            import jlrpy
        except ImportError:
            raise RuntimeError("Bibliothek fehlt: pip install jlrpy")
        conn = jlrpy.Connection(
            email=self.config.get("jlr_username", ""),
            password=self.config.get("jlr_password", ""),
        )
        conn.connect()
        vin_cfg = self.config.get("jlr_vin", "").strip()
        vehicles = conn.vehicles
        if not vehicles:
            raise RuntimeError("Kein JLR Fahrzeug gefunden")
        if vin_cfg:
            for v in vehicles:
                if v.vin == vin_cfg:
                    return v
        return vehicles[0]

    def get_state(self) -> VehicleState:
        try:
            v    = self._get_vehicle()
            status = v.get_status()

            def _attr(key):
                return next((x["value"] for x in status.get("vehicleStatus", []) if x.get("key") == key), None)

            soc      = _attr("EV_STATE_OF_CHARGE")
            chg_stat = _attr("EV_CHARGING_STATUS") or ""
            charging = str(chg_stat).upper() in ("CHARGING", "RAPID_CHARGE")
            odo_raw  = _attr("ODOMETER_MASTER_MILES") or _attr("ODOMETER_MASTER_KM")
            odo      = round(float(odo_raw) * 1.60934, 1) if odo_raw and "MILES" in str(_attr) else (float(odo_raw) if odo_raw else None)

            location = "unknown"
            try:
                pos = v.get_position()
                if pos:
                    lat = pos.get("position", {}).get("lat")
                    lon = pos.get("position", {}).get("lon")
                    location = _home_location(self.config, lat, lon)
            except Exception:
                pass

            return VehicleState(charging=charging, soc=float(soc) if soc else None,
                                odometer=odo, location=location)
        except Exception as e:
            return VehicleState(error=str(e))

    def test_connection(self) -> dict:
        try:
            v = self._get_vehicle()
            return {"ok": True, "message": f"✅ Verbunden · VIN: {v.vin}"}
        except Exception as e:
            return {"ok": False, "message": f"❌ {e}"}

    @classmethod
    def get_config_fields(cls) -> list[dict]:
        return [
            {"id":"jlr_username", "label":"InControl E-Mail",          "type":"text",     "placeholder":"email@example.com", "required":True},
            {"id":"jlr_password", "label":"Passwort",                   "type":"password", "placeholder":"",                  "required":True},
            {"id":"jlr_vin",      "label":"Fahrzeug VIN (optional)",    "type":"text",     "placeholder":"SAL...",            "required":False},
            {"id":"home_lat",     "label":"Heimat Breitengrad",         "type":"text",     "placeholder":"51.5074",           "required":False},
            {"id":"home_lon",     "label":"Heimat Längengrad",          "type":"text",     "placeholder":"7.4653",            "required":False},
            {"id":"home_radius_m","label":"Heimat-Radius (Meter)",      "type":"number",   "placeholder":"200",               "required":False},
        ]


# ─────────────────────────────────────────────────────────────────────────────
# XPENG — Stub (no stable direct API)
# ─────────────────────────────────────────────────────────────────────────────

class XPengProvider(BaseProvider):
    """
    XPeng — stub provider. No stable direct API available.
    Use via Enode, TRONITY, or Smartcar aggregator.
    """

    PROVIDER_ID   = "xpeng"
    PROVIDER_NAME = "XPeng"
    CAPABILITIES  = ProviderCapabilities(
        charging_state  = False,
        soc             = False,
        odometer        = False,
        charge_power    = False,
        location        = False,
        charge_type     = False,
        notes           = [
            "⚠️ Keine stabile direkte API verfügbar",
            "Bitte Tronity, Enode oder Smartcar als Aggregator verwenden",
        ],
        official_api    = False,
        requires_oauth  = False,
        requires_password = False,
        stability_level = "fragile",
        region_support  = "EU",
    )

    def get_state(self) -> VehicleState:
        return VehicleState(error="XPeng: Keine direkte API verfügbar — bitte Enode/Tronity/Smartcar verwenden")

    def test_connection(self) -> dict:
        return {"ok": False, "message": "⚠️ XPeng hat keine stabile direkte API. Bitte Enode, TRONITY oder Smartcar als Aggregator verwenden."}

    @classmethod
    def get_config_fields(cls) -> list[dict]:
        return [
            {"id": "_xpeng_info", "label": "Information", "type": "info",
             "placeholder": "Bitte Tronity, Enode oder Smartcar Provider verwenden.", "required": False},
        ]


# ─────────────────────────────────────────────────────────────────────────────
# BYD — Stub (no stable direct API for EU)
# ─────────────────────────────────────────────────────────────────────────────

class BYDProvider(BaseProvider):
    """
    BYD — stub provider. No stable direct EU API available.
    Use via Enode, TRONITY, or Smartcar aggregator.
    """

    PROVIDER_ID   = "byd"
    PROVIDER_NAME = "BYD"
    CAPABILITIES  = ProviderCapabilities(
        charging_state  = False,
        soc             = False,
        odometer        = False,
        charge_power    = False,
        location        = False,
        charge_type     = False,
        notes           = [
            "⚠️ Keine stabile direkte API für Europa verfügbar",
            "Bitte Tronity, Enode oder Smartcar als Aggregator verwenden",
        ],
        official_api    = False,
        requires_oauth  = False,
        requires_password = False,
        stability_level = "fragile",
        region_support  = "unknown",
    )

    def get_state(self) -> VehicleState:
        return VehicleState(error="BYD: Keine direkte API für Europa — bitte Enode/Tronity/Smartcar verwenden")

    def test_connection(self) -> dict:
        return {"ok": False, "message": "⚠️ BYD hat keine stabile direkte API für Europa. Bitte Enode, TRONITY oder Smartcar als Aggregator verwenden."}

    @classmethod
    def get_config_fields(cls) -> list[dict]:
        return [
            {"id": "_byd_info", "label": "Information", "type": "info",
             "placeholder": "Bitte Tronity, Enode oder Smartcar Provider verwenden.", "required": False},
        ]


# ─────────────────────────────────────────────────────────────────────────────
# TRONITY — Aggregator
# ─────────────────────────────────────────────────────────────────────────────

class TronityProvider(BaseProvider):
    """
    TRONITY EV Cloud Aggregator.
    Supports 90+ EV brands via single API. Requires TRONITY account + API key.
    https://app.tronity.io
    """

    PROVIDER_ID   = "tronity"
    PROVIDER_NAME = "TRONITY (Aggregator)"
    CAPABILITIES  = ProviderCapabilities(
        charging_state  = True,
        soc             = True,
        odometer        = True,
        charge_power    = True,
        location        = True,
        charge_type     = False,
        notes           = [
            "TRONITY Aggregator — 90+ Fahrzeugmarken",
            "Kostenpflichtiger Dienst (kostenlose Testphase verfügbar)",
            "https://app.tronity.io",
        ],
        official_api    = True,
        requires_oauth  = True,
        requires_password = False,
        stability_level = "stable",
        region_support  = "global",
    )

    API_BASE  = "https://api.tronity.io/tronity"
    AUTH_URL  = "https://api.tronity.io/authentication"

    def _get_token(self) -> str:
        r = requests.post(
            self.AUTH_URL,
            json={
                "client_id":     self.config.get("tronity_client_id", ""),
                "client_secret": self.config.get("tronity_client_secret", ""),
                "grant_type":    "app",
            },
            timeout=15,
        )
        r.raise_for_status()
        return r.json().get("access_token", "")

    def _get_vehicle_id(self, token: str) -> str:
        vid = self.config.get("tronity_vehicle_id", "").strip()
        if vid:
            return vid
        r = requests.get(
            f"{self.API_BASE}/vehicles",
            headers={"Authorization": f"Bearer {token}"},
            timeout=15,
        )
        r.raise_for_status()
        vehicles = r.json().get("data", r.json() if isinstance(r.json(), list) else [])
        if not vehicles:
            raise RuntimeError("Kein Fahrzeug im TRONITY-Konto")
        return vehicles[0].get("id", vehicles[0].get("vehicleId", ""))

    def get_state(self) -> VehicleState:
        try:
            token = self._get_token()
            vid   = self._get_vehicle_id(token)
            hdrs  = {"Authorization": f"Bearer {token}"}

            # Last record
            r = requests.get(f"{self.API_BASE}/vehicles/{vid}/last_record",
                             headers=hdrs, timeout=15)
            r.raise_for_status()
            data = r.json()

            soc      = data.get("level")
            charging = bool(data.get("is_charging"))
            odo      = data.get("odometer")
            power    = data.get("charging_rate")

            location = "unknown"
            try:
                lat = data.get("latitude")
                lon = data.get("longitude")
                location = _home_location(self.config, lat, lon)
            except Exception:
                pass

            return VehicleState(charging=charging, soc=soc, odometer=odo,
                                charge_power=power, location=location)
        except Exception as e:
            return VehicleState(error=str(e))

    def test_connection(self) -> dict:
        try:
            token = self._get_token()
            vid   = self._get_vehicle_id(token)
            return {"ok": True, "message": f"✅ TRONITY verbunden · Fahrzeug: {vid}"}
        except Exception as e:
            return {"ok": False, "message": f"❌ {e}"}

    @classmethod
    def get_config_fields(cls) -> list[dict]:
        return [
            {"id":"tronity_client_id",     "label":"TRONITY Client ID",       "type":"text",     "placeholder":"",  "required":True,
             "hint":"Zu finden unter app.tronity.io → API Keys"},
            {"id":"tronity_client_secret", "label":"TRONITY Client Secret",   "type":"password", "placeholder":"",  "required":True},
            {"id":"tronity_vehicle_id",    "label":"Fahrzeug ID (optional)",   "type":"text",     "placeholder":"",  "required":False,
             "hint":"Leer lassen = erstes Fahrzeug wird verwendet"},
            {"id":"home_lat",              "label":"Heimat Breitengrad",       "type":"text",     "placeholder":"51.5074","required":False},
            {"id":"home_lon",              "label":"Heimat Längengrad",        "type":"text",     "placeholder":"7.4653", "required":False},
            {"id":"home_radius_m",         "label":"Heimat-Radius (Meter)",    "type":"number",   "placeholder":"200",    "required":False},
        ]


# ─────────────────────────────────────────────────────────────────────────────
# ENODE — Aggregator
# ─────────────────────────────────────────────────────────────────────────────

class EnodeProvider(BaseProvider):
    """
    Enode EV API Aggregator.
    Supports 50+ EV brands. Requires Enode API key.
    https://enode.com
    """

    PROVIDER_ID   = "enode"
    PROVIDER_NAME = "Enode (Aggregator)"
    CAPABILITIES  = ProviderCapabilities(
        charging_state  = True,
        soc             = True,
        odometer        = True,
        charge_power    = True,
        location        = True,
        charge_type     = False,
        notes           = [
            "Enode API Aggregator — 50+ Fahrzeugmarken",
            "Kostenpflichtiger Dienst",
            "https://enode.com",
        ],
        official_api    = True,
        requires_oauth  = True,
        requires_password = False,
        stability_level = "stable",
        region_support  = "global",
    )

    API_BASE = "https://enode-api.production.enode.io"

    def _get_token(self) -> str:
        r = requests.post(
            f"{self.API_BASE}/oauth2/token",
            auth=(self.config.get("enode_client_id", ""), self.config.get("enode_client_secret", "")),
            data={"grant_type": "client_credentials"},
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            timeout=15,
        )
        r.raise_for_status()
        return r.json().get("access_token", "")

    def _get_vehicle(self, token: str) -> dict:
        vid = self.config.get("enode_vehicle_id", "").strip()
        uid = self.config.get("enode_user_id", "").strip()
        if not uid:
            raise RuntimeError("Enode User ID erforderlich (enode_user_id)")
        hdrs = {"Authorization": f"Bearer {token}"}
        if vid:
            r = requests.get(f"{self.API_BASE}/vehicles/{vid}", headers=hdrs, timeout=15)
        else:
            r = requests.get(f"{self.API_BASE}/users/{uid}/vehicles", headers=hdrs, timeout=15)
            r.raise_for_status()
            data = r.json()
            vehicles = data.get("data", data) if isinstance(data, dict) else data
            if not vehicles:
                raise RuntimeError("Kein Fahrzeug im Enode-Konto")
            return vehicles[0]
        r.raise_for_status()
        return r.json()

    def get_state(self) -> VehicleState:
        try:
            token   = self._get_token()
            vehicle = self._get_vehicle(token)
            info    = vehicle.get("information", {})
            chg     = vehicle.get("chargeState", {})
            loc     = vehicle.get("location", {})
            odo_raw = info.get("odometer") or vehicle.get("odometer")

            soc      = chg.get("batteryLevel")
            charging = chg.get("isCharging", False)
            power    = chg.get("chargeRate")
            odo      = float(odo_raw) if odo_raw else None

            location = "unknown"
            try:
                lat = loc.get("latitude") or loc.get("lat")
                lon = loc.get("longitude") or loc.get("lon")
                location = _home_location(self.config, lat, lon)
            except Exception:
                pass

            return VehicleState(charging=charging, soc=soc, odometer=odo,
                                charge_power=power, location=location)
        except Exception as e:
            return VehicleState(error=str(e))

    def test_connection(self) -> dict:
        try:
            token   = self._get_token()
            vehicle = self._get_vehicle(token)
            name    = vehicle.get("information", {}).get("displayName") or vehicle.get("id", "")
            return {"ok": True, "message": f"✅ Enode verbunden · {name}"}
        except Exception as e:
            return {"ok": False, "message": f"❌ {e}"}

    @classmethod
    def get_config_fields(cls) -> list[dict]:
        return [
            {"id":"enode_client_id",     "label":"Enode Client ID",          "type":"text",     "placeholder":"",  "required":True,
             "hint":"Zu finden im Enode Developer Dashboard"},
            {"id":"enode_client_secret", "label":"Enode Client Secret",      "type":"password", "placeholder":"",  "required":True},
            {"id":"enode_user_id",       "label":"Enode User ID",            "type":"text",     "placeholder":"",  "required":True,
             "hint":"User ID aus dem Enode-Konto des Fahrzeugbesitzers"},
            {"id":"enode_vehicle_id",    "label":"Fahrzeug ID (optional)",   "type":"text",     "placeholder":"",  "required":False},
            {"id":"home_lat",            "label":"Heimat Breitengrad",       "type":"text",     "placeholder":"51.5074","required":False},
            {"id":"home_lon",            "label":"Heimat Längengrad",        "type":"text",     "placeholder":"7.4653", "required":False},
            {"id":"home_radius_m",       "label":"Heimat-Radius (Meter)",    "type":"number",   "placeholder":"200",    "required":False},
        ]


# ─────────────────────────────────────────────────────────────────────────────
# SMARTCAR — Aggregator
# ─────────────────────────────────────────────────────────────────────────────

class SmartcarProvider(BaseProvider):
    """
    Smartcar EV API Aggregator.
    Supports 30+ brands. Requires Smartcar API key + user OAuth token.
    https://smartcar.com
    """

    PROVIDER_ID   = "smartcar"
    PROVIDER_NAME = "Smartcar (Aggregator)"
    CAPABILITIES  = ProviderCapabilities(
        charging_state  = True,
        soc             = True,
        odometer        = True,
        charge_power    = False,
        location        = True,
        charge_type     = False,
        notes           = [
            "Smartcar API Aggregator — 30+ Fahrzeugmarken",
            "Benötigt OAuth-Verbindung pro Fahrzeug",
            "https://smartcar.com",
        ],
        official_api    = True,
        requires_oauth  = True,
        requires_password = False,
        stability_level = "stable",
        region_support  = "global",
    )

    API_BASE = "https://api.smartcar.com/v2.0"

    def _headers(self) -> dict:
        access_token = self.config.get("smartcar_access_token", "")
        return {"Authorization": f"Bearer {access_token}"}

    def _get_vehicle_id(self) -> str:
        vid = self.config.get("smartcar_vehicle_id", "").strip()
        if vid:
            return vid
        r = requests.get(f"{self.API_BASE}/vehicles", headers=self._headers(), timeout=15)
        r.raise_for_status()
        vehicles = r.json().get("vehicles", [])
        if not vehicles:
            raise RuntimeError("Kein Fahrzeug im Smartcar-Konto")
        return vehicles[0]

    def get_state(self) -> VehicleState:
        try:
            vid   = self._get_vehicle_id()
            hdrs  = self._headers()

            # Batch request
            soc      = None
            charging = False
            odo      = None
            location = "unknown"

            try:
                r = requests.get(f"{self.API_BASE}/vehicles/{vid}/battery", headers=hdrs, timeout=15)
                r.raise_for_status()
                data = r.json()
                soc  = data.get("percentRemaining")
                if soc is not None:
                    soc = round(soc * 100, 1)
            except Exception:
                pass

            try:
                r = requests.get(f"{self.API_BASE}/vehicles/{vid}/charge", headers=hdrs, timeout=15)
                r.raise_for_status()
                data     = r.json()
                chg_stat = data.get("state", "")
                charging = str(chg_stat).upper() in ("CHARGING", "FULLY_CHARGED")
            except Exception:
                pass

            try:
                r = requests.get(f"{self.API_BASE}/vehicles/{vid}/odometer", headers=hdrs, timeout=15)
                r.raise_for_status()
                odo = r.json().get("distance")
            except Exception:
                pass

            try:
                r = requests.get(f"{self.API_BASE}/vehicles/{vid}/location", headers=hdrs, timeout=15)
                r.raise_for_status()
                loc_data = r.json()
                lat = loc_data.get("latitude")
                lon = loc_data.get("longitude")
                location = _home_location(self.config, lat, lon)
            except Exception:
                pass

            return VehicleState(charging=charging, soc=soc, odometer=odo, location=location)
        except Exception as e:
            return VehicleState(error=str(e))

    def test_connection(self) -> dict:
        try:
            vid = self._get_vehicle_id()
            r   = requests.get(f"{self.API_BASE}/vehicles/{vid}", headers=self._headers(), timeout=15)
            r.raise_for_status()
            data  = r.json()
            make  = data.get("make", "")
            model = data.get("model", "")
            return {"ok": True, "message": f"✅ Smartcar verbunden · {make} {model} ({vid[:8]}…)"}
        except Exception as e:
            return {"ok": False, "message": f"❌ {e}"}

    @classmethod
    def get_config_fields(cls) -> list[dict]:
        return [
            {"id":"smartcar_access_token", "label":"Smartcar Access Token",    "type":"password","placeholder":"",  "required":True,
             "hint":"OAuth2 Access Token für das verknüpfte Fahrzeug (über Smartcar Connect erhalten)"},
            {"id":"smartcar_vehicle_id",   "label":"Fahrzeug ID (optional)",   "type":"text",    "placeholder":"",  "required":False,
             "hint":"Leer lassen = erstes Fahrzeug wird verwendet"},
            {"id":"home_lat",              "label":"Heimat Breitengrad",       "type":"text",    "placeholder":"51.5074","required":False},
            {"id":"home_lon",              "label":"Heimat Längengrad",        "type":"text",    "placeholder":"7.4653", "required":False},
            {"id":"home_radius_m",         "label":"Heimat-Radius (Meter)",    "type":"number",  "placeholder":"200",    "required":False},
        ]
