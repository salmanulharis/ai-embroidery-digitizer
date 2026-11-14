"""Decorative styles: motif outline and fills, pattern fill, jagged fill, hand stitch,
raised satin and applique (with its machine stop)."""

import math
import os
import tempfile

import numpy as np
import pystitch as pe
import pytest
from fastapi.testclient import TestClient
from shapely.geometry import LineString, Point, box

from embroidery_app.api import app
from embroidery_app.export import export_embroidery, worksheet
from embroidery_app.models import STITCH, STOP, ColorLayer, Design, DesignObject, StitchParams
from embroidery_app.stitch import decorative as dec
from embroidery_app.stitch import plan_stitches
from embroidery_app.stitch.planner import classify

SQUARE = box(5, 5, 35, 35)
QUIET = dict(tie_stitches=False, optimize=False)


def _plan(geom, style, kind="area", **params):
    obj = DesignObject(0, geom, kind=kind, stroke_width_mm=0.5 if kind == "line" else 0.0, stitch_type=style)
    return plan_stitches(Design(50, 45, [ColorLayer(0, (170, 40, 90), "c")], [obj]), StitchParams(**{**QUIET, **params}))


def _stitches(plan, kinds=None):
    return [(x, y) for c, x, y, k in plan.ops if c == STITCH and (kinds is None or k in kinds)]


def test_motif_library_motifs_chain_end_to_end():
    lib = dec.motifs()
    assert {"wave", "scallop", "loop", "leaf", "diamond", "tooth"} <= set(lib)
    for name in lib:
        pts = dec.motif_points(name)
        assert tuple(pts[0]) == (0, 0) and tuple(pts[-1]) == (1, 0), name
        assert np.abs(pts[:, 1]).max() <= 0.5


def test_motif_run_repeats_the_motif_along_the_path():
    pts = np.array(dec.motif_along([(0, 0), (60, 0)], 6.0, "wave"))
    assert pts[:, 1].max() == pytest.approx(2.4, abs=0.05) and pts[:, 1].min() == pytest.approx(-2.4, abs=0.05)
    crossings = np.sum(np.diff(np.sign(np.round(pts[:, 1], 6))) != 0)
    assert crossings >= 18  # ten waves, each crossing the line twice


@pytest.mark.parametrize("style", ["motif_run", "hand_stitch"])
def test_path_styles_on_lines_and_outlines(style):
    for geom, kind in ((LineString([(5, 20), (45, 20)]), "line"), (SQUARE, "area")):
        assert [it.method for it in classify(DesignObject(0, geom, kind=kind), style, StitchParams())] == [style]
        pts = _stitches(_plan(geom, style, kind))
        path = geom if kind == "line" else geom.exterior
        assert len(pts) > 10
        assert max(path.distance(Point(p)) for p in pts) <= StitchParams().motif_size_mm / 2 + 0.1


def test_hand_stitch_alternates_long_and_tiny_stitches():
    pts = dec.hand_stitch(LineString([(0, 0), (40, 0)]), (0, 0), 2.5)[0]
    steps = np.array([math.dist(a, b) for a, b in zip(pts, pts[1:])])
    assert np.all(steps[1::2] < 0.8) and np.all(steps[0:-1:2] > 1.5)  # (the last one is what is left of the line)
    assert len(set(np.round(steps[0::2], 2))) > 5  # uneven, like by hand


@pytest.mark.parametrize("style", ["motif_fill", "paired_motif_fill"])
def test_motif_fills_cover_the_shape_with_rows_of_motifs(style):
    pts = np.array(_stitches(_plan(SQUARE, style, fill_angle=0.0, vary_fill_angle=False)))
    assert all(SQUARE.buffer(0.3).contains(Point(p)) for p in pts)
    rows = np.unique(np.round((pts[:, 1] - 5) / StitchParams().motif_size_mm))
    assert len(rows) >= 5


def test_paired_rows_are_mirrored():
    a = dec.motif_fill(SQUARE, 0.0, 6.0, "scallop", (5, 5), paired=False)
    b = dec.motif_fill(SQUARE, 0.0, 6.0, "scallop", (5, 5), paired=True)
    assert a != b


@pytest.mark.parametrize("pattern", ["diamonds", "squares", "bricks", "scales"])
def test_pattern_fill_puts_needle_points_on_the_pattern(pattern):
    row_xs = dec.pattern_xs(pattern, 8.0, 3.5, 0.4)
    for y in np.arange(0.2, 16, 0.4):
        xs = [0.0, *row_xs(float(y), 0.0, 40.0), 40.0]
        steps = np.diff(xs)
        assert steps.min() >= 0.5 - 1e-9 and steps.max() <= dec.PATTERN_MAX_STITCH_MM + 1e-9
    if pattern == "diamonds":  # the row at y crosses the diamond lines at y and 8 - y
        xs = row_xs(1.0, 0.0, 40.0)
        assert any(abs(x - 1.0) < 1e-6 for x in xs) and any(abs(x - 7.0) < 1e-6 for x in xs)


def test_jagged_fill_edges_wander_but_the_middle_is_full():
    pts = np.array(_stitches(_plan(SQUARE, "jagged_fill", fill_angle=0.0, vary_fill_angle=False), ("fill",)))
    left = pts[:, 0].reshape(-1)
    assert left.min() < 5 - 0.3  # some rows reach out past the edge
    ends = [p for p in pts if p[0] < 8]
    assert np.ptp([p[0] for p in ends]) > 1.0  # ragged, not a straight edge


def test_raised_satin_pads_narrow_shapes_and_sews_wide_ones_normally():
    bar = box(5, 10, 40, 15)
    plan = _plan(bar, "raised_satin")
    assert len(_stitches(plan, ("underlay",))) > 0.8 * len(_stitches(plan, ("satin",)))  # two padding layers
    satin = np.array(_stitches(plan, ("satin",)))
    assert len(satin) / 35 == pytest.approx(1 / 0.30, rel=0.1)  # 0.30 mm between stitches
    wide = classify(DesignObject(0, SQUARE), "raised_satin", StitchParams())
    assert [it.method for it in wide] == ["fill"]


def test_applique_sews_placement_stop_tack_cover():
    plan = _plan(SQUARE, "applique")
    cmds = [op[0] for op in plan.ops]
    assert cmds.count(STOP) == 1
    stop = cmds.index(STOP)
    before = [op for op in plan.ops[:stop] if op[0] == STITCH]
    after = [op for op in plan.ops[stop:] if op[0] == STITCH]
    assert before and all(SQUARE.exterior.distance(Point(op[1:3])) < 0.05 for op in before)  # placement line
    assert {op[3] for op in after} == {"running", "satin"}  # tack-down, then cover
    cover = np.array([op[1:3] for op in after if op[3] == "satin"])
    reach = np.array([SQUARE.exterior.distance(Point(p)) for p in cover])
    half = dec.APPLIQUE_COVER_MM / 2 + StitchParams().pull_compensation_mm
    assert reach.max() <= half * math.sqrt(2) + 0.1  # at a corner the overlapping pieces reach diagonally
    threads = [LineString([a, b]) for a, b in zip(cover, cover[1:])]
    for corner in SQUARE.exterior.coords[:-1]:  # the satin thread passes over every corner
        assert min(t.distance(Point(corner)) for t in threads) < 0.2
    assert plan.stats()["stops"] == 1
    assert "Stops: 1" in worksheet(plan, "patch")


def test_the_stop_reaches_the_machine_file():
    plan = _plan(SQUARE, "applique")
    path = os.path.join(tempfile.mkdtemp(), "patch.pes")
    with open(path, "wb") as f:
        f.write(export_embroidery(plan, "pes"))
    assert pe.read(path).count_stitch_commands(pe.STOP) + pe.read(path).count_stitch_commands(pe.COLOR_CHANGE) >= 1


def test_config_lists_motifs():
    r = TestClient(app).get("/api/config").json()
    assert ["wave", "Wave"] in r["motifs"]
    styles = dict(r["stitch_styles"])
    assert {"motif_fill", "pattern_fill", "applique", "raised_satin"} <= set(styles)
