# Changelog

## v2.4.1 — 2026-08-04

### Fix: Restfehler — veralteter Live-Zählerwert nach fehlgeschlagenem/Power-only-Poll

Nachprüfung des 2.4.0-Fixes ergab einen Restfehler in `meter_snapshot_service.py`: `meter_snap_last_val` (kumulativer Live-Zählerwert) wurde nur bei einem vollständigen erfolgreichen Poll aktualisiert. Nach einem fehlgeschlagenen Poll oder einem Power-only-Poll (kein `chargeTotalImport`) blieb der alte Wert stehen und konnte über `app/server.py` als aktuelles Signal an die `ChargingStateMachine` weitergereicht werden — mit dem Risiko einer falschen Energy-only-Start-/Stop-Erkennung anhand veralteter Daten.

- **Live-State** (`meter_snap_last_power`, `meter_snap_last_val`, `meter_snap_last_ok`) repräsentiert jetzt ausschließlich den aktuellen Poll: bei Fehlschlag werden beide Werte auf `None` gesetzt, bei einem Power-only-Poll wird der kumulative Wert auf `None` gesetzt statt den vorherigen zu behalten
- **Snapshot-Deduplizierung** (Heartbeat/Delta-Vergleich) verwendet jetzt einen eigenen Vergleichswert `meter_snap_last_saved_val`, der unabhängig vom volatilen Live-Wert den zuletzt in die DB geschriebenen Zählerstand hält — die Dedupe-Logik beeinflusst dadurch ausschließlich, ob ein DB-Snapshot geschrieben wird, nie mehr die Live-Werte
- Ein gültiger Zählerwert von `0` wird weiterhin korrekt erkannt (keine Truthiness-Prüfung)
- Neue Debug-/Info-Logs für: fehlgeschlagenen Poll, Power-only-Poll, fehlenden kumulativen Zähler im aktuellen Poll, verworfene veraltete Live-Werte

5 neue Regressionstests in `tests/test_home_charging_meter_fixes.py::TestLiveStateNeverStale`, getrieben über die echte `EvccMeterProvider`/`maybe_record_poll_snapshot`/`ChargingStateMachine`-Kette (nur die HTTP-Grenze wird gemockt).

Für bereits fehlerhaft gespeicherte Legacy-Sessions (`meter_old=1`, `meter_new` leer) gibt es **keine automatische Migration** — siehe `docs/maintenance-fix-legacy-evcc-session.md` für eine manuell auszuführende, gezielte Bereinigung einzelner Session-IDs.

## v2.4.0 — 2026-08-04

### Zuhause-Laden-Logik vollständig überarbeitet

Root-Cause-Fix für den "Zähler Alt = 1 kWh / Zähler Neu leer"-Fehler samt umfassender Absicherung der gesamten Home-Charging-Kette (EVCC-Zählerwerte, Live-State, Session-Start/-Ende, Tracker-/App-Neustart):

**1. EVCC-Provider verwechselte Gesamtzähler und Session-Energie**
- `chargeTotalImport` (kumulativer Gesamtzähler) und `chargedEnergy` (Energie der aktuellen/letzten Session) wurden bisher mit `or` verknüpft — dadurch landete `chargedEnergy` (z.B. 1000 Wh → "1") fälschlich als Zählerstand in `meter_old`/`meter_new`, und ein echter Zählerwert von `0` wurde als "fehlend" behandelt
- `chargeTotalImport` wird jetzt ausschließlich per Existenzprüfung ausgewertet, nie über Truthiness
- Fehlt `chargeTotalImport`, bleibt der kumulative Wert `None` — `chargedEnergy` wird optional als reine Session-Info mitgeliefert, niemals als Zählerstand
- Ein Ergebnis mit gültiger Leistung aber ohne Gesamtzähler gilt als erfolgreicher Power-only-Read (kein Fehler)

**2. Live-Leistung wurde bei unverändertem Zähler nicht aktualisiert**
- `maybe_record_poll_snapshot()` aktualisierte den Live-State (`meter_snap_last_power`) nur, wenn auch ein DB-Snapshot geschrieben wurde — bei unverändertem Zähler + Ladeende (0 kW) blieb der alte Leistungswert stehen und die Stop-Erkennung griff nicht
- Live-State und DB-Schreib-Deduplizierung sind jetzt getrennt: die Leistung/der Zähler im Laufzeit-State wird bei jedem erfolgreichen Poll aktualisiert, unabhängig davon ob ein neuer Snapshot gespeichert wird
- Ein fehlgeschlagener Poll verwirft sofort den zuletzt bekannten Leistungswert, damit kein veralteter Ladezustand endlos an die ChargingStateMachine weitergegeben wird

**3. Offene normale Sessions gingen bei einem Tracker-/App-Neustart verloren**
- Der Tracker sucht beim Start jetzt nach einer bereits offenen Session (`end_ts IS NULL`) für das Fahrzeug und nimmt sie wieder auf (Startzähler, SOC, Odometer, Standort, Meter-Home-Detection-State)
- Keine zweite Session mehr bei laufendem Ladevorgang nach Neustart; mehrere offene Sessions führen zu einer klaren Warnung, ältere Duplikate werden nicht automatisch gelöscht
- Der in der DB gespeicherte Startzähler ist beim Abschluss Source of Truth, nicht ausschließlich die lokale Laufzeitvariable

**4. Zähler-Endwert wurde bei zwischenzeitlich unbekanntem Standort übersprungen** (Fix aus 2.3.1, jetzt zusätzlich testabgesichert)

**5. Aktive `wallbox_sessions` existierten nur im RAM**
- Findet der Tracker nach einem Neustart keine aktive Wallbox-Session im Laufzeit-State, wird eine in der DB aktive Session derselben Quelle automatisch rehydriert — keine doppelten Wallbox-Sessions mehr, Stop-Debounce beginnt sauber neu und schließt dieselbe Session

**6. Energy-only-Erkennung öffnete beim ersten Messwert eine Phantom-Session**
- Der erste Messwert setzt nur noch eine Baseline; eine Session öffnet erst bei einem tatsächlichen, plausiblen Zähleranstieg, mit dem Wert vor Beginn des Anstiegs als Startwert
- Ein fallender/zurückgesetzter Zähler erzeugt keine negative Energie und keine Phantom-Session mehr

21 neue Regressionstests in `tests/test_home_charging_meter_fixes.py`.

## v2.3.1 — 2026-08-04

### Fix: Zählerstand-Ende fehlt bei Zuhause-Ladevorgängen

- Bei Ladevorgängen zu Hause wurde `meter_new` (Zählerstand Ende) manchmal nicht gespeichert, obwohl `meter_old` (Zählerstand Start) korrekt gesetzt war
- Ursache: Die Standortprüfung am Session-Ende (`meter_scope=home_only`) wurde live neu ermittelt und konnte genau im Moment des Ladeendes kurzzeitig `unknown` liefern (z. B. GPS-/Provider-Daten hinken dem Ladeende-Event hinterher), wodurch das Auslesen des Zählerstands übersprungen wurde
- Fix: Wenn das Live-Standortsignal beim Session-Ende `unknown` ist, wird jetzt auf den bereits während der Session bestätigten Standort zurückgefallen (z. B. durch Zähler-Delta-Erkennung), statt den Zählerstand-Endwert zu verwerfen

## v2.3.0 — 2026-06-23

### Zentraler Bearbeiten-Dialog für Ladevorgänge

- Zentraler ✎ Bearbeiten-Button ersetzt separate Preis- und Standort-Buttons in der Ladeliste
- Bearbeiten-Dialog: Standort, Ladetyp (AC/DC/Unbekannt), KM-Stand Start/Ende, Preis/kWh, Gesamtkosten, kWh, max. Leistung, Notiz — alle über `PATCH /api/sessions/<id>`
- Manuelles Setzen von AC/DC setzt `charger_type_source=manual` und `charger_type_confidence=100`
- Manuelle Kosten (`cost_manual=1`) werden bei Ladetyp-Änderung nicht überschrieben
- Validierung: Ladetyp muss `ac`/`dc`/`unknown` sein, KM-Stand ≥ 0, KM-Ende ≥ KM-Start
- Missing-Charge-Erkennung: Energy-Balance erkennt jetzt auch Ladevorgänge bei steigendem SOC mit großer Fahrtstrecke
- Snapshots ohne SOC-Wert werden bei der Baseline-Ermittlung übersprungen

## v2.2.0 — 2026-06-12

### Provider-Review, AC/DC-Schätzung & Dokumentation

**AC/DC-Ladertyp automatisch schätzen**
- Wenn die Hersteller-API keinen Ladetyp liefert, schätzt EV Tracker ihn aus verfügbaren Daten:
  Priorität: API → Zuhause/Zähler bestätigt → Ladeleistung → geschätzte Durchschnittsleistung → Unbekannt
- Schätzungen werden mit `~` in der Ladevorgangsliste markiert
- Quelle und Konfidenz werden in der DB gespeichert (`charger_type_source`, `charger_type_confidence`)
- Manuelle Korrekturen überschreiben Schätzungen und werden als `source=manual` gespeichert

**Provider-Review**
- **VW WeConnect**: ab Juni 2026 gesperrt — klare Fehlermeldung mit Empfehlung auf Home Assistant wechseln
- **Audi**: alte `msg.volkswagen.de`-Endpunkte seit 2023 abgeschaltet — als fragil markiert
- Alle Provider haben jetzt korrekte `stability_level`- und `official_api`-Flags
- Graceful Imports: eine fehlende Bibliothek bei einem Provider blockiert nicht mehr den App-Start
- Stabilitätsbadge (Stabil / Mittel / Fragil) in der Provider-Auswahl der Fahrzeugkonfiguration
- `type=info` Config-Felder werden als gestylte Warning-Box gerendert

**Dokumentation**
- `docs/quickstart.md`: 5-Schritte-Kurzanleitung (Docker → Benutzer → Fahrzeug → Template → E-Mail)
- `docs/anleitung.md`: vollständige Referenz aller Funktionen
- `docs/provider-status.md`: Provider-Statustabelle mit bekannten Einschränkungen und HA-Fallback-Links
- Hilfe-Links unter Konfiguration → Version & Update
- README neu strukturiert mit Fokus auf Excel-Ladeabrechnung

---

## v2.1.0 — 2026-06-07

### Erster Stable-Release: Heimladung, Fahrzeugprofil & Docker-Hardening

Fasst alle Entwicklungen seit v2.0.35 zusammen und markiert den ersten stabilen Hauptrelease.
DB-Migrationen laufen automatisch beim Container-Start; kein manueller Eingriff nötig.

**Automatische Heimladungs-Erkennung**
- Neue Tabellen `wallbox_sessions` und `charge_evidence` — Zähler-/Wallbox-Ladevorgänge werden separat erfasst und mit Fahrzeug-API-Sessions abgeglichen
- `reportable_session_where_clause()` — einheitlicher Report-Filter auf alle Query-Stellen angewendet (Sessions-Liste, Monatsstatistiken, Excel-Export, API v1)
- `reconciliation_service.py` verhindert Doppel-Sessions, wenn Wallbox und Fahrzeug-API denselben Ladevorgang melden (±3 kWh Toleranz, Zeitüberschneidung)
- Dashboard-Kachel „Offene Heimladungen" mit Zuordnen/Fremdfahrzeug/Ignorieren-Aktionen
- API-Routen `/api/wallbox/sessions/*`: offene Sessions anzeigen, Fahrzeug zuordnen, Fremdfahrzeug markieren, ignorieren

**go-e RFID/Ladekarten-Zuordnung**
- `goe_rfid_service.py` liest `cae`-Array (0,1-Wh-Einheiten) und erkennt, welche Karte geladen hat
- `goe_card_vehicle_map` ordnet Karten-Slots Fahrzeug-IDs zu
- Genau eine gemappte Karte → `confirmed`; Konflikt (mehrere Karten) → `unassigned`
- RFID-Snapshots in `_resolve_vehicle` integriert (Session-Start/-Ende)

**Schnelleingabe externe Ladung**
- `POST /api/sessions/quick-add-external` mit Smart-Defaults (letztes Fahrzeug, letzter Vertrag, geschätztes Ende)
- `import_service.py`: `make_dedup_key()` und `BaseImportProfile` ABC als Basis für künftige Ladekarten-Importe

**Fehlende Ladevorgänge erkennen**
- Snapshot-Vergleich nach Provider-Poll erkennt SOC-Anstiege während Offline-Phasen
- Kandidaten mit Konfidenz-Score (50–95 %) und Vorausfüll-Daten; akzeptieren, ablehnen oder dauerhaft ignorieren
- Neue Tabellen `vehicle_snapshots` und `missing_charge_candidates`

**Fahrzeugprofil-UI**
- Fahrzeugkonfiguration in dediziertem Profil mit Tabs (Verbindung, Heimladung, Verlauf, Zähler)
- `_vprofilFetchProviderFields`-Helper: Provider-Felder werden zuverlässig mit gespeicherten Werten befüllt; sichtbare Fehlermeldung statt stiller Fehler
- Secret-Felder (Token, Passwort) werden maskiert angezeigt; leere oder maskierte Werte (`********`) überschreiben gespeicherte Secrets nicht
- `vehicle_image_entity` und `ha_connected_means_charging` zu `VEHICLE_SPECIFIC_KEYS` hinzugefügt

**Provider-Datenverfügbarkeit**
- `VehicleState` um `data_timestamp`, `data_age_seconds`, `data_stale`, `stale_reason` erweitert
- HA-Provider wertet `last_changed`/`last_updated` aus; `provider_stale_after_minutes` konfigurierbar

**Update-Check**
- `GET /api/update-info` liefert Remote-Versionsabgleich (6 h Cache), Grund-Code und Release-Notizen — kein In-App-Update, kein Docker-Socket-Mount

**Docker & Deployment**
- PUID/PGID-User-Mapping: Container läuft als konfigurierbarer User (Unraid: `PUID=99 PGID=100`)
- `/api/health` mit `db_writable`, `startup_error`, `data_ok` — vollständige Diagnose ohne Login
- Cache-Busting für JS/CSS über `ASSET_VERSION`-Suffix
- Mobile-UI: alle Buttons und Schnellaktionen vollständig funktionstüchtig
- GitHub Actions: `VERSION` aus Git-Tag bei Prod-Releases (`v*`-Tag → `latest`/`stable`/`vX.Y.Z`)

**Security**
- PBKDF2:SHA-256 Passwort-Hashing; Legacy-SHA-256-Hashes werden beim Login transparent upgradet
- Fahrzeug-Credentials (Tokens, Passwörter) in API-Antworten maskiert (`********`)
- `EV_TRACKER_EXPOSURE=external`: ProxyFix, Secure-Cookies, HSTS, `X-Frame-Options: DENY`
- XSS-Fixes im Audit-Log, Session-Modal, Fahrzeug-Detail und Ladeabo-Liste

**Fixes**
- `allow_probable`-Bug: `default_vehicle_always` + `allow_probable=False` → `unassigned` (vorher fälschlicherweise `probable`)
- Status-Polling mit In-Flight-Guard und AbortController-Timeout (15 s)
- `normalizeLocation()` erkennt `intern`/`internal`/`zuhause_laden`/`öffentlich` korrekt

---

## v2.0.51 — 2026-06-02

### Wallbox-/Zähler-Heimladung (PRs 1–8)

Vollständige Umsetzung der automatischen Heimladungs-Erkennung über Wallbox oder Energiezähler, mit sicherer Fahrzeugzuordnung und filtertem Report-Export.

**Datenbasis (PR 1)**
- Neue Tabelle `wallbox_sessions` — technische Ladevorgänge aus Zähler/Wallbox
- Neue Tabelle `charge_evidence` — verknüpft Wallbox-, API- und Zähler-Signale
- `meter_snapshots` um `source_type`, `source_name`, `power_kw`, `energy_total_kwh`, `raw_json` erweitert (additiv, Backfill alter Zeilen)
- `sessions` um `source_primary`, `evidence_json`, `vehicle_assignment_status`, `excluded_from_reports` erweitert

**Zentraler Report-Filter (PR 2)**
- `reportable_session_where_clause()` — einheitlicher SQL-Guard mit COALESCE für rückwärtskompatible alte Zeilen
- Angewendet auf alle 7 Query-Stellen: Sessions-Liste, Monatsstatistiken, Excel-Export, API v1
- Neue Blueprint-Routen `/api/wallbox/sessions/*`: offene Sessions anzeigen, Fahrzeug zuordnen, Fremdfahrzeug markieren, ignorieren

**Deduplizierung (PR 3)**
- `reconciliation_service.py`: verhindert Doppel-Sessions wenn Wallbox und Fahrzeug-API denselben Ladevorgang erkennen (±3 kWh Toleranz, Zeitüberschneidung)

**Provider-Datenverfügbarkeit (PR 4)**
- `VehicleState` um `data_timestamp`, `data_age_seconds`, `data_stale`, `stale_reason` erweitert
- HA-Provider wertet `last_changed`/`last_updated` aus; `provider_stale_after_minutes` konfigurierbar
- `/api/status` liefert Staleness-Felder; Dashboard-Status zeigt `data_stale`

**go-e RFID/Ladekarten (PR 5)**
- `goe_rfid_service.py`: liest `cae`-Array (0,1-Wh-Einheiten), erkennt welche Karte geladen hat
- `goe_card_vehicle_map` ordnet Karten-Slots Fahrzeug-IDs zu
- Konflikt (mehrere Karten steigen) → `unassigned`; genau eine → `confirmed`

**Schnelleingabe externe Ladung (PR 6)**
- `POST /api/sessions/quick-add-external` mit Smart-Defaults (letztes Fahrzeug, letzter Vertrag, geschätztes Ende)
- `import_service.py`: `make_dedup_key()` + `BaseImportProfile` ABC für künftige Ladekarten-Importe

**Wallbox-/Zähler-Config-UI (PR 7)**
- Konfigurations-Abschnitt „Automatische Heimladung" in Konfiguration → Zähler & Wallbox
- Dashboard-Kachel „Offene Heimladungen" mit Zuordnen/Fremdfahrzeug/Ignorieren-Buttons

**Detection im Poll-Loop verdrahtet (PR 8)**
- `MeterResult.power_kw` (optional) — go-e und evcc liefern jetzt Momentanleistung
- `process_power_snapshot` / `process_energy_snapshot` werden in jedem Poll-Durchlauf aufgerufen (eigenständiger try/except, bricht Loop nie ab)
- go-e RFID in `_resolve_vehicle` integriert: Karten-Snapshots bei Session-Start/-Ende; `confirmed` wenn genau eine gemappte Karte steigt
- `allow_probable`-Bug behoben: `default_vehicle_always` + `allow_probable=False` → `unassigned` (vorher fälschlicherweise `probable`)

## v2.0.43 — 2026-05-25

### Update-Check, Notification-UI, Permissions, Session-Validierung

**Update-Check (Punkt 1)**
- `update-info.json` auf aktuelle Version 2.0.42 aktualisiert
- `GET /api/update-info?force=1` — erzwingt Neuladen, ignoriert Server-Cache
- Neue Felder: `reason`, `cache_hit`, `remote_url`, `warning`
- `reason`-Werte: `update_available` · `current_is_latest` · `remote_metadata_older_than_current` · `remote_unreachable` · `invalid_remote_json` · `version_compare_failed` · `update_check_disabled`
- Wenn Remote älter als installierte Version: `warning` + `reason=remote_metadata_older_than_current`
- `Cache-Control: no-store` auf `/api/update-info`
- UI: Button „Jetzt prüfen" deaktiviert sich während Prüfung, zeigt „⏳ Prüfe…"
- UI: zeigt spezifische Hinweise je nach reason (veraltet, nicht erreichbar, aktuell)

**Notification-Bell (Punkt 2)**
- Permanente 🔔-Glocke aus Navigation entfernt
- Stattdessen: `#notifTopHint` fixed oben rechts, nur sichtbar wenn unread_count > 0
- `updateNotifBadge(count)` zeigt/verbirgt den Hinweis
- `pollNotifBadge()` pollt nur wenn `hasPermission('notifications:view')` — kein 403-Spam

**Notification Event-Namen (Punkt 3)**
- `notification_event_update_available_new` → `notification_event_update_available`
- `notification_event_backup_failed_alert` → `notification_event_backup_failed`
- `notification_event_backup_success_alert` → `notification_event_backup_success`

**Docker/Unraid (Punkt 4)**
- `docker-entrypoint.sh`: nach chown in Mode B prüft `gosu PUID test -w $DATA_DIR` — exit 1 mit klarer Meldung wenn nicht schreibbar
- Mode A: prüft ebenfalls Schreibbarkeit vor Start

**Health (Punkt 5)**
- `/api/health`: `overall_ok` jetzt `db_ok AND data_ok AND db_writable AND startup_error IS NULL`
- Bei `db_writable=false`: HTTP 503 + `message: "/data oder Datenbank ist nicht beschreibbar."`

**Signatur-Permissions (Punkt 6)**
- `GET /api/signature` → `signature:view`
- `GET /api/signature/image` → `signature:view`
- `POST /api/signature/upload` → `signature:upload`
- `POST /api/signature/draw` → `signature:draw`
- `DELETE /api/signature` → `signature:delete`

**Session-Permissions (Punkt 7)**
- `GET /api/stats/monthly` → `sessions:view` oder `analytics:view`

**PATCH /api/sessions/<id> Validierung (Punkt 8)**
- 404 wenn Session nicht existiert
- `kwh_charged`, `cost_eur`, `price_per_kwh` ≥ 0
- `soc_start`, `soc_end` zwischen 0 und 100
- `odo_end` ≥ `odo_start`
- `meter_new` ≥ `meter_old`; `meter_delta_kwh` automatisch neu berechnet
- `end_ts` > `start_ts`
- `api_update_cost`: 404, negative Kosten → 400, Typ-Fehler → 400

## v2.0.42 — 2026-05-25

### Unraid-kompatibles User-Mapping (PUID / PGID)

- **`docker-compose.yml`**: `user: "${PUID:-10001}:${PGID:-100}"` — Container startet direkt als PUID:PGID ohne Root-Umweg; `cap_drop: ALL` bleibt aktiv
- **`docker-entrypoint.sh`**: Erkennt automatisch ob er als Root oder Non-Root startet
  - Non-Root (via `user:` in compose, empfohlen): exec gunicorn direkt, kein gosu nötig
  - Root (ohne `user:`, Legacy): chown /data auf PUID:PGID, dann gosu-Drop
- **`.env.example`**: Neu — dokumentiert PUID/PGID-Defaults; Unraid-Empfehlung `PUID=99 PGID=100`
- **README**: Neuer Abschnitt „Benutzer & Berechtigungen" mit Unraid-Setup, `.env`-Anleitung und Berechtigungs-Fixes
- Non-Root-Security-Verbesserung bleibt erhalten; Unraid-User müssen nur `.env` mit `PUID=99` anlegen

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
