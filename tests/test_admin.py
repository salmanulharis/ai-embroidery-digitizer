"""Super Admin console: authorization, user management, sessions, settings, audit log,
and analytics accuracy (counts, ratios, date filters, per-user details)."""

import json
import time
from datetime import datetime, timezone

import pytest

from accounts import PASSWORD, admin, browser, logged_in, new_user, real_login, stitched_project  # noqa: F401
from embroidery_app import analytics, auth, db, entitlements

pytestmark = pytest.mark.usefixtures("real_login")

ADMIN_ROUTES = [("GET", "/api/admin/users"), ("POST", "/api/admin/users"), ("GET", "/api/admin/users/1"),
                ("PATCH", "/api/admin/users/1"), ("DELETE", "/api/admin/users/1"), ("PUT", "/api/admin/users/1/subscription"),
                ("PUT", "/api/admin/users/1/overrides"), ("POST", "/api/admin/users/1/usage/reset"),
                ("POST", "/api/admin/users/1/sessions/revoke"), ("GET", "/api/admin/plans"), ("POST", "/api/admin/plans"),
                ("GET", "/api/admin/analytics"), ("GET", "/api/admin/ai"), ("GET", "/api/admin/settings"),
                ("PUT", "/api/admin/settings"), ("GET", "/api/admin/audit"), ("POST", "/api/admin/maintenance/run")]


def test_admin_routes_need_the_super_admin_role():
    _, user = new_user(admin())
    for method, path in ADMIN_ROUTES:
        assert user.request(method, path, json={}).status_code == 403, path
        assert browser().request(method, path, json={}).status_code == 401, path


def test_user_management_end_to_end_with_audit():
    a = admin()
    user, c = new_user(a, display_name="Cara Weaver", username=f"cara{int(time.time() * 1000) % 10**8}")
    uid = user["id"]
    assert user["display_name"] == "Cara Weaver"
    r = a.patch(f"/api/admin/users/{uid}", json={"display_name": "Cara W."}).json()
    assert r["display_name"] == "Cara W."
    # deactivate: signed out everywhere and cannot sign in
    a.patch(f"/api/admin/users/{uid}", json={"active": False})
    assert c.get("/api/auth/me").status_code == 401
    r = browser().post("/api/auth/login", json={"login": user["email"], "password": PASSWORD})
    assert r.status_code == 403 and "deactivated" in r.json()["detail"]
    a.patch(f"/api/admin/users/{uid}", json={"active": True})
    c = logged_in(user["email"])
    # roles: promoted users can use the console after signing in again
    a.patch(f"/api/admin/users/{uid}", json={"role": "super_admin"})
    assert c.get("/api/auth/me").status_code == 401
    c = logged_in(user["email"])
    assert c.get("/api/admin/users").status_code == 200
    me_id = c.get("/api/auth/me").json()["user"]["id"]
    assert c.patch(f"/api/admin/users/{me_id}", json={"role": "user"}).status_code == 400  # not their own role
    a.patch(f"/api/admin/users/{uid}", json={"role": "user"})
    # the deployment's Super Admin is protected
    boss = next(u for u in a.get("/api/admin/users?q=admin@example.com").json()["users"])
    assert a.patch(f"/api/admin/users/{boss['id']}", json={"active": False}).status_code == 400
    assert a.patch(f"/api/admin/users/{boss['id']}", json={"role": "user"}).status_code == 400
    assert a.delete(f"/api/admin/users/{boss['id']}").status_code == 400
    # a password reset never shows up in the audit log
    a.post(f"/api/admin/users/{uid}/password", json={"password": "brand-new-password-77"})
    entries = a.get(f"/api/admin/audit?user_id={uid}").json()["entries"]
    actions = [e["action"] for e in entries]
    assert {"user.create", "user.update", "user.deactivate", "user.activate", "user.password_reset"} <= set(actions)
    assert "brand-new-password-77" not in json.dumps(entries) and PASSWORD not in json.dumps(entries)
    assert all(e["actor"] == "admin@example.com" and e["at"] for e in entries)
    a.delete(f"/api/admin/users/{uid}")
    assert any(e["action"] == "user.delete" for e in a.get(f"/api/admin/audit?user_id={uid}").json()["entries"])


def test_session_lengths_and_revoking_sessions():
    a = admin()
    user, _ = new_user(a)
    cfg = browser().get("/api/config").json()
    assert cfg["session_choices"][0] == 0 and 7 in cfg["session_choices"]
    c = browser()
    r = c.post("/api/auth/login", json={"login": user["email"], "password": PASSWORD, "days": 7})
    assert r.json()["days"] == 7 and f"max-age={7 * 86400}" in r.headers["set-cookie"].lower()
    r = browser().post("/api/auth/login", json={"login": user["email"], "password": PASSWORD, "days": 3})
    assert r.status_code == 400
    phone = logged_in(user["email"])
    sessions = c.get("/api/me/account").json()["sessions"]
    assert len(sessions) == 3 and sum(s["current"] for s in sessions) == 1  # new_user signed in once too
    other = sessions[0] if not sessions[0]["current"] else sessions[1]  # most recently seen first: the phone
    assert c.delete(f"/api/me/sessions/{other['id']}").status_code == 200
    assert phone.get("/api/auth/me").status_code == 401 and c.get("/api/auth/me").status_code == 200
    # the admin can sign a user out everywhere
    assert a.post(f"/api/admin/users/{user['id']}/sessions/revoke").json()["ended"] == 2
    assert c.get("/api/auth/me").status_code == 401


def test_users_can_edit_their_profile_but_not_their_identity():
    _, c = new_user(admin())
    assert c.patch("/api/me/profile", json={"display_name": "  Dana   Stitch "}).json()["user"]["display_name"] == "Dana Stitch"
    assert c.patch("/api/me/profile", json={"display_name": "x" * 200}).status_code == 400


def test_settings_are_validated_and_audited():
    a = admin()
    s = {x["key"]: x for x in a.get("/api/admin/settings").json()["settings"]}
    assert s["project_idle_minutes"]["value"] == 120
    assert a.put("/api/admin/settings", json={"project_idle_minutes": 5}).status_code == 400
    assert a.put("/api/admin/settings", json={"nope": 1}).status_code == 400
    try:
        a.put("/api/admin/settings", json={"project_idle_minutes": 90})
        assert {x["key"]: x for x in a.get("/api/admin/settings").json()["settings"]}["project_idle_minutes"]["value"] == 90
        entry = a.get("/api/admin/audit?q=settings").json()["entries"][0]
        assert entry["action"] == "settings.update" and entry["detail"]["project_idle_minutes"] == "120 -> 90"
    finally:
        a.put("/api/admin/settings", json={"project_idle_minutes": 120})


def test_auto_update_switch_reaches_the_editor():
    a = admin()
    s = {x["key"]: x for x in a.get("/api/admin/settings").json()["settings"]}["auto_update"]
    assert (s["value"], s["min"], s["max"], s["default"]) == (1, 0, 1, 1)
    assert browser().get("/api/config").json()["auto_update"] is True
    assert a.put("/api/admin/settings", json={"auto_update": 2}).status_code == 400
    try:
        assert a.put("/api/admin/settings", json={"auto_update": 0}).status_code == 200
        assert browser().get("/api/config").json()["auto_update"] is False
        entry = a.get("/api/admin/audit?q=settings").json()["entries"][0]
        assert entry["detail"]["auto_update"] == "1 -> 0"
    finally:
        a.put("/api/admin/settings", json={"auto_update": 1})
    assert browser().get("/api/config").json()["auto_update"] is True


# --------------------------------------------------------------------------- analytics

def _ts(day: str, hour: int = 12) -> float:
    d = datetime.fromisoformat(day).replace(hour=hour, tzinfo=timezone.utc)
    return d.timestamp()


def test_analytics_counts_ratios_and_date_filter():
    """Synthetic records in 2001, far from anything else in the test database."""
    a = admin()
    u1, _ = new_user(a)
    u2, _ = new_user(a)
    rows = [  # (user, status, day, provider, model, kind)
        (u1, "succeeded", "2001-03-01", None, None, "generation"),
        (u1, "succeeded", "2001-03-01", None, None, "generation"),
        (u1, "failed", "2001-03-02", None, None, "generation"),
        (u2, "succeeded", "2001-03-02", None, None, "generation"),
        (u2, "rejected", "2001-03-03", None, None, "generation"),
        (u2, "succeeded", "2001-04-10", None, None, "generation"),  # outside the range
        (u1, "succeeded", "2001-03-02", "openai", "gpt-x", "ai_suggest"),
        (u1, "failed", "2001-03-02", "openai", "gpt-x", "ai_review"),
        (u2, "succeeded", "2001-03-03", "gemini", "g-1", "ai_suggest"),
        (u2, "succeeded", "2001-03-03", "builtin", None, "ai_suggest"),  # built-in: not an AI request
    ]
    with db.tx() as c:
        for i, (u, status, day, prov, model, kind) in enumerate(rows):
            c.execute("INSERT INTO usage_records (user_id, kind, feature, status, counted, dedupe_key, provider, model, duration_ms, "
                      "tokens_in, tokens_out, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                      (u["id"], kind, "export:dst" if kind == "generation" else "ai", status,
                       int(status == "succeeded" and kind == "generation"), f"a{i}", prov, model, 1000 + i,
                       100 if prov == "openai" else None, 20 if prov == "openai" else None, _ts(day)))
        for u, day in ((u1, "2001-03-01"), (u1, "2001-03-02"), (u2, "2001-03-02")):
            c.execute("INSERT INTO daily_activity (day, user_id) VALUES (?, ?)", (int(_ts(day) // 86400), u["id"]))
    r = a.get("/api/admin/analytics?start=2001-03-01&end=2001-03-31").json()
    g = r["generations"]
    assert (g["requests"], g["succeeded"], g["failed"], g["rejected"]) == (5, 3, 1, 1)
    assert g["success_rate"] == 75.0  # 3 of the 4 attempts that were allowed to run
    assert r["users"]["active_in_range"] == 2 and g["per_active_user"] == 1.5
    assert r["range"] == {"from": "2001-03-01", "to": "2001-03-31", "days": 31}
    series = {d["day"]: d for d in g["series"]}
    assert series["2001-03-01"]["succeeded"] == 2 and series["2001-03-02"] == {"day": "2001-03-02", "succeeded": 1, "failed": 1, "rejected": 0}
    assert {d["day"]: d["n"] for d in r["users"]["dau_series"]}["2001-03-02"] == 2
    assert g["top_users"][0]["id"] == u1["id"] and g["top_users"][0]["n"] == 2
    ai = r["ai"]
    assert ai["total"]["requests"] == 3 and ai["total"]["succeeded"] == 2 and ai["total"]["failed"] == 1
    assert ai["by_provider"][0] == {"provider": "openai", "requests": 2, "succeeded": 1, "failed": 1, "avg_ms": 1006,
                                    "tokens_in": 200, "tokens_out": 40}
    assert {m["model"] for m in ai["by_model"]} == {"gpt-x", "g-1"}
    # a narrower range
    r = a.get("/api/admin/analytics?start=2001-03-02&end=2001-03-02").json()
    assert r["generations"]["requests"] == 2 and r["generations"]["succeeded"] == 1
    assert r["users"]["dau_today"] == 2 and r["range"]["days"] == 1
    assert a.get("/api/admin/analytics?start=2001-03-05&end=2001-03-01").status_code == 400
    assert a.get("/api/admin/analytics?start=1990-01-01&end=2001-03-01").status_code == 400  # too long


def test_subscription_stats_and_user_table_filters():
    a = admin()
    unl, _ = new_user(a)
    a.put(f"/api/admin/users/{unl['id']}/overrides", json={"limit_type": "unlimited"})
    full, cf = new_user(a)
    a.put(f"/api/admin/users/{full['id']}/overrides", json={"limit_type": "limited", "generation_limit": 1})
    pid = stitched_project(cf)
    cf.get(f"/api/projects/{pid}/export/dst")
    near, _ = new_user(a)
    a.put(f"/api/admin/users/{near['id']}/overrides", json={"limit_type": "limited", "generation_limit": 5})
    with db.tx() as c:
        for i in range(4):
            c.execute("INSERT INTO usage_records (user_id, kind, status, counted, dedupe_key, created_at) "
                      "VALUES (?, 'generation', 'succeeded', 1, ?, ?)", (near["id"], f"n{i}", time.time()))
    table = {u["id"]: u for u in a.get("/api/admin/users?per_page=200").json()["users"]}
    assert table[unl["id"]]["usage_status"] == "unlimited" and table[unl["id"]]["remaining"] is None
    assert table[full["id"]]["usage_status"] == "exhausted" and table[full["id"]]["remaining"] == 0
    assert table[near["id"]]["usage_status"] == "approaching" and table[near["id"]]["remaining"] == 1
    ids = {u["id"] for u in a.get("/api/admin/users?usage_status=exhausted&per_page=200").json()["users"]}
    assert full["id"] in ids and near["id"] not in ids
    found = a.get(f"/api/admin/users?q={full['email'][:8]}").json()["users"]
    assert [u["id"] for u in found] == [full["id"]]
    assert all(u["plan_code"] == "free" for u in a.get("/api/admin/users?plan=free&per_page=200").json()["users"])
    # stats agree with the per-account entitlements
    stats = a.get("/api/admin/analytics").json()["subscriptions"]
    ents = entitlements.effective_many()
    assert stats["unlimited"] == sum(e.unlimited for e in ents.values())
    assert stats["exhausted"] >= 1 and stats["approaching"] >= 1
    assert sum(p["n"] for p in stats["by_plan"]) == len(ents)
    # one user's usage details
    d = a.get(f"/api/admin/users/{full['id']}").json()
    assert d["entitlement"]["used"] == 1 and d["recent"][0]["kind"] == "generation" and d["recent"][0]["counted"]
    assert d["history"][0]["event"] == "assigned" and len(d["daily"]) == 30
    assert "dedupe_key" not in json.dumps(d)


def test_user_counts():
    a = admin()
    before = a.get("/api/admin/analytics").json()["users"]
    u, _ = new_user(a)
    a.patch(f"/api/admin/users/{u['id']}", json={"active": False})
    after = a.get("/api/admin/analytics").json()["users"]
    assert after["total"] == before["total"] + 1 and after["inactive"] == before["inactive"] + 1
    assert after["new"] == before["new"] + 1
    with db.tx() as c:
        assert after["total"] == c.execute("SELECT COUNT(*) FROM users").fetchone()[0]
    assert analytics.overview()["users"]["total"] == after["total"]


def test_ai_overview_never_contains_keys():
    a = admin()
    _, c = new_user(a)
    key = "sk-overview-SECRET-998877"
    c.put("/api/me/ai", json={"vision": "openai", "providers": {"openai": {"api_key": key}}})
    r = a.get("/api/admin/ai")
    assert r.status_code == 200 and key not in r.text and "9988" not in r.text
    assert r.json()["accounts"]["providers"]["openai"]["accounts_with_key"] >= 1


def test_role_and_auth_basics_unchanged():
    assert auth.session_choices()[-1] >= 1
