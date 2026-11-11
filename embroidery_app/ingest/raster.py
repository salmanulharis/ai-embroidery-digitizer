"""Raster image -> embroidery Design.

Pipeline (deterministic, no AI required):
  1. load (EXIF orientation, alpha), resample to a working resolution tied to the
     final size in mm, so "detail" settings are physical (mm) not pixels
  2. edge-preserving denoise (flattens JPEG noise / paper texture)
  3. background removal: alpha channel, or border-colour flood (logos), or rembg
  4. colour reduction: k-means in CIELAB + perceptual merge of near-duplicates;
     anti-aliased edge shades are split per pixel between their two neighbour colours
  5. label-map smoothing (blurred one-hot argmax) -> smooth, gap-free region borders
  6. speck removal: ragged/faint fragments smaller than the minimum area are
     re-assigned to their neighbours; solid small marks (dots, hyphens, full stops,
     letter holes) are kept - they carry meaning, especially in lettering
  7. contour tracing -> shapely polygons (with holes) in mm
"""

from __future__ import annotations

import io
from dataclasses import dataclass
from typing import Optional

import cv2
import numpy as np
from PIL import Image, ImageOps
from shapely import affinity

from ..colors import color_name, delta_e, lab_to_rgb, merge_palette, rgb_to_lab
from ..config import settings
from ..geometry import mask_to_polygons
from ..models import ColorLayer, Design, DesignObject

BG = -1

# Small marks smaller than "remove specks" are still kept when they are real marks:
MARK_MIN_MM2 = 0.04  # never keep anything smaller (~0.2 x 0.2 mm) - that is noise
MARK_SOLID_DE = 12.0  # pixel counts as "solid" (full colour) when this close (ΔE) to its colour
# A real mark has a solid core at full colour; faint noise blotches never reach it.
# Thin strokes are mostly anti-aliased edge pixels, so the core can be a small share.
MARK_MIN_SOLID = 0.2
MARK_MIN_FILL = 0.3  # area / bounding box: dots and dashes are compact, slivers are not


@dataclass
class RasterOptions:
    width_mm: float = 100.0
    num_colors: Optional[int] = None  # None = automatic
    max_auto_colors: int = 10
    background: str = "auto"  # auto | none | color | ai
    bg_tolerance: float = 14.0  # ΔE
    min_feature_mm: float = 0.3  # details thinner than this are smoothed away
    min_area_mm2: float = 1.0  # ragged/faint specks below this are removed (solid marks are kept)
    denoise: int = 2  # 0..3 picture grain clean-up
    # manual picture adjustments (applied before colours are found)
    brightness: int = 0  # -100..100
    contrast: int = 0  # -100..100
    sharpen: int = 0  # 0..3 - crisper letter edges in soft / blurry pictures


def load_raster(data: bytes) -> np.ndarray:
    img = Image.open(io.BytesIO(data))
    img = ImageOps.exif_transpose(img)
    return np.array(img.convert("RGBA"))


def encode_png(rgba: np.ndarray) -> bytes:
    buf = io.BytesIO()
    Image.fromarray(rgba).save(buf, format="PNG")
    return buf.getvalue()


def _resample(rgba: np.ndarray, width_mm: float) -> tuple[np.ndarray, float]:
    h, w = rgba.shape[:2]
    target_w = width_mm * settings.raster_px_per_mm
    scale = target_w / w
    if max(h, w) * scale > settings.raster_max_side_px:
        scale = settings.raster_max_side_px / max(h, w)
    new_w, new_h = max(8, round(w * scale)), max(8, round(h * scale))
    interp = cv2.INTER_AREA if scale < 1 else cv2.INTER_CUBIC
    out = cv2.resize(rgba, (new_w, new_h), interpolation=interp)
    return out, new_w / width_mm  # px per mm


def _adjust(rgb: np.ndarray, brightness: int, contrast: int, sharpen: int) -> np.ndarray:
    """Manual picture adjustments. Contrast pivots on mid-grey; sharpening is an unsharp
    mask whose radius stays below a thin stroke, so it crisps letter edges without halos."""
    b = int(np.clip(brightness, -100, 100))
    c = int(np.clip(contrast, -100, 100))
    k = int(np.clip(sharpen, 0, 3))
    out = rgb
    if b or c:
        gain = (100 + c) / 100 if c <= 0 else 100 / max(1, 100 - c * 0.9)
        lut = np.clip((np.arange(256, dtype=np.float32) - 128) * gain + 128 + b * 1.28, 0, 255).astype(np.uint8)
        out = cv2.LUT(out, lut)
    if k:
        blur = cv2.GaussianBlur(out, (0, 0), 0.8 + 0.4 * k)
        out = cv2.addWeighted(out, 1 + 0.6 * k, blur, -0.6 * k, 0)
    return np.ascontiguousarray(out)


def _denoise(rgb: np.ndarray, level: int) -> np.ndarray:
    """Picture grain clean-up (JPEG blotches, scan/photo noise) before colours are found.

    Edge-preserving bilateral passes keep letter edges sharp. The strongest level adds
    a 3x3 median for salt-and-pepper noise; a 5x5 median used to erase strokes thinner
    than ~3 px, which broke small lettering."""
    out = rgb
    for _ in range(level):
        out = cv2.bilateralFilter(out, 9, 35, 7)
    if level >= 3:
        out = cv2.medianBlur(out, 3)
    return out


def _border_background(
    lab: np.ndarray, valid: np.ndarray, tol: float, connected_only: bool
) -> tuple[np.ndarray, Optional[np.ndarray]]:
    """Mask of background pixels from the dominant border colour, plus that colour (LAB)."""
    h, w = valid.shape
    border = np.zeros((h, w), bool)
    b = max(2, min(h, w) // 100)
    border[:b, :], border[-b:, :], border[:, :b], border[:, -b:] = True, True, True, True
    border &= valid
    if border.sum() < 10:
        return np.zeros((h, w), bool), None
    samples = lab[border]
    bg_lab = np.median(samples, axis=0)
    share = float(np.mean(delta_e(samples, bg_lab) < tol))
    if share < 0.5:  # no uniform background (e.g. photo, full-bleed artwork)
        return np.zeros((h, w), bool), None
    candidate = (delta_e(lab, bg_lab) < tol) & valid
    if not connected_only:
        return candidate, bg_lab
    n, labels = cv2.connectedComponents(candidate.astype(np.uint8), connectivity=4)
    touching = np.unique(labels[border & candidate])
    touching = touching[touching != 0]
    return np.isin(labels, touching), bg_lab


def _ai_background(rgb: np.ndarray) -> np.ndarray:
    try:
        from rembg import remove  # type: ignore
    except ImportError as exc:  # pragma: no cover - optional dependency
        raise ValueError("AI background removal needs the optional 'rembg' package: pip install rembg") from exc
    out = np.array(remove(Image.fromarray(rgb)).convert("RGBA"))
    return out[:, :, 3] < 128


def _kmeans_lab(samples: np.ndarray, k: int) -> np.ndarray:
    k = max(1, min(k, len(samples)))
    criteria = (cv2.TERM_CRITERIA_EPS + cv2.TERM_CRITERIA_MAX_ITER, 40, 0.2)
    cv2.setRNGSeed(7)
    _, _, centers = cv2.kmeans(samples.astype(np.float32), k, None, criteria, 3, cv2.KMEANS_PP_CENTERS)
    return centers


def _assign(lab_pixels: np.ndarray, centers: np.ndarray) -> np.ndarray:
    out = np.empty(len(lab_pixels), dtype=np.int32)
    chunk = 400_000
    for s in range(0, len(lab_pixels), chunk):
        d = ((lab_pixels[s : s + chunk, None, :] - centers[None, :, :]) ** 2).sum(-1)
        out[s : s + chunk] = d.argmin(1)
    return out


def _quantize(lab: np.ndarray, fg: np.ndarray, opts: RasterOptions) -> tuple[np.ndarray, np.ndarray]:
    """Returns (label map with BG=-1, LAB centers)."""
    pixels = lab[fg]
    if len(pixels) == 0:
        raise ValueError("Nothing left after background removal - try background mode 'none'.")
    rng = np.random.default_rng(7)
    samples = pixels if len(pixels) <= 150_000 else pixels[rng.choice(len(pixels), 150_000, replace=False)]

    if opts.num_colors:
        centers = _kmeans_lab(samples, opts.num_colors)
    else:
        centers = _kmeans_lab(samples, min(16, opts.max_auto_colors + 6))
        weights = np.bincount(_assign(samples, centers), minlength=len(centers)).astype(float)
        groups = merge_palette(centers, weights, threshold=16.0)
        merged = np.array(
            [np.average(centers[groups == g], axis=0, weights=weights[groups == g] + 1e-9) for g in range(groups.max() + 1)]
        )
        if len(merged) > opts.max_auto_colors:
            merged = _kmeans_lab(samples, opts.max_auto_colors)
        centers = merged.astype(np.float32)

    # drop colours covering < 0.15% of the design and re-assign their pixels
    for _ in range(3):
        counts = np.bincount(_assign(samples, centers), minlength=len(centers))
        keep = counts >= max(1, 0.0015 * len(samples))
        if keep.all() or keep.sum() == 0:
            break
        centers = centers[keep]

    labels = np.full(fg.shape, BG, dtype=np.int32)
    labels[fg] = _assign(pixels, centers)
    return labels, centers


def _smooth_labels(labels: np.ndarray, n: int, sigma: float) -> np.ndarray:
    """Blurred one-hot argmax: removes jaggies and hairline slivers, keeps borders shared."""
    if sigma <= 0.3:
        return labels
    best = np.full(labels.shape, -1.0, dtype=np.float32)
    out = labels.copy()
    for c in range(BG, n):
        m = cv2.GaussianBlur((labels == c).astype(np.float32), (0, 0), sigma)
        better = m > best
        best[better] = m[better]
        out[better] = c
    return out


def _remove_specks(
    labels: np.ndarray,
    n: int,
    min_px: int,
    lab: Optional[np.ndarray] = None,
    centers: Optional[np.ndarray] = None,
    bg_lab: Optional[np.ndarray] = None,
    keep_px: int = 0,
) -> np.ndarray:
    """Re-assign pieces smaller than ``min_px`` to their neighbours.

    With ``lab``/``centers`` given, small pieces that are clearly real marks are kept:
    at least ``keep_px`` big, mostly solid in their own colour and compact (dots,
    hyphens, full stops, letter holes). Ragged or faint fragments are still removed."""
    unknown = np.zeros(labels.shape, bool)
    for c in range(BG, n):
        mask = (labels == c).astype(np.uint8)
        if not mask.any():
            continue
        count, comp, stats, _ = cv2.connectedComponentsWithStats(mask, connectivity=8)
        areas = stats[1:, cv2.CC_STAT_AREA]
        small = np.where(areas < min_px)[0] + 1
        if len(small) and keep_px and lab is not None:
            small = small[~_real_marks(comp, stats, small, lab, centers, bg_lab, c, keep_px)]
        if len(small):
            unknown |= np.isin(comp, small)
    if not unknown.any():
        return labels
    labels = labels.copy()
    labels[unknown] = -2
    kernel = np.ones((3, 3), np.uint8)
    for _ in range(200):
        todo = labels == -2
        if not todo.any():
            break
        best = np.zeros(labels.shape, np.float32)
        choice = np.full(labels.shape, -2, np.int32)
        for c in range(BG, n):
            grown = cv2.dilate((labels == c).astype(np.uint8), kernel).astype(np.float32)
            score = cv2.blur((labels == c).astype(np.float32), (5, 5)) + grown
            upd = todo & (grown > 0) & (score > best)
            best[upd] = score[upd]
            choice[upd] = c
        labels[todo] = choice[todo]
    labels[labels == -2] = BG
    return labels


def _real_marks(comp, stats, ids, lab, centers, bg_lab, c, keep_px) -> np.ndarray:
    """Which of the small components ``ids`` (of class ``c``) are real marks."""
    area = stats[ids, cv2.CC_STAT_AREA].astype(float)
    box = (stats[ids, cv2.CC_STAT_WIDTH] * stats[ids, cv2.CC_STAT_HEIGHT]).astype(float)
    keep = (area >= keep_px) & (area / np.maximum(box, 1) >= MARK_MIN_FILL)
    ref = bg_lab if c == BG else (centers[c] if centers is not None and c < len(centers) else None)
    if ref is None:
        return keep  # e.g. transparent holes: no colour to compare against, shape test only
    sel = np.isin(comp, ids[keep])
    if not sel.any():
        return keep
    solid = delta_e(lab[sel], ref) < MARK_SOLID_DE
    solid_count = np.bincount(comp[sel], weights=solid.astype(float), minlength=comp.max() + 1)
    share = solid_count[ids] / np.maximum(area, 1)
    return keep & (share >= MARK_MIN_SOLID)


def _fold_edge_shades(labels: np.ndarray, rgb: np.ndarray, centers: np.ndarray, bg_lab: Optional[np.ndarray]) -> np.ndarray:
    """Anti-aliased edges between two colours (e.g. navy text on white) form small
    "in-between" colour clusters. Give each such pixel to whichever of the two
    neighbouring colours it is closer to, so outlines sit where the original edge was -
    instead of the whole in-between shade going to one side, which thins or fattens
    thin strokes. Only thin, edge-only clusters lying on the mix of two colours are
    touched; real colours (with an interior) are never folded."""
    fg = labels >= 0
    total = int(fg.sum())
    if total == 0 or len(centers) < 2:
        return labels
    counts = np.bincount(labels[fg], minlength=len(centers))
    rgb_c = lab_to_rgb(centers.reshape(-1, 3)).astype(np.float32)
    candidates = []
    for i in range(len(centers)):
        if counts[i] == 0 or counts[i] / total >= 0.05:
            continue
        mask = (labels == i).astype(np.uint8)
        interior = cv2.erode(mask, np.ones((3, 3), np.uint8)).sum()
        if interior <= 0.25 * counts[i]:  # 1-2 px wide fringe, no body of its own
            candidates.append(i)
    if not candidates:
        return labels
    ends = [(c, rgb_c[c]) for c in range(len(centers)) if counts[c] and c not in candidates]
    if bg_lab is not None:
        ends.append((BG, lab_to_rgb(np.asarray(bg_lab, np.float32).reshape(1, 3))[0].astype(np.float32)))
    if len(ends) < 2:
        return labels
    labels = labels.copy()
    pix = rgb.astype(np.float32)
    for i in candidates:
        best = None
        for k, (a, ca) in enumerate(ends):
            for b, cb in ends[k + 1 :]:
                ab = cb - ca
                t = float(np.dot(rgb_c[i] - ca, ab) / max(float(np.dot(ab, ab)), 1e-6))
                d = float(np.linalg.norm(rgb_c[i] - (ca + t * ab)))
                if 0.08 < t < 0.92 and d < 22 and (best is None or d < best[0]):
                    best = (d, a, ca, b, ab)
        if best is None:
            continue
        _, a, ca, b, ab = best
        sel = labels == i
        t_pix = ((pix[sel] - ca) @ ab) / max(float(np.dot(ab, ab)), 1e-6)
        labels[sel] = np.where(t_pix < 0.5, a, b)  # 50 % coverage = the true edge
    return labels


def _crop_to_foreground(rgba: np.ndarray, opts: RasterOptions) -> np.ndarray:
    """Crop away empty background so ``width_mm`` refers to the design itself."""
    if opts.background in ("none", "ai"):
        return rgba
    h, w = rgba.shape[:2]
    s = min(1.0, 500 / max(h, w))
    small = cv2.resize(rgba, (max(8, int(w * s)), max(8, int(h * s))), interpolation=cv2.INTER_AREA)
    alpha = small[:, :, 3]
    if float(np.mean(alpha < 128)) > 0.005:
        fg = alpha >= 128
    else:
        rgb = _denoise(np.ascontiguousarray(small[:, :, :3]), 1)
        bg, _ = _border_background(rgb_to_lab(rgb), np.ones(alpha.shape, bool), opts.bg_tolerance, connected_only=True)
        fg = ~bg
    ys, xs = np.nonzero(fg)
    if len(xs) < 10:
        return rgba
    x0, x1 = xs.min() / s, (xs.max() + 1) / s
    y0, y1 = ys.min() / s, (ys.max() + 1) / s
    pad = 0.02 * max(x1 - x0, y1 - y0) + 2
    x0, y0 = max(0, int(x0 - pad)), max(0, int(y0 - pad))
    x1, y1 = min(w, int(np.ceil(x1 + pad))), min(h, int(np.ceil(y1 + pad)))
    return rgba[y0:y1, x0:x1]


def _fold_minor_colors(labels: np.ndarray, centers: np.ndarray, max_share: float = 0.03, max_de: float = 30.0) -> np.ndarray:
    """Auto mode: a colour covering only a sliver of the final design that is close to
    another colour (gradient end, anti-alias halo) is not worth a thread change."""
    labels = labels.copy()
    alive = {c for c in range(len(centers)) if (labels == c).any()}
    while len(alive) > 1:
        counts = {c: int((labels == c).sum()) for c in alive}
        total = sum(counts.values()) or 1
        i = min(alive, key=lambda c: counts[c])
        others = [c for c in alive if c != i]
        j = min(others, key=lambda c: float(delta_e(centers[i], centers[c])))
        if counts[i] / total >= max_share or float(delta_e(centers[i], centers[j])) >= max_de:
            break
        labels[labels == i] = j
        alive.discard(i)
    return labels


def raster_to_design(rgba: np.ndarray, opts: RasterOptions) -> tuple[Design, np.ndarray]:
    """Returns the design plus an RGBA preview of the flattened/extracted artwork."""
    rgba = _crop_to_foreground(rgba, opts)
    work, ppm = _resample(rgba, opts.width_mm)
    alpha = work[:, :, 3]
    rgb = np.ascontiguousarray(work[:, :, :3])
    has_alpha = float(np.mean(alpha < 128)) > 0.005
    valid = alpha >= 128 if has_alpha else np.ones(alpha.shape, bool)

    rgb = _denoise(rgb, max(0, min(3, opts.denoise)))
    rgb = _adjust(rgb, opts.brightness, opts.contrast, opts.sharpen)
    lab = rgb_to_lab(rgb)

    warnings: list[str] = []
    fg = valid.copy()
    bg_lab: Optional[np.ndarray] = None
    if opts.background == "ai":
        fg &= ~_ai_background(rgb)
    elif opts.background in ("auto", "color"):
        if not (has_alpha and opts.background == "auto"):
            bg, bg_lab = _border_background(lab, valid, opts.bg_tolerance, connected_only=opts.background == "auto")
            if not bg.any() and opts.background == "color":
                warnings.append("No plain background was found around the picture, so nothing was removed. Try 'Keep everything' or the AI cut-out.")
            fg &= ~bg

    labels, centers = _quantize(lab, fg, opts)
    n = len(centers)
    if not opts.num_colors:
        labels = _fold_edge_shades(labels, rgb, centers, bg_lab)
    min_feature_px = opts.min_feature_mm * ppm
    labels = _smooth_labels(labels, n, sigma=min_feature_px * 0.45)
    keep_px = int(max(4, MARK_MIN_MM2 * ppm * ppm))
    labels = _remove_specks(
        labels, n, int(max(4, opts.min_area_mm2 * ppm * ppm)),
        lab=lab, centers=centers, bg_lab=bg_lab, keep_px=keep_px,
    )
    if not opts.num_colors:
        labels = _fold_minor_colors(labels, centers)

    rgb_centers = lab_to_rgb(centers.reshape(-1, 3))
    objects: list[DesignObject] = []
    areas = []
    for c in range(n):
        mask = (labels == c).astype(np.uint8) * 255
        polys = mask_to_polygons(mask, epsilon_px=max(0.6, ppm * 0.04))
        layer_area = 0.0
        for p in polys:
            g = affinity.scale(p, 1 / ppm, 1 / ppm, origin=(0, 0)).simplify(0.03)
            # speck removal already decided what is noise; only drop tracing crumbs here
            if g.is_empty or g.area < MARK_MIN_MM2 / 2:
                continue
            objects.append(DesignObject(layer=c, geometry=g))
            layer_area += g.area
        areas.append(layer_area)

    # Layer order = stitch order: large background areas first, small details on top.
    order = [c for c in sorted(range(n), key=lambda c: -areas[c]) if areas[c] > 0]
    remap = {old: new for new, old in enumerate(order)}
    layers = []
    for old in order:
        rgb_c = tuple(int(v) for v in rgb_centers[old])
        layer = ColorLayer(index=remap[old], rgb=rgb_c, name=color_name(rgb_c))  # type: ignore[arg-type]
        if bg_lab is not None and float(delta_e(centers[old], bg_lab)) < opts.bg_tolerance:
            # enclosed regions in the background colour (letter counters etc.) usually show fabric
            layer.enabled = False
            warnings.append(f"'{layer.name}' looks like the background colour, so it is not stitched (the fabric shows there). Tick it in the colour list to stitch it.")
        layers.append(layer)
    objects = [DesignObject(layer=remap[o.layer], geometry=o.geometry) for o in objects if o.layer in remap]

    h, w = labels.shape
    # where the working picture sits in design mm (x, y, width, height): lets the editor
    # show the original picture exactly under the traced shapes
    reference_box = (0.0, 0.0, w / ppm, h / ppm)
    if objects:
        from shapely.affinity import translate

        minx = min(o.geometry.bounds[0] for o in objects)
        miny = min(o.geometry.bounds[1] for o in objects)
        objects = [DesignObject(layer=o.layer, geometry=translate(o.geometry, -minx, -miny)) for o in objects]
        width_mm = max(o.geometry.bounds[2] for o in objects)
        height_mm = max(o.geometry.bounds[3] for o in objects)
        k = opts.width_mm / width_mm  # exact requested width (crop padding is ~4%)
        if abs(k - 1) > 1e-3:
            objects = [DesignObject(layer=o.layer, geometry=affinity.scale(o.geometry, k, k, origin=(0, 0))) for o in objects]
            width_mm, height_mm = width_mm * k, height_mm * k
        else:
            k = 1.0
        reference_box = (-minx * k, -miny * k, w / ppm * k, h / ppm * k)
    else:
        width_mm, height_mm = w / ppm, h / ppm
    design = Design(
        width_mm=width_mm,
        height_mm=height_mm,
        layers=layers,
        objects=objects,
        source_kind="raster",
        warnings=warnings,
    )

    preview = np.zeros((h, w, 4), np.uint8)
    for c in range(n):
        preview[labels == c, :3] = rgb_centers[c]
        preview[labels == c, 3] = 255
    # not part of the saved design: the service stores it as the editing reference
    design.reference = {"box": [round(v, 4) for v in reference_box], "picture": work}  # type: ignore[attr-defined]
    return design, preview
