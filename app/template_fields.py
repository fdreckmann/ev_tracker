# TABLE_FIELDS: columns in the data table
TABLE_FIELDS = {
    "row_num":       {"label": "Nr.",              "type": "int",      "synonyms": ["nr", "lfd", "#", "position", "pos", "lfd. nr"]},
    "date":          {"label": "Datum",             "type": "date",     "synonyms": ["datum", "date", "ladedatum", "ladedat.", "dat."]},
    "start_time":    {"label": "Startzeit",         "type": "time",     "synonyms": ["start", "beginn", "von", "startzeit", "ladestart", "ab"]},
    "end_time":      {"label": "Endzeit",           "type": "time",     "synonyms": ["ende", "end", "bis", "endzeit", "ladeende"]},
    "duration":      {"label": "Ladedauer",         "type": "duration", "synonyms": ["dauer", "ladedauer", "ladezeit", "duration", "zeitdauer"]},
    "duration_hours":   {"label": "Ladedauer (h)",       "type": "number",   "synonyms": ["dauer h", "dauer stunden", "ladezeit h", "stunden"]},

    # Calculated average charging power (kWh ÷ hours). Distinct from installed charger rating.
    "charge_power_kw":  {"label": "Ø Ladeleistung berech. (kW)", "type": "number",
                         "help": "Berechnung: geladene kWh geteilt durch Ladedauer in Stunden.",
                         "synonyms": [
        "berechnete ladeleistung", "durchschnittliche ladeleistung", "ø ladeleistung",
        "ladeleistung berechnet", "durchschnittsleistung",
        "avg charging power", "average charging power", "average charging speed",
        "kwh per hour", "kwh/h", "kwh pro stunde",
        "charging power", "charging speed",
    ]},

    # Installed wallbox/charge-point rating (fixed kW, e.g. 11 or 22 kW).
    # Keywords with kW/Stunde, kW/h, Wallbox, Ladepunkt → prefer this field.
    "charger_power_kw": {"label": "Ladepunkt-/Wallbox-Leistung (kW)", "type": "number",
                         "help": "Installierte Wallbox-/Ladepunkt-Leistung (z. B. 11 oder 22 kW).",
                         "synonyms": [
        "wallbox leistung", "wallbox-leistung", "ladepunkt leistung", "ladepunkt-leistung",
        "ladegeschwindigkeit kw/stunde", "ladegeschwindigkeit kw/h",
        "ladegeschwindigkeit kwh/stunde", "ladegeschwindigkeit kw",
        "ladegeschwindigkeit", "ladeleistung kw", "leistung kw", "ladepunkt kw",
        "anschlussleistung", "nennleistung", "nennleistung wallbox",
        "11kw", "22kw", "11 kw", "22 kw",
        "charger power", "charge point power", "wallbox power", "rated power",
    ]},

    "odo_start":     {"label": "KM-Stand Start",    "type": "number",   "synonyms": ["km start", "km-start", "start km", "kilometerstand start", "km anfang", "odometer start", "tachostand start"]},
    "odo_end":       {"label": "KM-Stand Ende",     "type": "number",   "synonyms": ["km ende", "km-ende", "end km", "kilometerstand", "kilometerstand ende", "km end", "tachostand"]},
    "soc_start":     {"label": "SOC Start (%)",     "type": "percent",  "synonyms": ["soc start", "soc anfang", "akku start", "ladezustand start", "batterie start", "soc von", "ladestand start"]},
    "soc_end":       {"label": "SOC Ende (%)",      "type": "percent",  "synonyms": ["soc ende", "soc end", "akku ende", "ladezustand ende", "batterie ende", "soc bis", "ladestand ende"]},
    "kwh_charged":   {"label": "Geladene kWh",      "type": "number",   "synonyms": ["kwh", "geladen", "energie", "energy", "lademenge", "geladene energie", "lademenge kwh", "kwh geladen", "stromverbrauch"]},
    "cost_eur":      {"label": "Kosten (€)",        "type": "currency", "synonyms": ["kosten", "cost", "betrag", "ladekosten", "eur", "€", "preis", "gesamtbetrag", "summe", "betrag eur"]},
    "price_per_kwh": {"label": "Preis/kWh",         "type": "currency", "synonyms": ["preis pro kwh", "kosten pro kwh", "kwh preis", "strompreis", "tarif"]},
    "meter_old":     {"label": "Zählerstand Alt",   "type": "number",   "synonyms": ["zähler alt", "zählerstand alt", "zähler anfang", "counter start", "meter start", "zählerstand vorher", "zähler von"]},
    "meter_new":     {"label": "Zählerstand Neu",   "type": "number",   "synonyms": ["zähler neu", "zählerstand neu", "zähler ende", "counter end", "meter end", "zählerstand nachher", "zähler bis"]},
    "location":      {"label": "Standort",          "type": "text",     "synonyms": ["standort", "ort", "location", "ladeort", "ladepunkt", "ladestation", "adresse"]},
    "charger_type":  {"label": "Ladeart",           "type": "text",     "synonyms": ["ladeart", "ladetyp", "ac/dc", "charger type", "lademodus", "typ"]},
}

# Synonym priority rules: when a header matches both charge_power_kw and charger_power_kw,
# these keywords force charger_power_kw (installed rating, not calculated average).
CHARGER_POWER_PRIORITY_KEYWORDS = {
    "kw/stunde", "kwh/stunde", "kw/h", "wallbox", "ladepunkt", "anschluss",
    "nennleistung", "11kw", "22kw", "11 kw", "22 kw", "rated", "charge point",
}

# HEADER_FIELDS: single-cell values (header/footer area)
HEADER_FIELDS = {
    "fahrer":                   {"label": "Fahrer",                 "synonyms": ["fahrer", "name", "mitarbeiter", "driver", "nutzer", "benutzer", "person"]},
    "kennzeichen":              {"label": "Kennzeichen",            "synonyms": ["kennzeichen", "kfz", "kfz-kennzeichen", "fahrzeug", "nummernschild", "vehicle", "auto", "pkw"]},
    "abteilung":                {"label": "Abteilung",              "synonyms": ["abteilung", "department", "bereich", "team", "gruppe"]},
    "kostenstelle":             {"label": "Kostenstelle",           "synonyms": ["kostenstelle", "cost center", "kst", "kostcenter"]},
    "month_year":               {"label": "Monat/Jahr",             "synonyms": ["monat", "abrechnungsmonat", "periode", "zeitraum", "month", "abrechnungszeitraum", "monat/jahr"]},
    "export_date":              {"label": "Exportdatum",            "synonyms": ["exportdatum", "erstellt am", "datum der erstellung", "erstellungsdatum", "ausgabedatum", "druckdatum"]},
    "total_sessions":           {"label": "Anzahl Ladevoergänge",    "synonyms": ["anzahl", "ladevoergänge gesamt", "summe ladevoergänge", "anzahl ladevoergänge", "ladeanzahl"]},
    "total_kwh":                {"label": "Gesamt kWh",             "synonyms": ["gesamt kwh", "gesamtenergie", "gesamt energie", "kwh gesamt", "total kwh", "gesamtverbrauch"]},
    "total_cost":               {"label": "Gesamtkosten",           "synonyms": ["gesamtkosten", "total kosten", "kosten gesamt", "gesamtbetrag", "total cost", "summe kosten"]},
    "total_home_kwh":           {"label": "Home kWh",               "synonyms": ["home kwh", "zuhause kwh", "kwh zuhause", "heimladen kwh", "ac kwh"]},
    "total_external_kwh":       {"label": "Extern kWh",             "synonyms": ["extern kwh", "öffentlich kwh", "kwh extern", "dc kwh", "fremdladen kwh"]},
    "total_home_cost":          {"label": "Home Kosten",            "synonyms": ["home kosten", "zuhause kosten", "heimladen kosten", "kosten zuhause"]},
    "total_external_cost":      {"label": "Extern Kosten",          "synonyms": ["extern kosten", "öffentlich kosten", "fremdladen kosten"]},
    "total_km":                 {"label": "Gesamte KM",             "synonyms": ["gesamt km", "km gesamt", "gefahrene km", "kilometer gesamt", "strecke gesamt"]},
    "avg_consumption_kwh_100km": {"label": "Verbrauch kWh/100km",  "synonyms": ["verbrauch", "kwh/100km", "durchschnittsverbrauch", "stromverbrauch pro 100km", "kwh je 100km"]},
    "meter_start_value":        {"label": "Zählerstand Anfang",     "synonyms": ["zählerstand anfang", "zähler anfangswert", "meter anfang", "anfangszählerstand", "zähler start"]},
    "meter_end_value":          {"label": "Zählerstand Ende",       "synonyms": ["zählerstand ende", "zähler endwert", "meter ende", "endzählerstand", "zähler ende"]},
    "month_name":               {"label": "Monatsname",              "synonyms": ["monatsname", "month name", "monat text"]},
    "export_period":            {"label": "Abrechnungszeitraum",     "synonyms": ["zeitraum", "abrechnungszeitraum", "period", "laufzeit"]},
    "total_charging_hours":     {"label": "Ladezeit gesamt (h)",     "synonyms": ["ladezeit gesamt", "total ladezeit", "ladestunden gesamt", "charging hours"]},
    "avg_charge_power_kw":      {"label": "Ø Ladeleistung / Ladegeschwindigkeit (kW)",
                                 "help": "Berechnung: geladene kWh geteilt durch Ladedauer in Stunden.",
                                 "synonyms": [
        "durchschnittliche ladeleistung", "ø ladeleistung", "avg power", "mittlere ladeleistung",
        "durchschnittliche ladegeschwindigkeit", "ø ladegeschwindigkeit",
        "avg charging speed", "average charging power",
    ]},
}
