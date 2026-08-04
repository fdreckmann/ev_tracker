# Wartung: Fehlerhafte Zählerstände einzelner Legacy-Sessions bereinigen

Vor dem EVCC-Fix in Version 2.4.0 konnte `chargedEnergy` (Session-Energie)
fälschlich als kumulativer Zählerstand gespeichert werden — sichtbar an
Sessions mit `meter_old = 1` (oder einem anderen unplausibel kleinen Wert)
und leerem `meter_new`.

Der Code-Fix in 2.4.0 korrigiert nur zukünftige Sessions. Bereits gespeicherte
fehlerhafte Datensätze werden **nicht automatisch migriert** — eine
automatische Massen-Migration könnte gültige Daten verändern, da ein kleiner
`meter_old`-Wert nicht in jedem Fall auf diesen Bug zurückgeht.

## Betroffene Session identifizieren

```sql
SELECT id, start_ts, end_ts, location, meter_old, meter_new,
       meter_delta_kwh, meter_source_start, provider
FROM sessions
WHERE meter_old IS NOT NULL AND meter_new IS NULL
  AND meter_source_start = 'evcc'
ORDER BY start_ts DESC;
```

Prüfen, ob `meter_old` plausibel zur restlichen Session passt (z. B. deutlich
kleiner als der übliche Zählerstand des Hauses/Wallbox). Nur eindeutig
betroffene Zeilen bereinigen — im Zweifel die Session-ID mit dem Nutzer
abstimmen, bevor sie geändert wird.

## Eine konkrete Session bereinigen

Die Session-ID **muss** vorher eindeutig bestimmt sein (siehe oben). Diese
Anweisung setzt nur die Zählerfelder zurück — `kwh_charged`, `cost_eur` und
alle übrigen Felder bleiben unverändert, da sie in der Regel weiterhin aus
der SOC-Berechnung korrekt sind:

```sql
UPDATE sessions
SET meter_old = NULL,
    meter_new = NULL,
    meter_delta_kwh = NULL,
    meter_used = 0,
    meter_skipped_reason = 'legacy_evcc_charged_energy'
WHERE id = ?;   -- konkrete Session-ID einsetzen, z.B. WHERE id = 4711
```

`meter_skipped_reason = 'legacy_evcc_charged_energy'` dokumentiert im
Datensatz selbst, dass der Zählerstand bewusst wegen dieses Bugs entfernt
wurde (nicht wegen eines echten Lesefehlers).

## Ausführung

Über die SQLite-CLI gegen die Produktions-DB (Pfad je nach Deployment,
typischerweise `/data/sessions.db`):

```bash
sqlite3 /data/sessions.db "UPDATE sessions SET meter_old=NULL, meter_new=NULL, meter_delta_kwh=NULL, meter_used=0, meter_skipped_reason='legacy_evcc_charged_energy' WHERE id=4711;"
```

Vor der Ausführung: Backup des `/data`-Verzeichnisses erstellen (siehe
Konfiguration → Backup in der App, oder manuelles Kopieren der DB-Datei).

**Es gibt bewusst keine automatische Migration für diesen Fall** — nur
gezielt einzelne, manuell geprüfte Session-IDs bereinigen.
