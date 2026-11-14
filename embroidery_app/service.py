"""Workflow orchestration + on-disk project storage.

Each project lives in ``data/projects/<id>/``:
  meta.json     filename, kind, options, stitch params
  source.*      the original upload (never modified)
  work.png      current raster working image (original or AI-cleaned)
  vector.svg    SVG text for vector uploads (AI/PDF converted on upload)
  design.json   the extracted, user-editable design
  history/      undo / redo snapshots of design.json
  design.extracted.json, reference.png   editing reference (as first extracted)
"""

from __future__ import annotations

import hashlib
import io
import json
import math
import re
import shutil
import threading
import time
import uuid
import zipfile
from dataclasses import asdict, replace
from pathlib import Path

from .ai import tasks as ai_tasks
from .config import settings
from .export import FORMATS, design_to_svg, export_embroidery, plan_preview_json, render_png, worksheet
from .ingest import detect_kind
from .ingest.raster import RasterOptions, encode_png, load_raster, raster_to_design
from .ingest.vector import VectorOptions, ai_or_pdf_to_svg, svg_to_design
from .export.vector import object_d
from .geometry import iter_polygons, union_all
from .ingest.raster import MARK_MIN_MM2
from .models import (
    PULL_RANGE_MM,
    SPACING_RANGE_MM,
    STITCH_TYPES,
    UNDERLAY_STYLES,
    ColorLayer,
    clean_guide,
    Design,
    StitchParams,
    StitchPlan,
    normalize_angle,
)
from .stitch import plan_stitches
from .stitch.planner import describe_objects
from .stitch.optimize import MAX_COVERAGE_LOSS_PCT, coverage_loss_pct

PROJECTS = settings.data_dir / "projects"
PROJECTS.mkdir(parents=True, exist_ok=True)
_ID_RE = re.compile(r"^[a-f0-9]{12}$")
_plan_cache: dict[str, dict[str, StitchPlan]] = {}  # pid -> {params key: plan}; holds original + optimised
_lock = threading.Lock()


class NotFound(Exception):
    pass


# --------------------------------------------------------------------------- storage

def _dir(pid: str) -> Path:
    if not _ID_RE.match(pid):
        raise NotFound(pid)
    d = PROJECTS / pid
    if not d.exists():
        raise NotFound(pid)
    return d


def _meta(pid: str) -> dict:
    return json.loads((_dir(pid) / "meta.json").read_text(encoding="utf-8"))


def _save_meta(pid: str, meta: dict) -> None:
    (_dir(pid) / "meta.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")


def load_design(pid: str) -> Design:
    path = _dir(pid) / "design.json"
    if not path.exists():
        raise ValueError("Prepare the design first (step 2).")
    raw = json.loads(path.read_text(encoding="utf-8"))
    design = Design.from_dict(raw)
    if any("id" not in o for o in raw.get("objects", [])):
        # saved before shapes had ids: persist the new ids so they stay stable
        path.write_text(json.dumps(design.to_dict()), encoding="utf-8")
    return design


HISTORY_LIMIT = 50  # undo steps kept per project


def _history(pid: str, kind: str) -> Path:
    """Undo / redo stacks: numbered snapshots of design.json (newest = highest)."""
    d = _dir(pid)
    p = d / "history" / kind
    p.mkdir(parents=True, exist_ok=True)
    old = d / "design.prev.json"  # one-level undo of earlier versions
    if kind == "undo" and old.exists():
        if not any(p.glob("*.json")):
            shutil.move(str(old), p / "000001.json")
        else:
            old.unlink()
    return p


def _entries(p: Path) -> list[Path]:
    return sorted(p.glob("*.json"))


def _push(pid: str, kind: str, text: str) -> None:
    p = _history(pid, kind)
    files = _entries(p)
    n = int(files[-1].stem) + 1 if files else 1
    (p / f"{n:06d}.json").write_text(text, encoding="utf-8")
    for f in _entries(p)[:-HISTORY_LIMIT]:
        f.unlink()


def _pop(pid: str, kind: str) -> str | None:
    files = _entries(_history(pid, kind))
    if not files:
        return None
    text = files[-1].read_text(encoding="utf-8")
    files[-1].unlink()
    return text


def _save_design(pid: str, design: Design, undoable: bool = True) -> None:
    d = _dir(pid)
    current = d / "design.json"
    if undoable and current.exists():
        _push(pid, "undo", current.read_text(encoding="utf-8"))
        for f in _entries(_history(pid, "redo")):  # a new change ends the redo chain
            f.unlink()
    current.write_text(json.dumps(design.to_dict()), encoding="utf-8")
    _plan_cache.pop(pid, None)


def can_undo(pid: str) -> bool:
    return bool(_entries(_history(pid, "undo")))


def can_redo(pid: str) -> bool:
    return bool(_entries(_history(pid, "redo")))


def _step(pid: str, src: str, dst: str, empty_msg: str) -> dict:
    current = _dir(pid) / "design.json"
    text = _pop(pid, src)
    if text is None:
        raise ValueError(empty_msg)
    if current.exists():
        _push(pid, dst, current.read_text(encoding="utf-8"))
    current.write_text(text, encoding="utf-8")
    _plan_cache.pop(pid, None)
    return design_payload(load_design(pid), pid)


def undo(pid: str) -> dict:
    return _step(pid, "undo", "redo", "There is nothing to undo.")


def redo(pid: str) -> dict:
    return _step(pid, "redo", "undo", "There is nothing to redo.")


def cleanup_old_projects(max_age_days: float = 7) -> int:
    cutoff = time.time() - max_age_days * 86400
    removed = 0
    for d in PROJECTS.iterdir():
        if d.is_dir() and d.stat().st_mtime < cutoff:
            shutil.rmtree(d, ignore_errors=True)
            removed += 1
    return removed


# --------------------------------------------------------------------------- workflow

def owned_by(pid: str, user_id: int) -> bool:
    """True when the project exists and belongs to this account."""
    try:
        return _meta(pid).get("owner") == user_id
    except (NotFound, FileNotFoundError, ValueError):
        return False


def delete_projects_of(user_id: int) -> int:
    """Remove every project of a deleted account."""
    removed = 0
    for d in PROJECTS.iterdir():
        meta = d / "meta.json"
        try:
            if d.is_dir() and json.loads(meta.read_text(encoding="utf-8")).get("owner") == user_id:
                shutil.rmtree(d, ignore_errors=True)
                removed += 1
        except (OSError, ValueError):
            continue
    return removed


def create_project(filename: str, data: bytes, owner: int | None = None) -> dict:
    if len(data) > settings.max_upload_mb * 1024 * 1024:
        raise ValueError(f"File is larger than {settings.max_upload_mb} MB.")
    kind = detect_kind(filename, data)
    pid = uuid.uuid4().hex[:12]
    d = PROJECTS / pid
    d.mkdir(parents=True)
    safe_name = re.sub(r"[^\w.\- ]", "_", Path(filename).name)[:80] or "design"
    ext = Path(safe_name).suffix.lower() or {"raster": ".png", "svg": ".svg", "pdf": ".pdf"}[kind]
    (d / f"source{ext}").write_bytes(data)
    info: dict = {}
    try:
        if kind == "raster":
            rgba = load_raster(data)
            (d / "work.png").write_bytes(encode_png(rgba))
            info = {"width_px": int(rgba.shape[1]), "height_px": int(rgba.shape[0])}
        else:
            svg = data.decode("utf-8", errors="replace") if kind == "svg" else ai_or_pdf_to_svg(data)
            (d / "vector.svg").write_text(svg, encoding="utf-8")
    except Exception:
        shutil.rmtree(d, ignore_errors=True)
        raise
    meta = {"id": pid, "filename": safe_name, "kind": kind, "created": time.time(), "info": info, "ai_cleaned": False,
            "owner": owner}
    (d / "meta.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")
    return meta


def source_file(pid: str) -> tuple[Path, str]:
    d = _dir(pid)
    meta = _meta(pid)
    if meta["kind"] == "raster":
        return d / "work.png", "image/png"
    return d / "vector.svg", "image/svg+xml"


def original_file(pid: str) -> tuple[Path, str]:
    d = _dir(pid)
    meta = _meta(pid)
    if meta["kind"] == "raster":
        src = next(d.glob("source.*"))
        rgba = load_raster(src.read_bytes())
        out = d / "original.png"
        if not out.exists():
            out.write_bytes(encode_png(rgba))
        return out, "image/png"
    return d / "vector.svg", "image/svg+xml"


def _options(cls, values: dict):
    obj = cls()
    for k, v in (values or {}).items():
        if hasattr(obj, k):
            cur = getattr(obj, k)
            if v is None or v == "":
                setattr(obj, k, None if k == "num_colors" else cur)
            elif isinstance(cur, bool):
                setattr(obj, k, bool(v))
            elif isinstance(cur, (int, float)) and not isinstance(cur, bool):
                setattr(obj, k, type(cur)(v))
            else:
                setattr(obj, k, int(v) if k == "num_colors" else v)
    return obj


def extract(pid: str, options: dict) -> dict:
    d = _dir(pid)
    meta = _meta(pid)
    width = float(options.get("width_mm") or 100)
    if not 10 <= width <= 500:
        raise ValueError("Design width must be between 10 and 500 mm.")
    reference = None
    if meta["kind"] == "raster":
        opts = _options(RasterOptions, options)
        rgba = load_raster((d / "work.png").read_bytes())
        design, preview = raster_to_design(rgba, opts)
        (d / "extracted.png").write_bytes(encode_png(preview))
        reference = getattr(design, "reference", None)
    else:
        opts = _options(VectorOptions, options)
        design = svg_to_design((d / "vector.svg").read_text(encoding="utf-8"), opts)
    design.normalize()  # one continuous region per colour (merged/overlapping same-colour shapes)
    if not design.objects:
        raise ValueError("Nothing to stitch was found. Try fewer colours, another Background option or a larger design width.")
    if (d / "design.json").exists():
        _carry_shape_settings(load_design(pid), design)
    _save_design(pid, design)
    _save_reference(pid, meta, design, reference)
    meta["extract_options"] = asdict(opts)
    _save_meta(pid, meta)
    return design_payload(design, pid)


REFERENCE_MAX_PX = 1600


def _save_reference(pid: str, meta: dict, design: Design, reference: dict | None) -> None:
    """Keep the design as first extracted (and, for pictures, the picture exactly
    where the shapes were traced from) as a see-through guide for shape editing."""
    d = _dir(pid)
    (d / "design.extracted.json").write_text(json.dumps(design.to_dict()), encoding="utf-8")
    meta["reference_size"] = [round(design.width_mm, 4), round(design.height_mm, 4)]
    meta.pop("reference_box", None)
    (d / "reference.png").unlink(missing_ok=True)
    if reference is None:
        return
    pic = reference["picture"]
    h, w = pic.shape[:2]
    s = min(1.0, REFERENCE_MAX_PX / max(h, w))
    if s < 1:
        import cv2

        pic = cv2.resize(pic, (max(1, int(w * s)), max(1, int(h * s))), interpolation=cv2.INTER_AREA)
    (d / "reference.png").write_bytes(encode_png(pic))
    meta["reference_box"] = reference["box"]


def reference_info(pid: str) -> dict:
    d = _dir(pid)
    meta = _meta(pid)
    box = meta.get("reference_box")
    snap = d / "design.extracted.json"
    return {
        "picture": bool(box) and (d / "reference.png").exists(),
        "box": box,
        "design": snap.exists(),
        "size": meta.get("reference_size"),  # [w, h] mm of the design as first extracted
        "version": int(snap.stat().st_mtime_ns // 1_000_000) if snap.exists() else 0,  # cache-buster
    }


def reference_file(pid: str, kind: str) -> tuple[bytes, str]:
    d = _dir(pid)
    if kind == "picture" and (d / "reference.png").exists():
        return (d / "reference.png").read_bytes(), "image/png"
    if kind == "design" and (d / "design.extracted.json").exists():
        snap = Design.from_dict(json.loads((d / "design.extracted.json").read_text(encoding="utf-8")))
        return design_to_svg(snap).encode(), "image/svg+xml"
    raise NotFound()


def _carry_shape_settings(old: Design, new: Design, min_overlap: float = 0.5) -> None:
    """Re-extracting rebuilds every shape. Give each new shape the settings of the
    old customised shape covering the same place (overlap >= ``min_overlap`` of the
    union), compared at the same scale so a changed design width still matches."""
    from shapely import STRtree
    from shapely import affinity

    customised = [o for o in old.objects if o.customised]
    if not customised:
        return
    k = new.width_mm / old.width_mm if old.width_mm else 1.0

    def footprint(o):
        return o.geometry if o.kind == "area" else o.geometry.buffer(max(o.stroke_width_mm, 0.5) / 2)

    targets = [footprint(o) for o in new.objects]
    tree = STRtree(targets)
    best: dict[int, tuple[float, tuple]] = {}  # new object index -> (overlap, settings)
    kept = 0
    for o in customised:
        g = affinity.scale(footprint(o), k, k, origin=(0, 0))
        match = None
        for j in tree.query(g, predicate="intersects"):
            if new.objects[j].kind != o.kind:
                continue
            union = g.union(targets[j]).area
            iou = g.intersection(targets[j]).area / union if union else 0.0
            if iou >= min_overlap and (match is None or iou > match[0]):
                match = (iou, int(j))
        if match is None:
            continue
        kept += 1
        iou, j = match
        if j not in best or iou > best[j][0]:
            best[j] = (iou, o.settings())
    for j, (_, settings) in best.items():
        new.objects[j].apply_settings(settings)
    lost = len(customised) - kept
    msg = f"Your own settings were kept for {kept} of {len(customised)} customised shape(s)."
    if lost:
        msg += f" {lost} shape(s) changed too much to be recognised - please set them again."
    new.warnings.append(msg)


def design_payload(design: Design, pid: str | None = None) -> dict:
    params = StitchParams.from_dict(_meta(pid).get("stitch_params")) if pid else StitchParams()
    return {
        "design": design.summary(),
        "svg": design_to_svg(design, interactive=True),
        "objects": describe_objects(design, params),
        "can_undo": can_undo(pid) if pid else False,
        "can_redo": can_redo(pid) if pid else False,
        "reference": reference_info(pid) if pid else None,
    }


def update_layers(pid: str, layers: list[dict]) -> dict:
    design = load_design(pid)
    by_index = {int(l["index"]): l for l in layers}
    new_layers = []
    for layer in design.layers:
        if layer.index in by_index:
            merged = {**layer.to_dict(), **by_index[layer.index]}
            if "rgb" in by_index[layer.index] and "hex" not in by_index[layer.index]:
                merged.pop("hex", None)
            layer = ColorLayer.from_dict(merged)
        new_layers.append(layer)
    order = [int(l["index"]) for l in layers if int(l["index"]) in {x.index for x in new_layers}]
    if sorted(order) == sorted(x.index for x in new_layers):
        # the client may re-order layers (= colour sequence)
        pos = {idx: i for i, idx in enumerate(order)}
        new_layers.sort(key=lambda l: pos[l.index])
    design.layers = new_layers
    _save_design(pid, design)
    return design_payload(design, pid)


def merge_layers(pid: str, source: int, target: int) -> dict:
    design = load_design(pid)
    if source == target:
        raise ValueError("Pick two different colours to merge.")
    design.layer(source), design.layer(target)  # validate
    for obj in design.objects:
        if obj.layer == source:
            obj.layer = target
    design.layers = [l for l in design.layers if l.index != source]
    # fuse the merged shades into one region, so they stitch like a single solid colour
    design.normalize(layers=[target])
    _save_design(pid, design)
    return design_payload(design, pid)


_INHERIT = "inherit"


def _simplified_guide(points) -> list:
    """A drawn guide line, thinned to the points that matter (0.2 mm tolerance)."""
    from shapely.geometry import LineString  # noqa: PLC0415

    try:
        pts = [(float(x), float(y)) for x, y in points]
    except (TypeError, ValueError):
        return []
    if len(pts) < 2:
        return []
    line = LineString(pts)
    if line.length < 1.0:
        return []
    return [list(c) for c in line.simplify(0.2).coords]


def update_objects(
    pid: str,
    ids: list[str],
    scope: str = "selected",
    stitch_type: str | None = None,
    angle=None,
    enabled: bool | None = None,
    spacing_mm=None,
    pull_mm=None,
    bold=None,
    underlay_style=None,
    guide=None,
    blend_to=None,
) -> dict:
    """Set per-shape settings. ``None`` = leave unchanged, ``"inherit"`` = use the
    colour's / design's setting again. ``scope``: selected shapes, all shapes of their
    colours, or every shape in the design."""
    spacing_mm = _checked_mm(spacing_mm, SPACING_RANGE_MM, "Thickness")
    pull_mm = _checked_mm(pull_mm, PULL_RANGE_MM, "Extra width")
    if bold is not None and bold != _INHERIT and not isinstance(bold, bool):
        raise ValueError("Bold outline must be on, off or same as design.")
    if underlay_style is not None and underlay_style != _INHERIT and underlay_style not in UNDERLAY_STYLES:
        raise ValueError("Unknown foundation type.")
    if blend_to is not None and blend_to != _INHERIT:
        if not isinstance(blend_to, int) or blend_to not in {l.index for l in load_design(pid).layers}:
            raise ValueError("Choose one of the design's colours to blend into.")
    guide_line = None
    if guide is not None and guide != _INHERIT:
        guide_line = clean_guide(_simplified_guide(guide))
        if guide_line is None:
            raise ValueError("A guide line needs at least two points.")
    design = load_design(pid)
    if scope not in ("selected", "colour", "all"):
        raise ValueError("Unknown scope.")
    if stitch_type is not None and stitch_type != _INHERIT and stitch_type not in STITCH_TYPES:
        raise ValueError("Unknown stitch style.")
    if angle is not None and angle != _INHERIT:
        try:
            angle = normalize_angle(float(angle))
        except (TypeError, ValueError):
            raise ValueError("Direction must be a number of degrees.") from None
        if not math.isfinite(angle):
            raise ValueError("Direction must be a number of degrees.")
    by_id = {o.id: o for o in design.objects}
    chosen = [by_id[i] for i in ids if i in by_id]
    if scope == "all":
        chosen = list(design.objects)
    elif scope == "colour":
        layers = {o.layer for o in chosen}
        chosen = [o for o in design.objects if o.layer in layers]
    if not chosen:
        raise ValueError("Select at least one shape first.")
    for o in chosen:
        if stitch_type is not None:
            o.stitch_type = None if stitch_type == _INHERIT else stitch_type
        if angle is not None and o.kind == "area":  # lines follow their path: no direction
            o.angle = None if angle == _INHERIT else angle
        if enabled is not None:
            o.enabled = bool(enabled)
        # values are kept even where they do not apply right now (e.g. thickness on an
        # outline) and take effect again if the stitch style changes back
        if spacing_mm is not None:
            o.spacing_mm = None if spacing_mm == _INHERIT else spacing_mm
        if pull_mm is not None:
            o.pull_mm = None if pull_mm == _INHERIT else pull_mm
        if bold is not None:
            o.bold = None if bold == _INHERIT else bold
        if underlay_style is not None:
            o.underlay_style = None if underlay_style == _INHERIT else underlay_style
        if guide is not None and o.kind == "area":  # lines follow their own path
            o.guide = None if guide == _INHERIT else guide_line
        if blend_to is not None and o.kind == "area":
            o.blend_to = None if blend_to == _INHERIT or blend_to == o.layer else blend_to
    _save_design(pid, design)
    return design_payload(design, pid)


# --------------------------------------------------------------------------- colour + shape editing

def recolor_objects(pid: str, ids: list[str], target: int | None = None, hex_color: str | None = None) -> dict:
    """Move shapes to another thread colour: an existing one (``target``) or the colour
    ``hex_color`` (an existing colour with that value is reused, else a new one is added).
    Colours left without shapes are removed; the receiving colour is fused into regions."""
    from .colors import color_name
    from .models import hex_to_rgb

    design = load_design(pid)
    by_id = {o.id: o for o in design.objects}
    chosen = [by_id[i] for i in ids if i in by_id]
    if not chosen:
        raise ValueError("Select at least one shape first.")
    if target is not None:
        design.layer(int(target))  # validate
        dest = int(target)
    else:
        if not hex_color or not re.fullmatch(r"#?[0-9a-fA-F]{6}", hex_color):
            raise ValueError("Pick a thread colour.")
        rgb = hex_to_rgb(hex_color)
        found = next((l for l in design.layers if tuple(l.rgb) == tuple(rgb)), None)
        if found is None:
            src = design.layer(chosen[0].layer)
            found = ColorLayer(index=max(l.index for l in design.layers) + 1, rgb=rgb, name=color_name(rgb),
                               stitch_type=src.stitch_type, angle=src.angle)
            pos = design.layers.index(src) + 1  # sewn right after the colour the shapes came from
            design.layers.insert(pos, found)
        found.enabled = True
        dest = found.index
    for o in chosen:
        o.layer = dest
    moved = union_all([o.geometry for o in chosen])
    used = {o.layer for o in design.objects}
    design.layers = [l for l in design.layers if l.index in used]
    design.normalize(layers=[dest])
    _save_design(pid, design)
    return {**design_payload(design, pid), "selected": _covering(design, {dest}, moved)}


def _covering(design: Design, layers: set, geom) -> list[str]:
    """Ids of the shapes that now hold ``geom`` (after merging / splitting), so the
    editor keeps the same areas selected."""
    if geom is None or geom.is_empty:
        return []
    probe = geom.buffer(-0.02) if geom.area > 0.01 else geom
    probe = geom if probe.is_empty else probe
    return [o.id for o in design.objects if o.layer in layers and o.geometry.intersects(probe)]


EDIT_OPS = ("add", "erase", "grow", "shrink", "smooth")
MAX_EDIT_POINTS = 5000


def _clean_area(geom):
    """Valid polygons only, without tracing crumbs."""
    from shapely import make_valid
    from shapely.geometry import MultiPolygon

    parts = [p for p in iter_polygons(make_valid(geom)) if p.area >= MARK_MIN_MM2 / 2]
    if not parts:
        return None
    return parts[0] if len(parts) == 1 else MultiPolygon(parts)


def _edit_region(tool: str, points, size_mm: float):
    from shapely.geometry import LineString, Point, Polygon

    pts = []
    for p in points or []:
        try:
            x, y = float(p[0]), float(p[1])
        except (TypeError, ValueError, IndexError):
            raise ValueError("Invalid drawing points.") from None
        if not (math.isfinite(x) and math.isfinite(y)):
            raise ValueError("Invalid drawing points.")
        pts.append((x, y))
    if not pts or len(pts) > MAX_EDIT_POINTS:
        raise ValueError("Draw on the design to edit the shape.")
    if tool == "lasso":
        if len(pts) < 3:
            raise ValueError("Draw a closed outline around the area.")
        region = Polygon(pts).buffer(0)
    else:  # brush
        region = Point(pts[0]).buffer(size_mm / 2) if len(pts) == 1 else LineString(pts).buffer(size_mm / 2)
    if region.is_empty or region.area <= 0:
        raise ValueError("The drawn area is empty.")
    return region


def edit_shapes(pid: str, ids: list[str], op: str, tool: str = "brush", points=None, size_mm: float = 1.0) -> dict:
    """Change the outline of selected areas: paint / lasso area on (``add``) or off
    (``erase``), or ``grow`` / ``shrink`` / ``smooth`` them by ``size_mm``.
    Area added to a colour is removed from the other colours under it, so nothing is
    stitched twice."""
    if op not in EDIT_OPS:
        raise ValueError("Unknown shape edit.")
    if tool not in ("brush", "lasso"):
        raise ValueError("Unknown drawing tool.")
    try:
        size_mm = float(size_mm)
    except (TypeError, ValueError):
        raise ValueError("Brush size must be a number.") from None
    if not (0.05 <= size_mm <= 30):
        raise ValueError("Size must be between 0.05 and 30 mm.")
    design = load_design(pid)
    by_id = {o.id: o for o in design.objects}
    chosen = [by_id[i] for i in ids if i in by_id and by_id[i].kind == "area"]
    if not chosen:
        raise ValueError("Select a filled shape to edit.")
    touched = {o.layer for o in chosen}
    added = None
    if op == "add":
        region = _edit_region(tool, points, size_mm)
        target = max((o for o in chosen if o.layer == chosen[0].layer), key=lambda o: o.geometry.area)
        target.geometry = target.geometry.union(region)
        added = region
    elif op == "erase":
        region = _edit_region(tool, points, size_mm)
        for o in chosen:
            o.geometry = o.geometry.difference(region)
    else:
        for o in chosen:
            g = o.geometry
            if op == "grow":
                new = g.buffer(size_mm, join_style="round")
                added = new if added is None else added.union(new)
            elif op == "shrink":
                new = g.buffer(-size_mm, join_style="round")
            else:  # smooth: round off jaggies and notches smaller than size
                r = size_mm / 2
                new = g.buffer(r, join_style="round").buffer(-2 * r, join_style="round").buffer(r, join_style="round")
            o.geometry = new
    if added is not None:  # cover, don't stack: other colours give way under the new area
        mine = set(id(o) for o in chosen)
        for o in design.objects:
            if o.kind == "area" and id(o) not in mine and o.layer not in touched and o.geometry.intersects(added):
                o.geometry = o.geometry.difference(added)
                touched.add(o.layer)
    kept = []
    for o in design.objects:
        if o.kind == "area":
            cleaned = _clean_area(o.geometry)
            if cleaned is None:
                continue
            o.geometry = cleaned
        kept.append(o)
    if not any(o.kind == "area" or o.kind == "line" for o in kept):
        raise ValueError("That would remove the whole design.")
    design.objects = kept
    alive = {id(o) for o in kept}
    edited = union_all([o.geometry for o in chosen if id(o) in alive])
    chosen_layers = {o.layer for o in chosen}
    design.normalize(layers=touched)
    _save_design(pid, design)
    return {**design_payload(design, pid), "selected": _covering(design, chosen_layers, edited)}


def _checked_mm(value, bounds: tuple[float, float], label: str):
    """None (unchanged) / "inherit" / a number within ``bounds``."""
    if value is None or value == _INHERIT:
        return value
    try:
        v = float(value)
    except (TypeError, ValueError):
        raise ValueError(f"{label} must be a number of millimetres.") from None
    if not math.isfinite(v) or not bounds[0] <= v <= bounds[1]:
        raise ValueError(f"{label} must be between {bounds[0]:g} and {bounds[1]:g} mm.")
    return round(v, 3)


def reset_objects(pid: str, ids: list[str] | None = None) -> dict:
    """Back to the colour settings: the given shapes, or every shape when ``ids`` is empty."""
    design = load_design(pid)
    wanted = set(ids or [])
    for o in design.objects:
        if not wanted or o.id in wanted:
            o.clear_settings()
    _save_design(pid, design)
    return design_payload(design, pid)


def shape_outlines(design: Design) -> list[dict]:
    """Outlines (SVG path data, mm) of the stitched shapes - lets the stitch preview
    map a click to a shape."""
    enabled = {l.index for l in design.layers if l.enabled}
    order = {l.index: i for i, l in enumerate(design.layers)}
    return [
        {"id": o.id, "layer": o.layer, "order": order[o.layer], "kind": o.kind, "d": object_d(o),
         "width": o.stroke_width_mm}
        for o in design.objects
        if o.layer in enabled and o.enabled
    ]


def _plan_key(design: Design, params: StitchParams) -> str:
    raw = json.dumps([design.to_dict(), params.to_dict()], sort_keys=True)
    return hashlib.sha1(raw.encode()).hexdigest()


def get_plan(pid: str, params: StitchParams | None = None) -> StitchPlan:
    meta = _meta(pid)
    if params is None:
        params = StitchParams.from_dict(meta.get("stitch_params"))
    design = load_design(pid)
    key = _plan_key(design, params)
    with _lock:
        cached = _plan_cache.get(pid, {}).get(key)
        if cached is not None:
            return cached
    plan = plan_stitches(design, params)
    if not plan.ops:
        raise ValueError("Nothing to stitch - tick at least one thread colour.")
    with _lock:
        entry = _plan_cache.setdefault(pid, {})
        entry[key] = plan
        while len(entry) > 2:  # keep the latest original/optimised pair only
            entry.pop(next(iter(entry)))
    return plan


def _comparison(original: StitchPlan, optimized: StitchPlan) -> dict:
    """Before/after figures + end-to-end safety check of the visible stitching."""
    so, sn = original.stats(), optimized.stats()
    saved = so["stitches"] - sn["stitches"]
    micro = int(optimized.optimization.get("micro_removed", 0))
    return {
        "original": so,
        "optimized": sn,
        "saved_stitches": saved,
        "saved_pct": round(100 * saved / max(1, so["stitches"]), 1),
        "saved_minutes": round(so["est_minutes"] - sn["est_minutes"], 1),
        "micro_removed": micro,
        "underlay_saved": max(0, saved - micro),
        "coverage_change_pct": round(coverage_loss_pct(original.ops, optimized.ops), 3),
    }


def stitch(pid: str, params: dict) -> dict:
    p = StitchParams.from_dict(params)
    meta = _meta(pid)
    meta["stitch_params"] = p.to_dict()
    _save_meta(pid, meta)
    plan = get_plan(pid, p)
    comparison = None
    original = None
    if p.optimize:
        original = get_plan(pid, replace(p, optimize=False))
        comparison = _comparison(original, plan)
        if comparison["coverage_change_pct"] > MAX_COVERAGE_LOSS_PCT:
            # safety net: never ship an optimisation that visibly changes the design
            p = replace(p, optimize=False)
            meta["stitch_params"] = p.to_dict()
            _save_meta(pid, meta)
            plan, comparison = original, {**comparison, "reverted": True}
    preview = plan_preview_json(plan)
    design = load_design(pid)
    preview["shapes"] = shape_outlines(design)
    preview["objects"] = describe_objects(design, p)  # stitch style / direction per shape with these settings
    if comparison is not None:
        preview["comparison"] = comparison
        preview["original_ops"] = plan_preview_json(original)["ops"] if original is not plan else preview["ops"]
    stats = preview["stats"]
    warnings = []
    if stats["stitches"] > 100_000:
        warnings.append("Very high number of stitches - try a larger fill gap (thinner fill) or a smaller design size.")
    if stats["width_mm"] > 400 or stats["height_mm"] > 400:
        warnings.append("This design is larger than most hoops - check that it fits yours.")
    risky = [r for r in preview.get("risks", []) if r["type"] in ("short", "stack", "dense")]
    if risky:
        warnings.append(f"Machine check: {len(risky)} spot(s) where the thread may break - open the Machine check view to see them.")
    if any(r["type"] == "thin" for r in preview.get("risks", [])):
        warnings.append("Machine check: some lettering is thinner than the machine can sew cleanly - see the Machine check view "
                        "(fine 60 wt thread or a larger design helps).")
    if _dense_on_tiny_shapes(load_design(pid)):
        warnings.append("Some very small shapes are set to dense stitching - this can pile up thread and cause thread breaks.")
    preview["warnings"] = warnings
    return preview


def _dense_on_tiny_shapes(design: Design) -> bool:
    """Dense (< 0.35 mm) per-shape thickness on shapes under ~3 mm² or ~1.5 mm wide."""
    enabled = {l.index for l in design.layers if l.enabled}
    for o in design.objects:
        if o.spacing_mm is None or o.spacing_mm >= 0.35 or not o.enabled or o.layer not in enabled:
            continue
        if o.kind == "line":
            if o.stroke_width_mm < 1.5:
                return True
            continue
        area, perimeter = o.geometry.area, o.geometry.length
        if area < 3 or 2 * area / max(perimeter, 1e-6) < 1.5:
            return True
    return False


EXTRA_EXPORTS = ("svg", "png", "txt")  # besides the machine formats in FORMATS
ZIP_DEFAULT = ("dst", "exp", "pes", "svg", "png", "txt")  # "zip" without a choice of formats


def _export_file(pid: str, fmt: str, base: str, plan=None) -> tuple[bytes, str, str]:
    """One downloadable file: (data, filename, mime type). ``plan`` is built when needed."""
    if fmt == "svg":
        return design_to_svg(load_design(pid), only_enabled=True).encode(), f"{base}.svg", "image/svg+xml"
    plan = plan or get_plan(pid)
    if fmt in FORMATS:
        return export_embroidery(plan, fmt, base), f"{base}.{fmt}", "application/octet-stream"
    if fmt == "png":
        return render_png(plan), f"{base}_preview.png", "image/png"
    if fmt == "txt":
        return worksheet(plan, base).encode(), f"{base}_worksheet.txt", "text/plain"
    raise ValueError(f"Unknown export format '{fmt}'")


def parse_formats(value: str | None) -> list[str] | None:
    """``"dst, PES,dst"`` -> ``["dst", "pes"]`` (order kept, duplicates dropped);
    None when no choice was made. Unknown formats are an error."""
    if value is None:
        return None
    out: list[str] = []
    for part in value.split(","):
        f = part.strip().lower()
        if not f:
            continue
        if f not in FORMATS and f not in EXTRA_EXPORTS:
            raise ValueError(f"Unknown export format '{f}'")
        if f not in out:
            out.append(f)
    if not out:
        raise ValueError("Choose at least one file to download.")
    return out


def export(pid: str, fmt: str, formats: list[str] | None = None) -> tuple[bytes, str, str]:
    """A single file, or ``zip``: the chosen ``formats`` (default ``ZIP_DEFAULT``) in one archive."""
    meta = _meta(pid)
    base = Path(meta["filename"]).stem or "design"
    fmt = fmt.lower()
    if fmt != "zip":
        return _export_file(pid, fmt, base)
    chosen = formats or list(ZIP_DEFAULT)
    plan = get_plan(pid) if any(f != "svg" for f in chosen) else None
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for f in chosen:
            data, filename, _ = _export_file(pid, f, base, plan)
            z.writestr(filename, data)
    return buf.getvalue(), f"{base}_embroidery.zip", "application/zip"


# --------------------------------------------------------------------------- AI assistant
# ``ai`` is the request's AI block: {"provider", "api_key", "base_url", "edit_model",
# "vision_model"}. Keys are used for the single call only - never stored or logged.
# Without it (or with provider "builtin") the built-in algorithms are used.

def _raster_rgba(pid: str):
    meta = _meta(pid)
    if meta["kind"] != "raster":
        raise ValueError("This works on pictures only - artwork files are already clean and are used as they are.")
    return load_raster((_dir(pid) / "work.png").read_bytes()), meta


def ai_clean(pid: str, ai: dict | None, mode: str = "redraw", colors: int | None = None) -> dict:
    d = _dir(pid)
    _, meta = _raster_rgba(pid)
    src = next(d.glob("source.*"))
    rgba = load_raster(src.read_bytes())  # always clean up the original, not a previous AI result
    png, label, model = ai_tasks.clean_image(rgba, ai, mode, colors or 6)
    cleaned = load_raster(png)  # validates it is an image
    (d / "work.png").write_bytes(encode_png(cleaned))
    meta["ai_cleaned"] = {"provider": label, "model": model, "mode": mode}
    _save_meta(pid, meta)
    return meta


def ai_revert(pid: str) -> dict:
    d = _dir(pid)
    meta = _meta(pid)
    src = next(d.glob("source.*"))
    (d / "work.png").write_bytes(encode_png(load_raster(src.read_bytes())))
    meta["ai_cleaned"] = False
    _save_meta(pid, meta)
    return meta


def ai_suggest(pid: str, ai: dict | None, width_mm: float | None = None) -> dict:
    rgba, meta = _raster_rgba(pid)
    width = float(width_mm or (meta.get("extract_options") or {}).get("width_mm") or 100)
    return ai_tasks.suggest_settings(rgba, max(10.0, min(width, 500.0)), ai)


def ai_review(pid: str, ai: dict | None) -> dict:
    plan = get_plan(pid)
    design = load_design(pid)
    params = StitchParams.from_dict(_meta(pid).get("stitch_params"))
    preview = render_png(plan, px_per_mm=6.0)
    original = None
    if ai and ai.get("provider") not in (None, "", "builtin"):
        path, mime = original_file(pid)
        if mime == "image/png":
            rgba = load_raster(path.read_bytes())
            original = ai_tasks._png(rgba, 1024)
    return ai_tasks.review_stitches(plan, design, params, preview, original, ai)


def ai_models(ai: dict, refresh: bool = False) -> dict:
    return ai_tasks.list_models(ai, refresh)
