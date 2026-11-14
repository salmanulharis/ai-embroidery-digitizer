"""Machine safety: what the needle and thread can physically do.

* ``make_safe`` (always on, even with smart reduction switched off) removes needle
  drops that would make a stitch shorter than the thread can take. Very short
  stitches cut the thread or the fabric (needle hits its own hole) and are the main
  cause of thread breaks in small lettering. Drops are only removed, never moved, so
  the stitching cannot leave the design; the drop where a sequence starts or ends
  (landing after a jump, last drop before a trim) is always kept.
* ``cap_stitch_length`` (always on) splits any stitch longer than the maximum, the
  last guarantee before the machine file whatever stitch type produced it.
* ``machine_check`` lists the spots that still need attention so the preview can
  mark them: stitches that had to stay short, needle holes hit many times, very
  dense spots, and lettering strokes thinner than a sewable satin column.
"""

from __future__ import annotations

import math
from collections import Counter, defaultdict

from ..models import JUMP, STITCH

Op = tuple[str, float, float, str]

STACK_RADIUS_MM = 0.15  # drops this close together land in the same hole
STACK_LIMIT = 5  # more hits than this in one hole -> thread may shred / break
DENSITY_CELL_MM = 1.0
DENSITY_LIMIT = 40  # needle drops per mm² above which the fabric gets hard / perforated
CLUSTER_MM = 1.5  # risks closer than this are reported as one marker


def make_safe(ops: list[Op], min_stitch: float, max_stitch: float) -> list[Op]:
    """Drop needle penetrations that are closer than ``min_stitch`` to the previous one."""
    out: list[Op] = []
    n = len(ops)
    i = 0
    while i < n:
        if ops[i][0] != STITCH:
            out.append(ops[i])
            i += 1
            continue
        j = i
        while j + 1 < n and ops[j + 1][0] == STITCH:
            j += 1
        seq = ops[i : j + 1]
        kept: list[Op] = [seq[0]]
        for k in range(1, len(seq)):
            p = seq[k]
            last = kept[-1]
            if math.dist(last[1:3], p[1:3]) >= min_stitch:
                kept.append(p)
            elif k < len(seq) - 1:
                if math.dist(last[1:3], seq[k + 1][1:3]) > max_stitch:
                    kept.append(p)  # removing it would make a too-long stitch
            elif len(kept) > 1 and math.dist(kept[-2][1:3], p[1:3]) <= max_stitch:
                kept[-1] = p  # the sequence end must stay: drop the drop before it instead
            else:
                kept.append(p)
        out.extend(kept)
        i = j + 1
    return out


def cap_stitch_length(ops: list[Op], max_stitch: float) -> list[Op]:
    """Split any stitch longer than ``max_stitch`` into equal shorter ones.

    Every generator already keeps to the limit; this is the last guarantee before the
    machine file, so a new stitch type can never produce a stitch that snags or that a
    format (e.g. PES) would store unsplit. Jumps are left to the file writer."""
    out: list[Op] = []
    prev: tuple[float, float] | None = None
    for op in ops:
        cmd, x, y = op[0], op[1], op[2]
        if cmd == STITCH and prev is not None:
            d = math.dist(prev, (x, y))
            if d > max_stitch:
                n = math.ceil(d / max_stitch)
                for k in range(1, n):
                    t = k / n
                    out.append((STITCH, prev[0] + (x - prev[0]) * t, prev[1] + (y - prev[1]) * t, op[3]))
        out.append(op)
        if cmd in (STITCH, JUMP):
            prev = (x, y)
    return out


def _cluster(points: list[tuple[float, float]], kind: str) -> list[dict]:
    cells: dict[tuple[int, int], list[tuple[float, float]]] = defaultdict(list)
    for x, y in points:
        cells[(int(x // CLUSTER_MM), int(y // CLUSTER_MM))].append((x, y))
    out = []
    for pts in cells.values():
        out.append({
            "x": round(sum(p[0] for p in pts) / len(pts), 2),
            "y": round(sum(p[1] for p in pts) / len(pts), 2),
            "type": kind,
            "count": len(pts),
        })
    return out


def machine_check(ops: list[Op], limits, narrow_spots=()) -> list[dict]:
    """Spots where the machine may break thread or a detail may not show.

    types: ``short`` (stitch shorter than the thread can take), ``stack`` (one needle
    hole hit many times), ``dense`` (too many drops in a small area), ``thin`` (a
    lettering stroke narrower than a sewable column - widened as far as the gaps allow
    or sewn as a triple run)."""
    short: list[tuple[float, float]] = []
    holes: Counter = Counter()
    cells: Counter = Counter()
    prev = None
    for cmd, x, y, _ in ops:
        if cmd == STITCH:
            if prev is not None and 1e-9 < math.dist(prev, (x, y)) < limits.min_stitch_mm * 0.75:
                short.append(((prev[0] + x) / 2, (prev[1] + y) / 2))
            holes[(round(x / STACK_RADIUS_MM), round(y / STACK_RADIUS_MM))] += 1
            cells[(int(x // DENSITY_CELL_MM), int(y // DENSITY_CELL_MM))] += 1
        if cmd in (STITCH, "jump"):
            prev = (x, y)
    stacked = [(hx * STACK_RADIUS_MM, hy * STACK_RADIUS_MM) for (hx, hy), c in holes.items() if c > STACK_LIMIT]
    area = DENSITY_CELL_MM * DENSITY_CELL_MM
    dense = [((cx + 0.5) * DENSITY_CELL_MM, (cy + 0.5) * DENSITY_CELL_MM) for (cx, cy), c in cells.items() if c / area > DENSITY_LIMIT]
    risks = []
    risks += _cluster(short, "short")
    risks += _cluster(stacked, "stack")
    risks += _cluster(dense, "dense")
    risks += _cluster([tuple(p) for p in narrow_spots], "thin")
    return risks


RISK_TEXT = {
    "short": "very short stitches the thread may not survive",
    "stack": "one needle hole hit many times",
    "dense": "too many stitches in a small area",
    "thin": "lettering thinner than the machine can sew cleanly",
}


def summarize_risks(risks: list[dict]) -> dict[str, int]:
    out: dict[str, int] = {}
    for r in risks:
        out[r["type"]] = out.get(r["type"], 0) + 1
    return out
