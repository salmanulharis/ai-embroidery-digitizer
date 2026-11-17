# App UX plan: start, export, account, admin, phones and first run

Status: **planned, nothing built** (written 2026-11-17). It's Phase 7 in [ROADMAP.md](ROADMAP.md) §1 and follows
[EDITOR_UX_PLAN.md](EDITOR_UX_PLAN.md) (Phase 6, the editor itself, done in v1.6.0).

Based on a screenshot tour of v1.6.0 (sign-in, start screen, editor on first open, Export dialog, My account,
Admin console) at 1440 × 900, 1024 × 768, 820 × 1180 (tablet) and 390 × 844 (phone), in light and dark, plus a read
of `index.html`, `app.js`, `account.js`, `admin.js` and `style.css`.

The aim is the same as Phase 6: **fewer decisions, the next step always in view, details on demand.** Phase 6
tidied where things are. This phase changes some of the **workflow**: what the app asks, when, and what it
decides by itself.

---

## 1. Findings

### G1. Export: the button you came for is below the fold (the biggest problem)
- At 1440 × 900 the dialog is taller than the screen and scrolls as a whole. **Download** sits in the footer under
  12 file cards, so it's off-screen until you scroll past every format. On a phone it's about three screens down.
- All 12 outputs have the same weight: 8 machine formats, Wilcom ZIP, SVG, worksheet and preview PNG. Most people
  need exactly one machine file, but they have to read all eight descriptions to be sure.
- The app already knows the next step but only says it. The summary warns "DST files carry no thread colours: add
  the worksheet". It could tick the worksheet. It says "12 spots where the thread may break – open the Machine
  check view", but there's no button to get there.
- The machine format is chosen again for every design, although a shop almost always uses the same machine.
- "Select all" invites downloading 12 files, and every machine format counts towards the same design download.

### G2. Start: the one question that matters most is asked too late
- The upload goes straight into the editor with a **100 mm width** and no hoop. But the finished size is the first
  thing a digitiser decides, because it changes everything: satin vs fill, small lettering, density, stitch count. Today you
  find "Design width on fabric" in the left panel after the design has already been prepared once at the wrong
  size, and changing it prepares it again.
- Uploading shows a full-screen "Uploading…" overlay with no progress and no hint of what happens next.
- You can't paste a picture (Ctrl+V), the most common way to bring in a logo copied from an email or a website.
- For a signed-in user who has used the app before, the 4 feature cards ("Smart by default", "Machine-ready", …)
  are marketing. They take the space where useful things could be, like the last choice of placement and hoop.
- The footer tip "hover or tap the ? next to a setting" is out of date: help buttons now appear on hover.

### G3. First open of the editor: the panel starts with a report, not with the settings
- The left panel opens with a large **"Smart settings applied"** card (about 300 px: detections, explanations,
  a suggested preset). It's useful once, but it pushes Size, Picture and Stitches below the fold every time, and it
  can't be closed.
- The suggested preset sits in that card *and* the preset picker is above it: two places for the same decision.
- New users see every "?" for their first 3 visits (Phase 6 default), which is the busiest version of the screen at
  the moment they're least sure where to look.
- There's no hint of the order of work: check shapes → check stitches → export. Shapes / Stitches and Export are
  in the header, but nothing tells a first-time user that's the path.

### G4. The editor on phones and tablets
- The view bar wraps to **two rows** at 1440 px, on tablets and on phones: zoom drops to its own row and takes
  40 px from the preview. The container query only switches to drop-downs below 760 px of preview width, so
  1440 px (about 820 px of preview) gets all the buttons, which then don't fit.
- On phones the two buttons that open the panels (Settings, Design) are icons only. The status line ("Up to date",
  Auto-update) takes a whole row under the player.
- Panels open as sheets that cover the preview completely, so you can't see the effect of a change you just made.
- The header on phones is fine (Phase 6), but the file name is hidden there and only shown inside the Settings sheet.

### G5. My account
- The page is titled **"Welcome"** instead of "My account"; the subtitle repeats the e-mail shown again under Profile.
- Four equal cards in a 2 × 2 grid. The AI settings card is half empty but as tall as the plan card next to it.
  "0 saved" in a 32 px figure is the loudest thing on the page.
- The presets card says "Settings → Save as preset…", but since v1.6.0 that's in the preset ⋯ menu.
- The plan card lists every included feature with a tick, but not what *isn't* included. The useful part ("what
  would an upgrade give me?") is missing.
- "AI settings" is also a button in the header on every page, as a chip with a "Built-in" badge. For most users
  it's a set-once preference, yet it takes the same header weight as Export.

### G6. Admin console
- On phones the section list becomes a sideways-scrolling tab strip. The **current tab can be off-screen** (on
  Settings, you see Overview / Users / Subscription plans and an underline under the wrong place).
- The overview chart takes a 30-day row of zeros on a new install; the stat tiles leave a 7th-column gap at
  1440 px.
- The Users table puts "You · set in the deployment secrets" in an unlabelled column after the Details button.
- Every admin form is a long column of fields with help text under each, with no grouping summary or "changed" marks
  (the editor has both since Phase 2).

### G7. Consistency across the app
- Three button styles for the same "go back" action: "← Back to my design", "← Back to the app", and the
  editor's "New design".
- The header chips (plan, AI, user) look like buttons but are three different kinds of thing: a status, a settings
  dialog and a menu.
- Dialog sizes, paddings and footers differ (Export, presets, users, plans), and only some dialogs have a sticky
  footer.
- Empty states vary from helpful ("Figures appear when the stitches are ready.") to none ("No downloads yet.").

---

## 2. Target workflow

```
Sign in ─► Start ───────────────► Editor ─────────────────────────────► Export
           drop / paste / choose   1. Shapes: check colours & shapes     one sticky Download button,
           "What is it for?"       2. Stitches: check, play, fix         your machine's file pre-ticked,
           (left chest, cap,       3. Export ▸                           worksheet added when needed,
            jacket back, custom)   (a quiet 1-2-3 guide on first use)    checks with "Show me"
           → size, hoop, preset
             decided before the
             first preparation
```

Rule of thumb: **ask once, remember, and let every screen end in its next step.**

---

## 3. Changes in detail

Same rules as Phase 6: keep ids (the smoke tests use them), help text in `help.js`, `Jobs` / `History` for edits,
no new dependencies, and limits only through `entitlements`.

### Batch A: Export that finishes the job (about 2 days, highest value)

**A1. Sticky actions and a short summary**
- The dialog gets a fixed header (title, close) and a **fixed footer**; only the file list scrolls between them.
  The footer button names what you get: **"Download DST + worksheet"**, or "Download 3 files (ZIP)". `#btnDownload`
  keeps its id; `#expState` sits in the footer.
- The summary column becomes a compact strip at the top on screens under 900 px (thumbnail, size, stitches,
  time), so the list starts on the first screen.

**A2. Your machine first**
- A **"Your machine"** select at the top (Tajima / WONYO / Dahao, Brother, Janome, Bernina, Melco, Barudan,
  Husqvarna / Pfaff, Singer, Other). It's remembered per account: a new `user_prefs` row, appended to
  `db.MIGRATIONS`, holding the machine key only and no design data. If the account has no row yet, the browser's
  `localStorage` is used.
- The machine's format is pre-ticked and shown first, with a "recommended for your machine" note. The other machine
  formats go under a closed **"Other machine formats (7)"**. Wilcom ZIP, SVG, worksheet and preview stay as now in
  their groups.
- *Select all* is removed. A ZIP still happens on its own when more than one file is ticked (as today, with the
  `zip_export` feature).

**A3. Do what the checks say**
- DST / EXP / U01 (no thread colours): the **worksheet is ticked automatically**, with "(added: DST has no thread
  colours)". You can untick it, and that choice is kept for this dialog.
- Each check in the summary gets an action where there is one. "Show me" on machine-check spots closes the dialog
  and switches to Stitches → Check view; the hoop warning opens Size.
- The allowance note says what this download costs ("This design counts once: 3 of 10 left this month.
  Downloading it again is free.") and sits next to the button. `usage.generation` already charges once per design.

Done when: at 1440 × 900 and 390 × 844 the Download button is visible without scrolling; a returning user
downloads their usual file in two clicks (Export → Download).

### Batch B: Start screen and the size question (about 3 days)

**B1. "What is it for?" before the first preparation**
- After a file is chosen (drop, button or **paste**), a short step in place of the drop zone shows the picture's
  thumbnail and a row of **placement chips**:
  Left chest (≈ 90 mm) · Cap front (≈ 55 mm) · Sleeve (≈ 75 mm) · Full front (≈ 200 mm) · Jacket back (≈ 280 mm) · Custom…
- Each chip sets `width_mm` (fitted to the picture's aspect and the hoop), the smallest standard hoop that fits,
  and a suggested built-in preset. "Small text & logos" is only suggested when the smart analysis finds lettering
  under 6 mm *at that size*.
- One button, **"Create stitches"** (Enter), and *Skip* (keeps today's 100 mm). The last chip is remembered and
  pre-selected, so a returning user presses Enter.
- The placement values live in one server table (`models` or a small `placements.py` beside `fabrics.py`),
  returned in `/api/config`, clamped by `STITCH_RANGES` / `RASTER_RANGES`, and checked by a test like the presets.
  The editor's Size section shows the chosen placement as its own drop-down above the width ("Left chest – 90 mm").

**B2. Upload feedback and paste**
- Ctrl+V / ⌘V on the start screen (and "Paste" in the drop zone on touch, where the clipboard API allows) uploads
  the pasted image as `pasted.png`.
- An inline progress bar in the drop zone (fetch upload progress via `XMLHttpRequest`) replaces the full-screen
  overlay, then "Finding colours, background and text…" while the design is prepared.

**B3. A start screen for people who already know the app**
- The feature cards are shown only to accounts with no design downloads yet (`/api/me/account` usage). Everyone else
  sees a single line under the drop zone: the remembered placement and machine ("Left chest · Tajima DST –
  change").
- The out-of-date "hover or tap the ?" tip is removed; the help line moves to the Help and shortcuts dialog.
- Projects stay temporary (privacy rule): there is **no** "recent designs" list. The open-design note
  (`#openNote`) stays as it is.

Done when: first upload → stitches at the right size in one step; a returning user's path is drop → Enter → Export
→ Download.

### Batch C: First run of the editor (about 2 days)

**C1. The smart report becomes one line**
- `#smartInfo` shows one summary line: "✨ 3 colours · background removed · lettering 5.7 mm". **Details** opens
  the explanations, and × hides the line for the design. It's open by default only on the first design of a new
  account, then closed (remembered per browser).
- The suggested preset moves to the preset picker itself: the option is marked "Suggested" and a small badge sits
  beside the picker ("Suggested: Small text & logos · Apply"). One place for the decision.

**C2. A quiet first-use guide**
- On the first design of a browser, three numbered marks: **1** on Shapes ("check colours and shapes"), **2** on
  Stitches ("check the stitches, press ▶ to watch them sew") and **3** on Export. Each disappears when that step is
  done (pane visited, Export opened). It isn't a tour or a modal and it never blocks; "Hide tips" in Help and
  shortcuts turns it off.
- Help buttons: new users see them on hover like everyone else. The "first 3 visits" default from Phase 6 is
  replaced by this guide, because the "?" marks are noisiest exactly when the screen is new. *Always show* stays.

**C3. Sensible defaults, fewer choices shown**
- Picture, Fabric & thread and Advanced start closed (Picture already does); Stitches shows the four core
  numbers. The smart analysis may open Picture by itself when it found something worth checking (noisy photo,
  background not found), with the reason in the summary line.
- Settings that don't apply are hidden instead of disabled: no picture options for artwork files (done), and no
  satin settings when the design has no satin, using the same mechanism as `STYLE_PARAMS`.

Done when: on the first open the Size section is above the fold at 1440 × 900, with no "?" marks visible until
you point at a setting.

### Batch D: Phones and tablets (about 2 days)

**D1. The view bar is always one row**
- One row at every width: the preview style and the version become drop-downs when the bar is narrower than
  ~980 px (today it's 760); zoom collapses to "− 138 % +" and the rest (fit, 100 %, full screen, shortcuts) moves
  into a zoom ⋯ menu below ~600 px.
- Test: `layout.mjs` checks the view bar's height (one row) at all 7 sizes in both views.

**D2. A bottom bar made for thumbs**
- On phones: **Settings** and **Design** get short text labels under their icons, and the status (Up to date /
  Update) becomes a dot on the play row with the Update button only when needed. *Auto-update* moves to Help and
  shortcuts → Preferences on phones, where it's set once.

**D3. Half-height sheets**
- On phones and tablets the Settings and Design sheets open at **half height** with a drag handle (full height by
  dragging up or with a button), so the preview above stays visible while you change a setting. Esc, the handle
  and a tap on the preview close them. They already restore focus; that stays.

**D4. The file name and New design**
- The file name becomes a small menu button in the header ("example-logo.png ▾": New design, Close design). On
  phones, where the header has no room for the name, the same menu sits in the user menu's header. That's the
  file-name menu Phase 6 left out, built once for all widths.

Done when: at 390 × 844 the preview is at least 60 % of the screen with a sheet closed and 45 % with it open.

### Batch E: Account, admin and consistency (about 2–3 days)

**E1. My account**
- Title "My account"; the subtitle shows the name or e-mail once.
- Layout: the plan card full width at the top, as an allowance meter (this month's designs, renewal date) plus
  **"Included / Not on your plan"** in two short columns, with the upgrade line under the second. Then a row of
  three smaller cards: Presets ("3 of 10 saved · Manage"), AI ("Built-in · Change"), Profile and sign-in
  (name, password, devices).
- Fix the presets hint ("in the editor: Preset ⋯ → Save as preset…").
- The header **AI settings** chip moves into the user menu (still one click); the header keeps the plan chip only
  where it carries information (a limit, close to the limit, expired), so it's empty for unlimited plans.

**E2. Admin console**
- Phones: the section list becomes a select ("Section: Settings ▾") instead of a sideways tab strip; tablets keep
  the side list.
- Overview: stat tiles in a grid that fills the row; the chart shows "No downloads in the last 30 days" instead
  of a flat line at 0 when there's no data.
- Users table: the "You · set in the deployment secrets" note moves under the account name; Details is the last,
  labelled column.
- Settings forms: grouped cards with a heading summary ("Privacy · designs deleted after 2 h idle, 24 h max"),
  changed-value marks and a sticky Save bar that appears only when something changed (the editor's pattern).

**E3. One way to do the same thing**
- One back control: a "← Editor" (or "← Start" when no design is open) breadcrumb in the same place on account and
  admin pages.
- Header: the user menu holds account, admin, AI settings, theme and sign out; chips are only for status.
- One dialog frame for every dialog: fixed header and footer, the body scrolls, primary button right, Cancel
  left of it, and the same paddings. It's built once as CSS (`.form-dialog` rules plus a `.dlg-foot` sticky footer)
  and applied to Export, presets, users, plans and AI.
- Empty states: one pattern (an icon, one sentence, and the action that fills it), used in the admin tables,
  account presets, the Shapes list and the Checks group ("No problems found ✓").

---

## 4. Order, size and risk

| Batch | Days | Risk | Can ship alone | Main tests to touch |
|---|---|---|---|---|
| A: Export | 2 | low (UI; one small DB migration for the machine choice) | yes | `workspace.mjs`, `phase5.mjs` (export), `test_plans_usage.py` (unchanged charging) |
| B: Start + size question | 3 | medium (new step before the first extract; placement table) | yes | `workspace.mjs`, new `start.mjs`, new `test_placements.py` |
| C: First run | 2 | low | yes | `layout.mjs` (help defaults), `presets.mjs` (suggested preset) |
| D: Phones and tablets | 2 | medium (sheets, view bar breakpoints) | yes | `layout.mjs` sizes, `batchb.mjs` |
| E: Account, admin, consistency | 2–3 | low | yes | `test_admin.py` (unchanged API), `batchc.mjs` (dialogs), new admin smoke checks |

Ship **A** first: it's the step every user takes, and today its main button is off-screen. **B** changes the most
for quality, because designs are prepared at the right size from the start.

## 5. Rules that still apply (CLAUDE.md)

- Projects stay temporary: no recent-designs list. The machine and placement choices are preferences, not design
  data, and nothing about the design goes in the DB.
- Limits and features only through `entitlements.effective`; the Export note reads the quota endpoint as now.
- Placement values are clamped by `STITCH_RANGES` / `RASTER_RANGES` like presets, and checked by a test.
- DB changes are appended to `db.MIGRATIONS`.
- Every user-facing text goes in `help.js`; ids are kept; the smoke tests change selectors, never what they check.
- Stitch output doesn't change: the golden plans stay as they are. The placement step only sets values the user could already set.

## 6. Decisions needed before building

1. **Placement step (B1):** ask "What is it for?" after every upload (recommended, with the last choice
   pre-selected so Enter is enough), or only offer it as chips on the start screen and keep going straight into
   the editor?
2. **Your machine (A2):** store it per account in the DB (recommended: it follows the user to the other machine
   they work from), or only in the browser?
3. **AI settings in the header (E1):** move it into the user menu (recommended), or keep the header chip?
4. **Auto-update on phones (D2):** move it to Preferences (recommended), or keep it in the bar on all screens?
