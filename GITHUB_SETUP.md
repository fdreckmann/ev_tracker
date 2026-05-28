# EV Tracker — GitHub Actions Setup

Diese Anleitung beschreibt, wie GitHub Actions den Docker Build und Push auf Docker Hub automatisiert.

## Voraussetzungen

- GitHub-Repository: `fdreckmann/ev_tracker`
- Docker Hub Account mit Repository `19121412/ev-tracker`

## Schritt 1 — Docker Hub Access Token erstellen

1. hub.docker.com → Account Settings → **Security**
2. **"New Access Token"** → Name: `github-actions` → Permissions: **Read & Write**
3. Token kopieren

## Schritt 2 — Secrets in GitHub hinterlegen

GitHub → Repository → **Settings** → **Secrets and variables** → **Actions**:

| Secret | Wert |
|--------|------|
| `DOCKERHUB_USERNAME` | `19121412` |
| `DOCKERHUB_TOKEN` | Access Token aus Schritt 1 |

## Schritt 3 — Workflow

Der Workflow `.github/workflows/docker-build.yml` läuft automatisch:

| Trigger | Tag |
|---------|-----|
| Push auf `main` | `:beta`, `:main-{sha}` |
| Push auf `dev` | `:dev`, `:dev-{sha}` |
| Push auf `test` | `:beta`, `:beta-{sha}` |
| Git-Tag `v*` (z.B. `v2.0.53`) | `:latest`, `:v2.0.53` |
| Nightly (02:00 UTC) | `:nightly` |

> **Wichtig:** `:latest` wird nur bei einem expliziten Git-Tag `v*` erzeugt — nicht bei jedem Push auf `main`.

## Schritt 4 — Stable Release erstellen

```bash
# Version in version.json und update-info.json anpassen
# channel auf "stable" setzen
git tag v2.0.53
git push origin v2.0.53
```

GitHub Actions baut dann automatisch `:latest` und `:v2.0.53`.

## Unraid CA Template

Das Unraid Community Apps Template wird **separat** gepflegt:

➡ https://github.com/fdreckmann/ev-tracker-unraid-app

Es muss nicht bei einem App-Release manuell synchronisiert werden.
