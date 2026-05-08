import os, json, time, sqlite3, logging, threading, requests
import xml.etree.ElementTree as ET
from datetime import datetime, timezone, timedelta
from pathlib import Path
from flask import Flask, render_template, request, jsonify, send_file, make_response
from providers import get_provider, get_all_capabilities, get_config_fields, PROVIDERS

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger(__name__)

APP_VERSION   = "1.4.3"

CHANGELOG = [
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
    "meter_source":           "none", # none | ha | shelly | tasmota
    "meter_sensor":           "",     # HA entity_id for meter reading
    "meter_device_ip":        "",     # IP for Shelly / Tasmota
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
        meter_old     REAL, meter_new REAL
    )""")
    # migrate existing DB: add meter columns if missing
    for col in ("meter_old", "meter_new"):
        try: con.execute(f"ALTER TABLE sessions ADD COLUMN {col} REAL")
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

def get_sessions(year=None, month=None, location=None, limit=50):
    where = ["end_ts IS NOT NULL"]; params = []
    if year and month:
        where.append("start_ts LIKE ?"); params.append(f"{year:04d}-{month:02d}%")
    if location and location != "all":
        where.append("location = ?"); params.append(location)
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
_state = {
    "running":False,"session_active":False,"session_id":None,
    "last_poll":None,"last_error":None,"soc_current":None,
    "odo_current":None,"charging":False,"location":"unknown",
    "charger_type":"unknown","power_kw":None,
    "entsoe_spot":None,"provider":"ha",
    "provider_name":"Home Assistant",
}
_stop = threading.Event()

def read_meter_value():
    cfg = load_config()
    source = cfg.get("meter_source", "none")
    try:
        if source == "ha":
            entity = cfg.get("meter_sensor", "").strip()
            if not entity: return None
            url = cfg.get("ha_url","").rstrip("/") + f"/api/states/{entity}"
            r = requests.get(url, headers={"Authorization": f"Bearer {cfg.get('ha_token','')}"},
                             timeout=5)
            return float(r.json()["state"])
        elif source == "shelly":
            ip = cfg.get("meter_device_ip","").strip()
            if not ip: return None
            # Shelly EM: /emeter/0 → total in Wh; Shelly PM: /meter/0 → total in Wh
            for endpoint in ("/emeter/0", "/meter/0"):
                try:
                    r = requests.get(f"http://{ip}{endpoint}", timeout=5)
                    data = r.json()
                    if "total" in data:
                        return round(data["total"] / 1000, 3)  # Wh → kWh
                except Exception: pass
        elif source == "tasmota":
            ip = cfg.get("meter_device_ip","").strip()
            if not ip: return None
            r = requests.get(f"http://{ip}/cm?cmnd=Status%208", timeout=5)
            return float(r.json()["StatusSNS"]["ENERGY"]["Total"])
    except Exception as e:
        log.warning("Meter read error (%s): %s", source, e)
    return None

def tracker_loop():
    _state["running"] = True
    session_active = False; session_id = None
    soc_start = odo_start = peak_power = None

    log.info("Tracker gestartet")
    while not _stop.is_set():
        cfg      = load_config()
        provider_id = cfg.get("provider","ha")

        try:
            provider = get_provider(provider_id, cfg)
            state    = provider.get_state()

            if state.error:
                _state["last_error"] = state.error
                _stop.wait(cfg.get("poll_interval",60)); continue

            charging     = state.charging or False
            soc          = state.soc
            odo          = state.odometer
            power_kw     = state.charge_power
            location     = state.location or "unknown"
            charger_type = state.charge_type or "unknown"

            _state.update(
                charging=charging, soc_current=soc, odo_current=odo,
                location=location, charger_type=charger_type, power_kw=power_kw,
                session_active=session_active,
                last_poll=datetime.now().isoformat(timespec="seconds"),
                last_error=None, provider=provider_id,
                provider_name=PROVIDERS[provider_id].PROVIDER_NAME,
            )

            con = sqlite3.connect(DB_PATH); cur = con.cursor()

            if charging and not session_active:
                soc_start = soc; odo_start = odo; peak_power = power_kw or 0
                meter_start_val = read_meter_value()
                spot = fetch_entsoe_spot(cfg.get("entsoe_api_key","")) if location=="extern" else None
                _state["entsoe_spot"] = spot
                price_kwh = (cfg["price_per_kwh_home"] if location=="home"
                             else calc_extern_price(cfg, charger_type, spot))
                cur.execute("""INSERT INTO sessions
                    (start_ts,odo_start,soc_start,location,charger_type,
                     max_power_kw,price_per_kwh,entsoe_spot,provider,meter_old)
                    VALUES (?,?,?,?,?,?,?,?,?,?)""",
                    (datetime.now().isoformat(timespec="seconds"),
                     odo_start,soc_start,location,charger_type,power_kw,price_kwh,spot,provider_id,
                     meter_start_val))
                con.commit(); session_id=cur.lastrowid; session_active=True
                _state["session_id"]=session_id
                cur.execute("INSERT INTO session_points (session_id,ts,soc,power_kw) VALUES (?,?,?,?)",
                            (session_id,datetime.now().isoformat(timespec="seconds"),soc_start,power_kw))
                con.commit()
                log.info("⚡ Session #%d | %s | %s | %.2f €/kWh",
                         session_id,location.upper(),charger_type.upper(),price_kwh)
                ha_notify(cfg,f"⚡ {cfg['car_name']} lädt",
                    f"{'🏠 Zuhause' if location=='home' else '⚡ Extern'} · "
                    f"{'DC' if charger_type=='dc' else 'AC'} · {price_kwh:.2f} €/kWh · SOC {soc_start or '?'}%")

            elif charging and session_active:
                cur.execute("INSERT INTO session_points (session_id,ts,soc,power_kw) VALUES (?,?,?,?)",
                            (session_id,datetime.now().isoformat(timespec="seconds"),soc,power_kw))
                con.commit()
                if power_kw and (peak_power is None or power_kw > peak_power):
                    peak_power = power_kw
                    new_type = "dc" if power_kw > float(cfg.get("dc_threshold_kw",22)) else "ac"
                    if new_type != charger_type:
                        spot = _state.get("entsoe_spot")
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
                    kwh  = round(max(0.0,soc-soc_start)/100.0*cfg["battery_capacity_kwh"],2)
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
                _state.update(session_active=False,session_id=None)
                log.info("✅ Session #%d | %.2f kWh | %.2f €",session_id,kwh or 0,cost or 0)
                ha_notify(cfg,f"✅ {cfg['car_name']} fertig",
                    f"{'🏠' if location=='home' else '⚡'} · {kwh or 0:.2f} kWh · {cost or 0:.2f} €")
                session_id=None; peak_power=None
            con.close()

        except Exception as e:
            log.warning("Tracker error: %s", e); _state["last_error"]=str(e)
        _stop.wait(cfg.get("poll_interval",60))
    _state["running"]=False

def start_tracker():
    _stop.clear()
    threading.Thread(target=tracker_loop, daemon=True).start()

# ── Routes ────────────────────────────────────────────────────────────────────
@app.route("/")
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
                           changelog=CHANGELOG))
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
def api_status(): return jsonify(_state)

@app.route("/api/sessions")
def api_sessions():
    return jsonify(get_sessions(
        request.args.get("year",type=int),
        request.args.get("month",type=int),
        request.args.get("location",default="all"),
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

def docker_pull_and_restart(tag: str):
    """Pull new image in background, then stop/remove/recreate container."""
    if not os.path.exists(DOCKER_SOCKET):
        return False, "Docker Socket nicht gefunden"

    # Read container config before starting background thread
    try:
        containers = docker_socket_request("GET", "/containers/json?all=1")
        container_id = None
        container_name = None
        for c in containers.get("data", []):
            names = c.get("Names", [])
            if any("ev-tracker" in n for n in names):
                container_id = c["Id"]
                container_name = names[0].lstrip("/") if names else "ev-tracker"
                break
        if not container_id:
            return False, "Container 'ev-tracker' nicht gefunden"

        inspect = docker_socket_request("GET", f"/containers/{container_id}/json")
        if inspect["status"] != 200:
            return False, "Container-Konfiguration nicht lesbar"

        old_cfg     = inspect["data"]
        host_config = old_cfg.get("HostConfig", {})
        env         = old_cfg.get("Config", {}).get("Env", [])
        labels      = old_cfg.get("Config", {}).get("Labels", {})
    except Exception as e:
        return False, str(e)

    def pull_and_recreate():
        import time as _time
        import socket as _socket, json as _json
        try:
            # Pull with long timeout (no limit via raw socket)
            sock = _socket.socket(_socket.AF_UNIX, _socket.SOCK_STREAM)
            sock.settimeout(600)
            sock.connect(DOCKER_SOCKET)
            req = (f"POST /images/create?fromImage={DOCKER_HUB_REPO}&tag={tag} HTTP/1.1\r\n"
                   f"Host: localhost\r\nContent-Length: 0\r\n\r\n")
            sock.send(req.encode())
            # drain response (streaming JSON lines from Docker)
            buf = b""
            while True:
                chunk = sock.recv(4096)
                if not chunk: break
                buf += chunk
            sock.close()
            header = buf.split(b"\r\n")[0]
            status = int(header.split(b" ")[1]) if len(header.split(b" ")) > 1 else 0
            if status not in (200, 204):
                log.error("Pull fehlgeschlagen: HTTP %s", status)
                return
        except Exception as e:
            log.error("Pull error: %s", e)
            return

        _time.sleep(1)
        try:
            docker_socket_request("POST",   f"/containers/{container_id}/stop")
            _time.sleep(1)
            docker_socket_request("DELETE", f"/containers/{container_id}")
            _time.sleep(1)
            new = docker_socket_request(
                "POST",
                f"/containers/create?name={container_name}",
                {"Image": f"{DOCKER_HUB_REPO}:{tag}",
                 "Env": env, "Labels": labels, "HostConfig": host_config}
            )
            new_id = (new.get("data") or {}).get("Id")
            if new_id:
                docker_socket_request("POST", f"/containers/{new_id}/start")
        except Exception as e:
            log.error("Recreate error: %s", e)

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

@app.route("/api/update/check")
def api_update_check(): return jsonify(get_update_info())

@app.route("/api/update/pull", methods=["POST"])
def api_update_pull():
    cfg = load_config()
    tag = cfg.get("update_channel","latest")
    ok, msg = docker_pull_and_restart(tag)
    return jsonify({"ok": ok, "output": msg, "restarting": ok})

if __name__=="__main__":
    init_db(); start_tracker(); schedule_backup()
    app.run(host="0.0.0.0",port=8080,debug=False)
