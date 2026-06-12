# EV Tracker — Ausführliche Anleitung

> Für den schnellen Einstieg: → [Quickstart](quickstart.md)

## Inhaltsverzeichnis

1. [Konzept](#konzept)
2. [Installation & Konfiguration](#installation--konfiguration)
3. [Fahrzeug & Provider einrichten](#fahrzeug--provider-einrichten)
4. [Standorterkennung](#standorterkennung)
5. [Zähler & Wallbox](#zähler--wallbox)
6. [Ladertyp (AC/DC)](#ladertyp-acdc)
7. [Preise & Tarife](#preise--tarife)
8. [Öffentliche Ladeabos](#öffentliche-ladeabos)
9. [Excel-Export & Templates](#excel-export--templates)
10. [Automatische Berichte & E-Mail-Versand](#automatische-berichte--e-mail-versand)
11. [Ladevorgänge manuell verwalten](#ladevorgänge-manuell-verwalten)
12. [Fehlende Ladevorgänge](#fehlende-ladevorgänge)
13. [Multi-User & Berechtigungen](#multi-user--berechtigungen)
14. [Benachrichtigungen](#benachrichtigungen)
15. [Backup & Wiederherstellung](#backup--wiederherstellung)

---

## Konzept

EV Tracker verbindet sich im Hintergrund mit deiner Fahrzeug-API (oder Home Assistant) und speichert jeden Ladevorgang automatisch. Die wichtigsten Daten pro Session:

| Feld | Quelle |
|------|--------|
| Datum / Uhrzeit | API-Polling-Zeitstempel |
| kWh geladen | SOC-Delta × Akkukapazität, oder Wallbox-Zähler |
| Kosten in € | Preis/kWh × kWh |
| Standort | GPS / HA Entity / Zähler-Fallback |
| AC oder DC | API-Leistungssensor oder automatische Schätzung |
| SOC Start/Ende | Fahrzeug-API |
| KM-Stand | Odometer aus API |
| Zählerstand | Wallbox/Shelly/HA (optional) |

Aus diesen Daten erstellt EV Tracker auf Knopfdruck oder automatisch einen monatlichen Excel-Bericht.

---

## Installation & Konfiguration

### Docker

```bash
docker run -d --name ev-tracker \
  --restart unless-stopped \
  -p 8054:8080 \
  -v $(pwd)/data:/data \
  -e TZ=Europe/Berlin \
  -e PUID=10001 \
  -e PGID=100 \
  19121412/ev-tracker:latest
```

### Wichtige Umgebungsvariablen

| Variable | Standard | Beschreibung |
|----------|----------|-------------|
| `TZ` | `Europe/Berlin` | Zeitzone (wichtig für Monatsberichte) |
| `PUID` | `10001` | User-ID für /data Dateien |
| `PGID` | `100` | Gruppen-ID für /data Dateien |
| `DATA_DIR` | `/data` | Pfad zum Datenverzeichnis |
| `EV_TRACKER_EXPOSURE` | `internal` | `external` aktiviert HTTPS-Cookies + HSTS |

### Datenverzeichnis

```
/data/
├── config.json       ← alle Konfigurationseinstellungen
├── sessions.db       ← SQLite-Datenbank
├── template.xlsx     ← eigene Excel-Vorlage (optional)
├── signature.png     ← Unterschrift für Export (optional)
├── exports/          ← fertige Monatsberichte
└── backups/          ← automatische Backups
```

Alle Einstellungen werden in `config.json` gespeichert. Die Datenbank enthält alle Sessions, User, Rollen, Benachrichtigungsregeln und Ladeabos. Beides liegt im `/data`-Volume und sollte regelmäßig gesichert werden.

---

## Fahrzeug & Provider einrichten

Unter **Konfiguration → Fahrzeuge** klicke auf **+ Fahrzeug hinzufügen**.

<!-- TODO: Screenshot → docs/images/config-vehicle.png -->

### Direktanbindung (Hersteller-API)

Wähle deinen Hersteller aus der Liste. Die benötigten Felder variieren je Provider:

- **VW / Skoda / Seat / Cupra:** VW ID E-Mail + Passwort (WeConnect)
- **Tesla:** Refresh Token (über Tesla Auth App generieren)
- **BMW / Mini:** BMW-Konto-E-Mail + Passwort
- **Mercedes:** Client ID + Client Secret aus dem Developer Portal
- **Hyundai / Kia:** Bluelink/UVO E-Mail + Passwort + PIN
- **Stellantis** (Peugeot, Opel, Citroën, DS, Fiat, Jeep): E-Mail + Passwort + Land

Nach dem Speichern zeigt der Status-Badge „Verbunden" und der erste Poll-Zyklus startet (Standard: alle 60 Sekunden, konfigurierbar).

### Home Assistant

Falls das Fahrzeug bereits in Home Assistant integriert ist:

| Einstellung | Beispiel-Sensor |
|-------------|----------------|
| Ladestatus (binär) | `binary_sensor.mein_auto_charging` |
| SOC (%) | `sensor.mein_auto_soc` |
| Odometer (km) | `sensor.mein_auto_odometer` |
| Ladeleistung (kW) | `sensor.mein_auto_charging_power` |
| Standort | `device_tracker.mein_auto` |
| AC/DC-Typ | `sensor.mein_auto_charger_type` |
| HA-URL | `http://homeassistant.local:8123` |
| HA-Token | Long-lived Access Token |

Nicht alle Sensoren sind Pflicht. Je mehr vorhanden sind, desto vollständiger die Daten.

<!-- TODO: Screenshot HA-Provider-Konfiguration → docs/images/config-ha.png -->

### Mehrere Fahrzeuge

Jedes Fahrzeug bekommt einen eigenen Eintrag. Ladevorgänge werden automatisch dem richtigen Fahrzeug zugeordnet. Im Export und in den Berichten kann nach Fahrzeug gefiltert werden.

---

## Standorterkennung

EV Tracker unterscheidet zwischen **Zuhause** (→ Heimtarif) und **Extern** (→ öffentlicher Ladepreis). Die Erkennungslogik prüft mehrere Signale:

### Erkennungsquellen (Priorität hoch → niedrig)

1. **Provider-GPS:** Das Fahrzeug meldet Koordinaten → Haversine-Distanz zur Heimadresse. Wenn innerhalb des Radius (Standard: 200m) → Zuhause.

2. **Home Assistant `device_tracker`:** HA meldet `home` → Zuhause. HA meldet `not_home` → Extern.

3. **Zähler-Fallback:** Wenn Standort unbekannt und der lokale Zähler während des Ladens steigt → automatisch Zuhause. Funktioniert nur wenn ein Zähler konfiguriert ist.

4. **Manuell:** Jeder Ladevorgang kann nachträglich manuell auf Zuhause/Extern gesetzt werden.

### Konfiguration

Unter **Konfiguration → Fahrzeuge → [Fahrzeug] → Standort**:
- Heimadresse (Straße) oder GPS-Koordinaten eintragen
- Erkennungsradius setzen (Standard: 200m)
- Erkennungsmodus: `provider_only`, `ha_only`, `any` (eines reicht), `all` (beide müssen stimmen)

<!-- TODO: Screenshot Standort-Konfiguration → docs/images/config-location.png -->

### Standort-Verlauf

Wenn aktiviert (**Konfiguration → Standort → Standortverlauf**), speichert EV Tracker den Verlauf mit Zeitstempel. Präzision wählbar: `status_only` (Zuhause/Extern), `rounded` (GPS gerundet), `exact` (GPS exakt). Nur Benutzer mit `vehicles:location_exact_view` sehen exakte Koordinaten.

---

## Zähler & Wallbox

Ein lokaler Zähler liefert präzise kWh-Werte, die unabhängig von der Fahrzeug-API sind. Das ist besonders für die Arbeitgeberabrechnung wertvoll, da Messgeräte rechtssicher sind.

### Unterstützte Geräte

- **Shelly** (Gen1/Gen2/Pro/Plus) — automatische API-Erkennung
- **Tasmota** — SML-Sensor, JSON-Pfad konfigurierbar
- **go-e Charger** — direkter HTTP-Zugriff
- **openWB** — Ladepunkt-Index konfigurierbar
- **WARP Charger, EVCC, Webasto, Alfen, Juice** — HTTP
- **Home Assistant** — beliebiger HA-Sensor (Wh/kWh/MWh automatisch erkannt)
- **Generic HTTP** — beliebige URL mit JSON-Pfad

### Konfiguration

Unter **Konfiguration → Fahrzeuge → [Fahrzeug] → Zähler & Wallbox**:
1. Provider wählen (z.B. Shelly)
2. IP-Adresse oder URL eintragen
3. „Verbindung testen" klicken
4. Konfigurieren, ob der Zählerstand als kWh-Quelle bevorzugt wird (`Zähler bevorzugen`)

<!-- TODO: Screenshot Zähler-Konfiguration → docs/images/config-meter.png -->

### Zähler-Scope

`meter_scope = home_only` (Standard): Der Zähler wird nur bei Heimsessions ausgelesen. Bei Extern-Sessions wird der Zähler übersprungen (Grund wird pro Session gespeichert: `meter_skipped_reason`).

### Zähler-basierte Heimerkennung

Wenn der Standort unbekannt ist und der lokale Zähler während des Ladens ansteigt, erkennt EV Tracker die Session automatisch als Zuhause-Laden. Konfigurierbar unter: **Konfiguration → Laden & Erkennung → Zähler-Heimerkennung**:
- Mindestzuwachs (Standard: 0,2 kWh)
- Zeitfenster (Standard: 10 Minuten)

---

## Ladertyp (AC/DC)

Der Ladertyp (AC oder DC) beeinflusst die Preisberechnung (AC- und DC-Tarife können unterschiedlich sein).

### Erkennungsquellen

1. **API:** Manche Provider liefern den Typ direkt.
2. **Leistungssensor:** Bei > 22 kW (konfigurierbar: `dc_threshold_kw`) → DC; darunter → AC.
3. **Standort:** Zuhause-Ladevorgänge → immer AC (Wallboxen sind AC).
4. **Schätzung:** Wenn keine der obigen Quellen verfügbar ist, wird aus kWh ÷ Dauer ein Durchschnittswert geschätzt (mit `~` markiert).
5. **Manuell:** Jeder Ladevorgang kann korrigiert werden.

### DC-Schwellwert anpassen

Standard: 22 kW. Unter **Konfiguration → Fahrzeuge → [Fahrzeug] → Erweitert → DC-Schwellwert** ändern, falls deine Wallbox über 22 kW lädt und fälschlich als DC erkannt wird.

---

## Preise & Tarife

### Heimtarif

Unter **Konfiguration → Laden & Preise → Heimtarif**:

**Fester Preis:**
```
Heimtarif: 0,32 €/kWh
```

**Dynamischer Tarif (Tibber):**
- Tibber-Token eintragen
- EV Tracker ruft den Stundentarif ab und mittelt ihn zeitgewichtet über den Ladezeitraum

**Andere Quellen:** Octopus Energy, Home Assistant Sensor, EVCC, oder Generic HTTP (beliebige JSON-API).

### Externtarif (öffentliches Laden)

Unter **Konfiguration → Laden & Preise → Externtarif**:
- Separater Festpreis für AC Extern und DC Extern
- ENTSO-E Spotpreis (kostenpflichtiger API-Key, gratis erhältlich bei entsoe.eu)
- Oder: Ladeabos (siehe nächster Abschnitt)

---

## Öffentliche Ladeabos

Wenn du ein Ladeabo (ADAC, EnBW, Ionity, NewMotion, …) hast, kannst du den Vertrag in EV Tracker hinterlegen. EV Tracker berechnet dann automatisch den richtigen Preis für externe Ladevorgänge.

Unter **Konfiguration → Laden & Preise → Ladeabos**:
1. **+ Ladeabo hinzufügen**
2. Anbieter, kWh-Preis, Minutenpreis, Session-Fee eintragen
3. Optional: Vorgefertigte Vorlagen über „Vorlagen importieren" (ADAC, EnBW, Ionity, …)

<!-- TODO: Screenshot Ladeabos → docs/images/config-contracts.png -->

---

## Excel-Export & Templates

### Direkt-Export (ohne Template)

Unter **Export**:
1. Fahrzeug wählen
2. Monat wählen
3. **Download** — fertige XLSX-Datei mit allen Ladevorgängen

Das Standardformat enthält alle relevanten Felder und ist für viele Abrechnungen direkt nutzbar.

### Eigenes Template einrichten

Wenn dein Arbeitgeber eine spezifische Vorlage vorschreibt:

1. **Template hochladen:** Unter **Export → Template verwalten → Hochladen**
2. **Start-Zeile festlegen:** In der Vorschau klicken, ab welcher Zeile Daten eingetragen werden
3. **Spalten zuordnen** (3 Modi):
   - **Klick-Modus:** Spaltenüberschrift klicken → Feld auswählen
   - **Drag-Modus:** Felder in die Spalten ziehen
   - **Manuell:** Direkt Buchstabe/Nummer eintragen
4. **Einzelzellen** (z.B. Name in `B3`, Kennzeichen in `C3`): Über **Einzelzellen-Mapping** konfigurieren

<!-- TODO: Screenshot Export-Tab mit Template → docs/images/export-template.png -->
<!-- TODO: Screenshot Spalten-Mapping-Dialog → docs/images/export-mapping.png -->

### Vorgefertigte Vorlagen

EV Tracker enthält 4+ fertige Vorlagen:
- **Standard:** Alle Felder, DIN A4
- **Arbeitgeber:** Für Arbeitgeberabrechnungen optimiert (Fahrername, Kennzeichen, Unterschrift)
- **Steuer:** Mit Belegnummern und steuerrelevanten Feldern
- **Minimal:** Nur Datum, kWh, Kosten

### Unterschrift

Unter **Export → Signatur**: Unterschrift als Bild hochladen oder direkt im Browser mit der Maus/Touchscreen zeichnen. Die Signatur wird im Export an der konfigurierten Position eingefügt.

### Platzhalter in Templates

In einzelnen Zellen können Platzhalter wie `{{month_year}}` hinterlegt werden, die beim Export automatisch befüllt werden:

| Platzhalter | Inhalt |
|-------------|--------|
| `{{month_year}}` | z.B. „Januar 2025" |
| `{{total_kwh}}` | Gesamt geladene kWh |
| `{{home_kwh}}` | Davon zuhause |
| `{{extern_kwh}}` | Davon extern |
| `{{total_cost}}` | Gesamtkosten |
| `{{home_cost}}` | Heimladekosten |
| `{{extern_cost}}` | Externe Ladekosten |
| `{{driver_name}}` | Fahrername |
| `{{vehicle_name}}` | Fahrzeugname |
| `{{license_plate}}` | Kennzeichen |
| `{{meter_start_value}}` | Zählerstand Monatsbeginn |
| `{{meter_end_value}}` | Zählerstand Monatsende |
| `{{session_count}}` | Anzahl Ladevorgänge |

---

## Automatische Berichte & E-Mail-Versand

### SMTP einrichten

Unter **Konfiguration → Berichte & Export → E-Mail-Einstellungen**:

**Einfaches SMTP:**

| Feld | Beispiel |
|------|---------|
| SMTP-Server | `smtp.gmail.com` |
| Port | `587` (STARTTLS) oder `465` (SSL) |
| Benutzername | `deine@email.de` |
| Passwort | App-Passwort (nicht dein normales Passwort) |

**Google Gmail:** Unter Google-Konto → Sicherheit → App-Passwörter → App-Passwort für „E-Mail" erstellen.

**Microsoft 365:** OAuth2-Verbindung nutzen (Schaltfläche „Mit Microsoft verbinden") — kein App-Passwort nötig.

**OAuth2 (Google/Microsoft ohne Passwort):** Schaltfläche „Mit Google/Microsoft verbinden" → Browser-Login → fertig.

<!-- TODO: Screenshot SMTP-Konfiguration → docs/images/config-smtp.png -->

### E-Mail-Bericht einrichten

Unter **Konfiguration → Berichte & Export → Berichte → Neuer Bericht**:

| Einstellung | Optionen |
|-------------|---------|
| Turnus | Täglich, Wöchentlich, Monatlich, Quartalsweise, Jährlich, Benutzerdefiniert |
| Zeitraum | Vorheriger Monat, Aktueller Monat, Letzten N Monate, Benutzerdefiniert |
| Format | Excel (Standard), Excel mit Template, PDF |
| Empfänger | Beliebig viele E-Mail-Adressen |
| Fahrzeug | Alle oder ein bestimmtes |
| Standort | Alle, nur Zuhause, nur Extern |
| Versandzeit | Uhrzeit des automatischen Versands |

<!-- TODO: Screenshot Bericht-Einstellungen → docs/images/config-report-schedule.png -->

### Report-Archiv

Bereits versendete Reports werden unter **Konfiguration → Berichte** archiviert. Du kannst sie dort:
- Herunterladen
- Erneut versenden
- Als genehmigt markieren
- Löschen

---

## Ladevorgänge manuell verwalten

### Manuellen Ladevorgang hinzufügen

Unter **Ladevorgänge → + Manuell erfassen** (Desktop) oder über das FAB-Menü (Mobile):

- Start- und Endzeit
- kWh geladen (oder Zähleranfang/-ende)
- Standort (Zuhause/Extern)
- AC oder DC
- SOC Start/Ende (optional)
- KM-Stand (optional)
- Kosten oder Preis/kWh (optional, sonst automatisch aus Tarif)
- Notiz und Begründung (optional)

### Ladevorgang bearbeiten

Klicke in der Liste auf einen Ladevorgang → **Detail-Ansicht** öffnet sich. Dort:
- `✎ Kosten` — kWh, Preis, Gesamtkosten anpassen
- `📍` — Standort ändern
- Klick auf den Eintrag → vollständiges Bearbeitungsformular öffnet sich

### Preise neu berechnen

Falls du den Tarif geändert hast und bestehende Sessions neu berechnen möchtest: Klicke im Session-Detail auf **Preis neu berechnen** — EV Tracker wendet den aktuellen Tarif an.

---

## Fehlende Ladevorgänge

EV Tracker analysiert die SOC-Historie des Fahrzeugs. Wenn der Akkustand zwischen zwei Abfragen deutlich gestiegen ist, ohne dass eine Session erfasst wurde (z.B. Fahrzeug war offline), erscheint ein Vorschlag unter **Ladevorgänge → Fehlende Ladevorgänge**.

<!-- TODO: Screenshot Fehlende Ladevorgänge → docs/images/missing-charges.png -->

Für jeden Vorschlag kannst du:
- **Annehmen:** Dialog öffnet sich vorausgefüllt → prüfen und speichern
- **Einmalig ignorieren:** Kandidat als „erledigt" markieren
- **Dauerhaft ignorieren:** Für dieses Zeitfenster nie wieder vorschlagen

Die automatische Erkennung berücksichtigt auch die Energiebilanz: Wenn das Auto eine Strecke gefahren ist, für die die SOC-Abnahme energetisch implausibel gering ist (möglicher Zwischenladestopp), wird ebenfalls ein Kandidat erstellt.

---

## Multi-User & Berechtigungen

Unter **Konfiguration → Benutzer & Rollen**:

### Benutzer einladen

1. **Benutzer einladen** → E-Mail-Adresse eingeben
2. Rolle zuweisen: `admin`, `user`, `readonly` oder eigene Rolle
3. Einladungslink wird per E-Mail verschickt (oder direkt angezeigt)

### Standardrollen

| Rolle | Rechte |
|-------|--------|
| `admin` | Vollzugriff |
| `user` | Export, Sessions erfassen/bearbeiten, Dashboard |
| `readonly` | Nur lesen, Dashboard, Export-Vorschau |

### Eigene Rollen

Eigene Rollen mit granularen Berechtigungen sind möglich — z.B. eine Rolle nur für den Export-Download ohne Konfigurationszugriff.

---

## Benachrichtigungen

EV Tracker unterstützt Push-Benachrichtigungen über:
- **Home Assistant** (`notify`-Service)
- **ntfy** (selfhosted oder ntfy.sh)
- **Gotify**
- **Telegram**
- **E-Mail** (via konfiguriertem SMTP)
- **Webhook** (beliebige URL)
- **MQTT**

Unter **Konfiguration → Benachrichtigungen → Regeln**: Konfiguriere, welche Ereignisse eine Benachrichtigung auslösen (z.B. „Ladevorgang gestartet", „Session gespeichert", „Bericht versendet"). Ruhezeitfenster (Standard: 22:00–07:00) verhindern Nachrichten in der Nacht.

---

## Backup & Wiederherstellung

### Manuelles Backup

Unter **Backup → Jetzt sichern** — erstellt ein vollständiges Backup von `config.json` und `sessions.db` als ZIP.

### Automatisches Backup

Unter **Backup → Zeitplan**: Cron-Ausdruck eintragen (z.B. `0 3 * * 0` = jeden Sonntag um 3 Uhr). Alte Backups werden automatisch rotiert (konfigurierbare Aufbewahrungsdauer).

### Wiederherstellung

1. Unter **Backup → Backup wiederherstellen** die ZIP-Datei hochladen
2. EV Tracker extrahiert `config.json` und `sessions.db` ins Datenverzeichnis
3. Neustart des Containers für vollständigen Neustart mit den wiederhergestellten Daten

---

## Weiterführende Informationen

- [Quickstart](quickstart.md) — 5-Schritte-Kurzanleitung
- [README](../README.md) — Provider-Übersicht, Sicherheit, Reverse Proxy, Updates
- **Im App** unter Konfiguration → Version & Update: Release Notes und aktuelle Versionsinformationen
