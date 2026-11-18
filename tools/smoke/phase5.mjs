// Headless browser smoke test of Phase 5 (theme, woven fabric, machine speed, thread charts, new formats) via the Chrome DevTools protocol (no dependencies).
// Usage: node tools/smoke/phase5.mjs [out-dir]      (see tools/smoke/README.md)
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


  // ------------------------------------------------ theme
  const bg = `getComputedStyle(document.documentElement).getPropertyValue("--bg").trim()`;
  const LIGHT = "#f3f4f8", DARK = "#0f1218";
  await send("Emulation.setEmulatedMedia", { features: [{ name: "prefers-color-scheme", value: "light" }] });
  await sleep(150);
  check("default: follows the device (light)", await ev(`Theme.get() === "system" && !document.documentElement.dataset.theme && ${bg} === "${LIGHT}"`), await ev(bg));
  await ev(`$("btnUser").click(); true`); await sleep(150);
  check("theme choice in the account menu", await ev(`!$("userMenu").classList.contains("hidden") && $("themePick").querySelectorAll("[role=radio]").length === 3 && $("themePick").querySelector('[aria-checked="true"]').dataset.themeChoice === "system"`));
  await ev(`$("showGrid").checked || $("showGrid").click(); true`);
  await ev(`setPane("stitches", { user: true }); true`); await sleep(400);
  const gridBefore = await pixels("#gridLayer");
  await ev(`$("themePick").querySelector('[data-theme-choice="dark"]').click(); true`); await sleep(200);
  check("Dark: dark colours although the device is light", await ev(`document.documentElement.dataset.theme === "dark" && ${bg} === "${DARK}" && localStorage.getItem("theme") === "dark"`), await ev(bg));
  check("form controls follow (color-scheme)", await ev(`getComputedStyle(document.documentElement).colorScheme === "dark"`));
  check("the rulers repaint in the new colours", JSON.stringify(await pixels("#gridLayer")) !== JSON.stringify(gridBefore));
  await send("Emulation.setEmulatedMedia", { features: [{ name: "prefers-color-scheme", value: "dark" }] }); await sleep(150);
  await ev(`$("btnUser").click(); true`); await sleep(150);
  if (await ev(`$("userMenu").classList.contains("hidden")`)) { await ev(`$("btnUser").click(); true`); await sleep(150); }
  await ev(`$("themePick").querySelector('[data-theme-choice="light"]').focus(); $("themePick").querySelector('[data-theme-choice="light"]').click(); true`); await sleep(150);
  check("Light: light colours although the device is dark", await ev(`document.documentElement.dataset.theme === "light" && ${bg} === "${LIGHT}"`), await ev(bg));
  await key("ArrowRight");
  check("→ in the group chooses the next (Dark) and moves focus", await ev(`Theme.get() === "dark" && document.activeElement.dataset.themeChoice === "dark"`));
  await key("ArrowRight");
  check("→ wraps round to System: follows the dark device again", await ev(`Theme.get() === "system" && localStorage.getItem("theme") === null && ${bg} === "${DARK}"`));
  await ev(`Theme.set("dark"); true`);
  await send("Page.reload", {});
  await sleep(300);
  await waitFor(`document.readyState !== "loading"`, 30000);
  check("remembered: dark from the first paint after a reload", await ev(`document.documentElement.dataset.theme === "dark"`));
  await waitFor(`document.readyState === "complete" && !document.body.classList.contains("booting")`);
  await waitFor(`document.body.classList.contains("signed-out") || !!Auth.user()`);
  if (await ev(`document.body.classList.contains("signed-out")`)) await signIn(EMAIL, PASSWORD);
  await shot("p5-dark");
  await ev(`Theme.set("system"); true`);
  await send("Emulation.setEmulatedMedia", { features: [] });
  // the reload restores the open design; open the example only when nothing came back
  const restored = await waitFor(`!!state.preview && ${idle}`, 20000).then(() => true, () => false);
  if (!restored) await openExample();
  await ev(`setPane("stitches", { user: true }); true`);
  await waitFor(idle, 30000);
  await ev(`$("showGrid").checked && $("showGrid").click(); true`);

  // ------------------------------------------------ woven fabric
  await ev(`localStorage.removeItem("weave"); true`);
  check("woven off by default", await ev(`!$("canvasWrap").classList.contains("weave") && !$("showWeave").checked`));
  await ev(`$("showWeave").click(); true`);
  check("Woven shows a texture under the stitches", await ev(`$("canvasWrap").classList.contains("weave") && getComputedStyle($("canvasWrap")).backgroundImage.includes("repeating-linear-gradient") && localStorage.getItem("weave") === "1"`));
  await ev(`$("showFabric").click(); true`); await sleep(150);
  check("... also on the dark fabric", await ev(`$("canvasWrap").classList.contains("dark") && getComputedStyle($("canvasWrap")).backgroundImage.includes("255, 255, 255")`));
  await shot("p5-woven");
  await ev(`$("showFabric").click(); $("showWeave").click(); true`);

  // ------------------------------------------------ machine speed
  const t0 = await ev(`state.preview.stats.est_minutes`);
  check("speed control with the fabric, thread and machine (300-1500)", await ev(`(() => { const e = $("stitchForm").elements.machine_spm; return !!e && e.min === "300" && e.max === "1500" && e.value === "750"; })()`));
  await ev(change(`${f}.machine_spm`, 1500));
  await sleep(300);
  await waitFor(`${idle} && state.preview.stats.machine_spm === 1500`, 60000);
  const t1 = await ev(`state.preview.stats.est_minutes`);
  check("faster machine: shorter time estimate, same stitches", t1 < t0 && await ev(`state.preview.stats.stitches`) > 0, `${t0} -> ${t1} min`);
  check("the bobbin estimate is sent", await ev(`state.preview.stats.bobbin_m > 0`));
  await ev(`$("btnUndo").click(); true`);
  await waitFor(`${idle} && state.preview.stats.machine_spm === 750`, 60000);
  check("Undo restores the speed", await ev(`${f}.machine_spm.value === "750"`));

  // ------------------------------------------------ thread chart
  const charts = await ev(`state.config.thread_charts`);
  check("the server's chart is offered", Array.isArray(charts) && charts.some((c) => c.id === "smoke-rayon") && await ev(`!!${f}.thread_chart && ${f}.thread_chart.querySelector('option[value="smoke-rayon"]') !== null`), JSON.stringify(charts));
  await ev(change(`${f}.thread_chart`, "smoke-rayon"));
  await sleep(300);
  await waitFor(`${idle} && state.preview.colors.every((c) => c.thread && c.thread.brand === "Smoke Rayon")`, 60000);
  const seq = await ev(`$("sequence").querySelector(".seq-thread")?.textContent.replace(/\\s+/g, " ").trim() || ""`);
  check("Thread order shows brand, number and closeness", /Smoke Rayon [0-9]{4} /.test(seq) && /(same colour|very close|close|nearest available)/.test(seq), seq);
  await shot("p5-threads");
  await ev(change(`${f}.thread_chart`, ""));
  await sleep(300);
  await waitFor(`${idle} && state.preview.colors.every((c) => !c.thread)`, 60000);
  check("None: back to colours only", await ev(`!$("sequence").querySelector(".seq-thread")`));

  // ------------------------------------------------ new formats
  await ev(`$("btnExport").click(); true`);
  await waitFor(`$("exportDialog").open`, 10000);
  const fm = await ev(`$("exports").textContent.replace(/\\s+/g, " ")`);
  check("export offers Brother PEC and Singer XXX with the test-sew note", /Brother PEC \(\.pec\)/.test(fm) && /Singer XXX \(\.xxx\)/.test(fm) && (fm.match(/sew a test piece first/g) || []).length >= 2, fm.slice(0, 200));
  await key("Escape");

  // ------------------------------------------------ auto-update switch (Admin > Settings)
  await ev(`Admin.open("settings"); true`);
  await waitFor(`!!document.querySelector('#setForm input[name="auto_update"]')`, 15000);
  check("admin: auto-update is a switch", await ev(`(() => { const c = document.querySelector('#setForm input[name="auto_update"]'); return c.type === "checkbox" && c.checked; })()`));
  await ev(`document.querySelector('#setForm input[name="auto_update"]').click(); $("setForm").requestSubmit(); true`);
  await sleep(600);
  check("admin: saved off", await ev(`fetch("/api/config").then((r) => r.json()).then((c) => c.auto_update === false)`));
  await send("Page.reload", {});
  await sleep(300);
  await waitFor(`document.readyState === "complete" && !document.body.classList.contains("booting")`);
  await waitFor(`!!state.preview && ${idle}`, 60000);
  await ev(`setPane("stitches", { user: true }); true`); await sleep(300);
  check("editor: Auto-update off and locked", await ev(`!$("autoUpdate").checked && $("autoUpdate").disabled && /administrator/.test($("autoUpdate").closest("label").title)`));
  await ev(change(`${f}.fill_spacing_mm`, 0.5));
  await sleep(800);
  check("a change waits for Update", await ev(`Jobs.stale() && !$("btnUpdate").hidden`));
  await ev(`$("btnUpdate").click(); true`);
  await waitFor(`${idle} && !Jobs.stale()`, 60000);
  check("Update creates the stitches", await ev(`!Jobs.stale() && $("btnUpdate").hidden`));
  await ev(`fetch("/api/admin/settings", { method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ auto_update: 1 }) }).then((r) => r.ok)`);
  check("switched back on", await ev(`fetch("/api/config").then((r) => r.json()).then((c) => c.auto_update === true)`));
} catch (e) {
  results.push("ERROR " + e.message);
  try { await shot("p5-error"); } catch (_) {}
} finally {
  console.log(results.join("\n"));
  console.log("\nexceptions:", errors.length ? errors.join("\n---\n") : "none");
  console.log("console errors/warnings:", logs.filter((l) => !l.includes("/api/auth/me")).join("\n") || "none");
  try { ws.close(); } catch (_) {}
  chrome.kill();
  process.exit(0);
}
