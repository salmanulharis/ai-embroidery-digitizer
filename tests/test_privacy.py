"""Temporary projects: deleted on a new upload, on close, on sign-out, when the session
ends, after a page release (with a grace period for reloads), when idle or too old, and
when orphaned. Projects stay invisible to other accounts, and no project content reaches
the database."""

import time

import pytest

from accounts import admin, browser, logged_in, new_user, png, real_login, stitched_project  # noqa: F401
from embroidery_app import app_settings, db, service, temp_projects

pytestmark = pytest.mark.usefixtures("real_login")


def on_disk(pid: str) -> bool:
    return (service.PROJECTS / pid).exists()


def upload(c, name="logo.png") -> str:
    r = c.post("/api/projects", files={"file": (name, png())})
    assert r.status_code == 200, r.text
    return r.json()["id"]


def test_a_new_upload_replaces_the_sessions_previous_project():
    a = admin()
    user, c = new_user(a)
    other_device = logged_in(user["email"])
    first = upload(c)
    elsewhere = upload(other_device)
    second = upload(c)
    assert not on_disk(first) and on_disk(second) and on_disk(elsewhere)  # only this browser's project goes
    assert c.get(f"/api/projects/{first}/design").status_code == 404


def test_close_and_sign_out_delete_projects():
    a = admin()
    user, c = new_user(a)
    pid = upload(c)
    assert c.delete(f"/api/projects/{pid}").status_code == 200 and not on_disk(pid)
    pid = upload(c)
    c.post("/api/auth/logout")
    assert not on_disk(pid)
    with db.tx() as conn:
        assert conn.execute("SELECT COUNT(*) FROM temp_projects WHERE user_id = ?", (user["id"],)).fetchone()[0] == 0


def test_ended_sessions_lose_their_projects():
    a = admin()
    user, c = new_user(a)
    pid = upload(c)
    with db.tx() as conn:  # the session runs out
        conn.execute("UPDATE sessions SET expires_at = ? WHERE user_id = ?", (time.time() - 1, user["id"]))
    temp_projects.sweep()
    assert not on_disk(pid)
    # a password reset by the admin ends the sessions too
    c2 = logged_in(user["email"])
    pid2 = upload(c2)
    a.post(f"/api/admin/users/{user['id']}/password", json={"password": "another-password-1"})
    assert not on_disk(pid2) and c2.get("/api/auth/me").status_code == 401


def test_page_release_waits_for_a_reload():
    a = admin()
    _, c = new_user(a)
    pid = stitched_project(c)
    grace = app_settings.get("project_release_grace_s")
    assert c.post(f"/api/projects/{pid}/release").status_code == 204
    temp_projects.sweep()
    assert on_disk(pid)  # within the grace period: a reload can reopen it
    assert c.get(f"/api/projects/{pid}/design").status_code == 200  # the reloaded page uses it: release cancelled
    temp_projects.sweep(time.time() + grace + 5)
    assert on_disk(pid)
    c.post(f"/api/projects/{pid}/release")
    temp_projects.sweep(time.time() + grace + 5)  # the tab was closed for good
    assert not on_disk(pid)


def test_idle_and_old_projects_are_removed_on_the_server():
    a = admin()
    _, c = new_user(a)
    pid = upload(c)
    idle = app_settings.get("project_idle_minutes") * 60
    temp_projects.sweep(time.time() + idle - 30)
    assert on_disk(pid)
    temp_projects.sweep(time.time() + idle + 30)
    assert not on_disk(pid)
    pid = upload(c)
    with db.tx() as conn:  # in use all the time, but older than the maximum
        conn.execute("UPDATE temp_projects SET created_at = ? WHERE id = ?",
                     (time.time() - app_settings.get("project_max_hours") * 3600 - 10, pid))
    temp_projects.sweep()
    assert not on_disk(pid)


def test_orphan_folders_are_removed():
    d = service.PROJECTS / "0123abcd0123"
    d.mkdir(exist_ok=True)
    (d / "source.png").write_bytes(b"x")
    temp_projects.sweep()
    assert d.exists()  # might be an upload in progress
    temp_projects.sweep(time.time() + temp_projects.ORPHAN_GRACE_S + 5)
    assert not d.exists()


def test_projects_are_invisible_to_other_accounts():
    a = admin()
    _, owner = new_user(a)
    _, other = new_user(a)
    pid = stitched_project(owner)
    for path in ("design", "source", "export/dst", "quota"):
        assert other.get(f"/api/projects/{pid}/{path}").status_code == 404, path
    assert other.delete(f"/api/projects/{pid}").status_code == 404
    other.post(f"/api/projects/{pid}/release")  # silently ignored
    temp_projects.sweep(time.time() + app_settings.get("project_release_grace_s") + 5)
    assert on_disk(pid)
    assert browser().post(f"/api/projects/{pid}/release").status_code == 204  # not signed in: nothing happens
    assert on_disk(pid)


def test_deleting_an_account_deletes_its_files():
    a = admin()
    user, c = new_user(a)
    pid = upload(c)
    assert a.delete(f"/api/admin/users/{user['id']}").status_code == 200
    assert not on_disk(pid)


def test_no_project_content_in_the_database():
    a = admin()
    _, c = new_user(a)
    name = "secret-client-artwork-2931.png"
    r = c.post("/api/projects", files={"file": (name, png())})
    pid = r.json()["id"]
    c.post(f"/api/projects/{pid}/extract", json={"options": {"width_mm": 60}})
    c.post(f"/api/projects/{pid}/stitch", json={"params": {}})
    c.get(f"/api/projects/{pid}/export/dst")
    c.post(f"/api/projects/{pid}/ai/suggest", json={})
    with db.tx() as conn:
        tables = [r["name"] for r in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")]
        for t in tables:
            for row in conn.execute(f"SELECT * FROM {t}"):
                text = " ".join(str(v) for v in tuple(row))
                assert "secret-client-artwork" not in text, t
                assert pid not in text or t == "temp_projects", t  # usage keeps only a one-way hash
    for f in (db.PATH, db.PATH.with_name(db.PATH.name + "-wal")):
        assert not f.exists() or b"secret-client-artwork" not in f.read_bytes()
