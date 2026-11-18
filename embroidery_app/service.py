"""Workflow orchestration + temporary on-disk project storage.

Projects are temporary working folders, deleted when no longer in use (see
temp_projects.py for when). Each project lives in ``data/projects/<id>/``:
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
import logging
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
from .ai.builtin import analyze_design, analyze_image, review_plan
from .config import settings
from .export import FORMATS, design_to_eps, design_to_svg, export_embroidery, plan_preview_json, render_png, worksheet
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
    rgb_to_hex,
)
from .stitch import plan_stitches
from .stitch.planner import _fill_like, describe_objects
from .stitch.optimize import MAX_COVERAGE_LOSS_PCT, coverage_loss_pct

PROJECTS = settings.data_dir / "projects"
PROJECTS.mkdir(parents=True, exist_ok=True)
_ID_RE = re.compile(r"^[a-f0-9]{12}$")
log = logging.getLogger("embroidery")
_plan_cache: dict[str, dict[str, StitchPlan]] = {}  # pid -> {params key: plan}; holds original + optimised
_lock = threading.Lock()
# One stitch / export at a time per project: the editor updates its preview automatically, so
# requests can overlap (a click during an update, two tabs). A second identical request then
# finds the plan in the cache instead of computing it again in parallel.
_project_locks: dict[str, threading.Lock] = {}


def _project_lock(pid: str) -> threading.Lock:
    with _lock:
        return _project_locks.setdefault(pid, threading.Lock())


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
        raise ValueError("Prepare the design first.")
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


def delete_project(pid: str) -> None:
    """Remove a project's files and its cached stitch plans (temp_projects.py decides when)."""
    with _lock:
        _plan_cache.pop(pid, None)
        _project_locks.pop(pid, None)
    if _ID_RE.match(pid or ""):
        shutil.rmtree(PROJECTS / pid, ignore_errors=True)


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
    obj.__post_init__()  # clamp to the allowed ranges, like StitchParams.from_dict
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
        # a turn since the last preparation (Size: Turn 90°): the old shapes are turned the same
        # way before they are matched, so each shape keeps its own settings
        turned = (getattr(opts, "rotate", 0) - int((meta.get("extract_options") or {}).get("rotate") or 0)) % 360
        _carry_shape_settings(load_design(pid), design, turned=turned)
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


def _carry_shape_settings(old: Design, new: Design, min_overlap: float = 0.5, turned: int = 0) -> None:
    """Re-extracting rebuilds every shape. Give each new shape the settings of the
    old customised shape covering the same place (overlap >= ``min_overlap`` of the
    union), compared at the same scale so a changed design width still matches.
    ``turned``: degrees clockwise the design was turned since (0, 90, 180, 270): old shapes,
    their own directions and guide lines are turned the same way first."""
    from shapely import STRtree
    from shapely import affinity

    customised = [o for o in old.objects if o.customised]
    if not customised:
        return
    to_new = _turn_map(old, new, turned)

    def footprint(o):
        return o.geometry if o.kind == "area" else o.geometry.buffer(max(o.stroke_width_mm, 0.5) / 2)

    targets = [footprint(o) for o in new.objects]
    tree = STRtree(targets)
    best: dict[int, tuple[float, tuple]] = {}  # new object index -> (overlap, settings)
    kept = 0
    for o in customised:
        g = to_new(footprint(o))  # the old shape where it lies in the new design
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
            best[j] = (iou, _turned_settings(o, to_new, turned))
    for j, (_, settings) in best.items():
        new.objects[j].apply_settings(settings)
    lost = len(customised) - kept
    msg = f"Your own settings were kept for {kept} of {len(customised)} customised shape(s)."
    if lost:
        msg += f" {lost} shape(s) changed too much to be recognised - please set them again."
    new.warnings.append(msg)


def _turn_map(old: Design, new: Design, turned: int):
    """Geometry in the old design -> the same place in the new one: turned ``turned`` degrees
    clockwise (y down), moved back to the top-left corner and scaled to the new width."""
    from shapely import affinity
    from shapely.geometry import box

    frame = affinity.rotate(box(0, 0, old.width_mm, old.height_mm), turned, origin=(0, 0)) if turned else None
    minx, miny = (frame.bounds[0], frame.bounds[1]) if frame is not None else (0.0, 0.0)
    old_w = (frame.bounds[2] - frame.bounds[0]) if frame is not None else old.width_mm
    k = new.width_mm / old_w if old_w else 1.0

    def to_new(g):
        if turned:
            g = affinity.translate(affinity.rotate(g, turned, origin=(0, 0)), -minx, -miny)
        return affinity.scale(g, k, k, origin=(0, 0)) if k != 1.0 else g
    return to_new


def _turned_settings(o, to_new, turned: int) -> tuple:
    """A shape's own settings in the new design: its direction turns with it, its guide line
    follows the shape (turn and size)."""
    from shapely.geometry import MultiPoint

    settings = list(o.settings())
    if turned and o.angle is not None:
        settings[1] = (o.angle + turned) % 180
    if o.guide:
        moved = to_new(MultiPoint([tuple(p) for p in o.guide]))
        settings[7] = [[round(p.x, 3), round(p.y, 3)] for p in moved.geoms]
    return tuple(settings)


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
    _merge_into(design, {source: target})
    _save_design(pid, design)
    return design_payload(design, pid)


def _merge_into(design: Design, into: dict[int, int]) -> None:
    """Every shape of colour ``s`` takes colour ``into[s]``; the merged colours disappear
    and each target's shades are fused into one region, so they stitch like a single
    solid colour. ``into`` must not chain (a target is never itself merged)."""
    for obj in design.objects:
        if obj.layer in into:
            obj.layer = into[obj.layer]
    design.layers = [l for l in design.layers if l.index not in into]
    design.normalize(layers=sorted(set(into.values())))


# Merge similar colours: CIEDE2000 below this suggests "these threads look alike".
# Around 2 is the smallest difference most people notice side by side; thread colours
# under ~5 are hard to tell apart on fabric. The editor's slider uses this range.
SIMILAR_RANGE = (1.0, 15.0)
SIMILAR_DEFAULT = 5.0


def _colour_weight(design: Design, index: int) -> float:
    """How much of the design a colour covers (mm²; a line counts its width)."""
    return sum(o.geometry.area if o.kind == "area" else o.geometry.length * (o.stroke_width_mm or 0)
               for o in design.objects if o.layer == index)


def similar_colours(pid: str, max_de=None) -> dict:
    """Suggested merges of colours that look alike (CIEDE2000 < ``max_de``).

    Only stitched colours are compared. The colour covering less merges into the larger
    one, so the main thread stays; every suggestion targets a colour that is kept (no
    chains), and each colour is merged at most once - the suggestions can all be applied
    together. Sorted by difference, closest first."""
    import numpy as np  # noqa: PLC0415

    from .colors import ciede2000, rgb_to_lab  # noqa: PLC0415
    from .models import bounded  # noqa: PLC0415

    limit = float(bounded(max_de, SIMILAR_RANGE, SIMILAR_DEFAULT))
    design = load_design(pid)
    layers = [l for l in design.layers if l.enabled]
    weight = {l.index: _colour_weight(design, l.index) for l in layers}
    order = {l.index: i for i, l in enumerate(design.layers)}
    # heaviest first (ties: the one sewn first) - lighter colours are merged into heavier ones
    layers.sort(key=lambda l: (-weight[l.index], order[l.index]))
    labs = rgb_to_lab(np.array([l.rgb for l in layers], dtype=np.uint8)) if layers else np.zeros((0, 3))
    kept: list[int] = []  # positions in `layers` that stay
    pairs = []
    for i, l in enumerate(layers):
        best = None
        for j in kept:
            d = float(ciede2000(labs[i], labs[j]))
            if d < limit and (best is None or d < best[0]):
                best = (d, j)
        if best is None:
            kept.append(i)
            continue
        t = layers[best[1]]
        pairs.append({"source": l.index, "target": t.index, "delta_e": round(best[0], 2),
                      "source_mm2": round(weight[l.index], 1), "target_mm2": round(weight[t.index], 1)})
    pairs.sort(key=lambda p: (p["delta_e"], order[p["source"]]))
    return {"max_de": limit, "pairs": pairs}


def merge_many(pid: str, pairs: list[dict]) -> dict:
    """Apply several merges as one change (one undo step). ``pairs``: [{source, target}].
    A target that is itself merged further is followed to its final colour; a colour
    merged twice keeps the first target."""
    design = load_design(pid)
    known = {l.index for l in design.layers}
    if not pairs:
        raise ValueError("Choose at least one pair of colours to merge.")
    into: dict[int, int] = {}
    for p in pairs:
        try:
            s, t = int(p["source"]), int(p["target"])
        except (KeyError, TypeError, ValueError):
            raise ValueError("Each merge needs a colour and the colour to merge it into.") from None
        if s not in known or t not in known:
            raise ValueError("One of the colours is no longer in the design - check the suggestions again.")
        if s == t or s in into:
            continue
        final = t
        while final in into:
            final = into[final]
        if final == s:  # would close a loop (a -> b, b -> a): the earlier merge wins
            continue
        into[s] = t
    resolved = {}
    for s, t in into.items():  # follow a -> b -> c to c (no loops, see above)
        while t in into:
            t = into[t]
        resolved[s] = t
    if not resolved:
        raise ValueError("Pick two different colours to merge.")
    _merge_into(design, resolved)
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
    map a click to a shape; ``bbox`` [x0, y0, x1, y1] mm (a line with its width) places
    the selection toolbar."""
    enabled = {l.index for l in design.layers if l.enabled}
    order = {l.index: i for i, l in enumerate(design.layers)}
    out = []
    for o in design.objects:
        if o.layer not in enabled or not o.enabled:
            continue
        bbox = None  # an empty geometry has NaN bounds (not valid JSON)
        if not o.geometry.is_empty:
            x0, y0, x1, y1 = o.geometry.bounds
            r = (o.stroke_width_mm or 0) / 2 if o.kind == "line" else 0
            bbox = [round(x0 - r, 2), round(y0 - r, 2), round(x1 + r, 2), round(y1 + r, 2)]
        out.append({"id": o.id, "layer": o.layer, "order": order[o.layer], "kind": o.kind, "d": object_d(o),
                    "width": o.stroke_width_mm, "bbox": bbox})
    return out


# settings that change only the figures, not the stitches: a cached plan is reused
_FIGURES_ONLY = ("machine_spm", "thread_chart", "garment_hex")


def _plan_key(design: Design, params: StitchParams) -> str:
    p = {k: v for k, v in params.to_dict().items() if k not in _FIGURES_ONLY}
    raw = json.dumps([design.to_dict(), p], sort_keys=True)
    return hashlib.sha1(raw.encode()).hexdigest()


PLAN_CACHE_SIZE = 6  # plans kept per open design (memory: roughly 1 MB per 10,000 stitches each)


def get_plan(pid: str, params: StitchParams | None = None) -> StitchPlan:
    meta = _meta(pid)
    if params is None:
        params = StitchParams.from_dict(meta.get("stitch_params"))
    design = load_design(pid)
    key = _plan_key(design, params)
    with _lock:
        cached = _plan_cache.get(pid, {}).get(key)
        if cached is not None:
            cached.machine_spm = params.machine_spm  # the figures follow the latest machine speed
            cached.thread_chart = params.thread_chart  # ... and thread chart
            return cached
    plan = plan_stitches(design, params)
    if not plan.ops:
        raise ValueError("Nothing to stitch - tick at least one thread colour.")
    with _lock:
        entry = _plan_cache.setdefault(pid, {})
        entry[key] = plan
        # the latest three original/optimised pairs: the editor updates the preview while
        # settings change, and going back to a setting tried a moment ago is then instant
        while len(entry) > PLAN_CACHE_SIZE:
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
    with _project_lock(pid):
        return _stitch(pid, params)


def _stitch(pid: str, params: dict) -> dict:
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
        if not comparison.get("reverted"):
            # the unreduced stitches are fetched only when the user compares (original_ops):
            # this halves the preview; the ref ties that request to exactly these stitches
            preview["original_ref"] = _original_ref(design, p)
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
    dense_tiny = _dense_tiny_ids(design)
    if dense_tiny:
        warnings.append("Some very small shapes are set to dense stitching - this can pile up thread and cause thread breaks.")
    preview["warnings"] = warnings
    preview["checks"] = _stitch_checks(plan, design, p, stats, preview.get("risks", []), warnings, dense_tiny, preview["objects"])
    return preview


# The editor's Checks: the warnings above, each with a code (for its help text and actions)
# and what the editor can do about it, plus the built-in review's important findings with a
# one-click setting change. "warnings" stays as plain text for older readers.
#   actions: "show" = Machine check view at the spots of ``spots`` (risk types), "size" = the
#   design size, "select" = select ``ids``; "change" + "fix" = a setting change and its label.
#   Further one-click fixes, each a button of its own: "resize" = {width_mm} (a design width,
#   offered within the chosen hoop), "merge" = {pairs, label} (colours to merge, as the
#   Merge similar colours dialog does), "order" = {layers, label} (a new colour sequence),
#   "skip" = {layers, label} (colours to switch off: the garment's own colour).
REVIEW_IN_CHECKS = ("high", "medium")  # low findings stay in "Check my stitches"
_CHECK_ACTIONS = {
    "stitch_count": ["size"], "oversize": ["size"], "cap_height": ["size"],
    "thread_break": ["show"], "thin_lettering": ["show"],
    "dense_tiny": ["select"], "skipped_marks": ["select"], "guide_missing": ["select"],
}
_CHECK_SPOTS = {"thread_break": ["short", "stack", "dense"], "thin_lettering": ["thin"]}


def _stitch_checks(plan: StitchPlan, design: Design, params: StitchParams, stats: dict, risks: list,
                   warnings: list[str], dense_tiny: list[str], objects: list[dict] | None = None) -> list[dict]:
    codes = []  # the code of each warning, in the same order
    if stats["stitches"] > 100_000:
        codes.append(("stitch_count", "high"))
    if stats["width_mm"] > 400 or stats["height_mm"] > 400:
        codes.append(("oversize", "high"))
    if any(r["type"] in ("short", "stack", "dense") for r in risks):
        codes.append(("thread_break", "high"))
    if any(r["type"] == "thin" for r in risks):
        codes.append(("thin_lettering", "medium"))
    if dense_tiny:
        codes.append(("dense_tiny", "medium"))
    checks = [{"code": c, "severity": sev, "text": w} for (c, sev), w in zip(codes, warnings)]
    if dense_tiny and checks:
        checks[-1]["ids"] = dense_tiny
    by_code = {c["code"]: c for c in checks}
    try:
        review = review_plan(plan, design, params)
    except Exception:  # the review is advice: the stitches are shown without it
        log.exception("built-in review failed")
        review = {"issues": []}
    for issue in review["issues"]:
        code = issue.get("code") or ""
        if issue["severity"] not in REVIEW_IN_CHECKS and not (code in by_code and issue.get("change")):
            continue
        own = by_code.get(code)
        if own is None:  # a finding of its own
            own = {"code": code, "severity": issue["severity"], "title": issue["title"], "text": issue["detail"]}
            if issue.get("ids"):
                own["ids"] = issue["ids"]
            checks.append(own)
            if code:
                by_code[code] = own
        if issue.get("change"):  # the same problem found twice: its fixes go together
            own["change"] = {**own.get("change", {}), **issue["change"]}
            own["fix"] = issue["fix"] if "fix" not in own else f"{own['fix']} · {issue['fix']}"
        if issue.get("resize") and "resize" not in own:
            own["resize"] = issue["resize"]
    for extra in (_garment_check, _same_thread_check, _colour_order_check):
        try:
            found = extra(design, params, objects)
        except Exception:  # advice only: the stitches are shown without it
            log.exception("%s failed", extra.__name__)
            found = None
        if found:
            checks.append(found)
    for c in checks:
        c["actions"] = [a for a in _CHECK_ACTIONS.get(c["code"], []) if a != "select" or c.get("ids")]
        if c["code"] in _CHECK_SPOTS:
            c["spots"] = _CHECK_SPOTS[c["code"]]
    return checks


def _sewn_layers(design: Design):
    """Enabled colours that have something to sew, in sewing order."""
    used = {o.layer for o in design.objects if o.enabled}
    return [l for l in design.layers if l.enabled and l.index in used]


def _same_thread_check(design: Design, params: StitchParams, objects=None) -> dict | None:
    """Colours that match the same thread of the chosen brand (threads.py) sew as one thread
    with an extra colour change: offer to merge each into the heavier one (merge_many)."""
    from . import threads  # noqa: PLC0415

    layers = _sewn_layers(design)
    found = threads.matches(params.thread_chart, [l.rgb for l in layers]) if len(layers) > 1 else None
    if not found:
        return None
    groups: dict[tuple, list] = {}
    for pos, (layer, m) in enumerate(zip(layers, found)):
        groups.setdefault((m["number"], m["name"]), []).append((pos, layer, m))
    pairs, names = [], []
    for group in groups.values():
        if len(group) < 2:
            continue
        group.sort(key=lambda g: (-_colour_weight(design, g[1].index), g[0]))  # heaviest stays (ties: sewn first)
        keep = group[0][1]
        thread = f"{group[0][2]['brand']} {group[0][2]['number']}"
        for _, layer, _m in group[1:]:
            pairs.append({"source": layer.index, "target": keep.index})
        names.append(f"{' and '.join(g[1].name for g in group)} ({thread})")
    if not pairs:
        return None
    n = len(pairs)
    changes = "an extra colour change" if n == 1 else f"{n} extra colour changes"
    text = f"{'; '.join(names)} match the same thread, so they sew as one thread with {changes}."
    return {"code": "same_thread", "severity": "medium", "title": "Same thread twice", "text": text,
            "merge": {"pairs": pairs, "label": "Merge them" if n == 1 else f"Merge {n} colours"}}


GARMENT_DE = 5.0  # CIEDE2000: a colour this close to the garment does not show on it


def _garment_check(design: Design, params: StitchParams, objects=None) -> dict | None:
    """Colours (almost) the same as the garment (StitchParams.garment_hex) are sewn where
    nobody sees them: offer not to sew them. Never every colour (then nothing is left)."""
    if not params.garment_hex:
        return None
    from .colors import ciede2000, rgb_to_lab  # noqa: PLC0415

    import numpy as np  # noqa: PLC0415

    layers = _sewn_layers(design)
    if len(layers) < 2:
        return None
    garment = rgb_to_lab(np.array([int(params.garment_hex[i:i + 2], 16) for i in (1, 3, 5)], np.uint8))
    same = [l for l in layers if float(ciede2000(rgb_to_lab(np.array(l.rgb, np.uint8)), garment)) < GARMENT_DE]
    if not same or len(same) == len(layers):
        return None
    names = ", ".join(l.name for l in same)
    area = sum(_colour_weight(design, l.index) for l in same)
    text = (f"{names} {'is' if len(same) == 1 else 'are'} the colour of the garment: about {area:.0f} mm² of stitches "
            "that will not show. Leave them out, unless they are there on purpose (e.g. to cover the fabric's texture).")
    label = f"Don't sew {same[0].name}" if len(same) == 1 else f"Don't sew these {len(same)} colours"
    return {"code": "garment", "severity": "medium", "title": "Same colour as the garment", "text": text,
            "skip": {"layers": [l.index for l in same], "label": label}}


# Methods that sew outlines and lettering (thin, on top) and fills (wide areas under them).
_DETAIL_METHODS = frozenset({"satin", "satin_band", "satin_line", "column", "stroke", "dot", "running", "outline"})
ORDER_TOUCH_MM = 0.5  # a detail this close to a fill borders it
ORDER_MIN_FILL_MM2 = 20.0  # smaller fills are details themselves


def _colour_order_check(design: Design, params: StitchParams, objects=None) -> dict | None:
    """An outline or lettering colour sewn before a fill it borders: the fill's edge (and its
    shrink compensation) then pushes over it. Digitizers sew outlines and lettering last;
    offer the sequence with each such colour moved to just after the last fill it borders."""
    if not objects or params.photo_stitch:
        return None
    layers = _sewn_layers(design)
    if len(layers) < 2:
        return None
    shapes = {o.id: o for o in design.objects}
    by_layer: dict[int, list] = {}
    for d in objects:
        if d.get("enabled") and d.get("method") not in (None, "none") and d.get("id") in shapes:
            by_layer.setdefault(d["layer"], []).append(d)
    detail = [l.index for l in layers if by_layer.get(l.index) and all(d["method"] in _DETAIL_METHODS for d in by_layer[l.index])]
    fills = {l.index: [shapes[d["id"]].geometry for d in by_layer.get(l.index, []) if _fill_like(d["method"])] for l in layers}
    fills = {k: g for k, g in fills.items() if g and sum(x.area for x in g) >= ORDER_MIN_FILL_MM2}
    sequence = [l.index for l in design.layers]
    if not any(sequence.index(f) > sequence.index(d) for d in detail for f in fills):
        return None  # nothing to look at (the usual case): no geometry work

    import shapely  # noqa: PLC0415

    def body(index):
        parts = [shapes[d["id"]] for d in by_layer[index]]
        return union_all([o.geometry.buffer(max(o.stroke_width_mm, 0.1) / 2) if o.kind == "line" else o.geometry for o in parts])

    fill_body = {k: union_all(v) for k, v in fills.items()}
    moves = []
    for d in reversed(detail):  # from the last: moved colours keep their order among themselves
        at = sequence.index(d)
        later = [f for f in fill_body if sequence.index(f) > at]
        if not later:
            continue
        geom = body(d)
        touching = [f for f in later if shapely.dwithin(geom, fill_body[f], ORDER_TOUCH_MM)]
        if not touching:
            continue
        last = max(touching, key=sequence.index)
        sequence.remove(d)
        sequence.insert(sequence.index(last) + 1, d)
        moves.append((d, last))
    if not moves:
        return None
    name = {l.index: l.name for l in design.layers}
    moves.reverse()
    if len(moves) == 1:
        d, f = moves[0]
        text = (f"{name[d]} (an outline or lettering colour) is sewn before {name[f]}, which it borders: the fill "
                "then pushes over its edge. Outlines and lettering usually go last.")
        label = f"Sew {name[d]} after {name[f]}"
    else:
        text = (f"{len(moves)} outline or lettering colours ({', '.join(name[d] for d, _ in moves)}) are sewn before "
                "fills they border: the fills then push over their edges. Outlines and lettering usually go last.")
        label = "Sew outlines last"
    return {"code": "colour_order", "severity": "medium", "title": "Outline sewn before its fill", "text": text,
            "order": {"layers": sequence, "label": label}}


def _original_ref(design: Design, params: StitchParams) -> str:
    return _plan_key(design, replace(params, optimize=False))[:20]


def original_ops(pid: str, ref: str) -> dict:
    """The unreduced stitches of the last stitch run (Optimised / Original in the editor), as
    preview ops. ``ref`` comes with that run; a newer run makes it stale (LookupError)."""
    with _project_lock(pid):
        params = StitchParams.from_dict(_meta(pid).get("stitch_params"))
        design = load_design(pid)
        if not params.optimize or not ref or _original_ref(design, params) != ref:
            raise LookupError("The stitches have changed since then. Compare again.")
        return {"ops": plan_preview_json(get_plan(pid, replace(params, optimize=False)))["ops"]}


def _dense_tiny_ids(design: Design) -> list[str]:
    """Shapes with dense (< 0.35 mm) own thickness that are under ~3 mm² or ~1.5 mm wide."""
    enabled = {l.index for l in design.layers if l.enabled}
    out = []
    for o in design.objects:
        if o.spacing_mm is None or o.spacing_mm >= 0.35 or not o.enabled or o.layer not in enabled:
            continue
        if o.kind == "line":
            if o.stroke_width_mm < 1.5:
                out.append(o.id)
            continue
        area, perimeter = o.geometry.area, o.geometry.length
        if area < 3 or 2 * area / max(perimeter, 1e-6) < 1.5:
            out.append(o.id)
    return out


def _dense_on_tiny_shapes(design: Design) -> bool:
    """Dense (< 0.35 mm) per-shape thickness on shapes under ~3 mm² or ~1.5 mm wide."""
    return bool(_dense_tiny_ids(design))


def saved_stitch_params(pid: str) -> StitchParams:
    """The stitch settings the design is exported with (the last stitch run)."""
    return StitchParams.from_dict(_meta(pid).get("stitch_params"))


EXTRA_EXPORTS = ("svg", "png", "txt")  # besides the machine formats in FORMATS
ZIP_DEFAULT = ("dst", "exp", "pes", "svg", "png", "txt")  # "zip" without a choice of formats


def _export_file(pid: str, fmt: str, base: str, plan=None, quote: dict | None = None) -> tuple[bytes, str, str]:
    """One downloadable file: (data, filename, mime type). ``plan`` is built when needed;
    ``quote``: the account's price settings for the worksheet (prefs.quote)."""
    if fmt == "svg":
        return design_to_svg(load_design(pid), only_enabled=True).encode(), f"{base}.svg", "image/svg+xml"
    plan = plan or get_plan(pid)
    if fmt in FORMATS:
        return export_embroidery(plan, fmt, base), f"{base}.{fmt}", "application/octet-stream"
    if fmt == "png":
        return render_png(plan), f"{base}_preview.png", "image/png"
    if fmt == "txt":
        return worksheet(plan, base, quote).encode(), f"{base}_worksheet.txt", "text/plain"
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


def export(pid: str, fmt: str, formats: list[str] | None = None, quote: dict | None = None) -> tuple[bytes, str, str]:
    """A single file, or ``zip``: the chosen ``formats`` (default ``ZIP_DEFAULT``) in one archive.
    Waits for a stitch run of the same project, so the file has the settings just stitched.
    ``quote``: the account's price settings; the worksheet then has a price estimate."""
    with _project_lock(pid):
        return _export(pid, fmt, formats, quote)


def _export(pid: str, fmt: str, formats: list[str] | None = None, quote: dict | None = None) -> tuple[bytes, str, str]:
    meta = _meta(pid)
    base = Path(meta["filename"]).stem or "design"
    fmt = fmt.lower()
    if fmt == WILCOM_PACKAGE:
        return wilcom_package(pid, base, quote), f"{base}_for_wilcom.zip", "application/zip"
    if fmt != "zip":
        return _export_file(pid, fmt, base, quote=quote)
    chosen = formats or list(ZIP_DEFAULT)
    plan = get_plan(pid) if any(f != "svg" for f in chosen) else None
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for f in chosen:
            data, filename, _ = _export_file(pid, f, base, plan, quote)
            z.writestr(filename, data)
    return buf.getvalue(), f"{base}_embroidery.zip", "application/zip"


# --------------------------------------------------------------------------- Wilcom package
# EmbroideryStudio's own editable format (EMB) is closed: no other software can write it.
# What Wilcom can use is documented: it opens machine files and rebuilds satin / fill
# objects from the stitches ("object recognition", on by default), and it imports EPS
# artwork that its Convert / Auto-Digitize tools turn into fully editable objects. The
# package carries both, plus the thread list DST cannot hold. It never claims to be EMB.

WILCOM_PACKAGE = "wilcom"
_SAFE_NAME = re.compile(r"[^A-Za-z0-9_-]+")


def _safe(text: str, fallback: str) -> str:
    return _SAFE_NAME.sub("_", str(text)).strip("_")[:40] or fallback


def _wilcom_readme(design: Design, plan: StitchPlan, base: str, colour_files: list[tuple[str, str]]) -> str:
    s = plan.stats()
    lines = [
        f"{base} - files for continuing in Wilcom EmbroideryStudio",
        "",
        "This is not a Wilcom .EMB project: that format is Wilcom's own and cannot be written by other",
        "software. These files let you open the stitches in Wilcom and rebuild editable objects there.",
        "",
        f"1. {base}.dst - the stitches exactly as created ({s['stitches']:,} stitches, {s['width_mm']} x {s['height_mm']} mm).",
        "   Open it in EmbroideryStudio. With object recognition switched on (Wilcom's default for machine",
        "   files), Wilcom rebuilds satin and fill objects from the stitches so they can be reshaped, scaled",
        "   and given another density. Parts it does not recognise stay as stitch blocks: scale those only",
        "   a little. DST files carry no thread colours - set them from the thread list below.",
        "",
        f"2. {base}_artwork.eps - the clean shapes in their thread colours, at real size (also as .svg).",
        "   For fully editable objects, import it as a graphic and convert the shapes to embroidery",
        "   (Convert or Auto-Digitize). The folder artwork_by_colour has one file per thread, named with",
        "   the sewing order, the stitch style and the direction used here.",
        "",
        f"3. {base}_worksheet.txt - thread order, stitch count, time and machine notes.",
        "",
        "Thread list (sewing order):",
    ]
    for i, block in enumerate(plan.blocks, 1):
        lines.append(f"  {i:>2}. {block.name:<14} {rgb_to_hex(block.rgb)}")
    lines += ["", "Artwork files:"] + [f"  {name}  ({what})" for name, what in colour_files]
    return "\n".join(lines) + "\n"


def wilcom_package(pid: str, base: str, quote: dict | None = None) -> bytes:
    """A ZIP for continuing in Wilcom: DST + EPS / SVG artwork (all colours and one file per
    colour) + worksheet + a short how-to. Counts as stitch output (it contains the DST)."""
    design = load_design(pid)
    plan = get_plan(pid)
    colour_files: list[tuple[str, str]] = []
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr(f"{base}.dst", export_embroidery(plan, "dst", base))
        z.writestr(f"{base}_artwork.eps", design_to_eps(design, title=base))
        z.writestr(f"{base}_artwork.svg", design_to_svg(design, only_enabled=True))
        z.writestr(f"{base}_worksheet.txt", worksheet(plan, base, quote))
        order = 0
        for layer in design.layers:
            if not layer.enabled or not any(o.layer == layer.index and o.enabled for o in design.objects):
                continue
            order += 1
            style = layer.stitch_type or "auto"
            angle = "auto" if layer.angle is None else f"{round(layer.angle)}deg"
            name = f"artwork_by_colour/{order:02d}_{_safe(layer.name, 'colour')}_{_safe(style, 'auto')}_{angle}.eps"
            z.writestr(name, design_to_eps(design, layers=[layer.index], title=f"{base} {layer.name}"))
            colour_files.append((name, f"{layer.name} {layer.hex}, stitch style {style}, direction {angle}"))
        z.writestr("Open in Wilcom - read me.txt", _wilcom_readme(design, plan, base, colour_files))
    return buf.getvalue()


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


def _analysis_width(meta: dict, width_mm: float | None) -> float:
    width = float(width_mm or (meta.get("extract_options") or {}).get("width_mm") or 100)
    return max(10.0, min(width, 500.0))


def ai_suggest(pid: str, ai: dict | None, width_mm: float | None = None) -> dict:
    rgba, meta = _raster_rgba(pid)
    return ai_tasks.suggest_settings(rgba, _analysis_width(meta, width_mm), ai)


def analysis(pid: str, width_mm: float | None = None) -> dict:
    """The built-in analysis only (no AI, so no AI quota or rate), for pictures and artwork
    files (SVG, AI, PDF: their shapes are measured, ai/builtin.analyze_design). The editor
    uses it for artwork after the upload and for any design after a width change, so what
    it says about lettering and the suggested preset stay true."""
    meta = _meta(pid)
    if meta["kind"] != "raster":
        return _analyze_artwork(pid, _analysis_width(meta, width_mm))
    rgba, meta = _raster_rgba(pid)
    return analyze_image(rgba, _analysis_width(meta, width_mm))


def _analyze_artwork(pid: str, width_mm: float) -> dict:
    design = svg_to_design((_dir(pid) / "vector.svg").read_text(encoding="utf-8"), _options(VectorOptions, {"width_mm": width_mm}))
    design.normalize()
    if not design.objects:
        raise ValueError("Nothing to stitch was found in this artwork.")
    return analyze_design(design)


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
