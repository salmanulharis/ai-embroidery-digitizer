"""Shapes the deep property search (HYPOTHESIS_PROFILE=deep) found stitches in the wrong place
for: small holes, a hole across a column's centre line, a notch. Kept as fixed cases."""

import math

import pytest
from shapely.geometry import LineString, Point, Polygon, box

from embroidery_app.models import STITCH, StitchParams
from embroidery_app.stitch import decorative, guided
from test_properties import REACH, _plan


def holed(outline, centre, r):
    return Polygon(outline, [Point(*centre).buffer(r).exterior.coords])


SMALL_HOLE = holed(box(0, 0, 4, 3).exterior.coords, (1.9, 1.5), 0.95)
BIG_HOLE = holed([(0, 8), (2, 14), (11, 1), (0, 0)], (4.288461538461538, 4.5), 3.0)
NOTCH = Polygon([(0, 26), (14, 3), (0, 0)]).difference(Point(6.5, 13.5).buffer(2.6))


def stray(geom, style):
    plan = _plan(geom, style)
    region = geom.buffer(REACH.get(style, StitchParams().pull_compensation_mm + 0.5))
    return [(x, y) for c, x, y, k in plan.ops if c == STITCH and k != "travel" and not region.contains(Point(x, y))]


@pytest.mark.parametrize("style, geom", [
    ("hand_stitch", SMALL_HOLE), ("double_run", SMALL_HOLE), ("running", SMALL_HOLE),
    ("flexi_split", BIG_HOLE), ("flexi_split", NOTCH), ("satin", BIG_HOLE),
])
def test_no_stitches_in_holes_or_notches(style, geom):
    assert geom.is_valid
    assert not stray(geom, style)


def test_hand_stitch_follows_tight_curves():
    ring = Point(0, 0).buffer(1.0)
    for piece in decorative.hand_stitch(ring, (1, 0), 2.5):
        for a, b in zip(piece, piece[1:]):
            mid = Point((a[0] + b[0]) / 2, (a[1] + b[1]) / 2)
            assert ring.exterior.distance(mid) <= decorative.HAND_FOLLOW_MM + 0.16  # + the wobble


def test_hand_stitch_unchanged_on_straight_lines():
    """Straight paths never shorten: same stitch lengths as before the curve rule."""
    line = LineString([(0, 0), (40, 0)])
    pieces = decorative.hand_stitch(line, (0, 0), 2.5)
    xs = [p[0] for p in pieces[0]]
    longs = [b - a for a, b in zip(xs[::2], xs[1::2])]
    assert all(2.5 * 0.8 - 0.31 <= d <= 2.5 * 1.3 + 0.31 for d in longs[:-1])


def test_rungs_never_cross_holes():
    centre = guided.auto_centre(BIG_HOLE)
    rungs = guided.column_rungs(BIG_HOLE, centre, 0.4, 0.2)
    assert rungs
    inside = BIG_HOLE.buffer(0.25)
    assert all(inside.contains(LineString([l, r])) for _, l, r in rungs)
    # a plain column keeps every rung across it (the two ends lie along the edge and never had one)
    bar = box(0, 0, 30, 4)
    c = LineString([(0, 2), (30, 2)])
    rungs = guided.column_rungs(bar, c, 0.5, 0.2)
    assert len(rungs) == math.ceil(30 / 0.5) - 1
    assert all(abs(l[1] - 4.2) < 1e-9 and abs(r[1] + 0.2) < 1e-9 for _, l, r in rungs)
