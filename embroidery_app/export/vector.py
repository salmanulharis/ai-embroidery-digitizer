"""Design -> SVG / EPS (clean, mm-scaled vector artwork for Illustrator, Inkscape and
embroidery software that imports vector graphics, such as Wilcom)."""

from __future__ import annotations

import math
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


PT_PER_MM = 72 / 25.4


def _ps_name(text: str) -> str:
    """Text safe inside a PostScript comment (one line, printable ASCII)."""
    return "".join(c if 32 <= ord(c) < 127 else "?" for c in str(text)).strip()[:80]


def design_to_eps(design: Design, layers: list[int] | None = None, title: str = "design") -> str:
    """Design -> EPS (vector artwork at real size, for embroidery software that imports
    EPS graphics, such as Wilcom's "Import Graphic"). Switched-off colours and shapes are
    left out; ``layers`` limits the file to those colour indexes. Areas keep their holes
    (even-odd fill); lines are stroked at their own width. Units: 1 mm, y up."""
    w, h = design.width_mm, design.height_mm
    wanted = None if layers is None else set(layers)
    out = [
        "%!PS-Adobe-3.0 EPSF-3.0",
        "%%Creator: AI Embroidery Digitizer",
        f"%%Title: {_ps_name(title)}",
        f"%%BoundingBox: 0 0 {math.ceil(w * PT_PER_MM)} {math.ceil(h * PT_PER_MM)}",
        f"%%HiResBoundingBox: 0 0 {w * PT_PER_MM:.3f} {h * PT_PER_MM:.3f}",
        "%%LanguageLevel: 2",
        "%%DocumentData: Clean7Bit",
        "%%Pages: 1",
        "%%EndComments",
        "%%BeginProlog",
        "/m {moveto} bind def /l {lineto} bind def /z {closepath} bind def",
        "%%EndProlog",
        "%%Page: 1 1",
        "gsave",
        f"{PT_PER_MM:.6f} dup scale",  # 1 unit = 1 mm
        f"0 {h:.3f} translate 1 -1 scale",  # the design's y points down
        "1 setlinejoin 1 setlinecap",
    ]
    for layer in design.layers:
        if not layer.enabled or (wanted is not None and layer.index not in wanted):
            continue
        r, g, b = (c / 255 for c in layer.rgb)
        body: list[str] = []
        for obj in design.objects:
            if obj.layer != layer.index or not obj.enabled:
                continue
            if obj.kind == "area":
                for poly in iter_polygons(obj.geometry):
                    path = []
                    for ring in (poly.exterior, *poly.interiors):
                        pts = list(ring.coords)
                        if len(pts) < 4:
                            continue
                        path.append("{:.3f} {:.3f} m".format(*pts[0][:2]))
                        path += ["{:.3f} {:.3f} l".format(*p[:2]) for p in pts[1:-1]]
                        path.append("z")
                    if path:
                        body += ["newpath", *path, "eofill"]
            else:
                for line in iter_lines(obj.geometry):
                    pts = list(line.coords)
                    if len(pts) < 2:
                        continue
                    body += ["newpath", "{:.3f} {:.3f} m".format(*pts[0][:2]),
                             *("{:.3f} {:.3f} l".format(*p[:2]) for p in pts[1:]),
                             f"{max(obj.stroke_width_mm, 0.1):.3f} setlinewidth stroke"]
        if body:
            out += [f"% colour {layer.index}: {_ps_name(layer.name)} {layer.hex}", f"{r:.4f} {g:.4f} {b:.4f} setrgbcolor", *body]
    out += ["grestore", "showpage", "%%EOF"]
    return "\n".join(out) + "\n"


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
