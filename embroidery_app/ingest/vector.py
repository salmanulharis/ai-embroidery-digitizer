"""Vector artwork (SVG, AI, PDF) -> embroidery Design.

* SVG is parsed with ``svgelements`` (transforms, viewBox, CSS, arcs, fill-rule).
* Adobe Illustrator ``.ai`` files saved with "PDF compatibility" (the default since
  CS) and PDFs are converted to SVG with PyMuPDF, then parsed the same way.
* Fills become area objects, strokes become line objects (stroke width kept so the
  stitch planner can choose running / satin / fill).
* Overlapping shapes are "knocked out" in paint order (with a small underlap), as a
  digitizer would do, so hidden parts are not stitched twice.
"""

from __future__ import annotations

import io
import math
from dataclasses import dataclass
from typing import Optional

import numpy as np
from shapely import affinity
from shapely.geometry import LineString, MultiLineString, Polygon
from shapely.geometry.base import BaseGeometry
from shapely.ops import unary_union

from ..colors import color_name, merge_palette, rgb_to_lab
from ..geometry import clean_polygonal, iter_lines, iter_polygons
from ..models import ColorLayer, Design, DesignObject


@dataclass
class VectorOptions:
    width_mm: float = 100.0
    num_colors: Optional[int] = None  # None = keep artwork colours (near-duplicates merged)
    knockout: bool = True
    underlap_mm: float = 0.3
    drop_canvas_background: bool = True
    min_area_mm2: float = 0.5


def ai_or_pdf_to_svg(data: bytes) -> str:
    try:
        import fitz  # PyMuPDF
    except ImportError as exc:  # pragma: no cover
        raise ValueError("PDF/AI import needs PyMuPDF: pip install pymupdf") from exc
    try:
        doc = fitz.open(stream=data, filetype="pdf")
    except Exception as exc:
        raise ValueError(
            "This Illustrator file has no PDF-compatible content. Re-save it in Illustrator with "
            "'Create PDF Compatible File' enabled, or export it as SVG."
        ) from exc
    if doc.page_count == 0:
        raise ValueError("The document has no pages.")
    return doc[0].get_svg_image(text_as_path=True)


@dataclass
class _Shape:
    geom: BaseGeometry
    rgb: tuple[int, int, int]
    kind: str  # area | line
    stroke_width: float = 0.0


def _subpath_points(sub, tolerance: float) -> list[tuple[float, float]]:
    pts: list[tuple[float, float]] = []
    for seg in sub:
        name = type(seg).__name__
        if name == "Move":
            if seg.end is not None:
                pts.append((seg.end.x, seg.end.y))
            continue
        if name in ("Line", "Close"):
            if seg.end is not None:
                pts.append((seg.end.x, seg.end.y))
            continue
        try:
            length = seg.length(error=1e-3)
        except Exception:
            length = 10 * tolerance
        n = max(2, min(400, int(math.ceil(length / tolerance))))
        for p in seg.npoint(np.linspace(0, 1, n + 1)[1:]):
            pts.append((float(p[0]), float(p[1])))
    return pts


def _color(value) -> Optional[tuple[int, int, int, float]]:
    from svgelements import Color

    if value is None or not isinstance(value, Color) or value.value is None:
        return None
    if value.alpha == 0:
        return None
    return (value.red, value.green, value.blue, value.alpha / 255)


def _parse_svg(svg_text: str) -> tuple[list[_Shape], list[str]]:
    from svgelements import SVG, Path, Shape, SVGImage, SVGText, Use

    warnings: list[str] = []
    svg = SVG.parse(io.StringIO(svg_text), reify=True, ppi=96.0)
    bbox = svg.bbox()
    span = max((bbox[2] - bbox[0]) if bbox else 100, (bbox[3] - bbox[1]) if bbox else 100, 1e-6)
    tol = span / 1500.0  # curve flattening tolerance in document units

    shapes: list[_Shape] = []
    text_count = image_count = 0
    for el in svg.elements():
        if isinstance(el, SVGText):
            text_count += 1
            continue
        if isinstance(el, SVGImage):
            image_count += 1
            continue
        if not isinstance(el, Shape) or isinstance(el, Use):
            continue
        if el.values.get("visibility") == "hidden" or el.values.get("display") == "none":
            continue
        path = Path(el)
        if len(path) == 0:
            continue
        fill, stroke = _color(el.fill), _color(el.stroke)
        if fill is None and el.values.get("fill", "").startswith("url("):
            fill = (128, 128, 128, 1.0)  # gradient/pattern: keep the shape, user picks a thread
            warnings.append("Colour fades (gradients) were replaced with one solid colour - check the thread colour.")
        subpaths = []
        for sub in path.as_subpaths():
            pts = _subpath_points(list(sub), tol)
            if len(pts) >= 2:
                subpaths.append(pts)
        if not subpaths:
            continue

        if fill is not None:
            rings = [Polygon(p).buffer(0) for p in subpaths if len(p) >= 3]
            rings = [r for r in rings if not r.is_empty and r.area > tol * tol]
            if rings:
                if str(el.values.get("fill-rule", "nonzero")).lower() == "evenodd":
                    geom = rings[0]
                    for r in rings[1:]:
                        geom = geom.symmetric_difference(r)
                else:
                    geom = _nonzero(subpaths, rings)
                geom = clean_polygonal(geom)
                if not geom.is_empty:
                    shapes.append(_Shape(geom, fill[:3], "area"))  # type: ignore[arg-type]
        if stroke is not None:
            width = float(getattr(el, "stroke_width", 1.0) or 0.0)
            try:
                width *= math.sqrt(abs(el.transform.determinant)) if el.transform is not None else 1.0
            except Exception:
                pass
            if width > 0:
                lines = [LineString(p) for p in subpaths if len(p) >= 2]
                geom = lines[0] if len(lines) == 1 else MultiLineString(lines)
                shapes.append(_Shape(geom, stroke[:3], "line", width))  # type: ignore[arg-type]

    if text_count:
        warnings.append(f"{text_count} piece(s) of live text were skipped - in your design program convert text to outlines (shapes) and upload again.")
    if image_count:
        warnings.append(f"{image_count} picture(s) inside the artwork file were skipped - upload them separately as a picture.")
    return shapes, warnings


def _nonzero(subpaths: list[list[tuple[float, float]]], rings: list[Polygon]) -> BaseGeometry:
    """Approximate the non-zero winding rule: a ring nested inside a ring of opposite
    orientation is a hole, same orientation adds."""

    def signed_area(pts):
        a = 0.0
        for (x1, y1), (x2, y2) in zip(pts, pts[1:] + pts[:1]):
            a += x1 * y2 - x2 * y1
        return a / 2

    items = []
    for pts in subpaths:
        if len(pts) < 3:
            continue
        poly = Polygon(pts).buffer(0)
        if poly.is_empty:
            continue
        items.append((abs(poly.area), signed_area(pts) > 0, poly))
    items.sort(key=lambda t: -t[0])
    placed: list[tuple[bool, Polygon]] = []
    geom: BaseGeometry = Polygon()
    for _, ccw, poly in items:
        container = next((c for c in reversed(placed) if c[1].contains(poly.representative_point())), None)
        if container is not None and container[0] != ccw:
            geom = geom.difference(poly)
        else:
            geom = geom.union(poly)
        placed.append((ccw, poly))
    return geom


def svg_to_design(svg_text: str, opts: VectorOptions) -> Design:
    shapes, warnings = _parse_svg(svg_text)
    if not shapes:
        raise ValueError("No fillable or stroked shapes found in the vector file.")

    # Normalise: fit overall bounds to requested width (mm), origin at top-left.
    bounds = unary_union([s.geom.buffer(s.stroke_width / 2) if s.kind == "line" else s.geom for s in shapes]).bounds
    minx, miny, maxx, maxy = bounds
    scale = opts.width_mm / max(maxx - minx, 1e-9)

    def tx(g: BaseGeometry) -> BaseGeometry:
        return affinity.scale(affinity.translate(g, -minx, -miny), scale, scale, origin=(0, 0))

    for s in shapes:
        s.geom = tx(s.geom)
        s.stroke_width *= scale
    width_mm, height_mm = (maxx - minx) * scale, (maxy - miny) * scale

    if opts.drop_canvas_background and len(shapes) > 1:
        first = shapes[0]
        lab = rgb_to_lab(np.array(first.rgb, np.uint8))
        if first.kind == "area" and first.geom.area >= 0.97 * width_mm * height_mm and lab[0] > 90:
            shapes = shapes[1:]
            warnings.append("A white background behind the artwork was removed (it would have been stitched otherwise).")

    # Knockout hidden areas in paint order (later shapes are on top).
    if opts.knockout:
        above: BaseGeometry = Polygon()
        for s in reversed(shapes):
            cover = s.geom if s.kind == "area" else s.geom.buffer(s.stroke_width / 2, cap_style="flat")
            if not above.is_empty:
                cut = above.buffer(-opts.underlap_mm) if s.kind == "area" else above
                if not cut.is_empty:
                    s.geom = s.geom.difference(cut)
                    if s.kind == "area":
                        s.geom = clean_polygonal(s.geom)
            above = above.union(cover)

    shapes = [s for s in shapes if not s.geom.is_empty]
    shapes = [s for s in shapes if s.kind == "line" or s.geom.area >= opts.min_area_mm2]

    # Palette: merge perceptually identical colours; optionally reduce to N.
    unique = sorted({s.rgb for s in shapes})
    labs = rgb_to_lab(np.array(unique, np.uint8))
    weights = np.array([sum(s.geom.area + s.geom.length for s in shapes if s.rgb == u) for u in unique])
    groups = merge_palette(labs, weights, threshold=3.0)
    if opts.num_colors and groups.max() + 1 > opts.num_colors:
        groups = _reduce_groups(labs, weights, opts.num_colors)
    group_rgb: dict[int, tuple[int, int, int]] = {}
    for g in range(groups.max() + 1):
        members = [i for i in range(len(unique)) if groups[i] == g]
        heaviest = max(members, key=lambda i: weights[i])
        group_rgb[g] = unique[heaviest]
    color_group = {unique[i]: int(groups[i]) for i in range(len(unique))}

    # Stitch order follows first appearance in paint order (preserves layering).
    first_seen: list[int] = []
    for s in shapes:
        g = color_group[s.rgb]
        if g not in first_seen:
            first_seen.append(g)
    remap = {g: i for i, g in enumerate(first_seen)}
    layers = [ColorLayer(index=remap[g], rgb=group_rgb[g], name=color_name(group_rgb[g])) for g in first_seen]

    objects: list[DesignObject] = []
    for s in shapes:
        layer = remap[color_group[s.rgb]]
        if s.kind == "area":
            for p in iter_polygons(s.geom):
                if p.area >= opts.min_area_mm2:
                    objects.append(DesignObject(layer=layer, geometry=p))
        else:
            for line in iter_lines(s.geom):
                if line.length > 0.3:
                    objects.append(DesignObject(layer=layer, geometry=line, kind="line", stroke_width_mm=s.stroke_width))

    return Design(
        width_mm=width_mm,
        height_mm=height_mm,
        layers=layers,
        objects=objects,
        source_kind="vector",
        warnings=sorted(set(warnings)),
    )


def _reduce_groups(labs: np.ndarray, weights: np.ndarray, n: int) -> np.ndarray:
    """Agglomerative merge of the closest colour pair until n remain."""
    groups = np.arange(len(labs))
    centers = {i: (labs[i].astype(float), float(weights[i])) for i in range(len(labs))}
    while len(centers) > n:
        keys = list(centers)
        best = None
        for i, a in enumerate(keys):
            for b in keys[i + 1 :]:
                d = float(np.linalg.norm(centers[a][0] - centers[b][0]))
                if best is None or d < best[0]:
                    best = (d, a, b)
        _, a, b = best  # type: ignore[misc]
        (ca, wa), (cb, wb) = centers[a], centers[b]
        centers[a] = ((ca * wa + cb * wb) / (wa + wb + 1e-9), wa + wb)
        del centers[b]
        groups[groups == b] = a
    _, dense = np.unique(groups, return_inverse=True)
    return dense
