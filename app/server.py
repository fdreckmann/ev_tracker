import os, json, time, sqlite3, logging, threading, requests, hashlib, secrets, functools
import xml.etree.ElementTree as ET
from datetime import datetime, timezone, timedelta
from pathlib import Path
from flask import Flask, render_template, request, jsonify, send_file, make_response, session, redirect, url_for
from providers import get_provider, get_all_capabilities, get_config_fields, PROVIDERS

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger(__name__)

APP_VERSION   = "1.6.1"

CHANGELOG = [
    {"version": "1.6.1", "changes": [
        "SSO Login via Google-Konto (OAuth2)",
        "SSO Login via Microsoft-Konto / Azure AD (OAuth2)",
        "OAuth-Konfiguration + Redirect-URI-Anzeige im Sicherheits-Tab",
        "Login-Seite: Google- und Microsoft-Buttons werden automatisch angezeigt",
    ]},
    {"version": "1.6.0", "changes": [
        "Multi-Auto-Support: beliebig viele Fahrzeuge gleichzeitig tracken",
        "Jedes Fahrzeug läuft in eigenem Tracker-Thread mit eigenem Provider",
        "Fahrzeugverwaltung im Konfig-Tab (hinzufügen, bearbeiten, löschen)",
        "Ladevorgänge-Liste: Filter nach Fahrzeug, Fahrzeug-Spalte bei mehreren Autos",
        "Bestehende Sessions automatisch als Primärfahrzeug (v0) zugeordnet",
    ]},
    {"version": "1.5.0", "changes": [
        "Neue Provider: Hyundai / Kia (Bluelink/UVO), Renault / Dacia, Polestar, Audi (MyAudi)",
        "Login mit Passwort + optionaler 2FA (TOTP · Google Authenticator / Authy)",
        "Zählerstand-Quelle auf Konfig-Seite verschoben",
        "Update: Container-Erkennung per Image (funktioniert jetzt auf Unraid)",
        "Update: Remote-Versionsnummer und Changelog vor Installation sichtbar",
        "Update-Status: Live-Log wird im Browser angezeigt",
    ]},
    {"version": "1.4.6", "changes": [
        "Zählerstand im Dashboard als eigene Kachel (nur wenn Daten vorhanden)",
        "Zählerstand Alt→Neu in der Ladevorgänge-Liste (Spalte erscheint nur wenn Daten vorhanden)",
        "Zählerstand Alt/Neu im Session-Detail-Modal",
    ]},
    {"version": "1.4.5", "changes": [
        "Update-Mechanismus: HTTP/1.0 für Pull behoben (HTTP/1.1 Keep-Alive verhinderte Abschluss)",
        "Stop mit Force-Delete, besseres Logging für Fehlerdiagnose",
    ]},
    {"version": "1.4.4", "changes": [
        "Wallbox-Quellen: go-e Charger, openWB, WARP Charger, EVCC, Webasto, Alfen, Juice Charger",
        "EVCC als Universalquelle für KEBA, ABL, Mennekes, Heidelberg, Wallbe, NRGKick u.v.m.",
        "Erweiterte Konfiguration: EVCC-Port, Loadpoint-Index, Alfen-Passwort",
    ]},
    {"version": "1.4.3", "changes": [
        "Zählerstand-Quelle: Home Assistant, Shelly oder Tasmota",
        "Zählerstand wird beim Start und Ende jeder Session automatisch erfasst",
        "Fallback auf berechneten Zählerstand wenn keine Quelle konfiguriert",
    ]},
    {"version": "1.4.2", "changes": [
        "Zählerstand Alt/Neu wird automatisch aus Anfangszählerstand + kumulierten kWh berechnet",
        "Neues Konfigurationsfeld: Anfangszählerstand (kWh) im Export-Panel",
    ]},
    {"version": "1.4.1", "changes": [
        "Schrift in Dashboard-Kacheln skaliert automatisch — kein Überlauf mehr",
        "Schrift passt sich auch bei Fenstergrößenänderung neu an",
    ]},
    {"version": "1.4.0", "changes": [
        "Template-Kopfzeilen automatisch befüllen (Abrechnungsmonat, Kennzeichen, Fahrer, Abteilung, Kostenstelle, Gesamtkosten)",
        "Neue Template-Felder: Ladedauer, Kilometerstand, Ladekosten, Lademenge",
        "Konfigurationsfelder für Fahrer, Kennzeichen, Abteilung, Kostenstelle",
        "Kritischer Export-Fehler (NameError) behoben",
    ]},
    {"version": "1.3.0", "changes": [
        "Update-Tab als eigener Reiter neben Backup",
        "Changelog mit Versionshistorie im Update-Tab",
        "Update-Badge öffnet direkt Update-Tab und prüft automatisch",
        "Browser-Cache deaktiviert — kein manuelles Löschen mehr nötig",
    ]},
    {"version": "1.2.0", "changes": [
        "Excel-Template Editor mit visueller Vorschau",
        "Startzeile per Klick wählbar, Live-Vorschau mit echten Daten",
        "Template-Spalten-Zuordnung wird persistent gespeichert",
        "Export-Fehler werden als Toast angezeigt",
    ]},
    {"version": "1.1.0", "changes": [
        "Software-Update Kachel neben Backup",
        "Fortschrittsbalken beim Update-Install",
        "Dev-Channel und dev-Branch eingeführt",
        "Batteriekapazität als freies Zahlenfeld",
    ]},
]

DATA_DIR      = Path(os.environ.get("DATA_DIR", "/data"))
CONFIG_FILE   = DATA_DIR / "config.json"
DB_PATH       = DATA_DIR / "sessions.db"
EXPORT_DIR    = DATA_DIR / "exports"
TEMPLATE_PATH = DATA_DIR / "template.xlsx"
BACKUP_DIR    = DATA_DIR / "backups"

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 10 * 1024 * 1024

# ── Auth helpers ─────────────────────────────────────────────────────────────

def _get_secret_key():
    key_file = DATA_DIR / ".secret_key"
    if key_file.exists():
        return key_file.read_text().strip()
    key = secrets.token_hex(32)
    key_file.write_text(key)
    return key

def _hash_password(pw: str) -> str:
    return hashlib.sha256(pw.encode()).hexdigest()

def _auth_enabled() -> bool:
    cfg = load_config()
    return bool(cfg.get("auth_password_hash"))

def require_auth(f):
    @functools.wraps(f)
    def wrapper(*args, **kwargs):
        if not _auth_enabled():
            return f(*args, **kwargs)
        if session.get("authenticated"):
            return f(*args, **kwargs)
        # API endpoints return JSON
        if request.path.startswith("/api/"):
            return jsonify({"error": "Nicht eingeloggt"}), 401
        return redirect(url_for("login_page", next=request.path))
    return wrapper

DEFAULT_CONFIG = {
    # Provider selection
    "provider":             "ha",        # ha | vw | tesla | volvo | bmw | mercedes
    "car_name":             "Mein EV",

    # HA provider fields
    "ha_url":               "http://homeassistant.local:8123",
    "ha_token":             "",
    "charging_sensor":      "",
    "soc_sensor":           "",
    "odo_sensor":           "",
    "power_sensor":         "",
    "charge_speed_sensor":  "",
    "charge_type_sensor":   "",
    "location_sensor":      "",
    "home_states":          "home,zuhause",
    "dc_threshold_kw":      22.0,

    # VW provider fields
    "vw_username":          "",
    "vw_password":          "",
    "vw_vin":               "",

    # Tesla provider fields
    "tesla_email":          "",
    "tesla_vin":            "",

    # Volvo provider fields
    "volvo_api_key":        "",
    "volvo_access_token":   "",
    "volvo_vin":            "",

    # BMW provider fields
    "bmw_username":         "",
    "bmw_password":         "",
    "bmw_vin":              "",
    "bmw_region":           "rest_of_world",

    # Mercedes provider fields
    "mercedes_token":       "",
    "mercedes_vin":         "",

    # Shared location fields (non-HA providers)
    "home_lat":             "",
    "home_lon":             "",
    "home_radius_m":        200,

    # Pricing
    "battery_capacity_kwh": 77.0,
    "price_per_kwh_home":   0.30,
    "price_per_kwh_ac":     0.45,
    "price_per_kwh_dc":     0.75,
    "entsoe_api_key":       "",
    "entsoe_ac_markup":     3.0,
    "entsoe_dc_markup":     6.0,

    # Notifications
    "notify_service":       "",

    # System
    "poll_interval":        60,
    "backup_cron":          "",
    "update_channel":       "latest",  # latest | nightly | dev
    "template_mapping":     {},        # {col_index_str: field_name}
    "template_start_row":   None,      # row number where data starts (1-based)
    "template_fahrer":        "",      # driver name for template header
    "template_kennzeichen":   "",      # license plate for template header
    "template_abteilung":     "",      # department for template header
    "template_kostenstelle":  "",      # cost center for template header
    "template_meter_start":   0.0,    # starting electricity meter reading (kWh) fallback
    "meter_source":      "none",  # none|ha|shelly|tasmota|go_e|openwb|warp|evcc|webasto|alfen|juice
    "meter_sensor":      "",      # HA entity_id
    "meter_device_ip":   "",      # IP for all device-based sources
    "meter_evcc_port":   7070,    # EVCC port (default 7070)
    "meter_evcc_lp":     0,       # EVCC loadpoint index (0-based)
    "meter_alfen_pass":  "admin", # Alfen web UI password

    # Auth — password + TOTP
    "auth_password_hash": "",
    "auth_totp_secret":   "",

    # OAuth2 SSO — Google
    "oauth_google_client_id":     "",
    "oauth_google_client_secret": "",

    # OAuth2 SSO — Microsoft
    "oauth_microsoft_client_id":     "",
    "oauth_microsoft_client_secret": "",
    "oauth_microsoft_tenant":        "common",  # "common" = any MS account, or tenant-id

    # Base URL for OAuth redirect URIs (e.g. https://ev.example.com)
    # Leave empty to auto-detect from request
    "oauth_base_url": "",

    # Multi-vehicle: additional vehicles beyond the primary (v0)
    "extra_vehicles":    [],
}

# Fields that belong to a vehicle config (vs. app-level config)
VEHICLE_SPECIFIC_KEYS = {
    "provider","car_name","poll_interval","battery_capacity_kwh",
    "home_lat","home_lon","home_radius_m","dc_threshold_kw",
    "ha_url","ha_token","charging_sensor","soc_sensor","odo_sensor",
    "power_sensor","charge_speed_sensor","charge_type_sensor","location_sensor","home_states",
    "vw_username","vw_password","vw_vin","vw_update_interval",
    "tesla_email","tesla_vin",
    "volvo_api_key","volvo_access_token","volvo_vin",
    "bmw_username","bmw_password","bmw_vin","bmw_region",
    "mercedes_token","mercedes_vin",
    "hk_brand","hk_username","hk_password","hk_pin","hk_region","hk_vin",
    "renault_username","renault_password","renault_locale","renault_account","renault_vin",
    "polestar_username","polestar_password","polestar_vin",
    "audi_username","audi_password","audi_vin",
}

def load_config():
    if CONFIG_FILE.exists():
        with open(CONFIG_FILE) as f:
            return {**DEFAULT_CONFIG, **json.load(f)}
    return DEFAULT_CONFIG.copy()

def save_config(cfg):
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    with open(CONFIG_FILE, "w") as f:
        json.dump(cfg, f, indent=2)

def get_all_vehicles(cfg=None) -> list[dict]:
    """Returns primary vehicle (v0 from flat config) plus extra vehicles."""
    if cfg is None:
        cfg = load_config()
    primary = {
        "id":   "v0",
        "name": cfg.get("car_name", "Mein EV"),
        "provider": cfg.get("provider", "ha"),
        "active": True,
        **{k: cfg[k] for k in VEHICLE_SPECIFIC_KEYS if k in cfg and k not in ("provider","car_name")},
    }
    extras = cfg.get("extra_vehicles", [])
    return [primary] + extras

def build_vehicle_config(vehicle: dict, cfg=None) -> dict:
    """Merge app-level config with vehicle-specific fields for provider initialization."""
    if cfg is None:
        cfg = load_config()
    merged = dict(cfg)
    merged.update(vehicle)
    if "name" in vehicle:
        merged["car_name"] = vehicle["name"]
    return merged

# ── Database ──────────────────────────────────────────────────────────────────
def init_db():
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(DB_PATH)
    con.execute("""CREATE TABLE IF NOT EXISTS sessions (
        id            INTEGER PRIMARY KEY AUTOINCREMENT,
        start_ts      TEXT, end_ts TEXT,
        odo_start     REAL, odo_end REAL,
        soc_start     REAL, soc_end REAL,
        kwh_charged   REAL, cost_eur REAL,
        cost_manual   INTEGER DEFAULT 0,
        location      TEXT DEFAULT 'unknown',
        charger_type  TEXT DEFAULT 'unknown',
        max_power_kw  REAL,
        price_per_kwh REAL,
        entsoe_spot   REAL,
        provider      TEXT DEFAULT 'ha',
        meter_old     REAL, meter_new REAL,
        vehicle_id    TEXT DEFAULT 'v0'
    )""")
    # migrate existing DB
    for col, typedef in [("meter_old","REAL"),("meter_new","REAL"),("vehicle_id","TEXT DEFAULT 'v0'")]:
        try: con.execute(f"ALTER TABLE sessions ADD COLUMN {col} {typedef}")
        except Exception: pass
    con.execute("""CREATE TABLE IF NOT EXISTS session_points (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        session_id INTEGER NOT NULL,
        ts TEXT NOT NULL,
        soc REAL, power_kw REAL
    )""")
    # live migration
    for col, typedef in [
        ("cost_manual",  "INTEGER DEFAULT 0"),
        ("charger_type", "TEXT DEFAULT 'unknown'"),
        ("max_power_kw", "REAL"),
        ("price_per_kwh","REAL"),
        ("entsoe_spot",  "REAL"),
        ("provider",     "TEXT DEFAULT 'ha'"),
    ]:
        try:
            con.execute(f"ALTER TABLE sessions ADD COLUMN {col} {typedef}")
        except sqlite3.OperationalError: pass
    con.commit(); con.close()

def get_sessions(year=None, month=None, location=None, vehicle_id=None, limit=50):
    where = ["end_ts IS NOT NULL"]; params = []
    if year and month:
        where.append("start_ts LIKE ?"); params.append(f"{year:04d}-{month:02d}%")
    if location and location != "all":
        where.append("location = ?"); params.append(location)
    if vehicle_id and vehicle_id != "all":
        where.append("vehicle_id = ?"); params.append(vehicle_id)
    sql = f"SELECT * FROM sessions WHERE {' AND '.join(where)} ORDER BY start_ts DESC"
    if not (year and month): sql += f" LIMIT {limit}"
    con = sqlite3.connect(DB_PATH); con.row_factory = sqlite3.Row
    rows = con.execute(sql, params).fetchall(); con.close()
    return [dict(r) for r in rows]

def get_monthly_stats():
    con = sqlite3.connect(DB_PATH); con.row_factory = sqlite3.Row
    rows = con.execute("""
        SELECT strftime('%Y-%m', start_ts) AS month,
               COUNT(*) AS sessions,
               SUM(kwh_charged) AS total_kwh,
               SUM(cost_eur) AS total_cost,
               MAX(odo_end) - MIN(odo_start) AS km_driven,
               SUM(CASE WHEN location='home'   THEN cost_eur ELSE 0 END) AS home_cost,
               SUM(CASE WHEN location='extern' THEN cost_eur ELSE 0 END) AS ext_cost,
               SUM(CASE WHEN charger_type='dc' THEN kwh_charged ELSE 0 END) AS dc_kwh,
               SUM(CASE WHEN charger_type='ac' THEN kwh_charged ELSE 0 END) AS ac_kwh
        FROM sessions WHERE end_ts IS NOT NULL
        GROUP BY month ORDER BY month DESC LIMIT 12
    """).fetchall()
    con.close(); return [dict(r) for r in rows]

# ── ENTSO-E ───────────────────────────────────────────────────────────────────
_entsoe_cache = {"price": None, "ts": 0}

def fetch_entsoe_spot(api_key: str):
    if not api_key: return None
    if _entsoe_cache["price"] is not None and time.time() - _entsoe_cache["ts"] < 3600:
        return _entsoe_cache["price"]
    try:
        now       = datetime.now(timezone.utc)
        day_start = now.replace(hour=0,minute=0,second=0,microsecond=0)
        day_end   = day_start + timedelta(days=1)
        url = (
            "https://web-api.tp.entsoe.eu/api"
            f"?securityToken={api_key}"
            "&documentType=A44"
            "&in_Domain=10Y1001A1001A83F"
            "&out_Domain=10Y1001A1001A83F"
            f"&periodStart={day_start.strftime('%Y%m%d%H%M')}"
            f"&periodEnd={day_end.strftime('%Y%m%d%H%M')}"
        )
        r    = requests.get(url, timeout=15); r.raise_for_status()
        root = ET.fromstring(r.text)
        ns   = "urn:iec62325.351:tc57wg16:451-3:publicationdocument:7:3"
        pts  = root.findall(f".//{{{ns}}}Point")
        if not pts: return None
        current_hour = now.hour
        pt = pts[min(current_hour, len(pts)-1)]
        price_el = pt.find(f"{{{ns}}}price.amount")
        if price_el is None: return None
        price_kwh = round(float(price_el.text) / 1000, 4)
        _entsoe_cache.update(price=price_kwh, ts=time.time())
        log.info("ENTSO-E: %.4f €/kWh", price_kwh)
        return price_kwh
    except Exception as e:
        log.warning("ENTSO-E error: %s", e); return None

def calc_extern_price(cfg, charger_type, spot):
    is_dc = charger_type == "dc"
    if spot is not None:
        markup = cfg.get("entsoe_dc_markup" if is_dc else "entsoe_ac_markup", 6.0 if is_dc else 3.0)
        return round(spot * markup, 4)
    return cfg.get("price_per_kwh_dc" if is_dc else "price_per_kwh_ac", 0.75 if is_dc else 0.45)

def ha_notify(cfg, title, message):
    service = cfg.get("notify_service","").strip()
    if not service or cfg.get("provider") != "ha": return
    try:
        parts = service.split(".",1)
        svc   = parts[1] if len(parts)==2 else parts[0]
        requests.post(
            f"{cfg['ha_url'].rstrip('/')}/api/services/notify/{svc}",
            headers={"Authorization":f"Bearer {cfg['ha_token']}","Content-Type":"application/json"},
            json={"title":title,"message":message}, timeout=8)
    except Exception as e:
        log.warning("Notify error: %s", e)

# ── Tracker ───────────────────────────────────────────────────────────────────
def _make_state(vehicle_id="v0", provider_id="ha"):
    return {
        "vehicle_id": vehicle_id,
        "running": False, "session_active": False, "session_id": None,
        "last_poll": None, "last_error": None, "soc_current": None,
        "odo_current": None, "charging": False, "location": "unknown",
        "charger_type": "unknown", "power_kw": None, "entsoe_spot": None,
        "provider": provider_id,
        "provider_name": PROVIDERS.get(provider_id, PROVIDERS["ha"]).PROVIDER_NAME,
    }

_vehicle_states: dict[str, dict] = {"v0": _make_state("v0")}
_vehicle_stops:  dict[str, threading.Event] = {"v0": threading.Event()}

# Backward compat alias
_state = _vehicle_states["v0"]
_stop  = _vehicle_stops["v0"]

def read_meter_value():
    cfg = load_config()
    source = cfg.get("meter_source", "none")
    ip  = cfg.get("meter_device_ip", "").strip()
    try:
        # ── Smart plugs / energy monitors ────────────────────────────────────
        if source == "ha":
            entity = cfg.get("meter_sensor", "").strip()
            if not entity: return None
            url = cfg.get("ha_url","").rstrip("/") + f"/api/states/{entity}"
            r = requests.get(url, headers={"Authorization": f"Bearer {cfg.get('ha_token','')}"},
                             timeout=5)
            return float(r.json()["state"])

        elif source == "shelly":
            if not ip: return None
            for endpoint in ("/emeter/0", "/meter/0"):
                try:
                    data = requests.get(f"http://{ip}{endpoint}", timeout=5).json()
                    if "total" in data:
                        return round(data["total"] / 1000, 3)  # Wh → kWh
                except Exception: pass

        elif source == "tasmota":
            if not ip: return None
            r = requests.get(f"http://{ip}/cm?cmnd=Status%208", timeout=5)
            return float(r.json()["StatusSNS"]["ENERGY"]["Total"])

        # ── Wallboxen ─────────────────────────────────────────────────────────
        elif source == "go_e":
            if not ip: return None
            # Try v2 API, then v1 (auto-detect firmware)
            for path in ("/api/status", "/status"):
                try:
                    data = requests.get(f"http://{ip}{path}", timeout=5).json()
                    if "eto" in data:
                        return round(data["eto"] / 10000, 3)  # 0.1 Wh → kWh
                except Exception: pass

        elif source == "openwb":
            if not ip: return None
            # openWB v1/v2 ramdisk endpoint (LP1); try per-LP paths
            for path in ("/openWB/ramdisk/llkwh",
                         "/openWB/ramdisk/lp/1/llkwh",
                         "/openWB/ramdisk/evsoc"):
                try:
                    r = requests.get(f"http://{ip}{path}", timeout=5)
                    if r.ok:
                        return round(float(r.text.strip()), 3)
                except Exception: pass

        elif source == "warp":
            if not ip: return None
            # WARP Charger 1/2/3 (Tinkerforge) — /meter/state
            data = requests.get(f"http://{ip}/meter/state", timeout=5).json()
            return round(float(data["energy_abs"]), 3)  # kWh

        elif source == "evcc":
            # EVCC covers: KEBA, ABL, Mennekes, Heidelberg, Wallbe, Alfen,
            # ABB Terra, Webasto, NRGKick, Fronius Wattpilot, Easee + ~100 more
            if not ip: return None
            port = int(cfg.get("meter_evcc_port") or 7070)
            data = requests.get(f"http://{ip}:{port}/api/state", timeout=5).json()
            result = data.get("result", data)
            lps = result.get("loadpoints", [])
            lp_idx = int(cfg.get("meter_evcc_lp", 0))
            if lps and lp_idx < len(lps):
                lp = lps[lp_idx]
                # chargeTotalImport in kWh (EVCC ≥ 0.12x)
                v = lp.get("chargeTotalImport") or (lp.get("chargedEnergy", 0) / 1000)
                return round(float(v), 3)

        elif source == "webasto":
            if not ip: return None
            # Webasto Next / Live — local REST (firmware ≥ 1.8)
            data = requests.get(f"http://{ip}/api/1/status", timeout=5).json()
            # totalEnergy in Wh
            v = data.get("totalEnergy") or data.get("MeterReading", 0)
            return round(float(v) / 1000, 3)

        elif source == "alfen":
            if not ip: return None
            # Alfen Eve Single / Double — Modbus-JSON proxy via local web UI
            r = requests.get(f"http://{ip}/api", timeout=5,
                             auth=("admin", cfg.get("meter_alfen_pass","admin")))
            data = r.json()
            # prop ID 21 = Meter Reading kWh
            for prop in data.get("data", []):
                if prop.get("id") == "3EA00020" or "Total" in str(prop.get("description","")):
                    return round(float(prop.get("value", 0)), 3)

        elif source == "juice":
            if not ip: return None
            # Juice Charger Me³ / Pro — local HTTP API
            data = requests.get(f"http://{ip}/api/1.0.0/details", timeout=5).json()
            return round(float(data.get("meter_total_kwh", data.get("totalEnergy", 0))), 3)

    except Exception as e:
        log.warning("Meter read error (%s): %s", source, e)
    return None

def tracker_loop(vehicle_id: str = "v0"):
    st   = _vehicle_states[vehicle_id]
    stop = _vehicle_stops[vehicle_id]
    st["running"] = True
    session_active = False; session_id = None
    soc_start = odo_start = peak_power = None

    log.info("Tracker gestartet: %s", vehicle_id)
    while not stop.is_set():
        cfg = load_config()
        # For v0 use flat config; for extra vehicles get their config merged with app config
        if vehicle_id == "v0":
            vcfg = cfg
        else:
            extras = cfg.get("extra_vehicles", [])
            vehicle = next((v for v in extras if v["id"] == vehicle_id), None)
            if not vehicle:
                log.warning("Fahrzeug %s nicht gefunden — Tracker stoppt", vehicle_id)
                break
            vcfg = build_vehicle_config(vehicle, cfg)
        provider_id = vcfg.get("provider", "ha")

        try:
            provider = get_provider(provider_id, vcfg)
            state    = provider.get_state()

            if state.error:
                st["last_error"] = state.error
                stop.wait(vcfg.get("poll_interval", 60)); continue

            charging     = state.charging or False
            soc          = state.soc
            odo          = state.odometer
            power_kw     = state.charge_power
            location     = state.location or "unknown"
            charger_type = state.charge_type or "unknown"

            st.update(
                charging=charging, soc_current=soc, odo_current=odo,
                location=location, charger_type=charger_type, power_kw=power_kw,
                session_active=session_active,
                last_poll=datetime.now().isoformat(timespec="seconds"),
                last_error=None, provider=provider_id,
                provider_name=PROVIDERS.get(provider_id, PROVIDERS["ha"]).PROVIDER_NAME,
            )

            con = sqlite3.connect(DB_PATH); cur = con.cursor()

            if charging and not session_active:
                soc_start = soc; odo_start = odo; peak_power = power_kw or 0
                meter_start_val = read_meter_value()
                spot = fetch_entsoe_spot(cfg.get("entsoe_api_key","")) if location=="extern" else None
                st["entsoe_spot"] = spot
                price_kwh = (cfg["price_per_kwh_home"] if location=="home"
                             else calc_extern_price(cfg, charger_type, spot))
                cur.execute("""INSERT INTO sessions
                    (start_ts,odo_start,soc_start,location,charger_type,
                     max_power_kw,price_per_kwh,entsoe_spot,provider,meter_old,vehicle_id)
                    VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                    (datetime.now().isoformat(timespec="seconds"),
                     odo_start,soc_start,location,charger_type,power_kw,price_kwh,spot,
                     provider_id,meter_start_val,vehicle_id))
                con.commit(); session_id=cur.lastrowid; session_active=True
                st["session_id"]=session_id
                cur.execute("INSERT INTO session_points (session_id,ts,soc,power_kw) VALUES (?,?,?,?)",
                            (session_id,datetime.now().isoformat(timespec="seconds"),soc_start,power_kw))
                con.commit()
                log.info("⚡ [%s] Session #%d | %s | %s | %.2f €/kWh",
                         vehicle_id,session_id,location.upper(),charger_type.upper(),price_kwh)
                ha_notify(vcfg,f"⚡ {vcfg['car_name']} lädt",
                    f"{'🏠 Zuhause' if location=='home' else '⚡ Extern'} · "
                    f"{'DC' if charger_type=='dc' else 'AC'} · {price_kwh:.2f} €/kWh · SOC {soc_start or '?'}%")

            elif charging and session_active:
                cur.execute("INSERT INTO session_points (session_id,ts,soc,power_kw) VALUES (?,?,?,?)",
                            (session_id,datetime.now().isoformat(timespec="seconds"),soc,power_kw))
                con.commit()
                if power_kw and (peak_power is None or power_kw > peak_power):
                    peak_power = power_kw
                    new_type = "dc" if power_kw > float(vcfg.get("dc_threshold_kw",22)) else "ac"
                    if new_type != charger_type:
                        spot = st.get("entsoe_spot")
                        price = (cfg["price_per_kwh_home"] if location=="home"
                                 else calc_extern_price(cfg,new_type,spot))
                        cur.execute("UPDATE sessions SET charger_type=?,max_power_kw=?,price_per_kwh=? WHERE id=?",
                                    (new_type,peak_power,price,session_id))
                        con.commit(); charger_type=new_type

            elif not charging and session_active:
                row = cur.execute("SELECT price_per_kwh,cost_manual FROM sessions WHERE id=?",
                                  (session_id,)).fetchone()
                db_price=row[0] if row else None; cost_manual=row[1] if row else 0
                kwh=cost=None
                if soc is not None and soc_start is not None:
                    kwh  = round(max(0.0,soc-soc_start)/100.0*vcfg["battery_capacity_kwh"],2)
                    if not cost_manual:
                        cost = round(kwh*(db_price or cfg["price_per_kwh_home"]),2)
                meter_end_val = read_meter_value()
                cur.execute("""UPDATE sessions
                    SET end_ts=?,odo_end=?,soc_end=?,kwh_charged=?,
                    cost_eur=CASE WHEN cost_manual=1 THEN cost_eur ELSE ? END,
                    max_power_kw=?,meter_new=?
                    WHERE id=?""",
                    (datetime.now().isoformat(timespec="seconds"),odo,soc,kwh,cost,peak_power,
                     meter_end_val,session_id))
                con.commit(); session_active=False
                st.update(session_active=False,session_id=None)
                log.info("✅ [%s] Session #%d | %.2f kWh | %.2f €",vehicle_id,session_id,kwh or 0,cost or 0)
                ha_notify(vcfg,f"✅ {vcfg['car_name']} fertig",
                    f"{'🏠' if location=='home' else '⚡'} · {kwh or 0:.2f} kWh · {cost or 0:.2f} €")
                session_id=None; peak_power=None
            con.close()

        except Exception as e:
            log.warning("Tracker error [%s]: %s", vehicle_id, e); st["last_error"]=str(e)
        stop.wait(vcfg.get("poll_interval",60))
    st["running"]=False

def _start_vehicle_tracker(vehicle_id: str):
    if vehicle_id not in _vehicle_states:
        cfg = load_config()
        extras = cfg.get("extra_vehicles", [])
        v = next((x for x in extras if x["id"] == vehicle_id), {})
        _vehicle_states[vehicle_id] = _make_state(vehicle_id, v.get("provider","ha"))
    if vehicle_id not in _vehicle_stops:
        _vehicle_stops[vehicle_id] = threading.Event()
    _vehicle_stops[vehicle_id].clear()
    threading.Thread(target=tracker_loop, args=(vehicle_id,), daemon=True).start()

def _stop_vehicle_tracker(vehicle_id: str):
    if vehicle_id in _vehicle_stops:
        _vehicle_stops[vehicle_id].set()

def start_tracker():
    """Start trackers for all active vehicles."""
    _vehicle_stops["v0"].clear()
    threading.Thread(target=tracker_loop, args=("v0",), daemon=True).start()
    # Start extra vehicle trackers
    cfg = load_config()
    for v in cfg.get("extra_vehicles", []):
        if v.get("active", True):
            vid = v["id"]
            _vehicle_states[vid] = _make_state(vid, v.get("provider","ha"))
            _vehicle_stops[vid]  = threading.Event()
            threading.Thread(target=tracker_loop, args=(vid,), daemon=True).start()

# ── Routes ────────────────────────────────────────────────────────────────────

_AUTH_EXEMPT = {"/login", "/logout", "/api/auth/setup", "/api/auth/status",
                "/auth/google", "/auth/google/callback",
                "/auth/microsoft", "/auth/microsoft/callback"}

@app.before_request
def check_auth():
    if not _auth_enabled():
        return
    if request.path in _AUTH_EXEMPT or request.path.startswith("/static"):
        return
    if session.get("authenticated"):
        return
    if request.path.startswith("/api/"):
        return jsonify({"error": "Nicht eingeloggt", "login_required": True}), 401
    return redirect(url_for("login_page", next=request.path))

@app.route("/login", methods=["GET","POST"])
def login_page():
    if not _auth_enabled():
        return redirect(url_for("index"))
    error = None
    if request.method == "POST":
        cfg = load_config()
        pw  = request.form.get("password","")
        if _hash_password(pw) == cfg.get("auth_password_hash",""):
            totp_secret = cfg.get("auth_totp_secret","")
            if totp_secret:
                code = request.form.get("totp","").strip().replace(" ","")
                try:
                    import pyotp
                    totp = pyotp.TOTP(totp_secret)
                    if not totp.verify(code, valid_window=1):
                        error = "Ungültiger 2FA-Code"
                except ImportError:
                    error = "pyotp nicht installiert"
            if not error:
                session["authenticated"] = True
                session.permanent = True
                return redirect(request.args.get("next") or url_for("index"))
        else:
            error = "Falsches Passwort"
    cfg = load_config()
    return render_template("login.html", error=error,
                           totp_enabled=bool(cfg.get("auth_totp_secret","")),
                           google_enabled=bool(cfg.get("oauth_google_client_id","")),
                           microsoft_enabled=bool(cfg.get("oauth_microsoft_client_id","")))

@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login_page"))

@app.route("/api/auth/setup", methods=["POST"])
def api_auth_setup():
    data = request.json or {}
    cfg  = load_config()
    if "password" in data and data["password"]:
        cfg["auth_password_hash"] = _hash_password(data["password"])
    if data.get("disable_password"):
        cfg["auth_password_hash"] = ""
        cfg["auth_totp_secret"]   = ""
    save_config(cfg)
    return jsonify({"ok": True})

@app.route("/api/auth/totp/setup", methods=["POST"])
def api_totp_setup():
    import pyotp
    secret = pyotp.random_base32()
    cfg = load_config()
    cfg["auth_totp_secret"] = secret
    save_config(cfg)
    car_name = cfg.get("car_name","EV Tracker")
    email    = cfg.get("ha_token","")[:6] or "user"
    uri = pyotp.TOTP(secret).provisioning_uri(name=email, issuer_name=f"EV Tracker ({car_name})")
    return jsonify({"ok": True, "secret": secret, "uri": uri})

@app.route("/api/auth/totp/disable", methods=["POST"])
def api_totp_disable():
    cfg = load_config()
    cfg["auth_totp_secret"] = ""
    save_config(cfg)
    return jsonify({"ok": True})

@app.route("/api/auth/status")
def api_auth_status():
    cfg = load_config()
    return jsonify({
        "auth_enabled":    bool(cfg.get("auth_password_hash")),
        "totp_enabled":    bool(cfg.get("auth_totp_secret")),
        "google_enabled":  bool(cfg.get("oauth_google_client_id")),
        "microsoft_enabled": bool(cfg.get("oauth_microsoft_client_id")),
        "authenticated":   bool(session.get("authenticated")) or not _auth_enabled(),
        "user_email":      session.get("user_email",""),
    })

# ── OAuth2 helpers ────────────────────────────────────────────────────────────

def _oauth_redirect_base() -> str:
    cfg = load_config()
    base = cfg.get("oauth_base_url","").rstrip("/")
    if base:
        return base
    # Auto-detect: honour X-Forwarded-Proto for reverse proxy
    scheme = request.headers.get("X-Forwarded-Proto", request.scheme)
    host   = request.headers.get("X-Forwarded-Host", request.host)
    return f"{scheme}://{host}"

def _oauth_finish(email: str):
    """Called after successful OAuth login — create session."""
    session["authenticated"] = True
    session["user_email"]    = email
    session.permanent        = True
    next_url = session.pop("oauth_next", None) or "/"
    return redirect(next_url)

# ── Google OAuth2 ─────────────────────────────────────────────────────────────

@app.route("/auth/google")
def auth_google():
    cfg = load_config()
    if not cfg.get("oauth_google_client_id"):
        return "Google OAuth nicht konfiguriert", 400
    state = secrets.token_urlsafe(16)
    session["oauth_state"] = state
    session["oauth_next"]  = request.args.get("next","/")
    redirect_uri = _oauth_redirect_base() + "/auth/google/callback"
    params = {
        "client_id":     cfg["oauth_google_client_id"],
        "redirect_uri":  redirect_uri,
        "response_type": "code",
        "scope":         "openid email profile",
        "state":         state,
        "access_type":   "online",
    }
    from urllib.parse import urlencode
    return redirect("https://accounts.google.com/o/oauth2/v2/auth?" + urlencode(params))

@app.route("/auth/google/callback")
def auth_google_callback():
    cfg   = load_config()
    state = request.args.get("state","")
    code  = request.args.get("code","")
    if not code or state != session.pop("oauth_state",""):
        return render_template("login.html", error="OAuth-Fehler: ungültiger State", totp_enabled=False,
                               google_enabled=bool(cfg.get("oauth_google_client_id")),
                               microsoft_enabled=bool(cfg.get("oauth_microsoft_client_id")))
    redirect_uri = _oauth_redirect_base() + "/auth/google/callback"
    try:
        r = requests.post("https://oauth2.googleapis.com/token", data={
            "code":          code,
            "client_id":     cfg["oauth_google_client_id"],
            "client_secret": cfg["oauth_google_client_secret"],
            "redirect_uri":  redirect_uri,
            "grant_type":    "authorization_code",
        }, timeout=10)
        r.raise_for_status()
        token = r.json().get("access_token","")
        info  = requests.get("https://www.googleapis.com/oauth2/v3/userinfo",
                             headers={"Authorization":f"Bearer {token}"}, timeout=10).json()
        email = info.get("email","")
        if not email:
            raise RuntimeError("Keine E-Mail vom Google-Konto erhalten")
        return _oauth_finish(email)
    except Exception as e:
        return render_template("login.html", error=f"Google Login fehlgeschlagen: {e}",
                               totp_enabled=False,
                               google_enabled=True, microsoft_enabled=bool(cfg.get("oauth_microsoft_client_id")))

# ── Microsoft OAuth2 ──────────────────────────────────────────────────────────

@app.route("/auth/microsoft")
def auth_microsoft():
    cfg = load_config()
    if not cfg.get("oauth_microsoft_client_id"):
        return "Microsoft OAuth nicht konfiguriert", 400
    tenant = cfg.get("oauth_microsoft_tenant","common") or "common"
    state  = secrets.token_urlsafe(16)
    session["oauth_state"] = state
    session["oauth_next"]  = request.args.get("next","/")
    redirect_uri = _oauth_redirect_base() + "/auth/microsoft/callback"
    from urllib.parse import urlencode
    params = {
        "client_id":     cfg["oauth_microsoft_client_id"],
        "redirect_uri":  redirect_uri,
        "response_type": "code",
        "scope":         "openid email profile User.Read",
        "state":         state,
        "response_mode": "query",
    }
    return redirect(f"https://login.microsoftonline.com/{tenant}/oauth2/v2.0/authorize?" + urlencode(params))

@app.route("/auth/microsoft/callback")
def auth_microsoft_callback():
    cfg    = load_config()
    tenant = cfg.get("oauth_microsoft_tenant","common") or "common"
    state  = request.args.get("state","")
    code   = request.args.get("code","")
    if not code or state != session.pop("oauth_state",""):
        return render_template("login.html", error="OAuth-Fehler: ungültiger State", totp_enabled=False,
                               google_enabled=bool(cfg.get("oauth_google_client_id")),
                               microsoft_enabled=bool(cfg.get("oauth_microsoft_client_id")))
    redirect_uri = _oauth_redirect_base() + "/auth/microsoft/callback"
    try:
        r = requests.post(
            f"https://login.microsoftonline.com/{tenant}/oauth2/v2.0/token",
            data={
                "code":          code,
                "client_id":     cfg["oauth_microsoft_client_id"],
                "client_secret": cfg["oauth_microsoft_client_secret"],
                "redirect_uri":  redirect_uri,
                "grant_type":    "authorization_code",
            }, timeout=10)
        r.raise_for_status()
        token = r.json().get("access_token","")
        info  = requests.get("https://graph.microsoft.com/v1.0/me",
                             headers={"Authorization":f"Bearer {token}"}, timeout=10).json()
        email = info.get("mail") or info.get("userPrincipalName","")
        if not email:
            raise RuntimeError("Keine E-Mail vom Microsoft-Konto erhalten")
        return _oauth_finish(email)
    except Exception as e:
        return render_template("login.html", error=f"Microsoft Login fehlgeschlagen: {e}",
                               totp_enabled=False,
                               google_enabled=bool(cfg.get("oauth_google_client_id")),
                               microsoft_enabled=True)

@app.route("/")
@require_auth
def index():
    cfg = load_config()
    caps = get_all_capabilities()
    provider_fields = get_config_fields(cfg.get("provider","ha"))
    resp = make_response(render_template("index.html", cfg=cfg, state=_state,
                           has_template=TEMPLATE_PATH.exists(),
                           all_providers=caps,
                           provider_fields=provider_fields,
                           provider_names={k:v.PROVIDER_NAME for k,v in PROVIDERS.items()},
                           app_version=APP_VERSION,
                           changelog=CHANGELOG,
                           all_vehicles=get_all_vehicles(cfg)))
    resp.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
    resp.headers["Pragma"]        = "no-cache"
    resp.headers["Expires"]       = "0"
    return resp

@app.route("/api/config", methods=["GET"])
def api_get_config(): return jsonify(load_config())

@app.route("/api/config", methods=["POST"])
def api_save_config():
    data=request.json; cfg=load_config()
    floats={"battery_capacity_kwh","price_per_kwh_home","price_per_kwh_ac","price_per_kwh_dc",
            "dc_threshold_kw","entsoe_ac_markup","entsoe_dc_markup","home_radius_m"}
    ints={"poll_interval"}
    for key in DEFAULT_CONFIG:
        if key in data:
            v=data[key]
            if key in floats and v!="": v=float(v)
            elif key in ints: v=int(v)
            cfg[key]=v
    save_config(cfg); return jsonify({"ok":True})

@app.route("/api/providers")
def api_providers(): return jsonify(get_all_capabilities())

@app.route("/api/providers/<provider_id>/fields")
def api_provider_fields(provider_id):
    return jsonify(get_config_fields(provider_id))

@app.route("/api/status")
def api_status():
    vid = request.args.get("vehicle_id","v0")
    st  = _vehicle_states.get(vid, _vehicle_states.get("v0", {}))
    result = dict(st)
    result["all_vehicles"] = [
        {"vehicle_id": k, "name": v.get("name",k), "running": v.get("running",False),
         "charging": v.get("charging",False), "session_active": v.get("session_active",False)}
        for k, v in _vehicle_states.items()
    ]
    return jsonify(result)

@app.route("/api/vehicles", methods=["GET"])
def api_get_vehicles():
    return jsonify(get_all_vehicles())

@app.route("/api/vehicles", methods=["POST"])
def api_add_vehicle():
    data = request.json or {}
    cfg  = load_config()
    extras = list(cfg.get("extra_vehicles", []))
    vid = f"v{int(time.time())}"
    data["id"] = vid
    data.setdefault("active", True)
    extras.append(data)
    cfg["extra_vehicles"] = extras
    save_config(cfg)
    if data.get("active", True):
        _start_vehicle_tracker(vid)
    return jsonify({"ok": True, "id": vid})

@app.route("/api/vehicles/<vid>", methods=["PUT"])
def api_update_vehicle(vid):
    if vid == "v0":
        # Update primary vehicle = update flat config fields
        data = request.json or {}
        cfg  = load_config()
        for k, val in data.items():
            if k in VEHICLE_SPECIFIC_KEYS or k == "car_name":
                cfg[k] = val
        save_config(cfg)
        return jsonify({"ok": True})
    data   = request.json or {}
    cfg    = load_config()
    extras = list(cfg.get("extra_vehicles", []))
    for i, v in enumerate(extras):
        if v["id"] == vid:
            was_active = v.get("active", True)
            extras[i] = {**v, **data, "id": vid}
            cfg["extra_vehicles"] = extras
            save_config(cfg)
            now_active = extras[i].get("active", True)
            if was_active:
                _stop_vehicle_tracker(vid)
            if now_active:
                _start_vehicle_tracker(vid)
            return jsonify({"ok": True})
    return jsonify({"error": "Fahrzeug nicht gefunden"}), 404

@app.route("/api/vehicles/<vid>", methods=["DELETE"])
def api_delete_vehicle(vid):
    if vid == "v0":
        return jsonify({"error": "Primärfahrzeug kann nicht gelöscht werden"}), 400
    cfg    = load_config()
    extras = [v for v in cfg.get("extra_vehicles", []) if v["id"] != vid]
    cfg["extra_vehicles"] = extras
    save_config(cfg)
    _stop_vehicle_tracker(vid)
    return jsonify({"ok": True})

@app.route("/api/sessions")
def api_sessions():
    return jsonify(get_sessions(
        request.args.get("year",type=int),
        request.args.get("month",type=int),
        request.args.get("location",default="all"),
        request.args.get("vehicle_id",default=None),
    ))

@app.route("/api/sessions/<int:sid>", methods=["DELETE"])
def api_delete_session(sid):
    con=sqlite3.connect(DB_PATH)
    con.execute("DELETE FROM sessions WHERE id=?",(sid,))
    con.execute("DELETE FROM session_points WHERE session_id=?",(sid,))
    con.commit(); con.close()
    return jsonify({"ok":True})

@app.route("/api/sessions/<int:sid>/points")
def api_session_points(sid):
    con=sqlite3.connect(DB_PATH); con.row_factory=sqlite3.Row
    rows=con.execute("SELECT ts,soc,power_kw FROM session_points WHERE session_id=? ORDER BY ts",(sid,)).fetchall()
    con.close(); return jsonify([dict(r) for r in rows])

@app.route("/api/sessions/<int:sid>/location", methods=["POST"])
def api_update_location(sid):
    loc = request.json.get("location","unknown")
    if loc not in ("home","extern","unknown"):
        return jsonify({"ok":False,"error":"Ungültiger Standort"}), 400
    con = sqlite3.connect(DB_PATH)
    con.execute("UPDATE sessions SET location=? WHERE id=?", (loc, sid))
    con.commit(); con.close()
    log.info("Session #%d Standort → %s", sid, loc)
    return jsonify({"ok":True})

@app.route("/api/sessions/<int:sid>/cost", methods=["POST"])
def api_update_cost(sid):
    data=request.json; cost=float(data["cost_eur"]); price_kwh=data.get("price_per_kwh")
    con=sqlite3.connect(DB_PATH); cur=con.cursor()
    if price_kwh is not None:
        row=cur.execute("SELECT kwh_charged FROM sessions WHERE id=?",(sid,)).fetchone()
        if row and row[0]: cost=round(float(row[0])*float(price_kwh),2)
        cur.execute("UPDATE sessions SET cost_eur=?,price_per_kwh=?,cost_manual=1 WHERE id=?",(cost,float(price_kwh),sid))
    else:
        cur.execute("UPDATE sessions SET cost_eur=?,cost_manual=1 WHERE id=?",(cost,sid))
    con.commit(); con.close()
    return jsonify({"ok":True,"cost_eur":cost})

@app.route("/api/stats/monthly")
def api_monthly_stats(): return jsonify(get_monthly_stats())

@app.route("/api/test-connection", methods=["POST"])
def api_test():
    data=request.json; cfg=load_config()
    # merge submitted fields into config for test
    test_cfg={**cfg,**data}
    # use saved token if empty
    if not test_cfg.get("ha_token"): test_cfg["ha_token"]=cfg.get("ha_token","")
    try:
        provider=get_provider(test_cfg.get("provider","ha"), test_cfg)
        result=provider.test_connection()
        return jsonify(result)
    except Exception as e:
        return jsonify({"ok":False,"message":str(e)})

@app.route("/api/meter/test", methods=["POST"])
def api_meter_test():
    val = read_meter_value()
    if val is not None:
        return jsonify({"ok": True, "value": val, "message": f"✅ Zählerstand: {val:.3f} kWh"})
    return jsonify({"ok": False, "message": "❌ Kein Wert erhalten — Quelle prüfen"})

@app.route("/api/entsoe/test", methods=["POST"])
def api_entsoe_test():
    key=request.json.get("entsoe_api_key","").strip()
    if not key: return jsonify({"ok":False,"error":"Kein API Key"})
    _entsoe_cache["price"]=None
    price=fetch_entsoe_spot(key)
    if price: return jsonify({"ok":True,"price_kwh":price,"price_mwh":round(price*1000,2)})
    return jsonify({"ok":False,"error":"Kein Preis erhalten"})

@app.route("/api/template", methods=["POST"])
def api_upload_template():
    if "file" not in request.files: return jsonify({"ok":False,"error":"Keine Datei"}),400
    f=request.files["file"]
    if not f.filename.endswith(".xlsx"): return jsonify({"ok":False,"error":"Nur .xlsx"}),400
    DATA_DIR.mkdir(parents=True,exist_ok=True); f.save(TEMPLATE_PATH)
    return jsonify({"ok":True,"filename":f.filename,"size":TEMPLATE_PATH.stat().st_size})

@app.route("/api/template", methods=["DELETE"])
def api_delete_template():
    if TEMPLATE_PATH.exists(): TEMPLATE_PATH.unlink()
    return jsonify({"ok":True})

@app.route("/api/template/info")
def api_template_info():
    if TEMPLATE_PATH.exists():
        return jsonify({"exists":True,"size":TEMPLATE_PATH.stat().st_size,
                        "modified":datetime.fromtimestamp(TEMPLATE_PATH.stat().st_mtime).isoformat(timespec="seconds")})
    return jsonify({"exists":False})

@app.route("/api/template/preview")
def api_template_preview():
    if not TEMPLATE_PATH.exists(): return jsonify({"ok":False,"error":"Kein Template"})
    try:
        import openpyxl
        from openpyxl.utils import get_column_letter
        from export_excel import match_column
        wb  = openpyxl.load_workbook(TEMPLATE_PATH, read_only=True, data_only=True)
        ws  = wb.active
        all_rows = []
        max_col  = 0
        raw_rows = list(ws.iter_rows(values_only=True, max_row=40))
        for row in raw_rows:
            last = max((i for i,v in enumerate(row) if v is not None), default=-1)
            if last >= 0: max_col = max(max_col, last + 1)
        max_col = max(max_col, 1)
        for ri, row in enumerate(raw_rows):
            cells = []
            for ci in range(max_col):
                v = row[ci] if ci < len(row) else None
                cells.append(str(v) if v is not None else "")
            all_rows.append({"row": ri + 1, "cells": cells})
        # auto-detect header row (first row with >= 2 filled cells)
        auto_header = None
        for r in all_rows:
            if sum(1 for c in r["cells"] if c.strip()) >= 2:
                auto_header = r["row"]; break
        # build column letters
        col_letters = [get_column_letter(i+1) for i in range(max_col)]
        # build column mapping suggestions from auto-header row
        col_suggestions = {}
        if auto_header:
            hrow = next(r for r in all_rows if r["row"] == auto_header)
            for ci, val in enumerate(hrow["cells"], 1):
                col_suggestions[ci] = {
                    "header": val,
                    "mapped_to": match_column(val) if val else None,
                    "col_letter": get_column_letter(ci),
                }
        wb.close()
        return jsonify({"ok": True, "rows": all_rows, "col_letters": col_letters,
                        "auto_header": auto_header, "col_suggestions": col_suggestions})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)})

@app.route("/api/template/render")
def api_template_render():
    if not TEMPLATE_PATH.exists(): return jsonify({"ok": False, "error": "Kein Template"})
    try:
        import openpyxl
        from openpyxl.utils import get_column_letter
        wb = openpyxl.load_workbook(TEMPLATE_PATH, data_only=True)
        ws = wb.active

        # merged cell lookup
        merged_topleft = {}   # (r,c) -> (rowspan, colspan)
        merged_skip    = set()
        for rng in ws.merged_cells.ranges:
            merged_topleft[(rng.min_row, rng.min_col)] = (
                rng.max_row - rng.min_row + 1,
                rng.max_col - rng.min_col + 1,
            )
            for r in range(rng.min_row, rng.max_row + 1):
                for c in range(rng.min_col, rng.max_col + 1):
                    if (r, c) != (rng.min_row, rng.min_col):
                        merged_skip.add((r, c))

        max_row = min(ws.max_row or 1, 40)
        max_col = ws.max_column or 1

        def hex_color(color_obj):
            if color_obj is None: return None
            try:
                if color_obj.type == "rgb":
                    rgb = color_obj.rgb
                    if rgb and rgb not in ("00000000", "FF000000", "FFFFFFFF"):
                        return "#" + rgb[-6:]
                if color_obj.type == "theme":
                    return None
            except Exception:
                pass
            return None

        rows = []
        for ri in range(1, max_row + 1):
            cells = []
            for ci in range(1, max_col + 1):
                if (ri, ci) in merged_skip:
                    cells.append(None)
                    continue
                cell = ws.cell(row=ri, column=ci)
                rs, cs = merged_topleft.get((ri, ci), (1, 1))
                bg = bold = fg = None
                try:
                    if cell.fill and cell.fill.patternType not in (None, "none"):
                        bg = hex_color(cell.fill.fgColor)
                except Exception: pass
                try:
                    if cell.font:
                        bold = bool(cell.font.bold)
                        fg   = hex_color(cell.font.color)
                except Exception: pass
                cells.append({
                    "v": str(cell.value) if cell.value is not None else "",
                    "bg": bg, "fg": fg, "bold": bold,
                    "rs": rs, "cs": cs, "r": ri, "c": ci,
                })
            rows.append(rows.__class__() or None)  # placeholder replaced below
            rows[-1] = {"row": ri, "cells": cells}

        # column widths (approximate em)
        col_widths = []
        for ci in range(1, max_col + 1):
            letter = get_column_letter(ci)
            dim = ws.column_dimensions.get(letter)
            w = dim.width if dim and dim.width else 8
            col_widths.append(round(min(max(w, 4), 25), 1))

        col_letters = [get_column_letter(i) for i in range(1, max_col + 1)]
        wb.close()
        return jsonify({"ok": True, "rows": rows, "col_letters": col_letters,
                        "col_widths": col_widths, "max_col": max_col})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)})

@app.route("/api/sessions/sample")
def api_sessions_sample():
    """Return one recent completed session for live export preview."""
    try:
        con = sqlite3.connect(DB_PATH); con.row_factory = sqlite3.Row
        row = con.execute(
            "SELECT * FROM sessions WHERE end_ts IS NOT NULL ORDER BY start_ts DESC LIMIT 1"
        ).fetchone()
        con.close()
        return jsonify(dict(row) if row else {})
    except Exception:
        return jsonify({})

@app.route("/api/template/mapping", methods=["GET","POST"])
def api_template_mapping():
    cfg = load_config()
    if request.method == "POST":
        body = request.get_json(force=True)
        cfg["template_mapping"]   = body.get("mapping", {})
        cfg["template_start_row"] = body.get("start_row")
        save_config(cfg)
        return jsonify({"ok": True})
    return jsonify({"mapping": cfg.get("template_mapping", {}),
                    "start_row": cfg.get("template_start_row")})

@app.route("/api/export")
def api_export():
    from export_excel import export
    y=request.args.get("year",datetime.now().year,type=int)
    m=request.args.get("month",datetime.now().month,type=int)
    loc=request.args.get("location","all")
    override=json.loads(request.args.get("col_override","null") or "null")
    cfg = load_config()
    if override is None:
        saved = cfg.get("template_mapping") or {}
        if saved:
            override = {k: v for k, v in saved.items() if v}
    start_row = cfg.get("template_start_row")
    header_info = {
        "fahrer":            cfg.get("template_fahrer", ""),
        "kennzeichen":       cfg.get("template_kennzeichen", ""),
        "abteilung":         cfg.get("template_abteilung", ""),
        "kostenstelle":      cfg.get("template_kostenstelle", ""),
        "price_per_kwh":     cfg.get("price_per_kwh_home", 0.30),
        "meter_start_value": cfg.get("template_meter_start", 0.0),
    }
    try:
        path = export(y, m, loc, col_override=override, start_row=start_row, header_info=header_info)
        return send_file(path, as_attachment=True)
    except Exception as e:
        log.exception("Export fehlgeschlagen")
        return jsonify({"ok": False, "error": str(e)}), 500

# ── Backup ────────────────────────────────────────────────────────────────────
import zipfile, subprocess
from threading import Timer

_backup_timer = None

def create_backup(label="manual"):
    BACKUP_DIR.mkdir(parents=True,exist_ok=True)
    ts=datetime.now().strftime("%Y%m%d_%H%M%S")
    out=BACKUP_DIR/f"ev-tracker_backup_{label}_{ts}.zip"
    with zipfile.ZipFile(out,"w",zipfile.ZIP_DEFLATED) as zf:
        for item in DATA_DIR.rglob("*"):
            if BACKUP_DIR in item.parents or item==out: continue
            zf.write(item,item.relative_to(DATA_DIR))
    all_backups=sorted(BACKUP_DIR.glob("*.zip"),key=lambda p:p.stat().st_mtime)
    while len(all_backups)>10: all_backups.pop(0).unlink()
    return out

def restore_backup(zip_path):
    with zipfile.ZipFile(zip_path,"r") as zf:
        for member in zf.namelist():
            if member.startswith("backups/"): continue
            zf.extract(member,DATA_DIR)

def parse_cron_next(cron_expr):
    now=datetime.now(); expr=cron_expr.strip().lower()
    if expr in ("@daily","0 0 * * *"):
        nxt=now.replace(hour=0,minute=0,second=0,microsecond=0)+timedelta(days=1)
    elif expr in ("@weekly","0 0 * * 0"):
        days=(6-now.weekday())%7 or 7
        nxt=now.replace(hour=0,minute=0,second=0,microsecond=0)+timedelta(days=days)
    elif expr in ("@monthly","0 0 1 * *"):
        nxt=now.replace(month=now.month%12+1,day=1,hour=0,minute=0,second=0,microsecond=0)
    else:
        try:
            parts=expr.split()
            if len(parts)>=2:
                nxt=now.replace(hour=int(parts[1]),minute=int(parts[0]),second=0,microsecond=0)
                if nxt<=now: nxt+=timedelta(days=1)
            else: return None
        except: return None
    return (nxt-now).total_seconds()

def schedule_backup():
    global _backup_timer
    cfg=load_config(); cron=cfg.get("backup_cron","").strip()
    if not cron: return
    secs=parse_cron_next(cron)
    if not secs or secs<=0: return
    def run():
        try: create_backup("auto"); log.info("Auto-Backup OK")
        except Exception as e: log.warning("Auto-Backup Fehler: %s",e)
        schedule_backup()
    _backup_timer=Timer(secs,run); _backup_timer.daemon=True; _backup_timer.start()
    log.info("Nächstes Backup: %s",(datetime.now()+timedelta(seconds=secs)).strftime("%d.%m.%Y %H:%M"))

@app.route("/api/backup/list")
def api_backup_list():
    BACKUP_DIR.mkdir(parents=True,exist_ok=True)
    backups=[{"name":f.name,"size":f.stat().st_size,
              "modified":datetime.fromtimestamp(f.stat().st_mtime).isoformat(timespec="seconds")}
             for f in sorted(BACKUP_DIR.glob("*.zip"),key=lambda p:p.stat().st_mtime,reverse=True)]
    cfg=load_config(); cron=cfg.get("backup_cron","")
    next_backup=None
    if cron:
        secs=parse_cron_next(cron)
        if secs: next_backup=(datetime.now()+timedelta(seconds=secs)).strftime("%d.%m.%Y %H:%M")
    return jsonify({"backups":backups,"next_backup":next_backup,"cron":cron})

@app.route("/api/backup/create",methods=["POST"])
def api_backup_create():
    try: out=create_backup("manual"); return jsonify({"ok":True,"name":out.name,"size":out.stat().st_size})
    except Exception as e: return jsonify({"ok":False,"error":str(e)})

@app.route("/api/backup/download/<filename>")
def api_backup_download(filename):
    if ".." in filename or "/" in filename: return jsonify({"error":"ungültig"}),400
    path=BACKUP_DIR/filename
    if not path.exists(): return jsonify({"error":"nicht gefunden"}),404
    return send_file(path,as_attachment=True)

@app.route("/api/backup/restore",methods=["POST"])
def api_backup_restore():
    name=request.json.get("name","")
    if ".." in name or "/" in name: return jsonify({"ok":False,"error":"ungültig"}),400
    path=BACKUP_DIR/name
    if not path.exists(): return jsonify({"ok":False,"error":"nicht gefunden"}),404
    try: restore_backup(path); return jsonify({"ok":True})
    except Exception as e: return jsonify({"ok":False,"error":str(e)})

@app.route("/api/backup/upload",methods=["POST"])
def api_backup_upload():
    if "file" not in request.files: return jsonify({"ok":False,"error":"Keine Datei"}),400
    f=request.files["file"]
    if not f.filename.endswith(".zip"): return jsonify({"ok":False,"error":"Nur .zip"}),400
    BACKUP_DIR.mkdir(parents=True,exist_ok=True)
    tmp=BACKUP_DIR/f"upload_{datetime.now().strftime('%Y%m%d_%H%M%S')}.zip"; f.save(tmp)
    try: restore_backup(tmp); return jsonify({"ok":True,"restored":tmp.name})
    except Exception as e: tmp.unlink(missing_ok=True); return jsonify({"ok":False,"error":str(e)})

@app.route("/api/backup/cron",methods=["POST"])
def api_backup_cron():
    global _backup_timer
    cron=request.json.get("cron","").strip()
    cfg=load_config(); cfg["backup_cron"]=cron; save_config(cfg)
    if _backup_timer: _backup_timer.cancel()
    if cron:
        schedule_backup()
        secs=parse_cron_next(cron)
        nxt=(datetime.now()+timedelta(seconds=secs)).strftime("%d.%m.%Y %H:%M") if secs else "?"
        return jsonify({"ok":True,"next":nxt})
    return jsonify({"ok":True,"next":None})

@app.route("/api/backup/delete/<filename>",methods=["DELETE"])
def api_backup_delete(filename):
    if ".." in filename or "/" in filename: return jsonify({"ok":False}),400
    path=BACKUP_DIR/filename
    if path.exists(): path.unlink()
    return jsonify({"ok":True})

# ── Update via Docker Hub ────────────────────────────────────────────────────
DOCKER_HUB_REPO = "19121412/ev-tracker"
DOCKER_SOCKET   = "/var/run/docker.sock"

def get_dockerhub_digest(tag: str) -> str | None:
    """Fetch latest digest from Docker Hub for given tag."""
    try:
        # Get token
        r = requests.get(
            f"https://auth.docker.io/token?service=registry.docker.io&scope=repository:{DOCKER_HUB_REPO}:pull",
            timeout=10)
        token = r.json().get("token")
        # Get manifest digest
        r2 = requests.get(
            f"https://registry-1.docker.io/v2/{DOCKER_HUB_REPO}/manifests/{tag}",
            headers={"Authorization": f"Bearer {token}",
                     "Accept": "application/vnd.docker.distribution.manifest.v2+json"},
            timeout=10)
        return r2.headers.get("Docker-Content-Digest","")
    except Exception as e:
        log.warning("Docker Hub check error: %s", e)
        return None

def get_local_digest(tag: str) -> str | None:
    """Get local image digest via Docker socket."""
    if not os.path.exists(DOCKER_SOCKET):
        return None
    try:
        import socket, json as _json
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        sock.connect(DOCKER_SOCKET)
        req = f"GET /images/{DOCKER_HUB_REPO}:{tag}/json HTTP/1.0\r\nHost: localhost\r\n\r\n"
        sock.send(req.encode())
        resp = b""
        while chunk := sock.recv(4096): resp += chunk
        sock.close()
        body = resp.split(b"\r\n\r\n", 1)[-1]
        data = _json.loads(body)
        digests = data.get("RepoDigests", [])
        for d in digests:
            if "@" in d: return d.split("@")[1]
        return None
    except Exception as e:
        log.warning("Docker socket error: %s", e)
        return None

def docker_socket_request(method: str, path: str, body: dict = None) -> dict:
    """Make HTTP request to Docker socket using HTTP/1.0 so connection closes after response."""
    import socket as _socket, json as _json
    sock = _socket.socket(_socket.AF_UNIX, _socket.SOCK_STREAM)
    sock.settimeout(30)
    sock.connect(DOCKER_SOCKET)
    body_bytes = _json.dumps(body).encode() if body else b""
    headers = (
        f"{method} {path} HTTP/1.0\r\n"
        f"Host: localhost\r\n"
        f"Content-Type: application/json\r\n"
        f"Content-Length: {len(body_bytes)}\r\n"
        f"\r\n"
    )
    sock.send(headers.encode() + body_bytes)
    resp = b""
    while True:
        chunk = sock.recv(4096)
        if not chunk: break
        resp += chunk
    sock.close()
    header, _, body_raw = resp.partition(b"\r\n\r\n")
    status = int(header.split(b" ")[1])
    try: data = _json.loads(body_raw)
    except: data = {}
    return {"status": status, "data": data}

_update_log: list[str] = []
_update_running = False

def _ulog(msg: str):
    _update_log.append(msg)
    if len(_update_log) > 200:
        _update_log.pop(0)
    log.info("Update: %s", msg)

def docker_pull_and_restart(tag: str):
    """Pull new image in background, then stop/remove/recreate container."""
    global _update_running
    if not os.path.exists(DOCKER_SOCKET):
        return False, "Docker Socket nicht gefunden"

    # Find container by image name (works regardless of container name on Unraid)
    try:
        containers = docker_socket_request("GET", "/containers/json?all=1")
        container_id = None
        container_name = None
        clist = containers.get("data", [])
        if not isinstance(clist, list):
            clist = []
        for c in clist:
            img = c.get("Image", "")
            if DOCKER_HUB_REPO in img:
                container_id = c["Id"]
                names = c.get("Names", [])
                container_name = names[0].lstrip("/") if names else "ev-tracker"
                break
        if not container_id:
            # Fallback: search by container name keywords
            for c in clist:
                names = c.get("Names", [])
                if any("ev" in n.lower() and "track" in n.lower() for n in names):
                    container_id = c["Id"]
                    container_name = names[0].lstrip("/") if names else "ev-tracker"
                    break
        if not container_id:
            return False, f"Container für Image '{DOCKER_HUB_REPO}' nicht gefunden — {len(clist)} Container auf dem Host"

        inspect = docker_socket_request("GET", f"/containers/{container_id}/json")
        if inspect["status"] != 200:
            return False, "Container-Konfiguration nicht lesbar"

        old_cfg     = inspect["data"]
        host_config = old_cfg.get("HostConfig", {})
        env         = old_cfg.get("Config", {}).get("Env", [])
        labels      = old_cfg.get("Config", {}).get("Labels", {})
    except Exception as e:
        return False, str(e)

    _update_running = True
    _update_log.clear()

    def pull_and_recreate():
        global _update_running
        import time as _time
        import socket as _socket, json as _json
        _ulog(f"Starte Pull für {DOCKER_HUB_REPO}:{tag}")
        try:
            # Use HTTP/1.0 so Docker closes connection after response (avoids keep-alive hang)
            sock = _socket.socket(_socket.AF_UNIX, _socket.SOCK_STREAM)
            sock.settimeout(600)
            sock.connect(DOCKER_SOCKET)
            req = (f"POST /images/create?fromImage={DOCKER_HUB_REPO}&tag={tag} HTTP/1.0\r\n"
                   f"Host: localhost\r\nContent-Length: 0\r\n\r\n")
            sock.send(req.encode())
            buf = b""
            while True:
                chunk = sock.recv(8192)
                if not chunk: break
                buf += chunk
            sock.close()
            # parse status from first line: "HTTP/1.0 200 OK"
            first_line = buf.split(b"\r\n")[0]
            parts = first_line.split(b" ")
            status = int(parts[1]) if len(parts) > 1 else 0
            _ulog(f"Pull HTTP-Status {status}")
            if status not in (200, 204):
                _ulog(f"FEHLER: Pull fehlgeschlagen — HTTP {status}")
                _update_running = False
                return
            # Check response body for error JSON lines
            body_part = buf.split(b"\r\n\r\n", 1)[-1].decode(errors="replace")
            for line in body_part.splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    j = _json.loads(line)
                    if "error" in j:
                        _ulog(f"FEHLER vom Docker-Daemon: {j['error']}")
                        _update_running = False
                        return
                    if "status" in j:
                        _ulog(j["status"])
                except Exception:
                    pass
        except Exception as e:
            _ulog(f"FEHLER beim Pull: {e}")
            _update_running = False
            return

        _ulog("Pull abgeschlossen — starte Stop/Remove/Recreate")
        _time.sleep(2)
        try:
            _ulog(f"Stoppe Container {container_name} ({container_id[:12]})")
            docker_socket_request("POST", f"/containers/{container_id}/stop?t=5")
            _time.sleep(2)
            _ulog("Container gestoppt — lösche alten Container")
            docker_socket_request("DELETE", f"/containers/{container_id}?force=1")
            _time.sleep(1)
            _ulog(f"Erstelle neuen Container '{container_name}' mit Image {DOCKER_HUB_REPO}:{tag}")
            new = docker_socket_request(
                "POST",
                f"/containers/create?name={container_name}",
                {"Image": f"{DOCKER_HUB_REPO}:{tag}",
                 "Env": env, "Labels": labels, "HostConfig": host_config}
            )
            new_id = (new.get("data") or {}).get("Id")
            if new_id:
                _ulog(f"Container erstellt ({new_id[:12]}) — starte...")
                docker_socket_request("POST", f"/containers/{new_id}/start")
                _ulog("Container gestartet — Update abgeschlossen!")
            else:
                _ulog(f"FEHLER: Keine Container-ID erhalten — {new}")
        except Exception as e:
            _ulog(f"FEHLER beim Recreate: {e}")
        _update_running = False

    threading.Thread(target=pull_and_recreate, daemon=True).start()
    return True, "Update läuft im Hintergrund · Seite lädt neu sobald der Container bereit ist"

def get_update_info():
    cfg = load_config()
    tag = cfg.get("update_channel", "latest")
    remote = get_dockerhub_digest(tag)
    local  = get_local_digest(tag)
    if not remote:
        return {"ok": False, "error": "Docker Hub nicht erreichbar"}
    up_to_date = (remote == local) if local else False
    has_socket = os.path.exists(DOCKER_SOCKET)
    return {
        "ok":          True,
        "up_to_date":  up_to_date,
        "local_digest": (local or "unbekannt")[:19],
        "remote_digest": remote[:19],
        "tag":         tag,
        "has_socket":  has_socket,
        "update_count": 0 if up_to_date else 1,
    }

def fetch_remote_version(tag: str) -> dict:
    """Fetch version.json from GitHub to get remote version number and changelog."""
    try:
        url = f"https://raw.githubusercontent.com/fdreckmann/ev_tracker/{'main' if tag == 'latest' else 'dev'}/version.json"
        r = requests.get(url, timeout=8)
        if r.status_code == 200:
            return r.json()
    except Exception:
        pass
    return {}

@app.route("/api/update/check")
def api_update_check():
    info = get_update_info()
    if info.get("ok") and not info.get("up_to_date"):
        tag = info.get("tag", "latest")
        remote_ver = fetch_remote_version(tag)
        info["remote_version"] = remote_ver.get("version", "")
        info["remote_changelog"] = remote_ver.get("changelog", [])
    return jsonify(info)

@app.route("/api/update/pull", methods=["POST"])
def api_update_pull():
    cfg = load_config()
    tag = cfg.get("update_channel","latest")
    ok, msg = docker_pull_and_restart(tag)
    return jsonify({"ok": ok, "output": msg, "restarting": ok})

@app.route("/api/update/log")
def api_update_log():
    return jsonify({"running": _update_running, "lines": list(_update_log)})

if __name__=="__main__":
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    app.secret_key = _get_secret_key()
    init_db(); start_tracker(); schedule_backup()
    app.run(host="0.0.0.0",port=8080,debug=False)
