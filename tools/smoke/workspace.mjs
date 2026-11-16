// Headless browser smoke test of the editor via the Chrome DevTools protocol (no dependencies).
// Usage: node tools/smoke/workspace.mjs [out-dir]     (see tools/smoke/README.md)
import { spawn } from "node:child_process";
import { writeFileSync, mkdirSync, rmSync } from "node:fs";

const SP = process.argv[2] || "./smoke-out";
const APP = process.env.SMOKE_APP || "http://127.0.0.1:8765/";
const EMAIL = process.env.SMOKE_EMAIL || "smoke@example.com";
const PASSWORD = process.env.SMOKE_PASSWORD || "SmokeTest-12345";
const CHROME = process.env.SMOKE_CHROME || {
  win32: "C:/Program Files/Google/Chrome/Application/chrome.exe",
  darwin: "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
}[process.platform] || "google-chrome";
const PORT = +(process.env.SMOKE_DEBUG_PORT || 9333);
mkdirSync(`${SP}/shots`, { recursive: true });
rmSync(`${SP}/chrome-profile`, { recursive: true, force: true }); // fresh: no cached old scripts

// a browser left over from an interrupted run would answer instead of ours: never attach to it
try {
  await fetch(`http://127.0.0.1:${PORT}/json/version`);
  console.error(`Port ${PORT} is already used by another browser (an interrupted run?). Close it or set SMOKE_DEBUG_PORT.`);
  process.exit(2);
} catch (_) {}
const chrome = spawn(CHROME, [`--remote-debugging-port=${PORT}`, `--user-data-dir=${SP}/chrome-profile`, "--headless=new",
  "--no-first-run", "--no-default-browser-check", "--window-size=1440,900",
  ...(process.env.SMOKE_CHROME_ARGS || "").split(" ").filter(Boolean), "about:blank"], { stdio: "ignore" });
for (const sig of ["SIGINT", "SIGTERM"]) process.on(sig, () => { chrome.kill(); process.exit(1); });
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

let ws, id = 0;
const pending = new Map();
const errors = [];
const logs = [];
async function connect() {
  for (let i = 0; i < 50; i++) {
    try {
      const list = await (await fetch(`http://127.0.0.1:${PORT}/json/list`)).json();
      const page = list.find((t) => t.type === "page");
      if (page) { ws = new WebSocket(page.webSocketDebuggerUrl); break; }
    } catch (_) {}
    await sleep(200);
  }
  await new Promise((r) => (ws.onopen = r));
  ws.onmessage = (m) => {
    const msg = JSON.parse(m.data);
    if (msg.id && pending.has(msg.id)) { pending.get(msg.id)(msg); pending.delete(msg.id); return; }
    if (msg.method === "Runtime.exceptionThrown") errors.push(msg.params.exceptionDetails.exception?.description || msg.params.exceptionDetails.text);
    if (msg.method === "Runtime.consoleAPICalled" && ["error", "warning"].includes(msg.params.type))
      logs.push(`${msg.params.type}: ${msg.params.args.map((a) => a.value ?? a.description).join(" ")}`);
    if (msg.method === "Log.entryAdded" && msg.params.entry.level === "error") logs.push(`log: ${msg.params.entry.text} ${msg.params.entry.url || ""}`);
  };
}
const send = (method, params = {}) => new Promise((res) => { const i = ++id; pending.set(i, res); ws.send(JSON.stringify({ id: i, method, params })); });
async function ev(expr) {
  const r = await send("Runtime.evaluate", { expression: expr, awaitPromise: true, returnByValue: true });
  if (r.result?.exceptionDetails) throw new Error(`eval failed: ${expr}\n${r.result.exceptionDetails.exception?.description}`);
  return r.result?.result?.value;
}
async function waitFor(expr, ms = 60000) {
  const t0 = Date.now();
  while (Date.now() - t0 < ms) { if (await ev(expr)) return true; await sleep(250); }
  throw new Error(`timeout waiting for ${expr}`);
}
async function shot(name) {
  const r = await send("Page.captureScreenshot", { format: "png" });
  writeFileSync(`${SP}/shots/${name}.png`, Buffer.from(r.result.data, "base64"));
}
const results = [];
const check = (name, ok, detail = "") => results.push(`${ok ? "PASS" : "FAIL"} ${name}${detail ? " – " + detail : ""}`);

try {
  await connect();
  await send("Runtime.enable"); await send("Log.enable"); await send("Page.enable");
  await send("Emulation.setDeviceMetricsOverride", { width: 1440, height: 900, deviceScaleFactor: 1, mobile: false });
  await send("Page.navigate", { url: APP });
  await waitFor(`document.readyState === "complete" && !document.body.classList.contains("booting")`);
  await waitFor(`document.body.classList.contains("signed-out") || !!Auth.user()`);
  if (await ev(`document.body.classList.contains("signed-out")`)) {
    await ev(`$("loginEmail").value = ${JSON.stringify(EMAIL)}; $("loginPassword").value = ${JSON.stringify(PASSWORD)}; $("loginForm").requestSubmit(); true`);
    await waitFor(`!!Auth.user() && !document.body.classList.contains("signed-out")`);
  }
  check("signed in", true);
  await ev(`$("btnExample").click(); true`);
  await waitFor(`!!state.design`, 90000);
  check("design prepared", true, await ev(`state.design.layers.length + " colours"`));
  await waitFor(`!!state.preview && !Jobs.pending()`, 90000);
  await sleep(500);
  check("stitches created", true, await ev(`state.preview.stats.stitches + " stitches"`));
  check("auto switched to stitches pane", await ev(`state.pane === "stitches"`));
  check("status up to date", await ev(`$("edStatusText").textContent === "Up to date"`), await ev(`$("edStatusText").textContent`));
  check("no page scroll in editor", await ev(`document.documentElement.scrollHeight <= innerHeight + 1`), await ev(`document.documentElement.scrollHeight + " vs " + innerHeight`));
  check("canvas has size", await ev(`$("canvas").clientWidth > 300 && $("canvas").clientHeight > 300`), await ev(`$("canvas").clientWidth + "x" + $("canvas").clientHeight`));
  check("bottom bar visible", await ev(`(() => { const r = $("edBar").getBoundingClientRect(); return r.bottom <= innerHeight + 1 && r.height > 30; })()`));
  check("export button is not stretched", await ev(`$("btnExport").getBoundingClientRect().width < 200`), await ev(`Math.round($("btnExport").getBoundingClientRect().width) + "px"`));
  check("stats in right panel", await ev(`$("stats").children.length === 7`));
  check("threads listed", await ev(`$("layers").querySelectorAll(".thr").length === state.design.layers.length`));
  check("zoom label", await ev(`/%$/.test($("zoomPct").textContent)`), await ev(`$("zoomPct").textContent`));
  await shot("1-stitches-desktop");

  // a stitch setting changes the stitches by itself
  const before = await ev(`state.preview.stats.stitches`);
  await ev(`{ const f = $("stitchForm").elements.fill_spacing_mm; f.value = "0.6"; f.dispatchEvent(new Event("change", { bubbles: true })); } true`);
  await waitFor(`Jobs.pending()`, 3000).catch(() => {});
  await waitFor(`!Jobs.pending() && state.preview.stats.stitches !== ${before}`, 60000);
  check("auto-update after setting change", true, `${before} → ${await ev(`state.preview.stats.stitches`)}`);

  // view modes and playback
  for (const m of ["direction", "type", "check", "real"]) await ev(`document.querySelector('#viewMode [data-mode="${m}"]').click(); Render.now(); true`);
  await ev(`$("btnPlay").click(); true`); await sleep(600); await ev(`$("btnPlay").click(); true`);
  check("playback ran", await ev(`state.upto > 0`));
  await ev(`state.upto = activeOps().length / 4; $("scrub").value = state.upto; draw(); true`);
  await ev(`$("btnZoomIn").click(); $("btnZoomIn").click(); true`); await sleep(300);
  check("zoom in changes label", await ev(`$("zoomPct").textContent !== "–"`), await ev(`$("zoomPct").textContent`));
  await ev(`$("btnFit").click(); true`);

  // selection in stitches, then shapes pane
  const firstId = await ev(`(() => { const o = state.design && document.querySelector("#svgBox [data-oid]"); return o ? o.dataset.oid : null; })()`);
  await ev(`Shapes.select(["${firstId}"]); true`);
  check("selection panel filled", await ev(`$("shapePanel").textContent.includes("selected")`));
  check("selection count badge", await ev(`$("selCount").textContent === "1"`));
  await ev(`document.querySelector('.ed-views [data-pane="shapes"]').click(); true`);
  await sleep(400);
  check("shapes pane shows svg", await ev(`state.pane === "shapes" && $("svgBox").querySelector("svg").getBoundingClientRect().width > 300`));
  check("selection kept across panes", await ev(`$("shapePanel").textContent.includes("1 shape selected")`));
  await ev(`$("showRef").click(); true`); await sleep(500);
  await shot("2-shapes-desktop");
  // edit shape from the panel opens the edit bar
  await ev(`document.querySelector('#shapePanel [data-action="edit"]')?.click(); true`);
  check("edit bar opens", await ev(`!$("editBar").classList.contains("hidden") && $("editBar").querySelectorAll("svg.ic").length === 5`));
  await shot("3-edit-shape");
  await ev(`document.querySelector('#editBar [data-edit="done"]').click(); true`);

  // thread list: expand, rename (a design change -> stitches follow)
  await ev(`document.querySelector("#layers [data-expand]").click(); true`);
  check("thread details open", await ev(`!document.querySelector("#layers .thr-more").hidden`));
  const name0 = await ev(`state.design.layers[0].name`);
  await ev(`{ const n = document.querySelector("#layers .thr-name"); n.value = "Renamed"; n.dispatchEvent(new Event("change", { bubbles: true })); } true`);
  await waitFor(`state.design.layers[0].name === "Renamed" && working.length === 0 && !Jobs.pending()`, 30000);
  check("rename saved + undo enabled", await ev(`!$("btnUndo").disabled`), `${name0} → Renamed`);
  check("thread details stay open after update", await ev(`!document.querySelector("#layers .thr-more").hidden`));
  await ev(`$("btnUndo").click(); true`);
  await waitFor(`state.design.layers[0].name === ${JSON.stringify(name0)} && working.length === 0 && !Jobs.pending()`, 30000);
  check("undo restores", true);

  // picture setting -> re-extract -> stitches
  await ev(`$("contrast").value = "20"; $("contrast").dispatchEvent(new Event("change")); true`);
  await waitFor(`Jobs.pending()`, 3000).catch(() => {});
  await waitFor(`!Jobs.pending()`, 90000);
  check("re-extract + stitch after picture change", await ev(`$("edStatusText").textContent === "Up to date"`), await ev(`$("edStatusText").textContent`));

  // manual mode
  await ev(`$("autoUpdate").click(); true`);
  await ev(`{ const f = $("stitchForm").elements.fill_spacing_mm; f.value = "0.45"; f.dispatchEvent(new Event("change", { bubbles: true })); } true`);
  await sleep(600);
  check("manual mode marks stale", await ev(`Jobs.stale() && !$("btnUpdate").hidden`), await ev(`$("edStatusText").textContent`));
  await shot("4-manual-stale");

  // export dialog brings the stitches up to date first
  await ev(`$("btnExport").click(); true`);
  await waitFor(`$("exportDialog").open && !Jobs.pending() && !$("btnDownload").disabled`, 60000);
  check("export dialog up to date", await ev(`!Jobs.stale() && state.preview.stats.stitches > 0`), await ev(`$("btnDownload").textContent`));
  check("export lists groups", await ev(`$("exports").querySelectorAll(".exp-grp").length === 3`));
  check("export summary facts", await ev(`$("expFacts").children.length >= 8`));
  check("quota note shown", await ev(`!$("quotaNote").classList.contains("hidden")`), await ev(`$("quotaNote").textContent`));
  await sleep(300);
  await shot("5-export-dialog");
  // download DST through the real route (fetch, same as Account.download)
  const dl = await ev(`fetch("/api/projects/" + state.project.id + "/export/dst").then((r) => r.status + " " + r.headers.get("content-disposition"))`);
  check("dst download works", dl.startsWith("200"), dl);
  await ev(`$("exportDialog").close(); $("autoUpdate").click(); document.querySelector('.ed-views [data-pane="stitches"]').click(); true`);

  // tablet + phone layouts
  await send("Emulation.setDeviceMetricsOverride", { width: 1024, height: 768, deviceScaleFactor: 1, mobile: false });
  await sleep(500);
  check("tablet: right panel is a sheet", await ev(`getComputedStyle($("rightPanel")).position === "fixed" && getComputedStyle($("barInfo")).display !== "none"`));
  await ev(`$("barInfo").click(); true`); await sleep(400);
  await shot("6-tablet-sheet");
  await ev(`$("sheetBackdrop").click(); true`);
  await send("Emulation.setDeviceMetricsOverride", { width: 390, height: 844, deviceScaleFactor: 2, mobile: true });
  await sleep(600);
  check("phone: no horizontal scroll", await ev(`document.documentElement.scrollWidth <= innerWidth + 1`), await ev(`document.documentElement.scrollWidth + " vs " + innerWidth`));
  check("phone: canvas visible", await ev(`$("canvas").clientHeight > 250`), await ev(`$("canvas").clientWidth + "x" + $("canvas").clientHeight`));
  await send("Emulation.setPageScaleFactor", { pageScaleFactor: 1 }); await sleep(300);
  await shot("7-phone");
  await ev(`$("barSettings").click(); true`); await sleep(400);
  await shot("8-phone-settings");
  await ev(`document.querySelector("#leftPanel [data-close-sheet]").click(); true`);

  // reload: the design opens again in the same pane
  await send("Emulation.setDeviceMetricsOverride", { width: 1440, height: 900, deviceScaleFactor: 1, mobile: false });
  await send("Page.reload");
  await waitFor(`document.readyState === "complete" && !!Auth.user?.()`, 30000);
  await waitFor(`!!state.preview && !Jobs.pending()`, 90000);
  check("reload reopens design", await ev(`currentPage === "editor"`), await ev(`state.pane`));
  // account page and back
  await ev(`Account.open(); true`); await sleep(400);
  check("account view hides editor", await ev(`getComputedStyle($("editor")).display === "none"`));
  await ev(`Views.close(); true`); await sleep(400);
  check("back to editor", await ev(`getComputedStyle($("editor")).display === "grid" && $("canvas").clientWidth > 300`));
  await shot("9-after-reload");

  // nothing to stitch: the error shows in the status, the old preview stays, export waits
  const keep = await ev(`state.preview.stats.stitches`);
  await ev(`state.design.layers.forEach((l) => (l.enabled = false)); saveLayers(); true`);
  await waitFor(`working.length === 0 && !Jobs.pending() && !!Jobs.error()`, 60000);
  check("error shown in status", await ev(`$("edDot").dataset.level === "error" && !$("btnUpdate").hidden`), await ev(`$("edStatusText").textContent`));
  check("old preview kept on error", await ev(`state.preview.stats.stitches === ${keep}`));
  await ev(`$("btnExport").click(); true`);
  await sleep(1500);
  check("export blocks stitch files while not up to date", await ev(`$("btnDownload").disabled && $("expState").textContent.length > 0`), await ev(`$("expState").textContent`));
  await ev(`[...document.querySelectorAll("#exports input")].forEach((b) => { if (b.checked !== (b.value === "svg")) b.click(); }); true`);
  check("artwork SVG still downloadable", await ev(`!$("btnDownload").disabled`), await ev(`$("btnDownload").textContent`));
  await ev(`$("exportDialog").close(); true`);
  await ev(`$("btnUndo").click(); true`);
  await waitFor(`working.length === 0 && !Jobs.pending() && !Jobs.error()`, 60000);
  check("undo recovers", await ev(`$("edStatusText").textContent === "Up to date"`));

  // an artwork file (SVG): its own settings, no picture settings, no AI clean-up
  await ev(`upload(new File(['<svg xmlns="http://www.w3.org/2000/svg" width="200" height="100" viewBox="0 0 200 100"><rect x="5" y="5" width="90" height="90" fill="#c00"/><circle cx="150" cy="50" r="40" fill="#036"/></svg>'], "art.svg", { type: "image/svg+xml" })); true`);
  await waitFor(`state.project?.filename === "art.svg" && !!state.preview && !Jobs.pending()`, 90000);
  check("svg: artwork settings only", await ev(`$("rasterOpts").classList.contains("hidden") && !$("vectorOpts").classList.contains("hidden") && $("aiBox").classList.contains("hidden")`));
  check("svg: header shows new file", await ev(`$("hdrName").textContent === "art.svg"`));
  await ev(`$("knockout").click(); true`);
  await waitFor(`Jobs.pending()`, 3000).catch(() => {});
  await waitFor(`!Jobs.pending()`, 60000);
  check("svg: option change updates", await ev(`$("edStatusText").textContent === "Up to date"`), await ev(`$("edStatusText").textContent`));
  await shot("10-svg");
} catch (e) {
  results.push("ERROR " + e.message);
  try { await shot("error"); } catch (_) {}
} finally {
  console.log(results.join("\n"));
  console.log("\nexceptions:", errors.length ? errors.join("\n---\n") : "none");
  console.log("console errors/warnings:", logs.length ? logs.join("\n") : "none");
  try { ws.close(); } catch (_) {}
  chrome.kill();
  process.exit(0);
}
