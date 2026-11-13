"""Knockdown (laydown) under small details on pile fabric."""

from shapely.geometry import LineString, Point, box

from embroidery_app.fabrics import get_fabric
from embroidery_app.models import STITCH, ColorLayer, Design, DesignObject, StitchParams
from embroidery_app.stitch import plan_stitches
from embroidery_app.stitch.planner import KNOCKDOWN_NAME, KNOCKDOWN_OFFSET_MM

LETTER = box(10, 10, 13, 30)  # a 3 mm stroke
BIG = box(30, 5, 80, 40)  # a large fill


def _design():
    return Design(90, 45, [ColorLayer(0, (200, 20, 20), "red"), ColorLayer(1, (20, 150, 20), "green")],
                  [DesignObject(0, LETTER), DesignObject(1, BIG),
                   DesignObject(0, LineString([(5, 42), (25, 42)]), kind="line", stroke_width_mm=0.5)])


def _knock_points(plan):
    ops, out, block = plan.ops, [], 0
    for c, x, y, k in ops:
        if c == "color_change":
            block += 1
        elif c == STITCH and block == 0 and k == "fill":
            out.append((x, y))
    return out


def test_knockdown_goes_first_under_details_only():
    plan = plan_stitches(_design(), StitchParams(knockdown=True))
    assert plan.blocks[0].name == KNOCKDOWN_NAME
    pts = _knock_points(plan)
    assert pts
    near_details = LETTER.union(LineString([(5, 42), (25, 42)]).buffer(0.5)).buffer(KNOCKDOWN_OFFSET_MM + 0.5)
    assert all(near_details.contains(Point(p)) for p in pts)  # not under the large fill
    assert any(not LETTER.buffer(0.2).contains(Point(p)) and near_details.contains(Point(p)) for p in pts)  # reaches beyond


def test_knockdown_is_off_by_default_and_on_for_pile_fabrics():
    assert not StitchParams().knockdown
    assert all(b.name != KNOCKDOWN_NAME for b in plan_stitches(_design(), StitchParams()).blocks)
    assert get_fabric("towel").settings["knockdown"] and get_fabric("fleece").settings["knockdown"]
    assert not get_fabric("woven").settings["knockdown"]


def test_knockdown_is_open_and_two_layered():
    plan = plan_stitches(Design(40, 40, [ColorLayer(0, (1, 2, 3), "c")], [DesignObject(0, box(5, 5, 8, 35))]),
                         StitchParams(knockdown=True, optimize=False))
    knock = [r for r in plan.blocks[0].runs]
    assert len(knock) >= 2  # 45 and 135 degree layers
    pts = _knock_points(plan)
    letter = box(5, 5, 8, 35)
    knock_area = letter.buffer(KNOCKDOWN_OFFSET_MM).area
    detail = sum(1 for op in plan.ops if op[0] == STITCH) - len(pts)
    assert len(pts) / knock_area < 0.7 * detail / letter.area  # far fewer stitches per mm2: an open fill
