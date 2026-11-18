// Headless browser smoke test of the editor layout (docs/EDITOR_UX_PLAN.md): nothing in the bottom bar overlaps at
// any screen size, no sideways page scroll, plus the Batch A clean-ups (stats wording, selection panel, thread rows),
// Batch B (header actions, view bar, Show menu, nothing over the design) and Batch C (preset menu, Fabric & thread,
// compact numbers, help buttons on demand), Batch D (right panel: Selection heading, Not sewn, similar threads,
// four figures, Checks first) and Batch E (icon-only Export on narrow screens).
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
const SIZES = [[1920, 1080], [1600, 900], [1440, 900], [1280, 800], [1024, 768], [820, 1180, true], [390, 844, true]];
const pane = (p) => ev(`document.querySelector('[data-pane="${p}"]').click(); true`);
// every visible item of the bottom bar, and the pairs whose boxes overlap (more than 1 px each way)
const barOverlaps = (items = "#edBar > .ed-grp > *, #edBar > button", box = "#edBar") => ev(`(() => {
  const items = [...document.querySelectorAll(${JSON.stringify(items)})].filter((e) => {
    const s = getComputedStyle(e), r = e.getBoundingClientRect(); return s.display !== "none" && s.visibility !== "hidden" && r.width > 0 && r.height > 0; });
  const name = (e) => e.id || e.className || e.tagName;
  const bad = [];
  for (let i = 0; i < items.length; i++) for (let j = i + 1; j < items.length; j++) {
    if (items[i].contains(items[j]) || items[j].contains(items[i])) continue; // a group and its own buttons
    const a = items[i].getBoundingClientRect(), b = items[j].getBoundingClientRect();
    if (Math.min(a.right, b.right) - Math.max(a.left, b.left) > 1 && Math.min(a.bottom, b.bottom) - Math.max(a.top, b.top) > 1) bad.push(name(items[i]) + " × " + name(items[j]));
  }
  const bar = document.querySelector(${JSON.stringify(box)}).getBoundingClientRect();
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
      const vb = await barOverlaps("#viewBar > *:not(.grow), #viewBar .vb-grp > *, #viewBar .ed-zoom > *", "#viewBar");
      check(`${w}×${h} ${p}: view bar items don't overlap`, vb.length === 0, vb.join(", "));
      const hd = await barOverlaps("header > *, header .hdr-file > *, header .hdr-undo > *, header .hdr-actions > *, #paneSwitch > *", "header");
      check(`${w}×${h} ${p}: header items don't overlap`, hd.length === 0, hd.join(", "));
      if (w >= 1024) check(`${w}×${h} ${p}: header is one row`, await ev(`document.querySelector("header").getBoundingClientRect().height < 70`),
        await ev(`Math.round(document.querySelector("header").getBoundingClientRect().height) + " px"`));
      check(`${w}×${h} ${p}: undo, view switch and Export in the header`, await ev(`["btnUndo", "btnRedo", "paneSwitch", "btnExport"].every((id) => $(id).offsetParent !== null && $(id).closest("header"))`));
      check(`${w}×${h} ${p}: nothing floats over the top of the preview`, await ev(`(() => { const s = $("stage").getBoundingClientRect(), v = $("viewBar").getBoundingClientRect();
        return v.bottom <= s.top + 1 && [...document.querySelectorAll(".ed-tools > *")].every((e) => e.offsetParent === null); })()`));
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

  // ------------------------------------------------ view bar: preview style as buttons when wide, a drop-down when narrow
  check("wide preview: style buttons", await ev(`document.querySelector('#viewMode [data-mode="direction"]').offsetParent !== null && $("viewMode").querySelector(".seg-sel").offsetParent === null`));
  await send("Emulation.setDeviceMetricsOverride", { width: 1024, height: 768, deviceScaleFactor: 1, mobile: false }); await sleep(400);
  check("narrow preview: style drop-down", await ev(`$("viewMode").querySelector(".seg-sel").offsetParent !== null && document.querySelector('#viewMode [data-mode="direction"]').offsetParent === null`));
  await ev(`{ const s = $("viewMode").querySelector(".seg-sel"); s.value = "type"; s.dispatchEvent(new Event("change", { bubbles: true })); } true`); await sleep(200);
  check("the drop-down switches the style", await ev(`state.mode === "type" && document.querySelector('#viewMode [data-mode="type"]').getAttribute("aria-pressed") === "true"`));
  await ev(`document.querySelector('#viewMode [data-mode="real"]').click(); true`); await sleep(200);
  check("and follows the buttons", await ev(`$("viewMode").querySelector(".seg-sel").value === "real"`));
  await shot("layout-viewbar-narrow");
  await send("Emulation.setDeviceMetricsOverride", { width: 1920, height: 1080, deviceScaleFactor: 1, mobile: false }); await sleep(400);

  // ------------------------------------------------ the Show menu
  await ev(`$("btnShow").click(); true`); await sleep(150);
  check("Show opens its menu", await ev(`!$("showPop").hidden && $("btnShow").getAttribute("aria-expanded") === "true"`));
  check("stitches view: woven + dark fabric, no directions", await ev(`$("showWeave").offsetParent !== null && $("showFabric").offsetParent !== null && $("showDirections").offsetParent === null`));
  await shot("layout-show-menu");
  await ev(`$("showGrid").click(); true`); await sleep(200);
  check("ticking keeps it open and counts", await ev(`!$("showPop").hidden && $("showCount").textContent.trim() === "· 1" && !$("gridLayer").hidden`), await ev(`$("showCount").textContent`));
  await ev(`$("showGrid").click(); true`); await sleep(100);
  await send("Input.dispatchKeyEvent", { type: "keyDown", key: "Escape", code: "Escape", windowsVirtualKeyCode: 27 });
  await send("Input.dispatchKeyEvent", { type: "keyUp", key: "Escape", code: "Escape", windowsVirtualKeyCode: 27 }); await sleep(150);
  check("Esc closes it, focus back on Show", await ev(`$("showPop").hidden && document.activeElement === $("btnShow") && $("showCount").classList.contains("hidden")`));
  await ev(`document.activeElement.blur(); true`);
  await key("o", { code: "KeyO" }); await sleep(300);
  check("O shows the original with the menu closed", await ev(`$("showRef").checked && $("showPop").hidden`));
  await key("o", { code: "KeyO" }); await sleep(200);
  await pane("shapes"); await sleep(300);
  await ev(`$("btnShow").click(); true`); await sleep(150);
  check("shapes view: directions, no woven", await ev(`$("showDirections").offsetParent !== null && $("showWeave").offsetParent === null`));
  await mouse("mousePressed", 700, 600, "left"); await mouse("mouseReleased", 700, 600, "left"); await sleep(150);
  check("a click elsewhere closes it", await ev(`$("showPop").hidden`));
  await pane("stitches"); await sleep(300);

  // ------------------------------------------------ left panel: sections, Fabric & thread, compact numbers
  check("sections: Size & colours, Picture, Fabric, thread & machine, Stitches", await ev(`[...document.querySelectorAll("#leftPanel .ed-sec:not(.hidden) > summary")].map((s) => s.firstChild.textContent.trim()).slice(0, 4).join("|") === "Size & colours|Picture|Fabric, thread & machine|Stitches"`),
    await ev(`[...document.querySelectorAll("#leftPanel .ed-sec:not(.hidden) > summary")].map((s) => s.firstChild.textContent.trim()).join("|")`));
  check("fabric and thread are stitch settings in their own section", await ev(`["fabric", "thread_weight"].every((k) => $("stitchForm").elements[k]?.closest("#materialFields"))`));
  const undo0 = await ev(`$("btnUndo").title`);
  await ev(change(`$("stitchForm").elements.thread_weight`, "60")); await sleep(300); await waitFor(idle, 60000);
  check("a fabric & thread change is one undo step and is marked", await ev(`$("btnUndo").title !== ${JSON.stringify(undo0)} && $("stitchForm").elements.thread_weight.closest("label").classList.contains("mod") && document.querySelector('.ed-sec[data-sec="material"]').classList.contains("mod")`),
    await ev(`$("btnUndo").title`));
  await ev(`History.undo(); true`); await waitFor(`${idle} && $("stitchForm").elements.thread_weight.value === "40"`, 60000);
  check("undo puts it back", await ev(`!$("stitchForm").elements.thread_weight.closest("label").classList.contains("mod")`));
  await ev(`document.querySelector('.ed-sec[data-sec="stitches"]').open = true; true`);
  // what the browser says it is decides the expected layout: a mouse screen gets the compact rows
  // and help on hover, a touch screen (or a headless browser that reports one) keeps them visible
  const compact = await ev(`matchMedia("(min-width: 900px) and (pointer: fine)").matches`);
  const hoverScreen = await ev(`matchMedia("(hover: hover) and (pointer: fine)").matches`);
  if (compact) check("number fields are one row (label left, value right)", await ev(`(() => { const i = $("stitchForm").elements.fill_spacing_mm, l = i.closest("label").querySelector(".lbl");
    const a = i.getBoundingClientRect(), b = l.getBoundingClientRect(); return a.left > b.right - 1 && Math.abs((a.top + a.bottom) / 2 - (b.top + b.bottom) / 2) < 14 && a.width < 100; })()`));
  else check("number fields (touch screen reported): label above a full-width field", await ev(`(() => { const i = $("stitchForm").elements.fill_spacing_mm, l = i.closest("label").querySelector(".lbl");
    return i.getBoundingClientRect().top >= l.getBoundingClientRect().bottom - 1; })()`));

  // ------------------------------------------------ help buttons: on demand, or always
  const helpOpacity = `getComputedStyle($("stitchForm").elements.fill_spacing_mm.closest("label").querySelector(".help")).opacity`;
  await ev(`HelpButtons.set(false); true`); await sleep(300); // the buttons fade (0.12 s)
  if (hoverScreen) {
    check("help buttons hidden until you point at the setting", await ev(`${helpOpacity} === "0"`), await ev(helpOpacity));
    // :hover forced through the devtools (a moved mouse depends on the window and the fonts)
    await ev(`$("stitchForm").elements.fill_spacing_mm.closest("label").setAttribute("data-smoke-hover", "1"); true`);
    await send("DOM.enable"); await send("CSS.enable");
    const doc = await send("DOM.getDocument", { depth: 0 });
    const node = await send("DOM.querySelector", { nodeId: doc.result.root.nodeId, selector: "[data-smoke-hover]" });
    await send("CSS.forcePseudoState", { nodeId: node.result.nodeId, forcedPseudoClasses: ["hover"] }); await sleep(300);
    check("pointing at it shows it", await ev(`${helpOpacity} === "1"`), await ev(helpOpacity));
    await send("CSS.forcePseudoState", { nodeId: node.result.nodeId, forcedPseudoClasses: [] }); await sleep(300);
    await ev(`document.querySelector("[data-smoke-hover]")?.removeAttribute("data-smoke-hover"); true`);
  } else {
    check("touch screen reported: help buttons stay visible", await ev(`${helpOpacity} === "1"`), await ev(helpOpacity));
  }
  check("the section headings keep theirs", await ev(`getComputedStyle($("selTitle").querySelector(".help")).opacity === "1"`));
  await ev(`$("btnKeys").click(); true`); await sleep(150);
  check("Help and shortcuts has the switch", await ev(`$("keysDialog").open && !$("helpAlways").checked`));
  await ev(`$("helpAlways").click(); $("keysDialog").close(); true`); await sleep(300);
  check("always show: visible and remembered", await ev(`${helpOpacity} === "1" && localStorage.getItem("helpAlways") === "1"`));
  await ev(`localStorage.removeItem("helpAlways"); true`);

  // ------------------------------------------------ design figures
  check("figures use the singular for one", await ev(`![...document.querySelectorAll("#stats > div, #statsExtra > div")].some((d) => d.querySelector("b").textContent.trim() === "1" && /(colours|cuts|changes|stitches)/.test(d.querySelector("span").textContent))`),
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

  // ------------------------------------------------ Batch D: Selection is only its heading while nothing is selected
  await ev(`Shapes.select([]); true`); await sleep(200);
  check("empty selection: the group is one row", await ev(`$("selGroup").classList.contains("sel-empty") && $("selGroup").getBoundingClientRect().height < 64`),
    await ev(`Math.round($("selGroup").getBoundingClientRect().height) + " px"`));
  await ev(`${first}.click(); true`); await sleep(300);
  check("a selection opens it", await ev(`!$("selGroup").classList.contains("sel-empty") && $("selGroup").getBoundingClientRect().height > 100`));
  await ev(`Shapes.select([]); true`); await sleep(200);

  // ------------------------------------------------ four figures, the rest under More details (remembered)
  check("four figures shown, three more under More details", await ev(`$("stats").children.length === 4 && $("statsExtra").children.length === 3 && $("statsExtra").closest("#statsMore")`));
  const moreWas = await ev(`$("statsMore").open`);
  await ev(`$("statsMore").querySelector("summary").click(); true`); await sleep(150);
  check("More details opens and is remembered", await ev(`$("statsMore").open === ${!moreWas} && localStorage.getItem("statsMore") === ${JSON.stringify(moreWas ? "0" : "1")}`));
  await ev(`$("statsMore").querySelector("summary").click(); true`); await sleep(150);

  // ------------------------------------------------ Checks: a count, and first when there is a problem
  await ev(`$("warnings3").innerHTML = "<div>Test warning</div>"; renderChecks(); true`);
  check("a warning: count in the heading, Checks above Design info", await ev(`$("checkCount").textContent === "· 1" && !$("checkCount").hidden &&
    $("checkGroup").getBoundingClientRect().top < $("infoGroup").getBoundingClientRect().top`));
  await shot("layout-checks-first");
  await ev(`$("warnings3").innerHTML = ""; renderChecks(); true`);
  check("no warnings: no count, Design info first", await ev(`$("checkCount").hidden && $("checkGroup").getBoundingClientRect().top > $("infoGroup").getBoundingClientRect().top`));
  await ev(`renderStats(); true`);

  // ------------------------------------------------ the Threads menu
  await ev(`$("btnThrMenu").click(); true`); await sleep(150);
  check("Threads ⋯ opens its menu with Merge similar colours", await ev(`!$("thrMenu").hidden && !!$("thrMenu").querySelector("[data-similar]")`));
  await ev(`$("btnThrOpenAll").click(); true`); await sleep(150);
  check("open every thread's settings (menu closes)", await ev(`$("thrMenu").hidden && [...document.querySelectorAll("#layers .thr-more")].every((m) => !m.hidden)`));
  await ev(`$("btnThrMenu").click(); $("btnThrCloseAll").click(); true`); await sleep(150);
  check("close them all", await ev(`[...document.querySelectorAll("#layers .thr-more")].every((m) => m.hidden)`));
  check("the heading counts the sewn threads", await ev(`$("thrCount").textContent === "· " + state.design.layers.filter((l) => l.enabled).length`), await ev(`$("thrCount").textContent`));

  if (nThreads > 1) {
    // ------------------------------------------------ switched-off threads: their own closed group
    const off0 = await ev(`state.design.layers.filter((l) => !l.enabled).length`);
    const offIdx = await ev(`state.design.layers.filter((l) => l.enabled).at(-1).index`);
    await ev(`{ const c = document.querySelector('#layers [data-index="${offIdx}"] [data-k="enabled"]'); c.checked = false; c.dispatchEvent(new Event("change", { bubbles: true })); } true`);
    await waitFor(`${idle} && !!document.querySelector('#layers .thr-offgrp [data-index="${offIdx}"]')`, 60000); await sleep(200);
    check("a switched-off thread moves to Not sewn (closed)", await ev(`(() => { const g = document.querySelector("#layers .thr-offgrp");
      return !g.open && g.querySelector("summary").textContent.includes("Not sewn (${off0 + 1})") && !!g.querySelector('[data-index="${offIdx}"]'); })()`));
    await ev(`document.querySelector("#layers .thr-offgrp > summary").click(); true`); await sleep(150);
    await ev(`renderLayers(); true`);
    check("Not sewn stays open across updates", await ev(`document.querySelector("#layers .thr-offgrp").open`));
    await shot("layout-not-sewn");
    const back = `!document.querySelector('#layers .thr-offgrp [data-index="${offIdx}"]') && !!document.querySelector('#layers > .thr[data-index="${offIdx}"]')`;
    await ev(`$("btnUndo").click(); true`); await waitFor(`${idle} && ${back}`, 60000);
    check("undo brings it back to the list", await ev(back));

    // ------------------------------------------------ threads that look the same: a suggestion
    const [a, b] = await ev(`state.design.layers.filter((l) => l.enabled).slice(0, 2).map((l) => l.index)`);
    const hex0 = await ev(`state.design.layers.find((l) => l.index === ${a}).hex`);
    await ev(`{ const c = document.querySelector('#layers [data-index="${b}"] [data-k="hex"]'); c.value = ${JSON.stringify(hex0)}; c.dispatchEvent(new Event("change", { bubbles: true })); } true`);
    await waitFor(`${idle} && !$("simNote").hidden`, 60000);
    check("same-looking threads: a note offers to merge them", await ev(`/2 threads look almost the same/.test($("simNote").textContent)`), await ev(`$("simNote").textContent`));
    await shot("layout-similar-note");
    await ev(`$("simNote").querySelector("[data-similar]").click(); true`); await sleep(200);
    check("Merge… opens the dialog at the suggestion's closeness", await ev(`$("similarDialog").open && +$("similarDe").value === Similar.SUGGEST_DE`));
    await waitFor(`$("similarList").querySelectorAll("input:checked").length > 0`, 20000);
    check("…with the pair already ticked", await ev(`$("similarList").querySelectorAll("input:checked").length >= 1`));
    await ev(`$("similarDialog").close(); true`);
    await ev(`$("simNote").querySelector("[data-dismiss]").click(); true`); await sleep(100);
    check("dismissed: hidden", await ev(`$("simNote").hidden`));
    await ev(`$("btnUndo").click(); true`); await waitFor(`${idle} && state.design.layers.find((l) => l.index === ${b}).hex !== ${JSON.stringify(hex0)}`, 60000);
    await sleep(300);
    check("and it stays hidden for this design", await ev(`$("simNote").hidden`));
  } else check("Not sewn / similar threads (skipped: one thread)", true);

  // ------------------------------------------------ Batch E: Export is its icon below 900 px
  await send("Emulation.setDeviceMetricsOverride", { width: 800, height: 900, deviceScaleFactor: 1, mobile: false }); await sleep(400);
  check("narrow: Export is its icon, still named", await ev(`getComputedStyle($("btnExport").querySelector("span")).display === "none" && $("btnExport").getAttribute("aria-label") === "Export" && $("btnExport").offsetParent !== null`));
  await send("Emulation.setDeviceMetricsOverride", { width: 1920, height: 1080, deviceScaleFactor: 1, mobile: false }); await sleep(400);

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
