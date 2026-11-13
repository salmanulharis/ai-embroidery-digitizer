"""Photo stitch (Wilcom: Photo Flash): a picture sewn in one dark thread, as rows.

Each row zigzags across its own band; the zigzag is as wide as the picture is dark
at that spot, so dark areas fill in and light areas stay open (a running stitch
along the row, or nothing on the background). The brightness comes from the design
itself: every shape's thread colour is a shade of the picture, so extracting a photo
with several colours (e.g. 8) gives the shading - no access to the original image is
needed and the result follows any edits made to the design.
"""

from __future__ import annotations

import math

import numpy as np
import shapely
from shapely.affinity import rotate
from shapely.geometry import LineString

from ..geometry import Pt
from ..models import ColorBlock, Design, Run, StitchParams

PITCH_MM = 0.6  # distance between zigzag points along a row
LIGHT = 0.12  # darkness below this: no zigzag, just a running stitch along the row
RUN_STEP_MM = 3.0  # running stitch through light areas
COVER = 0.95  # the darkest zigzag spans this share of the row gap
LINK_MAX_MM = 6.0  # rows closer than this at their ends are joined by one or two stitches
MIN_LEVEL = 0.15  # after auto contrast the lightest shade of the picture keeps a thin zigzag


def luminance(rgb) -> float:
    r, g, b = rgb
    return (0.299 * r + 0.587 * g + 0.114 * b) / 255.0


def photo_block(design: Design, params: StitchParams) -> ColorBlock:
    """One colour block (the darkest thread of the design) sewing the whole picture."""
    layers = [l for l in design.layers if l.enabled]
    areas = [(o.geometry, 1.0 - luminance(design.layer(o.layer).rgb)) for o in design.objects
             if o.kind == "area" and o.enabled and o.layer in {l.index for l in layers}]
    darkest = min(layers, key=lambda l: luminance(l.rgb)) if layers else None
    block = ColorBlock(rgb=darkest.rgb if darkest else (0, 0, 0), name="Photo stitch")
    if not areas:
        return block
    angle = params.fill_angle if not params.vary_fill_angle else 0.0
    gap = params.photo_row_mm
    shapes = [rotate(g, -angle, origin=(0, 0)) for g, _ in areas]
    dark = np.array([d for _, d in areas])
    lo, hi = dark.min(), dark.max()
    if hi - lo > 0.05:  # auto contrast: the lightest shade thin, the darkest full width
        dark = MIN_LEVEL + (1.0 - MIN_LEVEL) * (dark - lo) / (hi - lo)
    extent = shapely.union_all(shapes).buffer(0.3)  # bridges hairline seams between shades
    minx, miny, maxx, maxy = extent.bounds
    for p in shapes:
        shapely.prepare(p)
    pieces: list[list[Pt]] = []
    row = 0
    y = miny + gap / 2
    while y < maxy:
        xs = np.arange(minx, maxx + PITCH_MM, PITCH_MM)
        ys = np.full_like(xs, y)
        level = np.full_like(xs, -1.0)
        for p, d in zip(shapes, dark):  # darkness of the shape under each point (shapes do not overlap)
            inside = shapely.contains_xy(p, xs, ys)
            level = np.where(inside, np.maximum(level, d), level)
        covered = shapely.contains_xy(extent, xs, ys)
        level = _fill_seams(level, covered)
        pieces += _row(xs, y, level, covered, gap, row)
        y += gap
        row += 1
    linked: list[list[Pt]] = []
    for piece in pieces:  # the next row starts near where this one ended: step over instead of cutting
        if (linked and len(piece) >= 2 and math.dist(linked[-1][-1], piece[0]) <= LINK_MAX_MM
                and extent.contains(LineString([linked[-1][-1], piece[0]]))):
            linked[-1].extend(piece)
        elif len(piece) >= 2:
            linked.append(list(piece))
    out = []
    for piece in linked:  # back to the design's orientation
        out.append([(float(x), float(yy)) for x, yy in rotate(LineString(piece), angle, origin=(0, 0)).coords])
    block.runs = [Run("fill", p, -1) for p in out]
    return block


def _fill_seams(level: np.ndarray, covered: np.ndarray) -> np.ndarray:
    """Points on a seam between two shades (inside the picture, in no shape) take the
    darkness of the nearest point along the row that has one."""
    known = np.flatnonzero(level >= 0)
    out = np.where(level >= 0, level, 0.0)
    if len(known) == 0:
        return out
    gaps = np.flatnonzero((level < 0) & covered)
    if len(gaps):
        nearest = known[np.clip(np.searchsorted(known, gaps), 0, len(known) - 1)]
        before = known[np.clip(np.searchsorted(known, gaps) - 1, 0, len(known) - 1)]
        pick = np.where(np.abs(before - gaps) < np.abs(nearest - gaps), before, nearest)
        out[gaps] = level[pick]
    return out


def _row(xs: np.ndarray, y: float, level: np.ndarray, covered: np.ndarray, gap: float, row: int) -> list[list[Pt]]:
    """One row, left to right on even rows and back on odd ones: zigzag where dark,
    running stitch where light, a break where there is no picture."""
    order = range(len(xs)) if row % 2 == 0 else range(len(xs) - 1, -1, -1)
    pieces: list[list[Pt]] = []
    cur: list[Pt] = []
    side = 1
    last_run_x = None
    for i in order:
        if not covered[i]:
            if len(cur) >= 2:
                pieces.append(cur)
            cur, last_run_x = [], None
            continue
        x = float(xs[i])
        d = float(level[i])
        if d < LIGHT:  # light: a plain running stitch
            if last_run_x is None or abs(x - last_run_x) >= RUN_STEP_MM:
                cur.append((x, y))
                last_run_x = x
            continue
        half = d * gap * COVER / 2
        cur.append((x, y + side * half))
        side = -side
        last_run_x = x
    if len(cur) >= 2:
        pieces.append(cur)
    return pieces

