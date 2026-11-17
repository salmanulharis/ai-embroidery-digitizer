# Editor UI/UX clean-up: findings and fix plan

Status: **Batch A done** (branch `editor-ux-cleanup`, see "Batch A: what was built" below); B–E not started (written 2026-11-17 from a 1910 × 1035 screenshot of a real design plus a read of
`index.html`, `style.css`, `app.js`, `shapes.js`, `help.js`). It's Phase 6 in [ROADMAP.md](ROADMAP.md) §1.

The editor has every feature it needs. The trouble is that each feature was added where there was room at the time,
so related controls are split across five places and the same facts appear up to three times. This plan doesn't
remove any feature. It **moves, groups and hides until needed**, and fixes one real layout bug.

---

## 1. Findings

Each finding lists what the screenshot shows, the cause in the code, and its impact.

### F1. The bottom bar overflows, so text overlaps the zoom buttons (a bug, even at 1910 px)
- **Seen:** `7,3…stitch 182% wn · colour 1 of 1` is drawn on top of the zoom buttons. The status reads `S…` and the facts read `10…`.
- **Cause:** `.ed-bar` is `flex-wrap: nowrap` with 7 groups and about 25 controls. Every `.ed-grp` is `flex: none`. Only
  `.ed-player` can shrink (`flex: 1 1 140px`), but its `#scrubInfo` is `white-space: nowrap` (about 260 px:
  "7,354 stitches sewn · colour 1 of 1"). The text overflows its group and paints over `.ed-zoom`. `.ed-status` is
  squeezed to ellipses. The breakpoints at 1499 and 1299 px hide some parts, but 1500 px and up has no rule that fits.
- **Impact:** the zoom % can't be read, the status can't be read, and the bar looks broken.

### F2. Floating controls cover the design
- **Seen:** the `Original | Grid | Woven` pill (top right) sits on the Arabic lettering. `Shapes | Stitches` (top left)
  sits on the ring.
- **Cause:** `.ed-float` boxes are `position: absolute` over `.ed-stage`. "Fit" uses the whole stage, so it doesn't
  leave room for them. Only the shapes view pads for `--tools-top`; the stitches canvas has no such padding.
- **Impact:** the part of the design you're checking is hidden, and the pills read as part of the artwork.

### F3. "How the preview looks" is spread over three places
| Control | Where it is now |
|---|---|
| Shapes / Stitches (the main mode) | Floating pill, top left |
| Original, Hoop, Grid, Directions, Woven | Floating pill, top right |
| Realistic / Direction / Types / Check / Density | Bottom bar |
| Optimised / Original | Bottom bar |
| Dark fabric | Bottom bar |
| Zoom, fit, full screen | Bottom bar |

A user who wants to change how the preview looks has to search three places. Two labels also clash: "Original"
means the reference picture at the top and the unoptimised stitches at the bottom.

### F4. Too many help icons
- About **22 "?" buttons are visible** in the screenshot. The Selection heading has two side by side
  (`shape_select`, `shape_menu`). Some come from `index.html` (44 `data-help`) and the rest from `Tip.icon` calls in
  app.js (14) and shapes.js (17).
- **Impact:** each row ends in a ?, which breaks the scan line and doubles the number of things on screen. The help
  text is good; the problem is that it's always visible.

### F5. The left panel is long, mixes kinds of setting, and its main field has no visible label
- The **preset bar** needs 3 rows and 4 buttons (`Preset` select, `Apply`, `Save as preset…`, `Manage…`). It's the
  only part of the editor that needs an explicit Apply; everything else updates by itself.
- The **Stitches** section opens with the Fabric select (the screenshot shows "General (standard settings)" with no
  label above it, because the label has scrolled away). Then the order is a toggle (Smart stitch reduction),
  thread weight, 4 numbers, then 3 more toggles. So material, behaviour and numbers are interleaved
  (`buildStitchForm`, app.js:858).
- **Every numeric field takes about 92 px:** a label row, then a full-width input showing "0.4". Units are only in
  the label text. Seven core settings fill the whole panel height.
- **All sections start open** (Size, Colours, Picture, Stitches), so the panel is a long scroll from the start.
- "Fabric" and "Thread" are material choices and belong together. Today they sit inside "Stitches", with the
  thread brand (`thread_chart`) between them.

### F6. The right panel repeats itself and contradicts itself
- **The empty Selection state** shows "Select by colour" chips (`Navy 157`, `Navy 247`). The Threads list shows the
  same threads straight underneath. It also shows 2 lines of instructions, "Reset all shape settings (72)" (also on
  the left as `#btnResetAll`), and "Choose shapes from a list", which repeats the **Shapes (404)** tab.
- **Two threads are both called "Navy"**, with almost the same colour, and nothing tells the user why or what to do.
  Merge similar colours… exists, but it sits below the list and isn't linked to this case.
- **The stats contradict the list:** "**1 colours**" (also a grammar slip) and "0 colour changes", while the list
  shows 3 threads (2 Navy, 1 White switched off). The stat counts distinct colours and the list counts thread
  entries, but nothing explains the difference.
- **The stitch count appears 3 times:** in the Design info tile, in the bottom bar facts (`#edFacts`), and in the
  bottom bar player text (`#scrubInfo`). Width × height appears twice.
- To select one thread's shapes you open the thread row's details and press "Select". The chips exist because
  that action is hidden.
- A thread that is switched off (White) keeps its place in the sewing order list and looks almost the same as the
  threads being sewn.

### F7. Smaller issues
- The header has spare room but holds nothing editor-related. Export, the action people come for, sits at the far
  bottom right, beside status text.
- `Auto-update` (a preference you set once) sits next to `Update` (an action) all the time.
- The `Woven` overlay only applies in the Stitches view, but it sits in the same pill as items that apply to both
  views.
- The numeric inputs give no hint of their range, even though `STITCH_RANGES` has one for every field.

---

## 2. Target layout (desktop, 1200 px and up)

```
┌ Header (56 px) ─────────────────────────────────────────────────────────────────────────────────────┐
│ ✽ Embroidery Digitizer │ IMG_7512.PNG ▾ │ ↶ ↷ │      [ Shapes | Stitches ]       │ Free·∞  ✦AI  (S)  [⤓ Export] │
├ Left 320 ──────────┬ View bar (40 px, part of the stage, never over the design) ─────┬ Right 300 ────────┤
│ Preset [Dense ▾] ⋯ │ [Realistic▾] [Optimised|Original]   Show ▾      − 182% + ⤢ ⛶   │ ▸ Selection        │
│ ● 3 changed  Reset │─────────────────────────────────────────────────────────────────│   (hidden until a  │
│ ▾ Size & colours   │                                                                 │    shape is chosen)│
│ ▸ Picture          │                                                                 │ THREADS  2 · 404   │
│ ▸ Fabric & thread  │                     the design, fitted inside                   │ [Threads|Shapes]   │
│ ▾ Stitches         │                     the free area                               │ ⠿☑▇ Navy     157 ⌖›│
│   Fill gap   [0.40]mm                                                                │ ⠿☑▇ Navy 2   247 ⌖›│
│   Satin gap  [0.35]mm                                                                │ ⓘ Look the same:   │
│   Shrink comp[0.25]mm                                                                │   [Merge]          │
│   Fill angle [ 45 ]°                                                                 │ ▸ Not sewn (1)     │
│   ☑ Foundation     │                                                                 │ DESIGN             │
│   ☑ Vary direction │                                                                 │ 7,354 st · 100×100 │
│ ▸ Style settings   │                                                                 │ 1 colour · 87 cuts │
│ ▸ Advanced         │                                                                 │ ≈ 24 min  ▸ More   │
│ ▸ Smart assistant  │                                                                 │ CHECKS (2) ▸       │
├────────────────────┴─────────────────────────────────────────────────────────────────┴────────────────────┤
│ ▶ ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━  3,120 / 7,354 · colour 1 of 1        ● Up to date  [Update] │
└ Bottom bar (44 px): Stitches view = sewing player + status; Shapes view = status only ─────────────────┘
```

The rule of thumb: **the header holds what you do to the design (mode, undo, export), the view bar holds how you
look at it, the left panel holds how it's made, the right panel holds what's in it, and the bottom bar holds
progress and status.**

---

## 3. Changes in detail

Every item lists its files, the ids to keep (the smoke tests and `tests/test_param_ranges.py` use them), and how
to tell it's done. **Rule for every batch: move elements, keep their ids.** That way `app.js` wiring, `shapetools.js`
and the 8 smoke scripts keep working with changed selectors at most.

### Batch A: bug fixes and quick wins (about 1 day, low risk, can ship alone)

**A1. Stop the bottom bar overflow (F1)**
- `style.css` `.ed-player #scrubInfo`: add `min-width: 0; overflow: hidden; text-overflow: ellipsis;`. Also give `.ed-player`
  `min-width: 0; overflow: hidden`, so its text can never paint outside it.
- Shorten `#scrubInfo` (app.js:2132) to `3,120 / 7,354 · colour 1 of 1`. The full sentence moves to the
  `title` and to `aria-valuetext` on `#scrub`, so screen readers lose nothing.
- Remove `#edFacts` (app.js:1358). It repeats Design info; see C1. Until Batch C ships, hide it at every width.
- **Done when:** at 1920, 1600, 1440, 1280 and 1024 px, no two bottom bar elements overlap and the status text is never
  shortened to fewer than 12 characters. Add this to the smoke tests (A6).

**A2. Correct the stats wording (F6)**
- `renderStats` (app.js:1488): add a plural helper `n === 1 ? "colour" : "colours"`, and do the same for cuts and
  changes.
- When the number of thread entries differs from the number of distinct colours, say so:
  `1 colour` with a small line under it, `2 thread entries share it`. Clicking that line opens Merge similar colours.

**A3. One help icon in the Selection heading**
- index.html:290: keep `shape_select` and move `shape_menu` into the shape menu itself (as its first,
  greyed-out line: "Right-click a shape for more").

**A4. Make same-named threads distinct**
- When two enabled layers have the same `name`, show the later one as `Navy 2` in the list. This changes the
  display only, not the stored name, so exports and the worksheet stay unchanged.

**A5. Thin out the empty Selection state (F6)**
- `shapes.js renderPanel`: with nothing selected, show **one line**: "Click a shape to change its stitching". Drop the
  colour chips (A7 replaces them), drop "Choose shapes from a list" (the Shapes tab does that), and drop "Reset all
  shape settings" here. Keep it in the left panel's changed summary (`#modSummary`, `#btnResetAll`), labelled
  "Reset 72 shape settings".
- Keep `chipsHtml()`, but use it only **when one or more shapes are selected** (for "select the rest of this colour").

**A6. Smoke test for layout**
- New `tools/smoke/layout.mjs`, run by `run_all.py`. At 1920×1080, 1440×900, 1280×800, 1024×768, 820×1180 and
  390×844, in both panes, it checks:
  - the bounding boxes of the `.ed-bar` children don't intersect;
  - no floating control intersects the fitted design's box (after Batch B, the view bar is outside the stage, so
    this checks that Fit respects it);
  - the page has no horizontal scrollbar.
- Screenshots go to the existing smoke artifact folder.

**A7. Select a thread's shapes in one click**
- In the thread row (app.js:497), add a small "select" icon button (⌖, `aria-label="Select all shapes of Navy"`)
  before the expand chevron. It does the same as the existing `data-select-layer` button, which stays in the
  details. Shift-click adds to the selection, as the chips do today. This replaces the chips' main job.

**Batch A: what was built (and where it differs)**
- A1: the new layout test also found that **at 1024 and 1280 px the Stitches bar was wider than the screen** (Export off the
  edge, the page scrolled sideways). Until Batch B moves the view controls out, the bar wraps to a second row instead
  (`.ed-bar { flex-wrap: wrap }`). The short progress text reads `7,354 sewn · colour 3 of 3`. `#scrub` has the full
  sentence as `aria-valuetext`.
- A2: `#statsNote` under the figures explains when the list has more threads than the design has colours, and links
  to Merge similar colours.
- A5: the left summary (`#modSummary`) now has two lines: `#modSettings` (n settings changed, Reset all) and
  `#modShapes` (n shapes have their own settings, `#btnResetShapes` → `Shapes.resetAll()`). `renderShapeMods(n)` is
  called by shapes.js.
- A7: instead of a new icon button, the **shape count in each thread row ("7 shapes") is the select button**
  (`.thr-pick`, Shift adds or removes). The right panel is too narrow for one more column.
- A4: the same-name number is a `#1` / `#2` badge next to the name.
- A6: `tools/smoke/layout.mjs` (52 checks) is in `run_all.py`.

### Batch B: header, view bar and bottom bar (about 3 days)

**B1. Move the main mode and the actions into the header**
- `Shapes | Stitches` (`.ed-views`, buttons `[data-pane]`) moves to the header, centred. Update
  `paneButtons()` (app.js:1510) and the `shapetools.js:321` click check to select `#paneSwitch [data-pane]` instead
  of `.ed-views`. It's the editor's main mode, so it shouldn't float over the work.
- `#btnUndo` and `#btnRedo` move next to the file name. Their help icon goes; the tooltip "Undo (Ctrl+Z)" stays.
- `#btnExport` moves to the far right of the header, after the avatar, and stays the primary button. Below 560 px it
  becomes an icon-only button (the current rule already does this for the label).
- The file name becomes a menu button (`IMG_7512.PNG ▾`): New design, Close design. This removes `#btnNew` as a
  separate button, but `#btnNew` stays as a menu item with the same id.
- The header only gets these when `body.page-editor` is set. The account and admin views keep today's header.

**B2. A view bar at the top of the stage (F2, F3)**
- A new `<div class="ed-viewbar" id="viewBar">` as the first row of `.ed-main`. It's a real grid row, not a float, so
  **Fit never puts the design under it**.
- From left to right:
  1. **Preview style** (`#viewMode`). This is a segmented control at 1440 px and up, and a `<select>` styled like the
     segments below that. It keeps `data-mode` values and ids, and only shows in Stitches.
  2. **Optimised | Original** (`#variant`) is renamed **Optimised | Unoptimised**, so "Original" means the reference
     picture only. It only shows in Stitches when optimisation is on (as today).
  3. A spacer.
  4. **Show ▾**: one popover with checkboxes (it keeps the existing ids, so the app.js handlers don't change). Inside:
     - Both views: Original picture (`#showRef`), Hoop (`#showHoop`), Grid & rulers (`#showGrid`).
     - Shapes view only: Directions (`#showDirections`).
     - Stitches view only: Woven texture (`#showWeave`), Dark fabric (`#showFabric`).
     - The button shows a count when something not shown by default is on ("Show · 2").
  5. **Zoom**: `#btnZoomOut`, `#zoomPct`, `#btnZoomIn`, `#btnFit`, `#btnFullscreen`, then `#btnKeys` (renamed
     "Help & shortcuts", see C4).
- The reference controls (`#refBar`: what to show, opacity, split) show **inside the Show popover under Original
  picture** when it's ticked, instead of as a second floating bar. `#editBar` (shape editing tools) stays a floating
  bar, but it moves to the **top left of the stage, below the view bar**. That keeps it next to the shapes and off
  the header.
- `workspace.js`: the rulers start under the view bar. `viewMoved()` already runs after view-box changes, so only the
  origin offset changes. In `--tools-top` the 60 px float allowance becomes the edit bar's height only.

**B3. A slimmer bottom bar**
- Keep only:
  - left: `#btnPlay`, `#scrub` (flex 1), `#scrubInfo` (Stitches only);
  - right: `#edDot`, `#edStatusText`, `#btnUpdate`.
- `#autoUpdate` moves into the Help & shortcuts dialog (where workspace settings live already), and it also goes
  in the status text's menu: clicking "● Up to date" opens a small popover with "Update automatically ☑" and the
  last update time.
- With auto-update off, the status reads "● Changes waiting" in the warning colour and `Update` gets the accent.
- In the Shapes view the bar shows status only. It stays visible, so the layout doesn't jump when you switch views.
- Below 900 px, `#barSettings` / `#barInfo` (the sheet openers) stay in this bar, as today.

### Batch C: left panel (about 3 days)

**C1. A compact preset row**
- One row: `Preset [Dense coverage ▾] [⋯]`. **Choosing a preset applies it at once** as one undo step. presets.js
  already applies through `History` and `Jobs.extract({step})`, so this is the existing `apply()` called from
  `change`. A toast confirms it with an Undo button: "Dense coverage applied · Undo".
  - Remove `#btnPresetApply`. Smoke tests that click it change to selecting the option instead.
  - The `⋯` menu holds Save as preset… (`#btnPresetSave`), Manage presets… (`#btnPresetManage`), and Reset to preset
    when something was changed.
- `#presetHint` (the description) shows as the select's `title` and as one muted line, cut to 1 line with the full
  text on hover.
- The "Changed" state (`#presetModified`) and the shape changes summary (`#modSummary`) become **one line** under the
  preset: `● 3 settings changed · 72 shape settings  Reset ▾`. The Reset menu offers: Reset settings, Reset shape
  settings, Reset both.

**C2. Regroup the sections by what the user decides**
| Section | Holds | Open at start |
|---|---|---|
| Size & colours | Hoop, Design width, Number of thread colours | yes |
| Picture (raster) / Artwork (vector) | Background, Text & fine details, ▸ Picture adjustments, ▸ Advanced detail | no |
| Fabric & thread | Fabric, Thread weight, Thread brand | no |
| Stitches | Fill gap, Satin gap, Shrink compensation, Fill direction, Foundation ☑, Change direction per colour ☑, Smart direction ☑, Smart stitch reduction ☑ | yes |
| Style settings | (only when the design uses such a style, as today: `#styleParams`) | follows use |
| Advanced stitch settings | as today | no |
| Smart assistant | as today | no |
- `buildStitchForm` (app.js:830) builds two forms: `#materialForm` (fabric, thread weight, thread brand) and
  `#stitchForm` (the rest). Both report through the same change listener, `History.stitchChange` and `Jobs.stitch`.
  `stitchValues()` reads both. Keep the name `stitchForm` for the second form; the smoke tests use it 21 times.
  - Check: `tests/test_param_ranges.py` parses `PARAMS` and index.html. `PARAMS` keeps its shape, and only the
    template changes.
- **Section state is remembered** per browser, as the panel widths are (workspace.js already stores layout). The
  default is the "Open at start" column.
- A section summary shows a **changed dot** when any field inside it differs from `state.baseline`. That way a
  closed section still tells you something in it was changed.

**C3. Compact number fields**
- On fine-pointer screens 900 px and up, a numeric field becomes one row: the label on the left (it may wrap to 2
  lines), the input on the right, `width: 88px`, with the unit as a suffix inside the box (`0.40 mm`, `45 °`). This
  halves the height: 7 core fields go from about 640 px to about 300 px.
  - The unit moves out of the label text into a `data-unit` attribute. `PARAMS` labels keep "(mm)" for
    `test_param_ranges.py`, and the template strips it when it renders the label.
  - The range shows as a placeholder-style hint on focus (`0.25–1.0`), from `PARAMS` min and max.
- Touch screens keep the stacked layout and the 44 px tap height (`--tap`).

**C4. Help on demand (F4)**
- In the editor's panels, `.help` buttons are **hidden until the row is hovered or focus-within**, via
  `label:hover .help, label:focus-within .help, .help:focus-visible { opacity: 1 }`. Hidden buttons stay in the tab
  order, so keyboard users still reach the help. On `(hover: none)` they stay visible.
- The `Help & shortcuts` dialog (today's `#keysDialog`) gets a switch: **"Always show help buttons ?"**, stored per
  browser. It's on for the first 3 sessions, so new users learn the buttons exist, then the default turns off.
  - The footer tip ("hover or tap the ? …") already exists outside the editor. Inside the editor, the
    Stitches note (index.html:210) says the same once.
- Section headings keep their help icon visible, since there are few of them.
- The help text itself stays in `help.js`, unchanged.

### Batch D: right panel (about 2 days)

**D1. Selection only when there is one**
- The Selection group is **collapsed to its heading** ("Selection: click a shape") while nothing is selected. With
  a selection it opens and scrolls into view. `#shapePanel` and the `#selCount` badge behave as today.

**D2. A clearer thread list**
- Order: the threads being sewn, then a collapsed **"Not sewn (n)"** group for disabled threads, so White doesn't
  look like a thread that will be sewn.
- **Suggest merging similar threads:** after each extract, `/layers/similar` (already there) is called with a tight
  threshold. If it finds pairs, a one-line note shows above the list: "2 threads look the same · Merge…", which
  opens `#similarDialog` already filled in. Dismissing it hides the note for this design.
- The `Merge similar colours…` button moves into the `⋯` of the Threads heading, with "Show threads / Show shapes".
- The heading reads `THREADS · 2` and the hint text "sewn from top to bottom" becomes a tooltip, because
  the drag handles already say "drag to reorder".

**D3. Fewer design facts, with more on demand**
- 4 tiles always shown: **Stitches**, **Size (mm)**, **Colours / colour changes**, **Sewing time**. Thread cuts and
  thread used, Stitch types (`#kindStats`), Optimise info (`#optimizeInfo`) and Thread order (`#sequence`) go under
  `▸ More details`, which is remembered.
- This is now the **only** place for these figures. The bottom bar shows sewing progress only (A1, B3).

**D4. Checks first when there is a problem**
- The Checks heading shows a count badge (`CHECKS · 2`, in the warning colour). If there are warnings, the Checks
  group moves above Design info (CSS `order`), so problems are seen before stats.

### Batch E: finishing (about 1–2 days)

- **Spacing and type scale:** one spacing scale (4 / 8 / 12 / 16 / 24) in the editor. Section headings use the same
  style on both sides (today the left uses a `summary` and the right an `h3`, with slightly different spacing). Inputs are
  32 px high on fine pointers, `--tap` on coarse.
- **Fewer borders:** the threads list and stat tiles lose their individual borders in favour of row separators
  and a light background, so the panel isn't a stack of boxes.
- **Responsive check** at the 6 sizes in A6, in light and dark themes. The header must keep one row at 1024 px and
  up. Below 900 px the mode switch stays in the header (centred, compact), and Export becomes icon-only.
- **Help texts:** add entries in `help.js` for the new "Show" popover and the preset menu, and check that
  `shape_menu` (A3) still reads right.
- **README / ROADMAP:** update "The editor" section of README.md and the ROADMAP §1 row.

---

## 4. Order, size and risk

| Batch | Days | Risk | Can ship alone | Main tests to touch |
|---|---|---|---|---|
| A: bugs and quick wins | 1 | low | yes | new `layout.mjs`; `extras.mjs` (selection panel text) |
| B: header, view bar, bottom bar | 3 | medium (wiring of moved controls, rulers origin) | yes | `workspace.mjs`, `batchb.mjs`, `batchc.mjs`, `phase5.mjs` (selectors only) |
| C: left panel | 3 | medium (two forms → one value set; presets apply on change) | yes | `presets.mjs`, `controls.mjs`, `test_param_ranges.py` |
| D: right panel | 2 | low | yes | `batchc.mjs` (merge similar), `extras.mjs` |
| E: polish | 1–2 | low | yes | screenshots only |

Ship A first: it fixes the visible bug and the contradictions. B gives the biggest visual gain.

## 5. Rules that still apply (CLAUDE.md)

- Edits go through `Jobs` / `work()`, and every settings change records with `History.stitchChange` /
  `History.designChange`. Applying a preset on change must stay **one undo step**.
- Overlays and toolbars follow the view through `viewMoved()`, with no polling. The view bar changes the stage's
  top, so `workspace.js` must take the origin from the stage box, not a constant.
- All user-facing help text goes in `help.js`.
- Script order and globals don't change. There are no new dependencies and no bundler.
- Every moved control keeps its id. When a test has to change, it's the selector, never what it checks.

## 6. Decisions needed before building

1. **Apply presets on select (C1)?** The recommendation is yes, with an Undo toast. The alternative is to keep
   `Apply` but put it inside the select row.
2. **Export in the header (B1)?** The recommendation is yes, as in most design tools. The alternative is to keep it at
   the bottom right of the slimmer bar.
3. **Help buttons hidden until hover (C4)?** The recommendation is yes, with "Always show" in Help & shortcuts. The
   alternative is to keep them always visible but lighter (40 % opacity).
