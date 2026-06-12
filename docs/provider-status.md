# EV Tracker — Provider-Status

Stand: Juni 2026 · Wird bei bekannten API-Änderungen aktualisiert.

## Stabilitätsstufen

| Stufe | Bedeutung |
|-------|-----------|
| 🟢 Stabil | Offizielle API oder bewährte Integration — langfristig zuverlässig |
| 🟡 Mittel | Inoffizielle API — funktioniert aktuell, kann sich ohne Vorwarnung ändern |
| 🔴 Fragil | Bekannte Einschränkungen oder API-Sperrung — ggf. nicht mehr nutzbar |

---

## Direktintegrationen

| Provider | Bibliothek / API | Stabilität | SOC | Leistung | Standort | AC/DC | Einschränkungen |
|----------|-----------------|-----------|-----|---------|---------|-------|-----------------|
| **Home Assistant** | HA REST API (offiziell) | 🟢 Stabil | ✅ | ✅ | ✅ | ✅ | Empfohlen — funktioniert mit jedem HA-integrierten Fahrzeug |
| **Tesla** | teslapy (inoffiziell) | 🟡 Mittel | ✅ | ✅ | ✅ | ✅ | Einmalige Token-Autorisierung per CLI erforderlich |
| **Volvo** | Volvo Cars API (offiziell) | 🟢 Stabil | ✅ | ❌ | ❌ | ❌ | Kein KM-Stand, Leistung oder Standort via API |
| **BMW / Mini** | bimmer-connected (inoffiziell) | 🟡 Mittel | ✅ | ❌ | ✅ | ❌ | Ladeleistung nicht verfügbar |
| **Mercedes-Benz** | Mercedes API (offiziell) | 🟢 Stabil | ✅ | ❌ | ❌ | ❌ | Developer-Account erforderlich; Leistung/Standort fehlen |
| **Hyundai / Kia** | hyundai-kia-connect-api | 🟡 Mittel | ✅ | ❌ | ✅ | ❌ | App-PIN erforderlich; Ladeleistung nicht verfügbar |
| **Renault / Dacia** | renault-api | 🟡 Mittel | ✅ | ✅ | ❌ | ❌ | Standort nicht verfügbar |
| **Polestar** | inoffizielle GraphQL-API | 🟡 Mittel | ✅ | ❌ | ❌ | ❌ | Ladeleistung und Standort nicht verfügbar |
| **Stellantis** (Peugeot/Opel/Citroën/Fiat) | PSA/Stellantis API (inoffiziell) | 🟡 Mittel | ✅ | ✅ | ✅ | ❌ | Kann sich ohne Vorwarnung ändern |
| **Ford** | FordPass Connect (inoffiziell) | 🟡 Mittel | ✅ | ❌ | ✅ | ❌ | Ladeleistung nicht verfügbar |
| **MG / SAIC** | iSMART API (inoffiziell) | 🔴 Fragil | ✅ | ✅ | ✅ | ❌ | API ändert sich häufig |
| **Toyota / Lexus** | mytoyota *(optional)* | 🟡 Mittel | ✅ | ❌ | ✅ | ❌ | `pip install mytoyota` erforderlich; Ladeleistung fehlt |
| **Nissan** | nissan-connect-ev *(optional)* | 🟡 Mittel | ✅ | ❌ | ❌ | ❌ | `pip install nissan-connect-ev`; SOC + Ladestatus only |
| **Porsche** | pyporsche *(optional)* | 🟡 Mittel | ✅ | ❌ | ✅ | ❌ | `pip install pyporsche` erforderlich |
| **Jaguar / Land Rover** | jlrpy *(optional)* | 🟡 Mittel | ✅ | ❌ | ✅ | ❌ | `pip install jlrpy` erforderlich |

### Eingeschränkte / fragile Direktintegrationen

| Provider | Stabilität | Status | Empfehlung |
|----------|-----------|--------|------------|
| **VW / Skoda / Seat** (WeConnect) | 🔴 Fragil | VW hat WeConnect-API für Drittanwendungen ab 2024 stark eingeschränkt. Verbindung schlägt bei vielen Nutzern fehl. | **Home Assistant Provider** verwenden (VW/Skoda/Seat HACS-Integration vorhanden) |
| **Audi** (MyAudi Connect) | 🔴 Fragil | Nutzt alte VW-Gruppeninfrastruktur (`msg.volkswagen.de`), die seit 2023 weitgehend abgeschaltet wurde. | **Home Assistant Provider** verwenden |
| **XPeng** | 🔴 Fragil | Keine stabile direkte EU-API verfügbar. | Aggregator (Tronity, Enode, Smartcar) verwenden |
| **BYD** | 🔴 Fragil | Keine stabile direkte EU-API verfügbar. | Aggregator (Tronity, Enode, Smartcar) verwenden |

---

## Aggregatoren

Aggregatoren unterstützen viele Fahrzeugmarken über eine einheitliche API. Kostenpflichtig, dafür stabil.

| Aggregator | Marken | Stabilität | Kosten |
|-----------|--------|-----------|--------|
| **TRONITY** | 90+ Marken | 🟢 Stabil | Abo (kostenlose Testphase) |
| **Enode** | 50+ Marken | 🟢 Stabil | Abo (Developer-Tier verfügbar) |
| **Smartcar** | 30+ Marken | 🟢 Stabil | Abo (kostenlose Testphase) |

---

## Optionale Bibliotheken

Folgende Provider benötigen zusätzliche Pakete, die nicht im Standard-Docker-Image enthalten sind:

```bash
pip install mytoyota       # Toyota / Lexus
pip install nissan-connect-ev  # Nissan Ariya / Leaf
pip install pyporsche      # Porsche Connect
pip install jlrpy          # Jaguar / Land Rover InControl
```

---

## Home Assistant als universelle Fallback-Lösung

Home Assistant hat für fast alle Fahrzeugmarken eigene Integrationen (viele via HACS):

- **VW / Skoda / Seat / Audi**: [Volkswagen We Connect ID](https://github.com/mitch-dc/volkswagen_we_connect_id) (HACS)
- **Tesla**: [Tesla Integration](https://www.home-assistant.io/integrations/tesla_wall_connector/) (offiziell)
- **BMW**: [BMW Connected Drive](https://www.home-assistant.io/integrations/bmw_connected_drive/) (offiziell)
- **Hyundai / Kia**: [Hyundai/Kia Connect](https://github.com/Hyundai-Kia-Connect/kia_uvo) (HACS)
- **Renault**: [MyRenault](https://www.home-assistant.io/integrations/renault/) (offiziell)
- **Polestar**: [Polestar EV](https://github.com/pypolestar/polestar_api) (HACS)
- **Toyota / Lexus**: [MyT](https://github.com/catsmanac/ha_toyota_na) (HACS)
- **MG / SAIC**: [SAIC MQTT Gateway](https://github.com/SAIC-iSmart-API/saic-mqtt-gateway) (HACS)

Wenn HA bereits installiert ist und das Fahrzeug dort eingebunden ist, ist **Home Assistant Provider** die empfohlene Wahl.

---

## Bekannte Probleme

### VW / WeConnect (Stand 2024)
VW hat den API-Zugang für Drittanwendungen im Rahmen der WeConnect ID-Plattform deutlich eingeschränkt.
- Verbindungsaufbau schlägt bei vielen Nutzern mit Authentifizierungsfehlern fehl
- 2FA-Pflicht erscheint im WeConnect-Portal und wird von teslapy/weconnect nicht unterstützt
- **Lösung**: Home Assistant mit der [Volkswagen We Connect ID HACS-Integration](https://github.com/mitch-dc/volkswagen_we_connect_id)

### Audi / MyAudi Connect
- Die alten `msg.volkswagen.de/fs-car/` Endpunkte wurden 2023 eingestellt
- Neue MyAudi-App nutzt anderes Backend — dieser Provider ist für neue Fahrzeuge nicht nutzbar
- **Lösung**: Home Assistant mit der [MyAudi HACS-Integration](https://github.com/arjenvrh/audi_connect_ha)

### API-Daten veraltet (VW, Stellantis, Toyota)
Manche APIs liefern Daten nur alle 15–60 Minuten. Kurze Ladevorgänge (< 15 Min.) können verpasst werden.
**Lösung**: Wallbox-/Zähler-Integration aktivieren (Konfiguration → Fahrzeuge → Zähler & Wallbox).
