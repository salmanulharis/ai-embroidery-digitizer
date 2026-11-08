"""Scan-line machinery shared by fill and satin stitches.

A polygon is rotated so stitch rows are horizontal, intersected with evenly spaced
scan lines, and the resulting row segments are grouped into *chains*
(boustrophedon / "monotone" sub-regions). Inside a chain each row overlaps exactly
one row before and after it, so the needle can sew back and forth without crossing
outside the shape or leaving gaps.
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache

import numpy as np
import shapely
from shapely.geometry import Polygon

from ..geometry import iter_lines, rotate_geom

Segment = tuple[float, float]  # (x0, x1) with x0 < x1, in the rotated frame


@dataclass
class Chain:
    ys: list[float]
    segs: list[Segment]

    def __len__(self) -> int:
        return len(self.segs)


def scan_rows(poly: Polygon, angle_deg: float, spacing: float, phase: float = 0.5) -> tuple[np.ndarray, list[list[Segment]]]:
    """Intersect the polygon (rotated by -angle) with horizontal scan lines."""
    rp = rotate_geom(poly, -angle_deg)
    minx, miny, maxx, maxy = rp.bounds
    ys = np.arange(miny + spacing * phase, maxy, spacing)
    if len(ys) == 0:
        ys = np.array([(miny + maxy) / 2])
    lines = shapely.linestrings([[[minx - 1, y], [maxx + 1, y]] for y in ys])
    shapely.prepare(rp)
    inters = shapely.intersection(rp, lines)
    rows: list[list[Segment]] = []
    for inter in inters:
        segs = []
        for ln in iter_lines(inter):
            xs = [c[0] for c in ln.coords]
            x0, x1 = min(xs), max(xs)
            if x1 - x0 > 0.05:
                segs.append((x0, x1))
        segs.sort()
        rows.append(segs)
    return ys, rows


def _overlaps(a: Segment, b: Segment, min_overlap: float) -> bool:
    return min(a[1], b[1]) - max(a[0], b[0]) > min_overlap


def build_chains(ys: np.ndarray, rows: list[list[Segment]], min_overlap: float = 0.1) -> list[Chain]:
    chains: list[Chain] = []
    active: list[tuple[int, Segment]] = []  # (chain index, segment) for previous row
    for y, segs in zip(ys, rows):
        new_active: list[tuple[int, Segment]] = []
        for s in segs:
            above = [a for a in active if _overlaps(a[1], s, min_overlap)]
            if len(above) == 1:
                below_count = sum(1 for t in segs if _overlaps(above[0][1], t, min_overlap))
                if below_count == 1:
                    chains[above[0][0]].ys.append(float(y))
                    chains[above[0][0]].segs.append(s)
                    new_active.append((above[0][0], s))
                    continue
            chains.append(Chain([float(y)], [s]))
            new_active.append((len(chains) - 1, s))
        active = new_active
    return chains


def chain_endpoints(chain: Chain) -> list[tuple[bool, bool, tuple[float, float], tuple[float, float]]]:
    """Four ways to sew a chain: (reverse_rows, start_left, start_point, end_point), rotated frame."""
    out = []
    n = len(chain)
    for reverse in (False, True):
        first = n - 1 if reverse else 0
        last = 0 if reverse else n - 1
        for start_left in (True, False):
            # after n rows the direction has flipped n times
            sx = chain.segs[first][0] if start_left else chain.segs[first][1]
            # rows alternate direction; the last row starts left iff (n-1) is even and row 0 did
            last_starts_left = start_left if (n - 1) % 2 == 0 else not start_left
            ex = chain.segs[last][1] if last_starts_left else chain.segs[last][0]
            out.append((reverse, start_left, (sx, chain.ys[first]), (ex, chain.ys[last])))
    return out


def unrotate(points: list[tuple[float, float]], angle_deg: float) -> list[tuple[float, float]]:
    from ..geometry import rotate_pts

    return rotate_pts(points, angle_deg)


def to_rotated(pt: tuple[float, float], angle_deg: float) -> tuple[float, float]:
    from ..geometry import rotate_pts

    return rotate_pts([pt], -angle_deg)[0]


def best_satin_angle(poly: Polygon, step: int = 15) -> tuple[float, float]:
    """Angle whose rows are shortest (stitches cross the narrow dimension).

    Returns (angle, p95 row length). Pure function of the outline, so results are
    cached: the shape editor and the stitch planner ask for the same shapes repeatedly."""
    return _best_satin_angle_cached(shapely.to_wkb(poly), step)


@lru_cache(maxsize=8192)
def _best_satin_angle_cached(wkb: bytes, step: int) -> tuple[float, float]:
    return _best_satin_angle(shapely.from_wkb(wkb), step)


def _best_satin_angle(poly: Polygon, step: int) -> tuple[float, float]:
    best = (0.0, float("inf"), float("inf"))
    spacing = max(0.3, min(1.0, np.sqrt(poly.area) / 40))
    for angle in range(0, 180, step):
        ys, rows = scan_rows(poly, angle, spacing)
        lengths = [x1 - x0 for segs in rows for x0, x1 in segs]
        if not lengths:
            continue
        p95 = float(np.percentile(lengths, 95))
        n_chains = len(build_chains(ys, rows))
        score = p95 + 0.4 * n_chains
        if score < best[2]:
            best = (float(angle), p95, score)
    return best[0], best[1]
