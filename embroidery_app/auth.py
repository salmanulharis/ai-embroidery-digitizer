"""Accounts: sign-in with email or username + password, sessions and the two roles.

- Passwords are hashed with scrypt (salted, memory-hard); only the hash is stored.
- A login creates a random session token. The browser gets it in an HttpOnly cookie, the
  database keeps only its SHA-256, so a copy of the database cannot be used to log in.
- A sign-in lasts for the browser session (the server forgets it after a day at most) or
  for a chosen number of days (``DAY_CHOICES``, up to the ``session_max_days`` setting).
  Each session has a public id (``sid``) so it can be listed and revoked; ending a session
  also deletes the temporary projects opened in it (temp_projects.py).
- There is no sign-up. The Super Admin (from SUPER_ADMIN_EMAIL / SUPER_ADMIN_PASSWORD)
  is created at startup and creates every other account. Its username comes only from
  SUPER_ADMIN_USERNAME (applied at every start); other accounts get theirs from the admin.
- A username is optional, unique in any letter case and never contains "@", so the one
  sign-in field tells an email from a username.
- Roles: ``super_admin`` manages accounts; ``user`` only uses the app. Every rule is
  enforced here, on the server. The Super Admin from the secrets cannot be demoted.
- New accounts get the default plan in the same transaction (subscriptions.py), so the
  same ``create_user`` can serve a public sign-up later without other changes.

Command line (on the server, for a lost password):
    python -m embroidery_app.auth reset-password someone@example.com   (or a username)
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

from . import app_settings, db, subscriptions, temp_projects
from .config import settings
from .service import NotFound as NotFoundError

log = logging.getLogger("embroidery")

COOKIE = "emb_session"
SUPER_ADMIN, USER = "super_admin", "user"
MIN_PASSWORD = 10
MAX_PASSWORD = 256
SHORT_SESSION_S = 24 * 3600  # "this browser session": the server forgets it after a day at most
DAY_CHOICES = (1, 7, 15, 30)  # "keep me signed in" choices, capped by the session_max_days setting
SEEN_EVERY_S = 60  # last-seen / activity is written at most this often per session
MAX_DISPLAY_NAME = 80
_EMAIL = re.compile(r"^[^@\s]{1,64}@[^@\s]+\.[^@\s]{2,}$")
_USERNAME = re.compile(r"^[a-z0-9][a-z0-9._-]{2,31}$")


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


def check_username(username: str | None) -> str | None:
    """A cleaned username, or None for none. 3-32 characters: letters, digits, . _ -
    starting with a letter or digit (stored in lower case)."""
    username = (username or "").strip().lower()
    if not username:
        return None
    if not _USERNAME.match(username):
        raise ValueError("A username has 3-32 characters: letters, digits, dot, dash or underscore, starting with a letter or digit.")
    return username


def _username_free(c, username: str | None, uid: int | None = None) -> None:
    if username and c.execute("SELECT 1 FROM users WHERE username = ? AND id != ?", (username, uid or 0)).fetchone():
        raise ValueError("This username is already taken.")


# --------------------------------------------------------------------------- users

def check_display_name(name: str | None) -> str | None:
    name = re.sub(r"\s+", " ", (name or "")).strip()
    if len(name) > MAX_DISPLAY_NAME:
        raise ValueError(f"The name can have at most {MAX_DISPLAY_NAME} characters.")
    if any(ord(ch) < 32 for ch in name):
        raise ValueError("The name contains characters that are not allowed.")
    return name or None


def _public(row) -> dict:
    keys = row.keys()
    return {"id": row["id"], "email": row["email"], "username": row["username"], "role": row["role"], "active": bool(row["active"]),
            "display_name": row["display_name"] if "display_name" in keys else None,
            "created_at": row["created_at"], "last_login_at": row["last_login_at"],
            "last_active_at": row["last_active_at"] if "last_active_at" in keys else None,
            "env_admin": row["role"] == SUPER_ADMIN and row["email"] == settings.super_admin_email}


def create_user(email: str, password: str, role: str = USER, username: str | None = None, display_name: str | None = None,
                actor_id: int | None = None) -> dict:
    email, password, username = check_email(email), check_password_rules(password), check_username(username)
    display_name = check_display_name(display_name)
    if role not in (USER, SUPER_ADMIN):
        raise ValueError("Unknown role.")
    now = time.time()
    with db.tx() as c:
        if c.execute("SELECT 1 FROM users WHERE email = ?", (email,)).fetchone():
            raise ValueError("An account with this email already exists.")
        _username_free(c, username)
        cur = c.execute("INSERT INTO users (email, username, display_name, password_hash, role, active, created_at, updated_at) "
                        "VALUES (?, ?, ?, ?, ?, 1, ?, ?)", (email, username, display_name, hash_password(password), role, now, now))
        subscriptions.assign_default(c, cur.lastrowid, actor_id)  # every account starts on the default (Free) plan
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


def find_user(login: str):
    """The account row for a sign-in name: an email (contains "@") or a username."""
    login = (login or "").strip().lower()
    if not login:
        return None
    column = "email" if "@" in login else "username"
    with db.tx() as c:
        return c.execute(f"SELECT * FROM users WHERE {column} = ?", (login,)).fetchone()


def update_user(uid: int, email: str | None = None, active: bool | None = None, username: str | None = None,
                display_name: str | None = None, role: str | None = None, actor_id: int | None = None) -> dict:
    """``username`` / ``display_name``: None leaves it, "" removes it. The Super Admin from
    the deployment secrets keeps its email, username, role and active state; nobody can
    change their own role or deactivate themselves."""
    target = get_user(uid)
    if target["env_admin"] and (active is False or (email is not None and check_email(email) != target["email"])
                                or (username is not None and check_username(username) != target["username"])
                                or (role is not None and role != SUPER_ADMIN)):
        raise ValueError("The Super Admin account cannot be deactivated, demoted or renamed here "
                         "(its email and username are set in the deployment secrets).")
    if actor_id == uid and (active is False or (role is not None and role != target["role"])):
        raise ValueError("You cannot deactivate your own account or change your own role.")
    if role is not None and role not in (USER, SUPER_ADMIN):
        raise ValueError("Unknown role.")
    now = time.time()
    with db.tx() as c:
        if username is not None:
            username = check_username(username)
            _username_free(c, username, uid)
            c.execute("UPDATE users SET username = ?, updated_at = ? WHERE id = ?", (username, now, uid))
        if display_name is not None:
            c.execute("UPDATE users SET display_name = ?, updated_at = ? WHERE id = ?", (check_display_name(display_name), now, uid))
        if email is not None:
            email = check_email(email)
            if c.execute("SELECT 1 FROM users WHERE email = ? AND id != ?", (email, uid)).fetchone():
                raise ValueError("An account with this email already exists.")
            c.execute("UPDATE users SET email = ?, updated_at = ? WHERE id = ?", (email, now, uid))
        if role is not None and role != target["role"]:
            c.execute("UPDATE users SET role = ?, updated_at = ? WHERE id = ?", (role, now, uid))
        if active is not None:
            c.execute("UPDATE users SET active = ?, updated_at = ? WHERE id = ?", (int(active), now, uid))
    if active is False or (role is not None and role != target["role"]):
        end_sessions(uid)  # signed out everywhere; a new role takes effect at the next sign-in
    return get_user(uid)


def set_password(uid: int, password: str, keep_session: str | None = None) -> None:
    """New password; every other session of the account ends (``keep_session``: a token hash to keep)."""
    check_password_rules(password)
    get_user(uid)
    with db.tx() as c:
        c.execute("UPDATE users SET password_hash = ?, updated_at = ? WHERE id = ?", (hash_password(password), time.time(), uid))
    end_sessions(uid, keep_token_hash=keep_session)


def change_own_password(uid: int, current: str, new: str, session_token: str | None) -> None:
    """A user's own change: needs the current password; their other sessions end."""
    with db.tx() as c:
        row = c.execute("SELECT password_hash FROM users WHERE id = ?", (uid,)).fetchone()
    if not row or not verify_password(current or "", row["password_hash"]):
        raise ValueError("The current password is not correct.")
    set_password(uid, new, keep_session=_token_hash(session_token) if session_token else None)


def delete_user(uid: int, actor_id: int | None = None) -> dict:
    target = get_user(uid)
    if target["env_admin"]:
        raise ValueError("The Super Admin account cannot be deleted.")
    if actor_id == uid:
        raise ValueError("You cannot delete your own account.")
    temp_projects.delete_for_user(uid)  # files first: the registry rows go with the account
    with db.tx() as c:
        # sessions, AI settings, subscription, overrides, usage and project rows go with it (ON DELETE CASCADE)
        c.execute("DELETE FROM users WHERE id = ?", (uid,))
    return target


def ensure_super_admin() -> None:
    """Create the Super Admin from the deployment secrets when no account has that email,
    and give it the username from SUPER_ADMIN_USERNAME (the only place it is set)."""
    email, password = settings.super_admin_email, settings.super_admin_password
    if not email or not password:
        with db.tx() as c:
            if not c.execute("SELECT 1 FROM users WHERE role = ?", (SUPER_ADMIN,)).fetchone():
                log.warning("No Super Admin: set SUPER_ADMIN_EMAIL and SUPER_ADMIN_PASSWORD to create one.")
        return
    with db.tx() as c:
        row = c.execute("SELECT id, role, username FROM users WHERE email = ?", (email.lower(),)).fetchone()
    if not row:
        try:
            create_user(email, password, SUPER_ADMIN)
        except ValueError as e:  # a bad secret must not stop the whole app from starting
            log.error("Super Admin not created - fix SUPER_ADMIN_EMAIL / SUPER_ADMIN_PASSWORD and deploy again: %s", e)
            return
        log.info("Super Admin account created for %s", email)
    elif row["role"] != SUPER_ADMIN:
        return  # an ordinary account already uses this email: leave it alone
    _apply_admin_username(email.lower())


def _apply_admin_username(email: str) -> None:
    if not settings.super_admin_username:
        return
    try:
        username = check_username(settings.super_admin_username)
    except ValueError as e:
        log.error("SUPER_ADMIN_USERNAME is not used: %s", e)
        return
    with db.tx() as c:
        row = c.execute("SELECT id, username FROM users WHERE email = ?", (email,)).fetchone()
        if row["username"] == username:
            return
        if c.execute("SELECT 1 FROM users WHERE username = ? AND id != ?", (username, row["id"])).fetchone():
            log.error("SUPER_ADMIN_USERNAME %r is already used by another account; not applied.", username)
            return
        c.execute("UPDATE users SET username = ?, updated_at = ? WHERE id = ?", (username, time.time(), row["id"]))
    log.info("Super Admin username set to %s", username)


# --------------------------------------------------------------------------- login throttling

_failures: dict[str, list[float]] = {}
_flock = threading.Lock()
WINDOW_S, MAX_PER_ACCOUNT, MAX_PER_IP = 15 * 60, 5, 30


def _throttle_keys(ip: str, account: str) -> list[tuple[str, int]]:
    return [(f"a:{ip}:{account}", MAX_PER_ACCOUNT), (f"i:{ip}", MAX_PER_IP)]


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


def session_choices() -> list[int]:
    """Days a user may choose on the sign-in screen (0 = this browser session only)."""
    most = app_settings.get("session_max_days")
    return [0] + sorted({d for d in DAY_CHOICES if d < most} | {most})


def _session_days(days: int | None, remember: bool) -> int:
    choices = session_choices()
    if days is None:
        return choices[-1] if remember else 0
    if isinstance(days, bool) or days not in choices:
        raise ValueError("Choose how long to stay signed in from the list.")
    return int(days)


def _device(user_agent: str | None) -> str:
    """A short, non-identifying label ("Chrome on Windows") so a user can tell sessions apart."""
    ua = user_agent or ""
    browser = next((b for k, b in (("Edg/", "Edge"), ("OPR/", "Opera"), ("Firefox/", "Firefox"), ("Chrome/", "Chrome"),
                                   ("Safari/", "Safari")) if k in ua), "Browser")
    system = next((s for k, s in (("Windows", "Windows"), ("Android", "Android"), ("iPhone", "iPhone"), ("iPad", "iPad"),
                                  ("Mac OS", "macOS"), ("Linux", "Linux")) if k in ua), "")
    return f"{browser} on {system}" if system else browser


def login(name: str, password: str, remember: bool, ip: str, days: int | None = None,
          user_agent: str | None = None) -> tuple[dict, str, float, int]:
    """Sign in with an email or a username. (user, session token, expiry, days - 0 for a
    browser session), or a 401/403/429 error with a plain message."""
    days = _session_days(days, remember)
    row = find_user(name)
    # failures count per account, whether its email or its username was typed
    account = f"#{row['id']}" if row else (name or "").strip().lower()
    if _blocked(ip, account):
        raise HTTPException(429, "Too many attempts. Wait 15 minutes, then try again.")
    if not verify_password(password or "", row["password_hash"] if row else _DUMMY_HASH) or not row:
        _record_failure(ip, account)
        raise HTTPException(401, "Wrong email, username or password.")
    if not row["active"]:
        raise HTTPException(403, "This account is deactivated. Ask your administrator to activate it.")
    _clear_failures(ip, account)
    token = secrets.token_urlsafe(32)
    now = time.time()
    expires = now + (days * 86400 if days else SHORT_SESSION_S)
    with db.tx() as c:
        c.execute("DELETE FROM sessions WHERE expires_at < ?", (now,))
        c.execute("INSERT INTO sessions (token_hash, sid, user_id, created_at, expires_at, remember, last_seen_at, user_agent) "
                  "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                  (_token_hash(token), secrets.token_hex(9), row["id"], now, expires, int(bool(days)), now, _device(user_agent)))
        c.execute("UPDATE users SET last_login_at = ?, last_active_at = ? WHERE id = ?", (now, now, row["id"]))
        c.execute("INSERT OR IGNORE INTO daily_activity (day, user_id) VALUES (?, ?)", (int(now // 86400), row["id"]))
    return _public(row), token, expires, days


def logout(token: str | None) -> None:
    """End this session and delete the temporary projects opened in it."""
    if not token:
        return
    with db.tx() as c:
        row = c.execute("SELECT sid FROM sessions WHERE token_hash = ?", (_token_hash(token),)).fetchone()
        c.execute("DELETE FROM sessions WHERE token_hash = ?", (_token_hash(token),))
    if row:
        temp_projects.delete_for_session(row["sid"])


def end_sessions(uid: int, keep_token_hash: str | None = None, sid: str | None = None) -> int:
    """Sign an account out: every session, every session but one, or one (``sid``). The
    temporary projects of the ended sessions are deleted."""
    with db.tx() as c:
        if sid:
            rows = c.execute("SELECT sid FROM sessions WHERE user_id = ? AND sid = ?", (uid, sid)).fetchall()
        else:
            rows = c.execute("SELECT sid FROM sessions WHERE user_id = ? AND token_hash != ?", (uid, keep_token_hash or "")).fetchall()
        for r in rows:
            c.execute("DELETE FROM sessions WHERE user_id = ? AND sid = ?", (uid, r["sid"]))
    for r in rows:
        temp_projects.delete_for_session(r["sid"])
    return len(rows)


def list_sessions(uid: int, current_sid: str | None = None) -> list[dict]:
    with db.tx() as c:
        rows = c.execute("SELECT sid, created_at, expires_at, remember, last_seen_at, user_agent FROM sessions "
                         "WHERE user_id = ? AND expires_at > ? ORDER BY last_seen_at DESC", (uid, time.time())).fetchall()
    return [{"id": r["sid"], "device": r["user_agent"] or "Browser", "created_at": r["created_at"], "expires_at": r["expires_at"],
             "kept": bool(r["remember"]), "last_seen_at": r["last_seen_at"], "current": r["sid"] == current_sid} for r in rows]


def session_user(token: str | None) -> dict | None:
    """The account of a valid session (with ``sid`` = the session's public id), or None.
    Notes the activity (last seen, daily active users) at most once a minute."""
    if not token:
        return None
    now = time.time()
    th = _token_hash(token)
    with db.tx() as c:
        row = c.execute("SELECT u.*, s.sid AS session_sid, s.last_seen_at AS session_seen FROM sessions s JOIN users u ON u.id = s.user_id "
                        "WHERE s.token_hash = ? AND s.expires_at > ? AND u.active = 1", (th, now)).fetchone()
        if row and (row["session_seen"] or 0) < now - SEEN_EVERY_S:
            c.execute("UPDATE sessions SET last_seen_at = ? WHERE token_hash = ?", (now, th))
            c.execute("UPDATE users SET last_active_at = ? WHERE id = ?", (now, row["id"]))
            c.execute("INSERT OR IGNORE INTO daily_activity (day, user_id) VALUES (?, ?)", (int(now // 86400), row["id"]))
    return _public(row) | {"sid": row["session_sid"]} if row else None


def set_cookie(response: Response, request: Request, token: str, days: int) -> None:
    secure = settings.cookie_secure == "1" or (settings.cookie_secure == "auto" and request.url.scheme == "https")
    response.set_cookie(COOKIE, token, max_age=days * 86400 if days else None,
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
    if pid is not None and not temp_projects.touch(pid, user["id"]):  # ownership check + "in use"
        raise NotFoundError(pid)
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
    name = argv[1].strip().lower()
    row = find_user(name)
    if not row:
        print(f"No account for {name}.")
        return 1
    password = getpass.getpass("New password: ")
    if password != getpass.getpass("Repeat: "):
        print("The passwords differ.")
        return 1
    set_password(row["id"], password)
    print(f"Password changed for {row['email']}; all of its sessions have ended.")
    return 0


if __name__ == "__main__":
    import sys

    sys.exit(_main(sys.argv[1:]))
