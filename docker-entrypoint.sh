#!/bin/sh
# Entrypoint supports two privilege modes:
#
#   A) user: set in compose (recommended) — container starts as PUID:PGID,
#      entrypoint just execs gunicorn directly. cap_drop: ALL is safe.
#
#   B) root start (no user: in compose) — entrypoint fixes /data ownership
#      via chown, then drops to PUID:PGID using gosu. Requires CAP_SETUID +
#      CAP_SETGID (remove cap_drop: ALL or add cap_add: [SETUID, SETGID]).
set -e

DATA_DIR="${DATA_DIR:-/data}"

if [ "$(id -u)" = "0" ]; then
    # Mode B: running as root — chown /data to PUID:PGID, then drop privileges
    PUID="${PUID:-10001}"
    PGID="${PGID:-100}"
    chown -R "$PUID:$PGID" "$DATA_DIR" 2>/dev/null || true
    # Verify that the target user can actually write after chown
    if ! gosu "$PUID:$PGID" test -w "$DATA_DIR" 2>/dev/null; then
        echo "ERROR: $DATA_DIR is not writable for UID $PUID after chown" >&2
        echo "Fix on Unraid: chown -R 10001:100 /mnt/user/appdata/ev-tracker" >&2
        echo "Or set PUID=99 PGID=100 in .env for Unraid (nobody:users)" >&2
        exit 1
    fi
    exec gosu "$PUID:$PGID" gunicorn server:app -c gunicorn.conf.py "$@"
fi

# Mode A: already non-root (user: set in compose or equivalent)
# Verify /data is writable before starting to fail fast with a clear message.
if ! test -w "$DATA_DIR" 2>/dev/null; then
    echo "ERROR: $DATA_DIR is not writable for current user (UID $(id -u))" >&2
    echo "Fix: ensure /data is owned by UID $(id -u)" >&2
    echo "Unraid: chown -R 99:100 /mnt/user/appdata/ev-tracker  (for PUID=99)" >&2
    echo "Other:  chown -R 10001:100 /mnt/user/appdata/ev-tracker" >&2
    exit 1
fi

exec gunicorn server:app -c gunicorn.conf.py "$@"
