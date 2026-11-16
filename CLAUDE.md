# CLAUDE.md

AI Embroidery Digitizer: raster (PNG/JPG/WEBP) and vector (SVG/AI/PDF) artwork → machine embroidery files
(Tajima DST first; EXP, U01, PES, JEF, VP3), plus SVG/PNG/worksheet/ZIP. FastAPI backend, no-build vanilla JS
front end, SQLite for accounts. `README.md` is the authoritative feature/API reference; read its
"How the stitch engine works" and "Extending" sections before changing stitching.
`docs/ROADMAP.md` is the editor redesign plan: status, how the editor code works, and detailed specs for the
remaining phases (presets built, professional extras next). Browser smoke tests: `tools/smoke/`.

## Commands

```bash
python run.py                                   # http://127.0.0.1:8000  (HOST, PORT, RELOAD=1)
python -m pytest -q -W error::DeprecationWarning # what CI runs; deprecations are errors
UPDATE_GOLDEN=1 python -m pytest tests/test_golden.py   # only for deliberate stitch output changes
HYPOTHESIS_PROFILE=deep python -m pytest tests/test_properties.py  # longer random search after generator changes
python -m embroidery_app.auth reset-password <email|username>
```

Windows dev box: venv at `.venv`; the shell is PowerShell (Git Bash also available).

## Request flow

`api.py` (routes, pydantic bodies, `auth.require_user`, same-origin middleware)
→ `service.py` (projects on disk under `DATA_DIR/projects/<12-hex id>`, ownership, undo/redo history,
in-memory `_plan_cache` keyed by design + params)
→ `ingest/` (raster.py / vector.py → `models.Design`, vector shapes in mm)
→ `stitch/planner.plan_stitches(design, params)` → `models.StitchPlan`
→ `export/` (embroidery.py via pystitch, vector.py SVG, preview.py JSON + PNG).

Core data types live in `models.py`: `Design` → `ColorLayer`s + `DesignObject`s (per-shape settings),
`StitchParams` (all tunables, `from_dict` clamps), `StitchPlan` → `ColorBlock` → `Run`.

## Accounts, plans, usage, privacy (server)

- `auth.py` sessions (sid, chosen length, revocable) + roles; `admin_api.py` Super Admin routes (each change → `audit.record`).
- `entitlements.effective(uid)` is the ONLY place to ask what an account may do (plan → override → role; expired
  subscription → default plan). Features are declared in `entitlements.FEATURES`, numeric limits in
  `entitlements.LIMITS` (`ent.limit_of(name)`, None = no limit); values live in the DB (plans / overrides `features`
  + `limits`). Never hardcode limits or check plans elsewhere.
- `presets.py`: settings presets. `KEYS` is the whitelist + ranges (mirrors the editor's ranges in app.js); every
  preset goes through `presets.clean` on save and on read. Built-ins live in `BUILTINS` (tier, values) and are checked
  by `tests/test_presets.py` + `tests/golden/presets.json` (`UPDATE_GOLDEN=1` after a deliberate change). Own presets:
  `custom_presets` + `max_custom_presets`, counted in the write transaction; after a downgrade they stay usable, only
  create / duplicate are refused. Premium built-in values are never sent without the tier.
- `usage.generation(uid, pid, feature)` wraps every stitch-output download: one charge per design (partial unique
  index `usage_one_charge`), atomic check+reserve, refunded on error. Re-downloads are free; SVG artwork is free.
- `temp_projects` is the ownership authority for projects (not meta.json). Projects are temporary: deleted on new
  upload in the same session, close, logout, session end, page release + grace, idle/max age (`maintenance.py`).
  Never store project content, file names or prompts in the DB.
- `user_ai.resolve` decides AI credentials: own key, or the server's `.env` keys only with the `server_ai` feature.
- Usage/analytics records hold metadata only; `analytics.py` aggregates in SQL.
- Front end: `account.js` (chip, My account, fetch-based downloads with quota dialog), `admin.js` (console),
  `charts.js` (SVG charts), views switch via body classes `view-account` / `view-admin`.

## Stitch engine map (`embroidery_app/stitch/`)

- `planner.py`: classification (`classify`), settings precedence **shape → colour → smart → design**
  (`resolve_settings`, `shape_params`), sewing order (nearest-neighbour + 2-opt), knockdown, blend,
  `assemble` (travel/jump/trim/tie-in/tie-off).
- `registry.py` + `methods.py`: every stitch method is a registered `StitchMethod` with a generator
  `(item, angle, params, start) -> list[Run]`; the planner dispatches through the registry.
- `rows.py`/`generators.py`: scan lines, tatami, satin, running, underlay primitives.
- Styles: `fills.py`, `outlines.py`, `decorative.py` (+ `library/motifs.json`), `guided.py` (guide lines),
  `photo.py`, `strokes.py` (small lettering), `smart.py` (smart direction), `underlay.py`.
- `optimize.py`: smart stitch reduction (removes drops only, coverage-checked, falls back if it fails).
- `safety.py`: `cap_stitch_length` guarantees ≤ 7 mm stitches whatever a generator does.

## Conventions and invariants

- Units: mm everywhere in the engine; exports use 0.1 mm. Angles are y-down (45° = top-left → bottom-right).
- Stitching must be deterministic (fixed seeds). With an option off, output must stay byte-identical.
  `tests/golden/plans.json.gz` enforces this; don't regenerate it to make a failing test pass.
- New per-shape settings must survive merge, undo and re-extract (any Size / Colours / Picture change,
  `_carry_shape_settings`).
- AI is optional everywhere: built-in result first (`ai/builtin.py`), AI refines it (`ai/tasks.py`), and any AI
  failure falls back with a note. AI answers are validated and clamped. Providers are one file each in
  `ai/providers/` with `@register`; plain REST over `httpx2`, no vendor SDKs.
- Per-user AI keys are AES-GCM encrypted (`user_ai.py`, `APP_SECRET_KEY`) and never returned to the browser
  or logged.
- Every project route is owner-checked; admin routes need the Super Admin role. Keep it that way for new routes.
- DB schema changes: append to `db.MIGRATIONS` (applied on start); never edit existing entries.
- Editor (`static/app.js`): one workspace, panes `shapes` / `stitches`. `Jobs` keeps the preview current (extract,
  then stitch; debounced, one request at a time, latest values win; manual mode for slow designs); `work()` runs
  design changes one at a time without blocking the screen. Use them, not `busy()`, for edits. Export waits for
  `Jobs.flush()` so downloads match the screen; the server also serialises stitch/export per project.
  `History` is the one undo timeline: record design changes (`History.designChange`) and stitch-setting changes
  (`History.stitchChange`); extracts record themselves. Settings are compared with `state.baseline` (markers,
  reset). Style-only stitch settings are declared in `STYLE_PARAMS`.
- User-facing help text belongs in `static/help.js`. Front-end scripts load in order:
  help.js, auth.js, charts.js, account.js, admin.js, shapes.js, ai.js, presets.js, app.js (plain globals, no bundler).
  `presets.js` applies presets through app.js (`setStitchValues`, `History`, `Jobs.extract({step})` = one undo step).
- Dependencies were chosen deliberately (pystitch not pyembroidery, PyMuPDF not svgelements, httpx2 not httpx,
  `import pymupdf` not `fitz`). PyMuPDF is AGPL; see README Licences.

## Tests

`tests/conftest.py` sets a temp `DATA_DIR`, blanks AI keys, sets Super Admin env vars, overrides
`auth.require_user` with a test user (unlimited override, so workflow tests are not quota-limited) and resets rate
limits per test. Real-login tests use `tests/accounts.py` (`real_login` fixture, `new_user`, `stitched_project`):
test_auth, test_plans_usage, test_privacy, test_admin, test_ai_accounts. The DB is shared across the session, so
use unique emails and restore any plan/setting you change. Fixtures: `logo_rgba`, `logo_png`,
`svg_text`. AI tests are mocked; no network.

## Deploy

`.github/workflows/deploy.yml`: tests then deploy to EC2 on version tags on `main` only
(see `deploy/ec2/README.md`). Needs valid `SUPER_ADMIN_*` secrets; startup must never fail on them.
