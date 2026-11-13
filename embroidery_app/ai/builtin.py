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
from ..ingest.raster import _assign, _border_background, _denoise, _fold_edge_shades, _kmeans_lab
from ..models import GUIDED_STYLES, THREAD_WEIGHTS, Design, StitchParams, StitchPlan

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
    "optimize_order": (bool,),
    "knockdown": (bool,),
    "thread_weight": ("choice", THREAD_WEIGHTS),
}


def clamp_change(name: str, value):
    """Validated value for a stitch setting, or None when invalid."""
    limits = STITCH_LIMITS.get(name)
    if limits is None:
        return None
    if limits == (bool,):
        return value if isinstance(value, bool) else None
    if limits[0] == "choice":
        try:
            v = int(value)
        except (TypeError, ValueError):
            return None
        return v if v in limits[1] else None
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

    bg_lab = None
    if has_alpha:
        bg_found, fg = True, valid
    else:
        bg, bg_lab = _border_background(lab, valid, 14.0, connected_only=True)
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
    group_centers = np.array([
        np.average(centers[groups == g], axis=0, weights=weights[groups == g] + 1e-9) for g in range(groups.max() + 1)
    ], np.float32)
    labels = np.full(fg.shape, -1, np.int32)
    labels[fg] = groups[_assign(pixels, centers)]
    # anti-aliased letter edges are not thread colours (same rule as the extraction)
    labels = _fold_edge_shades(labels, rgb, group_centers, bg_lab)
    counts_g = np.bincount(labels[labels >= 0], minlength=len(group_centers)).astype(float)
    share = counts_g / max(counts_g.sum(), 1)
    significant = [g for g in range(len(share)) if share[g] >= 0.015]
    n_colors = max(1, len(significant))

    # photo / gradients: many distinct colours with a real share
    codes = (rgb[fg] // 16).astype(np.int32)
    codes = codes[:, 0] * 256 + codes[:, 1] * 16 + codes[:, 2]
    counts = np.bincount(codes, minlength=4096)
    photo_like = int(np.sum(counts >= max(1, 0.0005 * len(codes)))) > 120

    # thinnest line, in mm at the chosen design width
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

    min_feature = 0.3  # the extraction default: keeps lettering intact
    if thin_mm is not None and thin_mm < 0.35:
        min_feature = max(0.2, round(thin_mm * 0.8, 1))

    # lettering: rows of similar-sized marks sitting on a common baseline
    text = detect_text(fg, mm_per_px)
    thread_weight = 40
    if text["found"]:
        h = text["letter_height_mm"]
        if h < 8:
            min_feature = min(min_feature, 0.25)  # keep letter shapes, holes and punctuation
            denoise = min(denoise, 2)  # strong grain clean-up softens small letters
        if h < 5:
            thread_weight = 60
        msg = (f"Text found: {text['lines']} line(s), letters about {h:.1f} mm tall. "
               "Letters are kept sharp and sewn with satin that follows each stroke")
        msg += " - fine 60 wt thread is suggested for letters this small." if thread_weight == 60 else "."
        notes.append(msg)
        if h < 3:
            needed = int(math.ceil(width_mm * 4.0 / h / 5) * 5)
            notes.append(f"Letters under 3 mm tall are hard to sew cleanly - about {needed} mm design width makes them 4 mm tall.")
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
        "stitch": {"thread_weight": thread_weight},
        "text": text,
        "cleanup": cleanup,
        "notes": notes,
        "measurements": {
            "noise_level": round(sigma, 2),
            "thinnest_line_mm": None if thin_mm is None else round(thin_mm, 2),
            "colours_found": n_colors,
        },
    }


def detect_text(fg: np.ndarray, mm_per_px: float) -> dict:
    """Find lines of lettering in a foreground mask: at least four marks of similar
    height whose bottoms line up (a baseline), close together along the line. Works for
    Latin print/script and Arabic (connected letters + dots) well enough to switch on the
    fine-detail settings; it does not read the text."""
    out = {"found": False, "lines": 0, "letter_height_mm": None}
    if fg.sum() < 50:
        return out
    n, _, stats, _ = cv2.connectedComponentsWithStats(fg.astype(np.uint8), connectivity=8)
    if n < 5:
        return out
    H = fg.shape[0]
    x, y, w, h, area = (stats[1:, i].astype(float) for i in range(5))
    ok = (h >= 4) & (h <= 0.35 * H) & (w <= 4 * h) & (area >= 6)
    idx = np.nonzero(ok)[0]
    if len(idx) < 4:
        return out
    bottom = y + h
    cx = x + w / 2
    text_ids = []
    for i in idx:
        same = idx[
            (np.abs(bottom[idx] - bottom[i]) <= 0.25 * h[i])
            & (h[idx] >= 0.5 * h[i]) & (h[idx] <= 2.0 * h[i])
            & (np.abs(cx[idx] - cx[i]) <= 12 * h[i])
        ]
        if len(same) >= 4:
            text_ids.append(i)
    if len(text_ids) < 4:
        return out
    ids = np.array(text_ids)
    order = ids[np.argsort(bottom[ids])]
    lines, last = 0, None
    for i in order:  # count baselines
        if last is None or bottom[i] - last > 0.5 * h[i]:
            lines += 1
        last = bottom[i]
    height = float(np.median(h[ids]) * mm_per_px)
    return {"found": True, "lines": int(lines), "letter_height_mm": round(height, 1)}


# --------------------------------------------------------------------------- stitch review

CAP_MAX_HEIGHT_MM = 57.0  # 2.25 in, the usual limit for the front of a structured cap (ASI)


def _layer_style(design: Design, index: int) -> str:
    try:
        return design.layer(index).stitch_type
    except KeyError:
        return "auto"


def _review_fabric(plan: StitchPlan, design: Design, params: StitchParams, stats: dict, add) -> None:
    from ..fabrics import get_fabric

    fabric = get_fabric(params.fabric)
    preset = fabric.settings
    if fabric.id == "general":
        return
    if preset.get("knockdown") and not params.knockdown:
        add("medium", "No knockdown on pile fabric",
            f"On {fabric.label.lower()} the loops swallow small text and outlines. A knockdown (an open fill sewn "
            "first, in the fabric's colour) keeps them on top.", knockdown=True)
    want_pull = preset.get("pull_compensation_mm", 0.0)
    if params.pull_compensation_mm < want_pull - 0.05:
        add("medium", "Low shrink compensation for this fabric",
            f"{fabric.label} pulls stitches in more than firm cotton: {want_pull:.2f} mm shrink compensation keeps "
            "columns and fills full width.", pull_compensation_mm=want_pull)
    if fabric.id == "cap":
        if stats["height_mm"] > CAP_MAX_HEIGHT_MM:
            add("medium", "Design too tall for a cap",
                f"The design is {stats['height_mm']:.0f} mm tall; most caps take about {CAP_MAX_HEIGHT_MM:.0f} mm on "
                "the front panel. Make it smaller in step 2.")
        if params.satin_max_width_mm > 7.0:
            add("low", "Wide satin on a cap", "Caps get rubbed: satin up to 7 mm wide holds up better.",
                satin_max_width_mm=7.0)


def review_plan(plan: StitchPlan, design: Design, params: StitchParams) -> dict:
    from ..stitch.safety import summarize_risks

    stats = plan.stats()
    issues: list[dict] = []
    changes: dict = {}
    enabled = {l.index for l in design.layers if l.enabled}
    shapes = [o for o in design.objects if o.layer in enabled and o.enabled]
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

    # Tiny shapes are often dots and punctuation of lettering (meaningful - e.g. Arabic
    # dots), so never suggest deleting them; suggest a larger design instead.
    skipped = [o for o in shapes if o.kind == "area" and o.geometry.area < params.min_object_area_mm2]
    tiny = [o for o in shapes if o.kind == "area" and params.min_object_area_mm2 <= o.geometry.area < 2.0]
    if skipped:
        add("medium", "Small marks are not stitched",
            f"{len(skipped)} shape(s) such as dots or punctuation are smaller than "
            f"“Skip shapes smaller than” ({params.min_object_area_mm2:g} mm²) and are left out. "
            "Lower that setting or make the design larger to keep them.")
    if len(tiny) > 5:
        add("low", "Many very small shapes",
            f"{len(tiny)} shapes are smaller than 2 mm² - they sew as tiny knots. For lettering, "
            "a larger design width gives cleaner results.")

    # Machine check (stitch/safety.py): where the thread may break or a detail may not show.
    # Small lettering is never "fixed" with extra width - that closes the gaps between
    # letters; the stroke engine already widens strokes as far as the gaps allow.
    risk = summarize_risks(plan.risks)
    if risk.get("thin"):
        if params.thread_weight != 60:
            add("medium", "Lettering thinner than the machine can sew",
                f"{risk['thin']} spot(s) in small lettering are thinner than a 40 wt thread column and could not be "
                "widened without closing gaps between letters. Fine 60 wt thread (with a 65/9 or 70/10 needle) sews "
                "narrower columns cleanly - or make the design larger. See the Machine check view.",
                thread_weight=60)
        else:
            add("medium", "Lettering thinner than the machine can sew",
                f"{risk['thin']} spot(s) in small lettering are thinner than even fine thread can sew as satin; they are "
                "sewn as a triple run or slightly narrow. Make the design larger for crisp letters. See the Machine check view.")
    if risk.get("stack") or risk.get("short"):
        n = risk.get("stack", 0) + risk.get("short", 0)
        add("high", "Spots where the thread may break",
            f"{n} spot(s) have very short stitches or hit one needle hole many times. Turning on smart stitch "
            "reduction or a slightly lighter thickness helps; see the Machine check view.",
            **({"optimize": True} if not params.optimize else {"satin_spacing_mm": params.satin_spacing_mm + 0.05}))
    if risk.get("dense"):
        add("medium", "Very dense spots",
            f"{risk['dense']} small area(s) get more stitches than the fabric can take (hard spots, needle holes). "
            "A slightly thinner fill helps.", fill_spacing_mm=params.fill_spacing_mm + 0.05)

    if params.satin_max_width_mm > 10:
        add("low", "Very wide satin allowed",
            "Satin wider than 10 mm is split into shorter stitches but still snags easily on clothing; "
            "10 mm or less is safer.", satin_max_width_mm=10.0)

    if not params.optimize:
        add("low", "Smart stitch reduction is off", "Turning it on removes stacked stitches without changing the design.",
            optimize=True)
    if not params.optimize_order and stats["trims"] > 10:
        add("low", "Smart sewing order is off",
            "Turning it on sews the shapes of each colour in an order with shorter jumps, so fewer thread cuts.",
            optimize_order=True)

    # Fabric (step 3): settings the chosen fabric needs (see fabrics.py)
    _review_fabric(plan, design, params, stats, add)

    # Guided styles need a line drawn on the shape (stitch/guided.py)
    unguided = [o for o in shapes if o.kind == "area" and o.guide is None
                and (o.stitch_type or _layer_style(design, o.layer)) in GUIDED_STYLES]
    if unguided:
        add("medium", "Guide line missing",
            f"{len(unguided)} shape(s) are set to guided satin or Florentine fill but have no guide line yet, so they "
            "are sewn the usual way. Select them in step 2 and use “Draw guide line”.")
    if stats.get("stops"):
        add("low", "The machine stops for applique",
            f"The machine stops {stats['stops']} time(s) after the placement line: lay the fabric piece over it, "
            "then continue. The worksheet lists the stops.")

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
