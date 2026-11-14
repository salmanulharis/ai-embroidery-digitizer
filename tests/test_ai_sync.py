"""Encrypted AI settings shared between browsers: the server keeps only what the browser
encrypted, and only the passphrase that saved them can replace or delete them."""

import base64
import json
import secrets

from fastapi.testclient import TestClient

from embroidery_app import ai_sync
from embroidery_app.api import app

client = TestClient(app)


def _ids():
    return secrets.token_hex(32), secrets.token_hex(32)


def _blob(text="ciphertext"):
    return base64.b64encode(text.encode()).decode(), base64.b64encode(secrets.token_bytes(12)).decode()


def test_save_then_load_returns_the_same_encrypted_settings():
    sid, write = _ids()
    data, iv = _blob()
    r = client.post("/api/ai/sync/save", json={"id": sid, "write": write, "data": data, "iv": iv})
    assert r.status_code == 200 and r.json()["updated"] > 0
    got = client.post("/api/ai/sync/load", json={"id": sid}).json()
    assert (got["data"], got["iv"]) == (data, iv)
    assert "write" not in got  # the write token's hash stays on the server


def test_the_write_token_is_stored_only_as_a_hash():
    sid, write = _ids()
    client.post("/api/ai/sync/save", json={"id": sid, "write": write, "data": _blob()[0], "iv": _blob()[1]})
    stored = (ai_sync.STORE / f"{sid}.json").read_text()
    assert write not in stored
    assert json.loads(stored)["write"] == ai_sync._write_hash(write)


def test_unknown_passphrase_is_404():
    r = client.post("/api/ai/sync/load", json={"id": secrets.token_hex(32)})
    assert r.status_code == 404
    assert "passphrase" in r.json()["detail"]


def test_latest_save_wins():
    sid, write = _ids()
    for text in ("first", "second"):
        data, iv = _blob(text)
        assert client.post("/api/ai/sync/save", json={"id": sid, "write": write, "data": data, "iv": iv}).status_code == 200
    got = client.post("/api/ai/sync/load", json={"id": sid}).json()
    assert base64.b64decode(got["data"]) == b"second"


def test_another_write_token_cannot_overwrite_or_delete():
    sid, write = _ids()
    data, iv = _blob("mine")
    client.post("/api/ai/sync/save", json={"id": sid, "write": write, "data": data, "iv": iv})
    other = secrets.token_hex(32)
    assert client.post("/api/ai/sync/save", json={"id": sid, "write": other, "data": _blob("theirs")[0], "iv": iv}).status_code == 403
    assert client.post("/api/ai/sync/delete", json={"id": sid, "write": other}).status_code == 403
    assert base64.b64decode(client.post("/api/ai/sync/load", json={"id": sid}).json()["data"]) == b"mine"


def test_delete_with_the_right_token():
    sid, write = _ids()
    data, iv = _blob()
    client.post("/api/ai/sync/save", json={"id": sid, "write": write, "data": data, "iv": iv})
    assert client.post("/api/ai/sync/delete", json={"id": sid, "write": write}).status_code == 200
    assert client.post("/api/ai/sync/load", json={"id": sid}).status_code == 404


def test_bad_input_is_rejected():
    sid, write = _ids()
    data, iv = _blob()
    bad = [
        {"id": "../../etc/passwd", "write": write, "data": data, "iv": iv},  # no path tricks
        {"id": sid.upper(), "write": write, "data": data, "iv": iv},
        {"id": sid, "write": "short", "data": data, "iv": iv},
        {"id": sid, "write": write, "data": "not base64!", "iv": iv},
        {"id": sid, "write": write, "data": "A" * (ai_sync.MAX_DATA_CHARS + 4), "iv": iv},
        {"id": sid, "write": write, "data": "", "iv": iv},
    ]
    for body in bad:
        assert client.post("/api/ai/sync/save", json=body).status_code == 400, body
    assert client.post("/api/ai/sync/load", json={"id": "x"}).status_code == 400
