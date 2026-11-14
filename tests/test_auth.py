"""Accounts end to end: Super Admin from the deployment secrets, login and sessions,
"Remember me", logout, roles, user management, and that no account can reach another
account's projects or AI keys. Runs with the real login (the test-wide login override
from conftest.py is switched off here)."""

import time

import cv2
import numpy as np
import pytest
from fastapi.testclient import TestClient

from embroidery_app import auth, db, service, user_ai
from embroidery_app.api import app
from embroidery_app.config import settings

ADMIN = ("admin@example.com", "admin-password-123")


@pytest.fixture(autouse=True)
def real_login():
    saved = app.dependency_overrides.pop(auth.require_user)
    auth._failures.clear()
    auth.ensure_super_admin()
    yield
    app.dependency_overrides[auth.require_user] = saved
    auth._failures.clear()


def browser() -> TestClient:
    return TestClient(app)  # each client has its own cookie jar, like a separate browser


def logged_in(email, password, remember=False) -> TestClient:
    c = browser()
    r = c.post("/api/auth/login", json={"email": email, "password": password, "remember": remember})
    assert r.status_code == 200, r.text
    return c


def new_user(admin: TestClient, email: str, password: str = "user-password-123") -> dict:
    r = admin.post("/api/admin/users", json={"email": email, "password": password})
    assert r.status_code == 200, r.text
    return r.json()


def png() -> bytes:
    img = np.full((200, 300, 3), 255, np.uint8)
    cv2.rectangle(img, (40, 40), (200, 160), (40, 40, 200), -1)
    return cv2.imencode(".png", img)[1].tobytes()


# --------------------------------------------------------------------------- Super Admin + login

def test_super_admin_is_created_once_from_the_secrets():
    auth.ensure_super_admin()  # again: no duplicate, password not reset
    admins = [u for u in auth.list_users() if u["role"] == auth.SUPER_ADMIN]
    assert [u["email"] for u in admins] == [ADMIN[0]]
    me = logged_in(*ADMIN).get("/api/auth/me").json()
    assert me["user"]["role"] == "super_admin" and me["session_days"] == settings.session_days


def test_wrong_password_and_unknown_email_get_the_same_answer():
    c = browser()
    a = c.post("/api/auth/login", json={"email": ADMIN[0], "password": "nope-nope-nope"})
    b = c.post("/api/auth/login", json={"email": "nobody@example.com", "password": "nope-nope-nope"})
    assert a.status_code == b.status_code == 401 and a.json() == b.json() == {"detail": "Wrong email or password."}


def test_session_cookie_remember_me_and_logout():
    c = browser()
    r = c.post("/api/auth/login", json={"email": ADMIN[0], "password": ADMIN[1], "remember": False})
    cookie = r.headers["set-cookie"].lower()
    assert "httponly" in cookie and "samesite=lax" in cookie and "max-age" not in cookie  # ends with the browser
    r = c.post("/api/auth/login", json={"email": ADMIN[0].upper(), "password": ADMIN[1], "remember": True})  # email: any case
    assert f"max-age={settings.session_days * 86400}" in r.headers["set-cookie"].lower()
    token = c.cookies.get(auth.COOKIE)
    assert c.get("/api/auth/me").status_code == 200
    with db.tx() as conn:  # only a hash of the token is stored
        assert not conn.execute("SELECT 1 FROM sessions WHERE token_hash = ?", (token,)).fetchone()
        assert conn.execute("SELECT remember FROM sessions WHERE token_hash = ?", (auth._token_hash(token),)).fetchone()[0] == 1
    assert c.post("/api/auth/logout").status_code == 200
    assert c.get("/api/auth/me").status_code == 401
    stolen = browser()
    stolen.cookies.set(auth.COOKIE, token)  # the old token is dead on the server too
    assert stolen.get("/api/auth/me").status_code == 401


def test_expired_session_is_refused():
    c = logged_in(*ADMIN)
    with db.tx() as conn:
        conn.execute("UPDATE sessions SET expires_at = ? WHERE token_hash = ?", (time.time() - 1, auth._token_hash(c.cookies.get(auth.COOKIE))))
    assert c.get("/api/auth/me").status_code == 401


def test_repeated_failures_are_slowed_down():
    c = browser()
    for _ in range(auth.MAX_PER_ACCOUNT):
        assert c.post("/api/auth/login", json={"email": ADMIN[0], "password": "guess-guess-1"}).status_code == 401
    r = c.post("/api/auth/login", json={"email": ADMIN[0], "password": ADMIN[1]})
    assert r.status_code == 429 and "15 minutes" in r.json()["detail"]


def test_everything_but_login_needs_a_session():
    c = browser()
    assert c.get("/api/config").status_code == 200  # public: the login screen and health checks use it
    for method, url in [("post", "/api/projects"), ("get", "/api/me/ai"), ("put", "/api/me/ai"), ("post", "/api/ai/models"),
                        ("get", "/api/admin/users"), ("get", "/api/auth/me"), ("get", "/api/projects/abcdefabcdef/design")]:
        assert getattr(c, method)(url).status_code == 401, url


def test_requests_from_other_websites_are_refused():
    r = browser().post("/api/auth/login", json={"email": ADMIN[0], "password": ADMIN[1]}, headers={"Origin": "https://evil.example"})
    assert r.status_code == 403


# --------------------------------------------------------------------------- Super Admin: user management

def test_admin_manages_users_and_users_cannot():
    admin = logged_in(*ADMIN)
    u = new_user(admin, "Maria@Example.com")
    assert u["email"] == "maria@example.com" and u["role"] == "user" and u["active"]
    assert admin.post("/api/admin/users", json={"email": "maria@example.com", "password": "another-password"}).status_code == 400
    assert admin.post("/api/admin/users", json={"email": "short@example.com", "password": "short"}).status_code == 400
    assert admin.post("/api/admin/users", json={"email": "not-an-email", "password": "long-enough-password"}).status_code == 400
    listed = admin.get("/api/admin/users").json()["users"]
    assert any(x["email"] == "maria@example.com" for x in listed) and all("password_hash" not in x for x in listed)

    maria = logged_in("maria@example.com", "user-password-123")
    for method, url, body in [("get", "/api/admin/users", None), ("post", "/api/admin/users", {"email": "x@example.com", "password": "x" * 12}),
                              ("patch", f"/api/admin/users/{u['id']}", {"active": True}), ("delete", f"/api/admin/users/{u['id']}", None),
                              ("post", f"/api/admin/users/{u['id']}/password", {"password": "y" * 12})]:
        r = getattr(maria, method)(url, **({"json": body} if body else {}))
        assert r.status_code == 403, url

    # rename, deactivate (signed out at once), activate again
    assert admin.patch(f"/api/admin/users/{u['id']}", json={"email": "maria.r@example.com"}).json()["email"] == "maria.r@example.com"
    assert admin.patch(f"/api/admin/users/{u['id']}", json={"active": False}).json()["active"] is False
    assert maria.get("/api/auth/me").status_code == 401
    r = browser().post("/api/auth/login", json={"email": "maria.r@example.com", "password": "user-password-123"})
    assert r.status_code == 403 and "deactivated" in r.json()["detail"]
    admin.patch(f"/api/admin/users/{u['id']}", json={"active": True})

    # reset password: old sessions end, the new password works
    maria = logged_in("maria.r@example.com", "user-password-123")
    assert admin.post(f"/api/admin/users/{u['id']}/password", json={"password": "brand-new-password-1"}).status_code == 200
    assert maria.get("/api/auth/me").status_code == 401
    logged_in("maria.r@example.com", "brand-new-password-1")


def test_super_admin_cannot_be_removed_or_disabled():
    admin = logged_in(*ADMIN)
    me = admin.get("/api/auth/me").json()["user"]
    assert admin.delete(f"/api/admin/users/{me['id']}").status_code == 400
    assert admin.patch(f"/api/admin/users/{me['id']}", json={"active": False}).status_code == 400
    assert admin.patch(f"/api/admin/users/{me['id']}", json={"email": "other@example.com"}).status_code == 400
    assert admin.get("/api/auth/me").status_code == 200


def test_change_own_password():
    admin = logged_in(*ADMIN)
    new_user(admin, "sam@example.com")
    here, elsewhere = logged_in("sam@example.com", "user-password-123"), logged_in("sam@example.com", "user-password-123")
    assert here.post("/api/auth/password", json={"current": "wrong-password", "password": "sam-new-password"}).status_code == 400
    assert here.post("/api/auth/password", json={"current": "user-password-123", "password": "sam-new-password"}).status_code == 200
    assert here.get("/api/auth/me").status_code == 200 and elsewhere.get("/api/auth/me").status_code == 401
    logged_in("sam@example.com", "sam-new-password")


# --------------------------------------------------------------------------- isolation between accounts

def test_projects_belong_to_their_owner():
    admin = logged_in(*ADMIN)
    new_user(admin, "ann@example.com")
    bob_user = new_user(admin, "bob@example.com")
    ann, bob = logged_in("ann@example.com", "user-password-123"), logged_in("bob@example.com", "user-password-123")
    pid = ann.post("/api/projects", files={"file": ("a.png", png())}).json()["id"]
    assert ann.post(f"/api/projects/{pid}/extract", json={"options": {"width_mm": 60}}).status_code == 200
    for method, url in [("get", f"/api/projects/{pid}/design"), ("get", f"/api/projects/{pid}/source"), ("get", f"/api/projects/{pid}/export/svg"),
                        ("post", f"/api/projects/{pid}/stitch"), ("post", f"/api/projects/{pid}/undo")]:
        r = getattr(bob, method)(url, **({"json": {"params": {}}} if method == "post" else {}))
        assert r.status_code == 404, url  # not even confirmed to exist
    assert admin.get(f"/api/projects/{pid}/design").status_code == 404  # the admin manages accounts, not designs
    assert ann.get(f"/api/projects/{pid}/design").status_code == 200

    bob_pid = bob.post("/api/projects", files={"file": ("b.png", png())}).json()["id"]
    assert admin.delete(f"/api/admin/users/{bob_user['id']}").status_code == 200
    assert not (service.PROJECTS / bob_pid).exists()  # a deleted account's projects go with it
    assert bob.get("/api/auth/me").status_code == 401


def test_ai_settings_and_keys_are_per_account():
    admin = logged_in(*ADMIN)
    new_user(admin, "cara@example.com")
    new_user(admin, "dev@example.com")
    cara, dev = logged_in("cara@example.com", "user-password-123"), logged_in("dev@example.com", "user-password-123")
    key = "sk-cara-PRIVATE-0123456789"
    saved = cara.put("/api/me/ai", json={"vision": "openai", "providers": {"openai": {"api_key": key, "vision_model": "gpt-x"}}}).json()
    assert saved["providers"]["openai"] == {"vision_model": "gpt-x", "has_key": True, "key_hint": "6789"} and saved["configured"]
    assert key not in cara.get("/api/me/ai").text  # the key never comes back to the browser
    # saving again without a key keeps it; Dev sees nothing of Cara's
    cara.put("/api/me/ai", json={"vision": "openai", "providers": {"openai": {"vision_model": "gpt-y"}}})
    cid = cara.get("/api/auth/me").json()["user"]["id"]
    assert user_ai.resolve(cid, {"provider": "openai"})["api_key"] == key
    assert dev.get("/api/me/ai").json() == {"vision": "builtin", "edit": "", "providers": {}, "configured": False}
    did = dev.get("/api/auth/me").json()["user"]["id"]
    with pytest.raises(ValueError, match="AI settings"):
        user_ai.resolve(did, {"provider": "openai"})
    # encrypted at rest, and bound to its account
    for f in (db.PATH, db.PATH.with_name(db.PATH.name + "-wal")):
        assert not f.exists() or key.encode() not in f.read_bytes(), f.name
    with db.tx() as conn:
        row = conn.execute("SELECT data FROM ai_settings WHERE user_id = ?", (cid,)).fetchone()
        conn.execute("INSERT OR REPLACE INTO ai_settings (user_id, data, updated_at) VALUES (?, ?, ?)", (did, row["data"], time.time()))
    assert dev.get("/api/me/ai").json()["providers"] == {}  # a copied row cannot be read by another account
