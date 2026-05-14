"""Meter provider implementations for EV Tracker."""
from __future__ import annotations
import logging, time
from dataclasses import dataclass, field
from typing import Optional, Any

log = logging.getLogger(__name__)

@dataclass
class MeterResult:
    value: Optional[float] = None   # kWh total reading
    ok: bool = False
    source: str = ""                # provider key
    debug: list[str] = field(default_factory=list)
    error: Optional[str] = None

def build_base_url(cfg: dict, default_scheme: str = "http") -> str:
    ip = cfg.get("meter_device_ip", "").strip()
    scheme = cfg.get("meter_device_scheme", default_scheme)
    port = cfg.get("meter_device_port", "")
    if not ip:
        return ""
    if ip.startswith("http://") or ip.startswith("https://"):
        return ip.rstrip("/")
    if port:
        return f"{scheme}://{ip}:{port}"
    return f"{scheme}://{ip}"

def normalize_energy_value(raw: Any, unit: str = "kwh", factor: float = 1.0) -> Optional[float]:
    """Convert raw value to kWh. unit: 'kwh'|'wh'|'mwh'. factor applied after unit conversion."""
    try:
        v = float(raw)
        if unit == "wh":
            v /= 1000.0
        elif unit == "mwh":
            v *= 1000.0
        return round(v * factor, 3)
    except (TypeError, ValueError):
        return None

def _get_json(url: str, timeout: int = 8, auth=None, verify_ssl: bool = True, debug: list = None) -> Optional[dict]:
    import requests
    if debug is not None:
        debug.append(f"GET {url}")
    try:
        kwargs = {"timeout": timeout}
        if auth:
            kwargs["auth"] = auth
        if not verify_ssl:
            kwargs["verify"] = False
        r = requests.get(url, **kwargs)
        if debug is not None:
            debug.append(f"  → HTTP {r.status_code}")
        r.raise_for_status()
        return r.json()
    except Exception as e:
        if debug is not None:
            debug.append(f"  → ERROR: {e}")
        return None

def _get_text(url: str, timeout: int = 8, auth=None, debug: list = None) -> Optional[str]:
    import requests
    if debug is not None:
        debug.append(f"GET {url}")
    try:
        kwargs = {"timeout": timeout}
        if auth:
            kwargs["auth"] = auth
        r = requests.get(url, **kwargs)
        if debug is not None:
            debug.append(f"  → HTTP {r.status_code}")
        r.raise_for_status()
        return r.text.strip()
    except Exception as e:
        if debug is not None:
            debug.append(f"  → ERROR: {e}")
        return None

def _json_path(data: dict, path: str) -> Optional[Any]:
    """Traverse nested dict/list using dot notation. Keys with colons supported."""
    # path like "StatusSNS.ENERGY.Total" or "result:data.value"
    # colons are alternative separators for keys containing dots
    parts = path.replace(":", ".").split(".")
    cur = data
    for p in parts:
        if isinstance(cur, dict):
            cur = cur.get(p)
        elif isinstance(cur, list):
            try:
                cur = cur[int(p)]
            except (ValueError, IndexError):
                return None
        else:
            return None
        if cur is None:
            return None
    return cur


class BaseMeterProvider:
    SOURCE_KEY = "base"

    def __init__(self, cfg: dict):
        self.cfg = cfg
        self.timeout = int(cfg.get("meter_timeout_seconds", 8))
        self.verify_ssl = bool(cfg.get("meter_verify_ssl", True))
        self.base_url = build_base_url(cfg)

    def read(self) -> MeterResult:
        raise NotImplementedError

    def _result(self, value=None, debug=None, error=None):
        ok = value is not None
        return MeterResult(value=value, ok=ok, source=self.SOURCE_KEY,
                           debug=debug or [], error=error)


class NoneMeterProvider(BaseMeterProvider):
    SOURCE_KEY = "none"
    def read(self) -> MeterResult:
        return MeterResult(ok=False, source="none", debug=["Kein Zähler konfiguriert."])


class HaMeterProvider(BaseMeterProvider):
    SOURCE_KEY = "ha"

    def read(self) -> MeterResult:
        debug = []
        entity = self.cfg.get("meter_sensor", "").strip()
        if not entity:
            return self._result(error="Keine HA Entity ID konfiguriert", debug=debug)
        ha_url = self.cfg.get("ha_url", "").rstrip("/")
        token = self.cfg.get("ha_token", "")
        url = f"{ha_url}/api/states/{entity}"
        debug.append(f"GET {url}")
        import requests
        try:
            r = requests.get(url, headers={"Authorization": f"Bearer {token}"},
                             timeout=self.timeout)
            debug.append(f"  → HTTP {r.status_code}")
            r.raise_for_status()
            data = r.json()
            state = data.get("state")
            debug.append(f"  → state={state}")
            val = normalize_energy_value(state)
            if val is None:
                return self._result(error=f"Ungültiger Wert: {state}", debug=debug)
            unit = data.get("attributes", {}).get("unit_of_measurement", "kWh")
            debug.append(f"  → unit={unit}, value={val}")
            # normalize if sensor reports Wh
            if unit.lower() == "wh":
                val = round(val / 1000, 3)
            return self._result(value=val, debug=debug)
        except Exception as e:
            debug.append(f"  → EXCEPTION: {e}")
            return self._result(error=str(e), debug=debug)


class ShellyMeterProvider(BaseMeterProvider):
    """Supports Gen1 (EM, 1PM, 3EM), Gen2/Plus/Pro (RPC API), Gen3."""
    SOURCE_KEY = "shelly"

    def read(self) -> MeterResult:
        debug = []
        if not self.base_url:
            return self._result(error="Keine IP konfiguriert", debug=debug)

        channel = int(self.cfg.get("meter_channel", 0))
        phase_mode = self.cfg.get("meter_phase_mode", "total")  # total|a|b|c|sum
        username = self.cfg.get("meter_username", "")
        password = self.cfg.get("meter_password", "")
        auth = (username, password) if username and password else None

        # --- Try Gen2/Plus/Pro RPC API first ---
        debug.append("=== Versuche Gen2/Plus/Pro RPC API ===")
        # Try Shelly.GetStatus (Gen2)
        for rpc_path in ["/rpc/Shelly.GetStatus", "/rpc/EM.GetStatus?id=0", "/rpc/Switch.GetStatus?id=0"]:
            data = _get_json(f"{self.base_url}{rpc_path}", timeout=self.timeout, auth=auth, debug=debug)
            if data is None:
                continue
            # Gen2 EM: data["em:0"]["total_act_energy"] in Wh
            em_key = f"em:{channel}"
            if em_key in data:
                em = data[em_key]
                debug.append(f"  → Gen2 EM key '{em_key}' found")
                if phase_mode == "total":
                    raw = em.get("total_act_energy") or em.get("a_act_energy", 0) + em.get("b_act_energy", 0) + em.get("c_act_energy", 0)
                elif phase_mode == "a":
                    raw = em.get("a_act_energy")
                elif phase_mode == "b":
                    raw = em.get("b_act_energy")
                elif phase_mode == "c":
                    raw = em.get("c_act_energy")
                else:
                    raw = em.get("total_act_energy")
                if raw is not None:
                    val = normalize_energy_value(raw, unit="wh")
                    debug.append(f"  → raw={raw} Wh → {val} kWh")
                    return self._result(value=val, debug=debug)
            # Gen2 switch: data["switch:0"]["aenergy"]["total"] in Wh
            sw_key = f"switch:{channel}"
            if sw_key in data:
                sw = data[sw_key]
                debug.append(f"  → Gen2 Switch key '{sw_key}' found")
                aen = sw.get("aenergy", {})
                raw = aen.get("total")
                if raw is not None:
                    val = normalize_energy_value(raw, unit="wh")
                    debug.append(f"  → raw={raw} Wh → {val} kWh")
                    return self._result(value=val, debug=debug)
            # If data has "switch:0" at top level
            if "aenergy" in data:
                raw = data["aenergy"].get("total")
                if raw is not None:
                    val = normalize_energy_value(raw, unit="wh")
                    debug.append(f"  → Gen2 aenergy.total={raw} Wh → {val} kWh")
                    return self._result(value=val, debug=debug)
            # Try result wrapper (some firmware versions)
            if "result" in data:
                result = data["result"]
                if isinstance(result, dict):
                    for key in [f"em:{channel}", f"switch:{channel}"]:
                        if key in result:
                            sub = result[key]
                            raw = sub.get("total_act_energy") or (sub.get("aenergy") or {}).get("total")
                            if raw is not None:
                                val = normalize_energy_value(raw, unit="wh")
                                return self._result(value=val, debug=debug)

        # --- Try Gen1 API ---
        debug.append("=== Versuche Gen1 API ===")
        gen1_endpoints = [
            f"/emeter/{channel}",   # Shelly EM, 3EM
            f"/meter/{channel}",    # Shelly 1PM, 2.5
            "/status",              # fallback: full status
        ]
        for ep in gen1_endpoints:
            data = _get_json(f"{self.base_url}{ep}", timeout=self.timeout, auth=auth, debug=debug)
            if data is None:
                continue
            # /emeter/N: {"total": ..., "power": ...} — total in Wh
            if "total" in data:
                raw = data["total"]
                debug.append(f"  → Gen1 {ep}: total={raw} Wh")
                val = normalize_energy_value(raw, unit="wh")
                return self._result(value=val, debug=debug)
            # /status may have emmeters or meters list
            if "emmeters" in data:
                emmeters = data["emmeters"]
                if channel < len(emmeters):
                    raw = emmeters[channel].get("total")
                    if raw is not None:
                        val = normalize_energy_value(raw, unit="wh")
                        debug.append(f"  → Gen1 status.emmeters[{channel}].total={raw} Wh → {val} kWh")
                        return self._result(value=val, debug=debug)
            if "meters" in data:
                meters = data["meters"]
                if channel < len(meters):
                    raw = meters[channel].get("total")
                    if raw is not None:
                        val = normalize_energy_value(raw, unit="wh")
                        debug.append(f"  → Gen1 status.meters[{channel}].total={raw} Wh → {val} kWh")
                        return self._result(value=val, debug=debug)

        return self._result(error="Kein Zählerstand gefunden (alle Endpunkte fehlgeschlagen)", debug=debug)


class TasmotaMeterProvider(BaseMeterProvider):
    SOURCE_KEY = "tasmota"

    def read(self) -> MeterResult:
        debug = []
        if not self.base_url:
            return self._result(error="Keine IP konfiguriert", debug=debug)

        username = self.cfg.get("meter_username", "")
        password = self.cfg.get("meter_password", "")
        auth = (username, password) if username and password else None
        custom_path = self.cfg.get("meter_json_path", "").strip()  # e.g. "StatusSNS.ENERGY.Total"

        endpoints = [
            "/cm?cmnd=Status%208",
            "/cm?cmnd=StatusSNS",
            "/cm?cmnd=Status%200",
            "/cm?cmnd=EnergyTotal",
            "/cm?user={user}&password={pass}&cmnd=Status%208",
        ]

        for ep_tpl in endpoints:
            ep = ep_tpl.replace("{user}", username).replace("{pass}", password)
            data = _get_json(f"{self.base_url}{ep}", timeout=self.timeout, auth=auth, debug=debug)
            if data is None:
                continue

            # If user specified a custom JSON path, try it first
            if custom_path:
                val = _json_path(data, custom_path)
                if val is not None:
                    kwh = normalize_energy_value(val)
                    debug.append(f"  → custom path '{custom_path}' = {val} → {kwh} kWh")
                    if kwh is not None:
                        return self._result(value=kwh, debug=debug)

            # Try common paths
            paths_to_try = [
                "StatusSNS.ENERGY.Total",
                "StatusSNS.ENERGY.TotalStartTime",  # skip
                "ENERGY.Total",
                "StatusSNS.SML.Total_in",
                "StatusSNS.SML.Energy",
                "Total",
            ]
            for path in paths_to_try:
                if "TotalStartTime" in path:
                    continue
                val = _json_path(data, path)
                if val is not None:
                    kwh = normalize_energy_value(val)
                    if kwh is not None and kwh > 0:
                        debug.append(f"  → path '{path}' = {val} → {kwh} kWh")
                        return self._result(value=kwh, debug=debug)

        return self._result(error="Kein Energiewert gefunden", debug=debug)


class GoEMeterProvider(BaseMeterProvider):
    SOURCE_KEY = "go_e"

    def read(self) -> MeterResult:
        debug = []
        if not self.base_url:
            return self._result(error="Keine IP konfiguriert", debug=debug)
        for path in ("/api/status", "/status"):
            data = _get_json(f"{self.base_url}{path}", timeout=self.timeout, debug=debug)
            if data is None:
                continue
            if "eto" in data:
                # v2: eto in 0.1 Wh
                val = round(data["eto"] / 10000, 3)
                debug.append(f"  → eto={data['eto']} → {val} kWh")
                return self._result(value=val, debug=debug)
            if "wh" in data:
                # v1 alternative
                val = round(float(data["wh"]) / 1000, 3)
                debug.append(f"  → wh={data['wh']} → {val} kWh")
                return self._result(value=val, debug=debug)
        return self._result(error="Kein Zählerstand (eto/wh) gefunden", debug=debug)


class OpenWBMeterProvider(BaseMeterProvider):
    SOURCE_KEY = "openwb"

    def read(self) -> MeterResult:
        debug = []
        if not self.base_url:
            return self._result(error="Keine IP konfiguriert", debug=debug)
        lp = int(self.cfg.get("meter_openwb_lp", 1))
        paths = [
            f"/openWB/ramdisk/lp/{lp}/llkwh",
            f"/openWB/ramdisk/lp/{lp}/energyImport",
            "/openWB/ramdisk/llkwh",
            "/openWB/ramdisk/evsoc",
        ]
        for path in paths:
            txt = _get_text(f"{self.base_url}{path}", timeout=self.timeout, debug=debug)
            if txt is not None:
                try:
                    val = round(float(txt), 3)
                    debug.append(f"  → {path} = {val} kWh")
                    return self._result(value=val, debug=debug)
                except ValueError:
                    debug.append(f"  → {path} non-numeric: {txt!r}")
        return self._result(error="Kein Zählerstand gefunden", debug=debug)


class WarpMeterProvider(BaseMeterProvider):
    SOURCE_KEY = "warp"

    def read(self) -> MeterResult:
        debug = []
        if not self.base_url:
            return self._result(error="Keine IP konfiguriert", debug=debug)
        meter_idx = int(self.cfg.get("meter_warp_meter_index", 0))
        # WARP 3 supports multiple meters
        for path in [f"/meters/{meter_idx}/state", "/meter/state"]:
            data = _get_json(f"{self.base_url}{path}", timeout=self.timeout, debug=debug)
            if data is None:
                continue
            for key in ["energy_abs", "energyImport", "totalEnergy"]:
                if key in data:
                    val = round(float(data[key]), 3)
                    debug.append(f"  → {path}.{key} = {val} kWh")
                    return self._result(value=val, debug=debug)
        return self._result(error="Kein Zählerstand gefunden", debug=debug)


class EvccMeterProvider(BaseMeterProvider):
    SOURCE_KEY = "evcc"

    def read(self) -> MeterResult:
        debug = []
        if not self.base_url:
            return self._result(error="Keine IP konfiguriert", debug=debug)
        port = int(self.cfg.get("meter_evcc_port", 7070))
        lp_idx = int(self.cfg.get("meter_evcc_lp", 0))
        # Build URL with port override
        ip = self.cfg.get("meter_device_ip", "").strip()
        url = f"http://{ip}:{port}/api/state"
        data = _get_json(url, timeout=self.timeout, debug=debug)
        if data is None:
            return self._result(error="EVCC nicht erreichbar", debug=debug)
        result = data.get("result", data)
        lps = result.get("loadpoints", [])
        debug.append(f"  → {len(lps)} Loadpoints gefunden")
        if lp_idx >= len(lps):
            return self._result(error=f"Loadpoint {lp_idx} nicht vorhanden (nur {len(lps)})", debug=debug)
        lp = lps[lp_idx]
        v = lp.get("chargeTotalImport") or (lp.get("chargedEnergy", 0) / 1000)
        val = round(float(v), 3)
        debug.append(f"  → LP{lp_idx} chargeTotalImport={val} kWh")
        return self._result(value=val, debug=debug)


class WebastoMeterProvider(BaseMeterProvider):
    SOURCE_KEY = "webasto"

    def read(self) -> MeterResult:
        debug = []
        if not self.base_url:
            return self._result(error="Keine IP konfiguriert", debug=debug)
        for path in ["/api/1/status", "/api/v1.0/status", "/status"]:
            data = _get_json(f"{self.base_url}{path}", timeout=self.timeout, debug=debug)
            if data is None:
                continue
            for key in ["totalEnergy", "MeterReading", "meterEnergy"]:
                if key in data:
                    raw = data[key]
                    # Webasto returns Wh
                    val = round(float(raw) / 1000, 3)
                    debug.append(f"  → {path}.{key} = {raw} Wh → {val} kWh")
                    return self._result(value=val, debug=debug)
        return self._result(error="Kein Zählerstand gefunden", debug=debug)


class AlfenMeterProvider(BaseMeterProvider):
    SOURCE_KEY = "alfen"

    def read(self) -> MeterResult:
        debug = []
        if not self.base_url:
            return self._result(error="Keine IP konfiguriert", debug=debug)
        pw = self.cfg.get("meter_password") or self.cfg.get("meter_alfen_pass", "admin")
        auth = ("admin", pw)
        data = _get_json(f"{self.base_url}/api", timeout=self.timeout, auth=auth, debug=debug)
        if data is None:
            return self._result(error="Alfen API nicht erreichbar", debug=debug)
        for prop in data.get("data", []):
            pid = str(prop.get("id", ""))
            desc = str(prop.get("description", ""))
            if pid == "3EA00020" or "Total" in desc or "Meter" in desc:
                val = round(float(prop.get("value", 0)), 3)
                debug.append(f"  → prop {pid} ({desc}) = {val} kWh")
                return self._result(value=val, debug=debug)
        return self._result(error="Kein Zähler-Property gefunden", debug=debug)


class JuiceMeterProvider(BaseMeterProvider):
    SOURCE_KEY = "juice"

    def read(self) -> MeterResult:
        debug = []
        if not self.base_url:
            return self._result(error="Keine IP konfiguriert", debug=debug)
        data = _get_json(f"{self.base_url}/api/1.0.0/details", timeout=self.timeout, debug=debug)
        if data is None:
            return self._result(error="Juice API nicht erreichbar", debug=debug)
        for key in ["meter_total_kwh", "totalEnergy", "total_energy_kwh"]:
            if key in data:
                val = round(float(data[key]), 3)
                debug.append(f"  → {key} = {val} kWh")
                return self._result(value=val, debug=debug)
        return self._result(error="Kein Zählerstand gefunden", debug=debug)


class GenericHttpMeterProvider(BaseMeterProvider):
    SOURCE_KEY = "generic"

    def read(self) -> MeterResult:
        debug = []
        url = self.cfg.get("meter_generic_url", "").strip()
        if not url:
            # fall back to base_url
            url = self.base_url
        if not url:
            return self._result(error="Keine URL konfiguriert", debug=debug)

        json_path = self.cfg.get("meter_json_path", "").strip()
        unit = self.cfg.get("meter_value_unit", "kwh").lower()
        factor = float(self.cfg.get("meter_value_factor", 1.0))
        username = self.cfg.get("meter_username", "")
        password = self.cfg.get("meter_password", "")
        auth = (username, password) if username and password else None

        data = _get_json(url, timeout=self.timeout, auth=auth,
                         verify_ssl=self.verify_ssl, debug=debug)
        if data is None:
            # Try plain text
            txt = _get_text(url, timeout=self.timeout, auth=auth, debug=debug)
            if txt is not None:
                val = normalize_energy_value(txt, unit=unit, factor=factor)
                if val is not None:
                    debug.append(f"  → plain text {txt} → {val} kWh")
                    return self._result(value=val, debug=debug)
            return self._result(error="Anfrage fehlgeschlagen", debug=debug)

        if json_path:
            raw = _json_path(data, json_path)
            debug.append(f"  → path '{json_path}' = {raw!r}")
        else:
            # Try common single-value responses
            raw = None
            for k in ["value", "total", "energy", "kwh", "reading"]:
                if k in data:
                    raw = data[k]
                    debug.append(f"  → key '{k}' = {raw!r}")
                    break
            if raw is None and len(data) == 1:
                raw = list(data.values())[0]
                debug.append(f"  → single key = {raw!r}")

        if raw is None:
            return self._result(error=f"Kein Wert gefunden (Pfad: {json_path or 'auto'})", debug=debug)

        val = normalize_energy_value(raw, unit=unit, factor=factor)
        if val is None:
            return self._result(error=f"Wert nicht konvertierbar: {raw!r}", debug=debug)
        debug.append(f"  → {raw} {unit} × {factor} = {val} kWh")
        return self._result(value=val, debug=debug)


_PROVIDERS: dict[str, type[BaseMeterProvider]] = {
    "none":    NoneMeterProvider,
    "ha":      HaMeterProvider,
    "shelly":  ShellyMeterProvider,
    "tasmota": TasmotaMeterProvider,
    "go_e":    GoEMeterProvider,
    "openwb":  OpenWBMeterProvider,
    "warp":    WarpMeterProvider,
    "evcc":    EvccMeterProvider,
    "webasto": WebastoMeterProvider,
    "alfen":   AlfenMeterProvider,
    "juice":   JuiceMeterProvider,
    "generic": GenericHttpMeterProvider,
}


def read_meter(cfg: dict) -> MeterResult:
    """Main entry point. Returns MeterResult."""
    source = cfg.get("meter_source", "none")
    cls = _PROVIDERS.get(source, NoneMeterProvider)
    try:
        result = cls(cfg).read()
        result.source = source
        return result
    except Exception as e:
        log.warning("Meter read error (%s): %s", source, e)
        return MeterResult(ok=False, source=source, error=str(e),
                           debug=[f"EXCEPTION: {e}"])
