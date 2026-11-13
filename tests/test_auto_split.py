"""Auto split satin: wide satin columns keep their satin, and long stitches are split
at points that shift from row to row so no seam line shows."""

import math

import numpy as np
import pytest
from shapely.geometry import box

from embroidery_app.models import STITCH, ColorLayer, Design, DesignObject, StitchParams
from embroidery_app.stitch import plan_stitches
from embroidery_app.stitch.generators import split_stitch
from embroidery_app.stitch.planner import classify


def test_short_stitches_are_not_split():
    assert split_stitch((0, 0), (6.9, 0), 7.0, 3) == [(6.9, 0)]


@pytest.mark.parametrize("length", [7.1, 9.0, 12.0, 20.0])
def test_split_pieces_stay_within_limits(length):
    for row in range(50):
        pts = [(0.0, 0.0), *split_stitch((0.0, 0.0), (length, 0.0), 7.0, row)]
        steps = [b[0] - a[0] for a, b in zip(pts, pts[1:])]
        assert pts[-1] == (length, 0.0)
        assert max(steps) <= 7.0 + 1e-9
        assert min(steps) >= 0.24 * 7.0 - 1e-9
    assert len(split_stitch((0.0, 0.0), (9.0, 0.0), 7.0, 0)) == 2  # as few pieces as possible


def test_split_points_move_from_row_to_row():
    firsts = [split_stitch((0.0, 0.0), (9.0, 0.0), 7.0, row)[0][0] for row in range(40)]
    gaps = [abs(a - b) for a, b in zip(firsts, firsts[1:])]
    assert min(gaps) > 0.3  # neighbouring rows never split at the same spot
    assert max(firsts) - min(firsts) > 2.5  # spread across the column, not one seam


def _bar_design(width: float) -> Design:
    return Design(60, 30, [ColorLayer(0, (200, 30, 30), "red")], [DesignObject(0, box(5, 5, 50, 5 + width))])


def test_a_9_mm_column_is_satin_by_default():
    bar = DesignObject(0, box(5, 5, 50, 14))
    assert [it.method for it in classify(bar, "auto", StitchParams())] == ["satin"]
    assert [it.method for it in classify(bar, "auto", StitchParams(satin_max_width_mm=7.0))] == ["fill"]


def test_wide_satin_is_split_without_a_seam():
    plan = plan_stitches(_bar_design(9.0), StitchParams(underlay=False, tie_stitches=False))
    satin = [(x, y) for c, x, y, k in plan.ops if c == STITCH and k == "satin"]
    steps = [math.dist(a, b) for a, b in zip(satin, satin[1:])]
    assert max(steps) <= 7.0 + 1e-6
    # split points: penetrations inside the column, away from both edges
    inner = np.array([y for x, y in satin if 10 < x < 45 and 6.0 < y < 13.0])
    assert len(inner) > 50
    # spread over the column, not stacked on one line (an even split would give one y)
    assert np.ptp(inner) > 2.5
    hist, _ = np.histogram(inner, bins=14, range=(6.0, 13.0))
    assert hist.max() < 0.25 * len(inner)
