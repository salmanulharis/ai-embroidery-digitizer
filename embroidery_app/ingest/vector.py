"""Vector artwork (SVG, AI, PDF) -> embroidery Design.

* Parsing uses PyMuPDF (MuPDF), which handles transforms, viewBox, ``<use>``, arcs,
  fill rules and stroke widths consistently. A small pre-pass adds what MuPDF's SVG
  reader lacks: ``<style>`` CSS rules (Illustrator exports colour every shape with
  ``.cls-N`` classes) and gradient fills (replaced by their average colour).
* Adobe Illustrator ``.ai`` files saved with "PDF compatibility" (the default since
  CS) and PDFs are converted to SVG with text as outlines, then parsed the same way.
* Fills become area objects, strokes become line objects (stroke width kept so the
  stitch planner can choose running / satin / fill).
* Overlapping shapes are "knocked out" in paint order (with a small underlap), as a
  digitizer would do, so hidden parts are not stitched twice.
"""

from __future__ import annotations

import math
import re
import xml.etree.ElementTree as ET
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


def pymupdf_module():
    """PyMuPDF under its current import name (``fitz`` is deprecated)."""
    try:
        import pymupdf
    except ImportError:  # pragma: no cover - very old PyMuPDF releases
        try:
            import fitz as pymupdf  # type: ignore[no-redef]
        except ImportError as exc:
            raise ValueError("Vector import needs PyMuPDF: pip install pymupdf") from exc
    return pymupdf


def ai_or_pdf_to_svg(data: bytes) -> str:
    pymupdf = pymupdf_module()
    try:
        doc = pymupdf.open(stream=data, filetype="pdf")
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


# --------------------------------------------------------------------------- SVG pre-pass

SVG_NS = "http://www.w3.org/2000/svg"
XLINK_NS = "http://www.w3.org/1999/xlink"
_GRADIENT_WARNING = "Colour fades (gradients) were replaced with one solid colour - check the thread colour."
_SIMPLE_SELECTOR = re.compile(r"(\*|[A-Za-z][\w-]*)?((?:#[\w-]+)?)((?:\.[\w-]+)*)")
_NAMED = {"black": "#000000", "white": "#ffffff", "red": "#ff0000", "green": "#008000", "blue": "#0000ff",
          "yellow": "#ffff00", "gray": "#808080", "grey": "#808080", "orange": "#ffa500", "purple": "#800080"}


def _local(tag) -> str:
    return tag.rsplit("}", 1)[-1] if isinstance(tag, str) else ""


def _style_dict(style: str) -> dict[str, str]:
    out = {}
    for decl in style.split(";"):
        if ":" in decl:
            k, v = decl.split(":", 1)
            out[k.strip().lower()] = v.replace("!important", "").strip()
    return out


def _parse_css(css: str) -> list[tuple[str, str, str, str]]:
    """Simple selectors only (tag, #id, .class and combinations) - what design programs
    emit. Returns (tag, id, classes, declarations) sorted by specificity, then order."""
    css = re.sub(r"/\*.*?\*/", "", css, flags=re.S)
    css = re.sub(r"@[\w-]+[^{;]*\{(?:[^{}]*\{[^{}]*\})*[^{}]*\}", "", css)  # drop @media / @font-face blocks
    rules = []
    for order, m in enumerate(re.finditer(r"([^{}]+)\{([^{}]*)\}", css)):
        for sel in m.group(1).split(","):
            sel = sel.strip()
            sm = _SIMPLE_SELECTOR.fullmatch(sel)
            if not sel or not sm:
                continue  # descendant / attribute selectors are not supported
            tag, sid, classes = sm.group(1) or "", sm.group(2)[1:], sm.group(3)
            spec = (1 if sid else 0, classes.count("."), 1 if tag and tag != "*" else 0)
            rules.append((spec, order, tag, sid, classes, m.group(2).strip()))
    rules.sort(key=lambda r: (r[0], r[1]))
    return [(tag, sid, classes, decls) for _, _, tag, sid, classes, decls in rules]


def _hex_color(value: str | None) -> Optional[str]:
    if not value:
        return None
    v = value.strip().lower()
    if v in _NAMED:
        return _NAMED[v]
    m = re.fullmatch(r"#([0-9a-f]{3}|[0-9a-f]{6})", v)
    if m:
        h = m.group(1)
        return "#" + ("".join(c * 2 for c in h) if len(h) == 3 else h)
    m = re.fullmatch(r"rgba?\(([^)]*)\)", v)
    if m:
        parts = [p.strip() for p in m.group(1).split(",")][:3]
        try:
            vals = [round(float(p[:-1]) * 2.55) if p.endswith("%") else round(float(p)) for p in parts]
            return "#{:02x}{:02x}{:02x}".format(*(max(0, min(255, c)) for c in vals))
        except ValueError:
            return None
    return None


def _gradient_colors(root: ET.Element) -> dict[str, str]:
    """Average stop colour of every gradient (following href templates)."""
    grads = {
        el.get("id"): el for el in root.iter()
        if _local(el.tag) in ("linearGradient", "radialGradient") and el.get("id")
    }

    def stops(el, depth=0):
        found = [s for s in el if _local(s.tag) == "stop"]
        if found or depth > 5:
            return found
        ref = (el.get(f"{{{XLINK_NS}}}href") or el.get("href") or "").lstrip("#")
        return stops(grads[ref], depth + 1) if ref in grads else []

    out = {}
    for gid, el in grads.items():
        cols = []
        for s in stops(el):
            c = _hex_color(_style_dict(s.get("style", "")).get("stop-color") or s.get("stop-color"))
            if c:
                cols.append([int(c[i : i + 2], 16) for i in (1, 3, 5)])
        if cols:
            out[gid] = "#{:02x}{:02x}{:02x}".format(*(int(round(v)) for v in np.mean(cols, axis=0)))
    return out


def _prepare_svg(svg_text: str | bytes) -> tuple[bytes, list[str]]:
    """Inline <style> CSS and flatten gradient fills so MuPDF sees plain attributes."""
    ET.register_namespace("", SVG_NS)
    ET.register_namespace("xlink", XLINK_NS)
    try:
        root = ET.fromstring(svg_text.encode("utf-8") if isinstance(svg_text, str) else svg_text)
    except ET.ParseError as exc:
        raise ValueError("The SVG file could not be read - it is not valid SVG.") from exc
    if _local(root.tag) != "svg":
        raise ValueError("The file is not an SVG drawing.")
    warnings: list[str] = []
    elements = list(root.iter())
    texts = sum(1 for el in elements if _local(el.tag) == "text")
    images = sum(1 for el in elements if _local(el.tag) == "image")

    css = "\n".join(el.text or "" for el in elements if _local(el.tag) == "style")
    rules = _parse_css(css) if css.strip() else []
    if rules:
        for el in elements:
            tag = _local(el.tag)
            if tag in ("style", "svg", "defs", ""):
                continue
            classes = set((el.get("class") or "").split())
            matched = [
                decls for rtag, rid, rclasses, decls in rules
                if (not rtag or rtag in ("*", tag))
                and (not rid or el.get("id") == rid)
                and all(c in classes for c in rclasses.split(".")[1:])
            ]
            if matched:
                # later rules (higher specificity) override earlier ones and inline style wins,
                # as in browsers. Merged into one declaration per property, because MuPDF
                # keeps the *first* of duplicate properties.
                merged: dict[str, str] = {}
                for decls in matched + [el.get("style", "")]:
                    merged.update(_style_dict(decls))
                el.set("style", ";".join(f"{k}:{v}" for k, v in merged.items()))

    grads = _gradient_colors(root)
    replaced = False

    def flatten(value: str) -> str:
        def sub(m):
            nonlocal replaced
            replaced = True
            return grads.get(m.group(1), "#808080")  # unknown pattern / gradient: neutral grey

        return re.sub(r"url\(\s*['\"]?#([^'\")]+)['\"]?\s*\)", sub, value)

    for el in elements:
        for attr in ("fill", "stroke"):
            if "url(" in (el.get(attr) or ""):
                el.set(attr, flatten(el.get(attr)))
        if "url(" in (el.get("style") or ""):
            el.set("style", flatten(el.get("style")))
    if replaced:
        warnings.append(_GRADIENT_WARNING)
    if texts:
        warnings.append(
            f"{texts} piece(s) of live text were skipped - in your design program convert text to "
            "outlines (shapes) and upload again."
        )
    if images:
        warnings.append(f"{images} picture(s) inside the artwork file were skipped - upload them separately as a picture.")
    return ET.tostring(root, encoding="utf-8"), warnings


# --------------------------------------------------------------------------- MuPDF drawings -> shapes

def _subpaths(items, tol: float) -> list[list[tuple[float, float]]]:
    """MuPDF drawing items (lines, cubic curves, rects, quads) -> point lists per subpath."""
    out: list[list[tuple[float, float]]] = []
    cur: list[tuple[float, float]] = []

    def start_at(p):
        nonlocal cur
        if not cur or math.dist(cur[-1], p) > 1e-6:
            if len(cur) >= 2:
                out.append(cur)
            cur = [p]

    for it in items:
        op = it[0]
        if op == "l":
            start_at((it[1].x, it[1].y))
            cur.append((it[2].x, it[2].y))
        elif op == "c":
            p0, p1, p2, p3 = [(q.x, q.y) for q in it[1:5]]
            start_at(p0)
            length = math.dist(p0, p1) + math.dist(p1, p2) + math.dist(p2, p3)
            n = max(2, min(400, int(math.ceil(length / tol))))
            for t in np.linspace(0, 1, n + 1)[1:]:
                u = 1 - t
                cur.append((
                    u**3 * p0[0] + 3 * u * u * t * p1[0] + 3 * u * t * t * p2[0] + t**3 * p3[0],
                    u**3 * p0[1] + 3 * u * u * t * p1[1] + 3 * u * t * t * p2[1] + t**3 * p3[1],
                ))
        elif op in ("re", "qu"):
            if len(cur) >= 2:
                out.append(cur)
            cur = []
            q = it[1].quad if op == "re" else it[1]
            out.append([(q.ul.x, q.ul.y), (q.ur.x, q.ur.y), (q.lr.x, q.lr.y), (q.ll.x, q.ll.y)])
    if len(cur) >= 2:
        out.append(cur)
    return out


def _rgb(color) -> Optional[tuple[int, int, int]]:
    if color is None or len(color) < 3:
        return None
    return tuple(int(round(max(0.0, min(1.0, c)) * 255)) for c in color[:3])  # type: ignore[return-value]


def _visible(opacity) -> bool:
    return opacity is None or opacity > 0


def _parse_svg(svg_text: str) -> tuple[list[_Shape], list[str]]:
    prepared, warnings = _prepare_svg(svg_text)
    pymupdf = pymupdf_module()
    try:
        page = pymupdf.open(stream=prepared, filetype="svg")[0]
    except Exception as exc:
        raise ValueError("The SVG file could not be read.") from exc
    r = page.rect
    tol = max(r.width, r.height, 1e-6) / 1500.0  # curve flattening tolerance

    shapes: list[_Shape] = []
    for d in page.get_drawings():
        kind = d.get("type") or ""
        subpaths = _subpaths(d.get("items", []), tol)
        if not subpaths:
            continue
        fill = _rgb(d.get("fill")) if "f" in kind and _visible(d.get("fill_opacity")) else None
        stroke = _rgb(d.get("color")) if "s" in kind and _visible(d.get("stroke_opacity")) else None
        if fill is not None:
            rings = [Polygon(p).buffer(0) for p in subpaths if len(p) >= 3]
            rings = [g for g in rings if not g.is_empty and g.area > tol * tol]
            if rings:
                if d.get("even_odd"):
                    geom = rings[0]
                    for g in rings[1:]:
                        geom = geom.symmetric_difference(g)
                else:
                    geom = _nonzero(subpaths, rings)
                geom = clean_polygonal(geom)
                if not geom.is_empty:
                    shapes.append(_Shape(geom, fill, "area"))
        if stroke is not None:
            width = float(d.get("width") or 1.0)
            if width > 0:
                lines = [LineString(p) for p in subpaths if len(p) >= 2]
                geom = lines[0] if len(lines) == 1 else MultiLineString(lines)
                shapes.append(_Shape(geom, stroke, "line", width))
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
