// Headless browser smoke test of the Phase 4 batch A extras via the Chrome DevTools protocol (no dependencies).
// Usage: node tools/smoke/extras.mjs [out-dir]      (see tools/smoke/README.md)
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
  // a cold start on a CI runner can take well over 10 s: wait up to 60 s, then say so clearly
  for (let i = 0; i < 300 && !ws; i++) {
    try {
      const list = await (await fetch(`http://127.0.0.1:${PORT}/json/list`)).json();
      const page = list.find((t) => t.type === "page");
      if (page) { ws = new WebSocket(page.webSocketDebuggerUrl); break; }
    } catch (_) {}
    await sleep(200);
  }
  if (!ws) throw new Error(`Chrome did not answer on port ${PORT} within 60 s (SMOKE_CHROME=${CHROME})`);
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

try {
  await connect();
  await send("Runtime.enable"); await send("Log.enable"); await send("Page.enable");
  await send("Emulation.setDeviceMetricsOverride", { width: 1440, height: 900, deviceScaleFactor: 1, mobile: false });
  await send("Page.navigate", { url: APP });
  await waitFor(`document.readyState === "complete" && !document.body.classList.contains("booting")`);
  await waitFor(`document.body.classList.contains("signed-out") || !!Auth.user()`);
  if (await ev(`document.body.classList.contains("signed-out")`)) await signIn(EMAIL, PASSWORD);
  await openExample();
  await ev(`setPane("stitches", { user: true }); true`);
  await waitFor(idle, 30000);

  // ------------------------------------------------ stitch types (Design info)
  const kinds = await ev(`(() => { const ops = activeOps(); let n = 0; for (let i = 0; i < ops.length; i += 4) if (ops[i] === 0) n++;
    const shown = [...document.querySelectorAll("#kindStats .k-row b")].reduce((a, b) => a + +b.textContent.replace(/\\D/g, ""), 0);
    return { n, shown, rows: document.querySelectorAll("#kindStats .k-row").length, visible: !$("kindTitle").classList.contains("hidden") }; })()`);
  check("stitch types listed and add up", kinds.visible && kinds.rows >= 3 && kinds.shown === kinds.n, JSON.stringify(kinds));

  // ------------------------------------------------ original stitches only when comparing
  const lazy = await ev(`({ ops: !!state.preview.original_ops, ref: !!state.preview.original_ref, compare: !$("variant").classList.contains("hidden") })`);
  check("preview comes without the original stitches", !lazy.ops && lazy.ref && lazy.compare, JSON.stringify(lazy));
  const optCount = await ev(`state.preview.ops.length / 4`);
  await ev(`document.querySelector('#variant [data-variant="original"]').click(); true`);
  await waitFor(`state.variant === "original" && !!state.preview.original_ops`, 20000);
  check("Original loads them and shows them", await ev(`activeOps().length / 4 > ${optCount} && +$("scrub").max === activeOps().length / 4 && $("stats").textContent.includes(state.preview.comparison.original.stitches.toLocaleString())`),
    await ev(`activeOps().length / 4 + " vs " + ${optCount}`));
  await ev(`document.querySelector('#variant [data-variant="optimized"]').click(); true`);
  check("back to Optimised", await ev(`state.variant === "optimized" && activeOps() === state.preview.ops`));
  // still comparing while the settings change: the new original is loaded for the new stitches
  await ev(`document.querySelector('#variant [data-variant="original"]').click(); true`);
  await waitFor(`state.variant === "original"`, 20000);
  const st0 = await ev(`state.preview.stats.stitches`);
  await ev(`{ const e = $("stitchForm").elements.fill_spacing_mm; e.value = "0.5"; e.dispatchEvent(new Event("change", { bubbles: true })); } true`);
  await waitFor(`state.preview.stats.stitches !== ${st0} && ${idle}`, 60000);
  await waitFor(`state.variant === "original" && !!state.preview.original_ops`, 20000);
  check("comparing survives a re-stitch", await ev(`activeOps() === state.preview.original_ops`));
  await ev(`document.querySelector('#variant [data-variant="optimized"]').click(); History.undo(); true`);
  await waitFor(`${idle} && $("stitchForm").elements.fill_spacing_mm.value === "0.4"`, 60000);

  // ------------------------------------------------ objects list
  await clickAt("#tabShapes");
  check("Shapes tab shows the list", await ev(`!$("objPanel").hidden && $("layers").hidden && $("tabShapes").getAttribute("aria-selected") === "true"`));
  const counts = await ev(`(() => { const on = new Set(state.design.layers.filter((l) => l.enabled).map((l) => l.index));
    return { opts: $("objList").querySelectorAll("[role=option]").length, shapes: Shapes.objects().filter((o) => on.has(o.layer)).length,
      groups: $("objList").querySelectorAll("[role=group]").length }; })()`);
  check("every shape listed, grouped by colour", counts.opts === counts.shapes && counts.opts > 0 && counts.groups >= 1, JSON.stringify(counts));
  await shot("p4a-objects");
  await ev(`Shapes.select([]); $("objList").focus(); true`);
  check("focus makes the first shape active", await ev(`$("objList").getAttribute("aria-activedescendant") === "objopt-0"`));
  await key("ArrowDown");
  check("arrow keys move the active shape", await ev(`$("objList").getAttribute("aria-activedescendant") === "objopt-1"`), await ev(`$("objList").getAttribute("aria-activedescendant")`));
  await key("Enter");
  const one = await ev(`(() => { const ids = [...document.querySelectorAll("#objList [aria-selected=true]")].map((e) => e.id); return { ids, n: +($("selCount").textContent || 0) }; })()`);
  check("Enter selects that shape", one.ids.length === 1 && one.ids[0] === "objopt-1" && one.n === 1, JSON.stringify(one));
  await key("ArrowDown"); await key(" ", { code: "Space" });
  check("Space adds the next one", await ev(`document.querySelectorAll("#objList [aria-selected=true]").length === 2 && $("selCount").textContent === "2"`));
  await key(" ", { code: "Space" });
  check("Space again removes it", await ev(`document.querySelectorAll("#objList [aria-selected=true]").length === 1`));
  await key("Home");
  check("Home goes to the first shape", await ev(`$("objList").getAttribute("aria-activedescendant") === "objopt-0"`));
  check("list keys do not trigger shortcuts", await ev(`!$("keysDialog").open && state.pane === "stitches"`));
  // selecting elsewhere shows in the list
  await ev(`Shapes.select([Shapes.objects().find((o) => state.design.layers.find((l) => l.index === o.layer)?.enabled).id]); true`);
  check("selection elsewhere shows in the list", await ev(`document.querySelectorAll("#objList [aria-selected=true]").length === 1`));
  await clickAt("#objopt-2");
  check("a click selects", await ev(`$("objopt-2").getAttribute("aria-selected") === "true" && $("selCount").textContent === "1"`));
  await ev(`$("tabShapes").focus(); true`); await key("ArrowLeft");
  check("arrow keys switch the tabs", await ev(`!$("layers").hidden && document.activeElement === $("tabThreads")`));
  await key("ArrowRight");
  await ev(`Shapes.select([]); document.activeElement.blur(); true`);

  // ------------------------------------------------ shortcuts
  await key("?", { code: "Slash", mods: ["shift"] });
  check("? opens the shortcuts", await ev(`$("keysDialog").open && $("keysTable").querySelectorAll("kbd").length > 15`));
  await shot("p4a-keys");
  await key("Escape");
  check("Esc closes them", await ev(`!$("keysDialog").open`));
  // the zoom label follows the next frame; headless browsers deliver frames irregularly
  const zoomIs = (cond) => waitFor(cond, 3000).catch(() => {});
  await key("2", { code: "Digit2" });
  await zoomIs(`$("zoomPct").textContent === "200%"`);
  check("2 = 200 %", await ev(`$("zoomPct").textContent === "200%"`), await ev(`$("zoomPct").textContent`));
  await key("1", { code: "Digit1" });
  await zoomIs(`$("zoomPct").textContent === "100%"`);
  check("1 = 100 %", await ev(`$("zoomPct").textContent === "100%"`), await ev(`$("zoomPct").textContent`));
  await key("+", { code: "Equal", mods: ["shift"] });
  await zoomIs(`parseInt($("zoomPct").textContent) > 100`);
  check("+ zooms in", await ev(`parseInt($("zoomPct").textContent) > 100`), await ev(`$("zoomPct").textContent`));
  await key("f", { code: "KeyF" });
  await zoomIs(`parseInt($("zoomPct").textContent) !== 125`);
  check("F fits", await ev(`parseInt($("zoomPct").textContent) !== 125`), await ev(`$("zoomPct").textContent`));
  await key("s", { code: "KeyS" });
  check("S switches to Shapes", await ev(`state.pane === "shapes"`));
  await key("o", { code: "KeyO" });
  check("O shows the original picture", await ev(`$("showRef").checked`));
  await key("o", { code: "KeyO" });
  // editing tools: select an area shape, Edit shape, then B / E / V / L
  await ev(`Shapes.select([Shapes.objects().filter((o) => o.kind === "area" && state.design.layers.find((l) => l.index === o.layer)?.enabled).sort((a, b) => b.area_mm2 - a.area_mm2)[0].id]); true`);
  await ev(`$("shapePanel").querySelector('[data-action="edit"]').click(); true`);
  await waitFor(`!$("editBar").classList.contains("hidden")`, 5000);
  await key("b", { code: "KeyB" });
  check("B = paint", await ev(`$("editBar").querySelector('[data-tool="add"]').getAttribute("aria-pressed") === "true"`));
  await key("l", { code: "KeyL", mods: ["shift"] });
  check("Shift+L = lasso cut", await ev(`$("editBar").querySelector('[data-tool="lasso-erase"]').getAttribute("aria-pressed") === "true"`));
  await key("v", { code: "KeyV" });
  check("V = move", await ev(`$("editBar").querySelector('[data-tool="move"]').getAttribute("aria-pressed") === "true"`));
  await key("Escape");
  // not while typing in a field
  await ev(`$("width_mm").focus(); true`);
  await key("s", { code: "KeyS" });
  check("no shortcuts while typing", await ev(`state.pane === "shapes"`));
  await ev(`document.activeElement.blur(); true`);
  await key("e", { code: "KeyE", mods: ["ctrl"] });
  await waitFor(`$("exportDialog").open`, 10000).catch(() => {});
  check("Ctrl+E opens Export", await ev(`$("exportDialog").open`));
  await ev(`$("exportDialog").close(); true`);

  // ------------------------------------------------ full screen
  const fsOk = await ev(`document.fullscreenEnabled`);
  check("full screen button shown where supported", await ev(`$("btnFullscreen").classList.contains("hidden") === !document.fullscreenEnabled`), `fullscreenEnabled=${fsOk}`);
  if (fsOk) {
    // headless browsers refuse full screen ("not granted"); a real browser enters it
    await clickAt("#btnFullscreen");
    await waitFor(`!!document.fullscreenElement || /Full screen is not available/.test($("toastText").textContent)`, 5000).catch(() => {});
    const fs = await ev(`({ on: !!document.fullscreenElement, pressed: $("btnFullscreen").getAttribute("aria-pressed"), note: $("toastText").textContent })`);
    check("full screen on (or a clear note if the browser refuses)", (fs.on && fs.pressed === "true") || (!fs.on && /not available/.test(fs.note)), JSON.stringify(fs));
    if (fs.on) {
      await clickAt("#btnFullscreen");
      await waitFor(`!document.fullscreenElement`, 5000).catch(() => {});
      check("full screen off", await ev(`!document.fullscreenElement && $("btnFullscreen").getAttribute("aria-pressed") === "false"`));
    }
  }
  await shot("p4a-done");
} catch (e) {
  results.push("ERROR " + e.message);
  try { await shot("p4a-error"); } catch (_) {}
} finally {
  console.log(results.join("\n"));
  console.log("\nexceptions:", errors.length ? errors.join("\n---\n") : "none");
  console.log("console errors/warnings:", logs.filter((l) => !l.includes("/api/auth/me")).join("\n") || "none");
  try { ws.close(); } catch (_) {}
  chrome.kill();
  process.exit(0);
}
