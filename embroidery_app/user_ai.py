"""Each user's own AI settings: which service to use for what, their keys and models.

Stored per account in the database, encrypted as a whole with AES-GCM (the key comes from
APP_SECRET_KEY, or a random key file in DATA_DIR) and bound to the account id, so a row
copied to another account cannot be read. Keys never leave the server again: the browser
sees only whether a key is saved and its last four characters. AI requests always use
the settings of the account that makes them.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import secrets
import time

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from . import db
from .ai import catalog
from .config import settings

FIELDS = ("api_key", "base_url", "vision_model", "edit_model")
MAX_FIELD = 4000


def _secret() -> bytes:
    if settings.app_secret_key:
        return hashlib.sha256(settings.app_secret_key.encode()).digest()
    path = settings.data_dir / "secret.key"
    if not path.exists():
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "wb") as f:
            f.write(secrets.token_bytes(32))
    return path.read_bytes()


def _empty() -> dict:
    return {"vision": "builtin", "edit": "", "providers": {}}


def _load(uid: int) -> dict:
    with db.tx() as c:
        row = c.execute("SELECT data FROM ai_settings WHERE user_id = ?", (uid,)).fetchone()
    if not row:
        return _empty()
    blob = base64.b64decode(row["data"])
    try:
        plain = AESGCM(_secret()).decrypt(blob[:12], blob[12:], f"user:{uid}".encode())
    except Exception:  # secret changed: the old keys cannot be read any more
        return _empty()
    return {**_empty(), **json.loads(plain)}


def _store(uid: int, cfg: dict) -> None:
    nonce = secrets.token_bytes(12)
    blob = nonce + AESGCM(_secret()).encrypt(nonce, json.dumps(cfg).encode(), f"user:{uid}".encode())
    with db.tx() as c:
        c.execute("INSERT INTO ai_settings (user_id, data, updated_at) VALUES (?, ?, ?) "
                  "ON CONFLICT(user_id) DO UPDATE SET data = excluded.data, updated_at = excluded.updated_at",
                  (uid, base64.b64encode(blob).decode(), time.time()))


def _providers() -> dict:
    return {p["id"]: p for p in catalog()}


def view(uid: int) -> dict:
    """The settings as the browser may see them: keys replaced by a hint."""
    cfg = _load(uid)
    out = {"vision": cfg["vision"], "edit": cfg["edit"], "providers": {}}
    for pid, p in cfg["providers"].items():
        key = p.get("api_key") or ""
        out["providers"][pid] = {k: v for k, v in p.items() if k != "api_key"} | {
            "has_key": bool(key), "key_hint": key[-4:] if len(key) >= 8 else ""}
    out["configured"] = configured(cfg)
    return out


def configured(cfg: dict) -> bool:
    return cfg["vision"] not in ("", "builtin") or bool(cfg["edit"])


def save(uid: int, body: dict) -> dict:
    """Replace the settings. A provider without ``api_key`` keeps its saved key;
    ``remove_key: true`` deletes it; a provider left out is removed completely."""
    known = _providers()
    old = _load(uid)["providers"]
    providers = {}
    for pid, p in (body.get("providers") or {}).items():
        if pid not in known or not isinstance(p, dict):
            raise ValueError(f"Unknown AI service: {pid}")
        entry = {}
        for k in FIELDS:
            v = p.get(k)
            if v is None or v == "":
                continue
            if not isinstance(v, str) or len(v) > MAX_FIELD:
                raise ValueError(f"Invalid {k.replace('_', ' ')}.")
            entry[k] = v.strip()
        if "api_key" not in entry and not p.get("remove_key") and old.get(pid, {}).get("api_key"):
            entry["api_key"] = old[pid]["api_key"]
        if entry:
            providers[pid] = entry
    vision, edit = body.get("vision") or "builtin", body.get("edit") or ""
    for choice in (vision, edit):
        if choice not in ("", "builtin") and choice not in known:
            raise ValueError(f"Unknown AI service: {choice}")
    _store(uid, {"vision": vision, "edit": edit, "providers": providers})
    return view(uid)


def resolve(uid: int, requested: dict | None) -> dict | None:
    """The ``ai`` settings for a request of this user, or None for the built-in algorithms.
    The browser only names the service (and may pick a model); the key and address always
    come from this user's saved settings."""
    if not requested or requested.get("provider") in (None, "", "builtin"):
        return None
    pid = requested["provider"]
    info = _providers().get(pid)
    if not info:
        raise ValueError(f"Unknown AI service: {pid}")
    saved = _load(uid)["providers"].get(pid, {})
    if info["needs_key"] and not saved.get("api_key") and not info["server_configured"]:
        raise ValueError(f"{info['label']} is not set up for your account yet. Open AI settings and add your API key.")
    return {
        "provider": pid,
        "api_key": saved.get("api_key"),
        "base_url": saved.get("base_url"),
        "vision_model": requested.get("vision_model") or saved.get("vision_model"),
        "edit_model": requested.get("edit_model") or saved.get("edit_model"),
    }


def for_models(uid: int, requested: dict | None) -> dict | None:
    """Model listing ("Connect & load models"): a key or address typed in the dialog but not
    saved yet is used for this one request; otherwise the saved ones."""
    if not requested or requested.get("provider") in (None, "", "builtin"):
        return None
    saved = _load(uid)["providers"].get(requested["provider"], {})
    return {"provider": requested["provider"],
            "api_key": requested.get("api_key") or saved.get("api_key"),
            "base_url": requested.get("base_url") or saved.get("base_url")}
