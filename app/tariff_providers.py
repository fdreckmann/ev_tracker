"""
Tariff Provider System — fixed and dynamic electricity tariff providers.
Providers: fixed, octopus, tibber, generic_http
"""
import logging
from datetime import datetime, timezone
from typing import Optional

log = logging.getLogger(__name__)

_MISSING_MSG = "Kein API-Token/Key konfiguriert"


class BaseTariffProvider:
    """Abstract base for all tariff providers."""
    provider_id = "base"
    label = "Unbekannt"

    def __init__(self, config: dict):
        self.config = config

    def get_price_at(self, timestamp: datetime, location: Optional[str] = None) -> Optional[float]:
        raise NotImplementedError

    def get_prices_for_range(self, start: datetime, end: datetime) -> list:
        """Return list of {"ts": ISO, "price": float, "currency": str}."""
        raise NotImplementedError

    def test_connection(self) -> dict:
        raise NotImplementedError

    @classmethod
    def get_config_fields(cls) -> list:
        return []

    def _fallback(self) -> float:
        return float(self.config.get("tariff_fallback_price",
                     self.config.get("price_per_kwh_home", 0.30)))

    def get_average_price(self, start: datetime, end: datetime) -> Optional[float]:
        """Time-weighted average price over a session range."""
        try:
            prices = self.get_prices_for_range(start, end)
            if not prices:
                raise ValueError("no prices")
            if len(prices) == 1:
                return prices[0]["price"]
            # Sort by timestamp for time-weighted calculation
            sorted_prices = sorted(prices, key=lambda p: p.get("ts", ""))
            total_weight = 0.0
            weighted_sum = 0.0
            for i, slot in enumerate(sorted_prices):
                try:
                    slot_start = datetime.fromisoformat(slot["ts"].replace("Z", "+00:00"))
                except Exception:
                    continue
                slot_end = (
                    datetime.fromisoformat(sorted_prices[i + 1]["ts"].replace("Z", "+00:00"))
                    if i + 1 < len(sorted_prices) else end
                )
                eff_start = max(slot_start, start) if start else slot_start
                eff_end   = min(slot_end,   end)   if end   else slot_end
                if eff_end <= eff_start:
                    continue
                weight = (eff_end - eff_start).total_seconds()
                weighted_sum += slot["price"] * weight
                total_weight += weight
            if total_weight > 0:
                return weighted_sum / total_weight
            return sum(p["price"] for p in prices) / len(prices)
        except Exception:
            pass
        try:
            return self.get_price_at(start or datetime.now(timezone.utc))
        except Exception:
            return None


class FixedPriceTariffProvider(BaseTariffProvider):
    provider_id = "fixed"
    label = "Fester Preis"

    def get_price_at(self, timestamp, location=None):
        if location == "home":
            return float(self.config.get("price_per_kwh_home", 0.30))
        return float(self.config.get("price_per_kwh_dc", 0.75)) if location in ("extern","external","dc") \
               else float(self.config.get("price_per_kwh_ac", 0.45))

    def get_prices_for_range(self, start, end):
        price = self.get_price_at(start)
        return [{"ts": start.isoformat() if start else "", "price": price, "currency": "EUR"}]

    def test_connection(self):
        price = self.get_price_at(datetime.now(timezone.utc), "home")
        return {"ok": True, "message": f"Fester Preis Zuhause: {price:.4f} EUR/kWh", "sample_price": price}

    @classmethod
    def get_config_fields(cls):
        return [
            {"key": "price_per_kwh_home", "label": "Preis Zuhause (EUR/kWh)",   "type": "number"},
            {"key": "price_per_kwh_ac",   "label": "Preis AC Extern (EUR/kWh)", "type": "number"},
            {"key": "price_per_kwh_dc",   "label": "Preis DC Extern (EUR/kWh)", "type": "number"},
        ]


class OctopusTariffProvider(BaseTariffProvider):
    provider_id = "octopus"
    label = "Octopus Energy"
    BASE_URL = "https://api.octopus.energy/v1"

    def _auth_headers(self):
        import base64
        api_key = self.config.get("octopus_api_key", "")
        if not api_key:
            return {}
        token = base64.b64encode(f"{api_key}:".encode()).decode()
        return {"Authorization": f"Basic {token}"}

    def get_price_at(self, timestamp, location=None):
        prices = self.get_prices_for_range(timestamp, timestamp)
        return prices[0]["price"] if prices else self._fallback()

    def get_prices_for_range(self, start, end):
        import requests
        tariff  = self.config.get("octopus_tariff_code", "")
        product = self.config.get("octopus_product_code", "")
        if not tariff or not product:
            return []
        url = f"{self.BASE_URL}/products/{product}/electricity-tariffs/{tariff}/half-hour-unit-rates/"
        factor = float(self.config.get("octopus_gbp_eur_factor", 1.17)) / 100.0
        try:
            params = {}
            if start:
                params["period_from"] = start.strftime("%Y-%m-%dT%H:%M:%SZ")
            if end:
                params["period_to"]   = end.strftime("%Y-%m-%dT%H:%M:%SZ")
            r = requests.get(url, headers=self._auth_headers(), params=params, timeout=10)
            r.raise_for_status()
            results = r.json().get("results", [])
            return [{"ts": item.get("valid_from", ""),
                     "price": round(item.get("value_inc_vat", 0) * factor, 5),
                     "currency": "EUR"} for item in results]
        except Exception as e:
            log.warning("Octopus API error: %s", e)
            return []

    def test_connection(self):
        import requests
        if not self.config.get("octopus_api_key", ""):
            return {"ok": False, "message": _MISSING_MSG}
        try:
            r = requests.get(f"{self.BASE_URL}/products/", headers=self._auth_headers(), timeout=10)
            r.raise_for_status()
        except Exception as e:
            return {"ok": False, "message": str(e)}
        tariff = self.config.get("octopus_tariff_code", "")
        if not tariff:
            return {"ok": True, "message": "API erreichbar. Bitte Tariff-Code konfigurieren.", "sample_price": None}
        prices = self.get_prices_for_range(datetime.now(timezone.utc), datetime.now(timezone.utc))
        sp = prices[0]["price"] if prices else None
        msg = f"Verbindung OK. Preis: {sp:.5f} EUR/kWh" if sp else "Verbindung OK, keine aktuellen Preisdaten"
        return {"ok": True, "message": msg, "sample_price": sp}

    @classmethod
    def get_config_fields(cls):
        return [
            {"key": "octopus_api_key",       "label": "API Key",              "type": "password"},
            {"key": "octopus_account_id",    "label": "Account ID",           "type": "text"},
            {"key": "octopus_product_code",  "label": "Produkt-Code",         "type": "text"},
            {"key": "octopus_tariff_code",   "label": "Tariff-Code",          "type": "text",
             "help": "z. B. E-1R-AGILE-24-10-01-C"},
            {"key": "octopus_gbp_eur_factor","label": "GBP→EUR Kurs-Faktor",  "type": "number", "default": 1.17},
            {"key": "tariff_fallback_price", "label": "Fallback-Preis (EUR/kWh)", "type": "number"},
        ]


class TibberTariffProvider(BaseTariffProvider):
    provider_id = "tibber"
    label = "Tibber"
    API_URL = "https://api.tibber.com/v1-beta/gql"

    def _headers(self):
        return {"Authorization": f"Bearer {self.config.get('tibber_token', '')}",
                "Content-Type": "application/json"}

    def _fetch_prices(self):
        import requests, json as _json
        if not self.config.get("tibber_token", ""):
            return []
        query = """{ viewer { homes { currentSubscription { priceInfo {
            today { total currency startsAt }
            tomorrow { total currency startsAt }
        } } } } }"""
        r = requests.post(self.API_URL, headers=self._headers(),
                          data=_json.dumps({"query": query}), timeout=10)
        r.raise_for_status()
        homes = r.json().get("data", {}).get("viewer", {}).get("homes", [])
        if not homes:
            return []
        pi = homes[0].get("currentSubscription", {}).get("priceInfo", {})
        return pi.get("today", []) + pi.get("tomorrow", [])

    def get_price_at(self, timestamp, location=None):
        try:
            all_p = self._fetch_prices()
            ts_cmp = timestamp.strftime("%Y-%m-%dT%H")
            for p in all_p:
                if p.get("startsAt", "")[:13] == ts_cmp:
                    return float(p["total"])
            if all_p:
                return float(all_p[0]["total"])
        except Exception as e:
            log.warning("Tibber get_price_at: %s", e)
        return self._fallback()

    def get_prices_for_range(self, start, end):
        try:
            all_p = self._fetch_prices()
            s_cmp = start.strftime("%Y-%m-%dT%H") if start else ""
            e_cmp = end.strftime("%Y-%m-%dT%H")   if end   else "9999"
            out   = [{"ts": p["startsAt"], "price": float(p["total"]),
                      "currency": p.get("currency","EUR")}
                     for p in all_p
                     if s_cmp <= p.get("startsAt","")[:13] <= e_cmp]
            return out or (all_p[:1] and [{"ts": all_p[0]["startsAt"],
                                           "price": float(all_p[0]["total"]),
                                           "currency": "EUR"}] or [])
        except Exception as e:
            log.warning("Tibber get_prices_for_range: %s", e)
            return []

    def test_connection(self):
        import requests, json as _json
        if not self.config.get("tibber_token", ""):
            return {"ok": False, "message": _MISSING_MSG}
        query = '{ viewer { name homes { address { address1 } currentSubscription { priceInfo { current { total currency } } } } } }'
        try:
            r = requests.post(self.API_URL, headers=self._headers(),
                              data=_json.dumps({"query": query}), timeout=10)
            r.raise_for_status()
            viewer = r.json().get("data", {}).get("viewer", {})
            name  = viewer.get("name", "?")
            homes = viewer.get("homes", [])
            sp = None
            if homes:
                cur = homes[0].get("currentSubscription", {}).get("priceInfo", {}).get("current", {})
                sp = cur.get("total")
            msg = (f"Tibber-Konto: {name}. Preis: {sp} {cur.get('currency','EUR')}/kWh"
                   if sp else f"Verbindung OK (Konto: {name})")
            return {"ok": True, "message": msg, "sample_price": sp}
        except Exception as e:
            return {"ok": False, "message": str(e)}

    @classmethod
    def get_config_fields(cls):
        return [
            {"key": "tibber_token",          "label": "API Token",               "type": "password"},
            {"key": "tariff_fallback_price", "label": "Fallback-Preis (EUR/kWh)", "type": "number"},
        ]


class GenericHttpTariffProvider(BaseTariffProvider):
    provider_id = "generic_http"
    label = "Generic HTTP"

    def _fetch_raw(self) -> Optional[float]:
        import requests
        url = self.config.get("generic_tariff_url", "")
        if not url:
            return None
        headers   = self.config.get("generic_tariff_headers", {})
        json_path = self.config.get("generic_tariff_json_path", "price")
        unit      = self.config.get("generic_tariff_unit", "EUR/kWh")
        factor    = float(self.config.get("generic_tariff_factor", 1.0))
        r = requests.get(url, headers=headers, timeout=10)
        r.raise_for_status()
        val = r.json()
        for key in json_path.split("."):
            if isinstance(val, dict):
                val = val.get(key)
            elif isinstance(val, list) and key.isdigit():
                val = val[int(key)]
            else:
                val = None
            if val is None:
                return None
        raw = float(val) * factor
        if unit in ("Wh", "EUR/Wh"):
            raw /= 1000
        elif unit == "p/kWh":
            raw /= 100
        return raw

    def get_price_at(self, timestamp, location=None):
        try:
            v = self._fetch_raw()
            return v if v is not None else self._fallback()
        except Exception:
            return self._fallback()

    def get_prices_for_range(self, start, end):
        try:
            v = self._fetch_raw()
            if v is not None:
                return [{"ts": start.isoformat() if start else "", "price": v, "currency": "EUR"}]
        except Exception:
            pass
        return []

    def test_connection(self):
        url = self.config.get("generic_tariff_url", "")
        if not url:
            return {"ok": False, "message": "Keine URL konfiguriert"}
        try:
            v = self._fetch_raw()
            if v is not None:
                return {"ok": True, "message": f"Preis: {v:.5f} EUR/kWh", "sample_price": v}
            return {"ok": False, "message": "URL erreichbar, aber kein Preis extrahiert (JSON-Pfad prüfen)"}
        except Exception as e:
            return {"ok": False, "message": str(e)}

    @classmethod
    def get_config_fields(cls):
        return [
            {"key": "generic_tariff_url",      "label": "URL",               "type": "text"},
            {"key": "generic_tariff_headers",   "label": "Headers (JSON)",    "type": "json"},
            {"key": "generic_tariff_json_path", "label": "JSON-Pfad",         "type": "text",
             "help": "z. B. data.current_price oder result.0.price"},
            {"key": "generic_tariff_unit",      "label": "Einheit",           "type": "select",
             "options": ["EUR/kWh", "p/kWh", "Wh", "EUR/Wh"]},
            {"key": "generic_tariff_factor",    "label": "Multiplikator",     "type": "number", "default": 1.0},
            {"key": "tariff_fallback_price",    "label": "Fallback (EUR/kWh)", "type": "number"},
        ]


class HomeAssistantTariffProvider(BaseTariffProvider):
    provider_id = "home_assistant"
    label = "Home Assistant Sensor"

    def _ha_url(self):
        return (self.config.get("tariff_ha_url") or self.config.get("ha_url", "")).rstrip("/")

    def _ha_token(self):
        return self.config.get("tariff_ha_token") or self.config.get("ha_token", "")

    def _ha_entity(self):
        return self.config.get("tariff_ha_entity", "")

    def _fetch_current(self) -> Optional[float]:
        import requests
        url, token, entity = self._ha_url(), self._ha_token(), self._ha_entity()
        if not url or not token or not entity:
            return None
        r = requests.get(f"{url}/api/states/{entity}",
                         headers={"Authorization": f"Bearer {token}",
                                  "Content-Type": "application/json"}, timeout=8)
        r.raise_for_status()
        state = r.json().get("state")
        if state in (None, "unavailable", "unknown", ""):
            return None
        return float(state)

    def get_price_at(self, timestamp, location=None):
        try:
            v = self._fetch_current()
            return v if v is not None else self._fallback()
        except Exception:
            return self._fallback()

    def get_prices_for_range(self, start, end):
        import requests
        url, token, entity = self._ha_url(), self._ha_token(), self._ha_entity()
        if url and token and entity:
            try:
                hist_url = f"{url}/api/history/period/{start.strftime('%Y-%m-%dT%H:%M:%S')}"
                r = requests.get(hist_url, headers={"Authorization": f"Bearer {token}"},
                                 params={"filter_entity_id": entity,
                                         "end_time": end.strftime('%Y-%m-%dT%H:%M:%S')},
                                 timeout=10)
                r.raise_for_status()
                history = r.json()
                if history and isinstance(history, list) and history[0]:
                    result = []
                    for entry in history[0]:
                        try:
                            result.append({"ts": entry.get("last_changed", ""),
                                           "price": float(entry["state"]), "currency": "EUR"})
                        except (ValueError, TypeError):
                            continue
                    if result:
                        return result
            except Exception as e:
                log.debug("HA history fetch failed: %s", e)
        try:
            v = self._fetch_current()
            if v is not None:
                return [{"ts": start.isoformat() if start else "", "price": v, "currency": "EUR"}]
        except Exception:
            pass
        return []

    def test_connection(self):
        entity = self._ha_entity()
        if not entity:
            return {"ok": False, "message": "Kein HA-Entity konfiguriert (tariff_ha_entity)"}
        if not self._ha_url() or not self._ha_token():
            return {"ok": False, "message": "HA-URL oder Token nicht konfiguriert"}
        try:
            v = self._fetch_current()
            if v is not None:
                return {"ok": True, "message": f"HA Sensor '{entity}': {v:.5f} EUR/kWh", "sample_price": v}
            return {"ok": False, "message": f"Sensor '{entity}' hat keinen gültigen Wert (unavailable?)"}
        except Exception as e:
            return {"ok": False, "message": str(e)}

    @classmethod
    def get_config_fields(cls):
        return [
            {"key": "tariff_ha_url",    "label": "HA URL (leer = aus Verbindungs-Konfig)", "type": "text",
             "help": "z. B. http://homeassistant.local:8123"},
            {"key": "tariff_ha_token",  "label": "Long-Lived Access Token", "type": "password"},
            {"key": "tariff_ha_entity", "label": "Entity ID (Preis-Sensor)", "type": "text",
             "help": "z. B. sensor.electricity_price_current"},
            {"key": "tariff_fallback_price", "label": "Fallback-Preis (EUR/kWh)", "type": "number"},
        ]


class EvccTariffProvider(BaseTariffProvider):
    provider_id = "evcc"
    label = "EVCC"

    def _fetch_grid_price(self) -> Optional[float]:
        import requests
        url = (self.config.get("tariff_evcc_url") or "").rstrip("/")
        if not url:
            return None
        if not url.startswith("http"):
            url = f"http://{url}"
        r = requests.get(f"{url}/api/state", timeout=8)
        r.raise_for_status()
        result = r.json().get("result", r.json())
        for field in ["tariffGrid", "gridPrice", "tariff"]:
            try:
                val = result
                for key in field.split("."):
                    val = val[key]
                return float(val)
            except (KeyError, TypeError, ValueError):
                continue
        return None

    def get_price_at(self, timestamp, location=None):
        try:
            v = self._fetch_grid_price()
            return v if v is not None else self._fallback()
        except Exception:
            return self._fallback()

    def get_prices_for_range(self, start, end):
        try:
            v = self._fetch_grid_price()
            if v is not None:
                return [{"ts": start.isoformat() if start else "", "price": v, "currency": "EUR"}]
        except Exception:
            pass
        return []

    def test_connection(self):
        url = self.config.get("tariff_evcc_url", "")
        if not url:
            return {"ok": False, "message": "Keine EVCC-URL konfiguriert (tariff_evcc_url)"}
        try:
            v = self._fetch_grid_price()
            if v is not None:
                return {"ok": True, "message": f"EVCC Netz-Tarif: {v:.5f} EUR/kWh", "sample_price": v}
            return {"ok": False, "message": "EVCC erreichbar, aber kein Strompreis in /api/state (tariffGrid/gridPrice)"}
        except Exception as e:
            return {"ok": False, "message": str(e)}

    @classmethod
    def get_config_fields(cls):
        return [
            {"key": "tariff_evcc_url",  "label": "EVCC URL", "type": "text",
             "help": "z. B. http://evcc.local oder http://192.168.1.100:7070"},
            {"key": "tariff_fallback_price", "label": "Fallback-Preis (EUR/kWh)", "type": "number"},
        ]


TARIFF_PROVIDERS: dict = {
    "fixed":          FixedPriceTariffProvider,
    "octopus":        OctopusTariffProvider,
    "tibber":         TibberTariffProvider,
    "generic_http":   GenericHttpTariffProvider,
    "home_assistant": HomeAssistantTariffProvider,
    "evcc":           EvccTariffProvider,
}


def get_tariff_provider(config: dict) -> BaseTariffProvider:
    """Return the configured tariff provider instance."""
    cls = TARIFF_PROVIDERS.get(config.get("tariff_provider", "fixed"), FixedPriceTariffProvider)
    return cls(config)


def get_session_price(session: dict, config: dict) -> float:
    """Compute price/kWh for a session using the configured tariff provider."""
    if session.get("cost_manual") and session.get("price_per_kwh"):
        return float(session["price_per_kwh"])
    try:
        provider = get_tariff_provider(config)
        start_ts = session.get("start_ts")
        end_ts   = session.get("end_ts")
        location = session.get("location", "home")
        if start_ts and end_ts:
            s = datetime.fromisoformat(start_ts.replace("Z", "+00:00"))
            e = datetime.fromisoformat(end_ts.replace("Z", "+00:00"))
            price = provider.get_average_price(s, e)
            if price is not None:
                return price
        price = provider.get_price_at(
            datetime.fromisoformat(start_ts.replace("Z", "+00:00")) if start_ts
            else datetime.now(timezone.utc),
            location=location)
        return price if price is not None else float(
            config.get("tariff_fallback_price", config.get("price_per_kwh_home", 0.30)))
    except Exception:
        return float(config.get("tariff_fallback_price", config.get("price_per_kwh_home", 0.30)))
