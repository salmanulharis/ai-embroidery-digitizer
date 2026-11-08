"""Smart stitch reduction (deterministic, no AI needed).

Removes stitches that add thread but no coverage, without changing the design:

* **Stacked micro-stitches** - consecutive needle drops closer than ~0.3 mm in fill,
  underlay, travel and running sequences (typical at fill-row tips and in thin
  underlay). They pile thread on one spot, cause hard spots and thread breaks.
* (In the generators) **lattice underlay in thin areas**, where it would only sit on
  top of the edge-run underlay already sewn along both edges.

Safety rules:
* needle drops are only ever *removed* - none are added or moved, so the optimised
  stitching is a subset of the original path and cannot leave the design shape;
* satin crossings, lock stitches and intentional triple-run retraces are never touched;
* the first and last drop of every sequence (landing points / connections) are kept;
* no resulting stitch may exceed the maximum stitch length;
* a coverage check renders the visible (top) stitching before and after; if more
  than ``MAX_COVERAGE_LOSS_PCT`` of it changes, the reduction is discarded.
"""

from __future__ import annotations

import math

import cv2
import numpy as np

from ..models import STITCH

REDUCIBLE_KINDS = {"fill", "underlay", "travel", "running"}
TOP_KINDS = {"fill", "satin", "running"}  # what the viewer actually sees
MAX_COVERAGE_LOSS_PCT = 0.5

Op = tuple[str, float, float, str]


def micro_stitch_threshold(fill_spacing_mm: float) -> float:
    """Shorter than this = stacked. Kept below the fill row spacing so the short
    turn-around stitch between two fill rows is never mistaken for one."""
    return min(0.3, 0.7 * fill_spacing_mm)


def remove_stacked_stitches(ops: list[Op], threshold: float, max_stitch: float) -> tuple[list[Op], int]:
    out: list[Op] = []
    removed = 0
    n = len(ops)
    i = 0
    while i < n:
        cmd, x, y, kind = ops[i]
        if cmd != STITCH or kind not in REDUCIBLE_KINDS:
            out.append(ops[i])
            i += 1
            continue
        # maximal run of consecutive stitches of one reducible kind
        j = i
        while j + 1 < n and ops[j + 1][0] == STITCH and ops[j + 1][3] == kind:
            j += 1
        seq = ops[i : j + 1]
        # the drop before the sequence (if it is a stitch) anchors the first stitch length
        kept: list[Op] = [seq[0]]
        for k in range(1, len(seq)):
            p = seq[k]
            last = kept[-1]
            d = math.dist(last[1:3], p[1:3])
            is_end = k == len(seq) - 1
            if d >= threshold:
                kept.append(p)
            elif not is_end:
                nxt = seq[k + 1]
                if math.dist(last[1:3], nxt[1:3]) <= max_stitch:
                    removed += 1  # drop p: next stitch starts from the previous drop
                else:
                    kept.append(p)
            else:
                # the end drop must stay (it connects to what follows); drop the stacked one before it
                if len(kept) > 1 and math.dist(kept[-2][1:3], p[1:3]) <= max_stitch:
                    kept[-1] = p
                    removed += 1
                else:
                    kept.append(p)
        out.extend(kept)
        i = j + 1
    return out, removed


def _render_top(ops: list[Op], bounds: tuple[float, float, float, float], ppm: float = 10.0) -> np.ndarray:
    minx, miny, maxx, maxy = bounds
    w = int((maxx - minx) * ppm) + 8
    h = int((maxy - miny) * ppm) + 8
    scale = min(1.0, 3000 / max(w, h))
    ppm *= scale
    w, h = max(8, int(w * scale)), max(8, int(h * scale))
    img = np.zeros((h, w), np.uint8)
    thick = max(1, int(round(0.35 * ppm)))
    prev = None
    for cmd, x, y, kind in ops:
        p = (int((x - minx) * ppm) + 4, int((y - miny) * ppm) + 4)
        if cmd == STITCH:
            if prev is not None and kind in TOP_KINDS:
                cv2.line(img, prev, p, 255, thick)
            prev = p
        elif cmd == "jump":
            prev = p
    return img


GAP_TOLERANCE_MM = 0.3  # thread spreads ~0.3 mm; closer than this is not a visible gap


def coverage_loss_pct(before: list[Op], after: list[Op]) -> float:
    """Share of the visible (top) stitching of ``before`` that has no stitching of
    ``after`` within ``GAP_TOLERANCE_MM`` - i.e. gaps the change would open up.

    Re-ordered but identical rows (different start point, turn-arounds at the other
    row end) are not gaps and do not count."""
    pts = [(x, y) for c, x, y, _ in before if c == STITCH]
    if not pts:
        return 0.0
    xs, ys = [p[0] for p in pts], [p[1] for p in pts]
    bounds = (min(xs), min(ys), max(xs), max(ys))
    ppm = 10.0
    a = _render_top(before, bounds, ppm)
    b = _render_top(after, bounds, ppm)
    covered = int(np.count_nonzero(a))
    if covered == 0:
        return 0.0
    scale = a.shape[1] / max(1, int((bounds[2] - bounds[0]) * ppm) + 8)
    r = max(1, int(round(GAP_TOLERANCE_MM * ppm * scale)))
    b_near = cv2.dilate(b, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2 * r + 1, 2 * r + 1)))
    lost = int(np.count_nonzero((a > 0) & (b_near == 0)))
    return 100.0 * lost / covered


def optimize_ops(ops: list[Op], fill_spacing_mm: float, max_stitch: float) -> tuple[list[Op], dict]:
    threshold = micro_stitch_threshold(fill_spacing_mm)
    new_ops, removed = remove_stacked_stitches(ops, threshold, max_stitch)
    loss = coverage_loss_pct(ops, new_ops) if removed else 0.0
    report = {"micro_removed": removed, "coverage_change_pct": round(loss, 3), "reverted": False, "threshold_mm": threshold}
    if loss > MAX_COVERAGE_LOSS_PCT:
        report.update(micro_removed=0, reverted=True)
        return ops, report
    return new_ops, report
