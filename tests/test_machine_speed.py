"""Machine speed (time estimate only) and the bobbin estimate."""

from fastapi.testclient import TestClient

from embroidery_app import service
from embroidery_app.api import app
from embroidery_app.export import worksheet
from embroidery_app.models import STITCH_RANGES, StitchParams

client = TestClient(app)


def project(svg_text):
    pid = client.post("/api/projects", files={"file": ("a.svg", svg_text.encode())}).json()["id"]
    assert client.post(f"/api/projects/{pid}/extract", json={"options": {"width_mm": 80}}).status_code == 200
    return pid


def test_speed_changes_only_the_time(svg_text, monkeypatch):
    pid = project(svg_text)
    slow = client.post(f"/api/projects/{pid}/stitch", json={"params": {"optimize": False}}).json()
    calls = []
    real = service.plan_stitches
    monkeypatch.setattr(service, "plan_stitches", lambda d, p: calls.append(1) or real(d, p))
    fast = client.post(f"/api/projects/{pid}/stitch", json={"params": {"optimize": False, "machine_spm": 1500}}).json()
    assert calls == []  # same stitches: the cached plan is reused
    assert fast["ops"] == slow["ops"]
    s, f = slow["stats"], fast["stats"]
    assert (s["machine_spm"], f["machine_spm"]) == (750, 1500)
    fixed = s["trims"] * 10 / 60 + (s["color_changes"] + s["stops"]) * 15 / 60
    assert abs(s["est_minutes"] - (s["stitches"] / 750 + fixed)) < 0.06
    assert abs(f["est_minutes"] - (s["stitches"] / 1500 + fixed)) < 0.06
    assert f["est_minutes"] < s["est_minutes"]


def test_speed_is_clamped():
    lo, hi = STITCH_RANGES["machine_spm"]
    assert StitchParams.from_dict({"machine_spm": 5}).machine_spm == lo
    assert StitchParams.from_dict({"machine_spm": 10**9}).machine_spm == hi
    assert StitchParams.from_dict({"machine_spm": 900}).machine_spm == 900  # valid values unchanged
    assert StitchParams().machine_spm == 750


def test_bobbin_and_worksheet(svg_text):
    pid = project(svg_text)
    client.post(f"/api/projects/{pid}/stitch", json={"params": {"machine_spm": 600}})
    plan = service.get_plan(pid)
    s = plan.stats()
    assert s["bobbin_m"] == round(s["thread_m"] / 3, 1) or abs(s["bobbin_m"] - s["thread_m"] / 3) < 0.06
    text = worksheet(plan, "t")
    assert f"at 600 stitches/min" in text and f"bobbin ~{s['bobbin_m']} m" in text
