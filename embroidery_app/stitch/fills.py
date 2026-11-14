"""Advanced fills, chosen as a stitch style for a colour or a shape.

  offset_fill   rings that follow the outline, inwards (Wilcom: Offset Fill)
  spiral_fill   one continuous spiral from the outline inwards (Wilcom: Spiral Fill)
  contour_fill  rows that run along a column and follow its curve, across its width
                (Wilcom: Contour); shapes that are not a column get rings
  radial_fill   rays from the shape's centre (Wilcom: Radial Fill); rays near the
                centre are dropped so the thread does not pile up there
  stipple_fill  one meandering line that never crosses itself (Wilcom: Stipple Fill)
  maze_fill     a right-angled meander, like a maze (Wilcom: Maze Fill)
  cross_stitch  crosses on a square grid, like hand cross stitch (Wilcom: Cross Stitch)
  shaded_fill   tatami whose rows open up from dense to light across the shape: shading
                with one colour (Wilcom: Accordion Spacing)

All are plain geometry with a fixed random seed, so the same shape always gets the
same stitches. Ring, spiral, contour, radial and shaded fills get an edge-run
foundation and pull compensation like other dense fills; stipple, maze and cross
stitch are open, light fills that are sewn without them (Wilcom switches them off
for cross stitch too).
"""

from __future__ import annotations

import math
from typing import TYPE_CHECKING

import numpy as np
import shapely
from shapely.geometry import LineString, MultiLineString, Point, Polygon, box

from ..geometry import Pt, dist, iter_polygons, subdivide
from ..models import Run, StitchParams
from . import generators as gen
from . import smart
from .outlines import _Path, _resampled
from .registry import StitchMethod, register

if TYPE_CHECKING:  # pragma: no cover
    from .planner import _Item

Pieces = list[list[Pt]]

RADIAL_HOLE_MM = 1.0  # rays stop this far from the centre
RADIAL_SPACING_FACTOR = 1.2  # radial rows are spread a little (Wilcom suggests +20 %)
SHADE_MAX_FACTOR = 4.0  # shaded fill: the lightest rows are this many times further apart
SEED = 20261114
CROSS_SHIFT_MM = 0.3  # back-slash diagonals sit this far right of the "/" ones: fewer drops per hole


def _join(pieces: Pieces, max_gap: float) -> Pieces:
    """Merge consecutive pieces whose ends are at most ``max_gap`` apart (a short
    straight connection instead of a jump)."""
    out: Pieces = []
    for p in pieces:
        if len(p) < 2:
            continue
        if out and dist(out[-1][-1], p[0]) <= max_gap:
            out[-1].extend(subdivide(out[-1][-1], p[0], max_gap)[:-1] + p)
        else:
            out.append(list(p))
    return out


def _ring_from(coords: list[Pt], start: Pt) -> list[Pt]:
    """A closed ring as a point list beginning at the vertex nearest to ``start``."""
    pts = coords[:-1] if dist(coords[0], coords[-1]) < 1e-9 else list(coords)
    k = min(range(len(pts)), key=lambda i: dist(pts[i], start))
    pts = pts[k:] + pts[:k]
    return pts + [pts[0]]


def _even(points: list[Pt], step: float) -> list[Pt]:
    """Evenly spaced points along a polyline, ignoring its vertices (smooth curves)."""
    line = LineString(points)
    n = max(2, math.ceil(line.length / step - 1e-9))
    return [(q.x, q.y) for q in (line.interpolate(line.length * k / n) for k in range(n + 1))]


def _run(points: list[Pt], step: float, closed: bool = False) -> list[Pt]:
    """Running stitch along a polyline: even steps, sharp corners kept."""
    if len(points) < 2:
        return list(points)
    return _resampled(_Path(points, closed), step)


# --------------------------------------------------------------------------- offset / spiral

def _rings(poly: Polygon, gap: float) -> list[list[tuple[int, list[Pt]]]]:
    """Inward offset levels: per level, (part index, ring coords) for every ring."""
    levels = []
    k = 0
    while True:
        inner = poly.buffer(-(k + 0.5) * gap, join_style="round")
        if inner.is_empty or inner.area < gap * gap:
            break
        level = []
        for part in iter_polygons(inner):
            level.append(list(part.exterior.coords))
            level.extend(list(r.coords) for r in part.interiors)
        levels.append(level)
        k += 1
        if k > 2000:
            break
    return levels


def offset_fill(poly: Polygon, gap: float, step: float, start: Pt) -> Pieces:
    """Rings at ``gap`` from each other. The nearest ring of any level comes next, so
    the rings that follow the outline run inwards one after the other, and the rings
    around a hole then run outwards from it: neighbours are joined, not jumped."""
    todo = [LineString(r) for level in _rings(poly, gap) for r in level]
    pieces: Pieces = []
    cur = start
    if not todo:
        return pieces
    tree = shapely.STRtree(todo)
    left = set(range(len(todo)))
    while left:
        near = [int(i) for i in tree.query(Point(cur).buffer(3 * gap)) if int(i) in left]
        pool = near or list(left)
        k = min(pool, key=lambda i: todo[i].distance(Point(cur)))
        left.remove(k)
        ring = _run(_ring_from(list(todo[k].coords), cur), step, closed=True)
        pieces.append(ring)
        cur = ring[-1]
    return _join(pieces, 2.5 * gap)


def _aligned(ring: list[Pt], anchor: Pt, ccw: bool) -> LineString:
    line = LineString(ring)
    if shapely.is_ccw(shapely.linearrings(ring)) != ccw:
        line = LineString(ring[::-1])
    pts = list(line.coords)[:-1]
    k = min(range(len(pts)), key=lambda i: dist(pts[i], anchor))
    pts = pts[k:] + pts[:k]
    return LineString(pts + [pts[0]])


def spiral_fill(poly: Polygon, gap: float, step: float, start: Pt) -> Pieces:
    """Each ring blends into the next one along its length: one continuous spiral.
    Shapes with holes, and the part of a shape left once the rings split into
    several pieces, are filled with rings instead."""
    if poly.interiors:
        return offset_fill(poly, gap, step, start)
    levels = _rings(poly, gap)
    chain = []
    for level in levels:
        if len(level) != 1:
            break
        chain.append(level[0])
    if len(chain) < 2:
        return offset_fill(poly, gap, step, start)
    lines = []
    anchor = min(chain[0], key=lambda p: dist(p, start))
    for ring in chain:
        line = _aligned(ring, anchor, ccw=True)
        lines.append(line)
        anchor = line.coords[0]
    path: list[Pt] = []
    for a, b in zip(lines, lines[1:]):
        n = max(8, math.ceil(a.length / step))
        for i in range(n):
            t = i / n
            p, q = a.interpolate(t, normalized=True), b.interpolate(t, normalized=True)
            path.append((p.x + (q.x - p.x) * t, p.y + (q.y - p.y) * t))
    last = lines[-1]
    path.extend(_run(list(last.coords), step, closed=True))
    pieces = [path]
    # whatever the spiral did not reach (the rings split further in)
    rest = poly.buffer(-(len(chain) - 0.5) * gap - gap * 0.5)
    for part in iter_polygons(rest):
        if part.area > gap * gap * 4:
            pieces += offset_fill(part.buffer(gap * 0.5), gap, step, pieces[-1][-1])
    return _join(pieces, 2.5 * gap)


# --------------------------------------------------------------------------- contour

def contour_fill(poly: Polygon, gap: float, step: float, start: Pt) -> Pieces:
    """Rows along a column, at even shares of its width: they bend with the column.
    Not a single column: rings that follow the outline instead."""
    shape = smart.analyse_columns(poly) if smart.fits_columns(poly) else None
    if shape is None or not shape.ok or len(shape.columns) != 1 or shape.columns[0].closed:
        return offset_fill(poly, gap, step, start)
    col = shape.columns[0]
    if dist(start, tuple(col.pts[-1])) < dist(start, tuple(col.pts[0])):
        col = col.reversed()
    s = np.linspace(0.0, col.length, max(2, math.ceil(col.length / step) + 1))
    p, nrm, left, right = col.at(s)
    rows = max(2, math.ceil(float(np.max(left + right)) / gap))
    path: list[Pt] = []
    for k in range(rows):
        f = (k + 0.5) / rows  # share of the width, from the left edge
        off = left - f * (left + right)
        q = p + nrm * off[:, None]
        row = [(float(x), float(y)) for x, y in q]
        path.extend(row if k % 2 == 0 else row[::-1])
    keep = poly.buffer(0.05)
    pts = [pt for pt in path if keep.contains(Point(pt))]  # clip the tips of curved rows
    return [pts] if len(pts) >= 2 else offset_fill(poly, gap, step, start)


# --------------------------------------------------------------------------- radial

def radial_fill(poly: Polygon, gap: float, step: float, start: Pt) -> Pieces:
    centre = poly.centroid if poly.contains(poly.centroid) else poly.representative_point()
    cx, cy = centre.x, centre.y
    reach = max(Point(cx, cy).distance(Point(c)) for c in poly.exterior.coords) + 1.0
    spacing = gap * RADIAL_SPACING_FACTOR
    dtheta = spacing / reach
    n = max(8, 2 ** math.ceil(math.log2(2 * math.pi / dtheta)))  # a power of two: clean dropping
    dtheta = 2 * math.pi / n
    keep = poly.buffer(-0.05)
    rays: list[list[Pt]] = []
    for i in range(n):
        level = (i & -i) if i else n  # largest power of two dividing i
        r0 = max(RADIAL_HOLE_MM, spacing / (2 * level * dtheta))  # where this ray is needed
        a = i * dtheta
        ray = LineString([(cx + r0 * math.cos(a), cy + r0 * math.sin(a)), (cx + reach * math.cos(a), cy + reach * math.sin(a))])
        inside = ray.intersection(keep)
        segs = [s for s in getattr(inside, "geoms", [inside]) if isinstance(s, LineString) and s.length > 1.0]
        segs.sort(key=lambda s: s.distance(Point(cx, cy)))
        for depth, seg in enumerate(segs):  # beyond a hole or a notch the ray goes on
            a, b = seg.coords[0], seg.coords[-1]
            if Point(a).distance(Point(cx, cy)) > Point(b).distance(Point(cx, cy)):
                a, b = b, a
            rays.append((depth, a, b))
    pieces: Pieces = []
    inside = poly.buffer(0.1)
    for depth in sorted({r[0] for r in rays}):  # nearest parts of all rays first, then the parts beyond
        for k, (_, inner, outer) in enumerate([r for r in rays if r[0] == depth]):  # out, in, out ...
            a, b = (inner, outer) if k % 2 == 0 else (outer, inner)
            seg = [a, *subdivide(a, b, step)]
            link = LineString([pieces[-1][-1], a]) if pieces else None
            if link is not None and _hidden_link(link, (cx, cy), spacing) and inside.contains(link):
                pieces[-1].extend(subdivide(pieces[-1][-1], a, step)[:-1] + seg)
            else:  # the step to the next ray would leave the shape: a jump instead
                pieces.append(seg)
    if pieces and dist(start, pieces[-1][-1]) < dist(start, pieces[0][0]):
        pieces = [p[::-1] for p in pieces[::-1]]
    return pieces


def _hidden_link(link: LineString, centre: Pt, spacing: float) -> bool:
    """A step between two rays that the rays hide: short, or running along a ray
    (nearly radial). A long sideways step would lie on top of the rays."""
    if link.length <= 3 * spacing:
        return True
    (x0, y0), (x1, y1) = link.coords[0], link.coords[-1]
    mx, my = (x0 + x1) / 2 - centre[0], (y0 + y1) / 2 - centre[1]
    dx, dy = x1 - x0, y1 - y0
    cross = abs(mx * dy - my * dx) / max(math.hypot(mx, my) * math.hypot(dx, dy), 1e-9)
    return cross < math.sin(math.radians(8))


# --------------------------------------------------------------------------- stipple / maze

def _tree_loop(edges: list[tuple[Pt, Pt]], width: float, square: bool = False) -> list[list[Pt]]:
    """The outline of a tree drawn with thick lines: a closed meander that never crosses
    itself and visits every branch. One loop per connected tree."""
    if not edges:
        return []
    lines = MultiLineString([list(e) for e in edges])
    style = dict(cap_style="square", join_style="mitre") if square else dict(cap_style="round", join_style="round")
    blob = lines.buffer(width, **style)
    loops = []
    for part in iter_polygons(blob):
        loops.append(list(part.exterior.simplify(0.05).coords))
    return loops


def _spanning_tree(nodes: int, edges: list[tuple[int, int]], rng: np.random.Generator) -> list[tuple[int, int]]:
    """Random spanning forest (Kruskal with random weights)."""
    parent = list(range(nodes))

    def find(a: int) -> int:
        while parent[a] != a:
            parent[a] = parent[parent[a]]
            a = parent[a]
        return a

    out = []
    for i in rng.permutation(len(edges)):
        a, b = edges[i]
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[ra] = rb
            out.append((a, b))
    return out


def _poisson(region: Polygon, r: float, rng: np.random.Generator) -> np.ndarray:
    """Points at least ``r`` apart covering ``region`` (dart throwing on a grid)."""
    minx, miny, maxx, maxy = region.bounds
    cell = r / math.sqrt(2)
    cols, rows = int((maxx - minx) / cell) + 1, int((maxy - miny) / cell) + 1
    grid = -np.ones((rows, cols), dtype=int)
    pts: list[tuple[float, float]] = []
    tries = int(region.area / (r * r) * 30) + 50
    cx, cy = rng.uniform(minx, maxx, tries), rng.uniform(miny, maxy, tries)
    keep = shapely.contains_xy(region, cx, cy)  # candidates inside, tested in one go
    for x, y in zip(cx[keep], cy[keep]):
        gx, gy = int((x - minx) / cell), int((y - miny) / cell)
        ok = True
        for yy in range(max(0, gy - 2), min(rows, gy + 3)):
            for xx in range(max(0, gx - 2), min(cols, gx + 3)):
                j = grid[yy, xx]
                if j >= 0 and (pts[j][0] - x) ** 2 + (pts[j][1] - y) ** 2 < r * r:
                    ok = False
                    break
            if not ok:
                break
        if ok:
            grid[gy, gx] = len(pts)
            pts.append((float(x), float(y)))
    return np.array(pts)


def stipple_fill(poly: Polygon, spacing: float, step: float, start: Pt) -> Pieces:
    rng = np.random.default_rng(SEED)
    region = poly.buffer(-spacing * 0.5)
    pieces: Pieces = []
    for part in iter_polygons(region):
        pts = _poisson(part, spacing, rng)
        if len(pts) < 2:
            continue
        tri = shapely.delaunay_triangles(shapely.multipoints(pts), only_edges=True)
        index = {(round(x, 6), round(y, 6)): i for i, (x, y) in enumerate(pts)}
        edges = []
        inside = part.buffer(spacing * 0.2)
        for e in getattr(tri, "geoms", []):
            a, b = e.coords[0], e.coords[-1]
            if math.dist(a, b) <= 1.8 * spacing and inside.contains(e):
                edges.append((index[(round(a[0], 6), round(a[1], 6))], index[(round(b[0], 6), round(b[1], 6))]))
        tree = _spanning_tree(len(pts), edges, rng)
        loops = _tree_loop([(tuple(pts[a]), tuple(pts[b])) for a, b in tree], spacing * 0.25)
        pieces += [_even(_ring_from(loop, start), step) for loop in loops]
    return _order(pieces, start)


def maze_fill(poly: Polygon, spacing: float, step: float, start: Pt) -> Pieces:
    rng = np.random.default_rng(SEED)
    region = poly.buffer(-spacing * 0.5)
    pieces: Pieces = []
    for part in iter_polygons(region):
        minx, miny, maxx, maxy = part.bounds
        xs = np.arange(minx + spacing / 2, maxx, spacing)
        ys = np.arange(miny + spacing / 2, maxy, spacing)
        shapely.prepare(part)
        ids: dict[tuple[int, int], int] = {}
        nodes: list[Pt] = []
        for j, y in enumerate(ys):
            for i, x in enumerate(xs):
                if part.contains(Point(x, y)):
                    ids[(i, j)] = len(nodes)
                    nodes.append((float(x), float(y)))
        edges = []
        for (i, j), a in ids.items():
            for di, dj in ((1, 0), (0, 1)):
                b = ids.get((i + di, j + dj))
                if b is not None and part.contains(LineString([nodes[a], nodes[b]])):
                    edges.append((a, b))
        tree = _spanning_tree(len(nodes), edges, rng)
        loops = _tree_loop([(nodes[a], nodes[b]) for a, b in tree], spacing * 0.25, square=True)
        pieces += [_run(_ring_from(loop, start), step, closed=True) for loop in loops]
    return _order(pieces, start)


def _order(pieces: Pieces, start: Pt) -> Pieces:
    out: Pieces = []
    cur = start
    todo = [p for p in pieces if len(p) >= 2]
    while todo:
        k = min(range(len(todo)), key=lambda i: min(dist(cur, q) for q in todo[i][::4]))
        p = todo.pop(k)
        if dist(p[0], p[-1]) < 1e-6:  # a loop: start at its point nearest to the needle
            p = _ring_from(p, cur)
        out.append(p)
        cur = p[-1]
    return out


# --------------------------------------------------------------------------- cross stitch

def cross_stitch(poly: Polygon, cell: float, start: Pt, passes: int = 2) -> Pieces:
    """Crosses on a square grid aligned to the design, cells at least half covered.
    Each diagonal line of crosses is sewn as one straight run, there and back
    (``passes`` = 2), so the needle moves on to the next line at the shape's edge
    instead of along visible cell edges."""
    minx, miny, maxx, maxy = poly.bounds
    c0, r0 = math.floor(minx / cell), math.floor(miny / cell)
    c1, r1 = math.ceil(maxx / cell), math.ceil(maxy / cell)
    shapely.prepare(poly)
    cells = set()
    for r in range(r0, r1):
        for c in range(c0, c1):
            sq = box(c * cell, r * cell, (c + 1) * cell, (r + 1) * cell)
            if poly.intersects(sq) and poly.intersection(sq).area >= 0.5 * cell * cell:
                cells.add((c, r))
    pieces: Pieces = []
    for slope in (1, -1):  # "/" then "\"
        # cells on one diagonal line share c + r ("/") or c - r ("\")
        lines: dict[int, list[tuple[int, int]]] = {}
        for c, r in cells:
            lines.setdefault(c + r if slope == 1 else c - r, []).append((c, r))
        for key in sorted(lines):
            run = sorted(lines[key])
            groups, cur = [], [run[0]]
            for a, b in zip(run, run[1:]):
                if b[0] == a[0] + 1:
                    cur.append(b)
                else:
                    groups.append(cur)
                    cur = [b]
            groups.append(cur)
            for g in groups:
                if slope == 1:  # bottom-left to top-right of each cell (y down)
                    pts = [(g[0][0] * cell, (g[0][1] + 1) * cell)] + [((c + 1) * cell, r * cell) for c, r in g]
                else:  # top-left to bottom-right, shifted CROSS_SHIFT_MM: its own needle holes
                    sh = CROSS_SHIFT_MM
                    pts = [(g[0][0] * cell + sh, g[0][1] * cell)] + [((c + 1) * cell + sh, (r + 1) * cell) for c, r in g]
                seq = list(pts)
                for p in range(1, passes):
                    seq += (pts[::-1] if p % 2 else pts)[1:]
                pieces.append(seq)
    return _join(_order(pieces, start), cell * 1.5)


# --------------------------------------------------------------------------- shaded (accordion)

def shaded_rows(spacing: float, factor: float = SHADE_MAX_FACTOR, reverse: bool = False):
    """Row positions from dense (``spacing``) to light (``factor`` x) across the shape;
    ``reverse`` puts the dense end at the other side."""
    def rows(lo: float, hi: float) -> np.ndarray:
        ys, y = [], lo + spacing / 2
        span = max(hi - lo, 1e-6)
        while y < hi:
            ys.append(y)
            t = (y - lo) / span
            y += spacing * (1 + (factor - 1) * t)
        out = np.array(ys)
        return (lo + hi) - out[::-1] if reverse else out
    return rows


BLEND_FACTOR = 8.0  # a blend colour opens up to 8x its row gap at its light end


def blend_rows(spacing: float, reverse: bool = False, factor: float = BLEND_FACTOR):
    """Rows of one colour of a blend. Across the shape (t from 0 to 1) this colour sews
    a share 1 - t of the full row density, the other colour the share t: the gap is
    ``spacing / share``, so the two always add up to one full fill and the colours mix
    evenly instead of leaving thin patches in the middle. A share never drops below
    1 / ``factor``, so a few rows reach right across."""
    def rows(lo: float, hi: float) -> np.ndarray:
        ys, y = [], lo + spacing / 2
        span = max(hi - lo, 1e-6)
        while y < hi:
            ys.append(y)
            share = max(1.0 - (y - lo) / span, 1.0 / factor)
            y += spacing / share
        out = np.array(ys)
        return (lo + hi) - out[::-1] if reverse else out
    return rows


def _blend(poly, angle, p, start, reverse=False):
    """One colour of a blend: dense at one side, nearly gone at the other. The other
    colour is the same shape shaded the opposite way, so together they cover it."""
    return gen.tatami_fill(poly, angle, p.fill_spacing_mm, p.fill_stitch_length_mm, start, p.max_stitch_mm,
                           row_ys=blend_rows(p.fill_spacing_mm, reverse=reverse))


def _sew_blend_partner(item, angle, params, start):
    """The second colour of a blend: no foundation of its own (the first colour's is
    under it), shaded the opposite way."""
    from .methods import _compensate  # noqa: PLC0415
    poly = _compensate(item.geom, params)
    return [Run("fill", p, item.oid) for p in _blend(poly, angle, params, start, reverse=True) if len(p) >= 2]


# --------------------------------------------------------------------------- methods

def _dense(fill):
    """Generator for a dense fill: edge-run foundation, pull compensation, then the fill."""
    def generate(item: "_Item", angle: float, params: StitchParams, start: Pt) -> list[Run]:
        from .methods import _compensate  # noqa: PLC0415  (shared helper)
        runs: list[Run] = []
        if params.underlay and params.fill_underlay != "none":
            edge = gen.polygons(item.geom.buffer(-0.5))
            for part in edge:
                if part.length > 4:
                    for piece in gen.ring_from(part, start, 2.5):
                        runs.append(Run("underlay", piece, item.oid))
        cur = runs[-1].points[-1] if runs else start
        poly = _compensate(item.geom, params)
        for piece in fill(poly, angle, params, cur):
            if len(piece) >= 2:
                runs.append(Run("fill", piece, item.oid))
        return runs
    return generate


def _open(fill):
    """Generator for an open fill (stipple, maze, cross stitch): no foundation, no compensation."""
    def generate(item: "_Item", angle: float, params: StitchParams, start: Pt) -> list[Run]:
        return [Run("running", p, item.oid) for p in fill(item.geom, angle, params, start) if len(p) >= 2]
    return generate


def _shaded(poly, angle, p, start):
    return gen.tatami_fill(poly, angle, p.fill_spacing_mm, p.fill_stitch_length_mm, start, p.max_stitch_mm,
                           row_ys=shaded_rows(p.fill_spacing_mm))


FILL_STYLES = {
    "offset_fill": _dense(lambda poly, a, p, s: offset_fill(poly, p.fill_spacing_mm, p.running_stitch_length_mm, s)),
    "spiral_fill": _dense(lambda poly, a, p, s: spiral_fill(poly, p.fill_spacing_mm, p.running_stitch_length_mm, s)),
    "contour_fill": _dense(lambda poly, a, p, s: contour_fill(poly, p.fill_spacing_mm, p.running_stitch_length_mm, s)),
    "radial_fill": _dense(lambda poly, a, p, s: radial_fill(poly, p.fill_spacing_mm, p.fill_stitch_length_mm, s)),
    "shaded_fill": _dense(_shaded),
    "blend_fill": _dense(_blend),
    "stipple_fill": _open(lambda poly, a, p, s: stipple_fill(poly, p.stipple_spacing_mm, 2.0, s)),
    "maze_fill": _open(lambda poly, a, p, s: maze_fill(poly, p.stipple_spacing_mm, 2.0, s)),
    "cross_stitch": _open(lambda poly, a, p, s: cross_stitch(poly, p.cross_cell_mm, s)),
}

for _name, _gen in FILL_STYLES.items():
    register(StitchMethod(_name, _gen, directional=_name in ("shaded_fill", "blend_fill"), density=_name in (
        "offset_fill", "spiral_fill", "contour_fill", "radial_fill", "shaded_fill", "blend_fill")))
register(StitchMethod("blend_partner", _sew_blend_partner, directional=True, density=True))
