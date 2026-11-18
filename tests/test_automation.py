"""More automation (docs/AUTOMATION_UX_PLAN.md, batches F-J): the analysis of artwork files and
after a size change, size / merge / order fixes in the Checks, tone-based preset suggestions,
and satin compensation by column width (opt-in)."""

import cv2
import numpy as np
import pytest
from fastapi.testclient import TestClient
from shapely.geometry import LineString, Point, box

from accounts import admin, new_user, real_login  # noqa: F401
from embroidery_app import presets, service, subscriptions
from embroidery_app.ai.builtin import _width_for_thin_lines, analyze_design, analyze_image, measure_design, review_plan
from embroidery_app.api import app
from embroidery_app.ingest.vector import VectorOptions, svg_to_design
from embroidery_app.models import STITCH, ColorLayer, Design, DesignObject, StitchParams
from embroidery_app.stitch import plan_stitches
from embroidery_app.stitch.methods import PULL_SCALE, satin_pull
from embroidery_app.stitch.planner import describe_objects

client = TestClient(app)

# eight 5 x 8 "letters" on one baseline, a tall bar and a 0.6 wide line, on a wide artboard
LETTERS = "".join(f'<rect x="{20 + i * 9}" y="100" width="5" height="8" fill="#123"/>' for i in range(8))
ARTWORK = ('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 400 200">'
           f'<rect x="150" y="10" width="60" height="180" fill="#c00"/>{LETTERS}'
           '<rect x="300" y="20" width="0.6" height="100" fill="#0a0"/></svg>')


def _artwork(width_mm):
    d = svg_to_design(ARTWORK, VectorOptions(width_mm=width_mm))
    d.normalize()
    return d


# --------------------------------------------------------------------------- G: artwork analysis

def test_artwork_is_measured_like_a_picture():
    r = analyze_design(_artwork(60))
    assert r["kind"] == "vector" and r["cleanup"] == "none" and r["extract"] == {"num_colors": 3}
    assert r["text"]["found"] and r["text"]["letter_height_mm"] < 3
    assert r["stitch"]["thread_weight"] == 60  # letters under 5 mm
    assert 0.2 < r["measurements"]["thinnest_line_mm"] < 0.4
    assert r["measurements"]["design_aspect"] == pytest.approx(_artwork(60).width_mm / _artwork(60).height_mm, abs=1e-3)
    assert any("4 mm tall" in n for n in r["notes"]) and any("keeps them sewable" in n for n in r["notes"])
    big = analyze_design(_artwork(150))  # the same artwork larger: no size advice left for the letters
    assert big["text"]["letter_height_mm"] > 4 and not any("4 mm tall" in n for n in big["notes"])


def test_measure_design_without_lettering():
    d = Design(40, 30, [ColorLayer(0, (0, 0, 0), "Black")], [DesignObject(0, box(5, 5, 35, 25))])
    m = measure_design(d)
    assert not m["text"]["found"] and m["thinnest_line_mm"] > 10


def test_analysis_route_for_artwork_and_pictures(logo_png):
    pid = client.post("/api/projects", files={"file": ("a.svg", ARTWORK.encode())}).json()["id"]
    r = client.post(f"/api/projects/{pid}/analysis", json={"width_mm": 60})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["kind"] == "vector" and body["text"]["found"] and "preset" in body
    assert body["preset"] is None or body["preset"]["id"] == "small_text"
    # the AI assistant's picture analysis still refuses artwork files (unchanged)
    assert client.post(f"/api/projects/{pid}/ai/suggest", json={}).status_code == 400
    pic = client.post("/api/projects", files={"file": ("l.png", logo_png)}).json()["id"]
    r = client.post(f"/api/projects/{pic}/analysis", json={"width_mm": 80}).json()
    assert r["source"] == "builtin" and "background" in r["extract"] and "preset" in r


def test_analysis_route_is_not_an_ai_request(logo_png, monkeypatch):
    from embroidery_app import usage

    pic = client.post("/api/projects", files={"file": ("l.png", logo_png)}).json()["id"]
    buckets = []
    real = usage.rate_limit
    monkeypatch.setattr(usage, "rate_limit", lambda uid, b: buckets.append(b) or real(uid, b))
    client.post(f"/api/projects/{pic}/analysis", json={"width_mm": 120})
    assert buckets == ["work"]


# --------------------------------------------------------------------------- H: picture tone + presets

def _picture(fg, bg, alpha=False):
    img = np.zeros((300, 400, 4), np.uint8)
    img[...] = (*bg, 0 if alpha else 255)
    cv2.rectangle(img, (60, 60), (340, 240), (*fg, 255), -1)
    return img


def test_tone_measurements():
    strong = analyze_image(_picture((20, 20, 120), (255, 255, 255)), 80)["measurements"]
    faded = analyze_image(_picture((190, 185, 175), (225, 222, 215)), 80)["measurements"]
    pastel = analyze_image(_picture((170, 210, 240), (255, 255, 255)), 80)["measurements"]
    assert strong["lightness_range"] > 60 and faded["lightness_range"] < presets.FADED_LIGHTNESS_RANGE
    assert pastel["lightness"] > presets.PASTEL_MIN_LIGHTNESS
    assert presets.PASTEL_CHROMA[0] <= pastel["chroma"] <= presets.PASTEL_CHROMA[1]
    # a transparent background: only the design is there, so its spread is not judged
    assert analyze_image(_picture((190, 185, 175), (0, 0, 0), alpha=True), 80)["measurements"]["lightness_range"] is None


@pytest.mark.usefixtures("real_login")
def test_tone_suggests_high_contrast_and_soft_colours():
    a = admin()
    free_user, _ = new_user(a)
    pro_user, _ = new_user(a, plan_id=next(p["id"] for p in subscriptions.list_plans() if p["code"] == "pro"))
    base = {"design_type": "logo", "text": {"found": False}, "extract": {"denoise": 1}, "cleanup": "none"}
    faded = {**base, "measurements": {"lightness_range": 30.0, "lightness": 60.0, "chroma": 4.0}}
    pastel = {**base, "measurements": {"lightness_range": 80.0, "lightness": 82.0, "chroma": 20.0}}
    plain = {**base, "measurements": {"lightness_range": 90.0, "lightness": 40.0, "chroma": 60.0}}
    assert presets.suggest(faded, 100, free_user["id"]) == {"id": "high_contrast", "label": "High contrast (picture)",
                                                             "reason": "the picture looks faded"}
    assert presets.suggest(pastel, 100, pro_user["id"])["id"] == "soft_colours"
    assert presets.suggest(pastel, 100, free_user["id"]) is None  # premium: never offered without the tier
    assert presets.suggest(plain, 100, pro_user["id"]) is None
    photo = {**faded, "design_type": "photo"}  # photos have their own preset
    assert presets.suggest(photo, 100, pro_user["id"])["id"] == "photo"


# --------------------------------------------------------------------------- F: size + keep fixes

def test_cap_height_offers_a_width_that_fits_the_cap():
    d = Design(60, 80, [ColorLayer(0, (200, 20, 20), "red")], [DesignObject(0, box(0, 0, 60, 80))])
    p = StitchParams(fabric="cap")
    plan = plan_stitches(d, p)
    issue = next(i for i in review_plan(plan, d, p)["issues"] if i["code"] == "cap_height")
    w = issue["resize"]["width_mm"]
    assert w == int(w) and w * plan.stats()["height_mm"] / 60 <= 57.0 + 1e-6 and w > 35


def test_thin_lines_width_reaches_the_thread_limit_at_most_three_times():
    lines = [DesignObject(0, box(5 + i * 4, 5, 5.4 + i * 4, 25)) for i in range(5)]
    d = Design(40, 30, [ColorLayer(0, (0, 0, 0), "Black")], lines)
    thin = measure_design(d)["thinnest_line_mm"]
    assert 0.35 < thin < 0.55  # the 0.4 mm lines
    w40 = _width_for_thin_lines(d, StitchParams(thread_weight=40))  # to a 1.0 mm column
    w60 = _width_for_thin_lines(d, StitchParams(thread_weight=60))  # to a 0.6 mm column
    assert w40 % 5 == 0 and w40 == -(-40 * 1.0 / thin // 5) * 5 and w60 == -(-40 * 0.6 / thin // 5) * 5
    hair = Design(40, 30, [ColorLayer(0, (0, 0, 0), "Black")], [DesignObject(0, box(5 + i * 4, 5, 5.15 + i * 4, 25)) for i in range(5)])
    assert _width_for_thin_lines(hair, StitchParams()) <= 40 * 3
    wide = Design(40, 30, [ColorLayer(0, (0, 0, 0), "Black")], [DesignObject(0, box(5, 5, 35, 25))])
    assert _width_for_thin_lines(wide, StitchParams()) is None


def test_checks_carry_the_size_fix(monkeypatch):
    d = Design(60, 80, [ColorLayer(0, (200, 20, 20), "red")], [DesignObject(0, box(0, 0, 60, 80))])
    p = StitchParams(fabric="cap")
    plan = plan_stitches(d, p)
    checks = service._stitch_checks(plan, d, p, plan.stats(), [], [], [], describe_objects(d, p))
    cap = next(c for c in checks if c["code"] == "cap_height")
    assert cap["resize"]["width_mm"] < 60 and cap["actions"] == ["size"]


# --------------------------------------------------------------------------- I: same thread + colour order

GPL = """GIMP Palette
Name: Test Rayon
 40\t 41\t 42\t Black 1000
200\t 30\t 40\t Red 1147
 30\t 60\t200\t Blue 1076
"""


@pytest.fixture
def chart(tmp_path, monkeypatch):
    (tmp_path / "Test Rayon.gpl").write_text(GPL, encoding="utf-8")
    monkeypatch.setenv("THREAD_CHARTS_DIR", str(tmp_path))
    from embroidery_app import threads

    return next(iter(threads.charts()))


def _two_reds():
    layers = [ColorLayer(0, (205, 25, 35), "Red"), ColorLayer(1, (190, 40, 45), "Dark red"), ColorLayer(2, (30, 60, 200), "Blue")]
    objs = [DesignObject(0, box(0, 0, 30, 30)), DesignObject(1, box(32, 0, 40, 8)), DesignObject(2, box(0, 32, 30, 40))]
    return Design(40, 40, layers, objs)


def test_same_thread_check_merges_into_the_heavier_colour(chart):
    d = _two_reds()
    p = StitchParams(thread_chart=chart)
    c = service._same_thread_check(d, p)
    assert c["code"] == "same_thread" and c["merge"]["pairs"] == [{"source": 1, "target": 0}]
    assert "1147" in c["text"] and c["merge"]["label"] == "Merge them"
    assert service._same_thread_check(d, StitchParams()) is None  # no thread brand chosen: nothing to compare
    d.layers[1].enabled = False  # a colour that is not sewn is never a reason
    assert service._same_thread_check(d, p) is None


def _outline_first():
    ring = box(10, 10, 50, 40).difference(box(11.5, 11.5, 48.5, 38.5))
    layers = [ColorLayer(0, (0, 0, 0), "Black"), ColorLayer(1, (200, 20, 20), "Red")]
    return Design(60, 50, layers, [DesignObject(0, ring), DesignObject(1, box(11.5, 11.5, 48.5, 38.5))])


def test_outline_sewn_before_its_fill_is_moved_after_it():
    d, p = _outline_first(), StitchParams()
    c = service._colour_order_check(d, p, describe_objects(d, p))
    assert c["code"] == "colour_order" and c["order"]["layers"] == [1, 0]
    assert c["order"]["label"] == "Sew Black after Red"
    d.layers.reverse()  # already outlines last: nothing to say
    assert service._colour_order_check(d, p, describe_objects(d, p)) is None


def test_colour_order_leaves_unrelated_colours_alone():
    ring = box(10, 10, 50, 40).difference(box(11.5, 11.5, 48.5, 38.5))
    layers = [ColorLayer(0, (0, 0, 0), "Black"), ColorLayer(1, (200, 20, 20), "Red")]
    far = Design(120, 50, layers, [DesignObject(0, ring), DesignObject(1, box(70, 10, 110, 40))])  # does not touch
    p = StitchParams()
    assert service._colour_order_check(far, p, describe_objects(far, p)) is None
    assert service._colour_order_check(_outline_first(), StitchParams(photo_stitch=True), describe_objects(_outline_first(), p)) is None


def test_colour_order_check_end_to_end():
    img = np.full((400, 600, 3), 255, np.uint8)
    cv2.rectangle(img, (60, 60), (540, 340), (30, 30, 200), -1)
    cv2.rectangle(img, (60, 60), (540, 340), (0, 0, 0), 8)
    pid = client.post("/api/projects", files={"file": ("o.png", cv2.imencode(".png", img)[1].tobytes())}).json()["id"]
    design = client.post(f"/api/projects/{pid}/extract", json={"options": {"width_mm": 100}}).json()
    layers = design["design"]["layers"]
    black = next(l for l in layers if sum(int(l["hex"][i:i + 2], 16) for i in (1, 3, 5)) < 150)
    first = [black] + [l for l in layers if l is not black]  # the outline sewn first
    client.put(f"/api/projects/{pid}/layers", json={"layers": [{"index": l["index"]} for l in first]})
    out = client.post(f"/api/projects/{pid}/stitch", json={"params": {}}).json()
    order = next((c for c in out["checks"] if c["code"] == "colour_order"), None)
    assert order and order["order"]["layers"][-1] == black["index"]
    client.put(f"/api/projects/{pid}/layers", json={"layers": [{"index": i} for i in order["order"]["layers"]]})
    again = client.post(f"/api/projects/{pid}/stitch", json={"params": {}}).json()
    assert not any(c["code"] == "colour_order" for c in again["checks"])


def test_a_failing_extra_check_never_breaks_the_stitches(monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("check bug")

    monkeypatch.setattr(service, "_colour_order_check", boom)
    d, p = _outline_first(), StitchParams()
    plan = plan_stitches(d, p)
    checks = service._stitch_checks(plan, d, p, plan.stats(), [], [], [], describe_objects(d, p))
    assert isinstance(checks, list)


# --------------------------------------------------------------------------- J: compensation by width (opt-in)

def test_satin_pull_scales_with_width_only_when_on():
    off, on = StitchParams(pull_compensation_mm=0.2), StitchParams(pull_compensation_mm=0.2, pull_by_width=True)
    assert not StitchParams().pull_by_width
    assert satin_pull(off, 2.0) == satin_pull(off, 9.0) == 0.2
    assert satin_pull(on, 4.0) == 0.2 and satin_pull(on, 2.0) == 0.1 and satin_pull(on, 1.0) == 0.2 * PULL_SCALE[0]
    assert satin_pull(on, 12.0) == 0.2 * PULL_SCALE[1]
    assert satin_pull(StitchParams(pull_compensation_mm=0, pull_by_width=True), 8.0) == 0


def _satin_width(shape, **params):
    d = Design(40, 40, [ColorLayer(0, (0, 0, 0), "Black")], [DesignObject(0, shape)])
    plan = plan_stitches(d, StitchParams(underlay=False, **params))
    xs = [x for c, x, y, _ in plan.ops if c == STITCH]
    return max(xs) - min(xs)


def test_narrow_satin_gets_less_and_wide_satin_more_compensation():
    narrow, wide = box(10, 5, 12.5, 35), box(10, 5, 17, 35)  # 2.5 mm and 7 mm satin columns (8 mm sews as fill)
    assert _satin_width(narrow, pull_by_width=True) < _satin_width(narrow)
    assert _satin_width(wide, pull_by_width=True) > _satin_width(wide)


def test_line_satin_follows_the_stroke_width():
    line = DesignObject(0, LineString([(5, 20), (35, 20)]), kind="line", stroke_width_mm=2.0)
    d = Design(40, 40, [ColorLayer(0, (0, 0, 0), "Black")], [line])
    ys = lambda p: [y for c, x, y, _ in plan_stitches(d, p).ops if c == STITCH]  # noqa: E731
    on, off = ys(StitchParams(underlay=False, pull_by_width=True)), ys(StitchParams(underlay=False))
    assert max(on) - min(on) < max(off) - min(off)


def test_the_option_is_a_preset_key():
    assert presets.KEYS["pull_by_width"].section == "fabric"
    sec, dropped = presets.clean({"fabric": {"pull_by_width": True}})
    assert sec == {"fabric": {"pull_by_width": True}} and not dropped


def test_round_dots_keep_their_size_with_the_option():
    dot = DesignObject(0, Point(20, 20).buffer(0.5))
    d = Design(40, 40, [ColorLayer(0, (0, 0, 0), "Black")], [dot, DesignObject(0, box(2, 2, 10, 10))])
    a = plan_stitches(d, StitchParams()).stats()["stitches"]
    b = plan_stitches(d, StitchParams(pull_by_width=True)).stats()["stitches"]
    assert a > 0 and b > 0
