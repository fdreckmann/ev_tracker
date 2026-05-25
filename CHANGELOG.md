# Changelog

## v2.0.41 — 2026-05-25

### Bugfix: "readonly database" / "Permission denied: /home/evtracker"

- **Dockerfile**: Non-Root-User `evtracker` (UID 10001) erhält beschreibbares Home-Verzeichnis `/home/evtracker`; `ENV HOME=/home/evtracker` gesetzt — behebt `Permission denied` beim Schreiben von Temp-/Cache-Dateien
- **`_has_users()`** wirft `sqlite3.OperationalError` direkt (statt in `RuntimeError` zu wrappen), damit Aufrufer die Fehlermeldung korrekt auswerten können
- **`check_auth()`** und **`setup_page()`** verwenden `_db_error_hint()` / `_db_error_message()` — erkennen `readonly database`, `unable to open database file` und `no such table users` und zeigen jeweils den passenden deutschen Fix-Hinweis
- **`setup_page()`**: Zeigt niemals das Setup-Formular wenn `_has_users()` eine Exception wirft — stattdessen klare Fehlermeldung mit konkretem `chown`-Befehl
- **`/api/health`**: `db_writable`, `users_table_exists`, `users_count`, `startup_error` — vollständige Diagnose ohne Login
- **README** — Fehlerbehebungs-Abschnitt mit `chown`-Fix für Unraid und Docker-Compose

## v2.0.40 — 2026-05-25

### Bugfix: "First User Setup" trotz vorhandenem Admin / Internal Error

- **`_has_users()`** gibt keine `False` mehr zurück wenn die Datenbank nicht erreichbar ist — wirft stattdessen Exception, damit DB-Fehler und "kein Benutzer" klar unterschieden werden
- **`ensure_started_once()`** setzt `_started_once = True` nur noch nach erfolgreichem Startup; bei Fehler wird `_startup_error` gespeichert und erneut geworfen
- **`check_auth()`** fängt Exceptions von `ensure_started_once()` und `_has_users()` separat ab und zeigt benutzerfreundliche Fehlerseite (`error.html`) statt Internal Server Error
- **`setup_page()`** behandelt `sqlite3.OperationalError` zusätzlich zu `IntegrityError`
- **`/api/health`** erweitert: `db_writable`, `users_table_exists`, `users_count`, `startup_error` — erlaubt Diagnose ohne Login
- **`error.html`** — neue Fehlerseite mit Hinweisen zur Berechtigungsbehebung (Unraid / Docker)
- **README** — Abschnitt „Fehlerbehebung" mit `chown`-Fix für Unraid und allgemeine Docker-Lösung

---

## v2.0.37 — 2026-05-25

### Sichere Update-Anzeige (read-only)

- **Kein In-App-Update mehr** — Docker-Socket-Nutzung und automatische Container-Restarts dauerhaft entfernt
- **`GET /api/update-info`** — Neuer read-only Endpunkt; liefert aktuelle Version, Remote-Version und Release-Details
- **Update-Anzeige** in Konfiguration → „ℹ️ Version & Update"
  - Aktuelle Version und Build-Datum
  - Remote-Versionsabgleich via `update-info.json` auf GitHub (6 h Cache)
  - Release-Notizen: Zusammenfassung, Fixes, Breaking Changes, Migrationhinweise
  - Docker-/Watchtower-Update-Anleitung (kein Install-Button, keine automatische Aktion)
  - „Release Notes öffnen"-Link zur GitHub-Release-Seite
- **`update-info.json`** am Repo-Root als Remote-Metadatenquelle
- **`version.json`** vereinfacht auf `{version, build, channel, commit}`
- **Semver-Vergleich** mit Pre-Release-Unterstützung (`1.2.3-beta` < `1.2.3`)
- **`EV_TRACKER_UPDATE_CHECK_ENABLED=false`** deaktiviert den Remote-Check vollständig
- CHANGELOG.md eingeführt; README.md auf stabile Installationsdokumentation reduziert

---

## v2.0.36 — 2026-05-25

### Fehlende Ladevorgänge automatisch erkennen

- Nach jedem Provider-Poll wird ein Fahrzeug-Snapshot gespeichert (SOC, Kilometerstand, Standort)
- Wenn das Fahrzeug offline war und SOC danach gestiegen ist (oder nicht genug gefallen bei gefahrener Strecke), wird ein Kandidat erstellt
- Energieschätzung: SOC-Delta × Batteriekapazität + Fahrverbrauch (konfigurierbar, default 18 kWh/100 km)
- Vorschläge enthalten: Zeitraum, SOC Start/Ende, km, geschätzte kWh, Standort-Vorschlag, Ladetyp-Vorschlag, Konfidenz
- Konfidenz-Score: 50 %–95 % je nach verfügbaren Daten
- Plausibilitätsregeln: min. SOC-Anstieg 3 %, min. 2 kWh, min. 30 min Lücke, keine bestehende Session im Zeitraum
- Doppelte Vorschläge werden verhindert (gleiche Snapshot-IDs oder ignorierter Zeitraum)
- **API-Endpunkte**
  - `GET /api/missing-charges` — offene Vorschläge
  - `GET /api/missing-charges/<id>` — Vorschlag-Detail
  - `POST /api/missing-charges/<id>/accept` — akzeptieren + Vorausfüll-Daten zurückgeben
  - `POST /api/missing-charges/<id>/dismiss` — einmalig ignorieren
  - `POST /api/missing-charges/<id>/ignore` — dauerhaft ignorieren (gleicher Zeitraum wird nicht erneut vorgeschlagen)
  - `POST /api/missing-charges/check` — manuelle Neuberechnung
- **Desktop-UI**: Dashboard-Hinweiskarte, Sektion im Ladevorgänge-Tab, „Übernehmen" füllt manuellen Dialog vor
- **Mobile-UI**: Kompakter Hinweis im Mobile-Dashboard
- **Neue DB-Tabellen**: `vehicle_snapshots`, `missing_charge_candidates`
- **Neue Permissions**: `missing_charges:view`, `missing_charges:manage`
- **Konfigurierbar**: Mindest-Lücke, SOC-Schwelle, kWh-Schwelle, Verbrauch kWh/100 km, Batterie kWh pro Fahrzeug

---

## v2.0.35 — 2026-05-24

### Security-Hardening & Bugfixes

- **Security-Hardening**
  - Docker-Socket-Mount und In-App-Update vollständig entfernt
  - Passwort-Hashing auf PBKDF2:SHA-256 (werkzeug) migriert; Legacy-SHA-256-Hashes werden beim Login transparent upgradet
  - `require_login` invalidiert deaktivierte Benutzer-Sessions sofort
  - `EV_TRACKER_EXPOSURE=external`: ProxyFix, Secure-Cookies, HSTS, `X-Frame-Options: DENY`
  - Sicherheitsheader (`X-Content-Type-Options`, `X-Frame-Options`) immer gesetzt
  - Fahrzeug-Credentials (Tokens, Passwörter) in API-Antworten maskiert (`********`)
  - `escapeHtml()` in api.js; XSS-Fixes in Sessions-Modal und Toast-Notifications
- **Bugfixes**
  - Billing-Config `SELECT id` → `SELECT vehicle_id`
  - Neue Fahrzeug-IDs als UUID statt Unix-Timestamp
  - `refresh_vehicle_location_state()` unterstützt `force=True` zum Umgehen des 30 s-TTL-Cache
- **Container-Hardening** (`docker-compose.yml`): `no-new-privileges:true`, `cap_drop: ALL`

---

## v2.0.34 — 2026-05-20

### Manuelles Hinzufügen von Ladevorgängen (Desktop + Mobile)

- Neuer Dialog mit allen relevanten Feldern: Fahrzeug, Start/Ende, Standort, AC/DC, kWh, Preis/kWh, Kosten, Zählerstände, SOC Start/Ende, KM-Stand, Wallbox-kW, Notiz, Grund
- Auto-Berechnung: kWh aus Zählerständen, Kosten aus kWh × Preis, Ø-Leistung aus Dauer + kWh
- Überschneidungsprüfung mit Warnung und „Trotzdem speichern"-Option
- Manuell erfasste Sessions im Export, Reports und Dashboard vollständig sichtbar
- Badge „✏ Manuell" in der Session-Liste; Detailansicht zeigt Quelle, Grund und Notiz
- `PATCH /api/sessions/<id>` auf alle Felder erweitert (SOC, KM, Standort, Ladeart, Zähler, Notiz)
- Neue DB-Spalten: `manual_note`, `manual_reason`, `created_mode`
- **Standorterkennung Bugfixes**
  - `device_tracker`-Entities in HA: `not_home`/Zonen-Namen werden korrekt als „Extern" erkannt
  - `location_ha_entities` als String konfiguriert (Legacy-Format) wird jetzt korrekt als Liste geparst
  - Dashboard-Standort-Kachel zeigt „Deaktiviert" statt „—" wenn Standorterkennung abgeschaltet ist
  - Nach Standort-Test wird TTL-Cache sofort aktualisiert (kein 30 s-Delay)
- **Mobile Bugfixes**
  - Monatsstatistik nutzt korrektes Datumsfeld (`start_ts`)
  - Letzte 3 Ladevorgänge in korrekter Reihenfolge (neueste zuerst)
