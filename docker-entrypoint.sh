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
    # Mode B: running as root — chown /data, then drop to PUID:PGID
    PUID="${PUID:-10001}"
    PGID="${PGID:-100}"
    chown -R "$PUID:$PGID" "$DATA_DIR" 2>/dev/null || true
    exec gosu "$PUID:$PGID" gunicorn server:app -c gunicorn.conf.py "$@"
fi

# Mode A: already non-root (user: set in compose or equivalent)
exec gunicorn server:app -c gunicorn.conf.py "$@"
