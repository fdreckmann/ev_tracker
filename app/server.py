import sys, os, json, time, sqlite3, logging, threading, requests, hashlib, secrets, functools, re
# Doppelimport-Fix: VOR jedem weiteren Import — Blueprints rufen `from server import X` auf.
# setdefault: falls schon gesetzt (gunicorn re-import), bleibt das Original erhalten.
sys.modules.setdefault("server", sys.modules[__name__])
from typing import Optional
import smtplib, email
import xml.etree.ElementTree as ET
from datetime import datetime, timezone, timedelta
from pathlib import Path

from flask import Flask, render_template, request, jsonify, send_file, make_response, session, redirect, url_for, g, Response
from providers import get_provider, get_all_capabilities, get_config_fields, PROVIDERS
from meter_providers import read_meter as _read_meter_impl, MeterResult
from core.location import effective_session_location, normalize_location

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger(__name__)

# ── Core module imports ───────────────────────────────────────────────────────
from core.db import _get_db, close_db_if_owned, DB_PATH, DATA_DIR
from core.config import (load_config, save_config, DEFAULT_CONFIG, CONFIG_FILE,
                          VEHICLE_SPECIFIC_KEYS)
from core.security import (_hash_password, _password_ok, _get_secret_key, _has_users,
    _get_user_by_email, _get_user_by_id, _current_user, require_login, require_admin, require_auth,
    _get_user_permissions, has_permission, require_permission, _safe_next, _audit,
    ALL_PERMISSIONS, DEFAULT_ROLE_PERMISSIONS)
from core.tokens import _API_SCOPES, _hash_token, _check_api_token, _require_api_token
from routes import register_blueprints
import core.state as _core_state

from version import APP_VERSION

CHANGELOG = [
    {"version":"2.0.0","changes":[
        "Export-Lokalisierung vollständig: alle Spaltenüberschriften und Labels auf Deutsch/Englisch",
        "Platzhalter-Ersetzung erweitert: {{total_sessions}}, {{total_km}}, {{avg_charge_power_kw}} u.v.m.",
        "Export-Vorschau: echte XLSX-Datei wird erzeugt und als Grid zurückgegeben",
        "Download-Token: Vorschau-XLSX per /api/export/download/<token> abrufbar (30 Min. gültig)",
        "Zählertest: Body-Parameter werden ohne Speichern für den Test verwendet",
        "Zählertest: Erweiterte Antwort mit provider, endpoint, raw_value, unit, normalized_from",
        "meter_value_unit Default auf 'auto' geändert",
        "to_row(): duration_hours aus Sekunden berechnet, charger_type aus Standort abgeleitet",
    ]},
    {"version":"1.9.9","changes":[
        "Passkey (WebAuthn/FIDO2) Authentifizierung: Anmelden per Fingerabdruck, Face ID oder Hardware-Key",
        "Passkey-Registrierung und -Verwaltung in den Sicherheitseinstellungen",
        "Mehrere Passkeys pro Benutzer möglich (mit eigenem Namen)",
        "Passkey-Login-Button auf der Anmeldeseite",
    ]},
    {"version":"1.9.3","changes":[
        "Automatische Template-Analyse mit Konfidenz-Score",
        "Zellmapping für Einzelzellen (Header/Footer-Bereich)",
        "Platzhalter-Erkennung ({{field_name}}) im Template",
        "Neuer Analyse-Button mit Vorschau und Übernahme-Funktion",
    ]},
    {"version":"1.9.0","changes":[
        "Passwort-Reset per E-Mail (Token-basiert)",
        "Benutzer-Einladungen per E-Mail",
        "Brute-force-Schutz: Account-Sperrung nach 5 Fehlversuchen",
        "2FA Backup-Codes (8 Einmalcodes pro Benutzer)",
        "Verbesserte Sicherheit: CSRF-Token in AJAX-Requests",
    ]},
    {"version":"1.8.0","changes":[
        "Vollständige Benutzerverwaltung (Admin + Benutzer-Rollen)",
        "Login mit E-Mail + Passwort (Multi-User)",
        "Setup-Assistent beim ersten Start",
        "Admin kann Benutzer anlegen, bearbeiten, deaktivieren, löschen",
        "2FA pro Benutzer (TOTP) mit Code-Bestätigung",
        "Eigenes Passwort und 2FA im Profil-Bereich änderbar",
        "Audit-Log mit Benutzer-Zuordnung",
    ]},
    {"version":"1.7.0","changes":[
        "Neuer Konfig-Bereich mit Sidebar-Navigation",
        "SMTP-Konfiguration mit Verbindungstest und Testmail",
        "Export-Vorlagen: mehrere Vorlagen speicherbar",
        "Audit-Log für sicherheitsrelevante Aktionen",
        "2FA-Status und Reset verbessert",
    ]},
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

# DATA_DIR, DB_PATH, CONFIG_FILE imported from core.db / core.config
EXPORT_DIR    = DATA_DIR / "exports"
TEMPLATE_PATH = DATA_DIR / "template.xlsx"
BACKUP_DIR    = DATA_DIR / "backups"
SIGNATURE_DIR  = DATA_DIR / "signatures"
SIGNATURE_PATH = SIGNATURE_DIR / "default_signature.png"

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 10 * 1024 * 1024
app.secret_key = _get_secret_key()

_EXTERNAL_MODE = os.getenv("EV_TRACKER_EXPOSURE", "internal").lower() == "external"
if _EXTERNAL_MODE:
    from werkzeug.middleware.proxy_fix import ProxyFix
    app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1, x_prefix=1)
    app.config["SESSION_COOKIE_SECURE"]   = True
    app.config["SESSION_COOKIE_HTTPONLY"] = True
    app.config["SESSION_COOKIE_SAMESITE"] = "Lax"

@app.after_request
def _security_headers(resp):
    resp.headers.setdefault("X-Content-Type-Options", "nosniff")
    resp.headers.setdefault("X-Frame-Options", "DENY")
    if _EXTERNAL_MODE:
        resp.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
    return resp

@app.teardown_appcontext
def _close_db(exc):
    db = g.pop("_db", None)
    if db is not None:
        db.close()

register_blueprints(app)

# ── Helpers moved to core/ ────────────────────────────────────────────────────
# _get_secret_key, _hash_password, _password_ok  → core.security
# _get_db, close_db_if_owned                     → core.db
# _safe_next, _has_users, _get_user_by_*         → core.security
# require_login, require_admin, require_auth      → core.security
# ALL_PERMISSIONS, DEFAULT_ROLE_PERMISSIONS       → core.security
# _get_user_permissions, has_permission, require_permission → core.security
# DEFAULT_CONFIG, VEHICLE_SPECIFIC_KEYS          → core.config
# load_config, save_config                       → core.config
# _audit                                         → core.security
# _API_SCOPES, _hash_token, _check_api_token, _require_api_token → core.tokens

def get_all_vehicles(cfg=None, include_archived: bool = False) -> list[dict]:
    """Returns primary vehicle (v0) plus extra vehicles. Archived excluded by default."""
    if cfg is None:
        cfg = load_config()
    primary = {
        "id":   "v0",
        "name": cfg.get("car_name", "Mein EV"),
        "provider": cfg.get("provider", "ha"),
        "active": True,
        "archived": False,
        **{k: cfg[k] for k in VEHICLE_SPECIFIC_KEYS if k in cfg and k not in ("provider","car_name")},
    }
    extras = cfg.get("extra_vehicles", [])
    if not include_archived:
        extras = [v for v in extras if not v.get("archived", False)]
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
    con.row_factory = sqlite3.Row
    # Performance + reliability pragmas
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("PRAGMA foreign_keys=ON")
    con.execute("PRAGMA busy_timeout=5000")
    con.execute("PRAGMA synchronous=NORMAL")
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
    con.execute("""CREATE TABLE IF NOT EXISTS audit_log (
    id      INTEGER PRIMARY KEY AUTOINCREMENT,
    ts      TEXT NOT NULL,
    action  TEXT NOT NULL,
    details TEXT,
    ip      TEXT
)""")
    # live migration
    for col, typedef in [
        ("cost_manual",  "INTEGER DEFAULT 0"),
        ("charger_type", "TEXT DEFAULT 'unknown'"),
        ("max_power_kw", "REAL"),
        ("price_per_kwh","REAL"),
        ("entsoe_spot",  "REAL"),
        ("provider",     "TEXT DEFAULT 'ha'"),
        ("kwh_source",         "TEXT DEFAULT 'soc'"),
        ("meter_delta_kwh",    "REAL"),
        ("meter_error",        "TEXT"),
        ("charger_power_kw",   "REAL"),
        ("tariff_provider",    "TEXT"),
        ("tariff_price_source","TEXT DEFAULT 'config'"),
        ("meter_skipped_reason", "TEXT"),
        ("meter_used",           "INTEGER DEFAULT 0"),
        ("meter_home_detection_start_value", "REAL"),
        ("meter_home_detection_start_ts",    "TEXT"),
        ("meter_home_detection_delta_kwh",   "REAL"),
        ("location_source",      "TEXT DEFAULT 'unknown'"),
        ("location_confidence",  "INTEGER DEFAULT 0"),
        ("manual_note",                  "TEXT"),
        ("manual_reason",                "TEXT"),
        ("created_mode",                 "TEXT DEFAULT 'auto'"),
        ("missing_charge_candidate_id",  "INTEGER"),
    ]:
        try:
            con.execute(f"ALTER TABLE sessions ADD COLUMN {col} {typedef}")
        except sqlite3.OperationalError: pass
    # Backfill NULL vehicle_id → 'v0'
    try:
        con.execute("UPDATE sessions SET vehicle_id='v0' WHERE vehicle_id IS NULL")
    except Exception: pass
    con.execute("""CREATE TABLE IF NOT EXISTS users (
        id            INTEGER PRIMARY KEY AUTOINCREMENT,
        name          TEXT NOT NULL,
        email         TEXT UNIQUE NOT NULL,
        password_hash TEXT NOT NULL DEFAULT '',
        role          TEXT NOT NULL DEFAULT 'user',
        status        TEXT NOT NULL DEFAULT 'active',
        totp_secret   TEXT NOT NULL DEFAULT '',
        totp_enabled  INTEGER NOT NULL DEFAULT 0,
        created_at    TEXT NOT NULL,
        updated_at    TEXT NOT NULL,
        last_login_at TEXT
    )""")
    con.execute("""CREATE TABLE IF NOT EXISTS password_reset_tokens (
        id         INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id    INTEGER NOT NULL,
        token_hash TEXT NOT NULL,
        expires_at TEXT NOT NULL,
        used_at    TEXT,
        created_at TEXT NOT NULL
    )""")
    con.execute("""CREATE TABLE IF NOT EXISTS invite_tokens (
        id         INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id    INTEGER NOT NULL,
        token_hash TEXT NOT NULL,
        expires_at TEXT NOT NULL,
        used_at    TEXT,
        created_at TEXT NOT NULL
    )""")
    con.execute("""CREATE TABLE IF NOT EXISTS totp_backup_codes (
        id       INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id  INTEGER NOT NULL,
        code_hash TEXT NOT NULL,
        used_at  TEXT
    )""")
    # live migration: add brute-force columns to users
    for col, typedef in [
        ("failed_attempts", "INTEGER NOT NULL DEFAULT 0"),
        ("locked_until",    "TEXT"),
    ]:
        try: con.execute(f"ALTER TABLE users ADD COLUMN {col} {typedef}")
        except Exception: pass
    # live migration: add user_id column to audit_log
    try: con.execute("ALTER TABLE audit_log ADD COLUMN user_id INTEGER")
    except Exception: pass
    con.execute("""CREATE TABLE IF NOT EXISTS webauthn_credentials (
        id            INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id       INTEGER NOT NULL,
        credential_id TEXT NOT NULL UNIQUE,
        public_key    TEXT NOT NULL,
        sign_count    INTEGER NOT NULL DEFAULT 0,
        name          TEXT NOT NULL DEFAULT 'Passkey',
        created_at    TEXT NOT NULL,
        last_used_at  TEXT
    )""")
    # Roles
    con.execute("""CREATE TABLE IF NOT EXISTS roles (
        id          INTEGER PRIMARY KEY AUTOINCREMENT,
        name        TEXT UNIQUE NOT NULL,
        description TEXT DEFAULT '',
        is_system   INTEGER DEFAULT 0,
        created_at  TEXT,
        updated_at  TEXT
    )""")
    # Role → Permission mapping
    con.execute("""CREATE TABLE IF NOT EXISTS role_permissions (
        role_id         INTEGER NOT NULL,
        permission_key  TEXT NOT NULL,
        PRIMARY KEY (role_id, permission_key),
        FOREIGN KEY (role_id) REFERENCES roles(id) ON DELETE CASCADE
    )""")
    # User → Role mapping (mehrere Rollen pro User möglich)
    con.execute("""CREATE TABLE IF NOT EXISTS user_roles (
        user_id INTEGER NOT NULL,
        role_id INTEGER NOT NULL,
        PRIMARY KEY (user_id, role_id),
        FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE,
        FOREIGN KEY (role_id) REFERENCES roles(id) ON DELETE CASCADE
    )""")
    # Vehicle access scope (Vorbereitung, noch nicht aktiv)
    con.execute("""CREATE TABLE IF NOT EXISTS user_vehicle_access (
        user_id    INTEGER NOT NULL,
        vehicle_id INTEGER NOT NULL,
        can_view   INTEGER DEFAULT 1,
        can_edit   INTEGER DEFAULT 0,
        can_export INTEGER DEFAULT 0,
        PRIMARY KEY (user_id, vehicle_id)
    )""")
    con.execute("""CREATE TABLE IF NOT EXISTS email_report_history (
        id            INTEGER PRIMARY KEY AUTOINCREMENT,
        sent_at       TEXT NOT NULL,
        schedule_type TEXT,
        period_start  TEXT,
        period_end    TEXT,
        period_key    TEXT,
        location_filter TEXT DEFAULT 'all',
        vehicle_filter  TEXT DEFAULT 'all',
        recipients    TEXT,
        status        TEXT NOT NULL DEFAULT 'sent',
        error         TEXT,
        triggered_by  TEXT DEFAULT 'auto'
    )""")
    # Migrate: add columns if missing
    for _col in ["period_label TEXT", "period_mode TEXT"]:
        try: con.execute(f"ALTER TABLE email_report_history ADD COLUMN {_col}")
        except Exception: pass

    # Billing config (per-vehicle employer reimbursement settings)
    con.execute("""CREATE TABLE IF NOT EXISTS billing_config (
        vehicle_id               TEXT PRIMARY KEY,
        enabled                  INTEGER DEFAULT 0,
        location_filter          TEXT DEFAULT 'all',
        reimbursement_mode       TEXT DEFAULT 'fixed_price',
        reimbursement_price_per_kwh REAL DEFAULT 0.30,
        requires_approval        INTEGER DEFAULT 0,
        report_template_id       TEXT,
        auto_send                INTEGER DEFAULT 0,
        recipients               TEXT DEFAULT '[]',
        driver_name              TEXT DEFAULT '',
        license_plate            TEXT DEFAULT '',
        cost_center              TEXT DEFAULT '',
        employee_id              TEXT DEFAULT '',
        department               TEXT DEFAULT '',
        employer_email           TEXT DEFAULT '',
        requires_signature       INTEGER DEFAULT 0,
        created_at               TEXT,
        updated_at               TEXT
    )""")

    # Report archive
    con.execute("""CREATE TABLE IF NOT EXISTS reports (
        id               INTEGER PRIMARY KEY AUTOINCREMENT,
        created_at       TEXT NOT NULL,
        vehicle_id       TEXT,
        period_start     TEXT,
        period_end       TEXT,
        period_label     TEXT,
        period_mode      TEXT,
        location_filter  TEXT DEFAULT 'all',
        vehicle_filter   TEXT DEFAULT 'all',
        status           TEXT DEFAULT 'draft',
        created_by       INTEGER,
        sent_at          TEXT,
        recipients       TEXT DEFAULT '[]',
        excel_path       TEXT,
        pdf_path         TEXT,
        summary_json     TEXT DEFAULT '{}',
        approval_status  TEXT,
        excel_bytes      BLOB,
        pdf_bytes        BLOB
    )""")

    # API tokens
    con.execute("""CREATE TABLE IF NOT EXISTS api_tokens (
        id           INTEGER PRIMARY KEY AUTOINCREMENT,
        name         TEXT NOT NULL,
        token_hash   TEXT NOT NULL UNIQUE,
        token_prefix TEXT NOT NULL,
        scopes       TEXT DEFAULT '[]',
        expires_at   TEXT,
        last_used_at TEXT,
        created_by   INTEGER,
        created_at   TEXT NOT NULL,
        is_active    INTEGER DEFAULT 1
    )""")

    # Notification rules
    con.execute("""CREATE TABLE IF NOT EXISTS notification_rules (
        id                   INTEGER PRIMARY KEY AUTOINCREMENT,
        name                 TEXT NOT NULL,
        enabled              INTEGER DEFAULT 1,
        event_type           TEXT NOT NULL,
        channel              TEXT NOT NULL,
        vehicle_filter       TEXT DEFAULT 'all',
        user_filter          TEXT DEFAULT 'all',
        recipient            TEXT DEFAULT '',
        threshold            REAL,
        quiet_hours_enabled  INTEGER DEFAULT 0,
        quiet_hours_start    TEXT DEFAULT '22:00',
        quiet_hours_end      TEXT DEFAULT '07:00',
        created_at           TEXT,
        updated_at           TEXT
    )""")

    # Tariff price cache
    con.execute("""CREATE TABLE IF NOT EXISTS tariff_cache (
        id          INTEGER PRIMARY KEY AUTOINCREMENT,
        provider    TEXT NOT NULL,
        ts          TEXT NOT NULL,
        price_per_kwh REAL NOT NULL,
        currency    TEXT DEFAULT 'EUR',
        cached_at   TEXT NOT NULL,
        UNIQUE(provider, ts)
    )""")

    con.execute("""CREATE TABLE IF NOT EXISTS vehicle_location_history (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        vehicle_id TEXT NOT NULL,
        timestamp TEXT NOT NULL,
        location_status TEXT,
        source TEXT,
        latitude REAL,
        longitude REAL,
        accuracy_m REAL
    )""")
    con.execute("""CREATE INDEX IF NOT EXISTS idx_vlh_vehicle_ts ON vehicle_location_history(vehicle_id, timestamp)""")

    con.execute("""CREATE TABLE IF NOT EXISTS vehicle_snapshots (
        id              INTEGER PRIMARY KEY AUTOINCREMENT,
        vehicle_id      TEXT NOT NULL,
        ts              TEXT NOT NULL,
        soc             REAL,
        odometer_km     REAL,
        range_km        REAL,
        location_status TEXT,
        latitude        REAL,
        longitude       REAL,
        provider        TEXT,
        raw_available   INTEGER DEFAULT 1,
        created_at      TEXT NOT NULL
    )""")
    con.execute("""CREATE INDEX IF NOT EXISTS idx_vsnap_vehicle_ts
                   ON vehicle_snapshots(vehicle_id, ts)""")

    con.execute("""CREATE TABLE IF NOT EXISTS missing_charge_candidates (
        id                          INTEGER PRIMARY KEY AUTOINCREMENT,
        vehicle_id                  TEXT NOT NULL,
        snapshot_before_id          INTEGER,
        snapshot_after_id           INTEGER,
        start_ts                    TEXT,
        end_ts                      TEXT,
        soc_start                   REAL,
        soc_end                     REAL,
        odo_start                   REAL,
        odo_end                     REAL,
        driven_km                   REAL,
        estimated_kwh               REAL,
        estimated_consumption_kwh   REAL,
        estimated_battery_delta_kwh REAL,
        estimated_avg_power_kw      REAL,
        suggested_charger_type      TEXT,
        suggested_location          TEXT,
        confidence                  INTEGER DEFAULT 50,
        reason                      TEXT,
        status                      TEXT DEFAULT 'open',
        created_at                  TEXT,
        updated_at                  TEXT
    )""")
    con.execute("""CREATE INDEX IF NOT EXISTS idx_mcc_vehicle_status
                   ON missing_charge_candidates(vehicle_id, status)""")
    for _col, _typedef in [
        ("accepted_session_id", "INTEGER"),
    ]:
        try:
            con.execute(f"ALTER TABLE missing_charge_candidates ADD COLUMN {_col} {_typedef}")
        except Exception:
            pass
    con.execute("""CREATE TABLE IF NOT EXISTS notifications (
        id                   INTEGER PRIMARY KEY AUTOINCREMENT,
        type                 TEXT NOT NULL,
        severity             TEXT NOT NULL DEFAULT 'info',
        vehicle_id           TEXT,
        title                TEXT NOT NULL,
        message              TEXT,
        data_json            TEXT,
        dedupe_key           TEXT,
        status               TEXT DEFAULT 'pending',
        created_at           TEXT NOT NULL,
        sent_at              TEXT,
        error                TEXT,
        channel_results_json TEXT,
        is_read              INTEGER DEFAULT 0,
        action_url           TEXT,
        action_payload       TEXT
    )""")
    con.execute("CREATE INDEX IF NOT EXISTS idx_notifications_status ON notifications(status)")
    con.execute("CREATE INDEX IF NOT EXISTS idx_notifications_created ON notifications(created_at)")
    con.execute("CREATE INDEX IF NOT EXISTS idx_notifications_vehicle ON notifications(vehicle_id)")
    con.execute("CREATE INDEX IF NOT EXISTS idx_notifications_dedupe ON notifications(dedupe_key, created_at)")

    # Seed default roles
    now_iso = datetime.utcnow().isoformat()
    default_roles = [
        ("admin",    "Vollzugriff",                    1),
        ("user",     "Normaler Benutzer",               1),
        ("readonly", "Nur-Lese-Zugriff",               1),
    ]
    for name, desc, is_sys in default_roles:
        existing = con.execute("SELECT id FROM roles WHERE name=?", (name,)).fetchone()
        if not existing:
            con.execute(
                "INSERT INTO roles (name, description, is_system, created_at, updated_at) VALUES (?,?,?,?,?)",
                (name, desc, is_sys, now_iso, now_iso)
            )
            con.commit()
    # Seed default role permissions
    for role_name, perms in DEFAULT_ROLE_PERMISSIONS.items():
        role_row = con.execute("SELECT id FROM roles WHERE name=?", (role_name,)).fetchone()
        if role_row:
            role_id = role_row["id"]
            existing_perms = {r["permission_key"] for r in
                con.execute("SELECT permission_key FROM role_permissions WHERE role_id=?", (role_id,)).fetchall()}
            for pkey in perms:
                if pkey not in existing_perms and pkey in ALL_PERMISSIONS:
                    con.execute("INSERT OR IGNORE INTO role_permissions (role_id, permission_key) VALUES (?,?)",
                                (role_id, pkey))
            con.commit()
    # Migrate existing users to roles
    users_without_roles = con.execute("""
        SELECT u.id, u.role FROM users u
        WHERE NOT EXISTS (SELECT 1 FROM user_roles ur WHERE ur.user_id = u.id)
    """).fetchall()
    for u in users_without_roles:
        role_name = u["role"] if u["role"] in ("admin", "user", "readonly") else "user"
        role_row = con.execute("SELECT id FROM roles WHERE name=?", (role_name,)).fetchone()
        if role_row:
            con.execute("INSERT OR IGNORE INTO user_roles (user_id, role_id) VALUES (?,?)",
                        (u["id"], role_row["id"]))
    con.commit()

    # Idempotent indexes for performance
    _indexes = [
        ("idx_sessions_vehicle",    "sessions(vehicle_id)"),
        ("idx_sessions_start_ts",   "sessions(start_ts)"),
        ("idx_sessions_end_ts",     "sessions(end_ts)"),
        ("idx_sessions_location",   "sessions(location)"),
        ("idx_sessions_charger",    "sessions(charger_type)"),
        ("idx_sessions_kwh_source", "sessions(kwh_source)"),
        ("idx_users_email",         "users(email)"),
        ("idx_users_role",          "users(role)"),
        ("idx_users_status",        "users(status)"),
        ("idx_audit_ts",            "audit_log(ts)"),
        ("idx_audit_user",          "audit_log(user_id)"),
        ("idx_audit_action",        "audit_log(action)"),
        ("idx_reports_vehicle",     "reports(vehicle_id)"),
        ("idx_reports_period",      "reports(period_start, period_end)"),
        ("idx_reports_status",      "reports(status)"),
        ("idx_reports_created",     "reports(created_at)"),
        ("idx_api_tokens_hash",     "api_tokens(token_hash)"),
        ("idx_api_tokens_active",   "api_tokens(is_active)"),
        ("idx_notif_enabled",       "notification_rules(enabled)"),
        ("idx_notif_event",         "notification_rules(event_type)"),
        ("idx_notif_channel",       "notification_rules(channel)"),
        ("idx_session_points_session", "session_points(session_id)"),
        ("idx_session_points_ts",      "session_points(ts)"),
    ]
    for idx_name, idx_target in _indexes:
        try:
            con.execute(f"CREATE INDEX IF NOT EXISTS {idx_name} ON {idx_target}")
        except Exception:
            pass
    con.commit()
    close_db_if_owned(con)

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
    con = _get_db()
    rows = con.execute(sql, params).fetchall(); close_db_if_owned(con)
    return [dict(r) for r in rows]

def get_monthly_stats():
    con = _get_db()
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
    close_db_if_owned(con); return [dict(r) for r in rows]

# ── ENTSO-E ───────────────────────────────────────────────────────────────────
_entsoe_cache = _core_state.entsoe_cache  # shared via core.state
_forgot_pw_attempts: dict[str, list] = {}  # email -> [timestamp, ...]

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
        "running": False,
        "tracker_started": False,
        "tracker_alive": False,
        "tracker_thread_id": None,
        "tracker_start_time": None,
        "session_active": False, "session_id": None,
        "last_poll": None,
        "last_successful_poll": None,
        "last_error": None,
        "last_fatal_error": None,
        "last_exception_type": None,
        "poll_count": 0,
        "successful_poll_count": 0,
        "failed_poll_count": 0,
        "provider_debug": {},
        "provider_connected": False,
        "soc_current": None,
        "odo_current": None, "charging": False, "location": "unknown",
        "charger_type": "unknown", "power_kw": None, "entsoe_spot": None,
        "provider": provider_id,
        "provider_name": PROVIDERS.get(provider_id, PROVIDERS["ha"]).PROVIDER_NAME,
        "location_lat": None, "location_lon": None,
        "location_accuracy": None, "location_timestamp": None,
        "location_status": "unknown", "location_source": "none",
        "meter_home_det_start_val": None, "meter_home_det_start_ts": None,
    }

_vehicle_states      = _core_state.vehicle_states       # shared with blueprints
_vehicle_states_lock = _core_state.vehicle_states_lock
_vehicle_stops       = _core_state.vehicle_stops

if "v0" not in _vehicle_states:
    _vehicle_states["v0"] = _make_state("v0")
    _vehicle_stops["v0"]  = threading.Event()

def read_meter_value() -> Optional[float]:
    """Read current meter value. Returns kWh or None."""
    cfg = load_config()
    result = _read_meter_impl(cfg)
    if result.ok:
        return result.value
    if result.error:
        log.warning("Meter read error (%s): %s", result.source, result.error)
    return None

def tracker_loop(vehicle_id: str = "v0"):
    st   = _vehicle_states[vehicle_id]
    stop = _vehicle_stops[vehicle_id]
    st["running"] = True
    st["tracker_started"] = True
    st["tracker_alive"] = True
    st["tracker_start_time"] = datetime.now().isoformat(timespec="seconds")
    st["tracker_thread_id"] = threading.get_ident()
    session_active = False; session_id = None
    soc_start = odo_start = peak_power = meter_start_val = None

    log.info("Tracker gestartet: %s", vehicle_id)
    while not stop.is_set():
        try:
            cfg = load_config()
        except Exception as _cfg_err:
            log.warning("Tracker [%s]: load_config fehlgeschlagen: %s", vehicle_id, _cfg_err)
            st["last_poll"] = datetime.now().isoformat(timespec="seconds")
            st["last_error"] = f"Config-Fehler: {_cfg_err}"
            st["provider_connected"] = False
            st["failed_poll_count"] = st.get("failed_poll_count", 0) + 1
            st["poll_count"] = st.get("poll_count", 0) + 1
            stop.wait(30); continue
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
            debug = provider.get_debug() if hasattr(provider, 'get_debug') else {}
            st["provider_debug"] = debug

            if state.error:
                st["last_poll"] = datetime.now().isoformat(timespec="seconds")
                st["last_error"] = state.error
                st["provider_connected"] = False
                st["failed_poll_count"] = st.get("failed_poll_count", 0) + 1
                st["poll_count"] = st.get("poll_count", 0) + 1
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
                last_successful_poll=datetime.now().isoformat(timespec="seconds"),
                last_error=None, provider=provider_id,
                provider_name=PROVIDERS.get(provider_id, PROVIDERS["ha"]).PROVIDER_NAME,
                provider_connected=True,
                poll_count=st.get("poll_count", 0) + 1,
                successful_poll_count=st.get("successful_poll_count", 0) + 1,
                name=vcfg.get("car_name", vehicle_id),
            )

            # Update location status
            try:
                if getattr(state, 'lat', None) is not None:
                    st["location_lat"] = state.lat
                    st["location_lon"] = getattr(state, 'lon', None)
                    st["location_accuracy"] = getattr(state, 'accuracy', None)
                    st["location_timestamp"] = datetime.now().isoformat(timespec="seconds")
                loc_result = _detect_location_status(vehicle_id, vcfg, st)
                st["location_status"] = loc_result["status"]
                st["location_source"] = loc_result["source"]
            except Exception as _le:
                log.debug("Location detection error: %s", _le)

            con = sqlite3.connect(DB_PATH); cur = con.cursor()

            # Standort-Historie befüllen wenn aktiviert
            if vcfg.get("location_history_enabled") and st.get("location_status"):
                try:
                    precision = vcfg.get("location_history_precision", "status_only")
                    retention = int(vcfg.get("location_history_retention_days", 30))
                    lat = st.get("location_lat") if precision in ("rounded", "exact") else None
                    lon = st.get("location_lon") if precision in ("rounded", "exact") else None
                    acc = st.get("location_accuracy") if precision == "exact" else None
                    if precision == "rounded" and lat is not None:
                        lat = round(lat, 3); lon = round(lon, 3) if lon else lon
                    now_ts = datetime.utcnow().isoformat(timespec="seconds")
                    cur.execute(
                        "INSERT INTO vehicle_location_history "
                        "(vehicle_id, timestamp, location_status, source, latitude, longitude, accuracy_m) "
                        "VALUES (?,?,?,?,?,?,?)",
                        (vehicle_id, now_ts, normalize_location(st["location_status"]),
                         st.get("location_source",""), lat, lon, acc))
                    # Cleanup old entries beyond retention period
                    cutoff = (datetime.utcnow() - timedelta(days=retention)).isoformat(timespec="seconds")
                    cur.execute("DELETE FROM vehicle_location_history WHERE vehicle_id=? AND timestamp<?",
                                (vehicle_id, cutoff))
                    con.commit()
                except Exception as _lhe:
                    log.debug("Location history write error: %s", _lhe)

            if charging and not session_active:
                soc_start = soc; odo_start = odo; peak_power = power_kw or 0
                _meter_scope = cfg.get("meter_scope", "home_only")
                _effective_location = effective_session_location(location, st.get("location_status"))
                meter_start_val = None
                if _meter_scope == "disabled":
                    log.debug("[%s] Meter skipped at session start (scope=disabled)", vehicle_id)
                elif _meter_scope == "home_only" and _effective_location != "home":
                    log.info("[%s] Meter skipped at session start (scope=home_only, location=%s)",
                             vehicle_id, _effective_location)
                else:
                    _meter_start_res = _read_meter_impl(cfg)
                    meter_start_val = _meter_start_res.value
                    if meter_start_val is None and cfg.get("meter_source","none") != "none":
                        log.warning("[%s] Zählerstand-Lesefehler beim Session-Start: %s",
                                    vehicle_id, getattr(_meter_start_res, "error", "unknown"))
                        try:
                            from notification_manager import fire_event as _fe
                            _fe("meter_read_failed", {"vehicle_id": vehicle_id,
                                "phase": "start", "source": cfg.get("meter_source")},
                                cfg, db_path=DB_PATH)
                        except Exception: pass
                # Meter-based home detection: save reference value when location is unknown
                mhd_start_val = None
                mhd_start_ts  = None
                _mhd_enabled = cfg.get("meter_home_detection_enabled", True)
                if (_effective_location == "unknown" and _mhd_enabled
                        and cfg.get("meter_source", "none") != "none"):
                    _mhd_res = _read_meter_impl(cfg)
                    if _mhd_res.value is not None:
                        mhd_start_val = _mhd_res.value
                        mhd_start_ts  = datetime.utcnow().isoformat(timespec="seconds")
                        st["meter_home_det_start_val"] = mhd_start_val
                        st["meter_home_det_start_ts"]  = mhd_start_ts
                        log.debug("[%s] Meter-Home-Detection Referenzwert: %.3f kWh", vehicle_id, mhd_start_val)
                    else:
                        st["meter_home_det_start_val"] = None
                        st["meter_home_det_start_ts"]  = None
                else:
                    st["meter_home_det_start_val"] = None
                    st["meter_home_det_start_ts"]  = None

                spot = fetch_entsoe_spot(cfg.get("entsoe_api_key","")) if _effective_location == "extern" else None
                st["entsoe_spot"] = spot
                price_kwh = (cfg["price_per_kwh_home"] if _effective_location == "home"
                             else calc_extern_price(cfg, charger_type, spot))
                # Set wallbox power for home sessions
                sess_charger_kw = (cfg.get("home_charger_power_kw") or None) if _effective_location == "home" else None
                _loc_src = st.get("location_source", "unknown") if _effective_location != "unknown" else "unknown"
                cur.execute("""INSERT INTO sessions
                    (start_ts,odo_start,soc_start,location,charger_type,
                     max_power_kw,price_per_kwh,entsoe_spot,provider,meter_old,vehicle_id,charger_power_kw,
                     meter_home_detection_start_value,meter_home_detection_start_ts,
                     location_source,location_confidence)
                    VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (datetime.now().isoformat(timespec="seconds"),
                     odo_start,soc_start,_effective_location,charger_type,power_kw,price_kwh,spot,
                     provider_id,meter_start_val,vehicle_id,sess_charger_kw,
                     mhd_start_val, mhd_start_ts,
                     _loc_src, 0 if _effective_location == "unknown" else 80))
                con.commit(); session_id=cur.lastrowid; session_active=True
                st["session_id"]=session_id
                try:
                    from notification_manager import fire_event as _fire_event
                    _fire_event("charging_started", {
                        "vehicle_id": vehicle_id, "session_id": session_id,
                        "location": _effective_location, "soc_start": soc_start,
                        "charger_type": charger_type,
                    }, load_config(), db_path=DB_PATH)
                except Exception: pass
                cur.execute("INSERT INTO session_points (session_id,ts,soc,power_kw) VALUES (?,?,?,?)",
                            (session_id,datetime.now().isoformat(timespec="seconds"),soc_start,power_kw))
                con.commit()
                log.info("⚡ [%s] Session #%d | %s | %s | %.2f €/kWh",
                         vehicle_id,session_id,_effective_location.upper(),charger_type.upper(),price_kwh)
                ha_notify(vcfg,f"⚡ {vcfg['car_name']} lädt",
                    f"{'🏠 Zuhause' if _effective_location=='home' else '⚡ Extern'} · "
                    f"{'DC' if charger_type=='dc' else 'AC'} · {price_kwh:.2f} €/kWh · SOC {soc_start or '?'}%")

            elif charging and session_active:
                _effective_location = effective_session_location(location, st.get("location_status"))
                cur.execute("INSERT INTO session_points (session_id,ts,soc,power_kw) VALUES (?,?,?,?)",
                            (session_id,datetime.now().isoformat(timespec="seconds"),soc,power_kw))
                con.commit()

                # ── Meter-based home detection ─────────────────────────────────
                _mhd_enabled = cfg.get("meter_home_detection_enabled", True)
                _mhd_start   = st.get("meter_home_det_start_val")
                _mhd_start_ts= st.get("meter_home_det_start_ts")
                if (_mhd_enabled and _mhd_start is not None and _mhd_start_ts is not None
                        and cfg.get("meter_source", "none") != "none"):
                    _can_detect = (
                        _effective_location == "unknown" or
                        (_effective_location == "extern" and cfg.get("meter_home_detection_override_external", False))
                    )
                    _is_conflict = (_effective_location == "extern")
                    if _can_detect or _is_conflict:
                        try:
                            _mhd_now_res = _read_meter_impl(cfg)
                            if _mhd_now_res.value is not None:
                                _mhd_delta = _mhd_now_res.value - _mhd_start
                                _mhd_min   = float(cfg.get("meter_home_detection_min_delta_kwh", 0.2))
                                _mhd_win   = float(cfg.get("meter_home_detection_window_minutes", 10))
                                _mhd_max_h = float(cfg.get("meter_home_detection_max_delta_kwh_per_hour", 30.0))
                                # Validate time window
                                try:
                                    _mhd_age_min = (datetime.utcnow() - datetime.fromisoformat(_mhd_start_ts)).total_seconds() / 60
                                except Exception:
                                    _mhd_age_min = 0
                                # Rate sanity: delta per hour
                                _mhd_rate_ok = True
                                if _mhd_age_min > 0:
                                    _mhd_rate_kw_h = _mhd_delta / (_mhd_age_min / 60)
                                    if _mhd_rate_kw_h > _mhd_max_h:
                                        _mhd_rate_ok = False
                                        log.warning("[%s] Meter-Home-Detection: Delta %.3f kWh in %.1f min → Rate %.1f kWh/h > Max %.1f — ignoriert",
                                                    vehicle_id, _mhd_delta, _mhd_age_min, _mhd_rate_kw_h, _mhd_max_h)
                                if _is_conflict and not _can_detect:
                                    # Warn only: extern + meter rising
                                    if _mhd_delta >= _mhd_min and _mhd_rate_ok:
                                        log.warning("[%s] Zähler steigt (%.3f kWh), aber Standort als extern erkannt — kein Auto-Override",
                                                    vehicle_id, _mhd_delta)
                                        cur.execute("UPDATE sessions SET location_source=? WHERE id=?",
                                                    ("meter_conflict", session_id))
                                        con.commit()
                                elif (_can_detect and _mhd_delta >= _mhd_min
                                        and _mhd_rate_ok and 0 < _mhd_age_min <= _mhd_win):
                                    # Home detected via meter!
                                    log.info("[%s] Meter-Home-Detection: %.3f kWh Delta → Session als Zuhause markiert",
                                             vehicle_id, _mhd_delta)
                                    st["location_status"] = "home"
                                    st["location_source"]  = "meter_delta"
                                    _effective_location    = "home"
                                    # Recalculate price at home tariff
                                    _mhd_home_price = cfg.get("price_per_kwh_home", 0.30)
                                    cur.execute(
                                        "UPDATE sessions SET location=?,price_per_kwh=?,meter_old=?,"
                                        "meter_home_detection_start_value=?,meter_home_detection_start_ts=?,"
                                        "meter_home_detection_delta_kwh=?,location_source=?,location_confidence=?,"
                                        "meter_used=? WHERE id=?",
                                        ("home", _mhd_home_price, _mhd_start,
                                         _mhd_start, _mhd_start_ts, round(_mhd_delta, 3),
                                         "meter_delta", 70, 1, session_id))
                                    con.commit()
                                    # Prevent re-detection on next poll
                                    st["meter_home_det_start_val"] = None
                                    st["meter_home_det_start_ts"]  = None
                                    try:
                                        from notification_manager import fire_event as _fe
                                        _fe("session_location_detected_by_meter", {
                                            "vehicle_id": vehicle_id, "session_id": session_id,
                                            "delta_kwh": round(_mhd_delta, 3),
                                        }, load_config(), db_path=DB_PATH)
                                    except Exception: pass
                        except Exception as _mhd_err:
                            log.debug("[%s] Meter-Home-Detection Fehler: %s", vehicle_id, _mhd_err)

                if power_kw and (peak_power is None or power_kw > peak_power):
                    peak_power = power_kw
                    new_type = "dc" if power_kw > float(vcfg.get("dc_threshold_kw",22)) else "ac"
                    if new_type != charger_type:
                        spot = st.get("entsoe_spot")
                        price = (cfg["price_per_kwh_home"] if _effective_location=="home"
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
                _meter_scope = cfg.get("meter_scope", "home_only")
                _effective_location = effective_session_location(location, st.get("location_status"))

                # Final meter-based home detection at session end
                _mhd_enabled = cfg.get("meter_home_detection_enabled", True)
                _mhd_start   = st.get("meter_home_det_start_val")
                _mhd_start_ts= st.get("meter_home_det_start_ts")
                if (_effective_location == "unknown" and _mhd_enabled
                        and _mhd_start is not None and cfg.get("meter_source", "none") != "none"):
                    try:
                        _mhd_end_res = _read_meter_impl(cfg)
                        if _mhd_end_res.value is not None:
                            _mhd_delta_end = _mhd_end_res.value - _mhd_start
                            _mhd_min = float(cfg.get("meter_home_detection_min_delta_kwh", 0.2))
                            _mhd_max_h = float(cfg.get("meter_home_detection_max_delta_kwh_per_hour", 30.0))
                            _mhd_rate_ok = True
                            if _mhd_start_ts:
                                try:
                                    _mhd_age_min = (datetime.utcnow() - datetime.fromisoformat(_mhd_start_ts)).total_seconds() / 60
                                    if _mhd_age_min > 0:
                                        _rate = _mhd_delta_end / (_mhd_age_min / 60)
                                        if _rate > _mhd_max_h:
                                            _mhd_rate_ok = False
                                except Exception: pass
                            if _mhd_delta_end >= _mhd_min and _mhd_rate_ok and 0 < _mhd_delta_end <= 250:
                                log.info("[%s] Meter-Home-Detection (Session-Ende): %.3f kWh → Zuhause",
                                         vehicle_id, _mhd_delta_end)
                                _effective_location = "home"
                                st["location_status"] = "home"
                                st["location_source"]  = "meter_delta"
                                cur.execute(
                                    "UPDATE sessions SET location=?,meter_home_detection_start_value=?,"
                                    "meter_home_detection_start_ts=?,meter_home_detection_delta_kwh=?,"
                                    "location_source=?,location_confidence=? WHERE id=?",
                                    ("home", _mhd_start, _mhd_start_ts, round(_mhd_delta_end, 3),
                                     "meter_delta", 70, session_id))
                                con.commit()
                                db_price = cfg.get("price_per_kwh_home", 0.30)
                    except Exception as _mhd_end_err:
                        log.debug("[%s] Meter-Home-Detection (Session-Ende) Fehler: %s", vehicle_id, _mhd_end_err)
                st["meter_home_det_start_val"] = None
                st["meter_home_det_start_ts"]  = None

                meter_end_val = None
                meter_skipped_reason = None
                meter_used = 0
                if _meter_scope == "disabled":
                    meter_skipped_reason = "disabled"
                elif _meter_scope == "home_only" and _effective_location != "home":
                    meter_skipped_reason = "external_charging" if _effective_location == "extern" else "unknown_location"
                    log.info("[%s] Meter skipped at session end (scope=home_only, location=%s)",
                             vehicle_id, _effective_location)
                else:
                    _meter_end_res = _read_meter_impl(cfg)
                    meter_end_val = _meter_end_res.value
                prefer_delta = cfg.get("meter_prefer_meter_delta", False)
                kwh_source = "soc"
                if (prefer_delta and meter_start_val is not None and meter_end_val is not None
                        and meter_end_val >= meter_start_val):
                    meter_delta = round(meter_end_val - meter_start_val, 3)
                    # plausibility: max 250 kWh per session
                    if 0 < meter_delta <= 250:
                        kwh = meter_delta
                        if not cost_manual:
                            cost = round(kwh * (db_price or cfg["price_per_kwh_home"]), 2)
                        kwh_source = "meter"
                        meter_used = 1
                # Dynamic tariff price (only for home sessions with non-fixed provider)
                effective_price = db_price or cfg.get("price_per_kwh_home", 0.30)
                tariff_prov_name = cfg.get("tariff_provider", "fixed")
                tariff_price_src = "config"
                if not cost_manual and _effective_location == "home" and tariff_prov_name not in ("fixed", "", None):
                    try:
                        from tariff_providers import get_tariff_provider
                        _tp = get_tariff_provider(cfg)
                        _sess_start = cur.execute(
                            "SELECT start_ts FROM sessions WHERE id=?", (session_id,)).fetchone()
                        if _sess_start and _sess_start[0]:
                            from datetime import datetime as _dt
                            _s = _dt.fromisoformat(_sess_start[0])
                            _e = datetime.now()
                            _avg = _tp.get_average_price(_s, _e)
                            if _avg is not None and _avg > 0:
                                effective_price = round(_avg, 5)
                                tariff_price_src = tariff_prov_name
                                log.info("[%s] Tarifpreis %.4f €/kWh via %s",
                                         vehicle_id, effective_price, tariff_prov_name)
                    except Exception as _tp_err:
                        log.warning("[%s] Tarifprovider-Fehler, nutze Config-Preis: %s",
                                    vehicle_id, _tp_err)
                        tariff_price_src = "fallback"
                        try:
                            from notification_manager import fire_event as _fe
                            _fe("provider_error", {"vehicle_id": vehicle_id,
                                "provider": tariff_prov_name, "error": str(_tp_err)},
                                cfg, db_path=DB_PATH)
                        except Exception: pass
                if kwh is not None and not cost_manual:
                    cost = round(kwh * effective_price, 2)
                end_ts_str = datetime.now().isoformat(timespec="seconds")
                _fin_loc_src = st.get("location_source", "unknown")
                _fin_loc_conf = 70 if _fin_loc_src == "meter_delta" else (80 if _effective_location != "unknown" else 0)
                cur.execute("""UPDATE sessions
                    SET end_ts=?,odo_end=?,soc_end=?,kwh_charged=?,
                    cost_eur=CASE WHEN cost_manual=1 THEN cost_eur ELSE ? END,
                    price_per_kwh=CASE WHEN cost_manual=1 THEN price_per_kwh ELSE ? END,
                    tariff_provider=?,tariff_price_source=?,
                    max_power_kw=?,meter_new=?,kwh_source=?,
                    meter_delta_kwh=CASE WHEN ? IS NOT NULL AND ? IS NOT NULL THEN ?-? ELSE NULL END,
                    meter_skipped_reason=?,meter_used=?,
                    location_source=COALESCE(location_source,?),location_confidence=COALESCE(location_confidence,?)
                    WHERE id=?""",
                    (end_ts_str,odo,soc,kwh,cost,effective_price,
                     tariff_prov_name,tariff_price_src,peak_power,
                     meter_end_val,kwh_source,
                     meter_start_val, meter_end_val, meter_end_val, meter_start_val,
                     meter_skipped_reason, meter_used,
                     _fin_loc_src, _fin_loc_conf,
                     session_id))
                con.commit(); session_active=False
                st.update(session_active=False,session_id=None)
                log.info("✅ [%s] Session #%d | %.2f kWh | %.2f €",vehicle_id,session_id,kwh or 0,cost or 0)
                ha_notify(vcfg,f"✅ {vcfg['car_name']} fertig",
                    f"{'🏠' if _effective_location=='home' else '⚡'} · {kwh or 0:.2f} kWh · {cost or 0:.2f} €")
                try:
                    from notification_manager import fire_event as _fire_event
                    _fire_event("charging_stopped", {
                        "vehicle_id": vehicle_id, "session_id": session_id,
                        "location": _effective_location, "kwh": kwh or 0, "cost_eur": cost or 0,
                        "soc_end": soc, "kwh_source": kwh_source,
                    }, load_config(), db_path=DB_PATH)
                except Exception: pass
                session_id=None; peak_power=None

            # ── Snapshot + Missing-Charge Detection ───────────────────────────
            try:
                from services.missing_charge_service import save_snapshot, check_for_missing_charge
                _snap_id = save_snapshot(
                    vehicle_id, soc, odo,
                    getattr(state, "range_km", None),
                    st.get("location_status", "unknown"),
                    provider_id, con,
                )
                if _snap_id:
                    check_for_missing_charge(vehicle_id, _snap_id, vcfg, con)
            except Exception as _mce:
                log.debug("Missing-charge snapshot error [%s]: %s", vehicle_id, _mce)

            close_db_if_owned(con)

        except Exception as e:
            import traceback as _tb
            log.warning("Tracker error [%s]: %s", vehicle_id, e)
            st["last_error"] = str(e)
            st["last_fatal_error"] = _tb.format_exc()
            st["last_exception_type"] = type(e).__name__
            st["provider_connected"] = False
            st["failed_poll_count"] = st.get("failed_poll_count", 0) + 1
            st["poll_count"] = st.get("poll_count", 0) + 1
        stop.wait(vcfg.get("poll_interval",60))
    st["running"] = False
    st["tracker_alive"] = False

def _start_vehicle_tracker(vehicle_id: str):
    with _vehicle_states_lock:
        existing = _vehicle_states.get(vehicle_id, {})
        if existing.get("tracker_alive"):
            log.info("Tracker %s already running, skipping", vehicle_id)
            return
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
    existing = _vehicle_states.get("v0", {})
    if existing.get("tracker_alive") and existing.get("tracker_thread_id"):
        log.info("Tracker v0 already running (thread %s), skipping", existing["tracker_thread_id"])
    else:
        _vehicle_stops["v0"].clear()
        threading.Thread(target=tracker_loop, args=("v0",), daemon=True).start()
    # Always check extra vehicles
    cfg = load_config()
    for v in cfg.get("extra_vehicles", []):
        if not v.get("active", True):
            continue
        vid = v["id"]
        ex = _vehicle_states.get(vid, {})
        if ex.get("tracker_alive") and ex.get("tracker_thread_id"):
            log.info("Tracker %s already running, skipping", vid)
            continue
        if vid not in _vehicle_states:
            _vehicle_states[vid] = _make_state(vid, v.get("provider","ha"))
        if vid not in _vehicle_stops:
            _vehicle_stops[vid] = threading.Event()
        _vehicle_stops[vid].clear()
        threading.Thread(target=tracker_loop, args=(vid,), daemon=True).start()


_started_once = False
_started_once_lock = threading.Lock()


def ensure_started_once():
    """Idempotent startup: init DB, start tracker and schedulers exactly once."""
    global _started_once
    with _started_once_lock:
        if _started_once:
            return
        _started_once = True
    init_db()
    start_tracker()
    if callable(globals().get("schedule_backup")):
        schedule_backup()
    if callable(globals().get("schedule_report")):
        schedule_report()


# ── Routes ────────────────────────────────────────────────────────────────────

_AUTH_EXEMPT = {"/login", "/logout", "/setup",
                "/auth/google", "/auth/google/callback",
                "/auth/microsoft", "/auth/microsoft/callback",
                "/forgot-password",
                "/api/health",
                "/api/auth/passkey/login/begin",
                "/api/auth/passkey/login/complete"}

_AUTH_EXEMPT_PREFIXES = ("/reset-password", "/invite")
_API_V1_PREFIX = "/api/v1/"

@app.before_request
def check_auth():
    if request.path.startswith("/static"):
        return
    if request.path in _AUTH_EXEMPT:
        # If users exist, /setup should redirect to index
        if request.path == "/setup" and _has_users():
            return redirect(url_for("main_routes.index"))
        return
    if any(request.path.startswith(p) for p in _AUTH_EXEMPT_PREFIXES):
        return
    # /api/v1/* uses Bearer token auth — bypass session check entirely
    if request.path.startswith(_API_V1_PREFIX):
        return
    # If no users exist, everything redirects to setup
    if not _has_users():
        if request.path.startswith("/api/"):
            return jsonify({"error": "Setup erforderlich", "setup_required": True}), 503
        return redirect("/setup")
    # Check authentication
    if session.get("user_id"):
        # Ensure CSRF token is set
        if "csrf_token" not in session:
            session["csrf_token"] = secrets.token_hex(32)
        return _check_csrf()
    if request.path.startswith("/api/"):
        return jsonify({"error": "Nicht eingeloggt", "login_required": True}), 401
    return redirect(url_for("auth.login_page", next=request.path))

def _check_csrf():
    """Verify CSRF token for state-changing requests. Returns error response or None."""
    if request.method in ("GET", "HEAD", "OPTIONS"):
        return None
    _csrf_exempt_paths = {"/login", "/setup", "/forgot-password", "/api/health",
                          "/api/auth/passkey/login/begin",
                          "/api/auth/passkey/login/complete"}
    _csrf_exempt_prefixes = ("/reset-password", "/invite", "/auth/", "/api/v1/")
    if request.path in _csrf_exempt_paths:
        return None
    if any(request.path.startswith(p) for p in _csrf_exempt_prefixes):
        return None
    token = request.headers.get("X-CSRF-Token","")
    if not token or token != session.get("csrf_token",""):
        return jsonify({"error": "CSRF-Token ungültig"}), 403
    return None

@app.after_request
def add_security_headers(response):
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("X-Frame-Options", "SAMEORIGIN")
    response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
    response.headers.setdefault("Permissions-Policy", "geolocation=(), microphone=(), camera=()")
    return response

# ── SMTP helper ───────────────────────────────────────────────────────────────

def _email_html(title: str, *paragraphs: str) -> str:
    paras = "".join(f"<p style='margin:0 0 14px;line-height:1.6'>{p}</p>" for p in paragraphs)
    return f"""<!DOCTYPE html>
<html><head><meta charset='utf-8'></head>
<body style='margin:0;padding:0;background:#0f0f0f;font-family:system-ui,sans-serif'>
  <table width='100%' cellpadding='0' cellspacing='0'>
    <tr><td align='center' style='padding:40px 20px'>
      <table width='560' cellpadding='0' cellspacing='0' style='background:#1a1a1a;border-radius:12px;overflow:hidden;border:1px solid #2a2a2a'>
        <tr><td style='background:#1e3a5f;padding:24px 32px'>
          <div style='font-size:1.1rem;font-weight:700;color:#7eb8f7;letter-spacing:.05em'>⚡ EV Tracker</div>
        </td></tr>
        <tr><td style='padding:32px'>
          <h2 style='margin:0 0 20px;color:#e0e0e0;font-size:1.1rem;font-weight:600'>{title}</h2>
          <div style='color:#b0b0b0;font-size:.875rem'>{paras}</div>
        </td></tr>
        <tr><td style='padding:16px 32px;border-top:1px solid #2a2a2a'>
          <div style='color:#555;font-size:.75rem'>Diese E-Mail wurde automatisch von EV Tracker generiert.</div>
        </td></tr>
      </table>
    </td></tr>
  </table>
</body></html>"""

def _email_btn(url: str, label: str) -> str:
    return f"<a href='{url}' style='display:inline-block;background:#1e6fb5;color:#fff;text-decoration:none;padding:12px 28px;border-radius:8px;font-size:.875rem;font-weight:600;margin:8px 0'>{label}</a>"

def _xoauth2_string(user: str, access_token: str) -> str:
    """Build the SASL XOAUTH2 auth string."""
    import base64 as _b64
    raw = f"user={user}\x01auth=Bearer {access_token}\x01\x01"
    return _b64.b64encode(raw.encode()).decode()


def _smtp_google_access_token(cfg: dict):
    """Return a valid Google access token, refreshing if needed.
    Returns (token, error)."""
    now = time.time()
    tok = cfg.get("smtp_google_access_token", "")
    exp = float(cfg.get("smtp_google_token_expires_at", 0) or 0)
    if tok and exp - 60 > now:
        return tok, None
    refresh = cfg.get("smtp_google_refresh_token", "")
    if not refresh:
        return None, "Google nicht verbunden (kein Refresh-Token)"
    try:
        r = requests.post("https://oauth2.googleapis.com/token", data={
            "client_id":     cfg.get("smtp_google_client_id", ""),
            "client_secret": cfg.get("smtp_google_client_secret", ""),
            "refresh_token": refresh,
            "grant_type":    "refresh_token",
        }, timeout=15)
        r.raise_for_status()
        j = r.json()
        new_tok = j.get("access_token", "")
        ttl     = int(j.get("expires_in", 3600))
        if not new_tok:
            return None, "Kein Access-Token von Google erhalten"
        live = load_config()
        live["smtp_google_access_token"]     = new_tok
        live["smtp_google_token_expires_at"] = now + ttl
        save_config(live)
        return new_tok, None
    except Exception as e:
        return None, f"Google Token-Refresh fehlgeschlagen: {e}"


def _smtp_ms_access_token(cfg: dict):
    """Return a valid Microsoft access token, refreshing if needed.
    Returns (token, error)."""
    now = time.time()
    tok = cfg.get("smtp_ms_access_token", "")
    exp = float(cfg.get("smtp_ms_token_expires_at", 0) or 0)
    if tok and exp - 60 > now:
        return tok, None
    refresh = cfg.get("smtp_ms_refresh_token", "")
    if not refresh:
        return None, "Microsoft nicht verbunden (kein Refresh-Token)"
    tenant = cfg.get("smtp_ms_tenant_id", "common") or "common"
    try:
        r = requests.post(
            f"https://login.microsoftonline.com/{tenant}/oauth2/v2.0/token",
            data={
                "client_id":     cfg.get("smtp_ms_client_id", ""),
                "client_secret": cfg.get("smtp_ms_client_secret", ""),
                "refresh_token": refresh,
                "grant_type":    "refresh_token",
                "scope":         "https://outlook.office365.com/SMTP.Send offline_access",
            }, timeout=15)
        r.raise_for_status()
        j = r.json()
        new_tok = j.get("access_token", "")
        ttl     = int(j.get("expires_in", 3600))
        if not new_tok:
            return None, "Kein Access-Token von Microsoft erhalten"
        live = load_config()
        live["smtp_ms_access_token"]     = new_tok
        live["smtp_ms_token_expires_at"] = now + ttl
        # Microsoft may issue a rotated refresh token
        if j.get("refresh_token"):
            live["smtp_ms_refresh_token"] = j["refresh_token"]
        save_config(live)
        return new_tok, None
    except Exception as e:
        return None, f"Microsoft Token-Refresh fehlgeschlagen: {e}"


def _smtp_open(cfg: dict):
    """Open an authenticated SMTP connection based on smtp_auth_method.
    Returns (server, from_email, error). server is None on error."""
    import smtplib, ssl as _ssl
    method = cfg.get("smtp_auth_method", "basic") or "basic"

    if method == "oauth2_google":
        host, port, tls = "smtp.gmail.com", 587, "starttls"
        sender = cfg.get("smtp_google_sender_email", "") or cfg.get("smtp_from_email", "")
        token, err = _smtp_google_access_token(cfg)
        if err:
            return None, None, err
    elif method == "oauth2_microsoft":
        host, port, tls = "smtp.office365.com", 587, "starttls"
        sender = cfg.get("smtp_ms_sender_email", "") or cfg.get("smtp_from_email", "")
        token, err = _smtp_ms_access_token(cfg)
        if err:
            return None, None, err
    else:
        host = cfg.get("smtp_host", "")
        port = int(cfg.get("smtp_port", 587))
        tls  = cfg.get("smtp_tls", "starttls")
        sender = cfg.get("smtp_from_email", "")
        token  = None
        if not host:
            return None, None, "SMTP nicht konfiguriert"

    try:
        ctx = _ssl.create_default_context()
        if tls == "ssl":
            srv = smtplib.SMTP_SSL(host, port, context=ctx, timeout=15)
        else:
            srv = smtplib.SMTP(host, port, timeout=15)
            if tls == "starttls":
                srv.starttls(context=ctx)
        if method in ("oauth2_google", "oauth2_microsoft"):
            auth = _xoauth2_string(sender, token)
            code, resp = srv.docmd("AUTH", "XOAUTH2 " + auth)
            if code not in (235, 503):
                srv.quit()
                return None, None, f"XOAUTH2 abgelehnt (Code {code})"
        elif method == "relay_no_auth":
            pass
        else:
            user = cfg.get("smtp_user", "")
            pw   = cfg.get("smtp_password", "")
            if user:
                srv.login(user, pw)
        return srv, sender, None
    except Exception as e:
        return None, None, str(e)


def _send_email(to_addr: str, subject: str, body_html: str, body_text: str = None) -> tuple:
    from email.mime.multipart import MIMEMultipart
    from email.mime.text import MIMEText as _MIMEText
    cfg  = load_config()
    name = cfg.get("smtp_from_name","EV Tracker")
    srv, frm, err = _smtp_open(cfg)
    if err:
        return False, err
    if not frm:
        srv.quit()
        return False, "Keine Absenderadresse konfiguriert"
    try:
        msg = MIMEMultipart("alternative")
        msg["Subject"] = subject
        msg["From"]    = f"{name} <{frm}>"
        msg["To"]      = to_addr
        if body_text:
            msg.attach(_MIMEText(body_text, "plain", "utf-8"))
        msg.attach(_MIMEText(body_html, "html", "utf-8"))
        srv.sendmail(frm, [to_addr], msg.as_string())
        srv.quit()
        return True, None
    except Exception as e:
        try: srv.quit()
        except Exception: pass
        return False, str(e)

_SENSITIVE_CONFIG_KEYS = {
    "smtp_password", "oauth_google_client_secret", "oauth_microsoft_client_secret",
    "smtp_google_client_secret", "smtp_google_refresh_token", "smtp_google_access_token",
    "smtp_ms_client_secret", "smtp_ms_refresh_token", "smtp_ms_access_token",
    "meter_password", "meter_alfen_pass",
    "ha_token", "entsoe_api_key", "octopus_api_key", "tibber_token", "tariff_ha_token",
    "mqtt_password", "ntfy_token", "gotify_token",
}
from core.security import SECRET_MASK as _SECRET_MASK



# ── Vehicle Images ─────────────────────────────────────────────────────────────

_VEH_IMG_DIR = DATA_DIR / "vehicles"
_VEH_IMG_MAX_BYTES = 3 * 1024 * 1024  # 3 MB
_VEH_IMG_ALLOWED_MIME = {"image/png", "image/jpeg", "image/webp"}
_VEH_ID_RE = re.compile(r'^[a-zA-Z0-9_-]{1,64}$')

def _validate_vehicle_id(vid: str) -> bool:
    """Reject IDs with slashes, dots, traversal sequences or unsafe chars."""
    if not vid or not _VEH_ID_RE.match(vid):
        return False
    if ".." in vid or "/" in vid or "\\" in vid:
        return False
    return True

def _vehicle_exists(vid: str) -> bool:
    """Return True if vid is the primary vehicle or a configured extra vehicle."""
    if vid == "v0":
        return True
    cfg = load_config()
    return any(v.get("id") == vid for v in cfg.get("extra_vehicles", []))

def _safe_veh_img_path(vid: str) -> "Path":
    """Return resolved car.webp path; raises ValueError for unsafe IDs or traversal."""
    if not _validate_vehicle_id(vid):
        raise ValueError(f"Ungültige vehicle_id: {vid!r}")
    base = _VEH_IMG_DIR.resolve()
    target = (base / vid / "car.webp").resolve()
    if not str(target).startswith(str(base) + "/"):
        raise ValueError(f"Pfad-Traversal verhindert für vehicle_id: {vid!r}")
    return target

def _update_vehicle_image_meta(vid: str, mode: str, path: str,
                                source: str = "", attribution: str = "",
                                default_image_key: str = "") -> None:
    """Persist image metadata for v0 (top-level config keys) or extra_vehicles entry."""
    cfg = load_config()
    meta = {"image_mode": mode, "image_path": path,
            "image_source": source, "image_attribution": attribution,
            "default_image_key": default_image_key}
    if vid == "v0":
        cfg["vehicle_image_mode"]        = mode
        cfg["vehicle_image_path"]        = path
        cfg["vehicle_image_source"]      = source
        cfg["vehicle_image_attribution"] = attribution
        cfg["vehicle_default_image_key"] = default_image_key
    else:
        extras = cfg.get("extra_vehicles", [])
        for v in extras:
            if v.get("id") == vid:
                v.update(meta)
                break
        cfg["extra_vehicles"] = extras
    save_config(cfg)


# ── Location Helpers ────────────────────────────────────────────────────────────

from services.location_service import detect_location_status as _detect_location_status



import re as _re_sanitize_url

def _sanitize_url(url):
    if not url:
        return url
    # Remove user:pass@ from URL
    return _re_sanitize_url.sub(r'://([^@]+)@', '://', url)


def _normalize_signature_image(img, padding=None):
    """Crop to visible content bbox, then add transparent padding."""
    from PIL import Image as _PILImage, ImageOps as _ImageOps
    cfg = load_config()
    if padding is None:
        padding = int(cfg.get("signature_padding_px", 24))
    img = img.convert("RGBA")
    alpha = img.getchannel("A")
    bbox = alpha.getbbox()
    if bbox:
        img = img.crop(bbox)
    img = _ImageOps.expand(img, border=padding, fill=(255, 255, 255, 0))
    return img




# ── Export token store ────────────────────────────────────────────────────────
_export_tokens: dict = {}  # token -> {"path": str, "expires": float}

def _cleanup_export_tokens():
    """Delete expired token entries and their temp files."""
    now = time.time()
    expired = [k for k, v in list(_export_tokens.items()) if v["expires"] < now]
    for k in expired:
        info = _export_tokens.pop(k, None)
        if info:
            try:
                Path(info["path"]).unlink(missing_ok=True)
            except Exception:
                pass
    # Also clean up orphaned /tmp/ev_export_*.xlsx
    import glob as _glob
    for fp in _glob.glob("/tmp/ev_export_*.xlsx"):
        # only delete if not referenced by any token
        if not any(v["path"] == fp for v in _export_tokens.values()):
            try:
                if Path(fp).stat().st_mtime < now - 3600:
                    Path(fp).unlink(missing_ok=True)
            except Exception:
                pass

import calendar as _calendar

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

_RESTORE_ALLOWED_FILES = {
    "config.json", "sessions.db", "template.xlsx", "update_history.json",
}
_RESTORE_ALLOWED_DIRS = {
    "templates/", "signatures/", "vehicles/", "uploads/",
}
# WAL and SHM side-files are excluded from restore: they may be inconsistent
# without the matching DB and could corrupt the database on startup.
_RESTORE_BLOCKED_FILES = {"sessions.db-wal", "sessions.db-shm",
                          "ev_tracker.db-wal", "ev_tracker.db-shm"}
_BACKUP_MAX_UPLOAD_BYTES = 200 * 1024 * 1024  # 200 MB

def restore_backup(zip_path):
    """Zip-Slip-safe restore: validate all paths + symlinks, then extract allowed files."""
    # Reject oversized files
    if Path(zip_path).stat().st_size > _BACKUP_MAX_UPLOAD_BYTES:
        raise ValueError(f"ZIP zu groß (max. {_BACKUP_MAX_UPLOAD_BYTES//1024//1024} MB)")

    # Create a safety backup before overwriting anything
    try:
        create_backup("pre_restore")
    except Exception as e:
        log.warning("Sicherheits-Backup vor Restore fehlgeschlagen: %s", e)

    data_dir_resolved = DATA_DIR.resolve()

    with zipfile.ZipFile(zip_path, "r") as zf:
        members = zf.infolist()

        # Phase 1: validate every entry before extracting anything
        for info in members:
            member = info.filename
            if member.endswith("/"):
                continue
            # Reject symlinks (external_attr bit 0xA0000000 on Unix)
            if (info.external_attr >> 16) & 0xFFFF == 0xA1ED:
                raise ValueError(f"Symlink im ZIP nicht erlaubt: {member!r}")
            # Reject absolute paths and path-traversal components
            parts = member.replace("\\", "/").split("/")
            if any(p in ("", "..") for p in parts):
                raise ValueError(f"Unsicherer ZIP-Eintrag: {member!r}")
            dest = (DATA_DIR / member).resolve()
            if not str(dest).startswith(str(data_dir_resolved) + "/") and \
               str(dest) != str(data_dir_resolved):
                raise ValueError(f"Pfad außerhalb DATA_DIR: {member!r}")

        # Phase 2: extract only allowed paths, skip everything else
        for info in members:
            member = info.filename
            if member.endswith("/"):
                continue
            if member.startswith("backups/"):
                continue
            basename = member.rsplit("/", 1)[-1]
            if basename in _RESTORE_BLOCKED_FILES:
                log.debug("Restore: WAL/SHM-Datei blockiert %r", member)
                continue
            is_allowed = (
                member in _RESTORE_ALLOWED_FILES or
                any(member.startswith(d) for d in _RESTORE_ALLOWED_DIRS)
            )
            if not is_allowed:
                log.debug("Restore: übersprungen %r (nicht in Allowlist)", member)
                continue
            dest = DATA_DIR / member
            dest.parent.mkdir(parents=True, exist_ok=True)
            with zf.open(info) as src, open(dest, "wb") as dst:
                dst.write(src.read())

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
        except Exception: return None
    return (nxt-now).total_seconds()

def schedule_backup():
    global _backup_timer
    cfg=load_config(); cron=cfg.get("backup_cron","").strip()
    if not cron: return
    secs=parse_cron_next(cron)
    if not secs or secs<=0: return
    def run():
        cfg = load_config()
        try:
            out = create_backup("auto")
            log.info("Auto-Backup OK: %s", out.name)
            try:
                from notification_manager import fire_event as _fe
                _fe("backup_success", {"file": out.name, "size": out.stat().st_size}, cfg, db_path=DB_PATH)
            except Exception: pass
        except Exception as e:
            log.warning("Auto-Backup Fehler: %s", e)
            try:
                from notification_manager import fire_event as _fe
                _fe("backup_failed", {"error": str(e)}, cfg, db_path=DB_PATH)
            except Exception: pass
        schedule_backup()
    _backup_timer=Timer(secs,run); _backup_timer.daemon=True; _backup_timer.start()
    log.info("Nächstes Backup: %s",(datetime.now()+timedelta(seconds=secs)).strftime("%d.%m.%Y %H:%M"))

_cleanup_tokens_lock = False

def _cleanup_expired_tokens():
    now = time.time()
    for d in (_export_tokens, _pdf_tokens):
        expired = [k for k, v in list(d.items()) if isinstance(v, dict) and v.get("expires", 0) < now]
        for k in expired:
            d.pop(k, None)

_last_token_cleanup = 0.0

@app.before_request
def _before_request_cleanup():
    global _last_token_cleanup
    now = time.time()
    if now - _last_token_cleanup > 300:
        _last_token_cleanup = now
        _cleanup_expired_tokens()

# ── SMTP ─────────────────────────────────────────────────────────────────────

_SMTP_OAUTH = {
    "google": {
        "auth_url":  "https://accounts.google.com/o/oauth2/v2/auth",
        "token_url": "https://oauth2.googleapis.com/token",
        "scope":     "https://mail.google.com/",
        "cid":       "smtp_google_client_id",
        "csec":      "smtp_google_client_secret",
        "refresh":   "smtp_google_refresh_token",
        "access":    "smtp_google_access_token",
        "expires":   "smtp_google_token_expires_at",
        "sender":    "smtp_google_sender_email",
    },
    "microsoft": {
        "token_url": None,  # tenant-specific, built dynamically
        "scope":     "https://outlook.office365.com/SMTP.Send offline_access",
        "cid":       "smtp_ms_client_id",
        "csec":      "smtp_ms_client_secret",
        "refresh":   "smtp_ms_refresh_token",
        "access":    "smtp_ms_access_token",
        "expires":   "smtp_ms_token_expires_at",
        "sender":    "smtp_ms_sender_email",
    },
}




# ── Email Reports ─────────────────────────────────────────────────────────────

_report_timer = None

_DE_MONTHS_FULL = ["Januar","Februar","März","April","Mai","Juni",
                    "Juli","August","September","Oktober","November","Dezember"]
_EN_MONTHS_FULL = ["January","February","March","April","May","June",
                    "July","August","September","October","November","December"]


def _month_period(ym_str):
    """Build a period dict for a single YYYY-MM string."""
    from datetime import date
    try:
        year, month = int(ym_str[:4]), int(ym_str[5:7])
        start = date(year, month, 1)
        last = _calendar.monthrange(year, month)[1]
        end = date(year, month, last)
        return {"start": start, "end": end,
                "label_de": f"{_DE_MONTHS_FULL[month-1]} {year}",
                "label_en": f"{_EN_MONTHS_FULL[month-1]} {year}",
                "period_key": f"monthly:{year}-{month:02d}"}
    except Exception:
        return None


def calculate_report_period(schedule_type, period_mode, now, config):
    """Return dict with start, end (date objects), label_de, label_en, period_key."""
    from datetime import date, timedelta
    today = now.date() if hasattr(now, 'date') else now

    if period_mode == "single_month":
        ym = config.get("report_email_single_month", "")
        p = _month_period(ym)
        if p: return p
        return calculate_report_period(schedule_type, "current_period", now, config)

    if period_mode == "multiple_months":
        months = config.get("report_email_months", [])
        valid = sorted(set(m for m in months if len(m) == 7 and m[4] == "-"))
        if valid:
            p = _month_period(valid[0])
            if p:
                combined_key = "months:" + ",".join(valid)
                p = dict(p); p["period_key"] = combined_key
                return p
        return calculate_report_period(schedule_type, "current_period", now, config)

    if period_mode == "custom_range":
        s = config.get("report_email_custom_start_date", "")
        e = config.get("report_email_custom_end_date", "")
        try:
            start = date.fromisoformat(s); end = date.fromisoformat(e)
        except Exception:
            start = end = today
        return {"start": start, "end": end,
                "label_de": f"{start.strftime('%d.%m.%Y')} – {end.strftime('%d.%m.%Y')}",
                "label_en": f"{start.isoformat()} – {end.isoformat()}",
                "period_key": f"custom:{start}:{end}"}

    if schedule_type == "daily":
        d = (today - timedelta(days=1)) if period_mode == "previous_period" else today
        return {"start": d, "end": d, "label_de": d.strftime("%d.%m.%Y"),
                "label_en": d.isoformat(), "period_key": f"daily:{d}"}

    if schedule_type == "weekly":
        if period_mode == "previous_period":
            this_mon = today - timedelta(days=today.weekday())
            start = this_mon - timedelta(weeks=1)
        else:
            start = today - timedelta(days=today.weekday())
        end = start + timedelta(days=6)
        year, week, _ = start.isocalendar()
        return {"start": start, "end": end,
                "label_de": f"KW {week:02d} / {year}", "label_en": f"Week {week:02d} / {year}",
                "period_key": f"weekly:{year}-W{week:02d}"}

    if schedule_type == "monthly":
        if period_mode == "previous_period":
            first_this = today.replace(day=1)
            end = first_this - timedelta(days=1); start = end.replace(day=1)
        else:
            start = today.replace(day=1)
            last = _calendar.monthrange(today.year, today.month)[1]
            end = today.replace(day=last)
        return {"start": start, "end": end,
                "label_de": f"{_DE_MONTHS_FULL[start.month-1]} {start.year}",
                "label_en": f"{_EN_MONTHS_FULL[start.month-1]} {start.year}",
                "period_key": f"monthly:{start.year}-{start.month:02d}"}

    if schedule_type == "quarterly":
        q = (today.month - 1) // 3 + 1; year = today.year
        if period_mode == "previous_period":
            q -= 1
            if q == 0: q = 4; year -= 1
        sm = (q - 1) * 3 + 1; em = sm + 2
        start = date(year, sm, 1)
        end   = date(year, em, _calendar.monthrange(year, em)[1])
        return {"start": start, "end": end,
                "label_de": f"Q{q} {year}", "label_en": f"Q{q} {year}",
                "period_key": f"quarterly:{year}-Q{q}"}

    if schedule_type == "yearly":
        year = (today.year - 1) if period_mode == "previous_period" else today.year
        return {"start": date(year, 1, 1), "end": date(year, 12, 31),
                "label_de": str(year), "label_en": str(year),
                "period_key": f"yearly:{year}"}

    if schedule_type == "custom_days":
        x = int(config.get("report_email_custom_days", 14))
        end = today; start = today - timedelta(days=x)
        return {"start": start, "end": end,
                "label_de": f"Letzte {x} Tage", "label_en": f"Last {x} days",
                "period_key": f"custom_days:{x}:{today}"}

    return calculate_report_period("monthly", period_mode, now, config)


def calculate_report_periods(schedule_type, period_mode, now, config):
    """Return list of period dicts. Usually one, multiple for multiple_months."""
    if period_mode == "multiple_months":
        months = config.get("report_email_months", [])
        valid = sorted(set(m for m in months if len(m) == 7 and m[4] == "-"))[:24]
        periods = [p for m in valid for p in [_month_period(m)] if p]
        if periods:
            return periods
    return [calculate_report_period(schedule_type, period_mode, now, config)]


def _get_report_sessions(start_date, end_date, location_filter="all", vehicle_filter="all"):
    from datetime import timedelta
    where  = ["end_ts IS NOT NULL", "start_ts >= ?", "start_ts < ?"]
    params = [start_date.isoformat(), (end_date + timedelta(days=1)).isoformat()]
    _loc_norm = normalize_location(location_filter) if location_filter not in ("all",) else location_filter
    if _loc_norm == "home":
        where.append("location = 'home'")
    elif _loc_norm == "extern":
        where.append("location = 'extern'")
    if vehicle_filter and vehicle_filter != "all":
        where.append("vehicle_id = ?"); params.append(vehicle_filter)
    sql = f"SELECT * FROM sessions WHERE {' AND '.join(where)} ORDER BY start_ts ASC"
    con = sqlite3.connect(DB_PATH); con.row_factory = sqlite3.Row
    rows = con.execute(sql, params).fetchall(); close_db_if_owned(con)
    return [dict(r) for r in rows]


def _report_filter_labels(cfg, is_de):
    loc = cfg.get("report_email_location_filter", "all")
    veh = cfg.get("report_email_vehicle_filter", "all")
    if is_de:
        loc_lbl = {"all": "Alle Ladevorgänge", "home": "Nur Zuhause / Intern",
                   "external": "Nur Extern"}.get(loc, loc)
        veh_lbl = "Alle Fahrzeuge" if veh == "all" else veh
    else:
        loc_lbl = {"all": "All charging sessions", "home": "Home only",
                   "external": "External only"}.get(loc, loc)
        veh_lbl = "All vehicles" if veh == "all" else veh
    return loc_lbl, veh_lbl


def _build_report_html(sessions, period_info, cfg, lang="de"):
    is_de      = lang != "en"
    plabel     = period_info.get("label_de" if is_de else "label_en", "")
    loc_filter = cfg.get("report_email_location_filter", "all")
    loc_lbl, veh_lbl = _report_filter_labels(cfg, is_de)
    total_kwh  = sum(s.get("kwh_charged") or 0 for s in sessions)
    total_cost = sum(s.get("cost_eur")    or 0 for s in sessions)
    total_secs = sum(s.get("duration_sec") or 0 for s in sessions)
    home_kwh   = sum((s.get("kwh_charged") or 0) for s in sessions if s.get("location") == "home")
    ext_kwh    = sum((s.get("kwh_charged") or 0) for s in sessions if s.get("location") == "extern")
    total_h    = total_secs / 3600
    avg_price  = total_cost / total_kwh if total_kwh else 0
    avg_power  = total_kwh / total_h if total_h else 0
    n          = len(sessions)
    if is_de:
        title = "EV Tracker — Lade-Report"
        rows  = [("Zeitraum", plabel), ("Filter", loc_lbl), ("Fahrzeug", veh_lbl),
                 ("Ladevorgänge", str(n)),
                 ("Geladene kWh", f"{total_kwh:.2f} kWh"),
                 ("Gesamtkosten", f"{total_cost:.2f} €"),
                 ("Ø Preis/kWh", f"{avg_price:.4f} €"),
                 ("Ladezeit gesamt", f"{total_h:.1f} h"),
                 ("Ø Ladeleistung", f"{avg_power:.1f} kW")]
        if loc_filter == "all" and (home_kwh or ext_kwh):
            rows.append(("Zuhause / Extern", f"{home_kwh:.1f} / {ext_kwh:.1f} kWh"))
        empty_txt = "Keine Ladevorgänge im gewählten Zeitraum."
    else:
        title = "EV Tracker — Charging Report"
        rows  = [("Period", plabel), ("Sessions", str(n)),
                 ("Energy charged", f"{total_kwh:.2f} kWh"),
                 ("Total cost", f"{total_cost:.2f} €"),
                 ("Avg price/kWh", f"{avg_price:.4f} €"),
                 ("Total charge time", f"{total_h:.1f} h"),
                 ("Avg charge power", f"{avg_power:.1f} kW")]
        if home_kwh or ext_kwh:
            rows.append(("Home / External", f"{home_kwh:.1f} / {ext_kwh:.1f} kWh"))
        empty_txt = "No charging sessions in the selected period."
    trows = "".join(
        f'<tr><td style="padding:6px 14px;color:#888;white-space:nowrap">{k}</td>'
        f'<td style="padding:6px 14px;font-weight:600;color:#fff">{v}</td></tr>'
        for k, v in rows)
    empty = f'<p style="color:#f59e0b;margin:16px 0">{empty_txt}</p>' if not sessions else ""
    return (f'<!DOCTYPE html><html><head><meta charset="utf-8"></head>'
            f'<body style="background:#0f1117;color:#e8e8f0;font-family:sans-serif;margin:0;padding:24px">'
            f'<div style="max-width:560px;margin:0 auto"><div style="background:#1e2030;border-radius:12px;padding:28px">'
            f'<h1 style="color:#6ee7b7;font-size:1.3rem;margin:0 0 6px">⚡ {title}</h1>'
            f'<hr style="border:none;border-top:1px solid #2d3147;margin:16px 0">'
            f'{empty}<table style="width:100%;border-collapse:collapse">{trows}</table>'
            f'<hr style="border:none;border-top:1px solid #2d3147;margin:16px 0">'
            f'<p style="color:#555;font-size:.75rem;margin:0">EV Tracker v{APP_VERSION}</p>'
            f'</div></div></body></html>')


def _build_multi_month_html(periods_sessions, cfg, lang="de"):
    """Build email HTML for multiple months. periods_sessions: list of (period_info, sessions)."""
    is_de   = lang != "en"
    title   = "EV Tracker — Lade-Report" if is_de else "EV Tracker — Charging Report"
    n_months = len(periods_sessions)
    subj_lbl = (f"Bericht {n_months} Monate" if is_de else f"Report {n_months} months") if n_months != 1 else (
        periods_sessions[0][0].get("label_de" if is_de else "label_en", ""))
    if is_de:
        hdr_cells = ["Monat","Ladevorgänge","kWh","Kosten","Ø Preis/kWh"]
    else:
        hdr_cells = ["Month","Sessions","kWh","Cost","Avg Price/kWh"]
    hdr_row = "".join(
        f'<th style="padding:6px 10px;color:#888;text-align:left;white-space:nowrap">{h}</th>'
        for h in hdr_cells)
    data_rows = ""
    total_kwh = total_cost = total_n = 0
    for period_info, sessions in periods_sessions:
        plabel = period_info.get("label_de" if is_de else "label_en", "")
        kwh    = sum(s.get("kwh_charged") or 0 for s in sessions)
        cost   = sum(s.get("cost_eur") or 0 for s in sessions)
        n      = len(sessions)
        avg_p  = cost / kwh if kwh else 0
        total_kwh += kwh; total_cost += cost; total_n += n
        data_rows += (
            f'<tr style="border-bottom:1px solid rgba(255,255,255,.04)">'
            f'<td style="padding:5px 10px;color:#e8e8f0">{plabel}</td>'
            f'<td style="padding:5px 10px;color:#e8e8f0;text-align:right">{n}</td>'
            f'<td style="padding:5px 10px;color:#e8e8f0;text-align:right">{kwh:.2f}</td>'
            f'<td style="padding:5px 10px;color:#e8e8f0;text-align:right">{cost:.2f} €</td>'
            f'<td style="padding:5px 10px;color:#e8e8f0;text-align:right">{avg_p:.4f} €</td>'
            f'</tr>')
    avg_total_p = total_cost / total_kwh if total_kwh else 0
    data_rows += (
        f'<tr style="border-top:1px solid #2d3147;font-weight:700">'
        f'<td style="padding:5px 10px;color:#6ee7b7">{"Gesamt" if is_de else "Total"}</td>'
        f'<td style="padding:5px 10px;color:#6ee7b7;text-align:right">{total_n}</td>'
        f'<td style="padding:5px 10px;color:#6ee7b7;text-align:right">{total_kwh:.2f}</td>'
        f'<td style="padding:5px 10px;color:#6ee7b7;text-align:right">{total_cost:.2f} €</td>'
        f'<td style="padding:5px 10px;color:#6ee7b7;text-align:right">{avg_total_p:.4f} €</td>'
        f'</tr>')
    return (
        f'<!DOCTYPE html><html><head><meta charset="utf-8"></head>'
        f'<body style="background:#0f1117;color:#e8e8f0;font-family:sans-serif;margin:0;padding:24px">'
        f'<div style="max-width:640px;margin:0 auto"><div style="background:#1e2030;border-radius:12px;padding:28px">'
        f'<h1 style="color:#6ee7b7;font-size:1.3rem;margin:0 0 6px">⚡ {title}</h1>'
        f'<p style="color:#888;margin:0 0 16px;font-size:.85rem">{subj_lbl}</p>'
        f'<hr style="border:none;border-top:1px solid #2d3147;margin:16px 0">'
        f'<table style="width:100%;border-collapse:collapse">'
        f'<thead><tr style="border-bottom:1px solid #2d3147">{hdr_row}</tr></thead>'
        f'<tbody>{data_rows}</tbody></table>'
        f'<hr style="border:none;border-top:1px solid #2d3147;margin:16px 0">'
        f'<p style="color:#555;font-size:.75rem;margin:0">EV Tracker v{APP_VERSION}</p>'
        f'</div></div></body></html>')


def _send_email_with_attachments(to_addr, subject, body_html, attachments=None):
    from email.mime.multipart import MIMEMultipart
    from email.mime.text import MIMEText
    from email.mime.base import MIMEBase
    from email import encoders as _enc
    cfg  = load_config()
    name = cfg.get("smtp_from_name","EV Tracker")
    srv, frm, err = _smtp_open(cfg)
    if err:
        return False, err
    if not frm:
        srv.quit()
        return False, "Keine Absenderadresse konfiguriert"
    try:
        msg = MIMEMultipart(); msg["From"] = f"{name} <{frm}>"; msg["To"] = to_addr
        msg["Subject"] = subject; msg.attach(MIMEText(body_html, "html", "utf-8"))
        for fname, data, mime_type in (attachments or []):
            part = MIMEBase(*mime_type.split("/", 1)); part.set_payload(data)
            _enc.encode_base64(part)
            part.add_header("Content-Disposition", "attachment", filename=fname)
            msg.attach(part)
        srv.sendmail(frm, to_addr, msg.as_string()); srv.quit()
        return True, None
    except Exception as e:
        try: srv.quit()
        except Exception: pass
        return False, str(e)


def _log_report_history(period_info, cfg, status, error, triggered_by):
    try:
        period_label = period_info.get("label_de", period_info.get("period_key", ""))
        con = _get_db()
        con.execute("""INSERT INTO email_report_history
            (sent_at,schedule_type,period_start,period_end,period_key,
             location_filter,vehicle_filter,recipients,status,error,triggered_by,
             period_label,period_mode)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (datetime.utcnow().isoformat(),
             cfg.get("report_email_schedule_type","monthly"),
             period_info["start"].isoformat(), period_info["end"].isoformat(),
             period_info["period_key"],
             cfg.get("report_email_location_filter","all"),
             cfg.get("report_email_vehicle_filter","all"),
             json.dumps(cfg.get("report_email_recipients",[])),
             status, error, triggered_by,
             period_label,
             cfg.get("report_email_period_mode","previous_period")))
        con.commit(); close_db_if_owned(con)
    except Exception as e:
        log.warning("Report-History-Log fehlgeschlagen: %s", e)


def _send_report_email(cfg=None, triggered_by="auto"):
    if cfg is None: cfg = load_config()
    if not cfg.get("report_email_enabled"):
        return False, "Reports nicht aktiviert"
    if not cfg.get("smtp_host","") or not cfg.get("smtp_from_email",""):
        return False, "SMTP nicht konfiguriert"
    recipients = cfg.get("report_email_recipients", [])
    if not recipients:
        return False, "Keine Empfänger konfiguriert"
    stype       = cfg.get("report_email_schedule_type", "monthly")
    period_mode = cfg.get("report_email_period_mode", "previous_period")
    loc_filter  = cfg.get("report_email_location_filter", "all")
    veh_filter  = cfg.get("report_email_vehicle_filter", "all")
    lang        = cfg.get("report_email_language", "auto")
    if lang == "auto": lang = "de"
    is_de       = lang != "en"

    periods = calculate_report_periods(stype, period_mode, datetime.now(), cfg)
    if period_mode == "multiple_months" and len(periods) > 1:
        combined_key = "months:" + ",".join(
            p["period_key"].replace("monthly:", "") for p in periods
        )
    else:
        combined_key = periods[0]["period_key"] if periods else "unknown"

    if triggered_by == "auto" and cfg.get("report_email_last_sent_key","") == combined_key:
        log.info("Report bereits gesendet für %s — übersprungen", combined_key)
        _log_report_history(periods[0], cfg, "skipped", None, triggered_by)
        return True, None

    attachments = []

    if period_mode == "multiple_months" and len(periods) > 1:
        # Multi-month: per-month sessions + combined HTML + multi-sheet Excel
        periods_sessions = []
        for p in periods:
            s = _get_report_sessions(p["start"], p["end"], loc_filter, veh_filter)
            periods_sessions.append((p, s))
        all_sessions = [s for _, ss in periods_sessions for s in ss]
        if cfg.get("report_email_include_summary", True):
            html = _build_multi_month_html(periods_sessions, cfg, lang)
        else:
            n_months = len(periods)
            html = f"<p>EV Tracker Report — {n_months} {'Monate' if is_de else 'months'}</p>"
        n_months = len(periods)
        if is_de:
            subject = f"EV Tracker — Bericht {n_months} Monate"
        else:
            subject = f"EV Tracker — Report {n_months} months"
        if cfg.get("report_email_include_excel") and all_sessions:
            try:
                from export_excel import export_multi_month_bytes as _emm
                sig_path = str(SIGNATURE_PATH) if SIGNATURE_PATH.exists() and cfg.get("report_email_include_signature") else None
                sig_map  = cfg.get("signature_mapping", {}) if sig_path else {}
                xl_bytes, _ = _emm(
                    periods_sessions=periods_sessions,
                    loc_filter=loc_filter, config=cfg, lang=lang,
                    include_signature=bool(sig_path),
                    signature_path=sig_path, signature_mapping=sig_map)
                attachments.append(("Ladeprotokoll.xlsx", xl_bytes,
                                     "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"))
            except Exception as e:
                log.warning("Multi-Monats-Excel-Anhang fehlgeschlagen: %s", e)
        # use first period for history logging
        log_period = periods[0]
        log_period = dict(log_period); log_period["period_key"] = combined_key
    else:
        # Single period (includes single_month)
        period_info = periods[0]
        sessions    = _get_report_sessions(period_info["start"], period_info["end"], loc_filter, veh_filter)
        if cfg.get("report_email_include_summary", True):
            html = _build_report_html(sessions, period_info, cfg, lang)
        else:
            html = f"<p>EV Tracker Report — {period_info.get('label_de','')}</p>"
        plabel  = period_info.get("label_de" if is_de else "label_en", combined_key)
        subject = f"EV Tracker — {('Monatsbericht' if is_de else 'Monthly Report')} {plabel}" if period_mode in ("single_month","previous_period","current_period") and stype == "monthly" else f"EV Tracker — Report {plabel}"
        if cfg.get("report_email_include_excel") and sessions:
            try:
                from export_excel import export as _export_func
                sig_path = str(SIGNATURE_PATH) if SIGNATURE_PATH.exists() and cfg.get("report_email_include_signature") else None
                sig_map  = cfg.get("signature_mapping", {}) if sig_path else {}
                xl_loc   = normalize_location(loc_filter) if loc_filter not in ("all",) else loc_filter
                xl_bytes, _ = _export_func(
                    year=period_info["start"].year, month=period_info["start"].month,
                    location=xl_loc, config=cfg, lang=lang,
                    include_signature=bool(sig_path),
                    signature_path=sig_path, signature_mapping=sig_map, return_warnings=True)
                attachments.append(("Ladeprotokoll.xlsx", xl_bytes,
                                     "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"))
            except Exception as e:
                log.warning("Report-Excel-Anhang fehlgeschlagen: %s", e)
        log_period = period_info

    errors = []
    for to in recipients:
        ok, err = _send_email_with_attachments(to, subject, html, attachments)
        if not ok: errors.append(f"{to}: {err}")
    if errors:
        _log_report_history(log_period, cfg, "error", "; ".join(errors), triggered_by)
        return False, "; ".join(errors)
    cfg["report_email_last_sent_key"] = combined_key
    save_config(cfg)
    _log_report_history(log_period, cfg, "sent", None, triggered_by)
    log.info("Report gesendet: %s → %s", combined_key, recipients)
    return True, None


def _next_report_seconds(cfg, now=None):
    if not cfg.get("report_email_enabled"): return None
    stype  = cfg.get("report_email_schedule_type", "monthly")
    t_str  = cfg.get("report_email_time", "08:00")
    if now is None: now = datetime.now()
    try: t_hour, t_min = [int(x) for x in t_str.split(":")]
    except Exception: t_hour, t_min = 8, 0
    today = now.date()
    from datetime import date, timedelta

    if stype == "daily":
        fire = datetime.combine(today, datetime.min.time()).replace(hour=t_hour, minute=t_min)
        if fire <= now: fire += timedelta(days=1)
        return (fire - now).total_seconds()

    if stype == "weekly":
        wd = int(cfg.get("report_email_weekday", 1)) - 1
        da = (wd - today.weekday()) % 7 or 7
        fire = datetime.combine(today + timedelta(days=da), datetime.min.time()).replace(hour=t_hour, minute=t_min)
        if fire <= now: fire += timedelta(weeks=1)
        return (fire - now).total_seconds()

    if stype == "monthly":
        dom = int(cfg.get("report_email_day_of_month", 1))
        try:
            fire = datetime.combine(today.replace(day=dom), datetime.min.time()).replace(hour=t_hour, minute=t_min)
            if fire > now: return (fire - now).total_seconds()
        except ValueError: pass
        nm = today.month % 12 + 1; ny = today.year + (1 if today.month == 12 else 0)
        try: fire = datetime(ny, nm, dom, t_hour, t_min)
        except ValueError: fire = datetime(ny, nm, _calendar.monthrange(ny, nm)[1], t_hour, t_min)
        return (fire - now).total_seconds()

    if stype == "quarterly":
        dom = int(cfg.get("report_email_day_of_month", 1))
        for yo in range(2):
            for qm in [1, 4, 7, 10]:
                try:
                    fire = datetime(today.year + yo, qm, dom, t_hour, t_min)
                    if fire > now: return (fire - now).total_seconds()
                except ValueError: continue
        return 90 * 86400

    if stype == "yearly":
        mo  = int(cfg.get("report_email_month", 1))
        dom = int(cfg.get("report_email_day_of_month", 1))
        for year in [today.year, today.year + 1]:
            try:
                fire = datetime(year, mo, dom, t_hour, t_min)
                if fire > now: return (fire - now).total_seconds()
            except ValueError: continue
        return 365 * 86400

    if stype == "custom_days":
        x    = int(cfg.get("report_email_custom_days", 14))
        fire = datetime.combine(today, datetime.min.time()).replace(hour=t_hour, minute=t_min)
        if fire <= now: fire += timedelta(days=x)
        return (fire - now).total_seconds()

    if stype == "custom_cron":
        cron = cfg.get("report_email_cron", "").strip()
        if not cron: return None
        try:
            parts = cron.split()
            if len(parts) >= 2:
                c_min, c_hour = int(parts[0]), int(parts[1])
                fire = datetime.combine(today, datetime.min.time()).replace(hour=c_hour, minute=c_min)
                if fire <= now: fire += timedelta(days=1)
                return (fire - now).total_seconds()
        except Exception: return None

    return None


def schedule_report():
    global _report_timer
    cfg  = load_config()
    secs = _next_report_seconds(cfg)
    if not secs or secs <= 0: return
    def run():
        try:
            ok, err = _send_report_email(triggered_by="auto")
            if not ok: log.warning("Auto-Report Fehler: %s", err)
        except Exception as e:
            log.warning("Auto-Report Exception: %s", e)
        schedule_report()
    _report_timer = Timer(secs, run); _report_timer.daemon = True; _report_timer.start()
    log.info("Nächster Auto-Report: %s", (datetime.now() + timedelta(seconds=secs)).strftime("%d.%m.%Y %H:%M"))



def _save_report_record(vehicle_id, period_info, loc_filter, veh_filter, status,
                         created_by, excel_bytes=None, pdf_bytes=None, summary=None):
    """Insert a report into the archive. Returns the new report id."""
    con = _get_db()
    cur = con.execute("""INSERT INTO reports
        (created_at, vehicle_id, period_start, period_end, period_label, period_mode,
         location_filter, vehicle_filter, status, created_by, excel_bytes, pdf_bytes, summary_json)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (datetime.utcnow().isoformat(),
         vehicle_id,
         period_info["start"].isoformat(), period_info["end"].isoformat(),
         period_info.get("label_de", ""), period_info.get("period_key",""),
         loc_filter, veh_filter, status, created_by,
         excel_bytes, pdf_bytes,
         json.dumps(summary or {})))
    report_id = cur.lastrowid
    con.commit(); close_db_if_owned(con)
    return report_id




_pdf_tokens: dict = {}  # token -> {bytes, expires}


if __name__=="__main__":
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    ensure_started_once()
    app.run(host="0.0.0.0",port=8080,debug=False)
