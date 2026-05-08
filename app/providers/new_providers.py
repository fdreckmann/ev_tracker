"""
Neue Provider: Hyundai/Kia, Renault/Dacia, Polestar, Audi
"""
import asyncio
import hashlib
import logging
import requests
from .base import BaseProvider, ProviderCapabilities, VehicleState

log = logging.getLogger(__name__)


def _run(coro):
    """Asyncio-Bridge für sync Kontext."""
    try:
        loop = asyncio.get_event_loop()
        if loop.is_running():
            import concurrent.futures
            with concurrent.futures.ThreadPoolExecutor() as pool:
                future = pool.submit(asyncio.run, coro)
                return future.result()
        return loop.run_until_complete(coro)
    except RuntimeError:
        return asyncio.run(coro)


# ─────────────────────────────────────────────────────────────────────────────
# Hyundai / Kia (Bluelink / UVO Connect)
# ─────────────────────────────────────────────────────────────────────────────

class HyundaiKiaProvider(BaseProvider):

    PROVIDER_ID   = "hyundai_kia"
    PROVIDER_NAME = "Hyundai / Kia"
    CAPABILITIES  = ProviderCapabilities(
        charging_state = True,
        soc            = True,
        odometer       = True,
        charge_power   = False,
        location       = True,
        charge_type    = False,
        notes = [
            "Bluelink (Hyundai) / UVO Connect (Kia) — inoffizielle API",
            "Erfordert App-PIN (4-stellig)",
            "Ladeleistung nicht verfügbar via API",
        ]
    )

    def _get_manager(self):
        try:
            from hyundai_kia_connect_api import VehicleManager
        except ImportError:
            raise RuntimeError("Bibliothek nicht installiert: pip install hyundai-kia-connect-api")

        region_map = {"europe": 1, "usa": 2, "canada": 3, "australia": 4}
        brand_map  = {"hyundai": 1, "kia": 2, "genesis": 3}
        region = region_map.get(self.config.get("hk_region", "europe"), 1)
        brand  = brand_map.get(self.config.get("hk_brand",  "hyundai"), 1)

        manager = VehicleManager(
            region   = region,
            brand    = brand,
            username = self.config.get("hk_username", ""),
            password = self.config.get("hk_password", ""),
            pin      = self.config.get("hk_pin", ""),
        )
        _run(manager.check_and_refresh_token())
        _run(manager.update_all_vehicles_with_cached_state())
        return manager

    def _get_vehicle(self):
        manager = self._get_manager()
        vin = self.config.get("hk_vin", "").strip()
        for _, v in manager.vehicles.items():
            if not vin or v.vin == vin:
                return v
        raise RuntimeError("Kein Fahrzeug gefunden")

    def get_state(self) -> VehicleState:
        try:
            v        = self._get_vehicle()
            charging = bool(getattr(v, "ev_battery_is_charging", False))
            soc      = getattr(v, "ev_battery_percentage", None)
            odo      = getattr(v, "odometer", None)

            location  = "unknown"
            home_lat  = float(self.config.get("home_lat", 0) or 0)
            home_lon  = float(self.config.get("home_lon", 0) or 0)
            radius    = float(self.config.get("home_radius_m", 200) or 200)
            if home_lat and home_lon and hasattr(v, "location") and v.location:
                loc  = v.location
                lat  = getattr(loc, "lat", None)
                lng  = getattr(loc, "lng", None)
                if lat is not None and lng is not None:
                    dist     = ((lat - home_lat)**2 + (lng - home_lon)**2)**0.5 * 111000
                    location = "home" if dist < radius else "extern"

            return VehicleState(charging=charging, soc=soc, odometer=odo, location=location)
        except Exception as e:
            return VehicleState(error=str(e))

    def test_connection(self) -> dict:
        try:
            v = self._get_vehicle()
            return {"ok": True, "message": f"✅ {getattr(v,'name',v.vin)} ({v.vin})"}
        except Exception as e:
            return {"ok": False, "message": f"❌ {e}"}

    @classmethod
    def get_config_fields(cls) -> list[dict]:
        return [
            {"id":"hk_brand",      "label":"Marke",                    "type":"select",   "options":["hyundai","kia","genesis"],          "required":True},
            {"id":"hk_username",   "label":"Bluelink/UVO E-Mail",      "type":"text",     "placeholder":"email@example.com",             "required":True},
            {"id":"hk_password",   "label":"Passwort",                 "type":"password", "placeholder":"",                              "required":True},
            {"id":"hk_pin",        "label":"App-PIN (4-stellig)",      "type":"text",     "placeholder":"1234",                          "required":True,
             "hint":"4-stellige PIN wie in der Bluelink / UVO Connect App"},
            {"id":"hk_region",     "label":"Region",                   "type":"select",   "options":["europe","usa","canada","australia"],"required":False},
            {"id":"hk_vin",        "label":"Fahrzeug VIN (optional)",  "type":"text",     "placeholder":"TMAH...",                       "required":False},
            {"id":"home_lat",      "label":"Heimat Breitengrad",       "type":"text",     "placeholder":"51.5074",                       "required":False},
            {"id":"home_lon",      "label":"Heimat Längengrad",        "type":"text",     "placeholder":"7.4653",                        "required":False},
            {"id":"home_radius_m", "label":"Heimat-Radius (Meter)",    "type":"number",   "placeholder":"200",                           "required":False},
        ]


# ─────────────────────────────────────────────────────────────────────────────
# Renault / Dacia (My Renault API)
# ─────────────────────────────────────────────────────────────────────────────

class RenaultProvider(BaseProvider):

    PROVIDER_ID   = "renault"
    PROVIDER_NAME = "Renault / Dacia"
    CAPABILITIES  = ProviderCapabilities(
        charging_state = True,
        soc            = True,
        odometer       = True,
        charge_power   = True,
        location       = False,
        charge_type    = False,
        notes = [
            "My Renault API via renault-api Bibliothek",
            "Standort nicht verfügbar via API",
        ]
    )

    def _get_vehicle(self):
        try:
            from renault_api.renault_client import RenaultClient
        except ImportError:
            raise RuntimeError("Bibliothek nicht installiert: pip install renault-api")

        locale  = self.config.get("renault_locale", "de_DE")
        account = self.config.get("renault_account", "")

        async def _fetch():
            import aiohttp
            async with aiohttp.ClientSession() as session:
                client = RenaultClient(websession=session, locale=locale)
                await client.session.login(
                    self.config.get("renault_username",""),
                    self.config.get("renault_password","")
                )
                accounts = await client.get_api_accounts()
                acct = None
                for a in accounts:
                    aid = await a.get_account_id()
                    if not account or aid == account:
                        acct = a
                        break
                if not acct:
                    raise RuntimeError("Kein Renault-Konto gefunden")

                vehicles = await acct.get_vehicles()
                vin = self.config.get("renault_vin", "").strip()
                for v in vehicles.vehicleLinks:
                    if not vin or v.vin == vin:
                        veh = await acct.get_api_vehicle(v.vin)
                        battery = await veh.get_battery_status()
                        cockpit = None
                        try:
                            cockpit = await veh.get_cockpit()
                        except Exception:
                            pass
                        return v.vin, battery, cockpit
                raise RuntimeError("Kein Fahrzeug gefunden")

        return _run(_fetch())

    def get_state(self) -> VehicleState:
        try:
            vin, battery, cockpit = self._get_vehicle()
            charging = battery.chargingStatus is not None and float(battery.chargingStatus) > 0
            soc      = battery.batteryLevel
            power    = getattr(battery, "chargingInstantaneousPower", None)
            odo      = getattr(cockpit, "totalMileage", None) if cockpit else None
            return VehicleState(charging=charging, soc=soc, odometer=odo, charge_power=power)
        except Exception as e:
            return VehicleState(error=str(e))

    def test_connection(self) -> dict:
        try:
            vin, _, _ = self._get_vehicle()
            return {"ok": True, "message": f"✅ Verbunden · VIN: {vin}"}
        except Exception as e:
            return {"ok": False, "message": f"❌ {e}"}

    @classmethod
    def get_config_fields(cls) -> list[dict]:
        return [
            {"id":"renault_username", "label":"My Renault E-Mail",       "type":"text",     "placeholder":"email@example.com", "required":True},
            {"id":"renault_password", "label":"Passwort",                 "type":"password", "placeholder":"",                  "required":True},
            {"id":"renault_locale",   "label":"Locale",                   "type":"text",     "placeholder":"de_DE",             "required":False,
             "hint":"z.B. de_DE, fr_FR, en_GB"},
            {"id":"renault_account",  "label":"Account ID (optional)",    "type":"text",     "placeholder":"",                  "required":False},
            {"id":"renault_vin",      "label":"Fahrzeug VIN (optional)",  "type":"text",     "placeholder":"VF1...",            "required":False},
        ]


# ─────────────────────────────────────────────────────────────────────────────
# Polestar (inoffizielle GraphQL API)
# ─────────────────────────────────────────────────────────────────────────────

class PolestarProvider(BaseProvider):

    PROVIDER_ID   = "polestar"
    PROVIDER_NAME = "Polestar"
    CAPABILITIES  = ProviderCapabilities(
        charging_state = True,
        soc            = True,
        odometer       = True,
        charge_power   = False,
        location       = False,
        charge_type    = False,
        notes = [
            "Inoffizielle Polestar App-API (GraphQL)",
            "Ladeleistung und Standort nicht verfügbar",
        ]
    )

    AUTH_URL   = "https://polestarid.eu.polestar.com"
    API_URL    = "https://pc-api.polestar.com/eu-north-1/my-star/"
    CLIENT_ID  = "l3oopkc_10"
    REDIRECT   = "https://www.polestar.com/sign-in-callback"

    def _auth(self) -> str:
        """Gibt Bearer Token zurück."""
        sess = requests.Session()
        # Step 1: OIDC authorize → get code_challenge etc.
        r1 = sess.get(
            f"{self.AUTH_URL}/as/authorization.oauth2",
            params={
                "client_id":             self.CLIENT_ID,
                "redirect_uri":          self.REDIRECT,
                "response_type":         "code",
                "scope":                 "openid profile email customer:attributes",
                "state":                 "polestar_login",
            }, allow_redirects=False, timeout=15
        )
        # Step 2: POST login form
        login_url = r1.headers.get("Location", "")
        if not login_url:
            raise RuntimeError("Polestar Auth-Weiterleitung fehlt")
        r2 = sess.get(login_url, timeout=15)
        # Extract resumePath from page
        import re
        resume = re.search(r'resumePath=([^&"\']+)', r2.text)
        if not resume:
            raise RuntimeError("Polestar Login-Seite konnte nicht geladen werden")
        resume_path = resume.group(1)

        r3 = sess.post(
            f"{self.AUTH_URL}/as/{resume_path}/resume/as/authorization.ping",
            data={
                "subject":   self.config.get("polestar_username",""),
                "password":  self.config.get("polestar_password",""),
            },
            allow_redirects=True, timeout=15
        )
        # Extract code from final redirect URL
        code_match = re.search(r"[?&]code=([^&]+)", r3.url)
        if not code_match:
            raise RuntimeError("Polestar: Login fehlgeschlagen — Zugangsdaten prüfen")
        code = code_match.group(1)

        # Step 3: Exchange code for token
        r4 = sess.post(
            f"{self.AUTH_URL}/as/token.oauth2",
            data={
                "grant_type":   "authorization_code",
                "code":         code,
                "redirect_uri": self.REDIRECT,
                "client_id":    self.CLIENT_ID,
            }, timeout=15
        )
        r4.raise_for_status()
        return r4.json()["access_token"]

    def _query(self, token: str, vin: str) -> dict:
        query = """
        query GetBatteryData($vin: String!) {
          getBatteryStatus(vin: $vin) {
            batteryChargeLevelPercentage
            chargingStatus
            estimatedChargingTimeToFullMinutes
          }
          getOdometerData(vin: $vin) {
            odometerMeters
          }
        }"""
        r = requests.post(
            self.API_URL,
            json={"query": query, "variables": {"vin": vin}},
            headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
            timeout=15
        )
        r.raise_for_status()
        return r.json().get("data", {})

    def _get_vin(self, token: str) -> str:
        vin = self.config.get("polestar_vin", "").strip()
        if vin:
            return vin
        q = """query { getConsumerCarsV2 { vin } }"""
        r = requests.post(
            self.API_URL,
            json={"query": q},
            headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
            timeout=15
        )
        r.raise_for_status()
        cars = r.json().get("data", {}).get("getConsumerCarsV2", [])
        if not cars:
            raise RuntimeError("Kein Polestar gefunden")
        return cars[0]["vin"]

    def get_state(self) -> VehicleState:
        try:
            token = self._auth()
            vin   = self._get_vin(token)
            data  = self._query(token, vin)
            batt  = data.get("getBatteryStatus") or {}
            odo   = data.get("getOdometerData") or {}
            soc      = batt.get("batteryChargeLevelPercentage")
            charging = str(batt.get("chargingStatus","")).upper() in ("CHARGING","CHARGING_AC","CHARGING_DC","CONNECTED_AC","CONNECTED_DC")
            odometer = (odo.get("odometerMeters") or 0) / 1000 or None
            return VehicleState(charging=charging, soc=soc, odometer=odometer)
        except Exception as e:
            return VehicleState(error=str(e))

    def test_connection(self) -> dict:
        try:
            token = self._auth()
            vin   = self._get_vin(token)
            return {"ok": True, "message": f"✅ Verbunden · VIN: {vin}"}
        except Exception as e:
            return {"ok": False, "message": f"❌ {e}"}

    @classmethod
    def get_config_fields(cls) -> list[dict]:
        return [
            {"id":"polestar_username", "label":"Polestar ID E-Mail",       "type":"text",     "placeholder":"email@example.com", "required":True},
            {"id":"polestar_password", "label":"Passwort",                  "type":"password", "placeholder":"",                  "required":True},
            {"id":"polestar_vin",      "label":"Fahrzeug VIN (optional)",   "type":"text",     "placeholder":"YV3...",            "required":False},
        ]


# ─────────────────────────────────────────────────────────────────────────────
# Audi (MyAudi / Audi Connect)
# ─────────────────────────────────────────────────────────────────────────────

class AudiProvider(BaseProvider):

    PROVIDER_ID   = "audi"
    PROVIDER_NAME = "Audi"
    CAPABILITIES  = ProviderCapabilities(
        charging_state = True,
        soc            = True,
        odometer       = True,
        charge_power   = False,
        location       = False,
        charge_type    = False,
        notes = [
            "MyAudi Connect API (inoffiziell) — für e-tron, Q4, A6/A8 TFSI e",
            "Ladeleistung und Standort nicht verfügbar",
            "Audi Q4 e-tron / ID-Plattform: alternativ VW Provider verwenden",
        ]
    )

    TOKEN_URL = "https://identity.vwgroup.io/oidc/v1/token"
    API_URL   = "https://msg.volkswagen.de/fs-car/bs/batterycharge/v1/Audi/DE/vehicles/{vin}/charger"
    ODO_URL   = "https://msg.volkswagen.de/fs-car/bs/tripstatistics/v1/Audi/DE/vehicles/{vin}/tripdata/longTerm?type=reset"

    CLIENT_ID = "09b6cbec-cd19-4589-82fd-363dfa8c24da@apps_vw-dilab_com"

    def _get_token(self) -> str:
        r = requests.post(
            self.TOKEN_URL,
            data={
                "grant_type": "password",
                "client_id":  self.CLIENT_ID,
                "username":   self.config.get("audi_username",""),
                "password":   self.config.get("audi_password",""),
                "scope":      "openid profile address email phone offline_access",
            },
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            timeout=15,
        )
        if r.status_code != 200:
            raise RuntimeError(f"Audi Login fehlgeschlagen (HTTP {r.status_code}) — Zugangsdaten prüfen")
        return r.json().get("access_token","")

    def _get_vin(self) -> str:
        vin = self.config.get("audi_vin","").strip()
        if not vin:
            raise RuntimeError("Bitte Fahrzeug-VIN in der Konfiguration eintragen")
        return vin

    def get_state(self) -> VehicleState:
        try:
            token   = self._get_token()
            vin     = self._get_vin()
            headers = {"Authorization": f"Bearer {token}",
                       "Accept":         "application/json"}
            r = requests.get(self.API_URL.format(vin=vin), headers=headers, timeout=15)
            r.raise_for_status()
            data     = r.json().get("charger", {})
            status   = data.get("status", {})
            soc      = status.get("batteryStatusData", {}).get("stateOfCharge", {}).get("content")
            chg_stat = status.get("chargingStatusData", {}).get("chargingState", {}).get("content","")
            charging = chg_stat.lower() in ("charging","charge")

            odo = None
            try:
                r2 = requests.get(self.ODO_URL.format(vin=vin), headers=headers, timeout=15)
                r2.raise_for_status()
                trips = r2.json().get("tripDataList",{}).get("tripData",[])
                if trips:
                    odo = trips[0].get("overallMileage")
            except Exception:
                pass

            return VehicleState(charging=charging, soc=soc, odometer=odo)
        except Exception as e:
            return VehicleState(error=str(e))

    def test_connection(self) -> dict:
        try:
            token = self._get_token()
            vin   = self._get_vin()
            headers = {"Authorization": f"Bearer {token}", "Accept": "application/json"}
            r = requests.get(self.API_URL.format(vin=vin), headers=headers, timeout=15)
            r.raise_for_status()
            return {"ok": True, "message": f"✅ Verbunden · VIN: {vin}"}
        except Exception as e:
            return {"ok": False, "message": f"❌ {e}"}

    @classmethod
    def get_config_fields(cls) -> list[dict]:
        return [
            {"id":"audi_username", "label":"MyAudi E-Mail",            "type":"text",     "placeholder":"email@example.com", "required":True},
            {"id":"audi_password", "label":"Passwort",                  "type":"password", "placeholder":"",                  "required":True},
            {"id":"audi_vin",      "label":"Fahrzeug VIN",              "type":"text",     "placeholder":"WAU...",            "required":True,
             "hint":"VIN findest du in der MyAudi App unter Fahrzeugdaten"},
        ]
