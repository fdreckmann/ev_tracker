# EV Tracker — Ladeprotokoll für Elektrofahrzeuge

Automatisches Ladeprotokoll für Elektrofahrzeuge via direkter Hersteller-API oder Home Assistant. Läuft als Docker Container auf Unraid oder jedem anderen Docker-Host.

![Docker Hub](https://img.shields.io/docker/pulls/19121412/ev-tracker)
![GitHub Actions](https://github.com/fdreckmann/ev_tracker/actions/workflows/docker-build.yml/badge.svg)
![Version](https://img.shields.io/badge/version-2.0.0-blue)

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
| 🏠 Standort | Unterscheidet Zuhause / Extern — manuell korrigierbar |
| 🔌 AC / DC | Ladertyp-Erkennung via Leistungssensor oder HA Sensor |
| 💰 Preismodell | Heimtarif fix · Extern via ENTSO-E Spotpreis + Aufschlag |
| ✎ Manuelle Korrektur | Kosten und Standort pro Session überschreibbar |
| 📊 Dashboard | Live-Status, Charts, Ladekurve pro Session |
| 📱 Mobile App | Responsive Mobile-Ansicht mit Bottom-Navigation und Cards |
| 🔔 Push | Benachrichtigungen via Home Assistant notify |
| 💾 Backup | Manuell + automatisch per Cron-Zeitplan |
| ⬆ Auto-Update | Update direkt im Web UI — Latest, Nightly oder Dev Kanal |
| 👥 Multi-User | Mehrere Benutzer mit Rollen und granularen Berechtigungen |
| 🔐 Auth | E-Mail/Passwort, TOTP 2FA, Google/Microsoft OAuth, Passkeys (FIDO2) |

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
| {{Platzhalter}} | 25+ Platzhalter in Templates: `{{month_year}}`, `{{total_kwh}}`, `{{signature}}` … |
| 👁 Vorschau | Echte XLSX-Vorschau mit befüllten Daten vor dem Download |
| ⚠ Warnings | Export-Hinweise bei fehlenden Daten, Signatur etc. |

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

---

## Berechtigungssystem (RBAC)

Ab v2.0.0 gibt es ein flexibles rollenbasiertes Berechtigungssystem.

**Standardrollen:**

| Rolle | Beschreibung |
|-------|-------------|
| `admin` | Vollzugriff (`admin:all`) |
| `user` | Normaler Benutzer — Export, Sessions, Fahrzeuge, Signatur |
| `readonly` | Nur-Lese-Zugriff auf Dashboard, Sessions, Export-Vorschau |

**Eigene Rollen:** Der Admin kann zusätzliche Rollen erstellen (z.B. Buchhaltung, Fuhrpark) und ihnen beliebige Berechtigungen zuweisen.

**Berechtigungsgruppen:** Dashboard · Fahrzeuge · Ladevorgänge · Analyse · Export · Templates · Signatur · Zählerstand · Provider · Einstellungen · Benutzer · Backup · Updates · Audit · System

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
🏠 Zuhause  → Fixer Heimtarif (z.B. 0.30 €/kWh)
🔌 Extern AC → ENTSO-E Spot × AC-Faktor (Fallback: Fixpreis)
⚡ Extern DC → ENTSO-E Spot × DC-Faktor (Fallback: Fixpreis)
✎ Manuell   → Jede Session einzeln korrigierbar
```

### ENTSO-E API Key (optional)

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

## Changelog (Auszug)

| Version | Highlights |
|---------|-----------|
| **2.0.0** | Flexibles RBAC (70+ Permissions, eigene Rollen), Mobile-Responsive UI mit Bottom-Nav, vollständige Export-Lokalisierung (de/en), Export-Vorschau mit Download-Token, Zählertest ohne Speichern, Shelly EMData/EM1Data, HA Wh-Normalisierung |
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
| Datenbank | SQLite |
| Frontend | Vanilla JS + Chart.js (responsive, Mobile-optimiert) |
| Excel | openpyxl |
| Authentifizierung | Flask-Session, pyotp (TOTP), py_webauthn (FIDO2), Authlib (OAuth) |
| Fahrzeug-APIs | bimmer-connected, teslaPy, myrenaultapi, bluelinky u.v.m. |
| CI/CD | GitHub Actions → Docker Hub |
| Hosting | Docker (Unraid, Synology, Proxmox, bare metal …) |
