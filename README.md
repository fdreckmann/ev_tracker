# EV Tracker — Ladeprotokoll für Elektrofahrzeuge

Automatisches Ladeprotokoll für Elektrofahrzeuge via direkter Hersteller-API oder Home Assistant. Läuft als Docker Container auf Unraid oder jedem anderen Docker-Host.

![Docker Hub](https://img.shields.io/docker/pulls/19121412/ev-tracker)
![GitHub Actions](https://github.com/fdreckmann/ev_tracker/actions/workflows/docker-build.yml/badge.svg)

---

## Unterstützte Fahrzeuge / Provider

| Provider | Laden | SOC | KM | Leistung | Standort | AC/DC |
|----------|-------|-----|----|----------|----------|-------|
| 🏠 Home Assistant | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |
| 🚗 VW / Audi / Skoda / Seat | ✅ | ✅ | ✅ | ✅ | ✅ | ⚠ |
| ⚡ Tesla | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |
| 🔵 Volvo | ✅ | ✅ | ❌ | ❌ | ❌ | ❌ |
| 🔷 BMW / Mini | ✅ | ✅ | ✅ | ❌ | ✅ | ❌ |
| ⭐ Mercedes-Benz | ✅ | ✅ | ✅ | ❌ | ❌ | ❌ |

---

## Features

| Feature | Beschreibung |
|---------|-------------|
| ⚡ Auto-Erkennung | Ladevorgänge werden automatisch erkannt und gespeichert |
| 🏠 Standort | Unterscheidet Zuhause / Extern — manuell korrigierbar |
| 🔌 AC / DC | Ladertyp-Erkennung via Leistungssensor oder HA Sensor |
| 💰 Preismodell | Heimtarif fix · Extern via ENTSO-E Spotpreis + Aufschlag |
| ✎ Manuelle Korrektur | Kosten und Standort pro Session überschreibbar |
| 📊 Dashboard | Live-Status, 4 Charts, Ladekurve pro Session |
| 📋 Excel Export | Eingebautes Format oder eigenes Template mit Spalten-Mapping |
| 🔔 Push | Benachrichtigungen via Home Assistant notify |
| 💾 Backup | Manuell + automatisch per Cron-Zeitplan |
| ⬆ Auto-Update | Update direkt im Web UI — Latest, Nightly oder Dev Kanal |

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

→ **Konfiguration** Tab → Provider wählen → Verbindung einrichten → Speichern

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
├── sessions.db          ← SQLite Datenbank
├── template.xlsx        ← Eigene Excel-Vorlage (optional)
├── exports/             ← Generierte Monatsberichte
└── backups/             ← Automatische Backups
```

---

## Entwicklung

```bash
git clone https://github.com/fdreckmann/ev_tracker.git
cd ev_tracker
docker build -t ev-tracker:latest .
docker run -d --name ev-tracker -p 8054:8080 \
  -v $(pwd)/data:/data -e DATA_DIR=/data -e TZ=Europe/Berlin \
  ev-tracker:latest

# Änderungen pushen → GitHub Actions baut automatisch
git add . && git commit -m "Beschreibung" && git push
```

---

## Technologie

- **Backend:** Python 3.12 + Flask
- **Datenbank:** SQLite
- **Frontend:** Vanilla JS + Chart.js
- **Excel:** openpyxl
- **CI/CD:** GitHub Actions → Docker Hub
- **Hosting:** Docker auf Unraid
