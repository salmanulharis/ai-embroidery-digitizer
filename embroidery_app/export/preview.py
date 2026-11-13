"""Stitch previews: compact JSON for the browser simulator and a realistic PNG render."""

from __future__ import annotations

import cv2
import numpy as np

from ..models import COLOR_CHANGE, JUMP, STITCH, STOP, TRIM, StitchPlan, rgb_to_hex

CMD_CODES = {STITCH: 0, JUMP: 1, TRIM: 2, COLOR_CHANGE: 3, STOP: 4}
KIND_CODES = {"fill": 0, "satin": 1, "running": 2, "underlay": 3, "travel": 4, "tie": 5, "": 6}


def plan_preview_json(plan: StitchPlan) -> dict:
    """Flat integer arrays (0.1 mm units) - ~4 numbers per stitch."""
    data: list[int] = []
    for cmd, x, y, kind in plan.ops:
        data.extend((CMD_CODES[cmd], int(round(x * 10)), int(round(y * 10)), KIND_CODES.get(kind, 6)))
    return {
        "width_mm": plan.width_mm,
        "height_mm": plan.height_mm,
        "colors": [{"hex": rgb_to_hex(b.rgb), "name": b.name} for b in plan.blocks],
        "kinds": list(KIND_CODES),
        "ops": data,
        "stats": plan.stats(),
        "thread_weight": plan.thread_weight,
        "risks": plan.risks,  # machine check markers (mm), see stitch/safety.py
    }


def render_png(plan: StitchPlan, px_per_mm: float = 10.0, background=(250, 250, 248), margin_mm: float = 3.0) -> bytes:
    """Render stitches as shaded thread lines (anti-aliased)."""
    w = int((plan.width_mm + 2 * margin_mm) * px_per_mm)
    h = int((plan.height_mm + 2 * margin_mm) * px_per_mm)
    scale = min(1.0, 4000 / max(w, h))
    ppm = px_per_mm * scale
    w, h = max(16, int(w * scale)), max(16, int(h * scale))
    img = np.full((h, w, 3), background[::-1], np.uint8)
    thick = max(1, int(round(0.32 * ppm)))
    shift = 4
    k = (1 << shift) * ppm

    def P(x: float, y: float) -> tuple[int, int]:
        return int((x + margin_mm) * k), int((y + margin_mm) * k)

    color_idx = 0
    prev = None
    colors = [b.rgb for b in plan.blocks] or [(0, 0, 0)]
    for cmd, x, y, _ in plan.ops:
        if cmd == COLOR_CHANGE:
            color_idx = min(color_idx + 1, len(colors) - 1)
            continue
        if cmd in (TRIM, STOP):
            continue
        if cmd == JUMP:
            prev = (x, y)
            continue
        if prev is not None:
            r, g, b = colors[color_idx]
            dark = (int(b * 0.7), int(g * 0.7), int(r * 0.7))
            light = (min(255, int(b * 1.15 + 20)), min(255, int(g * 1.15 + 20)), min(255, int(r * 1.15 + 20)))
            cv2.line(img, P(*prev), P(x, y), dark, thick + 1, cv2.LINE_AA, shift)
            cv2.line(img, P(*prev), P(x, y), (b, g, r), thick, cv2.LINE_AA, shift)
            if thick >= 3:
                cv2.line(img, P(*prev), P(x, y), light, max(1, thick // 3), cv2.LINE_AA, shift)
        prev = (x, y)
    ok, buf = cv2.imencode(".png", img)
    return buf.tobytes()
