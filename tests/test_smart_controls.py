"""Smart defaults (text detection) and the manual controls that override them:
picture adjustments, the "Lettering" stitch style, and validation of new settings."""

import cv2
import numpy as np
from shapely.geometry import LineString, box
from shapely.ops import unary_union

import embroidery_app.ingest.raster as R
from embroidery_app.ai.builtin import analyze_image, detect_text
from embroidery_app.models import STITCH_TYPES, ColorLayer, DesignObject, StitchParams
from embroidery_app.stitch.planner import classify


def text_picture(scale=1.2, thickness=3):
    img = np.full((360, 1000, 3), 255, np.uint8)
    cv2.putText(img, "Bakery & Cafe", (30, 140), cv2.FONT_HERSHEY_SIMPLEX, scale, (20, 20, 120), thickness, cv2.LINE_AA)
    cv2.putText(img, "Fresh every day", (30, 260), cv2.FONT_HERSHEY_SIMPLEX, scale, (20, 20, 120), thickness, cv2.LINE_AA)
    return cv2.cvtColor(img, cv2.COLOR_BGR2RGBA)


def shapes_picture():
    img = np.full((400, 400, 3), 255, np.uint8)
    cv2.circle(img, (200, 200), 150, (0, 0, 200), -1)
    cv2.rectangle(img, (20, 20), (80, 60), (0, 150, 0), -1)
    return cv2.cvtColor(img, cv2.COLOR_BGR2RGBA)


# ---------------------------------------------------------------- text detection

def test_text_lines_are_found_and_measured():
    r = analyze_image(text_picture(), 50)
    assert r["text"]["found"] and r["text"]["lines"] == 2
    assert 1.0 < r["text"]["letter_height_mm"] < 5.0
    assert r["extract"]["min_feature_mm"] <= 0.25  # small text kept sharp
    assert r["stitch"]["thread_weight"] == 60  # letters under 5 mm: fine thread
    assert any("Text found" in n for n in r["notes"])


def test_shapes_are_not_mistaken_for_text():
    r = analyze_image(shapes_picture(), 100)
    assert not r["text"]["found"]
    assert r["stitch"]["thread_weight"] == 40


def test_large_text_keeps_standard_thread():
    r = analyze_image(text_picture(), 400)  # same text, 5x bigger on fabric
    assert r["text"]["found"] and r["text"]["letter_height_mm"] > 5
    assert r["stitch"]["thread_weight"] == 40


def test_detect_text_handles_empty_masks():
    assert detect_text(np.zeros((50, 50), bool), 0.1)["found"] is False


# ---------------------------------------------------------------- picture adjustments

def test_adjustments_change_the_picture_as_expected():
    rgb = np.full((40, 40, 3), 128, np.uint8)
    rgb[:, :20] = 90
    assert R._adjust(rgb, 50, 0, 0).mean() > rgb.mean() + 30
    assert R._adjust(rgb, -50, 0, 0).mean() < rgb.mean() - 30
    more = R._adjust(rgb, 0, 60, 0)
    assert int(more[:, 30, 0].mean()) - int(more[:, 5, 0].mean()) > 38 * 1.5  # stronger contrast
    assert R._adjust(rgb, 0, 0, 0) is rgb  # no-op stays a no-op


def test_sharpen_crisps_soft_edges():
    img = np.full((40, 40, 3), 255, np.uint8)
    img[:, 20:] = 0
    soft = cv2.GaussianBlur(img, (0, 0), 1.5)
    sharp = R._adjust(soft, 0, 0, 2)
    edge = lambda a: np.abs(np.diff(a[:, :, 0].astype(int), axis=1)).max()
    assert edge(sharp) > edge(soft)


def test_adjustment_options_reach_the_extraction():
    opts = R.RasterOptions(width_mm=60, brightness=-40, contrast=40, sharpen=2)
    design, _ = R.raster_to_design(text_picture(), opts)
    assert design.objects  # still extracts lettering


# ---------------------------------------------------------------- lettering style

def test_lettering_style_follows_strokes_even_for_bolder_text():
    stroke = unary_union([LineString([(0, 0), (20, 0)]).buffer(1.3, cap_style="flat"),
                          LineString([(10, 0), (10, 15)]).buffer(1.3, cap_style="flat")])  # 2.6 mm strokes
    p = StitchParams()
    assert classify(DesignObject(0, stroke), "auto", p)[0].method != "stroke"  # too bold for automatic
    assert [it.method for it in classify(DesignObject(0, stroke), "lettering", p)] == ["stroke"]
    big = box(0, 0, 40, 40)  # far wider than a satin column: usual method
    assert classify(DesignObject(0, big), "lettering", p)[0].method == "fill"
    line = DesignObject(0, LineString([(0, 0), (30, 0)]), kind="line", stroke_width_mm=2)
    assert classify(line, "lettering", p)[0].method == "satin_line"


def test_lettering_is_a_valid_style_everywhere():
    assert "lettering" in STITCH_TYPES
    d = DesignObject(0, box(0, 0, 1, 10), stitch_type="lettering").to_dict()
    assert DesignObject.from_dict(d).stitch_type == "lettering"
    assert ColorLayer.from_dict({"index": 0, "rgb": [0, 0, 0], "stitch_type": "lettering"}).stitch_type == "lettering"
    assert ColorLayer.from_dict({"index": 0, "rgb": [0, 0, 0], "stitch_type": "bogus"}).stitch_type == "auto"
