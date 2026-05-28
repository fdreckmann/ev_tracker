# EV Tracker — Docker Hub Setup

## Repository

Das offizielle Docker Image liegt auf Docker Hub:

```
19121412/ev-tracker
```

## Tags

| Tag | Beschreibung |
|-----|-------------|
| `latest` | Stabile Releases (nur bei Git-Tag `v*`) |
| `beta` | Main-Branch (aktueller Entwicklungsstand) |
| `dev` | Dev-Branch |
| `nightly` | Automatischer Build (täglich 02:00 UTC) |

## Einrichten (Contributor / Fork)

### Docker Hub Token

1. hub.docker.com → Account Settings → **Security**
2. **"New Access Token"** → Permissions: **Read & Write**
3. Token kopieren

### GitHub Secrets setzen

GitHub → Repository → **Settings** → **Secrets and variables** → **Actions**:

| Secret | Wert |
|--------|------|
| `DOCKERHUB_USERNAME` | Dein Docker Hub Username |
| `DOCKERHUB_TOKEN` | Access Token aus Schritt oben |

### Workflow

Builds laufen automatisch über `.github/workflows/docker-build.yml`.

Vor dem Docker Build läuft immer:
1. `python -m compileall -q app tests`
2. `node --check app/static/js/*.js`
3. `python -m pytest -q`

Ein Testfehler verhindert den Docker Push.

## Unraid CA Template

Das Unraid Community Apps Template wird **separat** gepflegt:

➡ https://github.com/fdreckmann/ev-tracker-unraid-app
