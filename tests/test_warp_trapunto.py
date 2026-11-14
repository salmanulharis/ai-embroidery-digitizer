"""3D warp for motif fills, and trapunto (fill travel only along the outline)."""

import math

import numpy as np
from shapely.geometry import LineString, Point, box

from embroidery_app.models import STITCH, ColorLayer, Design, DesignObject, StitchParams
from embroidery_app.stitch import generators as gen
from embroidery_app.stitch import plan_stitches
from embroidery_app.stitch.decorative import warp_3d


def test_3d_warp_bulges_from_the_middle_and_stays_in_the_shape():
    disc = Point(0, 0).buffer(20)
    rows = [[(x, y) for x in np.arange(-19, 19.5, 0.5)] for y in (-10.0, 0.0, 10.0)]
    out = warp_3d(rows, disc)
    pts = np.array([p for piece in out for p in piece])
    assert all(disc.contains(Point(p)) for p in pts)
    r = np.hypot(*pts.T)
    # fisheye: a point at r moves out to R sin(pi r / 2R) - further out in the middle
    assert np.max(np.abs(pts[np.abs(pts[:, 1]) < 1e-6][:, 0])) > 19.0 * 0.99
    mid = [p for p in out[1] if abs(p[0]) < 6]
    assert np.diff([p[0] for p in mid]).mean() > 0.5 * 1.2  # stretched in the middle


def test_motif_warp_changes_only_motif_fills():
    disc = Point(25, 25).buffer(20)
    d = Design(50, 50, [ColorLayer(0, (1, 2, 3), "c")], [DesignObject(0, disc, stitch_type="motif_fill")])
    flat = plan_stitches(d, StitchParams())
    warped = plan_stitches(d, StitchParams(motif_warp=True))
    assert flat.ops != warped.ops
    d2 = Design(50, 50, [ColorLayer(0, (1, 2, 3), "c")], [DesignObject(0, disc)])
    assert plan_stitches(d2, StitchParams()).ops == plan_stitches(d2, StitchParams(motif_warp=True)).ops


def _travel_off_edge(pieces, poly, spacing):
    """Length of connecting stitches (not along a row) that lie away from the outline."""
    total = 0.0
    edge = poly.boundary
    for piece in pieces:
        for a, b in zip(piece, piece[1:]):
            if abs(a[1] - b[1]) < 1e-6 or math.dist(a, b) <= spacing * 1.5:
                continue  # a row, or the short step to the next row
            mid = ((a[0] + b[0]) / 2, (a[1] + b[1]) / 2)
            if edge.distance(Point(mid)) > 0.8:
                total += math.dist(a, b)
    return total


def test_trapunto_moves_travel_to_the_outline():
    c_shape = box(0, 0, 30, 30).difference(box(8, 8, 30, 22))  # a C: rows break into groups
    plain = gen.tatami_fill(c_shape, 0.0, 1.2, 3.5, (0, 0))
    trap = gen.tatami_fill(c_shape, 0.0, 1.2, 3.5, (0, 0), edge_travel=True)
    assert _travel_off_edge(trap, c_shape, 1.2) <= _travel_off_edge(plain, c_shape, 1.2)
    assert _travel_off_edge(trap, c_shape, 1.2) < 1.0
    assert not StitchParams().trapunto


def test_fill_rows_never_cut_across_a_notch():
    """Rows of different length (a notch): the join between them used to cut straight
    across the notch, outside the shape."""
    shapes = [box(0, 0, 30, 30).difference(box(8, 8, 30, 22)), box(0, 0, 30, 8).union(box(0, 0, 8, 30)),
              box(0, 0, 30, 6).union(box(12, 0, 18, 30)), box(0, 0, 30, 30).difference(box(10, 10, 20, 40))]
    for c in shapes:
        for angle in (0, 30, 45, 90):
            outside = sum(LineString([a, b]).difference(c.buffer(0.05)).length
                          for piece in gen.tatami_fill(c, angle, 0.4, 3.5, (0, 0)) for a, b in zip(piece, piece[1:]))
            assert outside < 0.5, (c.bounds, angle, outside)  # only the thread's turn at a diagonal corner
