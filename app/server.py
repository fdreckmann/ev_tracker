import os, json, time, sqlite3, logging, threading, requests
import xml.etree.ElementTree as ET
from datetime import datetime, timezone, timedelta
from pathlib import Path
from flask import Flask, render_template, request, jsonify, send_file

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger(__name__)

DATA_DIR      = Path(os.environ.get("DATA_DIR", "/data"))
CONFIG_FILE   = DATA_DIR / "config.json"
DB_PATH       = DATA_DIR / "sessions.db"
EXPORT_DIR    = DATA_DIR / "exports"
TEMPLATE_PATH = DATA_DIR / "template.xlsx"

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 10 * 1024 * 1024

DEFAULT_CONFIG = {
    "ha_url":               "http://homeassistant.local:8123",
    "ha_token":             "",
    "charging_sensor":      "sensor.volkswagen_id_id_7_charging_state",
    "odo_sensor":           "sensor.volkswagen_id_id_7_mileage",
    "soc_sensor":           "sensor.volkswagen_id_id_7_state_of_charge",
    "power_sensor":         "sensor.volkswagen_id_id_7_charge_power",  # kW
    "charge_type_sensor":   "",   # optional: entity mit state "ac"/"dc" direkt aus HA
    "charge_speed_sensor":  "",   # optional: separater kW-Sensor (z.B. sensor.*_charge_power)
    "location_sensor":      "device_tracker.wvwzzzed8se059543_position",
    "home_states":          "home,zuhause",
    "notify_service":       "",
    "battery_capacity_kwh": 77.0,
    "price_per_kwh_home":   0.30,    # Heimpreis €/kWh (fixer Tarif)
    "price_per_kwh_ac":     0.45,    # Extern AC Fallback (ohne ENTSO-E)
    "price_per_kwh_dc":     0.75,    # Extern DC Fallback (ohne ENTSO-E)
    "dc_threshold_kw":      22.0,    # Ladeleistung ab der DC erkannt wird
    "entsoe_api_key":       "",      # ENTSO-E API Key
    "entsoe_ac_markup":     3.0,     # ENTSO-E Spotpreis × Faktor für AC
    "entsoe_dc_markup":     6.0,     # ENTSO-E Spotpreis × Faktor für DC
    "poll_interval":        60,
    "car_name":             "VW ID.7",
    "backup_cron":          "",  # z.B. "0 3 * * *" = täglich 03:00
}

# ── Config ────────────────────────────────────────────────────────────────────
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
        start_ts      TEXT,
        end_ts        TEXT,
        odo_start     REAL,
        odo_end       REAL,
        soc_start     REAL,
        soc_end       REAL,
        kwh_charged   REAL,
        cost_eur      REAL,
        cost_manual   INTEGER DEFAULT 0,  -- 1 = manually set by user
        location      TEXT DEFAULT 'unknown',
        charger_type  TEXT DEFAULT 'unknown',  -- 'ac', 'dc', 'unknown'
        max_power_kw  REAL,
        price_per_kwh REAL,
        entsoe_spot   REAL   -- raw ENTSO-E spot price at session start
    )""")
    # live migration
    for col, typedef in [
        ("cost_manual",   "INTEGER DEFAULT 0"),
        ("charger_type",  "TEXT DEFAULT 'unknown'"),
        ("max_power_kw",  "REAL"),
        ("price_per_kwh", "REAL"),
        ("entsoe_spot",   "REAL"),
    ]:
        try:
            con.execute(f"ALTER TABLE sessions ADD COLUMN {col} {typedef}")
            log.info("DB migriert: Spalte '%s' hinzugefügt", col)
        except sqlite3.OperationalError:
            pass
    con.execute("""CREATE TABLE IF NOT EXISTS session_points (
        id          INTEGER PRIMARY KEY AUTOINCREMENT,
        session_id  INTEGER NOT NULL,
        ts          TEXT NOT NULL,
        soc         REAL,
        power_kw    REAL
    )""")
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
               SUM(cost_eur)    AS total_cost,
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
_entsoe_cache = {"price": None, "ts": 0}  # simple 1h cache

def fetch_entsoe_spot(api_key: str) -> float | None:
    """Fetch current DE day-ahead spot price from ENTSO-E in €/kWh."""
    if not api_key:
        return None
    # reuse if < 60 min old
    if _entsoe_cache["price"] is not None and time.time() - _entsoe_cache["ts"] < 3600:
        return _entsoe_cache["price"]
    try:
        now   = datetime.now(timezone.utc)
        start = now.strftime("%Y%m%d%H00")
        end   = (now + timedelta(hours=1)).strftime("%Y%m%d%H00")
        url   = (
            "https://web-api.tp.entsoe.eu/api"
            "?securityToken=" + api_key +
            "&documentType=A44"          # day-ahead prices
            "&in_Domain=10Y1001A1001A83F"  # DE-LU bidding zone
            "&out_Domain=10Y1001A1001A83F"
            "&periodStart=" + start +
            "&periodEnd="   + end
        )
        r    = requests.get(url, timeout=15)
        r.raise_for_status()
        root = ET.fromstring(r.text)
        ns   = "urn:iec62325.351:tc57wg16:451-3:publicationdocument:7:3"
        # Use Clark notation {ns}tag — required for dotted tag names like price.amount
        pts  = root.findall(f".//{{{ns}}}Point")
        if not pts:
            log.warning("ENTSO-E: keine Preispunkte in Antwort — Raw: %s", r.text[:300])
            return None
        # last point = current hour; price in MWh → divide by 1000 for kWh
        price_el  = pts[-1].find(f"{{{ns}}}price.amount")
        if price_el is None:
            log.warning("ENTSO-E: price.amount Element nicht gefunden")
            return None
        price_mwh = float(price_el.text)
        price_kwh = round(price_mwh / 1000, 4)
        _entsoe_cache.update(price=price_kwh, ts=time.time())
        log.info("ENTSO-E Spotpreis: %.4f €/kWh (%.2f €/MWh)", price_kwh, price_mwh)
        return price_kwh
    except Exception as e:
        log.warning("ENTSO-E Fehler: %s", e)
        return None

def calc_extern_price(cfg: dict, charger_type: str, spot: float | None) -> float:
    """Calculate extern price per kWh based on charger type and spot price."""
    is_dc = charger_type == "dc"
    if spot is not None:
        markup = cfg.get("entsoe_dc_markup" if is_dc else "entsoe_ac_markup", 6.0 if is_dc else 3.0)
        return round(spot * markup, 4)
    # fallback to configured fixed price
    return cfg.get("price_per_kwh_dc" if is_dc else "price_per_kwh_ac", 0.75 if is_dc else 0.45)

# ── HA helpers ────────────────────────────────────────────────────────────────
def ha_headers(cfg):
    return {"Authorization": f"Bearer {cfg['ha_token']}", "Content-Type": "application/json"}

def ha_get(entity, cfg):
    r = requests.get(
        f"{cfg['ha_url'].rstrip('/')}/api/states/{entity}",
        headers=ha_headers(cfg), timeout=10)
    r.raise_for_status(); return r.json()

def ha_float(entity, cfg):
    try: return float(ha_get(entity, cfg)["state"])
    except: return None

def ha_charging(sensor, cfg):
    try: return ha_get(sensor, cfg)["state"].lower() in \
         ("charging","laden","true","on","1","conserving")
    except: return False

def ha_location(cfg):
    sensor = cfg.get("location_sensor","").strip()
    if not sensor: return "unknown"
    try:
        state = ha_get(sensor, cfg)["state"].lower().strip()
        home_states = [s.strip().lower() for s in cfg.get("home_states","home").split(",")]
        return "home" if state in home_states else "extern"
    except Exception as e:
        log.warning("Location sensor error: %s", e); return "unknown"

def ha_charger_type(cfg, power_kw: float | None) -> str:
    """Determine AC or DC — HA sensor has priority, power threshold as fallback."""
    # 1. Try dedicated charge type sensor from HA
    type_sensor = cfg.get("charge_type_sensor","").strip()
    if type_sensor:
        try:
            state = ha_get(type_sensor, cfg)["state"].lower().strip()
            if "dc" in state:   return "dc"
            if "ac" in state:   return "ac"
        except Exception as e:
            log.warning("Charge type sensor error: %s", e)
    # 2. Fallback: power threshold
    if power_kw is None: return "unknown"
    threshold = float(cfg.get("dc_threshold_kw", 22.0))
    return "dc" if power_kw > threshold else "ac"

def ha_notify(cfg, title, message):
    service = cfg.get("notify_service","").strip()
    if not service: return
    try:
        parts = service.split(".",1)
        svc   = parts[1] if len(parts)==2 else parts[0]
        requests.post(
            f"{cfg['ha_url'].rstrip('/')}/api/services/notify/{svc}",
            headers=ha_headers(cfg),
            json={"title":title,"message":message}, timeout=8)
    except Exception as e:
        log.warning("Notify error: %s", e)

# ── Tracker ───────────────────────────────────────────────────────────────────
_state = {
    "running":False,"session_active":False,"session_id":None,
    "last_poll":None,"last_error":None,"soc_current":None,
    "odo_current":None,"charging":False,"location":"unknown",
    "charger_type":"unknown","power_kw":None,
    "entsoe_spot":None,"entsoe_ok":False,
}
_stop = threading.Event()

def tracker_loop():
    _state["running"] = True
    session_active = False; session_id = None
    soc_start = odo_start = peak_power = None

    log.info("Tracker gestartet")
    while not _stop.is_set():
        cfg = load_config()
        if not cfg.get("ha_token"):
            _state["last_error"] = "Kein HA Token konfiguriert"
            _stop.wait(10); continue

        try:
            charging     = ha_charging(cfg["charging_sensor"], cfg)
            soc          = ha_float(cfg["soc_sensor"], cfg)
            odo          = ha_float(cfg["odo_sensor"],  cfg)
            # Power: use dedicated speed sensor if configured, else power_sensor
            speed_sensor = cfg.get("charge_speed_sensor","").strip()
            pwr_entity   = speed_sensor or cfg.get("power_sensor","")
            power_kw     = ha_float(pwr_entity, cfg) if pwr_entity else None
            location     = ha_location(cfg)
            charger_type = ha_charger_type(cfg, power_kw)

            _state.update(
                charging=charging, soc_current=soc, odo_current=odo,
                location=location, charger_type=charger_type, power_kw=power_kw,
                session_active=session_active,
                last_poll=datetime.now().isoformat(timespec="seconds"),
                last_error=None,
            )

            con = sqlite3.connect(DB_PATH); cur = con.cursor()

            if charging and not session_active:
                soc_start = soc; odo_start = odo; peak_power = power_kw or 0

                # fetch ENTSO-E spot price for extern sessions
                spot = None
                if location == "extern":
                    spot = fetch_entsoe_spot(cfg.get("entsoe_api_key",""))
                    _state["entsoe_spot"] = spot
                    _state["entsoe_ok"]   = spot is not None

                price_kwh = (
                    cfg["price_per_kwh_home"] if location == "home"
                    else calc_extern_price(cfg, charger_type, spot)
                )

                cur.execute("""INSERT INTO sessions
                    (start_ts,odo_start,soc_start,location,charger_type,max_power_kw,price_per_kwh,entsoe_spot)
                    VALUES (?,?,?,?,?,?,?,?)""",
                    (datetime.now().isoformat(timespec="seconds"),
                     odo_start, soc_start, location, charger_type, power_kw, price_kwh, spot))
                con.commit(); session_id = cur.lastrowid; session_active = True
                _state["session_id"] = session_id

                charger_lbl = "DC ⚡" if charger_type=="dc" else "AC 🔌" if charger_type=="ac" else "?"
                # log initial point
                try:
                    con.execute(
                        "INSERT INTO session_points (session_id, ts, soc, power_kw) VALUES (?,?,?,?)",
                        (session_id, datetime.now().isoformat(timespec="seconds"), soc_start, power_kw))
                    con.commit()
                except: pass
                log.info("⚡ Session #%d | %s | %s | %.2f €/kWh%s | SOC=%s%%",
                         session_id, location.upper(), charger_lbl, price_kwh,
                         f" (Spot: {spot:.4f})" if spot else " (Fallback)", soc_start)
                ha_notify(cfg,
                    f"⚡ {cfg['car_name']} lädt",
                    f"{'🏠 Zuhause' if location=='home' else '⚡ Extern'} · "
                    f"{charger_lbl} · {price_kwh:.2f} €/kWh · SOC {soc_start or '?'}%")

            elif charging and session_active:
                # log data point for charge curve
                try:
                    con.execute(
                        "INSERT INTO session_points (session_id, ts, soc, power_kw) VALUES (?,?,?,?)",
                        (session_id, datetime.now().isoformat(timespec="seconds"), soc, power_kw))
                    con.commit()
                except: pass
                # track peak power during session for AC/DC detection
                if power_kw and (peak_power is None or power_kw > peak_power):
                    peak_power = power_kw
                    new_type = ha_charger_type(cfg, peak_power)
                    if new_type != charger_type:
                        # update charger type and recalc price if type changed
                        spot = _state.get("entsoe_spot")
                        price_kwh = (cfg["price_per_kwh_home"] if location=="home"
                                     else calc_extern_price(cfg, new_type, spot))
                        cur.execute("UPDATE sessions SET charger_type=?,max_power_kw=?,price_per_kwh=? WHERE id=?",
                                    (new_type, peak_power, price_kwh, session_id))
                        con.commit()
                        charger_type = new_type
                        log.info("Session #%d: Ladertyp aktualisiert auf %s (%.1f kW)",
                                 session_id, new_type.upper(), peak_power)

            elif not charging and session_active:
                kwh = cost = None
                # load current price from DB (might have been manually set)
                row = con.execute("SELECT price_per_kwh,cost_manual FROM sessions WHERE id=?",
                                  (session_id,)).fetchone()
                db_price   = row[0] if row else None
                cost_manual= row[1] if row else 0

                if soc is not None and soc_start is not None:
                    kwh = round(max(0.0, soc - soc_start) / 100.0 * cfg["battery_capacity_kwh"], 2)
                    if not cost_manual:  # don't overwrite manual cost
                        price = db_price or cfg["price_per_kwh_home"]
                        cost  = round(kwh * price, 2)

                cur.execute("""UPDATE sessions
                    SET end_ts=?,odo_end=?,soc_end=?,kwh_charged=?,
                        cost_eur=CASE WHEN cost_manual=1 THEN cost_eur ELSE ? END,
                        max_power_kw=?
                    WHERE id=?""",
                    (datetime.now().isoformat(timespec="seconds"), odo, soc,
                     kwh, cost, peak_power, session_id))
                con.commit()
                session_active = False
                _state.update(session_active=False, session_id=None)

                charger_lbl = "DC" if charger_type=="dc" else "AC"
                log.info("✅ Session #%d | %s | %s | %.2f kWh | %.2f €",
                         session_id, location.upper(), charger_lbl, kwh or 0, cost or 0)
                ha_notify(cfg,
                    f"✅ {cfg['car_name']} fertig",
                    f"{'🏠 Zuhause' if location=='home' else '⚡ Extern'} · "
                    f"{charger_lbl} · {kwh or 0:.2f} kWh · {cost or 0:.2f} €")
                session_id = None; peak_power = None

            con.close()
        except Exception as e:
            log.warning("Tracker error: %s", e); _state["last_error"] = str(e)

        _stop.wait(cfg.get("poll_interval", 60))
    _state["running"] = False

def start_tracker():
    _stop.clear()
    threading.Thread(target=tracker_loop, daemon=True).start()

# ── Routes ────────────────────────────────────────────────────────────────────
@app.route("/")
def index():
    return render_template("index.html", cfg=load_config(), state=_state,
                           has_template=TEMPLATE_PATH.exists())

@app.route("/api/config", methods=["GET"])
def api_get_config(): return jsonify(load_config())

@app.route("/api/config", methods=["POST"])
def api_save_config():
    data = request.json; cfg = load_config()
    # backup_cron handled separately via /api/backup/cron
    floats = {"battery_capacity_kwh","price_per_kwh_home","price_per_kwh_ac",
              "price_per_kwh_dc","dc_threshold_kw","entsoe_ac_markup","entsoe_dc_markup"}
    ints   = {"poll_interval"}
    for key in DEFAULT_CONFIG:
        if key in data:
            v = data[key]
            if key in floats: v = float(v)
            elif key in ints: v = int(v)
            cfg[key] = v
    save_config(cfg); return jsonify({"ok": True})

@app.route("/api/status")
def api_status(): return jsonify(_state)

@app.route("/api/sessions")
def api_sessions():
    return jsonify(get_sessions(
        request.args.get("year",     type=int),
        request.args.get("month",    type=int),
        request.args.get("location", default="all"),
    ))

@app.route("/api/sessions/<int:sid>", methods=["DELETE"])
def api_delete_session(sid):
    con = sqlite3.connect(DB_PATH)
    con.execute("DELETE FROM sessions WHERE id=?", (sid,))
    con.commit(); con.close()
    return jsonify({"ok": True})

@app.route("/api/sessions/<int:sid>/cost", methods=["POST"])
def api_update_cost(sid):
    """Manually override cost for a session."""
    data = request.json
    cost      = float(data["cost_eur"])
    price_kwh = data.get("price_per_kwh")
    con = sqlite3.connect(DB_PATH); cur = con.cursor()
    if price_kwh is not None:
        # recalc cost from price
        row = cur.execute("SELECT kwh_charged FROM sessions WHERE id=?", (sid,)).fetchone()
        if row and row[0]:
            cost = round(float(row[0]) * float(price_kwh), 2)
        cur.execute("UPDATE sessions SET cost_eur=?,price_per_kwh=?,cost_manual=1 WHERE id=?",
                    (cost, float(price_kwh), sid))
    else:
        cur.execute("UPDATE sessions SET cost_eur=?,cost_manual=1 WHERE id=?", (cost, sid))
    con.commit(); con.close()
    log.info("Session #%d Kosten manuell: %.2f €", sid, cost)
    return jsonify({"ok": True, "cost_eur": cost})

@app.route("/api/sessions/<int:sid>/cost", methods=["DELETE"])
def api_reset_cost(sid):
    """Reset manual cost override — recalc from price_per_kwh."""
    con = sqlite3.connect(DB_PATH); cur = con.cursor()
    row = cur.execute("SELECT kwh_charged, price_per_kwh FROM sessions WHERE id=?", (sid,)).fetchone()
    if row and row[0] and row[1]:
        cost = round(float(row[0]) * float(row[1]), 2)
        cur.execute("UPDATE sessions SET cost_eur=?,cost_manual=0 WHERE id=?", (cost, sid))
        con.commit()
    con.close(); return jsonify({"ok": True})

@app.route("/api/sessions/<int:sid>/points")
def api_session_points(sid):
    con = sqlite3.connect(DB_PATH); con.row_factory = sqlite3.Row
    rows = con.execute(
        "SELECT ts, soc, power_kw FROM session_points WHERE session_id=? ORDER BY ts",
        (sid,)).fetchall()
    con.close()
    return jsonify([dict(r) for r in rows])

@app.route("/api/stats/monthly")
def api_monthly_stats(): return jsonify(get_monthly_stats())

@app.route("/api/entsoe/test", methods=["POST"])
def api_entsoe_test():
    key = request.json.get("entsoe_api_key","").strip()
    if not key:
        return jsonify({"ok":False,"error":"Kein API Key angegeben"})
    _entsoe_cache["price"] = None  # force fresh fetch
    price = fetch_entsoe_spot(key)
    if price is not None:
        return jsonify({"ok":True,"price_kwh":price,"price_mwh":round(price*1000,2)})
    return jsonify({"ok":False,"error":"Kein Preis erhalten — Key prüfen oder warte auf Tagespreise (ab ~13 Uhr verfügbar)"})

@app.route("/api/test-connection", methods=["POST"])
def api_test():
    d = request.json
    if not d.get("ha_token"):
        d["ha_token"] = load_config().get("ha_token","")
    try:
        r = requests.get(
            f"{d['ha_url'].rstrip('/')}/api/states/{d['charging_sensor']}",
            headers={"Authorization":f"Bearer {d['ha_token']}"}, timeout=8)
        r.raise_for_status(); s = r.json()
        loc_info = ""
        if d.get("location_sensor"):
            try:
                lr = requests.get(
                    f"{d['ha_url'].rstrip('/')}/api/states/{d['location_sensor']}",
                    headers={"Authorization":f"Bearer {d['ha_token']}"}, timeout=8)
                lr.raise_for_status()
                loc_info = f" · Standort: \"{lr.json().get('state')}\""
            except: loc_info=" · ⚠ Standort-Sensor nicht gefunden"
        pwr_info = ""
        if d.get("power_sensor"):
            try:
                pr = requests.get(
                    f"{d['ha_url'].rstrip('/')}/api/states/{d['power_sensor']}",
                    headers={"Authorization":f"Bearer {d['ha_token']}"}, timeout=8)
                pr.raise_for_status()
                pwr_info = f" · Leistung: {pr.json().get('state')} kW"
            except: pwr_info=" · ⚠ Leistungs-Sensor nicht gefunden"
        type_info = ""
        if d.get("charge_type_sensor"):
            try:
                tr = requests.get(
                    f"{d['ha_url'].rstrip('/')}/api/states/{d['charge_type_sensor']}",
                    headers={"Authorization":f"Bearer {d['ha_token']}"}, timeout=8)
                tr.raise_for_status()
                type_info = f" · Ladetyp: '{tr.json().get('state')}'"
            except: type_info=" · ⚠ Ladetyp-Sensor nicht gefunden"
        return jsonify({"ok":True,"state":s.get("state"),
                        "name":s.get("attributes",{}).get("friendly_name",d["charging_sensor"]),
                        "loc_info":loc_info,"pwr_info":pwr_info,"type_info":type_info})
    except requests.HTTPError as e:
        return jsonify({"ok":False,"error":f"HTTP {e.response.status_code}"})
    except Exception as e:
        return jsonify({"ok":False,"error":str(e)})

@app.route("/api/template", methods=["POST"])
def api_upload_template():
    if "file" not in request.files:
        return jsonify({"ok":False,"error":"Keine Datei"}), 400
    f = request.files["file"]
    if not f.filename.endswith(".xlsx"):
        return jsonify({"ok":False,"error":"Nur .xlsx"}), 400
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    f.save(TEMPLATE_PATH)
    return jsonify({"ok":True,"filename":f.filename,"size":TEMPLATE_PATH.stat().st_size})

@app.route("/api/template", methods=["DELETE"])
def api_delete_template():
    if TEMPLATE_PATH.exists(): TEMPLATE_PATH.unlink()
    return jsonify({"ok":True})

@app.route("/api/template/info")
def api_template_info():
    if TEMPLATE_PATH.exists():
        return jsonify({"exists":True,"size":TEMPLATE_PATH.stat().st_size,
                        "modified":datetime.fromtimestamp(
                            TEMPLATE_PATH.stat().st_mtime).isoformat(timespec="seconds")})
    return jsonify({"exists":False})

@app.route("/api/template/preview")
def api_template_preview():
    if not TEMPLATE_PATH.exists():
        return jsonify({"ok":False,"error":"Kein Template"})
    try:
        import openpyxl
        from export_excel import match_column
        wb = openpyxl.load_workbook(TEMPLATE_PATH, read_only=True); ws = wb.active
        col_map = {}
        for row in ws.iter_rows(values_only=False):
            filled = [c for c in row if c.value is not None and str(c.value).strip()]
            if len(filled) >= 2:
                for cell in row:
                    col_map[cell.column] = {
                        "header": str(cell.value) if cell.value else "",
                        "mapped_to": match_column(cell.value),
                        "col_letter": cell.column_letter,
                    }
                break
        wb.close()
        return jsonify({"ok":True,"columns":col_map})
    except Exception as e:
        return jsonify({"ok":False,"error":str(e)})

@app.route("/api/export")
def api_export():
    from export_excel import export
    y   = request.args.get("year",     datetime.now().year,  type=int)
    m   = request.args.get("month",    datetime.now().month, type=int)
    loc = request.args.get("location", "all")
    override = json.loads(request.args.get("col_override","null") or "null")
    return send_file(export(y, m, loc, col_override=override), as_attachment=True)



# ── Backup / Restore ──────────────────────────────────────────────────────────
import zipfile, shutil, re
from threading import Timer

BACKUP_DIR = DATA_DIR / "backups"

def create_backup(label="manual") -> Path:
    """Zip all of /data (except backups folder itself) into /data/backups/."""
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    ts   = datetime.now().strftime("%Y%m%d_%H%M%S")
    name = f"ev-tracker_backup_{label}_{ts}.zip"
    out  = BACKUP_DIR / name
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as zf:
        for item in DATA_DIR.rglob("*"):
            if BACKUP_DIR in item.parents or item == out:
                continue
            zf.write(item, item.relative_to(DATA_DIR))
    size = out.stat().st_size
    log.info("Backup erstellt: %s (%.1f KB)", name, size/1024)
    # keep only last 10 backups per label type
    all_backups = sorted(BACKUP_DIR.glob("*.zip"), key=lambda p: p.stat().st_mtime)
    while len(all_backups) > 10:
        all_backups.pop(0).unlink()
    return out

def restore_backup(zip_path: Path):
    """Restore /data from a backup zip (excluding backups folder)."""
    with zipfile.ZipFile(zip_path, "r") as zf:
        for member in zf.namelist():
            if member.startswith("backups/"):
                continue
            zf.extract(member, DATA_DIR)
    log.info("Restore abgeschlossen von: %s", zip_path.name)

# ── Cron scheduler ────────────────────────────────────────────────────────────
_backup_timer: Timer | None = None

def parse_cron_next(cron_expr: str) -> float | None:
    """Very simple cron parser — supports: @daily @weekly @monthly or 'min hour * * *'."""
    now   = datetime.now()
    expr  = cron_expr.strip().lower()
    if expr in ("@daily",   "0 0 * * *"):
        nxt = now.replace(hour=0,minute=0,second=0,microsecond=0) + timedelta(days=1)
    elif expr in ("@weekly", "0 0 * * 0"):
        days = (6 - now.weekday()) % 7 or 7
        nxt  = now.replace(hour=0,minute=0,second=0,microsecond=0) + timedelta(days=days)
    elif expr in ("@monthly","0 0 1 * *"):
        if now.month == 12:
            nxt = now.replace(year=now.year+1,month=1,day=1,hour=0,minute=0,second=0,microsecond=0)
        else:
            nxt = now.replace(month=now.month+1,day=1,hour=0,minute=0,second=0,microsecond=0)
    else:
        # simple "MIN HOUR * * *" pattern
        try:
            parts = expr.split()
            if len(parts) >= 2:
                minute = int(parts[0]); hour = int(parts[1])
                nxt = now.replace(hour=hour,minute=minute,second=0,microsecond=0)
                if nxt <= now:
                    nxt += timedelta(days=1)
            else:
                return None
        except:
            return None
    return (nxt - now).total_seconds()

def schedule_backup():
    global _backup_timer
    cfg  = load_config()
    cron = cfg.get("backup_cron","").strip()
    if not cron:
        return
    secs = parse_cron_next(cron)
    if secs is None or secs <= 0:
        return
    def run():
        try:
            create_backup("auto")
            log.info("Auto-Backup abgeschlossen")
        except Exception as e:
            log.warning("Auto-Backup Fehler: %s", e)
        schedule_backup()   # reschedule
    _backup_timer = Timer(secs, run)
    _backup_timer.daemon = True
    _backup_timer.start()
    nxt = datetime.now() + timedelta(seconds=secs)
    log.info("Nächstes Auto-Backup: %s", nxt.strftime("%d.%m.%Y %H:%M"))

# ── Backup routes ─────────────────────────────────────────────────────────────
@app.route("/api/backup/list")
def api_backup_list():
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    backups = []
    for f in sorted(BACKUP_DIR.glob("*.zip"), key=lambda p: p.stat().st_mtime, reverse=True):
        backups.append({
            "name":     f.name,
            "size":     f.stat().st_size,
            "modified": datetime.fromtimestamp(f.stat().st_mtime).isoformat(timespec="seconds"),
        })
    cfg = load_config()
    # calc next backup time
    cron = cfg.get("backup_cron","")
    next_backup = None
    if cron:
        secs = parse_cron_next(cron)
        if secs:
            next_backup = (datetime.now()+timedelta(seconds=secs)).strftime("%d.%m.%Y %H:%M")
    return jsonify({"backups": backups, "next_backup": next_backup, "cron": cron})

@app.route("/api/backup/create", methods=["POST"])
def api_backup_create():
    try:
        out = create_backup("manual")
        return jsonify({"ok":True,"name":out.name,"size":out.stat().st_size})
    except Exception as e:
        return jsonify({"ok":False,"error":str(e)})

@app.route("/api/backup/download/<filename>")
def api_backup_download(filename):
    # sanitize filename
    if ".." in filename or "/" in filename:
        return jsonify({"error":"ungültig"}), 400
    path = BACKUP_DIR / filename
    if not path.exists():
        return jsonify({"error":"nicht gefunden"}), 404
    return send_file(path, as_attachment=True)

@app.route("/api/backup/restore", methods=["POST"])
def api_backup_restore():
    """Restore from an existing backup file in /data/backups/."""
    name = request.json.get("name","")
    if ".." in name or "/" in name:
        return jsonify({"ok":False,"error":"ungültig"}), 400
    path = BACKUP_DIR / name
    if not path.exists():
        return jsonify({"ok":False,"error":"Datei nicht gefunden"}), 404
    try:
        restore_backup(path)
        return jsonify({"ok":True})
    except Exception as e:
        return jsonify({"ok":False,"error":str(e)})

@app.route("/api/backup/upload", methods=["POST"])
def api_backup_upload():
    """Upload a backup zip and restore it."""
    if "file" not in request.files:
        return jsonify({"ok":False,"error":"Keine Datei"}), 400
    f = request.files["file"]
    if not f.filename.endswith(".zip"):
        return jsonify({"ok":False,"error":"Nur .zip Dateien"}), 400
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    tmp = BACKUP_DIR / f"upload_{datetime.now().strftime('%Y%m%d_%H%M%S')}.zip"
    f.save(tmp)
    try:
        restore_backup(tmp)
        return jsonify({"ok":True,"restored":tmp.name})
    except Exception as e:
        tmp.unlink(missing_ok=True)
        return jsonify({"ok":False,"error":str(e)})

@app.route("/api/backup/cron", methods=["POST"])
def api_backup_cron():
    global _backup_timer
    cron = request.json.get("cron","").strip()
    cfg  = load_config(); cfg["backup_cron"] = cron; save_config(cfg)
    if _backup_timer:
        _backup_timer.cancel()
    if cron:
        schedule_backup()
        secs = parse_cron_next(cron)
        nxt  = (datetime.now()+timedelta(seconds=secs)).strftime("%d.%m.%Y %H:%M") if secs else "?"
        return jsonify({"ok":True,"next":nxt})
    return jsonify({"ok":True,"next":None})

@app.route("/api/backup/delete/<filename>", methods=["DELETE"])
def api_backup_delete(filename):
    if ".." in filename or "/" in filename:
        return jsonify({"ok":False}), 400
    path = BACKUP_DIR / filename
    if path.exists(): path.unlink()
    return jsonify({"ok":True})

# ── Update / Git ──────────────────────────────────────────────────────────────
import subprocess

GIT_REPO = Path(os.environ.get("GIT_DIR", str(DATA_DIR.parent / "ev-tracker-src")))

def git_run(*args):
    try:
        r = subprocess.run(
            ["git"] + list(args),
            cwd=str(GIT_REPO),
            capture_output=True, text=True, timeout=30
        )
        return r.returncode, (r.stdout + r.stderr).strip()
    except Exception as e:
        return 1, str(e)

def get_update_info():
    rc, local_hash = git_run("rev-parse", "HEAD")
    if rc != 0:
        return {"ok": False, "error": "Kein Git-Repo — siehe GITHUB_SETUP.md"}
    rc2, _ = git_run("fetch", "origin", "main")
    if rc2 != 0:
        return {"ok": False, "error": "GitHub nicht erreichbar"}
    rc3, remote_hash = git_run("rev-parse", "origin/main")
    up_to_date = local_hash[:8] == remote_hash[:8]
    changelog = []
    if not up_to_date:
        rc4, log_out = git_run("log", "--oneline", "HEAD..origin/main")
        if rc4 == 0 and log_out:
            changelog = [l.strip() for l in log_out.splitlines() if l.strip()]
    return {
        "ok": True,
        "up_to_date": up_to_date,
        "local_hash": local_hash[:8],
        "remote_hash": remote_hash[:8],
        "changelog": changelog,
        "update_count": len(changelog),
    }

@app.route("/api/update/check")
def api_update_check():
    return jsonify(get_update_info())

@app.route("/api/update/pull", methods=["POST"])
def api_update_pull():
    rc, out = git_run("pull", "origin", "main")
    if rc != 0:
        return jsonify({"ok": False, "error": out})
    def restart():
        time.sleep(2)
        os.execv("/usr/local/bin/python", ["python", "server.py"])
    threading.Thread(target=restart, daemon=True).start()
    return jsonify({"ok": True, "output": out, "restarting": True})


if __name__ == "__main__":
    init_db(); start_tracker(); schedule_backup()
    app.run(host="0.0.0.0", port=8080, debug=False)
