"""Application settings the Super Admin can change at run time (Admin > Settings).

Each setting is declared once in ``SETTINGS`` with its type, allowed range and default;
the database only stores values that differ from the default. Deployment-level settings
(secrets, paths, AI keys) stay in the environment (config.py) and are not editable here.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass

from . import db
from .config import settings as env_settings


@dataclass(frozen=True)
class Setting:
    key: str
    label: str
    help: str
    default: int
    minimum: int
    maximum: int
    group: str


SETTINGS: dict[str, Setting] = {s.key: s for s in (
    Setting("project_idle_minutes", "Delete an inactive design after (minutes)",
            "A design nobody has opened for this long is deleted from the server.", 120, 10, 24 * 60, "Privacy"),
    Setting("project_max_hours", "Delete every design after (hours)",
            "The longest a design is kept, even while it is in use.", 24, 1, 7 * 24, "Privacy"),
    Setting("project_release_grace_s", "Keep a closed design for (seconds)",
            "When a browser tab closes, its design is kept this long so a page reload can reopen it.", 120, 15, 3600, "Privacy"),
    Setting("session_max_days", "Longest \"keep me signed in\" (days)",
            "Sign-in screen choices are limited to this. Changing it does not shorten existing sessions.",
            max(1, min(env_settings.session_days, 365)), 1, 365, "Sign-in"),
    Setting("usage_retention_days", "Keep usage records for (days)",
            "Older usage records are deleted. Must cover at least a year for monthly limits and yearly trends.", 730, 400, 3650, "Retention"),
    Setting("audit_retention_days", "Keep the audit log for (days)", "Older audit entries are deleted.", 730, 90, 3650, "Retention"),
    Setting("approaching_limit_pct", "\"Approaching the limit\" from (%)",
            "Users who have used this share of their allowance are listed as approaching the limit.", 80, 50, 99, "Analytics"),
)}


def get(key: str) -> int:
    s = SETTINGS[key]
    with db.tx() as c:
        row = c.execute("SELECT value FROM app_settings WHERE key = ?", (key,)).fetchone()
    if not row:
        return s.default
    try:
        return max(s.minimum, min(s.maximum, int(json.loads(row["value"]))))
    except (TypeError, ValueError):
        return s.default


def all_settings() -> list[dict]:
    with db.tx() as c:
        stored = {r["key"]: r for r in c.execute("SELECT key, value, updated_at FROM app_settings")}
    out = []
    for s in SETTINGS.values():
        out.append({"key": s.key, "label": s.label, "help": s.help, "group": s.group, "default": s.default,
                    "min": s.minimum, "max": s.maximum, "value": get(s.key),
                    "updated_at": stored[s.key]["updated_at"] if s.key in stored else None})
    return out


def update(values: dict, actor_id: int | None) -> dict[str, tuple[int, int]]:
    """Validate and save; returns {key: (old, new)} for the changed ones."""
    changes: dict[str, tuple[int, int]] = {}
    clean: dict[str, int] = {}
    for key, raw in (values or {}).items():
        s = SETTINGS.get(key)
        if s is None:
            raise ValueError(f"Unknown setting: {key}")
        if isinstance(raw, bool):
            raise ValueError(f"{s.label}: enter a whole number.")
        try:
            value = int(raw)
        except (TypeError, ValueError):
            raise ValueError(f"{s.label}: enter a whole number.") from None
        if not s.minimum <= value <= s.maximum:
            raise ValueError(f"{s.label}: choose a value from {s.minimum} to {s.maximum}.")
        clean[key] = value
    now = time.time()
    with db.tx() as c:
        for key, value in clean.items():
            old = get(key)
            if old == value:
                continue
            c.execute("INSERT INTO app_settings (key, value, updated_by, updated_at) VALUES (?, ?, ?, ?) "
                      "ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_by = excluded.updated_by, "
                      "updated_at = excluded.updated_at", (key, json.dumps(value), actor_id, now))
            changes[key] = (old, value)
    return changes
