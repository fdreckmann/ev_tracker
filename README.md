# EV Tracker — Ladeprotokoll für Elektrofahrzeuge

Automatisches Ladeprotokoll für Elektrofahrzeuge via direkter Hersteller-API oder Home Assistant. Läuft als Docker Container auf Unraid oder jedem anderen Docker-Host.

![Docker Hub](https://img.shields.io/docker/pulls/19121412/ev-tracker)
![GitHub Actions](https://github.com/fdreckmann/ev_tracker/actions/workflows/docker-build.yml/badge.svg)
![Version](https://img.shields.io/badge/version-2.0.19-blue)

---

## Unterstützte Fahrzeuge / Provider

| Provider | Laden | SOC | KM | Leistung | Standort | AC/DC |
|----------|-------|-----|----|----------|----------|-------|
| 🏠 Home Assistant | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |
| 🚗 VW / Skoda / Seat / Cupra | ✅ | ✅ | ✅ | ✅ | ✅ | ⚠ |
| 🔵 Audi (MyAudi Connect) | ✅ | ✅ | ✅ | ✅ | ✅ | ⚠ |
| ⚡ Tesla (TeslaPy) | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |
| 🔵 Volvo Cars API | ✅ | ✅ | ❌ | ❌ | ❌ | ❌ |
| 🔷 BMW / Mini (bimmer-connected) | ✅ | ✅ | ✅ | ❌ | ✅ | ❌ |
| ⭐ Mercedes-Benz (offizielle API) | ✅ | ✅ | ✅ | ❌ | ❌ | ❌ |
| 🏎 Polestar (GraphQL) | ✅ | ✅ | ✅ | ❌ | ❌ | ❌ |
| 🐻 Hyundai / Kia (Bluelink / UVO) | ✅ | ✅ | ✅ | ❌ | ✅ | ❌ |
| 🌸 Renault / Dacia (My Renault) | ✅ | ✅ | ✅ | ❌ | ✅ | ❌ |
| 🦁 Stellantis (Peugeot/Opel/Citroën/DS/Fiat/Jeep) | ✅ | ✅ | ✅ | ❌ | ✅ | ❌ |
| 🔵 Ford (FordPass) | ✅ | ✅ | ✅ | ❌ | ✅ | ❌ |
| 🟢 MG / SAIC (iSMART) | ✅ | ✅ | ✅ | ❌ | ✅ | ❌ |
| 🔴 Toyota / Lexus (MyT) | ✅ | ✅ | ✅ | ❌ | ✅ | ❌ |
| 🔵 Nissan (Ariya / Leaf) | ✅ | ✅ | ✅ | ❌ | ✅ | ❌ |
| 🟡 Porsche Connect | ✅ | ✅ | ✅ | ❌ | ✅ | ❌ |
| 🟢 Jaguar / Land Rover (jlrpy) | ✅ | ✅ | ✅ | ❌ | ✅ | ❌ |
| 🌐 TRONITY Aggregator (90+ Marken) | ✅ | ✅ | ✅ | ⚠ | ✅ | ⚠ |
| 🌐 Enode Aggregator (50+ Marken) | ✅ | ✅ | ✅ | ⚠ | ✅ | ⚠ |
| 🌐 Smartcar Aggregator (30+ Marken) | ✅ | ✅ | ✅ | ❌ | ✅ | ❌ |

---

## Features

### Kernfunktionen

| Feature | Beschreibung |
|---------|-------------|
| ⚡ Auto-Erkennung | Ladevorgänge werden automatisch erkannt und gespeichert |
| 🏠 Standort | Unterscheidet Zuhause / Extern — GPS + Home Assistant Entities + Geofence |
| 🔌 AC / DC | Ladertyp-Erkennung via Leistungssensor oder HA Sensor |
| 💰 Preismodell | Heimtarif fix · dynamisch via Tibber/Octopus/HA/EVCC · Extern via ENTSO-E Spotpreis |
| ✎ Manuelle Korrektur | Kosten und Standort pro Session überschreibbar |
| 📊 Dashboard | Live-Status, Charts, Ladekurve, kontextsensitive Ladeinfo |
| 📱 Mobile App | PWA-fähig, Bottom-Navigation, Cards, Bottom Sheets, installierbar |
| 🔔 Push | Benachrichtigungen via Home Assistant notify, ntfy, Gotify |
| 💾 Backup | Manuell + automatisch per Cron-Zeitplan |
| ⬆ Auto-Update | Update direkt im Web UI — Latest, Nightly oder Dev Kanal |
| 👥 Multi-User | Mehrere Benutzer mit Rollen und granularen Berechtigungen |
| 🔐 Auth | E-Mail/Passwort, TOTP 2FA, Google/Microsoft OAuth, Passkeys (FIDO2) |
| 🚗 Mehrfahrzeuge | Beliebig viele Fahrzeuge parallel tracken |
| 🗃 Fahrzeug-Archiv | Soft-Delete (Archivieren) oder Hard-Delete mit Bestätigung |

### Excel Export & Templates

| Feature | Beschreibung |
|---------|-------------|
| 📋 Eingebauter Export | Fertige xlsx-Datei ohne Template-Konfiguration |
| 📁 Template-Upload | Eigene xlsx-Vorlage mit beliebigem Layout hochladen |
| 🗂 Template-Galerie | 4+ vorgefertigte Vorlagen (Standard, Arbeitgeber, Steuer, Minimal) |
| 🔧 Spalten-Mapping | Tabellenspalten den EV-Datenfeldern zuweisen (3-Modi-UI) |
| 🔢 Einzelzellen-Mapping | Einzelne Zellen mit Kopfdaten befüllen (Fahrer, Kennzeichen, Monat …) |
| ✍ Unterschrift | Bild hochladen oder im Browser zeichnen, frei positionierbar mit Ankerzelle |
| 🌍 Mehrsprachig | Export auf Deutsch oder Englisch (Monatsnamen, Labels, Standorte) |
| {{Platzhalter}} | 25+ Platzhalter in Templates: `{{month_year}}`, `{{total_kwh}}`, `{{meter_start_value}}` … |
| 👁 Vorschau | Echte XLSX-Vorschau mit befüllten Daten vor dem Download |
| 📄 PDF-Export | reportlab-basierter PDF-Report mit Kopfband, Zusammenfassung und Signaturfeld |
| 🔢 Zähleranzeige | meter_old/meter_new im Export als ganze kWh (exakter Rohwert intern) |

### Zählerstand-Integration

| Provider | Protokoll | Besonderheit |
|----------|-----------|-------------|
| Shelly (Gen1/Gen2/Pro/Plus) | HTTP RPC | Auto-Erkennung, EMData/EM1Data, Phasen A/B/C |
| Tasmota | HTTP | SML-Sensor, benutzerdefinierter JSON-Pfad, Basic Auth |
| go-e Charger | HTTP | |
| openWB | HTTP | Konfigurierbarer Ladepunkt-Index |
| WARP Charger | HTTP | |
| EVCC | HTTP | |
| Webasto | HTTP | |
| Alfen | HTTP | |
| Juice Charger | HTTP | |
| Generic HTTP | HTTP | Beliebige URL, JSON-Pfad, Einheit konfigurierbar |
| Home Assistant | REST | Beliebiger HA-Sensor (Wh/kWh/MWh auto-erkannt) |

**Zähler-Scope (`meter_scope`):** Der lokale Stromzähler kann auf Zuhause-Ladevorgänge beschränkt werden (`home_only`, Standard). Externe Ladevorgänge überspringen die Zählerablesung automatisch. Der Grund wird pro Session gespeichert (`meter_skipped_reason`).

### Stromtarif

| Quelle | Beschreibung |
|--------|-------------|
| Fester Preis | Separat für Zuhause, AC Extern, DC Extern |
| Tibber | Stündliche Spotpreise via GraphQL API |
| Octopus Energy | Halbstündliche Tarife (Agile u.a.) via REST API |
| Home Assistant | Beliebiger HA-Sensor als Preisquelle, inkl. History-API |
| EVCC | Netz-Tarif aus `/api/state` (tariffGrid/gridPrice) |
| Generic HTTP | Beliebige Preis-API mit JSON-Pfad |
| ENTSO-E | Spotpreise für externe Ladevorgänge |

Preise werden **zeitgewichtet** über den Ladezeitraum gemittelt. Bestehende Home-Sessions können im UI per Knopfdruck mit dem aktuellen Tarif neu berechnet werden.

### Abrechnung & Reports

| Feature | Beschreibung |
|---------|-------------|
| 📊 Auto-Berichte | Automatischer Monats-/Mehrmonats-Report per E-Mail |
| 📁 Report-Archiv | Reports erstellen, verwalten, herunterladen, versenden, genehmigen |
| 💼 Billing-Wizard | Schritt-für-Schritt Abrechnung: Fahrzeug, Zeitraum, Format, Signatur |
| 📧 E-Mail-Versand | SMTP-Konfiguration, HTML-E-Mails mit Übersichtstabelle |
| 📄 PDF-Export | Professioneller PDF-Report mit reportlab |
| 🔑 API-Tokens | SHA-256-gesicherte Tokens mit Scopes, einmalige Anzeige |
| 📡 MQTT | Home Assistant Auto-Discovery, ntfy, Gotify |
| 🔔 Regeln | DB-getriebene Benachrichtigungsregeln mit Ruhezeitfenstern |

### Fahrzeug-Standorterkennung

| Feature | Beschreibung |
|---------|-------------|
| 📍 GPS-Geofence | Haversine-Distanz zur Heimadresse, konfigurierbare Radius |
| 🏠 HA Entities | Home Assistant `device_tracker` Entities als Standortquelle |
| 🔀 Kombiniert | Provider-GPS + HA kombinierbar (any/all/provider_only/ha_only) |
| 📜 Historie | Standort-Verlauf mit Zeitstempel (lat/lon nur mit `vehicles:location_exact_view`) |

### Benutzerverwaltung & Sicherheit

| Feature | Beschreibung |
|---------|-------------|
| 👥 Multi-User | Beliebig viele Benutzer |
| 🔐 Passkeys | WebAuthn/FIDO2 — Fingerabdruck, Face ID, Hardware-Key |
| 🛡 2FA | TOTP + 10 Backup-Codes |
| 🔑 OAuth | Google & Microsoft SSO |
| 📧 Einladungen | Benutzer per E-Mail-Link einladen |
| 🔒 Rate-Limiting | Kontosperrung nach zu vielen Fehlversuchen |
| 📝 Audit-Log | Alle sicherheitsrelevanten Aktionen protokolliert |
| 🎭 Rollen | admin, user, readonly + eigene Rollen |
| ✅ Berechtigungen | 70+ granulare Permissions, pro Rolle konfigurierbar |
| 🛡 CSRF-Schutz | Alle POST/PUT/DELETE-Endpunkte geschützt |
| 🔒 Security Headers | X-Frame-Options, X-Content-Type-Options, Referrer-Policy |

---

## Berechtigungssystem (RBAC)

Flexibles rollenbasiertes Berechtigungssystem mit 70+ granularen Permissions.

**Standardrollen:**

| Rolle | Beschreibung |
|-------|-------------|
| `admin` | Vollzugriff (`admin:all`) |
| `user` | Normaler Benutzer — Export, Sessions, Fahrzeuge, Signatur |
| `readonly` | Nur-Lese-Zugriff auf Dashboard, Sessions, Export-Vorschau |

**Eigene Rollen:** Der Admin kann zusätzliche Rollen erstellen (z.B. Buchhaltung, Fuhrpark) und ihnen beliebige Berechtigungen zuweisen.

**Berechtigungsgruppen:** Dashboard · Fahrzeuge · Ladevorgänge · Analyse · Export · Templates · Signatur · Zählerstand · Provider · Tarife · Einstellungen · Benutzer · Backup · Updates · Audit · System · MQTT · Benachrichtigungen

---

## Installation auf Unraid

### Schritt 1 — CA Template einrichten

Im Unraid Terminal:

```bash
mkdir -p /boot/config/plugins/dockerMan/templates-user/
cat > /boot/config/plugins/dockerMan/templates-user/ev-tracker.xml << 'XMLEOF'
<?xml version="1.0"?>
<Container version="2">
  <Name>ev-tracker</Name>
  <Repository>19121412/ev-tracker:latest</Repository>
  <Registry>https://hub.docker.com/r/19121412/ev-tracker</Registry>
  <Network>bridge</Network>
  <Privileged>false</Privileged>
  <Overview>EV Ladeprotokoll für Elektrofahrzeuge via Home Assistant oder direkter Hersteller-API.</Overview>
  <Category>Tools:</Category>
  <WebUI>http://[IP]:[PORT:8054]/</WebUI>
  <Icon>https://raw.githubusercontent.com/home-assistant/brands/master/custom_integrations/volkswagen_we_connect_id/icon.png</Icon>
  <ExtraParams>--restart unless-stopped</ExtraParams>
  <Config Name="Web UI Port" Target="8080" Default="8054" Mode="tcp" Type="Port" Display="always" Required="true" Mask="false">8054</Config>
  <Config Name="Daten-Verzeichnis" Target="/data" Default="/mnt/user/appdata/ev-tracker" Mode="rw" Type="Path" Display="always" Required="true" Mask="false">/mnt/user/appdata/ev-tracker</Config>
  <Config Name="Docker Socket" Target="/var/run/docker.sock" Default="/var/run/docker.sock" Mode="rw" Type="Path" Display="always" Required="false" Mask="false">/var/run/docker.sock</Config>
  <Config Name="DATA_DIR" Target="DATA_DIR" Default="/data" Type="Variable" Display="advanced" Required="true" Mask="false">/data</Config>
  <Config Name="TZ" Target="TZ" Default="Europe/Berlin" Type="Variable" Display="always" Required="true" Mask="false">Europe/Berlin</Config>
</Container>
XMLEOF
```

### Schritt 2 — Container starten

Browser neu laden (F5) → Unraid → Docker → **"Add Container"** → Template **"ev-tracker"** → **Apply**

### Schritt 3 — Web UI öffnen

```
http://<unraid-ip>:8054
```

→ Einrichtungsassistent folgen → Provider wählen → Verbindung einrichten → Speichern

---

## Installation (normales Docker)

```bash
docker run -d --name ev-tracker \
  --restart unless-stopped \
  -p 8054:8080 \
  -v $(pwd)/data:/data \
  -v /var/run/docker.sock:/var/run/docker.sock \
  -e DATA_DIR=/data \
  -e TZ=Europe/Berlin \
  19121412/ev-tracker:latest
```

> Der Docker Socket `/var/run/docker.sock` ist optional — wird nur für den Auto-Update Button im Web UI benötigt.

---

## Update-Kanäle

| Kanal | Beschreibung | Wann |
|-------|-------------|------|
| 🟢 `latest` | Stabile Releases | Bei jedem Push auf `main` |
| 🌙 `nightly` | Automatischer Build | Täglich um 02:00 UTC |
| 🔧 `dev` | Entwicklungsversion | Manuell |

Update-Kanal wählbar im Web UI → **Backup Tab** → **Software Update**.

---

## Preismodell

```
🏠 Zuhause  → Fixer Heimtarif oder dynamisch (Tibber/Octopus/HA/EVCC)
🔌 Extern AC → ENTSO-E Spot × AC-Faktor (Fallback: Fixpreis)
⚡ Extern DC → ENTSO-E Spot × DC-Faktor (Fallback: Fixpreis)
✎ Manuell   → Jede Session einzeln korrigierbar
```

Dynamische Preise werden **zeitgewichtet** über den Ladezeitraum gemittelt (z.B. 30 min zu 0,25 € + 30 min zu 0,35 € = 0,30 €/kWh). Bei API-Fehler greift automatisch der konfigurierte Fallback-Preis.

### ENTSO-E API Key (optional, für externe Ladevorgänge)

1. Registrieren auf [transparency.entsoe.eu](https://transparency.entsoe.eu)
2. Email an `transparency@entsoe.eu` — Betreff: "Restful API access"
3. Key per Email erhalten (wenige Tage)
4. Im Web UI → Konfiguration → ENTSO-E eintragen

---

## Dateistruktur

```
/mnt/user/appdata/ev-tracker/
├── config.json          ← Konfiguration
├── sessions.db          ← SQLite Datenbank (Users, Sessions, Vehicles, Roles …)
├── template.xlsx        ← Eigene Excel-Vorlage (optional)
├── signature.png        ← Unterschrift für Export (optional)
├── exports/             ← Generierte Monatsberichte
└── backups/             ← Automatische Backups
```

---

## Changelog

| Version | Highlights |
|---------|-----------|
| **2.0.19** | Bugfix: `close_db_if_owned()` recursiver Selbstaufruf (alle DB-Verbindungen wurden nie geschlossen); SQL-Injection in Excel-Export behoben; XSS in Audit-Log, Benutzerverwaltung und Mobile-Ansicht; Open-Redirect via `?next=` abgesichert; 7× ungeschützte `int()`-Konvertierungen; 404 bei Update nicht-existenter Sessions/Regeln; UX-Fehlermeldungen bei delete/toggle |
| **2.0.18** | Mobile: `/api/mobile/summary` als Single-Call für Dashboard (1 statt 3 API-Requests); Fahrzeugbilder in Mobile-Cards; Standort-Historie (`vehicle_location_history`); CSRF-Fix für 12 weitere fetch()-Aufrufe |
| **2.0.17** | Performance: `flask.g`-scoped DB-Connection (`_get_db()`), `teardown_appcontext`-Cleanup; `re.compile()` auf Modul-Ebene; `threading.Lock` für `_vehicle_states` |
| **2.0.16** | `/api/state`-Alias für Mobile; Sensible Config-Felder maskiert; granulare Permissions für Updates/Users/Audit/Settings; `GET /api/mobile/summary` |
| **2.0.15** | HA + EVCC Tarifprovider, zeitgewichteter Preisdurchschnitt, `POST /api/tariffs/recalculate` für Neuberechnung bestehender Sessions |
| **2.0.14** | Dashboard: kontextsensitive Ladetyp-Kachel — bei aktiver Session "Lädt gerade ⚡ · Zuhause · AC · 11 kW", sonst "Letzte Ladung · vor X Std." |
| **2.0.13** | Zählerstände (meter_old/meter_new) überall als ganze kWh — UI, Excel, Template-Platzhalter; Berechnung intern weiterhin mit Rohwerten |
| **2.0.12** | `meter_scope`: lokaler Zähler nur für Zuhause-Ladevorgänge (`home_only` Standard); `meter_skipped_reason` + `meter_used` pro Session |
| **2.0.11** | Mobile Bottom-Sheet-System: openMobileSessionCreate, Meter-Test, Connection-Test, Signatur, System-Status, Update-Check, Backup, Fahrzeugdetails |
| **2.0.10** | Mobile PWA (installierbar), FAB, echte Session-Cards, Fahrzeuge-Tab, Skeleton Loader |
| **2.0.9** | Fahrzeug-Standorterkennung (GPS-Geofence + HA device_tracker, kombinierbare Modi), Standort-Historie, Berechtigungs-Härtung für Templates/Meter/Provider-Test |
| **2.0.8** | Fahrzeug-Archivierung (soft-delete) und Hard-Delete mit "LÖSCHEN"-Bestätigung; `GET /api/vehicles/<id>/delete-check` |
| **2.0.7** | Session-Bearbeitungs-Modal (kwh, Leistung, Preis, Kosten), PATCH /api/sessions/<id>, Fahrzeugbild-Berechtigungen |
| **2.0.6** | Security Headers, Backup Zip-Slip-Härtung, Fahrzeugbild-Routen mit Path-Traversal-Schutz |
| **2.0.5** | API v1/* mit Bearer-Token, dynamischer Tarifpreis pro Session, Wallbox-Leistungs-Konfiguration |
| **2.0.4** | CSRF-Schutz global, SQLite WAL-Modus + 21 Indexe, Backup-Restore Sicherheits-Backup |
| **2.0.3** | Billing-Wizard, Report-Archiv, PDF-Export (reportlab), dynamische Tarifprovider, API-Tokens, MQTT, Benachrichtigungsregeln |
| **2.0.2** | Multi-Monats-Berichte: ein Tabellenblatt pro Monat, Chip-Auswahl bis 24 Monate |
| **2.0.1** | Bugfix: init_db() Absturz beim ersten Start (row_factory fehlte) |
| **2.0.0** | Flexibles RBAC (70+ Permissions, eigene Rollen), vollständige Export-Lokalisierung (de/en), Export-Vorschau mit Download-Token |
| **1.9.9** | Passkey (WebAuthn/FIDO2) — Fingerabdruck, Face ID, Hardware-Key |
| **1.9.8** | Export-Lokalisierung, charge_power_kw, Vorschau-API, Unterschrift-Ankerzelle |
| **1.9.7** | 12 neue Fahrzeug-Provider: Stellantis, Ford, MG, Toyota, Nissan, Porsche, JLR + Aggregatoren |
| **1.9.6** | Modulares Zähler-Provider-System (12 Provider), Shelly Gen2 RPC, Generic HTTP Meter |
| **1.9.5** | Unterschriften-Funktion: Upload oder Canvas-Zeichnen, frei positionierbar |
| **1.9.4** | Template-Galerie mit 4 vorgefertigten Vorlagen |
| **1.9.3** | Automatische Template-Analyse mit Konfidenz-Score und Synonymerkennung |
| **1.9.2** | Docker-Update überlebt Container-Neustart zuverlässig (Helper-Container) |
| **1.9.1** | HTML-E-Mails, Admin-Dashboard, Audit-Log mit Benutzerspalte |
| **1.9.0** | Rate-Limiting, Passwort-Reset per E-Mail, 2FA Backup-Codes, CSRF-Schutz |
| **1.8.0** | Multi-User-Auth, OAuth (Google/Microsoft), TOTP 2FA, Einrichtungsassistent |
| **1.7.0** | Docker-Update über Web UI, Update-Kanal wählbar, Live-Log |
| **1.6.0** | Excel-Template-System mit Spalten- und Zell-Mapping, Backup-System |
| **1.5.0** | Mehrfahrzeug-Unterstützung, Polestar, Audi, Hyundai/Kia, Renault |
| **1.4.0** | Zählerstand-Integration (Shelly, Tasmota, go-e, openWB …), ENTSO-E Spotpreise |
| **1.3.0** | BMW/Mini, Mercedes, Volvo, Tesla, VW-Provider |
| **1.0.0** | Erstes Release — Home Assistant, Auto-Ladeprotokoll, Excel-Export |

---

## Entwicklung

```bash
git clone https://github.com/fdreckmann/ev_tracker.git
cd ev_tracker
pip install -r requirements.txt
python app/server.py

# oder via Docker:
docker build -t ev-tracker:latest .
docker run -d --name ev-tracker -p 8054:8080 \
  -v $(pwd)/data:/data -e DATA_DIR=/data -e TZ=Europe/Berlin \
  ev-tracker:latest
```

Änderungen pushen → GitHub Actions baut automatisch → Docker Hub.

---

## Technologie

| Bereich | Technologie |
|---------|-------------|
| Backend | Python 3.12 + Flask |
| Datenbank | SQLite (WAL-Modus, 21+ Indexe) |
| Frontend | Vanilla JS + Chart.js (responsive, PWA-fähig) |
| Excel | openpyxl |
| PDF | reportlab |
| Authentifizierung | Flask-Session, pyotp (TOTP), py_webauthn (FIDO2), Authlib (OAuth) |
| Fahrzeug-APIs | bimmer-connected, teslaPy, myrenaultapi, bluelinky u.v.m. |
| Tarif-APIs | Tibber GraphQL, Octopus Energy REST, ENTSO-E, Home Assistant, EVCC |
| CI/CD | GitHub Actions → Docker Hub |
| Hosting | Docker (Unraid, Synology, Proxmox, bare metal …) |
