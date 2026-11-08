"""Merged / same-colour pieces must stitch as one continuous region."""

from collections import Counter

import cv2
import numpy as np
import pytest
from fastapi.testclient import TestClient
from shapely.geometry import LineString, Polygon, box

from embroidery_app.api import app
from embroidery_app.geometry import merge_areas
from embroidery_app.ingest.vector import VectorOptions, svg_to_design
from embroidery_app.models import ColorLayer, Design, DesignObject, StitchParams
from embroidery_app.stitch import plan_stitches
from embroidery_app.stitch.planner import classify

client = TestClient(app)
GAP = 0.08  # hairline left between regions traced separately from a raster


# ---------------------------------------------------------------- merge_areas

def test_hairline_seam_is_bridged_without_changing_the_outline():
    a, b = box(0, 0, 10, 10), box(10 + GAP, 0, 20, 10)
    merged = merge_areas([a, b])
    assert len(merged) == 1
    solid = box(0, 0, 20, 10)
    assert merged[0].difference(solid).area < 1e-6  # nothing protrudes past the outer edge
    assert solid.difference(merged[0]).area < 1e-3  # seam fully closed


def test_far_apart_pieces_stay_separate():
    assert len(merge_areas([box(0, 0, 10, 10), box(10.5, 0, 20, 10)])) == 2


def test_tiny_hole_inside_a_piece_is_kept():
    slot = box(4, 3, 4.1, 7)  # 0.1 mm wide hole - narrower than the seam tolerance
    a = box(0, 0, 10, 10).difference(slot)
    b = box(10 + GAP, 0, 20, 10)
    merged = merge_areas([a, b])
    assert len(merged) == 1
    assert len(merged[0].interiors) == 1
    assert merged[0].interiors[0].envelope.equals_exact(slot.exterior.envelope, 1e-6)


def test_other_colour_between_pieces_blocks_the_bridge():
    a, b = box(0, 0, 10, 10), box(10.15, 0, 20, 10)
    strip = box(10.02, 0, 10.13, 10)  # thin strip of another colour in the gap
    merged = merge_areas([a, b], obstacles=strip)
    assert len(merged) == 2
    assert all(m.intersection(strip).area < 1e-9 for m in merged)


# ---------------------------------------------------------------- Design.normalize

def _shades_design() -> Design:
    layers = [ColorLayer(0, (20, 20, 20), "Black"), ColorLayer(1, (200, 0, 0), "Red")]
    objects = [
        DesignObject(0, box(0, 0, 30, 20)),
        DesignObject(0, box(30 + GAP, 0, 60, 20)),  # merged shade, hairline apart
        DesignObject(1, box(70, 0, 80, 10)),
        DesignObject(0, LineString([(0, 25), (60, 25)]), kind="line", stroke_width_mm=2),
    ]
    return Design(80, 26, layers, objects)


def test_normalize_fuses_layer_keeps_lines_and_is_idempotent():
    d = _shades_design().normalize()
    areas = [o for o in d.objects if o.layer == 0 and o.kind == "area"]
    lines = [o for o in d.objects if o.kind == "line"]
    assert len(areas) == 1 and len(lines) == 1
    assert lines[0].geometry.equals(LineString([(0, 25), (60, 25)]))
    assert len([o for o in d.objects if o.layer == 1]) == 1
    before = [o.geometry.wkt for o in d.objects]
    assert [o.geometry.wkt for o in d.normalize().objects] == before


def test_normalized_copy_leaves_original_untouched():
    d = _shades_design()
    n_before = len(d.objects)
    copy = d.normalized()
    assert len(d.objects) == n_before
    assert len(copy.objects) == n_before - 1


def test_disabled_layer_still_blocks_bridging():
    layers = [ColorLayer(0, (0, 0, 0), "Black"), ColorLayer(1, (255, 255, 255), "White", enabled=False)]
    objects = [
        DesignObject(0, box(0, 0, 10, 10)),
        DesignObject(0, box(10.15, 0, 20, 10)),
        DesignObject(1, box(10.02, 0, 10.13, 10)),  # fabric-coloured gap the user switched off
    ]
    d = Design(20, 10, layers, objects).normalize()
    assert len([o for o in d.objects if o.layer == 0]) == 2


def test_unnormalized_design_still_stitches_as_one_region():
    """Designs saved before normalisation existed: the planner fuses them itself."""
    plan = plan_stitches(_shades_design(), StitchParams())
    runs = Counter((r.kind, r.object_id) for r in plan.blocks[0].runs if r.kind == "fill")
    fill_objects = {oid for _, oid in runs}
    # one fill region for the black area (+ none for the line, which is satin)
    assert len(fill_objects) == 1


# ---------------------------------------------------------------- vector import

def test_vector_near_identical_shades_become_one_region():
    svg = """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 50">
      <rect x="0" y="0" width="60" height="50" fill="#111111"/>
      <rect x="60" y="0" width="40" height="50" fill="#131313"/>
      <rect x="0" y="20" width="100" height="4" fill="#121212"/>
      <circle cx="80" cy="40" r="5" fill="#ff0000"/></svg>"""
    d = svg_to_design(svg, VectorOptions(width_mm=100)).normalize()
    black = next(l for l in d.layers if l.hex == "#111111")
    objs = [o for o in d.objects if o.layer == black.index]
    assert len(objs) == 1
    assert [it.method for it in classify(objs[0], "auto", StitchParams())] == ["fill"]
    assert len(objs[0].geometry.interiors) == 1  # the red circle on top stays knocked out


# ---------------------------------------------------------------- end-to-end via API

def _badge(shades) -> bytes:
    img = np.full((500, 700, 3), 255, np.uint8)
    cv2.rectangle(img, (100, 100), (300, 400), shades[0], -1)
    cv2.rectangle(img, (300, 100), (600, 400), shades[1], -1)
    cv2.circle(img, (350, 250), 90, shades[2], -1)
    return cv2.imencode(".png", img)[1].tobytes()


def _stitch_badge(shades, merge: bool) -> tuple[dict, dict]:
    pid = client.post("/api/projects", files={"file": ("badge.png", _badge(shades))}).json()["id"]
    res = client.post(f"/api/projects/{pid}/extract", json={"options": {"width_mm": 80}}).json()
    layers = res["design"]["layers"]
    if merge:
        for l in layers[1:]:
            res = client.post(f"/api/projects/{pid}/layers/merge", json={"source": l["index"], "target": layers[0]["index"]}).json()
    prev = client.post(f"/api/projects/{pid}/stitch", json={"params": {}}).json()
    return res["design"], prev["stats"]


def test_merged_shades_stitch_like_a_solid_colour():
    solid_design, solid = _stitch_badge([(20, 20, 20)] * 3, merge=False)
    merged_design, merged = _stitch_badge([(20, 20, 20), (70, 70, 70), (45, 45, 45)], merge=True)
    assert len(merged_design["layers"]) == 1
    assert merged_design["layers"][0]["objects"] == 1  # UI / SVG show one region too
    assert merged_design["layers"][0]["area_mm2"] == pytest.approx(solid_design["layers"][0]["area_mm2"], rel=0.005)
    assert merged["trims"] == solid["trims"]
    assert merged["jumps"] == solid["jumps"]
    assert merged["stitches"] == pytest.approx(solid["stitches"], rel=0.02)
