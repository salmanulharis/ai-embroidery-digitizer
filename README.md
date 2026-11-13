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
pytest                            # pipeline, exports, API, editing, AI (mocked), golden output
```

**Deploy to AWS EC2 with GitHub Actions** (deploys on version tags only): see [deploy/ec2/README.md](deploy/ec2/README.md).

AI is optional. Open **⚙ AI settings** in the app to add your own key, or set server-wide keys in `.env`
(copy `.env.example`). See [AI assistant](#ai-assistant).

## AI assistant

Every assistant feature works **without AI**. Built-in algorithms answer by default, and an AI service
refines the result when configured.

| Feature | Without AI (built-in) | With an AI service |
|---|---|---|
| **Suggest best settings** (step 2) | Measures the picture: background, colour count (k-means in LAB), noise level, thinnest line in mm at your size, photo vs. logo | A vision model judges the colour count, background and picture type. Measured values (noise, thin lines) stay from the built-in analysis |
| **Picture clean-up**: redraw / remove background / sharpen & enhance | Not needed. The built-in preparation already denoises and removes plain backgrounds | An image model edits the picture. You always compare it with the original and can switch back |
| **Check my stitches** (step 3) | Rule-based checks: density, foundation, thread cuts, tiny or very thin shapes, satin width, size and time, with one-click setting changes | A vision model also reviews the rendered stitch preview against the artwork |

**Providers:**
- Google Gemini
- OpenAI
- OpenRouter
- Anthropic Claude
- Ollama (local)
- any OpenAI-compatible service (LM Studio, vLLM, Groq, …)

**How it works:**
- **Models are listed live** from each provider with the user's key and filtered by capability (image
  editing or image understanding). A recommended model is preselected, so no model names go stale in the code.
- **Keys** are entered in the browser and kept there: per session by default, or "Remember on this device".
  They're sent only with AI requests, used for that one call, never saved or logged on the server, and
  removed from error messages. Server-wide keys in `.env` are optional defaults.
- **Safety:**
  - AI answers are validated and clamped to safe ranges, and unknown settings are ignored.
  - Any AI failure falls back to the built-in result with a note.
  - `AI_ALLOW_PRIVATE_ENDPOINTS=0` stops users pointing the server at internal addresses on public deployments.
- **Adding a provider:** one file in `embroidery_app/ai/providers/` with a `Provider` subclass and `@register`.
  The settings screen, model lists and API pick it up automatically.

## Research summary and decisions

### Is AI needed at each stage?

| Stage | Best approach | AI? |
|---|---|---|
| Background removal (logos) | Border-color detection, connected to the edge (deterministic, exact edges) | No |
| Background removal (photos) | `rembg` (local, free). Optional install. The model is pinned with `REMBG_MODEL` (default `isnet-general-use`): rembg's own default, BRIA RMBG-2.0, needs a paid licence for commercial use | Optional |
| Messy input (photo of a shirt, blurry or low-res logo) | Image model redraws, cuts out or enhances the picture (Gemini image models, GPT Image, via OpenRouter…). User must approve: the model can change details or text | Optional, *where AI clearly wins* |
| Color reduction | k-means in CIELAB + perceptual merge | No |
| Vectorization | Smoothed label map → contour tracing (shapely). LLM-generated SVG is unreliable | No |
| Parameter suggestions and stitch review | Built-in measurements and rules, refined by a vision model (incl. local Ollama `qwen3-vl`) | Optional |
| Stitch generation | Geometric algorithms (scan-line tatami, satin columns, underlay). No AI model produces reliable stitch paths | No |

AI costs money and isn't deterministic, so it's used only as an opt-in cleanup and advice step. The core
pipeline is fast (≈1 s per design), free, offline and repeatable.

### Libraries evaluated

| Need | Options considered | Chosen | Why |
|---|---|---|---|
| Embroidery file I/O | pyembroidery (no release since Mar 2024), **pystitch**, libembroidery | **pystitch** (MIT) | The maintained Ink/Stitch fork of pyembroidery (released 2026), same API. DST/EXP/U01/JEF output is byte-identical; PES/VP3 differ only in rounding |
| Stitch engine | Ink/Stitch (GPL, needs an Inkscape runtime, no supported headless library API), commercial engines (Wilcom, no API) | **Built-in engine** on shapely | Embeddable, testable, MIT-friendly. Ink/Stitch can be added later as an alternative backend |
| SVG / AI / PDF parsing | svgelements (inactive since 2024, and scaled transformed shapes inconsistently), svgpathtools, **PyMuPDF** | **PyMuPDF** plus a small CSS and gradient pre-pass | Actively maintained by Artifex. Handles transforms, `<use>`, arcs and fill rules consistently. The pre-pass adds Illustrator's `<style>` classes and gradient → average colour. *AGPL licence, see below* |
| HTTP client (AI) | httpx (no release since Dec 2024), **httpx2**, SDKs per provider | **httpx2** (BSD) | Maintained successor by the Pydantic team, same API. Plain REST keeps each provider in one small file without vendor SDKs |
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

> ⚠️ Generated files are validated by reading them back with pystitch and checking stitch counts,
> bounds, trims and color stops. Before production, sew a test piece on your WONYO machine and open the
> DST in Wilcom to confirm trims and color stops behave as expected with your controller settings.

## How the stitch engine works

0. **Fabric preset** (step 3, default "General" = the standard settings): choosing a fabric (woven, denim,
   T-shirt, piqué, fleece, towel, cap) fills in shrink compensation, thickness and foundation for it; every value
   stays editable. The worksheet names the backing and needle. Values are starting points: sew a test piece.
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
3. **Underlay (foundation)**, named types (`stitch/underlay.py`), design-wide or per shape:
   * under fills: none, edge run, edge run + lattice (standard), edge run + double lattice (crossing at +-45 deg)
   * under single-angle satin: none, centre run, edge run (standard), zigzag, edge run + zigzag, double zigzag;
     columns under 2.5 mm get a centre run instead of a zigzag
   * satin that follows a path (bands, lines, columns, lettering) keeps its own foundation along the path
   * fabric presets pick stronger foundations for knits, fleece and towels
   * **push compensation** (optional, 0-0.5 mm) shortens fills and columns at their ends
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

### Smart direction (optional)

By default every shape of a colour is filled in that colour's direction, and only thin strokes (< 2 mm),
clean rings and straight columns get satin. That suits some sections of a design and spoils others: curved
or medium-sized lettering becomes a flat fill, and a fill angle that is right for one shape runs along the
edge of the next. **Smart direction** (`smart_direction`, off by default) looks at each section on its own:

* **Columns**: a narrow section of any width up to the satin limit gets satin that follows its centre
  line: curved and circular text, swooshes, borders with corners, letters with several holes.
  * Stitches cross the column at right angles all the way along, with a stitch across each end.
  * The stitch gap is measured on the outer edge of a bend; on the inside of a tight bend every second
    (or two of three) stitch stops short, so the thread doesn't pile up.
  * Where strokes meet, the two that continue each other are sewn as one (the upright of an R, the bar
    of a T) and the others end at its edge with a 0.3 mm overlap. At a corner one stroke runs on to the
    outline and the other ends against it.
  * Foundation by width: centre run (from 1.5 mm), a run along both edges (from 2.5 mm), plus a zig-zag
    (from 4 mm).
  * Strokes of one shape are sewn in an order that avoids thread cuts: the needle moves along the centre
    of strokes that are still to be sewn, where their satin hides it.
* **Sections**: a narrow arm growing out of a wide shape (a tail, a connecting bar) is sewn as its own
  satin column and overlaps the filled body by 0.5 mm, so no gap opens at the seam. Rounded corners, short
  stubs and parts nearly as wide as the body stay with the fill.
* **Fill direction per section**: rows cross the section the short way, don't run along its edges
  (within 12°) and break into as few pieces as possible. Shapes that sew equally well in any direction
  (discs, squares at 45°) keep the colour's direction, so "change direction per colour" still shows.
* **Straight satin** is refined from the 15° search to the degree.

Rules:

* Precedence is unchanged: **shape → colour → smart → design**. A direction or stitch style you set for a
  shape or a colour always wins, and "same direction for the whole design" in the shape editor still
  gives one direction everywhere.
* With the option off, the stitching is byte-identical to before. Small lettering (< 1 mm strokes) keeps
  its own engine, which widens strokes and protects gaps.
* It is plain geometry in `stitch/smart.py`: no AI model, deterministic and cached per outline.
* A shape is only sewn as columns if they cover it (at most 6% left over after corner patches);
  otherwise it keeps the classic satin or fill.

The web UI explains every option in plain language: hover over, tap or focus the **?** next to it. All help
text lives in `embroidery_app/static/help.js`.

## Project layout

```
embroidery_app/
  api.py              FastAPI routes + static UI
  service.py          workflow orchestration, on-disk projects (data/projects/<id>)
  models.py           Design (vector, mm) · StitchParams · StitchPlan
  config.py           settings from env / .env
  fabrics.py          fabric presets (step 3): fill in compensation, density, foundation; backing + needle on the worksheet
  colors.py, geometry.py
  ingest/
    __init__.py       file-type detection
    raster.py         image → Design (background, colors, smoothing, tracing)
    vector.py         SVG / AI / PDF → Design (knockout, palette merge, strokes)
  stitch/
    rows.py           scan lines + chain decomposition
    generators.py     tatami, satin (area / line / band), running, underlay
    planner.py        classification, ordering, travel/jump/trim/lock assembly
    registry.py       stitch-method registry: name, flags, generator
    methods.py        the built-in methods (fill, satin, satin band/line, column, stroke, dot, running, outline)
    strokes.py        small lettering: satin along each letter stroke
    optimize.py       smart stitch reduction (coverage-checked)
    safety.py         min/max stitch length, machine check
    smart.py          smart direction: columns along centre lines, sections, fill direction per section
  export/
    embroidery.py     pystitch writers + worksheet
    vector.py         clean SVG (mm)
    preview.py        browser preview JSON + realistic PNG render
  ai/
    base.py           provider interface, credentials, safe HTTP (retry, redaction, endpoint check)
    registry.py       provider registry, live model lists (cached), model choice
    providers/        gemini · openai · openrouter · anthropic · ollama · custom (one file each)
    builtin.py        non-AI picture analysis + stitch review (the default)
    prompts.py        prompts per task
    tasks.py          suggest / clean-up / review: built-in first, AI on top, validated
  static/             index.html, app.js, shapes.js, ai.js, help.js, style.css (no build step)
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
| POST | `/api/projects/{id}/stitch` | `{params:{fill_spacing_mm, underlay, smart_direction, …}}` → preview data + stats, and each shape's stitch style and direction as sewn |
| GET | `/api/projects/{id}/export/{fmt}` | `dst exp u01 pes jef vp3 svg png txt zip`. For `zip`, `?formats=dst,pes,txt` picks the files (default: DST, EXP, PES, SVG, PNG, worksheet) |
| POST | `/api/ai/models` | `{ai:{provider, api_key, base_url}}` → live models + recommended picks |
| POST | `/api/projects/{id}/ai/suggest` | `{ai?, width_mm?}` → recommended preparation settings (`ai/analyze` = old name) |
| POST | `/api/projects/{id}/ai/clean` | `{ai, mode: redraw\|remove_background\|enhance, colors?}` (needs an image-capable AI) |
| POST | `/api/projects/{id}/ai/revert` | back to the original picture |
| POST | `/api/projects/{id}/ai/review` | `{ai?}` → issues + setting changes for the current stitches |

`ai` is `{provider, api_key?, base_url?, vision_model?, edit_model?}`. Leave it out (or use `provider: "builtin"`)
for the built-in algorithms.

## Extending

* **New stitch type**: write a generator `(item, angle, params, start) -> list[Run]` and register it with
  `registry.register(StitchMethod(name, generator, ...flags))` (see `stitch/methods.py`); then let
  `planner.classify` choose it. The planner dispatches through the registry, and `cap_stitch_length`
  guarantees no stitch exceeds the maximum whatever the generator does. Golden tests
  (`tests/test_golden.py`) catch unintended changes to existing stitching; regenerate them with
  `UPDATE_GOLDEN=1` only for deliberate changes.
* **New input format**: convert it to SVG or RGBA in `service.create_project`.
* **New AI provider**: add `ai/providers/<name>.py` with a `Provider` subclass decorated with `@register`
  (implement `list_models`, plus `edit_image` and/or `vision_json`), then import it in `ai/providers/__init__.py`.
  Services that speak the OpenAI API need no code: use the "OpenAI-compatible" provider.
* **New AI task**: add a prompt in `ai/prompts.py`, a built-in fallback in `ai/builtin.py` and the combination in `ai/tasks.py`.
* **Alternative stitch backend** (e.g. Ink/Stitch CLI): consume `design_to_svg()` output and return a pystitch pattern.

## Known limitations / roadmap

* Without **Smart direction**, satin on irregular shapes, such as some letters, falls back to fill. With it,
  narrow shapes up to the satin width get satin along their centre lines; shapes wider than that are still
  filled in one direction per section (no curved or contour fill yet).
* No specialty stitches yet (motif, contour/spiral fill, 3D puff, appliqué, sequins).
* Text inside SVGs must be converted to outlines. EPS and CorelDRAW must be exported to SVG or PDF first.
* Thread brand catalogs (Madeira, Isacord…) aren't bundled. Colors are free RGB that you can edit per layer.

## Licences

The app's own code has no third-party copyleft. Dependency licences: pystitch (MIT), shapely (BSD), OpenCV
(Apache-2), NumPy (BSD), Pillow (MIT-CMU), FastAPI (MIT), httpx2 (BSD).

**PyMuPDF is AGPL-3.0, and it now handles all vector import (SVG, AI and PDF).** If you distribute the app
or offer it as a public network service without releasing your source, buy an Artifex commercial licence.
Picture (PNG/JPG) import doesn't use it.

## Dependencies

Every dependency is actively maintained (releases in 2026) and pinned to a tested minimum version in
`requirements.txt`. Replaced in Nov 2026:
- `pyembroidery` → `pystitch`
- `svgelements` → PyMuPDF
- `httpx` → `httpx2`
- the deprecated `fitz` import → `pymupdf`

The test suite runs with deprecation warnings treated as errors (`pytest -W error::DeprecationWarning`)
to catch future deprecations early.
