"""Named foundation (underlay) types, push compensation, and the per-shape
foundation setting."""

import numpy as np
import pytest
from shapely.geometry import Point, box

from embroidery_app.models import (
    FILL_UNDERLAYS, SATIN_UNDERLAYS, STITCH, ColorLayer, Design, DesignObject, StitchParams,
)
from embroidery_app.stitch import plan_stitches
from embroidery_app.stitch.planner import describe_objects

QUIET = dict(tie_stitches=False, optimize=False, vary_fill_angle=False)


def _design(*objects) -> Design:
    return Design(80, 40, [ColorLayer(0, (30, 30, 200), "blue")], list(objects))


def _runs(plan, kind):
    return [r.points for b in plan.blocks for r in b.runs if r.kind == kind]


def _count(plan, kind):
    return sum(len(p) for p in _runs(plan, kind))


SQUARE = box(5, 5, 35, 35)  # a fill
BAR = box(5, 10, 65, 15)  # a 5 mm satin column
THIN_BAR = box(5, 20, 65, 21.8)  # 1.8 mm: too narrow for a zigzag foundation


def _plan(geom, **params):
    return plan_stitches(_design(DesignObject(0, geom)), StitchParams(**QUIET, **params))


# ---------------------------------------------------------------- fills

def test_fill_foundations_go_from_none_to_double():
    counts = {s: _count(_plan(SQUARE, fill_underlay=s), "underlay") for s in FILL_UNDERLAYS}
    assert counts["none"] == 0
    assert 0 < counts["edge"] < counts["edge_tatami"] < counts["double_tatami"]
    # the top fill itself does not depend on the foundation
    fills = {s: _count(_plan(SQUARE, fill_underlay=s), "fill") for s in FILL_UNDERLAYS}
    assert len(set(fills.values())) == 1


def test_foundation_stays_inside_the_shape():
    for style in FILL_UNDERLAYS:
        pts = [p for run in _runs(_plan(SQUARE, fill_underlay=style), "underlay") for p in run]
        assert all(SQUARE.buffer(-0.3).contains(Point(p)) for p in pts), style


def test_underlay_switch_still_turns_every_foundation_off():
    assert _count(_plan(SQUARE, underlay=False, fill_underlay="double_tatami"), "underlay") == 0


# ---------------------------------------------------------------- satin

def test_satin_foundations():
    plans = {s: _plan(BAR, satin_underlay=s) for s in SATIN_UNDERLAYS}
    assert _count(plans["none"], "underlay") == 0
    centre = np.array([p for run in _runs(plans["center"], "underlay") for p in run])
    assert np.allclose(centre[:, 1], 12.5, atol=0.3)  # along the middle of the column
    zig = np.array([p for run in _runs(plans["zigzag"], "underlay") for p in run])
    assert zig[:, 1].min() >= 10.3 and zig[:, 1].max() <= 14.7  # across it, inside the edges
    assert np.ptp(zig[:, 1]) > 3.0
    assert _count(plans["edge_zigzag"], "underlay") > _count(plans["zigzag"], "underlay")
    assert _count(plans["double_zigzag"], "underlay") >= 1.8 * _count(plans["zigzag"], "underlay")


def test_double_zigzag_second_pass_falls_between_the_first():
    runs = _runs(_plan(BAR, satin_underlay="double_zigzag"), "underlay")
    pts = [p for run in runs for p in run]
    xs = sorted({round(x, 2) for x, y in pts if 20 < x < 50})
    gaps = np.diff(xs)
    assert gaps.max() < 2.0  # two interleaved passes: no 2.5 mm gaps left


def test_narrow_columns_get_a_centre_run_instead_of_a_zigzag():
    zig = _runs(_plan(THIN_BAR, satin_underlay="zigzag"), "underlay")
    centre = _runs(_plan(THIN_BAR, satin_underlay="center"), "underlay")
    assert zig == centre and zig


# ---------------------------------------------------------------- push compensation

def _extent(plan, kind, axis):
    vals = [p[axis] for run in _runs(plan, kind) for p in run]
    return max(vals) - min(vals)


def test_push_compensation_shortens_fills_across_their_rows():
    # fill rows run at 0 deg (horizontal) and are stacked vertically: the ends are top and bottom
    base = _plan(SQUARE, fill_angle=0.0)
    pushed = _plan(SQUARE, fill_angle=0.0, push_compensation_mm=0.3)
    # 0.3 mm off each end; rows sit on a 0.4 mm grid, so within one row spacing
    assert _extent(base, "fill", 1) - _extent(pushed, "fill", 1) == pytest.approx(0.6, abs=0.4)
    assert _extent(base, "fill", 0) == pytest.approx(_extent(pushed, "fill", 0), abs=0.05)


def test_push_compensation_shortens_satin_columns_at_their_ends():
    base = _plan(BAR)
    pushed = _plan(BAR, push_compensation_mm=0.3)
    assert _extent(base, "satin", 0) - _extent(pushed, "satin", 0) == pytest.approx(0.6, abs=0.4)  # one row spacing
    assert _extent(base, "satin", 1) == pytest.approx(_extent(pushed, "satin", 1), abs=0.05)


def test_push_compensation_is_clamped():
    assert StitchParams(push_compensation_mm=2.0).push_compensation_mm == 0.5
    assert StitchParams(push_compensation_mm=-1).push_compensation_mm == 0.0


# ---------------------------------------------------------------- per-shape setting

def test_a_shape_can_have_its_own_foundation():
    own = DesignObject(0, box(5, 5, 35, 35), underlay_style="double_tatami")
    plain = DesignObject(0, box(45, 5, 75, 35))
    plan = plan_stitches(_design(own, plain), StitchParams(**QUIET))
    by_shape = {}
    for b in plan.blocks:
        for r in b.runs:
            if r.kind == "underlay":
                x = r.points[0][0]
                by_shape.setdefault("own" if x < 40 else "plain", []).append(len(r.points))
    assert sum(by_shape["own"]) > 1.3 * sum(by_shape["plain"])


def test_describe_objects_reports_which_foundation_list_applies():
    d = _design(DesignObject(0, SQUARE), DesignObject(0, BAR, underlay_style="edge_zigzag"),
                DesignObject(0, Point(60, 30).buffer(5).difference(Point(60, 30).buffer(4))))
    info = describe_objects(d, StitchParams())
    assert (info[0]["underlay_applies"], info[0]["effective_underlay"]) == ("fill", "edge_tatami")
    assert (info[1]["underlay_applies"], info[1]["effective_underlay"]) == ("satin", "edge_zigzag")
    assert info[2]["underlay_applies"] is None  # ring: satin that follows the band keeps its own
    assert describe_objects(d, StitchParams(underlay=False))[0]["underlay_applies"] is None


def test_foundation_setting_round_trips_and_resets():
    o = DesignObject(0, BAR, underlay_style="center")
    back = DesignObject.from_dict(o.to_dict())
    assert back.underlay_style == "center" and back.customised
    assert DesignObject.from_dict({**o.to_dict(), "underlay_style": "bogus"}).underlay_style is None
    back.clear_settings()
    assert back.underlay_style is None and not back.customised


def test_invalid_design_wide_styles_fall_back_to_the_standard():
    p = StitchParams.from_dict({"fill_underlay": "bogus", "satin_underlay": "edge_tatami"})
    assert (p.fill_underlay, p.satin_underlay) == ("edge_tatami", "edge")
