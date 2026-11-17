// Headless browser smoke test of the editor layout (docs/EDITOR_UX_PLAN.md): nothing in the bottom bar overlaps at
// any screen size, no sideways page scroll, plus the Batch A clean-ups (stats wording, selection panel, thread rows).
// Usage: node tools/smoke/layout.mjs [out-dir]      (see tools/smoke/README.md)
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

const SIZES = [[1920, 1080], [1600, 900], [1440, 900], [1280, 800], [1024, 768], [820, 1180, true], [390, 844, true]];
const pane = (p) => ev(`document.querySelector('[data-pane="${p}"]').click(); true`);
// every visible item of the bottom bar, and the pairs whose boxes overlap (more than 1 px each way)
const barOverlaps = () => ev(`(() => {
  const items = [...document.querySelectorAll("#edBar > .ed-grp > *, #edBar > button")].filter((e) => {
    const s = getComputedStyle(e), r = e.getBoundingClientRect(); return s.display !== "none" && s.visibility !== "hidden" && r.width > 0 && r.height > 0; });
  const name = (e) => e.id || e.className || e.tagName;
  const bad = [];
  for (let i = 0; i < items.length; i++) for (let j = i + 1; j < items.length; j++) {
    const a = items[i].getBoundingClientRect(), b = items[j].getBoundingClientRect();
    if (Math.min(a.right, b.right) - Math.max(a.left, b.left) > 1 && Math.min(a.bottom, b.bottom) - Math.max(a.top, b.top) > 1) bad.push(name(items[i]) + " × " + name(items[j]));
  }
  const bar = $("edBar").getBoundingClientRect();
  items.forEach((e) => { const r = e.getBoundingClientRect(); if (r.right > bar.right + 1 || r.left < bar.left - 1) bad.push(name(e) + " outside the bar"); });
  return bad; })()`);
const clipped = (id) => ev(`(() => { const e = $(${JSON.stringify(id)}); return e.offsetParent !== null && e.scrollWidth > e.clientWidth + 1; })()`);

try {
  await connect();
  await send("Runtime.enable"); await send("Log.enable"); await send("Page.enable");
  await send("Emulation.setDeviceMetricsOverride", { width: 1440, height: 900, deviceScaleFactor: 1, mobile: false });
  await send("Page.navigate", { url: APP });
  await waitFor(`document.readyState === "complete" && !document.body.classList.contains("booting")`);
  await waitFor(`document.body.classList.contains("signed-out") || !!Auth.user()`);
  if (await ev(`document.body.classList.contains("signed-out")`)) await signIn(EMAIL, PASSWORD);
  await openExample();

  // ------------------------------------------------ bottom bar and page at every size, both views
  for (const [w, h, mobile] of SIZES) {
    await send("Emulation.setDeviceMetricsOverride", { width: w, height: h, deviceScaleFactor: mobile ? 2 : 1, mobile: !!mobile });
    for (const p of ["stitches", "shapes"]) {
      await pane(p); await sleep(400);
      const bad = await barOverlaps();
      check(`${w}×${h} ${p}: bottom bar items don't overlap`, bad.length === 0, bad.join(", "));
      check(`${w}×${h} ${p}: no sideways page scroll`, await ev(`document.documentElement.scrollWidth <= innerWidth`),
        await ev(`document.documentElement.scrollWidth + " > " + innerWidth`));
      if (w >= 900) check(`${w}×${h} ${p}: status text not cut off`, !(await clipped("edStatusText")), await ev(`$("edStatusText").textContent`));
      await shot(`layout-${w}x${h}-${p}`);
    }
  }
  await send("Emulation.setDeviceMetricsOverride", { width: 1920, height: 1080, deviceScaleFactor: 1, mobile: false });
  await pane("stitches"); await sleep(400);
  check("sewing progress is short, the full sentence is for screen readers",
    await ev(`/sewn · colour \\d+ of \\d+$/.test($("scrubInfo").textContent) && /stitches sewn/.test($("scrub").getAttribute("aria-valuetext"))`),
    await ev(`$("scrubInfo").textContent`));
  check("the bar no longer repeats the design figures", await ev(`!document.getElementById("edFacts")`));

  // ------------------------------------------------ design figures
  check("figures use the singular for one", await ev(`![...$("stats").querySelectorAll("div")].some((d) => d.querySelector("b").textContent.trim() === "1" && /(colours|cuts|changes|stitches)/.test(d.querySelector("span").textContent))`),
    await ev(`$("stats").textContent`));

  // ------------------------------------------------ selection panel: one line when nothing is selected
  await ev(`Shapes.select([]); true`); await sleep(200);
  check("empty selection: one hint, no colour chips, no list, no reset", await ev(`(() => { const p = $("shapePanel");
    return /Click a shape/.test(p.textContent) && !p.querySelector(".chip, .sp-list, [data-action='reset-all']"); })()`), await ev(`$("shapePanel").textContent.trim()`));
  check("Selection heading has one help button", await ev(`$("selTitle").querySelectorAll(".help").length === 1`));

  // ------------------------------------------------ thread rows: one-click select, Shift adds / removes
  const first = `document.querySelector("#layers .thr:not(.off) .thr-pick")`;
  const idx = await ev(`+${first}.dataset.selectLayer`);
  const count = await ev(`Shapes.objects().filter((o) => o.layer === ${idx}).length`);
  await ev(`${first}.click(); true`); await sleep(300);
  check("thread row selects all its shapes", await ev(`Shapes.tools.count() === ${count}`), await ev(`Shapes.tools.count() + " of " + ${count}`));
  check("the selection shows the colour chips", await ev(`!!$("shapePanel").querySelector(".chip")`));
  await ev(`${first}.dispatchEvent(new MouseEvent("click", { bubbles: true, shiftKey: true })); true`); await sleep(300);
  check("Shift + click removes them again", await ev(`Shapes.tools.count() === 0`));

  // ------------------------------------------------ two threads with the same name are numbered
  const nThreads = await ev(`state.design.layers.length`);
  if (nThreads > 1) {
    const name = await ev(`state.design.layers[0].name`);
    await ev(change(`document.querySelectorAll("#layers .thr-name")[1]`, name)); await sleep(300); await waitFor(idle); await sleep(300);
    check("same-named threads get #1 / #2", await ev(`[...document.querySelectorAll("#layers .thr-dup")].map((e) => e.textContent).join(",") === "#1,#2"`),
      await ev(`[...document.querySelectorAll("#layers .thr-dup")].map((e) => e.textContent).join(",")`));
    check("the stored names stay as they are", await ev(`state.design.layers[1].name === ${JSON.stringify(name)}`));
    await shot("layout-same-names");
    await ev(`$("btnUndo").click(); true`); await sleep(300); await waitFor(idle); await sleep(300);
    check("undo removes the numbers", await ev(`!document.querySelector("#layers .thr-dup")`));
  } else check("same-named threads (skipped: one thread)", true);

  // ------------------------------------------------ shape settings are reset from the left panel
  check("no shape changes: the summary is hidden", await ev(`$("modShapes").hidden && $("modSummary").classList.contains("hidden")`));
  await ev(`renderShapeMods(3); true`);
  check("shape changes show in the left summary", await ev(`!$("modShapes").hidden && !$("modSummary").classList.contains("hidden") && $("modSettings").hidden && $("modShapeCount").textContent === "3 shapes have their own settings"`),
    await ev(`$("modShapeCount").textContent`));
  await ev(`renderShapeMods(0); true`);
  check("and hides again", await ev(`$("modSummary").classList.contains("hidden")`));
  await shot("layout-done");
} catch (e) {
  results.push("ERROR " + e.message);
  try { await shot("layout-error"); } catch (_) {}
} finally {
  console.log(results.join("\n"));
  console.log("\nexceptions:", errors.length ? errors.join("\n---\n") : "none");
  console.log("console errors/warnings:", logs.filter((l) => !l.includes("/api/auth/me")).join("\n") || "none");
  try { ws.close(); } catch (_) {}
  chrome.kill();
  process.exit(0);
}
