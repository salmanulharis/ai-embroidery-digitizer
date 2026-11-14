"""Photo stitch: one thread, rows that zigzag wider where the picture is darker."""

import numpy as np
from shapely.geometry import box

from embroidery_app.models import STITCH, ColorLayer, Design, DesignObject, StitchParams
from embroidery_app.stitch import plan_stitches
from embroidery_app.stitch.photo import luminance


def _steps_design():
    # four vertical bands from light to dark grey, side by side (no gaps)
    greys = [(210, 210, 210), (160, 160, 160), (110, 110, 110), (50, 50, 50)]
    layers = [ColorLayer(i, g, f"g{i}") for i, g in enumerate(greys)]
    objs = [DesignObject(i, box(5 + 10 * i, 5, 15 + 10 * i, 35)) for i in range(4)]
    return Design(50, 40, layers, objs)


def _zig_width(plan, x0, x1):
    pts = np.array([(x, y) for c, x, y, k in plan.ops if c == STITCH and x0 < x < x1])
    rows = np.round((pts[:, 1] - 5.2) / 1.0)  # rows start half a gap inside the (0.3 mm widened) picture
    return np.median([np.ptp(pts[rows == r][:, 1]) for r in np.unique(rows) if (rows == r).sum() > 3])


def test_darker_bands_get_wider_zigzags():
    plan = plan_stitches(_steps_design(), StitchParams(photo_stitch=True))
    assert [b.name for b in plan.blocks] == ["Photo stitch"]
    assert plan.blocks[0].rgb == (50, 50, 50)  # sewn in the darkest thread
    widths = [_zig_width(plan, 6 + 10 * i, 14 + 10 * i) for i in range(4)]
    assert all(a < b for a, b in zip(widths, widths[1:])), widths
    assert widths[-1] > 0.8  # the darkest band nearly fills its 1 mm row


def test_photo_stitch_keeps_machine_limits_and_stays_on_the_picture():
    plan = plan_stitches(_steps_design(), StitchParams(photo_stitch=True))
    pts = [(x, y) for c, x, y, k in plan.ops if c == STITCH]
    assert all(4.0 <= x <= 46.0 and 4.0 <= y <= 36.0 for x, y in pts)
    steps = [np.hypot(b[1] - a[1], b[2] - a[2]) for a, b in zip(plan.ops, plan.ops[1:]) if a[0] == b[0] == STITCH]
    assert max(steps) <= 7.0 + 1e-6
    assert not [r for r in plan.risks if r["type"] in ("stack", "short")]


def test_photo_stitch_is_off_by_default_and_rows_follow_the_setting():
    assert not StitchParams().photo_stitch
    dense = plan_stitches(_steps_design(), StitchParams(photo_stitch=True, photo_row_mm=0.8)).stats()["stitches"]
    loose = plan_stitches(_steps_design(), StitchParams(photo_stitch=True, photo_row_mm=1.6)).stats()["stitches"]
    assert dense > 1.5 * loose
    assert StitchParams(photo_row_mm=10).photo_row_mm == 3.0


def test_luminance():
    assert luminance((255, 255, 255)) == 1.0 and luminance((0, 0, 0)) == 0.0


def test_reef_is_denser_where_the_picture_is_darker():
    plan = plan_stitches(_steps_design(), StitchParams(photo_stitch=True, photo_style="reef"))
    assert [b.name for b in plan.blocks] == ["Reef photo stitch"]
    pts = np.array([(x, y) for c, x, y, k in plan.ops if c == STITCH])
    per_band = [((pts[:, 0] > 5 + 10 * i) & (pts[:, 0] < 15 + 10 * i)).sum() for i in range(4)]
    assert per_band[3] > 2 * per_band[0], per_band  # darkest band much denser than the lightest
    steps = [np.hypot(b[1] - a[1], b[2] - a[2]) for a, b in zip(plan.ops, plan.ops[1:]) if a[0] == b[0] == STITCH]
    assert max(steps) <= 7.0 + 1e-6


def test_colour_photo_sews_every_colour_and_mixes_them_at_the_seams():
    plan = plan_stitches(_steps_design(), StitchParams(photo_stitch=True, photo_style="colour"))
    assert [b.name for b in plan.blocks] == ["g0", "g1", "g2", "g3"]
    block, by_colour = 0, {}
    for c, x, y, k in plan.ops:
        if c == "color_change":
            block += 1
        elif c == STITCH:
            by_colour.setdefault(block, []).append(x)
    first = np.array(by_colour[0])
    assert first.max() > 15.2  # crosses its edge at x = 15 into the next colour's band (soft seam)
    assert first.max() < 17.5  # but only a little
    assert StitchParams(photo_style="bogus").photo_style == "rows"
