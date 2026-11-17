// Headless browser smoke test of the Phase 4 batch C (merge similar colours, split view, 100 % calibration) via the Chrome DevTools protocol (no dependencies).
// Usage: node tools/smoke/batchc.mjs [out-dir]      (see tools/smoke/README.md)
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

// real key presses (trusted events, like a user's)
const MODS = { alt: 1, ctrl: 2, meta: 4, shift: 8 };
async function key(k, { code, mods = [], text } = {}) {
  const modifiers = mods.reduce((a, m) => a | MODS[m], 0);
  const t = text ?? (k.length === 1 ? k : undefined);
  const vk = { Enter: 13, Escape: 27, ArrowDown: 40, ArrowUp: 38, ArrowLeft: 37, ArrowRight: 39, Home: 36, End: 35, " ": 32 }[k]
    ?? (k.length === 1 ? k.toUpperCase().charCodeAt(0) : 0);
  await send("Input.dispatchKeyEvent", { type: "keyDown", key: k, code: code || "", modifiers, windowsVirtualKeyCode: vk, text: mods.includes("ctrl") ? undefined : t });
  await send("Input.dispatchKeyEvent", { type: "keyUp", key: k, code: code || "", modifiers, windowsVirtualKeyCode: vk });
  await sleep(120);
}
async function clickAt(sel) {
  const r = await ev(`(() => { const b = document.querySelector(${JSON.stringify(sel)}).getBoundingClientRect(); return { x: b.left + b.width / 2, y: b.top + b.height / 2 }; })()`);
  for (const type of ["mousePressed", "mouseReleased"]) await send("Input.dispatchMouseEvent", { type, x: r.x, y: r.y, button: "left", clickCount: 1 });
  await sleep(200);
}


async function mouse(type, x, y, button = "none", extra = {}) {
  await send("Input.dispatchMouseEvent", { type, x, y, button, clickCount: type === "mouseMoved" ? 0 : 1, ...extra });
}
async function rightClick(x, y) {
  await mouse("mouseMoved", x, y);
  await mouse("mousePressed", x, y, "right", { buttons: 2 });
  await mouse("mouseReleased", x, y, "right");
  await sleep(250);
}
// a screen point inside a shape (shapes can have holes): sample its box until the hit test agrees
const pointIn = (id) => ev(`(() => {
  const t = Shapes.tools; t.select([${JSON.stringify(id)}]); const r = t.selectionRect(); if (!r) return null;
  for (let fy = 0.5, n = 0; n < 9; n++) for (const fx of [0.5, 0.3, 0.7, 0.2, 0.8, 0.4, 0.6]) {
    const y = r.top + (r.bottom - r.top) * ((n % 9) + 0.5) / 9, x = r.left + (r.right - r.left) * fx;
    if (t.hitAt(x, y) === ${JSON.stringify(id)}) { t.select([]); return { x, y }; }
  }
  t.select([]); return null; })()`);
const bigShape = () => ev(`Shapes.objects().filter((o) => o.kind === "area" && state.design.layers.find((l) => l.index === o.layer)?.enabled).sort((a, b) => b.area_mm2 - a.area_mm2)[0].id`);
const obj = (id) => `Shapes.objects().find((o) => o.id === ${JSON.stringify(id)})`;
const pixels = (sel) => ev(`(() => { const c = document.querySelector(${JSON.stringify(sel)}); const d = c.getContext("2d").getImageData(0, 0, c.width, c.height).data;
  let n = 0, h = 0; for (let i = 3; i < d.length; i += 16) if (d[i]) { n++; h = (h * 31 + d[i - 3] + d[i - 2] * 7 + d[i - 1] * 13) | 0; } return { n, h }; })()`);

try {
  await connect();
  await send("Runtime.enable"); await send("Log.enable"); await send("Page.enable");
  await send("Emulation.setDeviceMetricsOverride", { width: 1440, height: 900, deviceScaleFactor: 1, mobile: false });
  await send("Page.navigate", { url: APP });
  await waitFor(`document.readyState === "complete" && !document.body.classList.contains("booting")`);
  await waitFor(`document.body.classList.contains("signed-out") || !!Auth.user()`);
  if (await ev(`document.body.classList.contains("signed-out")`)) await signIn(EMAIL, PASSWORD);
  await ev(`try { localStorage.removeItem("edLayout"); localStorage.removeItem("showGrid"); } catch (_) {} true`);
  await openExample();
  await ev(`setPane("stitches", { user: true }); true`);
  await waitFor(idle, 30000);


  // ------------------------------------------------ merge similar colours
  await ev(`$("tabThreads").click(); true`);
  // the example's colours are far apart: give the second thread a near shade of the first (as a user would)
  await ev(`(() => { const L = state.design.layers, h = L[0].hex.slice(1), c = [0, 2, 4].map((i) => Math.min(255, parseInt(h.slice(i, i + 2), 16) + 10));
    const inp = $("layers").querySelector(\`[data-index="\${L[1].index}"] input[type=color]\`);
    inp.value = "#" + c.map((x) => x.toString(16).padStart(2, "0")).join(""); inp.dispatchEvent(new Event("change", { bubbles: true })); })(); true`);
  await sleep(300);
  await waitFor(idle, 60000);
  const nLayers = await ev(`state.design.layers.length`);
  check("threads menu offers Merge similar colours", await ev(`!!$("thrMenu").querySelector("[data-similar]")`));
  await ev(`$("thrMenu").querySelector("[data-similar]").click(); true`);
  await waitFor(`$("similarDialog").open && !$("similarList").hasAttribute("aria-busy")`, 15000);
  const cfg = await ev(`({ min: +$("similarDe").min, max: +$("similarDe").max, value: +$("similarDe").value, c: state.config.similar })`);
  check("dialog opens with the server's range and default", cfg.min === cfg.c.min && cfg.max === cfg.c.max && cfg.value === cfg.c.default, JSON.stringify(cfg));
  const pairsAt = async (v) => {
    await ev(`{ const r = $("similarDe"); r.value = ${v}; r.dispatchEvent(new Event("input", { bubbles: true })); } true`);
    await sleep(350);
    await waitFor(`!$("similarList").hasAttribute("aria-busy") && ${idle}`, 15000);
    return ev(`$("similarList").querySelectorAll("input[type=checkbox]").length`);
  };
  const lo = await pairsAt(cfg.min);
  check("no pairs: a hint instead of an empty list", lo > 0 || await ev(`/No colours are this alike/.test($("similarList").textContent) && $("similarApply").disabled`));
  const hi = await pairsAt(cfg.max);
  check("a wider range suggests at least as many pairs", hi >= lo && hi > 0, `${lo} at ${cfg.min}, ${hi} at ${cfg.max}`);
  await pairsAt(cfg.max);
  const row = await ev(`(() => { const r = $("similarList").querySelector(".sim-row"); return r ? r.textContent.replace(/\\s+/g, " ").trim() : ""; })()`);
  check("each pair names both colours and how alike they are", /→/.test(row) && /ΔE [0-9]+\.[0-9]/.test(row), row);
  check("Merge button counts the ticked pairs", await ev(`$("similarApply").textContent === "Merge ${hi} colour${hi === 1 ? "" : "s"}" && !$("similarApply").disabled`), await ev(`$("similarApply").textContent`));
  if (hi > 1) {
    await ev(`$("similarList").querySelector("input[type=checkbox]").click(); true`);
    check("unticking one updates the count", await ev(`$("similarApply").textContent === "Merge ${hi - 1} colour${hi - 1 === 1 ? "" : "s"}"`));
  }
  const chosen = await ev(`$("similarList").querySelectorAll("input:checked").length`);
  await shot("p4c-similar");
  await key("Escape"); await sleep(200);
  check("Esc closes the dialog without merging", await ev(`!$("similarDialog").open && state.design.layers.length === ${nLayers}`));
  // again, and merge for real
  await ev(`$("thrMenu").querySelector("[data-similar]").click(); true`);
  await waitFor(`$("similarDialog").open && !$("similarList").hasAttribute("aria-busy")`, 15000);
  check("the slider keeps its value while the page is open", await ev(`+$("similarDe").value === ${cfg.max}`));
  if (hi > 1) await ev(`$("similarList").querySelector("input[type=checkbox]").click(); true`);
  await ev(`$("similarApply").click(); true`);
  await waitFor(`!$("similarDialog").open && ${idle} && state.design.layers.length === ${nLayers - chosen}`, 60000);
  check("merged in one step", true, `${nLayers} -> ${nLayers - chosen} colours`);
  check("toast says how to undo", await ev(`/Use Undo to separate them again/.test($("toastText").textContent)`));
  await ev(`$("btnUndo").click(); true`);
  await waitFor(`${idle} && state.design.layers.length === ${nLayers}`, 60000);
  check("one Undo separates them all again", true);

  // ------------------------------------------------ split view
  await ev(`setPane("stitches", { user: true }); true`); await sleep(300);
  await waitFor(idle, 30000);
  await ev(`{ const c = $("showRef"); if (!c.checked) c.click(); } true`);
  await sleep(300);
  check("split control in the reference bar (stitch view)", await ev(`!$("refBar").classList.contains("hidden") && $("refSplit").offsetParent !== null`));
  const half = `(() => { const c = $("canvas"), d = c.getContext("2d").getImageData(0, 0, Math.floor(c.width * 0.45), c.height).data;
    let h = 0; for (let i = 0; i < d.length; i += 64) h = (h * 31 + d[i] + d[i + 1] * 7 + d[i + 2] * 13 + d[i + 3]) | 0; return h; })()`;
  await ev(`localStorage.removeItem("splitAt"); true`);
  const before = await ev(half);
  await ev(`$("refSplit").click(); true`);
  await sleep(500);
  const hs = await ev(`(() => { const h = $("splitHandle"), w = $("canvasWrap").getBoundingClientRect(), b = h.getBoundingClientRect();
    return { hidden: h.hidden, now: h.getAttribute("aria-valuenow"), mid: Math.round(b.left + b.width / 2 - w.left), half: Math.round(w.width / 2), op: $("refOpacity").offsetParent !== null }; })()`);
  check("split on: handle in the middle, opacity control steps aside", !hs.hidden && hs.now === "50" && Math.abs(hs.mid - hs.half) <= 2 && !hs.op, JSON.stringify(hs));
  check("the left side shows the original", (await ev(half)) !== before);
  await shot("p4c-split");
  await ev(`$("splitHandle").focus(); true`);
  await key("ArrowRight");
  check("→ moves the split by 5 %", await ev(`$("splitHandle").getAttribute("aria-valuenow") === "55"`));
  await key("End");
  check("End: all original", await ev(`$("splitHandle").getAttribute("aria-valuenow") === "100"`));
  await key("Home");
  check("Home: all stitches", await ev(`$("splitHandle").getAttribute("aria-valuenow") === "0"`));
  await key("End");
  const hp = await ev(`(() => { const b = $("splitHandle").getBoundingClientRect(), w = $("canvasWrap").getBoundingClientRect(); return { x: b.left + b.width / 2, y: w.top + w.height / 2 + 120, l: w.left, w: w.width }; })()`);
  await mouse("mouseMoved", hp.x, hp.y);
  await mouse("mousePressed", hp.x, hp.y, "left", { buttons: 1 });
  for (let i = 1; i <= 5; i++) await mouse("mouseMoved", hp.x - ((hp.x - (hp.l + hp.w * 0.25)) * i) / 5, hp.y, "left", { buttons: 1 });
  await mouse("mouseReleased", hp.l + hp.w * 0.25, hp.y, "left");
  await sleep(200);
  const dragged = await ev(`+$("splitHandle").getAttribute("aria-valuenow")`);
  check("dragging the handle moves the split", Math.abs(dragged - 25) <= 1, `${dragged} %`);
  check("the split is remembered", await ev(`Math.abs(parseFloat(localStorage.getItem("splitAt")) - 0.25) < 0.02`));
  await ev(`setPane("shapes", { user: true }); true`); await sleep(300);
  check("shapes view: no split control", await ev(`$("refSplit").offsetParent === null && $("refOpacity").offsetParent !== null`));
  await ev(`setPane("stitches", { user: true }); true`); await sleep(400);
  check("back in the stitches: the split again", await ev(`!$("splitHandle").hidden`));
  await ev(`$("refSplit").click(); true`); await sleep(300);
  check("split off: handle gone, opacity back", await ev(`$("splitHandle").hidden && $("refOpacity").offsetParent !== null`));
  await ev(`$("showRef").click(); true`); await sleep(200);

  // ------------------------------------------------ 100 % calibration
  const std = 96 / 25.4;
  await ev(`localStorage.removeItem("screenPxPerMm"); true`);
  await ev(`$("btnKeys").click(); true`); await sleep(200);
  await ev(`$("btnCalibrate").click(); true`); await sleep(200);
  check("Calibrate opens from the shortcuts list", await ev(`$("calibDialog").open && !$("keysDialog").open && Math.abs(+$("calibSize").value - ${std}) < 0.01`));
  const v = +(std * 1.1).toFixed(2);
  await ev(`{ const r = $("calibSize"); r.value = ${v}; r.dispatchEvent(new Event("input", { bubbles: true })); } true`);
  check("the card box follows the slider", await ev(`Math.abs($("calibCard").getBoundingClientRect().width - 85.6 * ${v}) < 1.5 && /110 %/.test($("calibOut").textContent)`),
    await ev(`$("calibOut").textContent + " " + Math.round($("calibCard").getBoundingClientRect().width)`));
  await shot("p4c-calibrate");
  await ev(`$("calibSave").click(); true`); await sleep(200);
  check("saved per browser (device pixels per mm)", await ev(`!$("calibDialog").open && Math.abs(parseFloat(localStorage.getItem("screenPxPerMm")) - ${v} * devicePixelRatio) < 0.01`));
  await ev(`$("zoomPct").click(); true`); await sleep(400);
  check("100 % now uses the calibration", await ev(`Math.abs(pxPerMm() - ${v}) / ${v} < 0.01 && $("zoomPct").textContent === "100%" && /calibrated/.test($("zoomPct").title)`),
    await ev(`pxPerMm().toFixed(3) + " " + $("zoomPct").textContent`));
  await ev(`Calibration.open(); true`); await sleep(100);
  await ev(`$("calibReset").click(); $("calibDialog").close(); true`); await sleep(200);
  check("Standard size undoes it", await ev(`localStorage.getItem("screenPxPerMm") === null && Math.abs(actualPxPerMm() - ${std}) < 1e-9 && /approximate/.test($("zoomPct").title)`));

  // ------------------------------------------------ phone width
  await send("Emulation.setDeviceMetricsOverride", { width: 390, height: 844, deviceScaleFactor: 2, mobile: true }); await sleep(500);
  await ev(`Calibration.open(); true`); await sleep(200);
  check("phone: calibration dialog fits the screen", await ev(`(() => { const b = $("calibDialog").getBoundingClientRect(); return b.left >= 0 && b.right <= innerWidth + 1; })()`));
  await ev(`$("calibDialog").close(); true`);
  await send("Emulation.setDeviceMetricsOverride", { width: 1440, height: 900, deviceScaleFactor: 1, mobile: false }); await sleep(400);
  await shot("p4c-done");
} catch (e) {
  results.push("ERROR " + e.message);
  try { await shot("p4c-error"); } catch (_) {}
} finally {
  console.log(results.join("\n"));
  console.log("\nexceptions:", errors.length ? errors.join("\n---\n") : "none");
  console.log("console errors/warnings:", logs.filter((l) => !l.includes("/api/auth/me")).join("\n") || "none");
  try { ws.close(); } catch (_) {}
  chrome.kill();
  process.exit(0);
}
