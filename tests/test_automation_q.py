"""Automation, round four (docs/AUTOMATION_UX_PLAN.md, batches Q-V): your check choices, needles,
the price estimate, the garment colour, Turn 90° and hiding moves under later fills. Stitches stay
byte-identical with the options off (tests/test_golden.py)."""

import cv2
import numpy as np
import pytest
from fastapi.testclient import TestClient
from shapely.geometry import LineString, Point, box

from accounts import admin, new_user, real_login  # noqa: F401
from embroidery_app import prefs, service
from embroidery_app.api import app
from embroidery_app.export.embroidery import quote_text, worksheet
from embroidery_app.ingest.raster import RasterOptions, raster_to_design
from embroidery_app.ingest.vector import VectorOptions, svg_to_design
from embroidery_app.models import JUMP, STITCH, TRIM, ColorLayer, Design, DesignObject, StitchParams, quarter_turns
from embroidery_app.stitch import plan_stitches
from embroidery_app.stitch.planner import TRAVEL_UNDER_INSET_MM

client = TestClient(app)


# --------------------------------------------------------------------------- U, T, V: preferences

@pytest.mark.usefixtures("real_login")
def test_new_preferences_are_validated():
    _, c = new_user(admin())
    ok = {"hidden_checks": ["colour_order", "garment"], "auto_fix": True, "needles": 12,
          "price_per_1000": 1.2, "price_min": 8, "currency": "€", "garment": "#112233"}
    assert c.put("/api/me/prefs", json=ok).json() == ok
    for bad in ({"hidden_checks": "colour_order"}, {"hidden_checks": ["Bad Code"]}, {"hidden_checks": ["a_b", "a_b"]},
                {"auto_fix": "yes"}, {"needles": 0}, {"needles": 21}, {"needles": 2.5}, {"needles": True},
                {"price_per_1000": -1}, {"price_per_1000": "1"}, {"price_min": 1e9}, {"currency": "<b>"},
                {"currency": "EUROS"}, {"garment": "white"}, {"garment": "#FFF"}):
        assert c.put("/api/me/prefs", json=bad).status_code == 400, bad
    assert c.put("/api/me/prefs", json={"hidden_checks": None, "price_per_1000": None}).status_code == 200
    got = c.get("/api/me/prefs").json()
    assert "hidden_checks" not in got and "price_per_1000" not in got and got["needles"] == 12


def test_every_machine_has_its_usual_needles():
    for key, m in prefs.MACHINES.items():
        assert prefs.NEEDLES[0] <= m["needles"] <= prefs.NEEDLES[1], key
    assert prefs.MACHINES["brother"]["needles"] == 1 and prefs.MACHINES["tajima"]["needles"] > 1


def test_price_estimate_text():
    assert quote_text(10_000, None) == "" and quote_text(10_000, {"per_1000": 0}) == ""
    assert quote_text(12_500, {"per_1000": 1.2, "minimum": 0, "currency": "$"}) == \
        "Price estimate: $ 15.00 (12,500 stitches at $ 1.20 per 1,000)"
    low = quote_text(2_000, {"per_1000": 1.0, "minimum": 8, "currency": ""})
    assert low.startswith("Price estimate: 8.00 ") and "minimum 8.00" in low


@pytest.mark.usefixtures("real_login")
def test_worksheet_has_the_price_only_when_set():
    from accounts import stitched_project

    _, c = new_user(admin())
    pid = stitched_project(c)
    assert "Price estimate" not in c.get(f"/api/projects/{pid}/export/txt").text
    c.put("/api/me/prefs", json={"price_per_1000": 2.0, "currency": "AED"})
    sheet = c.get(f"/api/projects/{pid}/export/txt").text
    assert "Price estimate: AED " in sheet and "per 1,000" in sheet


def test_worksheet_without_a_quote_is_unchanged():
    d = Design(20, 20, [ColorLayer(0, (0, 0, 0), "Black")], [DesignObject(0, box(2, 2, 18, 18))])
    plan = plan_stitches(d, StitchParams())
    assert worksheet(plan, "x") == worksheet(plan, "x", None)


# --------------------------------------------------------------------------- Q: garment colour

def _white_and_red():
    layers = [ColorLayer(0, (250, 250, 248), "White"), ColorLayer(1, (200, 20, 20), "Red")]
    return Design(40, 40, layers, [DesignObject(0, box(2, 2, 20, 20)), DesignObject(1, box(22, 22, 38, 38))])


def test_garment_colour_check_offers_to_skip_that_colour():
    d = _white_and_red()
    c = service._garment_check(d, StitchParams(garment_hex="#ffffff"))
    assert c["code"] == "garment" and c["skip"]["layers"] == [0] and c["skip"]["label"] == "Don't sew White"
    assert service._garment_check(d, StitchParams()) is None  # no garment colour set
    assert service._garment_check(d, StitchParams(garment_hex="#0000ff")) is None
    only = Design(40, 40, [d.layers[0]], [d.objects[0]])
    assert service._garment_check(only, StitchParams(garment_hex="#ffffff")) is None  # never every colour


def test_garment_colour_is_validated_and_never_changes_the_stitches():
    assert StitchParams(garment_hex="#ABCDEF").garment_hex == "#abcdef"
    assert StitchParams(garment_hex="red").garment_hex == "" and StitchParams.from_dict({"garment_hex": 5}).garment_hex == ""
    d = _white_and_red()
    assert service._plan_key(d, StitchParams()) == service._plan_key(d, StitchParams(garment_hex="#ffffff"))
    assert plan_stitches(d, StitchParams()).ops == plan_stitches(d, StitchParams(garment_hex="#ffffff")).ops


# --------------------------------------------------------------------------- S: Turn 90°

def test_quarter_turns():
    assert [quarter_turns(v) for v in (0, 90, 180, 270, 360, 450, -90, 89, "90", None, "x")] == [0, 90, 180, 270, 0, 90, 270, 90, 90, 0, 0]
    assert RasterOptions(rotate=91).rotate == 90 and VectorOptions(rotate=-90).rotate == 270


def _wide_picture():
    img = np.full((200, 400, 4), 255, np.uint8)
    cv2.rectangle(img, (20, 40), (380, 160), (200, 30, 30, 255), -1)
    cv2.rectangle(img, (20, 40), (60, 160), (30, 30, 200, 255), -1)  # blue on the left
    return img


def test_a_turned_picture_is_tall_and_keeps_its_left_on_top():
    flat, _ = raster_to_design(_wide_picture(), RasterOptions(width_mm=90))
    tall, _ = raster_to_design(_wide_picture(), RasterOptions(width_mm=30, rotate=90))
    assert flat.width_mm > flat.height_mm and tall.height_mm > tall.width_mm
    blue = max(tall.layers, key=lambda l: l.rgb[2] - l.rgb[0])
    shape = next(o for o in tall.objects if o.layer == blue.index)
    assert shape.geometry.centroid.y < tall.height_mm / 3  # turned clockwise: the left end is now at the top


def test_a_turned_artwork_file():
    svg = '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 200 50"><rect width="200" height="50" fill="#c00"/><rect width="20" height="50" fill="#00c"/></svg>'
    flat, tall = svg_to_design(svg, VectorOptions(width_mm=100)), svg_to_design(svg, VectorOptions(width_mm=25, rotate=90))
    assert flat.width_mm == pytest.approx(100) and tall.width_mm == pytest.approx(25) and tall.height_mm == pytest.approx(100)


def test_turning_keeps_each_shapes_own_settings():
    img = _wide_picture()
    pid = client.post("/api/projects", files={"file": ("w.png", cv2.imencode(".png", img[:, :, :3])[1].tobytes())}).json()["id"]
    res = client.post(f"/api/projects/{pid}/extract", json={"options": {"width_mm": 90}}).json()
    blue_layer = max(res["design"]["layers"], key=lambda l: int(l["hex"][5:7], 16) - int(l["hex"][1:3], 16))
    blue = next(o for o in res["objects"] if o["layer"] == blue_layer["index"])
    client.put(f"/api/projects/{pid}/objects", json={"ids": [blue["id"]], "angle": 30.0})
    height = res["design"]["height_mm"]
    turned = client.post(f"/api/projects/{pid}/extract", json={"options": {"width_mm": round(height), "rotate": 90}}).json()
    mine = [o for o in turned["objects"] if o["angle"] is not None]
    assert len(mine) == 1 and mine[0]["angle"] == pytest.approx(120.0)
    assert any("kept for 1 of 1" in w for w in turned["design"]["warnings"])
    back = client.post(f"/api/projects/{pid}/undo").json()  # undo: the design as it was
    assert next(o for o in back["objects"] if o["id"] == blue["id"])["angle"] == pytest.approx(30.0)


def test_guide_lines_follow_a_turn():
    old = Design(40, 20, [ColorLayer(0, (0, 0, 0), "Black")], [DesignObject(0, box(0, 0, 40, 20), guide=[(0.0, 10.0), (40.0, 10.0)], angle=0.0)])
    new = Design(20, 40, [ColorLayer(0, (0, 0, 0), "Black")], [DesignObject(0, box(0, 0, 20, 40))])
    service._carry_shape_settings(old, new, turned=90)
    o = new.objects[0]
    assert o.angle == 90.0 and o.guide == [[10.0, 0.0], [10.0, 40.0]]  # the horizontal guide is vertical now


def test_guide_lines_follow_a_size_change():
    old = Design(40, 20, [ColorLayer(0, (0, 0, 0), "Black")], [DesignObject(0, box(0, 0, 40, 20), guide=[(0.0, 10.0), (40.0, 10.0)])])
    new = Design(80, 40, [ColorLayer(0, (0, 0, 0), "Black")], [DesignObject(0, box(0, 0, 80, 40))])
    service._carry_shape_settings(old, new)
    assert new.objects[0].guide == [[0.0, 20.0], [80.0, 20.0]]


# --------------------------------------------------------------------------- R: hide moves under later fills

def _pieces_under_a_later_fill():
    """Three red dots on a line, then a blue band sewn later around them (sharing their edges,
    as prepared regions do) that covers the gaps between them."""
    layers = [ColorLayer(0, (200, 20, 20), "Red"), ColorLayer(1, (20, 20, 200), "Blue")]
    dots = [DesignObject(0, box(x, 18, x + 4, 22)) for x in (5, 25, 45)]
    band = box(0, 10, 60, 30)
    for d in dots:
        band = band.difference(d.geometry)
    return Design(60, 40, layers, [*dots, DesignObject(1, band)])


def _first_colour(ops):
    out = []
    for op in ops:
        if op[0] == "color_change":
            break
        out.append(op)
    return out


def test_moves_hide_under_the_later_fill_instead_of_cuts():
    d = _pieces_under_a_later_fill()
    off, on = plan_stitches(d, StitchParams()), plan_stitches(d, StitchParams(travel_under=True))
    trims = lambda ops: sum(1 for c, *_ in _first_colour(ops) if c == TRIM)  # noqa: E731
    jumps = lambda ops: sum(1 for c, *_ in _first_colour(ops) if c == JUMP)  # noqa: E731
    assert trims(on.ops) < trims(off.ops) and jumps(on.ops) < jumps(off.ops)
    # every hidden stitch is under the blue band, or at the edge of a red dot it leaves / reaches
    band = d.objects[-1].geometry
    travel = [(x, y) for c, x, y, k in _first_colour(on.ops) if c == STITCH and k == "travel"]
    hidden = [p for p in travel if not any(o.geometry.buffer(0.2).contains(Point(p)) for o in d.objects[:3])]
    assert hidden and all(band.contains(Point(p)) for p in hidden)
    assert TRAVEL_UNDER_INSET_MM > 0


def test_no_hiding_without_a_later_fill_or_when_off():
    d = _pieces_under_a_later_fill()
    assert not StitchParams().travel_under
    alone = Design(60, 40, d.layers[:1], d.objects[:3])  # nothing sewn after the dots
    assert plan_stitches(alone, StitchParams()).ops == plan_stitches(alone, StitchParams(travel_under=True)).ops
    line = DesignObject(1, LineString([(0, 20), (60, 20)]), kind="line", stroke_width_mm=0.4)  # a thin line covers nothing
    thin = Design(60, 40, d.layers, [*d.objects[:3], line])
    assert plan_stitches(thin, StitchParams()).ops == plan_stitches(thin, StitchParams(travel_under=True)).ops


def test_the_option_is_a_preset_key():
    from embroidery_app import presets

    sec, dropped = presets.clean({"stitch": {"travel_under": True}})
    assert sec == {"stitch": {"travel_under": True}} and not dropped
