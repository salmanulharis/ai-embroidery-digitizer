"""Smart stitch reduction: saves stitches, never changes the design."""

import math

import cv2
import numpy as np
import pytest
from fastapi.testclient import TestClient
from shapely.geometry import box

from embroidery_app import service
from embroidery_app.api import app
from embroidery_app.export import worksheet
from embroidery_app.ingest.raster import RasterOptions, raster_to_design
from embroidery_app.models import STITCH, ColorLayer, Design, DesignObject, StitchParams
from embroidery_app.stitch import generators as gen
from embroidery_app.stitch import optimize as opt
from embroidery_app.stitch import plan_stitches

client = TestClient(app)


def _script_text_design(width=110):
    img = np.full((400, 1200, 3), 255, np.uint8)
    cv2.putText(img, "Script & Co", (30, 280), cv2.FONT_HERSHEY_SCRIPT_COMPLEX, 5, (30, 30, 160), 12)
    return raster_to_design(cv2.cvtColor(img, cv2.COLOR_BGR2RGBA), RasterOptions(width_mm=width))[0]


def _pen(ops):
    return [(round(x, 6), round(y, 6)) for c, x, y, _ in ops if c == STITCH]


# ---------------------------------------------------------------- micro-stitch removal

def test_removes_only_stacked_drops_and_keeps_protected_ones():
    ops = [
        ("jump", 0, 0, ""),
        ("stitch", 0.0, 0, "fill"),   # landing point - kept
        ("stitch", 3.0, 0, "fill"),
        ("stitch", 3.1, 0, "fill"),   # stacked - removed
        ("stitch", 6.0, 0, "fill"),
        ("stitch", 6.1, 0, "fill"),   # end of sequence - kept (6.0 dropped instead)
        ("stitch", 6.2, 0, "satin"),  # satin never touched
        ("stitch", 6.25, 0, "satin"),
        ("stitch", 6.3, 0, "tie"),    # lock stitches never touched
        ("stitch", 6.35, 0, "tie"),
    ]
    out, removed = opt.remove_stacked_stitches(ops, 0.3, 7.0)
    assert removed == 2
    pts = _pen(out)
    assert pts == [(0.0, 0), (3.0, 0), (6.1, 0), (6.2, 0), (6.25, 0), (6.3, 0), (6.35, 0)]
    assert set(pts) <= set(_pen(ops))  # never adds or moves a needle drop


def test_never_creates_a_too_long_stitch():
    # dropping the stacked 7.0 drop would create a 6.9 -> 13.95 stitch of 7.05 mm (> 7 mm max)
    ops = [("stitch", 0, 0, "fill"), ("stitch", 6.9, 0, "fill"), ("stitch", 7.0, 0, "fill"), ("stitch", 13.95, 0, "fill")]
    out, removed = opt.remove_stacked_stitches(ops, 0.3, 7.0)
    assert removed == 0 and out == ops


def test_fill_turnarounds_are_not_mistaken_for_stacked_stitches():
    pieces = gen.tatami_fill(box(0, 0, 20, 10), 0, 0.4, 3.5, (0, 0))
    ops = [("stitch", x, y, "fill") for x, y in pieces[0]]
    _, removed = opt.remove_stacked_stitches(ops, opt.micro_stitch_threshold(0.4), 7.0)
    assert removed == 0


def test_coverage_check_detects_gaps_but_ignores_reordering():
    rows = [[("stitch", 0, y * 0.4, "fill"), ("stitch", 20, y * 0.4, "fill")] for y in range(25)]
    full = [op for r in rows for op in r]
    reordered = [op for r in rows[::-1] for op in r[::-1]]
    assert opt.coverage_loss_pct(full, reordered) == 0
    holed = [op for i, r in enumerate(rows) if not 8 <= i <= 14 for op in r]  # 7 rows (2.8 mm) missing
    assert opt.coverage_loss_pct(full, holed) > opt.MAX_COVERAGE_LOSS_PCT


def test_reduction_is_discarded_when_it_would_open_gaps(monkeypatch):
    ops = [("stitch", 0, 0, "fill"), ("stitch", 0.1, 0, "fill"), ("stitch", 3, 0, "fill")]
    monkeypatch.setattr(opt, "coverage_loss_pct", lambda a, b: 5.0)
    out, report = opt.optimize_ops(ops, 0.4, 7.0)
    assert out == ops and report["reverted"] and report["micro_removed"] == 0


# ---------------------------------------------------------------- whole designs

@pytest.mark.parametrize("design_factory", [
    lambda: _script_text_design(110),
    lambda: _script_text_design(60),
    lambda: Design(60, 33, [ColorLayer(0, (200, 0, 0), "Red")], [DesignObject(0, box(0, 0, 20, 20)), DesignObject(0, box(40, 0, 60, 3))]),
])
def test_optimised_plan_is_never_worse_and_keeps_the_design(design_factory):
    d = design_factory()
    before = plan_stitches(d, StitchParams(optimize=False))
    after = plan_stitches(d, StitchParams())
    sb, sa = before.stats(), after.stats()
    assert sa["stitches"] <= sb["stitches"]
    assert sa["trims"] <= sb["trims"] and sa["jumps"] <= sb["jumps"]
    assert sa["est_minutes"] <= sb["est_minutes"]
    assert (sa["width_mm"], sa["height_mm"]) == (sb["width_mm"], sb["height_mm"])
    assert opt.coverage_loss_pct(before.ops, after.ops) <= opt.MAX_COVERAGE_LOSS_PCT
    prev = None
    for cmd, x, y, _ in after.ops:  # still machine-safe
        if cmd == STITCH and prev is not None:
            assert math.dist(prev, (x, y)) <= 7.0 + 1e-6
        if cmd in ("stitch", "jump"):
            prev = (x, y)


def test_thin_lettering_saves_duplicate_foundation_stitches():
    d = _script_text_design(110)
    before = plan_stitches(d, StitchParams(optimize=False)).stats()["stitches"]
    after = plan_stitches(d, StitchParams()).stats()["stitches"]
    assert after < before * 0.8


def test_optimize_off_reports_nothing():
    plan = plan_stitches(_script_text_design(60), StitchParams(optimize=False))
    assert plan.optimization == {}


def test_worksheet_counts_match_final_stitches():
    plan = plan_stitches(_script_text_design(60), StitchParams())
    text = worksheet(plan, "t")
    listed = sum(int(line.split()[-2].replace(",", "")) for line in text.splitlines() if line.strip().endswith(" stitches") and line.strip()[0].isdigit())
    assert listed == plan.stats()["stitches"]


# ---------------------------------------------------------------- API comparison

def _project():
    img = np.full((400, 1200, 3), 255, np.uint8)
    cv2.putText(img, "Script & Co", (30, 280), cv2.FONT_HERSHEY_SCRIPT_COMPLEX, 5, (30, 30, 160), 12)
    pid = client.post("/api/projects", files={"file": ("t.png", cv2.imencode(".png", img)[1].tobytes())}).json()["id"]
    assert client.post(f"/api/projects/{pid}/extract", json={"options": {"width_mm": 110}}).status_code == 200
    return pid


def test_api_returns_comparison_and_exports_optimised_version():
    pid = _project()
    r = client.post(f"/api/projects/{pid}/stitch", json={"params": {"optimize": True}}).json()
    c = r["comparison"]
    assert c["saved_stitches"] == c["original"]["stitches"] - c["optimized"]["stitches"] > 0
    assert r["stats"]["stitches"] == c["optimized"]["stitches"]
    assert len(r["original_ops"]) // 4 > len(r["ops"]) // 4
    assert c["coverage_change_pct"] <= opt.MAX_COVERAGE_LOSS_PCT
    text = client.get(f"/api/projects/{pid}/export/txt").text
    assert f"Stitches: {c['optimized']['stitches']:,}" in text  # downloads use the optimised stitches

    r = client.post(f"/api/projects/{pid}/stitch", json={"params": {"optimize": False}}).json()
    assert "comparison" not in r and "original_ops" not in r
    assert r["stats"]["stitches"] == c["original"]["stitches"]


def test_api_falls_back_to_original_if_the_design_would_change(monkeypatch):
    pid = _project()
    monkeypatch.setattr(service, "coverage_loss_pct", lambda a, b: 5.0)
    r = client.post(f"/api/projects/{pid}/stitch", json={"params": {"optimize": True}}).json()
    assert r["comparison"]["reverted"] is True
    assert r["stats"]["stitches"] == r["comparison"]["original"]["stitches"]
    text = client.get(f"/api/projects/{pid}/export/txt").text
    assert f"Stitches: {r['comparison']['original']['stitches']:,}" in text
