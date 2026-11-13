"""Golden-output regression tests.

Every stitch method the planner knows is exercised on fixed designs with several
parameter sets. The resulting machine sequence (rounded to 0.1 mm, the resolution of
DST) is fingerprinted and compared with ``tests/golden/plans.json``.

A refactor must leave these fingerprints unchanged. A deliberate change in stitching
updates them in the same commit:

    UPDATE_GOLDEN=1 python -m pytest tests/test_golden.py
"""

import hashlib
import json
import os
from pathlib import Path

import pytest
from shapely.geometry import LineString, Point, Polygon, box

from embroidery_app.ingest.raster import RasterOptions, raster_to_design
from embroidery_app.ingest.vector import VectorOptions, svg_to_design
from embroidery_app.models import COLOR_CHANGE, JUMP, STITCH, TRIM, ColorLayer, Design, DesignObject, StitchParams
from embroidery_app.stitch import plan_stitches

from conftest import SVG_SAMPLE, make_logo  # tests/ is on sys.path (pytest rootdir-relative import)

GOLDEN = Path(__file__).parent / "golden" / "plans.json"


def _shapes_design() -> Design:
    """One colour per stitch method, so every branch of the planner is sewn."""
    arc = Point(70, 80).buffer(16).difference(Point(70, 80).buffer(13)).intersection(box(50, 60, 90, 80))
    layers = [ColorLayer(i, (20 * i, 100, 200 - 20 * i), f"c{i}") for i in range(9)]
    objects = [
        DesignObject(0, box(5, 5, 45, 35).difference(box(15, 12, 30, 25))),  # fill with a hole
        DesignObject(1, box(50, 5, 53, 35)),  # straight narrow column: satin
        DesignObject(2, Point(75, 20).buffer(12).difference(Point(75, 20).buffer(9.5))),  # ring: satin band
        DesignObject(3, LineString([(5, 45), (45, 50), (60, 42)]), kind="line", stroke_width_mm=2.0),  # satin line
        DesignObject(3, LineString([(5, 55), (40, 58)]), kind="line", stroke_width_mm=0.5),  # running
        DesignObject(4, box(5, 62, 5.8, 80).union(box(5, 79.2, 18, 80))),  # thin L: stroke engine
        DesignObject(4, Point(25, 70).buffer(0.35)),  # dot
        DesignObject(5, arc),  # curved band: column with smart direction
        DesignObject(6, Point(100, 75).buffer(8), stitch_type="running", bold=True),  # bold outline
        DesignObject(7, box(95, 5, 103, 25), stitch_type="satin", angle=30.0, spacing_mm=0.5, pull_mm=0.3),
        DesignObject(8, box(60, 40, 85, 58).union(box(84, 47, 112, 50))),  # wide body + narrow arm: smart sections
    ]
    return Design(width_mm=115, height_mm=95, layers=layers, objects=objects, source_kind="vector")


PARAM_SETS = {
    "default": {},
    "smart": {"smart_direction": True},
    "plain": {"underlay": False, "tie_stitches": False, "optimize": False, "vary_fill_angle": False},
    "fine": {"thread_weight": 60, "pull_compensation_mm": 0.35, "fill_spacing_mm": 0.33},
}


def _cases():
    shapes = _shapes_design()
    svg = svg_to_design(SVG_SAMPLE, VectorOptions(width_mm=100))
    logo, _ = raster_to_design(make_logo(), RasterOptions(width_mm=90))
    cases = {f"shapes-{name}": (shapes, p) for name, p in PARAM_SETS.items()}
    cases["svg-default"] = (svg, {})
    cases["svg-smart"] = (svg, {"smart_direction": True})
    cases["logo-default"] = (logo, {})
    return cases


def fingerprint(design: Design, params: dict) -> dict:
    plan = plan_stitches(design, StitchParams.from_dict(params))
    counts = {c: 0 for c in (STITCH, JUMP, TRIM, COLOR_CHANGE)}
    h = hashlib.sha256()
    for cmd, x, y, kind in plan.ops:
        counts[cmd] += 1
        h.update(f"{cmd}|{round(x * 10)}|{round(y * 10)}|{kind};".encode())
    return {**counts, "sha256": h.hexdigest()}


@pytest.fixture(scope="module")
def results():
    return {name: fingerprint(design, params) for name, (design, params) in _cases().items()}


def test_golden_plans(results):
    if os.environ.get("UPDATE_GOLDEN") == "1":
        GOLDEN.parent.mkdir(exist_ok=True)
        GOLDEN.write_text(json.dumps(results, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        pytest.skip("golden plans rewritten")
    expected = json.loads(GOLDEN.read_text(encoding="utf-8"))
    assert sorted(results) == sorted(expected)
    changed = {name for name in results if results[name] != expected[name]}
    assert not changed, {name: (expected[name], results[name]) for name in sorted(changed)}
