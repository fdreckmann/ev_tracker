# EV Tracker — Ladeprotokoll für Elektrofahrzeuge

Automatisches Ladeprotokoll für Elektrofahrzeuge via direkter Hersteller-API oder Home Assistant. Läuft als Docker Container auf Unraid oder jedem anderen Docker-Host.

![Docker Hub](https://img.shields.io/docker/pulls/19121412/ev-tracker)
![GitHub Actions](https://github.com/fdreckmann/ev_tracker/actions/workflows/docker-build.yml/badge.svg)
![Version](https://img.shields.io/badge/version-2.0.37-blue)

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
| ✏️ Manuell erfassen | Ladevorgänge nachträglich manuell anlegen (Desktop + Mobile) — mit Standort, AC/DC, kWh oder Zählerständen, SOC, KM-Stand, Kosten, Notiz, Grund |
| 🏠 Standort | Unterscheidet Zuhause / Extern — GPS + Home Assistant Entities + Geofence + Zähler-Fallback |
| 🔌 AC / DC | Ladertyp-Erkennung via Leistungssensor oder HA Sensor |
| 💰 Preismodell | Heimtarif fix · dynamisch via Tibber/Octopus/HA/EVCC · Extern via ENTSO-E Spotpreis |
| ✎ Manuelle Korrektur | Kosten, Standort, kWh, SOC, KM-Stand und alle weiteren Felder pro Session bearbeitbar |
| 📊 Dashboard | Live-Status, Charts, Ladekurve, kontextsensitive Ladeinfo |
| 📱 Mobile App | PWA-fähig, Bottom-Navigation, Cards, Bottom Sheets, installierbar |
| 🔔 Push | Benachrichtigungen via Home Assistant notify, ntfy, Gotify |
| 💾 Backup | Manuell + automatisch per Cron-Zeitplan |
| ⬆ Update-Check | Verfügbare Updates werden angezeigt; Update via Docker-Pull (kein In-App-Update) |
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
| 🔄 TTL-Cache | Standortabfrage intern gecacht (30 s) — verhindert HA-Stampede bei parallelen JS-Fetches |

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

Das Unraid Community Apps Template wird in einem separaten Repository gepflegt:

➡ **https://github.com/fdreckmann/ev-tracker-unraid-app**

Dort findest du das aktuelle CA-Template, Installationsanleitung und Icons.

### Kurzanleitung

1. Community Apps → Suche nach **ev-tracker** → **Install**
2. Daten-Verzeichnis anpassen (Standard: `/mnt/user/appdata/ev-tracker`)
3. Zeitzone setzen (Standard: `Europe/Berlin`)
4. **Apply** → Container startet automatisch

### Web UI öffnen

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
  -e DATA_DIR=/data \
  -e TZ=Europe/Berlin \
  19121412/ev-tracker:latest
```

> **Sicherheitshinweis:** Der Docker Socket (`/var/run/docker.sock`) wird **nicht** benötigt und darf nicht gemountet werden. Updates erfolgen ausschließlich über Docker Compose, Unraid, Portainer oder Watchtower — nicht über die Web-UI.

---

## Benutzer & Berechtigungen (PUID / PGID)

Der Container läuft **nicht als Root**. Der effektive User wird über `PUID` und `PGID` gesteuert — das `/data`-Volume muss demselben User gehören.

### Werte setzen (empfohlen: `.env`-Datei)

```bash
cp .env.example .env
# .env anpassen:
PUID=10001   # Standard
PGID=100
```

```yaml
# docker-compose.yml — liest automatisch aus .env:
user: "${PUID:-10001}:${PGID:-100}"
```

### Für Unraid (nobody:users = 99:100)

Unraid verwaltet Appdata standardmäßig als `nobody:users` (UID 99, GID 100). `.env` anpassen:

```
PUID=99
PGID=100
```

Oder direkt in `docker-compose.yml`:

```yaml
user: "99:100"
```

### /data-Berechtigungen anpassen (falls nötig)

Der Eigentümer des `/data`-Verzeichnisses muss mit PUID:PGID übereinstimmen.

**Unraid (Appdata-Pfad):**
```bash
chown -R 99:100 /mnt/user/appdata/ev-tracker
chmod -R u+rwX,g+rwX /mnt/user/appdata/ev-tracker
```

**Standard (UID 10001):**
```bash
chown -R 10001:100 /mnt/user/appdata/ev-tracker
```

**Docker named volume:**
```bash
docker run --rm -v ev-tracker_data:/data alpine chown -R 10001:100 /data
```

---

## Fehlerbehebung

### "First User Setup" erscheint obwohl Admin bereits existiert

Ursache: Der Container kann `/data` nicht lesen oder schreiben — der Eigentümer stimmt nicht mit PUID:PGID überein.

**Diagnose:** `/api/health` aufrufen — zeigt `db_writable`, `users_table_exists`, `users_count` und `startup_error` ohne Login.

Lösung: PUID/PGID korrekt setzen (siehe oben) und `/data`-Berechtigungen anpassen.

### "attempt to write a readonly database" / "Permission denied"

Gleiche Ursache wie oben. Die App zeigt eine Fehlerseite mit dem konkreten Fix-Hinweis statt dem Setup-Formular.

---

## Updates

Updates erfolgen **ausschließlich über den Container-Daemon** — kein Docker Socket, kein In-App-Update.

### Docker Compose
```bash
docker compose pull
docker compose up -d
docker image prune -f
```

### Unraid
Container über die Unraid Docker-GUI aktualisieren (Update-Button neben dem Container).

### Portainer / Watchtower
Image auf `latest` aktualisieren oder Watchtower für automatische Updates konfigurieren.

Die Web-UI zeigt unter **Konfiguration → Version & Update** ob eine neue Version verfügbar ist und was sich geändert hat — installiert wird dabei nichts.

### Image-Tags

| Tag | Beschreibung |
|-----|-------------|
| `latest` | Stabile Releases (nur bei Git-Tag `v*`) |
| `beta` | Main-Branch (aktueller Entwicklungsstand) |
| `nightly` | Automatischer Build (täglich 02:00 UTC) |
| `dev` | Entwicklungsversion |

---

## Reverse Proxy / External Mode

Für den Betrieb hinter einem Reverse Proxy (nginx, Traefik, Caddy) die Umgebungsvariable setzen:

```yaml
environment:
  EV_TRACKER_EXPOSURE: "external"
```

**Was `external` aktiviert:**
- `ProxyFix` — liest `X-Forwarded-Proto`, `X-Forwarded-For`, `Host` korrekt aus
- Session-Cookies mit `Secure`, `HttpOnly`, `SameSite=Lax`
- `Strict-Transport-Security` (HSTS)
- `X-Frame-Options: DENY`
- `Referrer-Policy: no-referrer`

**Reverse-Proxy muss setzen:**
```
X-Forwarded-Proto: https
X-Forwarded-For: <client-ip>
Host: <your-domain>
```

**Intern (kein Reverse Proxy):**
```yaml
environment:
  EV_TRACKER_EXPOSURE: "internal"   # default
```
Kein Secure-Cookie-Zwang, kein HSTS. Direkter HTTP-Zugriff im lokalen Netz.

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

---

## Changelog

Alle Versionen und Änderungen: **[CHANGELOG.md](CHANGELOG.md)**
