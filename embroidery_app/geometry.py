"""Shared geometry utilities (shapely based)."""

from __future__ import annotations

import math
from typing import Iterable, Iterator

import cv2
import numpy as np
from shapely import affinity
from shapely.geometry import LinearRing, LineString, MultiLineString, MultiPolygon, Point, Polygon
from shapely.geometry.base import BaseGeometry
from shapely.ops import substring, unary_union

Pt = tuple[float, float]


def iter_polygons(geom: BaseGeometry | None) -> Iterator[Polygon]:
    if geom is None or geom.is_empty:
        return
    if isinstance(geom, Polygon):
        yield geom
    elif hasattr(geom, "geoms"):
        for g in geom.geoms:
            yield from iter_polygons(g)


def iter_lines(geom: BaseGeometry | None) -> Iterator[LineString]:
    if geom is None or geom.is_empty:
        return
    if isinstance(geom, LineString):
        yield geom
    elif isinstance(geom, LinearRing):
        yield LineString(geom.coords)
    elif hasattr(geom, "geoms"):
        for g in geom.geoms:
            yield from iter_lines(g)


def clean_polygonal(geom: BaseGeometry) -> BaseGeometry:
    """Make valid and keep only polygonal parts."""
    if geom.is_empty:
        return geom
    if not geom.is_valid:
        geom = geom.buffer(0)
    polys = list(iter_polygons(geom))
    if not polys:
        return Polygon()
    return polys[0] if len(polys) == 1 else MultiPolygon(polys)


def mask_to_polygons(mask: np.ndarray, epsilon_px: float = 0.7) -> list[Polygon]:
    """Binary mask (uint8 0/255) -> list of polygons with holes, in pixel coords."""
    contours, hierarchy = cv2.findContours(mask, cv2.RETR_CCOMP, cv2.CHAIN_APPROX_NONE)
    if hierarchy is None:
        return []
    hierarchy = hierarchy[0]
    polys: list[Polygon] = []

    def ring(c: np.ndarray) -> list[Pt] | None:
        if epsilon_px > 0:
            c = cv2.approxPolyDP(c, epsilon_px, True)
        pts = [(float(p[0][0]) + 0.5, float(p[0][1]) + 0.5) for p in c]
        return pts if len(pts) >= 3 else None

    for i, (nxt, prev, child, parent) in enumerate(hierarchy):
        if parent != -1:
            continue  # holes are handled with their parent
        shell = ring(contours[i])
        if shell is None:
            continue
        holes = []
        c = child
        while c != -1:
            h = ring(contours[c])
            if h is not None:
                holes.append(h)
            c = hierarchy[c][0]
        poly = Polygon(shell, holes)
        if not poly.is_valid:
            poly = poly.buffer(0)
        polys.extend(p for p in iter_polygons(poly) if p.area > 0)
    return polys


def rotate_pts(points: Iterable[Pt], angle_deg: float) -> list[Pt]:
    a = math.radians(angle_deg)
    ca, sa = math.cos(a), math.sin(a)
    return [(x * ca - y * sa, x * sa + y * ca) for x, y in points]


def rotate_geom(geom: BaseGeometry, angle_deg: float) -> BaseGeometry:
    return affinity.rotate(geom, angle_deg, origin=(0, 0))


def dist(a: Pt, b: Pt) -> float:
    return math.hypot(a[0] - b[0], a[1] - b[1])


def subdivide(a: Pt, b: Pt, max_len: float) -> list[Pt]:
    """Points strictly after ``a`` up to and including ``b`` with spacing <= max_len."""
    d = dist(a, b)
    n = max(1, math.ceil(d / max_len - 1e-9))
    return [(a[0] + (b[0] - a[0]) * k / n, a[1] + (b[1] - a[1]) * k / n) for k in range(1, n + 1)]


def resample_line(line: LineString, step: float, closed: bool = False) -> list[Pt]:
    """Evenly spaced points along a line (keeps sharp corners)."""
    coords = list(line.coords)
    if len(coords) < 2:
        return [tuple(c[:2]) for c in coords]  # type: ignore[misc]
    out: list[Pt] = [tuple(coords[0][:2])]  # type: ignore[list-item]
    for c in coords[1:]:
        p = tuple(c[:2])
        if dist(out[-1], p) < 1e-6:
            continue
        out.extend(subdivide(out[-1], p, step))  # type: ignore[arg-type]
    return out


def _walk_ring(ring, a: Pt, b: Pt, max_len: float) -> list[Pt]:
    """Shortest path from a to b along one ring (both assumed on/near the ring)."""
    line = LineString(ring.coords)
    length = line.length
    da, db = line.project(Point(a)), line.project(Point(b))
    fwd = (db - da) % length
    if fwd <= length - fwd:
        if da <= db:
            coords = list(substring(line, da, db).coords)
        else:
            coords = list(substring(line, da, length).coords) + list(substring(line, 0, db).coords)
    else:
        if db <= da:
            coords = list(substring(line, db, da).coords)[::-1]
        else:
            coords = (list(substring(line, db, length).coords) + list(substring(line, 0, da).coords))[::-1]
    path: list[Pt] = [a]
    for c in coords + [b]:
        p = (float(c[0]), float(c[1]))
        if dist(path[-1], p) > 1e-6:
            path.extend(subdivide(path[-1], p, max_len))
    return path


def ring_walk(poly: Polygon, a: Pt, b: Pt, max_len: float, tolerance: float = 0.6) -> list[Pt] | None:
    """Travel path from a to b along the polygon boundary.

    Same ring: shortest way round. Different rings (outer edge <-> hole): walk to the
    closest crossing point, cross straight (short), walk on. None if not possible.
    """
    rings = [poly.exterior, *poly.interiors]
    pa, pb = Point(a), Point(b)
    ra = [r for r in rings if r.distance(pa) <= tolerance]
    rb = [r for r in rings if r.distance(pb) <= tolerance]
    if not ra or not rb:
        return None
    for r in ra:
        if r in rb:
            return _walk_ring(r, a, b, max_len)
    from shapely.ops import nearest_points

    r1, r2 = ra[0], rb[0]
    p, q = nearest_points(r1, r2)
    p, q = (p.x, p.y), (q.x, q.y)
    if not segment_inside(poly, p, q):
        return None
    path = _walk_ring(r1, a, p, max_len)
    path.extend(subdivide(path[-1], q, max_len))
    path.extend(_walk_ring(r2, q, b, max_len)[1:])
    return path


def segment_inside(poly: BaseGeometry, a: Pt, b: Pt, slack: float = 0.15) -> bool:
    if dist(a, b) < 1e-6:
        return True
    return poly.buffer(slack).contains(LineString([a, b]))


def as_multiline(lines: list[LineString]) -> BaseGeometry:
    return lines[0] if len(lines) == 1 else MultiLineString(lines)


def union_all(geoms: list[BaseGeometry]) -> BaseGeometry:
    return unary_union(geoms) if geoms else Polygon()


# Pieces of one colour closer than 2x this are treated as touching. Covers the
# ~1 px hairline left between regions traced separately from a raster (0.08 mm at
# the default 12 px/mm) plus outline simplification; far below a sewable gap.
SEAM_JOIN_MM = 0.1


def merge_areas(
    pieces: Iterable[BaseGeometry],
    obstacles: BaseGeometry | None = None,
    seam_mm: float = SEAM_JOIN_MM,
) -> list[Polygon]:
    """Fuse same-colour area pieces into continuous regions.

    Only the *seams between different pieces* are bridged: a bridge is the zone
    within ``seam_mm`` of two pieces at once, clipped to the closed outline of the
    group (so nothing protrudes past the outer edge) and minus ``obstacles`` (other
    colours, so a bridge never covers a different colour, however thin). A piece's
    own outline and holes are never altered; pieces farther apart stay separate.
    """
    return [poly for poly, _, _ in merge_areas_indexed(pieces, obstacles, seam_mm)]


def merge_areas_indexed(
    pieces: Iterable[BaseGeometry],
    obstacles: BaseGeometry | None = None,
    seam_mm: float = SEAM_JOIN_MM,
) -> list[tuple[Polygon, list[int], bool]]:
    """``merge_areas`` that also reports, for every resulting region, the indexes of
    the input pieces it was built from and whether it is an input piece left
    untouched (no neighbour within reach) - so callers can carry identity/settings."""
    pieces = list(pieces)
    flat: list[tuple[Polygon, int]] = [
        (p, i) for i, g in enumerate(pieces) for p in iter_polygons(clean_polygonal(g))
    ]
    polys = [p for p, _ in flat]
    if len(polys) <= 1:
        return [(p, [i], True) for p, i in flat]
    from shapely import STRtree

    grown = [p.buffer(seam_mm, join_style="mitre", mitre_limit=2.0) for p in polys]
    tree = STRtree(grown)
    bridges: list[Polygon] = []
    near: set[int] = set()  # polygons with a same-colour neighbour within reach
    for i, g in enumerate(grown):
        for j in tree.query(g, predicate="intersects"):
            if j > i:
                bridges.extend(iter_polygons(g.intersection(grown[j])))
                near.update((i, int(j)))
    base = unary_union(polys)
    if bridges:
        bridge = unary_union(bridges)
        # the closed outline of the group: fills seam notches, never bulges past a straight edge
        envelope = base.buffer(seam_mm).buffer(-seam_mm)
        bridge = bridge.intersection(envelope)
        if obstacles is not None and not obstacles.is_empty:
            bridge = bridge.difference(obstacles)
        base = unary_union([base, *iter_polygons(bridge)])
    merged = list(iter_polygons(clean_polygonal(base)))
    # deterministic order (top-to-bottom, left-to-right) for stable output/caching
    merged.sort(key=lambda p: (round(p.bounds[1], 3), round(p.bounds[0], 3)))
    # every input polygon lies inside exactly one region (regions only grow)
    out_tree = STRtree(merged)
    sources: list[list[int]] = [[] for _ in merged]
    touched = [False] * len(merged)
    for n, (poly, piece) in enumerate(flat):
        pt = poly.representative_point()
        for k in out_tree.query(pt, predicate="intersects"):
            if piece not in sources[k]:
                sources[k].append(piece)
            touched[k] = touched[k] or n in near
            break
    pieces_per_region = [sum(1 for _, piece in flat if piece in s) for s in sources]
    return [
        (m, s, len(s) == 1 and not touched[k] and pieces_per_region[k] == 1)
        for k, (m, s) in enumerate(zip(merged, sources))
    ]
