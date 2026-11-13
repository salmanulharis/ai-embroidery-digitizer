"""The built-in stitch methods, registered in :mod:`.registry`.

Each generator takes a classified piece of a shape (``planner._Item``), the fill
direction, the effective stitch params and the needle position, and returns the
runs that sew it: underlay first, then the top stitching.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from shapely import affinity
from shapely.geometry import LineString, Point, Polygon, box
from shapely.geometry.base import BaseGeometry

from ..geometry import Pt, dist, iter_polygons
from ..models import Run, StitchParams
from . import generators as gen
from . import smart
from . import strokes as st
from . import underlay
from .registry import StitchMethod, register

if TYPE_CHECKING:  # pragma: no cover
    from .planner import _Item


# --------------------------------------------------------------------------- helpers

def thread_limits(params: StitchParams) -> st.ThreadLimits:
    return st.thread_limits(params.thread_weight, params.satin_min_width_mm)


def _runs(kind: str, pieces: list[list[Pt]], oid: int) -> list[Run]:
    return [Run(kind, p, oid) for p in pieces if len(p) >= 2]


def _after(runs: list[Run], start: Pt) -> Pt:
    """Needle position after ``runs`` (or ``start`` when nothing was sewn)."""
    return runs[-1].points[-1] if runs else start


def _compensate(poly: Polygon, params: StitchParams) -> Polygon:
    if params.pull_compensation_mm <= 0:
        return poly
    grown = poly.buffer(params.pull_compensation_mm, join_style="mitre", mitre_limit=2.0)
    parts = sorted(iter_polygons(grown), key=lambda p: -p.area)
    return parts[0] if parts else poly


def _push(poly: Polygon, angle: float, push: float) -> Polygon:
    """Push compensation: shorten the shape by ``push`` at both ends of its stitching.

    Rows run at ``angle`` and are stacked across it; the stitches push the fabric out
    along that stacking direction, so the first and last rows are moved inwards."""
    if push <= 0 or poly.is_empty:
        return poly
    centre = poly.centroid
    turned = affinity.rotate(poly, -angle, origin=centre)
    minx, miny, maxx, maxy = turned.bounds
    if maxy - miny <= 2 * push + 0.5:  # too short to shorten: keep it
        return poly
    kept = turned.intersection(box(minx - 1, miny + push, maxx + 1, maxy - push))
    parts = sorted(iter_polygons(affinity.rotate(kept, angle, origin=centre)), key=lambda p: -p.area)
    return parts[0] if parts else poly


def _oriented(line: LineString, start: Pt) -> LineString:
    """The line, reversed when its far end is nearer to the needle."""
    if dist(start, line.coords[-1]) < dist(start, line.coords[0]):
        return LineString(list(line.coords)[::-1])
    return line


def _stroke_spacing(params: StitchParams, width: float) -> float:
    """Narrow columns get slightly lighter density: on a 0.6-1 mm column the needle
    drops are already close together and a dense setting piles up thread."""
    if width < 1.2:
        return max(params.satin_spacing_mm, 0.45)
    return params.satin_spacing_mm


def _stroke_entry(stroke: st.Stroke, pos: Pt) -> tuple[float, int]:
    coords = list(stroke.line.coords)
    if stroke.closed:
        return stroke.line.distance(Point(pos)), 0
    a, b = dist(pos, coords[0]), dist(pos, coords[-1])
    return (a, 0) if a <= b else (b, 1)


def _rotate_ring(line: LineString, pos: Pt) -> LineString:
    coords = list(line.coords)
    if dist(coords[0], coords[-1]) < 1e-6:
        coords = coords[:-1]
    k = min(range(len(coords)), key=lambda i: dist(coords[i], pos))
    ring = coords[k:] + coords[:k]
    return LineString(ring + [ring[0]])


def _extent(geom: BaseGeometry) -> tuple[float, float]:
    minx, miny, maxx, maxy = geom.bounds
    return max(maxx, 1.0), max(maxy, 1.0)


# --------------------------------------------------------------------------- generators

def sew_fill(item: "_Item", angle: float, params: StitchParams, start: Pt) -> list[Run]:
    """Tatami fill over an edge-run + lattice underlay."""
    runs: list[Run] = []
    if params.underlay:
        found = underlay.fill_foundation(params.fill_underlay, item.geom, angle, params.underlay_spacing_mm, start, smart=params.optimize)
        runs += _runs("underlay", found, item.oid)
    poly = _push(_compensate(item.geom, params), angle, params.push_compensation_mm)
    cur = _after(runs, start)
    runs += _runs("fill", gen.tatami_fill(poly, angle, params.fill_spacing_mm, params.fill_stitch_length_mm, cur, params.max_stitch_mm), item.oid)
    return runs


def sew_satin(item: "_Item", angle: float, params: StitchParams, start: Pt) -> list[Run]:
    """Single-angle satin across a straight-ish column."""
    runs: list[Run] = []
    if params.underlay:
        found = underlay.satin_foundation(params.satin_underlay, item.geom, item.satin_angle, params.underlay_spacing_mm, start, params.max_stitch_mm)
        runs += _runs("underlay", found, item.oid)
    poly = _compensate(item.geom, params)
    if item.satin_angle is not None:
        # satin rows run across the column: its ends lie along the row direction + 90
        poly = _push(poly, item.satin_angle, params.push_compensation_mm)
    cur = _after(runs, start)
    runs += _runs("satin", gen.satin_area(poly, params.satin_spacing_mm, cur, params.max_stitch_mm, item.satin_angle), item.oid)
    return runs


def sew_satin_band(item: "_Item", angle: float, params: StitchParams, start: Pt) -> list[Run]:
    """Satin along the centre line of a ring-shaped band (outline, border)."""
    runs: list[Run] = []
    if params.underlay:
        runs += _runs("underlay", [gen.running(item.centre, 2.0)], item.oid)
    cur = _after(runs, start)
    band = gen.satin_band(item.geom, item.centre, params.satin_spacing_mm, params.pull_compensation_mm, cur, params.max_stitch_mm)
    return runs + _runs("satin", [band], item.oid)


def sew_satin_line(item: "_Item", angle: float, params: StitchParams, start: Pt) -> list[Run]:
    """Satin along a stroked path, entered from its nearer end."""
    runs: list[Run] = []
    line = _oriented(item.geom, start)
    if params.underlay and item.width >= 1.5:
        runs += _runs("underlay", [gen.running(line, 2.0)], item.oid)
        line = LineString(list(line.coords)[::-1])  # satin comes back over the underlay
    satin = gen.satin_line(line, item.width, params.satin_spacing_mm, params.pull_compensation_mm, params.max_stitch_mm)
    return runs + _runs("satin", [satin], item.oid)


def sew_strokes(item: "_Item", angle: float, params: StitchParams, start: Pt) -> list[Run]:
    """Small lettering: satin along every stroke of a letter (see stitch/strokes.py)."""
    poly: Polygon = item.geom
    limits = thread_limits(params)
    ink = item.ink or st.InkMap([poly], *_extent(poly))
    runs: list[Run] = []
    if item.method == "dot":
        res = st.satin_dot(poly, ink, limits, 0.3)
        item.narrow_spots = res.narrow_spots
        if len(res.points) >= 2:
            runs.append(Run("satin", res.points, item.oid))
        return runs
    shape = st.analyse_stroke_shape(poly)
    strokes = list(shape.strokes)
    if not strokes:  # nothing left after thinning (a speck): sew it as a dot
        res = st.satin_dot(poly, ink, limits, 0.3)
        item.narrow_spots = res.narrow_spots
        return [Run("satin", res.points, item.oid)] if len(res.points) >= 2 else []
    width = st.small_stroke_width(poly)
    spacing = _stroke_spacing(params, width)
    spots: list[Pt] = []
    cur = start
    todo = list(strokes)
    while todo:  # nearest stroke next, entered from its nearer end
        k = min(range(len(todo)), key=lambda i: _stroke_entry(todo[i], cur)[0])
        stroke = todo.pop(k)
        # the satin is computed on the stroke as found and reversed when entered from
        # the other end, so the stitching does not depend on the sewing order
        backwards = False
        if stroke.closed:
            stroke = st.Stroke(_rotate_ring(stroke.line, cur), True)
        else:
            backwards = dist(cur, stroke.line.coords[-1]) < dist(cur, stroke.line.coords[0])
        under = st.stroke_underlay(stroke, poly) if params.underlay else []
        if len(under) >= 2:
            runs.append(Run("underlay", under[::-1] if backwards else under, item.oid))
            if not stroke.closed:  # the satin comes back over its centre run
                backwards = not backwards
        res = st.satin_stroke(stroke, poly, ink, limits, spacing, params.pull_compensation_mm, params.max_stitch_mm)
        spots.extend(res.narrow_spots)
        if len(res.points) >= 2:
            pts = res.points[::-1] if backwards else res.points
            runs.append(Run(res.kind, pts, item.oid))
            cur = pts[-1]
    for patch in shape.patches:  # square corners the stroke columns cut off
        pieces = gen.satin_area(patch, spacing, cur, params.max_stitch_mm)
        for piece in pieces:
            if len(piece) >= 2:
                runs.append(Run("satin", piece, item.oid))
                cur = piece[-1]
    item.narrow_spots = spots
    return runs


def sew_columns(item: "_Item", angle: float, params: StitchParams, start: Pt) -> list[Run]:
    """Smart direction: satin along every column of a narrow section (see stitch/smart.py)."""
    pieces = smart.sew_shape(
        smart.analyse_columns(item.geom), item.geom, start,
        lambda width: _stroke_spacing(params, width), params.satin_spacing_mm,
        params.pull_compensation_mm, params.max_stitch_mm, thread_limits(params).min_column_mm, params.underlay,
    )
    return [Run(kind, pts, item.oid) for kind, pts in pieces if len(pts) >= 2]


def sew_running(item: "_Item", angle: float, params: StitchParams, start: Pt) -> list[Run]:
    """Running (or triple) stitch along a thin line."""
    line = _oriented(item.geom, start)
    return _runs("running", [gen.running(line, params.running_stitch_length_mm, params.running_repeats)], item.oid)


def sew_outline(item: "_Item", angle: float, params: StitchParams, start: Pt) -> list[Run]:
    """Running (or triple) stitch around a shape's outline and holes."""
    pieces = gen.ring_from(item.geom, start, params.running_stitch_length_mm, holes=True)
    if params.running_repeats >= 3:
        pieces = [gen.running(LineString(p), params.running_stitch_length_mm, 3) for p in pieces]
    return _runs("running", pieces, item.oid)


# --------------------------------------------------------------------------- registration

register(StitchMethod("fill", sew_fill, directional=True, density=True))
register(StitchMethod("satin", sew_satin, directional=True, density=True))
register(StitchMethod("satin_band", sew_satin_band, density=True))
register(StitchMethod("satin_line", sew_satin_line, density=True))
register(StitchMethod("column", sew_columns, density=True, multi_piece=True))
register(StitchMethod("stroke", sew_strokes, density=True, multi_piece=True))
register(StitchMethod("dot", sew_strokes, density=True, multi_piece=True))
register(StitchMethod("running", sew_running, outline=True))
register(StitchMethod("outline", sew_outline, outline=True))
