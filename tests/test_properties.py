"""Property tests (Hypothesis): every stitch style on random shapes - thin slivers,
shapes with holes, concave shapes, tiny and large - must not crash, must keep every
stitch within the maximum length, and must stay on its shape.

Runs are derandomized, so CI sees the same shapes every time; set
HYPOTHESIS_PROFILE=deep for a longer random search when changing a generator."""

import math
import os
import tempfile

import pystitch as pe
import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as hs
from shapely.geometry import MultiPoint, Point, box

from embroidery_app.export import export_embroidery
from embroidery_app.models import STITCH, STITCH_TYPES, ColorLayer, Design, DesignObject, StitchParams
from embroidery_app.stitch import plan_stitches

settings.register_profile("ci", max_examples=6, deadline=None, derandomize=True,
                          suppress_health_check=[HealthCheck.too_slow])
settings.register_profile("deep", max_examples=60, deadline=None, suppress_health_check=[HealthCheck.too_slow])
settings.load_profile(os.environ.get("HYPOTHESIS_PROFILE", "ci"))

# How far a style may reach beyond its shape (mm): borders and outlines sit on the
# edge, cross stitch sews whole grid cells, jagged edges wander out on purpose.
REACH = {"zigzag": 2.5, "estitch": 2.5, "cross_stitch": 4.5, "jagged_fill": 2.0, "motif_run": 3.5,
         "stemstitch": 1.0, "applique": 2.5, "hand_stitch": 0.6, "running": 0.6}
AREA_STYLES = [s for s in STITCH_TYPES if s not in ("lettering",)]


@hs.composite
def shapes(draw):
    """A random shape: a convex blob, a thin sliver, an L, or a blob with a hole."""
    kind = draw(hs.sampled_from(["blob", "sliver", "ell", "holed"]))
    size = draw(hs.floats(4.0, 60.0))
    if kind == "sliver":
        return box(0, 0, size, draw(hs.floats(0.6, 6.0)))
    if kind == "ell":
        w = draw(hs.floats(1.5, size / 2))
        return box(0, 0, size, w).union(box(0, 0, w, size))
    pts = draw(hs.lists(hs.tuples(hs.floats(0, size), hs.floats(0, size)), min_size=5, max_size=12))
    hull = MultiPoint(pts).convex_hull
    if hull.geom_type != "Polygon" or hull.area < 2.0:
        hull = box(0, 0, size, size / 2 + 1)
    if kind == "holed":
        c = hull.representative_point()
        r = draw(hs.floats(0.5, 0.4 * math.sqrt(hull.area)))
        holed = hull.difference(Point(c.x, c.y).buffer(r))
        if holed.geom_type == "Polygon" and holed.area > 2.0:
            return holed
    return hull


def _plan(geom, style, params=None):
    d = Design(70, 70, [ColorLayer(0, (40, 90, 160), "c")], [DesignObject(0, geom, stitch_type=style)])
    return plan_stitches(d, params or StitchParams())


@pytest.mark.parametrize("style", AREA_STYLES)
@given(geom=shapes())
def test_every_style_is_safe_on_any_shape(style, geom):
    plan = _plan(geom, style)
    steps = [math.dist(a[1:3], b[1:3]) for a, b in zip(plan.ops, plan.ops[1:]) if a[0] == b[0] == STITCH]
    if steps:
        assert max(steps) <= StitchParams().max_stitch_mm + 1e-6
    margin = REACH.get(style, StitchParams().pull_compensation_mm + 0.5)
    region = geom.buffer(margin)
    stray = [(x, y) for c, x, y, k in plan.ops if c == STITCH and k != "travel" and not region.contains(Point(x, y))]
    assert not stray, (style, stray[:3])


@given(geom=shapes(), style=hs.sampled_from(["fill", "satin", "radial_fill", "blend_fill", "applique", "motif_fill"]))
def test_machine_files_read_back(geom, style):
    plan = _plan(geom, style)
    if not plan.ops:
        return
    path = os.path.join(tempfile.mkdtemp(), "p.dst")
    with open(path, "wb") as f:
        f.write(export_embroidery(plan, "dst"))
    back = pe.read(path)
    assert back.count_stitch_commands(pe.STITCH) > 0


@given(geom=shapes(), spacing=hs.floats(0.25, 1.0), pull=hs.floats(0.0, 0.8), weight=hs.sampled_from([40, 60]))
def test_settings_extremes_stay_safe(geom, spacing, pull, weight):
    plan = _plan(geom, "auto", StitchParams(fill_spacing_mm=spacing, satin_spacing_mm=spacing, pull_compensation_mm=pull,
                                            thread_weight=weight))
    steps = [math.dist(a[1:3], b[1:3]) for a, b in zip(plan.ops, plan.ops[1:]) if a[0] == b[0] == STITCH]
    assert not steps or max(steps) <= 7.0 + 1e-6
    assert all(geom.buffer(pull + 1.0).contains(Point(x, y)) for c, x, y, k in plan.ops if c == STITCH and k != "travel")
