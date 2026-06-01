#!/bin/sh
# Three startup modes (in priority order):
#
#   1) root + PUID/PGID set — chown /data, drop to PUID:PGID via gosu.
#      Requires CAP_SETUID + CAP_SETGID. Recommended for Unraid.
#
#   2) root, no PUID/PGID — run as root. Works everywhere (Synology default).
#      /data is always writable.
#
#   3) user: set in compose — container starts as that user directly.
#      /data must already be owned by that user.
#
set -e

DATA_DIR="${DATA_DIR:-/data}"

if [ "$(id -u)" = "0" ]; then
    if [ -n "$PUID" ] && [ -n "$PGID" ]; then
        # Mode 1: root + PUID/PGID — chown /data, drop privileges via gosu
        chown -R "$PUID:$PGID" "$DATA_DIR" 2>/dev/null || true
        if ! gosu "$PUID:$PGID" test -w "$DATA_DIR" 2>/dev/null; then
            echo "ERROR: $DATA_DIR is not writable for UID $PUID after chown" >&2
            echo "Tipp: PUID/PGID aus .env entfernen um als root zu laufen (Mode 2, Synology-kompatibel)" >&2
            exit 1
        fi
        exec gosu "$PUID:$PGID" gunicorn server:app -c gunicorn.conf.py "$@"
    else
        # Mode 2: root without PUID/PGID — run as root (Synology, simple setups)
        exec gunicorn server:app -c gunicorn.conf.py "$@"
    fi
fi

# Mode 3: already non-root (user: set in compose)
if ! test -w "$DATA_DIR" 2>/dev/null; then
    _UID="$(id -u)"
    _GID="$(id -g)"
    echo "ERROR: $DATA_DIR is not writable for current user (UID $_UID)" >&2
    echo "Fix: chown -R $_UID:$_GID <pfad-zu-appdata>" >&2
    exit 1
fi

exec gunicorn server:app -c gunicorn.conf.py "$@"
