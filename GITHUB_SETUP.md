# EV Tracker — GitHub Setup Anleitung

## Schritt 1 — Personal Access Token erstellen

1. GitHub.com → oben rechts dein Profilbild → **Settings**
2. Ganz unten links → **Developer settings**
3. **Personal access tokens** → **Tokens (classic)**
4. **"Generate new token (classic)"**
5. Einstellungen:
   - Note: `ev-tracker-unraid`
   - Expiration: `No expiration` (oder 1 Jahr)
   - Scope: nur **`repo`** ankreuzen
6. **"Generate token"** → Token kopieren und sicher speichern!

---

## Schritt 2 — Privates Repository erstellen

1. GitHub.com → oben rechts **"+"** → **"New repository"**
2. Einstellungen:
   - Repository name: `ev-tracker`
   - Visibility: **Private** ← wichtig!
   - README: **nicht** ankreuzen
3. **"Create repository"**

---

## Schritt 3 — Lokalen Code auf GitHub hochladen

Auf deinem PC im entpackten ZIP-Ordner (ev-tracker-v4):

```bash
cd /pfad/zum/ev-tracker-v4

git init
git add .
git commit -m "Initial commit — EV Tracker v4"
git branch -M main
git remote add origin https://github.com/DEIN-USERNAME/ev-tracker.git
git push -u origin main
```

Beim Push nach Benutzername + Passwort gefragt:
- Benutzername: dein GitHub Username
- Passwort: **der Token aus Schritt 1** (nicht dein GitHub Passwort!)

---

## Schritt 4 — Unraid einrichten

Im Unraid Terminal:

```bash
# Git installieren (falls nicht vorhanden)
which git || (echo "Git nicht gefunden — in Unraid Terminal: nerd-tools installieren")

# Repository klonen
cd /mnt/user/appdata
git clone https://DEIN-USERNAME:DEIN-TOKEN@github.com/DEIN-USERNAME/ev-tracker.git ev-tracker-src

# Token dauerhaft speichern
cd ev-tracker-src
git config credential.helper store
```

---

## Schritt 5 — Erstes Build & Start

```bash
cd /mnt/user/appdata/ev-tracker-src
docker build -t ev-tracker:latest .
docker run -d --name ev-tracker --restart unless-stopped \
  -p 8054:8080 \
  -v /mnt/user/appdata/ev-tracker:/data \
  -e DATA_DIR=/data \
  -e TZ=Europe/Berlin \
  ev-tracker:latest
```

---

## Update-Workflow (ab jetzt immer so)

```bash
cd /mnt/user/appdata/ev-tracker-src && \
git pull && \
docker build -t ev-tracker:latest . && \
docker stop ev-tracker && docker rm ev-tracker && \
docker run -d --name ev-tracker --restart unless-stopped \
  -p 8054:8080 \
  -v /mnt/user/appdata/ev-tracker:/data \
  -e DATA_DIR=/data \
  -e TZ=Europe/Berlin \
  ev-tracker:latest
```

**Daten bleiben erhalten** — nur der Code wird aktualisiert.

---

## Änderungen pushen (wenn du Code angepasst hast)

```bash
cd /mnt/user/appdata/ev-tracker-src
git add .
git commit -m "Beschreibung der Änderung"
git push
```

---

## .gitignore (bereits im Projekt enthalten)

Folgende Dateien werden nicht auf GitHub hochgeladen:
- `__pycache__/`
- `*.pyc`
- `/data/` (Datenbank, Config, Backups)
