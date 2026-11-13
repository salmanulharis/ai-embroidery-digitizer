"""Decorative outlines and borders: double run, backstitch, stemstitch, sculpture run,
zigzag and E-stitch, along lines and around filled shapes."""

import math

import numpy as np
import pytest
from fastapi.testclient import TestClient
from shapely.geometry import LineString, Point, box

from embroidery_app.api import app
from embroidery_app.models import OUTLINE_STYLES, STITCH, STITCH_TYPES, ColorLayer, Design, DesignObject, StitchParams
from embroidery_app.stitch import outlines as ol
from embroidery_app.stitch import plan_stitches
from embroidery_app.stitch.planner import classify, describe_objects

QUIET = StitchParams(tie_stitches=False, optimize=False)
LINE = LineString([(5, 20), (25, 10), (55, 20)])
SQUARE = box(10, 10, 40, 40)


def _plan(obj, params=QUIET, layer_style="auto"):
    d = Design(70, 50, [ColorLayer(0, (200, 20, 20), "red", stitch_type=layer_style)], [obj])
    return plan_stitches(d, params)


def _stitches(plan):
    return [(x, y) for c, x, y, k in plan.ops if c == STITCH]


def _steps(pts):
    return [math.dist(a, b) for a, b in zip(pts, pts[1:])]


@pytest.mark.parametrize("style", OUTLINE_STYLES)
def test_every_style_sews_lines_and_shape_outlines_safely(style):
    assert style in STITCH_TYPES
    for obj, path in [(DesignObject(0, LINE, kind="line", stroke_width_mm=0.5, stitch_type=style), LINE),
                      (DesignObject(0, SQUARE, stitch_type=style), SQUARE.exterior)]:
        assert [it.method for it in classify(obj, style, QUIET)] == [style]
        plan = _plan(obj)
        pts = _stitches(plan)
        assert len(pts) > 20
        steps = [s for s in _steps(pts) if s > 1e-9]
        assert max(steps) <= QUIET.max_stitch_mm + 1e-6
        assert min(steps) >= 0.5 - 1e-6  # never under the minimum stitch
        reach = QUIET.border_width_mm + 0.2 if style in ("zigzag", "estitch") else 1.0
        assert max(path.distance(Point(p)) for p in pts) <= reach  # stays on its path
        assert not [r for r in plan.risks if r["type"] in ("short", "stack")]


def test_a_filled_shape_gets_only_its_edge_sewn():
    pts = np.array(_stitches(_plan(DesignObject(0, SQUARE, stitch_type="backstitch"))))
    inside = [p for p in pts if SQUARE.buffer(-3).contains(Point(p))]
    assert not inside  # no fill: the middle stays empty


def test_holes_get_their_own_outline():
    ring = box(10, 10, 40, 40).difference(box(20, 20, 30, 30))
    pts = _stitches(_plan(DesignObject(0, ring, stitch_type="double_run")))
    hole = box(20, 20, 30, 30).exterior
    assert sum(hole.distance(Point(p)) < 0.3 for p in pts) > 20


def test_double_run_comes_back_between_its_forward_stitches():
    path = ol._Path(list(LINE.coords), False)
    pts = ol.double_run(path, 2.5)
    assert pts[0] == pts[-1]  # ends where it started
    forward = len(ol._resampled(path, 2.5))
    corners = set(LINE.coords)
    back = [b for b in pts[forward:-1] if b not in corners]  # the way back passes through the corners
    assert back and all(min(math.dist(b, f) for f in pts[:forward]) > 0.5 for b in back)  # new holes


@pytest.mark.parametrize("style", ["double_run", "backstitch", "sculpture_run", "stemstitch"])
def test_corners_stay_sharp(style):
    pts = _stitches(_plan(DesignObject(0, SQUARE, stitch_type=style)))
    for corner in SQUARE.exterior.coords[:-1]:
        reach = 0.75 if style == "stemstitch" else 0.05  # stemstitch leans half its width off the line
        assert min(math.dist(corner, p) for p in pts) <= reach, corner


def test_backstitch_steps_two_forward_one_back():
    pts = ol.backstitch(ol._Path([(0, 0), (10, 0)], False), 2.0)
    xs = [round(x, 3) for x, y in pts]
    moves = np.sign(np.diff(xs))
    assert list(moves[:6]) == [1, -1, 1, -1, 1, -1]
    assert xs[-1] == 10 and max(xs) == 10


def test_stemstitch_leans_across_the_line():
    pts = np.array(ol.stemstitch(ol._Path([(0, 0), (20, 0)], False), 2.5))
    assert set(np.round(np.abs(pts[:, 1]), 3)) == {ol.STEM_WIDTH_MM / 2}  # both sides of the line
    assert np.all(np.diff(pts[::2, 0]) > 0)  # each stitch starts further on


def test_sculpture_run_sews_long_stitches_three_times():
    pts = ol.sculpture_run(ol._Path([(0, 0), (12, 0)], False))
    xs = [round(x, 3) for x, y in pts]
    assert xs[:6] == [0, 2.5, 0, 2.5, 4.0, 6.5]  # A sewn there-back-there, then B once


def test_estitch_spikes_point_into_the_shape():
    plan = _plan(DesignObject(0, SQUARE, stitch_type="estitch"))
    pts = _stitches(plan)
    off_edge = [p for p in pts if SQUARE.exterior.distance(Point(p)) > 1.0]
    assert len(off_edge) > 20 and all(SQUARE.contains(Point(p)) for p in off_edge)
    hole_ring = box(10, 10, 40, 40).difference(box(20, 20, 30, 30))
    pts = _stitches(_plan(DesignObject(0, hole_ring, stitch_type="estitch")))
    assert all(hole_ring.buffer(0.05).contains(Point(p)) for p in pts)  # hole spikes point away from the hole


def test_border_width_follows_the_setting_or_the_stroke():
    def width(obj, params):
        pts = np.array(_stitches(_plan(obj, params)))
        mid = pts[(pts[:, 0] > 20) & (pts[:, 0] < 30)]
        return np.ptp(mid[:, 1])
    flat = LineString([(5, 20), (55, 20)])
    assert width(DesignObject(0, flat, kind="line", stroke_width_mm=0.4, stitch_type="zigzag"), QUIET) == pytest.approx(2.0, abs=0.1)
    wide = StitchParams(tie_stitches=False, optimize=False, border_width_mm=3.5)
    assert width(DesignObject(0, flat, kind="line", stroke_width_mm=0.4, stitch_type="zigzag"), wide) == pytest.approx(3.5, abs=0.1)
    assert width(DesignObject(0, flat, kind="line", stroke_width_mm=1.6, stitch_type="zigzag"), wide) == pytest.approx(1.6, abs=0.1)


def test_a_colour_can_use_an_outline_style():
    info = describe_objects(Design(70, 50, [ColorLayer(0, (1, 2, 3), "c", stitch_type="stemstitch")],
                                   [DesignObject(0, SQUARE)]), QUIET)
    assert info[0]["method"] == "stemstitch"
    assert not info[0]["direction_applies"] and not info[0]["thickness_applies"]


def test_config_lists_the_stitch_styles():
    styles = TestClient(app).get("/api/config").json()["stitch_styles"]
    assert [s[0] for s in styles] == list(STITCH_TYPES)
    assert dict(styles)["estitch"].startswith("E-stitch")


@pytest.mark.parametrize("style, step", [("double_run", 2.5), ("estitch", 1.2), ("zigzag", 1.2)])
def test_curves_keep_the_set_stitch_length(style, step):
    # a circle's outline has a vertex every ~0.7 mm: that must not become the stitch length
    circle = Point(25, 25).buffer(10)
    pts = _stitches(_plan(DesignObject(0, circle, stitch_type=style)))
    if style == "zigzag":  # stitches cross the outline: measure along the rail on one side
        rail = [p for p in pts if Point(p).distance(Point(25, 25)) > 10.5]
    else:
        rail = [p for p in pts if abs(Point(p).distance(Point(25, 25)) - 10) < 0.05]
    steps = sorted(math.dist(a, b) for a, b in zip(rail, rail[1:]) if math.dist(a, b) > 1e-6)
    expected = step if style != "zigzag" else 2 * step  # zigzag returns to a side every second stitch
    assert steps[len(steps) // 2] == pytest.approx(expected, rel=0.15)
