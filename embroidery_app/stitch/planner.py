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

import math
from dataclasses import dataclass, replace

import shapely
from shapely.geometry import LineString, Point, Polygon
from shapely.geometry.base import BaseGeometry

from ..geometry import Pt, dist, iter_polygons, segment_inside, subdivide
from ..models import COLOR_CHANGE, JUMP, STITCH, TRIM, ColorBlock, ColorLayer, Design, DesignObject, Run, StitchParams, StitchPlan
from . import generators as gen
from . import methods  # noqa: F401  (registers the built-in stitch methods)
from . import registry
from . import smart
from . import strokes as st
from .methods import thread_limits as _limits
from .optimize import optimize_ops
from .rows import best_satin_angle
from .safety import cap_stitch_length, machine_check, make_safe

ANGLE_OFFSETS = (0, 90, -30, 60, 30, -60)


@dataclass
class _Item:
    oid: int
    geom: BaseGeometry  # polygon (areas) or line
    method: str  # a registered stitch method (see stitch/registry.py, stitch/methods.py)
    width: float = 0.0
    satin_angle: float | None = None
    centre: LineString | None = None  # centreline for ring-shaped bands
    angle: float = 0.0  # fill direction for this item
    source_id: str = ""  # DesignObject.id it came from
    params: StitchParams | None = None  # design params with this shape's overrides
    ink: st.InkMap | None = None  # same-colour ink, so widened strokes keep gaps open
    narrow_spots: list | None = None  # stroke spots that could not reach a sewable width


# Small-lettering methods (see stitch/strokes.py): narrow free-form strokes up to this
# average width get satin that follows each stroke; wider shapes keep the usual methods.
STROKE_MAX_WIDTH_MM = 2.0
CLASSIC_MIN_WIDTH_MM = 1.0  # from this width plain satin / fill sew a shape well too
BAND_MIN_WIDTH_MM = 1.2  # thinner rings go to the stroke engine (widening, gap protection)


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
        forced = "auto" if forced == "lettering" else forced  # a line already follows its path
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
        if forced == "lettering":
            # chosen by the user: follow the strokes whenever the shape can be sewn that way
            limits = st.thread_limits(params.thread_weight, params.satin_min_width_mm)
            if st.is_dot(poly, limits):
                items.append(_Item(-1, poly, "dot"))
                continue
            if narrow and st.fits_stroke_engine(poly):
                items.append(_Item(-1, poly, "stroke"))
                continue
            forced = "auto"  # too wide for a satin column (very large letters): usual methods
        # smart direction: only where nothing was chosen by hand for this shape
        smart_auto = params.smart_direction and forced == "auto" and satin_angle is None
        if smart_auto and not narrow:
            sections = _smart_sections(obj, poly, params)
            if sections:
                items.extend(sections)
                continue
        if forced == "auto" and satin_angle is None:
            limits = st.thread_limits(params.thread_weight, params.satin_min_width_mm)
            if st.is_dot(poly, limits):
                items.append(_Item(-1, poly, "dot"))
                continue
            width = st.small_stroke_width(poly)
            # clean rings already sewable as they are keep the band satin (below)
            band_ok = len(poly.interiors) == 1 and width >= BAND_MIN_WIDTH_MM
            if (
                narrow and width < STROKE_MAX_WIDTH_MM and not band_ok and st.fits_stroke_engine(poly)
                # compact blobs (e.g. letter counters) are not strokes; thinner than a sewable
                # column the stroke engine is still the only way to get them sewn cleanly
                and (width < CLASSIC_MIN_WIDTH_MM or st.analyse_stroke_shape(poly).uncovered_share <= st.MAX_UNCOVERED)
                # smart direction: strokes wide enough to sew as they are become columns (below)
                and not (smart_auto and width >= CLASSIC_MIN_WIDTH_MM and _column_shape(poly))
            ):
                items.append(_Item(-1, poly, "stroke"))
                continue
        if narrow and len(poly.interiors) == 1:
            centre = gen.band_centerline(poly)
            if centre is not None:
                items.append(_Item(-1, poly, "satin_band", centre=centre))
                continue
        if smart_auto and narrow and _column_shape(poly):
            # satin that follows the section's centre line, whatever its width and curve
            items.append(_Item(-1, poly, "column"))
            continue
        if forced == "satin" or narrow:
            angle, p95 = best_satin_angle(poly)
            # average stroke width of a long thin shape ~ 2*area/perimeter; a single-angle
            # satin is only good when its rows stay close to that width (straight-ish columns)
            width_est = 2 * poly.area / max(poly.length, 1e-6)
            straight_column = p95 <= min(params.satin_max_width_mm, _row_tolerance(width_est))
            if forced == "satin" or straight_column:
                if params.smart_direction and satin_angle is None:
                    angle = smart.refine_satin_angle(poly, angle)
                items.append(_Item(-1, poly, "satin", satin_angle=angle if satin_angle is None else satin_angle))
                continue
        items.append(_Item(-1, poly, "fill"))
    return items


SPLIT_SATIN_FROM_MM = 7.0  # satin rows longer than this are auto-split (Wilcom: 7 mm)


def _row_tolerance(width: float) -> float:
    """Longest single-angle satin row still accepted for a column of this average
    width. Narrow columns may slant a little (1.6 x width + 0.6 mm, never past the
    7 mm split length); a wide, auto-split column must be nearly straight (rows at most
    15% longer than its width), so a curved stroke is never sewn with long slanted rows."""
    return min(1.6 * width + 0.6, max(SPLIT_SATIN_FROM_MM, 1.15 * width + 0.6))


def _column_shape(poly: Polygon) -> bool:
    """Can satin columns along its centre lines sew this (narrow) shape?"""
    return smart.fits_columns(poly) and smart.analyse_columns(poly).ok


def _smart_sections(obj: DesignObject, poly: Polygon, params: StitchParams) -> list[_Item]:
    """A wide shape with narrow arms, as separately sewn sections: every arm that can
    be sewn as satin gets it (overlapping the body at the seam), the rest is filled.
    Empty when the shape has no such arm - it is then sewn in one piece as usual."""
    arms: list[Polygon] = []
    items: list[_Item] = []
    for arm in smart.narrow_arms(poly, params.satin_max_width_mm):
        sewn = classify(DesignObject(obj.layer, smart.with_seam(arm, poly)), "auto", params)
        if sewn and all(it.method != "fill" for it in sewn):
            arms.append(arm)
            items.extend(sewn)
    if not arms:
        return []
    body = poly.difference(shapely.union_all(arms))
    bodies = [_Item(-1, part, "fill") for part in iter_polygons(body) if part.area >= params.min_object_area_mm2]
    return bodies + items  # the body first, the details on top of it


def smart_fill_angles(items: list[_Item], obj: DesignObject, layer: ColorLayer, base: float, params: StitchParams) -> None:
    """Smart direction: every filled section gets its own best direction, unless the
    shape or its colour has a direction of its own. ``base`` is the colour's direction."""
    for it in items:
        it.angle = base
        if params.smart_direction and it.method == "fill" and obj.angle is None and layer.angle is None:
            it.angle = smart.fill_angle(it.geom, base)


def _layer_angle(position: int, layer_angle: float | None, params: StitchParams) -> float:
    if layer_angle is not None:
        return layer_angle
    if not params.vary_fill_angle:
        return params.fill_angle
    return params.fill_angle + ANGLE_OFFSETS[position % len(ANGLE_OFFSETS)]


def _stitch_item(item: _Item, angle: float, params: StitchParams, start: Pt) -> list[Run]:
    """Sew one classified piece with its registered stitch method."""
    return registry.get(item.method).generate(item, angle, params, start)


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
        smart_fill_angles(items, obj, layer, fill_angle, params)
        methods = sorted({it.method for it in items})
        method = methods[0] if len(methods) == 1 else ("mixed" if methods else "none")
        if method == "fill":
            angle = max(items, key=lambda it: it.geom.area).angle  # the largest section's direction
        elif method == "satin":
            angle = items[0].satin_angle
        else:
            angle = None
        area = obj.geometry.area if obj.kind == "area" else 0.0
        sp = shape_params(obj, params)
        density = any(registry.is_density(m) for m in methods)
        outline = any(registry.is_outline(m) for m in methods)
        if method == "fill":
            spacing = sp.fill_spacing_mm
        elif method in methods and registry.is_density(method):  # one method (not mixed / none)
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
            "method": method,  # a registered stitch method, or mixed | none
            "effective_angle": None if angle is None else round(angle % 180, 1),
            "effective_spacing_mm": spacing,
            "effective_pull_mm": sp.pull_compensation_mm if density else None,
            "effective_bold": sp.running_repeats >= 3 if outline else None,
            "direction_applies": obj.kind == "area" and any(registry.is_directional(m) for m in methods),
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
    stroke_oids: set[int] = set()
    narrow_spots: list[Pt] = []
    min_fill_spacing = params.fill_spacing_mm  # smallest fill row gap actually sewn
    enabled = [l for l in design.layers if l.enabled]
    for position, layer in enumerate(enabled):
        items: list[_Item] = []
        for obj in design.objects:
            if obj.layer != layer.index or not obj.enabled:
                continue
            forced, fill_angle, satin_angle = resolve_settings(obj, layer, position, params)
            own = shape_params(obj, params)
            found = classify(obj, forced, params, satin_angle)
            smart_fill_angles(found, obj, layer, fill_angle, params)
            for it in found:
                it.oid = oid
                it.source_id = obj.id
                it.params = own
                if it.method == "fill":
                    min_fill_spacing = min(min_fill_spacing, own.fill_spacing_mm)
                if registry.is_multi_piece(it.method):
                    stroke_oids.add(oid)
                geoms[oid] = it.geom if it.method not in ("running", "satin_line") else it.geom.buffer(max(0.5, it.width / 2))
                oid += 1
                items.append(it)
        if not items:
            continue
        if any(it.method in st.STROKE_METHODS for it in items):
            # everything sewn in this colour: widened strokes must not close gaps to it
            ink = st.InkMap([geoms[it.oid] for it in items], design.width_mm, design.height_mm)
            for it in items:
                it.ink = ink
        block = ColorBlock(rgb=layer.rgb, name=layer.name)
        while items:
            idx = min(range(len(items)), key=lambda i: _entry_distance(items[i], pos))
            item = items.pop(idx)
            runs = _stitch_item(item, item.angle, item.params or params, pos)
            narrow_spots.extend(item.narrow_spots or [])
            if runs:
                block.runs.extend(runs)
                pos = runs[-1].points[-1]
        if block.runs:
            blocks.append(block)

    plan = StitchPlan(width_mm=design.width_mm, height_mm=design.height_mm, blocks=blocks)
    limits = _limits(params)
    plan.thread_weight = limits.weight
    plan.ops = assemble(blocks, geoms, params, stroke_oids)
    # always on: no stitch shorter than the thread can take (thread breaks / needle cuts)
    # (kept below the smallest fill row gap: the turn-around between two rows is not a fault)
    plan.ops = make_safe(plan.ops, min(limits.min_stitch_mm, 0.9 * min_fill_spacing), params.max_stitch_mm)
    if params.optimize:
        # threshold below the *smallest* row gap in use, so turn-around stitches of a
        # shape set to "dense" are never mistaken for stacked stitches
        plan.ops, plan.optimization = optimize_ops(plan.ops, min_fill_spacing, params.max_stitch_mm)
    plan.ops = cap_stitch_length(plan.ops, params.max_stitch_mm)  # last guarantee, whatever sewed it
    plan.risks = machine_check(plan.ops, limits, narrow_spots)
    return plan


# --------------------------------------------------------------------------- assembly

TIE_STEP_MM = 0.6  # lock stitch length: long enough to hold, never below the 0.5 mm minimum stitch
STROKE_TRAVEL_MM = 4.0  # max hidden run between strokes of one letter instead of a cut


def _reach(points: list[Pt], origin: Pt, d: float) -> tuple[Pt, int] | None:
    """First point along a polyline whose straight-line distance from ``origin`` is
    ``d``, with the index of the segment it lies on (None if the line never gets that
    far). Straight-line, not along the path: on a zigzag satin path 0.6 mm *along* the
    path can be a 0.3 mm stitch."""
    for i, (a, b) in enumerate(zip(points, points[1:])):
        if dist(origin, b) < d:
            continue
        fx, fy = a[0] - origin[0], a[1] - origin[1]
        vx, vy = b[0] - a[0], b[1] - a[1]
        qa = vx * vx + vy * vy
        qc = fx * fx + fy * fy - d * d
        if qa < 1e-12 or qc >= 0:
            return (b if qa < 1e-12 else a), i
        qb = 2 * (fx * vx + fy * vy)
        t = (-qb + math.sqrt(max(qb * qb - 4 * qa * qc, 0.0))) / (2 * qa)
        return (a[0] + vx * t, a[1] + vy * t), i
    return None


def _tie(points: list[Pt], at_start: bool, length: float = TIE_STEP_MM, min_stitch: float = 0.5) -> list[Pt]:
    """Lock stitches along the run's own path (they are sewn over / sit under the
    last stitches): two steps forward and one back at the start, the mirror at the
    end. Every lock stitch is at least ``min_stitch`` long as sewn (straight line);
    short pieces get a single step, pieces too short for even that get none rather
    than stacked drops."""
    path = points if at_start else points[::-1]
    if len(path) < 2:
        return []
    step = max(length, min_stitch)
    first = _reach(path, path[0], step)
    second = _reach([first[0], *path[first[1] + 1 :]], first[0], step) if first else None
    if first and second:
        p1, p2 = first[0], second[0]
        # start: (landed at path[0]) -> p1 -> p2 -> p1 -> caller stitches path[0] and runs over them
        # end:   (at path end)       -> p1 -> p2 -> p1 -> end
        return [p1, p2, p1] if at_start else [p1, p2, p1, path[0]]
    single = first or _reach(path, path[0], min_stitch)
    if single is None:
        return []
    p1 = single[0]
    return [p1] if at_start else [p1, path[0]]


def assemble(
    blocks: list[ColorBlock],
    geoms: dict[int, BaseGeometry],
    params: StitchParams,
    stroke_oids: set[int] | frozenset = frozenset(),
) -> list[tuple[str, float, float, str]]:
    ops: list[tuple[str, float, float, str]] = []
    cur: Pt | None = None
    last_run: Run | None = None
    min_stitch = st.thread_limits(params.thread_weight, params.satin_min_width_mm).min_stitch_mm
    cut = True  # thread was cut (start, trim, colour change): the next run needs a tie-in

    def stitch(p: Pt, kind: str) -> None:
        ops.append((STITCH, p[0], p[1], kind))

    def end_segment() -> None:
        if params.tie_stitches and last_run is not None:
            for p in _tie(last_run.points, at_start=False, min_stitch=min_stitch):
                stitch(p, "tie")

    for bi, block in enumerate(blocks):
        if bi > 0:
            end_segment()
            ops.append((TRIM, cur[0], cur[1], ""))  # type: ignore[index]
            ops.append((COLOR_CHANGE, cur[0], cur[1], ""))  # type: ignore[index]
            last_run = None
            cut = True
        for run in block.runs:
            pts = run.points
            start = pts[0]
            needs_jump = cur is None or last_run is None
            if not needs_jump:
                gap = dist(cur, start)  # type: ignore[arg-type]
                # straight connections inside an object are only hidden when the top
                # stitching still has to cover them (underlay -> top / underlay -> underlay)
                same = last_run.object_id == run.object_id and last_run.kind == "underlay"  # type: ignore[union-attr]
                # strokes of one letter: a short run inside the letter sinks into its satin
                # and saves a cut + two locks on a tiny piece
                stroke = last_run.object_id == run.object_id and run.object_id in stroke_oids and gap <= STROKE_TRAVEL_MM  # type: ignore[union-attr]
                geom = geoms.get(run.object_id)
                if gap <= 1.0 or ((same or stroke) and geom is not None and segment_inside(geom, cur, start)):  # type: ignore[arg-type]
                    step = min(params.max_stitch_mm, 1.5 if stroke else 3.0)
                    for p in subdivide(cur, start, step):  # type: ignore[arg-type]
                        stitch(p, "travel")
                else:
                    needs_jump = True
                    if gap > params.trim_threshold_mm:
                        end_segment()
                        ops.append((TRIM, cur[0], cur[1], ""))  # type: ignore[index]
                        cut = True
            if needs_jump:
                ops.append((JUMP, start[0], start[1], ""))
                # locks only where the thread was cut; a jump without trim keeps the thread tied
                if params.tie_stitches and cut:
                    for p in _tie(pts, at_start=True, min_stitch=min_stitch):
                        stitch(p, "tie")
                stitch(start, run.kind)
                cut = False
            for p in pts[1:]:
                stitch(p, run.kind)
            cur = pts[-1]
            last_run = run
    if cur is not None:
        end_segment()
        ops.append((TRIM, cur[0], cur[1], ""))
    return ops
