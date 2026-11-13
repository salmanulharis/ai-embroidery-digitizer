"""Machine files never hold a move longer than the format can encode, and the
planner never hands the writer a stitch longer than the maximum.

* Stitches: ``safety.cap_stitch_length`` is the planner's last step. PES, for one,
  stores a long stitch unsplit, so the guarantee must come from the planner.
* Jumps: pystitch's encoder splits them per format. VP3 has no jump command: a long
  move is stored as one move, which machines treat as a trim (moves of 25.5 mm and
  more), so VP3 is checked for reaching the far end only.
"""

import math
import os
import tempfile

import pystitch as pe
import pytest

from embroidery_app.export.embroidery import FORMATS, export_embroidery
from embroidery_app.models import COLOR_CHANGE, JUMP, STITCH, TRIM, ColorBlock, StitchPlan
from embroidery_app.stitch.safety import cap_stitch_length

# longest single move each format can hold, in 0.1 mm (None: no jump command)
FORMAT_MAX = {"dst": 121, "exp": 127, "u01": 127, "jef": 127, "pes": 2047, "vp3": None}


def _long_jumps_plan() -> StitchPlan:
    plan = StitchPlan(200, 200, [ColorBlock((255, 0, 0), "red"), ColorBlock((0, 0, 255), "blue")])
    plan.ops = [
        (STITCH, 0, 0, "fill"), (STITCH, 5, 0, "fill"),
        (TRIM, 5, 0, ""), (JUMP, 150, 90, ""),  # 175 mm jump
        (STITCH, 150, 90, "fill"), (STITCH, 156, 94, "fill"),
        (TRIM, 156, 94, ""), (COLOR_CHANGE, 156, 94, ""),
        (JUMP, 10, 180, ""), (STITCH, 10, 180, "fill"), (STITCH, 16, 180, "fill"),
    ]
    return plan


def test_every_export_format_is_covered():
    assert sorted(FORMAT_MAX) == sorted(FORMATS)


@pytest.mark.parametrize("fmt", sorted(FORMAT_MAX))
def test_jumps_are_split_to_the_format_limit(fmt):
    path = os.path.join(tempfile.mkdtemp(), f"design.{fmt}")
    with open(path, "wb") as f:
        f.write(export_embroidery(_long_jumps_plan(), fmt))
    back = pe.read(path)

    longest = 0.0
    prev = None
    for x, y, c in back.stitches:
        if c & pe.COMMAND_MASK not in (pe.STITCH, pe.JUMP):
            continue
        if prev is not None:
            longest = max(longest, abs(x - prev[0]), abs(y - prev[1]))
        prev = (x, y)

    if FORMAT_MAX[fmt] is not None:
        assert longest <= FORMAT_MAX[fmt]
    assert back.count_stitch_commands(pe.TRIM) >= 2  # both trims survive (JEF needs trims=True)
    xs = [x for x, y, c in back.stitches if c & pe.COMMAND_MASK == pe.STITCH]
    assert max(xs) - min(xs) == pytest.approx(1560, abs=2)  # far end reached: nothing lost


def test_long_stitches_are_split_but_jumps_are_not():
    ops = [
        (STITCH, 0, 0, "fill"), (STITCH, 20, 0, "fill"),  # 20 mm stitch -> 3 x 6.67 mm
        (JUMP, 60, 0, ""),  # jumps are left to the writer
        (STITCH, 60, 0, "fill"), (STITCH, 63, 4, "fill"),  # 5 mm: unchanged
    ]
    out = cap_stitch_length(ops, 7.0)
    assert [op[0] for op in out] == [STITCH, STITCH, STITCH, STITCH, JUMP, STITCH, STITCH]
    xs = [round(op[1], 2) for op in out[:4]]
    assert xs == [0, 6.67, 13.33, 20]
    assert out[1][3] == "fill"
    steps = [math.dist(a[1:3], b[1:3]) for a, b in zip(out, out[1:]) if b[0] == STITCH and a[0] == STITCH]
    assert max(steps) <= 7.0
