"""Advanced fills: offset, spiral, contour, radial, shaded, stipple, maze, cross stitch,
plus the tatami textures."""

import math

import cv2
import numpy as np
import pytest
from shapely.affinity import translate
from shapely.geometry import LineString, Point, box

from embroidery_app.models import FILL_STYLE_NAMES, STITCH, ColorLayer, Design, DesignObject, StitchParams
from embroidery_app.stitch import fills, plan_stitches
from embroidery_app.stitch.planner import classify, describe_objects

BLOB = translate(Point(0, 0).buffer(14).union(Point(14, -4).buffer(9)), 20, 20)
HOLED = box(5, 5, 40, 40).difference(Point(22, 22).buffer(6))
DENSE = ("offset_fill", "spiral_fill", "contour_fill", "radial_fill")


def _plan(geom, style, **params):
    d = Design(60, 50, [ColorLayer(0, (30, 90, 200), "blue")], [DesignObject(0, geom, stitch_type=style)])
    return plan_stitches(d, StitchParams(**params))


def _stitches(plan, kinds=None):
    return [(x, y) for c, x, y, k in plan.ops if c == STITCH and (kinds is None or k in kinds)]


def _coverage(pts_runs, geom, px=10):
    """Share of the shape covered by the sewn thread (0.4 mm wide), on a 0.1 mm raster."""
    minx, miny, maxx, maxy = geom.bounds
    w, h = int((maxx - minx + 2) * px), int((maxy - miny + 2) * px)
    thread = np.zeros((h, w), np.uint8)
    shape = np.zeros((h, w), np.uint8)
    to_px = lambda p: (int((p[0] - minx + 1) * px), int((p[1] - miny + 1) * px))  # noqa: E731
    for a, b in zip(pts_runs, pts_runs[1:]):
        cv2.line(thread, to_px(a), to_px(b), 1, max(1, int(0.4 * px)))
    for poly in getattr(geom, "geoms", [geom]):
        cv2.fillPoly(shape, [np.array([to_px(p) for p in poly.exterior.coords], np.int32)], 1)
        for hole in poly.interiors:
            cv2.fillPoly(shape, [np.array([to_px(p) for p in hole.coords], np.int32)], 0)
    return float((thread & shape).sum()) / max(int(shape.sum()), 1)


@pytest.mark.parametrize("style", FILL_STYLE_NAMES)
def test_every_fill_stays_in_its_shape_and_keeps_stitch_limits(style):
    for geom in (BLOB, HOLED):
        assert [it.method for it in classify(DesignObject(0, geom), style, StitchParams())] == [style]
        plan = _plan(geom, style)
        pts = _stitches(plan, ("fill", "running"))
        assert len(pts) > 100
        # cross stitch sews whole cells that are at least half inside: up to a cell beyond the edge
        margin = 1.5 * StitchParams().cross_cell_mm if style == "cross_stitch" else StitchParams().pull_compensation_mm + 0.35
        assert all(geom.buffer(margin).contains(Point(p)) for p in pts), style
        steps = [math.dist(a[1:3], b[1:3]) for a, b in zip(plan.ops, plan.ops[1:]) if a[0] == b[0] == STITCH]
        assert max(steps) <= StitchParams().max_stitch_mm + 1e-6
        assert not [r for r in plan.risks if r["type"] == "stack"]
        assert plan.ops == _plan(geom, style).ops  # same shape, same stitches (fixed seed)


@pytest.mark.parametrize("style", DENSE)
def test_dense_fills_cover_their_shape(style):
    plan = _plan(BLOB, style)
    assert _coverage(_stitches(plan, ("fill",)), BLOB) > 0.85


@pytest.mark.parametrize("style", ["stipple_fill", "maze_fill", "cross_stitch"])
def test_open_fills_are_light_but_reach_everywhere(style):
    plan = _plan(BLOB, style)
    cover = _coverage(_stitches(plan), BLOB)
    assert 0.15 < cover < 0.85
    pts = np.array(_stitches(plan))
    minx, miny, maxx, maxy = BLOB.bounds
    # every 6 mm square of the shape gets some stitching
    for x in np.arange(minx + 3, maxx - 3, 6):
        for y in np.arange(miny + 3, maxy - 3, 6):
            if BLOB.buffer(-3).contains(Point(x, y)):
                assert (np.hypot(pts[:, 0] - x, pts[:, 1] - y) < 4.5).any(), (x, y)
    assert not any(k in ("underlay",) for c, x, y, k in plan.ops if c == STITCH)


def test_offset_rings_are_one_gap_apart_and_joined():
    pieces = fills.offset_fill(Point(0, 0).buffer(10), 0.4, 2.5, (10, 0))
    assert len(pieces) == 1  # every ring joined to the next: no jumps
    radii = sorted({round(math.hypot(*p) / 0.4) for p in pieces[0]})
    assert len(radii) > 20


def test_spiral_is_one_continuous_line_that_moves_inwards():
    pieces = fills.spiral_fill(Point(0, 0).buffer(10), 0.4, 2.5, (10, 0))
    assert len(pieces) == 1
    r = np.hypot(*np.array(pieces[0]).T)
    window = 40
    trend = [r[i:i + window].mean() for i in range(0, len(r) - window, window)]
    assert all(b < a + 0.05 for a, b in zip(trend, trend[1:]))  # never heads back out


def test_contour_rows_follow_a_curved_column():
    arch = Point(0, 0).buffer(18).difference(Point(0, 0).buffer(12)).intersection(box(-20, -20, 20, 2))
    pieces = fills.contour_fill(arch, 0.4, 2.5, (-15, 2))
    r = np.hypot(*np.array(pieces[0]).T)
    levels = np.round((r - 12) / 0.4)
    assert len(set(levels)) >= 12  # rows at many distances from the arch's centre: they bend with it


def test_radial_rays_thin_out_towards_the_centre():
    disc = Point(0, 0).buffer(15)
    pieces = fills.radial_fill(disc, 0.4, 3.5, (15, 0))
    pts = np.array([p for piece in pieces for p in piece])
    r = np.hypot(*pts.T)
    inner = (r < 3).sum() / (math.pi * 9)
    outer = ((r > 12) & (r < 15)).sum() / (math.pi * (225 - 144))
    assert inner < 3 * outer  # no pile of needle drops in the middle
    assert r.min() >= fills.RADIAL_HOLE_MM - 0.05


def test_shaded_fill_is_dense_on_one_side_and_light_on_the_other():
    sq = box(0, 0, 30, 30)
    pts = _stitches(_plan(sq, "shaded_fill", fill_angle=0.0, vary_fill_angle=False), ("fill",))
    dense, light = box(0, 0, 30, 8), box(0, 22, 30, 30)
    assert _coverage(pts, dense) > 0.8
    assert _coverage(pts, light) < 0.6 * _coverage(pts, dense)


def test_shaded_fill_opens_up_across_the_shape():
    rows = fills.shaded_rows(0.4)(0.0, 30.0)
    gaps = np.diff(rows)
    assert gaps[0] == pytest.approx(0.4, abs=0.05) and gaps[-1] > 1.2
    assert np.all(np.diff(gaps) >= -1e-9)


def test_cross_stitch_sews_whole_crosses_on_a_grid():
    sq = box(0, 0, 12, 9)  # exactly 4 x 3 cells of 3 mm
    pieces = fills.cross_stitch(sq, 3.0, (0, 0))
    pts = np.array([p for piece in pieces for p in piece])
    slash = pts[np.isclose(pts[:, 0] % 3, 0, atol=1e-6)]
    back = pts[np.isclose((pts[:, 0] - fills.CROSS_SHIFT_MM) % 3, 0, atol=1e-6)]
    assert len(slash) and len(back) and len(slash) + len(back) == len(pts)
    segs = [math.dist(a, b) for piece in pieces for a, b in zip(piece, piece[1:])]
    assert max(segs) <= 3 * math.sqrt(2) * 1.6 + 1e-6


def test_tatami_textures():
    sq = box(0, 0, 30, 30)
    plans = {p: _plan(sq, "fill", fill_pattern=p, underlay=False) for p in ("standard", "brick", "diagonal", "random")}
    assert len({tuple(pl.ops) for pl in plans.values()}) == 4
    assert StitchParams(fill_pattern="nope").fill_pattern == "standard"


def test_a_line_with_a_fill_style_is_sewn_along_its_path():
    line = DesignObject(0, LineString([(0, 0), (30, 0)]), kind="line", stroke_width_mm=0.5)
    assert [it.method for it in classify(line, "radial_fill", StitchParams())] == ["running"]


def test_describe_objects_for_fills():
    d = Design(60, 50, [ColorLayer(0, (1, 2, 3), "c")], [DesignObject(0, BLOB, stitch_type="spiral_fill", spacing_mm=0.5)])
    info = describe_objects(d, StitchParams())[0]
    assert info["method"] == "spiral_fill" and info["thickness_applies"] and info["effective_spacing_mm"] == 0.5
