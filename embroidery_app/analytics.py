"""Admin analytics from usage metadata only (no files, prompts or project content).

All figures are aggregated in SQL (GROUP BY day / provider / model ...) over indexed
columns, so the browser receives summaries, never raw records, and the queries stay fast
as ``usage_records`` grows. Days are UTC days (``created_at // 86400``).

Definitions:
- *Active user* (DAU / MAU): an account that signed in or used the app on that day
  (``daily_activity``, written at most once a minute per session by auth.py).
- *Generation request*: an attempt to download stitch output for a design not yet charged
  (usage.py): succeeded, failed (not charged), cancelled (interrupted, not charged) or
  rejected (allowance used up). Re-downloads of a charged design are not requests.
- *AI request*: a suggestion, clean-up, stitch check or connection test that used an AI
  service; built-in (non-AI) answers are counted as feature use, not as AI requests.
"""

from __future__ import annotations

import time
from datetime import date, datetime, timezone

from . import app_settings, db, entitlements, subscriptions, temp_projects, usage
from .entitlements import GENERATION

MAX_RANGE_DAYS = 400
DAY = 86400


def _day(value: str | None, fallback: int) -> int:
    if not value:
        return fallback
    try:
        d = date.fromisoformat(str(value)[:10])
    except ValueError:
        raise ValueError("Dates are YYYY-MM-DD.") from None
    return int(datetime(d.year, d.month, d.day, tzinfo=timezone.utc).timestamp() // DAY)


def date_range(start: str | None, end: str | None) -> tuple[int, int]:
    """Inclusive UTC day numbers; default the last 30 days."""
    today = int(time.time() // DAY)
    to_day = _day(end, today)
    from_day = _day(start, to_day - 29)
    if from_day > to_day:
        raise ValueError("The start date is after the end date.")
    if to_day - from_day + 1 > MAX_RANGE_DAYS:
        raise ValueError(f"Choose at most {MAX_RANGE_DAYS} days.")
    return from_day, to_day


def iso(day: int) -> str:
    return datetime.fromtimestamp(day * DAY, timezone.utc).date().isoformat()


def _series(rows, from_day: int, to_day: int, *fields: str) -> list[dict]:
    by_day = {r["day"]: r for r in rows}
    return [{"day": iso(d), **{f: (by_day[d][f] or 0) if d in by_day else 0 for f in fields}} for d in range(from_day, to_day + 1)]


# --------------------------------------------------------------------------- dashboard

def overview(start: str | None = None, end: str | None = None) -> dict:
    from_day, to_day = date_range(start, end)
    t0, t1 = from_day * DAY, (to_day + 1) * DAY
    with db.tx() as c:
        u = c.execute("SELECT COUNT(*) AS total, SUM(active) AS active, SUM(created_at >= ? AND created_at < ?) AS new "
                      "FROM users", (t0, t1)).fetchone()
        new_rows = c.execute("SELECT CAST(created_at / 86400 AS INTEGER) AS day, COUNT(*) AS n FROM users "
                             "WHERE created_at >= ? AND created_at < ? GROUP BY day", (t0, t1)).fetchall()
        dau_rows = c.execute("SELECT day, COUNT(*) AS n FROM daily_activity WHERE day BETWEEN ? AND ? GROUP BY day",
                             (from_day, to_day)).fetchall()
        mau = c.execute("SELECT COUNT(DISTINCT user_id) FROM daily_activity WHERE day BETWEEN ? AND ?",
                        (to_day - 29, to_day)).fetchone()[0]
        active_in_range = c.execute("SELECT COUNT(DISTINCT user_id) FROM daily_activity WHERE day BETWEEN ? AND ?",
                                    (from_day, to_day)).fetchone()[0]
        g = c.execute("SELECT COUNT(*) AS requests, SUM(status = 'succeeded') AS succeeded, SUM(status = 'failed') AS failed, "
                      "SUM(status = 'cancelled') AS cancelled, SUM(status = 'rejected') AS rejected, "
                      "COUNT(DISTINCT CASE WHEN status = 'succeeded' THEN user_id END) AS users "
                      "FROM usage_records WHERE kind = ? AND created_at >= ? AND created_at < ?", (GENERATION, t0, t1)).fetchone()
        gen_rows = c.execute("SELECT CAST(created_at / 86400 AS INTEGER) AS day, SUM(status = 'succeeded') AS succeeded, "
                             "SUM(status IN ('failed', 'cancelled')) AS failed, SUM(status = 'rejected') AS rejected "
                             "FROM usage_records WHERE kind = ? AND created_at >= ? AND created_at < ? GROUP BY day",
                             (GENERATION, t0, t1)).fetchall()
        top_users = c.execute("SELECT r.user_id, u.email, u.display_name, COUNT(*) AS n FROM usage_records r JOIN users u ON u.id = r.user_id "
                              "WHERE r.kind = ? AND r.status = 'succeeded' AND r.created_at >= ? AND r.created_at < ? "
                              "GROUP BY r.user_id ORDER BY n DESC LIMIT 10", (GENERATION, t0, t1)).fetchall()
        features = c.execute("SELECT kind, feature, COUNT(*) AS n, SUM(status = 'succeeded') AS ok FROM usage_records "
                             "WHERE created_at >= ? AND created_at < ? AND status != 'rejected' GROUP BY kind, feature "
                             "ORDER BY n DESC LIMIT 20", (t0, t1)).fetchall()
    ai = ai_stats(t0, t1)
    subs = subscription_stats()
    succeeded = g["succeeded"] or 0
    return {
        "range": {"from": iso(from_day), "to": iso(to_day), "days": to_day - from_day + 1},
        "users": {"total": u["total"] or 0, "active": u["active"] or 0, "inactive": (u["total"] or 0) - (u["active"] or 0),
                  "new": u["new"] or 0, "active_in_range": active_in_range, "mau": mau,
                  "dau_today": next((r["n"] for r in dau_rows if r["day"] == to_day), 0),
                  "dau_avg": round(sum(r["n"] for r in dau_rows) / (to_day - from_day + 1), 1),
                  "new_series": _series(new_rows, from_day, to_day, "n"),
                  "dau_series": _series(dau_rows, from_day, to_day, "n")},
        "generations": {"requests": g["requests"] or 0, "succeeded": succeeded, "failed": g["failed"] or 0,
                        "cancelled": g["cancelled"] or 0, "rejected": g["rejected"] or 0,
                        "success_rate": (round(100 * succeeded / attempts, 1) if (attempts := (g["requests"] or 0) - (g["rejected"] or 0)) else None),
                        "users": g["users"] or 0,
                        "per_active_user": round(succeeded / active_in_range, 2) if active_in_range else 0,
                        "series": _series(gen_rows, from_day, to_day, "succeeded", "failed", "rejected"),
                        "top_users": [{"id": r["user_id"], "email": r["email"], "name": r["display_name"], "n": r["n"]} for r in top_users]},
        "features": [{"kind": r["kind"], "feature": r["feature"], "label": feature_label(r["kind"], r["feature"]),
                      "n": r["n"], "succeeded": r["ok"] or 0} for r in features],
        "subscriptions": subs,
        "ai": ai,
        "open_projects": temp_projects.open_count(),
    }


def feature_label(kind: str, feature: str) -> str:
    if kind == GENERATION:
        return f"Design download ({(feature or '').split(':')[-1].upper() or '?'})"
    names = {"ai_suggest": "Suggest settings", "ai_clean": "Picture clean-up", "ai_review": "Check my stitches",
             "ai_test": "AI connection test"}
    name = names.get(kind, kind)
    return f"{name} ({'built-in' if feature == 'builtin' else 'AI'})" if feature else name


def ai_stats(t0: float, t1: float) -> dict:
    """AI requests (``provider`` set and not built-in) by provider and by model."""
    base = ("FROM usage_records WHERE kind LIKE 'ai_%' AND provider IS NOT NULL AND provider != 'builtin' "
            "AND created_at >= ? AND created_at < ?")
    cols = ("COUNT(*) AS requests, SUM(status = 'succeeded') AS succeeded, SUM(status = 'failed') AS failed, "
            "CAST(AVG(duration_ms) AS INTEGER) AS avg_ms, SUM(tokens_in) AS tokens_in, SUM(tokens_out) AS tokens_out")
    with db.tx() as c:
        total = c.execute(f"SELECT {cols} {base}", (t0, t1)).fetchone()
        by_provider = c.execute(f"SELECT provider, {cols} {base} GROUP BY provider ORDER BY requests DESC", (t0, t1)).fetchall()
        by_model = c.execute(f"SELECT provider, model, {cols} {base} GROUP BY provider, model ORDER BY requests DESC LIMIT 15",
                             (t0, t1)).fetchall()
        by_kind = c.execute(f"SELECT kind, {cols} {base} GROUP BY kind ORDER BY requests DESC", (t0, t1)).fetchall()

    def row(r) -> dict:
        return {"requests": r["requests"] or 0, "succeeded": r["succeeded"] or 0, "failed": r["failed"] or 0,
                "avg_ms": r["avg_ms"] or 0, "tokens_in": r["tokens_in"] or 0, "tokens_out": r["tokens_out"] or 0}

    return {"total": row(total),
            "by_provider": [{"provider": r["provider"], **row(r)} for r in by_provider],
            "by_model": [{"provider": r["provider"], "model": r["model"] or "?", **row(r)} for r in by_model],
            "by_task": [{"kind": r["kind"], "label": feature_label(r["kind"], ""), **row(r)} for r in by_kind]}


def subscription_stats() -> dict:
    ents = entitlements.effective_many()
    pct = app_settings.get("approaching_limit_pct")
    with db.tx() as c:
        active = {r["id"] for r in c.execute("SELECT id FROM users WHERE active = 1")}
    by_plan: dict[str, int] = {}
    unlimited = approaching = exhausted = expired = 0
    for uid, e in ents.items():
        by_plan[e.plan["name"]] = by_plan.get(e.plan["name"], 0) + 1
        expired += int(e.subscription["expired"])
        if e.unlimited:
            unlimited += 1
        elif uid in active:
            if e.used >= (e.limit or 0):
                exhausted += 1
            elif e.limit and e.used * 100 >= e.limit * pct:
                approaching += 1
    plans = [{"plan": p["name"], "code": p["code"], "enabled": p["enabled"], "n": by_plan.pop(p["name"], 0)}
             for p in subscriptions.list_plans()]
    plans += [{"plan": name, "code": "", "enabled": False, "n": n} for name, n in by_plan.items()]
    return {"by_plan": plans, "unlimited": unlimited, "approaching": approaching, "exhausted": exhausted,
            "expired": expired, "approaching_pct": pct}


# --------------------------------------------------------------------------- users table

USAGE_STATUSES = ("ok", "approaching", "exhausted", "unlimited")


def _usage_status(e, pct: int) -> str:
    if e.unlimited:
        return "unlimited"
    if e.used >= (e.limit or 0):
        return "exhausted"
    if e.limit and e.used * 100 >= e.limit * pct:
        return "approaching"
    return "ok"


def users_table(q: str = "", plan: str = "", status: str = "", usage_status: str = "", sort: str = "email",
                page: int = 1, per_page: int = 50) -> dict:
    """The searchable user list: account, plan, allowance and activity per account."""
    pct = app_settings.get("approaching_limit_pct")
    sql = "SELECT * FROM users WHERE 1 = 1"
    args: list = []
    if q:
        like = f"%{q.strip()[:80]}%"
        sql += " AND (email LIKE ? OR username LIKE ? OR display_name LIKE ?)"
        args += [like, like, like]
    if status in ("active", "inactive"):
        sql += " AND active = ?"
        args.append(int(status == "active"))
    with db.tx() as c:
        rows = c.execute(sql, args).fetchall()
        sessions = {r["user_id"]: r["n"] for r in c.execute(
            "SELECT user_id, COUNT(*) AS n FROM sessions WHERE expires_at > ? GROUP BY user_id", (time.time(),))}
        totals = {r["user_id"]: r["n"] for r in c.execute(
            "SELECT user_id, COUNT(*) AS n FROM usage_records WHERE kind = ? AND status = 'succeeded' GROUP BY user_id", (GENERATION,))}
    ents = entitlements.effective_many([r["id"] for r in rows])
    out = []
    for r in rows:
        e = ents[r["id"]]
        st = _usage_status(e, pct)
        if plan and plan not in (e.plan["code"], str(e.plan["id"])):
            continue
        if usage_status and usage_status != st:
            continue
        out.append({
            "id": r["id"], "email": r["email"], "username": r["username"], "name": r["display_name"], "role": r["role"],
            "active": bool(r["active"]), "created_at": r["created_at"], "last_login_at": r["last_login_at"],
            "last_active_at": r["last_active_at"], "plan": e.plan["name"], "plan_code": e.plan["code"],
            "assigned_plan": (e.subscription["plan"] or {}).get("name"), "expired": e.subscription["expired"],
            "expires_at": e.subscription["expires_at"], "used": e.used, "limit": e.limit, "remaining": e.remaining,
            "unlimited": e.unlimited, "limit_source": e.limit_source, "usage_status": st, "total_generations": totals.get(r["id"], 0),
            "sessions": sessions.get(r["id"], 0),
            "env_admin": r["role"] == entitlements.SUPER_ADMIN and r["email"] == _env_admin_email(),
        })
    keys = {
        "email": lambda x: x["email"],
        "created": lambda x: -(x["created_at"] or 0),
        "active": lambda x: -(x["last_active_at"] or 0),
        "used": lambda x: -x["used"],
        "remaining": lambda x: (x["remaining"] is None, x["remaining"] if x["remaining"] is not None else 0),
    }
    out.sort(key=keys.get(sort, keys["email"]))
    per_page = max(10, min(int(per_page), 200))
    page = max(1, int(page))
    return {"total": len(out), "page": page, "per_page": per_page, "users": out[(page - 1) * per_page: page * per_page]}


def _env_admin_email() -> str:
    from .config import settings  # noqa: PLC0415

    return settings.super_admin_email


def user_detail(user_id: int) -> dict:
    """One account's usage details for the admin (metadata only)."""
    with db.tx() as c:
        u = c.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
        if not u:
            raise LookupError("No such account.")
        by_kind = c.execute("SELECT kind, COUNT(*) AS n, SUM(status = 'succeeded') AS ok, SUM(status = 'failed') AS failed, "
                            "SUM(status = 'rejected') AS rejected FROM usage_records WHERE user_id = ? GROUP BY kind",
                            (user_id,)).fetchall()
        since = int(time.time() // DAY) - 29
        daily = c.execute("SELECT CAST(created_at / 86400 AS INTEGER) AS day, SUM(status = 'succeeded') AS succeeded "
                          "FROM usage_records WHERE user_id = ? AND kind = ? AND created_at >= ? GROUP BY day",
                          (user_id, GENERATION, since * DAY)).fetchall()
        active_days = c.execute("SELECT COUNT(*) FROM daily_activity WHERE user_id = ? AND day >= ?", (user_id, since)).fetchone()[0]
    e = entitlements.effective(user_id)
    return {
        "user": {"id": u["id"], "email": u["email"], "username": u["username"], "name": u["display_name"], "role": u["role"],
                 "active": bool(u["active"]), "created_at": u["created_at"], "last_login_at": u["last_login_at"],
                 "last_active_at": u["last_active_at"]},
        "entitlement": e.to_dict(),
        "totals": [{"kind": r["kind"], "label": feature_label(r["kind"], ""), "n": r["n"], "succeeded": r["ok"] or 0,
                    "failed": r["failed"] or 0, "rejected": r["rejected"] or 0} for r in by_kind],
        "daily": _series(daily, since, since + 29, "succeeded"),
        "active_days_30": active_days,
        "recent": usage.user_usage(user_id, 30),
        "history": subscriptions.history(user_id, 30),
        "open_projects": temp_projects.open_count(user_id),
    }
