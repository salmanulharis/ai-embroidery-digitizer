// Headless browser smoke test of the Phase 4 batch B (density, grid, panels, shape menu, toolbar) via the Chrome DevTools protocol (no dependencies).
// Usage: node tools/smoke/batchb.mjs [out-dir]      (see tools/smoke/README.md)
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

  // ------------------------------------------------ density view
  await ev(`document.querySelector('#viewMode [data-mode="density"]').click(); true`);
  await waitFor(`!$("legend").classList.contains("hidden") && /Needle points per mm²/.test($("legend").textContent)`, 5000);
  const dens = await ev(`(() => { const ops = activeOps(), cells = new Map(); const cu = state.config.density.cell_mm * 10;
    for (let i = 0; i < ops.length; i += 4) if (ops[i] === 0) { const k = Math.floor(ops[i + 1] / cu) + "," + Math.floor(ops[i + 2] / cu); cells.set(k, (cells.get(k) || 0) + 1); }
    const over = [...cells.values()].filter((n) => n > state.config.density.limit).length;
    return { over, legend: $("legend").textContent, swatches: $("legend").querySelectorAll("span").length }; })()`);
  check("density legend: five bands, count of dense spots", dens.swatches === 5 && (dens.over ? dens.legend.includes(`${dens.over} mm² over the limit`) : dens.legend.includes("nothing over the limit")), dens.legend);
  // hover over a real needle point (checks the screen <-> mm mapping too)
  const hoverPt = await ev(`(() => { const ops = activeOps(), r = $("canvas").getBoundingClientRect(), v = state.view;
    const i = 4 * Math.floor(ops.length / 8); let j = i; while (ops[j] !== 0) j += 4;
    return { x: r.left + v.x + ops[j + 1] * v.scale, y: r.top + v.y + ops[j + 2] * v.scale }; })()`);
  await mouse("mouseMoved", hoverPt.x, hoverPt.y);
  await sleep(200);
  check("hover shows the figure", await ev(`!$("densityTip").hidden && /[0-9] needle points per mm²/.test($("densityTip").textContent)`),
    await ev(`JSON.stringify({ hidden: $("densityTip").hidden, text: $("densityTip").textContent, mode: state.mode })`));
  await shot("p4b-density");
  await ev(`document.querySelector('#viewMode [data-mode="real"]').click(); true`);
  await mouse("mouseMoved", hoverPt.x, hoverPt.y);
  check("no density note in other views", await ev(`$("densityTip").hidden`));

  // ------------------------------------------------ grid + rulers
  await ev(`$("showPop").hidden && $("btnShow").click(); true`); await sleep(150); // Grid is in the Show menu
  await clickAt("#showGrid");
  await ev(`document.activeElement.blur(); true`); // keys typed into a focused checkbox are not shortcuts
  await waitFor(`!$("gridLayer").hidden`, 3000);
  await sleep(300);
  const g1 = await pixels("#gridLayer");
  check("grid drawn over the stitches", g1.n > 500 && await ev(`$("stage").classList.contains("with-grid")`), JSON.stringify(g1));
  await key("+", { code: "Equal", mods: ["shift"] }); await sleep(400);
  const g2 = await pixels("#gridLayer");
  check("grid follows the zoom", g2.h !== g1.h, `${g1.h} -> ${g2.h}`);
  await shot("p4b-grid-stitches");
  await key("s", { code: "KeyS" }); await sleep(500);
  const g3 = await pixels("#gridLayer");
  check("grid in the shapes view too", g3.n > 500 && g3.h !== g2.h, JSON.stringify(g3));
  await shot("p4b-grid-shapes");
  await key("s", { code: "KeyS" }); await key("f", { code: "KeyF" }); await sleep(300);
  await ev(`$("showPop").hidden && $("btnShow").click(); true`); await sleep(150); // Grid is in the Show menu
  await clickAt("#showGrid");
  check("grid off", await ev(`$("gridLayer").hidden && !$("stage").classList.contains("with-grid")`));

  // ------------------------------------------------ resizable / collapsible panels
  const w0 = await ev(`Math.round($("leftPanel").getBoundingClientRect().width)`);
  await ev(`$("sepLeft").focus(); true`);
  await key("ArrowRight");
  check("separator: → widens by 16 px", await ev(`Math.round($("leftPanel").getBoundingClientRect().width)`) === w0 + 16, `${w0} -> ${await ev(`Math.round($("leftPanel").getBoundingClientRect().width)`)}`);
  await key("End");
  check("End = widest (420)", await ev(`Math.round($("leftPanel").getBoundingClientRect().width) === 420 && $("sepLeft").getAttribute("aria-valuenow") === "420"`));
  await key("Home");
  check("Home = narrowest (240)", await ev(`Math.round($("leftPanel").getBoundingClientRect().width) === 240`));
  // drag the right panel's edge with the mouse
  const sr = await ev(`(() => { const b = $("sepRight").getBoundingClientRect(); return { x: b.left + b.width / 2, y: b.top + 200 }; })()`);
  const r0 = await ev(`Math.round($("rightPanel").getBoundingClientRect().width)`);
  await mouse("mouseMoved", sr.x, sr.y); await mouse("mousePressed", sr.x, sr.y, "left", { buttons: 1 });
  for (let i = 1; i <= 6; i++) await mouse("mouseMoved", sr.x - i * 10, sr.y, "left", { buttons: 1 });
  await mouse("mouseReleased", sr.x - 60, sr.y, "left");
  await sleep(200);
  check("drag widens the design panel", await ev(`Math.round($("rightPanel").getBoundingClientRect().width)`) === Math.min(420, r0 + 60), `${r0} -> ${await ev(`Math.round($("rightPanel").getBoundingClientRect().width)`)}`);
  await ev(`document.activeElement.blur(); true`);
  await key("[", { code: "BracketLeft" });
  check("[ closes the settings panel", await ev(`document.body.classList.contains("left-off") && getComputedStyle($("leftPanel")).display === "none" && !$("sepLeftBtn").closest("[hidden]")`));
  await shot("p4b-left-closed");
  await clickAt("#sepLeftBtn");
  await sleep(200);
  check("its button opens it again (at its width)", await ev(`!document.body.classList.contains("left-off") && Math.round($("leftPanel").getBoundingClientRect().width) === 240`));
  check("sizes are remembered", await ev(`JSON.parse(localStorage.getItem("edLayout")).left === 240`));
  await send("Emulation.setDeviceMetricsOverride", { width: 1024, height: 768, deviceScaleFactor: 1, mobile: false }); await sleep(500);
  check("tablet: only the settings edge (the design panel is a sheet)", await ev(`!$("sepLeft").closest("[hidden]") && !!$("sepRight").closest("[hidden]")`),
    await ev(`JSON.stringify({ left: !$("sepLeft").closest("[hidden]"), right: !$("sepRight").closest("[hidden]"), w: innerWidth, off: document.body.className })`));
  await send("Emulation.setDeviceMetricsOverride", { width: 1440, height: 900, deviceScaleFactor: 1, mobile: false }); await sleep(500);
  await ev(`$("btnKeys").click(); true`);
  await ev(`$("btnResetWorkspace").click(); $("keysDialog").close(); true`);
  await sleep(300);
  check("Reset workspace: standard sizes", await ev(`Math.round($("leftPanel").getBoundingClientRect().width)`) === w0 && await ev(`Math.round($("rightPanel").getBoundingClientRect().width)`) === r0);

  // ------------------------------------------------ shape menu
  await ev(`setPane("stitches", { user: true }); true`); await sleep(400);
  const id = await bigShape();
  const pt = await pointIn(id);
  await rightClick(pt.x, pt.y);
  check("right-click opens the menu and selects the shape", await ev(`!!document.querySelector(".ctx-menu[role=menu]") && Shapes.tools.has(${JSON.stringify(id)}) && Shapes.tools.count() === 1`),
    await ev(`document.querySelector(".ctx-menu")?.getAttribute("aria-label") || "no menu"`));
  check("first entry focused", await ev(`document.activeElement?.closest(".ctx-menu") && document.activeElement.textContent.includes("Stitch style")`));
  check("the toolbar steps aside while the menu is open", await ev(`$("selBar").hidden`));
  await shot("p4b-menu");
  await key("ArrowRight");
  check("→ opens the submenu on the current style", await ev(`!!document.querySelector(".ctx-sub") && document.activeElement.closest(".ctx-sub") && document.activeElement.getAttribute("aria-checked") === "true"`));
  await key("Escape");
  check("Esc closes only the submenu", await ev(`!document.querySelector(".ctx-sub") && !!document.querySelector(".ctx-menu") && document.activeElement.textContent.includes("Stitch style")`));
  await key("ArrowDown"); await key("ArrowRight"); // Direction ▸
  await key("ArrowDown"); await key("ArrowDown"); await key("ArrowDown"); // Same, 0, 45, -> 90
  check("direction submenu reached 90°", await ev(`/90°/.test(document.activeElement.textContent)`), await ev(`document.activeElement.textContent.trim()`));
  await key("Enter");
  await waitFor(`${idle} && ${obj(id)}.angle === 90`, 60000);
  check("picking it changes the shape (and closes the menu)", await ev(`!document.querySelector(".ctx-menu")`));
  await ev(`History.undo(); true`);
  await waitFor(`${idle} && ${obj(id)}.angle == null`, 60000);
  check("undo restores it", true);
  // right-click on a shape that is part of a selection keeps the selection
  await ev(`(() => { const others = Shapes.objects().filter((o) => o.layer === ${obj(id)}.layer && o.id !== ${JSON.stringify(id)}); Shapes.tools.select([${JSON.stringify(id)}, others[0].id]); })(); true`);
  await rightClick(pt.x, pt.y);
  check("right-click keeps a multiple selection", await ev(`Shapes.tools.count() === 2 && /2 selected shapes/.test(document.querySelector(".ctx-menu").getAttribute("aria-label"))`));
  await key("d", { code: "KeyD" });
  check("type-ahead: d = Direction", await ev(`/Direction/.test(document.activeElement.textContent)`));
  await key("d", { code: "KeyD" });
  check("type-ahead: d again = Don't stitch", await ev(`/Don't stitch/.test(document.activeElement.textContent)`));
  await key("Enter");
  await waitFor(`${idle} && ${obj(id)}.enabled === false`, 60000);
  check("Don't stitch leaves both out", await ev(`Shapes.objects().filter((o) => o.enabled === false).length >= 2`));
  await ev(`History.undo(); true`);
  await waitFor(`${idle} && ${obj(id)}.enabled !== false`, 60000);
  // empty space: the browser's own menu, nothing selected
  await ev(`Shapes.tools.select([]); true`);
  const empty = await ev(`(() => { const r = $("canvas").getBoundingClientRect(); return { x: r.left + 8, y: r.bottom - 60 }; })()`);
  await rightClick(empty.x, empty.y);
  check("empty space: no shape menu", await ev(`!document.querySelector(".ctx-menu") && Shapes.tools.count() === 0`));
  // keyboard: Shift+F10 with a selection, Esc gives focus back
  await ev(`Shapes.tools.select([${JSON.stringify(id)}]); $("tabShapes").click(); $("objList").focus(); true`);
  await key("F10", { code: "F10", mods: ["shift"] });
  check("Shift+F10 opens it from the shapes list", await ev(`!!document.querySelector(".ctx-menu")`));
  await key("Escape");
  check("Esc returns focus to the list", await ev(`!document.querySelector(".ctx-menu") && document.activeElement === $("objList")`));
  await ev(`$("tabThreads").click(); true`);

  // ------------------------------------------------ floating selection toolbar
  await ev(`Shapes.tools.select([${JSON.stringify(id)}]); document.activeElement.blur(); true`);
  await waitFor(`!$("selBar").hidden`, 3000).catch(() => {});
  const pos = await ev(`(() => { const b = $("selBar").getBoundingClientRect(), s = $("stage").getBoundingClientRect(), r = Shapes.tools.selectionRect();
    return { shown: !$("selBar").hidden, inside: b.left >= s.left && b.right <= s.right && b.top >= s.top && b.bottom <= s.bottom,
      clear: b.bottom <= r.top + 1 || b.top >= r.bottom - 1 || b.top >= s.top + 50 }; })()`);
  check("toolbar shown beside the selection, inside the preview", pos.shown && pos.inside && pos.clear, JSON.stringify(pos) +
    await ev(`JSON.stringify({ count: Shapes.tools.count(), busy: Shapes.tools.busy(), menu: !!document.querySelector(".ctx-menu"), pane: state.pane, focus: document.activeElement?.id })`));
  await shot("p4b-toolbar");
  await ev(`$("selBar").querySelector('[data-sb="thick:0.33"]').click(); true`);
  await waitFor(`${idle} && ${obj(id)}.spacing_mm === 0.33`, 60000);
  await waitFor(`$("selBar").querySelector('[data-sb="thick:0.33"]')?.getAttribute("aria-pressed") === "true"`, 3000).catch(() => {});
  check("thickness from the toolbar (and shown pressed)", await ev(`$("selBar").querySelector('[data-sb="thick:0.33"]').getAttribute("aria-pressed") === "true"`));
  await ev(`History.undo(); true`);
  await waitFor(`${idle} && ${obj(id)}.spacing_mm == null`, 60000);
  await ev(`$("selBar").querySelector("[data-sb]").focus(); true`);
  await key("ArrowRight");
  check("← → move between its controls", await ev(`document.activeElement.dataset.sb !== "style" && document.activeElement.tabIndex === 0 && $("selBar").querySelectorAll('[tabindex="0"]').length === 1`));
  await ev(`document.activeElement.blur(); true`);
  const c = await ev(`(() => { const r = $("canvas").getBoundingClientRect(); return { x: r.left + r.width / 2, y: r.top + r.height / 2 }; })()`);
  await send("Input.dispatchMouseEvent", { type: "mouseWheel", x: c.x, y: c.y, deltaX: 0, deltaY: -120 });
  await sleep(60);
  check("hidden while zooming", await ev(`$("selBar").hidden`));
  await waitFor(`!$("selBar").hidden`, 3000).catch(() => {});
  check("back after zooming", await ev(`!$("selBar").hidden`));
  await ev(`setPane("shapes", { user: true }); true`); await sleep(300);
  await ev(`$("shapePanel").querySelector('[data-action="edit"]').click(); true`);
  await sleep(300);
  check("hidden while editing the shape", await ev(`$("selBar").hidden`));
  await key("Escape"); await sleep(300);
  await waitFor(`!$("selBar").hidden`, 3000).catch(() => {});
  check("shapes view: toolbar again after editing", await ev(`!$("selBar").hidden`));
  await send("Emulation.setDeviceMetricsOverride", { width: 820, height: 900, deviceScaleFactor: 1, mobile: false }); await sleep(500);
  check("narrow screens: no toolbar (the panel sheet has it all)", await ev(`$("selBar").hidden`));
  await send("Emulation.setDeviceMetricsOverride", { width: 1440, height: 900, deviceScaleFactor: 1, mobile: false }); await sleep(400);
  await shot("p4b-done");
} catch (e) {
  results.push("ERROR " + e.message);
  try { await shot("p4b-error"); } catch (_) {}
} finally {
  console.log(results.join("\n"));
  console.log("\nexceptions:", errors.length ? errors.join("\n---\n") : "none");
  console.log("console errors/warnings:", logs.filter((l) => !l.includes("/api/auth/me")).join("\n") || "none");
  try { ws.close(); } catch (_) {}
  chrome.kill();
  process.exit(0);
}
