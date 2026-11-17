// Headless browser smoke test of the app UX phase (docs/APP_UX_PLAN.md): Export (your machine first, worksheet
// when needed, Download always in view, checks with actions), the start screen (placement step, paste),
// first run, phones and tablets, account and admin.
// Usage: node tools/smoke/appux.mjs [out-dir]      (see tools/smoke/README.md)
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
    if (msg.method === "Page.javascriptDialogOpening") { // never expected here: note it and close it
      errors.push(`unexpected ${msg.params.type}: ${msg.params.message}`);
      send("Page.handleJavaScriptDialog", { accept: false });
    }
  };
}
const send = (method, params = {}) => new Promise((res) => { const i = ++id; pending.set(i, res); ws.send(JSON.stringify({ id: i, method, params })); });
async function ev(expr) {
  // a frozen page or an open alert / confirm would wait for ever: give up after 30 s, naming the step
  const r = await Promise.race([send("Runtime.evaluate", { expression: expr, awaitPromise: true, returnByValue: true }),
    sleep(30000).then(() => { throw new Error(`no answer from the page within 30 s: ${expr.slice(0, 160)}`); })]);
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
const check = (name, ok, detail = "") => {
  const line = `${ok ? "PASS" : "FAIL"} ${name}${detail ? " – " + detail : ""}`;
  results.push(line);
  if (process.env.SMOKE_LIVE) console.log(line);
};
const idle = `working.length === 0 && !Jobs.pending()`;
const change = (el, v) => `{ const e = ${el}; e.value = ${JSON.stringify(v)}; e.dispatchEvent(new Event("change", { bubbles: true })); } true`;
const f = `$("stitchForm").elements`;
async function signIn(login, password) {
  await ev(`$("loginEmail").value = ${JSON.stringify(login)}; $("loginPassword").value = ${JSON.stringify(password)}; $("loginForm").requestSubmit(); true`);
  await waitFor(`!!Auth.user() && !document.body.classList.contains("signed-out")`);
}
async function openExample() {
  await ev(`$("btnExample").click(); true`);
  await waitFor(`!!state.preview && ${idle}`, 90000);

}

async function mouse(type, x, y, button = "none") {
  await send("Input.dispatchMouseEvent", { type, x, y, button, clickCount: type === "mouseMoved" ? 0 : 1 });
}
const MODS = { alt: 1, ctrl: 2, meta: 4, shift: 8 };
async function key(k, { code, mods = [] } = {}) {
  const modifiers = mods.reduce((a, m) => a | MODS[m], 0);
  const vk = k.length === 1 ? k.toUpperCase().charCodeAt(0) : { Escape: 27, Enter: 13 }[k] || 0;
  await send("Input.dispatchKeyEvent", { type: "keyDown", key: k, code: code || "", modifiers, windowsVirtualKeyCode: vk, text: k.length === 1 ? k : undefined });
  await send("Input.dispatchKeyEvent", { type: "keyUp", key: k, code: code || "", modifiers, windowsVirtualKeyCode: vk });
  await sleep(120);
}
const size = (w, h, mobile = false) => send("Emulation.setDeviceMetricsOverride", { width: w, height: h, deviceScaleFactor: 1, mobile });
const pane = (p) => ev(`document.querySelector('[data-pane="${p}"]').click(); true`);
const inView = (id) => ev(`(() => { const r = $(${JSON.stringify(id)}).getBoundingClientRect(); return r.width > 0 && r.top >= 0 && r.bottom <= innerHeight + 1 && r.left >= 0 && r.right <= innerWidth + 1; })()`);
const picked = `[...$("exports").querySelectorAll("input:checked")].map((b) => b.value).sort().join(",")`;
const exportReady = `$("exportDialog").open && ${idle} && !$("btnDownload").disabled`;
async function openExport() {
  await ev(`$("btnExport").click(); true`);
  await waitFor(exportReady, 60000); await sleep(300);
}
const setMachine = async (m) => { await ev(change(`$("expMachine")`, m)); await sleep(500); };

try {
  await connect();
  await send("Runtime.enable"); await send("Log.enable"); await send("Page.enable");
  await size(1440, 900);
  await send("Page.navigate", { url: APP });
  await waitFor(`document.readyState === "complete" && !document.body.classList.contains("booting")`);
  await waitFor(`document.body.classList.contains("signed-out") || !!Auth.user()`);
  if (await ev(`document.body.classList.contains("signed-out")`)) await signIn(EMAIL, PASSWORD);
  await ev(`Prefs.set("machine", null); Prefs.set("placement", null); true`); await sleep(300);
  await openExample();
  await pane("stitches"); await sleep(500);

  // ================================================ Batch A: Export
  await openExport();
  check("A: Download is in view without scrolling (1440 × 900)", await inView("btnDownload"));
  check("A: no machine chosen: DST, with the worksheet added", await ev(`${picked} === "dst,txt"`), await ev(picked));
  check("A: the button says what you get", await ev(`$("btnDownload").textContent === "Download DST + worksheet"`), await ev(`$("btnDownload").textContent`));
  check("A: the worksheet says why it was added", await ev(`!$("exports").querySelector(".exp-auto").hidden && /DST has no thread colours/.test($("exports").querySelector(".exp-auto").textContent)`));
  check("A: other machine formats folded away", await ev(`(() => { const d = $("exports").querySelector(".exp-more"); return !d.open && d.querySelector("summary").textContent.trim() === "Other machine formats (7)"; })()`),
    await ev(`(() => { const d = $("exports").querySelector(".exp-more"); return d.open + " " + d.querySelector("summary").textContent; })()`));
  check("A: no Select all", await ev(`!document.getElementById("btnExportAll")`));
  check("A: still three groups", await ev(`$("exports").querySelectorAll(".exp-grp").length === 3`));
  await shot("appux-export-desktop");

  await setMachine("brother");
  check("A: Brother: PES first and ticked, DST and the worksheet off", await ev(`${picked} === "pes" && $("exports").querySelector("label.rec input").value === "pes"`), await ev(picked));
  check("A: …marked for your machine", await ev(`/for your machine/.test($("exports").querySelector("label.rec").textContent)`));
  check("A: the machine is remembered for the account", await ev(`api("/api/me/prefs").then((p) => p.machine === "brother")`));
  await setMachine("");
  check("A: back to DST + worksheet", await ev(`${picked} === "dst,txt"`), await ev(picked));
  await ev(`$("exports").querySelector('input[value="txt"]').click(); true`); await sleep(200);
  check("A: unticking the worksheet keeps it off, a check offers it again", await ev(`${picked} === "dst" && !!$("expChecks").querySelector('[data-check-act="sheet"]')`), await ev(picked));
  await ev(`$("expChecks").querySelector('[data-check-act="sheet"]').click(); true`); await sleep(200);
  check("A: the check's button adds it", await ev(`${picked} === "dst,txt"`));
  await ev(`$("exports").querySelector('input[value="exp"]').click(); true`); await sleep(200);
  check("A: another format adds to the files", await ev(`${picked} === "dst,exp,txt"`), await ev(picked));
  check("A: three files: a ZIP", await ev(`$("btnDownload").textContent === "Download 3 files (.zip)"`), await ev(`$("btnDownload").textContent`));
  await ev(`$("exports").querySelector('input[value="exp"]').click(); true`); await sleep(200);

  const act = await ev(`$("expChecks").querySelector('[data-check-act="check"]') ? "check" : ""`);
  if (act) {
    await ev(`$("expChecks").querySelector('[data-check-act="check"]').click(); true`); await sleep(500);
    check("A: 'Show me' on the machine check opens the Check view", await ev(`!$("exportDialog").open && state.pane === "stitches" && state.mode === "check"`), await ev(`state.pane + " " + state.mode`));
    await ev(`document.querySelector('#viewMode [data-mode="real"]').click(); true`);
  } else check("A: 'Show me' (skipped: no machine check warning)", true);

  await size(390, 844, true); await sleep(500);
  await openExport();
  check("A: phone: Download is in view", await inView("btnDownload"));
  check("A: phone: the summary is a strip above the list", await ev(`$("expThumb").getBoundingClientRect().top < $("exports").getBoundingClientRect().top`));
  await shot("appux-export-phone");
  await ev(`$("exportDialog").close(); true`);
  await size(1440, 900); await sleep(400);
  // ================================================ Batch B: start screen and "What is it for?"
  const exampleFile = `fetch("/static/example-logo.png").then((r) => r.blob()).then((b) => new File([b], "my-logo.png", { type: "image/png" }))`;
  await ev(`$("btnNew").click(); true`); await sleep(400);
  check("B: start: the feature cards for someone new (no usual choices yet)", await ev(`!$("features").classList.contains("hidden") && $("startPrefs").classList.contains("hidden")`));
  check("B: the drop zone mentions paste", await ev(`/paste/i.test($("drop").textContent)`));
  check("B: no out-of-date hover tip", await ev(`!/hover or tap the/.test(document.body.innerText)`));
  await ev(`${exampleFile}.then((f) => PlaceStep.show(f)).then(() => true)`); await sleep(400);
  check("B: choosing a file asks what it is for", await ev(`!$("placeStep").classList.contains("hidden") && $("drop").offsetParent === null && $("placeChips").querySelectorAll("input").length === state.config.placements.length`));
  check("B: nothing remembered yet: Custom size, with its width field", await ev(`$("placeChips").querySelector("input:checked").value === "custom" && !$("placeCustomRow").classList.contains("hidden")`));
  check("B: the file name is shown", await ev(`$("placeFile").textContent === "my-logo.png"`));
  await ev(`$("placeChips").querySelector('input[value="left_chest"]').click(); true`); await sleep(150);
  check("B: a placement explains the size", await ev(`/about 90 mm/.test($("placeNote").textContent) && $("placeCustomRow").classList.contains("hidden")`));
  await shot("appux-place-step");
  await ev(`$("btnPlaceGo").click(); true`);
  await waitFor(`currentPage === "editor" && !!state.preview && ${idle}`, 90000); await sleep(300);
  const dims = await ev(`[state.design.width_mm, state.design.height_mm]`);
  const longest = Math.max(...dims);
  check("B: left chest: the longest side is about 90 mm from the first preparation", Math.abs(longest - 90) <= 9, dims.map((v) => Math.round(v)).join(" × ") + " mm");
  check("B: …in the smallest hoop that fits", await ev(`chosenHoop()?.name === "100 x 100 mm" && hoopFit(state.design.width_mm, state.design.height_mm).fits`), await ev(`chosenHoop()?.name`));
  check("B: the Size section shows the placement", await ev(`$("placement").value === "left_chest"`));
  check("B: one preparation, nothing to undo", await ev(`$("btnUndo").disabled`));
  check("B: the placement is remembered for the account", await ev(`api("/api/me/prefs").then((p) => p.placement === "left_chest")`));
  await ev(`$("width_mm").value = 70; $("width_mm").dispatchEvent(new Event("change", { bubbles: true })); true`); await sleep(200);
  check("B: a width typed by hand makes it a custom size", await ev(`$("placement").value === "custom"`));
  await waitFor(idle, 60000);

  await ev(`$("btnNew").click(); true`); await sleep(400);
  check("B: back on the start: your usual choice instead of the feature cards", await ev(`$("features").classList.contains("hidden") && /Left chest/.test($("startPrefs").textContent)`), await ev(`$("startPrefs").textContent`));
  await ev(`${exampleFile}.then((f) => { const dt = new DataTransfer(); dt.items.add(new File([f], "image.png", { type: "image/png" })); document.dispatchEvent(new ClipboardEvent("paste", { clipboardData: dt, bubbles: true })); return true; })`); await sleep(400);
  check("B: a pasted picture opens the step", await ev(`!$("placeStep").classList.contains("hidden") && $("placeFile").textContent === "pasted.png"`), await ev(`$("placeFile").textContent`));
  check("B: the remembered placement is pre-selected (Enter is enough)", await ev(`$("placeChips").querySelector("input:checked").value === "left_chest"`));
  await key("Escape", { code: "Escape" }); await sleep(200);
  check("B: Esc goes back to choosing a file", await ev(`$("placeStep").classList.contains("hidden") && $("drop").offsetParent !== null`));
  await size(390, 844, true); await sleep(400);
  await ev(`${exampleFile}.then((f) => PlaceStep.show(f)).then(() => true)`); await sleep(400);
  check("B: phone: the step fits (no sideways scroll), Create stitches in view", await ev(`document.documentElement.scrollWidth <= innerWidth`) && await inView("btnPlaceGo"));
  await shot("appux-place-phone");
  await ev(`$("btnPlaceBack").click(); true`); await sleep(200);
  await size(1440, 900); await sleep(300);
  await ev(`$("btnBackToOpen").click(); true`); await sleep(500);

  // ================================================ Batch C: first run
  await waitFor(`currentPage === "editor" && ${idle}`, 60000);
  check("C: help buttons follow the pointer for new users too", await ev(`!document.body.classList.contains("help-always")`));
  check("C: the analysis is a short summary (a later design: details closed)", await ev(`!$("smartInfo").classList.contains("hidden") && $("smartMore").hidden && $("smartInfo").offsetHeight < 84`),
    await ev(`$("smartInfo").offsetHeight + " px: " + $("smartInfo").querySelector(".smart-facts").textContent + " / " + [...$("smartInfo").children].map((c) => c.className + ":" + c.offsetHeight + ":" + getComputedStyle(c).display).join(", ")`));
  await ev(`$("smartInfo").querySelector(".smart-more-btn").click(); true`); await sleep(100);
  check("C: Details opens the explanations", await ev(`!$("smartMore").hidden && $("smartInfo").querySelector(".smart-more-btn").getAttribute("aria-expanded") === "true"`));
  await ev(`$("smartInfo").querySelector(".smart-x").click(); true`); await sleep(100);
  check("C: × hides it", await ev(`$("smartInfo").classList.contains("hidden")`));
  check("C: the suggested preset sits with the picker", await ev(`!$("presetSuggest").hidden && !!$("presetSuggest").querySelector("[data-suggest]") && /suggested/.test($("presetPick").selectedOptions[0]?.textContent + [...$("presetPick").options].map((o) => o.textContent).join())`));
  check("C: the size fields are above the fold (1440 × 900)", await ev(`$("width_mm").getBoundingClientRect().bottom < innerHeight - 60`), await ev(`Math.round($("width_mm").getBoundingClientRect().bottom) + " px"`));
  await shot("appux-first-run");

  // the fill / satin numbers follow what the design is sewn with
  await ev(`coreParams(new Set(["fill", "running"])); true`);
  check("C: no satin in the design: no satin gap", await ev(`$("stitchForm").elements.satin_spacing_mm.closest("label").hidden && !$("stitchForm").elements.fill_spacing_mm.closest("label").hidden`));
  await ev(`$("styleShowAll").click(); true`); await sleep(100);
  check("C: …unless every style is shown", await ev(`!$("stitchForm").elements.satin_spacing_mm.closest("label").hidden`));
  await ev(`$("styleShowAll").click(); renderKinds(); true`); await sleep(100);
  check("C: the example has both: both shown", await ev(`!$("stitchForm").elements.satin_spacing_mm.closest("label").hidden && !$("stitchForm").elements.fill_spacing_mm.closest("label").hidden`));

  // the first steps
  await ev(`localStorage.removeItem("guide"); Guide.init(); true`); await sleep(100);
  const g = (p) => `document.querySelector('#paneSwitch [data-pane="${p}"]')`;
  check("C: first steps: 1 Shapes (now), 2 Stitches, 3 Export", await ev(`${g("shapes")}.dataset.guide === "1" && ${g("shapes")}.classList.contains("guide-now") && ${g("stitches")}.dataset.guide === "2" && $("btnExport").dataset.guide === "3"`));
  await pane("shapes"); await sleep(100); await pane("stitches"); await sleep(300);
  check("C: going to Stitches finishes step 1", await ev(`!${g("shapes")}.dataset.guide && ${g("stitches")}.classList.contains("guide-now")`));
  await shot("appux-guide");
  await ev(`togglePlay(); true`); await sleep(300); await ev(`if (state.playing) togglePlay(); true`);
  check("C: playing finishes step 2", await ev(`!${g("stitches")}.dataset.guide && $("btnExport").classList.contains("guide-now")`));
  await openExport(); await ev(`$("exportDialog").close(); true`); await sleep(100);
  check("C: Export finishes the steps", await ev(`!document.querySelector("[data-guide]") && !$("guideOn").checked`));
  await ev(`$("guideOn").click(); true`); await sleep(100);
  check("C: Help and shortcuts can show them again", await ev(`${g("shapes")}.dataset.guide === "1"`));
  await ev(`$("guideOn").click(); true`); await sleep(100);
  check("C: …and switch them off", await ev(`!document.querySelector("[data-guide]")`));

  // ================================================ Batch D: phones and tablets
  const SIZES = [[1920, 1080], [1600, 900], [1440, 900], [1280, 800], [1024, 768], [820, 1180, true], [390, 844, true]];
  for (const [w, h, mobile] of SIZES) {
    await size(w, h, !!mobile); await sleep(450);
    for (const p of ["shapes", "stitches"]) {
      await pane(p); await sleep(250);
      const vb = await ev(`(() => { const b = $("viewBar"), r = b.getBoundingClientRect();
        const out = [...b.querySelectorAll("button, select")].filter((e) => e.offsetParent !== null && getComputedStyle(e).visibility !== "hidden")
          .filter((e) => { const q = e.getBoundingClientRect(); return q.right > r.right + 1 || q.left < r.left - 1; }).map((e) => e.id || e.className);
        return { h: Math.round(r.height), out }; })()`);
      check(`D: ${w}×${h} ${p}: the view bar is one row, everything inside`, vb.h <= 50 && vb.out.length === 0, `${vb.h} px ${vb.out.join(",")}`);
      check(`D: ${w}×${h} ${p}: the bottom bar is one row`, await ev(`$("edBar").getBoundingClientRect().height <= 64`), await ev(`Math.round($("edBar").getBoundingClientRect().height) + " px"`));
    }
  }
  await pane("stitches"); await sleep(200);
  // phone: the ⋯ menu instead of the zoom buttons
  check("D: phone: zoom buttons folded into ⋯", await ev(`$("btnViewMore").offsetParent !== null && $("btnFit").offsetParent === null`));
  await ev(`$("btnViewMore").click(); true`); await sleep(150);
  check("D: ⋯ opens zoom, fit and help", await ev(`!$("viewMore").hidden && /Fit to the screen/.test($("viewMore").textContent)`));
  await ev(`$("viewMore").querySelector('[data-click="btnKeys"]').click(); true`); await sleep(200);
  check("D: …which work (Help and shortcuts opens; menu closed)", await ev(`$("keysDialog").open && $("viewMore").hidden`));
  check("D: Auto-update is a preference there", await ev(`$("autoUpdatePref").checked === $("autoUpdate").checked`));
  await ev(`$("keysDialog").close(); true`);
  check("D: phone: panel buttons have labels, status is a dot", await ev(`getComputedStyle($("barSettings").querySelector("span")).display !== "none" && $("edStatusText").offsetParent === null && $("edDot").offsetParent !== null`));
  // phone: half-height sheet, the preview above it
  await ev(`$("barSettings").click(); true`); await sleep(500);
  const sheet = await ev(`(() => { const r = $("leftPanel").getBoundingClientRect(), m = document.querySelector(".ed-main").getBoundingClientRect(); return { top: r.top, h: innerHeight, main: m.bottom }; })()`);
  check("D: phone: Settings opens at about half height", sheet.top > sheet.h * 0.35 && sheet.top < sheet.h * 0.6, `top ${Math.round(sheet.top)} of ${sheet.h}`);
  check("D: …with the preview in view above it", sheet.main <= sheet.top + 2, `preview ends ${Math.round(sheet.main)}`);
  await shot("appux-phone-sheet");
  await ev(`document.querySelector("#leftPanel [data-sheet-size]").click(); true`); await sleep(400);
  check("D: ↕ makes it taller", await ev(`document.body.classList.contains("sheet-full") && $("leftPanel").getBoundingClientRect().top < innerHeight * 0.2`));
  await ev(`document.querySelector("#leftPanel [data-close-sheet]").click(); true`); await sleep(400);
  check("D: closing resets the size", await ev(`!document.body.classList.contains("sheet-full") && !document.body.classList.contains("sheet-left")`));
  // the file menu
  check("D: phone: the file menu reads File", await ev(`$("btnFileMenu").offsetParent !== null && /File/.test($("btnFileMenu").innerText)`));
  await size(1440, 900); await sleep(400);
  check("D: desktop: the file menu shows the file name", await ev(`$("hdrName").offsetParent !== null && $("hdrName").textContent === state.project.filename`));
  await ev(`$("btnFileMenu").click(); true`); await sleep(150);
  check("D: it offers New design and Close design", await ev(`!$("fileMenu").hidden && !!$("btnMenuNew") && !!$("btnMenuClose")`));
  await ev(`$("btnMenuNew").click(); true`); await sleep(300);
  check("D: New design goes to the start screen", await ev(`currentPage === "upload" && $("fileMenu").hidden`));
  await ev(`$("btnBackToOpen").click(); true`); await sleep(400);

} catch (e) {
  results.push("ERROR " + e.message);
  try { await shot("appux-error"); } catch (_) {}
} finally {
  console.log(results.join("\n"));
  console.log("\nexceptions:", errors.length ? errors.join("\n---\n") : "none");
  console.log("console errors/warnings:", logs.filter((l) => !l.includes("/api/auth/me")).join("\n") || "none");
  try { ws.close(); } catch (_) {}
  chrome.kill();
  process.exit(0);
}
