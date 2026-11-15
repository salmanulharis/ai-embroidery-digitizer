// Headless browser smoke test of the editor via the Chrome DevTools protocol (no dependencies).
// Usage: node tools/smoke/controls.mjs [out-dir]     (see tools/smoke/README.md)
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

const chrome = spawn(CHROME, [`--remote-debugging-port=${PORT}`, `--user-data-dir=${SP}/chrome-profile`, "--headless=new",
  "--no-first-run", "--no-default-browser-check", "--window-size=1440,900", "about:blank"], { stdio: "ignore" });
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
const idle = `working.length === 0 && !Jobs.pending()`;
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
  await ev(`$("btnExample").click(); true`);
  await waitFor(`!!state.preview && ${idle}`, 90000);
  check("no settings marked after upload", await ev(`$("modSummary").classList.contains("hidden") && !document.querySelector("#leftPanel label.mod")`));
  check("undo disabled at start", await ev(`$("btnUndo").disabled`));
  check("style settings hidden for a plain logo", await ev(`$("styleParams").hidden`));

  // a stitch setting: marked, undo puts it back (and the stitches), redo again
  const st0 = await ev(`state.preview.stats.stitches`);
  await ev(`{ const f = $("stitchForm").elements.fill_spacing_mm; f.value = "0.6"; f.dispatchEvent(new Event("change", { bubbles: true })); } true`);
  await waitFor(`Jobs.pending()`, 3000).catch(() => {});
  await waitFor(`${idle} && state.preview.stats.stitches !== ${st0}`, 60000);
  check("changed setting marked", await ev(`$("stitchForm").elements.fill_spacing_mm.closest("label").classList.contains("mod") && !$("modSummary").classList.contains("hidden")`), await ev(`$("modCount").textContent`));
  check("section marked", await ev(`document.querySelector('.ed-sec[data-sec="stitches"]').classList.contains("mod")`));
  const ut = await ev(`$("btnUndo").title`);
  check("undo names the step", /Fill thickness/.test(ut), ut);
  await ev(`History.undo(); true`);
  await waitFor(`${idle} && $("stitchForm").elements.fill_spacing_mm.value === "0.4"`, 60000);
  await waitFor(`state.preview.stats.stitches === ${st0}`, 60000);
  check("undo restores setting + stitches", await ev(`!$("stitchForm").elements.fill_spacing_mm.closest("label").classList.contains("mod")`), `${st0} stitches`);
  await ev(`History.redo(); true`);
  await waitFor(`${idle} && $("stitchForm").elements.fill_spacing_mm.value === "0.6"`, 60000);
  check("redo", true);

  // reset buttons: one setting, then a section
  await ev(`document.querySelector('#stitchForm label.mod .reset-one').click(); true`);
  await waitFor(`${idle}`, 60000);
  check("reset one setting", await ev(`$("stitchForm").elements.fill_spacing_mm.value === "0.4" && $("modSummary").classList.contains("hidden")`));
  await ev(`{ const f = $("stitchForm").elements; f.satin_spacing_mm.value = "0.5"; f.satin_spacing_mm.dispatchEvent(new Event("change", { bubbles: true })); f.pull_compensation_mm.value = "0.4"; f.pull_compensation_mm.dispatchEvent(new Event("change", { bubbles: true })); } true`);
  await waitFor(`${idle}`, 60000);
  check("two changes counted", await ev(`$("modCount").textContent === "2 settings changed"`), await ev(`$("modCount").textContent`));
  await shot("p2-marked");
  const open = await ev(`document.querySelector('.ed-sec[data-sec="stitches"]').open`);
  await ev(`document.querySelector('.ed-sec[data-sec="stitches"] .sec-reset').click(); true`);
  await waitFor(`${idle}`, 60000);
  check("section reset (section stays open)", await ev(`$("modSummary").classList.contains("hidden") && document.querySelector('.ed-sec[data-sec="stitches"]').open === ${open}`));
  check("label names stay clean", await ev(`!/↺/.test($("stitchForm").elements.fill_spacing_mm.getAttribute("aria-label") || "")`), await ev(`$("stitchForm").elements.fill_spacing_mm.getAttribute("aria-label")`));

  // a picture setting: re-prepared; undo puts back the design and the slider
  await ev(`$("contrast").value = "30"; $("contrast").dispatchEvent(new Event("change", { bubbles: true })); true`);
  await waitFor(`Jobs.pending()`, 3000).catch(() => {});
  await waitFor(`${idle}`, 90000);
  check("picture change marked", await ev(`$("contrast").closest("label").classList.contains("mod")`));
  await ev(`History.undo(); true`);
  await waitFor(`${idle} && $("contrast").value === "0"`, 90000);
  check("undo picture change restores slider", await ev(`$("contrastOut").textContent === "0" && !$("contrast").closest("label").classList.contains("mod")`));

  // style settings appear when a colour uses zigzag
  await ev(`document.querySelector("#layers [data-expand]").click(); { const s = document.querySelector('#layers [data-k="stitch_type"]'); s.value = "zigzag"; s.dispatchEvent(new Event("change", { bubbles: true })); } true`);
  await waitFor(`${idle} && !$("styleParams").hidden`, 60000);
  const shown = await ev(`[...$("styleParams").querySelectorAll("label:not([hidden])")].map((l) => l.querySelector("[name]").name).join(",")`);
  check("style settings for zigzag", shown === "border_width_mm,border_spacing_mm", shown);
  await ev(`$("styleShowAll").click(); true`);
  check("show every style", await ev(`$("styleParams").querySelectorAll("label:not([hidden])").length >= 11`));
  await ev(`$("styleShowAll").click(); History.undo(); true`);
  await waitFor(`${idle} && $("styleParams").hidden`, 60000);
  check("undo thread style hides them again", true);

  // hoop: outline, fit check
  await ev(`$("hoop").value = "0"; $("hoop").dispatchEvent(new Event("change", { bubbles: true })); true`);
  await waitFor(`Jobs.pending()`, 3000).catch(() => {});
  await waitFor(`${idle}`, 90000);
  check("hoop toggle shown", await ev(`!$("hoopToggle").classList.contains("hidden")`));
  check("hoop drawn in shapes view", await ev(`!!$("svgBox").querySelector("rect[stroke-dasharray]")`));
  await ev(`$("width_mm").value = "150"; $("width_mm").dispatchEvent(new Event("change", { bubbles: true })); true`);
  await waitFor(`Jobs.pending()`, 3000).catch(() => {});
  await waitFor(`${idle}`, 90000);
  check("too large for hoop: warning", await ev(`$("warnings3").textContent.includes("larger than the 100 x 100 mm hoop")`), await ev(`$("warnings3").textContent.slice(0, 90)`));
  await ev(`document.querySelector('.ed-views [data-pane="stitches"]').click(); true`); await sleep(500);
  await shot("p2-hoop-too-large");
  await ev(`History.undo(); true`);
  await waitFor(`${idle} && $("width_mm").value !== "150"`, 90000);
  check("undo width", await ev(`!$("warnings3").textContent.includes("larger than")`), await ev(`$("width_mm").value + " mm"`));
  await sleep(400);
  await shot("p2-hoop-fits");

  // export: the Wilcom package
  await ev(`$("btnExport").click(); true`);
  await waitFor(`$("exportDialog").open && ${idle} && !$("btnDownload").disabled`, 60000);
  check("wilcom option listed", await ev(`!!$("exports").querySelector('input[value="wilcom"]')`));
  await ev(`$("exports").querySelector('input[value="wilcom"]').click(); true`);
  check("wilcom downloads on its own", await ev(`[...$("exports").querySelectorAll("input:checked")].map((b) => b.value).join() === "wilcom"`), await ev(`$("btnDownload").textContent`));
  await shot("p2-export");
  await ev(`$("exports").querySelector('input[value="txt"]').click(); true`);
  check("another file clears the package", await ev(`!$("exports").querySelector('input[value="wilcom"]').checked`));
  const dl = await ev(`fetch("/api/projects/" + state.project.id + "/export/wilcom").then((r) => r.status + " " + r.headers.get("content-disposition"))`);
  check("wilcom package downloads", dl.startsWith("200"), dl);
  await ev(`$("exportDialog").close(); true`);

  // reload keeps the undo steps and the starting values
  const titleBefore = await ev(`$("btnUndo").title`);
  await send("Page.reload");
  await waitFor(`document.readyState === "complete" && !!Auth.user?.()`, 30000);
  await waitFor(`!!state.preview && ${idle}`, 90000);
  const titleAfter = await ev(`$("btnUndo").title`);
  check("undo steps survive reload", titleAfter === titleBefore, titleAfter);
} catch (e) {
  results.push("ERROR " + e.message);
  try { await shot("p2-error"); } catch (_) {}
} finally {
  console.log(results.join("\n"));
  console.log("\nexceptions:", errors.length ? errors.join("\n---\n") : "none");
  console.log("console errors/warnings:", logs.filter((l) => !l.includes("/api/auth/me")).join("\n") || "none");
  try { ws.close(); } catch (_) {}
  chrome.kill();
  process.exit(0);
}
