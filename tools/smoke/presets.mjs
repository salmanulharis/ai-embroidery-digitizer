// Headless browser smoke test of the settings presets via the Chrome DevTools protocol (no dependencies).
// Usage: node tools/smoke/presets.mjs [out-dir]     (see tools/smoke/README.md)
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
  await waitFor(`$("presetPick").options.length > 3`, 10000);
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

  // the upload analysis offers a preset (the example's lettering is under 6 mm); it is not applied by itself
  check("suggestion offered after upload", await ev(`/Suggested preset: Small text & logos/.test($("smartInfo").textContent) && !!$("smartInfo").querySelector("[data-suggest]")`),
    await ev(`$("smartInfo").querySelector(".preset-offer")?.textContent.trim().replace(/\\s+/g, " ") || "none"`));
  check("suggestion not applied by itself", await ev(`$("stitchForm").elements.satin_spacing_mm.value === "0.4" && $("text_mode").value === "auto"`));

  // the picker: built-ins, nothing chosen, Apply off until a preset is chosen
  check("preset bar lists built-ins", await ev(`[...$("presetPick").options].some((o) => o.value === "b:standard")`));
  check("apply disabled without a choice", await ev(`$("btnPresetApply").disabled`));
  await ev(change(`$("presetPick")`, "b:small_text"));
  check("hint describes the preset", /lettering/.test(await ev(`$("presetHint").textContent`)), await ev(`$("presetHint").textContent`));

  // apply: values filled in, undo step, the preset becomes the baseline
  const st0 = await ev(`state.preview.stats.stitches`);
  await ev(`$("btnPresetApply").click(); true`);
  await waitFor(`Jobs.pending()`, 3000).catch(() => {});
  await waitFor(`${idle} && state.preview.stats.stitches !== ${st0}`, 90000);
  check("preset values applied", await ev(`${f}.thread_weight.value === "60" && ${f}.satin_spacing_mm.value === "0.35" && $("text_mode").value === "sharp" && $("min_feature_mm").value === "0.2"`));
  check("toast reports the change", /Applied Small text & logos · \d+ settings changed/.test(await ev(`$("toastText").textContent`)), await ev(`$("toastText").textContent`));
  check("no marks right after applying", await ev(`$("modSummary").classList.contains("hidden") && $("presetModified").classList.contains("hidden")`), await ev(`$("modCount").textContent`));
  check("one undo step named after the preset", /Undo: preset Small text & logos/.test(await ev(`$("btnUndo").title`)), await ev(`$("btnUndo").title`));
  await shot("p3-applied");

  // a change after applying: marked against the preset, "Changed" badge
  await ev(change(`${f}.fill_spacing_mm`, "0.6"));
  await waitFor(`${idle}`, 60000);
  check("change after preset marked", await ev(`!$("presetModified").classList.contains("hidden") && $("modCount").textContent === "1 setting changed"`), await ev(`$("modCount").textContent`));
  await ev(`History.undo(); true`);
  await waitFor(`${idle} && ${f}.fill_spacing_mm.value === "0.4"`, 60000);
  check("undo clears the badge", await ev(`$("presetModified").classList.contains("hidden")`));
  // undoing the preset puts back the stitch and the picture settings in one step
  await ev(`History.undo(); true`);
  await waitFor(`${idle} && ${f}.thread_weight.value === "40"`, 90000);
  check("undo preset restores both", await ev(`$("text_mode").value !== "sharp" && ${f}.satin_spacing_mm.value === "0.4"`), await ev(`$("text_mode").value`));
  await ev(`History.redo(); true`);
  await waitFor(`${idle} && ${f}.thread_weight.value === "60"`, 90000);
  check("redo preset", await ev(`$("text_mode").value === "sharp"`));

  // save as preset, then the same name again: Replace / Keep both (unique names: re-runnable on the same data)
  const name = `Smoke ${Date.now() % 100000}`;
  const before = await ev(`Presets.status().count`);
  await ev(`$("btnPresetSave").click(); true`);
  check("save dialog opens with a summary", await ev(`$("presetDialog").open && /stitch, fabric & thread/.test($("presetSummary").textContent)`), await ev(`$("presetSummary").textContent`));
  await shot("p3-save");
  await ev(`$("presetName").value = ${JSON.stringify(name)}; $("presetForm").requestSubmit(); true`);
  await waitFor(`!$("presetDialog").open && $("presetPick").value.startsWith("u:")`, 10000);
  check("saved preset selected in picker", await ev(`$("presetPick").selectedOptions[0].textContent === ${JSON.stringify(name)}`));
  await ev(`$("btnPresetSave").click(); $("presetName").value = ${JSON.stringify(name.toUpperCase())}; $("presetForm").requestSubmit(); true`);
  await waitFor(`!$("presetClash").classList.contains("hidden")`, 10000);
  check("name clash offers replace / keep both", true, await ev(`$("presetStatus").textContent`));
  await ev(`$("presetKeepBoth").click(); true`);
  await waitFor(`!$("presetDialog").open`, 10000);
  check("keep both saves a second name", await ev(`$("presetPick").selectedOptions[0].textContent === ${JSON.stringify(name.toUpperCase() + " (2)")}`), await ev(`$("presetPick").selectedOptions[0].textContent`));

  // manage: list, delete with inline confirm
  await ev(`$("btnPresetManage").click(); true`);
  await waitFor(`$("presetsDialog").open && $("presetsBody").querySelectorAll("[data-del]").length === ${before + 2}`, 10000);
  await shot("p3-manage");
  await ev(`$("presetsBody").querySelector("[data-del]").click(); true`);
  check("delete asks inline", await ev(`!!$("presetsBody").querySelector("[data-del-yes]")`));
  await ev(`$("presetsBody").querySelector("[data-del-yes]").click(); true`);
  await waitFor(`$("presetsBody").querySelectorAll("[data-del]").length === ${before + 1}`, 10000);
  check("preset deleted", true);
  await ev(`$("presetsDialog").close(); true`);

  // app defaults put the stitch settings back
  await ev(change(`$("presetPick")`, "@defaults"));
  await ev(`$("btnPresetApply").click(); true`);
  await waitFor(`${idle} && ${f}.thread_weight.value === "40"`, 60000);
  check("app defaults restore stitch settings", await ev(`${f}.satin_spacing_mm.value === "0.4"`));

  // account page card and admin console
  await ev(`Account.open(); true`);
  await waitFor(`/${before + 1} saved/.test($("accPresets").textContent)`, 10000);
  check("account page shows presets", true, await ev(`$("accPresets").querySelector(".big-num").textContent`));
  await ev(`Admin.open("plans"); true`);
  await waitFor(`!!document.querySelector("[data-tier]")`, 10000);
  check("admin lists built-in presets with tiers", await ev(`document.querySelectorAll("[data-tier]").length === 12`));
  await shot("p3-admin");
  await ev(`document.querySelector("[data-plan]").click(); true`);
  check("plan dialog has a limits field", await ev(`!!$("planLimits").querySelector('[data-pl="max_custom_presets"]')`));
  await ev(`$("planDialog").close(); Views.close(); true`);

  // a Free account: premium presets locked, saving locked
  const email = `free${Date.now()}@example.com`;
  const made = await ev(`fetch("/api/admin/users", { method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ email: ${JSON.stringify(email)}, password: "FreeUser-12345" }) }).then((r) => r.status)`);
  check("free test account created", made === 200, String(made));
  await ev(`Auth.signOut(); true`);
  await waitFor(`document.body.classList.contains("signed-out")`, 20000);
  await signIn(email, "FreeUser-12345");
  await openExample();
  check("premium presets locked", await ev(`/🔒/.test([...$("presetPick").options].find((o) => o.value === "b:fine_detail").textContent)`));
  await ev(change(`$("presetPick")`, "b:fine_detail"));
  check("locked preset cannot be applied", await ev(`$("btnPresetApply").disabled && !!$("presetHint").querySelector("[data-lock]")`), await ev(`$("presetHint").textContent`));
  await ev(`$("presetHint").querySelector("[data-lock]").click(); true`);
  await sleep(200);
  check("lock popover names the plan", await ev(`$("presetLockPop").matches(":popover-open") && /the Pro plan/.test($("presetLockPop").textContent) && /administrator/.test($("presetLockPop").textContent)`),
    await ev(`$("presetLockPop")?.textContent || "no popover"`));
  check("lock badge reports expanded", await ev(`$("presetHint").querySelector("[data-lock]").getAttribute("aria-expanded") === "true"`));
  await shot("p3-lock-popover");
  await send("Input.dispatchKeyEvent", { type: "keyDown", key: "Escape", code: "Escape", windowsVirtualKeyCode: 27 });
  await send("Input.dispatchKeyEvent", { type: "keyUp", key: "Escape", code: "Escape", windowsVirtualKeyCode: 27 });
  await sleep(200);
  check("Escape closes the popover", await ev(`!$("presetLockPop").matches(":popover-open") && $("presetHint").querySelector("[data-lock]").getAttribute("aria-expanded") === "false"`));
  check("save shows a lock", await ev(`/🔒/.test($("btnPresetSave").textContent)`));
  await ev(`$("btnPresetSave").click(); true`);
  check("save explains the plan", await ev(`!$("presetDialog").open && /not included/.test($("toastText").textContent)`), await ev(`$("toastText").textContent`));
  await shot("p3-free");

  // the suggestion button applies the suggested preset
  await ev(`$("smartInfo").querySelector("[data-suggest]").click(); true`);
  await waitFor(`${idle} && $("stitchForm").elements.thread_weight.value === "60"`, 90000);
  check("suggestion button applies the preset", await ev(`$("presetPick").value === "b:small_text" && !$("smartInfo").querySelector("[data-suggest]")`));

  // tablet: the settings panel stays beside the preview; the bar fits it
  const fits = (el) => `(() => { const r = ${el}.getBoundingClientRect(), p = $("presetBar").getBoundingClientRect(); return r.width > 0 && r.right <= p.right + 1 && r.left >= p.left - 1; })()`;
  await send("Emulation.setDeviceMetricsOverride", { width: 1024, height: 768, deviceScaleFactor: 1, mobile: false });
  await sleep(500);
  check("tablet: preset bar fits", await ev(`${fits(`$("btnPresetApply")`)} && ${fits(`$("btnPresetManage")`)} && $("presetPick").offsetWidth >= 140`),
    await ev(`$("presetPick").offsetWidth + "px select"`));
  await shot("p3-tablet");

  // phone: the settings sheet shows the bar first; dialogs and the popover fit the screen
  await send("Emulation.setDeviceMetricsOverride", { width: 390, height: 844, deviceScaleFactor: 2, mobile: true });
  await sleep(600);
  await send("Emulation.setPageScaleFactor", { pageScaleFactor: 1 }); await sleep(300);
  // headless browsers render no frames between checks, so the sheet's slide-in transition would never finish
  await ev(`document.head.insertAdjacentHTML("beforeend", "<style id=smokeNoAnim>*{transition:none!important}</style>"); true`);
  await ev(`$("barSettings").click(); true`); await sleep(500);
  check("phone: settings sheet open", await ev(`document.body.classList.contains("sheet-left")`));
  check("phone: preset bar visible in the settings sheet", await ev(`(() => { const r = $("presetBar").getBoundingClientRect(); return r.height > 40 && r.top >= 0 && r.top < innerHeight / 2 && r.left >= 0 && r.right <= innerWidth; })()`),
    await ev(`JSON.stringify({ bar: $("presetBar").getBoundingClientRect(), innerWidth, scrollX, vv: visualViewport.offsetLeft + "/" + visualViewport.scale })`));
  check("phone: bar fits the width", await ev(`${fits(`$("btnPresetApply")`)} && ${fits(`$("btnPresetSave")`)} && $("presetBar").scrollWidth <= $("presetBar").clientWidth + 1 && $("presetPick").offsetWidth >= 150`),
    await ev(`$("presetPick").offsetWidth + "px select, bar " + $("presetBar").scrollWidth + "/" + $("presetBar").clientWidth`));
  check("phone: no horizontal scroll", await ev(`document.documentElement.scrollWidth <= innerWidth + 1`));
  await shot("p3-phone-settings");
  await ev(`$("btnPresetManage").click(); true`);
  await waitFor(`$("presetsDialog").open && !!$("presetsBody").querySelector("[data-lock]")`, 10000);
  check("phone: manage dialog fits", await ev(`$("presetsDialog").getBoundingClientRect().width <= innerWidth && $("presetsBody").scrollWidth <= $("presetsBody").clientWidth + 1`),
    await ev(`$("presetsDialog").getBoundingClientRect().width + " / " + innerWidth`));
  await ev(`$("presetsBody").querySelector("[data-lock]").click(); true`);
  await sleep(200);
  check("lock popover works inside the dialog", await ev(`(() => { const p = $("presetLockPop"), r = p.getBoundingClientRect(); return p.matches(":popover-open") && p.closest("dialog") === $("presetsDialog") && r.left >= 0 && r.right <= innerWidth; })()`),
    await ev(`JSON.stringify($("presetLockPop")?.getBoundingClientRect())`));
  await shot("p3-phone-manage");
  await ev(`$("presetsDialog").close(); true`);
} catch (e) {
  results.push("ERROR " + e.message);
  try { await shot("p3-error"); } catch (_) {}
} finally {
  console.log(results.join("\n"));
  console.log("\nexceptions:", errors.length ? errors.join("\n---\n") : "none");
  console.log("console errors/warnings:", logs.filter((l) => !l.includes("/api/auth/me")).join("\n") || "none");
  try { ws.close(); } catch (_) {}
  chrome.kill();
  process.exit(0);
}
