"""Prompts for the AI tasks. Answers are always validated before use (see tasks.py)."""

CLEAN_MODES = {
    "redraw": (
        "Redraw this logo/design as clean, flat artwork ready for machine embroidery. "
        "Keep exactly the same shapes, text, layout and proportions. Use only solid flat colours "
        "(at most {colors} colours). Remove photo background, fabric texture, shadows, gradients, glare and noise. "
        "Place the design on a plain pure white background. Make thin lines bold enough to sew. "
        "Do not add any new elements or text."
    ),
    "remove_background": (
        "Remove the background completely and place the design on a plain pure white background. "
        "Do not change the design itself: keep every shape, colour, line and letter exactly as it is. "
        "Remove shadows on the background, surrounding objects and fabric texture."
    ),
    "enhance": (
        "Make this artwork sharper and cleaner at higher resolution: remove blur, JPEG artefacts, noise and "
        "colour banding, straighten slightly wobbly edges and make solid areas evenly coloured. "
        "Keep the design, layout, colours and text exactly the same. Use a plain white background."
    ),
}

SUGGEST = """You are an expert machine-embroidery digitizer. The picture will be embroidered about {width_mm:.0f} mm wide.
Look at it and reply with JSON only, no other text:
{{
  "design_type": "logo" | "text" | "illustration" | "photo" | "mixed",
  "thread_colors": <integer 1-15: distinct thread colours needed; ignore anti-aliasing and JPEG shades>,
  "background_is_part_of_design": <true if the background area must be stitched, else false>,
  "detail_level": "low" | "medium" | "high",
  "has_small_text": <true/false>,
  "has_gradients_or_photo": <true/false>,
  "picture_quality": "clean" | "slightly_noisy" | "noisy_or_blurry",
  "recommended_cleanup": "none" | "redraw" | "remove_background" | "enhance",
  "notes": [<up to 3 short, plain-language tips for this design>]
}}"""

REVIEW = """You are an expert machine-embroidery digitizer reviewing a design before it is sewn.
Image 1 is a realistic preview of the generated stitches{original_hint}.
Design facts: {facts}
Current settings: {settings}

Look for problems a customer would see: gaps or fabric showing, stitching too dense or stiff, thin lines or
small text that will not sew cleanly, shapes that look distorted compared to the artwork, wrong stitch direction
choices. Only suggest setting changes that are clearly needed, within these limits:
fill_spacing_mm 0.25-1.0, fill_stitch_length_mm 2-7, satin_spacing_mm 0.25-1.0, satin_max_width_mm 3-12,
pull_compensation_mm 0-0.8, underlay true/false.
Reply with JSON only:
{{
  "overall": "good" | "ok" | "needs_work",
  "issues": [{{"severity": "high" | "medium" | "low", "title": "<short>", "detail": "<one plain-language sentence>"}}],
  "changes": {{<setting name>: <new value>, ...}}
}}"""
