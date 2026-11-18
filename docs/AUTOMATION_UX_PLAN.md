# Automation, grouping and help: findings and what was built

Status: **all batches (A–E) built** (2026-11-18). It's Phase 8 in [ROADMAP.md](ROADMAP.md) §1 and follows
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

## 3. Tests
- New: `tests/test_checks.py`, `tests/test_style_groups.py`, `tests/test_help_coverage.py`, and preference tests in
  `tests/test_prefs.py`.
- Browser tests: `layout.mjs` and `phase5.mjs` only had the section's new name changed.
- Stitch output is unchanged: the golden plans weren't touched.

## 4. Open
- Confirm the machine speeds (`prefs.MACHINES[*].spm`).
- Design-level warnings (`Design.warnings`, from preparing the picture) stay plain text: they are stored with the
  design, and changing them would change saved designs.
