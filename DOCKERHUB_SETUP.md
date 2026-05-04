# EV Tracker — Docker Hub Setup

## Schritt 1 — Docker Hub Account erstellen

1. hub.docker.com → **"Sign up"**
2. Username wählen (z.B. `19121412`) → wird Teil des Image-Namens
3. Email bestätigen

---

## Schritt 2 — Repository auf Docker Hub erstellen

1. hub.docker.com → **"Create Repository"**
2. Name: `ev-tracker`
3. Visibility: **Public**
4. **"Create"**

---

## Schritt 3 — GitHub Actions einrichten (automatisch bauen & pushen)

Das ist der einfachste Weg — jedes Mal wenn du Code auf GitHub pushst,
wird das Docker Image automatisch gebaut und auf Docker Hub hochgeladen.

### 3a — Docker Hub Token erstellen

1. hub.docker.com → Account Settings → **Security**
2. **"New Access Token"**
3. Name: `github-actions`
4. Permissions: **Read & Write**
5. Token kopieren

### 3b — Token in GitHub hinterlegen

1. github.com/19121412/ev_tracker → **Settings**
2. **Secrets and variables** → **Actions**
3. **"New repository secret"**:
   - Name: `DOCKERHUB_USERNAME` → Wert: `19121412`
4. Noch ein Secret:
   - Name: `DOCKERHUB_TOKEN` → Wert: dein Token aus 3a

### 3c — GitHub Actions Workflow (bereits im Repo enthalten)

Die Datei `.github/workflows/docker-build.yml` ist bereits im Projekt.
Bei jedem Push auf `main` wird automatisch:
1. Image gebaut
2. Auf Docker Hub als `19121412/ev-tracker:latest` gepusht

---

## Schritt 4 — Container in Unraid hinzufügen

Unraid → Docker → **"Add Container"**:

| Feld | Wert |
|------|------|
| Name | `ev-tracker` |
| Repository | `19121412/ev-tracker:latest` |
| Port | `8054` → `8080` |
| Path `/data` | `/mnt/user/appdata/ev-tracker` |
| Variable `DATA_DIR` | `/data` |
| Variable `TZ` | `Europe/Berlin` |

---

## Schritt 5 — Auto Update in Unraid

Unraid → Docker → Container `ev-tracker` → **"Edit"**:
- **"Auto Update"**: Aktivieren ✅

Oder über **CA Auto Update Plugin**:
- Apps → CA Auto Update installieren
- ev-tracker dort aktivieren

Ab jetzt: Wenn du Code auf GitHub pushst → GitHub Actions baut → Docker Hub bekommt neues Image → Unraid updated automatisch!

---

## Update-Workflow ab jetzt

1. Code ändern (auf PC oder direkt auf GitHub.com)
2. Auf GitHub pushen (oder per GitHub Desktop)
3. GitHub Actions baut automatisch (~3 Min)
4. Unraid zieht automatisch das neue Image (je nach Auto-Update Intervall)

