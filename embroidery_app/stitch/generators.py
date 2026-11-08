"""Stitch generators. Each returns a list of *pieces*; a piece is a list of
needle points (mm) that can be sewn continuously. Pieces of the same object that
could not be joined by an internal travel path need a jump (and maybe a trim).
"""

from __future__ import annotations

import math
from typing import Callable

from shapely.geometry import LineString, Point, Polygon
from shapely.ops import unary_union

from ..geometry import Pt, dist, resample_line, ring_walk, segment_inside, subdivide
from .rows import Chain, best_satin_angle, build_chains, chain_endpoints, scan_rows, to_rotated, unrotate

Pieces = list[list[Pt]]
TATAMI_OFFSETS = (0.0, 0.5, 0.25, 0.75)  # brick pattern, hides penetration lines
MAX_TRAVEL_MM = 40.0


# --------------------------------------------------------------------------- chains

def _sew_chains(
    poly: Polygon,
    chains: list[Chain],
    angle: float,
    start: Pt,
    row_points: Callable[[Chain, bool, bool], list[Pt]],
    travel_step: float,
) -> Pieces:
    pieces: Pieces = []
    current: list[Pt] = []
    pos = start
    remaining = [c for c in chains if len(c)]
    sewn: list[Polygon] = []  # regions already covered by top stitching

    def visible(path: list[Pt]) -> float:
        """Length of a travel path lying on top of already-sewn rows."""
        if not sewn or len(path) < 2:
            return 0.0
        covered = unary_union(sewn).buffer(-0.4)
        return LineString(path).intersection(covered).length if not covered.is_empty else 0.0

    while remaining:
        pos_r = to_rotated(pos, angle)
        best = None
        for ci, chain in enumerate(remaining):
            for reverse, start_left, sp, _ep in chain_endpoints(chain):
                d = dist(pos_r, sp)
                if best is None or d < best[0]:
                    best = (d, ci, reverse, start_left)
        _, ci, reverse, start_left = best  # type: ignore[misc]
        chain = remaining.pop(ci)
        pts = unrotate(row_points(chain, reverse, start_left), angle)
        if not pts:
            continue
        if current:
            a, b = current[-1], pts[0]
            straight = dist(a, b)
            if straight <= travel_step:
                current.extend(subdivide(a, b, travel_step))
            else:
                # candidate travel routes; prefer the one that shows least on finished stitching
                options: list[tuple[float, list[Pt]]] = []
                walk = ring_walk(poly, a, b, travel_step)
                if walk is not None:
                    walk_len = sum(dist(p, q) for p, q in zip(walk, walk[1:]))
                    if walk_len <= MAX_TRAVEL_MM:
                        options.append((visible(walk) + 0.02 * walk_len, walk))
                if straight <= MAX_TRAVEL_MM and segment_inside(poly, a, b):
                    line = [a, *subdivide(a, b, travel_step)]
                    options.append((visible(line) + 0.02 * straight, line))
                options.sort(key=lambda o: o[0])
                if options and options[0][0] <= 2.0:
                    current.extend(options[0][1][1:])
                else:
                    pieces.append(current)  # a jump (trimmed if long) is cleaner than a visible travel
                    current = []
        current.extend(pts if not current or dist(current[-1], pts[0]) > 1e-6 else pts[1:])
        pos = current[-1]
        region = _chain_region(chain, angle)
        if region is not None:
            sewn.append(region)
    if current:
        pieces.append(current)
    return pieces


def _chain_region(chain: Chain, angle: float) -> Polygon | None:
    """Area covered by a chain: left row ends down, right row ends back up."""
    if len(chain) < 2:
        return None
    left = [(x0, y) for y, (x0, _) in zip(chain.ys, chain.segs)]
    right = [(x1, y) for y, (_, x1) in zip(chain.ys, chain.segs)][::-1]
    region = Polygon(unrotate(left + right, angle))
    return region if region.is_valid else region.buffer(0)


def _oriented_rows(chain: Chain, reverse: bool) -> tuple[list[float], list[tuple[float, float]]]:
    if reverse:
        return chain.ys[::-1], chain.segs[::-1]
    return chain.ys, chain.segs


# --------------------------------------------------------------------------- tatami fill

def tatami_fill(
    poly: Polygon,
    angle: float,
    spacing: float,
    stitch_len: float,
    start: Pt,
    max_stitch: float = 7.0,
) -> Pieces:
    if poly.is_empty:
        return []
    ys, rows = scan_rows(poly, angle, spacing)
    chains = build_chains(ys, rows, min_overlap=min(0.2, spacing / 2))
    edge_margin = min(0.6, stitch_len * 0.3)

    def row_points(chain: Chain, reverse: bool, start_left: bool) -> list[Pt]:
        out: list[Pt] = []
        left_to_right = start_left
        for y, (x0, x1) in zip(*_oriented_rows(chain, reverse)):
            idx = int(round(y / spacing))
            off = TATAMI_OFFSETS[idx % len(TATAMI_OFFSETS)] * stitch_len
            k0 = math.ceil((x0 + edge_margin - off) / stitch_len)
            grid = []
            x = off + k0 * stitch_len
            while x < x1 - edge_margin:
                grid.append(x)
                x += stitch_len
            xs = [x0, *grid, x1] if left_to_right else [x1, *grid[::-1], x0]
            row = [(xx, y) for xx in xs]
            if out:
                out.extend(subdivide(out[-1], row[0], max_stitch))
                row = row[1:]
            out.extend(row)
            left_to_right = not left_to_right
        return out

    return _sew_chains(poly, chains, angle, start, row_points, travel_step=min(2.5, stitch_len))


# --------------------------------------------------------------------------- satin

def satin_area(
    poly: Polygon,
    spacing: float,
    start: Pt,
    max_stitch: float = 7.0,
    angle: float | None = None,
) -> Pieces:
    """Zig-zag satin across a narrow polygon at its best (shortest-row) angle."""
    if poly.is_empty:
        return []
    if angle is None:
        angle, _ = best_satin_angle(poly)
    ys, rows = scan_rows(poly, angle, spacing)
    chains = build_chains(ys, rows, min_overlap=min(0.1, spacing / 2))

    def row_points(chain: Chain, reverse: bool, start_left: bool) -> list[Pt]:
        out: list[Pt] = []
        left = start_left
        for y, (x0, x1) in zip(*_oriented_rows(chain, reverse)):
            p = (x0 if left else x1, y)
            if out and dist(out[-1], p) > max_stitch:
                out.extend(subdivide(out[-1], p, max_stitch))  # split satin
            else:
                out.append(p)
            left = not left
        return out

    return _sew_chains(poly, chains, angle, start, row_points, travel_step=2.0)


def satin_line(
    line: LineString,
    width: float,
    spacing: float,
    pull: float,
    max_stitch: float = 7.0,
    width_at: Callable[[Pt], float] | None = None,
    closed: bool = False,
) -> list[Pt]:
    """True satin column following a centreline (SVG strokes, ring-shaped bands).

    ``width_at`` optionally gives the local column width for variable-width bands."""
    centre = resample_line(line, spacing)
    if closed and len(centre) > 2 and dist(centre[0], centre[-1]) < 1e-6:
        centre = centre[:-1]
    if len(centre) < 2:
        return []
    out: list[Pt] = []
    n = len(centre)
    for i, (x, y) in enumerate(centre):
        half = (width_at((x, y)) if width_at else width) / 2 + pull
        if closed:
            ax, ay = centre[(i - 1) % n]
            bx, by = centre[(i + 1) % n]
        else:
            ax, ay = centre[max(0, i - 1)]
            bx, by = centre[min(n - 1, i + 1)]
        tx, ty = bx - ax, by - ay
        norm = math.hypot(tx, ty) or 1.0
        nx, ny = -ty / norm, tx / norm
        side = 1 if i % 2 == 0 else -1
        p = (x + nx * half * side, y + ny * half * side)
        if out and dist(out[-1], p) > max_stitch:
            out.extend(subdivide(out[-1], p, max_stitch))
        else:
            out.append(p)
    if closed and out:
        out.append(out[0] if n % 2 == 0 else out[1])  # close the loop on the matching side
    return out


def band_centerline(poly: Polygon) -> LineString | None:
    """Closed centreline of a ring-shaped band (polygon with exactly one hole),
    e.g. a badge border or circle outline. None if the shape is not a clean band."""
    if len(poly.interiors) != 1:
        return None
    width = 2 * poly.area / max(poly.length, 1e-6)
    inner = poly.buffer(-width * 0.45)
    parts = [p for p in _polys(inner) if len(p.interiors) == 1]
    if len(parts) != 1 or parts[0].area < 0.6 * (inner.area if not inner.is_empty else 1):
        return None
    ext = LineString(parts[0].exterior.coords)
    hole = LineString(parts[0].interiors[0].coords)
    # centreline halfway between the shrunken band's two edges
    pts = resample_line(ext, max(0.2, width / 4))
    mid = []
    for p in pts:
        q = hole.interpolate(hole.project(Point(p)))
        mid.append(((p[0] + q.x) / 2, (p[1] + q.y) / 2))
    if len(mid) < 4:
        return None
    mid.append(mid[0])
    return LineString(mid).simplify(0.05)


def satin_band(poly: Polygon, centre: LineString, spacing: float, pull: float, start: Pt, max_stitch: float = 7.0) -> list[Pt]:
    boundary = poly.boundary
    coords = list(centre.coords)[:-1]
    k = min(range(len(coords)), key=lambda i: dist(coords[i], start))
    ring = LineString(coords[k:] + coords[:k] + [coords[k]])
    return satin_line(
        ring, 0.0, spacing, pull, max_stitch,
        width_at=lambda p: 2 * boundary.distance(Point(p)),
        closed=True,
    )


# --------------------------------------------------------------------------- running / outlines

def running(line: LineString, stitch_len: float, repeats: int = 1) -> list[Pt]:
    pts = resample_line(line, stitch_len)
    if repeats < 3 or len(pts) < 2:
        return pts
    out = [pts[0]]
    for a, b in zip(pts, pts[1:]):
        out.extend([b, a, b])  # bean / triple stitch
    return out


def ring_from(poly: Polygon, start: Pt, step: float, holes: bool = False) -> Pieces:
    """Running stitch around the polygon boundary, starting near ``start``."""
    rings = [poly.exterior, *(poly.interiors if holes else [])]
    pieces: Pieces = []
    for ring in rings:
        line = LineString(ring.coords)
        if line.length < step:
            continue
        d0 = line.project(Point(start))
        coords = [line.interpolate((d0 + t) % line.length) for t in _steps(line.length, step)]
        pts = [(p.x, p.y) for p in coords]
        pts.append(pts[0])
        pieces.append(pts)
    return pieces


def _steps(length: float, step: float) -> list[float]:
    n = max(3, math.ceil(length / step))
    return [length * k / n for k in range(n)]


# --------------------------------------------------------------------------- underlay

def fill_underlay(poly: Polygon, angle: float, spacing: float, start: Pt, smart: bool = False) -> Pieces:
    """Edge run + lattice. ``smart`` drops the lattice where the shape is too thin for
    it to do anything but sit on top of the edge run sewn along both edges."""
    pieces: Pieces = []
    edge = poly.buffer(-0.5)
    for part in _polys(edge):
        if part.length > 4:
            pieces.extend(ring_from(part, start, 2.5))
    cur = pieces[-1][-1] if pieces else start
    lattice = poly.buffer(-0.9)
    chosen = _lattice(lattice, angle, spacing, cur)
    if smart and chosen:
        # morphological opening: drops lattice where the shape is < ~3.8 mm wide (there it
        # would only cover the edge run). Accepted only if it really saves stitches and does
        # not split the lattice into more pieces (each extra piece = jump/trim = machine time).
        opened = lattice.intersection(lattice.buffer(-1.0).buffer(1.0))
        alt = _lattice(opened, angle, spacing, cur)
        if len(alt) <= len(chosen) and sum(map(len, alt)) < sum(map(len, chosen)):
            chosen = alt
    return pieces + chosen


def _lattice(region, angle: float, spacing: float, start: Pt) -> Pieces:
    pieces: Pieces = []
    if region.is_empty or region.area <= 15:
        return pieces
    cur = start
    for part in _polys(region):
        pieces.extend(tatami_fill(part, angle + 90, spacing, 3.0, cur))
        cur = pieces[-1][-1] if pieces else cur
    return pieces


def satin_underlay(poly: Polygon, start: Pt) -> Pieces:
    inset = poly.buffer(-0.35)
    out: Pieces = []
    for part in _polys(inset):
        if part.area > 1.5:
            out.extend(ring_from(part, start, 2.0))
    return out


def _polys(geom) -> list[Polygon]:
    from ..geometry import iter_polygons

    return sorted(iter_polygons(geom), key=lambda p: -p.area)
