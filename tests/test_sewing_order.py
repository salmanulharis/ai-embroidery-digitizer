"""Smart sewing order: 2-opt over the pieces of one colour."""

import math
import random

import numpy as np
from shapely.geometry import Point

from embroidery_app.models import JUMP, STITCH, ColorLayer, Design, DesignObject, StitchParams
from embroidery_app.stitch import plan_stitches
from embroidery_app.stitch.planner import _Item, _two_opt, sewing_order


def _path_cost(order, D, start):
    return start[order[0]] + sum(D[a, b] for a, b in zip(order, order[1:]))


def test_two_opt_never_makes_the_path_longer():
    rng = np.random.default_rng(5)
    for _ in range(20):
        pts = rng.uniform(0, 100, (25, 2))
        D = np.hypot(*(pts[:, None, :] - pts[None, :, :]).transpose(2, 0, 1))
        start = np.hypot(*(pts - [0, 0]).T)
        greedy = list(range(25))
        better = _two_opt(list(greedy), D, start)
        assert sorted(better) == greedy  # same pieces, each once
        assert _path_cost(better, D, start) <= _path_cost(greedy, D, start) + 1e-9


def test_a_crossing_order_is_untangled():
    # four dots on a line: visiting 0, 2, 1, 3 crosses back and forth
    items = [_Item(i, Point(10 * i, 0).buffer(1), "fill") for i in range(4)]
    order = sewing_order([items[0], items[2], items[1], items[3]], (-5.0, 0.0))
    assert [it.oid for it in order] == [0, 1, 2, 3]


def _jump_length(plan):
    total, prev = 0.0, None
    for c, x, y, _ in plan.ops:
        if c == JUMP and prev:
            total += math.dist(prev, (x, y))
        if c in (STITCH, JUMP):
            prev = (x, y)
    return total


def test_scattered_shapes_need_less_jumping():
    random.seed(3)
    dots = [DesignObject(0, Point(random.uniform(5, 145), random.uniform(5, 95)).buffer(1.5)) for _ in range(60)]
    d = Design(150, 100, [ColorLayer(0, (0, 0, 0), "k")], dots)
    plain = plan_stitches(d, StitchParams(optimize_order=False))
    smart = plan_stitches(d, StitchParams())
    assert StitchParams().optimize_order
    assert _jump_length(smart) < 0.95 * _jump_length(plain)
    stitched = lambda p: sum(1 for op in p.ops if op[0] == STITCH)  # noqa: E731
    assert abs(stitched(smart) - stitched(plain)) < 0.02 * stitched(plain)  # same shapes, same stitching
