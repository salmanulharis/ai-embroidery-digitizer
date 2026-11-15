"""Plans, subscriptions, overrides and the generation allowance: automatic Free plan,
upgrade / downgrade / expiry, disabled plans, overrides winning over plans, what counts as
a generation, failed downloads not charged, limits, unlimited, concurrency and duplicates."""

import threading
import time
import uuid

import pytest

from accounts import admin, new_user, png, real_login, stitched_project  # noqa: F401
from embroidery_app import db, entitlements, subscriptions, usage
from embroidery_app.entitlements import GENERATION

pytestmark = pytest.mark.usefixtures("real_login")


def plan_id(code: str) -> int:
    return next(p["id"] for p in subscriptions.list_plans() if p["code"] == code)


def me(c) -> dict:
    return c.get("/api/me/account").json()


# --------------------------------------------------------------------------- Free plan + plan changes

def test_new_accounts_get_the_free_plan_automatically():
    a = admin()
    user, c = new_user(a)
    acc = me(c)
    assert acc["plan"]["code"] == "free" and acc["subscription"]["status"] == "active"
    assert acc["usage"] == {"used": 0, "limit": 10, "remaining": 10, "unlimited": False, "period": "month",
                            "resets_at": acc["usage"]["resets_at"], "plan": "Free"}
    hist = subscriptions.history(user["id"])
    assert [h["event"] for h in hist] == ["assigned"] and hist[0]["to_plan"] == "Free"
    # the admin may choose another plan when creating the account
    user2, c2 = new_user(a, plan_id=plan_id("basic"))
    assert me(c2)["plan"]["code"] == "basic" and me(c2)["usage"]["limit"] == 100


def test_upgrade_downgrade_and_expiry_are_recorded():
    a = admin()
    user, c = new_user(a)
    uid = user["id"]
    r = a.put(f"/api/admin/users/{uid}/subscription", json={"plan_id": plan_id("pro")})
    assert r.status_code == 200 and r.json()["unlimited"] and r.json()["plan"]["code"] == "pro"
    a.put(f"/api/admin/users/{uid}/subscription", json={"plan_id": plan_id("basic"), "keep_expiry": False,
                                                       "expires_at": time.time() + 30 * 86400})
    ent = entitlements.effective(uid)
    assert ent.plan["code"] == "basic" and ent.subscription["expires_at"] > time.time()
    # an expired subscription falls back to the default plan, it never locks the account out
    a.put(f"/api/admin/users/{uid}/expiry", json={"expires_at": time.time() - 60})
    ent = entitlements.effective(uid)
    assert ent.subscription["expired"] and ent.plan["code"] == "free" and ent.limit == 10
    assert subscriptions.expire_due() >= 1
    assert entitlements.effective(uid).subscription["status"] == "expired"
    # extending re-activates it
    a.put(f"/api/admin/users/{uid}/expiry", json={"expires_at": time.time() + 86400})
    assert entitlements.effective(uid).plan["code"] == "basic"
    events = [h["event"] for h in subscriptions.history(uid)]
    assert events[:5] == ["extended", "expired", "extended", "downgraded", "upgraded"]
    audit = a.get(f"/api/admin/audit?user_id={uid}").json()["entries"]
    assert {"subscription.upgraded", "subscription.downgraded", "subscription.expiry"} <= {e["action"] for e in audit}


def test_disabled_and_default_plans_are_protected():
    a = admin()
    code = f"t{uuid.uuid4().hex[:8]}"
    plan = a.post("/api/admin/plans", json={"code": code, "name": "Trial", "limit_type": "limited", "generation_limit": 3}).json()
    user, _ = new_user(a)
    assert a.patch(f"/api/admin/plans/{plan['id']}", json={"enabled": False}).json()["enabled"] is False
    r = a.put(f"/api/admin/users/{user['id']}/subscription", json={"plan_id": plan["id"]})
    assert r.status_code == 400 and "disabled" in r.json()["detail"]
    free = plan_id("free")
    assert a.patch(f"/api/admin/plans/{free}", json={"enabled": False}).status_code == 400
    assert a.delete(f"/api/admin/plans/{free}").status_code == 400
    # a plan in use cannot be deleted; an unused one can
    a.patch(f"/api/admin/plans/{plan['id']}", json={"enabled": True})
    a.put(f"/api/admin/users/{user['id']}/subscription", json={"plan_id": plan["id"]})
    assert a.delete(f"/api/admin/plans/{plan['id']}").status_code == 400
    a.put(f"/api/admin/users/{user['id']}/subscription", json={"plan_id": free})
    assert a.delete(f"/api/admin/plans/{plan['id']}").status_code == 200
    # validation
    assert a.post("/api/admin/plans", json={"code": "Bad Code", "name": "x", "limit_type": "limited", "generation_limit": 1}).status_code == 400
    assert a.post("/api/admin/plans", json={"code": "nolimit", "name": "x", "limit_type": "limited"}).status_code == 400
    assert a.post("/api/admin/plans", json={"code": "feat", "name": "x", "limit_type": "unlimited", "features": {"nope": True}}).status_code == 400


def test_only_one_default_plan():
    a = admin()
    code = f"d{uuid.uuid4().hex[:8]}"
    p = a.post("/api/admin/plans", json={"code": code, "name": "New default", "limit_type": "limited", "generation_limit": 5,
                                         "is_default": True}).json()
    try:
        plans = subscriptions.list_plans()
        assert [x["code"] for x in plans if x["is_default"]] == [code]
        user, c = new_user(a)
        assert me(c)["plan"]["code"] == code
    finally:
        a.patch(f"/api/admin/plans/{plan_id('free')}", json={"is_default": True})
        a.put(f"/api/admin/users/{user['id']}/subscription", json={"plan_id": plan_id("free")})
        assert a.delete(f"/api/admin/plans/{p['id']}").status_code == 200
    assert [x["code"] for x in subscriptions.list_plans() if x["is_default"]] == ["free"]


# --------------------------------------------------------------------------- what counts

def test_one_design_counts_once_whatever_is_downloaded():
    a = admin()
    _, c = new_user(a)
    pid = stitched_project(c)
    assert c.get(f"/api/projects/{pid}/quota").json()["charged"] is False
    assert c.get(f"/api/projects/{pid}/export/svg").status_code == 200  # artwork only: free
    assert me(c)["usage"]["used"] == 0
    for fmt in ("dst", "pes", "png", "txt", "wilcom", "dst"):
        assert c.get(f"/api/projects/{pid}/export/{fmt}").status_code == 200, fmt
    assert c.get(f"/api/projects/{pid}/export/zip?formats=dst,exp").status_code == 200
    assert me(c)["usage"]["used"] == 1
    assert c.get(f"/api/projects/{pid}/quota").json()["charged"] is True
    pid2 = stitched_project(c, 3)  # a new upload is a new design
    c.get(f"/api/projects/{pid2}/export/dst")
    assert me(c)["usage"] == me(c)["usage"] | {"used": 2, "remaining": 8}


def test_failed_downloads_are_not_charged():
    a = admin()
    user, c = new_user(a)
    pid = c.post("/api/projects", files={"file": ("x.png", png())}).json()["id"]
    r = c.get(f"/api/projects/{pid}/export/dst")  # not prepared yet: an error, not a generation
    assert r.status_code == 400
    assert me(c)["usage"]["used"] == 0
    with db.tx() as conn:
        row = conn.execute("SELECT status, counted FROM usage_records WHERE user_id = ? AND kind = ?", (user["id"], GENERATION)).fetchone()
    assert row["status"] == "failed" and row["counted"] == 0
    # and the design can still be charged normally later
    c.post(f"/api/projects/{pid}/extract", json={"options": {"width_mm": 60}})
    c.post(f"/api/projects/{pid}/stitch", json={"params": {}})
    assert c.get(f"/api/projects/{pid}/export/dst").status_code == 200 and me(c)["usage"]["used"] == 1


def test_limit_reached_blocks_new_designs_with_a_clear_answer():
    a = admin()
    user, c = new_user(a)
    a.put(f"/api/admin/users/{user['id']}/overrides", json={"limit_type": "limited", "generation_limit": 1})
    pid = stitched_project(c)
    assert c.get(f"/api/projects/{pid}/export/dst").status_code == 200
    assert c.get(f"/api/projects/{pid}/export/pes").status_code == 200  # the same design again: free
    pid2 = stitched_project(c, 5)
    r = c.get(f"/api/projects/{pid2}/export/dst")
    body = r.json()
    assert r.status_code == 403 and body["code"] == "quota_exceeded"
    assert body["usage"]["used"] == 1 and body["usage"]["limit"] == 1 and body["usage"]["remaining"] == 0
    assert "administrator" in body["detail"]
    q = c.get(f"/api/projects/{pid2}/quota").json()
    assert q == q | {"charged": False} and q["usage"]["remaining"] == 0
    with db.tx() as conn:
        assert conn.execute("SELECT COUNT(*) FROM usage_records WHERE user_id = ? AND status = 'rejected'", (user["id"],)).fetchone()[0] == 1
    # the admin resets the counter: the user can continue
    assert a.post(f"/api/admin/users/{user['id']}/usage/reset").json()["used"] == 0
    assert c.get(f"/api/projects/{pid2}/export/dst").status_code == 200


def test_unlimited_users_are_never_blocked_but_still_counted():
    a = admin()
    user, c = new_user(a)
    a.put(f"/api/admin/users/{user['id']}/overrides", json={"limit_type": "limited", "generation_limit": 0})
    pid = stitched_project(c)
    assert c.get(f"/api/projects/{pid}/export/dst").status_code == 403
    ent = a.put(f"/api/admin/users/{user['id']}/overrides", json={"limit_type": "unlimited"}).json()
    assert ent["unlimited"] and ent["limit"] is None and ent["remaining"] is None and ent["limit_source"] == "override"
    assert c.get(f"/api/projects/{pid}/export/dst").status_code == 200
    assert me(c)["usage"]["used"] == 1 and me(c)["usage"]["unlimited"] is True
    # revoking unlimited goes back to the plan's allowance
    ent = a.put(f"/api/admin/users/{user['id']}/overrides", json={"limit_type": None}).json()
    assert ent["limit"] == 10 and ent["limit_source"] == "plan"


def test_super_admin_is_unlimited_with_every_feature():
    e = entitlements.effective(next(u["id"] for u in subscriptions_users() if u["role"] == "super_admin"))
    assert e.unlimited and e.limit_source == "role" and all(e.features.values())


def subscriptions_users():
    with db.tx() as c:
        return [dict(r) for r in c.execute("SELECT id, role FROM users")]


def test_last_months_downloads_do_not_count_this_month():
    a = admin()
    user, _ = new_user(a)
    month_start, _ = entitlements.month_bounds(time.time())
    with db.tx() as conn:
        for i, at in enumerate((month_start - 3600, month_start - 86400 * 3, month_start + 60)):
            conn.execute("INSERT INTO usage_records (user_id, kind, status, counted, dedupe_key, created_at) "
                         "VALUES (?, ?, 'succeeded', 1, ?, ?)", (user["id"], GENERATION, f"k{i}", at))
    assert entitlements.effective(user["id"]).used == (1 if time.time() > month_start + 60 else 0)


# --------------------------------------------------------------------------- overrides + features

def test_plan_features_and_user_overrides():
    a = admin()
    code = f"f{uuid.uuid4().hex[:8]}"
    plan = a.post("/api/admin/plans", json={"code": code, "name": "DST only", "limit_type": "limited", "generation_limit": 5,
                                            "features": {"all_formats": False, "zip_export": False, "photo_stitch": False}}).json()
    user, c = new_user(a, plan_id=plan["id"])
    pid = stitched_project(c)
    r = c.get(f"/api/projects/{pid}/export/pes")
    assert r.status_code == 403 and r.json()["code"] == "feature_unavailable" and "PES" in r.json()["detail"]
    assert c.get(f"/api/projects/{pid}/export/zip?formats=dst").status_code == 403
    assert c.post(f"/api/projects/{pid}/stitch", json={"params": {"photo_stitch": True}}).status_code == 403
    assert me(c)["usage"]["used"] == 0  # refused before anything was charged
    assert c.get(f"/api/projects/{pid}/export/dst").status_code == 200
    assert c.get(f"/api/projects/{pid}/export/wilcom").status_code == 200  # DST-based: allowed on a DST-only plan
    # an individual override wins over the plan
    a.put(f"/api/admin/users/{user['id']}/overrides", json={"features": {"all_formats": True}})
    assert c.get(f"/api/projects/{pid}/export/pes").status_code == 200
    feats = {f["key"]: f["on"] for f in me(c)["features"]}
    assert feats["all_formats"] and not feats["zip_export"]
    # null removes the override again
    a.put(f"/api/admin/users/{user['id']}/overrides", json={"features": {"all_formats": None}})
    assert not entitlements.effective(user["id"]).has("all_formats")
    a.put(f"/api/admin/users/{user['id']}/subscription", json={"plan_id": plan_id("free")})
    a.delete(f"/api/admin/plans/{plan['id']}")


def test_unlimited_is_explicit_not_a_big_number():
    a = admin()
    r = a.post("/api/admin/plans", json={"code": f"u{uuid.uuid4().hex[:8]}", "name": "Unl", "limit_type": "unlimited"})
    p = r.json()
    assert p["limit_type"] == "unlimited"
    user, _ = new_user(a, plan_id=p["id"])
    e = entitlements.effective(user["id"])
    assert e.unlimited and e.limit is None and e.remaining is None and e.can_generate()
    a.put(f"/api/admin/users/{user['id']}/subscription", json={"plan_id": plan_id("free")})
    a.delete(f"/api/admin/plans/{p['id']}")


# --------------------------------------------------------------------------- safety under concurrency

def test_parallel_requests_cannot_exceed_the_limit():
    a = admin()
    user, _ = new_user(a)
    subscriptions.set_override(user["id"], None, limit_type="limited", generation_limit=3)
    results, lock = [], threading.Lock()

    def attempt(i):
        try:
            usage.begin_generation(user["id"], f"{i:012x}", "export:dst").succeed()
            out = "ok"
        except entitlements.EntitlementError:
            out = "refused"
        with lock:
            results.append(out)

    threads = [threading.Thread(target=attempt, args=(i,)) for i in range(12)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert results.count("ok") == 3 and results.count("refused") == 9
    assert entitlements.effective(user["id"]).used == 3


def test_the_same_design_is_never_charged_twice():
    a = admin()
    user, _ = new_user(a)
    pid = "abcdefabcdef"
    tickets = []

    def attempt():
        tickets.append(usage.begin_generation(user["id"], pid, "export:dst"))

    threads = [threading.Thread(target=attempt) for _ in range(8)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert sum(t.charged for t in tickets) == 1
    [t.succeed() for t in tickets]
    assert entitlements.effective(user["id"]).used == 1
    # a retry after a failure is charged once
    other = usage.begin_generation(user["id"], "0123456789ab", "export:dst")
    other.fail("RuntimeError")
    assert usage.begin_generation(user["id"], "0123456789ab", "export:dst").charged
    assert entitlements.effective(user["id"]).used == 2


def test_interrupted_reservations_are_released():
    a = admin()
    user, _ = new_user(a)
    usage.begin_generation(user["id"], "fedcbafedcba", "export:dst")  # never finished (crash)
    assert entitlements.effective(user["id"]).used == 1
    assert usage.cancel_stale(time.time() + usage.STALE_RESERVATION_S + 1) >= 1
    assert entitlements.effective(user["id"]).used == 0
    with db.tx() as conn:
        assert conn.execute("SELECT status FROM usage_records WHERE user_id = ?", (user["id"],)).fetchone()["status"] == "cancelled"
