"""Temporary projects: the app processes designs, it does not store them.

A project (the uploaded file and everything made from it, in DATA_DIR/projects/<id>) is
registered in ``temp_projects`` with its owner and the login session that created it.
The registry is the authority for ownership; it holds no content, only ids and times.

A project is deleted - files and registry row - when:

- the same browser session starts a new project (upload);
- the user closes it ("Close design"), or signs out (the session's projects);
- the browser tab goes away: the page sends a *release* (``pagehide`` beacon) and the
  project is deleted ``project_release_grace_s`` later unless the page comes back (a
  reload reopens it and clears the release). Browsers do not always send it, so:
- its login session ends (expired, revoked, password changed, account deactivated);
- nobody has opened it for ``project_idle_minutes``, or it is older than
  ``project_max_hours`` (app settings);
- a folder on disk has no registry row (e.g. created before this existed).

``sweep()`` runs every few minutes in the app (see api.py) and does not depend on any
browser event. The systemd timer from deploy/ec2/setup.sh is a last-resort backstop.
"""

from __future__ import annotations

import logging
import shutil
import time
from pathlib import Path

from . import app_settings, db, service

log = logging.getLogger("embroidery")
ORPHAN_GRACE_S = 600  # a folder without a registry row is left alone this long (upload in progress)


def register(project_id: str, user_id: int, session_sid: str | None, replace_others: bool = True) -> list[str]:
    """Record a new project. With ``replace_others`` the session's earlier projects are
    deleted (a new project replaces the open one). Returns the ids removed."""
    now = time.time()
    with db.tx() as c:
        old = []
        if replace_others and session_sid:
            old = [r["id"] for r in c.execute("SELECT id FROM temp_projects WHERE user_id = ? AND session_sid = ? AND id != ?",
                                              (user_id, session_sid, project_id))]
        c.execute("INSERT OR REPLACE INTO temp_projects (id, user_id, session_sid, created_at, last_access_at) VALUES (?, ?, ?, ?, ?)",
                  (project_id, user_id, session_sid, now, now))
    for pid in old:
        delete(pid)
    return old


def owned_by(project_id: str, user_id: int) -> bool:
    with db.tx() as c:
        return c.execute("SELECT 1 FROM temp_projects WHERE id = ? AND user_id = ?", (project_id, user_id)).fetchone() is not None


def touch(project_id: str, user_id: int) -> bool:
    """The owner used the project: it is in use again (a pending release is cancelled).
    False when it is not this account's (or no longer exists)."""
    with db.tx() as c:
        return c.execute("UPDATE temp_projects SET last_access_at = ?, released_at = NULL WHERE id = ? AND user_id = ?",
                         (time.time(), project_id, user_id)).rowcount == 1


def release(project_id: str, user_id: int) -> None:
    """The page that had the project open went away; delete it after the grace period."""
    with db.tx() as c:
        c.execute("UPDATE temp_projects SET released_at = ? WHERE id = ? AND user_id = ?", (time.time(), project_id, user_id))


def delete(project_id: str) -> None:
    with db.tx() as c:
        c.execute("DELETE FROM temp_projects WHERE id = ?", (project_id,))
    service.delete_project(project_id)


def delete_for_session(session_sid: str | None) -> int:
    if not session_sid:
        return 0
    with db.tx() as c:
        ids = [r["id"] for r in c.execute("SELECT id FROM temp_projects WHERE session_sid = ?", (session_sid,))]
    for pid in ids:
        delete(pid)
    return len(ids)


def delete_for_user(user_id: int) -> int:
    with db.tx() as c:
        ids = [r["id"] for r in c.execute("SELECT id FROM temp_projects WHERE user_id = ?", (user_id,))]
    for pid in ids:
        delete(pid)
    return len(ids) + service.delete_projects_of(user_id)  # unregistered folders of the account, if any


def open_count(user_id: int | None = None) -> int:
    with db.tx() as c:
        if user_id is None:
            return c.execute("SELECT COUNT(*) FROM temp_projects").fetchone()[0]
        return c.execute("SELECT COUNT(*) FROM temp_projects WHERE user_id = ?", (user_id,)).fetchone()[0]


def sweep(now: float | None = None) -> int:
    """Delete every project that is no longer in use (see the module docstring)."""
    now = now or time.time()
    idle = app_settings.get("project_idle_minutes") * 60
    max_age = app_settings.get("project_max_hours") * 3600
    grace = app_settings.get("project_release_grace_s")
    with db.tx() as c:
        rows = c.execute(
            "SELECT p.id FROM temp_projects p LEFT JOIN sessions s ON s.sid = p.session_sid AND s.expires_at > ? "
            "WHERE p.last_access_at < ? OR p.created_at < ? OR (p.released_at IS NOT NULL AND p.released_at < ?) "
            "OR (p.session_sid IS NOT NULL AND s.sid IS NULL)",
            (now, now - idle, now - max_age, now - grace)).fetchall()
        known = {r["id"] for r in c.execute("SELECT id FROM temp_projects")}
    removed = 0
    for r in rows:
        delete(r["id"])
        known.discard(r["id"])
        removed += 1
    # folders the registry does not know (older versions, crashes between write and register)
    for d in _project_dirs():
        if d.name not in known and now - d.stat().st_mtime > ORPHAN_GRACE_S:
            shutil.rmtree(d, ignore_errors=True)
            removed += 1
    if removed:
        log.info("Removed %d temporary project(s)", removed)
    return removed


def _project_dirs() -> list[Path]:
    try:
        return [d for d in service.PROJECTS.iterdir() if d.is_dir()]
    except FileNotFoundError:
        return []
