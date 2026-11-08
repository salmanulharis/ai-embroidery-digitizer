"""Design -> SVG (clean, mm-scaled vector artwork for Wilcom / Illustrator / Inkscape)."""

from __future__ import annotations

from xml.sax.saxutils import escape

from shapely.geometry import Polygon

from ..geometry import iter_lines, iter_polygons
from ..models import Design


def _ring_d(coords) -> str:
    pts = list(coords)
    if len(pts) < 3:
        return ""
    head = "M{:.3f},{:.3f}".format(*pts[0][:2])
    body = "".join("L{:.3f},{:.3f}".format(*p[:2]) for p in pts[1:-1])
    return head + body + "Z"


def polygon_d(poly: Polygon) -> str:
    return _ring_d(poly.exterior.coords) + "".join(_ring_d(r.coords) for r in poly.interiors)


def object_d(obj) -> str:
    """SVG path data (mm) of one design object."""
    if obj.kind == "area":
        return "".join(polygon_d(p) for p in iter_polygons(obj.geometry))
    return "".join(
        "M" + "L".join("{:.3f},{:.3f}".format(*p[:2]) for p in line.coords) for line in iter_lines(obj.geometry)
    )


def design_to_svg(design: Design, only_enabled: bool = False, interactive: bool = False) -> str:
    """``only_enabled`` (downloads): leave out switched-off colours and shapes.
    ``interactive`` (editor preview): tag every shape with ``data-oid`` for selection."""
    w, h = design.width_mm, design.height_mm
    out = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{w:.3f}mm" height="{h:.3f}mm" '
        f'viewBox="0 0 {w:.3f} {h:.3f}">',
    ]
    for layer in design.layers:
        if only_enabled and not layer.enabled:
            continue
        out.append(f'<g id="layer-{layer.index}" data-name="{escape(layer.name)}">')
        for obj in design.objects:
            if obj.layer != layer.index or (only_enabled and not obj.enabled):
                continue
            tag = ""
            if interactive:
                cls = "shape" + ("" if obj.enabled else " off") + (" custom" if obj.customised else "")
                tag = f' data-oid="{escape(obj.id)}" class="{cls}"'
            d = object_d(obj)
            if not d:
                continue
            if obj.kind == "area":
                out.append(f'<path d="{d}" fill="{layer.hex}" fill-rule="evenodd"{tag}/>')
            else:
                out.append(
                    f'<path d="{d}" fill="none" stroke="{layer.hex}" '
                    f'stroke-width="{obj.stroke_width_mm:.3f}" stroke-linejoin="round"{tag}/>'
                )
        out.append("</g>")
    out.append("</svg>")
    return "\n".join(out)
