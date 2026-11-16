"""Plans, each account's subscription, plan history and per-account overrides.

Every account has exactly one current subscription row (``subscriptions``, keyed by
user). A plan change updates that row and appends to ``subscription_history``, so the
history holds every assignment, upgrade, downgrade, extension, expiry and usage reset.

Payments are not processed yet. The columns a payment integration needs (subscription
``source``, ``payment_provider``, ``payment_reference``, ``payment_status``; plan prices;
the ``payment_transactions`` table) exist, but nothing here pretends a payment happened:
manual changes are recorded with ``source = 'manual'`` and ``payment_status = 'none'``.
"""

from __future__ import annotations

import json
import re
import time

from . import db
from .entitlements import FEATURES, LIMITS, clean_limits, plan_dict

_CODE = re.compile(r"^[a-z0-9][a-z0-9_-]{1,31}$")
MAX_LIMIT = 1_000_000


# --------------------------------------------------------------------------- plans

def list_plans() -> list[dict]:
    with db.tx() as c:
        counts = {r["plan_id"]: r["n"] for r in c.execute("SELECT plan_id, COUNT(*) AS n FROM subscriptions GROUP BY plan_id")}
        rows = c.execute("SELECT * FROM plans ORDER BY sort_order, id").fetchall()
    return [plan_dict(r) | {"subscribers": counts.get(r["id"], 0)} for r in rows]


def get_plan(plan_id: int) -> dict:
    with db.tx() as c:
        row = c.execute("SELECT * FROM plans WHERE id = ?", (plan_id,)).fetchone()
    if not row:
        raise LookupError("No such plan.")
    return plan_dict(row)


def default_plan_id(c) -> int:
    row = c.execute("SELECT id FROM plans WHERE is_default = 1").fetchone()
    if not row:
        raise RuntimeError("No default plan: create one in Admin > Plans.")
    return row["id"]


def _clean_plan(data: dict, partial: bool) -> dict:
    out: dict = {}

    def has(k):
        return k in data and data[k] is not None

    if has("code") or not partial:
        code = str(data.get("code") or "").strip().lower()
        if not _CODE.match(code):
            raise ValueError("Plan code: 2-32 lower-case letters, digits, dash or underscore.")
        out["code"] = code
    if has("name") or not partial:
        name = str(data.get("name") or "").strip()
        if not 1 <= len(name) <= 60:
            raise ValueError("Plan name: 1-60 characters.")
        out["name"] = name
    if has("description"):
        out["description"] = str(data["description"]).strip()[:300]
    if has("limit_type") or not partial:
        lt = data.get("limit_type") or "limited"
        if lt not in ("limited", "unlimited"):
            raise ValueError("Limit type is limited or unlimited.")
        out["limit_type"] = lt
    if has("generation_limit"):
        out["generation_limit"] = _whole(data["generation_limit"], "Generation limit", 0, MAX_LIMIT)
    if has("period"):
        if data["period"] not in ("month", "total"):
            raise ValueError("Period is month or total.")
        out["period"] = data["period"]
    if has("features"):
        out["features"] = json.dumps(_clean_features(data["features"]))
    if has("limits"):
        out["limits"] = json.dumps(clean_limits(data["limits"]))
    for flag in ("enabled", "is_default"):
        if has(flag):
            if not isinstance(data[flag], bool):
                raise ValueError(f"{flag.replace('_', ' ').capitalize()} is true or false.")
            out[flag] = int(data[flag])
    if has("sort_order"):
        out["sort_order"] = _whole(data["sort_order"], "Order", 0, 1000)
    # future payment integration: kept for display/mapping only, no checkout uses them yet
    if "price_cents" in data:
        out["price_cents"] = None if data["price_cents"] in (None, "") else _whole(data["price_cents"], "Price", 0, 10_000_000)
    if "currency" in data:
        cur = (data["currency"] or "").strip().upper()
        if cur and not re.fullmatch(r"[A-Z]{3}", cur):
            raise ValueError("Currency is a three-letter code such as USD.")
        out["currency"] = cur or None
    if "provider_price_ref" in data:
        out["provider_price_ref"] = (str(data["provider_price_ref"] or "").strip()[:200]) or None
    return out


def _clean_features(value) -> dict:
    if not isinstance(value, dict):
        raise ValueError("Features must be an object of feature: true/false.")
    out = {}
    for k, v in value.items():
        if k not in FEATURES:
            raise ValueError(f"Unknown feature: {k}")
        if v is None:
            continue  # inherit
        if not isinstance(v, bool):
            raise ValueError(f"Feature {k} is true, false or null.")
        out[k] = v
    return out


def _whole(value, label: str, lo: int, hi: int) -> int:
    if isinstance(value, bool):
        raise ValueError(f"{label}: enter a whole number.")
    try:
        v = int(value)
    except (TypeError, ValueError):
        raise ValueError(f"{label}: enter a whole number.") from None
    if float(value) != v or not lo <= v <= hi:
        raise ValueError(f"{label}: a whole number from {lo} to {hi}.")
    return v


def create_plan(data: dict) -> dict:
    fields = _clean_plan(data, partial=False)
    if fields.get("limit_type") == "limited" and "generation_limit" not in fields:
        raise ValueError("Enter the generation limit for a limited plan.")
    now = time.time()
    with db.tx() as c:
        if c.execute("SELECT 1 FROM plans WHERE code = ?", (fields["code"],)).fetchone():
            raise ValueError("A plan with this code already exists.")
        if fields.get("is_default"):
            if fields.get("enabled") == 0:
                raise ValueError("The default plan must be enabled.")
            c.execute("UPDATE plans SET is_default = 0 WHERE is_default = 1")
        cols = list(fields) + ["created_at", "updated_at"]
        cur = c.execute(f"INSERT INTO plans ({', '.join(cols)}) VALUES ({', '.join('?' * len(cols))})",
                        list(fields.values()) + [now, now])
        return plan_dict(c.execute("SELECT * FROM plans WHERE id = ?", (cur.lastrowid,)).fetchone())


def update_plan(plan_id: int, data: dict) -> tuple[dict, dict]:
    """Returns (plan, changed fields as {name: [old, new]})."""
    fields = _clean_plan(data, partial=True)
    old = get_plan(plan_id)
    with db.tx() as c:
        if "code" in fields and c.execute("SELECT 1 FROM plans WHERE code = ? AND id != ?", (fields["code"], plan_id)).fetchone():
            raise ValueError("A plan with this code already exists.")
        becomes_default = fields.get("is_default") == 1
        enabled = fields.get("enabled", int(old["enabled"]))
        if (old["is_default"] or becomes_default) and not enabled:
            raise ValueError("The default plan cannot be disabled. Make another plan the default first.")
        if old["is_default"] and fields.get("is_default") == 0:
            raise ValueError("Choose another plan as the default instead; there must always be one.")
        if becomes_default:
            c.execute("UPDATE plans SET is_default = 0 WHERE is_default = 1 AND id != ?", (plan_id,))
        if fields:
            sets = ", ".join(f"{k} = ?" for k in fields)
            c.execute(f"UPDATE plans SET {sets}, updated_at = ? WHERE id = ?", list(fields.values()) + [time.time(), plan_id])
    new = get_plan(plan_id)
    changed = {k: [old[k], new[k]] for k in new if old.get(k) != new.get(k)}
    return new, changed


def delete_plan(plan_id: int) -> dict:
    plan = get_plan(plan_id)
    if plan["is_default"]:
        raise ValueError("The default plan cannot be deleted.")
    with db.tx() as c:
        if c.execute("SELECT 1 FROM subscriptions WHERE plan_id = ?", (plan_id,)).fetchone():
            raise ValueError("Accounts use this plan. Move them to another plan or disable it instead.")
        c.execute("DELETE FROM plans WHERE id = ?", (plan_id,))
    return plan


# --------------------------------------------------------------------------- subscriptions

def assign_default(c, user_id: int, actor_id: int | None = None) -> None:
    """Give a new account the default (Free) plan. Runs inside the caller's transaction,
    so an account never exists without a subscription."""
    plan_id = default_plan_id(c)
    now = time.time()
    c.execute("INSERT INTO subscriptions (user_id, plan_id, status, started_at, source, updated_at) "
              "VALUES (?, ?, 'active', ?, 'auto', ?)", (user_id, plan_id, now, now))
    c.execute("INSERT INTO subscription_history (user_id, event, to_plan_id, actor_id, note, at) VALUES (?, 'assigned', ?, ?, ?, ?)",
              (user_id, plan_id, actor_id, "Default plan for a new account", now))


def _sub(c, user_id: int):
    row = c.execute("SELECT s.*, p.sort_order, p.name AS plan_name FROM subscriptions s JOIN plans p ON p.id = s.plan_id "
                    "WHERE s.user_id = ?", (user_id,)).fetchone()
    if row is None:  # an account from before plans existed, or a repaired database
        assign_default(c, user_id)
        row = _sub(c, user_id)
    return row


def change_plan(user_id: int, plan_id: int, actor_id: int | None, expires_at: float | None | str = "keep",
                note: str = "") -> dict:
    """Manual upgrade / downgrade. ``expires_at``: a timestamp, None (no end) or "keep"."""
    now = time.time()
    with db.tx() as c:
        if not c.execute("SELECT 1 FROM users WHERE id = ?", (user_id,)).fetchone():
            raise LookupError("No such account.")
        target = c.execute("SELECT * FROM plans WHERE id = ?", (plan_id,)).fetchone()
        if not target:
            raise LookupError("No such plan.")
        cur = _sub(c, user_id)
        if not target["enabled"] and target["id"] != cur["plan_id"]:
            raise ValueError(f"The {target['name']} plan is disabled and cannot be assigned.")
        new_expiry = cur["expires_at"] if expires_at == "keep" else _expiry(expires_at, now)
        if target["id"] == cur["plan_id"]:
            event = "changed"
        else:
            event = "upgraded" if target["sort_order"] > cur["sort_order"] else "downgraded" if target["sort_order"] < cur["sort_order"] else "changed"
        started = now if target["id"] != cur["plan_id"] else cur["started_at"]
        c.execute("UPDATE subscriptions SET plan_id = ?, status = 'active', started_at = ?, expires_at = ?, source = 'manual', "
                  "payment_status = CASE WHEN source = 'payment' THEN payment_status ELSE 'none' END, updated_at = ? WHERE user_id = ?",
                  (plan_id, started, new_expiry, now, user_id))
        c.execute("INSERT INTO subscription_history (user_id, event, from_plan_id, to_plan_id, from_expires_at, to_expires_at, "
                  "actor_id, note, at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                  (user_id, event, cur["plan_id"], plan_id, cur["expires_at"], new_expiry, actor_id, note[:300], now))
    return {"event": event, "from": cur["plan_name"], "to": target["name"], "expires_at": new_expiry}


def set_expiry(user_id: int, expires_at: float | None, actor_id: int | None, note: str = "") -> dict:
    """Extend, shorten or remove the end date of the current subscription (re-activates it
    when the new date is in the future)."""
    now = time.time()
    new = _expiry(expires_at, now)
    with db.tx() as c:
        cur = _sub(c, user_id)
        status = "active" if new is None or new > now else cur["status"]
        c.execute("UPDATE subscriptions SET expires_at = ?, status = ?, updated_at = ? WHERE user_id = ?", (new, status, now, user_id))
        c.execute("INSERT INTO subscription_history (user_id, event, from_plan_id, to_plan_id, from_expires_at, to_expires_at, "
                  "actor_id, note, at) VALUES (?, 'extended', ?, ?, ?, ?, ?, ?, ?)",
                  (user_id, cur["plan_id"], cur["plan_id"], cur["expires_at"], new, actor_id, note[:300], now))
    return {"from": cur["expires_at"], "to": new}


def _expiry(value, now: float) -> float | None:
    if value in (None, ""):
        return None
    try:
        v = float(value)
    except (TypeError, ValueError):
        raise ValueError("The end date is not valid.") from None
    if v < now - 86400 * 365 * 50 or v > now + 86400 * 365 * 100:
        raise ValueError("The end date is not valid.")
    return v


def reset_usage(user_id: int, actor_id: int | None) -> None:
    now = time.time()
    with db.tx() as c:
        cur = _sub(c, user_id)
        c.execute("UPDATE subscriptions SET usage_reset_at = ?, updated_at = ? WHERE user_id = ?", (now, now, user_id))
        c.execute("INSERT INTO subscription_history (user_id, event, from_plan_id, to_plan_id, actor_id, note, at) "
                  "VALUES (?, 'usage_reset', ?, ?, ?, 'Usage counter reset', ?)", (user_id, cur["plan_id"], cur["plan_id"], actor_id, now))


def expire_due(now: float | None = None) -> int:
    """Mark subscriptions past their end date as expired (entitlements already treat them
    so; this records it once in the history). Run by the maintenance job."""
    now = now or time.time()
    with db.tx() as c:
        rows = c.execute("SELECT user_id, plan_id, expires_at FROM subscriptions WHERE status = 'active' "
                         "AND expires_at IS NOT NULL AND expires_at <= ?", (now,)).fetchall()
        for r in rows:
            c.execute("UPDATE subscriptions SET status = 'expired', updated_at = ? WHERE user_id = ?", (now, r["user_id"]))
            c.execute("INSERT INTO subscription_history (user_id, event, from_plan_id, to_plan_id, from_expires_at, note, at) "
                      "VALUES (?, 'expired', ?, ?, ?, 'Subscription ended; the default plan applies', ?)",
                      (r["user_id"], r["plan_id"], r["plan_id"], r["expires_at"], now))
    return len(rows)


def history(user_id: int, limit: int = 50) -> list[dict]:
    with db.tx() as c:
        rows = c.execute(
            "SELECT h.*, fp.name AS from_name, tp.name AS to_name, a.email AS actor_email FROM subscription_history h "
            "LEFT JOIN plans fp ON fp.id = h.from_plan_id LEFT JOIN plans tp ON tp.id = h.to_plan_id "
            "LEFT JOIN users a ON a.id = h.actor_id WHERE h.user_id = ? ORDER BY h.id DESC LIMIT ?", (user_id, limit)).fetchall()
    return [{"event": r["event"], "from_plan": r["from_name"], "to_plan": r["to_name"], "from_expires_at": r["from_expires_at"],
             "to_expires_at": r["to_expires_at"], "by": r["actor_email"] or "system", "note": r["note"], "at": r["at"]} for r in rows]


# --------------------------------------------------------------------------- per-account overrides

def set_override(user_id: int, actor_id: int | None, limit_type: str | None = "keep", generation_limit: int | None | str = "keep",
                 features: dict | None = None, note: str | None = None, limits: dict | None = None) -> dict | None:
    """Change an account's overrides. ``limit_type``: "unlimited", "limited" (with
    ``generation_limit`` = the custom allowance) or None (= the plan's). ``features``:
    {feature: true | false | null}, null removes that override; ``limits`` likewise
    {limit: number | null}. Returns the new overrides (None when nothing is overridden any more)."""
    now = time.time()
    with db.tx() as c:
        if not c.execute("SELECT 1 FROM users WHERE id = ?", (user_id,)).fetchone():
            raise LookupError("No such account.")
        row = c.execute("SELECT * FROM entitlement_overrides WHERE user_id = ?", (user_id,)).fetchone()
        cur_type = row["limit_type"] if row else None
        cur_limit = row["generation_limit"] if row else None
        cur_features = json.loads(row["features"]) if row else {}
        cur_limits = json.loads(row["limits"] or "{}") if row else {}
        cur_note = row["note"] if row else ""
        if limit_type != "keep":
            if limit_type not in (None, "limited", "unlimited"):
                raise ValueError("Limit is limited, unlimited or the plan's.")
            cur_type = limit_type
        if generation_limit != "keep":
            cur_limit = None if generation_limit in (None, "") else _whole(generation_limit, "Custom limit", 0, MAX_LIMIT)
        if cur_type == "limited" and cur_limit is None:
            raise ValueError("Enter the custom generation limit.")
        if cur_type != "limited":
            cur_limit = None
        if features is not None:
            if not isinstance(features, dict):
                raise ValueError("Features must be an object of feature: true/false/null.")
            for k, v in features.items():
                if k not in FEATURES:
                    raise ValueError(f"Unknown feature: {k}")
                if v is None:
                    cur_features.pop(k, None)
                elif isinstance(v, bool):
                    cur_features[k] = v
                else:
                    raise ValueError(f"Feature {k} is true, false or null.")
        if limits is not None:
            for k, v in clean_limits(limits, allow_null=True).items():
                if v is None:
                    cur_limits.pop(k, None)
                else:
                    cur_limits[k] = v
        cur_limits = {k: v for k, v in cur_limits.items() if k in LIMITS}
        if note is not None:
            cur_note = str(note).strip()[:300]
        if cur_type is None and not cur_features and not cur_limits and not cur_note:
            c.execute("DELETE FROM entitlement_overrides WHERE user_id = ?", (user_id,))
            return None
        c.execute("INSERT INTO entitlement_overrides (user_id, limit_type, generation_limit, features, limits, note, updated_by, "
                  "updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?) ON CONFLICT(user_id) DO UPDATE SET limit_type = excluded.limit_type, "
                  "generation_limit = excluded.generation_limit, features = excluded.features, limits = excluded.limits, "
                  "note = excluded.note, updated_by = excluded.updated_by, updated_at = excluded.updated_at",
                  (user_id, cur_type, cur_limit, json.dumps(cur_features), json.dumps(cur_limits), cur_note, actor_id, now))
    return {"limit_type": cur_type, "generation_limit": cur_limit, "features": cur_features, "limits": cur_limits,
            "note": cur_note}
