"""Wilcom Auto Fabric circle tests: lettering / narrow / wide satin, and the
automatic satin foundation that follows them."""

from shapely.geometry import Point, box

from embroidery_app.models import STITCH, ColorLayer, Design, DesignObject, StitchParams
from embroidery_app.stitch import plan_stitches
from embroidery_app.stitch.planner import describe_objects
from embroidery_app.stitch.underlay import auto_satin_foundation, size_class, widest_circle


def test_circle_test_classes():
    assert abs(widest_circle(box(0, 0, 30, 3)) - 3.0) < 0.1
    assert size_class(box(0, 0, 30, 1.5)) == "lettering"
    assert size_class(box(0, 0, 30, 3)) == "narrow"
    assert size_class(box(0, 0, 30, 5)) == "wide"
    assert size_class(Point(0, 0).buffer(1.9)) == "narrow"  # a 3.8 mm dot


def test_auto_satin_foundation_follows_the_width():
    assert auto_satin_foundation(box(0, 0, 30, 2.5)) == "center"
    assert auto_satin_foundation(box(0, 0, 30, 3.5)) == "edge"
    assert auto_satin_foundation(box(0, 0, 30, 6)) == "edge_zigzag"


def _underlay_points(width):
    bar = box(5, 5, 60, 5 + width)
    plan = plan_stitches(Design(70, 20, [ColorLayer(0, (1, 2, 3), "c")], [DesignObject(0, bar, stitch_type="satin")]),
                         StitchParams(satin_underlay="auto", tie_stitches=False, optimize=False))
    return [(x, y) for c, x, y, k in plan.ops if c == STITCH and k == "underlay"]


def test_auto_foundation_in_a_plan():
    narrow = _underlay_points(2.5)
    assert narrow and all(abs(y - 6.25) < 0.3 for x, y in narrow)  # a centre run
    wide = _underlay_points(6.0)
    ys = [y for x, y in wide]
    assert max(ys) - min(ys) > 4.0  # edge run and zigzag reach across the column


def test_describe_reports_the_class():
    d = Design(70, 40, [ColorLayer(0, (1, 2, 3), "c")], [DesignObject(0, box(5, 5, 60, 6.5)), DesignObject(0, box(5, 10, 60, 35))])
    assert [o["size_class"] for o in describe_objects(d, StitchParams())] == ["lettering", "wide"]
    assert StitchParams().satin_underlay == "edge"  # the default is unchanged
