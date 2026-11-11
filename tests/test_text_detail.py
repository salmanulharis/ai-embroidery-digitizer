"""Regression: small lettering details (dots, hyphens, full stops, letter holes, thin
strokes) must survive picture preparation, while real noise is still removed."""

import cv2
import numpy as np
import pytest
from shapely.geometry import Point, box

import embroidery_app.ingest.raster as R
from embroidery_app.ai.builtin import analyze_image, review_plan
from embroidery_app.geometry import iter_polygons
from embroidery_app.models import ColorLayer, Design, DesignObject, StitchParams
from embroidery_app.stitch import plan_stitches

NAVY_BGR = (86, 22, 16)


def lettering() -> np.ndarray:
    """1200 px navy-on-white logo, anti-aliased: thin text with punctuation, a row of
    0.3-0.6 mm dots (like Arabic dots at 100 mm wide), a hyphen and a ring."""
    img = np.full((700, 1200, 3), 255, np.uint8)
    cv2.circle(img, (600, 200), 150, NAVY_BGR, 10, cv2.LINE_AA)
    cv2.putText(img, "Al-Dossary Sons Cont. Co. Ltd.", (40, 470), cv2.FONT_HERSHEY_SIMPLEX, 1.6, NAVY_BGR, 4, cv2.LINE_AA)
    cv2.putText(img, "o e a o e a", (300, 560), cv2.FONT_HERSHEY_SIMPLEX, 1.4, NAVY_BGR, 4, cv2.LINE_AA)
    for i in range(16):  # dots of 4-7 px diameter (0.3-0.6 mm at 100 mm wide)
        cv2.circle(img, (120 + i * 60, 640), 2 + i % 2 + (i % 3 == 0), NAVY_BGR, -1, cv2.LINE_AA)
    cv2.rectangle(img, (1000, 630), (1018, 634), NAVY_BGR, -1)  # hyphen 1.5 x 0.4 mm
    return cv2.cvtColor(img, cv2.COLOR_BGR2RGBA)


def navy_mask(prev: np.ndarray) -> np.ndarray:
    return (prev[:, :, 3] > 0) & (prev[:, :, :3].astype(int).sum(-1) < 400)


def truth_for(rgba, opts) -> np.ndarray:
    work, _ = R._resample(R._crop_to_foreground(rgba, opts), opts.width_mm)
    return work[:, :, :3].astype(int).sum(-1) < 400


def missing_pieces(truth, navy) -> int:
    n, comp = cv2.connectedComponents(truth.astype(np.uint8), connectivity=8)
    return sum(1 for i in range(1, n) if navy[comp == i].mean() < 0.3)


@pytest.mark.parametrize("denoise", [0, 1, 2, 3])
def test_small_marks_survive_at_every_grain_level(denoise):
    rgba = lettering()
    opts = R.RasterOptions(width_mm=100, denoise=denoise)
    design, prev = R.raster_to_design(rgba, opts)
    truth, navy = truth_for(rgba, opts), navy_mask(prev)
    assert navy.shape == truth.shape
    assert missing_pieces(truth, navy) <= 1  # dots, hyphen, full stops, letter parts
    assert (truth & ~navy).sum() / truth.sum() < 0.03  # thin strokes not eaten


def test_letter_holes_are_kept():
    design, _ = R.raster_to_design(lettering(), R.RasterOptions(width_mm=100))
    navy = {l.index for l in design.layers if sum(l.rgb) < 300}
    holes = sum(len(p.interiors) for o in design.objects if o.layer in navy for p in iter_polygons(o.geometry))
    assert holes >= 12  # ring + o/e/a counters + letters of the text line


def test_anti_aliased_edges_do_not_become_colours():
    design, _ = R.raster_to_design(lettering(), R.RasterOptions(width_mm=100))
    assert len(design.layers) <= 2  # navy (+ white inside the ring)
    s = analyze_image(lettering(), 100)
    assert s["extract"]["num_colors"] <= 2


def test_real_noise_is_still_removed():
    clean = lettering()
    noisy = clean.copy()
    rng = np.random.default_rng(5)
    for _ in range(300):  # faint blotches + dark one-pixel specks, all inside the logo area
        y, x = int(rng.integers(120, 600)), int(rng.integers(150, 1050))
        s = int(rng.integers(195, 235))
        cv2.circle(noisy, (x, y), int(rng.integers(1, 4)), (s, s, s + 10, 255), -1)
    for _ in range(150):
        noisy[int(rng.integers(120, 600)), int(rng.integers(150, 1050)), :3] = (20, 25, 90)
    def navy_pieces(img):
        d, _ = R.raster_to_design(img, R.RasterOptions(width_mm=100))
        navy = {l.index for l in d.layers if sum(l.rgb) < 300}
        return sum(1 for o in d.objects if o.layer in navy)
    assert navy_pieces(noisy) <= navy_pieces(clean) + 1


def test_simplifying_on_purpose_still_works():
    rgba = lettering()
    lost = {}
    for mm in (0.3, 1.0):
        opts = R.RasterOptions(width_mm=100, min_feature_mm=mm)
        _, prev = R.raster_to_design(rgba, opts)
        truth, navy = truth_for(rgba, opts), navy_mask(prev)
        lost[mm] = (truth & ~navy).sum() / truth.sum()
    assert lost[1.0] > lost[0.3] + 0.05  # a high "smallest detail" still simplifies


def test_grain_cleanup_keeps_two_pixel_lines():
    img = np.full((60, 120, 3), 255, np.uint8)
    img[29:31, 10:110] = (16, 22, 86)  # 2 px line
    out = R._denoise(img, 3)
    assert (out[25:35, 20:100].astype(int).sum(-1) < 400).sum() >= 140


def test_small_dots_are_stitched_by_default():
    dot = Point(5, 5).buffer(0.3)  # 0.28 mm² dot (e.g. an Arabic dot at 100 mm)
    design = Design(20, 10, [ColorLayer(0, (0, 0, 0), "Black")], [DesignObject(0, box(8, 0, 20, 10)), DesignObject(0, dot)])
    plan = plan_stitches(design, StitchParams())
    assert any(c == "stitch" and dot.buffer(0.3).contains(Point(x, y)) for c, x, y, _ in plan.ops)


def test_review_never_suggests_deleting_small_marks():
    shapes = [DesignObject(0, Point(i * 2, 0).buffer(0.4)) for i in range(10)] + [DesignObject(0, box(0, 5, 30, 25))]
    design = Design(30, 25, [ColorLayer(0, (0, 0, 0), "Black")], shapes)
    params = StitchParams(min_object_area_mm2=1.0)
    plan = plan_stitches(design, params)
    review = review_plan(plan, design, params)
    assert "min_object_area_mm2" not in review["changes"]
    assert any("not stitched" in i["title"] for i in review["issues"])
