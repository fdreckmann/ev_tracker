#!/bin/sh
# Entrypoint supports two privilege modes:
#
#   B) root start (default, recommended) — entrypoint fixes /data ownership
#      via chown, then drops to PUID:PGID using gosu.
#      Requires CAP_SETUID + CAP_SETGID (set via cap_add in docker-compose.yml).
#
#   A) user: set in compose — container starts directly as PUID:PGID,
#      /data must already be owned by that user. cap_drop: ALL is safe.
#
set -e

DATA_DIR="${DATA_DIR:-/data}"

if [ "$(id -u)" = "0" ]; then
    # Mode B: running as root — fix /data ownership, then drop privileges
    PUID="${PUID:-10001}"
    PGID="${PGID:-100}"
    chown -R "$PUID:$PGID" "$DATA_DIR" 2>/dev/null || true
    if ! gosu "$PUID:$PGID" test -w "$DATA_DIR" 2>/dev/null; then
        echo "ERROR: $DATA_DIR is not writable for UID $PUID after chown" >&2
        echo "Ensure CAP_SETUID and CAP_SETGID are available (cap_add in docker-compose.yml)" >&2
        exit 1
    fi
    exec gosu "$PUID:$PGID" gunicorn server:app -c gunicorn.conf.py "$@"
fi

# Mode A: already non-root (user: set in compose or equivalent)
if ! test -w "$DATA_DIR" 2>/dev/null; then
    _UID="$(id -u)"
    _GID="$(id -g)"
    echo "ERROR: $DATA_DIR is not writable for current user (UID $_UID)" >&2
    echo "" >&2
    echo "Option 1 — wechsle zu Mode B (empfohlen): 'user:' aus docker-compose.yml entfernen," >&2
    echo "           PUID=$_UID PGID=$_GID in .env setzen, cap_add: [SETUID, SETGID] setzen." >&2
    echo "" >&2
    echo "Option 2 — /data einmalig auf dem Host fixen:" >&2
    echo "           chown -R $_UID:$_GID /mnt/user/appdata/ev-tracker" >&2
    exit 1
fi

exec gunicorn server:app -c gunicorn.conf.py "$@"
