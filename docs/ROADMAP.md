# Editor redesign: roadmap, status and plan

The working plan for turning the editor into a professional embroidery workspace. It records what is done,
how the new editor code works, the decisions taken, and detailed specs for every remaining phase, so the
work can be picked up on any machine. Read `CLAUDE.md` and `README.md` ("The editor", "How the stitch
engine works") first.

Last updated: 15 Nov 2026 (after Phase 2, commit `cbb43a5` on `main`). Nothing of this has been deployed
yet: deploys run on version tags only.

---

## 1. Status at a glance

| Phase | Content | Status | Commit |
|---|---|---|---|
| 0 | Safety net + foundations: request queue, canvas renderer, server lock | **Done** (browser tests run before every release, §8) | `2fdefb4` |
| 1 | One workspace: 3 panes, bottom bar, Shapes/Stitches, auto-update, export dialog, icons | **Done** | `2fdefb4` |
| 2 | Control quality: undo for settings, changed markers + reset, style settings, hoop outline, Wilcom package, plan cache | **Done** | `cbb43a5` |
| 3 | Presets: built-in + custom, plan limits, admin | **Done** – sew-outs per built-in preset still open (§4.9) | branch `phase3-presets` |
| 4 | Professional extras: objects list, context menu, floating toolbar, grid/rulers, density view, resizable panels, colour merge, split compare | **Done** (A: shapes list, shortcuts, stitch types, full screen, lazy original; B: shape menu, selection toolbar, grid + rulers, density view, resizable panels; C: merge similar colours, split view, 100 % calibration) | – |
| 5 | Thread catalogs, more formats, machine speed, theme toggle | **Done** – sew-outs of PEC / XXX and real thread charts still open (§6) | – |
| 6 | Editor clean-up: fix bottom bar overflow, view bar, header actions, compact panels, help on demand | **A–E done** (A: bar overflow, figures, selection panel, thread rows; B: header actions, view bar, Show menu; C: presets apply on choose, Fabric & thread section, compact numbers, help on demand; D: right panel – Selection heading, Not sewn group, similar-thread note, four figures + More details, Checks first; E: one heading style, spacing scale, fewer borders, icon-only Export below 900 px) – see [EDITOR_UX_PLAN.md](EDITOR_UX_PLAN.md) | `2ed4a3a` (main) |
| 7 | App UX: Export that finishes the job, a size question before the first preparation, first-run guide, phones and tablets, account / admin consistency | **Planned** – see [APP_UX_PLAN.md](APP_UX_PLAN.md) (§6 has 4 decisions) | – |

Before the first release tag:
1. Click through on a real design (desktop, tablet, phone).
2. Have someone with Wilcom EmbroideryStudio open a "Continue in Wilcom" ZIP (checklist in §7.4).
3. `python -m pytest -q -W error::DeprecationWarning` and the smoke tests (`python tools/smoke/run_all.py`, §9) green -
   the release pipeline runs both and does not deploy otherwise.
4. A physical sew-out of every built-in preset (§4.8) before calling the presets production values.

---

## 2. What is done (Phases 0–2)

### Workspace (Phase 1)
- **Upload page** → **one editor** (`#editor`): left settings panel (Size, Colours, Picture or Artwork,
  Stitches, Smart assistant), centre preview, right panel (Selection, Threads, Design info, Checks), fixed
  bottom bar (undo/redo, preview style, Optimised/Original, dark fabric, sewing simulation, zoom, status,
  Export). Steps 2–4 and the "Looks good – create stitches" gate are gone.
- **Shapes / Stitches** view switch (`state.pane`). Selection is shared. Outline tools (paint, erase, lasso,
  guide line, draw direction) switch to Shapes by themselves. **Original** overlays the uploaded picture in both
  views, aligned in mm (`Shapes.reference()`).
- **Auto-update**: settings changes re-extract or re-stitch after a short delay, one request at a time, newest
  values win; designs slower than 3 s switch to a manual **Update** button; Auto-update can be turned off.
  Edits never cover the screen (only upload/open/close and the AI tasks use the blocking overlay).
- **Threads list** replaces the 10-column colour table (expand a row for style, direction, order, merge;
  drag-merge kept).
- **Export dialog**: grouped files, summary (size, hoop fit, stitches, time), checks, allowance note, plan
  locks; flushes pending updates first so downloads always match the screen.
- **Sheets**: right panel < 1200 px, left panel < 900 px; bottom bar wraps on phones.
- **Canvas**: stitches drawn once per view into an off-screen canvas; quick per-colour Path2D version while
  zooming/panning; sewing simulation draws only new stitches.
- **Server**: `service.stitch` / `service.export` serialised per project (`_project_lock`).
- Icons: inline SVG sprite in `index.html` (no emoji, no icon library).

### Control quality (Phase 2)
- **Undo/redo for everything** (`History` in `app.js`): design changes (server stack, 50 steps) and stitch
  settings (client snapshots) in one timeline; an extract step also restores the picture/size settings; undo
  button names the step; saved in `sessionStorage` (survives reload).
- **Changed markers + reset**: compared with `state.baseline` (stitch defaults + picture settings after the
  upload's smart analysis). Dot on the setting and its section, ↺ per setting, "Reset" per section, "Reset all".
- **Style settings**: style-only parameters (`STYLE_PARAMS`) shown only when the design uses those styles;
  "Show the settings of every stitch style" lists them all.
- **Hoop outline** in both views, centred like the machine file (DST origin = design centre); red + Checks
  warning when too large; Fit includes the hoop. Hoop width now uses the design's proportions (was the
  picture's – a portrait logo did not fit).
- **Continue in Wilcom** export (`service.wilcom_package`, `export/vector.design_to_eps`) – see §7.
- Plan cache: 6 plans per design (3 setting states). Static files revalidated on every load (`no-cache` +
  ETag) so a deploy never mixes a new page with cached old scripts.

### Measured performance (dev box, `example-logo.png`)
| Operation | 100 mm (~7.8k stitches) | 250 mm (~35k stitches) |
|---|---|---|
| Extract (prepare design) | 1.3 s | 1.4 s |
| Stitch, optimisation on (2 plans + coverage check) | 0.9–1.4 s | 2.3 s (4.0 s smart direction) |
| Same settings again (cache) | 0.1 s | 0.26 s |
| Preview JSON (ops + original_ops) | 0.3 MB | 1.3 MB |

---

## 3. How the new editor code works (read before changing it)

All in `embroidery_app/static/` (plain scripts; `theme.js` in `<head>`, then: help, auth, charts, account, admin, shapes, shapetools, ai, presets, workspace, app).

| Piece | Where | Job |
|---|---|---|
| `work(label, fn)` | app.js | Serialises every server call that changes the design; non-blocking; drives the status bar. Never call `work()` inside another `work()` (deadlock). |
| `Jobs` | app.js | Keeps the preview current: `Jobs.extract()`, `Jobs.stitch({report, force})`, `flush()`, `idle()`, `reset()`, manual mode (`SLOW_STITCH_MS`). Extract runs before stitch. |
| `History` | app.js | Undo timeline. Call `History.designChange(label)` after a successful design save, `History.stitchChange(label?)` after changing stitch-form values in code. Extracts record themselves (`History.extracted`). |
| Baseline / markers | app.js | `state.baseline`, `setBaseline()`, `markModified()`, `resetSettings(keys)`, `EXTRACT_IDS`, reset buttons added by `addResetButtons()`. |
| `STYLE_PARAMS`, `updateStyleParams()` | app.js | Which stitch setting belongs to which style (mapping taken from stitch/*.py). |
| `Render` | app.js | Canvas: `request(moving)`, `reset()`, `thumbnail(canvas)`, `now()`; `fit()`, `fitView()`, `stitchBounds()`, hoop helpers. |
| `Export` | app.js | Export dialog; the Wilcom package is exclusive (a ZIP of its own). |
| `setPane()`, sheets, zoom | app.js | Shapes/Stitches, side sheets, 100 % = 96 px/inch (approximate). |
| `Shapes` | shapes.js | Selection + shape panel (right panel), edit tools, SVG view, `reference()`, `fit()`, `zoomBy()`, `pxPerMm()`, `objects()`. |
| Help | help.js | Every new control needs a `HELP` entry (`modified`, `style_settings`, `hoop_outline`, `auto_update`, `view_switch` exist). |

Rules kept: entitlements only via `entitlements.effective`; every project route owner-checked; DB changes
appended to `db.MIGRATIONS`; deterministic stitching (golden plans untouched by UI work); AI optional.

---

## 4. Phase 3 – Presets (next, about 2–3 weeks)

Goal: reusable settings. Built-in presets everyone (by plan) can apply; custom presets paying plans can save.

### 4.1 Decisions (taken 2026-11-16: all four recommended defaults below)
1. **Built-in preset access**: by tier – `presets_basic` / `presets_premium` features – plus an admin
   override moving one preset between tiers **[recommended]**, or a full preset × plan matrix?
2. **Custom preset limits per plan**: **[Free 0, Basic 5, Pro 25]** – existing plans must get values in the
   migration.
3. **Downgrade**: presets above the new limit or without `custom_presets` – **[kept and can still be applied,
   renamed, deleted; no new ones]**, read-only, or hidden?
4. (Smaller) One preset type with optional sections **[recommended]** vs separate image/stitch presets;
   built-in values maintained in code + tests **[recommended]** vs admin-editable.

### 4.2 Model
A preset is a **partial** settings record: only the keys it contains are applied (like `fabrics.py`).
```json
{ "schema": 1,
  "sections": {
    "stitch":  { "fill_spacing_mm": 0.40, "satin_spacing_mm": 0.35, "smart_direction": true },
    "fabric":  { "fabric": "pique", "thread_weight": 60, "pull_compensation_mm": 0.30 },
    "image":   { "background": "auto", "sharpen": 1, "min_feature_mm": 0.2 },
    "size":    { "width_mm": 90 }
  } }
```
- Sections map to the left-panel sections. `size` / colour count are off by default when saving.
- Validation on save **and** apply: whitelist of `StitchParams` / `RasterOptions` keys the UI exposes,
  clamped through `StitchParams.from_dict` / `_options(RasterOptions, …)`; unknown keys dropped and reported.
- Never in presets: per-shape settings, layer colours/names, guide lines, geometry.
- Not applicable: an image section on a vector design is skipped with a note; style settings for unused
  styles are applied silently.
- A preset with `fabric` applies the fabric's values first, then its own values.

### 4.3 Storage (DB migration, appended to `db.MIGRATIONS`)
```sql
CREATE TABLE user_presets (
  id          INTEGER PRIMARY KEY,
  user_id     INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  name        TEXT NOT NULL,              -- 1..60 chars
  description TEXT NOT NULL DEFAULT '',   -- 0..300 chars
  settings    TEXT NOT NULL,              -- validated JSON (above), <= 16 KB
  schema      INTEGER NOT NULL DEFAULT 1,
  created_at  REAL NOT NULL,
  updated_at  REAL NOT NULL,
  UNIQUE (user_id, name)
);
CREATE INDEX user_presets_user ON user_presets(user_id);
ALTER TABLE plans ADD COLUMN limits TEXT NOT NULL DEFAULT '{}';
ALTER TABLE entitlement_overrides ADD COLUMN limits TEXT NOT NULL DEFAULT '{}';
```
JSON column (not key/value rows): settings mirror `StitchParams`, which grows often; presets are always read
and written whole. Privacy: only settings + name + description; admin views show counts only; deleted with
the account.

### 4.4 Entitlements (extend `entitlements.py`, no separate system)
| Name | Kind | Default | Enforced in |
|---|---|---|---|
| `presets_basic` | feature | on | `GET /api/presets` (settings only for allowed tiers) |
| `presets_premium` | feature | off | same |
| `custom_presets` | feature | off | `POST/PUT/DELETE /api/me/presets` |
| `max_custom_presets` | **limit (new concept)** | 0 | create + duplicate, in one write transaction |

- New `LIMITS: dict[str, Limit]` next to `FEATURES` (key, label, help, default, min, max). `effective_many`
  merges limits like features: default → plan → override → role (Super Admin = unlimited, `None`).
  `Entitlement.limits` + `limit(name)`.
- Admin plan dialog and override editor get a "Limits" fieldset generated from `LIMITS` (no hardcoded numbers
  in the front end); every change `audit.record`.
- Count enforcement: `BEGIN IMMEDIATE`, count, compare, insert (same pattern as `usage.generation`). Error:
  `EntitlementError(code="preset_limit")`.
- Premium built-in **values** are never sent to accounts without the tier (list shows name/description + lock).
- Optional (decision 1): `app_settings.preset_access = {preset_id: tier}` to move a preset between tiers.

### 4.5 API
```
GET    /api/presets                   built-ins: [{id, label, description, tier, kinds, locked, sections?}]
GET    /api/me/presets                [{id, name, description, sections, schema, updated_at}] + {count, limit}
POST   /api/me/presets                {name, description, sections}  -> 201 | 403 preset_limit / feature_unavailable
PUT    /api/me/presets/{id}           {name?, description?, sections?}   (owner-checked)
POST   /api/me/presets/{id}/duplicate                                     (counts toward the limit)
DELETE /api/me/presets/{id}                                               (owner-checked)
```
Pydantic bodies with length limits; rate-limited like the other account routes.

### 4.6 Built-in presets (code: new `embroidery_app/presets.py`, beside `fabrics.py`)
Starting values – all inside the UI ranges; **not production values until tests + sew-outs pass (§4.8)**.
"Transparent background" was dropped (it is one setting); "Mixed stitch" folded into Standard.

| Preset | Tier | Sets | Use |
|---|---|---|---|
| Standard | basic | fill 0.40, satin 0.40, pull 0.20, fill length 3.5, smart direction on, underlay edge_tatami / edge, optimise on | most logos, woven |
| Small text & logos | basic | 60 wt, satin 0.35, pull 0.15, running length 2.0, satin underlay center; image: text mode sharp (min feature 0.2, min area 0.3) | left chest, lettering < 6 mm |
| Fine detail | premium | fill 0.38, fill length 3.0, satin 0.36, smart direction; image: sharpen 1, min feature 0.2 | detailed art 80–150 mm |
| Dense coverage | premium | fill 0.35, satin 0.35, fill underlay double_tatami, pull 0.25 | dark / textured fabric |
| Lightweight | basic | fill 0.50, satin 0.45, fill underlay edge, pull 0.15 | thin fabric, fewer stitches |
| Large design | premium | fill 0.45, fill length 4.0, vary direction on, trim threshold 3.0 | > 200 mm backs |
| Satin-forward | premium | satin max width 12, smart direction on | bold lettering / columns |
| Fill-forward | premium | satin max width 5, smart direction off | flat textured look |
| Clean logo (image) | basic | background auto, sharpen 1, denoise 2, min area 1.5 | scanned / JPEG logos |
| High contrast (image) | basic | contrast +30, sharpen 1, denoise 2 | faded pictures |
| Soft colours (image) | premium | contrast −10, denoise 3, min area 2.0 | pastel art |
| Photo (image + stitch) | premium | background none, denoise 1, photo stitch on, style rows, row gap 1.0 (also needs `photo_stitch`) | portraits |

The smart analysis after upload stays the baseline; it may *suggest* a preset, never auto-apply it.

### 4.7 UI
- **Preset bar** fixed at the top of the left panel: picker (built-in groups, "My presets · 3 of 10", Reset to
  app defaults, Manage…), "modified" badge when settings differ from the applied preset, "Save as preset…".
- Applying: fill the controls (reuse `setStitchValues` / `setExtractValues`), one `History` step, the baseline
  becomes the preset (markers then show changes from the preset), toast "Applied *X* · 6 settings changed ·
  Undo", `Jobs.extract()` / `Jobs.stitch()` as needed. No hover preview (would trigger stitching).
- **Save dialog**: name, description, include sections (stitch, fabric & thread on; image; size & colours off),
  summary (settings, changed from defaults, "3 of 10"), "Only settings are saved, never your picture or design".
- **Manage**: list (built-in, then mine), Apply, Rename/Edit, Update from current settings, Duplicate,
  Delete (inline confirm); name clash → Replace / Keep both.
- **Restriction states**: locked built-ins show a plan badge + popover ("included in Pro; ask your
  administrator"); Save shows a lock when `custom_presets` is off; at the limit Save is disabled with
  "You have 10 of 10 presets"; all read from `/api/me/account`, enforced again on the server.
- `account.js`: "My presets" card on the account page. `admin.js`: limits fieldset in plan + override dialogs.
- New help entries in `help.js`.

### 4.8 Tests and acceptance
- API tests (`tests/accounts.py`, `real_login`): free account cannot create (403 `feature_unavailable`);
  limited account cannot exceed N with parallel requests; premium values absent for basic accounts;
  downgrade keeps presets usable; account deletion deletes presets; admin changes audited; existing plans
  migrate to presets_basic on, rest off.
- Preset regression tests: each built-in on fixtures (`logo_rgba`, `svg_text` + new small-lettering, large and
  photo samples): no stitch > 7 mm, machine-check risks ≤ Standard's, stitch count in an expected band.
  Separate golden file – never touch `tests/golden/plans.json.gz`.
- Physical sew-out per built-in preset before calling it production-ready (product owner).
- Schema versioning: `schema` per record, upgrade on read, unknown keys skipped and reported.

---

### 4.9 What was built (and where it differs from the plan above)
- Backend: `presets.py` (keys + ranges, `clean`, `BUILTINS`, tier overrides, own presets), migration 4 in `db.py`,
  `entitlements.LIMITS` + `limit_of`, plan / override `limits` (`subscriptions.py`, admin routes), routes in `api.py`
  and `admin_api.py` (`GET/PUT /api/admin/presets`, audited as `presets.access`).
- Seeded plans: Free = built-in basic presets, 0 own; Basic = + save own, 5; Pro = + premium presets, save own, 25.
  (The acceptance line "existing plans migrate to presets_basic on, rest off" was read together with decision 2:
  Basic and Pro need `custom_presets` on for their limits to mean anything.)
- Downgrade (decision 3): PUT and DELETE stay allowed without `custom_presets`; only POST and duplicate are refused.
- Front end: `static/presets.js` (bar, save + manage dialogs), account card, admin limits + tier table, help entries.
  The picker is a select + **Apply** button (a select that applies on change would apply while arrowing through it).
  "Reset to app defaults" resets the stitch settings only (picture settings come from the upload's analysis).
- One undo step: when a preset changes picture settings, the extract step also carries the stitch change
  (`Jobs.extract({step})`, `History.extracted(..., step)`); otherwise one stitch-settings step.
- Built-in values changed from §4.6:
  - **Standard**: smart direction left off. It is still opt-in until it has been sewn.
  - **Dense coverage**: fill spacing stays 0.40 (satin 0.35, double lattice foundation, pull 0.25). Fill rows at
    0.35 mm gave 108 short-stitch markers on the test logo against 4 for Standard (0.38 still gave 19).
- Tests: `tests/test_presets.py` (17: validation, tiers, limits incl. 10 parallel creates, downgrade, account
  deletion, owner checks, audit, migration, every built-in ≤ 7 mm with risks ≈ Standard's, stitch-count band) and
  fingerprints in `tests/golden/presets.json` (±3 %). Browser: `tools/smoke/presets.mjs` (39 checks).
- Suggestion: `presets.suggest` picks a built-in from the upload analysis (photo → Photo, lettering under 6 mm →
  Small text & logos, wider than 200 mm → Large design, grainy / low-resolution → Clean logo), only among presets the
  account can apply; `/ai/suggest` returns it as `preset` and the smart-settings box offers it with an Apply button.
- Locked built-ins: a "🔒 Premium" badge (picker hint and Manage dialog) opens a popover naming the plans that include
  them ("included in the Pro plan"; `plans` in `GET /api/presets`). Escape or a click elsewhere closes it.
- Checked on tablet (1024 px) and phone (390 px): the bar fits the settings sheet, dialogs and popover fit the screen.
- Open: a physical sew-out of every built-in preset.

## 5. Phase 4 – Professional extras (about 2–3 weeks; each item ships on its own)

| Item | Spec | Notes |
|---|---|---|
| Objects list | Tab in Threads: every shape grouped by colour, keyboard navigable, selects on click (keyboard path to every shape – accessibility) | **Done**: "Shapes" tab, WAI-ARIA listbox (↑ ↓ Home End PgUp PgDn, Enter = only this, Space = add/remove, Ctrl+A, Ctrl/Shift+click), tab remembered |
| Context menu | Right-click / long-press on a shape: Stitch style ▸, Direction ▸, Select same colour, Don't stitch, Reset | **Done** (`shapetools.js`): right-click, long-press (touch), Menu key / Shift+F10; WAI-ARIA menu with submenus (→ ← Esc, type-ahead); a right-click inside a multiple selection keeps it; same calls + undo as the panel (`Shapes.tools`) |
| Floating selection toolbar | Next to the selection box: style, direction, thickness, don't stitch, "Details →"; repositions to stay on screen; hidden while panning | **Done**: above (or below) the selection, inside the preview; one tab stop, ← → between controls; hidden while zooming / dragging, editing, with the menu open and below 900 px (the panel sheet has everything). Event-driven: follows stitch frames, the shapes view box, pane switches and resizes (`viewMoved()`); `shape_outlines` now sends each shape's `bbox` |
| Grid + rulers | mm rulers on two edges, grid 10 mm (1 mm when zoomed in); toggles in the overlay toolbar | **Done** (`workspace.js`): one overlay for both views (mm from the stitch view or the SVG screen matrix), rulers on the bottom + left edges (the floating bars are at the top), 1 / 10 mm lines by zoom, repainted only when the mapping changes |
| Density view | Fifth preview mode: needle points per mm² on a grid, colour scale; exact counts, visual interpretation | **Done**: counted like `safety.machine_check` (cell + limit from `/api/config` `density`); 4 blue sequential bands up to the limit + status red above; legend in words incl. mm² over the limit; hover shows the exact figure |
| Stitch type distribution | Design info: fill / satin / running / underlay / travel / tie counts (exact, from `kind`) | **Done** (follows Optimised / Original) |
| Resizable / collapsible panels | Drag inner edge (240–420 px), `[` / `]` to collapse, remembered in localStorage, "Reset workspace" | **Done**: drag or keyboard separator (← → ±16, Shift ±64, Home / End, Enter), open / close button and `[` / `]`, double-click = standard width, remembered per browser, Reset workspace in the shortcuts dialog; only while a panel sits beside the preview |
| Full screen | Fullscreen API toggle (feature-detected) | **Done** (whole app, so dialogs still show; headless browsers refuse it, so the smoke test checks the note) |
| Keyboard shortcuts | F fit, 1 = 100 %, 2 = 200 %, +/−, Space-drag pan (both views), S / A panes, O original, V B E L G tools, Enter = Update (manual mode), Ctrl+E export, ? sheet; only when focus is not in a field | **Done**: each key presses its button (same code path); **S toggles** Shapes / Stitches (no second pane key); not in fields, dialogs, lists or tabs; ⌨ button + `?` list |
| Merge similar colours | Server suggests pairs below a CIEDE2000 threshold; preview list; apply via existing merge (undoable) | **Done**: `colors.ciede2000` (checked against Sharma et al. 2005); `GET …/layers/similar?max_de=` (stitched colours only, smaller into larger, no chains, range in `service.SIMILAR_RANGE` via `/api/config` `similar`); `POST …/layers/merge-many` = one undo step, same result as single merges; dialog from the threads list |
| Split before/after | Draggable split: original picture vs stitches, aligned via `reference_info` | **Done**: Split in the Original bar (stitch view): the reference at full strength left of the handle, the stitches (without the faded copy) right; slider handle (drag, ← → 5 %, PgUp / PgDn, Home / End), remembered per browser |
| 100 % calibration | Optional: match a card on screen, stored per browser | **Done**: Calibrate 100 % (shortcuts list): match a bank card (ISO ID-1); stored as device pixels per mm, so browser zoom keeps it right; Standard size undoes it |
| Lazy `original_ops` | Send the original stitches only when Compare is opened (`GET …/stitch/original`) – halves the preview payload | **Done**: `original_ref` ties the request to the stitches on screen (404 when stale); test logo 83 → 48 KB per update (−42 %) |

---

## 6. Phase 5 – built (Nov 2026)

- **Thread catalogs** (`threads.py`): charts are **files in `THREAD_CHARTS_DIR`** (default
  `DATA_DIR/thread-charts`), none bundled - brands own their charts, and the good public ones (Ink/Stitch's
  palettes) are GPL-3 while the app has no third-party copyleft. GIMP `.gpl` (Ink/Stitch's format works as is)
  or CSV (`number,name,hex` or `r,g,b`); re-read when the folder changes; 2 MB / 5000 threads / 60 charts max.
  Nearest thread per colour by CIEDE2000. The design setting `thread_chart` changes only figures (not in the
  plan cache key, like `machine_spm`): Thread order list, worksheet (brand, number, name, closeness + "check
  against the real card") and **VP3** thread records. **PES is written as version 1, which has no thread
  records**; version 6 would carry them but changes every PES file - only after a sew-out on older and newer
  Brother machines.
- **More machine formats**: **Brother PEC** and **Singer XXX** (pystitch). Jumps split to the format limit,
  trims kept (`tests/test_export_limits.py`); the export dialog says "sew a test piece first" until tested.
  pystitch also writes `sew tbf qcc pmv edr inf col csv json gcode plt` - add on demand the same way.
- **Machine speed** (`machine_spm`, 300-1500, default 750, Advanced stitch settings): time estimate only, the
  cached plan is reused; worksheet shows the speed and a **bobbin estimate** (≈ ⅓ of the top thread).
- **Theme**: System / Light / Dark in the account menu (`theme.js`, loaded in `<head>` so there is no flash;
  `<html data-theme>`, each dark block also answers to the choice; `color-scheme` for form controls; canvases
  that read theme colours repaint on `themechange`). **Woven** fabric texture for the stitch preview
  (cosmetic, remembered per browser).
- Still open: **EMB via Wilcom's Embroidery Web API** only with a business case (paid, cloud, no better
  fidelity) – §7. Sew-outs of PEC and XXX; real thread charts (from the supplier, or checked public ones).

---

## 7. Wilcom findings (researched Nov 2026)

### 7.1 Finding
The app **cannot write Wilcom's editable EMB**: closed, unpublished format; pyembroidery / pystitch /
Ink/Stitch / libembroidery don't write it. The only official way is Wilcom's paid cloud **Embroidery Web API
(EWA)**, which converts machine files to EMB and builds designs from "recipes" – it does not digitize
arbitrary shapes. Never label DST (or anything else) as an editable Wilcom project.

### 7.2 What Wilcom does with what we can write
| Format | Category | Wilcom on open | Kept | Lost |
|---|---|---|---|---|
| DST | machine | object recognition (on by default) rebuilds Satin, Tatami, Program Split; rest = manual stitch blocks (scale ±10 %) | stitches, trims, stops | colours (Wilcom default palette), objects, settings |
| PES / VP3 | machine + colours | recognition | + thread RGB (whether Wilcom uses it: **unverified**) | objects |
| EXP / U01 / JEF | machine | recognition | stitches | colours (JEF palette index only) |
| EPS / DXF | vector artwork | Import Graphic, then Convert / Auto-Digitize → fully editable objects | shapes, colours | stitch settings |
| SVG / AI / PDF | vector artwork | probably via bundled CorelDRAW (**unverified**) | shapes | stitch settings |
| DST extended header (`TC`) / INF | colour info | **unverified**, probably ignored | – | – |

### 7.3 What was built
`GET /api/projects/{id}/export/wilcom` → `<name>_for_wilcom.zip`: `<name>.dst`, `<name>_artwork.eps`,
`<name>_artwork.svg`, `artwork_by_colour/NN_<colour>_<style>_<angle>.eps`, `<name>_worksheet.txt`,
`Open in Wilcom - read me.txt` (states clearly it is not EMB). Counts as one design download; allowed on
DST-only plans; not combinable into the normal ZIP.

### 7.4 Verification checklist (needs a Wilcom licence, e4 or later)
1. Open the DST: recognition on, objects editable, size correct, centred.
2. Import `_artwork.eps`: correct size in mm, colours, holes (even-odd), lines.
3. Convert / Auto-Digitize the imported artwork.
4. Open the PES of the same design: are thread colours kept?
5. Only after 1–4: consider DST extended header / INF.
Fix wording in `FORMATS` notes, `help.js` and the read-me with what is confirmed.

Sources: docs.wilcom.com (formats-5, convert-3, convert-4, Insert_vector_graphics, automatic-7),
help.wilcom.com (machine-files-colors, default colour palette, ewa-capabilities, supported-formats),
github.com/EmbroidePy/pyembroidery, inkstitch.org/docs/file-formats.

---

## 8. Open items and risks

- **Browser tests in CI**: done without a new dependency - `.github/workflows/browser-tests.yml` runs
  `tools/smoke/run_all.py` (the runner's Chrome, Node 22) before every release (`deploy.yml` needs it) and on
  demand (Actions > Browser tests > Run workflow); logs + screenshots are kept as an artifact for 7 days.
- **Server load**: auto-update multiplies stitch runs (two plans each with optimisation). Watch EC2 CPU after
  release; the fallback exists: **Admin > Settings > Server load > Update the stitch preview automatically**
  (off = every editor uses the Update button; editors opened afterwards follow it).
- **History vs server**: if a design change reaches the server but the reply is lost, the client timeline
  misses one step; `History` falls back to the server's undo stack and drops steps the server no longer has.
- **Running stitch on tight curves**: a hole or dot smaller than about one stitch length becomes a polygon
  (a 2 mm hole at 2.5 mm stitches is a triangle) - the needle points are on the outline, the thread cuts
  across. Hand stitch, double run and flexi split were fixed (Nov 2026, found by `HYPOTHESIS_PROFILE=deep`,
  kept in `tests/test_holes_and_curves.py`); shortening plain running stitches on curves would change every
  design's outlines (golden plans) - decide after a sew-out of small round lettering.
- **AI tasks** keep the blocking overlay on purpose: clean-up replaces the picture (edits meanwhile would be
  lost), advice and the stitch check describe the design as it was when asked.
- **100 % zoom** is approximate (96 px/inch) until the user calibrates it (per browser and screen); the tooltip says which.
- **Static caching**: scripts are revalidated every load; if a CDN is added later, use versioned file names.

---

## 9. How to test

```bash
python -m pytest -q -W error::DeprecationWarning      # everything CI runs (443 tests with Phase 3)
```

Browser smoke tests (no dependencies; Node 22+ and Chrome or Edge), see `tools/smoke/README.md`:
```bash
# 1. a throwaway server with its own data folder and admin login
DATA_DIR=/tmp/smokedata SUPER_ADMIN_EMAIL=smoke@example.com SUPER_ADMIN_PASSWORD=SmokeTest-12345 PORT=8765 python run.py
# 2. in another shell
node tools/smoke/workspace.mjs ./smoke-out     # Phase 1: 45 checks, screenshots in ./smoke-out/shots
node tools/smoke/controls.mjs  ./smoke-out     # Phase 2: 26 checks
node tools/smoke/presets.mjs   ./smoke-out     # Phase 3: 39 checks
node tools/smoke/extras.mjs    ./smoke-out     # Phase 4 batch A: 33 checks
node tools/smoke/batchb.mjs    ./smoke-out     # Phase 4 batch B: 39 checks
node tools/smoke/batchc.mjs    ./smoke-out     # Phase 4 batch C: 28 checks
node tools/smoke/phase5.mjs    ./smoke-out     # Phase 5: 26 checks (server with THREAD_CHARTS_DIR, see tools/smoke/README.md)
```
Use a fresh browser profile (the scripts delete theirs on start) – a cached `app.js` gives false failures.
