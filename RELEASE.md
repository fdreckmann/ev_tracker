# EV Tracker — Release-Anleitung

Diese Anleitung beschreibt den Ablauf für ein Produktions-Release.

---

## Voraussetzungen

- Alle Änderungen sind auf `dev` gemergt und getestet (CI grün)
- `version.json`, `update-info.json`, `CHANGELOG.md`, `README.md` und `build-info.json`
  zeigen die neue Versionsnummer
- `/data` bzw. der Docker-Appdata-Ordner ist gesichert (siehe Backup-Hinweis)

---

## 1. Backup vor dem Update

**Vor jedem Produktions-Update** sollte `/data` gesichert werden.

```bash
# Allgemein (Docker-Host)
tar -czf ev-tracker-backup-$(date +%F).tgz /pfad/zu/appdata/ev-tracker

# Unraid-Beispiel
tar -czf ev-tracker-backup-$(date +%F).tgz /mnt/user/appdata/ev-tracker
```

---

## 2. Release vorbereiten (main-Branch)

```bash
git checkout main
git pull origin main

# dev mergen (enthält alle Release-Änderungen)
git merge origin/dev --no-edit

# Release-Commit
git add version.json update-info.json CHANGELOG.md README.md build-info.json
git commit -m "Release v2.1.0"
git push origin main
```

> **Hinweis:** Ein Push auf `main` baut das Docker-Image mit dem Tag `stable`.
> Das `latest`-Tag und der versionierte Tag (`v2.1.0`) werden **nur durch einen Git-Tag** ausgelöst.

---

## 3. Release taggen — Docker-Prod auslösen

```bash
git tag -a v2.1.0 -m "EV Tracker v2.1.0"
git push origin v2.1.0
```

GitHub Actions baut dann automatisch:

| Docker-Tag | Bedeutung |
|---|---|
| `19121412/ev-tracker:v2.1.0` | Versionierter Prod-Tag |
| `19121412/ev-tracker:stable` | Stabiler Kanal |
| `19121412/ev-tracker:latest` | Standard-Tag für `docker compose pull` |

Der Workflow ist in `.github/workflows/docker-build.yml` definiert.

---

## 4. Prod-Deployment aktualisieren

### Option A — Versionierter Tag (empfohlen)

In `docker-compose.yml` den Image-Tag fest pinnen:

```yaml
services:
  ev-tracker:
    image: 19121412/ev-tracker:v2.1.0
```

Dann deployen:

```bash
docker compose pull
docker compose up -d
docker image prune -f
```

### Option B — latest

```yaml
services:
  ev-tracker:
    image: 19121412/ev-tracker:latest
```

```bash
docker compose pull
docker compose up -d
docker image prune -f
```

> Fest gepinnte Versions-Tags (`v2.1.0`) werden empfohlen, da versehentliche
> Breaking-Change-Updates durch `docker compose pull` ausgeschlossen werden.

---

## 5. Nach dem Deployment prüfen

```bash
# Logs beobachten
docker logs -f ev-tracker

# Health-Check
curl http://SERVER-IP:8054/api/health
```

Erwartete Antwort (vereinfacht):

```json
{
  "overall_ok": true,
  "version": "2.1.0",
  "db_ok": true,
  "db_writable": true,
  "data_ok": true,
  "startup_error": null
}
```

Die Web-UI zeigt die Version unter **Konfiguration → Version & Update**.

---

## 6. Docker-Compose-Referenz

Minimale Produktionskonfiguration:

```yaml
services:
  ev-tracker:
    image: 19121412/ev-tracker:v2.1.0
    container_name: ev-tracker
    restart: unless-stopped
    environment:
      DATA_DIR: "/data"
      TZ: "Europe/Berlin"
      # EV_TRACKER_EXPOSURE: "external"  # Aktivieren hinter Reverse Proxy
    ports:
      - "8054:8080"
    volumes:
      - /mnt/user/appdata/ev-tracker:/data
    security_opt:
      - no-new-privileges:true
```

Unraid — `.env` für User-Mapping:

```bash
# /mnt/user/appdata/ev-tracker/.env
PUID=99
PGID=100
```

```yaml
    environment:
      PUID: "${PUID:-10001}"
      PGID: "${PGID:-100}"
    cap_add:
      - SETUID
      - SETGID
```

---

## 7. GitHub Actions — Workflow-Übersicht

| Trigger | Docker-Tags | Hinweis |
|---|---|---|
| `git push origin v*` | `latest`, `stable`, `vX.Y.Z` | **Prod-Release** |
| `git push origin main` | `stable` | Kein `latest`-Update |
| `git push origin dev` | `dev` | Entwicklungs-Image |
| PR / andere Branches | — | Nur Tests, kein Push |

**Für einen vollständigen Prod-Release ist zwingend ein Git-Tag (`v2.1.0`) erforderlich.**
Nur der Tag-Build setzt `latest`.
