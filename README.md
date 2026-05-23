# EV Tracker — Ladeprotokoll für Elektrofahrzeuge

Automatisches Ladeprotokoll für Elektrofahrzeuge via direkter Hersteller-API oder Home Assistant. Läuft als Docker Container auf Unraid oder jedem anderen Docker-Host.

![Docker Hub](https://img.shields.io/docker/pulls/19121412/ev-tracker)
![GitHub Actions](https://github.com/fdreckmann/ev_tracker/actions/workflows/docker-build.yml/badge.svg)
![Version](https://img.shields.io/badge/version-2.0.33-blue)

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
| 🏠 Standort | Unterscheidet Zuhause / Extern — GPS + Home Assistant Entities + Geofence + Zähler-Fallback |
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

**Zähler-basierte Heimerkennung:** Wenn Standort unbekannt ist und der lokale Zähler während des Ladens steigt, erkennt EV Tracker die Session automatisch als Zuhause. Schwellwert (Standard: 0,2 kWh / 10 Minuten) und Ratengrenze konfigurierbar.

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
| 🔀 Kombiniert | Provider-GPS + HA kombinierbar (any/all/provider_only/ha_only/manual) |
| 📊 Zähler-Fallback | Steigender Wallbox-Zähler bei unbekanntem Standort → automatisch Zuhause |
| 📜 Historie | Standort-Verlauf mit Zeitstempel (lat/lon nur mit `vehicles:location_exact_view`) |
| 🏷 Standortquelle | Jede Session speichert die Erkennungsquelle: provider / ha / gps / meter_delta / manual |

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
| Backend | Python 3.12 + Flask (modular via Blueprints) |
| Datenbank | SQLite (WAL-Modus, 21+ Indexe) |
| Frontend | Vanilla JS + Chart.js (responsive, PWA-fähig) |
| Excel | openpyxl |
| PDF | reportlab |
| Authentifizierung | Flask-Session, pyotp (TOTP), py_webauthn (FIDO2), Authlib (OAuth) |
| Fahrzeug-APIs | bimmer-connected, teslaPy, myrenaultapi, bluelinky u.v.m. |
| Tarif-APIs | Tibber GraphQL, Octopus Energy REST, ENTSO-E, Home Assistant, EVCC |
| CI/CD | GitHub Actions → Docker Hub |
| Hosting | Docker (Unraid, Synology, Proxmox, bare metal …) |
