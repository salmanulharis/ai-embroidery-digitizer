# AI Embroidery Digitizer

Converts raster images (PNG/JPG/WEBP…) and vector artwork (SVG, Adobe Illustrator `.ai`, PDF) into
**machine-ready embroidery files** with real stitch data: fill, satin and running stitches, underlay,
pull compensation, lock stitches, trims, and color changes. The primary output is **Tajima DST**, for
WONYO (Dahao-controller) machines and Wilcom EmbroideryStudio.

```
Upload ──► Extract & approve ──► Stitch & preview ──► Export
 image     background removal     fill / satin / run     DST ★, EXP, U01, PES, JEF, VP3
 vector    color reduction        underlay, pull comp.   SVG (clean vector), PNG preview,
           vector shapes (mm)     trims, locks, order    production worksheet, ZIP
           per-color settings     simulator + stats
```

## Quick start

```bash
python -m venv .venv
.venv\Scripts\activate            # Windows  (macOS/Linux: source .venv/bin/activate)
pip install -r requirements.txt
python run.py                     # open http://127.0.0.1:8000
pytest                            # 23 tests: pipeline, export round-trip, API
```

AI is optional. To turn it on, copy `.env.example` to `.env` and add any OpenAI, Gemini or OpenRouter key, and/or
point it at an Ollama vision model.

## Research summary and decisions

### Is AI needed at each stage?

| Stage | Best approach | AI? |
|---|---|---|
| Background removal (logos) | Border-color detection, connected to the edge (deterministic, exact edges) | No |
| Background removal (photos) | `rembg` / BiRefNet (local, free). Optional install | Optional |
| Messy input (photo of a shirt, blurry or low-res logo) | Image-generation model redraws flat artwork (Gemini 2.5 Flash Image, `gpt-image-1`, via OpenRouter). User must approve: the model can change details or text | Optional, *where AI clearly wins* |
| Color reduction | k-means in CIELAB + perceptual merge | No |
| Vectorization | Smoothed label map → contour tracing (shapely). LLM-generated SVG is unreliable | No |
| Parameter suggestions | Vision LLM (incl. local Ollama `qwen2.5vl`) suggests color count and flags cleanup | Optional, low value |
| Stitch generation | Geometric algorithms (scan-line tatami, satin columns, underlay). No AI model produces reliable stitch paths | No |

AI costs money and isn't deterministic, so it's used only as an opt-in cleanup and advice step. The core
pipeline is fast (≈1 s per design), free, offline and repeatable.

### Libraries evaluated

| Need | Options considered | Chosen | Why |
|---|---|---|---|
| Embroidery file I/O | pyembroidery, libembroidery, Ink/Stitch | **pyembroidery** (MIT) | Pure Python. Writes DST, EXP, PES, JEF, VP3, U01 and TBF. Encodes DST trims the Tajima way |
| Stitch engine | Ink/Stitch (GPL, needs an Inkscape runtime, no supported headless library API), commercial engines (Wilcom, no API) | **Built-in engine** on shapely | Embeddable, testable, MIT-friendly. Ink/Stitch can be added later as an alternative backend |
| SVG parsing | svgelements, svgpathtools, cairosvg | **svgelements** | Full transform, viewBox, CSS, arc and fill-rule support |
| AI / PDF import | PyMuPDF, Inkscape CLI, Ghostscript | **PyMuPDF** | Modern `.ai` files are PDF containers. Converts to SVG with text as paths. *AGPL licence, see below* |
| Raster tracing | potrace (GPL, 2-color), vtracer, own contour tracing | **OpenCV contours + label smoothing** | Gap-free shared borders between colors, which embroidery needs |
| Web | Flask + Celery/Redis, FastAPI | **FastAPI**, no queue | Processing takes about a second, so a job queue adds ops cost without benefit |

### Machine and software compatibility

* **WONYO** multi-needle machines use **Dahao** controllers, which read **DST** (and DSB/DSZ). DST is
  the safest choice. Trims are written as the standard Tajima jump sequence, which Dahao recognises.
* **Wilcom EmbroideryStudio** imports DST, EXP, U01, PES, JEF, VP3 and more, and imports SVG artwork for
  manual re-digitizing. Wilcom's native EMB is closed and can't be written by any open-source tool.
* DST stores **no thread colors**, only color-change stops. The exported *worksheet* lists the color
  sequence for assigning needles. PES and VP3 keep colors.
* **DSB/DSZ** (Dahao/Barudan) can't be written by any open-source library. DST covers the same machines.
* Coordinates: 0.1 mm units, design centered on the hoop origin. Stitches are capped at 7 mm (the DST limit is 12.1 mm).

> ⚠️ Generated files are validated by reading them back with pyembroidery and checking stitch counts,
> bounds, trims and color stops. Before production, sew a test piece on your WONYO machine and open the
> DST in Wilcom to confirm trims and color stops behave as expected with your controller settings.

## How the stitch engine works

0. **Per-shape settings**: click shapes in the design (or in the stitch preview) to give them their
   own stitch style (automatic / fill / satin / outline / don't stitch) and direction. You can type the
   angle, use a preset, or drag to draw it. You can also apply one direction to the whole design.
   * Per-shape **thickness**: Light 0.50, Normal 0.40, Dense 0.33 mm, or custom 0.25–1.0 mm. This is the
     gap between fill rows or satin stitches.
   * Per-shape **extra width** (shrink compensation, 0–0.8 mm) for fill and satin, and **bold outline**
     (single or 3×) for outlines.
   * Smart stitch reduction derives its micro-stitch threshold from the smallest row gap actually used,
     so dense shapes keep every row turn-around.
   * Precedence: **shape → color → automatic**.
   * A color's direction steers fills only. A shape's direction also turns single-angle satin.
   * Satin that follows a curve (borders, strokes) always follows it.
   * Angles use the engine's y-down convention: 45° runs top-left → bottom-right.
   * Settings survive joining shapes (when pieces disagree, the largest piece wins and you're told).
   * Settings also survive "Update preview": shapes are re-matched by overlap, even at a new width.
   * One-level **Undo** (Ctrl+Z) covers every design change.
0. **One region per color**: all filled pieces of a color, such as merged shades, near-identical vector
   colors or overlapping same-color shapes, are joined into continuous regions. Only the hairline seams
   *between* pieces (< 0.2 mm) are bridged, never across another color. Outlines and holes stay exact,
   so a merged color stitches exactly like artwork drawn in one solid color.
1. **Classification** (per object, or forced per color):
   * ring-shaped band (outline, border) → **satin along its centerline**, adapting to the local width
   * straight-ish narrow column (≤ max satin width) → **satin** at the angle with the shortest rows
   * wide or irregular shapes → **tatami fill** (brick-offset penetrations, angle varied per color)
   * SVG strokes: < 1 mm → **running** (optional triple/bean), 1–7 mm → **satin along the path**, wider → fill
2. **Pull compensation**: shapes grow by 0.2 mm (configurable).
3. **Underlay**: edge run plus a perpendicular lattice under fills. Edge or center run under satins.
4. **Rows → chains**: scan lines are grouped into monotone regions, so the needle sews back and forth
   without leaving the shape. Chains are ordered nearest-first.
5. **Travel**: along the shape edge, or straight only where it doesn't show on finished stitching.
   Otherwise a jump.
6. **Sequencing**: colors from largest area to smallest (details on top), objects nearest-neighbor.
   Jumps longer than 2 mm get a **trim**, with **tie-in/tie-off** locks around every trim and color change.
7. **Smart stitch reduction** (on by default, `optimize`): removes stacked micro-stitches (< 0.3 mm, at
   fill-row tips and in underlay), and lattice underlay in areas narrower than ~3.8 mm where it only
   duplicates the edge run. Safety rules:
   * needle drops are only removed, never added or moved;
   * satin, lock stitches and triple runs are never touched;
   * no stitch exceeds 7 mm;
   * the underlay change is kept only if it adds no pieces (no extra jumps or trims);
   * a gap check compares the visible stitching before and after, and falls back to the original if more
     than 0.5% would lose coverage.

   The UI shows original vs optimized counts and lets you switch the preview between them. Typical savings:
   1–10% on logos, up to ~40% on thin lettering.

The web UI explains every option in plain language: hover over, tap or focus the **?** next to it. All help
text lives in `embroidery_app/static/help.js`.

## Project layout

```
embroidery_app/
  api.py              FastAPI routes + static UI
  service.py          workflow orchestration, on-disk projects (data/projects/<id>)
  models.py           Design (vector, mm) · StitchParams · StitchPlan
  config.py           settings from env / .env
  colors.py, geometry.py
  ingest/
    __init__.py       file-type detection
    raster.py         image → Design (background, colors, smoothing, tracing)
    vector.py         SVG / AI / PDF → Design (knockout, palette merge, strokes)
  stitch/
    rows.py           scan lines + chain decomposition
    generators.py     tatami, satin (area / line / band), running, underlay
    planner.py        classification, ordering, travel/jump/trim/lock assembly
  export/
    embroidery.py     pyembroidery writers + worksheet
    vector.py         clean SVG (mm)
    preview.py        browser preview JSON + realistic PNG render
  ai/providers.py     OpenAI · Gemini · OpenRouter · Ollama (REST, no SDKs)
  static/             index.html, app.js, style.css (no build step)
tests/                pytest suite
```

## API

| Method | Path | Purpose |
|---|---|---|
| GET | `/api/config` | formats, defaults, AI provider availability, hoops |
| POST | `/api/projects` | upload (multipart `file`) |
| POST | `/api/projects/{id}/extract` | `{options:{width_mm, num_colors, background, min_feature_mm, …}}` |
| PUT | `/api/projects/{id}/layers` | edit layers: enabled, thread color, stitch type, angle, order |
| POST | `/api/projects/{id}/layers/merge` | `{source, target}` |
| PUT | `/api/projects/{id}/objects` | per-shape settings `{ids, scope: selected\|colour\|all, stitch_type, angle, enabled, spacing_mm, pull_mm, bold}`. `"inherit"` = same as colour or design |
| POST | `/api/projects/{id}/objects/reset` | `{ids}` back to colour settings (empty = all shapes) |
| POST | `/api/projects/{id}/undo` | undo the last design change (one level) |
| POST | `/api/projects/{id}/stitch` | `{params:{fill_spacing_mm, underlay, …}}` → preview data + stats |
| GET | `/api/projects/{id}/export/{fmt}` | `dst exp u01 pes jef vp3 svg png txt zip` |
| POST | `/api/projects/{id}/ai/clean` · `ai/analyze` · `ai/revert` | optional AI assist |

## Extending

* **New stitch type**: add a generator in `stitch/generators.py` and a branch in `planner._stitch_item`.
* **New input format**: convert it to SVG or RGBA in `service.create_project`.
* **New AI provider**: subclass `Provider` in `ai/providers.py` and register it in `PROVIDERS`.
* **Alternative stitch backend** (e.g. Ink/Stitch CLI): consume `design_to_svg()` output and return a pyembroidery pattern.

## Known limitations / roadmap

* Satin on irregular shapes, such as some letters, falls back to fill. Variable-direction satin on free
  shapes (skeleton-based columns) is the next quality step. SVG strokes and ring borders already get true path satin.
* No specialty stitches yet (motif, contour/spiral fill, 3D puff, appliqué, sequins).
* Text inside SVGs must be converted to outlines. EPS and CorelDRAW must be exported to SVG or PDF first.
* Thread brand catalogs (Madeira, Isacord…) aren't bundled. Colors are free RGB that you can edit per layer.

## Licences

The app's own code has no third-party copyleft. Dependency licences: pyembroidery (MIT), shapely (BSD),
OpenCV (Apache-2), svgelements (MIT), FastAPI (MIT). **PyMuPDF is AGPL-3.0.** If you distribute the app or
offer it as a network service without releasing your source, buy an Artifex commercial licence or drop
`.ai`/PDF support (SVG import doesn't need it).
