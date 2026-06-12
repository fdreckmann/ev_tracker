# EV Tracker — Quickstart

EV Tracker protokolliert deine Ladevorgänge automatisch und erstellt daraus einen Excel-Bericht, den du monatlich an deinen Arbeitgeber schicken kannst.

**In 5 Schritten zur ersten Abrechnung:**

1. [Docker starten](#1-docker-starten)
2. [Ersten Benutzer anlegen](#2-ersten-benutzer-anlegen)
3. [Fahrzeug / Provider einrichten](#3-fahrzeug--provider-einrichten)
4. [Excel-Template einrichten](#4-excel-template-einrichten)
5. [Automatischen Versand konfigurieren](#5-automatischen-e-mail-versand-konfigurieren)

---

## 1. Docker starten

```bash
docker run -d --name ev-tracker \
  --restart unless-stopped \
  -p 8054:8080 \
  -v $(pwd)/data:/data \
  -e TZ=Europe/Berlin \
  19121412/ev-tracker:latest
```

Danach im Browser öffnen: `http://<deine-ip>:8054`

**Unraid:** Siehe [Unraid-Template](https://github.com/fdreckmann/ev-tracker-unraid-app) — Installation über Community Apps.

> **Hinweis zu Berechtigungen:** Falls der Container nicht startet oder „First User Setup" trotz vorhandenem Konto erscheint, stimmt der Dateisystem-Owner nicht. Lösung: `PUID`/`PGID` auf den User setzen, dem das `/data`-Verzeichnis gehört. Details in der [README](../README.md#benutzer--berechtigungen-puid--pgid).

<!-- TODO: Screenshot docker-compose oder Unraid-Install-Dialog → docs/images/install-unraid.png -->

---

## 2. Ersten Benutzer anlegen

Beim ersten Start erscheint der **Setup-Assistent**. Dort legt du:
- Admin-E-Mail und Passwort an
- Optional: Zeitzone bestätigen

Danach kannst du dich einloggen. Optional: 2FA unter **Konfiguration → Mein Konto → Sicherheit** einrichten.

<!-- TODO: Screenshot Setup-Formular → docs/images/setup-first-user.png -->

---

## 3. Fahrzeug / Provider einrichten

Unter **Konfiguration → Fahrzeuge** trägst du deinen Fahrzeug-Provider ein.

<!-- TODO: Screenshot Fahrzeug-Konfiguration → docs/images/config-vehicle.png -->

### Option A: Direkte Hersteller-API

Wähle deinen Hersteller (VW, Tesla, BMW, …) und trage die API-Zugangsdaten ein — meistens dieselben wie in der Hersteller-App.

**Wichtig:** Manche Hersteller (z.B. VW) liefern keinen AC/DC-Typ oder keinen Standort — EV Tracker schätzt diese Werte automatisch und markiert Schätzungen mit `~`. Du kannst sie jederzeit manuell korrigieren.

### Option B: Home Assistant

Wenn dein Fahrzeug bereits in Home Assistant eingebunden ist, trägst du die Sensor-Namen ein:
- `sensor.auto_state_of_charge` (SOC in %)
- `sensor.auto_odometer` (KM-Stand)
- `binary_sensor.auto_charging` (lädt ja/nein)
- `sensor.auto_charging_power` (Ladeleistung in kW, für AC/DC-Erkennung)
- Optional: `device_tracker.auto` (Standort)

<!-- TODO: Screenshot HA-Konfiguration → docs/images/config-ha.png -->

### Option C: Wallbox / Zähler direkt

Wenn du eine Wallbox (go-e, openWB, WARP, Shelly, …) betreibst, kann EV Tracker den Zählerstand direkt auslesen. Das gibt dir präzise kWh-Werte aus dem Messgerät statt aus dem Fahrzeug-SOC.

Konfiguriere den Zähler unter **Konfiguration → Fahrzeuge → [Fahrzeug] → Zähler & Wallbox**.

<!-- TODO: Screenshot Zähler-Konfiguration → docs/images/config-meter.png -->

### Verbindung testen

Nach dem Speichern erscheint unter **Konfiguration → Fahrzeuge** ein Status-Badge. Grün = verbunden. Falls rot: Logs unter **Konfiguration → System & Betrieb** prüfen.

---

## 4. Excel-Template einrichten

EV Tracker kann:
- **A)** Einen fertigen Standard-Export herunterladen (sofort, ohne Konfiguration)
- **B)** Ein eigenes Excel-Template mit deinem Firmen-Layout befüllen

### Option A: Standard-Export (sofort nutzbar)

Unter **Export** findest du fertige Vorlagen — einfach Monat wählen und herunterladen.

<!-- TODO: Screenshot Export-Tab → docs/images/export-overview.png -->

### Option B: Eigenes Template einrichten

1. Öffne deine bestehende Excel-Abrechnungsvorlage in Excel/LibreOffice
2. Notiere, in welcher Zeile/Spalte die Ladevorgänge stehen sollen
3. Lade die Datei unter **Export → Template hochladen** hoch
4. Klicke auf **Spalten zuordnen** und weise die Spalten zu:
   - Datum, Uhrzeit, kWh, Kosten, Standort, AC/DC, …
5. Falls Kopfdaten (Fahrer, Monat, Kennzeichen) in einzelnen Zellen stehen: **Einzelzellen-Mapping** nutzen, z.B. Zelle `B3` = `{{month_year}}`
6. Optional: Unterschrift hochladen (unter **Export → Signatur**)

**Verfügbare Platzhalter (Auswahl):**

| Platzhalter | Inhalt |
|-------------|--------|
| `{{month_year}}` | z.B. „Januar 2025" |
| `{{total_kwh}}` | Gesamt geladene kWh im Monat |
| `{{total_cost}}` | Gesamtkosten in € |
| `{{home_kwh}}` | Davon zuhause geladen |
| `{{driver_name}}` | Name des Fahrers |
| `{{vehicle_name}}` | Fahrzeugname |
| `{{meter_start_value}}` | Zählerstand Monatsbeginn |
| `{{meter_end_value}}` | Zählerstand Monatsende |

<!-- TODO: Screenshot Spalten-Mapping-Dialog → docs/images/export-mapping.png -->

---

## 5. Automatischen E-Mail-Versand konfigurieren

Damit der Bericht automatisch am Monatsende an deine Buchhaltung geht:

### SMTP einrichten

Unter **Konfiguration → Berichte & Export → E-Mail-Versand**:

| Feld | Beispiel |
|------|----------|
| SMTP-Server | `smtp.gmail.com` |
| Port | `587` (STARTTLS) oder `465` (SSL) |
| Benutzername | `deine@email.de` |
| Passwort | App-Passwort (bei Gmail/Microsoft) |

**Google/Microsoft ohne Passwort:** Über **OAuth2** verbinden — unter Konfiguration → E-Mail → Google oder Microsoft klicken.

<!-- TODO: Screenshot SMTP-Einstellungen → docs/images/config-smtp.png -->

### Report-Zeitplan einrichten

Unter **Konfiguration → Berichte & Export → Berichte**:

1. Klicke auf **Neuer Bericht**
2. Einstellungen:
   - **Turnus:** Monatlich
   - **Zeitraum:** Vorheriger Monat
   - **Empfänger:** Deine eigene Adresse und/oder Buchhaltung
   - **Format:** Excel (mit Template) oder PDF
   - **Fahrzeug:** Wenn mehrere vorhanden, das richtige wählen
3. Speichern — der erste Versand erfolgt am eingestellten Datum

<!-- TODO: Screenshot Bericht-Einstellungen → docs/images/config-report-schedule.png -->

---

## Ladevorgänge überprüfen

Unter **Ladevorgänge** siehst du alle erfassten Sessions. Felder, die automatisch geschätzt wurden, sind mit `~` markiert (z.B. AC/DC wenn die API keine Info liefert).

<!-- TODO: Screenshot Ladevorgänge-Liste → docs/images/sessions-list.png -->

### Häufige Korrekturen

**Standort falsch erkannt:**
Klicke auf `📍` neben dem Ladevorgang → Standort manuell auf „Zuhause" oder „Extern" setzen.

**AC/DC falsch:**
Klicke auf den Ladevorgang → `Bearbeiten` → Ladeart korrigieren.

**kWh fehlen oder falsch:**
Klicke `✎ Kosten` → kWh und/oder Preis manuell eintragen.

**Ladevorgang vergessen / war offline:**
EV Tracker erkennt verpasste Sessions automatisch (SOC-Sprung ohne erfasste Session). Unter **Ladevorgänge → Fehlende Ladevorgänge** erscheint ein Vorschlag — prüfen und übernehmen oder ignorieren.

---

## Typische Szenarien

### Wallbox zählt nicht / ändert sich nicht

EV Tracker kann aus einem unveränderten Wallbox-Zähler schließen, dass das Fahrzeug **extern** geladen wurde. Das ist normal und gewünscht — die Erkennung wird unter **Konfiguration → Laden & Erkennung → Zähler-Heimerkennung** gesteuert.

### API liefert keinen Standort

Wenn der Provider keinen Standort meldet, versucht EV Tracker:
1. GPS-Geofence (wenn Koordinaten verfügbar)
2. Home Assistant `device_tracker`
3. Anstieg des lokalen Zählers → Zuhause
4. Kein Signal → Standort bleibt „Unbekannt" und muss manuell gesetzt werden

### Ladevorgang als extern erkannt, obwohl zuhause geladen

Ursachen: GPS-Koordinaten außerhalb des Radius, HA-Entity meldet „away", oder kein Zähler konfiguriert. Lösung: Radius vergrößern (**Konfiguration → Standort → Heimradius**) oder Wallbox-Zähler einrichten.

### Fahrzeugdaten fehlen oder sind veraltet

Manche APIs (besonders VW, Stellantis) aktualisieren Daten nur alle 15–60 Minuten. Sehr kurze Ladevorgänge (< 15 Min.) können deshalb verpasst werden. Lösung: Zähler-Integration aktivieren, der immer live misst.

---

## Nächste Schritte

- [Ausführliche Anleitung](anleitung.md) — alle Konfigurationsoptionen im Detail
- [README](../README.md) — Provider-Übersicht, Installation, Sicherheit
- Im EV Tracker selbst: **Konfiguration → Version & Update** für Release Notes und Hilfe-Link
