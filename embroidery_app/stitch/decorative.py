"""Decorative stitch styles (Phase 3), chosen for a colour or a shape.

Along a path (a line, or a filled shape's outline):
  motif_run       a chain of small motifs (wave, scallop, loop, leaf, diamond, saw
                  tooth) following the path (Wilcom: Motif Run)
  hand_stitch     running stitch that looks sewn by hand: uneven long stitches with
                  a tiny stitch between them and a slight wobble (Wilcom: Hand Stitch)

Filling a shape:
  motif_fill        rows of motifs across the shape (Wilcom: Motif Fill)
  paired_motif_fill every second row mirrored and shifted half a motif, so the rows
                    interlock (Wilcom: Paired Motif Fill)
  pattern_fill      tatami whose needle points are placed on a tiled pattern (diamonds,
                    squares, bricks, scales), which shows embossed in the fill
                    (Wilcom: Program Split)
  jagged_fill       tatami with rough, uneven edges for fur and grass (Wilcom: Jagged Edge)
  raised_satin      satin over two padding layers of satin for a raised look; 0.30 mm
                    spacing, columns up to 7 mm (Wilcom: Satin Raised)
  applique          placement line, machine stop (lay the fabric), tack-down zigzag, then a
                    satin cover border (Wilcom: Applique)

Motifs are data in ``stitch/library/motifs.json`` (designed for this project). All
randomness uses fixed seeds: the same shape always gets the same stitches.
"""

from __future__ import annotations

import json
import math
from functools import lru_cache
from pathlib import Path
from typing import TYPE_CHECKING

import numpy as np
from shapely.affinity import rotate
from shapely.geometry import LineString, Point, Polygon

from ..geometry import Pt, dist, iter_polygons, subdivide
from ..models import Run, StitchParams
from . import generators as gen
from .outlines import _Path, _paths, _resampled, zigzag
from .registry import StitchMethod, register
from .rows import best_satin_angle

if TYPE_CHECKING:  # pragma: no cover
    from .planner import _Item

Pieces = list[list[Pt]]
LIBRARY = Path(__file__).parent / "library" / "motifs.json"
PATTERNS = ("diamonds", "squares", "bricks", "scales")
SEED = 7
RAISED_MAX_WIDTH_MM = 7.0
APPLIQUE_COVER_MM = 3.0  # satin cover border width
APPLIQUE_TACK = (1.5, 2.0)  # tack-down zigzag: width, gap (mm)
PATTERN_MAX_STITCH_MM = 5.0  # pattern fill: longest stitch between pattern points


@lru_cache(maxsize=1)
def motifs() -> dict[str, dict]:
    return json.loads(LIBRARY.read_text(encoding="utf-8"))["motifs"]


def motif_points(name: str) -> np.ndarray:
    lib = motifs()
    return np.asarray(lib.get(name, lib["wave"])["points"], dtype=float)


# --------------------------------------------------------------------------- motifs along a path

def motif_along(path: list[Pt], size: float, name: str, mirror: bool = False) -> list[Pt]:
    """Motifs end to end along a polyline, stretched a little so a whole number fits."""
    line = LineString(path)
    length = line.length
    if length < 1e-6:
        return []
    n = max(1, round(length / size))
    seg = length / n
    pts = motif_points(name)
    if mirror:
        pts = pts * [1, -1]
    out: list[Pt] = []
    for k in range(n):
        for j, (u, v) in enumerate(pts):
            if k and j == 0:
                continue  # the previous motif ended here
            s = min(max((k + u) * seg, 0.0), length)
            p = line.interpolate(s)
            a, b = line.interpolate(max(s - 0.3, 0)), line.interpolate(min(s + 0.3, length))
            tx, ty = b.x - a.x, b.y - a.y
            norm = math.hypot(tx, ty) or 1.0
            out.append((p.x - ty / norm * v * size, p.y + tx / norm * v * size))
    return out


def motif_run(geom, start: Pt, size: float, name: str) -> Pieces:
    return [motif_along(p.pts, size, name) for p in _paths(geom, start)]


# --------------------------------------------------------------------------- hand stitch

def hand_stitch(geom, start: Pt, step: float) -> Pieces:
    """Long stitches of uneven length, each followed by a tiny one, slightly wobbly."""
    rng = np.random.default_rng(SEED)
    pieces: Pieces = []
    for path in _paths(geom, start):
        line = LineString(path.pts)
        length = line.length
        s, positions, long_one = 0.0, [0.0], True
        while s < length - 1e-6:
            s = min(length, s + (step * rng.uniform(0.8, 1.3) if long_one else 0.6))
            positions.append(s)
            long_one = not long_one
        pts = []
        for d in positions:
            p = line.interpolate(d)
            a, b = line.interpolate(max(d - 0.3, 0)), line.interpolate(min(d + 0.3, length))
            tx, ty = b.x - a.x, b.y - a.y
            norm = math.hypot(tx, ty) or 1.0
            w = rng.uniform(-0.15, 0.15)
            pts.append((p.x - ty / norm * w, p.y + tx / norm * w))
        pieces.append(pts)
    return pieces


# --------------------------------------------------------------------------- motif fill

def warp_3d(pieces: Pieces, poly: Polygon) -> Pieces:
    """3D warp (Wilcom, with Motif Fill): the stitching bulges like a sphere seen from
    the front - points move outwards from the shape's middle on a fisheye curve, so the
    motifs are large in the middle and squeezed towards the edges. Clipped back to the
    shape afterwards."""
    c = poly.representative_point() if not poly.contains(poly.centroid) else poly.centroid
    radius = max(Point(c).distance(Point(p)) for p in poly.exterior.coords) or 1.0
    out: Pieces = []
    inside = poly.buffer(-0.25)
    for piece in pieces:
        warped = []
        for x, y in piece:
            dx, dy = x - c.x, y - c.y
            r = math.hypot(dx, dy)
            if r < 1e-9:
                warped.append((x, y))
                continue
            k = radius * math.sin(min(r / radius, 1.0) * math.pi / 2) / r  # r -> R sin(pi r / 2R)
            warped.append((c.x + dx * k, c.y + dy * k))
        cut = LineString(warped).intersection(inside) if len(warped) >= 2 else None
        if cut is not None:
            out += [list(g.coords) for g in getattr(cut, "geoms", [cut]) if isinstance(g, LineString) and g.length > 1.0]
    return out


def motif_fill(poly: Polygon, angle: float, size: float, name: str, start: Pt, paired: bool = False) -> Pieces:
    """Rows of motifs across the shape, ``size`` apart, clipped to it."""
    rp = rotate(poly, -angle, origin=(0, 0))
    minx, miny, maxx, maxy = rp.bounds
    x0 = math.floor(minx / size) * size - size
    x1 = math.ceil(maxx / size) * size + size
    inside = rp.buffer(-0.25)
    pieces: Pieces = []
    row = 0
    y = miny + size / 2
    while y < maxy:
        shift = size / 2 if (paired and row % 2) else 0.0
        base = [(x0 + shift, y), (x1 + shift, y)]
        pts = motif_along(base, size, name, mirror=paired and row % 2 == 1)
        clipped = LineString(pts).intersection(inside)
        parts = [list(g.coords) for g in getattr(clipped, "geoms", [clipped]) if isinstance(g, LineString) and g.length > 1.0]
        parts.sort(key=lambda c: min(c[0][0], c[-1][0]))
        if row % 2:
            parts = [c[::-1] for c in parts[::-1]]
        pieces += parts
        y += size
        row += 1
    out = _link(pieces, rp, 3 * size)
    back = [[(float(x), float(y)) for x, y in rotate(LineString(p), angle, origin=(0, 0)).coords] for p in out]
    if back and dist(start, back[-1][-1]) < dist(start, back[0][0]):
        back = [p[::-1] for p in back[::-1]]
    return back


def _link(pieces: Pieces, region, max_gap: float) -> Pieces:
    """Join consecutive pieces with a straight run when it stays inside and is short."""
    out: Pieces = []
    for p in pieces:
        if out and dist(out[-1][-1], p[0]) <= max_gap and region.contains(LineString([out[-1][-1], p[0]])):
            out[-1].extend(subdivide(out[-1][-1], p[0], 2.5)[:-1] + list(p))
        else:
            out.append(list(p))
    return out


# --------------------------------------------------------------------------- pattern fill (program split)

def pattern_xs(pattern: str, size: float, stitch_len: float, spacing: float):
    """Needle points of one tatami row (rotated frame) for a tiled pattern: where the
    row crosses the pattern's lines, plus even filler points so no stitch is longer
    than ``stitch_len``. Rows lying on a horizontal pattern line get short stitches
    (1 mm), which show as that line."""
    half = size / 2

    def lines_at(y: float) -> list[float]:
        xs: list[float] = []
        if pattern == "diamonds":
            xs = [y % size, (-y) % size]
        elif pattern == "squares":
            xs = [0.0]
        elif pattern == "bricks":
            course = math.floor(y / half)
            xs = [half if course % 2 else 0.0]
        elif pattern == "scales":
            j = round(y / half)
            for jj in (j - 1, j, j + 1):
                cy = jj * half
                if abs(y - cy) < half:
                    dx = math.sqrt(half * half - (y - cy) ** 2)
                    cx = (jj % 2) * half
                    xs += [(cx - dx) % size, (cx + dx) % size]
        return xs

    def on_line(y: float) -> bool:
        step = size if pattern == "squares" else half if pattern == "bricks" else None
        return step is not None and abs(y - round(y / step) * step) < spacing / 2

    def row_xs(y: float, x0: float, x1: float) -> list[float]:
        if on_line(y):
            n = max(1, math.ceil((x1 - x0) / 1.0))
            return [x0 + (x1 - x0) * k / n for k in range(1, n)]
        base = lines_at(y)
        marks = sorted({b + k * size for b in base for k in range(math.floor(x0 / size) - 1, math.ceil(x1 / size) + 1)})
        marks = [m for m in marks if x0 < m < x1]
        kept: list[float] = []
        for m in [x0, *marks, x1]:
            if not kept or m - kept[-1] >= 0.6:
                kept.append(m)
        # filler points only where a stitch would be too long, shifted from row to row
        # (golden-ratio steps) so they never line up into lines of their own
        limit = max(stitch_len, PATTERN_MAX_STITCH_MM)
        u = ((round(y / spacing) * 0.6180339887) % 1.0 - 0.5) * 0.6
        out: list[float] = []
        for a, b in zip(kept, kept[1:]):
            n = max(1, math.ceil((b - a) * 1.3 / limit - 1e-9))
            out += [a + (b - a) * (k + u) / n for k in range(1, n)] + [b]
        return out[:-1]  # inner points only (the row's end is added by the fill)

    return row_xs


# --------------------------------------------------------------------------- jagged edge

def jagged_ends(spacing: float, rough: float):
    """Each row end moves out by up to ``rough`` or in by up to 0.4 x ``rough``."""
    def ends(y: float, x0: float, x1: float) -> tuple[float, float]:
        rng = np.random.default_rng(SEED + int(round(y / spacing)) % 100000)
        a, b = rng.uniform(-0.4, 1.0, 2) * rough
        n0, n1 = x0 - a, x1 + b
        if n1 - n0 < 1.0:
            return x0, x1
        return n0, n1
    return ends


# --------------------------------------------------------------------------- generators

def _path_style(fn):
    def generate(item: "_Item", angle: float, params: StitchParams, start: Pt) -> list[Run]:
        return [Run("running", p, item.oid) for p in fn(item.geom, start, params) if len(p) >= 2]
    return generate


def _sew_motif_fill(paired: bool):
    def generate(item: "_Item", angle: float, params: StitchParams, start: Pt) -> list[Run]:
        pieces = motif_fill(item.geom, angle, params.motif_size_mm, params.motif, start, paired=paired)
        if params.motif_warp:
            pieces = warp_3d(pieces, item.geom)
        return [Run("running", p, item.oid) for p in pieces if len(p) >= 2]
    return generate


def _sew_tatami_variant(kind: str):
    """Pattern and jagged fills: the standard fill (foundation, compensation) with a hook."""
    def generate(item: "_Item", angle: float, params: StitchParams, start: Pt) -> list[Run]:
        from .methods import _compensate, _runs  # noqa: PLC0415
        from .underlay import fill_foundation  # noqa: PLC0415
        runs: list[Run] = []
        if params.underlay:
            runs += _runs("underlay", fill_foundation(params.fill_underlay, item.geom, angle, params.underlay_spacing_mm, start), item.oid)
        cur = runs[-1].points[-1] if runs else start
        poly = _compensate(item.geom, params)
        hooks = {}
        if kind == "pattern":
            hooks["row_xs"] = pattern_xs(params.pattern, params.pattern_size_mm, params.fill_stitch_length_mm, params.fill_spacing_mm)
        else:
            hooks["row_ends"] = jagged_ends(params.fill_spacing_mm, params.jagged_mm)
        pieces = gen.tatami_fill(poly, angle, params.fill_spacing_mm, params.fill_stitch_length_mm, cur, params.max_stitch_mm, **hooks)
        return runs + _runs("fill", pieces, item.oid)
    return generate


def sew_raised_satin(item: "_Item", angle: float, params: StitchParams, start: Pt) -> list[Run]:
    """Two padding layers of satin, each a little narrower, under a 0.30 mm satin top."""
    from .methods import _compensate  # noqa: PLC0415
    satin_angle = item.satin_angle if item.satin_angle is not None else best_satin_angle(item.geom)[0]
    runs: list[Run] = []
    cur = start
    for inset, gap, kind in ((0.6, 0.6, "underlay"), (0.3, 0.45, "underlay")):
        for part in iter_polygons(item.geom.buffer(-inset)):
            for p in gen.satin_area(part, gap, cur, params.max_stitch_mm, satin_angle):
                if len(p) >= 2:
                    runs.append(Run(kind, p, item.oid))
                    cur = p[-1]
    for p in gen.satin_area(_compensate(item.geom, params), 0.30, cur, params.max_stitch_mm, satin_angle):
        if len(p) >= 2:
            runs.append(Run("satin", p, item.oid))
    return runs


def sew_applique(item: "_Item", angle: float, params: StitchParams, start: Pt) -> list[Run]:
    """Placement line -> stop (lay the fabric piece) -> tack-down zigzag -> satin cover."""
    paths = _paths(item.geom, start)
    runs: list[Run] = []
    for path in paths:
        pts = _resampled(path, params.running_stitch_length_mm)
        runs.append(Run("running", pts, item.oid))
    if not runs:
        return runs
    runs.append(Run("stop", [runs[-1].points[-1]], item.oid))  # the machine stops: place the fabric
    width, gap = APPLIQUE_TACK
    for path in paths:
        runs.append(Run("running", zigzag(path, width, gap, 0.0, params.max_stitch_mm), item.oid))
    for path in paths:
        cover = zigzag(path, APPLIQUE_COVER_MM, params.satin_spacing_mm, params.pull_compensation_mm, params.max_stitch_mm)
        runs.append(Run("satin", cover, item.oid))
    return [r for r in runs if len(r.points) >= 2 or r.kind == "stop"]


# --------------------------------------------------------------------------- registration

PATH_DECOR = ("motif_run", "hand_stitch")
AREA_DECOR = ("motif_fill", "paired_motif_fill", "pattern_fill", "jagged_fill", "raised_satin", "applique")

register(StitchMethod("motif_run", _path_style(lambda g, s, p: motif_run(g, s, p.motif_size_mm, p.motif))))
register(StitchMethod("hand_stitch", _path_style(lambda g, s, p: hand_stitch(g, s, p.running_stitch_length_mm))))
register(StitchMethod("motif_fill", _sew_motif_fill(False), directional=True))
register(StitchMethod("paired_motif_fill", _sew_motif_fill(True), directional=True))
register(StitchMethod("pattern_fill", _sew_tatami_variant("pattern"), directional=True, density=True))
register(StitchMethod("jagged_fill", _sew_tatami_variant("jagged"), directional=True, density=True))
register(StitchMethod("raised_satin", sew_raised_satin, density=True))
register(StitchMethod("applique", sew_applique))
