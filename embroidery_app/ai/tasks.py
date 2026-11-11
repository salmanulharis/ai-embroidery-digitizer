"""The AI-assisted tasks. Each one works without AI (built-in algorithms) and uses an AI
service on top when the user has configured one. AI answers are validated; any AI
failure falls back to the built-in result with a plain-language note."""

from __future__ import annotations

import io
import json
import logging
from typing import Optional

import cv2
import numpy as np
from PIL import Image

from ..stitch.safety import summarize_risks
from . import builtin, prompts, registry
from .base import EDIT, VISION, AIError, Credentials

log = logging.getLogger("embroidery.ai")

CLEAN_MODES = tuple(prompts.CLEAN_MODES)


def _png(rgba: np.ndarray, max_side: int) -> bytes:
    h, w = rgba.shape[:2]
    s = min(1.0, max_side / max(h, w))
    if s < 1:
        rgba = cv2.resize(rgba, (max(8, int(w * s)), max(8, int(h * s))), interpolation=cv2.INTER_AREA)
    # flatten transparency onto white: image models handle opaque pictures more reliably
    rgb = rgba[:, :, :3].astype(np.float32)
    a = rgba[:, :, 3:4].astype(np.float32) / 255.0
    flat = (rgb * a + 255 * (1 - a)).astype(np.uint8)
    buf = io.BytesIO()
    Image.fromarray(flat).save(buf, format="PNG")
    return buf.getvalue()


def _provider_and_creds(ai: Optional[dict]):
    """(provider, credentials) from a request's ``ai`` block, or (None, None)."""
    if not ai or not ai.get("provider") or ai.get("provider") == "builtin":
        return None, None
    provider = registry.get(str(ai["provider"]))
    return provider, provider.resolve(Credentials.from_request(ai))


# --------------------------------------------------------------------------- clean-up (AI only)

def clean_image(rgba: np.ndarray, ai: Optional[dict], mode: str = "redraw", colors: int = 6) -> tuple[bytes, str, str]:
    """Returns (png, provider label, model). Needs an AI service that can edit pictures."""
    if mode not in prompts.CLEAN_MODES:
        raise ValueError("Unknown clean-up type.")
    provider, creds = _provider_and_creds(ai)
    if provider is None:
        raise AIError("Picture clean-up needs an AI service - add one in AI settings. "
                      "Without AI, the built-in preparation already removes noise and plain backgrounds.")
    model = registry.choose_model(provider, creds, EDIT)
    prompt = prompts.CLEAN_MODES[mode].format(colors=max(1, min(int(colors or 6), 15)))
    out = provider.edit_image(creds, model, _png(rgba, 1536), prompt)
    try:
        Image.open(io.BytesIO(out)).verify()
    except Exception as exc:
        raise AIError("The AI returned something that is not a picture.") from exc
    return out, provider.label, model


# --------------------------------------------------------------------------- settings suggestions

_DETAIL_HIGH_MM = 0.25  # fine detail / small text: keep even thinner lines
_DETAIL_LOW_MM = 0.5  # simple artwork from a blurry picture: smooth more
_QUALITY_DENOISE = {"clean": 0, "slightly_noisy": 2, "noisy_or_blurry": 3}


def suggest_settings(rgba: np.ndarray, width_mm: float, ai: Optional[dict]) -> dict:
    result = builtin.analyze_image(rgba, width_mm)
    provider, creds = _provider_and_creds(ai)
    if provider is None:
        return result
    try:
        model = registry.choose_model(provider, creds, VISION)
        answer = provider.vision_json(creds, model, [_png(rgba, 1024)], prompts.SUGGEST.format(width_mm=width_mm))
    except AIError as exc:
        log.info("AI suggestion failed, using built-in: %s", exc)
        result["ai_error"] = f"{provider.label}: {exc} The built-in analysis was used instead."
        return result
    return _merge_suggestion(result, answer, provider.label, model)


def _merge_suggestion(base: dict, ai: dict, label: str, model: str) -> dict:
    """AI judges what needs understanding (colour count, background, picture type);
    measured values (noise, thinnest line) stay from the built-in analysis."""
    out = json.loads(json.dumps(base))
    ext = out["extract"]
    colors = ai.get("thread_colors")
    if isinstance(colors, (int, float)) and not isinstance(colors, bool) and 1 <= colors <= 15:
        ext["num_colors"] = int(colors)
    if isinstance(ai.get("background_is_part_of_design"), bool):
        ext["background"] = "none" if ai["background_is_part_of_design"] else (ext["background"] if ext["background"] != "none" else "auto")
    if ai.get("detail_level") == "high" or ai.get("has_small_text") is True:
        ext["min_feature_mm"] = min(ext["min_feature_mm"], _DETAIL_HIGH_MM)
    if ai.get("has_small_text") is True and out.get("text", {}).get("found"):
        out.setdefault("stitch", {})["thread_weight"] = 60
    elif ai.get("detail_level") == "low":
        ext["min_feature_mm"] = max(ext["min_feature_mm"], _DETAIL_LOW_MM)
    if ai.get("picture_quality") in _QUALITY_DENOISE:
        ext["denoise"] = max(ext["denoise"], _QUALITY_DENOISE[ai["picture_quality"]])
    if ai.get("design_type") in ("logo", "text", "illustration", "photo", "mixed"):
        out["design_type"] = ai["design_type"]
    if ai.get("recommended_cleanup") in ("none",) + CLEAN_MODES:
        out["cleanup"] = ai["recommended_cleanup"]
    tips = [str(t)[:240] for t in (ai.get("notes") or []) if isinstance(t, (str, int, float))][:3]
    out["notes"] = tips + [n for n in out["notes"] if n not in tips]
    out.update(source="ai", provider=label, model=model)
    return out


# --------------------------------------------------------------------------- stitch review

def review_stitches(plan, design, params, preview_png: bytes, original_png: Optional[bytes], ai: Optional[dict]) -> dict:
    result = builtin.review_plan(plan, design, params)
    provider, creds = _provider_and_creds(ai)
    if provider is None:
        return result
    stats = plan.stats()
    facts = json.dumps({
        "size_mm": [stats["width_mm"], stats["height_mm"]], "stitches": stats["stitches"], "colours": stats["colors"],
        "thread_cuts": stats["trims"], "layers": [l.name for l in design.layers if l.enabled],
        "machine_check_spots": summarize_risks(plan.risks),
    })
    settings = json.dumps({k: getattr(params, k) for k in builtin.STITCH_LIMITS})
    prompt = prompts.REVIEW.format(
        facts=facts, settings=settings,
        original_hint=" and image 2 is the original artwork" if original_png else "",
    )
    images = [preview_png] + ([original_png] if original_png else [])
    try:
        model = registry.choose_model(provider, creds, VISION)
        answer = provider.vision_json(creds, model, images, prompt)
    except AIError as exc:
        log.info("AI review failed, using built-in: %s", exc)
        result["ai_error"] = f"{provider.label}: {exc} The built-in check was used instead."
        return result
    return _merge_review(result, answer, params, provider.label, model)


def _merge_review(base: dict, ai: dict, params, label: str, model: str) -> dict:
    out = json.loads(json.dumps(base))
    seen = {i["title"].lower() for i in out["issues"]}
    for item in (ai.get("issues") or [])[:8]:
        if not isinstance(item, dict):
            continue
        title = str(item.get("title") or "").strip()[:80]
        if not title or title.lower() in seen:
            continue
        sev = item.get("severity") if item.get("severity") in ("high", "medium", "low") else "low"
        out["issues"].append({"severity": sev, "title": title, "detail": str(item.get("detail") or "")[:300], "by_ai": True})
        seen.add(title.lower())
    for name, value in (ai.get("changes") or {}).items():
        v = builtin.clamp_change(name, value)
        if v is not None and v != getattr(params, name, None):
            out["changes"][name] = v
    order = {"high": 0, "medium": 1, "low": 2}
    out["issues"].sort(key=lambda i: order[i["severity"]])
    if ai.get("overall") in ("good", "ok", "needs_work"):
        rank = {"good": 0, "ok": 1, "needs_work": 2}
        out["overall"] = max(out["overall"], ai["overall"], key=rank.get)
    out.update(source="ai", provider=label, model=model)
    return out


# --------------------------------------------------------------------------- AI settings screen

def list_models(ai: dict, refresh: bool = False) -> dict:
    provider, creds = _provider_and_creds(ai)
    if provider is None:
        raise AIError("Choose an AI service first.")
    models = registry.models_for(provider, creds, refresh=refresh)
    return {
        "provider": provider.id,
        "models": [m.to_dict() for m in models],
        "recommended": registry.recommended(provider, models),
    }
