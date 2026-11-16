"""The app's small SQLite database: accounts, login sessions, each user's AI settings,
plans and subscriptions, usage metadata, saved settings presets, app settings and the audit log.

Project files stay as temporary folders under DATA_DIR/projects (temp_projects.py); the
database only knows a project's id, owner and timestamps, never its content. The schema is upgraded in place at startup (``MIGRATIONS``, tracked with SQLite's
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
    # 3: plans + subscriptions + overrides (entitlements.py, subscriptions.py), usage records
    # (usage.py), temporary project registry (temp_projects.py), app settings, audit log,
    # revocable sessions, and the structure a payment integration will need later.
    """
    ALTER TABLE users ADD COLUMN display_name TEXT;
    ALTER TABLE users ADD COLUMN last_active_at REAL;

    ALTER TABLE sessions ADD COLUMN sid TEXT;
    ALTER TABLE sessions ADD COLUMN last_seen_at REAL;
    ALTER TABLE sessions ADD COLUMN user_agent TEXT;
    UPDATE sessions SET sid = lower(hex(randomblob(9))), last_seen_at = created_at;
    CREATE UNIQUE INDEX sessions_sid ON sessions(sid);
    CREATE INDEX sessions_expires ON sessions(expires_at);

    CREATE TABLE plans (
        id                 INTEGER PRIMARY KEY AUTOINCREMENT,
        code               TEXT NOT NULL UNIQUE COLLATE NOCASE,
        name               TEXT NOT NULL,
        description        TEXT NOT NULL DEFAULT '',
        limit_type         TEXT NOT NULL CHECK (limit_type IN ('limited', 'unlimited')),
        generation_limit   INTEGER NOT NULL DEFAULT 0 CHECK (generation_limit >= 0),
        period             TEXT NOT NULL DEFAULT 'month' CHECK (period IN ('month', 'total')),
        features           TEXT NOT NULL DEFAULT '{}',
        enabled            INTEGER NOT NULL DEFAULT 1,
        is_default         INTEGER NOT NULL DEFAULT 0,
        sort_order         INTEGER NOT NULL DEFAULT 0,
        price_cents        INTEGER CHECK (price_cents IS NULL OR price_cents >= 0),
        currency           TEXT,
        provider_price_ref TEXT,
        created_at         REAL NOT NULL,
        updated_at         REAL NOT NULL
    );
    CREATE UNIQUE INDEX plans_one_default ON plans(is_default) WHERE is_default = 1;

    CREATE TABLE subscriptions (
        user_id           INTEGER PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
        plan_id           INTEGER NOT NULL REFERENCES plans(id) ON DELETE RESTRICT,
        status            TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'expired', 'cancelled', 'pending', 'past_due')),
        started_at        REAL NOT NULL,
        expires_at        REAL,
        source            TEXT NOT NULL DEFAULT 'manual' CHECK (source IN ('auto', 'manual', 'payment')),
        payment_provider  TEXT,
        payment_reference TEXT,
        payment_status    TEXT NOT NULL DEFAULT 'none' CHECK (payment_status IN ('none', 'pending', 'paid', 'failed', 'refunded')),
        usage_reset_at    REAL,
        updated_at        REAL NOT NULL
    );
    CREATE INDEX subscriptions_plan ON subscriptions(plan_id);

    CREATE TABLE subscription_history (
        id             INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id        INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        event          TEXT NOT NULL CHECK (event IN ('assigned', 'upgraded', 'downgraded', 'changed', 'extended', 'expired', 'status', 'usage_reset')),
        from_plan_id   INTEGER REFERENCES plans(id) ON DELETE SET NULL,
        to_plan_id     INTEGER REFERENCES plans(id) ON DELETE SET NULL,
        from_expires_at REAL,
        to_expires_at  REAL,
        actor_id       INTEGER REFERENCES users(id) ON DELETE SET NULL,
        note           TEXT NOT NULL DEFAULT '',
        at             REAL NOT NULL
    );
    CREATE INDEX subscription_history_user ON subscription_history(user_id, at);

    CREATE TABLE payment_transactions (
        id               INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id          INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        plan_id          INTEGER REFERENCES plans(id) ON DELETE SET NULL,
        provider         TEXT NOT NULL,
        provider_ref     TEXT NOT NULL,
        amount_cents     INTEGER NOT NULL CHECK (amount_cents >= 0),
        currency         TEXT NOT NULL,
        status           TEXT NOT NULL CHECK (status IN ('pending', 'succeeded', 'failed', 'refunded')),
        created_at       REAL NOT NULL,
        updated_at       REAL NOT NULL,
        UNIQUE (provider, provider_ref)
    );
    CREATE INDEX payment_transactions_user ON payment_transactions(user_id, created_at);

    CREATE TABLE entitlement_overrides (
        user_id          INTEGER PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
        limit_type       TEXT CHECK (limit_type IS NULL OR limit_type IN ('limited', 'unlimited')),
        generation_limit INTEGER CHECK (generation_limit IS NULL OR generation_limit >= 0),
        features         TEXT NOT NULL DEFAULT '{}',
        note             TEXT NOT NULL DEFAULT '',
        updated_by       INTEGER REFERENCES users(id) ON DELETE SET NULL,
        updated_at       REAL NOT NULL
    );

    CREATE TABLE usage_records (
        id           INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id      INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        kind         TEXT NOT NULL,
        feature      TEXT NOT NULL DEFAULT '',
        status       TEXT NOT NULL CHECK (status IN ('started', 'succeeded', 'failed', 'cancelled', 'rejected')),
        counted      INTEGER NOT NULL DEFAULT 0,
        dedupe_key   TEXT,
        provider     TEXT,
        model        TEXT,
        duration_ms  INTEGER,
        tokens_in    INTEGER,
        tokens_out   INTEGER,
        error        TEXT,
        created_at   REAL NOT NULL,
        finished_at  REAL
    );
    CREATE INDEX usage_created ON usage_records(created_at);
    CREATE INDEX usage_kind_created ON usage_records(kind, created_at);
    CREATE INDEX usage_user_quota ON usage_records(user_id, kind, counted, created_at);
    CREATE UNIQUE INDEX usage_one_charge ON usage_records(user_id, kind, dedupe_key) WHERE counted = 1;

    CREATE TABLE daily_activity (
        day     INTEGER NOT NULL,
        user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        PRIMARY KEY (day, user_id)
    ) WITHOUT ROWID;
    CREATE INDEX daily_activity_user ON daily_activity(user_id, day);

    CREATE TABLE temp_projects (
        id             TEXT PRIMARY KEY,
        user_id        INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        session_sid    TEXT,
        created_at     REAL NOT NULL,
        last_access_at REAL NOT NULL,
        released_at    REAL
    );
    CREATE INDEX temp_projects_user ON temp_projects(user_id);
    CREATE INDEX temp_projects_session ON temp_projects(session_sid);

    CREATE TABLE app_settings (
        key        TEXT PRIMARY KEY,
        value      TEXT NOT NULL,
        updated_by INTEGER REFERENCES users(id) ON DELETE SET NULL,
        updated_at REAL NOT NULL
    );

    CREATE TABLE audit_log (
        id           INTEGER PRIMARY KEY AUTOINCREMENT,
        actor_id     INTEGER REFERENCES users(id) ON DELETE SET NULL,
        actor_label  TEXT NOT NULL DEFAULT '',
        action       TEXT NOT NULL,
        target_type  TEXT NOT NULL DEFAULT '',
        target_id    TEXT NOT NULL DEFAULT '',
        target_label TEXT NOT NULL DEFAULT '',
        detail       TEXT NOT NULL DEFAULT '{}',
        at           REAL NOT NULL
    );
    CREATE INDEX audit_at ON audit_log(at);
    CREATE INDEX audit_target ON audit_log(target_type, target_id);

    INSERT INTO plans (code, name, description, limit_type, generation_limit, period, is_default, sort_order, created_at, updated_at) VALUES
        ('free',  'Free',  'Explore the app: every core feature, a monthly allowance of designs.', 'limited', 10, 'month', 1, 1,
         CAST(strftime('%s','now') AS REAL), CAST(strftime('%s','now') AS REAL)),
        ('basic', 'Basic', 'More designs every month.', 'limited', 100, 'month', 0, 2,
         CAST(strftime('%s','now') AS REAL), CAST(strftime('%s','now') AS REAL)),
        ('pro',   'Pro',   'Unlimited designs.', 'unlimited', 0, 'month', 0, 3,
         CAST(strftime('%s','now') AS REAL), CAST(strftime('%s','now') AS REAL));
    INSERT INTO subscriptions (user_id, plan_id, status, started_at, source, updated_at)
        SELECT u.id, p.id, 'active', CAST(strftime('%s','now') AS REAL), 'auto', CAST(strftime('%s','now') AS REAL)
        FROM users u, plans p WHERE p.code = 'free';
    INSERT INTO subscription_history (user_id, event, to_plan_id, note, at)
        SELECT user_id, 'assigned', plan_id, 'Default plan at upgrade', started_at FROM subscriptions;
    """,
    # 4: settings presets (presets.py): each account's own presets, and numeric limits
    # (entitlements.LIMITS) on plans and overrides. The seeded plans get the preset
    # features and limits (Free: built-in only; Basic: 5 own; Pro: 25 own + premium).
    """
    CREATE TABLE user_presets (
        id          INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id     INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        name        TEXT NOT NULL,
        description TEXT NOT NULL DEFAULT '',
        settings    TEXT NOT NULL,
        schema      INTEGER NOT NULL DEFAULT 1,
        created_at  REAL NOT NULL,
        updated_at  REAL NOT NULL,
        UNIQUE (user_id, name)
    );
    CREATE INDEX user_presets_user ON user_presets(user_id);
    ALTER TABLE plans ADD COLUMN limits TEXT NOT NULL DEFAULT '{}';
    ALTER TABLE entitlement_overrides ADD COLUMN limits TEXT NOT NULL DEFAULT '{}';
    UPDATE plans SET limits = '{"max_custom_presets": 0}' WHERE code = 'free';
    UPDATE plans SET limits = '{"max_custom_presets": 5}',
        features = json_set(features, '$.custom_presets', json('true')) WHERE code = 'basic';
    UPDATE plans SET limits = '{"max_custom_presets": 25}',
        features = json_set(features, '$.custom_presets', json('true'), '$.presets_premium', json('true')) WHERE code = 'pro';
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


_depth = threading.local()


@contextmanager
def tx():
    """One transaction on the shared connection (the app runs a single worker).

    ``BEGIN IMMEDIATE`` takes the write lock up front, so a read-check-write inside one
    ``tx()`` (e.g. the quota check in usage.py) cannot interleave with another request.
    Nested calls on the same thread join the outer transaction through a SAVEPOINT: an
    error inside rolls back only the inner part, and the outer one decides the rest."""
    global _conn
    with _lock:
        if _conn is None:
            _conn = _connect()
        level = getattr(_depth, "n", 0)
        _depth.n = level + 1
        try:
            if level:
                name = f"sp{level}"
                _conn.execute(f"SAVEPOINT {name}")
                try:
                    yield _conn
                except BaseException:
                    _conn.execute(f"ROLLBACK TO {name}")
                    _conn.execute(f"RELEASE {name}")
                    raise
                _conn.execute(f"RELEASE {name}")
                return
            _conn.execute("BEGIN IMMEDIATE")
            try:
                yield _conn
            except BaseException:
                _conn.execute("ROLLBACK")
                raise
            _conn.execute("COMMIT")
        finally:
            _depth.n = level


def reset_connection() -> None:
    """Close the shared connection (tests that swap the database file)."""
    global _conn
    with _lock:
        if _conn is not None:
            _conn.close()
        _conn = None
