# AI Embroidery Digitizer

Converts raster images (PNG/JPG/WEBP…) and vector artwork (SVG, Adobe Illustrator `.ai`, PDF) into
**machine-ready embroidery files** with real stitch data: fill, satin and running stitches, underlay,
pull compensation, lock stitches, trims, and color changes. The primary output is **Tajima DST**, for
WONYO (Dahao-controller) machines and Wilcom EmbroideryStudio.

```
Upload ──► Prepare shapes ─────► Stitch & preview ──► Export
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
pytest                            # pipeline, exports, API, editing, AI (mocked), golden output,
                                  # property tests on random shapes (HYPOTHESIS_PROFILE=deep for a longer search)
```

**Deploy to AWS EC2 with GitHub Actions** (deploys on version tags only): see [deploy/ec2/README.md](deploy/ec2/README.md).

**Sign in:** set `SUPER_ADMIN_EMAIL` and `SUPER_ADMIN_PASSWORD` (in `.env`, copy `.env.example`) before the first
start; that account is created at startup and adds everyone else. See [Accounts](#accounts),
[Plans, allowance and usage](#plans-allowance-and-usage) and [Privacy](#privacy-temporary-designs-and-what-is-stored).

AI is optional. Open **AI settings** in the app to add your own key, or set server-wide keys in `.env`.
See [AI assistant](#ai-assistant).

## The editor

After the upload one editor opens: settings on the left (Size, Colours, Picture or Artwork, Stitches, Smart
assistant), the preview in the middle, and Selection, Threads, Design info and Checks on the right. The bottom bar
holds undo/redo, the preview style, the sewing simulation, zoom (100% = actual size, approximate), the status and
**Export**. Below 1200 px the right panel, and below 900 px the left one, open as sheets from the bottom bar.

* **Shapes / Stitches**: the same design as shapes (select, edit outlines, draw guide and direction lines) or as
  stitches. The selection is shared. **Original** shows the uploaded picture under either view, aligned in mm.
* **The preview keeps itself up to date.** A changed setting prepares the design again (Size, Colours, Picture) or
  creates the stitches again (Stitches, threads, shapes) a moment later, without covering the screen. Requests run
  one at a time, and changes made meanwhile are sent together with the latest values. A design that takes over
  ~3 s to stitch switches to **Update** in the bottom bar; *Auto-update* can also be turned off there.
* **Export** opens a dialog: files grouped by use, a summary (size, hoop fit, stitches, time), checks and the
  allowance note. It brings the stitches up to date first, so a download always matches the screen.
* **Shapes list** (Threads panel → *Shapes*): every shape by thread colour with size, style and direction; select
  with a click or the keyboard (↑ ↓, Enter, Space) – a keyboard path to every shape.
* **Keyboard shortcuts** (⌨ in the bottom bar or `?`): F fit, 1 / 2 = 100 / 200 %, + / − zoom, S Shapes / Stitches,
  O original picture, V B E L G editing tools, Enter update, Ctrl+E export. **Full screen** where the browser allows it.
* **Design info** splits the stitches by type (fill, satin, running, foundation, travel, lock stitches).
* **Undo / redo** cover everything in the order it happened: shape edits, threads, merging, Size / Colours /
  Picture settings (undoing one puts back the design and the setting) and the stitch settings.
* **Changed settings** are marked against what the design started with (the stitch defaults and the picture
  settings found after the upload): ↺ resets one setting, "Reset" a section, "Reset all" everything.
* **Presets** (top of the settings panel): choose one and press **Apply** to fill in a set of settings at once.
  Built-in presets (Standard, Small text & logos, Lightweight, Clean logo, High contrast; Premium: Fine detail, Dense
  coverage, Large design, Satin-forward, Fill-forward, Soft colours, Photo) are by plan tier; *Save as preset…*
  keeps the current settings (stitch, fabric & thread; optionally picture and size) under a name, *Manage…* applies,
  edits, updates, duplicates and deletes them. Applying is one undo step and makes the preset the baseline, so the
  changed marks show what differs from it. Presets hold settings only, never the picture, shapes or threads.
  After an upload the smart analysis may *suggest* a preset (e.g. Small text & logos for lettering under 6 mm),
  offered with a button and never applied by itself. Locked presets show which plan includes them.
  Built-in values are starting points to confirm with a sew-out, like the fabric presets.
* **Style settings** (borders, stipple, cross stitch, motifs, pattern / jagged fills, photo stitch) are listed only
  for the styles the design uses; *Show the settings of every stitch style* lists them all.
* **Hoop**: the hoop chosen under Size is drawn around the design, centred like on the machine; it turns red and
  the Checks warn when the design does not fit.
* **Continue in Wilcom** (export): a ZIP with the DST, the artwork as EPS and SVG (also one EPS per thread, named
  with sewing order, style and direction), the worksheet and a how-to. Wilcom EmbroideryStudio rebuilds editable
  objects from the stitches (object recognition) or from the artwork (Convert / Auto-Digitize). It is not an EMB
  project: that format is Wilcom's own and cannot be written by other software.


## Accounts

- **Sign in** with email or username and password. There is no public sign-up: the **Super Admin** creates accounts
  in the *Admin console → Users* (add, edit name / email / username / role, activate / deactivate, reset password, sign out
  everywhere, delete), with a generated password to copy. Usernames are optional and unique in any letter case (3-32
  letters, digits, `.` `_` `-`, never `@`). The Super Admin comes from `SUPER_ADMIN_EMAIL` / `SUPER_ADMIN_PASSWORD`
  (GitHub secrets in production) and is created at startup when no account has that email; its username comes only from
  `SUPER_ADMIN_USERNAME`, applied at every start. That account cannot be demoted, deactivated or deleted in the app;
  other accounts can be given the Super Admin role.
- **Sessions:** at sign-in the user picks *until I close the browser* (the server forgets it after a day at most) or
  1 / 7 / 15 / 30 days (capped by the *Longest "keep me signed in"* setting, default `SESSION_DAYS`). *My account* lists
  every signed-in device and can sign out the others; the admin can sign a user out everywhere. Deactivating,
  resetting a password, changing a role or deleting an account ends its sessions.
- **Each account sees only its own designs and AI settings.** Every API request is checked on the server:
  projects belong to the account (and browser session) that uploaded them, and admin functions need the Super Admin role.
- **Security:** passwords hashed with scrypt; session tokens random, kept in an HttpOnly SameSite cookie
  (Secure over HTTPS) and stored only as a hash; failed sign-ins are rate-limited, and so is work per account
  (uploads and edits, downloads, AI requests); cross-site requests are refused; sensitive admin actions go to an
  audit log. Data lives in `DATA_DIR/app.db` (SQLite, upgraded automatically on start).
- Lost Super Admin password: `python -m embroidery_app.auth reset-password <email or username>` on the server.
- Public sign-up can be added later by calling `auth.create_user` from a sign-up route: it already assigns the default
  plan in the same transaction.

## Plans, allowance and usage

- **What counts:** one *design* = one uploaded file whose stitch output is downloaded. The first download of a machine
  file, the stitch preview PNG, the worksheet or a ZIP with any of them counts once; re-downloads, other formats and edits
  of the same upload are free. Uploading, preparing, previewing, creating stitches and the artwork SVG never count.
- **Plans** (*Admin console → Subscription plans*): Free (default, 10 designs per month), Basic (100 per month) and Pro
  (unlimited) are created on the first start; all values are editable and plans can be added, disabled or deleted (when
  unused). A plan has an allowance (a number per calendar month in UTC, a number in total, or **unlimited** - a state
  of its own, never a big number), feature switches (own AI service, the app's AI keys, all machine formats, ZIP
  downloads, photo stitch, built-in presets, premium presets, saving own presets) and limits (how many own presets:
  Free 0, Basic 5, Pro 25 by default). Every new account gets the default plan. The built-in presets table on the
  same page moves a preset between the Basic and Premium tiers.
- **Presets after a downgrade** stay usable (apply, rename, edit, delete); only saving new ones is refused until the
  account is under its limit again. The limit is checked and the preset saved in one transaction.
- **Per account** (*Users → Details*): change the plan (upgrade / downgrade), set or remove an end date, give a custom
  allowance or unlimited downloads, switch single features on or off, set a limit of its own, reset the usage counter. Overrides win over the
  plan; an ended subscription falls back to the default plan; the Super Admin always has everything.
- **Safety:** the allowance is checked and the design reserved in one database transaction, and a unique index allows
  only one charge per design, so parallel or repeated requests cannot overspend or double-charge. A failed download is
  never charged; an interrupted one is released after 10 minutes.
- **Users see** their plan, allowance and end date in the header chip and on *My account*; the download box says
  whether the next download counts. At the limit, downloads of new designs are refused with an explanation (and the
  admin is named as the way to get more); everything else keeps working.
- **Payments are not processed.** The data model is ready for it (subscription status, start and end dates, plan
  history, payment provider references, payment status, a transactions table, plan prices), but nothing in the app
  takes or pretends to take a payment.

## Privacy: temporary designs and what is stored

The app processes designs; it is not a storage service. A design (the upload and everything made from it) lives in a
temporary folder `DATA_DIR/projects/<random id>` and is deleted when:

- the same browser session uploads a new file, or the user clicks *Close design*;
- the user signs out, or the session ends (expired, signed out elsewhere, password reset, account deactivated);
- the page is closed: it tells the server (`pagehide` beacon), and the design is deleted after a 2-minute grace period
  unless the page comes back (a reload reopens it);
- nobody has used it for 2 hours, or it is older than 24 hours (both settings), whatever the browser did.

The server checks every 5 minutes (no external scheduler needed); the systemd timer from `deploy/ec2/setup.sh` stays
as a backstop.

| Stored permanently (SQLite) | How long |
|---|---|
| Accounts: email, username, name, role, scrypt password hash, active flag, sign-in times | until the account is deleted |
| Sessions: token hash, device label ("Chrome on Windows"), times | until they end |
| Plans, subscriptions, plan history, per-account overrides | until the account / plan is deleted |
| AI settings, keys encrypted with AES-GCM | until removed or the account is deleted |
| Usage metadata: what was done, when, success, AI service and model, duration, token counts | `usage_retention_days` (default 730) |
| Daily activity (account id + day) for active-user counts | same as usage |
| Audit log of admin actions (no passwords, keys or content) | `audit_retention_days` (default 730) |

Never stored in the database: uploaded files, designs, stitch files, file names, prompts or AI answers. Deleting an
account deletes all of the above for it.

## Admin console

*Account menu → Admin console* (Super Admin only): **Overview** (health at a glance), **Users** (search, filter by plan,
status and usage; create, edit, details), **Subscription plans**, **User usage** (allowance per account, reset),
**Analytics** (today / 7 / 30 / 90 days / custom: accounts, daily and monthly active users, designs per day with failures
and refusals, top users, most used features, plan distribution, accounts near or at their limit, AI requests by service,
model and task with time and tokens), **AI overview** (services, accounts using them, no keys), **Settings** (clean-up
times, session length, retention) and the **Audit log**. Charts aggregate in SQL; the browser never receives raw
records.

## AI assistant

Every assistant feature works **without AI**. Built-in algorithms answer by default, and an AI service
refines the result when configured.

| Feature | Without AI (built-in) | With an AI service |
|---|---|---|
| **Suggest best settings** (Smart assistant) | Measures the picture: background, colour count (k-means in LAB), noise level, thinnest line in mm at your size, photo vs. logo | A vision model judges the colour count, background and picture type. Measured values (noise, thin lines) stay from the built-in analysis |
| **Picture clean-up**: redraw / remove background / sharpen & enhance | Not needed. The built-in preparation already denoises and removes plain backgrounds | An image model edits the picture. You always compare it with the original and can switch back |
| **Check my stitches** (Checks) | Rule-based checks: density, foundation, thread cuts, tiny or very thin shapes, satin width, size and time, with one-click setting changes | A vision model also reviews the rendered stitch preview against the artwork |

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
- **Keys belong to an account.** Each user saves their own services, keys and models in AI settings and can test
  the connection. Keys are stored encrypted (AES-GCM with `APP_SECRET_KEY`, bound to the account) and never sent back
  to the browser, which only sees "saved, ends in 1234". AI requests always use the signed-in account's own settings;
  keys are removed from error messages and never logged or recorded. Server-wide keys in `.env` are used **only** for
  accounts whose plan (or override) includes *The app's AI keys* (off by default); everyone else gets a setup message.
  The same applies to keyless local services (Ollama) at the server's address.
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

0. **Blend fill** (Wilcom: Multi Blend): a shape set to "Blend fill" with "Blend into colour" chosen in the shape
   editor is sewn in its own colour with rows that thin out across it, and again in the second colour shaded the
   other way. At every point the two shares add up to one full fill (gap = row gap / share), so the colours mix
   evenly. Without a second colour it fades into the fabric. The second half is sewn in that colour's turn.
0. **Photo stitch** (stitch settings, off by default), three styles. *Rows* (Wilcom: Photo Flash): one dark thread in rows that
   zigzag as wide as the picture is dark (auto contrast). *Reef* (Wilcom: Reef PhotoStitch): one dark thread as a
   meander that never crosses itself, tight where dark and open where light. *Rows in every colour* (Wilcom: Color
   PhotoStitch): each thread colour sews rows as wide as it is present, with soft 2 mm edges where colours meet. The
   tones come from the shapes' colours, so extract a photo with several colours and remove its background.
0. **Knockdown** (towel / fleece presets): an open two-layer fill sewn first under small details and narrow fills,
   2 mm beyond them, in its own colour for the operator to load in the fabric's colour.
0. **Guide lines** (shape editor, Shapes view: "Draw guide line"): drag a line across a shape. *Guided satin* sews
   satin whose stitches cross that line at right angles from edge to edge (you decide how a curved satin turns -
   the practical form of Wilcom's column input / Ink/Stitch rails); *Florentine fill* makes every fill row a copy
   of the line, so the whole fill bends with it; *Carved fill* (Wilcom: user-defined split / carving stamp) puts the fill's
   needle points along the line so it shows pressed into the fill. *Flexi split satin* (Wilcom: Flexi Split) splits
   satin rungs on a pattern laid out in the column's own coordinates, so it bends with the column; *Motif column*
   repeats motifs as wide as the column. Both use the line if drawn, else the column's centre line. Without a line the shape is sewn the usual way. The line is a
   per-shape setting (undo, reset, merge and re-extract keep it).
0. **Decorative styles** (`stitch/decorative.py`): motif outline (a chain of motifs along a line or outline),
   hand stitch (uneven long stitches with tiny ones between), motif fill and paired motif fill (rows of motifs,
   alternate rows mirrored), pattern fill (Wilcom's program split: the fill's needle points sit on a tiled
   pattern - diamonds, squares, bricks, scales - which shows embossed), jagged fill (rough edges for fur and grass),
   raised satin (two padding layers under a 0.30 mm satin, up to 7 mm wide) and applique (placement line, machine
   stop to lay the fabric, tack-down zigzag, satin cover with overlapping corners). Stops are written as STOP
   commands; DST machines read them as a colour-change stop, and the worksheet lists them.
0. **Advanced fills** (stitch style for a colour or a shape, `stitch/fills.py`): offset (rings that follow the
   outline), spiral (one continuous line), contour (rows along a curved column), radial (rays from the centre, thinned
   towards it), shaded (accordion spacing: dense to light in one colour), stipple (meander that never crosses itself),
   maze (right-angled meander) and cross stitch (crosses on a grid, each diagonal line sewn there and back). Tatami
   fills get a texture choice: standard, half-step brick, diagonal twill or random. All use fixed seeds: the same shape
   always gets the same stitches.
   Options: *3D warp* bulges motif fills like a sphere (fisheye from the shape's middle); *Trapunto* makes fills
   travel between row groups only along their outline, so open fills show no travel lines.
0. **Decorative outlines and borders** (stitch style for a colour or a shape): double run, backstitch,
   stemstitch, sculpture run, zigzag border and E-stitch border. They are sewn along a line, or around a filled
   shape's outline and holes instead of filling it. Sharp corners stay needle points; spacing stays even on
   curves. Border width and gap are in the advanced settings (a stroked line of 1 mm or more keeps its width).
0. **Fabric preset** (stitch settings, default "General" = the standard settings): choosing a fabric (woven, denim,
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
   * Settings also survive preparing the design again (Size, Colours, Picture): shapes are re-matched by overlap,
     even at a new width.
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
   * satin "Automatic by width" uses Wilcom's circle tests: centre run under 3 mm, edge run under 4 mm,
     edge run + zigzag from 4 mm (shapes are also classed lettering / narrow / wide satin)
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
  fabrics.py          fabric presets (stitch settings): fill in compensation, density, foundation; backing + needle on the worksheet
  presets.py          settings presets: allowed keys + ranges, built-in presets by tier, each account's own presets (limits)
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
    outlines.py       decorative outlines/borders: double run, backstitch, stemstitch, sculpture run, zigzag, E-stitch
    underlay.py       named foundation types
    fills.py          advanced fills: offset, spiral, contour, radial, shaded, stipple, maze, cross stitch
    decorative.py     motif outline/fill, paired motif fill, pattern fill, jagged fill, hand stitch, raised satin, applique
    library/motifs.json  built-in motifs (designed for this project)
    guided.py         guide-line styles: guided satin, Florentine fill
    photo.py          photo stitch: the whole picture in one thread, rows zigzag wider where darker
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
  admin_api.py        the Super Admin console API (/api/admin/...), audited
  auth.py             accounts: passwords (scrypt), sessions (revocable, chosen length), roles, Super Admin from the secrets
  db.py               SQLite (DATA_DIR/app.db); append-only migrations on start; nested transactions via savepoints
  entitlements.py     what an account may do now: plan + overrides + status + usage + features (the one place to ask)
  subscriptions.py    plans, subscriptions, plan history, per-account overrides (payment-ready, no payments)
  usage.py            the design allowance (atomic, idempotent charges), usage records, AI metering, rate limits
  temp_projects.py    temporary designs: registry, ownership, deletion rules, sweep
  maintenance.py      background clean-up every 5 minutes (designs, sessions, expiries, retention)
  analytics.py        admin analytics aggregated in SQL
  app_settings.py     settings the admin can change (validated), audit.py: audit log
  user_ai.py          each account's AI settings, encrypted; used for that account's AI requests
  static/             index.html, app.js, auth.js, account.js (plan chip, My account, downloads), admin.js (console),
                      charts.js (small SVG charts), shapes.js, ai.js, presets.js (preset bar + dialogs), help.js,
                      style.css (no build step)
tests/                pytest suite
```

## API

Everything except `/api/config` and sign-in needs a session; a project is reachable only by its owner.

| Method | Path | Purpose |
|---|---|---|
| GET | `/api/config` | formats, defaults, AI provider availability, hoops, `session_choices` (public) |
| POST | `/api/auth/login` · `logout` | `{login (email or username), password, days (0 = browser session; or remember)}` → session cookie (public); logout deletes the session's designs |
| GET | `/api/auth/me` | the signed-in account |
| POST | `/api/auth/password` | `{current, password}`: change your own password |
| GET | `/api/me/account` · `/api/me/usage` | your plan, allowance, features, AI status and sessions · just the allowance |
| PATCH | `/api/me/profile` | `{display_name}` |
| DELETE · POST | `/api/me/sessions/{id}` · `/api/me/sessions/revoke-others` | sign out another device · all other devices |
| GET | `/api/presets` | built-in presets for your plan (locked ones without their settings) + the section of every preset key |
| GET · POST | `/api/me/presets` | your presets + `{count, limit, feature, can_save}` · `{name, description, sections}` → 201; 403 `feature_unavailable` / `preset_limit`, 409 `name_taken` (with `existing_id`) |
| PUT · DELETE | `/api/me/presets/{id}` | `{name?, description?, sections?}` · delete (your own presets only; allowed after a downgrade) |
| POST | `/api/me/presets/{id}/duplicate` | a copy ("… (copy)"), counts toward the limit |
| GET · PUT | `/api/me/ai` | your AI settings (keys only as `has_key` / `key_hint`); PUT keeps a saved key unless a new `api_key` or `remove_key` is sent |
| GET | `/api/me/ai/status` | which service and model you use, and whether it is ready (no keys) |
| GET · POST | `/api/admin/users` | Super Admin: list (`q, plan, status, usage_status, sort, page`) / create `{email, password, username?, display_name?, role?, plan_id?}` |
| GET · PATCH · DELETE | `/api/admin/users/{id}` | Super Admin: usage details / `{email?, username?, display_name?, active?, role?}` / delete |
| POST | `/api/admin/users/{id}/password` · `/sessions/revoke` · `/usage/reset` | new password · sign out everywhere · reset the usage counter |
| PUT | `/api/admin/users/{id}/subscription` · `/expiry` · `/overrides` | `{plan_id, keep_expiry, expires_at}` · `{expires_at}` · `{limit_type, generation_limit, features, limits, note}` |
| GET · POST · PATCH · DELETE | `/api/admin/plans[/{id}]` | plans (with `features` and `limits`) and the feature and limit catalogues |
| GET · PUT | `/api/admin/presets` | built-in presets with their tier and counts of saved presets · `{access: {id: "basic" or "premium" or null}}` |
| GET | `/api/admin/analytics` · `/api/admin/ai` | `?start=YYYY-MM-DD&end=…` aggregated analytics · AI overview |
| GET · PUT | `/api/admin/settings` | application settings (validated ranges) |
| GET | `/api/admin/audit` | `?q, before, user_id` |
| POST | `/api/admin/maintenance/run` | run the clean-up now |
| POST | `/api/projects` | upload (multipart `file`); replaces this browser session's previous design |
| DELETE | `/api/projects/{id}` | close the design: delete it now |
| POST | `/api/projects/{id}/release` | the page went away (beacon): delete after the grace period unless reopened |
| GET | `/api/projects/{id}/quota` | `{charged, usage, features}` before a download |
| POST | `/api/projects/{id}/extract` | `{options:{width_mm, num_colors, background, min_feature_mm, …}}` |
| PUT | `/api/projects/{id}/layers` | edit layers: enabled, thread color, stitch type, angle, order |
| POST | `/api/projects/{id}/layers/merge` | `{source, target}` |
| PUT | `/api/projects/{id}/objects` | per-shape settings `{ids, scope: selected\|colour\|all, stitch_type, angle, enabled, spacing_mm, pull_mm, bold}`. `"inherit"` = same as colour or design |
| POST | `/api/projects/{id}/objects/reset` | `{ids}` back to colour settings (empty = all shapes) |
| POST | `/api/projects/{id}/undo` | undo the last design change (one level) |
| POST | `/api/projects/{id}/stitch` | `{params:{fill_spacing_mm, underlay, smart_direction, …}}` → preview data + stats, and each shape's stitch style and direction as sewn; with smart reduction also `comparison` + `original_ref` |
| GET | `/api/projects/{id}/stitch/original?ref=…` | the stitches before smart reduction for that `original_ref` (Optimised / Original, loaded on demand; 404 when the stitches changed since) |
| GET | `/api/projects/{id}/export/{fmt}` | `dst exp u01 pes jef vp3 svg png txt zip wilcom`. `wilcom`: the package for continuing in Wilcom (DST + EPS/SVG artwork + worksheet + how-to, a download of its own). For `zip`, `?formats=dst,pes,txt` picks the files (default: DST, EXP, PES, SVG, PNG, worksheet). The first stitch-output download of a design counts against the allowance (403 `quota_exceeded` / `feature_unavailable` with an explanation) |
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
  filled in one direction per section. Curved fills (contour, spiral, radial) exist as stitch styles to choose per
  colour or shape; the automatic choice does not pick them by itself.
* Not built: 3D puff (foam) and sequins. Motif, pattern, contour / spiral fills, raised satin and appliqué are
  available as stitch styles.
* Design-wide settings are limited to the ranges the editor offers (`models.STITCH_RANGES`,
  `ingest/raster.RASTER_RANGES`); values outside are clamped by the server, and the 7 mm stitch cap cannot be raised.
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
