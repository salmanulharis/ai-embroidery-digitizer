"""Photo stitch: a picture sewn as a texture whose density follows its shading.

Three styles (StitchParams.photo_style):

  rows    (Wilcom: Photo Flash) one dark thread in rows; each row zigzags as wide as
          the picture is dark at that spot, a running stitch where it is light
  reef    (Wilcom: Reef PhotoStitch) one dark thread as an open meander that never
          crosses itself; its loops are tight where the picture is dark, wide where it
          is light, and absent on light background
  colour  (Wilcom: Color PhotoStitch) rows in every thread colour of the design; a
          colour's zigzag is as wide as that colour is present, with soft edges, so
          neighbouring colours share the rows where they meet and mix

The shading comes from the design itself: every shape's thread colour is a shade of the
picture, so extracting a photo with several colours (e.g. 8) gives the tones - no access
to the original image is needed and the result follows any edits made to the design.
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
EDGE_SOFT_MM = 1.0  # colour style: colours fade into each other over about 2 x this
REEF_MIN_MM, REEF_MAX_MM = 0.9, 3.5  # reef: loop spacing in the darkest / lightest tones
SEED = 11
PHOTO_STYLES = ("rows", "reef", "colour")


def luminance(rgb) -> float:
    r, g, b = rgb
    return (0.299 * r + 0.587 * g + 0.114 * b) / 255.0


def photo_blocks(design: Design, params: StitchParams) -> list[ColorBlock]:
    """The colour blocks of the photo stitch in the chosen style."""
    if params.photo_style == "colour":
        return _colour_blocks(design, params)
    block = _reef_block(design, params) if params.photo_style == "reef" else photo_block(design, params)
    return [block] if block.runs else []


def _areas(design: Design) -> list[tuple]:
    on = {l.index for l in design.layers if l.enabled}
    return [(o.geometry, o.layer) for o in design.objects if o.kind == "area" and o.enabled and o.layer in on]


def _contrast(dark: np.ndarray) -> np.ndarray:
    """Auto contrast: the lightest shade thin (MIN_LEVEL), the darkest full."""
    lo, hi = dark.min(), dark.max()
    if hi - lo > 0.05:
        return MIN_LEVEL + (1.0 - MIN_LEVEL) * (dark - lo) / (hi - lo)
    return dark


def _darkest(design: Design):
    layers = [l for l in design.layers if l.enabled]
    return min(layers, key=lambda l: luminance(l.rgb)) if layers else None


# --------------------------------------------------------------------------- rows (Photo Flash)

def photo_block(design: Design, params: StitchParams) -> ColorBlock:
    """One colour block (the darkest thread of the design) sewing the whole picture."""
    areas = _areas(design)
    darkest = _darkest(design)
    block = ColorBlock(rgb=darkest.rgb if darkest else (0, 0, 0), name="Photo stitch")
    if not areas:
        return block
    angle = _angle(params)
    shapes = [rotate(g, -angle, origin=(0, 0)) for g, _ in areas]
    dark = _contrast(np.array([1.0 - luminance(design.layer(i).rgb) for _, i in areas]))
    extent = shapely.union_all(shapes).buffer(0.3)  # bridges hairline seams between shades
    for p in shapes:
        shapely.prepare(p)

    def level(xs, ys):
        out = np.full_like(xs, -1.0)
        for p, d in zip(shapes, dark):  # shapes do not overlap
            out = np.where(shapely.contains_xy(p, xs, ys), np.maximum(out, d), out)
        return out

    pieces = _rows(extent, level, params.photo_row_mm, 0.0, light="run")
    block.runs = [Run("fill", p, -1) for p in _back(_link(pieces, extent), angle)]
    return block


def _angle(params: StitchParams) -> float:
    return params.fill_angle if not params.vary_fill_angle else 0.0


def _rows(extent, level_fn, gap: float, shift: float, light: str) -> list[list[Pt]]:
    """Rows across ``extent`` (rotated frame, rows along x), ``gap`` apart, the first one
    ``shift`` further down. ``level_fn(xs, ys)`` gives the zigzag width share (-1 where
    no shape decides: a seam). ``light``: "run" sews a running stitch where light,
    "skip" leaves it out."""
    minx, miny, maxx, maxy = extent.bounds
    pieces: list[list[Pt]] = []
    row = 0
    y = miny + gap / 2 + shift
    shapely.prepare(extent)
    while y < maxy:
        xs = np.arange(minx, maxx + PITCH_MM, PITCH_MM)
        ys = np.full_like(xs, y)
        covered = shapely.contains_xy(extent, xs, ys)
        lv = _fill_seams(level_fn(xs, ys), covered)
        pieces += _row(xs, y, lv, covered, gap, row, light)
        y += gap
        row += 1
    return pieces


def _link(pieces: list[list[Pt]], extent) -> list[list[Pt]]:
    """The next row starts near where this one ended: step over instead of cutting."""
    linked: list[list[Pt]] = []
    for piece in pieces:
        if (linked and len(piece) >= 2 and math.dist(linked[-1][-1], piece[0]) <= LINK_MAX_MM
                and extent.contains(LineString([linked[-1][-1], piece[0]]))):
            linked[-1].extend(piece)
        elif len(piece) >= 2:
            linked.append(list(piece))
    return linked


def _back(pieces: list[list[Pt]], angle: float) -> list[list[Pt]]:
    """Back to the design's orientation."""
    return [[(float(x), float(y)) for x, y in rotate(LineString(p), angle, origin=(0, 0)).coords] for p in pieces if len(p) >= 2]


def _fill_seams(level: np.ndarray, covered: np.ndarray) -> np.ndarray:
    """Points on a seam between two shades (inside the picture, in no shape) take the
    level of the nearest point along the row that has one."""
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


def _row(xs: np.ndarray, y: float, level: np.ndarray, covered: np.ndarray, gap: float, row: int,
         light: str = "run") -> list[list[Pt]]:
    """One row, left to right on even rows and back on odd ones: zigzag where the level
    is high, a running stitch (or nothing) where it is low, a break off the picture."""
    order = range(len(xs)) if row % 2 == 0 else range(len(xs) - 1, -1, -1)
    pieces: list[list[Pt]] = []
    cur: list[Pt] = []
    side = 1
    last_run_x = None
    for i in order:
        d = float(level[i])
        if not covered[i] or (light == "skip" and d < LIGHT):
            if len(cur) >= 2:
                pieces.append(cur)
            cur, last_run_x = [], None
            continue
        x = float(xs[i])
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


# --------------------------------------------------------------------------- colour (Color PhotoStitch)

def _colour_blocks(design: Design, params: StitchParams) -> list[ColorBlock]:
    """Rows in every colour: a colour's zigzag is as wide as the colour is present.
    Presence is soft at the edges (signed distance over EDGE_SOFT_MM), so where two
    colours meet each takes part of the rows and they mix instead of butting."""
    areas = _areas(design)
    if not areas:
        return []
    angle = _angle(params)
    gap = params.photo_row_mm
    layers = [l for l in design.layers if l.enabled and any(i == l.index for _, i in areas)]
    regions = {l.index: rotate(shapely.union_all([g for g, i in areas if i == l.index]), -angle, origin=(0, 0)) for l in layers}
    extent = shapely.union_all(list(regions.values())).buffer(0.3)
    for r in regions.values():
        shapely.prepare(r)
    blocks: list[ColorBlock] = []
    for n, layer in enumerate(layers):
        region = regions[layer.index]
        edge = region.boundary

        def presence(xs, ys, region=region, edge=edge):
            pts = shapely.points(xs, ys)
            d = shapely.distance(edge, pts)
            signed = np.where(shapely.contains_xy(region, xs, ys), d, -d)
            return np.clip(0.5 + signed / (2 * EDGE_SOFT_MM), 0.0, 1.0)

        # each colour's rows sit a little lower than the previous colour's: they interleave
        pieces = _rows(extent, presence, gap, gap * n / max(len(layers), 1), light="skip")
        block = ColorBlock(rgb=layer.rgb, name=layer.name)
        block.runs = [Run("fill", p, -1) for p in _back(_link(pieces, extent.buffer(-0.05)), angle)]
        if block.runs:
            blocks.append(block)
    return blocks


# --------------------------------------------------------------------------- reef (Reef PhotoStitch)

def _reef_block(design: Design, params: StitchParams) -> ColorBlock:
    """An open meander in one dark thread whose loops are tight where the picture is
    dark and wide where it is light: points are scattered with a density that follows
    the darkness, joined into a random spanning tree, and the tree's outline is sewn -
    one line per connected patch that never crosses itself."""
    from .fills import _even, _spanning_tree, _tree_loop  # noqa: PLC0415

    areas = _areas(design)
    darkest = _darkest(design)
    block = ColorBlock(rgb=darkest.rgb if darkest else (0, 0, 0), name="Reef photo stitch")
    if not areas:
        return block
    shapes = [g for g, _ in areas]
    dark = _contrast(np.array([1.0 - luminance(design.layer(i).rgb) for _, i in areas]))
    extent = shapely.union_all(shapes)
    for p in shapes:
        shapely.prepare(p)
    rng = np.random.default_rng(SEED)
    minx, miny, maxx, maxy = extent.bounds
    # jittered grid at the finest spacing, thinned so the density follows the darkness
    gx, gy = np.meshgrid(np.arange(minx, maxx, REEF_MIN_MM), np.arange(miny, maxy, REEF_MIN_MM))
    xs = (gx + rng.uniform(0, REEF_MIN_MM, gx.shape)).ravel()
    ys = (gy + rng.uniform(0, REEF_MIN_MM, gy.shape)).ravel()
    level = np.full_like(xs, -1.0)
    for p, d in zip(shapes, dark):
        level = np.where(shapely.contains_xy(p, xs, ys), np.maximum(level, d), level)
    radius = REEF_MIN_MM + (REEF_MAX_MM - REEF_MIN_MM) * (1.0 - np.clip(level, 0, 1))
    keep = (level >= LIGHT) & (rng.uniform(0, 1, xs.shape) < (REEF_MIN_MM / radius) ** 2)
    pts, rad = np.stack([xs[keep], ys[keep]], axis=1), radius[keep]
    if len(pts) < 3:
        return block
    tri = shapely.delaunay_triangles(shapely.multipoints(pts), only_edges=True)
    index = {(round(x, 6), round(y, 6)): i for i, (x, y) in enumerate(pts)}
    inside = extent.buffer(0.2)
    edges = []
    for e in getattr(tri, "geoms", []):
        a, b = e.coords[0], e.coords[-1]
        i, j = index.get((round(a[0], 6), round(a[1], 6))), index.get((round(b[0], 6), round(b[1], 6)))
        if i is None or j is None:
            continue
        if math.dist(a, b) <= 1.8 * max(rad[i], rad[j]) and inside.contains(e):
            edges.append((i, j))
    tree = _spanning_tree(len(pts), edges, rng)
    loops = _tree_loop([(tuple(pts[a]), tuple(pts[b])) for a, b in tree], REEF_MIN_MM * 0.25)
    pieces = [_even(loop, 1.5) for loop in loops if len(loop) >= 4]
    block.runs = [Run("fill", p, -1) for p in pieces if len(p) >= 2]
    return block
