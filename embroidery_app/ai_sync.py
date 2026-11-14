"""Encrypted AI settings, shared between one person's browsers.

The browser derives three values from a passphrase the person chooses (PBKDF2-SHA256):
a lookup id, a write token and an AES-GCM key. This store only ever receives the id,
the write token (kept as a hash) and the encrypted settings - never the passphrase,
never an API key in readable form. Whoever knows the passphrase can load the settings;
only the same passphrase can replace or delete them.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import json
import re
import threading
import time

from .config import settings

STORE = settings.data_dir / "ai_sync"
_HEX64 = re.compile(r"^[a-f0-9]{64}$")
MAX_DATA_CHARS = 64_000  # base64 of the encrypted settings: a few providers fit easily
_lock = threading.Lock()


class SyncNotFound(Exception):
    """Nothing saved under this passphrase."""


class SyncDenied(Exception):
    """The write token does not match the one the settings were saved with."""


def _hex64(value: str, what: str) -> str:
    if not isinstance(value, str) or not _HEX64.match(value):
        raise ValueError(f"Invalid {what}.")
    return value


def _b64(value: str, what: str, max_chars: int) -> str:
    if not isinstance(value, str) or not value or len(value) > max_chars:
        raise ValueError(f"Invalid {what}.")
    try:
        base64.b64decode(value, validate=True)
    except (binascii.Error, ValueError):
        raise ValueError(f"Invalid {what}.") from None
    return value


def _path(sid: str):
    return STORE / f"{_hex64(sid, 'sync id')}.json"


def _write_hash(write: str) -> str:
    return hashlib.sha256(_hex64(write, "write token").encode()).hexdigest()


def _read(sid: str) -> dict:
    path = _path(sid)
    if not path.exists():
        raise SyncNotFound(sid)
    return json.loads(path.read_text(encoding="utf-8"))


def load(sid: str) -> dict:
    rec = _read(sid)
    return {"data": rec["data"], "iv": rec["iv"], "updated": rec["updated"]}


def save(sid: str, write: str, data: str, iv: str) -> dict:
    path = _path(sid)
    token = _write_hash(write)
    record = {"data": _b64(data, "settings", MAX_DATA_CHARS), "iv": _b64(iv, "iv", 32), "write": token, "updated": time.time()}
    with _lock:
        if path.exists() and not hmac.compare_digest(_read(sid)["write"], token):
            raise SyncDenied(sid)
        STORE.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(record), encoding="utf-8")
        tmp.replace(path)
    return {"updated": record["updated"]}


def delete(sid: str, write: str) -> None:
    path = _path(sid)
    token = _write_hash(write)
    with _lock:
        if not hmac.compare_digest(_read(sid)["write"], token):
            raise SyncDenied(sid)
        path.unlink()
