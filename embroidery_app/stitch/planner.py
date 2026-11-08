"""Turns an approved Design into a machine-ready StitchPlan.

Responsibilities:
* classify every object (fill / satin / running) when the layer says "auto"
* apply pull compensation
* sequence colours (layer order) and objects (nearest-neighbour within a colour)
* generate underlay + top stitching
* connect runs: travel stitches inside a shape, otherwise jumps; jumps longer than the
  trim threshold get a trim; tie-in / tie-off locks around every trim and colour change
"""

from __future__ import annotations

from dataclasses import dataclass, replace

from shapely.geometry import LineString, Point, Polygon
from shapely.geometry.base import BaseGeometry

from ..geometry import Pt, dist, iter_polygons, segment_inside, subdivide
from ..models import COLOR_CHANGE, JUMP, STITCH, TRIM, ColorBlock, ColorLayer, Design, DesignObject, Run, StitchParams, StitchPlan
from . import generators as gen
from .optimize import optimize_ops
from .rows import best_satin_angle

ANGLE_OFFSETS = (0, 90, -30, 60, 30, -60)


@dataclass
class _Item:
    oid: int
    geom: BaseGeometry  # polygon (areas) or line
    method: str  # fill | satin | satin_line | satin_band | running | outline
    width: float = 0.0
    satin_angle: float | None = None
    centre: LineString | None = None  # centreline for ring-shaped bands
    angle: float = 0.0  # fill direction for this item
    source_id: str = ""  # DesignObject.id it came from
    params: StitchParams | None = None  # design params with this shape's overrides


# Methods whose stitch direction can be chosen; the others follow the shape's path.
DIRECTIONAL_METHODS = ("fill", "satin")
# Methods affected by the per-shape thickness / extra-width settings.
DENSITY_METHODS = ("fill", "satin", "satin_band", "satin_line")
OUTLINE_METHODS = ("running", "outline")


def shape_params(obj: DesignObject, params: StitchParams) -> StitchParams:
    """Design-wide stitch params with this shape's own thickness settings applied.
    Returns ``params`` itself when the shape has none (identical behaviour)."""
    changes = {}
    if obj.spacing_mm is not None:
        changes["fill_spacing_mm"] = obj.spacing_mm
        changes["satin_spacing_mm"] = obj.spacing_mm
    if obj.pull_mm is not None:
        changes["pull_compensation_mm"] = obj.pull_mm
    if obj.bold is not None:
        changes["running_repeats"] = 3 if obj.bold else 1
    return replace(params, **changes) if changes else params


def resolve_settings(obj: DesignObject, layer: ColorLayer, position: int, params: StitchParams) -> tuple[str, float, float | None]:
    """Effective (stitch type, fill angle, satin angle) for one shape.

    Precedence: shape setting -> colour setting -> automatic. A colour's direction
    only steers fills (as it always has); an explicit *shape* direction also turns
    satin, so "same direction everywhere" really applies to every shape."""
    forced = obj.stitch_type or layer.stitch_type
    fill_angle = obj.angle if obj.angle is not None else _layer_angle(position, layer.angle, params)
    return forced, fill_angle, obj.angle


def classify(obj: DesignObject, forced: str, params: StitchParams, satin_angle: float | None = None) -> list[_Item]:
    """Decide the stitch method for an object. ``satin_angle`` forces the direction
    of single-angle satin (otherwise the shortest-row angle is used)."""
    items: list[_Item] = []
    if obj.kind == "line":
        w = obj.stroke_width_mm
        if forced == "running" or (forced == "auto" and w < 1.0):
            return [_Item(-1, obj.geometry, "running", w)]
        if forced == "fill" or (forced == "auto" and w > params.satin_max_width_mm):
            poly = obj.geometry.buffer(w / 2, cap_style="flat", join_style="mitre", mitre_limit=2.0)
            return [_Item(-1, p, "fill") for p in iter_polygons(poly)]
        return [_Item(-1, obj.geometry, "satin_line", max(w, params.satin_min_width_mm))]

    for poly in iter_polygons(obj.geometry):
        if poly.area < params.min_object_area_mm2:
            continue
        if forced == "running":
            items.append(_Item(-1, poly, "outline"))
            continue
        if forced == "fill":
            items.append(_Item(-1, poly, "fill"))
            continue
        narrow = poly.buffer(-params.satin_max_width_mm / 2).is_empty
        if narrow and len(poly.interiors) == 1:
            centre = gen.band_centerline(poly)
            if centre is not None:
                items.append(_Item(-1, poly, "satin_band", centre=centre))
                continue
        if forced == "satin" or narrow:
            angle, p95 = best_satin_angle(poly)
            # average stroke width of a long thin shape ~ 2*area/perimeter; a single-angle
            # satin is only good when its rows stay close to that width (straight-ish columns)
            width_est = 2 * poly.area / max(poly.length, 1e-6)
            straight_column = p95 <= min(params.satin_max_width_mm, 1.6 * width_est + 0.6)
            if forced == "satin" or straight_column:
                items.append(_Item(-1, poly, "satin", satin_angle=angle if satin_angle is None else satin_angle))
                continue
        items.append(_Item(-1, poly, "fill"))
    return items


def _layer_angle(position: int, layer_angle: float | None, params: StitchParams) -> float:
    if layer_angle is not None:
        return layer_angle
    if not params.vary_fill_angle:
        return params.fill_angle
    return params.fill_angle + ANGLE_OFFSETS[position % len(ANGLE_OFFSETS)]


def _compensate(poly: Polygon, params: StitchParams) -> Polygon:
    if params.pull_compensation_mm <= 0:
        return poly
    grown = poly.buffer(params.pull_compensation_mm, join_style="mitre", mitre_limit=2.0)
    parts = sorted(iter_polygons(grown), key=lambda p: -p.area)
    return parts[0] if parts else poly


def _stitch_item(item: _Item, angle: float, params: StitchParams, start: Pt) -> list[Run]:
    runs: list[Run] = []

    def add(kind: str, pieces: list[list[Pt]]) -> None:
        for p in pieces:
            if len(p) >= 2:
                runs.append(Run(kind, p, item.oid))

    if item.method == "fill":
        poly = _compensate(item.geom, params)
        if params.underlay:
            add("underlay", gen.fill_underlay(item.geom, angle, params.underlay_spacing_mm, start, smart=params.optimize))
        cur = runs[-1].points[-1] if runs else start
        add("fill", gen.tatami_fill(poly, angle, params.fill_spacing_mm, params.fill_stitch_length_mm, cur, params.max_stitch_mm))
    elif item.method == "satin":
        poly = _compensate(item.geom, params)
        if params.underlay:
            add("underlay", gen.satin_underlay(item.geom, start))
        cur = runs[-1].points[-1] if runs else start
        add("satin", gen.satin_area(poly, params.satin_spacing_mm, cur, params.max_stitch_mm, item.satin_angle))
    elif item.method == "satin_band":
        if params.underlay:
            add("underlay", [gen.running(item.centre, 2.0)])
        cur = runs[-1].points[-1] if runs else start
        add("satin", [gen.satin_band(item.geom, item.centre, params.satin_spacing_mm, params.pull_compensation_mm, cur, params.max_stitch_mm)])
    elif item.method == "satin_line":
        line: LineString = item.geom
        if dist(start, line.coords[-1]) < dist(start, line.coords[0]):
            line = LineString(list(line.coords)[::-1])
        if params.underlay and item.width >= 1.5:
            add("underlay", [gen.running(line, 2.0)])
            line = LineString(list(line.coords)[::-1])  # satin comes back over the underlay
        add("satin", [gen.satin_line(line, item.width, params.satin_spacing_mm, params.pull_compensation_mm, params.max_stitch_mm)])
    elif item.method == "running":
        line = item.geom
        if dist(start, line.coords[-1]) < dist(start, line.coords[0]):
            line = LineString(list(line.coords)[::-1])
        add("running", [gen.running(line, params.running_stitch_length_mm, params.running_repeats)])
    elif item.method == "outline":
        pieces = gen.ring_from(item.geom, start, params.running_stitch_length_mm, holes=True)
        if params.running_repeats >= 3:
            pieces = [gen.running(LineString(p), params.running_stitch_length_mm, 3) for p in pieces]
        add("running", pieces)
    return runs


def describe_objects(design: Design, params: StitchParams) -> list[dict]:
    """Per-shape info for the editor: own settings plus the effective stitch method
    and direction exactly as ``plan_stitches`` would resolve them."""
    positions = {l.index: i for i, l in enumerate(l for l in design.layers if l.enabled)}
    out = []
    for obj in design.objects:
        try:
            layer = design.layer(obj.layer)
        except KeyError:
            continue
        forced, fill_angle, satin_angle = resolve_settings(obj, layer, positions.get(obj.layer, 0), params)
        items = classify(obj, forced, params, satin_angle)
        methods = sorted({it.method for it in items})
        method = methods[0] if len(methods) == 1 else ("mixed" if methods else "none")
        if method == "fill":
            angle = fill_angle
        elif method == "satin":
            angle = items[0].satin_angle
        else:
            angle = None
        area = obj.geometry.area if obj.kind == "area" else 0.0
        sp = shape_params(obj, params)
        density = any(m in DENSITY_METHODS for m in methods)
        outline = any(m in OUTLINE_METHODS for m in methods)
        if method == "fill":
            spacing = sp.fill_spacing_mm
        elif method in DENSITY_METHODS:
            spacing = sp.satin_spacing_mm
        else:
            spacing = None
        out.append({
            "id": obj.id,
            "layer": obj.layer,
            "kind": obj.kind,
            "area_mm2": round(area, 1),
            "stitch_type": obj.stitch_type,  # own settings (None = same as colour / design)
            "angle": obj.angle,
            "enabled": obj.enabled,
            "spacing_mm": obj.spacing_mm,
            "pull_mm": obj.pull_mm,
            "bold": obj.bold,
            "method": method,  # fill | satin | satin_band | satin_line | running | outline | mixed | none
            "effective_angle": None if angle is None else round(angle % 180, 1),
            "effective_spacing_mm": spacing,
            "effective_pull_mm": sp.pull_compensation_mm if density else None,
            "effective_bold": sp.running_repeats >= 3 if outline else None,
            "direction_applies": obj.kind == "area" and any(m in DIRECTIONAL_METHODS for m in methods),
            "thickness_applies": density,
            "bold_applies": outline,
            "too_small": not items,
        })
    return out


def _entry_distance(item: _Item, pos: Pt) -> float:
    return item.geom.distance(Point(pos))


def plan_stitches(design: Design, params: StitchParams) -> StitchPlan:
    # Each colour is stitched as continuous regions, never along internal seams of
    # merged shades. Also covers designs saved before normalisation existed.
    design = design.normalized()
    blocks: list[ColorBlock] = []
    geoms: dict[int, BaseGeometry] = {}
    pos: Pt = (0.0, 0.0)
    oid = 0
    min_fill_spacing = params.fill_spacing_mm  # smallest fill row gap actually sewn
    enabled = [l for l in design.layers if l.enabled]
    for position, layer in enumerate(enabled):
        items: list[_Item] = []
        for obj in design.objects:
            if obj.layer != layer.index or not obj.enabled:
                continue
            forced, fill_angle, satin_angle = resolve_settings(obj, layer, position, params)
            own = shape_params(obj, params)
            for it in classify(obj, forced, params, satin_angle):
                it.oid = oid
                it.angle = fill_angle
                it.source_id = obj.id
                it.params = own
                if it.method == "fill":
                    min_fill_spacing = min(min_fill_spacing, own.fill_spacing_mm)
                geoms[oid] = it.geom if it.method not in ("running", "satin_line") else it.geom.buffer(max(0.5, it.width / 2))
                oid += 1
                items.append(it)
        if not items:
            continue
        block = ColorBlock(rgb=layer.rgb, name=layer.name)
        while items:
            idx = min(range(len(items)), key=lambda i: _entry_distance(items[i], pos))
            item = items.pop(idx)
            runs = _stitch_item(item, item.angle, item.params or params, pos)
            if runs:
                block.runs.extend(runs)
                pos = runs[-1].points[-1]
        if block.runs:
            blocks.append(block)

    plan = StitchPlan(width_mm=design.width_mm, height_mm=design.height_mm, blocks=blocks)
    plan.ops = assemble(blocks, geoms, params)
    if params.optimize:
        # threshold below the *smallest* row gap in use, so turn-around stitches of a
        # shape set to "dense" are never mistaken for stacked stitches
        plan.ops, plan.optimization = optimize_ops(plan.ops, min_fill_spacing, params.max_stitch_mm)
    return plan


# --------------------------------------------------------------------------- assembly

def _tie(points: list[Pt], at_start: bool, length: float = 0.5) -> list[Pt]:
    """Lock stitches: tiny back-and-forth along the first/last segment."""
    if len(points) < 2:
        return []
    a, b = (points[0], points[1]) if at_start else (points[-1], points[-2])
    d = dist(a, b)
    if d < 1e-6:
        return []
    t = min(length, d * 0.9) / d
    m = (a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t)
    # at the start the caller stitches the first point itself right after
    return [m, a, m] if at_start else [m, a, m, a]


def assemble(blocks: list[ColorBlock], geoms: dict[int, BaseGeometry], params: StitchParams) -> list[tuple[str, float, float, str]]:
    ops: list[tuple[str, float, float, str]] = []
    cur: Pt | None = None
    last_run: Run | None = None

    def stitch(p: Pt, kind: str) -> None:
        ops.append((STITCH, p[0], p[1], kind))

    def end_segment() -> None:
        if params.tie_stitches and last_run is not None:
            for p in _tie(last_run.points, at_start=False):
                stitch(p, "tie")

    for bi, block in enumerate(blocks):
        if bi > 0:
            end_segment()
            ops.append((TRIM, cur[0], cur[1], ""))  # type: ignore[index]
            ops.append((COLOR_CHANGE, cur[0], cur[1], ""))  # type: ignore[index]
            last_run = None
        for run in block.runs:
            pts = run.points
            start = pts[0]
            needs_jump = cur is None or last_run is None
            if not needs_jump:
                gap = dist(cur, start)  # type: ignore[arg-type]
                # straight connections inside an object are only hidden when the top
                # stitching still has to cover them (underlay -> top / underlay -> underlay)
                same = last_run.object_id == run.object_id and last_run.kind == "underlay"  # type: ignore[union-attr]
                geom = geoms.get(run.object_id)
                if gap <= 1.0 or (same and geom is not None and segment_inside(geom, cur, start)):  # type: ignore[arg-type]
                    for p in subdivide(cur, start, min(params.max_stitch_mm, 3.0)):  # type: ignore[arg-type]
                        stitch(p, "travel")
                else:
                    needs_jump = True
                    if gap > params.trim_threshold_mm:
                        end_segment()
                        ops.append((TRIM, cur[0], cur[1], ""))  # type: ignore[index]
            if needs_jump:
                ops.append((JUMP, start[0], start[1], ""))
                if params.tie_stitches:
                    for p in _tie(pts, at_start=True):
                        stitch(p, "tie")
                stitch(start, run.kind)
            for p in pts[1:]:
                stitch(p, run.kind)
            cur = pts[-1]
            last_run = run
    if cur is not None:
        end_segment()
        ops.append((TRIM, cur[0], cur[1], ""))
    return ops
