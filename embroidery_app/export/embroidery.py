"""StitchPlan -> machine embroidery files via pystitch (the maintained Ink/Stitch fork of pyembroidery).

DST (Tajima) is the primary target: it is read by Dahao-based controllers (used in
WONYO and most Chinese multi-needle machines) and imported by Wilcom
EmbroideryStudio. DST has no thread colours, only colour-change stops, so the
worksheet export lists the colour sequence for the operator. Trims are written
the Tajima way (a short burst of jumps) which Dahao controllers and Wilcom
recognise as trims.
"""

from __future__ import annotations

import os
import tempfile

import pystitch as pe

from ..models import COLOR_CHANGE, JUMP, STITCH, TRIM, StitchPlan, rgb_to_hex

FORMATS: dict[str, dict[str, str]] = {
    "dst": {"label": "Tajima DST", "note": "For WONYO and almost all commercial machines; opens in Wilcom. Thread colours are listed on the worksheet"},
    "exp": {"label": "Melco EXP", "note": "For Melco and Bernina machines; also opens in Wilcom"},
    "u01": {"label": "Barudan U01", "note": "For Barudan machines and some others"},
    "pes": {"label": "Brother PES", "note": "For Brother and Babylock home machines; keeps thread colours"},
    "jef": {"label": "Janome JEF", "note": "For Janome and Elna home machines"},
    "vp3": {"label": "Pfaff/Husqvarna VP3", "note": "For Husqvarna Viking and Pfaff machines; keeps thread colours"},
}

UNITS_PER_MM = 10  # pystitch works in 0.1 mm


def plan_to_pattern(plan: StitchPlan, name: str = "design") -> pe.EmbPattern:
    pattern = pe.EmbPattern()
    pattern.metadata("name", name[:16])
    for block in plan.blocks:
        thread = pe.EmbThread()
        thread.set_hex_color(rgb_to_hex(block.rgb))
        thread.description = block.name
        pattern.add_thread(thread)

    stitches = [(x, y) for cmd, x, y, _ in plan.ops if cmd == STITCH]
    if stitches:
        xs, ys = [p[0] for p in stitches], [p[1] for p in stitches]
        cx, cy = (min(xs) + max(xs)) / 2, (min(ys) + max(ys)) / 2
    else:
        cx = cy = 0.0

    for cmd, x, y, _ in plan.ops:
        ux, uy = (x - cx) * UNITS_PER_MM, (y - cy) * UNITS_PER_MM
        if cmd == STITCH:
            pattern.add_stitch_absolute(pe.STITCH, ux, uy)
        elif cmd == JUMP:
            pattern.add_stitch_absolute(pe.JUMP, ux, uy)
        elif cmd == TRIM:
            pattern.add_command(pe.TRIM)
        elif cmd == COLOR_CHANGE:
            pattern.add_command(pe.COLOR_CHANGE)
    pattern.add_command(pe.END)
    return pattern


def export_embroidery(plan: StitchPlan, fmt: str, name: str = "design") -> bytes:
    fmt = fmt.lower()
    if fmt not in FORMATS:
        raise ValueError(f"Unsupported embroidery format: {fmt}")
    pattern = plan_to_pattern(plan, name)
    fd, path = tempfile.mkstemp(suffix="." + fmt)
    os.close(fd)
    try:
        pe.write(pattern, path)
        with open(path, "rb") as f:
            return f.read()
    finally:
        os.unlink(path)


def worksheet(plan: StitchPlan, name: str) -> str:
    s = plan.stats()
    lines = [
        f"Design: {name}",
        f"Size: {s['width_mm']} x {s['height_mm']} mm",
        f"Stitches: {s['stitches']:,}   Trims: {s['trims']}   Colour changes: {s['color_changes']}",
        f"Estimated run time: {s['est_minutes']} min   Thread (top): ~{s['thread_m']} m",
        "",
        "Colour sequence (assign needles on the machine; DST files carry no colours):",
    ]
    per_color = [0] * max(1, len(plan.blocks))
    color = 0
    for cmd, _, _, _ in plan.ops:  # count from the final machine sequence (after any optimisation)
        if cmd == COLOR_CHANGE:
            color = min(color + 1, len(per_color) - 1)
        elif cmd == STITCH:
            per_color[color] += 1
    for i, block in enumerate(plan.blocks, 1):
        lines.append(f"  {i:>2}. {block.name:<12} {rgb_to_hex(block.rgb)}   {per_color[i - 1]:,} stitches")
    lines += ["", "Hoop: centre of design = centre of hoop (DST origin)."]
    return "\n".join(lines) + "\n"
