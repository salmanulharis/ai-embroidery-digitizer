"""Smart direction (opt-in): every section of the design gets the stitch direction that
suits its shape - satin that follows narrow shapes of any width, narrow arms sewn apart
from the body they grow out of, a fill direction per section - while the existing
modes and hand-made settings stay exactly as they were."""

import math

import numpy as np
import pytest
from fastapi.testclient import TestClient
from shapely import affinity
from shapely.geometry import LineString, Point, box
from shapely.ops import unary_union

from embroidery_app.api import app
from embroidery_app.models import STITCH, ColorLayer, Design, DesignObject, StitchParams
from embroidery_app.stitch import plan_stitches, smart
from embroidery_app.stitch.planner import classify, describe_objects

client = TestClient(app)
OFF = StitchParams()
SMART = StitchParams(smart_direction=True)
BLACK = [ColorLayer(0, (0, 0, 0), "Black")]


def design(*geoms, w=90, h=70, layers=None):
    return Design(w, h, list(layers or BLACK), [DesignObject(0, g, id=f"s{i}") for i, g in enumerate(geoms)])


def s_curve(width, amp=8.0, length=50.0):
    pts = [(10 + t, 30 + amp * math.sin(t / length * 2 * math.pi)) for t in np.linspace(0, length, 120)]
    return LineString(pts), LineString(pts).buffer(width / 2, cap_style="flat")


def stitches(plan, kinds):
    """Stitches of ``kinds`` as (start, end) pairs."""
    out, prev = [], None
    for c, x, y, k in plan.ops:
        if c == STITCH and prev is not None and k in kinds:
            out.append((prev, (x, y)))
        if c in (STITCH, "jump"):
            prev = (x, y)
    return out


def sewn_share(plan, geom, kinds=("satin", "fill"), thread=0.45) -> float:
    """Share of ``geom`` covered by the top stitching (thread drawn ``thread`` mm wide)."""
    lines = [LineString(seg) for seg in stitches(plan, kinds) if seg[0] != seg[1]]
    return unary_union(lines).buffer(thread).intersection(geom).area / geom.area


def methods(geom, params=SMART):
    return [it.method for it in classify(DesignObject(0, geom), "auto", params)]


# ---------------------------------------------------------------- nothing changes unless switched on

def test_smart_direction_is_off_by_default_and_round_trips():
    assert StitchParams().smart_direction is False
    assert StitchParams.from_dict({"smart_direction": True}).smart_direction is True
    assert StitchParams.from_dict(StitchParams().to_dict()).smart_direction is False  # projects saved before it existed
    assert StitchParams.from_dict({}).smart_direction is False


def test_without_smart_direction_shapes_are_classified_as_before():
    _, curve = s_curve(5.0)
    tee = unary_union([box(10, 10, 40, 13), box(23.5, 10, 26.5, 40)])
    tailed = unary_union([Point(25, 30).buffer(14), LineString([(25, 30), (55, 30), (70, 16)]).buffer(1.6)])
    for geom in (curve, tee, tailed):
        assert "column" not in methods(geom, OFF)
    assert methods(curve, OFF) == ["fill"] and methods(tailed, OFF) == ["fill"]


# ---------------------------------------------------------------- columns: satin that follows the shape

def test_wide_curved_stroke_gets_satin_that_crosses_it_at_right_angles():
    centre, curve = s_curve(5.0)
    assert methods(curve) == ["column"]
    plan = plan_stitches(design(curve), SMART)
    satin = [seg for seg in stitches(plan, ("satin",)) if math.dist(*seg) > 3.0]
    assert len(satin) > 100
    off = []
    for a, b in satin:
        mid = Point((a[0] + b[0]) / 2, (a[1] + b[1]) / 2)
        d = centre.project(mid)
        p, q = centre.interpolate(max(d - 0.5, 0)), centre.interpolate(min(d + 0.5, centre.length))
        tangent = math.degrees(math.atan2(q.y - p.y, q.x - p.x))
        stitch = math.degrees(math.atan2(b[1] - a[1], b[0] - a[0]))
        off.append(abs((stitch - tangent) % 180 - 90))
    # a zig-zag tilts each crossing a little; none may run along the stroke
    assert np.percentile(off, 95) < 15, np.percentile(off, 95)
    assert sewn_share(plan, curve) > 0.985
    assert max(math.dist(*seg) for seg in stitches(plan, ("satin", "underlay", "travel", "tie"))) <= SMART.max_stitch_mm + 1e-6
    assert plan.risks == []


def test_column_ends_are_square_and_stay_inside_the_outline():
    bar = affinity.rotate(box(10, 20, 50, 24), 33)
    assert methods(bar) == ["column"]
    plan = plan_stitches(design(bar), SMART)
    grown = bar.buffer(SMART.pull_compensation_mm + 0.05)
    assert all(grown.contains(Point(p)) for seg in stitches(plan, ("satin",)) for p in seg)
    for corner in bar.exterior.coords[:4]:  # stitched right into every corner
        assert min(Point(corner).distance(Point(p)) for seg in stitches(plan, ("satin",)) for p in seg) < 0.7


def test_exact_diagonal_stroke_of_changing_width_keeps_its_full_length():
    # Zhang-Suen thinning eats such a stroke away from its end; the column engine must not
    stroke = unary_union([LineString([(10, 10), (40, 40)]).buffer(1.6), Point(10, 10).buffer(2.6)])
    shape = smart.analyse_columns(stroke)
    assert shape.ok
    assert max(c.length for c in shape.columns) > 0.9 * LineString([(10, 10), (40, 40)]).length


def test_strokes_meeting_at_a_junction_do_not_pile_up():
    tee = unary_union([box(10, 10, 40, 13), box(23.5, 10, 26.5, 40)])  # T: bar + stem, 3 mm strokes
    shape = smart.analyse_columns(tee)
    assert shape.ok and len(shape.columns) == 2
    bar, stem = sorted(shape.columns, key=lambda c: abs(c.pts[0][1] - c.pts[-1][1]))
    assert bar.length == pytest.approx(30, abs=1.0)  # the bar runs through the junction in one piece
    assert stem.length == pytest.approx(27, abs=1.2)  # the stem stops at its edge
    overlap = bar.area().intersection(stem.area()).area
    assert overlap < 0.15 * 9.0  # of the 3 x 3 mm junction
    links = [l for l in shape.links if any(e is not None for e in l)]
    assert len(links) == 1  # the stem ends against the bar
    plan = plan_stitches(design(tee), SMART)
    assert sewn_share(plan, tee) > 0.98
    # the stem is sewn first and the needle reaches the bar under its own satin: one piece, no cut inside
    assert plan.stats()["trims"] == 1 and plan.stats()["jumps"] == 1


def test_corner_is_drawn_by_one_stroke_running_on_and_the_other_ending_against_it():
    ell = unary_union([box(10, 10, 13, 40), box(10, 37, 35, 40)])  # L, 3 mm strokes
    shape = smart.analyse_columns(ell)
    assert shape.ok
    plan = plan_stitches(design(ell), SMART)
    assert sewn_share(plan, ell) > 0.98
    assert min(Point(10, 40).distance(Point(p)) for seg in stitches(plan, ("satin",)) for p in seg) < 0.8  # outer corner


def test_inside_of_a_tight_bend_gets_shortened_stitches():
    def lengths(radius):
        arc = LineString([(30 + radius * math.cos(a), 30 + radius * math.sin(a)) for a in np.linspace(0, math.pi, 60)])
        shape = smart.analyse_columns(arc.buffer(2.0, cap_style="flat"))
        assert shape.ok and len(shape.columns) == 1
        pts = smart.sew_column(shape.columns[0], 0.4, 0.0, 7.0, 0.8)
        return np.array([math.dist(a, b) for a, b in zip(pts, pts[1:])])

    tight, gentle = lengths(3.0), lengths(25.0)
    assert (gentle > 3.6).mean() > 0.95  # full-width stitches all along a gentle bend
    assert 0.2 < (tight < 3.4).mean() < 0.8  # every second (or two of three) stitch stops short


def test_column_foundation_grows_with_the_width():
    def underlay(width):
        shape = smart.analyse_columns(box(10, 10, 50, 10 + width))
        return smart.column_underlay(shape.columns[0], 7.0)

    assert underlay(1.2) == ([], True)  # too narrow to need one
    centre, at_start = underlay(2.0)
    assert not at_start and len({round(y, 1) for _, y in centre}) == 1  # one run down the middle
    walk, at_start = underlay(3.2)
    assert at_start and len({round(y, 1) for _, y in walk}) == 2  # out along one edge, back along the other
    zig, at_start = underlay(5.0)
    assert not at_start and len(zig) > len(walk) + 30  # ... plus a zig-zag
    assert len({round(y, 1) for _, y in zig}) == 2


def test_narrow_columns_get_less_extra_width_so_small_lettering_stays_open():
    plan = plan_stitches(design(box(10, 10, 40, 11.2), box(10, 20, 40, 23)), SMART)

    def width(region):  # the bars lie flat: a stitch's height is the column width
        return np.median([abs(a[1] - b[1]) for a, b in stitches(plan, ("satin",)) if region.contains(Point(a))])

    assert width(box(9, 9, 41, 12.5)) < 1.2 + 2 * 0.16  # 1.2 mm column: about 0.14 mm per side
    assert width(box(9, 19, 41, 24)) > 3.0 + 2 * 0.18  # 3 mm column: the full 0.2 mm


# ---------------------------------------------------------------- sections: narrow arms of a wide shape

def test_narrow_arm_of_a_wide_shape_is_sewn_as_satin_overlapping_the_body():
    tail = LineString([(25, 30), (55, 30), (70, 16)]).buffer(1.6)
    shape = unary_union([Point(25, 30).buffer(14), tail])
    items = classify(DesignObject(0, shape), "auto", SMART)
    assert [it.method for it in items] == ["fill", "column"]
    body, arm = (it.geom for it in items)
    assert unary_union([body, arm]).symmetric_difference(shape).area < 0.01  # nothing lost, nothing added
    assert body.intersection(arm).area > 0.5  # they overlap at the seam: no gap between them
    plan = plan_stitches(design(shape), SMART)
    assert sewn_share(plan, shape) > 0.985
    info = describe_objects(design(shape), SMART)[0]
    assert info["method"] == "mixed" and info["direction_applies"] and info["thickness_applies"]


def test_shapes_without_a_real_arm_stay_in_one_piece():
    assert smart.narrow_arms(box(0, 0, 40, 30), 7.0) == []  # corners are not arms
    assert smart.narrow_arms(Point(20, 20).buffer(15), 7.0) == []
    leaf = Point(0, 0).buffer(12).intersection(Point(14, 0).buffer(12))  # pointed tips are not arms
    assert smart.narrow_arms(leaf, 7.0) == []
    stub = unary_union([box(0, 0, 30, 30), box(30, 12, 34, 18)])  # too short to be worth a seam
    assert smart.narrow_arms(stub, 7.0) == []
    thick = unary_union([box(0, 0, 30, 30), box(30, 12, 60, 18.5)])  # nearly as wide as the satin limit
    assert smart.narrow_arms(thick, 7.0) == []
    for geom in (box(0, 0, 40, 30), leaf, stub, thick):
        assert methods(geom) == ["fill"]


# ---------------------------------------------------------------- bodies: a fill direction per section

def fill_angles(d, params):
    return {o["id"]: o["effective_angle"] for o in describe_objects(d, params)}


def test_fill_direction_follows_each_section_instead_of_the_colour():
    upright, lying = box(5, 5, 17, 60), box(25, 5, 85, 17)  # same colour, at right angles to each other
    d = design(upright, lying)
    assert set(fill_angles(d, OFF).values()) == {45.0}
    a, b = fill_angles(d, SMART).values()
    assert _gap(a, b) > 45  # each is crossed the short way
    assert _gap(a, 90) > 45 and _gap(b, 0) > 45  # ... never along its long edges
    for angle, edge in ((a, 90), (a, 0), (b, 90), (b, 0)):
        assert _gap(angle, edge) >= smart.GRAZE_DEG  # no rows running along an edge


def _gap(a, b):
    d = abs(a - b) % 180
    return min(d, 180 - d)


def test_shapes_that_sew_well_in_any_direction_keep_the_colours_direction():
    d = design(Point(30, 30).buffer(15))
    assert fill_angles(d, SMART) == fill_angles(d, OFF) == {"s0": 45.0}
    assert fill_angles(d, StitchParams(smart_direction=True, fill_angle=20, vary_fill_angle=False)) == {"s0": 20.0}
    assert smart.fill_angle(box(0, 0, 30, 30), 45.0) == 45.0  # a square at its diagonal already


def test_hand_made_settings_always_win_over_smart_direction():
    upright = box(5, 5, 17, 60)
    _, curve = s_curve(4.0)
    # a direction set for the shape: that direction, and no curve-following satin
    d = design(upright, curve)
    d.objects[0].angle = 10.0
    d.objects[1].angle = 10.0
    info = {o["id"]: o for o in describe_objects(d, SMART)}
    assert info["s0"]["effective_angle"] == 10.0 and info["s1"]["method"] != "column"
    assert describe_objects(d, SMART) == describe_objects(d, OFF)
    # a direction set for the colour steers its fills
    d = design(upright, layers=[ColorLayer(0, (0, 0, 0), "Black", angle=75.0)])
    assert fill_angles(d, SMART) == {"s0": 75.0}
    # a stitch style set for the shape or the colour
    d = design(curve)
    d.objects[0].stitch_type = "fill"
    assert describe_objects(d, SMART)[0]["method"] == "fill"
    d = design(curve, layers=[ColorLayer(0, (0, 0, 0), "Black", stitch_type="running")])
    assert describe_objects(d, SMART)[0]["method"] == "outline"


def test_straight_satin_direction_is_refined_to_the_degree():
    bar = affinity.rotate(box(10, 10, 40, 14), 37, origin=(25, 12))  # satin chosen for the colour: one direction
    satin = [ColorLayer(0, (0, 0, 0), "Black", stitch_type="satin")]
    off = describe_objects(design(bar, layers=satin), OFF)[0]
    on = describe_objects(design(bar, layers=satin), SMART)[0]
    assert off["method"] == on["method"] == "satin"
    assert off["effective_angle"] % 15 == 0  # the coarse search
    assert _gap(on["effective_angle"], 127) <= 2 < _gap(off["effective_angle"], 127)  # straight across the bar


# ---------------------------------------------------------------- whole designs

def test_smart_plan_is_deterministic_and_machine_safe_for_a_mixed_design(logo_rgba):
    from embroidery_app.ingest.raster import RasterOptions, raster_to_design

    d, _ = raster_to_design(logo_rgba, RasterOptions(width_mm=90))
    plan = plan_stitches(d, SMART)
    assert plan.ops == plan_stitches(d, SMART).ops
    lengths = [math.dist(a, b) for a, b in stitches(plan, ("satin", "fill", "underlay", "travel", "tie", "running"))]
    assert max(lengths) <= SMART.max_stitch_mm + 1e-6
    assert all(math.isfinite(v) for op in plan.ops for v in op[1:3])
    assert not [r for r in plan.risks if r["type"] in ("stack", "dense")]
    used = {o["method"] for o in describe_objects(d.normalized(), SMART)}
    assert "column" in used  # the lettering follows its strokes ...
    assert {o["method"] for o in describe_objects(d.normalized(), OFF)} == {"fill", "satin"}  # ... and did not before
    # no more thread cuts than a couple above the classic plan
    assert plan.stats()["trims"] <= plan_stitches(d, OFF).stats()["trims"] + 3


def test_smart_direction_through_the_api(logo_png):
    assert client.get("/api/config").json()["stitch_defaults"]["smart_direction"] is False
    pid = client.post("/api/projects", files={"file": ("logo.png", logo_png)}).json()["id"]
    assert client.post(f"/api/projects/{pid}/extract", json={"options": {"width_mm": 90}}).status_code == 200

    classic = client.post(f"/api/projects/{pid}/stitch", json={"params": {}}).json()
    assert "column" not in {o["method"] for o in classic["objects"]}
    r = client.post(f"/api/projects/{pid}/stitch", json={"params": {"smart_direction": True}})
    assert r.status_code == 200, r.text
    smart_preview = r.json()
    # the preview tells the editor how every shape was really sewn
    assert "column" in {o["method"] for o in smart_preview["objects"]}
    assert {o["id"] for o in smart_preview["objects"]} == {o["id"] for o in classic["objects"]}
    assert smart_preview["stats"]["stitches"] > 500 and smart_preview["ops"] != classic["ops"]
    # the setting is remembered with the project, so the design view and exports use it too
    assert "column" in {o["method"] for o in client.get(f"/api/projects/{pid}/design").json()["objects"]}
    for fmt in ("dst", "pes", "png", "txt"):
        assert client.get(f"/api/projects/{pid}/export/{fmt}").status_code == 200
    # switching it off again gives the classic stitching back
    again = client.post(f"/api/projects/{pid}/stitch", json={"params": {}}).json()
    assert again["ops"] == classic["ops"]
