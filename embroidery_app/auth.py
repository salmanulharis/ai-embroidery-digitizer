"""Accounts: email + password login, sessions and the two roles.

- Passwords are hashed with scrypt (salted, memory-hard); only the hash is stored.
- A login creates a random session token. The browser gets it in an HttpOnly cookie, the
  database keeps only its SHA-256, so a copy of the database cannot be used to log in.
- "Remember me" keeps the session for ``SESSION_DAYS``; otherwise the cookie ends with
  the browser session and the server forgets it after a day at most.
- There is no sign-up. The Super Admin (from SUPER_ADMIN_EMAIL / SUPER_ADMIN_PASSWORD)
  is created at startup and creates every other account.
- Roles: ``super_admin`` manages accounts; ``user`` only uses the app. Every rule is
  enforced here, on the server.

Command line (on the server, for a lost password):
    python -m embroidery_app.auth reset-password someone@example.com
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import logging
import re
import secrets
import threading
import time

from fastapi import Depends, HTTPException, Request, Response

from . import db, service
from .config import settings

log = logging.getLogger("embroidery")

COOKIE = "emb_session"
SUPER_ADMIN, USER = "super_admin", "user"
MIN_PASSWORD = 10
MAX_PASSWORD = 256
SHORT_SESSION_S = 24 * 3600  # without "Remember me"
_EMAIL = re.compile(r"^[^@\s]{1,64}@[^@\s]+\.[^@\s]{2,}$")


# --------------------------------------------------------------------------- passwords

_N, _R, _P = 2 ** 15, 8, 1  # scrypt cost: ~32 MB and ~0.1 s per check


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode(), salt=salt, n=_N, r=_R, p=_P, maxmem=64 * 1024 * 1024)
    b64 = lambda b: base64.b64encode(b).decode()  # noqa: E731
    return f"scrypt${_N}${_R}${_P}${b64(salt)}${b64(digest)}"


def verify_password(password: str, stored: str) -> bool:
    try:
        _, n, r, p, salt, digest = stored.split("$")
        expected = base64.b64decode(digest)
        got = hashlib.scrypt(password.encode(), salt=base64.b64decode(salt), n=int(n), r=int(r), p=int(p),
                             maxmem=64 * 1024 * 1024, dklen=len(expected))
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(got, expected)


_DUMMY_HASH = hash_password(secrets.token_hex(16))  # checked for unknown emails: same timing


def check_password_rules(password: str) -> str:
    if not isinstance(password, str) or len(password) < MIN_PASSWORD:
        raise ValueError(f"The password needs at least {MIN_PASSWORD} characters.")
    if len(password) > MAX_PASSWORD:
        raise ValueError("The password is too long.")
    return password


def check_email(email: str) -> str:
    email = (email or "").strip().lower()
    if not _EMAIL.match(email) or len(email) > 254:
        raise ValueError("Enter a valid email address.")
    return email


# --------------------------------------------------------------------------- users

def _public(row) -> dict:
    return {"id": row["id"], "email": row["email"], "role": row["role"], "active": bool(row["active"]),
            "created_at": row["created_at"], "last_login_at": row["last_login_at"]}


def create_user(email: str, password: str, role: str = USER) -> dict:
    email, password = check_email(email), check_password_rules(password)
    now = time.time()
    with db.tx() as c:
        if c.execute("SELECT 1 FROM users WHERE email = ?", (email,)).fetchone():
            raise ValueError("An account with this email already exists.")
        cur = c.execute("INSERT INTO users (email, password_hash, role, active, created_at, updated_at) VALUES (?, ?, ?, 1, ?, ?)",
                        (email, hash_password(password), role, now, now))
        return _public(c.execute("SELECT * FROM users WHERE id = ?", (cur.lastrowid,)).fetchone())


def get_user(uid: int) -> dict:
    with db.tx() as c:
        row = c.execute("SELECT * FROM users WHERE id = ?", (uid,)).fetchone()
    if not row:
        raise HTTPException(404, "No such account.")
    return _public(row)


def list_users() -> list[dict]:
    with db.tx() as c:
        return [_public(r) for r in c.execute("SELECT * FROM users ORDER BY role DESC, email")]


def update_user(uid: int, email: str | None = None, active: bool | None = None) -> dict:
    target = get_user(uid)
    if target["role"] == SUPER_ADMIN and (active is False or (email is not None and check_email(email) != target["email"])):
        raise ValueError("The Super Admin account cannot be deactivated or renamed here (its email is set in the deployment secrets).")
    with db.tx() as c:
        if email is not None:
            email = check_email(email)
            if c.execute("SELECT 1 FROM users WHERE email = ? AND id != ?", (email, uid)).fetchone():
                raise ValueError("An account with this email already exists.")
            c.execute("UPDATE users SET email = ?, updated_at = ? WHERE id = ?", (email, time.time(), uid))
        if active is not None:
            c.execute("UPDATE users SET active = ?, updated_at = ? WHERE id = ?", (int(active), time.time(), uid))
            if not active:
                c.execute("DELETE FROM sessions WHERE user_id = ?", (uid,))  # signed out everywhere
    return get_user(uid)


def set_password(uid: int, password: str, keep_session: str | None = None) -> None:
    """New password; every other session of the account ends (``keep_session``: a token hash to keep)."""
    check_password_rules(password)
    get_user(uid)
    with db.tx() as c:
        c.execute("UPDATE users SET password_hash = ?, updated_at = ? WHERE id = ?", (hash_password(password), time.time(), uid))
        c.execute("DELETE FROM sessions WHERE user_id = ? AND token_hash != ?", (uid, keep_session or ""))


def change_own_password(uid: int, current: str, new: str, session_token: str | None) -> None:
    """A user's own change: needs the current password; their other sessions end."""
    with db.tx() as c:
        row = c.execute("SELECT password_hash FROM users WHERE id = ?", (uid,)).fetchone()
    if not row or not verify_password(current or "", row["password_hash"]):
        raise ValueError("The current password is not correct.")
    set_password(uid, new, keep_session=_token_hash(session_token) if session_token else None)


def delete_user(uid: int) -> None:
    if get_user(uid)["role"] == SUPER_ADMIN:
        raise ValueError("The Super Admin account cannot be deleted.")
    with db.tx() as c:
        c.execute("DELETE FROM users WHERE id = ?", (uid,))  # sessions and AI settings go with it
    service.delete_projects_of(uid)


def ensure_super_admin() -> None:
    """Create the Super Admin from the deployment secrets when no account has that email."""
    email, password = settings.super_admin_email, settings.super_admin_password
    if not email or not password:
        with db.tx() as c:
            if not c.execute("SELECT 1 FROM users WHERE role = ?", (SUPER_ADMIN,)).fetchone():
                log.warning("No Super Admin: set SUPER_ADMIN_EMAIL and SUPER_ADMIN_PASSWORD to create one.")
        return
    with db.tx() as c:
        if c.execute("SELECT 1 FROM users WHERE email = ?", (email.lower(),)).fetchone():
            return
    create_user(email, password, SUPER_ADMIN)
    log.info("Super Admin account created for %s", email)


# --------------------------------------------------------------------------- login throttling

_failures: dict[str, list[float]] = {}
_flock = threading.Lock()
WINDOW_S, MAX_PER_ACCOUNT, MAX_PER_IP = 15 * 60, 5, 30


def _throttle_keys(ip: str, email: str) -> list[tuple[str, int]]:
    return [(f"a:{ip}:{email}", MAX_PER_ACCOUNT), (f"i:{ip}", MAX_PER_IP)]


def _blocked(ip: str, email: str) -> bool:
    now = time.time()
    with _flock:
        for key, limit in _throttle_keys(ip, email):
            recent = [t for t in _failures.get(key, []) if now - t < WINDOW_S]
            _failures[key] = recent
            if len(recent) >= limit:
                return True
    return False


def _record_failure(ip: str, email: str) -> None:
    with _flock:
        for key, _ in _throttle_keys(ip, email):
            _failures.setdefault(key, []).append(time.time())


def _clear_failures(ip: str, email: str) -> None:
    with _flock:
        _failures.pop(_throttle_keys(ip, email)[0][0], None)


# --------------------------------------------------------------------------- sessions

def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def login(email: str, password: str, remember: bool, ip: str) -> tuple[dict, str, float]:
    """(user, session token, expiry) or a 401/403/429 error with a plain message."""
    email = (email or "").strip().lower()
    if _blocked(ip, email):
        raise HTTPException(429, "Too many attempts. Wait 15 minutes, then try again.")
    with db.tx() as c:
        row = c.execute("SELECT * FROM users WHERE email = ?", (email,)).fetchone()
    if not verify_password(password or "", row["password_hash"] if row else _DUMMY_HASH) or not row:
        _record_failure(ip, email)
        raise HTTPException(401, "Wrong email or password.")
    if not row["active"]:
        raise HTTPException(403, "This account is deactivated. Ask your administrator to activate it.")
    _clear_failures(ip, email)
    token = secrets.token_urlsafe(32)
    now = time.time()
    expires = now + (settings.session_days * 86400 if remember else SHORT_SESSION_S)
    with db.tx() as c:
        c.execute("DELETE FROM sessions WHERE expires_at < ?", (now,))
        c.execute("INSERT INTO sessions (token_hash, user_id, created_at, expires_at, remember) VALUES (?, ?, ?, ?, ?)",
                  (_token_hash(token), row["id"], now, expires, int(remember)))
        c.execute("UPDATE users SET last_login_at = ? WHERE id = ?", (now, row["id"]))
    return _public(row), token, expires


def logout(token: str | None) -> None:
    if token:
        with db.tx() as c:
            c.execute("DELETE FROM sessions WHERE token_hash = ?", (_token_hash(token),))


def session_user(token: str | None) -> dict | None:
    if not token:
        return None
    with db.tx() as c:
        row = c.execute("SELECT u.* FROM sessions s JOIN users u ON u.id = s.user_id "
                        "WHERE s.token_hash = ? AND s.expires_at > ? AND u.active = 1", (_token_hash(token), time.time())).fetchone()
    return _public(row) if row else None


def set_cookie(response: Response, request: Request, token: str, remember: bool) -> None:
    secure = settings.cookie_secure == "1" or (settings.cookie_secure == "auto" and request.url.scheme == "https")
    response.set_cookie(COOKIE, token, max_age=settings.session_days * 86400 if remember else None,
                        httponly=True, secure=secure, samesite="lax", path="/")


def clear_cookie(response: Response) -> None:
    response.delete_cookie(COOKIE, path="/")


# --------------------------------------------------------------------------- FastAPI dependencies

def require_user(request: Request) -> dict:
    """The logged-in, active account; 401 otherwise. A project in the URL must be theirs
    (404 otherwise, so other people's project ids are not even confirmed)."""
    user = session_user(request.cookies.get(COOKIE))
    if not user:
        raise HTTPException(401, "Please log in.")
    pid = request.path_params.get("pid")
    if pid is not None and not service.owned_by(pid, user["id"]):
        raise service.NotFound(pid)
    request.state.user = user
    return user


def require_admin(user: dict = Depends(require_user)) -> dict:
    if user["role"] != SUPER_ADMIN:
        raise HTTPException(403, "Only the Super Admin can do this.")
    return user


# --------------------------------------------------------------------------- command line

def _main(argv: list[str]) -> int:
    import getpass

    if len(argv) != 2 or argv[0] not in ("reset-password",):
        print(__doc__.split("Command line")[1].strip())
        return 2
    email = check_email(argv[1])
    with db.tx() as c:
        row = c.execute("SELECT id FROM users WHERE email = ?", (email,)).fetchone()
    if not row:
        print(f"No account for {email}.")
        return 1
    password = getpass.getpass("New password: ")
    if password != getpass.getpass("Repeat: "):
        print("The passwords differ.")
        return 1
    set_password(row["id"], password)
    print(f"Password changed for {email}; all of its sessions have ended.")
    return 0


if __name__ == "__main__":
    import sys

    sys.exit(_main(sys.argv[1:]))
