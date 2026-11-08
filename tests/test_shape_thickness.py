"""Per-shape thickness (row / stitch gap), extra width and bold outlines."""

import math
from statistics import median

import cv2
import numpy as np
import pytest
from fastapi.testclient import TestClient
from shapely.geometry import LineString, Point, box

from embroidery_app.api import app
from embroidery_app.models import ColorLayer, Design, DesignObject, StitchParams
from embroidery_app.stitch import optimize as opt
from embroidery_app.stitch import plan_stitches
from embroidery_app.stitch.planner import describe_objects

client = TestClient(app)
FLAT = StitchParams(underlay=False, optimize=False, vary_fill_angle=False, fill_angle=0, tie_stitches=False)


def fill_row_gap(plan, region) -> float:
    """Median vertical distance between consecutive horizontal fill rows inside ``region``."""
    ys = sorted({round(y, 3) for c, x, y, k in plan.ops if c == "stitch" and k == "fill" and region.contains(Point(x, y))})
    return median(b - a for a, b in zip(ys, ys[1:]))


def satin_gap(plan, region) -> float:
    """Median advance between consecutive same-side satin penetrations along a horizontal bar."""
    pts = [(x, y) for c, x, y, k in plan.ops if c == "stitch" and k == "satin" and region.contains(Point(x, y))]
    top = sorted(x for x, y in pts if y < region.centroid.y)
    return median(b - a for a, b in zip(top, top[1:]))


def stitch_extent(plan, region) -> tuple:
    pts = [(x, y) for c, x, y, k in plan.ops if c == "stitch" and region.contains(Point(x, y))]
    xs, ys = [p[0] for p in pts], [p[1] for p in pts]
    return min(xs), min(ys), max(xs), max(ys)


def design(*objs) -> Design:
    return Design(100, 40, [ColorLayer(0, (0, 0, 0), "Black")], list(objs))


# ---------------------------------------------------------------- model

def test_new_settings_roundtrip_and_validate_on_load():
    o = DesignObject(0, box(0, 0, 1, 1), spacing_mm=0.33, pull_mm=0.4, bold=True)
    assert o.customised
    back = DesignObject.from_dict(o.to_dict())
    assert (back.spacing_mm, back.pull_mm, back.bold) == (0.33, 0.4, True)
    bad = DesignObject.from_dict({**o.to_dict(), "spacing_mm": 5, "pull_mm": -1, "bold": "yes"})
    assert (bad.spacing_mm, bad.pull_mm, bad.bold) == (None, None, None)
    plain = DesignObject(0, box(0, 0, 1, 1))
    assert not plain.customised and "spacing_mm" not in plain.to_dict()
    plain.apply_settings(o.settings())
    assert plain.settings() == o.settings()
    plain.clear_settings()
    assert not plain.customised


# ---------------------------------------------------------------- planner

def test_shape_thickness_overrides_design_fill_gap():
    normal = DesignObject(0, box(0, 0, 30, 30), id="n")
    dense = DesignObject(0, box(40, 0, 70, 30), id="d", spacing_mm=0.33)
    light = DesignObject(0, box(80, 0, 100, 30), id="l", spacing_mm=0.5)
    plan = plan_stitches(design(normal, dense, light), FLAT)
    assert fill_row_gap(plan, box(1, 1, 29, 29)) == pytest.approx(0.40, abs=0.01)
    assert fill_row_gap(plan, box(41, 1, 69, 29)) == pytest.approx(0.33, abs=0.01)
    assert fill_row_gap(plan, box(81, 1, 99, 29)) == pytest.approx(0.50, abs=0.01)


def test_shape_thickness_applies_to_satin():
    bars = [DesignObject(0, box(0, 0, 40, 3), stitch_type="satin", id="a"),
            DesignObject(0, box(0, 10, 40, 13), stitch_type="satin", id="b", spacing_mm=0.6)]
    plan = plan_stitches(design(*bars), FLAT)
    gap_a = satin_gap(plan, box(-1, -1, 41, 4))
    gap_b = satin_gap(plan, box(-1, 9, 41, 14))
    assert gap_b / gap_a == pytest.approx(0.6 / 0.4, rel=0.05)


def test_extra_width_grows_only_that_shape():
    a = DesignObject(0, box(0, 0, 30, 30), id="a")
    b = DesignObject(0, box(50, 0, 80, 30), id="b", pull_mm=0.6)
    plan = plan_stitches(design(a, b), FLAT)
    ax0, _, ax1, _ = stitch_extent(plan, box(-2, -2, 32, 32))
    bx0, _, bx1, _ = stitch_extent(plan, box(48, -2, 82, 32))
    assert (ax1 - ax0) == pytest.approx(30 + 2 * 0.2, abs=0.05)  # design default 0.2 mm
    assert (bx1 - bx0) == pytest.approx(30 + 2 * 0.6, abs=0.05)


def test_bold_outline_per_shape():
    thin = DesignObject(0, LineString([(0, 0), (50, 0)]), kind="line", stroke_width_mm=0.5, id="t")
    bold = DesignObject(0, LineString([(0, 20), (50, 20)]), kind="line", stroke_width_mm=0.5, id="b", bold=True)
    plan = plan_stitches(design(thin, bold), FLAT)
    n_thin = sum(1 for c, x, y, k in plan.ops if c == "stitch" and k == "running" and abs(y) < 1)
    n_bold = sum(1 for c, x, y, k in plan.ops if c == "stitch" and k == "running" and abs(y - 20) < 1)
    assert n_bold == pytest.approx(3 * n_thin, rel=0.1)
    # and "single" overrides a bold design setting
    thin.bold = False
    plan = plan_stitches(design(thin), StitchParams(running_repeats=3, optimize=False, tie_stitches=False))
    assert sum(1 for c, *_ , k in plan.ops if c == "stitch" and k == "running") == pytest.approx(n_thin, rel=0.1)


def test_no_override_means_identical_output():
    a = DesignObject(0, box(0, 0, 30, 30), id="a")
    before = plan_stitches(design(a), StitchParams()).ops
    a.spacing_mm = a.pull_mm = a.bold = None
    assert plan_stitches(design(a), StitchParams()).ops == before


def test_smart_reduction_keeps_dense_turnarounds():
    """Optimizer threshold follows the smallest gap in use (0.25 mm here, not the design's 0.40)."""
    dense = DesignObject(0, box(0, 0, 20, 20), spacing_mm=0.25)
    params = StitchParams(underlay=False, vary_fill_angle=False, fill_angle=0)
    plain = plan_stitches(design(dense), StitchParams(**{**params.to_dict(), "optimize": False}))
    optimised = plan_stitches(design(dense), params)
    assert optimised.optimization["threshold_mm"] <= 0.7 * 0.25 + 1e-9
    assert fill_row_gap(optimised, box(1, 1, 19, 19)) == pytest.approx(0.25, abs=0.01)
    assert opt.coverage_loss_pct(plain.ops, optimised.ops) == 0
    rows = lambda p: len({round(y, 3) for c, x, y, k in p.ops if c == "stitch" and k == "fill"})
    assert rows(optimised) == rows(plain)


def test_describe_reports_effective_values_and_applicability():
    fill = DesignObject(0, box(0, 0, 30, 30), id="f", spacing_mm=0.33)
    outline = DesignObject(0, box(40, 0, 70, 30), id="o", stitch_type="running", bold=True)
    info = {o["id"]: o for o in describe_objects(design(fill, outline), StitchParams())}
    assert info["f"]["effective_spacing_mm"] == 0.33 and info["f"]["thickness_applies"] and not info["f"]["bold_applies"]
    assert info["f"]["effective_pull_mm"] == 0.2
    assert info["o"]["effective_bold"] is True and info["o"]["bold_applies"] and not info["o"]["thickness_applies"]
    assert info["o"]["effective_spacing_mm"] is None


def test_merge_conflict_on_thickness_keeps_largest():
    big = DesignObject(0, box(0, 0, 30, 20), id="big", spacing_mm=0.5)
    small = DesignObject(0, box(30.08, 0, 40, 20), id="small", spacing_mm=0.33)
    d = design(big, small).normalize()
    assert len(d.objects) == 1 and d.objects[0].spacing_mm == 0.5
    assert any("largest" in w for w in d.warnings)


# ---------------------------------------------------------------- API

def _project():
    img = np.full((500, 700, 3), 255, np.uint8)
    cv2.rectangle(img, (60, 60), (300, 400), (40, 40, 200), -1)
    cv2.circle(img, (520, 230), 120, (20, 140, 20), -1)
    pid = client.post("/api/projects", files={"file": ("b.png", cv2.imencode(".png", img)[1].tobytes())}).json()["id"]
    res = client.post(f"/api/projects/{pid}/extract", json={"options": {"width_mm": 80}}).json()
    return pid, res


def test_api_thickness_settings_validation_reset_undo_reextract():
    pid, res = _project()
    big = max(res["objects"], key=lambda o: o["area_mm2"])
    base = client.post(f"/api/projects/{pid}/stitch", json={"params": {}}).json()["stats"]["stitches"]

    r = client.put(f"/api/projects/{pid}/objects", json={"ids": [big["id"]], "spacing_mm": 0.33, "pull_mm": 0.4}).json()
    o = next(x for x in r["objects"] if x["id"] == big["id"])
    assert (o["spacing_mm"], o["pull_mm"], o["effective_spacing_mm"]) == (0.33, 0.4, 0.33)
    dense = client.post(f"/api/projects/{pid}/stitch", json={"params": {}}).json()["stats"]["stitches"]
    assert dense > base  # denser = more stitches

    for bad in ({"spacing_mm": 0.1}, {"spacing_mm": 2}, {"pull_mm": 1.5}, {"spacing_mm": "thick"}, {"bold": "maybe"}):
        assert client.put(f"/api/projects/{pid}/objects", json={"ids": [big["id"]], **bad}).status_code == 400

    r = client.post(f"/api/projects/{pid}/extract", json={"options": {"width_mm": 100}}).json()  # carried over
    assert any(x["spacing_mm"] == 0.33 and x["pull_mm"] == 0.4 for x in r["objects"])

    r = client.put(f"/api/projects/{pid}/objects", json={"ids": [x["id"] for x in r["objects"]], "spacing_mm": "inherit"}).json()
    assert all(x["spacing_mm"] is None for x in r["objects"])
    r = client.post(f"/api/projects/{pid}/undo").json()
    assert any(x["spacing_mm"] == 0.33 for x in r["objects"])
    r = client.post(f"/api/projects/{pid}/objects/reset", json={}).json()
    assert all(x["spacing_mm"] is None and x["pull_mm"] is None and x["bold"] is None for x in r["objects"])


def test_dense_warning_only_for_tiny_or_thin_shapes():
    from embroidery_app.service import _dense_on_tiny_shapes

    big = DesignObject(0, box(0, 0, 20, 20), spacing_mm=0.25)
    assert not _dense_on_tiny_shapes(design(big))
    assert _dense_on_tiny_shapes(design(big, DesignObject(0, box(30, 0, 31.5, 1.5), spacing_mm=0.3)))  # 2.25 mm²
    assert _dense_on_tiny_shapes(design(DesignObject(0, box(0, 0, 40, 1.2), spacing_mm=0.3)))  # thin column
    assert not _dense_on_tiny_shapes(design(DesignObject(0, box(0, 0, 1, 1), spacing_mm=0.4)))  # not dense
    assert not _dense_on_tiny_shapes(design(DesignObject(0, box(0, 0, 1, 1), spacing_mm=0.25, enabled=False)))


def test_api_shows_dense_warning():
    pid, res = _project()
    small = min(res["objects"], key=lambda o: o["area_mm2"])
    client.put(f"/api/projects/{pid}/objects", json={"ids": [small["id"]], "spacing_mm": 0.25})
    warn = client.post(f"/api/projects/{pid}/stitch", json={"params": {}}).json()["warnings"]
    assert any("dense" in w for w in warn) == (small["area_mm2"] < 3)
