"""Settings presets (presets.py): validation, built-in access by tier, own presets with plan
limits (also under parallel requests), downgrades, account deletion, admin changes, and a
regression check of every built-in preset on the test designs.

Preset output fingerprints live in their own file (tests/golden/presets.json); the main
golden plans are never touched by presets. After a deliberate change of a built-in preset:

    UPDATE_GOLDEN=1 python -m pytest tests/test_presets.py
"""

import json
import math
import os
import threading
from dataclasses import fields
from pathlib import Path

import pytest

from accounts import admin, new_user, real_login  # noqa: F401
from conftest import SVG_SAMPLE, make_logo
from embroidery_app import db, entitlements, presets, subscriptions
from embroidery_app.entitlements import EntitlementError
from embroidery_app.fabrics import get_fabric
from embroidery_app.ingest.raster import RasterOptions, raster_to_design
from embroidery_app.ingest.vector import VectorOptions, svg_to_design
from embroidery_app.models import STITCH, StitchParams
from embroidery_app.stitch import plan_stitches
from embroidery_app.stitch.safety import summarize_risks

GOLDEN = Path(__file__).parent / "golden" / "presets.json"
SAMPLE = {"stitch": {"fill_spacing_mm": 0.42, "optimize": True}, "fabric": {"thread_weight": 60}}


def plan_id(code: str) -> int:
    return next(p["id"] for p in subscriptions.list_plans() if p["code"] == code)


def builtin(c, pid: str) -> dict:
    return next(p for p in c.get("/api/presets").json()["presets"] if p["id"] == pid)


# --------------------------------------------------------------------------- validation (no login needed)

def test_builtins_are_valid_and_inside_the_editor_ranges():
    ids = [b.id for b in presets.BUILTINS]
    assert len(ids) == len(set(ids)) and "standard" in ids
    for b in presets.BUILTINS:
        cleaned, dropped = presets.clean(b.sections)
        assert not dropped and cleaned == b.sections, b.id  # nothing clamped, nothing unknown
        assert b.tier in presets.TIERS and set(b.needs) <= set(entitlements.FEATURES)


def test_clean_drops_unknown_clamps_and_moves_keys():
    cleaned, dropped = presets.clean({
        "stitch": {"fill_spacing_mm": 9, "thread_weight": "60", "running_repeats": 3.0, "bogus": 1, "underlay": "yes"},
        "image": {"brightness": -500, "background": "sparkly"},
        "size": {"num_colors": "", "width_mm": 5},
        "layers": {"0": "#ff0000"},  # never in a preset
    })
    assert cleaned == {"stitch": {"fill_spacing_mm": 1.0, "running_repeats": 3},
                       "fabric": {"thread_weight": 60},  # moved to its own section
                       "image": {"brightness": -100}, "size": {"num_colors": None, "width_mm": 10.0}}
    assert set(dropped) == {"stitch.bogus", "stitch.underlay", "image.background", "layers"}
    with pytest.raises(ValueError):
        presets.clean(["not", "sections"])


def test_migration_gives_the_seeded_plans_their_preset_access():
    free, basic, pro = (subscriptions.get_plan(plan_id(c)) for c in ("free", "basic", "pro"))
    feats = {k: f.default for k, f in entitlements.FEATURES.items()}
    assert (feats | free["features"])["presets_basic"] and not (feats | free["features"])["custom_presets"]
    assert free["limits"] == {"max_custom_presets": 0}
    assert basic["features"]["custom_presets"] and not (feats | basic["features"])["presets_premium"]
    assert basic["limits"] == {"max_custom_presets": 5}
    assert pro["features"]["custom_presets"] and pro["features"]["presets_premium"]
    assert pro["limits"] == {"max_custom_presets": 25}


# --------------------------------------------------------------------------- built-ins by tier

@pytest.mark.usefixtures("real_login")
def test_premium_values_are_never_sent_without_the_tier():
    a = admin()
    _, free = new_user(a)
    _, pro = new_user(a, plan_id=plan_id("pro"))
    std, fine = builtin(free, "standard"), builtin(free, "fine_detail")
    assert not std["locked"] and std["sections"]["stitch"]["fill_spacing_mm"] == 0.40
    assert fine["locked"] and "sections" not in fine and fine["needs"] == ["Premium presets"]
    assert "fill_spacing_mm" not in json.dumps(free.get("/api/presets").json()["presets"][2])
    assert not builtin(pro, "fine_detail")["locked"] and builtin(pro, "fine_detail")["sections"]


@pytest.mark.usefixtures("real_login")
def test_locked_presets_name_the_plans_that_include_them():
    a = admin()
    _, free = new_user(a)
    assert builtin(free, "fine_detail")["plans"] == ["Pro"]
    assert "plans" not in builtin(free, "standard")


@pytest.mark.usefixtures("real_login")
def test_suggestion_after_upload_respects_the_tier():
    a = admin()
    free_user, _ = new_user(a)
    pro_user, _ = new_user(a, plan_id=plan_id("pro"))
    photo = {"design_type": "photo", "text": {"found": False}, "extract": {"denoise": 2}, "cleanup": "redraw"}
    small = {"design_type": "logo", "text": {"found": True, "letter_height_mm": 4.2}, "extract": {"denoise": 2}}
    grainy = {"design_type": "logo", "text": {"found": False}, "extract": {"denoise": 3}, "cleanup": "enhance"}
    plain = {"design_type": "logo", "text": {"found": True, "letter_height_mm": 12}, "extract": {"denoise": 1}}
    assert presets.suggest(photo, 100, pro_user["id"])["id"] == "photo"
    assert presets.suggest(photo, 100, free_user["id"]) is None  # premium: never offered without the tier
    s = presets.suggest(small, 90, free_user["id"])
    assert s == {"id": "small_text", "label": "Small text & logos", "reason": "it has lettering about 4.2 mm tall"}
    assert presets.suggest(plain, 250, pro_user["id"])["id"] == "large"
    assert presets.suggest(plain, 250, free_user["id"]) is None
    assert presets.suggest(grainy, 100, free_user["id"])["id"] == "clean_logo"
    assert presets.suggest(plain, 100, pro_user["id"]) is None


def test_upload_analysis_includes_a_preset_suggestion(logo_png):
    from fastapi.testclient import TestClient

    from embroidery_app.api import app

    c = TestClient(app)
    pid = c.post("/api/projects", files={"file": ("logo.png", logo_png)}).json()["id"]
    r = c.post(f"/api/projects/{pid}/ai/suggest", json={"ai": None, "width_mm": 80})
    assert r.status_code == 200, r.text
    s = r.json()["preset"]
    assert s is None or {"id", "label", "reason"} == set(s)


@pytest.mark.usefixtures("real_login")
def test_photo_preset_also_needs_photo_stitch():
    a = admin()
    user, pro = new_user(a, plan_id=plan_id("pro"))
    assert not builtin(pro, "photo")["locked"]
    a.put(f"/api/admin/users/{user['id']}/overrides", json={"features": {"photo_stitch": False}})
    photo = builtin(pro, "photo")
    assert photo["locked"] and photo["needs"] == ["Photo stitch"] and "sections" not in photo


@pytest.mark.usefixtures("real_login")
def test_admin_moves_a_builtin_between_tiers_and_it_is_audited():
    a = admin()
    _, free = new_user(a)
    assert builtin(free, "fine_detail")["locked"]
    try:
        r = a.put("/api/admin/presets", json={"access": {"fine_detail": "basic"}})
        assert r.status_code == 200, r.text
        row = next(b for b in r.json()["builtins"] if b["id"] == "fine_detail")
        assert row["tier"] == "basic" and row["default_tier"] == "premium"
        assert not builtin(free, "fine_detail")["locked"]
        entries = a.get("/api/admin/audit", params={"q": "presets"}).json()["entries"]
        assert entries[0]["action"] == "presets.access" and "premium -> basic" in json.dumps(entries[0]["detail"])
        assert a.put("/api/admin/presets", json={"access": {"nope": "basic"}}).status_code == 400
        assert a.put("/api/admin/presets", json={"access": {"standard": "gold"}}).status_code == 400
        assert free.put("/api/admin/presets", json={"access": {"fine_detail": "basic"}}).status_code == 403
    finally:
        a.put("/api/admin/presets", json={"access": {"fine_detail": None}})
    assert builtin(free, "fine_detail")["locked"]


# --------------------------------------------------------------------------- own presets

@pytest.mark.usefixtures("real_login")
def test_free_account_cannot_save_presets():
    a = admin()
    _, c = new_user(a)
    r = c.post("/api/me/presets", json={"name": "Mine", "sections": SAMPLE})
    assert r.status_code == 403 and r.json()["code"] == "feature_unavailable"
    acc = c.get("/api/me/account").json()
    assert acc["presets"] == {"count": 0, "limit": 0, "feature": False, "can_save": False}


@pytest.mark.usefixtures("real_login")
def test_save_list_edit_duplicate_delete():
    a = admin()
    _, c = new_user(a, plan_id=plan_id("basic"))
    r = c.post("/api/me/presets", json={"name": "  Polo   logos ", "description": "left chest",
                                        "sections": {**SAMPLE, "stitch": {**SAMPLE["stitch"], "nope": 1}}})
    assert r.status_code == 201, r.text
    p = r.json()
    assert p["name"] == "Polo logos" and p["skipped"] == ["stitch.nope"] and p["kinds"] == ["stitch"]
    assert p["sections"] == SAMPLE
    # name clash (any letter case): 409 with the existing preset, so the browser can offer Replace
    r = c.post("/api/me/presets", json={"name": "polo LOGOS", "sections": SAMPLE})
    assert r.status_code == 409 and r.json()["code"] == "name_taken" and r.json()["existing_id"] == p["id"]
    assert "\"Polo logos\"" in r.json()["detail"]  # the existing preset's name
    r = c.put(f"/api/me/presets/{p['id']}", json={"sections": {"image": {"sharpen": 2}}, "description": "new"})
    assert r.status_code == 200 and r.json()["sections"] == {"image": {"sharpen": 2}} and r.json()["kinds"] == ["image"]
    d = c.post(f"/api/me/presets/{p['id']}/duplicate")
    assert d.status_code == 201 and d.json()["name"] == "Polo logos (copy)"
    assert c.post(f"/api/me/presets/{p['id']}/duplicate").json()["name"] == "Polo logos (copy 2)"
    mine = c.get("/api/me/presets").json()
    assert mine["count"] == 3 and mine["limit"] == 5 and mine["can_save"]
    assert sorted(x["name"] for x in mine["presets"]) == ["Polo logos", "Polo logos (copy 2)", "Polo logos (copy)"]
    assert c.delete(f"/api/me/presets/{d.json()['id']}").json() == {"deleted": True}
    assert c.get("/api/me/presets").json()["count"] == 2
    # bad input
    assert c.post("/api/me/presets", json={"name": "", "sections": SAMPLE}).status_code == 400
    assert c.post("/api/me/presets", json={"name": "x" * 61, "sections": SAMPLE}).status_code == 400
    assert c.post("/api/me/presets", json={"name": "Empty", "sections": {"stitch": {"bogus": 1}}}).status_code == 400
    assert c.post("/api/me/presets", json={"name": "Big", "sections": SAMPLE, "description": "d" * 301}).status_code == 400


@pytest.mark.usefixtures("real_login")
def test_presets_are_owner_checked():
    a = admin()
    _, c1 = new_user(a, plan_id=plan_id("basic"))
    _, c2 = new_user(a, plan_id=plan_id("basic"))
    pid = c1.post("/api/me/presets", json={"name": "Private", "sections": SAMPLE}).json()["id"]
    assert c2.put(f"/api/me/presets/{pid}", json={"name": "Stolen"}).status_code == 404
    assert c2.delete(f"/api/me/presets/{pid}").status_code == 404
    assert c2.post(f"/api/me/presets/{pid}/duplicate").status_code == 404
    assert c2.get("/api/me/presets").json()["presets"] == []
    assert c1.get("/api/me/presets").json()["presets"][0]["name"] == "Private"


@pytest.mark.usefixtures("real_login")
def test_limit_holds_under_parallel_requests():
    a = admin()
    user, c = new_user(a, plan_id=plan_id("basic"))
    a.put(f"/api/admin/users/{user['id']}/overrides", json={"limits": {"max_custom_presets": 3}})
    assert entitlements.effective(user["id"]).limit_of("max_custom_presets") == 3
    results: list = []

    def save(i):
        try:
            presets.create(user["id"], f"P{i}", "", SAMPLE)
            results.append("ok")
        except EntitlementError as e:
            results.append(e.code)

    threads = [threading.Thread(target=save, args=(i,)) for i in range(10)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert results.count("ok") == 3 and results.count("preset_limit") == 7
    r = c.post("/api/me/presets", json={"name": "One more", "sections": SAMPLE})
    assert r.status_code == 403 and r.json()["code"] == "preset_limit" and "3 of 3" in r.json()["detail"]
    pid = c.get("/api/me/presets").json()["presets"][0]["id"]
    assert c.post(f"/api/me/presets/{pid}/duplicate").json()["code"] == "preset_limit"


@pytest.mark.usefixtures("real_login")
def test_downgrade_keeps_presets_usable_but_no_new_ones():
    a = admin()
    user, c = new_user(a, plan_id=plan_id("basic"))
    ids = [c.post("/api/me/presets", json={"name": f"Mine {i}", "sections": SAMPLE}).json()["id"] for i in range(3)]
    a.put(f"/api/admin/users/{user['id']}/subscription", json={"plan_id": plan_id("free")})
    mine = c.get("/api/me/presets").json()
    assert mine["count"] == 3 and mine["limit"] == 0 and not mine["feature"] and not mine["can_save"]
    assert mine["presets"][0]["sections"] == SAMPLE  # still applicable
    assert c.put(f"/api/me/presets/{ids[0]}", json={"name": "Renamed"}).status_code == 200
    assert c.put(f"/api/me/presets/{ids[0]}", json={"sections": {"stitch": {"optimize": False}}}).status_code == 200
    assert c.delete(f"/api/me/presets/{ids[1]}").status_code == 200
    assert c.post("/api/me/presets", json={"name": "New", "sections": SAMPLE}).json()["code"] == "feature_unavailable"
    assert c.post(f"/api/me/presets/{ids[2]}/duplicate").status_code == 403
    # on a plan with the feature but fewer slots than presets: still usable, saving waits
    a.put(f"/api/admin/users/{user['id']}/subscription", json={"plan_id": plan_id("basic")})
    a.put(f"/api/admin/users/{user['id']}/overrides", json={"limits": {"max_custom_presets": 1}})
    st = c.get("/api/me/account").json()["presets"]
    assert st == {"count": 2, "limit": 1, "feature": True, "can_save": False}
    assert c.post("/api/me/presets", json={"name": "New", "sections": SAMPLE}).json()["code"] == "preset_limit"
    # removing the override value brings back the plan's limit
    a.put(f"/api/admin/users/{user['id']}/overrides", json={"limits": {"max_custom_presets": None}})
    assert c.get("/api/me/account").json()["presets"]["limit"] == 5


@pytest.mark.usefixtures("real_login")
def test_deleting_the_account_deletes_its_presets():
    a = admin()
    user, c = new_user(a, plan_id=plan_id("basic"))
    c.post("/api/me/presets", json={"name": "Gone soon", "sections": SAMPLE})
    assert a.delete(f"/api/admin/users/{user['id']}").status_code == 200
    with db.tx() as conn:
        assert conn.execute("SELECT COUNT(*) FROM user_presets WHERE user_id = ?", (user["id"],)).fetchone()[0] == 0


@pytest.mark.usefixtures("real_login")
def test_super_admin_has_no_preset_limit_and_admin_sees_counts_only():
    a = admin()
    st = a.get("/api/me/account").json()["presets"]
    assert st["limit"] is None and st["feature"] and st["can_save"]
    assert not builtin(a, "photo")["locked"]
    view = a.get("/api/admin/presets").json()
    assert {"presets", "accounts"} == set(view["saved"]) and "sections" not in json.dumps(view)


@pytest.mark.usefixtures("real_login")
def test_plan_limits_are_validated_and_audited():
    a = admin()
    pid = plan_id("basic")
    try:
        r = a.patch(f"/api/admin/plans/{pid}", json={"limits": {"max_custom_presets": 7}})
        assert r.status_code == 200 and r.json()["limits"] == {"max_custom_presets": 7}
        entry = a.get("/api/admin/audit", params={"q": "plan.update"}).json()["entries"][0]
        assert "limits" in entry["detail"]
        assert a.patch(f"/api/admin/plans/{pid}", json={"limits": {"max_custom_presets": -1}}).status_code == 400
        assert a.patch(f"/api/admin/plans/{pid}", json={"limits": {"nope": 1}}).status_code == 400
        assert a.patch(f"/api/admin/plans/{pid}", json={"limits": {"max_custom_presets": 1.5}}).status_code == 400
        assert a.get("/api/admin/plans").json()["limits"][0]["key"] == "max_custom_presets"
    finally:
        a.patch(f"/api/admin/plans/{pid}", json={"limits": {"max_custom_presets": 5}})


# --------------------------------------------------------------------------- built-in presets: stitch regression

def _resolve(sections: dict) -> tuple[dict, dict]:
    """What applying a preset does in the editor: the fabric's values first, then its own."""
    stitch = {}
    fabric = sections.get("fabric", {}).get("fabric")
    if fabric:
        stitch.update(get_fabric(fabric).settings)
    stitch.update(sections.get("fabric", {}))
    stitch.update(sections.get("stitch", {}))
    image = {k: v for k, v in sections.get("image", {}).items() if k != "text_mode"}  # text_mode = the two detail fields
    return image | sections.get("size", {}), stitch


def _options(cls, values):
    names = {f.name for f in fields(cls)}
    return cls(**{k: v for k, v in values.items() if k in names})


def _measure(kind: str, sections: dict) -> dict:
    image, stitch = _resolve(sections)
    if kind == "raster":
        design, _ = raster_to_design(make_logo(), _options(RasterOptions, {"width_mm": 80, **image}))
    else:
        design = svg_to_design(SVG_SAMPLE, VectorOptions(width_mm=80))  # picture settings do not apply
    plan = plan_stitches(design, StitchParams.from_dict(stitch))
    steps = [math.dist(a[1:3], b[1:3]) for a, b in zip(plan.ops, plan.ops[1:]) if a[0] == b[0] == STITCH]
    return {"stitches": sum(1 for op in plan.ops if op[0] == STITCH), "longest": max(steps),
            "risks": sum(summarize_risks(plan.risks).values())}


@pytest.fixture(scope="module")
def measured() -> dict:
    return {(kind, b.id): _measure(kind, b.sections) for kind in ("raster", "vector") for b in presets.BUILTINS}


@pytest.mark.parametrize("kind", ["raster", "vector"])
def test_builtin_presets_sew_safely(measured, kind):
    base = measured[(kind, "standard")]
    for b in presets.BUILTINS:
        m = measured[(kind, b.id)]
        assert m["longest"] <= 7.0 + 1e-6, (b.id, m)
        # no more machine-check markers than Standard (a few more allowed for denser presets)
        assert m["risks"] <= base["risks"] + max(5, base["risks"] // 4), (b.id, m, base)
        if b.id != "photo":  # photo stitch sews the whole picture: a different order of magnitude
            assert 0.6 * base["stitches"] <= m["stitches"] <= 1.5 * base["stitches"], (b.id, m, base)


def test_builtin_preset_fingerprints(measured):
    """Stitch counts per preset and design, within 3 % (platform float differences)."""
    now = {f"{kind}:{pid}": m["stitches"] for (kind, pid), m in sorted(measured.items())}
    if os.environ.get("UPDATE_GOLDEN"):
        GOLDEN.write_text(json.dumps(now, indent=1, sort_keys=True) + "\n", encoding="utf-8")
        pytest.skip("preset fingerprints updated")
    assert GOLDEN.exists(), "no preset fingerprints yet: UPDATE_GOLDEN=1 python -m pytest tests/test_presets.py"
    saved = json.loads(GOLDEN.read_text(encoding="utf-8"))
    assert set(saved) == set(now), "a built-in preset was added or removed: UPDATE_GOLDEN=1"
    off = {k: (saved[k], now[k]) for k in now if abs(now[k] - saved[k]) > 0.03 * saved[k]}
    assert not off, off
