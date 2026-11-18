"""Automation, round three (docs/AUTOMATION_UX_PLAN.md, batches K-P): fine thread with its
closer satin, hoops by machine, the usual preset, where sewing starts (centre, cap), the fill
foundation by size and shorter outline stitches on tight curves. Every new stitch option is off
by default and leaves the stitches byte-identical (tests/test_golden.py)."""

import math

import pytest
from shapely.geometry import Point, box

from accounts import admin, new_user, real_login  # noqa: F401
from embroidery_app import api, prefs, subscriptions
from embroidery_app.ai import tasks
from embroidery_app.ai.builtin import FINE_SATIN_SPACING_MM, _thread_stitch, review_plan
from embroidery_app.fabrics import get_fabric
from embroidery_app.models import STITCH, ColorLayer, Design, DesignObject, StitchParams
from embroidery_app.stitch import generators as gen
from embroidery_app.stitch import plan_stitches
from embroidery_app.stitch.underlay import BY_SIZE_EDGE_ONLY_MM, fill_foundation


# --------------------------------------------------------------------------- K: fine thread

def test_fine_thread_comes_with_closer_satin():
    assert _thread_stitch(60) == {"thread_weight": 60, "satin_spacing_mm": FINE_SATIN_SPACING_MM}
    assert _thread_stitch(40) == {"thread_weight": 40}
    merged = tasks._merge_suggestion({"extract": {"num_colors": 2, "background": "auto", "min_feature_mm": 0.3, "denoise": 1},
                                      "stitch": {"thread_weight": 40}, "text": {"found": True}, "notes": []},
                                     {"has_small_text": True}, "AI", "m")
    assert merged["stitch"] == _thread_stitch(60)


def _satin_design():
    return Design(40, 40, [ColorLayer(0, (0, 0, 0), "Black")], [DesignObject(0, box(10, 5, 14, 35))])


def test_fine_thread_with_standard_spacing_is_a_check_with_a_fix():
    d = _satin_design()
    p = StitchParams(thread_weight=60, satin_spacing_mm=0.42)
    issue = next(i for i in review_plan(plan_stitches(d, p), d, p)["issues"] if i["code"] == "fine_spacing")
    assert issue["change"] == {"satin_spacing_mm": FINE_SATIN_SPACING_MM} and issue["severity"] == "medium"
    ok = StitchParams(thread_weight=60, satin_spacing_mm=0.35)
    assert not any(i["code"] == "fine_spacing" for i in review_plan(plan_stitches(d, ok), d, ok)["issues"])
    standard = StitchParams(satin_spacing_mm=0.42)  # 40 wt: nothing to say
    assert not any(i["code"] == "fine_spacing" for i in review_plan(plan_stitches(d, standard), d, standard)["issues"])


# --------------------------------------------------------------------------- L: hoops by machine

def test_hoop_list_keeps_its_order_and_every_machine_names_known_hoops():
    names = [h["name"] for h in prefs.HOOPS]
    assert names[:7] == ["100 x 100 mm", "150 x 150 mm", "200 x 200 mm", "300 x 300 mm", "360 x 200 mm",
                         "Cap 70 x 140 mm", "Left chest ~90 mm"]  # options are chosen by position: only appended
    assert "130 x 180 mm (5 x 7 in)" in names and api.HOOPS is prefs.HOOPS
    for key, m in prefs.MACHINES.items():
        assert m["hoops"] and set(m["hoops"]) <= set(names), key


def test_config_sends_each_machines_hoops():
    from fastapi.testclient import TestClient

    cfg = TestClient(api.app).get("/api/config").json()
    assert [h["name"] for h in cfg["hoops"]] == [h["name"] for h in prefs.HOOPS]
    brother = next(m for m in cfg["machines"] if m["key"] == "brother")
    assert "130 x 180 mm (5 x 7 in)" in brother["hoops"] and "Cap 70 x 140 mm" not in brother["hoops"]


# --------------------------------------------------------------------------- M: the usual preset

@pytest.mark.usefixtures("real_login")
def test_usual_preset_is_a_builtin_or_your_own():
    a = admin()
    _, c = new_user(a, plan_id=next(p["id"] for p in subscriptions.list_plans() if p["code"] == "pro"))
    _, other = new_user(a, plan_id=next(p["id"] for p in subscriptions.list_plans() if p["code"] == "pro"))
    assert c.put("/api/me/prefs", json={"preset": "b:small_text"}).json()["preset"] == "b:small_text"
    assert c.put("/api/me/prefs", json={"preset": "b:no_such"}).status_code == 400
    assert c.put("/api/me/prefs", json={"preset": "u:abc"}).status_code == 400
    mine = c.post("/api/me/presets", json={"name": "Polo", "sections": {"stitch": {"fill_spacing_mm": 0.42}}})
    assert mine.status_code in (200, 201), mine.text
    pid = mine.json()["id"]
    assert c.put("/api/me/prefs", json={"preset": f"u:{pid}"}).json()["preset"] == f"u:{pid}"
    assert other.put("/api/me/prefs", json={"preset": f"u:{pid}"}).status_code == 400  # never another account's
    c.delete(f"/api/me/presets/{pid}")
    assert "preset" not in c.get("/api/me/prefs").json()  # a deleted preset is forgotten
    assert c.put("/api/me/prefs", json={"preset": None}).status_code == 200


# --------------------------------------------------------------------------- N: where sewing starts

def _spread_design():
    layers = [ColorLayer(0, (200, 20, 20), "Red"), ColorLayer(1, (20, 20, 200), "Blue")]
    objs = [DesignObject(0, box(x, y, x + 6, y + 6)) for x in (2, 47, 92) for y in (2, 47, 92)]
    objs += [DesignObject(1, Point(x + 3, y + 3).buffer(2)) for x in (2, 47, 92) for y in (2, 47, 92)]
    return Design(100, 100, layers, objs)


def _stitches(plan):
    return [(x, y) for c, x, y, _ in plan.ops if c == STITCH]


def test_standard_order_is_unchanged_by_default():
    d = _spread_design()
    assert StitchParams().sew_order == "standard"
    assert plan_stitches(d, StitchParams()).ops == plan_stitches(d, StitchParams(sew_order="standard")).ops
    assert StitchParams.from_dict({"sew_order": "sideways"}).sew_order == "standard"


def test_centre_order_starts_and_ends_near_the_centre():
    d = _spread_design()
    centre = (50, 50)
    std, mid = _stitches(plan_stitches(d, StitchParams())), _stitches(plan_stitches(d, StitchParams(sew_order="centre")))
    assert math.dist(mid[0], centre) < 10 < math.dist(std[0], centre)
    assert math.dist(mid[-1], centre) < 10
    assert sorted(mid) == sorted(std) or len(mid) == pytest.approx(len(std), rel=0.05)  # the same shapes sewn


def test_cap_order_goes_from_the_bottom_centre_outwards():
    d = _spread_design()
    plan = plan_stitches(d, StitchParams(sew_order="cap"))
    first = _stitches(plan)[0]
    assert math.dist(first, (50, 100)) < 12  # the bottom-centre square first
    from embroidery_app.stitch.planner import _Item, cap_order
    items = [_Item(-1, box(x, y, x + 1, y + 1), "fill") for x, y in ((0, 0), (49, 98), (90, 98), (49, 50))]
    assert [it.geom.bounds[:2] for it in cap_order(items, Design(100, 100, [], []))] == [(49, 98), (90, 98), (49, 50), (0, 0)]  # by distance from the bottom centre


def test_cap_fabric_chooses_the_cap_order_and_the_others_leave_it():
    assert get_fabric("cap").settings["sew_order"] == "cap"
    assert all(get_fabric(f).settings["sew_order"] == "standard" for f in ("general", "woven", "knit", "towel"))


# --------------------------------------------------------------------------- O: foundation by size, curves

def test_fill_foundation_by_size():
    small, large = box(0, 0, 4, 20), box(0, 0, 40, 40)
    edge = fill_foundation("edge", small, 0, 2.5, (0, 0))
    assert fill_foundation("by_size", small, 0, 2.5, (0, 0)) == edge
    assert fill_foundation("by_size", large, 0, 2.5, (0, 0)) == fill_foundation("edge_tatami", large, 0, 2.5, (0, 0))
    assert BY_SIZE_EDGE_ONLY_MM == 6.0
    assert StitchParams(fill_underlay="by_size").fill_underlay == "by_size"
    # per shape: "by_size" sets the fill foundation only (satin's own "auto" is unchanged)
    from embroidery_app.stitch.planner import shape_params
    sp = shape_params(DesignObject(0, small, underlay_style="by_size"), StitchParams())
    assert sp.fill_underlay == "by_size" and sp.satin_underlay == "edge"


def test_outline_on_a_small_hole_follows_the_curve():
    hole = Point(10, 10).buffer(1.0, 64)  # a 2 mm round
    plain = gen.ring_from(hole, (11, 10), 2.5)[0]
    curved = gen.ring_adaptive(hole, (11, 10), 2.5)[0]
    assert len(plain) - 1 == 3  # the triangle
    assert len(curved) - 1 >= 5  # a pentagon at the 1 mm shortest stitch, not a triangle
    steps = [math.dist(a, b) for a, b in zip(curved, curved[1:])]
    assert min(steps) >= gen.CURVE_MIN_STEP_MM * 0.95 and curved[0] == pytest.approx(curved[-1], abs=1e-6)


def test_straight_outlines_keep_their_stitch_length():
    square = box(0, 0, 20, 20)
    pts = gen.ring_adaptive(square, (0, 0), 2.5)[0]
    steps = [math.dist(a, b) for a, b in zip(pts, pts[1:])]
    assert max(steps) <= 2.5 + 1e-6 and sum(s > 2.0 for s in steps) >= len(steps) * 0.7
    for a in pts:  # every needle point on the outline
        assert square.exterior.distance(Point(a)) < 1e-6


def test_running_curves_is_off_by_default_and_changes_only_outlines():
    d = Design(30, 30, [ColorLayer(0, (0, 0, 0), "Black")], [DesignObject(0, Point(15, 15).buffer(3), stitch_type="running")])
    assert not StitchParams().running_curves
    off, on = plan_stitches(d, StitchParams()), plan_stitches(d, StitchParams(running_curves=True))
    assert len(_stitches(on)) > len(_stitches(off))  # the 6 mm round gets more, shorter stitches
    filled = Design(30, 30, [ColorLayer(0, (0, 0, 0), "Black")], [DesignObject(0, box(2, 2, 28, 28))])
    assert plan_stitches(filled, StitchParams()).ops == plan_stitches(filled, StitchParams(running_curves=True)).ops


def test_new_options_are_preset_keys():
    from embroidery_app import presets

    sec, dropped = presets.clean({"fabric": {"sew_order": "cap", "fill_underlay": "by_size"}, "stitch": {"running_curves": True}})
    assert not dropped and sec["fabric"] == {"sew_order": "cap", "fill_underlay": "by_size"} and sec["stitch"] == {"running_curves": True}
