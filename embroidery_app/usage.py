"""Usage accounting: the generation allowance, usage records and request rate limits.

What counts as a generation
---------------------------
One **generation** = one uploaded design whose stitch output is downloaded. The first
download of stitch output for a project (a machine file, the stitch preview PNG, the
worksheet, or a ZIP with any of them) is charged once. Re-downloads, other formats and
edits of the same upload are free (the uploaded source never changes, so a project is
always the same artwork). Uploading, preparing, previewing and creating stitches are not
charged. The artwork SVG download is not stitch output and is free.

How a charge is made safe
-------------------------
``begin_generation`` runs in one ``BEGIN IMMEDIATE`` transaction: it checks whether this
project was already charged, computes the remaining allowance and inserts a *reservation*
(``status = 'started'``, ``counted = 1``). The partial unique index
``usage_one_charge (user_id, kind, dedupe_key) WHERE counted = 1`` makes a second charge for
the same project impossible, even for two requests at the same moment; the write lock
makes the limit check and the reservation atomic, so parallel requests cannot overspend.
If producing the file fails, the reservation is released (``failed``, ``counted = 0``):
users are never charged for errors. Reservations left by a crash are cancelled by the
maintenance job after ``STALE_RESERVATION_S``.

Records keep metadata only - kind, feature, status, provider, model, duration and
token counts - never file names, prompts, images or project content. ``dedupe_key`` is a
one-way hash of the temporary project id, meaningless once the project is deleted.
"""

from __future__ import annotations

import contextvars
import hashlib
import logging
import threading
import time
from collections import deque
from contextlib import contextmanager

from . import db, entitlements
from .entitlements import GENERATION, EntitlementError

log = logging.getLogger("embroidery")

STALE_RESERVATION_S = 600


def _key(project_id: str) -> str:
    return hashlib.sha256(f"project:{project_id}".encode()).hexdigest()[:32]


class Ticket:
    """A generation in progress. ``charged`` is False when the project was paid for
    before (nothing to finish)."""

    def __init__(self, record_id: int | None, charged: bool):
        self.id, self.charged, self.t0 = record_id, charged, time.monotonic()

    def succeed(self) -> None:
        if self.id is not None:
            _finish(self.id, "succeeded", None, self.t0)

    def fail(self, error: str) -> None:
        if self.id is not None:
            _finish(self.id, "failed", error, self.t0, release=True)


def _finish(record_id: int, status: str, error: str | None, t0: float, release: bool = False) -> None:
    with db.tx() as c:
        c.execute("UPDATE usage_records SET status = ?, error = ?, finished_at = ?, duration_ms = ?"
                  + (", counted = 0" if release else "") + " WHERE id = ?",
                  (status, (error or None) and error[:120], time.time(), int((time.monotonic() - t0) * 1000), record_id))


def already_charged(user_id: int, project_id: str) -> bool:
    with db.tx() as c:
        return c.execute("SELECT 1 FROM usage_records WHERE user_id = ? AND kind = ? AND dedupe_key = ? AND counted = 1",
                         (user_id, GENERATION, _key(project_id))).fetchone() is not None


def begin_generation(user_id: int, project_id: str, feature: str) -> Ticket:
    """Check the allowance and reserve one generation for this project, atomically.
    Raises ``EntitlementError`` (code ``quota_exceeded``) when the allowance is used up."""
    key = _key(project_id)
    now = time.time()
    rejected = None
    with db.tx() as c:
        if c.execute("SELECT 1 FROM usage_records WHERE user_id = ? AND kind = ? AND dedupe_key = ? AND counted = 1",
                     (user_id, GENERATION, key)).fetchone():
            return Ticket(None, charged=False)
        ent = entitlements.effective(user_id)
        if not ent.can_generate():
            c.execute("INSERT INTO usage_records (user_id, kind, feature, status, counted, created_at, finished_at, error) "
                      "VALUES (?, ?, ?, 'rejected', 0, ?, ?, 'quota_exceeded')", (user_id, GENERATION, feature, now, now))
            rejected = ent
        else:
            cur = c.execute("INSERT INTO usage_records (user_id, kind, feature, status, counted, dedupe_key, created_at) "
                            "VALUES (?, ?, ?, 'started', 1, ?, ?)", (user_id, GENERATION, feature, key, now))
            return Ticket(cur.lastrowid, charged=True)
    raise EntitlementError(_limit_message(rejected), "quota_exceeded", rejected.usage_summary())


def _limit_message(ent) -> str:
    period = "this month" if ent.period == "month" else "on your plan"
    allowance = "your 1 design download" if ent.limit == 1 else f"all {ent.limit} design downloads"
    return (f"You have used {allowance} {period} ({ent.plan['name']} plan). A design you already downloaded "
            "can still be downloaded again. To download new designs, contact your administrator about an upgrade.")


@contextmanager
def generation(user_id: int, project_id: str, feature: str):
    """``with generation(...)``: reserve, then confirm on success or release on error."""
    ticket = begin_generation(user_id, project_id, feature)
    try:
        yield ticket
    except BaseException as exc:
        ticket.fail(type(exc).__name__)
        raise
    ticket.succeed()


# --------------------------------------------------------------------------- other usage (AI tasks)

_meter: contextvars.ContextVar[dict | None] = contextvars.ContextVar("ai_meter", default=None)


def add_tokens(tokens_in: int | None, tokens_out: int | None) -> None:
    """Called by the AI HTTP layer for every provider answer that reports token usage."""
    m = _meter.get()
    if m is None:
        return
    if tokens_in:
        m["in"] = (m["in"] or 0) + int(tokens_in)
    if tokens_out:
        m["out"] = (m["out"] or 0) + int(tokens_out)


@contextmanager
def track(user_id: int, kind: str, feature: str = ""):
    """Record one non-generation operation (AI suggestion, clean-up, review, connection
    test). The body may set ``rec["provider"]``, ``rec["model"]`` and ``rec["status"]``
    (e.g. "failed" when the AI failed and the built-in result was used)."""
    rec = {"provider": None, "model": None, "status": "succeeded", "error": None}
    meter = {"in": None, "out": None}
    token = _meter.set(meter)
    t0, now = time.monotonic(), time.time()
    try:
        yield rec
    except BaseException as exc:
        rec["status"], rec["error"] = "failed", type(exc).__name__
        raise
    finally:
        _meter.reset(token)
        try:
            with db.tx() as c:
                c.execute("INSERT INTO usage_records (user_id, kind, feature, status, counted, provider, model, duration_ms, "
                          "tokens_in, tokens_out, error, created_at, finished_at) VALUES (?, ?, ?, ?, 0, ?, ?, ?, ?, ?, ?, ?, ?)",
                          (user_id, kind, feature, rec["status"], rec["provider"], (rec["model"] or None) and str(rec["model"])[:120],
                           int((time.monotonic() - t0) * 1000), meter["in"], meter["out"], rec["error"], now, time.time()))
        except Exception:  # accounting must never break the user's request
            log.exception("Could not record usage")


# --------------------------------------------------------------------------- maintenance

def cancel_stale(now: float | None = None) -> int:
    """Reservations whose request never finished (server restart, crash): not charged."""
    now = now or time.time()
    with db.tx() as c:
        return c.execute("UPDATE usage_records SET status = 'cancelled', counted = 0, finished_at = ?, error = 'interrupted' "
                         "WHERE status = 'started' AND created_at < ?", (now, now - STALE_RESERVATION_S)).rowcount


def purge(older_than_days: int) -> int:
    with db.tx() as c:
        n = c.execute("DELETE FROM usage_records WHERE created_at < ?", (time.time() - older_than_days * 86400,)).rowcount
        c.execute("DELETE FROM daily_activity WHERE day < ?", (int((time.time() - older_than_days * 86400) // 86400),))
    return n


def user_usage(user_id: int, limit: int = 30) -> list[dict]:
    """An account's recent usage records (metadata only), for the admin's user details."""
    with db.tx() as c:
        rows = c.execute("SELECT kind, feature, status, counted, provider, model, duration_ms, tokens_in, tokens_out, error, created_at "
                         "FROM usage_records WHERE user_id = ? ORDER BY id DESC LIMIT ?", (user_id, limit)).fetchall()
    return [dict(r) | {"counted": bool(r["counted"])} for r in rows]


# --------------------------------------------------------------------------- rate limits

class RateLimited(Exception):
    status = 429


_windows: dict[tuple, deque] = {}
_rlock = threading.Lock()
LIMITS = {  # bucket: (requests, seconds) per account
    "work": (240, 60),  # upload, prepare, edit, stitch
    "export": (60, 60),
    "ai": (20, 60),
}


def rate_limit(user_id: int, bucket: str) -> None:
    n, window = LIMITS[bucket]
    now = time.monotonic()
    with _rlock:
        q = _windows.setdefault((user_id, bucket), deque())
        while q and now - q[0] > window:
            q.popleft()
        if len(q) >= n:
            raise RateLimited("Too many requests in a short time. Wait a minute, then try again.")
        q.append(now)


def reset_rate_limits() -> None:
    with _rlock:
        _windows.clear()
