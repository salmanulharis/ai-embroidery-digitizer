"""Helpers for tests that use the real login (separate browsers, real sessions)."""

import uuid

import cv2
import numpy as np
import pytest
from fastapi.testclient import TestClient

from embroidery_app import auth
from embroidery_app.api import app

ADMIN = ("admin@example.com", "admin-password-123")
PASSWORD = "user-password-123"


@pytest.fixture
def real_login():
    saved = app.dependency_overrides.pop(auth.require_user, None)
    auth._failures.clear()
    auth.ensure_super_admin()
    yield
    if saved is not None:
        app.dependency_overrides[auth.require_user] = saved
    auth._failures.clear()


def browser() -> TestClient:
    return TestClient(app)  # its own cookie jar, like a separate browser


def logged_in(login: str, password: str = PASSWORD, **extra) -> TestClient:
    c = browser()
    r = c.post("/api/auth/login", json={"login": login, "password": password, **extra})
    assert r.status_code == 200, r.text
    return c


def admin() -> TestClient:
    return logged_in(*ADMIN)


def new_user(admin_client: TestClient, **extra) -> tuple[dict, TestClient]:
    email = f"u{uuid.uuid4().hex[:10]}@example.com"
    r = admin_client.post("/api/admin/users", json={"email": email, "password": PASSWORD, **extra})
    assert r.status_code == 200, r.text
    return r.json(), logged_in(email)


def png(seed: int = 0) -> bytes:
    img = np.full((200, 300, 3), 255, np.uint8)
    cv2.rectangle(img, (40 + seed % 20, 40), (200, 160), (40, 40, 200), -1)
    return cv2.imencode(".png", img)[1].tobytes()


def stitched_project(c: TestClient, seed: int = 0) -> str:
    """Upload, prepare and stitch a small design; returns the project id."""
    r = c.post("/api/projects", files={"file": (f"d{seed}.png", png(seed))})
    assert r.status_code == 200, r.text
    pid = r.json()["id"]
    assert c.post(f"/api/projects/{pid}/extract", json={"options": {"width_mm": 60}}).status_code == 200
    assert c.post(f"/api/projects/{pid}/stitch", json={"params": {}}).status_code == 200
    return pid
