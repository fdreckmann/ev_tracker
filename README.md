# EV Tracker — VW ID.7 Ladeprotokoll

Automatisches Ladeprotokoll für den VW ID.7 via Home Assistant WeConnect ID Integration. Läuft als Docker Container auf Unraid.

![Docker Hub](https://img.shields.io/docker/pulls/19121412/ev-tracker)
![GitHub Actions](https://github.com/fdreckmann/ev_tracker/actions/workflows/docker-build.yml/badge.svg)

---

## Features

| Feature | Beschreibung |
|---------|-------------|
| ⚡ Auto-Erkennung | Ladevorgänge werden automatisch erkannt und gespeichert |
| 🏠 Standort | Unterscheidet Zuhause / Extern via HA device_tracker |
| 🔌 AC / DC | Ladertyp-Erkennung via Leistungssensor oder HA Sensor |
| 💰 Preismodell | Heimtarif fix · Extern via ENTSO-E Spotpreis + Aufschlag |
| ✎ Manuelle Korrektur | Kosten pro Session überschreibbar |
| 📊 Dashboard | Live-Status, 4 Charts, Ladekurve pro Session |
| 📋 Excel Export | Eingebautes Format oder eigenes Template |
| 🔔 Push | Benachrichtigungen via Home Assistant notify |
| 💾 Backup | Manuell + automatisch per Cron-Zeitplan |
| ⬆ Auto-Update | Update-Check direkt im Web UI |

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
  <Overview>EV Ladeprotokoll für VW ID.7 via Home Assistant WeConnect.</Overview>
  <Category>Tools:</Category>
  <WebUI>http://[IP]:[PORT:8054]/</WebUI>
  <Icon>https://raw.githubusercontent.com/home-assistant/brands/master/custom_integrations/volkswagen_we_connect_id/icon.png</Icon>
  <ExtraParams>--restart unless-stopped</ExtraParams>
  <Config Name="Web UI Port" Target="8080" Default="8054" Mode="tcp" Type="Port" Display="always" Required="true" Mask="false">8054</Config>
  <Config Name="Daten-Verzeichnis" Target="/data" Default="/mnt/user/appdata/ev-tracker" Mode="rw" Type="Path" Display="always" Required="true" Mask="false">/mnt/user/appdata/ev-tracker</Config>
  <Config Name="DATA_DIR" Target="DATA_DIR" Default="/data" Type="Variable" Display="advanced" Required="true" Mask="false">/data</Config>
  <Config Name="TZ" Target="TZ" Default="Europe/Berlin" Type="Variable" Display="always" Required="true" Mask="false">Europe/Berlin</Config>
</Container>
XMLEOF
```

### Schritt 2 — Container starten

Browser neu laden (F5) → Unraid → Docker → **"Add Container"** → Template **"ev-tracker"** auswählen → **Apply**

### Schritt 3 — Web UI öffnen

```
http://<unraid-ip>:8054
```

→ **Konfiguration** Tab → HA URL + Token eingeben → Sensoren prüfen → Speichern

---

## Konfiguration

### Home Assistant Sensoren (VW ID.7 WeConnect)

| Sensor | Entity-ID |
|--------|-----------|
| Ladestatus | `sensor.volkswagen_id_id_7_charging_state` |
| Kilometerstand | `sensor.volkswagen_id_id_7_mileage` |
| SOC (%) | `sensor.volkswagen_id_id_7_state_of_charge` |
| Ladeleistung (kW) | `sensor.volkswagen_id_id_7_charge_power` |
| Standort | `device_tracker.wvwzzzed8se059543_position` |

> Entity-IDs in HA → Einstellungen → Entitäten → "volkswagen" suchen

### Preismodell

```
🏠 Zuhause  → Fixer Heimtarif (z.B. 0.30 €/kWh)
🔌 Extern AC → ENTSO-E Spot × AC-Faktor (Fallback: Fixpreis)
⚡ Extern DC → ENTSO-E Spot × DC-Faktor (Fallback: Fixpreis)
✎ Manuell   → Jede Session einzeln korrigierbar
```

### ENTSO-E API Key (optional, für automatische Strompreise)

1. Registrieren auf [transparency.entsoe.eu](https://transparency.entsoe.eu)
2. Email an `transparency@entsoe.eu` — Betreff: "Restful API access"
3. Key kommt per Email (wenige Tage)
4. In der Web UI unter Konfiguration → ENTSO-E eintragen

---

## Dateistruktur auf Unraid

```
/mnt/user/appdata/ev-tracker/
├── config.json          ← Konfiguration
├── sessions.db          ← SQLite Datenbank
├── template.xlsx        ← Eigene Excel-Vorlage (optional)
├── exports/             ← Generierte Monatsberichte
└── backups/             ← Automatische Backups
```

---

## Updates

Updates werden automatisch eingespielt wenn **Auto Update** in Unraid aktiviert ist:

> Unraid → Docker → ev-tracker → Edit → Auto Update: **Yes**

Oder manuell über das Web UI → **Backup Tab** → **"Auf Updates prüfen"**

---

## Entwicklung

```bash
# Repo klonen
git clone https://github.com/fdreckmann/ev_tracker.git
cd ev_tracker

# Lokal bauen und testen
docker build -t ev-tracker:latest .
docker run -d --name ev-tracker -p 8054:8080 \
  -v $(pwd)/data:/data \
  -e DATA_DIR=/data \
  -e TZ=Europe/Berlin \
  ev-tracker:latest

# Änderungen pushen → GitHub Actions baut automatisch
git add .
git commit -m "Beschreibung der Änderung"
git push
```

Bei jedem Push auf `main` wird automatisch ein neues Docker Image gebaut und auf Docker Hub veröffentlicht.

---

## Technologie

- **Backend:** Python 3.12 + Flask
- **Datenbank:** SQLite
- **Frontend:** Vanilla JS + Chart.js
- **Excel:** openpyxl
- **Container:** Docker auf Unraid
- **CI/CD:** GitHub Actions → Docker Hub
