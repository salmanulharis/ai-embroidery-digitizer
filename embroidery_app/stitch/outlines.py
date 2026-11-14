"""Decorative outlines and borders, sewn along a path.

A line object is sewn along its path (entered from its nearer end); a filled shape
along its outline and the edges of its holes, as a border. Run family (Wilcom:
Backstitch, Stemstitch, Sculpture Run; "double run" is the classic there-and-back
run) and two open borders (Wilcom: Zigzag, E Stitch):

  double_run     forward along the path, back on the in-between points: a bold line
                 that ends where it started
  backstitch     two steps forward, one back, all the way: a hand-sewn look
  stemstitch     overlapping slanted stitches: a rope-like line
  sculpture_run  long stitches sewn three times, short ones once (Wilcom's default
                 #A 3 / #B 1, A 2.5 mm, B 1.5 mm): a beaded line
  zigzag         an open zigzag across the path (border)
  estitch        a run along the path with a spike to one side every gap (blanket
                 edge, e.g. around applique); on a shape the spikes point inwards
"""

from __future__ import annotations

import math
from typing import TYPE_CHECKING

import numpy as np
from shapely.geometry import LineString, LinearRing, Polygon

from ..geometry import Pt, dist, iter_polygons, subdivide
from ..models import Run, StitchParams
from . import generators as gen
from .registry import StitchMethod, register

if TYPE_CHECKING:  # pragma: no cover
    from .planner import _Item

SCULPTURE_A_MM, SCULPTURE_B_MM = 2.5, 1.5
SCULPTURE_A_REPEATS = 3
STEM_WIDTH_MM = 1.0  # how far the slanted stem stitches lean across the line
MIN_BORDER_WIDTH_MM = 1.0


# --------------------------------------------------------------------------- paths

class _Path:
    """A polyline to sew; ``inside`` = +1 / -1 when the shape lies left / right of it."""

    def __init__(self, pts: list[Pt], closed: bool, inside: int = 1):
        self.pts, self.closed, self.inside = pts, closed, inside

    @property
    def line(self) -> LineString:
        return LineString(self.pts)


def _paths(geom, start: Pt) -> list[_Path]:
    if isinstance(geom, LineString):
        coords = list(geom.coords)
        if dist(start, coords[-1]) < dist(start, coords[0]):
            coords = coords[::-1]
        return [_Path(coords, False)]
    out: list[_Path] = []
    for poly in iter_polygons(geom):
        for ring, hole in [(poly.exterior, False), *((r, True) for r in poly.interiors)]:
            coords = list(ring.coords)[:-1]
            if len(coords) < 3:
                continue
            k = min(range(len(coords)), key=lambda i: dist(coords[i], start))
            coords = coords[k:] + coords[:k] + [coords[k]]
            ccw = LinearRing(ring.coords).is_ccw
            out.append(_Path(coords, True, 1 if ccw != hole else -1))  # the shape is left of a CCW outline
    return out


def _normals(pts: list[Pt], closed: bool) -> np.ndarray:
    """Left-hand unit normals of a resampled polyline."""
    a = np.asarray(pts, dtype=float)
    n = len(a)
    if closed:
        prev, nxt = np.roll(a, 1, axis=0), np.roll(a, -1, axis=0)
        if n > 2 and np.allclose(a[0], a[-1]):  # closing point repeats the first
            prev[0], nxt[-1] = a[-2], a[1]
    else:
        prev = np.vstack([a[:1], a[:-1]])
        nxt = np.vstack([a[1:], a[-1:]])
    t = nxt - prev
    t /= np.maximum(np.hypot(t[:, 0], t[:, 1]), 1e-9)[:, None]
    return np.stack([-t[:, 1], t[:, 0]], axis=1)


def _resampled(path: _Path, step: float) -> list[Pt]:
    """Points about ``step`` apart, evenly spaced between the sharp corners, which are
    kept. Gentle vertices are not kept: a circle's outline has one every ~0.7 mm and
    would otherwise set the stitch length instead of ``step``."""
    out: list[Pt] = []
    for piece in _corner_pieces(path):
        line = piece.line
        n = max(1, math.ceil(line.length / step - 1e-9))  # never longer than step (a longer piece would be split in two)
        pts = [(q.x, q.y) for q in (line.interpolate(line.length * k / n) for k in range(n + 1))]
        pts[0], pts[-1] = piece.pts[0], piece.pts[-1]  # corners exactly
        out.extend(pts if not out else pts[1:])
    return out if len(out) >= 2 else list(path.pts)


CORNER_DEG = 30.0  # a turn sharper than this is a corner: patterns restart there so it stays sharp


def _corner_pieces(path: _Path) -> list[_Path]:
    """The path split at its sharp corners. Patterns that step over points (backstitch,
    sculpture run, the way back of a double run) are sewn piece by piece, so every
    corner is a needle point instead of being cut off."""
    pts = [p for i, p in enumerate(path.pts) if i == 0 or dist(p, path.pts[i - 1]) > 1e-9]
    cuts = [0]
    for i in range(1, len(pts) - 1):
        a, b, c = pts[i - 1], pts[i], pts[i + 1]
        h1 = np.arctan2(b[1] - a[1], b[0] - a[0])
        h2 = np.arctan2(c[1] - b[1], c[0] - b[0])
        turn = abs((np.degrees(h2 - h1) + 180) % 360 - 180)
        if turn > CORNER_DEG:
            cuts.append(i)
    cuts.append(len(pts) - 1)
    return [_Path(pts[i:j + 1], False, path.inside) for i, j in zip(cuts, cuts[1:]) if j > i]


def _piecewise(path: _Path, sew) -> list[Pt]:
    """Sew each corner-to-corner piece with ``sew`` and join them (pieces share their corner)."""
    out: list[Pt] = []
    for piece in _corner_pieces(path):
        pts = sew(piece)
        out.extend(pts if not out or dist(out[-1], pts[0]) > 1e-9 else pts[1:])
    return out


# --------------------------------------------------------------------------- run family

def double_run(path: _Path, step: float) -> list[Pt]:
    """There along the path, back through the middle of each forward stitch (new holes,
    no thread cut) and through every corner."""
    pieces = _corner_pieces(path)
    forward: list[Pt] = []
    back: list[Pt] = []
    for piece in pieces:
        pts = _resampled(piece, step)
        forward.extend(pts if not forward else pts[1:])
        mids = [((a[0] + b[0]) / 2, (a[1] + b[1]) / 2) for a, b in zip(pts, pts[1:])]
        back = [*mids[::-1], pts[0], *back]
    return [*forward, *back]


def backstitch(path: _Path, step: float) -> list[Pt]:
    return _piecewise(path, lambda piece: _backstitch_piece(piece, step))


def _backstitch_piece(path: _Path, step: float) -> list[Pt]:
    pts = _resampled(path, step / 2)  # forward stitches are one step, the backward half of it
    if len(pts) < 3:
        return pts
    out = [pts[0]]
    for i in range(len(pts) - 2):
        out += [pts[i + 2], pts[i + 1]]
    out.append(pts[-1])
    return out


def stemstitch(path: _Path, step: float, width: float = STEM_WIDTH_MM) -> list[Pt]:
    """Each stitch runs from one side of the line to the other, one step on, and the
    next one starts half a step on: the stitches overlap like the strands of a rope."""
    return _piecewise(path, lambda piece: _stem_piece(piece, step, width))


def _stem_piece(path: _Path, step: float, width: float) -> list[Pt]:
    pts = _resampled(path, step / 2)
    if len(pts) < 3:
        return pts
    nrm = _normals(pts, path.closed) * (width / 2)
    out: list[Pt] = []
    for i in range(len(pts) - 2):
        a = (pts[i][0] + nrm[i][0], pts[i][1] + nrm[i][1])
        b = (pts[i + 2][0] - nrm[i + 2][0], pts[i + 2][1] - nrm[i + 2][1])
        out += [a, b]
    return out


def sculpture_run(path: _Path, a_len: float = SCULPTURE_A_MM, b_len: float = SCULPTURE_B_MM,
                  a_repeats: int = SCULPTURE_A_REPEATS) -> list[Pt]:
    return _piecewise(path, lambda piece: _sculpture_piece(piece, a_len, b_len, a_repeats))


def _sculpture_piece(path: _Path, a_len: float, b_len: float, a_repeats: int) -> list[Pt]:
    line = path.line
    total = line.length
    stops, s, long_one = [0.0], 0.0, True
    while s < total - 1e-6:
        s = min(total, s + (a_len if long_one else b_len))
        stops.append(s)
        long_one = not long_one
    pts = [(p.x, p.y) for p in (line.interpolate(d) for d in stops)]
    out = [pts[0]]
    for k, (a, b) in enumerate(zip(pts, pts[1:])):
        if k % 2 == 0:  # an A stitch: there, back, there again
            out += [b, a] * ((a_repeats - 1) // 2) + [b]
        else:
            out.append(b)
    return out


# --------------------------------------------------------------------------- borders

def zigzag(path: _Path, width: float, spacing: float, pull: float, max_stitch: float) -> list[Pt]:
    # piece by piece: across a corner the zigzag would splay out past the outline
    # (evenly resampled first: satin_line keeps every vertex of the line it is given)
    pieces = _corner_pieces(path)
    if len(pieces) == 1:
        return gen.satin_line(LineString(_resampled(path, spacing)), width, spacing, pull, max_stitch, closed=path.closed)
    # each piece runs on past its corners by half the width, so the pieces overlap there
    # and the corner is covered (an open corner would show the fabric, or an applique's edge)
    out: list[Pt] = []
    reach = width / 2 + pull
    for i, piece in enumerate(pieces):
        pts = _resampled(piece, spacing)
        if path.closed or i > 0:
            pts = [_beyond(pts[0], pts[1], reach), *pts]
        if path.closed or i < len(pieces) - 1:
            pts = [*pts, _beyond(pts[-1], pts[-2], reach)]
        sewn = gen.satin_line(LineString(pts), width, spacing, pull, max_stitch)
        out.extend(sewn if not out else [*subdivide(out[-1], sewn[0], max_stitch)[:-1], *sewn])
    return out


def _beyond(end: Pt, inner: Pt, d: float) -> Pt:
    """The point ``d`` past ``end``, continuing the direction from ``inner`` to ``end``."""
    dx, dy = end[0] - inner[0], end[1] - inner[1]
    n = math.hypot(dx, dy) or 1.0
    return (end[0] + dx / n * d, end[1] + dy / n * d)


def estitch(path: _Path, width: float, spacing: float) -> list[Pt]:
    # piece by piece: one spike at each corner instead of a fan of them
    return _piecewise(path, lambda piece: _estitch_piece(piece, width, spacing))


def _estitch_piece(path: _Path, width: float, spacing: float) -> list[Pt]:
    pts = _resampled(path, spacing)
    nrm = _normals(pts, path.closed) * (width * path.inside)
    last = len(pts) - 1  # the end point (on a ring: the closing point) gets no spike of its own
    out: list[Pt] = []
    for i, p in enumerate(pts):
        out.append(p)
        if i < last:
            out += [(p[0] + nrm[i][0], p[1] + nrm[i][1]), p]
    return out


# --------------------------------------------------------------------------- methods

def _border_width(item: "_Item", params: StitchParams) -> float:
    if isinstance(item.geom, LineString) and item.width >= MIN_BORDER_WIDTH_MM:
        return item.width  # a stroked line keeps its own width
    return params.border_width_mm


def _sew(fn):
    def generate(item: "_Item", angle: float, params: StitchParams, start: Pt) -> list[Run]:
        runs: list[Run] = []
        cur = start
        for path in _paths(item.geom, cur):
            pts = fn(path, item, params)
            if len(pts) >= 2:
                runs.append(Run("running" if item.method not in ("zigzag", "estitch") else "satin", pts, item.oid))
                cur = pts[-1]
        return runs
    return generate


STEP = lambda p: p.running_stitch_length_mm  # noqa: E731
OUTLINE_STYLES = {
    "double_run": lambda path, it, p: double_run(path, STEP(p)),
    "backstitch": lambda path, it, p: backstitch(path, STEP(p)),
    "stemstitch": lambda path, it, p: stemstitch(path, STEP(p)),
    "sculpture_run": lambda path, it, p: sculpture_run(path),
    "zigzag": lambda path, it, p: zigzag(path, _border_width(it, p), p.border_spacing_mm, 0.0, p.max_stitch_mm),
    "estitch": lambda path, it, p: estitch(path, _border_width(it, p), p.border_spacing_mm),
}

for _name, _fn in OUTLINE_STYLES.items():
    register(StitchMethod(_name, _sew(_fn)))  # follows its path: no direction, own density settings
