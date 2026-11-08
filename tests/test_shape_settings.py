"""Per-shape stitch style / direction, whole-design direction, identity, undo, re-extract."""

import math
from collections import Counter

import cv2
import numpy as np
import pytest
from fastapi.testclient import TestClient
from shapely.geometry import LineString, Point, box

from embroidery_app import service
from embroidery_app.api import app
from embroidery_app.models import ColorLayer, Design, DesignObject, StitchParams
from embroidery_app.stitch import plan_stitches
from embroidery_app.stitch.planner import describe_objects

client = TestClient(app)
PARAMS = StitchParams(underlay=False, optimize=False, vary_fill_angle=False, fill_angle=30)


def dominant_angle(plan, region, kinds=("fill",), min_len=1.5) -> float:
    """Most common direction (deg, 0-180) of stitches of ``kinds`` inside ``region``."""
    votes: Counter = Counter()
    prev = None
    for cmd, x, y, kind in plan.ops:
        if cmd == "stitch" and prev is not None and kind in kinds:
            dx, dy = x - prev[0], y - prev[1]
            if math.hypot(dx, dy) >= min_len and region.contains(Point((x + prev[0]) / 2, (y + prev[1]) / 2)):
                votes[round(math.degrees(math.atan2(dy, dx)) % 180 / 5) * 5 % 180] += 1
        if cmd in ("stitch", "jump"):
            prev = (x, y)
    assert votes, "no stitches found in region"
    return votes.most_common(1)[0][0]


def near(measured: float, expected: float, tol: float = 10) -> bool:
    """Satin is a zig-zag: each crossing also advances one spacing along the column,
    so on a 3 mm column the stitches tilt ~±7.5° around the true direction."""
    diff = abs(measured - expected) % 180
    return min(diff, 180 - diff) <= tol


def two_squares(colour_angle=None) -> Design:
    layers = [ColorLayer(0, (0, 0, 0), "Black", angle=colour_angle)]
    a = DesignObject(0, box(0, 0, 20, 20), id="a")
    b = DesignObject(0, box(30, 0, 50, 20), id="b")
    return Design(50, 20, layers, [a, b])


# ---------------------------------------------------------------- model / compatibility

def test_old_saved_objects_load_and_serialise_unchanged():
    old = {"layer": 0, "kind": "area", "stroke_width_mm": 0.0, "wkt": box(0, 0, 1, 1).wkt}
    o = DesignObject.from_dict(old)
    assert o.id and o.stitch_type is None and o.angle is None and o.enabled and not o.customised
    d = o.to_dict()
    assert set(d) == {"id", "layer", "kind", "stroke_width_mm", "wkt"}  # no override keys when unset


def test_angle_is_normalised_and_settings_roundtrip():
    o = DesignObject(0, box(0, 0, 1, 1), stitch_type="satin", angle=135.0, enabled=False)
    back = DesignObject.from_dict({**o.to_dict(), "angle": 315})
    assert back.angle == 135 and back.stitch_type == "satin" and back.enabled is False
    assert DesignObject.from_dict({**o.to_dict(), "stitch_type": "bogus"}).stitch_type is None


# ---------------------------------------------------------------- planner precedence

def test_direction_precedence_shape_then_colour_then_automatic():
    d = two_squares()
    plan = plan_stitches(d, PARAMS)
    assert dominant_angle(plan, box(0, 0, 20, 20)) == 30 and dominant_angle(plan, box(30, 0, 50, 20)) == 30

    d = two_squares(colour_angle=90)
    d.objects[0].angle = 0
    plan = plan_stitches(d, PARAMS)
    assert dominant_angle(plan, box(0, 0, 20, 20)) == 0  # shape setting wins
    assert dominant_angle(plan, box(30, 0, 50, 20)) == 90  # colour setting


def test_whole_design_direction_turns_every_fill_and_satin():
    layers = [ColorLayer(0, (0, 0, 0), "Black", angle=90), ColorLayer(1, (200, 0, 0), "Red")]
    objs = [
        DesignObject(0, box(0, 0, 20, 20)),
        DesignObject(1, box(30, 0, 50, 20)),
        DesignObject(1, box(0, 30, 50, 33), stitch_type="satin"),  # straight satin bar
    ]
    for o in objs:
        o.angle = 60
    plan = plan_stitches(Design(50, 33, layers, objs), PARAMS)
    assert dominant_angle(plan, box(0, 0, 20, 20)) == 60
    assert dominant_angle(plan, box(30, 0, 50, 20)) == 60
    assert near(dominant_angle(plan, box(0, 29, 50, 34), kinds=("satin",), min_len=1.0), 60)


def test_colour_direction_still_only_steers_fills():
    """Unchanged behaviour: a colour's direction never forces satin into split stitches."""
    layers = [ColorLayer(0, (0, 0, 0), "Black", angle=0)]
    bar = DesignObject(0, box(0, 0, 50, 3))
    plan = plan_stitches(Design(50, 3, layers, [bar]), PARAMS)
    measured = dominant_angle(plan, box(-1, -1, 51, 4), kinds=("satin",), min_len=1.0)
    assert near(measured, 90) and not near(measured, 0, 45)


def test_shape_style_and_dont_stitch():
    d = two_squares()
    d.objects[0].stitch_type = "running"
    plan = plan_stitches(d, PARAMS)
    kinds_in_a = {k for c, x, y, k in plan.ops if c == "stitch" and box(-1, -1, 21, 21).contains(Point(x, y))}
    assert "fill" not in kinds_in_a and "running" in kinds_in_a
    d.objects[0].enabled = False
    plan = plan_stitches(d, PARAMS)
    assert not any(c == "stitch" and x < 25 for c, x, y, k in plan.ops if k in ("fill", "running"))


def test_describe_matches_planner():
    d = two_squares(colour_angle=90)
    d.objects[0].angle = 15
    ring = Point(80, 10).buffer(9).difference(Point(80, 10).buffer(7))  # satin follows the band
    d.objects.append(DesignObject(0, ring, id="ring"))
    d.objects.append(DesignObject(0, LineString([(0, 40), (50, 40)]), kind="line", stroke_width_mm=2, id="ln"))
    info = {o["id"]: o for o in describe_objects(d, PARAMS)}
    assert info["a"]["effective_angle"] == 15 and info["a"]["direction_applies"]
    assert info["b"]["effective_angle"] == 90 and info["b"]["method"] == "fill"
    assert info["ring"]["method"] == "satin_band" and not info["ring"]["direction_applies"]
    assert info["ln"]["method"] == "satin_line" and not info["ln"]["direction_applies"]
    plan = plan_stitches(d, PARAMS)
    assert dominant_angle(plan, box(0, 0, 20, 20)) == 15


# ---------------------------------------------------------------- identity through normalisation

def test_untouched_shapes_keep_id_and_settings():
    d = two_squares()
    d.objects[0].angle = 10
    d.normalize()
    assert [o.id for o in d.objects] == ["a", "b"] and d.objects[0].angle == 10


def test_fused_shapes_carry_settings_or_largest_wins():
    layers = [ColorLayer(0, (0, 0, 0), "Black")]
    big = DesignObject(0, box(0, 0, 30, 20), id="big", angle=20)
    small = DesignObject(0, box(30.08, 0, 40, 20), id="small", angle=20)
    d = Design(40, 20, layers, [big, small]).normalize()
    assert len(d.objects) == 1 and d.objects[0].id == "big" and d.objects[0].angle == 20
    assert not d.warnings

    small = DesignObject(0, box(30.08, 0, 40, 20), id="small", angle=80)
    big = DesignObject(0, box(0, 0, 30, 20), id="big", angle=20)
    d = Design(40, 20, layers, [big, small]).normalize()
    assert d.objects[0].angle == 20 and any("largest" in w for w in d.warnings)


# ---------------------------------------------------------------- API

def _badge_png() -> bytes:
    img = np.full((500, 700, 3), 255, np.uint8)
    cv2.rectangle(img, (60, 60), (300, 400), (40, 40, 200), -1)
    cv2.rectangle(img, (380, 60), (640, 400), (40, 40, 200), -1)
    cv2.circle(img, (520, 230), 60, (20, 140, 20), -1)
    return cv2.imencode(".png", img)[1].tobytes()


def _project(width=80):
    pid = client.post("/api/projects", files={"file": ("badge.png", _badge_png())}).json()["id"]
    res = client.post(f"/api/projects/{pid}/extract", json={"options": {"width_mm": width}})
    assert res.status_code == 200, res.text
    return pid, res.json()


def test_payload_lists_shapes_and_tags_svg(tmp_path):
    pid, res = _project()
    objs = res["objects"]
    assert objs and all({"id", "layer", "method", "effective_angle", "direction_applies"} <= set(o) for o in objs)
    for o in objs:
        assert f'data-oid="{o["id"]}"' in res["svg"]
    assert res["can_undo"] is False
    clean = client.get(f"/api/projects/{pid}/export/svg").text
    assert "data-oid" not in clean  # downloads stay clean


def test_update_selected_colour_all_inherit_and_validation():
    pid, res = _project()
    red = [o for o in res["objects"] if o["kind"] == "area"]
    one = red[0]["id"]
    r = client.put(f"/api/projects/{pid}/objects", json={"ids": [one], "angle": 225, "stitch_type": "fill"}).json()
    o = next(x for x in r["objects"] if x["id"] == one)
    assert o["angle"] == 45 and o["stitch_type"] == "fill" and o["effective_angle"] == 45
    assert r["can_undo"] is True
    assert sum(l["customised"] for l in r["design"]["layers"]) == 1

    r = client.put(f"/api/projects/{pid}/objects", json={"ids": [one], "scope": "colour", "angle": 90}).json()
    layer = next(x for x in r["objects"] if x["id"] == one)["layer"]
    assert all(x["angle"] == 90 for x in r["objects"] if x["layer"] == layer)

    r = client.put(f"/api/projects/{pid}/objects", json={"scope": "all", "angle": 0}).json()
    assert all(x["angle"] == 0 for x in r["objects"] if x["kind"] == "area")

    r = client.put(f"/api/projects/{pid}/objects", json={"ids": [one], "angle": "inherit", "stitch_type": "inherit"}).json()
    o = next(x for x in r["objects"] if x["id"] == one)
    assert o["angle"] is None and o["stitch_type"] is None

    for bad in ({"ids": [one], "stitch_type": "zigzag"}, {"ids": [one], "angle": "north"}, {"ids": ["nope"], "angle": 10},
                {"ids": [one], "scope": "galaxy", "angle": 1}):
        assert client.put(f"/api/projects/{pid}/objects", json=bad).status_code == 400


def test_dont_stitch_shape_is_skipped_everywhere():
    pid, res = _project()
    target = max((o for o in res["objects"] if o["kind"] == "area"), key=lambda o: o["area_mm2"])
    client.put(f"/api/projects/{pid}/objects", json={"ids": [target["id"]], "enabled": False})
    stitched = client.post(f"/api/projects/{pid}/stitch", json={"params": {}}).json()
    assert target["id"] not in {s["id"] for s in stitched["shapes"]}
    assert all(s["d"].startswith("M") for s in stitched["shapes"])
    svg = client.get(f"/api/projects/{pid}/export/svg").text
    assert svg.count("<path") == len(res["objects"]) - 1


def test_reset_and_one_level_undo():
    pid, res = _project()
    ids = [o["id"] for o in res["objects"]]
    client.put(f"/api/projects/{pid}/objects", json={"ids": ids[:2], "angle": 10})
    r = client.post(f"/api/projects/{pid}/objects/reset", json={"ids": [ids[0]]}).json()
    assert {o["id"]: o["angle"] for o in r["objects"]}[ids[0]] is None
    r = client.post(f"/api/projects/{pid}/undo").json()  # back to before the reset
    assert {o["id"]: o["angle"] for o in r["objects"]}[ids[0]] == 10
    assert r["can_undo"] is False
    assert client.post(f"/api/projects/{pid}/undo").status_code == 400
    r = client.post(f"/api/projects/{pid}/objects/reset", json={}).json()
    assert all(o["angle"] is None for o in r["objects"])


def test_settings_survive_merge_and_reextract_at_new_width():
    pid, res = _project(80)
    big = max((o for o in res["objects"] if o["kind"] == "area"), key=lambda o: o["area_mm2"])
    client.put(f"/api/projects/{pid}/objects", json={"ids": [big["id"]], "angle": 120, "stitch_type": "fill"})
    r = client.post(f"/api/projects/{pid}/extract", json={"options": {"width_mm": 120}}).json()
    kept = [o for o in r["objects"] if o["angle"] == 120 and o["stitch_type"] == "fill"]
    assert len(kept) == 1
    assert any("kept for 1 of 1" in w for w in r["design"]["warnings"])
    r = client.post(f"/api/projects/{pid}/undo").json()  # a re-extract can be undone too
    assert any(o["id"] == big["id"] and o["angle"] == 120 for o in r["objects"])

    layers = r["design"]["layers"]
    if len(layers) > 1:
        other = next(l for l in layers if l["index"] != big["layer"])
        r = client.post(f"/api/projects/{pid}/layers/merge", json={"source": other["index"], "target": big["layer"]}).json()
        assert any(o["angle"] == 120 for o in r["objects"])


def test_legacy_design_without_ids_gets_stable_ids():
    pid, _ = _project()
    path = service.PROJECTS / pid / "design.json"
    import json

    raw = json.loads(path.read_text())
    for o in raw["objects"]:
        o.pop("id", None)
    path.write_text(json.dumps(raw))
    first = [o["id"] for o in client.get(f"/api/projects/{pid}/design").json()["objects"]]
    second = [o["id"] for o in client.get(f"/api/projects/{pid}/design").json()["objects"]]
    assert first == second and all(first)
