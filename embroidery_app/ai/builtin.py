"""Built-in "smart" analysis - the default when no AI service is configured, and the
measured baseline that AI answers are combined with.

* ``analyze_image``: recommends preparation settings from measurements of the picture
  (background, colour count, noise, thinnest line in mm at the chosen size).
* ``review_plan``: rule-based check of generated stitches (density, thread cuts, tiny
  or very thin shapes, foundation, size) with concrete setting changes.
"""

from __future__ import annotations

import math

import cv2
import numpy as np

from ..colors import merge_palette, rgb_to_lab
from ..ingest.raster import _assign, _border_background, _denoise, _kmeans_lab
from ..models import Design, StitchParams, StitchPlan

# Valid ranges for settings the assistant may change (same limits as the UI).
STITCH_LIMITS: dict[str, tuple] = {
    "fill_spacing_mm": (0.25, 1.0),
    "fill_stitch_length_mm": (2.0, 7.0),
    "satin_spacing_mm": (0.25, 1.0),
    "satin_max_width_mm": (3.0, 12.0),
    "pull_compensation_mm": (0.0, 0.8),
    "trim_threshold_mm": (1.0, 20.0),
    "min_object_area_mm2": (0.0, 10.0),
    "underlay": (bool,),
    "optimize": (bool,),
}


def clamp_change(name: str, value):
    """Validated value for a stitch setting, or None when invalid."""
    limits = STITCH_LIMITS.get(name)
    if limits is None:
        return None
    if limits == (bool,):
        return value if isinstance(value, bool) else None
    try:
        v = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(v):
        return None
    return round(min(max(v, limits[0]), limits[1]), 2)


# --------------------------------------------------------------------------- picture analysis

def analyze_image(rgba: np.ndarray, width_mm: float = 100.0) -> dict:
    h0, w0 = rgba.shape[:2]
    s = min(1.0, 600 / max(h0, w0))
    img = cv2.resize(rgba, (max(8, int(w0 * s)), max(8, int(h0 * s))), interpolation=cv2.INTER_AREA) if s < 1 else rgba
    alpha = img[:, :, 3]
    rgb = np.ascontiguousarray(img[:, :, :3])
    has_alpha = float(np.mean(alpha < 128)) > 0.005
    valid = alpha >= 128 if has_alpha else np.ones(alpha.shape, bool)
    lab = rgb_to_lab(_denoise(rgb, 1))
    notes: list[str] = []

    if has_alpha:
        bg_found, fg = True, valid
    else:
        bg, _ = _border_background(lab, valid, 14.0, connected_only=True)
        bg_found, fg = bool(bg.any()), valid & ~bg
    if fg.sum() < 100:
        fg = valid

    # noise: robust (median-based) Immerkaer estimate, so sharp logo edges don't count as noise
    gray = cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY).astype(np.float32)
    k = np.array([[1, -2, 1], [-2, 4, -2], [1, -2, 1]], np.float32)
    response = np.abs(cv2.filter2D(gray, -1, k))[1:-1, 1:-1]
    sigma = float(np.median(response) / 0.6745 / 6.0)
    denoise = 0 if sigma < 1.0 else 1 if sigma < 2.5 else 2 if sigma < 5.0 else 3

    # colours: k-means in LAB + perceptual merge (same method as the extraction)
    pixels = lab[fg]
    rng = np.random.default_rng(7)
    sample = pixels if len(pixels) <= 40_000 else pixels[rng.choice(len(pixels), 40_000, replace=False)]
    centers = _kmeans_lab(sample, min(16, max(1, len(sample) // 50)))
    weights = np.bincount(_assign(sample, centers), minlength=len(centers)).astype(float)
    groups = merge_palette(centers, weights, threshold=16.0)
    share = np.array([weights[groups == g].sum() for g in range(groups.max() + 1)]) / max(weights.sum(), 1)
    significant = [g for g in range(len(share)) if share[g] >= 0.015]
    n_colors = max(1, len(significant))

    # photo / gradients: many distinct colours with a real share
    codes = (rgb[fg] // 16).astype(np.int32)
    codes = codes[:, 0] * 256 + codes[:, 1] * 16 + codes[:, 2]
    counts = np.bincount(codes, minlength=4096)
    photo_like = int(np.sum(counts >= max(1, 0.0005 * len(codes)))) > 120

    # thinnest line, in mm at the chosen design width
    labels = np.full(fg.shape, -1, np.int32)
    labels[fg] = groups[_assign(pixels, centers)]
    ys, xs = np.nonzero(fg)
    fg_w_px = (xs.max() - xs.min() + 1) if len(xs) else fg.shape[1]
    mm_per_px = width_mm / max(fg_w_px, 1)
    widths: list[np.ndarray] = []
    for g in significant:
        mask = cv2.morphologyEx((labels == g).astype(np.uint8), cv2.MORPH_OPEN, np.ones((2, 2), np.uint8))
        if mask.sum() < 20:
            continue
        dt = cv2.distanceTransform(mask, cv2.DIST_L2, 3)
        ridge = (dt >= cv2.dilate(dt, np.ones((3, 3), np.uint8))) & (dt > 0.9)
        widths.append(2 * dt[ridge])
    thin_mm = float(np.percentile(np.concatenate(widths), 10) * mm_per_px) if widths and sum(map(len, widths)) else None

    min_feature = 0.7
    if thin_mm is not None and thin_mm < 0.7:
        min_feature = max(0.4, round(thin_mm * 0.8, 1))
    if thin_mm is not None and thin_mm < 0.5:
        needed = int(math.ceil(width_mm * 0.6 / thin_mm / 5) * 5)
        notes.append(
            f"Some lines are only about {thin_mm:.1f} mm thick at {width_mm:.0f} mm wide and may disappear or look "
            f"broken. A design width of about {needed} mm keeps them sewable."
        )

    if has_alpha:
        notes.append("Transparent background found - only the design will be stitched.")
    elif bg_found:
        notes.append("Plain background found - it will be removed so the fabric shows there.")
    else:
        notes.append("No plain background found - everything will be stitched. Use clean-up “Remove background” if that is not intended.")

    original_fg_px = int(fg_w_px / s)
    cleanup = "none"
    if original_fg_px < 300:
        cleanup = "enhance"
        notes.append(f"Low-resolution picture (about {original_fg_px} px wide) - an enhanced or redrawn version will give smoother edges.")
    if photo_like:
        cleanup = "redraw" if bg_found else "remove_background"
        n_colors = min(n_colors, 8)
        notes.append(f"Looks like a photo or has colour fades - it will be simplified to about {n_colors} thread colours.")
    elif denoise >= 3:
        cleanup = "enhance"

    return {
        "source": "builtin",
        "design_type": "photo" if photo_like else "logo",
        "extract": {
            "num_colors": n_colors,
            "background": "auto" if bg_found else "none",
            "min_feature_mm": min_feature,
            "denoise": denoise,
        },
        "cleanup": cleanup,
        "notes": notes,
        "measurements": {
            "noise_level": round(sigma, 2),
            "thinnest_line_mm": None if thin_mm is None else round(thin_mm, 2),
            "colours_found": n_colors,
        },
    }


# --------------------------------------------------------------------------- stitch review

def review_plan(plan: StitchPlan, design: Design, params: StitchParams) -> dict:
    from ..stitch.planner import describe_objects

    stats = plan.stats()
    issues: list[dict] = []
    changes: dict = {}
    enabled = {l.index for l in design.layers if l.enabled}
    shapes = [o for o in design.objects if o.layer in enabled and o.enabled]
    info = {d["id"]: d for d in describe_objects(design, params)}
    area_mm2 = sum(o.geometry.area for o in shapes if o.kind == "area")

    def add(severity, title, detail, **change):
        issues.append({"severity": severity, "title": title, "detail": detail})
        for k, v in change.items():
            v = clamp_change(k, v)
            if v is not None and v != getattr(params, k, None):
                changes[k] = v

    density = stats["stitches"] / (area_mm2 / 100) if area_mm2 > 50 else 0
    if density > 140:
        add("high", "Very dense stitching",
            f"About {density:.0f} stitches per cm² - the embroidery may be stiff and pucker the fabric.",
            fill_spacing_mm=params.fill_spacing_mm + 0.05)
    elif 0 < density < 35 and params.fill_spacing_mm >= 0.5:
        add("medium", "Thin coverage", "Fill rows are far apart - fabric may show through on contrasting colours.",
            fill_spacing_mm=params.fill_spacing_mm - 0.05)

    if not params.underlay and area_mm2 > 200:
        add("high", "Foundation stitches are off",
            "Large filled areas without foundation stitches can sink into the fabric and look patchy.", underlay=True)

    if stats["trims"] > 15 and stats["trims"] > stats["stitches"] / 400 and params.trim_threshold_mm < 3:
        add("medium", "Many thread cuts",
            f"{stats['trims']} thread cuts add about {stats['trims'] * 10 // 60} min of sewing time.",
            trim_threshold_mm=3.0)

    tiny = [o for o in shapes if o.kind == "area" and o.geometry.area < 2.0]
    if len(tiny) > 5 and params.min_object_area_mm2 < 1.5:
        add("medium", "Many tiny shapes",
            f"{len(tiny)} shapes are smaller than 2 mm² - they become blobs of thread and need extra cuts.",
            min_object_area_mm2=1.5)

    thin_satin = [
        o for o in shapes if o.kind == "area" and info.get(o.id, {}).get("method") in ("satin", "satin_band")
        and 2 * o.geometry.area / max(o.geometry.length, 1e-6) < 1.0
    ]
    if thin_satin and params.pull_compensation_mm < 0.3:
        add("medium", "Very thin satin shapes",
            f"{len(thin_satin)} satin shape(s) are under 1 mm wide and may look broken - a little extra width helps.",
            pull_compensation_mm=min(params.pull_compensation_mm + 0.1, 0.4))

    if params.satin_max_width_mm > 8:
        add("low", "Long satin stitches allowed",
            "Satin wider than 8 mm can snag on clothing; 7 mm is safer.", satin_max_width_mm=7.0)

    if not params.optimize:
        add("low", "Smart stitch reduction is off", "Turning it on removes stacked stitches without changing the design.",
            optimize=True)

    if stats["colors"] > 10:
        add("low", "Many thread colours",
            f"{stats['colors']} colours means many thread changes - consider merging similar shades in step 2.")
    if stats["stitches"] > 100_000:
        add("high", "Very high stitch count", "Consider a smaller design or a thinner fill.")
    if stats["est_minutes"] > 60:
        add("low", "Long sewing time", f"About {stats['est_minutes']:.0f} minutes on the machine.")

    severities = {i["severity"] for i in issues}
    overall = "needs_work" if "high" in severities else "ok" if "medium" in severities else "good"
    return {"source": "builtin", "overall": overall, "issues": issues, "changes": changes}
