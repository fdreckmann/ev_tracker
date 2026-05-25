"""
Gunicorn configuration for EV Tracker.

Single worker with multiple threads: the app uses background tracker threads
that must not be duplicated across worker processes.
"""
import os

bind    = "0.0.0.0:8080"
workers = 1          # MUST stay 1 — background tracker threads are process-local
threads = 4          # Handle concurrent HTTP requests within the single worker
timeout = 120
keepalive = 5
accesslog = "-"
errorlog  = "-"
loglevel  = os.getenv("LOG_LEVEL", "info")


def post_fork(server, worker):
    """Start the EV Tracker background threads after gunicorn forks the worker process."""
    try:
        import sys, os as _os
        sys.path.insert(0, _os.path.dirname(__file__))
        from server import ensure_started_once
        ensure_started_once()
    except Exception as exc:
        server.log.warning("ensure_started_once() failed: %s", exc)
