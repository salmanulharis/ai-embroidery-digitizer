"""Built-in stitch check: fabric-aware rules, guide lines, applique stops, sewing order."""

from shapely.geometry import Point, box

from embroidery_app.ai.builtin import review_plan
from embroidery_app.models import ColorLayer, Design, DesignObject, StitchParams
from embroidery_app.stitch import plan_stitches


def _review(design, **params):
    p = StitchParams(**params)
    return review_plan(plan_stitches(design, p), design, p)


def _titles(r):
    return {i["title"] for i in r["issues"]}


def _design(*objs, h=40):
    return Design(60, h, [ColorLayer(0, (200, 20, 20), "red")], list(objs))


def test_pile_fabric_without_knockdown_gets_one_suggested():
    r = _review(_design(DesignObject(0, box(5, 5, 8, 30))), fabric="towel")
    assert "No knockdown on pile fabric" in _titles(r) and r["changes"]["knockdown"] is True
    assert "No knockdown on pile fabric" not in _titles(_review(_design(DesignObject(0, box(5, 5, 8, 30))), fabric="towel", knockdown=True))


def test_stretchy_fabric_with_low_compensation():
    r = _review(_design(DesignObject(0, box(5, 5, 40, 30))), fabric="knit", pull_compensation_mm=0.1)
    assert "Low shrink compensation for this fabric" in _titles(r) and r["changes"]["pull_compensation_mm"] == 0.35
    assert "Low shrink compensation for this fabric" not in _titles(_review(_design(DesignObject(0, box(5, 5, 40, 30))), fabric="knit", pull_compensation_mm=0.35))
    assert "Low shrink compensation for this fabric" not in _titles(_review(_design(DesignObject(0, box(5, 5, 40, 30))), pull_compensation_mm=0.1))


def test_caps_too_tall_and_wide_satin():
    tall = _design(DesignObject(0, box(5, 2, 40, 68)), h=70)
    r = _review(tall, fabric="cap", satin_max_width_mm=10.0)
    assert {"Design too tall for a cap", "Wide satin on a cap"} <= _titles(r)
    assert r["changes"]["satin_max_width_mm"] == 7.0


def test_guided_style_without_a_line_is_flagged():
    r = _review(_design(DesignObject(0, box(5, 5, 40, 30), stitch_type="florentine_fill")))
    assert "Guide line missing" in _titles(r)
    with_line = DesignObject(0, box(5, 5, 40, 30), stitch_type="florentine_fill", guide=((5, 15), (40, 20)))
    assert "Guide line missing" not in _titles(_review(_design(with_line)))


def test_applique_stops_are_explained():
    r = _review(_design(DesignObject(0, box(5, 5, 40, 30), stitch_type="applique")))
    assert "The machine stops for applique" in _titles(r)


def test_sewing_order_off_with_many_cuts():
    dots = [DesignObject(0, Point(5 + 5 * (i % 10), 5 + 5 * (i // 10)).buffer(1.0)) for i in range(40)]
    r = _review(_design(*dots, h=40), optimize_order=False)
    assert "Smart sewing order is off" in _titles(r) and r["changes"]["optimize_order"] is True
