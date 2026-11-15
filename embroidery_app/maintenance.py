"""Periodic clean-up inside the app process (no external scheduler needed):

- delete temporary projects that are no longer in use (temp_projects.sweep);
- cancel generation reservations of requests that never finished (usage.cancel_stale);
- record subscriptions that reached their end date (subscriptions.expire_due);
- forget expired login sessions;
- once a day: delete usage records and audit entries past their retention period.

``start()`` runs ``run_once`` every ``INTERVAL_S`` on a daemon thread; ``stop()`` ends it.
Each step is isolated, so one failing step never stops the others.
"""

from __future__ import annotations

import logging
import threading
import time

from . import app_settings, audit, db, subscriptions, temp_projects, usage

log = logging.getLogger("embroidery")
INTERVAL_S = 300
_stop = threading.Event()
_thread: threading.Thread | None = None
_last_retention = 0.0


def run_once(now: float | None = None, retention: bool | None = None) -> dict:
    global _last_retention
    now = now or time.time()
    done: dict[str, int] = {}

    def step(name, fn):
        try:
            done[name] = fn()
        except Exception:  # keep going; the next run tries again
            log.exception("Maintenance step %s failed", name)

    step("sessions", lambda: _purge_sessions(now))
    step("projects", lambda: temp_projects.sweep(now))
    step("stale_generations", lambda: usage.cancel_stale(now))
    step("expired_subscriptions", lambda: subscriptions.expire_due(now))
    if retention or (retention is None and now - _last_retention > 86400):
        _last_retention = now
        step("usage_purged", lambda: usage.purge(app_settings.get("usage_retention_days")))
        step("audit_purged", lambda: audit.purge(app_settings.get("audit_retention_days")))
    return done


def _purge_sessions(now: float) -> int:
    # projects of these sessions are deleted by the sweep right after (no session = not in use)
    with db.tx() as c:
        return c.execute("DELETE FROM sessions WHERE expires_at <= ?", (now,)).rowcount


def _loop() -> None:
    while not _stop.wait(INTERVAL_S):
        run_once()


def start() -> None:
    global _thread
    run_once()
    if _thread is None or not _thread.is_alive():
        _stop.clear()
        _thread = threading.Thread(target=_loop, name="maintenance", daemon=True)
        _thread.start()


def stop() -> None:
    _stop.set()
