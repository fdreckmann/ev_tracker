# EV Tracker — Claude Instructions

## 1. Think Before Coding

**Don't assume. Don't hide confusion. Surface tradeoffs.**

Before implementing:
- State assumptions explicitly. If uncertain, ask.
- If multiple interpretations exist, present them — don't pick silently.
- If a simpler approach exists, say so. Push back when warranted.
- If something is unclear, stop. Name what's confusing. Ask.

## 2. Simplicity First

**Minimum code that solves the problem. Nothing speculative.**

- No features beyond what was asked.
- No abstractions for single-use code.
- No "flexibility" or "configurability" that wasn't requested.
- No error handling for impossible scenarios.
- If you write 200 lines and it could be 50, rewrite it.

## 3. Surgical Changes

**Touch only what you must. Clean up only your own mess.**

When editing existing code:
- Don't "improve" adjacent code, comments, or formatting.
- Don't refactor things that aren't broken.
- Match existing style, even if you'd do it differently.
- If you notice unrelated dead code, mention it — don't delete it.

When your changes create orphans:
- Remove imports/variables/functions that YOUR changes made unused.
- Don't remove pre-existing dead code unless asked.

Every changed line must trace directly to the user's request.

## 4. Goal-Driven Execution

**Define success criteria. Loop until verified.**

For multi-step tasks, state a brief plan:
```
1. [Step] → verify: [check]
2. [Step] → verify: [check]
```

Tests must pass before and after every change.

## 5. Git Workflow

**Nach jeder abgeschlossenen Aufgabe nur auf `dev` pushen.**

Ablauf:
1. Commits auf Feature-Branch `claude/push-latest-version-XCxXw`.
2. `git fetch origin dev`
3. Einmergen und pushen:
   ```bash
   git checkout -b dev-local origin/dev
   git merge claude/push-latest-version-XCxXw --no-edit
   git push origin dev-local:dev
   git checkout claude/push-latest-version-XCxXw
   git branch -d dev-local
   ```
4. Tests müssen vor dem Push grün sein (218+ passed).

**Niemals auf `main` pushen — auch nicht mit `git push origin HEAD:main`.**

Nach jedem Task: nur Feature-Branch + `dev`. Kein Push auf `main`, auch nicht als "sync".

## 6. Security Constraints

- Keine beliebigen file:// URLs, keine lokalen Pfad-Traversals.
- Keine SVGs als Upload erlauben.
- Remote-Bilder nur serverseitig abrufen, Größe begrenzen.
- Keine HA/API-Tokens an den Browser ausgeben.
- OAuth Client Secrets und Refresh Tokens niemals im Klartext anzeigen.
- API-Tokens: raw token nur einmalig bei Erstellung anzeigen, als SHA-256-Hash speichern.
- Leere oder '`********`' Passwortfelder dürfen gespeicherte Secrets nicht überschreiben.
- Notification-Tokens (`ntfy_token`, `gotify_token`, `telegram_bot_token`) niemals im Klartext zurückgeben — in `_SECRET_GLOBAL_KEYS`.
- `enbw_api_subscription_key` nur wenn explizit aktiviert, niemals im Klartext.
- Keine bestehenden Config-Keys, DB-Felder oder API-Routen entfernen.
- Kein Docker-Socket-Mount, kein In-App-Update, keine Shell-Kommandos aus der App heraus.
- Nur fest konfigurierte GitHub-/Update-URLs verwenden — keine User-supplied URLs abrufen.
