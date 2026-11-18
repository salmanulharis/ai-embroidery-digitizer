# Automation, grouping and help: findings and what was built

Status: **all batches (A–E, F–J, then K–P) built** (2026-11-18). It's Phase 8 in [ROADMAP.md](ROADMAP.md) §1 and follows
[EDITOR_UX_PLAN.md](EDITOR_UX_PLAN.md) (Phase 6) and [APP_UX_PLAN.md](APP_UX_PLAN.md) (Phase 7). The open decisions were
taken as recommended; the machine speeds in B are starting values to confirm.

Phases 6 and 7 already gave the editor sections that open and close, help on hover, a placement step and a suggested
preset. This phase covers what was left: **checks that fix things, choices remembered, settings grouped by what they
change, and help where it was missing.**

## 1. Findings

- **H1. Checks didn't fix anything.** The server sent warnings as English sentences. The Checks panel had no actions,
  and Export found its two "Show me" actions by matching the wording with regexes. The built-in reviewer
  (`ai/builtin.review_plan`) already worked out concrete setting changes, but it ran only on **Check my stitches**,
  behind the blocking overlay.
- **H2. The same choices for every design.** The thread brand started at "None", the machine speed at 750 whatever the
  account's machine, and the fabric at General. Cap front picked the cap hoop but not the Cap fabric.
- **H3.** "Show me" opened the Check view without zooming to the spots.
- **H4.** *Advanced stitch settings* was one flat list of 17 controls that mixed seven kinds of setting.
- **H5.** The style pickers had 35 options with no groups.
- **H6.** The *Smart assistant* section mixed picture clean-up with suggesting settings, which overlaps the automatic
  analysis.
- **H7.** The selection panel greyed out controls that don't apply instead of hiding them.
- **H8.** Inner groups (Advanced, Picture adjustments, Advanced detail) had no changed dot and didn't remember whether
  they were open.
- **H9.** There was no way to find one setting among about 45.
- **H10.** Section headings, the choices inside drop-downs, checks and Dark fabric had no help.

## 2. What was built

### A. Checks that fix things (H1, H3)
- The stitch result has `checks: [{code, severity, text, title?, actions, change?, fix?, ids?, spots?}]`
  (`service._stitch_checks`), next to the unchanged `warnings` strings. The built-in review runs after every stitch
  (3–14 ms against a 1–2 s stitch). Its `high` and `medium` findings become checks, merged by code with the server's
  own checks. A `low` finding joins only to add a fix to an existing check; everything else stays in **Check my
  stitches**. If the review fails, the stitches are still shown without it.
- `review_plan` issues carry `code`, their own `change` + `fix` label and `ids`; the combined `changes` is as before.
- Front end `Checks` (app.js):
  - a fix button (`History.stitchChange` = one undo step, never applied by itself);
  - *Show me*: Check view zoomed to the spot with a ring, ‹ › through the spots, announced to screen readers;
  - *Change the size*, *Fit to the hoop* / *Use the … hoop* (client hoop check), *Select them*;
  - × hides a check for this design.
- Export takes its checks from the same list, by action instead of by wording (`data-check-act` values unchanged).

### B. Remember and infer (H2)
- `prefs.py`: new preferences `fabric` (FABRIC_IDS) and `thread_chart` (charts on the server; a chart taken off the
  server is forgotten), validated by function (`VALID`). `MACHINES[*].spm` gives the speed a new design's time
  estimate starts with: Tajima / Melco / Barudan 850, Brother / Janome / Bernina / Husqvarna 650, Singer 600 (**to
  confirm**). `PLACEMENTS.cap.fabric = "cap"`. Import-time checks keep the values within the editor's ranges.
  There's no migration: preferences are a JSON column.
- `applyStartValues()` runs after the upload's analysis, before the baseline: the placement's fabric wins over the
  usual fabric. These values are part of the baseline (`state.startStitch`), so they aren't marked as changed.
- Choosing Cap front in the Size section later sets the Cap fabric in the same undo step as the size
  (`Jobs.extract({step})`).
- My account → *Your defaults*: Usual fabric and Thread brand (only when charts exist).

### C. Grouping (H4–H7)
- `ADVANCED_GROUPS`: Foundation, Stitch length & texture, Satin width & compensation, Sewing order & thread cuts,
  Special effects. A setting no group names goes under *Other settings*. Machine speed moved to *Fabric, thread &
  machine*, which is still `data-sec="material"` and still in `#stitchForm` through `form=`. `PARAMS` is unchanged.
- `models.STITCH_STYLE_GROUPS` (sent as `stitch_style_groups`) groups the styles: optgroups in the Selection, Threads
  and toolbar lists, and non-focusable headings in the shape menu (`{heading}` items). A style no group names goes
  under "Other".
- The smart assistant is a group (`#aiBox`, `data-sec="assistant"`) inside *Picture*; its ids are unchanged.
- Selection panel: Direction is hidden when it doesn't apply (the note says why). Thickness, extra width, bold outline
  and foundation are rendered only when they apply, under *Fine-tune*, which opens by itself when a selected shape has
  its own value there and otherwise remembers the user's choice.

### D. Sections and finding settings (H8, H9)
- The changed dot is on every `details[data-sec]` in the settings panel. `bindSections` remembers every group, built
  later or not (a capturing `toggle` listener). Inner groups have keys: `adjust`, `detail`, `assistant`, `advanced`,
  `adv_*`.
- `SettingFind`: 🔍 next to the preset picker or `/` opens a field under the preset bar, so it takes no space when
  closed and the Size fields stay above the fold. Every typed word must match the label or the help title and text;
  aliases cover trade words. Sections with matches open for the search and close again after it (the search isn't
  remembered as open state). Hidden parts stay hidden. Enter goes to the first match and Esc closes the field.

### E. Help (H10)
- Help entries for every section and group heading (`sec_*`, `adv_*`, `info_group`, `checks_group`, `shape_fine`),
  every check code (`check_*`) and Dark fabric.
- `OPTION_HELP` + `OptionHelp`: one line per choice for the style, foundation, fill texture, background and text-mode
  drop-downs. It appears under the drop-down, follows the choice (also after presets, undo and reset) and serves as
  the option's tooltip. Drop-downs built later get it through Tip's observer.
- `tests/test_help_coverage.py` fails when a setting, group, style, foundation or texture choice, or check code has no
  help.

## 2b. More automation (F–J): findings J1–J9 and what was built

Findings: artwork files got no analysis (and their placement size came from the canvas, not the artwork); size advice
was a sentence, not a button; *Small marks are not stitched* had no fix; fixes came one at a time; the analysis went
stale after a size change; only 4 of 12 built-in presets could be suggested; two colours could match the same thread;
nothing checked the colour order after merges or for vector paint order; width-based compensation was missing.

### F. Size, keep and Fix all
- `review_plan` issues may carry `resize: {width_mm}`: *Design too tall for a cap* (57 mm tall, rounded down) and
  *Lettering thinner than the machine can sew* (the thinnest lines as wide as the narrowest column of the thread,
  `strokes.thread_limits`, at most ×3, rounded up to 5 mm; measured on the design's shapes, `measure_design`).
  The editor offers *Make it … mm wide* within the chosen hoop (either way round, 6 mm room) and the Size field's
  range; the design is prepared again as one undo step.
- *Small marks are not stitched* offers *Keep them*: "Skip shapes smaller than" just below the smallest skipped mark,
  never below the default (marks become a sewable dot). The reviewer still never suggests deleting marks.
- *Fix all (n)*: every high / medium check's setting change as one undo step (the first wins on the same setting).
  Size, merge and order fixes stay buttons of their own.

### G. Artwork files
- `ai/builtin.analyze_design`: the shapes drawn at up to 20 px/mm (2.5 M px), then the same measures as pictures
  (`detect_text`, `_thinnest_mm`, `_size_advice`): lettering, 60 wt advice, size notes, colour count, proportions.
- `POST /api/projects/{id}/analysis` (built-in only, `work` rate bucket, not recorded as an AI request) answers for
  pictures and artwork. `/ai/suggest` is unchanged (pictures only). The editor uses `/analysis` after an artwork
  upload, so the summary line, the preset offer, 60 wt and the placement's size (measured proportions) work for vectors.

### H. Analysis that keeps up, more presets
- `SmartRefresh` (app.js): after an extract whose width differs by 5 % or more from the width measured at, the
  analysis is asked again; it updates the summary line (while shown) and the preset offer (`Presets.reoffer`, until a
  preset was applied). It never changes a setting; `state.smart` (what the upload filled in) stays.
- Picture tone (`measurements.lightness_range`, `lightness`, `chroma`; no spread with a transparent background):
  *High contrast* when the lightness spreads over less than 45 (faded), *Soft colours* when light (≥ 72) with a
  little colour (chroma 8–35). Never for photos. Tier rules as before.
- Fixed on the way: a colour filling the whole picture gave an infinite line width (NaN in the answer).

### I. Thread and order checks (service.py)
- `same_thread`: enabled colours that match the same thread of the chosen chart; *Merge them* merges each into the
  heaviest (`merge-many`, one undo step).
- `colour_order`: a colour sewn only as outlines / lettering (satin, columns, strokes, running) before a fill
  (≥ 20 mm²) it borders (0.5 mm); the button moves it to just after the last such fill (`PUT layers`, one undo step).
  Uses the per-shape methods the stitch run already computed; no geometry work unless the order is suspect.
- Both run after the review, each guarded: a failure never stops the stitches.

### J. Compensation by satin width (opt-in)
- `StitchParams.pull_by_width` (off; Advanced › Satin width & compensation; a preset key in "fabric"): satin, satin
  bands, satin lines and smart-direction columns get `satin_pull` = the set value × width / 4 mm, within ×0.5–×2.
  Fills, small-lettering strokes and decorative styles keep the set value. Off = byte-identical stitches.
- The width-based foundation (`satin_underlay = "auto"`) already existed; no fabric uses it until a sew-out.

## 2c. Round three (K–P)

- **K. Fine thread**: `ai/builtin.FINE_SATIN_SPACING_MM` (0.35, the Small text & logos value). The analysis answers
  `stitch: {thread_weight: 60, satin_spacing_mm: 0.35}` for small lettering (AI-refined answers too); the editor
  never widens the spacing (`applySmartStitch`) and puts both in the baseline. The thin-lettering fix includes it;
  check `fine_spacing` (60 wt, satin spacing over 0.38 mm, satin in the design) offers it on its own.
- **L. Hoops by machine**: `prefs.HOOPS` (moved from api.py; appended 130 × 180 and 160 × 260 mm; append only) and
  `MACHINES[*].hoops`. The hoop list puts your machine's hoops first (`renderHoops`); `bestHoop` takes the smallest of
  them that fits, else the smallest of all. Values are the common sizes of each range, to confirm.
- **M. Usual preset**: preference `preset` ("b:<id>" / "u:<id>", own presets checked per account, a deleted one is
  forgotten). `applyStartValues`: usual fabric, then the preset (`Presets.applyStart`, without its size; locked
  built-ins are skipped), then a placement's fabric; all starting values. My account › *Start new designs with*.
- **N. Where sewing starts**: `StitchParams.sew_order` = standard (unchanged) | centre (first shape nearest the
  centre, the last colour sewn towards it) | cap (`planner.cap_order`: each colour by distance from the bottom centre).
  Every fabric sets it ("cap" for Cap, "standard" otherwise), so leaving the cap leaves its order; saved designs keep
  their values until a fabric is chosen again.
- **O. Foundation and curves**: fill foundation `by_size` (its own name: per-shape "auto" already means satin by
  width): edge run alone under `BY_SIZE_EDGE_ONLY_MM` (6 mm widest circle), else edge run + lattice.
  `running_curves`: shape outlines use `generators.ring_adaptive` (stitches within 0.15 mm of the outline, at least
  1 mm, at most the outline length) - a 2 mm hole becomes a pentagon instead of a triangle.
- **P. Check my stitches** without AI runs like an update (no overlay) and lists only findings the Checks do not show,
  with their changes; with AI it is unchanged.

## 3. Tests
- New: `tests/test_checks.py`, `tests/test_style_groups.py`, `tests/test_help_coverage.py`, and preference tests in
  `tests/test_prefs.py`.
- Browser tests: `layout.mjs` and `phase5.mjs` only had the section's new name changed.
- Stitch output is unchanged: the golden plans weren't touched.
- F–J: `tests/test_automation.py` (artwork analysis, `/analysis`, tone and presets, size / keep fixes, same thread,
  colour order end to end, compensation by width). Two older tests now state the exact rule: the only change offered
  for skipped marks keeps them (`test_text_detail.py`, `test_checks.py`).
- K–P: `tests/test_automation_more.py`. The golden plans prove every new option is byte-identical when off.

## 4. Open
- Confirm the machine speeds and hoops (`prefs.MACHINES[*].spm`, `hoops`).
- Sew-outs: Where sewing starts (centre, cap - the Cap fabric chooses it), Automatic-by-size fill foundation, shorter
  stitches on tight curves.
- Sew-out for *Compensation grows with satin width* (`PULL_REF_WIDTH_MM`, `PULL_SCALE`) before any preset or fabric
  turns it on; the tone thresholds for High contrast / Soft colours are first guesses to tune with real pictures.
- Design-level warnings (`Design.warnings`, from preparing the picture) stay plain text: they are stored with the
  design, and changing them would change saved designs.
