# Browser smoke tests

Two dependency-free scripts that drive a headless Chrome (or Edge) through the editor via the DevTools
protocol and print PASS / FAIL per check, script exceptions and console errors. Screenshots go to
`<out-dir>/shots`. They are a manual check, not part of CI (see `docs/ROADMAP.md` §8).

| Script | Covers |
|---|---|
| `workspace.mjs` | Phase 1: sign in, example upload, auto-update, view modes, playback, selection, shapes view, edit tools, threads, undo, manual mode, export dialog + download, tablet / phone layouts, reload, account page, "nothing to stitch" error, SVG upload (45 checks) |
| `controls.mjs` | Phase 2: undo/redo of settings and picture changes, changed markers, reset one / section, style settings, hoop outline + fit warning, Wilcom package, undo after reload (26 checks) |
| `extras.mjs` | Phase 4 batch A: stitch types, Original loaded on demand (also across a re-stitch), Shapes list (mouse + keyboard), shortcuts (real key presses, not while typing), full screen (33 checks) |
| `batchb.mjs` | Phase 4 batch B: density view + hover, grid in both views, panel resize (keys, drag, close / open, remembered, reset), shape menu (right-click, submenus, keyboard, multiple selection, Shift+F10), selection toolbar (position, actions, roving focus, hides while zooming / editing / narrow) (39 checks) |
| `batchc.mjs` | Phase 4 batch C: merge similar colours (range, pairs, count, Esc, merge in one step, one Undo), split view (handle, keys, drag, remembered, only in the stitch view), Calibrate 100 % (card box, saved, used by 100 %, Standard size, phone) (28 checks) |
| `presets.mjs` | Phase 3: preset picker, apply (values, one undo step, baseline, "Changed" badge), undo/redo of a preset, save, name clash (keep both), manage + delete, app defaults, account card, admin tiers + plan limits, Free account restrictions + lock popover, suggestion after upload, tablet / phone layout (39 checks; re-runnable on the same data) |

## Run

Needs Node 22+ (built-in `fetch` and `WebSocket`) and Chrome.

1. Start a **throwaway** server with its own data folder and a test admin (never your real data):
   ```bash
   DATA_DIR=/tmp/smokedata SUPER_ADMIN_EMAIL=smoke@example.com SUPER_ADMIN_PASSWORD=SmokeTest-12345 PORT=8765 python run.py
   ```
   PowerShell: `$env:DATA_DIR="$env:TEMP\smokedata"; $env:SUPER_ADMIN_EMAIL="smoke@example.com"; $env:SUPER_ADMIN_PASSWORD="SmokeTest-12345"; $env:PORT="8765"; python run.py`
2. In another shell:
   ```bash
   node tools/smoke/workspace.mjs ./smoke-out
   node tools/smoke/controls.mjs ./smoke-out
   node tools/smoke/presets.mjs ./smoke-out
   node tools/smoke/extras.mjs ./smoke-out
   node tools/smoke/batchb.mjs ./smoke-out
   node tools/smoke/batchc.mjs ./smoke-out
   ```

A script refuses to start when its debug port is already taken (a browser left by an interrupted run) instead of
attaching to that browser; interrupting a script closes its browser.

Settings (environment): `SMOKE_APP` (default `http://127.0.0.1:8765/`), `SMOKE_EMAIL`, `SMOKE_PASSWORD`
(the test admin above), `SMOKE_CHROME` (browser path; defaults to the usual Chrome location),
`SMOKE_DEBUG_PORT` (9333), `SMOKE_CHROME_ARGS` (extra browser flags, space-separated, e.g. `--no-sandbox` where the
browser sandbox cannot start). Each run starts with a fresh browser profile in `<out-dir>/chrome-profile`.
`smoke-out/` is not committed.
