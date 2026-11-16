"""The Super Admin console API (/api/admin/...). Every route needs the Super Admin role,
checked on the server (auth.require_admin); every change is written to the audit log
(who, what, which account or resource, when - never passwords or keys)."""

from __future__ import annotations

from typing import Any, Optional

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from . import analytics, app_settings, audit, auth, entitlements, maintenance, presets, subscriptions, temp_projects, user_ai
from .ai import catalog as ai_catalog

router = APIRouter(prefix="/api/admin", dependencies=[Depends(auth.require_admin)])


def _label(uid: int) -> str:
    try:
        return auth.get_user(uid)["email"]
    except Exception:
        return f"#{uid}"


# --------------------------------------------------------------------------- users

class NewUserBody(BaseModel):
    email: str
    password: str
    username: Optional[str] = None
    display_name: Optional[str] = None
    role: str = auth.USER
    plan_id: Optional[int] = None  # None = the default plan


class UserPatch(BaseModel):
    email: Optional[str] = None
    username: Optional[str] = None  # "" removes it
    display_name: Optional[str] = None  # "" removes it
    active: Optional[bool] = None
    role: Optional[str] = None


class AdminPasswordBody(BaseModel):
    password: str


class SubscriptionBody(BaseModel):
    plan_id: int
    expires_at: Optional[float] = None
    keep_expiry: bool = True  # False: use expires_at (None = no end date)
    note: str = ""


class ExpiryBody(BaseModel):
    expires_at: Optional[float] = None  # None = no end date
    note: str = ""


@router.get("/users")
def users(q: str = "", plan: str = "", status: str = "", usage_status: str = "", sort: str = "email", page: int = 1,
          per_page: int = 50):
    return analytics.users_table(q, plan, status, usage_status, sort, page, per_page)


@router.post("/users")
def create_user(body: NewUserBody, admin: dict = Depends(auth.require_admin)):
    if body.plan_id is not None:
        subscriptions.get_plan(body.plan_id)  # exists, before anything is created
    user = auth.create_user(body.email, body.password, role=body.role, username=body.username,
                            display_name=body.display_name, actor_id=admin["id"])
    if body.plan_id is not None:
        try:
            subscriptions.change_plan(user["id"], body.plan_id, admin["id"], note="Plan chosen at creation")
        except ValueError:
            auth.delete_user(user["id"])  # no half-made account with the wrong plan
            raise
    audit.record(admin, "user.create", "user", user["id"], user["email"],
                 {"role": user["role"], "plan_id": body.plan_id or "default", "username": user["username"] or ""})
    return user


@router.get("/users/{uid}")
def user_detail(uid: int):
    detail = analytics.user_detail(uid)
    detail["sessions"] = auth.list_sessions(uid)
    return detail


@router.patch("/users/{uid}")
def update_user(uid: int, body: UserPatch, admin: dict = Depends(auth.require_admin)):
    before = auth.get_user(uid)
    after = auth.update_user(uid, body.email, body.active, body.username, body.display_name, body.role, actor_id=admin["id"])
    changed = {k: f"{before[k]} -> {after[k]}" for k in ("email", "username", "display_name", "role", "active") if before[k] != after[k]}
    if changed:
        action = "user.deactivate" if changed.keys() == {"active"} and not after["active"] else \
            "user.activate" if changed.keys() == {"active"} else "user.update"
        audit.record(admin, action, "user", uid, after["email"], changed)
    return after


@router.post("/users/{uid}/password")
def reset_password(uid: int, body: AdminPasswordBody, admin: dict = Depends(auth.require_admin)):
    auth.set_password(uid, body.password)  # the user is signed out everywhere
    audit.record(admin, "user.password_reset", "user", uid, _label(uid))
    return {"ok": True}


@router.post("/users/{uid}/sessions/revoke")
def revoke_sessions(uid: int, admin: dict = Depends(auth.require_admin)):
    auth.get_user(uid)
    if uid == admin["id"]:
        raise ValueError("Use your account page to end your own other sessions.")
    n = auth.end_sessions(uid)
    audit.record(admin, "user.sessions_revoked", "user", uid, _label(uid), {"sessions": n})
    return {"ended": n}


@router.delete("/users/{uid}")
def delete_user(uid: int, admin: dict = Depends(auth.require_admin)):
    target = auth.delete_user(uid, actor_id=admin["id"])
    audit.record(admin, "user.delete", "user", uid, target["email"])
    return {"deleted": True}


@router.put("/users/{uid}/subscription")
def change_subscription(uid: int, body: SubscriptionBody, admin: dict = Depends(auth.require_admin)):
    expiry = "keep" if body.keep_expiry else body.expires_at
    result = subscriptions.change_plan(uid, body.plan_id, admin["id"], expiry, body.note)
    audit.record(admin, f"subscription.{result['event']}", "user", uid, _label(uid),
                 {"from": result["from"], "to": result["to"], "expires_at": result["expires_at"]})
    return entitlements.effective(uid).to_dict()


@router.put("/users/{uid}/expiry")
def change_expiry(uid: int, body: ExpiryBody, admin: dict = Depends(auth.require_admin)):
    auth.get_user(uid)
    result = subscriptions.set_expiry(uid, body.expires_at, admin["id"], body.note)
    audit.record(admin, "subscription.expiry", "user", uid, _label(uid), {"from": result["from"], "to": result["to"]})
    return entitlements.effective(uid).to_dict()


@router.put("/users/{uid}/overrides")
def change_overrides(uid: int, body: dict[str, Any], admin: dict = Depends(auth.require_admin)):
    """Body keys are optional: ``limit_type`` ("unlimited" | "limited" | null = plan's),
    ``generation_limit`` (with "limited"), ``features`` {key: true | false | null},
    ``limits`` {key: number | null} (entitlements.LIMITS), ``note``."""
    unknown = set(body) - {"limit_type", "generation_limit", "features", "limits", "note"}
    if unknown:
        raise ValueError(f"Unknown fields: {', '.join(sorted(unknown))}")
    result = subscriptions.set_override(
        uid, admin["id"], body.get("limit_type", "keep"), body.get("generation_limit", "keep"), body.get("features"),
        body.get("note"), body.get("limits"))
    detail = {"limit_type": (result or {}).get("limit_type") or "plan", "limit": (result or {}).get("generation_limit")}
    for k, v in ((result or {}).get("features") or {}).items():
        detail[f"feature.{k}"] = v
    for k, v in ((result or {}).get("limits") or {}).items():
        detail[f"limit.{k}"] = v
    action = "entitlement.unlimited" if (result or {}).get("limit_type") == "unlimited" else "entitlement.update"
    audit.record(admin, action, "user", uid, _label(uid), detail)
    return entitlements.effective(uid).to_dict()


@router.post("/users/{uid}/usage/reset")
def reset_usage(uid: int, admin: dict = Depends(auth.require_admin)):
    auth.get_user(uid)
    before = entitlements.effective(uid).used
    subscriptions.reset_usage(uid, admin["id"])
    audit.record(admin, "usage.reset", "user", uid, _label(uid), {"used_before": before})
    return entitlements.effective(uid).to_dict()


# --------------------------------------------------------------------------- plans

@router.get("/plans")
def plans():
    return {"plans": subscriptions.list_plans(), "features": entitlements.catalog(), "limits": entitlements.limits_catalog()}


@router.post("/plans")
def create_plan(body: dict[str, Any], admin: dict = Depends(auth.require_admin)):
    plan = subscriptions.create_plan(body)
    audit.record(admin, "plan.create", "plan", plan["id"], plan["name"],
                 {"limit_type": plan["limit_type"], "limit": plan["generation_limit"], "period": plan["period"]})
    return plan


@router.patch("/plans/{plan_id}")
def update_plan(plan_id: int, body: dict[str, Any], admin: dict = Depends(auth.require_admin)):
    plan, changed = subscriptions.update_plan(plan_id, body)
    if changed:
        audit.record(admin, "plan.update", "plan", plan_id, plan["name"], {k: f"{v[0]} -> {v[1]}" for k, v in changed.items()})
    return plan


@router.delete("/plans/{plan_id}")
def delete_plan(plan_id: int, admin: dict = Depends(auth.require_admin)):
    plan = subscriptions.delete_plan(plan_id)
    audit.record(admin, "plan.delete", "plan", plan_id, plan["name"])
    return {"deleted": True}


# --------------------------------------------------------------------------- built-in presets

@router.get("/presets")
def builtin_presets():
    """Every built-in preset with its tier, and how many presets accounts saved (counts only)."""
    return presets.admin_view()


@router.put("/presets")
def change_preset_tiers(body: dict[str, Any], admin: dict = Depends(auth.require_admin)):
    """``{"access": {preset id: "basic" | "premium" | null}}``: move built-ins between tiers
    (null = back to the preset's own tier)."""
    if set(body) - {"access"}:
        raise ValueError("Send {\"access\": {preset: tier}}.")
    changes = presets.set_access(body.get("access") or {}, admin["id"])
    if changes:
        audit.record(admin, "presets.access", "settings", "", "Built-in presets",
                     {k: f"{a} -> {b}" for k, (a, b) in changes.items()})
    return presets.admin_view()


# --------------------------------------------------------------------------- analytics, AI, settings, audit

@router.get("/analytics")
def analytics_overview(start: Optional[str] = None, end: Optional[str] = None):
    """``start`` / ``end``: YYYY-MM-DD (UTC, inclusive); default the last 30 days."""
    return analytics.overview(start, end)


@router.get("/ai")
def ai_overview(start: Optional[str] = None, end: Optional[str] = None):
    """Which AI services the server offers, how many accounts set up which, and usage.
    Keys are never part of this (only whether the server has one)."""
    from_day, to_day = analytics.date_range(start, end)
    return {"providers": [{k: p[k] for k in ("id", "label", "needs_key", "capabilities", "server_configured")} for p in ai_catalog()],
            "accounts": user_ai.summary_counts(),
            "usage": analytics.ai_stats(from_day * analytics.DAY, (to_day + 1) * analytics.DAY),
            "range": {"from": analytics.iso(from_day), "to": analytics.iso(to_day)}}


@router.get("/settings")
def get_settings():
    return {"settings": app_settings.all_settings(), "open_projects": temp_projects.open_count()}


@router.put("/settings")
def put_settings(body: dict[str, Any], admin: dict = Depends(auth.require_admin)):
    changes = app_settings.update(body, admin["id"])
    if changes:
        audit.record(admin, "settings.update", "settings", "", "Application settings",
                     {k: f"{a} -> {b}" for k, (a, b) in changes.items()})
    return {"settings": app_settings.all_settings()}


@router.get("/audit")
def audit_log(q: str = "", before: Optional[int] = None, user_id: Optional[int] = None, limit: int = 100):
    return {"entries": audit.entries(limit, before, "user" if user_id else None, str(user_id) if user_id else None, q)}


@router.post("/maintenance/run")
def run_maintenance(admin: dict = Depends(auth.require_admin)):
    done = maintenance.run_once()
    audit.record(admin, "maintenance.run", "system", "", "Clean-up", done)
    return done
