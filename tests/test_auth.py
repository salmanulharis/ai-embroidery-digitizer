"""Accounts end to end: Super Admin from the deployment secrets, login and sessions,
"Remember me", logout, roles, user management, and that no account can reach another
account's projects or AI keys. Runs with the real login (the test-wide login override
from conftest.py is switched off here)."""

import dataclasses
import sqlite3
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
    c2 = c.post("/api/auth/login", json={"login": "nobody", "password": "nope-nope-nope"})
    assert a.status_code == b.status_code == c2.status_code == 401
    assert a.json() == b.json() == c2.json() == {"detail": "Wrong email, username or password."}


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


# --------------------------------------------------------------------------- usernames

def test_sign_in_with_username_or_email():
    admin = logged_in(*ADMIN)
    u = admin.post("/api/admin/users", json={"email": "lena@example.com", "password": "user-password-123", "username": "Lena.K"}).json()
    assert u["username"] == "lena.k"  # stored in lower case
    for name in ("lena.k", "LENA.K", " lena.k ", "lena@example.com"):
        c = browser()
        r = c.post("/api/auth/login", json={"login": name, "password": "user-password-123"})
        assert r.status_code == 200 and r.json()["user"]["username"] == "lena.k", name
    # older clients send "email"
    assert browser().post("/api/auth/login", json={"email": "lena.k", "password": "user-password-123"}).status_code == 200
    assert admin.get("/api/admin/users").json()["users"][0].keys() >= {"username"}


def test_usernames_are_unique_and_checked():
    admin = logged_in(*ADMIN)
    u = new_user(admin, "omar@example.com")
    assert u["username"] is None  # optional
    assert admin.post("/api/admin/users", json={"email": "o2@example.com", "password": "user-password-123", "username": "omar"}).status_code == 200
    for bad in ("OMAR", "omar"):  # taken, in any letter case
        r = admin.post("/api/admin/users", json={"email": f"x{len(bad)}{bad}@example.com", "password": "user-password-123", "username": bad})
        assert r.status_code == 400 and "taken" in r.json()["detail"]
    for bad in ("ab", "has space", "a@b", "-start", "x" * 33, "ümlaut"):
        r = admin.post("/api/admin/users", json={"email": "y@example.com", "password": "user-password-123", "username": bad})
        assert r.status_code == 400, bad
    assert admin.patch(f"/api/admin/users/{u['id']}", json={"username": "Omar"}).status_code == 400  # taken by o2
    assert admin.patch(f"/api/admin/users/{u['id']}", json={"username": "omar.f"}).json()["username"] == "omar.f"
    assert admin.patch(f"/api/admin/users/{u['id']}", json={"email": "omar.f@example.com"}).json()["username"] == "omar.f"  # untouched
    logged_in("omar.f", "user-password-123")
    assert admin.patch(f"/api/admin/users/{u['id']}", json={"username": ""}).json()["username"] is None  # removed
    assert browser().post("/api/auth/login", json={"login": "omar.f", "password": "user-password-123"}).status_code == 401
    logged_in("omar.f@example.com", "user-password-123")  # the email still works


def test_failures_count_per_account_whichever_name_is_typed():
    admin = logged_in(*ADMIN)
    admin.post("/api/admin/users", json={"email": "tina@example.com", "password": "user-password-123", "username": "tina"})
    c = browser()
    for i in range(auth.MAX_PER_ACCOUNT):
        name = "tina" if i % 2 else "tina@example.com"
        assert c.post("/api/auth/login", json={"login": name, "password": "guess-guess-1"}).status_code == 401
    assert c.post("/api/auth/login", json={"login": "tina", "password": "user-password-123"}).status_code == 429


def test_super_admin_username_comes_only_from_the_secret(monkeypatch):
    monkeypatch.setattr(auth, "settings", dataclasses.replace(auth.settings, super_admin_username="chief"))
    auth.ensure_super_admin()
    admin = logged_in("CHIEF", ADMIN[1])
    me = admin.get("/api/auth/me").json()["user"]
    assert me["username"] == "chief" and me["role"] == "super_admin"
    # not changeable in the app, not even by the Super Admin
    for body in ({"username": "boss"}, {"username": ""}):
        assert admin.patch(f"/api/admin/users/{me['id']}", json=body).status_code == 400
    assert admin.patch(f"/api/admin/users/{me['id']}", json={"username": "chief"}).status_code == 200  # unchanged: fine
    # a new secret value is applied at the next start; one taken by a user is not
    monkeypatch.setattr(auth, "settings", dataclasses.replace(auth.settings, super_admin_username="head"))
    auth.ensure_super_admin()
    assert auth.get_user(me["id"])["username"] == "head"
    other = new_user(admin, "taken@example.com")
    admin.patch(f"/api/admin/users/{other['id']}", json={"username": "taken-name"})
    monkeypatch.setattr(auth, "settings", dataclasses.replace(auth.settings, super_admin_username="taken-name"))
    auth.ensure_super_admin()
    assert auth.get_user(me["id"])["username"] == "head"
    monkeypatch.setattr(auth, "settings", dataclasses.replace(auth.settings, super_admin_username="Not valid!"))
    auth.ensure_super_admin()  # logged, ignored
    assert auth.get_user(me["id"])["username"] == "head"


def test_existing_database_gets_the_username_column(tmp_path, monkeypatch):
    path = tmp_path / "old.db"
    old = sqlite3.connect(path)
    old.executescript(db.MIGRATIONS[0] + "; PRAGMA user_version = 1;")
    old.execute("INSERT INTO users (email, password_hash, role, active, created_at, updated_at) VALUES ('a@example.com', 'x', 'user', 1, 0, 0)")
    old.execute("INSERT INTO users (email, password_hash, role, active, created_at, updated_at) VALUES ('b@example.com', 'x', 'user', 1, 0, 0)")
    old.commit()
    old.close()
    monkeypatch.setattr(db, "PATH", path)
    conn = db._connect()
    assert conn.execute("PRAGMA user_version").fetchone()[0] == len(db.MIGRATIONS)
    assert [r["username"] for r in conn.execute("SELECT username FROM users")] == [None, None]  # several without one
    conn.execute("UPDATE users SET username = 'same' WHERE email = 'a@example.com'")
    try:
        conn.execute("UPDATE users SET username = 'SAME' WHERE email = 'b@example.com'")
        raise AssertionError("duplicate username accepted")
    except sqlite3.IntegrityError:
        pass
    conn.close()


def test_a_bad_admin_secret_does_not_stop_the_app(monkeypatch, caplog):
    for email, password in (("new-admin@example.com", "short"), ("not-an-email", "long-enough-password")):
        monkeypatch.setattr(auth, "settings", dataclasses.replace(auth.settings, super_admin_email=email, super_admin_password=password))
        auth.ensure_super_admin()  # logs instead of raising
        assert "Super Admin not created" in caplog.text
        assert all(u["email"] != email for u in auth.list_users())
    with TestClient(app) as c:  # the whole startup (lifespan) runs
        assert c.get("/api/config").status_code == 200
