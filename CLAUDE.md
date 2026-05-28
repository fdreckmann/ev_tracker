# EV Tracker — Claude Instructions

## Git Workflow

**Nach jeder abgeschlossenen Aufgabe immer auf `dev` pushen.**

Ablauf:
1. Commits auf dem Feature-Branch `claude/push-latest-version-XCxXw` machen.
2. `git fetch origin dev` holen.
3. Lokalen dev-Stand erstellen und Feature-Branch einmergen:
   ```bash
   git checkout -b dev-local origin/dev
   git merge claude/push-latest-version-XCxXw --no-edit
   git push origin dev-local:dev
   git checkout claude/push-latest-version-XCxXw
   git branch -d dev-local
   ```
4. Tests müssen vor dem Push grün sein (218+ passed).

**Niemals direkt auf `main` pushen.**
