#!/bin/sh
# Entrypoint: fix /data ownership so the non-root evtracker user can access it,
# then exec gunicorn as that user.
set -e

DATA_DIR="${DATA_DIR:-/data}"

# If /data is not writable by the current user (likely because the host volume
# is owned by root), fix ownership. This runs as root before USER directive.
if [ ! -w "$DATA_DIR" ]; then
    chown -R evtracker:users "$DATA_DIR" 2>/dev/null || true
fi

# Drop privileges and start gunicorn
exec gosu evtracker gunicorn server:app -c gunicorn.conf.py "$@"
