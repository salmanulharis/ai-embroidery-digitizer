"""Guide-line styles: guided satin and Florentine fill, and the per-shape guide line."""

import math

import numpy as np
import pytest
from fastapi.testclient import TestClient
from shapely.geometry import LineString, Point, box

from embroidery_app.api import app
from embroidery_app.models import STITCH, ColorLayer, Design, DesignObject, StitchParams, clean_guide
from embroidery_app.stitch import guided, plan_stitches
from embroidery_app.stitch.planner import classify, describe_objects

BAND = Point(30, 40).buffer(26).difference(Point(30, 40).buffer(18)).intersection(box(0, 0, 60, 30))
ARC = tuple((x, 40 - math.sqrt(22 ** 2 - (x - 30) ** 2)) for x in range(9, 52, 3))
QUIET = StitchParams(tie_stitches=False, optimize=False, pull_compensation_mm=0.0)


def _satin(geom, guide):
    found, satin = guided.guided_satin(geom, list(guide), 0.4, 0.0, 7.0, (0, 0))
    return found, satin


def test_guided_satin_crosses_the_guide_at_right_angles_from_edge_to_edge():
    found, satin = _satin(BAND, ARC)
    pts = np.array(satin[0])
    r = np.hypot(pts[:, 0] - 30, pts[:, 1] - 40)
    assert r.min() == pytest.approx(18, abs=0.3) and r.max() == pytest.approx(26, abs=0.3)  # edge to edge
    # every stitch runs (nearly) through the arc's centre: across the band
    off = []
    for a, b in zip(pts, pts[1:]):
        if math.dist(a, b) < 5:
            continue
        d = np.array(b) - np.array(a)
        to_c = np.array([30, 40]) - np.array(a)
        off.append(abs(d[0] * to_c[1] - d[1] * to_c[0]) / np.linalg.norm(d) / np.linalg.norm(to_c))
    assert np.median(off) < math.sin(math.radians(8))
    assert found and LineString(found[0]).hausdorff_distance(LineString(ARC)) < 4.5  # centre run along the guide


def test_a_short_guide_is_continued_to_the_shape_edges():
    _, satin = _satin(box(0, 0, 40, 6), [(15, 3), (25, 3)])  # drawn only in the middle
    xs = [p[0] for p in satin[0]]
    assert min(xs) < 1.0 and max(xs) > 39.0


def test_wide_guided_satin_is_auto_split():
    _, satin = _satin(box(0, 0, 40, 12), [(0, 6), (40, 6)])
    steps = [math.dist(a, b) for a, b in zip(satin[0], satin[0][1:])]
    assert max(steps) <= 7.0 + 1e-6


def test_florentine_rows_are_copies_of_the_guide():
    sq = box(0, 0, 40, 30)
    wave = [(i, 15 + 5 * math.sin(i / 40 * 2 * math.pi)) for i in range(0, 41, 2)]
    pieces = guided.florentine_fill(sq, wave, 0.4, 3.5, (0, 0))
    pts = np.array([p for piece in pieces for p in piece])
    # each needle point sits on a shifted copy of the drawn line: y - line(x) is a multiple of 0.4 (+0.2)
    gx, gy = np.array(wave).T
    shift = (pts[:, 1] - np.interp(pts[:, 0], gx, gy) - 0.2) / 0.4
    inner = (pts[:, 1] > 1) & (pts[:, 1] < 29)
    assert np.mean(np.abs(shift[inner] - np.round(shift[inner])) < 0.3) > 0.9


def test_florentine_covers_the_whole_shape():
    sq = box(0, 0, 40, 30)
    wave = [(i, 15 + 6 * math.sin(i / 40 * 2 * math.pi)) for i in range(0, 41, 4)]
    pts = np.array([p for piece in guided.florentine_fill(sq, wave, 0.4, 3.5, (0, 0)) for p in piece])
    for x in range(2, 40, 4):  # every column of the shape reaches both edges
        col = pts[np.abs(pts[:, 0] - x) < 2.0]
        assert col[:, 1].min() < 1.5 and col[:, 1].max() > 28.5


def test_without_a_guide_the_shape_is_sewn_the_usual_way():
    assert [it.method for it in classify(DesignObject(0, box(0, 0, 30, 30)), "florentine_fill", QUIET)] == ["fill"]
    assert classify(DesignObject(0, box(0, 0, 40, 3)), "guided_satin", QUIET)[0].method == "satin"
    with_guide = DesignObject(0, BAND, guide=ARC)
    assert [it.method for it in classify(with_guide, "guided_satin", QUIET)] == ["guided_satin"]


def test_guide_lines_are_cleaned_saved_and_reset():
    assert clean_guide([(0, 0)]) is None and clean_guide("nonsense") is None
    assert clean_guide([(0, 0), (1, float("nan"))]) is None
    o = DesignObject(0, BAND, guide=clean_guide(ARC))
    back = DesignObject.from_dict(o.to_dict())
    assert back.guide == o.guide and back.customised
    back.clear_settings()
    assert back.guide is None


def test_guide_line_through_the_api(svg_text):
    client = TestClient(app)
    pid = client.post("/api/projects", files={"file": ("t.svg", svg_text.encode(), "image/svg+xml")}).json()["id"]
    objs = client.post(f"/api/projects/{pid}/extract", json={"options": {"width_mm": 100}}).json()["objects"]
    one = next(o for o in objs if o["kind"] == "area")["id"]
    line = [[10 + i, 20 + (i % 3) * 0.01] for i in range(30)]  # a hand-drawn, slightly wobbly line
    r = client.put(f"/api/projects/{pid}/objects", json={"ids": [one], "stitch_type": "florentine_fill", "guide": line}).json()
    o = next(x for x in r["objects"] if x["id"] == one)
    assert o["method"] == "florentine_fill" and 2 <= len(o["guide"]) < 30  # thinned
    r = client.put(f"/api/projects/{pid}/objects", json={"ids": [one], "guide": "inherit"}).json()
    assert next(x for x in r["objects"] if x["id"] == one)["guide"] is None
    assert client.put(f"/api/projects/{pid}/objects", json={"ids": [one], "guide": [[1, 1]]}).status_code == 400


def test_planned_guided_styles_keep_machine_limits():
    d = Design(70, 45, [ColorLayer(0, (1, 2, 3), "c")], [
        DesignObject(0, BAND, stitch_type="guided_satin", guide=ARC),
        DesignObject(0, box(5, 32, 60, 44), stitch_type="florentine_fill", guide=((5, 40), (30, 34), (60, 40))),
    ])
    plan = plan_stitches(d, StitchParams())
    steps = [math.dist(a[1:3], b[1:3]) for a, b in zip(plan.ops, plan.ops[1:]) if a[0] == b[0] == STITCH]
    assert max(steps) <= 7.0 + 1e-6
    assert not [r for r in plan.risks if r["type"] == "stack"]
    info = describe_objects(d, StitchParams())
    assert info[0]["method"] == "guided_satin" and info[0]["guide"]


def test_carved_fill_puts_needle_points_on_the_drawn_line():
    sq = box(0, 0, 40, 30)
    line = [(0, 5), (40, 25)]
    d = Design(45, 35, [ColorLayer(0, (1, 2, 3), "c")], [DesignObject(0, sq, stitch_type="carved_fill", guide=tuple(line))])
    plan = plan_stitches(d, StitchParams(fill_angle=0.0, vary_fill_angle=False, underlay=False, optimize=False))
    pts = [(x, y) for c, x, y, k in plan.ops if c == STITCH and k == "fill"]
    on_line = [p for p in pts if LineString(line).distance(Point(p)) < 0.05]
    assert len(on_line) > 40  # about one per row the line crosses
    near = [p for p in pts if 0.05 <= LineString(line).distance(Point(p)) < 0.3]
    assert len(near) < len(on_line) / 4  # other points kept clear of the line, so it stands out
    assert [it.method for it in classify(DesignObject(0, sq), "carved_fill", StitchParams())] == ["fill"]
