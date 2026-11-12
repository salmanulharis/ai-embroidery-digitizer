"""Small-lettering engine: satin that follows each stroke of a letter.

Thin letter strokes (lettering, Arabic script, fine line art) are sewn the way a
digitizer does it by hand:

* the shape is thinned to its centre line (skeleton) and split into strokes;
* each stroke gets a satin column whose stitches cross the stroke at right angles;
* strokes thinner than the machine can sew are widened to the minimum column width -
  but only as far as the free space allows, so gaps between letters and holes inside
  letters stay open (measured on an "ink map" of the whole design);
* strokes that still cannot reach a sewable width become bean (triple) stitch lines;
* tiny dots are enlarged to the smallest sewable dot where there is room.

Everything works in mm; the raster work happens at ``RES`` px/mm.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from functools import lru_cache

import cv2
import numpy as np
import shapely
from shapely.geometry import LineString, Point, Polygon

from ..geometry import Pt, dist, iter_polygons, resample_line, subdivide
from .generators import running

STROKE_METHODS = ("stroke", "dot")
RES = 20.0  # px per mm for skeletons and the ink map (0.05 mm)
MAX_SHAPE_PX = 8_000_000  # larger thin shapes (huge borders) keep the classic satin / fill
MAX_INK_PX = 30_000_000  # the ink map of very large designs uses a coarser grid
MIN_GAP_MM = 0.3  # open space kept between strokes / inside letters when widening


# --------------------------------------------------------------------------- thread limits

@dataclass(frozen=True)
class ThreadLimits:
    """What the machine can sew cleanly with a given thread weight."""

    weight: int
    min_column_mm: float  # narrowest satin column
    min_dot_mm: float  # smallest dot
    min_stitch_mm: float  # shortest stitch before thread breaks / needle cuts


def thread_limits(weight: int, satin_min_width_mm: float = 0.8) -> ThreadLimits:
    if int(weight) == 60:  # fine lettering: 60 wt thread, 65/9-70/10 needle
        return ThreadLimits(60, min(satin_min_width_mm, 0.6), 0.6, 0.3)
    return ThreadLimits(40, satin_min_width_mm, 0.8, 0.4)  # standard 40 wt, 75/11 needle


# --------------------------------------------------------------------------- ink map

class InkMap:
    """Raster of everything that will be stitched, to measure free space around strokes."""

    def __init__(self, geoms, width_mm: float, height_mm: float, margin: float = 2.0):
        self.ox, self.oy = -margin, -margin
        area = (width_mm + 2 * margin) * (height_mm + 2 * margin)
        self.res = min(RES, math.sqrt(MAX_INK_PX / max(area, 1e-6)))
        w = int(math.ceil((width_mm + 2 * margin) * self.res)) + 1
        h = int(math.ceil((height_mm + 2 * margin) * self.res)) + 1
        self.mask = np.zeros((h, w), np.uint8)
        for g in geoms:
            for poly in iter_polygons(g):
                cv2.fillPoly(self.mask, [self._px(poly.exterior.coords)], 1)
                for ring in poly.interiors:
                    cv2.fillPoly(self.mask, [self._px(ring.coords)], 0)

    def _px(self, coords) -> np.ndarray:
        a = np.asarray(coords, np.float64)[:, :2]
        return np.round((a - (self.ox, self.oy)) * self.res).astype(np.int32)

    def free_distance(self, origins: np.ndarray, dirs: np.ndarray, max_mm: float) -> np.ndarray:
        """Distance (mm) from each origin along its direction to the first ink pixel
        (``max_mm`` when none is found)."""
        steps = np.arange(0.0, max_mm + 1e-9, 1.0 / RES)
        pts = origins[:, None, :] + dirs[:, None, :] * steps[None, :, None]
        px = np.round((pts[..., 0] - self.ox) * self.res).astype(np.int64)
        py = np.round((pts[..., 1] - self.oy) * self.res).astype(np.int64)
        h, w = self.mask.shape
        inside = (px >= 0) & (py >= 0) & (px < w) & (py < h)
        hit = np.zeros(px.shape, bool)
        hit[inside] = self.mask[py[inside], px[inside]] > 0
        # a ray may start on the shape's own ink (at junctions and bends): measure the
        # free space from where it first leaves the ink to where it meets ink again
        free = ~hit
        start = np.where(free.any(1), free.argmax(1), len(steps))
        after = hit & (np.arange(len(steps))[None, :] >= start[:, None])
        end = np.where(after.any(1), after.argmax(1), len(steps))
        return np.clip((end - start) / RES, 0.0, max_mm)


# --------------------------------------------------------------------------- skeleton

def _thin(img: np.ndarray) -> np.ndarray:
    """Zhang-Suen thinning (vectorised) -> 1 px wide skeleton."""
    img = np.pad((img > 0).astype(np.uint8), 1)
    changed = True
    while changed:
        changed = False
        for step in (0, 1):
            p2, p3, p4 = img[:-2, 1:-1], img[:-2, 2:], img[1:-1, 2:]
            p5, p6, p7 = img[2:, 2:], img[2:, 1:-1], img[2:, :-2]
            p8, p9 = img[1:-1, :-2], img[:-2, :-2]
            nb = [p2, p3, p4, p5, p6, p7, p8, p9, p2]
            b = sum(n.astype(np.int16) for n in nb[:8])
            a = sum(((nb[i] == 0) & (nb[i + 1] == 1)).astype(np.int16) for i in range(8))
            if step == 0:
                c1, c2 = p2 * p4 * p6, p4 * p6 * p8
            else:
                c1, c2 = p2 * p4 * p8, p2 * p6 * p8
            kill = (img[1:-1, 1:-1] == 1) & (b >= 2) & (b <= 6) & (a == 1) & (c1 == 0) & (c2 == 0)
            if kill.any():
                img[1:-1, 1:-1][kill] = 0
                changed = True
    return img[1:-1, 1:-1]


_N8 = [(-1, -1), (-1, 0), (-1, 1), (0, -1), (0, 1), (1, -1), (1, 0), (1, 1)]


def _branches(skel: np.ndarray) -> list[tuple[list[tuple[int, int]], bool]]:
    """Split a skeleton into pixel paths between endpoints/junctions.
    Returns (path, closed) with path as (row, col) pixels."""
    pts = set(zip(*np.nonzero(skel)))
    if not pts:
        return []

    def nbrs(p):
        # m-adjacency: a diagonal neighbour only counts when no orthogonal pixel already
        # links the two (otherwise staircase corners form triangles = fake junctions)
        out = []
        for dy, dx in _N8:
            q = (p[0] + dy, p[1] + dx)
            if q not in pts:
                continue
            if dy and dx and ((p[0] + dy, p[1]) in pts or (p[0], p[1] + dx) in pts):
                continue
            out.append(q)
        return out

    degree = {p: len(nbrs(p)) for p in pts}
    nodes = {p for p, d in degree.items() if d != 2}
    used_edges: set[frozenset] = set()
    out: list[tuple[list[tuple[int, int]], bool]] = []

    def walk(start, nxt):
        path = [start, nxt]
        used_edges.add(frozenset((start, nxt)))
        prev, cur = start, nxt
        while cur not in nodes:
            options = [n for n in nbrs(cur) if n != prev and frozenset((cur, n)) not in used_edges]
            if not options:
                break
            prev, cur = cur, options[0]
            used_edges.add(frozenset((prev, cur)))
            path.append(cur)
            if cur == start:
                break
        return path

    for node in nodes:
        for n in nbrs(node):
            if frozenset((node, n)) not in used_edges:
                out.append((walk(node, n), False))
    # pure loops (e.g. the letter O) have no endpoint or junction
    remaining = {p for p in pts if p not in nodes and not any(frozenset((p, n)) in used_edges for n in nbrs(p))}
    while remaining:
        start = remaining.pop()
        ns = nbrs(start)
        if not ns:
            continue
        path = walk(start, ns[0])
        remaining -= set(path)
        out.append((path, True))
    if not out and len(pts) == 1:  # a single pixel
        out.append((list(pts), False))
    return out


@dataclass
class Stroke:
    line: LineString  # centre line (mm)
    closed: bool = False


MAX_UNCOVERED = 0.06  # shapes whose strokes would leave more uncovered are not strokes (blobs)
PATCH_MIN_MM2 = 0.12  # leftover corners at least this big get a small satin patch


@dataclass(frozen=True)
class StrokeAnalysis:
    strokes: tuple  # tuple[Stroke, ...]
    uncovered_share: float
    patches: tuple  # tuple[Polygon, ...] corners the stroke columns do not reach


def analyse_stroke_shape(poly: Polygon) -> StrokeAnalysis:
    """Centre lines of a thin shape plus how well satin along them covers it (cached:
    the editor asks for the stitch method of every shape on each change)."""
    return _analyse(poly.wkb)


@lru_cache(maxsize=2048)
def _analyse(wkb: bytes) -> StrokeAnalysis:
    poly = shapely.from_wkb(wkb)
    strokes = stroke_centrelines(poly)
    if not strokes or poly.area <= 0:
        return StrokeAnalysis(tuple(strokes), 1.0, ())
    cols = []
    for s in strokes:
        pts = np.asarray(resample_line(s.line, 0.15), np.float64)
        if len(pts) < 2:
            continue
        nrm = _normals(pts, s.closed)
        half = shapely.distance(shapely.points(pts), poly.boundary)[:, None]
        left, right = pts + nrm * half, pts - nrm * half
        col = Polygon(np.vstack([left, right[::-1]])).buffer(0)
        cols.append(col)
    # thread spreads ~0.1 mm beyond the needle drops
    covered = shapely.union_all(cols).buffer(0.1) if cols else Polygon()
    rest = poly.difference(covered)
    share = rest.area / poly.area
    patches = tuple(
        g for g in iter_polygons(rest) if g.area >= PATCH_MIN_MM2 and not g.buffer(-0.05).is_empty
    )
    return StrokeAnalysis(tuple(strokes), share, patches)


def stroke_centrelines(poly: Polygon) -> list[Stroke]:
    """Centre lines of the strokes of a (thin) shape, ends extended to the stroke tips."""
    minx, miny, maxx, maxy = poly.bounds
    pad = 3
    w = int(math.ceil((maxx - minx) * RES)) + 2 * pad + 1
    h = int(math.ceil((maxy - miny) * RES)) + 2 * pad + 1
    img = np.zeros((h, w), np.uint8)

    def px(coords):
        a = np.asarray(coords)[:, :2]
        return np.round((a - (minx, miny)) * RES + pad).astype(np.int32)

    cv2.fillPoly(img, [px(poly.exterior.coords)], 1)
    for ring in poly.interiors:
        cv2.fillPoly(img, [px(ring.coords)], 0)
    skel = _thin(img)
    dt = cv2.distanceTransform(img, cv2.DIST_L2, 3) / RES  # half width (mm) at each pixel

    def to_mm(path):
        return [((c - pad) / RES + minx, (r - pad) / RES + miny) for r, c in path]

    raw = _branches(skel)
    # prune short spurs: a branch with a free end that is shorter than the stroke is wide
    degree_end = {}
    for path, closed in raw:
        if not closed:
            for end in (path[0], path[-1]):
                degree_end[end] = degree_end.get(end, 0) + 1
    strokes: list[Stroke] = []
    for path, closed in raw:
        length = len(path) / RES
        half = float(np.median([dt[r, c] for r, c in path])) if path else 0.0
        free_end = (not closed) and (degree_end.get(path[0], 0) == 1 or degree_end.get(path[-1], 0) == 1)
        if free_end and len(raw) > 1 and length < 2.5 * half:
            continue
        coords = to_mm(path)
        if len(coords) < 2:
            continue
        if not closed and not free_end and length < max(0.3, half):
            continue  # link inside a junction cluster, covered by the strokes meeting there
        line = LineString(coords).simplify(0.6 / RES)
        if not closed:
            line = _extend_ends(line, poly)
        strokes.append(Stroke(line, closed))
    return strokes


def _extend_ends(line: LineString, poly: Polygon) -> LineString:
    """The skeleton stops about half a stroke width before each tip; extend free ends
    along their direction to just inside the outline so the satin reaches the tip."""
    coords = list(line.coords)
    if len(coords) < 2:
        return line
    boundary = poly.boundary

    def extend(p, q):  # extend from p away from q
        dx, dy = p[0] - q[0], p[1] - q[1]
        n = math.hypot(dx, dy)
        if n < 1e-9:
            return p
        dx, dy = dx / n, dy / n
        reach = boundary.distance(Point(p)) * 1.5 + 0.05
        ray = LineString([p, (p[0] + dx * reach, p[1] + dy * reach)])
        hit = ray.intersection(boundary)
        if hit.is_empty:
            return p
        d = min(Point(p).distance(g) for g in getattr(hit, "geoms", [hit]))
        d = max(0.0, d - 0.08)
        return (p[0] + dx * d, p[1] + dy * d)

    back = coords[min(2, len(coords) - 1)]
    coords[0] = extend(coords[0], back if dist(back, coords[0]) > 1e-6 else coords[1])
    fwd = coords[max(-3, -len(coords))]
    coords[-1] = extend(coords[-1], fwd if dist(fwd, coords[-1]) > 1e-6 else coords[-2])
    return LineString(coords)


# --------------------------------------------------------------------------- satin along a stroke

@dataclass
class StrokeResult:
    points: list[Pt] = field(default_factory=list)
    kind: str = "satin"  # satin | running (bean fallback)
    narrow_spots: list[Pt] = field(default_factory=list)  # could not reach the minimum width


def _normals(pts: np.ndarray, closed: bool) -> np.ndarray:
    n = len(pts)
    prev = np.roll(pts, 2, axis=0) if closed else np.vstack([pts[:1]] * 2 + [pts[:-2]])
    nxt = np.roll(pts, -2, axis=0) if closed else np.vstack([pts[2:]] + [pts[-1:]] * 2)
    t = nxt - prev
    norm = np.linalg.norm(t, axis=1, keepdims=True)
    norm[norm < 1e-9] = 1.0
    t = t / norm
    return np.stack([-t[:, 1], t[:, 0]], axis=1)[:n]


def satin_stroke(
    stroke: Stroke,
    poly: Polygon,
    ink: InkMap,
    limits: ThreadLimits,
    spacing: float,
    pull: float,
    max_stitch: float,
) -> StrokeResult:
    centre = resample_line(stroke.line, spacing)
    if stroke.closed and len(centre) > 2 and dist(centre[0], centre[-1]) < 1e-6:
        centre = centre[:-1]
    if len(centre) < 2:
        return StrokeResult([])
    pts = np.asarray(centre, np.float64)
    normals = _normals(pts, stroke.closed)
    half = shapely.distance(shapely.points(pts), poly.boundary)  # stroke half width at each point
    width = 2 * float(np.median(half))
    # extra width against fabric pull, smaller for narrow columns (a fixed 0.2 mm would
    # double a 0.4 mm stroke and close the gaps in small lettering)
    pc = min(pull, max(0.05, 0.1 + 0.1 * (width - limits.min_column_mm)))
    target = np.maximum(half + pc, limits.min_column_mm / 2)
    need = target - half
    # free space beyond each edge (towards other strokes, letters, counters)
    look = float(need.max()) * 2 + MIN_GAP_MM + 0.2
    sides = []
    for s in (1.0, -1.0):
        origins = pts + normals * s * (half[:, None] + 1.5 / RES)
        gap = ink.free_distance(origins, normals * s, look)
        sides.append(np.maximum(0.0, (gap - MIN_GAP_MM) / 2))  # neighbours may grow too: share the gap
    got_l = np.minimum(need, sides[0])
    got_r = np.minimum(need, sides[1])
    # if one side is blocked, give the missing width to the other side where possible
    got_l, got_r = (
        got_l + np.minimum(need - got_r, np.maximum(0.0, sides[0] - got_l)),
        got_r + np.minimum(need - got_l, np.maximum(0.0, sides[1] - got_r)),
    )
    hl, hr = half + got_l, half + got_r
    achieved = hl + hr
    if np.median(achieved) < limits.min_column_mm * 0.6:
        # cannot become a sewable column here: a bean (triple) stitch line keeps the stroke visible
        return StrokeResult(running(stroke.line, max(1.2, limits.min_stitch_mm * 3), 3), "running",
                            _spots(pts, np.ones(len(pts), bool)))
    narrow = achieved < limits.min_column_mm * 0.9
    if not stroke.closed:  # tips taper by nature: only the body of a stroke counts
        tip = max(2, int(math.ceil(limits.min_column_mm / max(spacing, 1e-6))))
        narrow[:tip] = False
        narrow[-tip:] = False
    if narrow.mean() < 0.2:
        narrow[:] = False  # a short pinch is covered by the thread spreading
    out: list[Pt] = []
    for i, (p, n) in enumerate(zip(pts, normals)):
        q = p + n * hl[i] if i % 2 == 0 else p - n * hr[i]
        out.append((float(q[0]), float(q[1])))
    if stroke.closed and out:
        close = out[0] if len(pts) % 2 == 0 else out[1]
        if dist(out[-1], close) > max_stitch:
            out.extend(subdivide(out[-1], close, max_stitch))
        else:
            out.append(close)
    return StrokeResult(out, "satin", _spots(pts, narrow))


def _spots(pts: np.ndarray, mask: np.ndarray) -> list[Pt]:
    """About one marker per mm of a too-thin stretch."""
    sel = pts[mask]
    if not len(sel):
        return []
    out = [tuple(map(float, sel[0]))]
    for p in sel[1:]:
        if dist(out[-1], p) >= 1.0:
            out.append(tuple(map(float, p)))
    return out


def stroke_underlay(stroke: Stroke, poly: Polygon) -> list[Pt]:
    """Centre run under columns wide enough to need support (>= 1.5 mm)."""
    half = poly.boundary.distance(stroke.line.interpolate(0.5, normalized=True))
    if 2 * half < 1.5 or stroke.line.length < 1.0:
        return []
    return resample_line(stroke.line, 2.0)


# --------------------------------------------------------------------------- dots

def dot_stroke(poly: Polygon, ink: InkMap, limits: ThreadLimits) -> tuple[Stroke, float]:
    """A tiny compact shape (dot, full stop): a short centre line along its long axis,
    with the shape enlarged to the smallest sewable dot where there is room."""
    rect = poly.minimum_rotated_rectangle
    coords = list(getattr(rect, "exterior", rect).coords) if not rect.is_empty else []
    c = poly.centroid
    if len(coords) >= 4:
        e1 = np.subtract(coords[1], coords[0])
        e2 = np.subtract(coords[2], coords[1])
        long_e, short_len = (e1, np.linalg.norm(e2)) if np.linalg.norm(e1) >= np.linalg.norm(e2) else (e2, np.linalg.norm(e1))
        long_len = float(np.linalg.norm(long_e))
        axis = long_e / max(long_len, 1e-9)
    else:
        axis, long_len, short_len = np.array([1.0, 0.0]), 0.0, 0.0
    # room around the dot (all directions) limits how much it may grow
    ang = np.linspace(0, 2 * math.pi, 16, endpoint=False)
    dirs = np.stack([np.cos(ang), np.sin(ang)], axis=1)
    radius = max(short_len, 1e-3) / 2
    origins = np.array([c.x, c.y]) + dirs * (radius + 1.5 / RES)
    room = float(np.min(ink.free_distance(origins, dirs, limits.min_dot_mm + MIN_GAP_MM)))
    grow = max(0.0, (room - MIN_GAP_MM) / 2)
    width = min(max(short_len, limits.min_dot_mm), short_len + 2 * grow)
    length = max(long_len, width)
    # crossings spread over the dot's length less one thread width -> a round dot, not a dash
    seg = max(length - limits.min_stitch_mm, 0.15)
    a = (c.x - axis[0] * seg / 2, c.y - axis[1] * seg / 2)
    b = (c.x + axis[0] * seg / 2, c.y + axis[1] * seg / 2)
    return Stroke(LineString([a, b])), width


def satin_dot(poly: Polygon, ink: InkMap, limits: ThreadLimits, spacing: float) -> StrokeResult:
    stroke, width = dot_stroke(poly, ink, limits)
    line = stroke.line
    n = max(3, int(round(line.length / spacing)) + 2)  # at least 3 crossings: a solid little dot
    xs = np.linspace(0, 1, n)
    pts = np.array([line.interpolate(t, normalized=True).coords[0] for t in xs])
    d = np.subtract(line.coords[-1], line.coords[0])
    nrm = np.array([-d[1], d[0]]) / max(float(np.linalg.norm(d)), 1e-9)
    half = width / 2
    out = [tuple(p + nrm * half) if i % 2 == 0 else tuple(p - nrm * half) for i, p in enumerate(pts)]
    narrow = [tuple(poly.centroid.coords[0])] if width < limits.min_dot_mm * 0.9 else []
    return StrokeResult([(float(x), float(y)) for x, y in out], "satin", narrow)


def is_dot(poly: Polygon, limits: ThreadLimits) -> bool:
    """Compact and no bigger than a couple of sewable dots."""
    if poly.interiors:
        return False
    rect = poly.minimum_rotated_rectangle
    coords = list(getattr(rect, "exterior", rect).coords)
    if len(coords) < 4:
        return True
    a, b = dist(coords[0], coords[1]), dist(coords[1], coords[2])
    long_len, short_len = max(a, b), min(a, b)
    return long_len <= max(1.2 * limits.min_dot_mm, 1.0) and long_len <= 2.5 * max(short_len, 1e-6)


def fits_stroke_engine(poly: Polygon) -> bool:
    minx, miny, maxx, maxy = poly.bounds
    return (maxx - minx + 0.5) * (maxy - miny + 0.5) * RES * RES <= MAX_SHAPE_PX


def small_stroke_width(poly: Polygon) -> float:
    """Average stroke width of a shape (2 x area / perimeter)."""
    return 2 * poly.area / max(poly.length, 1e-6)
