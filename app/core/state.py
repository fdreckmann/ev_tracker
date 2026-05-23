"""
Shared mutable runtime state — vehicle tracking, update log, export tokens, caches.

This module holds module-level dicts/lists that are shared between the
tracker loop (server.py) and blueprint route handlers. Python's module
system guarantees a single instance per process.

Usage:
    from core.state import vehicle_states, vehicle_states_lock, vehicle_stops
"""
import threading

# Vehicle tracking state — populated by server.py at startup
vehicle_states: dict = {}        # vid -> state dict from _make_state()
vehicle_states_lock = threading.Lock()
vehicle_stops: dict = {}         # vid -> threading.Event

# PDF export tokens: token -> {"bytes": ..., "filename": ..., "expires": float}
pdf_tokens: dict = {}

# Update log lines shown in the UI
update_log: list = []
update_lock = threading.Lock()
update_thread = None             # set by server.py when an update is running

# ENTSO-E spot price cache — written by server.py, readable by routes
entsoe_cache: dict = {"price": None, "ts": 0}
