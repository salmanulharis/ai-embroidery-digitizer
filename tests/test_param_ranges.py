"""Allowed ranges of the design-wide settings: one table (models.STITCH_RANGES,
ingest/raster.RASTER_RANGES) that the editor's controls, presets and the assistant follow,
and that every request is clamped to - so no request can raise the 7 mm stitch cap or ask
for work the server cannot finish. Valid values must pass through unchanged (byte-identical
stitches)."""

import math
import re
import time
from dataclasses import fields
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from embroidery_app import presets, service
from embroidery_app.ai import builtin
from embroidery_app.api import app
from embroidery_app.ingest.raster import MAX_COLOURS, RASTER_RANGES, RasterOptions
from embroidery_app.ingest.vector import VECTOR_RANGES, VectorOptions
from embroidery_app.models import RUNNING_REPEATS, STITCH, STITCH_RANGES, StitchParams

STATIC = Path(__file__).resolve().parent.parent / "embroidery_app" / "static"
client = TestClient(app)


# --------------------------------------------------------------------------- the editor follows the table

def test_editor_stitch_controls_match_the_ranges():
    js = (STATIC / "app.js").read_text(encoding="utf-8")
    block = js[js.index("const PARAMS = ["):js.index("];", js.index("const PARAMS = ["))]
    controls = {m[0]: (float(m[1]), float(m[2])) for m in re.findall(r'\["(\w+)", "[^"]*", ([-0-9.]+), ([-0-9.]+)', block)}
    assert len(controls) >= 19
    for key, bounds in controls.items():
        assert key in STITCH_RANGES, f"{key} has an editor control but no allowed range"
        assert bounds == tuple(map(float, STITCH_RANGES[key])), (key, bounds, STITCH_RANGES[key])


def test_editor_picture_controls_match_the_ranges():
    html = (STATIC / "index.html").read_text(encoding="utf-8")
    checked = 0
    for tag in re.findall(r"<input[^>]+>", html):
        m = re.search(r'id="(\w+)"', tag)
        if not m or m.group(1) not in RASTER_RANGES:
            continue
        lo, hi = re.search(r'min="([-0-9.]+)"', tag).group(1), re.search(r'max="([-0-9.]+)"', tag).group(1)
        assert (float(lo), float(hi)) == tuple(map(float, RASTER_RANGES[m.group(1)])), m.group(1)
        checked += 1
    assert checked == 7  # width, brightness, contrast, sharpen, denoise, smallest detail, specks
    js = (STATIC / "app.js").read_text(encoding="utf-8")
    assert f"i <= {MAX_COLOURS}; i++" in js  # the "Number of thread colours" choices


def test_presets_and_assistant_use_the_same_ranges():
    for name, key in presets.KEYS.items():
        if key.kind in ("num", "int", "colours"):
            table = STITCH_RANGES if key.section in ("stitch", "fabric") else RASTER_RANGES
            assert (key.lo, key.hi) == tuple(table[name]), name
    for name, limits in builtin.STITCH_LIMITS.items():
        if name in STITCH_RANGES:
            assert limits == STITCH_RANGES[name], name


# --------------------------------------------------------------------------- stitch settings

def test_valid_settings_pass_through_unchanged():
    assert StitchParams.from_dict(StitchParams().to_dict()) == StitchParams()
    for name, (lo, hi) in STITCH_RANGES.items():
        for v in (lo, hi, (lo + hi) / 2):
            assert getattr(StitchParams.from_dict({name: v}), name) == v, (name, v)
    assert StitchParams.from_dict({"running_repeats": 3}).running_repeats == 3


def test_extreme_settings_are_clamped():
    p = StitchParams.from_dict({
        "fill_spacing_mm": 0.001, "satin_spacing_mm": 50, "fill_stitch_length_mm": 0, "running_stitch_length_mm": -1,
        "underlay_spacing_mm": 0.0001, "trim_threshold_mm": -5, "max_stitch_mm": 500, "satin_min_width_mm": 0,
        "running_repeats": 1000, "pull_compensation_mm": 9, "min_object_area_mm2": -3,
    })
    assert (p.fill_spacing_mm, p.satin_spacing_mm) == (0.25, 1.0)
    assert (p.fill_stitch_length_mm, p.running_stitch_length_mm, p.underlay_spacing_mm) == (2.0, 1.0, 1.5)
    assert (p.trim_threshold_mm, p.max_stitch_mm, p.satin_min_width_mm) == (1.0, 7.0, 0.5)
    assert (p.running_repeats, p.pull_compensation_mm, p.min_object_area_mm2) == (3, 0.8, 0.0)
    for name, (lo, hi) in STITCH_RANGES.items():
        assert lo <= getattr(p, name) <= hi, name
    # a lower stitch cap is allowed (shorter stitches are always safe)
    assert StitchParams.from_dict({"max_stitch_mm": 4.5}).max_stitch_mm == 4.5


def test_angles_fold_back_into_the_same_direction():
    assert StitchParams.from_dict({"fill_angle": -45}).fill_angle == 135
    assert StitchParams.from_dict({"fill_angle": 200}).fill_angle == 20
    assert StitchParams.from_dict({"fill_angle": 180}).fill_angle == 180  # in range: unchanged


def test_non_numbers_and_switches():
    d = StitchParams()
    p = StitchParams.from_dict({"fill_spacing_mm": float("nan"), "pull_compensation_mm": float("inf")})
    assert (p.fill_spacing_mm, p.pull_compensation_mm) == (d.fill_spacing_mm, d.pull_compensation_mm)
    for bad in ("abc", [1], {"a": 1}):
        with pytest.raises(ValueError, match="fill_spacing_mm: enter a number"):
            StitchParams.from_dict({"fill_spacing_mm": bad})
    assert StitchParams.from_dict({"underlay": "false", "optimize": "0", "knockdown": "true"}).underlay is False
    assert StitchParams.from_dict({"optimize": "0"}).optimize is False
    assert StitchParams.from_dict({"knockdown": "true"}).knockdown is True
    # names that are not settings are ignored (methods, private attributes)
    p = StitchParams.from_dict({"to_dict": 1, "__class__": 2, "nope": 3})
    assert p == StitchParams() and callable(p.to_dict)
    assert StitchParams.from_dict({"thread_weight": "60"}).thread_weight == 60
    assert RUNNING_REPEATS == (1, 3)


# --------------------------------------------------------------------------- picture and artwork options

def test_picture_options_are_clamped():
    o = service._options(RasterOptions, {"num_colors": 10_000, "max_auto_colors": 999, "min_feature_mm": 1000,
                                         "min_area_mm2": -1, "denoise": 9, "brightness": -500, "contrast": 400,
                                         "sharpen": 7, "bg_tolerance": 0, "background": "sparkly", "width_mm": 80})
    assert (o.num_colors, o.max_auto_colors, o.min_feature_mm, o.min_area_mm2) == (MAX_COLOURS, MAX_COLOURS, 3.0, 0.2)
    assert (o.denoise, o.brightness, o.contrast, o.sharpen, o.bg_tolerance) == (3, -100, 100, 3, 1.0)
    assert o.background == "auto" and o.width_mm == 80
    assert service._options(RasterOptions, {"num_colors": 0}).num_colors is None  # automatic
    assert service._options(RasterOptions, {"num_colors": None}).num_colors is None
    d = RasterOptions()
    assert service._options(RasterOptions, {f.name: getattr(d, f.name) for f in fields(d)}) == d  # unchanged


def test_artwork_options_are_clamped():
    o = service._options(VectorOptions, {"num_colors": 500, "underlap_mm": 100, "min_area_mm2": -2, "knockout": False})
    assert (o.num_colors, o.underlap_mm, o.min_area_mm2, o.knockout) == (15, 2.0, 0.0, False)
    assert service._options(VectorOptions, {}) == VectorOptions()
    for name, (lo, hi) in VECTOR_RANGES.items():
        assert lo <= getattr(o, name) <= hi, name


# --------------------------------------------------------------------------- through the API

def _longest(ops) -> float:
    steps = [math.dist(a[1:3], b[1:3]) for a, b in zip(ops, ops[1:]) if a[0] == b[0] == STITCH]
    return max(steps)


def test_api_cannot_raise_the_stitch_cap_or_overload_the_server(logo_png):
    pid = client.post("/api/projects", files={"file": ("logo.png", logo_png)}).json()["id"]
    r = client.post(f"/api/projects/{pid}/extract", json={"options": {"width_mm": 80, "num_colors": 10_000, "min_feature_mm": 900}})
    assert r.status_code == 200, r.text
    assert len(r.json()["design"]["layers"]) <= MAX_COLOURS
    t0 = time.time()
    r = client.post(f"/api/projects/{pid}/stitch", json={"params": {
        "max_stitch_mm": 500, "fill_spacing_mm": 0.001, "satin_spacing_mm": 0.001, "fill_stitch_length_mm": 300,
        "running_stitch_length_mm": 0, "underlay_spacing_mm": 0.001, "running_repeats": 1000}})
    assert r.status_code == 200, r.text
    assert time.time() - t0 < 60  # clamped to the densest valid settings, not 400x more rows
    saved = service._meta(pid)["stitch_params"]
    assert saved["max_stitch_mm"] == 7.0 and saved["fill_spacing_mm"] == 0.25 and saved["running_repeats"] == 3
    plan = service.get_plan(pid)
    assert _longest(plan.ops) <= 7.0 + 1e-6
    r = client.post(f"/api/projects/{pid}/stitch", json={"params": {"fill_spacing_mm": "lots"}})
    assert r.status_code == 400 and "enter a number" in r.json()["detail"]
