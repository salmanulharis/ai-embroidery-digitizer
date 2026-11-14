"""Blend fill (Wilcom: Multi Blend): one colour fades into another."""

import numpy as np
import pytest
from fastapi.testclient import TestClient
from shapely.geometry import box

from embroidery_app.api import app
from embroidery_app.models import STITCH, ColorLayer, Design, DesignObject, StitchParams
from embroidery_app.stitch import plan_stitches
from embroidery_app.stitch.fills import blend_rows
from embroidery_app.stitch.planner import describe_objects

SQ = box(5, 5, 65, 35)


def _design(blend_to=1, second_enabled=True):
    return Design(70, 40, [ColorLayer(0, (230, 120, 20), "orange"), ColorLayer(1, (40, 40, 160), "blue", enabled=second_enabled)],
                  [DesignObject(0, SQ, stitch_type="blend_fill", blend_to=blend_to, angle=90.0)])


def _rows_by_colour(plan):
    out, block = {}, 0
    for c, x, y, k in plan.ops:
        if c == "color_change":
            block += 1
        elif c == STITCH and k == "fill":
            out.setdefault(plan.blocks[block].name, []).append(x)
    return {k: np.array(v) for k, v in out.items()}


def test_the_two_colours_add_up_to_one_full_fill():
    a = blend_rows(0.4)(0.0, 60.0)
    b = blend_rows(0.4, reverse=True)(0.0, 60.0)
    for lo in range(0, 60, 10):  # rows per 10 mm window, both colours together
        n = ((a >= lo) & (a < lo + 10)).sum() + ((b >= lo) & (b < lo + 10)).sum()
        assert n == pytest.approx(10 / 0.4, rel=0.25), lo
    assert ((a < 10).sum()) > 3 * ((a > 50).sum())  # this colour fades out across the shape


def test_blend_sews_the_shape_in_both_colours_shaded_opposite_ways():
    plan = plan_stitches(_design(), StitchParams(vary_fill_angle=False))
    assert [b.name for b in plan.blocks] == ["orange", "blue"]
    xs = _rows_by_colour(plan)
    orange_right = (xs["orange"] > 50).sum() / max((xs["orange"] < 20).sum(), 1)
    blue_left = (xs["blue"] < 20).sum() / max((xs["blue"] > 50).sum(), 1)
    assert orange_right > 2 and blue_left > 2  # dense ends at opposite sides
    assert not [r for r in plan.risks if r["type"] in ("stack", "dense")]


def test_without_a_second_colour_the_blend_fades_into_the_fabric():
    plan = plan_stitches(_design(blend_to=None), StitchParams(vary_fill_angle=False))
    assert [b.name for b in plan.blocks] == ["orange"]
    plan = plan_stitches(_design(second_enabled=False), StitchParams(vary_fill_angle=False))
    assert [b.name for b in plan.blocks] == ["orange"]  # a colour that is switched off gets no blend half


def test_blend_setting_round_trips_and_is_reported():
    o = DesignObject(0, SQ, stitch_type="blend_fill", blend_to=1)
    back = DesignObject.from_dict(o.to_dict())
    assert back.blend_to == 1 and back.customised
    info = describe_objects(_design(), StitchParams())[0]
    assert info["method"] == "blend_fill" and info["blend_to"] == 1


def test_blend_through_the_api(svg_text):
    client = TestClient(app)
    pid = client.post("/api/projects", files={"file": ("t.svg", svg_text.encode(), "image/svg+xml")}).json()["id"]
    r = client.post(f"/api/projects/{pid}/extract", json={"options": {"width_mm": 100}}).json()
    obj = next(o for o in r["objects"] if o["kind"] == "area")
    other = next(l["index"] for l in r["design"]["layers"] if l["index"] != obj["layer"])
    r = client.put(f"/api/projects/{pid}/objects", json={"ids": [obj["id"]], "stitch_type": "blend_fill", "blend_to": other}).json()
    assert next(o for o in r["objects"] if o["id"] == obj["id"])["blend_to"] == other
    assert client.put(f"/api/projects/{pid}/objects", json={"ids": [obj["id"]], "blend_to": 99}).status_code == 400
    r = client.put(f"/api/projects/{pid}/objects", json={"ids": [obj["id"]], "blend_to": "inherit"}).json()
    assert next(o for o in r["objects"] if o["id"] == obj["id"])["blend_to"] is None
