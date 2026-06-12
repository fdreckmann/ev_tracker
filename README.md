# EV Tracker — Automatische Ladeabrechnung für Elektrofahrzeuge

**EV Tracker** erstellt automatisch Excel-Reports deiner Ladevorgänge — damit du deine Ladekosten monatlich als Abrechnung an deinen Arbeitgeber schicken kannst, ohne manuell etwas zusammentragen zu müssen.

Die App läuft als Docker Container auf Unraid, Synology, Proxmox oder jedem anderen Docker-Host. Sie verbindet sich direkt mit der Hersteller-API deines Fahrzeugs oder über Home Assistant und speichert jeden Ladevorgang mit Datum, Dauer, kWh, Kosten, Standort und AC/DC-Typ.

![Docker Hub](https://img.shields.io/docker/pulls/19121412/ev-tracker)
![GitHub Actions](https://github.com/fdreckmann/ev_tracker/actions/workflows/docker-build.yml/badge.svg)
![Version](https://img.shields.io/badge/version-2.1.0-blue)

---

## Hauptanwendungsfall

```
Fahrzeug lädt → EV Tracker erkennt den Ladevorgang automatisch
             → speichert kWh, Kosten, Standort, AC/DC, SOC, KM-Stand
             → erstellt monatlichen Excel-Report (eigenes Template oder vorgefertigt)
             → verschickt den Report automatisch per E-Mail
```

**Typisches Szenario:** Du lädst dein Dienst- oder Privatfahrzeug regelmäßig zuhause. Am Monatsende soll dein Arbeitgeber die Ladekosten erstatten. EV Tracker protokolliert alles automatisch und schickt den fertigen Excel-Bericht auf Knopfdruck oder automatisch am Monatsende an die Buchhaltung.

📖 **[Quickstart-Anleitung](docs/quickstart.md)** · **[Ausführliche Anleitung](docs/anleitung.md)**

---

## Unterstützte Fahrzeuge / Provider

| Provider | Laden | SOC | KM | Leistung | Standort | AC/DC |
|----------|-------|-----|----|----------|----------|-------|
| 🏠 Home Assistant | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |
| ⚠️ VW / Skoda / Seat / Cupra | ✅ | ✅ | ✅ | ✅ | ✅ | ⚠ |
| ⚠️ Audi (MyAudi Connect) | ✅ | ✅ | ✅ | ✅ | ✅ | ⚠ |
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
| 🔵 XPeng (via Enode/TRONITY) | ✅¹ | ✅¹ | ✅¹ | — | ✅¹ | — |
| 🟢 BYD (via Enode/TRONITY) | ✅¹ | ✅¹ | ✅¹ | — | ✅¹ | — |

> ⚠ = nur teilweise / abhängig vom Modell · ❌ = nicht verfügbar · ¹ = empfohlen über Aggregator (keine stabile Direkt-API)
>
> **⚠️ VW / Audi:** Die WeConnect-API und die alten MyAudi-Endpunkte sind seit 2024 stark eingeschränkt und funktionieren für viele Nutzer nicht mehr. **Empfehlung: Home Assistant Provider** mit der jeweiligen HACS-Integration verwenden. → [Provider-Status](docs/provider-status.md)

---

## Features

### Kernfunktionen

| Feature | Beschreibung |
|---------|-------------|
| ⚡ Auto-Erkennung | Ladevorgänge werden automatisch erkannt und gespeichert |
| 📊 Excel-Report | Monatlicher Ladebericht als XLSX — eigenes Template oder vorgefertigt |
| 📧 Auto-Versand | Excel-Report automatisch per E-Mail an Arbeitgeber oder Buchhaltung schicken |
| ✏️ Manuell erfassen | Ladevorgänge nachträglich manuell anlegen (Desktop + Mobile) — mit Standort, AC/DC, kWh oder Zählerständen, SOC, KM-Stand, Kosten, Notiz |
| 🔍 Fehlende Ladevorgänge | Automatische Erkennung verpasster Sessions via SOC-Delta-Analyse; Vorschlag mit Konfidenz-Score und Vorausfüll-Dialog |
| 🏠 Standort | Unterscheidet Zuhause / Extern — GPS + Home Assistant Entities + Geofence + Zähler-Fallback |
| 🔌 AC / DC | Ladertyp-Erkennung via Leistungssensor oder HA Sensor; automatische Schätzung wenn API keine Info liefert |
| 💰 Preismodell | Heimtarif fix · dynamisch via Tibber/Octopus/HA/EVCC · Extern via ENTSO-E oder EnBW Spotpreis |
| 📋 Ladeabos | Öffentliche Ladepreis-Verträge (ADAC, EnBW, Ionity etc.) als Preisquelle für Extern-Sessions |
| ✎ Manuelle Korrektur | Kosten, Standort, kWh, SOC, KM-Stand und alle weiteren Felder pro Session bearbeitbar |
| 📊 Dashboard | Live-Status, Charts, Ladekurve, kontextsensitive Ladeinfo |
| 📱 Mobile App | PWA-fähig, Bottom-Navigation, Cards, Bottom Sheets, Schnellaktionen, installierbar |
| 🖼 Fahrzeugbilder | Automatische Silhouette-Zuordnung nach Marke/Modell; manueller Bild-Upload hat immer Vorrang |
| 🔔 Push | Benachrichtigungen via Home Assistant notify, ntfy, Gotify, Telegram |
| 💾 Backup | Manuell + automatisch per Cron-Zeitplan |
| ⬆ Update-Check | Verfügbare Updates werden angezeigt; Update via Docker-Pull (kein In-App-Update) |
| 👥 Multi-User | Mehrere Benutzer mit Rollen und granularen Berechtigungen |
| 🔐 Auth | E-Mail/Passwort, TOTP 2FA, Google/Microsoft OAuth, Passkeys (FIDO2) |
| 🚗 Mehrfahrzeuge | Beliebig viele Fahrzeuge parallel tracken |

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
| 📅 Auto-Berichte | Automatische Reports: täglich, wöchentlich, monatlich, quartalsweise, jährlich, oder benutzerdefiniert |

### Zählerstand-Integration

| Provider | Protokoll | Besonderheit |
|----------|-----------|-------------|
| Shelly (Gen1/Gen2/Pro/Plus) | HTTP RPC | Auto-Erkennung, EMData/EM1Data, Phasen A/B/C |
| Tasmota | HTTP | SML-Sensor, benutzerdefinierter JSON-Pfad, Basic Auth |
| go-e Charger | HTTP | RFID/Karten-Mapping |
| openWB | HTTP | Konfigurierbarer Ladepunkt-Index |
| WARP Charger | HTTP | |
| EVCC | HTTP | |
| Webasto | HTTP | |
| Alfen | HTTP | |
| Juice Charger | HTTP | |
| Generic HTTP | HTTP | Beliebige URL, JSON-Pfad, Einheit konfigurierbar |
| Home Assistant | REST | Beliebiger HA-Sensor (Wh/kWh/MWh auto-erkannt) |

Der lokale Zähler kann auf Zuhause-Ladevorgänge beschränkt werden (`meter_scope = home_only`, Standard). Externe Ladevorgänge überspringen die Zählerablesung. Wenn Standort unbekannt ist und der lokale Zähler während des Ladens steigt, erkennt EV Tracker die Session automatisch als Zuhause.

### Stromtarif

| Quelle | Beschreibung |
|--------|-------------|
| Fester Preis | Separat für Zuhause, AC Extern, DC Extern |
| Tibber | Stündliche Spotpreise via GraphQL API |
| Octopus Energy | Halbstündliche Tarife (Agile u.a.) via REST API |
| Home Assistant | Beliebiger HA-Sensor als Preisquelle |
| EVCC | Netz-Tarif aus `/api/state` |
| Generic HTTP | Beliebige Preis-API mit JSON-Pfad |
| ENTSO-E | Spotpreise für externe Ladevorgänge |
| EnBW | Öffentliche Ladepreise via EnBW API |
| Ladeabos | Eigene Vertrags-Preismodelle (kWh, Minute, Session-Fee) |

Dynamische Preise werden zeitgewichtet über den Ladezeitraum gemittelt. Bestehende Home-Sessions können per Knopfdruck mit dem aktuellen Tarif neu berechnet werden.

### Abrechnung & Reports

| Feature | Beschreibung |
|---------|-------------|
| 📊 Auto-Berichte | Automatischer Monats-/Mehrmonats-Report per E-Mail |
| 📁 Report-Archiv | Reports erstellen, verwalten, herunterladen, versenden, genehmigen |
| 💼 Billing-Wizard | Schritt-für-Schritt Abrechnung: Fahrzeug, Zeitraum, Format, Signatur |
| 📧 E-Mail-Versand | SMTP (inkl. OAuth2 für Google & Microsoft 365), HTML-Tabelle + Excel-Anhang |
| 📄 PDF-Export | Professioneller PDF-Report mit reportlab |
| 🔑 API-Tokens | SHA-256-gesicherte Tokens mit Scopes, einmalige Anzeige |
| 📡 MQTT | Home Assistant Auto-Discovery, Fahrzeugstatus-Publish |
| 🔔 Regeln | Benachrichtigungsregeln mit Ruhezeitfenstern (ntfy, Gotify, Telegram, MQTT, E-Mail, Webhook) |

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

---

## Installation auf Unraid

Das Unraid Community Apps Template wird in einem separaten Repository gepflegt:

➡ **https://github.com/fdreckmann/ev-tracker-unraid-app**

### Kurzanleitung

1. Community Apps → Suche nach **ev-tracker** → **Install**
2. Daten-Verzeichnis anpassen (Standard: `/mnt/user/appdata/ev-tracker`)
3. Zeitzone setzen (Standard: `Europe/Berlin`)
4. **Apply** → Container startet automatisch

```
http://<unraid-ip>:8054
```

---

## Installation (Docker)

```bash
docker run -d --name ev-tracker \
  --restart unless-stopped \
  -p 8054:8080 \
  -v $(pwd)/data:/data \
  -e DATA_DIR=/data \
  -e TZ=Europe/Berlin \
  19121412/ev-tracker:latest
```

**Docker Compose:**

```yaml
services:
  ev-tracker:
    image: 19121412/ev-tracker:latest
    restart: unless-stopped
    ports:
      - "8054:8080"
    volumes:
      - ./data:/data
    environment:
      DATA_DIR: /data
      TZ: Europe/Berlin
```

> **Sicherheitshinweis:** Der Docker Socket (`/var/run/docker.sock`) wird **nicht** benötigt und darf nicht gemountet werden.

---

## Benutzer & Berechtigungen (PUID / PGID)

Der Container läuft nicht als Root. Der effektive User wird über `PUID` und `PGID` gesteuert — das `/data`-Volume muss demselben User gehören (Standard: `10001:100`).

```yaml
environment:
  PUID: "10001"   # Standard
  PGID: "100"
```

Für Unraid (`nobody:users = 99:100`):
```yaml
environment:
  PUID: "99"
  PGID: "100"
```

Falls nötig, Berechtigungen anpassen:
```bash
chown -R 99:100 /mnt/user/appdata/ev-tracker   # Unraid
chown -R 10001:100 ./data                        # Standard
```

### Diagnose bei Zugriffsfehlern

`/api/health` aufrufen (ohne Login) — zeigt `db_writable`, `users_count` und `startup_error`. Ursache ist fast immer ein PUID/PGID-Mismatch.

---

## Updates

Updates erfolgen **ausschließlich über den Container-Daemon** — kein Docker Socket, kein In-App-Update.

```bash
# Docker Compose:
docker compose pull && docker compose up -d && docker image prune -f

# Unraid: Update-Button neben dem Container
```

### Image-Tags

| Tag | Beschreibung |
|-----|-------------|
| `latest` | Stabile Releases — wird nur bei Git-Tag `v*` gebaut |
| `stable` | Main-Branch (Staging / Pre-Release) |
| `dev` | Entwicklungsversion (dev-Branch) |

---

## Reverse Proxy

```yaml
environment:
  EV_TRACKER_EXPOSURE: "external"   # aktiviert HTTPS-Cookies, HSTS, ProxyFix
```

Der Reverse Proxy muss `X-Forwarded-Proto: https` und `X-Forwarded-For` setzen.

---

## Dateistruktur

```
/data/                          ← Volume-Mount
├── config.json                 ← Konfiguration (alle Einstellungen)
├── sessions.db                 ← SQLite (Sessions, Users, Vehicles, Rollen …)
├── template.xlsx               ← Eigene Excel-Vorlage (optional)
├── signature.png               ← Unterschrift für Export (optional)
├── exports/                    ← Generierte Monatsberichte
└── backups/                    ← Automatische Backups
```

---

## Bekannte Einschränkungen

- Manche Provider-APIs liefern keinen Standort oder AC/DC-Typ — EV Tracker versucht, diese Werte automatisch zu schätzen (Leistungsschwelle, Zähler, Standorthistorie). Unsichere Werte können manuell korrigiert werden.
- Verpasste Ladevorgänge (Fahrzeug war offline) werden erkannt, müssen aber vom Nutzer bestätigt werden.
- Dynamic Pricing (Tibber/Octopus) erfordert einen gültigen API-Key und Internetzugang.
- Der PDF-Export hat ein einfacheres Layout als der Excel-Export; für Arbeitgeberabrechnungen empfiehlt sich XLSX.

---

## Technologie

| Bereich | Technologie |
|---------|-------------|
| Backend | Python 3.12 + Flask (modular via Blueprints) |
| Datenbank | SQLite (WAL-Modus) |
| Frontend | Vanilla JS + Chart.js (responsive, PWA-fähig) |
| Excel | openpyxl |
| PDF | reportlab |
| Auth | Flask-Session, pyotp (TOTP), py_webauthn (FIDO2), Authlib (OAuth) |
| Fahrzeug-APIs | bimmer-connected, teslaPy, myrenaultapi, bluelinky u.v.m. |
| CI/CD | GitHub Actions → Docker Hub |
| Hosting | Docker (Unraid, Synology, Proxmox, bare metal …) |

---

## Dokumentation

| Dokument | Inhalt |
|----------|--------|
| [docs/quickstart.md](docs/quickstart.md) | Schnelleinstieg: Installation und erste Abrechnung |
| [docs/anleitung.md](docs/anleitung.md) | Ausführliche Anleitung mit allen Features |
| [CHANGELOG.md](CHANGELOG.md) | Versionshistorie |

---

## Entwicklung

```bash
git clone https://github.com/fdreckmann/ev_tracker.git
cd ev_tracker
pip install -r requirements.txt
python app/server.py
```

Änderungen pushen → GitHub Actions baut automatisch → Docker Hub.
