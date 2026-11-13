"""Small lettering is sewn so the machine can stitch it cleanly: satin that follows each
stroke, widened to a sewable column without closing gaps, sewable dots, no stitches
shorter than the thread can take, locks only at cuts, and a machine check of the rest."""

import math

import numpy as np
import pytest
from shapely.geometry import LineString, Point, box
from shapely.ops import unary_union

from embroidery_app.ai.builtin import clamp_change, review_plan
from embroidery_app.export import worksheet
from embroidery_app.export.preview import plan_preview_json
from embroidery_app.models import STITCH, ColorLayer, Design, DesignObject, StitchParams
from embroidery_app.stitch import plan_stitches
from embroidery_app.stitch import strokes as st
from embroidery_app.stitch.planner import _tie, classify, describe_objects
from embroidery_app.stitch.safety import machine_check, make_safe

BLACK = [ColorLayer(0, (0, 0, 0), "Black")]


def thin_line(coords, width):
    return LineString(coords).buffer(width / 2, cap_style="flat")


def design(*geoms, w=40, h=20):
    return Design(w, h, list(BLACK), [DesignObject(0, g, id=f"s{i}") for i, g in enumerate(geoms)])


def satin_widths(plan, region):
    """Lengths of satin stitches inside ``region`` (each crosses the column)."""
    out, prev = [], None
    for c, x, y, k in plan.ops:
        if c == STITCH and prev is not None and k == "satin" and region.contains(Point(x, y)):
            out.append(math.dist(prev, (x, y)))
        if c in (STITCH, "jump"):
            prev = (x, y)
    return np.array(out)


def stitched_points(plan, kinds=("satin", "running", "fill")):
    return [(x, y) for c, x, y, k in plan.ops if c == STITCH and k in kinds]


# ---------------------------------------------------------------- stroke engine

def test_thin_letter_strokes_get_satin_that_follows_each_stroke():
    letter_t = unary_union([thin_line([(5, 5), (15, 5)], 0.4), thin_line([(10, 5), (10, 15)], 0.4)])
    items = classify(DesignObject(0, letter_t), "auto", StitchParams())
    assert [it.method for it in items] == ["stroke"]
    strokes = st.stroke_centrelines(letter_t)
    assert 2 <= len(strokes) <= 3  # bar (maybe split at the junction) + stem, no fake spurs
    assert sum(s.line.length for s in strokes) == pytest.approx(20, abs=1.5)
    info = describe_objects(design(letter_t), StitchParams())[0]
    assert info["method"] == "stroke" and not info["direction_applies"]


def test_thin_strokes_are_widened_to_a_sewable_column():
    bar = thin_line([(5, 10), (35, 10)], 0.4)  # 0.4 mm stroke: thinner than 40 wt thread can sew
    plan = plan_stitches(design(bar), StitchParams())
    widths = satin_widths(plan, box(8, 8, 32, 12))
    assert len(widths) > 30
    min_column = st.thread_limits(40).min_column_mm  # 1.0 mm for 40 wt
    assert np.median(widths) >= min_column * 0.95  # widened to the minimum column
    assert np.median(widths) <= min_column * 1.2  # ...but not more than needed


def test_widening_never_closes_the_gap_between_letters():
    a = thin_line([(5, 10), (35, 10)], 0.4)
    b = thin_line([(5, 10.9), (35, 10.9)], 0.4)  # 0.5 mm gap between the strokes
    plan = plan_stitches(design(a, b), StitchParams())
    pts = np.array([p for p in stitched_points(plan, ("satin",)) if 8 < p[0] < 32])
    upper = pts[pts[:, 1] < 10.45]
    lower = pts[pts[:, 1] >= 10.45]
    gap = lower[:, 1].min() - upper[:, 1].max()
    assert gap >= st.MIN_GAP_MM * 0.9  # fabric still shows between the two letters


def test_letter_counters_stay_open():
    ring = Point(10, 10).buffer(0.75).difference(Point(10, 10).buffer(0.35))  # tiny "o": 0.4 mm stroke, 0.7 mm counter
    plan = plan_stitches(design(ring), StitchParams())
    pts = stitched_points(plan)
    assert pts
    # the stroke is widened, but the counter keeps at least the protected opening
    assert min(math.dist(p, (10, 10)) for p in pts) >= st.MIN_GAP_MM / 2 - 0.02
    widths = satin_widths(plan, Point(10, 10).buffer(3))
    assert np.median(widths) > 0.5  # wider than the 0.4 mm original


def test_too_thin_strokes_between_neighbours_become_a_triple_run():
    stroke = thin_line([(5, 10), (35, 10)], 0.2)
    ink = st.InkMap([stroke, box(0, 10.25, 40, 12), box(0, 8, 40, 9.75)], 40, 20)  # neighbours 0.15 mm away
    res = st.satin_stroke(st.stroke_centrelines(stroke)[0], stroke, ink, st.thread_limits(40), 0.45, 0.2, 7.0)
    assert res.kind == "running" and len(res.points) > 10
    assert res.narrow_spots  # reported to the machine check


def test_fine_thread_allows_narrower_columns():
    assert st.thread_limits(60).min_column_mm < st.thread_limits(40).min_column_mm
    bar = thin_line([(5, 10), (35, 10)], 0.4)
    w40 = np.median(satin_widths(plan_stitches(design(bar), StitchParams(thread_weight=40)), box(8, 8, 32, 12)))
    w60 = np.median(satin_widths(plan_stitches(design(bar), StitchParams(thread_weight=60)), box(8, 8, 32, 12)))
    assert w60 < w40


def test_wide_shapes_and_explicit_choices_keep_their_methods():
    p = StitchParams()
    assert classify(DesignObject(0, box(0, 0, 40, 3)), "auto", p)[0].method == "satin"  # 3 mm column
    assert classify(DesignObject(0, box(0, 0, 30, 30)), "auto", p)[0].method == "fill"
    thin = thin_line([(0, 0), (20, 0)], 0.5)
    assert classify(DesignObject(0, thin), "satin", p)[0].method == "satin"  # user chose satin
    assert classify(DesignObject(0, thin), "fill", p)[0].method == "fill"
    assert classify(DesignObject(0, thin), "auto", p, satin_angle=30)[0].method == "satin"  # own direction


# ---------------------------------------------------------------- dots

def test_tiny_dots_are_enlarged_to_a_sewable_dot():
    dot = Point(20, 10).buffer(0.15)  # 0.3 mm dot (0.07 mm²)
    items = classify(DesignObject(0, dot), "auto", StitchParams())
    assert [it.method for it in items] == ["dot"]
    plan = plan_stitches(design(dot), StitchParams())
    pts = np.array(stitched_points(plan, ("satin",)))
    assert len(pts) >= 3
    span = max(math.dist(a, b) for a in pts for b in pts)
    assert span >= 0.8 * 0.9
    assert span <= 1.2  # a dot, not a blob


def test_dots_do_not_grow_into_their_neighbours():
    dot = Point(20, 10).buffer(0.15)
    wall = box(0, 10.4, 40, 12)  # 0.25 mm from the dot
    plan = plan_stitches(design(dot, wall), StitchParams())
    dot_pts = [p for p in stitched_points(plan, ("satin",)) if p[1] < 10.3]
    assert dot_pts and max(p[1] for p in dot_pts) < 10.4


# ---------------------------------------------------------------- machine safety

def lettering_design():
    shapes = []
    for i in range(8):  # rows of thin letters, dots and hyphens
        x = 3 + i * 4.5
        shapes.append(unary_union([thin_line([(x, 3), (x, 9)], 0.45), thin_line([(x, 9), (x + 2.5, 9)], 0.45)]))
        shapes.append(Point(x + 1.2, 1.5).buffer(0.2))
        shapes.append(Point(x + 1.2, 12).buffer(1.2).difference(Point(x + 1.2, 12).buffer(0.75)))
    return design(*shapes, w=42, h=16)


@pytest.mark.parametrize("weight", [40, 60])
def test_no_stitch_shorter_than_the_thread_can_take(weight):
    plan = plan_stitches(lettering_design(), StitchParams(thread_weight=weight))
    limit = st.thread_limits(weight).min_stitch_mm * 0.9
    prev, short = None, 0
    for c, x, y, k in plan.ops:
        if c == STITCH and prev is not None and 1e-9 < math.dist(prev, (x, y)) < limit:
            short += 1
        if c in (STITCH, "jump"):
            prev = (x, y)
    assert short == 0
    assert not [r for r in plan.risks if r["type"] in ("short", "stack")]


def test_safety_pass_also_runs_without_smart_reduction():
    plan = plan_stitches(lettering_design(), StitchParams(optimize=False))
    prev = None
    for c, x, y, k in plan.ops:
        if c == STITCH and prev is not None:
            d = math.dist(prev, (x, y))
            assert d == 0 or d >= 0.36 - 1e-9
            assert d <= 7.0 + 1e-9
        if c in (STITCH, "jump"):
            prev = (x, y)


def test_locks_only_where_the_thread_is_cut():
    plan = plan_stitches(lettering_design(), StitchParams())
    ops = plan.ops
    for i, op in enumerate(ops):
        if op[0] == "jump":
            cut_before = any(o[0] in ("trim", "color_change") for o in ops[max(0, i - 2) : i]) or i == 0
            tie_after = i + 1 < len(ops) and ops[i + 1][3] == "tie"
            if tie_after:
                assert cut_before
    trims = sum(1 for o in ops if o[0] == "trim")
    ties = sum(1 for o in ops if o[3] == "tie")
    assert ties <= trims * 8  # at most one tie-in + one tie-off per cut


def test_lock_stitches_follow_the_path_and_are_never_tiny():
    path = [(0, 0), (0.3, 0), (0.6, 0), (2, 0)]
    for at_start in (True, False):
        tie = _tie(path, at_start, min_stitch=0.4)
        seq = ([path[0]] + tie) if at_start else ([path[-1]] + tie)
        assert all(abs(p[1]) < 1e-9 for p in tie)  # on the path
        assert all(math.dist(a, b) >= 0.4 - 1e-9 for a, b in zip(seq, seq[1:]))
    assert _tie([(0, 0), (0.2, 0)], True, min_stitch=0.4) == []  # too short for a lock: none, not stacked


def test_lock_stitches_on_a_zigzag_path_are_long_enough_as_sewn():
    # a satin column 0.4 mm wide: 0.6 mm *along* this path is only ~0.3 mm as the crow flies
    path = [(0.1 * i, 0.4 * (i % 2)) for i in range(40)]
    for at_start in (True, False):
        tie = _tie(path, at_start, min_stitch=0.5)
        assert tie
        seq = ([path[0]] + tie) if at_start else ([path[-1]] + tie)
        assert all(math.dist(a, b) >= 0.5 - 1e-9 for a, b in zip(seq, seq[1:]))


def test_make_safe_keeps_sequence_ends_and_max_length():
    ops = [("jump", 0, 0, ""), ("stitch", 0, 0, "satin"), ("stitch", 0.1, 0, "satin"), ("stitch", 1.0, 0, "satin"),
           ("stitch", 1.1, 0, "satin"), ("trim", 1.1, 0, ""), ("jump", 5, 0, ""), ("stitch", 5, 0, "fill"), ("stitch", 12, 0, "fill"),
           ("stitch", 12.1, 0, "fill"), ("stitch", 19.2, 0, "fill")]
    out = make_safe(ops, 0.4, 7.0)
    xs = [o[1] for o in out if o[0] == STITCH]
    assert xs == [0, 1.1, 5, 12, 12.1, 19.2]  # stacked drops gone, sequence ends kept,
    # and 12.1 stays: without it the next stitch would be longer than 7 mm
    assert [o[0] for o in out].count("trim") == 1 and [o[0] for o in out].count("jump") == 2


def test_machine_check_flags_stacked_and_short_stitches():
    ops = [("jump", 5, 5, "")] + [("stitch", 5 + (i % 2) * 0.05, 5, "satin") for i in range(12)]
    kinds = {r["type"] for r in machine_check(ops, st.thread_limits(40))}
    assert {"stack", "short"} <= kinds


# ---------------------------------------------------------------- settings, preview, review, worksheet

def test_thread_weight_setting_is_validated():
    assert StitchParams.from_dict({"thread_weight": "60"}).thread_weight == 60
    assert StitchParams.from_dict({"thread_weight": 50}).thread_weight == 40
    assert clamp_change("thread_weight", 60) == 60 and clamp_change("thread_weight", 30) is None


def test_preview_and_worksheet_carry_the_machine_check():
    plan = plan_stitches(lettering_design(), StitchParams(thread_weight=60))
    prev = plan_preview_json(plan)
    assert prev["thread_weight"] == 60 and isinstance(prev["risks"], list)
    text = worksheet(plan, "t")
    assert "60 wt" in text and "Machine check" in text


def test_review_suggests_fine_thread_not_extra_width_for_thin_lettering():
    strokes = [thin_line([(2 + i * 0.75, 2), (2 + i * 0.75, 12)], 0.3) for i in range(10)]  # 0.45 mm gaps
    d = design(*strokes, w=12, h=14)
    params = StitchParams()
    plan = plan_stitches(d, params)
    assert any(r["type"] == "thin" for r in plan.risks)
    review = review_plan(plan, d, params)
    assert review["changes"].get("thread_weight") == 60
    assert "pull_compensation_mm" not in review["changes"]
