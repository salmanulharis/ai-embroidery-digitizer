"""Smart stitch direction (``StitchParams.smart_direction``): shape-aware stitching.

Without it every shape of a colour shares one fill direction. With it each *section*
of the design is analysed on its own and gets the direction that suits its shape:

* **Sections** - a region is split where it narrows: the wide *body* is filled, the
  narrow *arms* growing out of it (tails, serifs, connecting bars) are sewn as their
  own shapes. The pieces overlap by ``SEAM_OVERLAP_MM`` so no gap opens between them.
* **Columns** - a narrow section of any width up to the satin limit gets satin that
  follows its centre line (curved lettering, swooshes, circular text), with the
  stitches crossing the column at right angles all the way along, square ends,
  shortened stitches on the inside of tight bends and an edge-walk foundation.
  Where strokes meet, one runs through and the others end at its edge; the strokes
  of a shape are sewn in an order that needs as few thread cuts as possible.
* **Bodies** - the fill direction is chosen per section: rows cross the section the
  short way, do not run along its edges and break into as few pieces as possible.
  Shapes that sew equally well in any direction keep the colour's direction.
* **Straight satin** - the direction of single-angle satin is refined to the degree.

Everything is deterministic geometry (no AI), cached per outline, and nothing in this
module runs unless the option is switched on.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from functools import lru_cache

import cv2
import numpy as np
import shapely
from shapely.geometry import LineString, Polygon

from ..geometry import Pt, dist, iter_polygons, resample_line, subdivide
from . import generators as gen
from . import strokes as st
from .rows import build_chains, scan_rows

# --------------------------------------------------------------------------- fill direction

ANGLE_STEP_DEG = 7.5  # candidate fill directions
GRAZE_DEG = 12.0  # rows closer than this to an edge's direction leave it ragged
SHORT_ROW_MM = 1.2  # rows shorter than this are slivers (dense turn-arounds)
KEEP_BASE = 0.04  # the colour's direction is kept unless another one is clearly better


def _angle_gap(a: float, b: float) -> float:
    d = abs(a - b) % 180.0
    return min(d, 180.0 - d)


def fill_angle(poly: Polygon, base: float) -> float:
    """Best tatami direction for one section; ``base`` (the colour's direction) wins ties."""
    return _fill_angle(shapely.to_wkb(poly), float(base))


@lru_cache(maxsize=4096)
def _fill_angle(wkb: bytes, base: float) -> float:
    poly = shapely.from_wkb(wkb)
    if poly.is_empty or poly.area <= 0:
        return base
    edges = _edges(poly)
    candidates = [float(a) for a in np.arange(0.0, 180.0, ANGLE_STEP_DEG)]
    scores = {a: _fill_score(poly, a, edges) for a in candidates}
    base_score = _fill_score(poly, base, edges)
    best = min(candidates, key=lambda a: (round(scores[a] + 0.02 * _angle_gap(a, base) / 90.0, 6), a))
    return base if base_score <= scores[best] + KEEP_BASE else best


def _edges(poly: Polygon) -> tuple[np.ndarray, np.ndarray]:
    """(length, direction in degrees 0..180) of every outline segment."""
    lengths, angles = [], []
    for ring in (poly.exterior, *poly.interiors):
        c = np.asarray(ring.simplify(0.05).coords, np.float64)[:, :2]
        d = np.diff(c, axis=0)
        lengths.append(np.hypot(d[:, 0], d[:, 1]))
        angles.append(np.degrees(np.arctan2(d[:, 1], d[:, 0])) % 180.0)
    return np.concatenate(lengths), np.concatenate(angles)


def _fill_score(poly: Polygon, angle: float, edges: tuple[np.ndarray, np.ndarray]) -> float:
    """Lower is better: short rows across the section, no rows along an edge, few
    separately sewn pieces, few sliver rows."""
    size = math.sqrt(poly.area)
    spacing = max(0.4, min(1.5, size / 30.0))
    ys, rows = scan_rows(poly, angle, spacing)
    lengths = np.array([x1 - x0 for segs in rows for x0, x1 in segs])
    if not len(lengths):
        return float("inf")
    seg_len, seg_dir = edges
    gap = np.abs(seg_dir - angle % 180.0)
    gap = np.minimum(gap, 180.0 - gap)
    graze = float((seg_len * np.clip(1.0 - gap / GRAZE_DEG, 0.0, None)).sum() / max(seg_len.sum(), 1e-9))
    pieces = len(build_chains(ys, rows)) - 1
    short = float((lengths < SHORT_ROW_MM).mean())
    return float(lengths.mean()) / size + 2.0 * graze + 0.12 * min(pieces, 8) + short


@lru_cache(maxsize=8192)
def _refined_satin_angle(wkb: bytes, angle: float) -> float:
    poly = shapely.from_wkb(wkb)
    spacing = max(0.3, min(1.0, math.sqrt(poly.area) / 40.0))

    def score(a: float) -> float:
        ys, rows = scan_rows(poly, a, spacing)
        lengths = [x1 - x0 for segs in rows for x0, x1 in segs]
        if not lengths:
            return float("inf")
        return float(np.percentile(lengths, 95)) + 0.4 * len(build_chains(ys, rows))

    best, best_score = angle, score(angle)
    for step in (4.0, 2.0, 1.0):  # hill-climb inside the +-7 degrees the coarse search left open
        for a in (best - step, best + step):
            s = score(a)
            if s < best_score - 1e-9:
                best, best_score = a, s
    return best % 180.0


def refine_satin_angle(poly: Polygon, angle: float) -> float:
    """``rows.best_satin_angle`` searches in 15 degree steps; this finds the direction
    whose stitches really cross the column the short way."""
    return _refined_satin_angle(shapely.to_wkb(poly), float(angle))


# --------------------------------------------------------------------------- sections

SEAM_OVERLAP_MM = 0.5  # an arm reaches this far into the body it grows out of
ARM_MIN_AREA_MM2 = 4.0
ARM_MAX_CONTACT = 0.3  # an arm touches its body along a short neck, not along its length
ARM_MAX_WIDTH = 0.7  # ... is clearly narrower than the widest satin (else it belongs to the body)
ARM_MIN_ASPECT = 2.0  # ... and at least this many times longer than wide


def narrow_arms(poly: Polygon, max_width: float) -> list[Polygon]:
    """Narrow parts (up to ``max_width`` wide) that grow out of a wide shape.

    The wide body is what survives opening the shape with a disc of that width; what
    is left over are arms, but also rounded-off corners and slivers along curved
    edges - those touch the body along most of their outline and are not arms. Short
    stubs and parts nearly as wide as the body stay with it too: a shape sewn half as
    fill and half as satin for no visible reason looks worse than one fill."""
    r = max_width / 2.0
    core = poly.buffer(-r)
    if core.is_empty:
        return []
    body = core.buffer(r * 1.02, join_style="mitre", mitre_limit=2.5)
    rest = poly.difference(body)
    if rest.is_empty:
        return []
    near_body = body.buffer(0.05)
    arms = []
    for part in iter_polygons(rest):
        if part.area < ARM_MIN_AREA_MM2:
            continue
        if part.boundary.intersection(near_body).length > ARM_MAX_CONTACT * part.length:
            continue
        if not part.buffer(-ARM_MAX_WIDTH * r).is_empty:  # as wide as the body somewhere
            continue
        width = st.small_stroke_width(part)
        if part.area < ARM_MIN_ASPECT * width * width:
            continue
        arms.append(part)
    return arms


def with_seam(arm: Polygon, poly: Polygon) -> Polygon:
    """The arm as it is sewn: grown into the body (never outside the shape) so the
    two overlap instead of leaving a gap at the seam."""
    grown = arm.buffer(SEAM_OVERLAP_MM).intersection(poly)
    parts = sorted(iter_polygons(grown), key=lambda p: -p.area)
    return parts[0] if parts else arm


# --------------------------------------------------------------------------- columns

COLUMN_STEP_MM = 0.25  # sampling of a column's centre line
MAX_UNCOVERED = 0.06  # more left unsewn by columns and patches = not a column shape
MAX_PATCHED = 0.25  # more than this sewn as patches = the columns do not describe the shape
PATCH_MIN_MM2 = 0.3  # leftover corners at least this big get a small satin patch
EDGE_WALK_MIN_MM = 2.5  # from this width the foundation runs along both edges
ZIGZAG_MIN_MM = 4.0  # from this width a zig-zag foundation is added
TIGHT, VERY_TIGHT = 0.55, 0.25  # inner-edge stitch gap, as a share of the normal gap


class Column:
    """A satin column: centre line samples with the unit normal and the distance to
    the outline on either side (``left`` is along the normal). Closed columns repeat
    their first sample at the end. Instances are shared by the cache - never mutated."""

    __slots__ = ("pts", "nrm", "left", "right", "closed", "s")

    def __init__(self, pts: np.ndarray, nrm: np.ndarray, left: np.ndarray, right: np.ndarray, closed: bool):
        self.pts, self.nrm, self.left, self.right, self.closed = pts, nrm, left, right, closed
        seg = np.hypot(*np.diff(pts, axis=0).T)
        self.s = np.concatenate([[0.0], np.cumsum(seg)])

    @property
    def length(self) -> float:
        return float(self.s[-1])

    @property
    def width(self) -> float:
        return float(np.median(self.left + self.right))

    def reversed(self) -> "Column":
        return Column(self.pts[::-1].copy(), -self.nrm[::-1], self.right[::-1].copy(), self.left[::-1].copy(), self.closed)

    def starting_near(self, pos: Pt) -> "Column":
        """Closed column re-cut so that it starts next to ``pos``."""
        k = int(np.argmin(np.hypot(self.pts[:-1, 0] - pos[0], self.pts[:-1, 1] - pos[1])))
        if not self.closed or k == 0:
            return self

        def roll(a: np.ndarray) -> np.ndarray:
            r = np.roll(a[:-1], -k, axis=0)
            return np.concatenate([r, r[:1]])

        return Column(roll(self.pts), roll(self.nrm), roll(self.left), roll(self.right), True)

    def at(self, s: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
        """Centre point, unit normal and half widths at arc positions ``s``."""
        p = np.stack([np.interp(s, self.s, self.pts[:, 0]), np.interp(s, self.s, self.pts[:, 1])], axis=1)
        n = np.stack([np.interp(s, self.s, self.nrm[:, 0]), np.interp(s, self.s, self.nrm[:, 1])], axis=1)
        norm = np.linalg.norm(n, axis=1, keepdims=True)
        norm[norm < 1e-9] = 1.0
        return p, n / norm, np.interp(s, self.s, self.left), np.interp(s, self.s, self.right)

    def cut(self, start: int, stop: int) -> "Column | None":
        """The samples ``start`` .. ``stop`` (exclusive) of an open column."""
        if stop - start < 3:
            return None
        return Column(self.pts[start:stop], self.nrm[start:stop], self.left[start:stop], self.right[start:stop], False)

    def area(self):
        """What the column's satin covers."""
        left = self.pts + self.nrm * self.left[:, None]
        right = self.pts - self.nrm * self.right[:, None]
        if self.closed:
            return shapely.make_valid(Polygon(left)).symmetric_difference(shapely.make_valid(Polygon(right)))
        return shapely.make_valid(Polygon(np.vstack([left, right[::-1]])))


@dataclass(frozen=True)
class ColumnShape:
    columns: tuple  # tuple[Column, ...]
    uncovered_share: float  # left unsewn (slivers too small for a patch)
    patches: tuple  # tuple[Polygon, ...] corners the columns do not reach, sewn as small satin areas
    patched_share: float = 0.0
    # per column: the column its start / its end stops against (index into ``columns``) or None
    links: tuple = ()

    @property
    def ok(self) -> bool:
        return bool(self.columns) and self.uncovered_share <= MAX_UNCOVERED and self.patched_share <= MAX_PATCHED


MAX_COLUMN_PX = 4_000_000  # larger shapes keep the classic satin / fill


def _column_res(poly: Polygon) -> float:
    """Grid (px per mm) a shape is thinned on: ~24 px across the column is plenty."""
    return min(st.RES, max(4.0, 24.0 / max(st.small_stroke_width(poly), 1e-6)))


def fits_columns(poly: Polygon) -> bool:
    minx, miny, maxx, maxy = poly.bounds
    return (maxx - minx + 0.5) * (maxy - miny + 0.5) * _column_res(poly) ** 2 <= MAX_COLUMN_PX


def analyse_columns(poly: Polygon) -> ColumnShape:
    """Satin columns that follow the centre lines of a narrow shape, and how well they
    cover it (cached: the editor asks for every shape's stitch method on each change)."""
    return _analyse_columns(poly.wkb)


@lru_cache(maxsize=2048)
def _analyse_columns(wkb: bytes) -> ColumnShape:
    poly = shapely.from_wkb(wkb)
    if poly.is_empty or poly.area <= 0:
        return ColumnShape((), 1.0, ())
    columns, links = _columns(poly)
    if not columns:
        return ColumnShape((), 1.0, ())
    # thread spreads ~0.1 mm beyond the needle drops
    covered = shapely.union_all([c.area() for c in columns]).buffer(0.1)
    rest = poly.difference(covered)
    patches = tuple(g for g in iter_polygons(rest) if g.area >= PATCH_MIN_MM2 and not g.buffer(-0.15).is_empty)
    patched = sum(g.area for g in patches)
    return ColumnShape(tuple(columns), max(0.0, rest.area - patched) / poly.area, patches, patched / poly.area, tuple(links))


def _smooth(pts: np.ndarray, k: int, closed: bool) -> np.ndarray:
    """Moving average over 2k+1 samples (the thinned centre line is a pixel staircase)."""
    if k < 1 or len(pts) < 2 * k + 2:
        return pts
    kernel = np.ones(2 * k + 1) / (2 * k + 1)
    mode = "wrap" if closed else "edge"
    padded = np.pad(pts, ((k, k), (0, 0)), mode=mode)
    out = np.stack([np.convolve(padded[:, i], kernel, mode="valid") for i in (0, 1)], axis=1)
    if not closed:
        out[0], out[-1] = pts[0], pts[-1]  # tips stay where the stroke ends
    return out


def _unit_normals(pts: np.ndarray, k: int, closed: bool) -> np.ndarray:
    n = len(pts)
    idx = np.arange(n)
    if closed:
        t = pts[(idx + k) % n] - pts[(idx - k) % n]
    else:
        t = pts[np.minimum(idx + k, n - 1)] - pts[np.maximum(idx - k, 0)]
    norm = np.linalg.norm(t, axis=1, keepdims=True)
    norm[norm < 1e-9] = 1.0
    t = t / norm
    return np.stack([-t[:, 1], t[:, 0]], axis=1)


def _window_median(values: np.ndarray, k: int, closed: bool) -> np.ndarray:
    padded = np.pad(values, k, mode="wrap" if closed else "reflect")
    return np.median(np.lib.stride_tricks.sliding_window_view(padded, 2 * k + 1), axis=1)


def _extend_tips(pts: np.ndarray, half: float, boundary, head: float, tail: float) -> np.ndarray:
    """Run the ends of a centre line on, straight, to just inside the outline.

    The thinned line stops about half a stroke width short of a stroke's end, and its
    last stretch is bent towards a corner of that end: it is cut off and replaced by a
    straight run in the direction the stroke had before. ``head`` / ``tail`` say how
    far ahead (in half-widths) the outline is looked for; 0 leaves the end alone."""
    step = COLUMN_STEP_MM
    cut = min(int(round(half / step)), max(0, (len(pts) - 3) // 3))  # what is cut off each end
    # The run stops a little inside the outline: the last stitch is measured across the
    # stroke there, and right at the end it would only find the end itself.
    margin = min(0.2, 0.25 * half)

    def tip(body: np.ndarray, factor: float) -> np.ndarray:
        """``body`` runs towards the end; returns it with that end re-made."""
        if factor <= 0:
            return body
        kept = body[: len(body) - cut] if cut else body
        base = min(max(2, cut), len(kept) - 1)
        d = kept[-1] - kept[-1 - base]
        norm = float(np.hypot(*d))
        if norm < 1e-9:
            return body
        d = d / norm
        lost = cut * step
        reach = factor * half + 0.1 + lost
        hit = shapely.distance(shapely.Point(kept[-1]), LineString([kept[-1], kept[-1] + d * reach]).intersection(boundary))
        run = lost if np.isnan(hit) else hit - margin  # no outline ahead (inside a junction): as long as it was
        if run < step / 2:
            return kept
        n = max(1, int(round(run / step)))
        return np.vstack([kept, kept[-1] + d * np.linspace(run / n, run, n)[:, None]])

    pts = tip(pts, tail)
    return tip(pts[::-1], head)[::-1]


def _reach(pts: np.ndarray, dirs: np.ndarray, limit: np.ndarray, boundary) -> np.ndarray:
    """Distance from each point along its direction to the outline, at most ``limit``."""
    rays = shapely.linestrings(np.stack([pts, pts + dirs * limit[:, None]], axis=1))
    hit = shapely.distance(shapely.points(pts), shapely.intersection(rays, boundary))
    return np.minimum(np.where(np.isnan(hit), limit, hit), limit)


END_REACH = {"tip": 2.0, "corner": 3.5}  # how far ahead of an end the outline is looked for (half-widths)
THROUGH_COS = -0.766  # two strokes within 40 degrees of a straight line run through a junction
BUTT_OVERLAP_MM = 0.3  # a stroke ending at another one reaches this far into it


def _thin(img: np.ndarray) -> np.ndarray:
    """Guo-Hall thinning (vectorised) -> 1 px wide skeleton. Unlike Zhang-Suen it does
    not eat a diagonal stroke away from its end, which matters for wide columns: a
    stroke lost there is a whole section left to the plain fill."""
    img = np.pad(img > 0, 1)
    changed = True
    while changed:
        changed = False
        for step in (0, 1):
            p2, p3, p4 = img[:-2, 1:-1], img[:-2, 2:], img[1:-1, 2:]
            p5, p6, p7 = img[2:, 2:], img[2:, 1:-1], img[2:, :-2]
            p8, p9 = img[1:-1, :-2], img[:-2, :-2]
            c = ((~p2 & (p3 | p4)).astype(np.int8) + (~p4 & (p5 | p6)) + (~p6 & (p7 | p8)) + (~p8 & (p9 | p2)))
            n1 = (p9 | p2).astype(np.int8) + (p3 | p4) + (p5 | p6) + (p7 | p8)
            n2 = (p2 | p3).astype(np.int8) + (p4 | p5) + (p6 | p7) + (p8 | p9)
            n = np.minimum(n1, n2)
            m = ((p2 | p3 | ~p5) & p4) if step == 0 else ((p6 | p7 | ~p9) & p8)
            kill = img[1:-1, 1:-1] & (c == 1) & (n >= 2) & (n <= 3) & ~m
            if kill.any():
                img[1:-1, 1:-1][kill] = False
                changed = True
    return img[1:-1, 1:-1].astype(np.uint8)


SPUR_INSIDE = 1.2  # a branch this short (in half-widths at its junction) never leaves the junction
SPUR_TIP = 0.35  # a corner spur runs out to (nearly) nothing ...
SPUR_REACH = 1.7  # ... within reach of the corners of the stroke end it starts from


def _skeleton_lines(poly: Polygon, res: float) -> list[tuple[list[Pt], bool]]:
    """Centre lines (mm) of a shape thinned on a ``res`` px/mm grid, as ``(coords, closed)``.

    Thinning leaves artefacts that are not strokes: spurs running from a square stroke
    end into its corners, and short links inside junctions. They are removed here; a
    real stub keeps its width to the end, a corner spur tapers to nothing."""
    minx, miny, maxx, maxy = poly.bounds
    pad = 3
    w = int(math.ceil((maxx - minx) * res)) + 2 * pad + 1
    h = int(math.ceil((maxy - miny) * res)) + 2 * pad + 1
    img = np.zeros((h, w), np.uint8)

    def px(coords) -> np.ndarray:
        return np.round((np.asarray(coords)[:, :2] - (minx, miny)) * res + pad).astype(np.int32)

    cv2.fillPoly(img, [px(poly.exterior.coords)], 1)
    for ring in poly.interiors:
        cv2.fillPoly(img, [px(ring.coords)], 0)
    half = cv2.distanceTransform(img, cv2.DIST_L2, 3) / res  # half width (mm) at each pixel
    raw = st._branches(_thin(img))
    touching: dict = {}
    for path, closed in raw:
        if not closed:
            for end in (path[0], path[-1]):
                touching[end] = touching.get(end, 0) + 1
    out: list[tuple[list[Pt], bool]] = []
    for path, closed in raw:
        if len(path) < 2:
            continue
        if not closed and len(raw) > 1:
            free = [touching[path[0]] == 1, touching[path[-1]] == 1]
            length = len(path) / res
            if free[0] != free[1]:
                node, tip = (path[-1], path[0]) if free[0] else (path[0], path[-1])
                reach = length / max(float(half[node]), 1e-6)
                if reach <= SPUR_INSIDE or (half[tip] <= SPUR_TIP * half[node] and reach <= SPUR_REACH):
                    continue
            elif not any(free) and length < max(0.3, float(np.median([half[r, c] for r, c in path]))):
                continue  # link inside a junction, covered by the strokes meeting there
        coords = [((c - pad) / res + minx, (r - pad) / res + miny) for r, c in path]
        out.append(([tuple(c[:2]) for c in LineString(coords).simplify(0.6 / res).coords], closed))
    return out


@dataclass
class _Line:
    """A centre line and what each of its ends is: a free ``tip``, a ``corner`` (the
    stroke that runs on to the outline where two strokes meet at an angle), a ``butt``
    end that stops against another stroke, or strokes that just ``meet``."""

    coords: list
    closed: bool = False
    head: str = "tip"
    tail: str = "tip"
    head_at: "_Line | None" = None
    tail_at: "_Line | None" = None
    into: "_Line | None" = None  # the line this one was joined into ...
    far: str = ""  # ... and which end of it this line's other end became


def _resolve(line: _Line, side: str = "") -> tuple[_Line, str]:
    while line.into is not None:
        line, side = line.into, line.far
    return line, side


def _centre_lines(poly: Polygon, boundary) -> list[_Line]:
    """Centre lines of a narrow shape, joined the way it would be drawn: at a junction
    the two strokes that continue each other become one line (the stem of a T, the
    upright of an R) and the others end against it."""
    res = _column_res(poly)
    lines = [_Line(coords, closed) for coords, closed in _skeleton_lines(poly, res)]
    ends = [(ln, side) for ln in lines if not ln.closed for side in ("head", "tail")]
    if len(ends) < 2:
        return lines
    pts = np.array([ln.coords[0] if side == "head" else ln.coords[-1] for ln, side in ends], np.float64)
    half = shapely.distance(shapely.points(pts), boundary)
    inside = poly.buffer(0.05)
    shapely.prepare(inside)
    group = list(range(len(ends)))

    def find(i: int) -> int:
        while group[i] != i:
            group[i] = group[group[i]]
            i = group[i]
        return i

    for i in range(len(ends)):
        for j in range(i + 1, len(ends)):
            gap = float(np.hypot(*(pts[i] - pts[j])))
            # the same junction: close together and joined by ink (not two tips facing each other)
            if gap <= 1.2 * max(half[i], half[j]) + 3.0 / res and (
                gap < 1e-6 or inside.contains(LineString([pts[i], pts[j]]))
            ):
                group[find(j)] = find(i)
    junctions: dict[int, list[int]] = {}
    for i in range(len(ends)):
        junctions.setdefault(find(i), []).append(i)

    def leaving(i: int) -> np.ndarray:
        """Unit direction in which the stroke leaves the junction."""
        ln, side = ends[i]
        coords = ln.coords if side == "head" else ln.coords[::-1]
        line = LineString(coords)
        q = line.interpolate(min(max(2.0 * half[i], 1.0), line.length))
        d = np.array([q.x - coords[0][0], q.y - coords[0][1]])
        norm = float(np.hypot(*d))
        return d / norm if norm > 1e-9 else np.zeros(2)

    for members in junctions.values():
        if len(members) < 2:
            continue
        dirs = {i: leaving(i) for i in members}
        best = None
        for a in members:
            for b in members:
                if a < b and _resolve(*ends[a])[0] is not _resolve(*ends[b])[0]:
                    cos = float(dirs[a] @ dirs[b])
                    if best is None or cos < best[0] - 1e-9:
                        best = (cos, a, b)
        through = None
        if best is not None and best[0] <= THROUGH_COS:
            _, a, b = best
            (la, sa), (lb, sb) = _resolve(*ends[a]), _resolve(*ends[b])
            first = la.coords if sa == "tail" else la.coords[::-1]  # ends at the junction
            second = lb.coords if sb == "head" else lb.coords[::-1]  # starts at it
            far_a, far_b = ("head" if sa == "tail" else "tail"), ("tail" if sb == "head" else "head")
            through = _Line(first + second, False, getattr(la, far_a), getattr(lb, far_b),
                            getattr(la, far_a + "_at"), getattr(lb, far_b + "_at"))
            la.into, la.far = through, "head"
            lb.into, lb.far = through, "tail"
            lines.append(through)
        if through is None and len(members) == 2 and best is not None:
            # a corner (L, V, the apex of an M): the longer stroke runs on to the outline
            # and the other one ends against it, the way the letter would be drawn
            (la, sa), (lb, sb) = _resolve(*ends[members[0]]), _resolve(*ends[members[1]])
            if LineString(lb.coords).length > LineString(la.coords).length + 1e-9:
                (la, sa), (lb, sb) = (lb, sb), (la, sa)
            setattr(la, sa, "corner")
            setattr(lb, sb, "butt")
            setattr(lb, sb + "_at", la)
            continue
        for i in members:
            if through is not None and i in best[1:]:  # type: ignore[index]
                continue
            ln, side = _resolve(*ends[i])
            setattr(ln, side, "butt" if through is not None else "meet")
            setattr(ln, side + "_at", through)
    return [ln for ln in lines if ln.into is None]


def _columns(poly: Polygon) -> tuple[list[Column], list[tuple]]:
    """The shape's columns and, for each, which column its start / end stops against."""
    boundary = poly.boundary
    lines = _centre_lines(poly, boundary)
    built = [(ln, _column(np.asarray(ln.coords, np.float64), ln.closed, END_REACH.get(ln.head, 0.0), END_REACH.get(ln.tail, 0.0), boundary))
             for ln in lines]
    zones: dict[int, object] = {}

    def zone(line: _Line):
        """Where a stroke ending against ``line`` must stop: just inside its edge."""
        line = _resolve(line)[0]
        if id(line) not in zones:
            col = next((c for ln, c in built if ln is line), None)
            zones[id(line)] = None if col is None else col.area().buffer(-BUTT_OVERLAP_MM)
        return zones[id(line)]

    columns = []
    kept: list[_Line] = []
    for ln, col in built:
        if col is None:
            continue
        start, stop = 0, len(col.pts)
        for side in ("head", "tail"):
            target = getattr(ln, side + "_at")
            z = zone(target) if getattr(ln, side) == "butt" and target is not None else None
            if z is None or z.is_empty:
                continue
            covered = shapely.contains_xy(z, col.pts[:, 0], col.pts[:, 1])
            if side == "head":
                while start < stop and covered[start]:
                    start += 1
            else:
                while stop > start and covered[stop - 1]:
                    stop -= 1
        if (start, stop) != (0, len(col.pts)):
            col = col.cut(start, stop)  # nothing left: the stroke lies inside the one it ends at
        if col is not None and col.length >= 2 * COLUMN_STEP_MM:
            columns.append(col)
            kept.append(ln)

    def against(ln: _Line, side: str):
        target = getattr(ln, side + "_at")
        if getattr(ln, side) != "butt" or target is None:
            return None
        target = _resolve(target)[0]
        return next((i for i, other in enumerate(kept) if other is target), None)

    return columns, [(against(ln, "head"), against(ln, "tail")) for ln in kept]


def _column(line: np.ndarray, closed: bool, reach_head: float, reach_tail: float, boundary) -> Column | None:
    if len(line) < 2:
        return None
    pts = np.asarray(resample_line(LineString(line), COLUMN_STEP_MM), np.float64)
    if closed and len(pts) > 2 and dist(tuple(pts[0]), tuple(pts[-1])) < 1e-6:
        pts = pts[:-1]
    if len(pts) < 3:
        return None
    half = shapely.distance(shapely.points(pts), boundary)
    k = int(np.clip(round(0.6 * float(np.median(half)) / COLUMN_STEP_MM), 1, 8))
    pts = _smooth(pts, k, closed)
    if not closed:
        pts = _extend_tips(pts, float(np.median(half)), boundary, reach_head, reach_tail)
    nrm = _unit_normals(pts, max(2, k), closed)
    half = shapely.distance(shapely.points(pts), boundary)
    # Width is measured across the column (not to the nearest outline point), so ends
    # stay square. Inside a junction the ray would shoot along the other stroke: it is
    # limited to a little more than the column's usual width around there.
    window = max(1, int(round(2.4 * float(np.median(half)) / COLUMN_STEP_MM)))
    limit = 1.2 * _window_median(half, min(window, len(pts) - 1), closed) + 0.1
    left = _reach(pts, nrm, limit, boundary)
    right = _reach(pts, -nrm, limit, boundary)
    if not closed:
        # at the very end a ray can run into the stroke's own end instead of its side
        # (a round end narrows gently - only a sudden drop is that)
        for side in (left, right):
            for end, inner in ((0, 1), (-1, -2)):
                if side[end] < 0.6 * side[inner]:
                    side[end] = side[inner]
    if closed:
        pts, nrm = np.vstack([pts, pts[:1]]), np.vstack([nrm, nrm[:1]])
        left, right = np.append(left, left[0]), np.append(right, right[0])
    col = Column(pts, nrm, left, right, closed)
    return col if col.length >= 2 * COLUMN_STEP_MM else None


def _capped(points: list[Pt], max_stitch: float) -> list[Pt]:
    out: list[Pt] = []
    for p in points:
        if out and dist(out[-1], p) > max_stitch:
            out.extend(subdivide(out[-1], p, max_stitch))
        elif not out or dist(out[-1], p) > 1e-9:
            out.append(p)
    return out


def _shortening(rail: np.ndarray, nrm: np.ndarray, step: float, on_side: np.ndarray, closed: bool) -> np.ndarray:
    """Per stitch: the share of its half width to sew on this side. On the inside of a
    tight bend the needle drops crowd together; every second (or two of three) stitch
    is stopped short there so the thread does not pile up."""
    n = len(rail)
    idx = np.arange(n)
    if closed:
        d = rail[(idx + 1) % n] - rail[(idx - 1) % n]
        span = np.full(n, 2.0)
    else:
        hi, lo = np.minimum(idx + 1, n - 1), np.maximum(idx - 1, 0)
        d = rail[hi] - rail[lo]
        span = (hi - lo).astype(np.float64)
    tangent = np.stack([nrm[:, 1], -nrm[:, 0]], axis=1)
    ratio = (d * tangent).sum(axis=1) / np.maximum(span * step, 1e-9)  # < 0: the edge folds back
    scale = np.ones(n)
    count = np.cumsum(on_side) - 1  # running number of this side's stitches
    tight = on_side & (ratio < TIGHT) & (ratio >= VERY_TIGHT)
    very = on_side & (ratio < VERY_TIGHT)
    scale[tight & (count % 2 == 1)] = 0.6
    scale[very & (count % 3 == 1)] = 0.6
    scale[very & (count % 3 == 2)] = 0.35
    return scale


def sew_column(col: Column, spacing: float, pull: float, max_stitch: float, min_width: float) -> list[Pt]:
    """Zig-zag satin along the column, from its start to its end."""
    # The stitch gap is measured along the outer edge: on a bend the outside of the
    # column would otherwise open up (the inside is handled by shortening below).
    wl = np.maximum(col.left + pull, min_width / 2.0)
    wr = np.maximum(col.right + pull, min_width / 2.0)
    edge_l = np.hypot(*np.diff(col.pts + col.nrm * wl[:, None], axis=0).T)
    edge_r = np.hypot(*np.diff(col.pts - col.nrm * wr[:, None], axis=0).T)
    centre = np.diff(col.s)
    travel = np.concatenate([[0.0], np.cumsum(np.maximum(np.maximum(edge_l, edge_r), 0.5 * centre))])
    total = float(travel[-1])
    if col.closed:
        n = max(4, int(round(total / spacing)))
        s = np.interp(np.linspace(0.0, total, n, endpoint=False), travel, col.s)
    else:
        n = max(2, int(round(total / spacing)) + 1)
        s = np.interp(np.linspace(0.0, total, n), travel, col.s)
    p, nrm, left, right = col.at(s)
    left = np.maximum(left + pull, min_width / 2.0)
    right = np.maximum(right + pull, min_width / 2.0)
    step = total / max(n if col.closed else n - 1, 1)
    even = np.arange(n) % 2 == 0
    left = left * _shortening(p + nrm * left[:, None], nrm, step, even, col.closed)
    right = right * _shortening(p - nrm * right[:, None], nrm, step, ~even, col.closed)
    q = np.where(even[:, None], p + nrm * left[:, None], p - nrm * right[:, None])
    out = [(float(x), float(y)) for x, y in q]
    if col.closed:
        out.append(out[0] if n % 2 == 0 else out[1])  # close the loop on the matching side
    else:
        # one stitch straight across each end: the zig-zag alone leaves the ends slanted
        other = np.where(even[:, None], p - nrm * right[:, None], p + nrm * left[:, None])
        out = [(float(other[0][0]), float(other[0][1])), *out, (float(other[-1][0]), float(other[-1][1]))]
    return _capped(out, max_stitch)


def column_underlay(col: Column, max_stitch: float) -> tuple[list[Pt], bool]:
    """Foundation for one column and whether it ends where it started (then the satin
    starts there too; otherwise the satin comes back from the far end).

    Narrow columns get a centre run; from ``EDGE_WALK_MIN_MM`` a run along both edges
    holds the outline, and wide columns also get a zig-zag that carries the satin."""
    width, length = col.width, col.length
    if width < 1.5 or length < 1.0:
        return [], True
    s = np.linspace(0.0, length, max(2, int(math.ceil(length / 2.0)) + 1))
    p, nrm, left, right = col.at(s)
    if width < EDGE_WALK_MIN_MM:
        return _capped([(float(x), float(y)) for x, y in p], max_stitch), col.closed
    inset = min(0.6, 0.3 * width)
    l_rail = p + nrm * np.maximum(left - inset, 0.0)[:, None]
    r_rail = p - nrm * np.maximum(right - inset, 0.0)[:, None]
    walk = np.vstack([l_rail, r_rail[::-1]])
    at_start = True
    if width >= ZIGZAG_MIN_MM:
        z = np.linspace(0.0, length, max(2, int(math.ceil(length / 1.0)) + 1))
        zp, zn, zl, zr = col.at(z)
        even = (np.arange(len(z)) % 2 == 0)[:, None]
        zig = np.where(even, zp + zn * np.maximum(zl - inset, 0.0)[:, None], zp - zn * np.maximum(zr - inset, 0.0)[:, None])
        walk = np.vstack([walk, zig])
        at_start = False
    return _capped([(float(x), float(y)) for x, y in walk], max_stitch), at_start


def _centre_run(col: Column, max_stitch: float) -> list[Pt]:
    s = np.linspace(0.0, col.length, max(2, int(math.ceil(col.length / 2.0)) + 1))
    return _capped([(float(x), float(y)) for x, y in col.at(s)[0]], max_stitch)


HIDDEN_TRAVEL_MAX_MM = 40.0
PATCH_NEAR_MM = 1.5
HIDDEN_HOP_MM = 0.8  # a run may start / end this far outside the column that hides it


def _hidden_path(a: Pt, b: Pt, unsewn: list[tuple[Column, object]], inside, max_stitch: float) -> list[Pt] | None:
    """A run from ``a`` to ``b`` along the centre of a column that is still to be
    sewn - its satin will cover the run, so no thread has to be cut. ``unsewn`` holds
    each such column with the area its satin will cover."""
    best: tuple[float, list[Pt]] | None = None
    here, there = shapely.Point(a), shapely.Point(b)
    for col, area in unsewn:
        if area.distance(here) > HIDDEN_HOP_MM or area.distance(there) > HIDDEN_HOP_MM:
            continue  # the way onto / off the column would show
        line = LineString(col.pts)
        sa, sb = line.project(here), line.project(there)
        (pa, _, _, _), (pb, _, _, _) = col.at(np.array([sa])), col.at(np.array([sb]))
        length = dist(a, tuple(pa[0])) + abs(sa - sb) + dist(b, tuple(pb[0]))
        if length > HIDDEN_TRAVEL_MAX_MM or (best is not None and length >= best[0]):
            continue
        along = col.at(np.linspace(sa, sb, max(2, int(math.ceil(abs(sa - sb) / 2.0)) + 1)))[0]
        path = _capped([a, *[(float(x), float(y)) for x, y in along], b], max_stitch)
        if len(path) >= 2 and inside.contains(LineString(path)):
            best = (length, path)
    return best[1] if best else None


def sew_shape(
    shape: ColumnShape,
    poly: Polygon,
    start: Pt,
    spacing_of,
    patch_spacing: float,
    pull: float,
    max_stitch: float,
    min_width: float,
    underlay: bool,
) -> list[tuple[str, list[Pt]]]:
    """All columns (and corner patches) of a narrow shape as ``(kind, points)`` runs,
    in an order that needs as few thread cuts as possible.

    Strokes that end against another stroke are sewn first and the stroke they end
    against last; in between, the needle moves along the centre of strokes that are
    not sewn yet, where the satin sewn later hides the run. A column finishes at the
    end from which the next one can be reached that way (out along its centre, satin
    back - or the other way round). ``spacing_of(width)`` gives the stitch gap."""
    cols = list(shape.columns)
    links = list(shape.links) or [(None, None)] * len(cols)
    done = [False] * len(cols)
    children: list[set[int]] = [set() for _ in cols]
    for i, ends in enumerate(links):
        for parent in ends:
            if parent is not None and parent != i:
                children[parent].add(i)
    inside = poly.buffer(pull + 0.2)  # satin ends lie on the outline, grown by the extra width
    shapely.prepare(inside)
    areas = [c.area() for c in cols]
    runs: list[tuple[str, list[Pt]]] = []
    cur = start

    def go(to: Pt) -> None:
        nonlocal cur
        if runs and dist(cur, to) > 1.0:  # shorter hops are joined by the planner anyway
            path = _hidden_path(cur, to, [(c, a) for c, a, d in zip(cols, areas, done) if not d], inside, max_stitch)
            if path:
                runs.append(("underlay", path))
                cur = path[-1]

    def add(kind: str, pts: list[Pt], width: float) -> None:
        """A run of the current column. The needle is already on the column (at its
        centre, or at the end of its foundation): it goes to the run's first point
        with one stitch that the column's satin covers."""
        nonlocal cur
        if len(pts) < 2:
            return
        if runs and 1.0 < dist(cur, pts[0]) <= width + 1.0 and inside.contains(LineString([cur, pts[0]])):
            pts = _capped([cur, *pts], max_stitch)
        runs.append((kind, pts))
        cur = pts[-1]

    patches = list(shape.patches)  # corners the columns do not reach

    def sew_patches(within: float) -> None:
        """Patches next to the needle (all that are left with ``within`` = infinity)."""
        nonlocal cur
        while patches:
            here = shapely.Point(cur)
            k = min(range(len(patches)), key=lambda j: patches[j].distance(here))
            if patches[k].distance(here) > within:
                return
            for piece in gen.satin_area(patches.pop(k), patch_spacing, cur, max_stitch):
                if len(piece) >= 2:
                    go(piece[0])
                    runs.append(("satin", piece))
                    cur = piece[-1]

    while not all(done):
        ready = [i for i in range(len(cols)) if not done[i] and all(done[c] for c in children[i])]
        ready = ready or [i for i in range(len(cols)) if not done[i]]  # strokes ending against each other in a ring
        i = min(ready, key=lambda j: column_entry(cols[j], cur))
        sew_patches(column_entry(cols[i], cur))  # ... or nearer than the next column
        col = column_from(cols[i], cur)
        head, tail = links[i] if col is cols[i] or col.closed else links[i][::-1]
        waiting = [end is not None and not done[end] for end in (head, tail)]  # the stroke it ends against comes later
        finish = None if col.closed or waiting[0] == waiting[1] else ("near" if waiting[0] else "far")
        go(tuple(col.pts[0]))
        at_far = False
        if underlay:
            under, at_start = column_underlay(col, max_stitch)
            if len(under) >= 2:
                add("underlay", under, col.width)
                at_far = not at_start and not col.closed
            if finish is not None and at_far != (finish == "near"):
                # the satin ends opposite to where it starts: bring the needle to the other end first
                run = _centre_run(col, max_stitch)
                add("underlay", run[::-1] if at_far else run, col.width)
                at_far = not at_far
        # extra width against fabric pull, less on narrow columns (a fixed 0.2 mm would
        # close the gaps in small lettering) - the same rule as the small-lettering engine
        extra = min(pull, max(0.05, 0.1 + 0.1 * (col.width - min_width)))
        pts = sew_column(col.reversed() if at_far else col, spacing_of(col.width), extra, max_stitch, min_width)
        add("satin", pts, col.width)
        done[i] = True
        sew_patches(PATCH_NEAR_MM)  # a corner is filled in while the needle is next to it
    sew_patches(float("inf"))
    return runs


def column_entry(col: Column, pos: Pt) -> float:
    """How far ``pos`` is from where the column can be entered."""
    if col.closed:
        return float(LineString(col.pts).distance(shapely.Point(pos)))
    return min(dist(pos, tuple(col.pts[0])), dist(pos, tuple(col.pts[-1])))


def column_from(col: Column, pos: Pt) -> Column:
    """The column oriented so that it starts at the end nearest to ``pos``."""
    if col.closed:
        return col.starting_near(pos)
    if dist(pos, tuple(col.pts[-1])) < dist(pos, tuple(col.pts[0])):
        return col.reversed()
    return col
