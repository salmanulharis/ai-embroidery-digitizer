"""Styles steered by a guide line the user draws across a shape.

  guided_satin     satin whose stitches cross the guide line at right angles all along
                   it, reaching from edge to edge of the shape: the user decides how the
                   satin turns (Wilcom: turning satin / Column input; Ink/Stitch: rails
                   and rungs). Wide places are auto-split like other satin.
  florentine_fill  fill rows that are copies of the guide line, one row gap apart, so
                   the whole fill bends with it (Wilcom: Florentine Effect)

Without a guide line on the shape, guided satin is sewn automatically and the
Florentine fill as a standard fill.
"""

from __future__ import annotations

import math
from typing import TYPE_CHECKING

from shapely.geometry import LineString, MultiPoint, Point, Polygon

from ..geometry import Pt, dist, subdivide
from ..models import Run, StitchParams
from . import generators as gen
from .registry import StitchMethod, register

if TYPE_CHECKING:  # pragma: no cover
    from .planner import _Item

Pieces = list[list[Pt]]
REACH_MM = 60.0  # how far the edges are searched from the guide line


def _extended(line: LineString, d: float) -> LineString:
    """The line continued straight on at both ends by ``d``."""
    pts = list(line.coords)
    (x0, y0), (x1, y1) = pts[0], pts[1]
    (xa, ya), (xb, yb) = pts[-2], pts[-1]
    n0 = math.hypot(x0 - x1, y0 - y1) or 1.0
    n1 = math.hypot(xb - xa, yb - ya) or 1.0
    head = (x0 + (x0 - x1) / n0 * d, y0 + (y0 - y1) / n0 * d)
    tail = (xb + (xb - xa) / n1 * d, yb + (yb - ya) / n1 * d)
    return LineString([head, *pts, tail])


def _centre(poly: Polygon, guide: list[Pt]) -> LineString | None:
    """The guide line inside the shape, continued to the shape's edges."""
    line = LineString(guide).simplify(0.1)
    if line.length < 1.0:
        return None
    inside = _extended(line, REACH_MM).intersection(poly)
    parts = [g for g in getattr(inside, "geoms", [inside]) if isinstance(g, LineString) and g.length > 1.0]
    return max(parts, key=lambda g: g.length) if parts else None


def _to_edge(poly: Polygon, p: Pt, n: tuple[float, float]) -> float:
    """Distance from ``p`` (inside) along direction ``n`` to the shape's edge."""
    ray = LineString([p, (p[0] + n[0] * REACH_MM, p[1] + n[1] * REACH_MM)])
    hit = ray.intersection(poly.boundary)
    if hit.is_empty:
        return 0.0
    pts = [hit] if isinstance(hit, Point) else [g for g in getattr(hit, "geoms", []) if isinstance(g, Point)]
    if not pts and isinstance(hit, MultiPoint):
        pts = list(hit.geoms)
    return min((Point(p).distance(q) for q in pts), default=0.0)


# --------------------------------------------------------------------------- guided satin

def guided_satin(poly: Polygon, guide: list[Pt], spacing: float, pull: float, max_stitch: float, start: Pt) -> tuple[Pieces, Pieces]:
    """(foundation, satin): a centre run along the guide, then satin across it."""
    centre = _centre(poly, guide)
    if centre is None:
        return [], []
    if dist(start, centre.coords[-1]) < dist(start, centre.coords[0]):
        centre = LineString(list(centre.coords)[::-1])
    n = max(2, math.ceil(centre.length / spacing))
    pts = [centre.interpolate(centre.length * k / n) for k in range(n + 1)]
    out: list[Pt] = []
    for i, q in enumerate(pts):
        a = centre.interpolate(max(0.0, centre.length * i / n - 0.5))
        b = centre.interpolate(min(centre.length, centre.length * i / n + 0.5))
        tx, ty = b.x - a.x, b.y - a.y
        norm = math.hypot(tx, ty) or 1.0
        nx, ny = -ty / norm, tx / norm
        side = 1 if i % 2 == 0 else -1
        reach = _to_edge(poly, (q.x, q.y), (nx * side, ny * side))
        if reach <= 0.05:
            continue
        p = (q.x + nx * side * (reach + pull), q.y + ny * side * (reach + pull))
        out.extend(gen.split_stitch(out[-1], p, max_stitch, i) if out else [p])
    run = gen.running(centre, 2.0)
    return ([run] if len(run) >= 2 else []), ([out] if len(out) >= 2 else [])


# --------------------------------------------------------------------------- florentine fill

def florentine_fill(poly: Polygon, guide: list[Pt], spacing: float, step: float, start: Pt) -> Pieces:
    """Rows that are the guide line shifted across the shape, ``spacing`` apart."""
    line = LineString(guide).simplify(0.1)
    if line.length < 1.0:
        return []
    (x0, y0), (x1, y1) = line.coords[0], line.coords[-1]
    chord = math.hypot(x1 - x0, y1 - y0) or 1.0
    nx, ny = -(y1 - y0) / chord, (x1 - x0) / chord  # across the guide's overall direction
    minx, miny, maxx, maxy = poly.bounds
    span = math.hypot(maxx - minx, maxy - miny)
    base = _extended(line, span)
    # how far the rows must be shifted to sweep the whole shape
    offs = [(cx - x0) * nx + (cy - y0) * ny for cx, cy in poly.exterior.coords]
    bend = [(gx - x0) * nx + (gy - y0) * ny for gx, gy in base.coords]  # the guide's own sweep across
    lo, hi = min(offs) - max(bend) - spacing, max(offs) - min(bend) + spacing
    rows: list[list[list[Pt]]] = []
    k = math.floor(lo / spacing)
    while k * spacing <= hi:
        d = k * spacing + spacing / 2
        row = LineString([(x + nx * d, y + ny * d) for x, y in base.coords])
        cut = row.intersection(poly)
        segs = [list(g.coords) for g in getattr(cut, "geoms", [cut]) if isinstance(g, LineString) and g.length > 0.5]
        if segs:
            rows.append(segs)
        k += 1
    # where the shape cuts a row into several pieces, the k-th pieces of all rows form a
    # lane: each lane is sewn back and forth on its own, so the needle steps one row
    # gap at a time instead of jumping between the pieces of every row
    lanes: dict[int, list[tuple[int, list[Pt]]]] = {}
    for r, segs in enumerate(rows):
        segs.sort(key=lambda s: LineString(base.coords).project(Point(s[0])))
        for j, s in enumerate(segs):
            lanes.setdefault(j, []).append((r, s))
    pieces: Pieces = []
    inside = poly.buffer(0.05)
    for j in sorted(lanes):
        for k, (r, s) in enumerate(lanes[j]):
            s = s if k % 2 == 0 else s[::-1]
            pts = _even_with_phase(s, step, gen.TATAMI_OFFSETS[r % len(gen.TATAMI_OFFSETS)])
            if pieces and dist(pieces[-1][-1], pts[0]) <= 3 * spacing and inside.contains(LineString([pieces[-1][-1], pts[0]])):
                pieces[-1].extend(subdivide(pieces[-1][-1], pts[0], step)[:-1] + pts)
            else:
                pieces.append(pts)
    if pieces and dist(start, pieces[-1][-1]) < dist(start, pieces[0][0]):
        pieces = [p[::-1] for p in pieces[::-1]]
    return pieces


def _even_with_phase(coords: list[Pt], step: float, phase: float) -> list[Pt]:
    """Needle points along a row: both ends, every sharp corner, and ``step`` apart in
    between, shifted by ``phase`` x step from row to row (brick pattern, as in tatami)."""
    from .outlines import _corner_pieces, _Path  # noqa: PLC0415
    out: list[Pt] = []
    for piece in _corner_pieces(_Path(list(coords), False)):
        pts = _even_piece(piece.pts, step, phase)
        out.extend(pts if not out else pts[1:])
    return out


def _even_piece(coords: list[Pt], step: float, phase: float) -> list[Pt]:
    line = LineString(coords)
    length = line.length
    ds = [0.0]
    s = step * phase if step * phase > 0.6 else step * (phase + 1)
    while s < length - 0.6:
        ds.append(s)
        s += step
    ds.append(length)
    return [(q.x, q.y) for q in (line.interpolate(d) for d in ds)]


# --------------------------------------------------------------------------- methods

def sew_guided_satin(item: "_Item", angle: float, params: StitchParams, start: Pt) -> list[Run]:
    found, satin = guided_satin(item.geom, item.guide, params.satin_spacing_mm, params.pull_compensation_mm, params.max_stitch_mm, start)
    runs = [Run("underlay", p, item.oid) for p in found] if params.underlay else []
    return runs + [Run("satin", p, item.oid) for p in satin]


def sew_florentine(item: "_Item", angle: float, params: StitchParams, start: Pt) -> list[Run]:
    from .methods import _compensate  # noqa: PLC0415
    from .underlay import fill_foundation  # noqa: PLC0415
    runs: list[Run] = []
    if params.underlay:
        runs += [Run("underlay", p, item.oid) for p in fill_foundation("edge", item.geom, angle, params.underlay_spacing_mm, start) if len(p) >= 2]
    cur = runs[-1].points[-1] if runs else start
    poly = _compensate(item.geom, params)
    return runs + [Run("fill", p, item.oid) for p in florentine_fill(poly, item.guide, params.fill_spacing_mm, params.fill_stitch_length_mm, cur) if len(p) >= 2]


GUIDED_STYLES = ("guided_satin", "florentine_fill")
register(StitchMethod("guided_satin", sew_guided_satin, density=True))
register(StitchMethod("florentine_fill", sew_florentine, density=True))
