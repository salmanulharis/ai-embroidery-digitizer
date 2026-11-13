"""Named foundation (underlay) types, sewn before the top stitching.

Under fills:
  none           no foundation
  edge           a run just inside the outline
  edge_tatami    edge run + an open tatami lattice across the fill (the default)
  double_tatami  edge run + two crossing lattices at +-45 deg to the fill: more
                 support for stretchy or fluffy fabric

Under satin columns:
  none           no foundation
  center         a run along the middle of the column
  edge           a run just inside both edges (the default)
  zigzag         an open zigzag across the column
  edge_zigzag    edge run + zigzag
  double_zigzag  two zigzag passes, the second between the first one's stitches

A zigzag only fits a column wide enough for it: under a column narrower than
ZIGZAG_MIN_WIDTH_MM a zigzag becomes a centre run (``edge_zigzag`` an edge run),
following Wilcom's guidance (centre run for 2-3 mm columns, zigzag for wider ones).
Path-following satin (bands, lines, columns, lettering strokes) keeps its own
foundation, which already follows the stroke.
"""

from __future__ import annotations

from shapely.geometry import LineString, Polygon

from ..geometry import Pt
from ..models import FILL_UNDERLAYS, SATIN_UNDERLAYS  # noqa: F401  (the names, re-exported)
from . import generators as gen
from .rows import scan_rows, unrotate
ZIGZAG_MIN_WIDTH_MM = 2.5
ZIGZAG_INSET_MM = 0.4  # zigzag foundation stays this far inside the edges

Pieces = list[list[Pt]]


def column_width(poly: Polygon) -> float:
    """Average width of a long, thin shape (~ 2 x area / perimeter)."""
    return 2 * poly.area / max(poly.length, 1e-6)


# --------------------------------------------------------------------------- fills

def fill_foundation(style: str, poly: Polygon, angle: float, spacing: float, start: Pt, smart: bool = False) -> Pieces:
    if style == "none":
        return []
    if style == "edge":
        return _edge_run(poly, start)
    if style == "double_tatami":
        pieces = _edge_run(poly, start)
        cur = pieces[-1][-1] if pieces else start
        lattice = poly.buffer(-0.9)
        for a in (angle + 45, angle - 45):
            layer = gen.lattice(lattice, a - 90, spacing, cur)  # lattice() turns its rows by +90
            pieces += layer
            cur = pieces[-1][-1] if pieces else cur
        return pieces
    return gen.fill_underlay(poly, angle, spacing, start, smart=smart)  # edge_tatami


def _edge_run(poly: Polygon, start: Pt) -> Pieces:
    pieces: Pieces = []
    for part in gen.polygons(poly.buffer(-0.5)):
        if part.length > 4:
            pieces.extend(gen.ring_from(part, start, 2.5))
    return pieces


# --------------------------------------------------------------------------- satin

def satin_foundation(style: str, poly: Polygon, angle: float | None, spacing: float, start: Pt, max_stitch: float) -> Pieces:
    """Foundation under single-angle satin. ``angle`` is the satin's stitch direction."""
    if style == "none":
        return []
    if column_width(poly) < ZIGZAG_MIN_WIDTH_MM:
        style = {"zigzag": "center", "double_zigzag": "center", "edge_zigzag": "edge"}.get(style, style)
    if style == "center":
        return _centre_run(poly, angle)
    if style == "zigzag":
        return _zigzag(poly, angle, spacing, start, max_stitch)
    if style == "edge_zigzag":
        pieces = gen.satin_underlay(poly, start)
        cur = pieces[-1][-1] if pieces else start
        return pieces + _zigzag(poly, angle, spacing, cur, max_stitch)
    if style == "double_zigzag":
        first = _zigzag(poly, angle, spacing, start, max_stitch)
        cur = first[-1][-1] if first else start
        return first + _zigzag(poly, angle, spacing, cur, max_stitch, phase=0.0)
    return gen.satin_underlay(poly, start)  # edge


def _zigzag(poly: Polygon, angle: float | None, spacing: float, start: Pt, max_stitch: float, phase: float = 0.5) -> Pieces:
    inner = poly.buffer(-ZIGZAG_INSET_MM)
    pieces: Pieces = []
    cur = start
    for part in gen.polygons(inner):
        if part.area > 1.0:
            pieces += gen.satin_area(part, spacing, cur, max_stitch, angle, phase=phase)
            cur = pieces[-1][-1] if pieces else cur
    return pieces


def _centre_run(poly: Polygon, angle: float | None, step: float = 2.0) -> Pieces:
    """A run along the middle of a straight-ish column: the midpoints of its satin rows."""
    if angle is None:
        angle, _ = gen.best_satin_angle(poly)
    ys, rows = scan_rows(poly, angle, 0.5)
    mids = [((r[0][0] + r[0][1]) / 2, y) for y, r in zip(ys, rows) if len(r) == 1]
    if len(mids) < 2:
        return []
    line = LineString(unrotate(mids, angle))
    return [gen.running(line, step)] if line.length >= 1.0 else []
