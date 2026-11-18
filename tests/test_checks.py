"""The editor's Checks: structured warnings with codes, actions and one-click fixes."""

import cv2
import numpy as np
from fastapi.testclient import TestClient
from shapely.geometry import box

from embroidery_app import service
from embroidery_app.ai.builtin import review_plan
from embroidery_app.api import app
from embroidery_app.models import ColorLayer, Design, DesignObject, StitchParams
from embroidery_app.stitch import plan_stitches

client = TestClient(app)


def _project(width_mm=80):
    img = np.full((500, 700, 3), 255, np.uint8)
    cv2.rectangle(img, (60, 60), (300, 400), (40, 40, 200), -1)
    cv2.circle(img, (520, 230), 120, (20, 140, 20), -1)
    pid = client.post("/api/projects", files={"file": ("b.png", cv2.imencode(".png", img)[1].tobytes())}).json()["id"]
    res = client.post(f"/api/projects/{pid}/extract", json={"options": {"width_mm": width_mm}}).json()
    return pid, res


def _stitch(pid, **params):
    r = client.post(f"/api/projects/{pid}/stitch", json={"params": params})
    assert r.status_code == 200, r.text
    return r.json()


def test_every_warning_is_a_check_with_the_same_text():
    pid, res = _project()
    small = min(res["objects"], key=lambda o: o["area_mm2"])
    client.put(f"/api/projects/{pid}/objects", json={"ids": [small["id"]], "spacing_mm": 0.25})
    out = _stitch(pid)
    texts = [c["text"] for c in out["checks"]]
    assert all(w in texts for w in out["warnings"])  # the plain warnings are unchanged and all present
    for c in out["checks"]:
        assert c["code"] and c["severity"] in ("high", "medium", "low") and isinstance(c["actions"], list)
        assert set(c["actions"]) <= {"show", "size", "select"}
        if "change" in c:
            assert c["fix"] and c["change"]
    if small["area_mm2"] < 3:
        dense = next(c for c in out["checks"] if c["code"] == "dense_tiny")
        assert dense["ids"] == [small["id"]] and dense["actions"] == ["select"]


def test_review_findings_become_checks_with_a_fix():
    pid, _ = _project()
    out = _stitch(pid, underlay=False)
    under = next(c for c in out["checks"] if c["code"] == "underlay_off")
    assert under["change"] == {"underlay": True} and under["fix"] == "Turn on foundation stitches"
    # a low finding alone is not a check (it stays in "Check my stitches")
    out = _stitch(pid, optimize=False)
    assert not any(c["code"] == "optimize_off" for c in out["checks"])


def test_fix_solves_the_check():
    pid, _ = _project()
    out = _stitch(pid, underlay=False)
    fix = next(c for c in out["checks"] if c["code"] == "underlay_off")["change"]
    again = _stitch(pid, **fix)
    assert not any(c["code"] == "underlay_off" for c in again["checks"])


def test_review_issues_carry_code_change_and_ids():
    d = Design(60, 40, [ColorLayer(0, (200, 20, 20), "red")],
               [DesignObject(0, box(5, 5, 40, 30)), DesignObject(0, box(50, 5, 50.1, 5.1))])
    p = StitchParams(underlay=False, min_object_area_mm2=0.5)
    r = review_plan(plan_stitches(d, p), d, p)
    by = {i["code"]: i for i in r["issues"]}
    assert by["underlay_off"]["change"] == {"underlay": True}
    # the skipped mark (0.01 mm²) is kept by going down to the default, never lower (marks become a sewable dot)
    assert by["skipped_marks"]["ids"] == [d.objects[1].id]
    assert by["skipped_marks"]["change"] == {"min_object_area_mm2": StitchParams().min_object_area_mm2}
    assert by["skipped_marks"]["fix"].startswith("Keep them")
    assert r["changes"]["underlay"] is True  # the combined changes stay as before
    assert all(i["code"] for i in r["issues"])


def test_a_failing_review_never_breaks_the_stitches(monkeypatch):
    pid, _ = _project()

    def boom(*a, **k):
        raise RuntimeError("review bug")

    monkeypatch.setattr(service, "review_plan", boom)
    out = _stitch(pid)
    assert out["stats"]["stitches"] > 0 and isinstance(out["checks"], list)


def test_machine_check_spots_lead_to_the_check_view():
    plan_risks = [{"type": "short", "x": 1, "y": 1}, {"type": "thin", "x": 2, "y": 2}]
    checks = service._stitch_checks(
        plan=_fake_plan(), design=Design(10, 10, [], []), params=StitchParams(),
        stats={"stitches": 10, "width_mm": 10, "height_mm": 10}, risks=plan_risks,
        warnings=["Machine check: 1 spot(s)", "Machine check: thin"], dense_tiny=[])
    by = {c["code"]: c for c in checks}
    assert by["thread_break"]["actions"] == ["show"] and by["thread_break"]["spots"] == ["short", "stack", "dense"]
    assert by["thin_lettering"]["spots"] == ["thin"]


def _fake_plan():
    d = Design(20, 20, [ColorLayer(0, (0, 0, 0), "black")], [DesignObject(0, box(2, 2, 18, 18))])
    return plan_stitches(d, StitchParams())
