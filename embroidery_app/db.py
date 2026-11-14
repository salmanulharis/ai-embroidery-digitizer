"""The app's small SQLite database: accounts, login sessions and each user's AI settings.

Projects stay as folders under DATA_DIR/projects; only data that belongs to a person lives
here. The schema is upgraded in place at startup (``MIGRATIONS``, tracked with SQLite's
``user_version``), so a deployment never needs a manual migration step.
"""

from __future__ import annotations

import sqlite3
import threading
from contextlib import contextmanager

from .config import settings

PATH = settings.data_dir / "app.db"

# Append only: each entry upgrades the schema by one version.
MIGRATIONS = [
    """
    CREATE TABLE users (
        id            INTEGER PRIMARY KEY AUTOINCREMENT,
        email         TEXT NOT NULL UNIQUE COLLATE NOCASE,
        password_hash TEXT NOT NULL,
        role          TEXT NOT NULL CHECK (role IN ('super_admin', 'user')),
        active        INTEGER NOT NULL DEFAULT 1,
        created_at    REAL NOT NULL,
        updated_at    REAL NOT NULL,
        last_login_at REAL
    );
    CREATE TABLE sessions (
        token_hash TEXT PRIMARY KEY,
        user_id    INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        created_at REAL NOT NULL,
        expires_at REAL NOT NULL,
        remember   INTEGER NOT NULL DEFAULT 0
    );
    CREATE INDEX sessions_user ON sessions(user_id);
    CREATE TABLE ai_settings (
        user_id    INTEGER PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
        data       TEXT NOT NULL,
        updated_at REAL NOT NULL
    );
    """,
    # 2: optional sign-in name, unique in any letter case (several accounts may have none)
    """
    ALTER TABLE users ADD COLUMN username TEXT;
    CREATE UNIQUE INDEX users_username ON users(username COLLATE NOCASE);
    """,
]

_lock = threading.RLock()
_conn: sqlite3.Connection | None = None


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(PATH, check_same_thread=False, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    version = conn.execute("PRAGMA user_version").fetchone()[0]
    for i, sql in enumerate(MIGRATIONS[version:], start=version + 1):
        conn.executescript(f"BEGIN; {sql}; PRAGMA user_version = {i}; COMMIT;")
    return conn


@contextmanager
def tx():
    """One transaction on the shared connection (the app runs a single worker)."""
    global _conn
    with _lock:
        if _conn is None:
            _conn = _connect()
        _conn.execute("BEGIN IMMEDIATE")
        try:
            yield _conn
        except BaseException:
            _conn.execute("ROLLBACK")
            raise
        _conn.execute("COMMIT")
