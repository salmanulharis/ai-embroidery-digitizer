import math

import pyembroidery as pe
import pytest
from shapely.geometry import LineString, Point, Polygon, box

from embroidery_app.export import export_embroidery
from embroidery_app.ingest import detect_kind
from embroidery_app.ingest.raster import RasterOptions, raster_to_design
from embroidery_app.ingest.vector import VectorOptions, svg_to_design
from embroidery_app.models import STITCH, Design, DesignObject, ColorLayer, StitchParams
from embroidery_app.stitch import generators as gen
from embroidery_app.stitch import plan_stitches
from embroidery_app.stitch.planner import classify


# ---------------------------------------------------------------- ingest

def test_raster_extracts_layers_and_disables_background_colour(logo_rgba):
    design, preview = raster_to_design(logo_rgba, RasterOptions(width_mm=90))
    assert abs(design.width_mm - 90) < 0.01
    assert 40 < design.height_mm < 70
    names = [l.name for l in design.layers]
    assert len(design.layers) == 4, names
    white = [l for l in design.layers if l.name == "White"]
    assert white and not white[0].enabled  # ring hole / letter counters = background colour
    # largest colour is sewn first
    areas = [design.layer_area(l.index) for l in design.layers]
    assert areas[0] == max(areas)
    assert preview.shape[2] == 4


def test_raster_fixed_colour_count(logo_rgba):
    design, _ = raster_to_design(logo_rgba, RasterOptions(width_mm=90, num_colors=3, background="none"))
    assert len(design.layers) <= 3


def test_svg_import_knockout_strokes_and_warnings(svg_text):
    design = svg_to_design(svg_text, VectorOptions(width_mm=100))
    assert abs(design.width_mm - 100) < 0.5
    hexes = {l.hex for l in design.layers}
    assert "#ffffff" not in hexes  # canvas background dropped
    assert {"#1144cc", "#dd2222", "#118833", "#000000"} <= hexes
    blue = next(l for l in design.layers if l.hex == "#1144cc")
    blue_area = design.layer_area(blue.index)
    assert blue_area < 40 * 40 * 0.95  # the red circle on top was knocked out
    green = next(l for l in design.layers if l.hex == "#118833")
    assert design.layer_area(green.index) == pytest.approx(35 * 40 - 15 * 20, rel=0.03)  # evenodd hole
    lines = [o for o in design.objects if o.kind == "line"]
    assert lines and lines[0].stroke_width_mm == pytest.approx(1.5, rel=0.05)
    assert any("text" in w for w in design.warnings)


def test_detect_kind():
    assert detect_kind("a.png", b"\x89PNG\r\n\x1a\nxxxx") == "raster"
    assert detect_kind("a.svg", b"<svg></svg>") == "svg"
    assert detect_kind("a.ai", b"%PDF-1.5 ...") == "pdf"
    with pytest.raises(ValueError):
        detect_kind("a.ai", b"%!PS-Adobe-3.0")
    with pytest.raises(ValueError):
        detect_kind("a.emb", b"\x00\x01")


# ---------------------------------------------------------------- stitch generation

def _max_step(points):
    return max(math.dist(a, b) for a, b in zip(points, points[1:]))


def test_tatami_fill_stays_inside_and_covers():
    poly = Polygon([(0, 0), (30, 0), (30, 20), (15, 8), (0, 20)])  # concave -> several chains
    pieces = gen.tatami_fill(poly, 45, 0.4, 3.5, (0, 0))
    pts = [p for piece in pieces for p in piece]
    grown = poly.buffer(0.25)
    assert all(grown.contains(Point(p)) for p in pts)
    assert all(_max_step(piece) <= 7.0 + 1e-6 for piece in pieces)
    # every part of the shape gets needle penetrations nearby
    for probe in [(5, 2), (25, 2), (3, 15), (27, 15), (15, 4)]:
        assert min(math.dist(probe, p) for p in pts) < 2.0


def test_satin_column_alternates_sides():
    bar = box(0, 0, 40, 3)
    pieces = gen.satin_area(bar, 0.4, (0, 0))
    pts = pieces[0]
    assert len(pieces) == 1
    # stitches cross the bar (short direction), consecutive points on opposite edges
    ys = [round(p[1], 3) for p in pts[:10]]
    assert all(abs(a - b) > 2.5 for a, b in zip(ys, ys[1:]))
    assert len(pts) == pytest.approx(40 / 0.4, abs=3)


def test_band_outline_uses_centreline_satin():
    ring = Point(0, 0).buffer(20).difference(Point(0, 0).buffer(17))
    items = classify(DesignObject(0, ring), "auto", StitchParams())
    assert items[0].method == "satin_band"
    pts = gen.satin_band(ring, items[0].centre, 0.4, 0.0, (20, 0))
    radii = [math.hypot(*p) for p in pts]
    assert min(radii) > 16.5 and max(radii) < 20.5  # crossings span the band, never leave it
    assert sum(r > 19 for r in radii) > len(pts) * 0.4 and sum(r < 18 for r in radii) > len(pts) * 0.4


def test_classification():
    params = StitchParams()
    bar = DesignObject(0, box(0, 0, 40, 3))
    square = DesignObject(0, box(0, 0, 30, 30))
    stroke_thin = DesignObject(0, LineString([(0, 0), (30, 0)]), kind="line", stroke_width_mm=0.5)
    stroke_mid = DesignObject(0, LineString([(0, 0), (30, 10)]), kind="line", stroke_width_mm=3)
    assert classify(bar, "auto", params)[0].method == "satin"
    assert classify(square, "auto", params)[0].method == "fill"
    assert classify(stroke_thin, "auto", params)[0].method == "running"
    assert classify(stroke_mid, "auto", params)[0].method == "satin_line"
    assert classify(square, "running", params)[0].method == "outline"


def _design():
    layers = [ColorLayer(0, (200, 0, 0), "Red"), ColorLayer(1, (0, 0, 200), "Blue")]
    objects = [
        DesignObject(0, box(0, 0, 20, 20)),
        DesignObject(0, box(40, 0, 60, 20)),  # far away -> jump + trim
        DesignObject(1, box(0, 30, 60, 33)),  # satin bar
    ]
    return Design(60, 33, layers, objects)


def test_plan_sequence_trims_and_colour_changes():
    plan = plan_stitches(_design(), StitchParams())
    cmds = [op[0] for op in plan.ops]
    assert cmds.count("color_change") == 1
    assert cmds.count("trim") >= 2  # between the two red squares + before colour change (+ end)
    stats = plan.stats()
    assert stats["colors"] == 2 and stats["stitches"] > 500
    # no stitch longer than max (jumps excluded)
    prev = None
    for cmd, x, y, _ in plan.ops:
        if cmd == STITCH and prev is not None:
            assert math.dist(prev, (x, y)) <= 7.0 + 1e-6
        if cmd in ("stitch", "jump"):
            prev = (x, y)


def test_disabled_layer_is_skipped():
    d = _design()
    d.layers[1].enabled = False
    plan = plan_stitches(d, StitchParams())
    assert len(plan.blocks) == 1


# ---------------------------------------------------------------- export

@pytest.mark.parametrize("fmt", ["dst", "exp", "pes", "jef", "vp3", "u01"])
def test_export_roundtrip(fmt, tmp_path):
    plan = plan_stitches(_design(), StitchParams())
    data = export_embroidery(plan, fmt, "test")
    path = tmp_path / f"t.{fmt}"
    path.write_bytes(data)
    pattern = pe.read(str(path))
    assert pattern is not None
    assert pattern.count_stitches() >= plan.stats()["stitches"] * 0.95
    if fmt in ("dst", "exp"):
        # centred on the hoop origin, mm*10 units
        minx, miny, maxx, maxy = pattern.bounds()
        assert abs(minx + maxx) < 5 and abs(miny + maxy) < 5
        assert maxx - minx == pytest.approx(600, abs=15)


def test_dst_has_colour_stops_and_trims(tmp_path):
    plan = plan_stitches(_design(), StitchParams())
    path = tmp_path / "t.dst"
    path.write_bytes(export_embroidery(plan, "dst"))
    pattern = pe.read(str(path))
    commands = [s[2] & pe.COMMAND_MASK for s in pattern.stitches]
    assert commands.count(pe.COLOR_CHANGE) + commands.count(pe.STOP) >= 1
    assert commands.count(pe.TRIM) >= 1
    header = path.read_bytes()[:512]
    assert header.startswith(b"LA:")
