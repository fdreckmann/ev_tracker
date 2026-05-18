"""Meter provider implementations for EV Tracker."""
from __future__ import annotations
import logging, time, re
from dataclasses import dataclass, field
from typing import Optional, Any

log = logging.getLogger(__name__)

@dataclass
class MeterResult:
    value: Optional[float] = None   # kWh total reading
    ok: bool = False
    source: str = ""                # provider key
    endpoint: Optional[str] = None
    raw_value: Any = None
    unit: Optional[str] = None
    normalized_from: Optional[str] = None
    debug: list = field(default_factory=list)
    suggestions: list = field(default_factory=list)
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

def normalize_energy_value(raw: Any, unit: str = "auto", factor: float = 1.0) -> Optional[float]:
    """Convert raw value to kWh.
    unit: 'auto'|'kWh'|'Wh'|'MWh'|'mWh' (case-insensitive legacy: 'kwh'|'wh'|'mwh').
    factor applied after unit conversion.
    'auto': heuristic based on magnitude.
    """
    try:
        v = float(raw)
        unit_lower = unit.lower()
        if unit_lower == "auto":
            if v > 100000:
                # Probably Wh
                v = v / 1000.0
            elif v > 1000:
                # Could be kWh or Wh — interpret as kWh (no conversion)
                pass
            # else: < 1000, probably kWh already
        elif unit_lower in ("wh", "watt-hour", "watthour"):
            v /= 1000.0
        elif unit_lower in ("kwh", "kilowatt-hour", "kilowatthour"):
            pass  # already kWh
        elif unit_lower in ("mwh", "megawatt-hour", "megawatthour"):
            v *= 1000.0
        elif unit_lower in ("mwh_milli", "milli-wh", "milliwatthour"):
            v /= 1000000.0
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

def _json_path(data, path: str):
    """Navigate nested dict/list by dot-separated path. Colon is part of key name."""
    if not path or data is None:
        return None
    # Split only on "." — colons stay in key names
    parts = path.split(".")
    cur = data
    for part in parts:
        if cur is None:
            return None
        # Support array index notation: "emeters[0]" or pure numeric "0"
        m = re.match(r'^(.*?)\[(\d+)\]$', part)
        if m:
            key, idx = m.group(1), int(m.group(2))
            if key:
                cur = cur.get(key) if isinstance(cur, dict) else None
            if isinstance(cur, (list, tuple)):
                cur = cur[idx] if idx < len(cur) else None
            elif isinstance(cur, dict):
                cur = cur.get(str(idx))
            else:
                return None
        elif isinstance(cur, dict):
            if part.isdigit():
                # Check if key exists as string first
                if part in cur:
                    cur = cur.get(part)
                else:
                    return None
            else:
                cur = cur.get(part)
        elif isinstance(cur, (list, tuple)) and part.isdigit():
            idx = int(part)
            cur = cur[idx] if idx < len(cur) else None
        else:
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

    def _result(self, value=None, debug=None, error=None, endpoint=None,
                raw_value=None, unit=None, normalized_from=None, suggestions=None):
        return MeterResult(
            value=value,
            ok=value is not None,
            source=self.__class__.__name__,
            endpoint=endpoint,
            raw_value=raw_value,
            unit=unit,
            normalized_from=normalized_from,
            debug=debug or [],
            suggestions=suggestions or [],
            error=error,
        )


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
        phase_mode = self.cfg.get("meter_phase_mode", "total")  # total|a|b|c|sum_phases
        unit = self.cfg.get("meter_value_unit", "auto")
        username = self.cfg.get("meter_username", "")
        password = self.cfg.get("meter_password", "")
        auth = (username, password) if username and password else None
        custom_json_path = self.cfg.get("meter_json_path", "").strip()

        # --- If meter_json_path is set, try custom path first ---
        if custom_json_path:
            debug.append(f"=== Versuche meter_json_path='{custom_json_path}' ===")
            for ep in ["/rpc/Shelly.GetStatus", "/status"]:
                data = _get_json(f"{self.base_url}{ep}", timeout=self.timeout, auth=auth, debug=debug)
                if data is None:
                    continue
                raw = _json_path(data, custom_json_path)
                if raw is not None:
                    val = normalize_energy_value(raw, unit=unit)
                    if val is not None:
                        debug.append(f"  → custom path '{custom_json_path}' = {raw} → {val} kWh")
                        return self._result(value=val, debug=debug)
            debug.append(f"  → meter_json_path nicht gefunden, versuche Auto-Detection")

        # --- Try Gen2/Plus/Pro RPC API first ---
        debug.append("=== Versuche Gen2/Plus/Pro RPC API ===")

        # 1. GET /rpc/Shelly.GetStatus — try multiple field patterns
        dbg = debug
        _getstatus_url = f"{self.base_url}/rpc/Shelly.GetStatus"
        data = _get_json(_getstatus_url, timeout=self.timeout, auth=auth, debug=dbg)
        if data is not None:
            result = self._parse_gen2_status(data, channel, phase_mode, unit, dbg)
            if result is not None:
                return self._result(value=result, endpoint=_getstatus_url, debug=dbg)

        # 2. /rpc/Switch.GetStatus?id=0 and id=1
        import requests as _requests_shelly
        timeout = self.timeout
        base_url = self.base_url
        cfg = self.cfg
        for switch_id in [channel, 1 - channel]:
            url = f"{base_url}/rpc/Switch.GetStatus?id={switch_id}"
            try:
                r = _requests_shelly.get(url, timeout=timeout, auth=auth)
                if r.ok:
                    data = r.json()
                    aenergy = data.get("aenergy", {})
                    total = aenergy.get("total") if isinstance(aenergy, dict) else None
                    if total is not None:
                        return self._result(
                            value=normalize_energy_value(total, "Wh"),
                            endpoint=url,
                            raw_value=total,
                            unit="Wh",
                            normalized_from="aenergy.total",
                            debug=dbg + [f"Switch.GetStatus?id={switch_id}: aenergy.total={total}"]
                        )
            except Exception as e:
                dbg.append(f"Switch.GetStatus?id={switch_id}: {e}")

        # 3. /rpc/PM1.GetStatus?id=0
        url = f"{base_url}/rpc/PM1.GetStatus?id=0"
        try:
            r = _requests_shelly.get(url, timeout=timeout, auth=auth)
            if r.ok:
                data = r.json()
                aenergy = data.get("aenergy", {})
                total = aenergy.get("total") if isinstance(aenergy, dict) else None
                if total is not None:
                    return self._result(
                        value=normalize_energy_value(total, "Wh"),
                        endpoint=url,
                        raw_value=total,
                        unit="Wh",
                        normalized_from="aenergy.total",
                        debug=dbg + [f"PM1.GetStatus: aenergy.total={total}"]
                    )
        except Exception as e:
            dbg.append(f"PM1.GetStatus: {e}")

        # 4. /rpc/EMData.GetStatus?id=0 and id=1
        phase_mode = cfg.get("meter_phase_mode", "total")
        for em_id in [0, 1]:
            url = f"{base_url}/rpc/EMData.GetStatus?id={em_id}"
            try:
                r = _requests_shelly.get(url, timeout=timeout, auth=auth)
                if r.ok:
                    data = r.json()
                    if phase_mode == "sum_phases":
                        a = data.get("a_total_act_energy", 0) or 0
                        b = data.get("b_total_act_energy", 0) or 0
                        c = data.get("c_total_act_energy", 0) or 0
                        total = a + b + c
                        normalized_from = "a+b+c_total_act_energy"
                    elif phase_mode == "phase_a":
                        total = data.get("a_total_act_energy")
                        normalized_from = "a_total_act_energy"
                    elif phase_mode == "phase_b":
                        total = data.get("b_total_act_energy")
                        normalized_from = "b_total_act_energy"
                    elif phase_mode == "phase_c":
                        total = data.get("c_total_act_energy")
                        normalized_from = "c_total_act_energy"
                    else:
                        total = data.get("total_act")
                        normalized_from = "total_act"
                    if total is not None and total > 0:
                        return self._result(
                            value=normalize_energy_value(total, "Wh"),
                            endpoint=url,
                            raw_value=total,
                            unit="Wh",
                            normalized_from=normalized_from,
                            debug=dbg + [f"EMData.GetStatus?id={em_id}: {normalized_from}={total}"]
                        )
            except Exception as e:
                dbg.append(f"EMData.GetStatus?id={em_id}: {e}")

        # 5. /rpc/EM1Data.GetStatus?id=0
        url = f"{base_url}/rpc/EM1Data.GetStatus?id=0"
        try:
            r = _requests_shelly.get(url, timeout=timeout, auth=auth)
            if r.ok:
                data = r.json()
                total = data.get("total_act") or _json_path(data, "aenergy.total")
                if total is not None:
                    normalized_from = "total_act" if data.get("total_act") is not None else "aenergy.total"
                    return self._result(
                        value=normalize_energy_value(total, "Wh"),
                        endpoint=url,
                        raw_value=total,
                        unit="Wh",
                        normalized_from=normalized_from,
                        debug=dbg + [f"EM1Data.GetStatus: {normalized_from}={total}"]
                    )
        except Exception as e:
            dbg.append(f"EM1Data.GetStatus: {e}")

        # --- Try Gen1 API ---
        debug.append("=== Versuche Gen1 API ===")

        # 5. GET /emeter/0, /emeter/1, /emeter/2
        for em_ch in range(3):
            data = _get_json(f"{self.base_url}/emeter/{em_ch}", timeout=self.timeout, auth=auth, debug=debug)
            if data is not None and "total" in data:
                raw = data["total"]
                val = normalize_energy_value(raw, unit="Wh")
                debug.append(f"  → Gen1 /emeter/{em_ch}: total={raw} Wh → {val} kWh")
                return self._result(value=val, debug=debug)

        # 6. GET /meter/0 (in Wh)
        data = _get_json(f"{self.base_url}/meter/0", timeout=self.timeout, auth=auth, debug=debug)
        if data is not None and "total" in data:
            raw = data["total"]
            val = normalize_energy_value(raw, unit="Wh")
            debug.append(f"  → Gen1 /meter/0: total={raw} Wh → {val} kWh")
            return self._result(value=val, debug=debug)

        # 7. GET /status — try emeters array (NOT emmeters!)
        data = _get_json(f"{self.base_url}/status", timeout=self.timeout, auth=auth, debug=debug)
        if data is not None:
            # Try emeters[channel].total
            emeters = data.get("emeters")
            if isinstance(emeters, list) and channel < len(emeters):
                raw = emeters[channel].get("total")
                if raw is not None:
                    val = normalize_energy_value(raw, unit="Wh")
                    debug.append(f"  → Gen1 status.emeters[{channel}].total={raw} Wh → {val} kWh")
                    return self._result(value=val, debug=debug)
            # Also try meters list
            meters = data.get("meters")
            if isinstance(meters, list) and channel < len(meters):
                raw = meters[channel].get("total")
                if raw is not None:
                    val = normalize_energy_value(raw, unit="Wh")
                    debug.append(f"  → Gen1 status.meters[{channel}].total={raw} Wh → {val} kWh")
                    return self._result(value=val, debug=debug)

        return self._result(error="Kein Zählerstand gefunden (alle Endpunkte fehlgeschlagen)", debug=debug)

    def _parse_gen2_status(self, data: dict, channel: int, phase_mode: str, unit: str, debug: list) -> Optional[float]:
        """Parse Gen2 /rpc/Shelly.GetStatus response. Returns kWh or None."""

        # switch:0.aenergy.total, switch:1.aenergy.total
        for sw_ch in [0, 1]:
            sw_key = f"switch:{sw_ch}"
            if sw_key in data:
                sw = data[sw_key]
                aen = sw.get("aenergy", {}) if isinstance(sw, dict) else {}
                raw = aen.get("total") if isinstance(aen, dict) else None
                if raw is not None:
                    val = normalize_energy_value(raw, unit="Wh")
                    debug.append(f"  → Gen2 {sw_key}.aenergy.total={raw} Wh → {val} kWh")
                    return val

        # pm1:0.aenergy.total
        pm_key = f"pm1:{channel}"
        if pm_key in data:
            pm = data[pm_key]
            aen = pm.get("aenergy", {}) if isinstance(pm, dict) else {}
            raw = aen.get("total") if isinstance(aen, dict) else None
            if raw is not None:
                val = normalize_energy_value(raw, unit="Wh")
                debug.append(f"  → Gen2 {pm_key}.aenergy.total={raw} Wh → {val} kWh")
                return val

        # em:0.total_act (in Wh)
        em_key = f"em:{channel}"
        if em_key in data:
            em = data[em_key]
            if isinstance(em, dict):
                raw = em.get("total_act")
                if raw is not None:
                    val = normalize_energy_value(raw, unit="Wh")
                    debug.append(f"  → Gen2 {em_key}.total_act={raw} Wh → {val} kWh")
                    return val
                # Also check total_act_energy
                raw = em.get("total_act_energy")
                if raw is not None:
                    val = normalize_energy_value(raw, unit="Wh")
                    debug.append(f"  → Gen2 {em_key}.total_act_energy={raw} Wh → {val} kWh")
                    return val
                # Phase mode sum_phases
                if phase_mode == "sum_phases":
                    a = em.get("a_act_energy", 0) or 0
                    b = em.get("b_act_energy", 0) or 0
                    c = em.get("c_act_energy", 0) or 0
                    raw = a + b + c
                    val = normalize_energy_value(raw, unit="Wh")
                    debug.append(f"  → Gen2 {em_key} sum_phases={raw} Wh → {val} kWh")
                    return val

        # emdata:0.total_act — requires separate call to /rpc/EMData.GetStatus?id=0
        # (handled separately, not from GetStatus)

        # If data has aenergy at top level (e.g. direct Switch.GetStatus response used here)
        if "aenergy" in data:
            raw = data["aenergy"].get("total") if isinstance(data.get("aenergy"), dict) else None
            if raw is not None:
                val = normalize_energy_value(raw, unit="Wh")
                debug.append(f"  → Gen2 top-level aenergy.total={raw} Wh → {val} kWh")
                return val

        return None


class TasmotaMeterProvider(BaseMeterProvider):
    SOURCE_KEY = "tasmota"

    def read(self) -> MeterResult:
        debug = []
        if not self.base_url:
            return self._result(error="Keine IP konfiguriert", debug=debug)

        username = self.cfg.get("meter_username", "")
        password = self.cfg.get("meter_password", "")
        # Build auth — use Basic Auth if credentials set
        auth = (username, password) if username and password else None
        custom_path = self.cfg.get("meter_custom_path", "").strip()
        json_path = self.cfg.get("meter_json_path", "").strip()

        # Endpoints to try in order
        endpoints = [
            "/cm?cmnd=Status%208",    # StatusSNS
            "/cm?cmnd=Status%2010",   # StatusSNS (alternative)
            "/cm?cmnd=StatusSNS",
            "/cm?cmnd=EnergyTotal",
        ]

        # If meter_custom_path set, prepend it
        if custom_path:
            endpoints = [custom_path] + endpoints

        # Also add auth query param variants if credentials present
        if username and password:
            # Note: passwords never logged
            auth_endpoints = [
                f"/cm?user={username}&password=***&cmnd=Status%208",
            ]
            # Use actual password in URL but never show it in debug
            auth_url_suffix = f"&user={username}&password={password}"
        else:
            auth_url_suffix = ""

        for ep in endpoints:
            # Build actual URL (with auth params if needed, but shown safely in debug)
            if auth_url_suffix and "user=" not in ep and "password=" not in ep:
                actual_ep = ep + auth_url_suffix
                # For debug, show masked version
                debug_ep = ep + f"&user={username}&password=***"
            else:
                actual_ep = ep
                debug_ep = ep

            data = _get_json(f"{self.base_url}{actual_ep}", timeout=self.timeout,
                             auth=auth, debug=None)
            # Log manually with masked URL
            debug.append(f"GET {self.base_url}{debug_ep}")
            if data is None:
                debug.append(f"  → no data / error")
                continue
            debug.append(f"  → got JSON response")

            # If meter_json_path set, try it on this response
            if json_path:
                val = _json_path(data, json_path)
                if val is not None:
                    kwh = normalize_energy_value(val)
                    debug.append(f"  → meter_json_path '{json_path}' = {val} → {kwh} kWh")
                    if kwh is not None:
                        return self._result(value=kwh, debug=debug)

            # Try common paths in priority order
            found = self._try_tasmota_paths(data, debug)
            if found is not None:
                return self._result(value=found, debug=debug)

        return self._result(error="Kein Energiewert gefunden", debug=debug)

    def _try_tasmota_paths(self, data: dict, debug: list) -> Optional[float]:
        """Try known Tasmota JSON paths. Returns kWh or None."""
        # Priority paths — do NOT use Today, Yesterday, Power, Voltage, Current
        priority_paths = [
            "StatusSNS.ENERGY.Total",
            "StatusSNS.ENERGY.Total_in",
            "ENERGY.Total",
            "ENERGY.Total_in",
            "StatusSNS.SML.Total",
            "StatusSNS.SML.Total_in",
            "StatusSNS.SML.E_in",
        ]
        for path in priority_paths:
            val = _json_path(data, path)
            if val is not None:
                try:
                    fval = float(val)
                except (TypeError, ValueError):
                    continue
                kwh = normalize_energy_value(fval)
                if kwh is not None:
                    debug.append(f"  → path '{path}' = {val} → {kwh} kWh")
                    return kwh

        # Generic: StatusSNS.<first_sensor_key>.Total / Total_in / E_in
        sns = data.get("StatusSNS") if isinstance(data, dict) else None
        if isinstance(sns, dict):
            # Skip known non-sensor keys
            skip_keys = {"Time", "TempUnit", "ENERGY", "SML"}
            for key, sensor_val in sns.items():
                if key in skip_keys:
                    continue
                if not isinstance(sensor_val, dict):
                    continue
                for subkey in ["Total", "Total_in", "E_in"]:
                    raw = sensor_val.get(subkey)
                    if raw is not None:
                        try:
                            fraw = float(raw)
                        except (TypeError, ValueError):
                            continue
                        kwh = normalize_energy_value(fraw)
                        if kwh is not None:
                            debug.append(f"  → generic StatusSNS.{key}.{subkey} = {raw} → {kwh} kWh")
                            return kwh

        return None


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
        unit = self.cfg.get("meter_value_unit", "auto").lower()
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
