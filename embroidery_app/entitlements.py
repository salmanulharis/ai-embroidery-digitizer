"""What an account may do right now: the one place every limit and feature check asks.

The effective entitlement of an account is built in this order (later wins):

1. the feature defaults in ``FEATURES``;
2. the account's plan (``plans`` row: limit, period, features). An expired or inactive
   subscription falls back to the default plan, so access never silently disappears;
3. the account's own overrides (``entitlement_overrides``: custom or unlimited allowance,
   individual features on/off);
4. the role: the Super Admin always has every feature and no limit.

Usage comes from ``usage_records`` (usage.py): counted generations since the start of the
current period (calendar month in UTC, or since the subscription started for "total"
plans), or since the last manual reset if that is later.

Unlimited is a state of its own (``limit_type == "unlimited"``, ``limit`` None), never a
large number. Plan limits and feature switches live in the database, not in the code; the
code only declares which features exist (``FEATURES``) because each one is enforced in a
specific place.
"""

from __future__ import annotations

import calendar
import json
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone

from . import db

SUPER_ADMIN = "super_admin"
GENERATION = "generation"  # usage_records.kind that counts against the allowance


@dataclass(frozen=True)
class Feature:
    key: str
    label: str
    help: str
    default: bool


FEATURES: dict[str, Feature] = {f.key: f for f in (
    Feature("ai_services", "Own AI service",
            "Suggestions, picture clean-up and stitch checks with the AI service and key the user adds in AI settings. "
            "The built-in assistant always works.", True),
    Feature("server_ai", "The app's AI keys",
            "When the user has no key of their own, the AI keys configured on the server are used for them "
            "(you pay the AI provider for these requests).", False),
    Feature("all_formats", "All machine formats", "EXP, U01, PES, JEF and VP3 downloads besides DST.", True),
    Feature("zip_export", "Several files as one ZIP", "Download the chosen files together as a ZIP archive.", True),
    Feature("photo_stitch", "Photo stitch", "Sew a whole picture as a photo texture (stitch settings).", True),
)}


class EntitlementError(Exception):
    """A request the account's plan does not allow. ``code`` tells the browser which."""

    status = 403

    def __init__(self, message: str, code: str = "feature_unavailable", usage: dict | None = None):
        super().__init__(message)
        self.code = code
        self.usage = usage


@dataclass
class Entitlement:
    user_id: int
    plan: dict  # the plan in effect (the default plan when the subscription lapsed)
    subscription: dict  # status, started_at, expires_at, expired, plan (the assigned one)
    limit_type: str  # "limited" | "unlimited"
    limit: int | None  # None when unlimited
    limit_source: str  # "plan" | "override" | "role"
    period: str  # "month" | "total"
    period_start: float
    resets_at: float | None
    used: int
    features: dict[str, bool] = field(default_factory=dict)
    override: dict | None = None

    @property
    def unlimited(self) -> bool:
        return self.limit_type == "unlimited"

    @property
    def remaining(self) -> int | None:
        return None if self.unlimited else max(0, (self.limit or 0) - self.used)

    def can_generate(self) -> bool:
        return self.unlimited or self.used < (self.limit or 0)

    def has(self, feature: str) -> bool:
        return bool(self.features.get(feature))

    def to_dict(self) -> dict:
        d = asdict(self)
        d.update(unlimited=self.unlimited, remaining=self.remaining)
        return d

    def usage_summary(self) -> dict:
        return {"used": self.used, "limit": self.limit, "remaining": self.remaining, "unlimited": self.unlimited,
                "period": self.period, "resets_at": self.resets_at, "plan": self.plan["name"]}


# --------------------------------------------------------------------------- periods

def month_bounds(now: float) -> tuple[float, float]:
    d = datetime.fromtimestamp(now, timezone.utc)
    start = datetime(d.year, d.month, 1, tzinfo=timezone.utc)
    days = calendar.monthrange(d.year, d.month)[1]
    return start.timestamp(), start.timestamp() + days * 86400


def plan_dict(row) -> dict:
    features = json.loads(row["features"] or "{}")
    return {"id": row["id"], "code": row["code"], "name": row["name"], "description": row["description"],
            "limit_type": row["limit_type"], "generation_limit": row["generation_limit"], "period": row["period"],
            "features": {k: bool(v) for k, v in features.items() if k in FEATURES},
            "enabled": bool(row["enabled"]), "is_default": bool(row["is_default"]), "sort_order": row["sort_order"],
            "price_cents": row["price_cents"], "currency": row["currency"], "provider_price_ref": row["provider_price_ref"]}


def _override_dict(row) -> dict | None:
    if row is None:
        return None
    features = {k: bool(v) for k, v in json.loads(row["features"] or "{}").items() if k in FEATURES}
    return {"limit_type": row["limit_type"], "generation_limit": row["generation_limit"], "features": features,
            "note": row["note"], "updated_at": row["updated_at"]}


# --------------------------------------------------------------------------- computation

def effective_many(user_ids: list[int] | None = None, now: float | None = None) -> dict[int, Entitlement]:
    """Entitlements of many accounts with a fixed number of queries (analytics, user list).
    ``None`` = every account."""
    now = now or time.time()
    month_start, month_end = month_bounds(now)
    with db.tx() as c:
        if user_ids is None:
            users = c.execute("SELECT id, role FROM users").fetchall()
        else:
            ids = sorted({int(u) for u in user_ids})
            users = []
            for i in range(0, len(ids), 900):
                chunk = ids[i:i + 900]
                users += c.execute(f"SELECT id, role FROM users WHERE id IN ({','.join('?' * len(chunk))})", chunk).fetchall()
        plans = {r["id"]: plan_dict(r) for r in c.execute("SELECT * FROM plans")}
        default = next((p for p in plans.values() if p["is_default"]), None)
        subs = {r["user_id"]: r for r in c.execute("SELECT * FROM subscriptions")}
        overrides = {r["user_id"]: r for r in c.execute("SELECT * FROM entitlement_overrides")}

        out: dict[int, Entitlement] = {}
        starts: dict[int, float] = {}
        for u in users:
            uid = u["id"]
            sub = subs.get(uid)
            assigned = plans.get(sub["plan_id"]) if sub else None
            lapsed = sub is None or sub["status"] != "active" or (sub["expires_at"] is not None and sub["expires_at"] <= now)
            plan = (default if lapsed else assigned) or assigned or default or _fallback_plan()
            sub_info = {
                "plan": assigned, "status": sub["status"] if sub else "none",
                "started_at": sub["started_at"] if sub else None, "expires_at": sub["expires_at"] if sub else None,
                "expired": bool(sub) and lapsed, "source": sub["source"] if sub else None,
                "payment_status": sub["payment_status"] if sub else "none",
                "usage_reset_at": sub["usage_reset_at"] if sub else None,
            }
            ov = _override_dict(overrides.get(uid))
            if u["role"] == SUPER_ADMIN:
                limit_type, limit, source = "unlimited", None, "role"
            elif ov and ov["limit_type"] == "unlimited":
                limit_type, limit, source = "unlimited", None, "override"
            elif ov and ov["limit_type"] == "limited" and ov["generation_limit"] is not None:
                limit_type, limit, source = "limited", ov["generation_limit"], "override"
            else:
                limit_type = plan["limit_type"]
                limit = None if limit_type == "unlimited" else plan["generation_limit"]
                source = "plan"
            if plan["period"] == "month":
                start, resets = month_start, month_end
            else:
                start, resets = (sub["started_at"] if sub else 0.0), None
            if sub and sub["usage_reset_at"]:
                start = max(start, sub["usage_reset_at"])
            features = {k: f.default for k, f in FEATURES.items()} | plan["features"] | ((ov or {}).get("features") or {})
            if u["role"] == SUPER_ADMIN:
                features = {k: True for k in FEATURES}
            out[uid] = Entitlement(uid, plan, sub_info, limit_type, limit, source, plan["period"], start, resets, 0, features, ov)
            starts[uid] = start

        # counted generations since each account's period start: one query per 400 accounts
        items = list(starts.items())
        for i in range(0, len(items), 400):
            chunk = items[i:i + 400]
            values = ",".join("(?, ?)" for _ in chunk)
            args = [x for pair in chunk for x in pair]
            rows = c.execute(
                f"WITH s(uid, start) AS (VALUES {values}) "
                "SELECT s.uid, (SELECT COUNT(*) FROM usage_records r WHERE r.user_id = s.uid AND r.kind = ? "
                "AND r.counted = 1 AND r.created_at >= s.start) AS used FROM s", args + [GENERATION]).fetchall()
            for r in rows:
                out[r["uid"]].used = r["used"]
    return out


def effective(user_id: int, now: float | None = None) -> Entitlement:
    ent = effective_many([user_id], now).get(int(user_id))
    if ent is None:
        raise EntitlementError("No such account.", "no_account")
    return ent


def require_feature(user_id: int, feature: str) -> Entitlement:
    ent = effective(user_id)
    if not ent.has(feature):
        raise EntitlementError(f"\"{FEATURES[feature].label}\" is not included in your {ent.plan['name']} plan. "
                               "Ask your administrator if you need it.", "feature_unavailable")
    return ent


def _fallback_plan() -> dict:
    """Used only if the plans table is empty (a broken database): no allowance at all."""
    return {"id": None, "code": "none", "name": "No plan", "description": "", "limit_type": "limited",
            "generation_limit": 0, "period": "month", "features": {}, "enabled": False, "is_default": False,
            "sort_order": 0, "price_cents": None, "currency": None, "provider_price_ref": None}


def catalog() -> list[dict]:
    return [asdict(f) for f in FEATURES.values()]
