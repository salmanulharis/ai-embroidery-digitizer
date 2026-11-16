"use strict";

const $ = (id) => document.getElementById(id);
// pane: "shapes" (the design as shapes) or "stitches" (the stitch preview) - see setPane()
const state = { config: null, project: null, design: null, preview: null, view: { scale: 1, x: 0, y: 0 }, mode: "real", variant: "optimized", upto: 0, playing: false, pane: "shapes", autoPane: true };
// stitches currently shown: optimised (default) or the original, unreduced version
const activeOps = () => (state.variant === "original" && state.preview.original_ops) || state.preview.ops;
const ico = (name) => `<svg class="ic" aria-hidden="true"><use href="#i-${name}"/></svg>`;

// ------------------------------------------------------------------ helpers
async function api(path, opts = {}) {
  const res = await fetch(path, opts);
  if (!res.ok) {
    let msg = res.statusText;
    try { msg = (await res.json()).detail || msg; } catch (_) {}
    if (res.status === 401 && path !== "/api/auth/login") Auth.sessionEnded();
    const err = new Error(msg);
    err.status = res.status;
    throw err;
  }
  return res.headers.get("content-type")?.includes("json") ? res.json() : res;
}
const json = (method, body) => ({ method, headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });

// Screen readers hear status politely and errors at once; the text is set a frame after
// clearing it so the same message twice is announced twice.
function announce(msg, urgent = false) {
  const region = $(urgent ? "srAlert" : "srStatus");
  region.textContent = "";
  requestAnimationFrame(() => (region.textContent = msg));
}
const reducedMotion = () => window.matchMedia("(prefers-reduced-motion: reduce)").matches;

// Notes close after 6 s (not while the pointer or focus is on them); errors stay until closed.
const INFO_TOAST_MS = 6000;
function hideToast() {
  clearTimeout(toast.timer);
  $("toast").classList.add("hidden");
}
function toast(msg, info = false) {
  const t = $("toast");
  $("toastText").textContent = msg;
  t.className = "toast" + (info ? " info" : "");
  t.dataset.info = info ? "1" : "";
  announce(msg, !info);
  clearTimeout(toast.timer);
  if (info) toast.timer = setTimeout(hideToast, INFO_TOAST_MS);
}
(() => {
  const t = $("toast");
  const pause = () => clearTimeout(toast.timer);
  const resume = () => { if (t.dataset.info && !t.matches(":hover, :focus-within")) toast.timer = setTimeout(hideToast, INFO_TOAST_MS); };
  t.addEventListener("mouseenter", pause);
  t.addEventListener("focusin", pause);
  t.addEventListener("mouseleave", resume);
  t.addEventListener("focusout", resume);
  $("toastClose").onclick = hideToast;
  document.addEventListener("keydown", (ev) => { if (ev.key === "Escape" && !t.classList.contains("hidden")) hideToast(); });
})();
/** Blocking: covers the page. Only for steps with nothing to look at yet (upload, opening,
 *  closing) and the explicit AI tasks. Editing uses work() and keeps the preview visible. */
async function busy(text, fn, { quiet = false } = {}) {
  $("busyText").textContent = text;
  $("busy").classList.remove("hidden");
  document.querySelector("main").setAttribute("aria-busy", "true");
  announce(text);
  Tip.hide();
  try { return await fn(); } catch (e) { if (!quiet) toast(e.message); throw e; } finally {
    $("busy").classList.add("hidden");
    document.querySelector("main").removeAttribute("aria-busy");
  }
}

// Non-blocking work on the open design: one request at a time, in the order asked (the
// server's design file is changed by several of them, so they must not overlap). The
// preview stays on screen; the bottom bar shows what is happening.
let workChain = Promise.resolve();
const working = [];
function work(label, fn, { quiet = false } = {}) {
  const run = workChain.then(async () => {
    working.push(label);
    renderStatus();
    try { return await fn(); } finally {
      working.splice(working.indexOf(label), 1);
      renderStatus();
    }
  });
  workChain = run.catch(() => {});
  return run.catch((e) => { if (!quiet) toast(e.message); throw e; });
}

// ------------------------------------------------------------------ pages: upload / editor
// Two pages: the upload page and the editor. Each page change adds a browser history entry,
// so Back returns to the previous page.
let currentPage = "upload";
function showPage(page, { fromHistory = false } = {}) {
  const changed = page !== currentPage;
  currentPage = page;
  $("step1").classList.toggle("hidden", page !== "upload");
  $("editor").classList.toggle("hidden", page !== "editor");
  document.body.classList.toggle("page-editor", page === "editor");
  if (page !== "editor") closeSheets();
  updateHeader();
  if (changed) {
    window.scrollTo(0, 0);
    if (!fromHistory) history.pushState({ page }, "");
  }
  requestAnimationFrame(measureHeader);
  if (page === "editor") requestAnimationFrame(() => { if (state.pane === "stitches") { if (state.viewMoved) draw(); else fit(); } });
  saveSession();
}
function updateHeader() {
  const open = !!state.project;
  $("hdrFile").classList.toggle("hidden", !open);
  $("btnNew").classList.toggle("hidden", currentPage !== "editor");
  $("hdrName").textContent = open ? state.project.filename : "";
  $("hdrName").title = open ? state.project.filename : "";
  $("openNote").classList.toggle("hidden", !open || currentPage !== "upload");
  if (open) $("openName").textContent = state.project.filename;
}
window.addEventListener("popstate", (ev) => {
  const page = ev.state?.page || (ev.state?.step >= 2 ? "editor" : "upload");
  showPage(page === "editor" && state.design ? "editor" : "upload", { fromHistory: true });
});
// account / admin pages: keep --hdr right for their sticky parts
function measureHeader() {
  const header = document.querySelector("header");
  const h = getComputedStyle(header).position === "sticky" ? header.getBoundingClientRect().height : 0;
  document.documentElement.style.setProperty("--hdr", `${Math.round(h)}px`);
}
window.addEventListener("resize", measureHeader);

// ------------------------------------------------------------------ the open design survives a reload
// Kept for this browser tab only: the project, the page, the preview and the form values.
// The design itself stays on the server and is fetched again.
const SESSION_KEY = "openDesign";
function formSnapshot() {
  const v = {};
  // fields with an id, except the stitch form (its fields get generated ids, see Tip)
  document.querySelectorAll("#leftPanel input[id], #leftPanel select[id]").forEach((e) => {
    if (!e.closest("#stitchForm")) v["#" + e.id] = e.type === "checkbox" ? e.checked : e.value;
  });
  [...$("stitchForm").elements].forEach((e) => { if (e.name) v[e.name] = e.type === "checkbox" ? e.checked : e.value; });
  return v;
}
function restoreForm(v) {
  Object.entries(v).forEach(([k, val]) => {
    const e = k.startsWith("#") ? $(k.slice(1)) : $("stitchForm").elements[k];
    if (!e || !e.matches?.("input, select") || (k.startsWith("#") && e.closest("#stitchForm"))) return;
    if (e.type === "checkbox") e.checked = !!val; else e.value = val;
  });
  showAdjustValues();
  const fabric = $("stitchForm").elements.fabric;
  if (fabric) showFabricNote(fabric.value);
  updateStyleParams();
}
function saveSession() {
  try {
    if (!state.project) sessionStorage.removeItem(SESSION_KEY);
    else sessionStorage.setItem(SESSION_KEY, JSON.stringify({ uid: Auth.user()?.id, meta: state.project, page: currentPage, pane: state.pane,
      form: formSnapshot(), baseline: state.baseline || null, history: History.save() }));
  } catch (_) {}
}
window.addEventListener("pagehide", saveSession);
// Privacy: projects are temporary. When the page goes away the server is told; it deletes
// the project after a short grace period unless the page comes back (a reload reopens it).
// Browsers do not always send this, so the server also deletes idle projects on its own.
window.addEventListener("pagehide", () => {
  if (state.project && Auth.user()) try { navigator.sendBeacon(`/api/projects/${state.project.id}/release`); } catch (_) {}
});
window.addEventListener("pageshow", (ev) => { // back/forward cache: the page is back, keep the project
  if (ev.persisted && state.project) api(`/api/projects/${state.project.id}/quota`).catch(() => {});
});
/** Forget the open design in this tab (after it was closed or removed on the server). */
function forgetDesign() {
  Jobs.reset();
  Object.assign(state, { project: null, design: null, preview: null });
  try { sessionStorage.removeItem(SESSION_KEY); } catch (_) {}
  showPage("upload");
}
async function closeDesign() {
  if (!state.project) return;
  if (!confirm(`Close ${state.project.filename}?

It is removed from the server. Download your files first if you still need them.`)) return;
  await busy("Closing…", () => api(`/api/projects/${state.project.id}`, { method: "DELETE" })).catch(() => {});
  forgetDesign();
  toast("The design was closed and removed from the server.", true);
}

async function resumeDesign() {
  let saved = null;
  try { saved = JSON.parse(sessionStorage.getItem(SESSION_KEY)); } catch (_) {}
  if (!saved?.meta?.id || saved.uid !== Auth.user()?.id) return;
  try {
    openProject(saved.meta);
    restoreForm(saved.form || {});
    $("hoopToggle").classList.toggle("hidden", !chosenHoop());
    // what the design started with and the undo steps of this tab (earlier versions: none)
    setBaseline(saved.baseline || { extract: extractValues(), stitch: { ...defaultStitch } });
    History.reset(saved.history);
    await loadSource();
    // a design that was never prepared (reload right after upload) is prepared now
    const res = await busy("Opening your design again…", () => api(`/api/projects/${saved.meta.id}/design`), { quiet: true }).catch(() => null);
    if (res) renderDesign(res);
    else await busy("Opening your design again…", () => api(`/api/projects/${saved.meta.id}/extract`, json("POST", { options: extractOptions() }))).then(renderDesign);
    // earlier versions saved the step (3 and 4 = stitches)
    const pane = saved.pane || (saved.step >= 3 ? "stitches" : "shapes");
    state.autoPane = pane === "shapes" && !saved.pane;
    setPane(pane);
    if (saved.page === "upload") showPage("upload"); else showPage("editor");
    Jobs.stitch({ force: true, delay: 0 });
    toast(`Welcome back – ${saved.meta.filename} is open again.`, true);
  } catch (_) {
    Jobs.reset();
    Object.assign(state, { project: null, design: null, preview: null });
    showPage("upload");
    toast("Your last design is no longer on the server (designs are removed when they are closed, after a while without use, "
      + "or when you sign out). Please upload the file again.", true);
  }
}
const esc = (s) => String(s).replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));

// ------------------------------------------------------------------ init
const DENOISE_LEVELS = ["Off", "Low", "Medium", "High"];

async function init() {
  Tip.init();
  Shapes.init();
  Auth.init();
  Admin.init();
  state.config = await api("/api/config");
  const hoop = $("hoop");
  hoop.innerHTML = '<option value="">Custom size</option>' + state.config.hoops.map((h, i) => `<option value="${i}">${esc(h.name)}</option>`).join("");
  hoop.onchange = () => {
    const h = state.config.hoops[hoop.value];
    if (h) {
      // the design's own proportions once it is prepared (a removed background changes them)
      const a = state.design ? state.design.width_mm / state.design.height_mm : aspect();
      $("width_mm").value = Math.round(Math.min(h.width, h.height * a) * 0.9);
      if (state.project) Jobs.extract();
    }
    hoopChanged();
  };
  const nc = $("num_colors");
  for (let i = 1; i <= 15; i++) nc.insertAdjacentHTML("beforeend", `<option value="${i}">${i}</option>`);
  bindAdjustments();

  buildStitchForm();
  bindSettingsMarkers();
  bindUpload();
  bindEditor();
  Workspace.init(); // grid + rulers, resizable panels (workspace.js)
  ShapeTools.init(); // shape menu + selection toolbar (shapetools.js)
  Similar.init();
  Split.init();
  Calibration.init();
  Theme.init();
  bindCanvas();
  await Auth.start(state.config); // the sign-in screen until someone is signed in
  document.body.classList.remove("booting", "signed-out");
  await AI.init(state.config); // this account's AI settings
  Account.init(state.config); // plan + usage chip, account page, admin console
  Presets.init(); // built-in and own settings presets (presets.js)
  history.replaceState({ page: "upload" }, "");
  await resumeDesign();
}

// ------------------------------------------------------------------ picture adjustments + text detail
const SHARPEN_LEVELS = ["Off", "Low", "Medium", "High"];
const ADJUST_DEFAULTS = { brightness: 0, contrast: 0, sharpen: 0, denoise: 2 };
function showAdjustValues() {
  const sign = (v) => (v > 0 ? `+${v}` : `${v}`);
  $("brightnessOut").textContent = sign(+$("brightness").value);
  $("contrastOut").textContent = sign(+$("contrast").value);
  $("sharpenOut").textContent = SHARPEN_LEVELS[+$("sharpen").value];
  $("denoiseOut").textContent = DENOISE_LEVELS[+$("denoise").value];
  $("denoise").setAttribute("aria-valuetext", DENOISE_LEVELS[+$("denoise").value]);
}
// "Text & fine details" presets -> the advanced detail fields
const TEXT_MODES = {
  sharp: { min_feature_mm: 0.2, min_area_mm2: 0.3 },
  standard: { min_feature_mm: 0.3, min_area_mm2: 1.0 },
  simple: { min_feature_mm: 0.8, min_area_mm2: 2.0 },
};
function applyTextMode() {
  const mode = $("text_mode").value;
  const smart = state.smart && state.smart.extract;
  const v = mode === "auto" ? (smart ? { min_feature_mm: smart.min_feature_mm, min_area_mm2: 1.0 } : TEXT_MODES.standard) : TEXT_MODES[mode];
  $("min_feature_mm").value = v.min_feature_mm;
  $("min_area_mm2").value = v.min_area_mm2;
}
// Every setting that prepares the design again does so by itself once it is changed
// (slider released, field left or Enter pressed): there is no "Update preview" button.
function bindAdjustments() {
  ["brightness", "contrast", "sharpen", "denoise"].forEach((k) => {
    $(k).addEventListener("input", showAdjustValues);
    $(k).addEventListener("change", () => state.project && Jobs.extract());
  });
  $("btnResetAdjust").onclick = () => {
    const smart = state.smart && state.smart.extract;
    Object.entries(ADJUST_DEFAULTS).forEach(([k, v]) => ($(k).value = smart && k === "denoise" ? smart.denoise : v));
    showAdjustValues();
    if (state.project) Jobs.extract();
  };
  $("text_mode").onchange = () => { applyTextMode(); if (state.project) Jobs.extract(); };
  ["min_feature_mm", "min_area_mm2"].forEach((k) => $(k).addEventListener("change", () => {
    const match = Object.entries(TEXT_MODES).find(([, v]) => v.min_feature_mm === +$("min_feature_mm").value && v.min_area_mm2 === +$("min_area_mm2").value);
    $("text_mode").value = match ? match[0] : "auto";
    if (state.project) Jobs.extract();
  }));
  ["width_mm", "num_colors", "background", "knockout"].forEach((k) => $(k).addEventListener("change", () => state.project && Jobs.extract()));
  showAdjustValues();
}

// Built-in picture analysis right after upload: smart settings by default (no AI key needed).
async function smartDefaults() {
  state.smart = null;
  $("smartInfo").classList.add("hidden");
  if (state.project.kind !== "raster") return;
  let r;
  try {
    r = await api(`/api/projects/${state.project.id}/ai/suggest`, json("POST", { ai: null, width_mm: +$("width_mm").value || 100 }));
  } catch (e) {
    return; // the analysis is a convenience: the standard settings still work
  }
  state.smart = r;
  const e = r.extract || {};
  $("num_colors").value = "";
  if (e.background) $("background").value = e.background;
  if (e.min_feature_mm != null) $("min_feature_mm").value = e.min_feature_mm;
  $("min_area_mm2").value = 1.0;
  if (e.denoise != null) $("denoise").value = e.denoise;
  $("text_mode").value = "auto";
  ["brightness", "contrast", "sharpen"].forEach((k) => ($(k).value = 0));
  showAdjustValues();
  const tw = r.stitch && r.stitch.thread_weight;
  if (tw && $("stitchForm").elements.thread_weight) $("stitchForm").elements.thread_weight.value = String(tw);
  const t = r.text || {};
  const facts = [
    `${e.num_colors} thread colour${e.num_colors === 1 ? "" : "s"}`,
    e.background === "auto" ? "background removed" : "background kept",
    t.found ? `text found (${t.lines} line${t.lines === 1 ? "" : "s"}, letters ~${t.letter_height_mm} mm)` : "no small text found",
  ];
  if (tw === 60) facts.push("fine 60 wt thread suggested");
  $("smartInfo").innerHTML = `<b>&#10024; Smart settings applied</b><span>${facts.map(esc).join(" · ")}</span>` +
    (r.notes || []).slice(0, 2).map((n) => `<span class="small muted">${esc(n)}</span>`).join("") +
    `<span class="small muted">Change anything below if the result is not what you want.</span>`;
  $("smartInfo").classList.remove("hidden");
  Presets.offer(r.preset); // a preset that suits the picture, offered with a button (never applied by itself)
}

function aspect() {
  const img = $("srcImg");
  return img.naturalWidth && img.naturalHeight ? img.naturalWidth / img.naturalHeight : 1;
}

// ------------------------------------------------------------------ upload
function bindUpload() {
  const drop = $("drop"), input = $("file");
  input.onchange = () => {
    const f = input.files[0];
    input.value = ""; // choosing the same file again still uploads it
    if (f) upload(f).catch(() => {});
  };
  $("btnChoose").onclick = () => input.click();
  // a bundled example goes through the normal upload, so it behaves like any file
  $("btnExample").onclick = async () => {
    const blob = await busy("Loading the example…", async () => {
      const r = await fetch("/static/example-logo.png");
      if (!r.ok) throw new Error("The example could not be loaded. Please try again.");
      return r.blob();
    }).catch(() => null);
    if (blob) upload(new File([blob], "example-logo.png", { type: "image/png" })).catch(() => {});
  };
  $("btnNew").onclick = () => { showPage("upload"); $("btnChoose").focus(); };
  $("btnBackToOpen").onclick = () => showPage("editor");
  $("btnCloseDesign").onclick = () => closeDesign().catch(() => {});
  ["dragenter", "dragover"].forEach((e) => drop.addEventListener(e, (ev) => { ev.preventDefault(); drop.classList.add("over"); }));
  ["dragleave", "drop"].forEach((e) => drop.addEventListener(e, (ev) => { ev.preventDefault(); drop.classList.remove("over"); }));
  drop.addEventListener("drop", (ev) => ev.dataTransfer.files[0] && upload(ev.dataTransfer.files[0]).catch(() => {}));
}

async function upload(file) {
  const fd = new FormData();
  fd.append("file", file);
  const meta = await busy("Uploading…", () => api("/api/projects", { method: "POST", body: fd }));
  openProject(meta);
  await loadSource();
  showPage("editor");
  await busy("Analysing your picture…", smartDefaults).catch(() => {});
  setBaseline(); // what this design starts with: marks and resets compare with it
  History.reset();
  await Jobs.extract({ delay: 0 }); // the stitches follow by themselves
}

// A new or reopened project: forget the previous design, set up the editor for this file.
function openProject(meta) {
  Jobs.reset();
  if (state.playing) togglePlay();
  Object.assign(state, { project: meta, design: null, preview: null, smart: null, viewMoved: false, autoPane: true, baseline: null });
  Shapes.reset();
  Render.reset();
  History.reset();
  Presets.reset();
  document.querySelectorAll("#leftPanel label.mod, #leftPanel .ed-sec.mod").forEach((n) => n.classList.remove("mod"));
  $("modSummary").classList.add("hidden");
  const raster = meta.kind === "raster";
  $("rasterOpts").classList.toggle("hidden", !raster);
  $("vectorOpts").classList.toggle("hidden", raster);
  $("smartInfo").classList.add("hidden");
  AI.onProject(meta);
  $("fileInfo").textContent = `${meta.filename} · ${raster ? `picture, ${meta.info.width_px} × ${meta.info.height_px} pixels` : "artwork file"}`;
  $("layers").innerHTML = "";
  $("svgBox").innerHTML = "";
  ["stats", "sequence", "warnings2", "warnings3"].forEach((id) => ($(id).innerHTML = ""));
  $("optimizeInfo").className = "optinfo hidden";
  $("sequenceTitle").classList.add("hidden");
  $("statsEmpty").classList.remove("hidden");
  $("variant").classList.add("hidden");
  $("variantHelp")?.classList.add("hidden");
  openThreads.clear();
  setPane("shapes");
  updateHeader();
  renderStatus();
}

async function loadSource() {
  await new Promise((resolve) => {
    const img = $("srcImg");
    img.onload = img.onerror = resolve;
    img.src = `/api/projects/${state.project.id}/source?t=${Date.now()}`;
  });
}

// ------------------------------------------------------------------ design (shapes)
function extractOptions() {
  const o = { width_mm: +$("width_mm").value, num_colors: $("num_colors").value ? +$("num_colors").value : null };
  if (state.project.kind === "raster") {
    Object.assign(o, {
      background: $("background").value,
      min_feature_mm: +$("min_feature_mm").value,
      min_area_mm2: +$("min_area_mm2").value,
      denoise: +$("denoise").value,
      brightness: +$("brightness").value,
      contrast: +$("contrast").value,
      sharpen: +$("sharpen").value,
    });
  } else {
    o.knockout = $("knockout").checked;
  }
  return o;
}

/** Prepare the design again now and wait until the preview is up to date (AI assistant). */
const extract = () => Jobs.extract({ delay: 0 });

function renderDesign(res) {
  state.design = res.design;
  $("svgBox").innerHTML = res.svg.replace(/<\?xml[^>]*>/, "");
  $("warnings2").innerHTML = res.design.warnings.map((w) => `<div>${esc(w)}</div>`).join("");
  renderLayers();
  // dim disabled layers in the shapes view
  res.design.layers.forEach((l) => {
    const g = $("svgBox").querySelector(`#layer-${l.index}`);
    if (g) {
      g.style.opacity = l.enabled ? 1 : 0.12;
      g.classList.toggle("layer-off", !l.enabled);
    }
  });
  History.setServer(res.can_undo, res.can_redo);
  Shapes.afterRender(res);
  updateStyleParams();
  renderStatus();
}

// stitch styles for colours and shapes come from the server (models.STITCH_STYLES)
const stitchStyles = () => state.config?.stitch_styles || [["auto", "Automatic"]];
// label + trade name ("Fill (large areas) — Tatami"), unless the label already starts with it
const styleLabel = (v, label) => {
  const term = state.config?.stitch_terms?.[v];
  return term && !label.toLowerCase().startsWith(term.toLowerCase()) ? `${label} — ${term}` : label;
};

// ------------------------------------------------------------------ threads (thread colours, sewn top to bottom)
// One compact row per colour: sew on/off, thread colour, name, shape count. The arrow opens
// its stitch style, direction, area, order and "merge into"; drag the handle onto another
// colour to merge them.
const openThreads = new Set(); // colours whose details are open (kept across updates)
function renderLayers() {
  const L = state.design.layers;
  $("layers").innerHTML = L.map((l, i) => {
    const n = esc(l.name), open = openThreads.has(l.index);
    return `
    <div class="thr ${l.enabled ? "" : "off"}" data-index="${l.index}" data-merge-target="${l.index}">
      <div class="thr-row">
        <span class="grip" data-drag-layer="${l.index}" title="Drag onto another colour to merge ${n} into it" role="img" aria-label="Drag ${n} onto another colour to merge">⠿</span>
        <input type="checkbox" data-k="enabled" ${l.enabled ? "checked" : ""} aria-label="Stitch ${n}">
        <input type="color" data-k="hex" value="${l.hex}" aria-label="Thread colour for ${n}">
        <input class="thr-name" data-k="name" value="${n}" aria-label="Name for ${n}">
        <span class="thr-meta">${l.objects} shape${l.objects === 1 ? "" : "s"}</span>
        <button type="button" class="ghost thr-toggle" data-expand aria-expanded="${open}" aria-controls="thr-more-${l.index}" aria-label="More settings for ${n}">›</button>
      </div>
      <div class="thr-more" id="thr-more-${l.index}" ${open ? "" : "hidden"}>
        <label><span class="lbl">Stitch style ${Tip.icon("layers_stitch")}</span><select data-k="stitch_type" aria-label="Stitch style for ${n}">${stitchStyles().map(([v, label]) => `<option value="${v}" ${v === l.stitch_type ? "selected" : ""}>${styleLabel(v, label)}</option>`).join("")}</select></label>
        <label><span class="lbl">Direction in degrees (empty = automatic) ${Tip.icon("layers_angle")}</span><input type="number" data-k="angle" placeholder="auto" min="0" max="180" value="${l.angle ?? ""}" aria-label="Stitch direction in degrees for ${n}"></label>
        <div class="row small"><span class="muted">${l.area_mm2} mm² ${Tip.icon("layers_area")}</span>
          <span class="muted">${l.objects} shape${l.objects === 1 ? "" : "s"}${l.customised ? ` (${l.customised} own)` : ""}</span>
          <button type="button" class="ghost small" data-select-layer="${l.index}" ${l.enabled ? "" : "disabled"} aria-label="Select all shapes of ${n}">Select</button></div>
        <div class="row small"><span class="muted">Order ${Tip.icon("layers_order")}</span>
          <button type="button" class="ghost small" data-move="-1" ${i === 0 ? "disabled" : ""} aria-label="Sew ${n} earlier">↑ Earlier</button>
          <button type="button" class="ghost small" data-move="1" ${i === L.length - 1 ? "disabled" : ""} aria-label="Sew ${n} later">↓ Later</button></div>
        <label><span class="lbl">Merge into ${Tip.icon("layers_merge")}</span><select data-merge aria-label="Merge ${n} into another colour"><option value="">–</option>${L.filter((o) => o.index !== l.index).map((o) => `<option value="${o.index}">${esc(o.name)}</option>`).join("")}</select></label>
      </div>
    </div>`;
  }).join("") + (L.filter((l) => l.enabled).length > 1
    ? `<div class="thr-foot"><button type="button" class="ghost small" data-similar aria-haspopup="dialog">Merge similar colours…</button> ${Tip.icon("merge_similar")}</div>` : "");
}

$("layers").addEventListener("change", async (ev) => {
  const item = ev.target.closest("[data-index]");
  if (!item) return;
  const idx = +item.dataset.index;
  if (ev.target.dataset.merge !== undefined) {
    if (!ev.target.value) return;
    return mergeColours(idx, +ev.target.value).catch(() => {});
  }
  const layer = state.design.layers.find((l) => l.index === idx);
  const k = ev.target.dataset.k;
  if (!layer || !k) return;
  layer[k] = ev.target.type === "checkbox" ? ev.target.checked : k === "angle" ? (ev.target.value === "" ? null : +ev.target.value) : ev.target.value;
  await saveLayers();
});
$("layers").addEventListener("click", async (ev) => {
  if (ev.target.closest("[data-similar]")) return Similar.open();
  const toggle = ev.target.closest("[data-expand]");
  if (toggle) {
    const idx = +toggle.closest("[data-index]").dataset.index;
    const open = !openThreads.has(idx);
    if (open) openThreads.add(idx); else openThreads.delete(idx);
    toggle.setAttribute("aria-expanded", open);
    $(`thr-more-${idx}`).hidden = !open;
    return;
  }
  const pick = ev.target.closest("[data-select-layer]");
  if (pick) return Shapes.selectLayer(+pick.dataset.selectLayer);
  const mv = ev.target.closest("[data-move]")?.dataset.move;
  if (!mv) return;
  const L = state.design.layers;
  const i = L.findIndex((l) => l.index === +ev.target.closest("[data-index]").dataset.index);
  const j = i + +mv;
  if (j < 0 || j >= L.length) return;
  [L[i], L[j]] = [L[j], L[i]];
  await saveLayers();
  $("layers").querySelector(`[data-index="${L[j].index}"] [data-move="${mv}"]:not(:disabled)`)?.focus();
});
async function saveLayers() {
  const layers = state.design.layers.map(({ index, hex, name, enabled, stitch_type, angle }) => ({ index, hex, name, enabled, stitch_type, angle }));
  const res = await work("Saving thread colours…", () => api(`/api/projects/${state.project.id}/layers`, json("PUT", { layers }))).catch(() => null);
  if (res) {
    History.designChange("thread colours");
    renderDesign(res);
    Jobs.stitch();
  }
}

// ------------------------------------------------------------------ merging colours (menu or drag & drop)
const layerName = (idx) => state.design.layers.find((l) => l.index === idx)?.name || "colour";

// Every area of `source` takes the `target` thread; both become one region and the
// `source` entry disappears. The stitches follow by themselves.
async function mergeColours(source, target) {
  if (source === target || !state.project) return;
  const from = layerName(source), to = layerName(target);
  const res = await work(`Merging ${from} into ${to}…`, () => api(`/api/projects/${state.project.id}/layers/merge`, json("POST", { source, target })));
  openThreads.delete(source);
  History.designChange(`merge ${from} into ${to}`);
  renderDesign(res);
  Jobs.stitch();
  toast(`${from} merged into ${to}. Use Undo to separate them again.`, true);
}

// Merge similar colours: the server suggests pairs that look alike as thread (CIEDE2000
// below the slider's value, the smaller colour into the larger one); the chosen ones merge
// in one step, so one Undo separates them all again.
const Similar = (() => {
  let seq = 0, timer = 0, pairs = [];
  const dlg = () => $("similarDialog");
  const word = (de) => de < 2 ? "almost identical" : de < 3.5 ? "very close" : de < 6 ? "close" : "similar shade";
  const chip = (idx) => `<i class="sim-chip" style="background:${state.design.layers.find((l) => l.index === idx)?.hex || "#999"}"></i>`;

  function render(res) {
    pairs = res.pairs;
    const box = $("similarList");
    box.innerHTML = pairs.length
      ? pairs.map((p, i) => `<label class="check sim-row"><input type="checkbox" data-i="${i}" checked>
          <span class="sim-pair">${chip(p.source)}<b>${esc(layerName(p.source))}</b> <span aria-hidden="true">→</span><span class="visually-hidden"> into </span> ${chip(p.target)}<b>${esc(layerName(p.target))}</b></span>
          <span class="muted small">${word(p.delta_e)} · ΔE ${p.delta_e.toFixed(1)}</span></label>`).join("")
      : `<p class="small muted">No colours are this alike. Move the slider to the right to include more different shades.</p>`;
    count();
  }

  function count() {
    const n = $("similarList").querySelectorAll("input:checked").length;
    $("similarApply").disabled = !n;
    $("similarApply").textContent = n ? `Merge ${n} colour${n === 1 ? "" : "s"}` : "Merge";
  }

  async function load() {
    const my = ++seq, de = +$("similarDe").value;
    $("similarOut").textContent = `${de.toFixed(1)} – ${word(de)}`;
    $("similarList").setAttribute("aria-busy", "true");
    try {
      const res = await work("Finding similar colours…", () => api(`/api/projects/${state.project.id}/layers/similar?max_de=${de}`), { quiet: true });
      if (my === seq) render(res);
    } catch (e) {
      if (my === seq) { $("similarList").innerHTML = `<p class="small error">${esc(e.message)}</p>`; pairs = []; count(); }
    } finally {
      if (my === seq) $("similarList").removeAttribute("aria-busy");
    }
  }

  function open() {
    if (!state.project || !state.design) return;
    const cfg = state.config.similar || { min: 1, max: 15, default: 5 };
    const r = $("similarDe");
    r.min = cfg.min; r.max = cfg.max;
    if (!r.dataset.set) { r.value = cfg.default; r.dataset.set = "1"; } // the value is kept while the page is open
    $("similarList").innerHTML = "";
    pairs = []; count();
    dlg().showModal();
    load();
  }

  async function apply() {
    const chosen = [...$("similarList").querySelectorAll("input:checked")].map((c) => pairs[+c.dataset.i]).filter(Boolean);
    if (!chosen.length) return;
    dlg().close();
    const res = await work(`Merging ${chosen.length} similar colour${chosen.length === 1 ? "" : "s"}…`, () =>
      api(`/api/projects/${state.project.id}/layers/merge-many`, json("POST", { pairs: chosen.map(({ source, target }) => ({ source, target })) }))).catch(() => null);
    if (!res) return;
    chosen.forEach((p) => openThreads.delete(p.source));
    History.designChange(`merge ${chosen.length} similar colour${chosen.length === 1 ? "" : "s"}`);
    renderDesign(res);
    Jobs.stitch();
    toast(`${chosen.length} similar colour${chosen.length === 1 ? "" : "s"} merged. Use Undo to separate them again.`, true);
  }

  function init() {
    $("similarDe").addEventListener("input", () => { clearTimeout(timer); timer = setTimeout(load, 200); });
    $("similarList").addEventListener("change", count);
    $("similarApply").addEventListener("click", apply);
  }
  return { open, init };
})();

// Drag a colour (handle in the threads list, or a colour chip) and drop it on another one.
// Pointer events, so it works with a mouse, a pen and touch alike.
const ColourDrag = (() => {
  let drag = null;
  let swallowClickUntil = 0;
  const THRESHOLD = 6;
  const swatch = (idx) => state.design.layers.find((l) => l.index === idx)?.hex || "#999";

  function targetAt(x, y) {
    const t = document.elementFromPoint(x, y)?.closest("[data-merge-target]");
    if (!t) return null;
    const idx = +t.dataset.mergeTarget;
    return idx === drag.source ? null : { el: t, idx };
  }

  function start(ev) {
    const h = ev.target.closest("[data-drag-layer]");
    if (!h || !state.design || (ev.pointerType === "mouse" && ev.button !== 0)) return;
    drag = { source: +h.dataset.dragLayer, handle: h, x0: ev.clientX, y0: ev.clientY, on: false, over: null, id: ev.pointerId };
  }

  function begin() {
    drag.on = true;
    const g = document.createElement("div");
    g.className = "drag-ghost";
    g.innerHTML = `<i style="background:${swatch(drag.source)}"></i><span>${esc(layerName(drag.source))}</span>`;
    document.body.append(g);
    drag.ghost = g;
    document.body.classList.add("dragging-colour");
    document.querySelectorAll(`[data-merge-target="${drag.source}"]`).forEach((n) => n.classList.add("drag-src"));
    try { drag.handle.setPointerCapture(drag.id); } catch (_) {}
  }

  function move(ev) {
    if (!drag || ev.pointerId !== drag.id) return;
    if (!drag.on) {
      if (Math.hypot(ev.clientX - drag.x0, ev.clientY - drag.y0) < THRESHOLD) return;
      begin();
    }
    ev.preventDefault();
    drag.ghost.style.transform = `translate(${ev.clientX + 12}px, ${ev.clientY + 12}px)`;
    const t = targetAt(ev.clientX, ev.clientY);
    if (drag.over?.el !== t?.el) {
      document.querySelectorAll(".drop-ok").forEach((n) => n.classList.remove("drop-ok"));
      if (t) document.querySelectorAll(`[data-merge-target="${t.idx}"]`).forEach((n) => n.classList.add("drop-ok"));
    }
    drag.over = t;
    drag.ghost.querySelector("span").innerHTML = t
      ? `Merge <b>${esc(layerName(drag.source))}</b> into <i style="background:${swatch(t.idx)}"></i><b>${esc(layerName(t.idx))}</b>`
      : esc(layerName(drag.source)) + " – drop on another colour";
    drag.ghost.classList.toggle("ok", !!t);
  }

  function end(ev, cancelled = false) {
    if (!drag || ev.pointerId !== drag.id) return;
    const d = drag;
    drag = null;
    if (!d.on) return; // a plain click: chips keep selecting their colour
    d.ghost.remove();
    document.body.classList.remove("dragging-colour");
    document.querySelectorAll(".drop-ok, .drag-src").forEach((n) => n.classList.remove("drop-ok", "drag-src"));
    // the click that follows a drag must not also select / toggle anything
    swallowClickUntil = performance.now() + 400;
    if (!cancelled && d.over) mergeColours(d.source, d.over.idx).catch(() => {});
  }

  function init() {
    window.addEventListener("click", (e) => {
      if (performance.now() < swallowClickUntil) { swallowClickUntil = 0; e.stopPropagation(); e.preventDefault(); }
    }, true);
    document.addEventListener("pointerdown", start);
    document.addEventListener("pointermove", move, { passive: false });
    document.addEventListener("pointerup", (ev) => end(ev));
    document.addEventListener("pointercancel", (ev) => end(ev, true));
    document.addEventListener("keydown", (ev) => { if (ev.key === "Escape" && drag?.on) end({ pointerId: drag.id }, true); });
  }
  return { init };
})();
ColourDrag.init();

$("btnUndo").onclick = () => History.undo().catch(() => {});
$("btnRedo").onclick = () => History.redo().catch(() => {});
document.addEventListener("keydown", (ev) => {
  // Ctrl/Cmd+Z = undo, Ctrl+Y or Ctrl/Cmd+Shift+Z = redo - never while typing in a field
  if (!(ev.ctrlKey || ev.metaKey) || ev.target.matches("input, select, textarea") || !state.project || currentPage !== "editor"
    || document.querySelector("dialog[open]") || document.body.matches(".view-account, .view-admin")) return;
  const k = ev.key.toLowerCase();
  if (k === "z" && !ev.shiftKey) { ev.preventDefault(); History.undo().catch(() => {}); }
  else if (k === "y" || (k === "z" && ev.shiftKey)) { ev.preventDefault(); History.redo().catch(() => {}); }
});

// AI assistant buttons live in ai.js

// ------------------------------------------------------------------ stitch settings
// [parameter name (sent to the server), plain-language label, min, max, step, advanced?]
const PARAMS = [
  ["fill_spacing_mm", "Fill thickness – gap between rows (mm)", 0.25, 1.0, 0.05],
  ["satin_spacing_mm", "Satin thickness – gap between stitches (mm)", 0.25, 1.0, 0.05],
  ["pull_compensation_mm", "Shrink compensation (mm)", 0, 0.8, 0.05],
  ["fill_angle", "Fill direction (°)", 0, 180, 5],
  ["fill_stitch_length_mm", "Fill stitch length (mm)", 2, 7, 0.1, true],
  ["satin_max_width_mm", "Widest satin shape (mm)", 3, 12, 0.5, true],
  ["running_stitch_length_mm", "Outline stitch length (mm)", 1, 5, 0.1, true],
  ["underlay_spacing_mm", "Foundation spacing (mm)", 1.5, 5, 0.1, true],
  ["push_compensation_mm", "Push compensation – shorten ends (mm)", 0, 0.5, 0.05, true],
  ["machine_spm", "Machine speed for the time estimate (stitches/min)", 300, 1500, 50, true],
  ["border_width_mm", "Zigzag / E-stitch border width (mm)", 1, 6, 0.1, true],
  ["border_spacing_mm", "Zigzag / E-stitch border gap (mm)", 0.5, 4, 0.1, true],
  ["stipple_spacing_mm", "Stipple / maze line gap (mm)", 1, 6, 0.1, true],
  ["cross_cell_mm", "Cross stitch size (mm)", 1.5, 6, 0.1, true],
  ["motif_size_mm", "Motif size (mm)", 3, 20, 0.5, true],
  ["pattern_size_mm", "Pattern fill tile size (mm)", 3, 30, 0.5, true],
  ["jagged_mm", "Jagged fill roughness (mm)", 0.3, 5, 0.1, true],
  ["photo_row_mm", "Photo stitch row gap (mm)", 0.6, 3, 0.1, true],
  ["trim_threshold_mm", "Cut thread for moves longer than (mm)", 1, 20, 0.5, true],
  ["min_object_area_mm2", "Skip shapes smaller than (mm²)", 0, 10, 0.05, true],
];
// Named foundation (underlay) types, see stitch/underlay.py. Shared with the shape editor.
const UNDERLAY_LABELS = {
  fill: [
    ["edge_tatami", "Edge run + lattice (standard)"],
    ["edge", "Edge run only (light)"],
    ["double_tatami", "Edge run + double lattice (stretchy or fluffy fabric)"],
    ["none", "None"],
  ],
  satin: [
    ["edge", "Edge run (standard)"],
    ["center", "Centre run (light, narrow columns)"],
    ["zigzag", "Zigzag"],
    ["edge_zigzag", "Edge run + zigzag (stretchy fabric)"],
    ["double_zigzag", "Double zigzag (fluffy fabric, raised look)"],
    ["auto", "Automatic by width (centre / edge / edge + zigzag)"],
    ["none", "None"],
  ],
};
const UNDERLAY_FIELDS = [["fill_underlay", "fill", "Foundation under fills"], ["satin_underlay", "satin", "Foundation under satin"]];
// tatami textures (models.FILL_PATTERNS)
UNDERLAY_LABELS.pattern = [["standard", "Standard (brick)"], ["brick", "Half-step brick"], ["diagonal", "Diagonal twill lines"], ["random", "Random (no lines)"]];
UNDERLAY_FIELDS.push(["fill_pattern", "pattern", "Fill texture"]);
UNDERLAY_LABELS.tile = [["diamonds", "Diamonds"], ["squares", "Squares"], ["bricks", "Bricks"], ["scales", "Scales"]];
UNDERLAY_FIELDS.push(["pattern", "tile", "Pattern fill pattern"]);
UNDERLAY_FIELDS.push(["motif", "motif", "Motif"]);
UNDERLAY_LABELS.photo = [["rows", "Rows, one thread (Photo Flash)"], ["reef", "Reef: meander, one thread"], ["colour", "Rows in every colour"]];
UNDERLAY_FIELDS.push(["photo_style", "photo", "Photo stitch style"]);
const CHECKS = [
  ["optimize", "Smart stitch reduction"],
  ["underlay", "Foundation stitches (recommended)"],
  ["vary_fill_angle", "Change fill direction for each colour"],
  ["smart_direction", "Smart direction – best angle for each section"],
  ["tie_stitches", "Lock stitches at every thread cut"],
  ["optimize_order", "Smart sewing order (fewer, shorter jumps)"],
  ["knockdown", "Knockdown under small details (towels, fleece)"],
  ["trapunto", "Trapunto: fills travel along their edges only"],
  ["motif_warp", "3D warp for motif fills"],
  ["photo_stitch", "Photo stitch: the whole picture as a photo texture"],
  ["triple_run", "Bold outlines (sew lines 3 times)"],
];
// Settings that only matter for some stitch styles (see stitch/outlines.py, fills.py,
// decorative.py, guided.py): shown under "Style settings" when the design uses one of
// those styles ("@photo": photo stitch is on). "Show the settings of every stitch style"
// shows them all.
const STYLE_PARAMS = {
  border_width_mm: ["zigzag", "estitch"], border_spacing_mm: ["zigzag", "estitch"],
  stipple_spacing_mm: ["stipple_fill", "maze_fill"], cross_cell_mm: ["cross_stitch"],
  motif: ["motif_fill", "paired_motif_fill", "motif_run", "motif_column"],
  motif_size_mm: ["motif_fill", "paired_motif_fill", "motif_run"], motif_warp: ["motif_fill", "paired_motif_fill"],
  pattern: ["pattern_fill", "flexi_split"], pattern_size_mm: ["pattern_fill", "flexi_split"],
  jagged_mm: ["jagged_fill"], photo_style: ["@photo"], photo_row_mm: ["@photo"],
};
const isStyleParam = (k) => k in STYLE_PARAMS;
let defaultStitch = null; // the stitch settings as the form was built (= the app's defaults)

function buildStitchForm() {
  const d = state.config.stitch_defaults;
  UNDERLAY_LABELS.motif = state.config.motifs || [["wave", "Wave"]];
  const styled = (k) => (isStyleParam(k) ? ` data-styles="${STYLE_PARAMS[k].join(" ")}"` : "");
  const checkbox = ([k, label]) => {
    const on = k === "triple_run" ? d.running_repeats >= 3 : !!d[k];
    return `<label class="check"${styled(k)}><input type="checkbox" name="${k}" ${on ? "checked" : ""}> ${label} ${Tip.icon(k)}</label>`;
  };
  const tw = d.thread_weight === 60 ? 60 : 40;
  const fabrics = state.config.fabrics || [];
  const fabricField = fabrics.length
    ? `<label class="full"><span class="lbl">Fabric ${Tip.icon("fabric")}</span><select name="fabric">` +
      fabrics.map((fb) => `<option value="${fb.id}" ${fb.id === d.fabric ? "selected" : ""}>${fb.label}</option>`).join("") +
      `</select><span class="muted small hint" id="fabricNote"></span></label>`
    : "";
  const charts = state.config.thread_charts || [];
  const chartField = charts.length // brand charts the server has (THREAD_CHARTS_DIR); none bundled
    ? `<label class="full"><span class="lbl">Thread brand ${Tip.icon("thread_chart")}</span><select name="thread_chart">` +
      `<option value="">None – colours only</option>` +
      charts.map((c) => `<option value="${esc(c.id)}" ${c.id === d.thread_chart ? "selected" : ""}>${esc(c.name)} (${c.count} colours)</option>`).join("") +
      `</select></label>`
    : "";
  const field = ([k, label, min, max, step]) =>
    `<label${styled(k)}><span class="lbl">${label} ${Tip.icon(k)}</span><input type="number" name="${k}" min="${min}" max="${max}" step="${step}" value="${d[k]}" inputmode="decimal"></label>`;
  const select = ([k, kind, label]) =>
    `<label${styled(k)}><span class="lbl">${label} ${Tip.icon(k)}</span><select name="${k}">` +
    UNDERLAY_LABELS[kind].map(([v, l]) => `<option value="${v}" ${v === d[k] ? "selected" : ""}>${l}</option>`).join("") +
    `</select></label>`;
  $("stitchForm").innerHTML =
    fabricField +
    CHECKS.slice(0, 1).map(checkbox).join("") +
    `<label class="full"><span class="lbl">Thread ${Tip.icon("thread_weight")}</span><select name="thread_weight">` +
    `<option value="40" ${tw === 40 ? "selected" : ""}>Standard (40 wt)</option>` +
    `<option value="60" ${tw === 60 ? "selected" : ""}>Fine, for small lettering (60 wt)</option></select></label>` +
    chartField +
    PARAMS.filter((p) => !p[5]).map(field).join("") +
    CHECKS.slice(1, 4).map(checkbox).join("") +
    `<div class="style-params full" id="styleParams" hidden><p class="style-title">Style settings ${Tip.icon("style_settings")}</p>` +
    UNDERLAY_FIELDS.filter(([k]) => isStyleParam(k)).map(select).join("") +
    PARAMS.filter((p) => isStyleParam(p[0])).map(field).join("") +
    CHECKS.filter(([k]) => isStyleParam(k)).map(checkbox).join("") +
    `</div>` +
    `<details class="group full"><summary>Advanced stitch settings</summary><div class="params">` +
    UNDERLAY_FIELDS.filter(([k]) => !isStyleParam(k)).map(select).join("") +
    PARAMS.filter((p) => p[5] && !isStyleParam(p[0])).map(field).join("") +
    CHECKS.slice(4).filter(([k]) => !isStyleParam(k)).map(checkbox).join("") +
    `<label class="check"><input type="checkbox" id="styleShowAll"> Show the settings of every stitch style</label>` +
    `</div></details>`;
  const sel = $("stitchForm").elements.fabric;
  if (sel) {
    sel.onchange = () => applyFabric(sel.value);
    showFabricNote(sel.value);
  }
  defaultStitch = stitchValues();
  addResetButtons($("stitchForm"));
  // a changed setting is recorded for undo and updates the stitches by itself (or marks
  // them out of date, see Jobs); "show every style" only changes what is listed
  $("stitchForm").addEventListener("change", (ev) => {
    updateStyleParams();
    if (!ev.target.name) return;
    History.stitchChange();
    if (state.design) Jobs.stitch();
  });
  $("stitchForm").addEventListener("submit", (ev) => ev.preventDefault());
}

// which special stitch styles the design uses (colours and single shapes that are sewn)
function usedStyles() {
  const used = new Set();
  const layers = state.design?.layers || [];
  const on = new Set(layers.filter((l) => l.enabled).map((l) => l.index));
  layers.forEach((l) => l.enabled && used.add(l.stitch_type));
  Shapes.objects().forEach((o) => { if (on.has(o.layer) && o.enabled !== false && o.stitch_type) used.add(o.stitch_type); });
  if ($("stitchForm").elements.photo_stitch?.checked) used.add("@photo");
  return used;
}
function updateStyleParams() {
  const box = $("styleParams");
  if (!box) return;
  const all = $("styleShowAll")?.checked, used = usedStyles();
  let shown = 0;
  box.querySelectorAll("[data-styles]").forEach((l) => {
    const on = all || l.dataset.styles.split(" ").some((s) => used.has(s));
    l.hidden = !on;
    if (on) shown++;
  });
  box.hidden = !shown;
}

// ------------------------------------------------------------------ settings: defaults, changed markers, reset
// The baseline is what the design started with: the app's stitch defaults, and the picture
// settings as set after the upload (including the smart analysis). A setting that differs
// is marked; it can be reset on its own, per section or all at once.
const EXTRACT_IDS = ["width_mm", "num_colors", "background", "text_mode", "brightness", "contrast", "sharpen", "denoise", "min_feature_mm", "min_area_mm2", "knockout"];
const valueOf = (el) => (el.type === "checkbox" ? el.checked : el.value);
function setValue(el, v) { if (el.type === "checkbox") el.checked = !!v; else el.value = v; }
function extractValues() { const v = {}; EXTRACT_IDS.forEach((id) => (v[id] = valueOf($(id)))); return v; }
function stitchValues() {
  const v = {};
  [...$("stitchForm").elements].forEach((e) => { if (e.name && e.matches("input, select")) v[e.name] = valueOf(e); });
  return v;
}
function setExtractValues(v) {
  Object.entries(v || {}).forEach(([k, val]) => { if ($(k)) setValue($(k), val); });
  showAdjustValues();
  markModified();
}
function setStitchValues(v) {
  const f = $("stitchForm");
  Object.entries(v || {}).forEach(([k, val]) => { if (f.elements[k]) setValue(f.elements[k], val); });
  showFabricNote(f.elements.fabric?.value);
  updateStyleParams();
  markModified();
}
const changedKeys = (a, b) => [...new Set([...Object.keys(a || {}), ...Object.keys(b || {})])].filter((k) => String(a?.[k]) !== String(b?.[k]));

/** The settings a fresh design starts from (after the upload and its smart analysis). */
function setBaseline(saved = null) {
  const tw = state.smart?.stitch?.thread_weight;
  state.baseline = saved || { extract: extractValues(), stitch: { ...defaultStitch, ...(tw ? { thread_weight: String(tw) } : {}) } };
  markModified();
}

// plain text of a setting's label (without help, value and reset buttons)
function labelText(label) {
  const t = label.cloneNode(true);
  t.querySelectorAll(".help, output, input, select, textarea, .hint, .reset-one, .sec-reset").forEach((n) => n.remove());
  return t.textContent.replace(/\s+/g, " ").trim();
}
const settingKey = (el) => (el.closest("#stitchForm") ? el.name : EXTRACT_IDS.includes(el.id) ? el.id : "");
const settingEl = (key) => (EXTRACT_IDS.includes(key) ? $(key) : $("stitchForm").elements[key]);
const settingName = (key) => { const l = settingEl(key)?.closest("label"); return l ? labelText(l) : key; };

function addResetButtons(root) {
  root.querySelectorAll("input, select").forEach((el) => {
    const key = settingKey(el), label = el.closest("label");
    if (!key || !label || label.querySelector(".reset-one")) return;
    const b = document.createElement("button");
    b.type = "button";
    b.className = "reset-one";
    b.dataset.reset = key;
    b.title = "Back to the starting value";
    b.setAttribute("aria-label", `Reset ${labelText(label)}`);
    b.textContent = "↺";
    (label.querySelector(".lbl") || label).append(b);
  });
  root.querySelectorAll?.(".ed-sec[data-sec] > summary").forEach((s) => {
    if (s.querySelector(".sec-reset")) return;
    s.insertAdjacentHTML("beforeend", `<button type="button" class="ghost small sec-reset" aria-label="Reset the ${esc(s.textContent.trim())} settings">Reset</button>`);
  });
}

function markModified() {
  const b = state.baseline;
  if (!b || !defaultStitch) return;
  let n = 0;
  const mark = (el, mod) => {
    el.closest("label")?.classList.toggle("mod", mod);
    if (mod) n++;
  };
  EXTRACT_IDS.forEach((id) => {
    const el = $(id);
    mark(el, !el.closest(".hidden") && String(valueOf(el)) !== String(b.extract[id]));
  });
  [...$("stitchForm").elements].forEach((e) => { if (e.name && e.name in b.stitch) mark(e, String(valueOf(e)) !== String(b.stitch[e.name])); });
  document.querySelectorAll(".ed-sec[data-sec]").forEach((s) => s.classList.toggle("mod", !!s.querySelector("label.mod")));
  $("modSummary").classList.toggle("hidden", !n);
  $("modCount").textContent = `${n} setting${n === 1 ? "" : "s"} changed`;
  Presets.sync();
}

/** Back to the baseline: ``keys`` (one setting or a section) or everything (null). */
function resetSettings(keys) {
  const b = state.baseline;
  if (!b) return;
  const want = (k) => !keys || keys.includes(k);
  let ex = false, st = false;
  EXTRACT_IDS.forEach((id) => {
    const el = $(id);
    if (want(id) && !el.closest(".hidden") && String(valueOf(el)) !== String(b.extract[id])) { setValue(el, b.extract[id]); ex = true; }
  });
  const f = $("stitchForm");
  Object.entries(b.stitch).forEach(([k, v]) => {
    const el = f.elements[k];
    if (want(k) && el && String(valueOf(el)) !== String(v)) { setValue(el, v); st = true; }
  });
  if (!ex && !st) return;
  showAdjustValues();
  showFabricNote(f.elements.fabric?.value);
  updateStyleParams();
  markModified();
  const what = keys?.length === 1 ? settingName(keys[0]) : keys ? "these settings" : "all settings";
  if (st) {
    History.stitchChange(`reset ${what}`);
    if (state.design) Jobs.stitch();
  }
  if (ex && state.project) Jobs.extract();
  announce(`Reset ${what}.`);
}
const keysIn = (root) => [...root.querySelectorAll("input, select")].map(settingKey).filter(Boolean);

function bindSettingsMarkers() {
  addResetButtons($("leftPanel"));
  ["input", "change"].forEach((t) => $("leftPanel").addEventListener(t, markModified));
  // capture: the reset buttons sit inside labels and summaries - they must not also tick the
  // box or open / close the section
  document.addEventListener("click", (ev) => {
    const b = ev.target.closest?.(".reset-one, .sec-reset, #btnResetAll");
    if (!b) return;
    ev.preventDefault();
    ev.stopPropagation();
    if (b.matches(".reset-one")) resetSettings([b.dataset.reset]);
    else if (b.matches(".sec-reset")) resetSettings(keysIn(b.closest(".ed-sec")));
    else resetSettings(null);
  }, true);
}
// A fabric preset fills in the stitch settings it is about; every value stays editable.
function applyFabric(id) {
  const fb = (state.config.fabrics || []).find((x) => x.id === id);
  if (!fb) return;
  const f = $("stitchForm");
  Object.entries(fb.settings).forEach(([k, v]) => {
    const el = f.elements[k];
    if (!el) return;
    if (el.type === "checkbox") el.checked = !!v;
    else el.value = v;
  });
  showFabricNote(id);
}
function showFabricNote(id) {
  const fb = (state.config.fabrics || []).find((x) => x.id === id);
  const note = $("fabricNote");
  if (!note) return;
  note.textContent = fb && fb.id !== "general"
    ? `Backing: ${fb.backing}. Needle: ${fb.needle}.${fb.notes ? " " + fb.notes : ""}`
    : "";
}
function stitchParams() {
  const f = $("stitchForm"), p = {};
  PARAMS.forEach(([k]) => (p[k] = +f.elements[k].value));
  CHECKS.forEach(([k]) => (p[k] = f.elements[k].checked));
  p.thread_weight = +f.elements.thread_weight.value;
  if (f.elements.fabric) p.fabric = f.elements.fabric.value;
  if (f.elements.thread_chart) p.thread_chart = f.elements.thread_chart.value; // only when the server has charts
  UNDERLAY_FIELDS.forEach(([k]) => (p[k] = f.elements[k].value));
  p.running_repeats = p.triple_run ? 3 : 1;
  delete p.triple_run;
  return p;
}

// ------------------------------------------------------------------ keeping the preview up to date
// Changes to the design or the settings are collected for a moment and then sent as one
// request; while it runs, newer changes wait and are sent once it is done with the latest
// values ("latest wins"). Preparing the design (extract) goes first; the stitches follow.
// A design that takes long to stitch switches to manual updates ("Update" in the bottom bar).
const SLOW_STITCH_MS = 3000;
const Jobs = (() => {
  let timer = null, wantExtract = false, wantStitch = false, loop = null, waiters = [];
  let stale = false, error = "", report = null, slow = false;
  let auto = true;
  try { auto = localStorage.getItem("autoUpdate") !== "0"; } catch (_) {}

  const autoOn = () => auto && !slow;
  const pending = () => !!(timer || loop || wantExtract || wantStitch);

  function settle() {
    if (pending()) return;
    const w = waiters;
    waiters = [];
    w.forEach((f) => f());
  }
  function idle() {
    return pending() ? new Promise((res) => waiters.push(res)) : Promise.resolve();
  }
  function kick(delay) {
    clearTimeout(timer);
    timer = setTimeout(start, delay);
    renderStatus();
    return idle();
  }

  /** Prepare the design again (extract) with the current settings; the stitches follow.
   *  ``step``: {label, stitch: {before, after}} makes the undo step also cover stitch
   *  settings changed together with the picture (a preset). */
  let nextStep = null;
  function extract({ delay = 250, step = null } = {}) {
    if (!state.project) return Promise.resolve();
    wantExtract = true;
    if (step) nextStep = step;
    return kick(delay);
  }
  /** Create the stitches again. ``report``: tell how the stitch count changed (shape edits).
   *  ``force``: also in manual mode (the Update button, export, the AI assistant). */
  function stitch({ delay = 350, report: rep = false, force = false } = {}) {
    if (!state.design) return Promise.resolve();
    if (rep && !report && state.preview) report = state.preview.stats;
    if (!force && !autoOn() && state.preview) {
      stale = true;
      renderStatus();
      return idle();
    }
    wantStitch = true;
    return kick(delay);
  }

  function start() {
    timer = null;
    if (loop) return;
    loop = (async () => {
      while (wantExtract || wantStitch) {
        if (wantExtract) {
          wantExtract = false;
          try {
            const pid = state.project.id, had = !!state.design, used = extractValues(), step = nextStep;
            nextStep = null;
            const res = await work("Preparing your design…", () => api(`/api/projects/${pid}/extract`, json("POST", { options: extractOptions() })));
            if (state.project?.id !== pid) continue;
            History.extracted(used, had, step);
            renderDesign(res);
            error = "";
            if (autoOn() || !state.preview) wantStitch = true;
            else stale = true;
          } catch (e) {
            error = e.message;
            wantStitch = false;
          }
          continue;
        }
        wantStitch = false;
        if (!state.design) continue;
        const pid = state.project.id, t0 = performance.now();
        try {
          const prev = await work("Creating stitches…", () => api(`/api/projects/${pid}/stitch`, json("POST", { params: stitchParams() })));
          if (state.project?.id !== pid) continue;
          stale = false;
          error = "";
          applyPreview(prev);
          if (performance.now() - t0 > SLOW_STITCH_MS && auto && !slow) {
            slow = true;
            toast("This design takes a few seconds to stitch, so the preview now updates when you press Update.", true);
          }
          if (report) reportChange(report, prev.stats);
        } catch (e) {
          error = e.message;
          stale = !!state.preview;
        }
        report = null;
      }
    })().finally(() => {
      loop = null;
      renderStatus();
      settle();
    });
  }

  function reportChange(before, after) {
    if (before.stitches === after.stitches) return;
    const d = after.stitches - before.stitches, m = Math.round((after.est_minutes - before.est_minutes) * 10) / 10;
    toast(`Stitches: ${before.stitches.toLocaleString()} → ${after.stitches.toLocaleString()} (${d > 0 ? "+" : ""}${d.toLocaleString()})` +
      (m ? ` · about ${Math.abs(m)} min ${m > 0 ? "longer" : "shorter"}` : ""), true);
  }

  /** Run what is waiting now (no delay) and resolve once everything is done. */
  function flush() {
    if (timer) { clearTimeout(timer); start(); }
    return idle();
  }
  function reset() {
    clearTimeout(timer);
    timer = null;
    wantExtract = wantStitch = false;
    stale = false; error = ""; report = null; slow = false;
    renderStatus();
    settle();
  }
  function setAuto(on) {
    auto = on;
    try { localStorage.setItem("autoUpdate", on ? "1" : "0"); } catch (_) {}
    if (on) slow = false;
    if (on && stale) stitch({ delay: 0 });
    renderStatus();
  }
  return {
    extract, stitch, flush, idle, reset, setAuto, pending,
    stale: () => stale, error: () => error, auto: () => autoOn(), working: () => !!loop,
  };
})();

// ------------------------------------------------------------------ undo / redo: design and settings together
// One timeline in the order things happened. Design changes are kept on the server (its
// undo stack, up to 50 steps); stitch settings are snapshots kept here. Preparing the design
// again (Size, Colours, Picture) is a design change that also remembers those settings, so
// undoing it puts both back. Ctrl+Z after a density change undoes the density change.
const History = (() => {
  let undo = [], redo = [];
  let lastStitch = null, lastExtract = null; // the settings at the last recorded step
  let serverUndo = false, serverRedo = false;
  const MAX = 100, MERGE_MS = 1500;

  function sync() {
    const u = undo[undo.length - 1], r = redo[redo.length - 1];
    $("btnUndo").disabled = !(undo.length || serverUndo);
    $("btnRedo").disabled = !redo.length;
    $("btnUndo").title = `Undo${u ? `: ${u.label}` : ""} (Ctrl+Z)`;
    $("btnRedo").title = `Redo${r ? `: ${r.label}` : ""} (Ctrl+Y)`;
  }
  function push(entry) {
    undo.push(entry);
    if (undo.length > MAX) undo.shift();
    redo = [];
    sync();
  }
  /** A fresh design (or one opened again): nothing to undo in this tab yet. */
  function reset(saved = null) {
    undo = saved?.undo || [];
    redo = saved?.redo || [];
    lastStitch = saved?.lastStitch || stitchValues();
    lastExtract = saved?.lastExtract || extractValues();
    sync();
  }
  function setServer(canUndo, canRedo) { serverUndo = !!canUndo; serverRedo = !!canRedo; sync(); }
  /** A design change saved on the server (shapes, threads, merge). */
  function designChange(label) { push({ type: "design", label: label || "design change" }); }
  /** The design was prepared again with these settings; ``had``: a design existed before
   *  (only then the server keeps a step to go back to). */
  function extracted(used, had, step = null) {
    const st = step?.stitch && changedKeys(step.stitch.before, step.stitch.after).length ? step.stitch : null;
    if (had) {
      const keys = changedKeys(lastExtract, used);
      const label = step?.label || (keys.length ? keys.slice(0, 2).map(settingName).join(", ") : "prepare design");
      push({ type: "design", label, ext: { before: lastExtract, after: used }, ...(st ? { st } : {}) });
    } else if (st) {
      push({ type: "settings", label: step.label, before: st.before, after: st.after, t: 0 });
    }
    if (st) lastStitch = st.after;
    lastExtract = used;
  }
  /** The stitch settings changed (a field, a fabric, a reset). Quick repeats of the same
   *  setting (number arrows) become one step. */
  function stitchChange(label = "") {
    const now = stitchValues();
    const keys = changedKeys(lastStitch, now);
    if (!keys.length) return;
    const top = undo[undo.length - 1];
    if (top?.type === "settings" && Date.now() - top.t < MERGE_MS && !redo.length
      && keys.every((k) => changedKeys(top.before, top.after).includes(k))) {
      top.after = now;
      top.t = Date.now();
    } else {
      push({ type: "settings", label: label || keys.slice(0, 2).map(settingName).join(", "), before: lastStitch, after: now, t: Date.now() });
    }
    lastStitch = now;
    sync();
  }

  async function step(back) {
    if (!state.project) return;
    await Jobs.flush(); // changes still waiting are recorded first
    const from = back ? undo : redo, to = back ? redo : undo;
    for (;;) {
      const server = back ? serverUndo : serverRedo;
      let e = from.pop();
      if (!e) {
        if (!back || !server) break;
        e = { type: "design", label: "design change" }; // a step from before this tab (reload)
      }
      if (e.type === "settings") {
        const v = back ? e.before : e.after;
        setStitchValues(v);
        lastStitch = v;
        to.push(e);
        sync();
        announce(`${back ? "Undone" : "Redone"}: ${e.label}`);
        Jobs.stitch();
        return;
      }
      if (!server) continue; // the server no longer has this step: skip it
      const res = await work(back ? "Undoing…" : "Redoing…", () => api(`/api/projects/${state.project.id}/${back ? "undo" : "redo"}`, { method: "POST" })).catch(() => null);
      if (!res) { from.push(e); sync(); return; }
      if (e.ext) {
        const v = back ? e.ext.before : e.ext.after;
        setExtractValues(v);
        lastExtract = v;
      }
      if (e.st) { // stitch settings changed in the same step (a preset)
        const v = back ? e.st.before : e.st.after;
        setStitchValues(v);
        lastStitch = v;
      }
      to.push(e);
      renderDesign(res);
      announce(`${back ? "Undone" : "Redone"}: ${e.label}`);
      Jobs.stitch();
      return;
    }
    sync();
  }

  const save = () => ({ undo: undo.slice(-30), redo: redo.slice(-30), lastStitch, lastExtract });
  return { reset, setServer, designChange, extracted, stitchChange, undo: () => step(true), redo: () => step(false), save };
})();

// The bottom bar's status: what the preview shows right now.
function renderStatus() {
  let level = "", text = "";
  if (working.length) { level = "busy"; text = working[working.length - 1]; }
  else if (Jobs.pending()) { level = "busy"; text = "Updating…"; }
  else if (Jobs.error()) { level = "error"; text = `Not updated: ${Jobs.error()}`; }
  else if (Jobs.stale()) { level = "stale"; text = "Settings changed"; }
  else if (state.preview) { level = "ok"; text = "Up to date"; }
  $("edDot").dataset.level = level;
  $("edStatusText").textContent = text;
  $("edStatusText").title = text;
  $("btnUpdate").hidden = !(level === "stale" || level === "error") || !state.design;
  $("btnUpdate").lastChild.textContent = level === "error" ? "Try again" : "Update";
  $("autoUpdate").checked = Jobs.auto();
  $("edProgress").hidden = level !== "busy";
  $("stage").classList.toggle("updating", level === "busy");
  const s = state.preview?.stats;
  $("edFacts").textContent = s ? `${s.width_mm} × ${s.height_mm} mm · ${s.stitches.toLocaleString()} stitches` : "";
  $("btnExport").disabled = !state.design;
  renderStageEmpty(level);
  if ($("exportDialog").open) Export.refresh();
}
function renderStageEmpty(level) {
  const shapes = state.pane === "shapes";
  const empty = shapes ? !state.design : !state.preview;
  const failed = level === "error";
  $("stageEmpty").classList.toggle("hidden", !empty || !state.project);
  $("stageEmpty").querySelector(".spinner").classList.toggle("hidden", failed);
  $("stageEmptyText").textContent = failed ? Jobs.error()
    : shapes || !state.design ? "Preparing your design…" : "Creating stitches…";
}

// ------------------------------------------------------------------ stitch preview data
function applyPreview(prev) {
  state.preview = prev;
  Render.reset();
  Shapes.setStitchShapes(prev.shapes);
  Shapes.setObjects(prev.objects); // stitch style and direction as they were really sewn
  updateStyleParams();
  const canCompare = !!(prev.comparison && !prev.comparison.reverted && (prev.original_ops || prev.original_ref));
  $("variant").classList.toggle("hidden", !canCompare);
  $("variantHelp")?.classList.toggle("hidden", !canCompare);
  setVariant(canCompare ? state.variant : "optimized", false);
  if (state.playing) togglePlay();
  resetScrub();
  renderOptimizeInfo();
  renderStats();
  AI.onStitched();
  Shapes.refresh();
  if (state.autoPane && state.pane === "shapes") setPane("stitches");
  else if (state.pane === "stitches") { if (state.viewMoved) draw(); else fit(); }
}

/** Create the stitches again now (also in manual mode) and wait for them (AI assistant). */
const generate = () => Jobs.stitch({ force: true, delay: 0 });

function resetScrub() {
  state.upto = activeOps().length / 4;
  $("scrub").max = state.upto;
  $("scrub").value = state.upto;
}

// The stitches before smart reduction are loaded only when the user compares (they would
// double every preview); the ref ties them to the stitches on screen.
async function loadOriginal(prev) {
  if (prev.original_ops) return true;
  const btn = document.querySelector('#variant [data-variant="original"]');
  btn?.setAttribute("aria-busy", "true");
  try {
    const r = await api(`/api/projects/${state.project.id}/stitch/original?ref=${encodeURIComponent(prev.original_ref)}`);
    prev.original_ops = r.ops;
    return true;
  } catch (e) {
    if (e.status !== 404 && state.preview === prev) toast(`The original stitches could not be loaded: ${e.message}`);
    return false; // 404: newer stitches are on their way
  } finally {
    btn?.removeAttribute("aria-busy");
  }
}

function setVariant(v, redraw = true) {
  const prev = state.preview;
  if (v === "original" && prev && !prev.original_ops && prev.original_ref) {
    loadOriginal(prev).then((ok) => { if (ok && state.preview === prev) setVariant("original"); });
    v = "optimized"; // until they are here
  }
  state.variant = v;
  document.querySelectorAll("#variant button").forEach((b) => {
    const on = b.dataset.variant === v;
    b.classList.toggle("on", on);
    b.setAttribute("aria-pressed", on);
  });
  if (!redraw) return;
  resetScrub();
  renderStats();
  draw();
}

function renderOptimizeInfo() {
  const c = state.preview.comparison, box = $("optimizeInfo");
  if (!c) { box.className = "optinfo hidden"; return; }
  if (c.reverted) {
    box.className = "optinfo warn";
    box.innerHTML = "<b>Smart stitch reduction was skipped</b> – it would have changed how your design looks, so the original stitches are used.";
    return;
  }
  box.className = "optinfo";
  if (c.saved_stitches <= 0) {
    box.innerHTML = "<b>Smart stitch reduction:</b> no unnecessary stitches found in this design – nothing to remove.";
    return;
  }
  const parts = [];
  if (c.micro_removed) parts.push(`${c.micro_removed.toLocaleString()} stacked stitches`);
  if (c.underlay_saved) parts.push(`${c.underlay_saved.toLocaleString()} duplicate foundation stitches`);
  const unchanged = Math.max(0, 100 - c.coverage_change_pct);
  box.innerHTML =
    `<b>Smart stitch reduction saved ${c.saved_stitches.toLocaleString()} stitches (${c.saved_pct}%)</b>` +
    `${c.saved_minutes > 0 ? ` · about ${c.saved_minutes} min faster` : ""}<br>` +
    `<span class="small">${c.original.stitches.toLocaleString()} → ${c.optimized.stitches.toLocaleString()} stitches` +
    `${parts.length ? ` · removed ${parts.join(" and ")}` : ""}. ` +
    `Shape check passed: no gaps – ${unchanged.toFixed(1)}% of the visible stitching is covered exactly as before. ` +
    `Use <i>Optimised</i> / <i>Original</i> in the bottom bar to compare.</span>`;
}

// Design info: how the needle stitches split by type (exact counts from the ops' kind codes).
const KIND_LABELS = { fill: "Fill", satin: "Satin", running: "Running (outlines, details)", underlay: "Foundation (underlay)",
  travel: "Travel (hidden moves)", tie: "Lock stitches" };
function renderKinds() {
  const ops = activeOps(), kinds = state.preview.kinds || [];
  const n = new Array(kinds.length).fill(0);
  let total = 0;
  for (let i = 0; i < ops.length; i += 4) if (ops[i] === 0) { n[ops[i + 3]] = (n[ops[i + 3]] || 0) + 1; total++; }
  const rows = kinds.map((k, i) => [k, n[i] || 0]).filter(([, c]) => c > 0).sort((a, b) => b[1] - a[1]);
  $("kindTitle").classList.toggle("hidden", !total);
  $("kindStats").innerHTML = rows.map(([k, c]) => {
    const pct = (100 * c) / total;
    return `<div class="k-row"><span>${esc(KIND_LABELS[k] || "Other")}</span><b>${c.toLocaleString()}</b><span class="pct">${pct < 1 ? "<1" : Math.round(pct)}%</span>
      <span class="k-bar" aria-hidden="true"><span style="width:${pct.toFixed(1)}%"></span></span></div>`;
  }).join("");
}

const shownStats = () => {
  const c = state.preview.comparison;
  return c && !c.reverted ? (state.variant === "original" ? c.original : c.optimized) : state.preview.stats;
};
// same words as the worksheet (threads.closeness)
const threadCloseness = (de) => (de < 2 ? "same colour" : de < 3.5 ? "very close" : de < 6 ? "close" : "nearest available");
function renderStats() {
  const s = shownStats();
  const items = [
    [s.stitches.toLocaleString(), "stitches", "stats_stitches"], [`${s.width_mm} × ${s.height_mm}`, "size (mm)"],
    [s.colors, "colours"], [s.trims, "thread cuts", "stats_trims"], [s.color_changes, "colour changes"],
    [`${s.est_minutes} min`, "sewing time (est.)", "stats_time"], [`${s.thread_m} m`, "thread used (est.)", "stats_thread"],
  ];
  $("statsEmpty").classList.add("hidden");
  $("stats").innerHTML = items.map(([v, l, k]) => `<div><b>${v}</b><span>${l}${k ? " " + Tip.icon(k) : ""}</span></div>`).join("");
  renderKinds();
  const warnings = [...state.preview.warnings];
  const hf = hoopFit(s.width_mm, s.height_mm);
  if (hf && !hf.fits && !hf.turned) warnings.push(`The design (${s.width_mm} × ${s.height_mm} mm) is larger than the ${hf.hoop.name} hoop. Choose a smaller width or a bigger hoop.`);
  $("warnings3").innerHTML = warnings.map((w) => `<div>${esc(w)}</div>`).join("");
  $("sequenceTitle").classList.toggle("hidden", !state.preview.colors.length);
  $("sequence").innerHTML = state.preview.colors.map((c, i) => `<div><i style="background:${c.hex}"></i>${i + 1}. ${esc(c.name)} <span class="muted">${c.hex}</span>` +
    (c.thread ? `<span class="seq-thread"><i style="background:${c.thread.hex}" aria-hidden="true"></i>${esc(c.thread.brand)} <b>${esc(c.thread.number)}</b> ${esc(c.thread.name)} <span class="muted">· ${threadCloseness(c.thread.delta_e)}</span></span>` : "") +
    `</div>`).join("");
  renderStatus();
}

// ------------------------------------------------------------------ the editor: panes, sheets, bottom bar
const paneButtons = () => document.querySelectorAll(".ed-views [data-pane]");
function setPane(pane, { user = false } = {}) {
  if (user) state.autoPane = false;
  state.pane = pane;
  $("editor").dataset.view = pane;
  paneButtons().forEach((b) => b.setAttribute("aria-pressed", b.dataset.pane === pane));
  if (pane === "stitches") requestAnimationFrame(() => { if (!state.preview) return; if (state.viewMoved) draw(); else fit(); });
  if (pane === "shapes" && state.playing) togglePlay();
  Shapes.refresh();
  updateZoomLabel();
  renderStatus();
  saveSession();
  viewMoved();
}
/** The shape tools (edit outline, draw direction / guide line) work on the shapes view. */
function ensureShapesPane() {
  if (state.pane !== "shapes") setPane("shapes", { user: true });
}

// Side panels as sheets (smaller screens): one open at a time, Esc or the backdrop closes it.
let sheetReturn = null;
function openSheet(side) {
  closeSheets(false);
  document.body.classList.add(`sheet-${side}`);
  $("sheetBackdrop").hidden = false;
  const btn = side === "left" ? $("barSettings") : $("barInfo");
  btn.setAttribute("aria-expanded", "true");
  sheetReturn = btn;
  const panel = side === "left" ? $("leftPanel") : $("rightPanel");
  requestAnimationFrame(() => panel.querySelector("[data-close-sheet]")?.focus());
}
function closeSheets(restore = true) {
  const was = document.body.matches(".sheet-left, .sheet-right");
  document.body.classList.remove("sheet-left", "sheet-right");
  $("sheetBackdrop").hidden = true;
  $("barSettings").setAttribute("aria-expanded", "false");
  $("barInfo").setAttribute("aria-expanded", "false");
  if (was && restore) sheetReturn?.focus();
}
const sheetOpen = (side) => document.body.classList.contains(`sheet-${side}`);

function updateSelCount(n) {
  $("selCount").textContent = n || "";
  $("selCount").classList.toggle("hidden", !n);
  $("barInfo").setAttribute("aria-label", n ? `Design panel, ${n} shape${n === 1 ? "" : "s"} selected` : "Design panel");
}

// Remember which settings sections are open (per browser, a convenience only).
function bindSections() {
  let open = {};
  try { open = JSON.parse(localStorage.getItem("edSections") || "{}"); } catch (_) {}
  document.querySelectorAll(".ed-sec[data-sec]").forEach((d) => {
    if (d.dataset.sec in open) d.open = !!open[d.dataset.sec];
    d.addEventListener("toggle", () => {
      open[d.dataset.sec] = d.open;
      try { localStorage.setItem("edSections", JSON.stringify(open)); } catch (_) {}
    });
  });
}

function bindEditor() {
  bindSections();
  // the shapes view keeps the design below the reference / edit bars (see --tools-h in style.css)
  const tools = document.querySelector(".ed-tools");
  new ResizeObserver(() => $("stage").style.setProperty("--tools-h", `${tools.offsetHeight}px`)).observe(tools);
  paneButtons().forEach((b) => (b.onclick = () => setPane(b.dataset.pane, { user: true })));
  $("barSettings").onclick = () => (sheetOpen("left") ? closeSheets() : openSheet("left"));
  $("barInfo").onclick = () => (sheetOpen("right") ? closeSheets() : openSheet("right"));
  $("sheetBackdrop").onclick = () => closeSheets();
  document.querySelectorAll("[data-close-sheet]").forEach((b) => (b.onclick = () => closeSheets()));
  document.addEventListener("keydown", (ev) => {
    if (ev.key === "Escape" && document.body.matches(".sheet-left, .sheet-right") && !document.querySelector("dialog[open]")) closeSheets();
  });
  // a wider window shows the panels again: no sheet stays open behind them
  window.matchMedia("(min-width: 1200px)").addEventListener("change", (m) => m.matches && closeSheets(false));
  $("btnUpdate").onclick = () => Jobs.stitch({ force: true, delay: 0 }).catch(() => {});
  $("autoUpdate").onchange = () => Jobs.setAuto($("autoUpdate").checked);
  $("btnExport").onclick = () => Export.open();
  $("btnFit").onclick = () => (state.pane === "stitches" ? fit() : Shapes.fit());
  try { $("showHoop").checked = localStorage.getItem("showHoop") !== "0"; } catch (_) {}
  $("hoopToggle").classList.toggle("hidden", !chosenHoop());
  $("showHoop").onchange = hoopChanged;
  $("btnZoomIn").onclick = () => zoomBy(1.25);
  $("btnZoomOut").onclick = () => zoomBy(1 / 1.25);
  $("zoomPct").onclick = () => zoomTo(actualPxPerMm());
  bindFullscreen();
  bindShortcuts();
  Export.init();
}

// Full screen (where the browser allows it): the whole app, so dialogs and notes still show.
function bindFullscreen() {
  const b = $("btnFullscreen"), root = document.documentElement;
  if (!(document.fullscreenEnabled && root.requestFullscreen)) return; // the button stays hidden
  b.classList.remove("hidden");
  const sync = () => {
    const on = !!document.fullscreenElement;
    b.setAttribute("aria-pressed", on);
    b.title = on ? "Leave full screen" : "Full screen";
    b.setAttribute("aria-label", b.title);
    b.querySelector("use").setAttribute("href", on ? "#i-fullscreen-exit" : "#i-fullscreen");
  };
  b.onclick = () => (document.fullscreenElement ? document.exitFullscreen() : root.requestFullscreen())
    .catch((e) => toast(`Full screen is not available here: ${e.message}`, true));
  document.addEventListener("fullscreenchange", sync);
  sync();
}

// Keyboard shortcuts of the editor. Each one presses the button it stands for, so it does
// exactly what a click does. Never while typing in a field or with a dialog open.
const SHORTCUTS = [
  ["View", [["F", "Fit the design to the view"], ["1", "Actual size (100 %)"], ["2", "200 %"], ["+ / −", "Zoom in / out"],
    ["Space + drag", "Move the view (in Stitches a plain drag works too)"], ["S", "Switch between Shapes and Stitches"],
    ["O", "Show or hide the original picture"], ["[ / ]", "Close or open the settings / design panel"]]],
  ["Editing a shape (Selection → Edit shape)", [["V", "Move and select"], ["B", "Paint area on"], ["E", "Erase area"],
    ["L / Shift+L", "Lasso: add / cut away"], ["G", "Draw a guide line on the selected shape"], ["Esc", "Cancel or finish"]]],
  ["Design", [["Ctrl+Z / Ctrl+Y", "Undo / redo"], ["Enter", "Update the preview (when Auto-update is off)"],
    ["Ctrl+E", "Export"], ["Menu key / Shift+F10", "Menu for the selected shapes (also right-click or long-press a shape)"], ["?", "This list"]]],
];
function openShortcuts() {
  // "Ctrl+Z / Ctrl+Y" -> keys joined by +, alternatives by /; "Space + drag" -> key + word
  const key = (k) => k.split(" / ").map((alt) => {
    if (alt.includes(" + ")) { const [a, b] = alt.split(" + "); return `<kbd>${esc(a)}</kbd> + ${esc(b)}`; }
    return (alt === "+" ? ["+"] : alt.split("+")).map((part) => `<kbd>${esc(part)}</kbd>`).join("+");
  }).join(" / ");
  $("keysTable").innerHTML = SHORTCUTS.map(([title, rows]) => `<tr><th colspan="2" scope="colgroup">${esc(title)}</th></tr>` +
    rows.map(([k, what]) => `<tr><td>${key(k)}</td><td>${esc(what)}</td></tr>`).join("")).join("");
  $("keysDialog").showModal();
}
function bindShortcuts() {
  $("btnKeys").onclick = openShortcuts;
  const press = (el) => { if (el && !el.disabled && !el.closest(".hidden, [hidden]")) { el.click(); return true; } return false; };
  document.addEventListener("keydown", (ev) => {
    if (ev.defaultPrevented || ev.altKey || !state.project || currentPage !== "editor" || document.querySelector("dialog[open]")
      || document.body.matches(".view-account, .view-admin, .signed-out")) return;
    const t = ev.target;
    if (t.matches?.("input, select, textarea, [contenteditable], [contenteditable] *")) return;
    const mod = ev.ctrlKey || ev.metaKey, k = ev.key;
    if (mod) {
      if (k.toLowerCase() === "e" && !ev.shiftKey) { ev.preventDefault(); press($("btnExport")); }
      return; // Ctrl+Z / Ctrl+Y have their own handler
    }
    // keys that buttons, lists and tabs use themselves
    if ((k === "Enter" || k === " ") && t.matches?.("button, a, summary, [role=listbox], [role=listbox] *, [role=tab]")) return;
    const lower = k.length === 1 ? k.toLowerCase() : k;
    const tool = (name) => press(document.querySelector(`#editBar [data-tool="${name}"]`));
    const done = {
      f: () => press($("btnFit")),
      1: () => zoomTo(actualPxPerMm()),
      2: () => zoomTo(2 * actualPxPerMm()),
      "+": () => zoomBy(1.25), "=": () => zoomBy(1.25),
      "-": () => zoomBy(1 / 1.25), _: () => zoomBy(1 / 1.25),
      s: () => setPane(state.pane === "stitches" ? "shapes" : "stitches", { user: true }),
      o: () => press($("showRef")),
      v: () => tool("move"), b: () => tool("add"), e: () => tool("erase"),
      l: () => tool(ev.shiftKey ? "lasso-erase" : "lasso-add"),
      g: () => press($("shapePanel").querySelector('[data-action="guide"]')),
      "[": () => Workspace.toggle("left"), "]": () => Workspace.toggle("right"),
      Enter: () => press($("btnUpdate")),
      "?": () => (openShortcuts(), true),
    }[lower];
    if (!done) return;
    if (ev.shiftKey && !["?", "+", "_", "l"].includes(lower)) return;
    if (done() !== false) ev.preventDefault();
  });
}

// Zoom: 100% = actual size. Without calibration the usual 96 screen pixels per inch (screens
// differ); "Calibrate 100 %" measures this screen against a bank card. Kept per browser as
// device pixels per mm, so it stays right when the browser zoom changes.
const STANDARD_PX_PER_MM = 96 / 25.4;
const Calibration = (() => {
  const KEY = "screenPxPerMm", CARD_MM = 85.6; // ISO/IEC 7810 ID-1 (bank card): 85.60 × 53.98 mm
  const RANGE = [0.5, 2]; // of the standard size
  let device = null;
  try { const v = parseFloat(localStorage.getItem(KEY)); if (v > 0) device = v; } catch (_) {}
  const dpr = () => window.devicePixelRatio || 1;
  const ppm = () => (device ? device / dpr() : STANDARD_PX_PER_MM);
  const label = () => $("zoomPct").title = device
    ? "Show at actual size (calibrated for this screen)"
    : "Show at actual size (approximate: screens differ - Calibrate 100 % in the shortcuts list)";

  function show(v) {
    $("calibCard").style.width = `${CARD_MM * v}px`;
    $("calibCard").style.height = `${53.98 * v}px`;
    $("calibOut").textContent = `${Math.round((100 * v) / STANDARD_PX_PER_MM)} % of the standard size`;
  }

  function open() {
    const r = $("calibSize");
    r.min = (STANDARD_PX_PER_MM * RANGE[0]).toFixed(2);
    r.max = (STANDARD_PX_PER_MM * RANGE[1]).toFixed(2);
    r.value = ppm().toFixed(2);
    show(+r.value);
    $("calibDialog").showModal();
  }

  function save(v) {
    device = v === null ? null : v * dpr();
    try { if (device) localStorage.setItem(KEY, device.toFixed(4)); else localStorage.removeItem(KEY); } catch (_) {}
    label();
    updateZoomLabel();
  }

  function init() {
    label();
    $("calibSize").addEventListener("input", (ev) => show(+ev.target.value));
    $("calibSave").addEventListener("click", () => {
      save(+$("calibSize").value);
      $("calibDialog").close();
      toast("100 % now shows designs at their real size on this screen.", true);
    });
    $("calibReset").addEventListener("click", () => {
      save(null);
      $("calibSize").value = STANDARD_PX_PER_MM.toFixed(2);
      show(STANDARD_PX_PER_MM);
    });
    $("btnCalibrate").addEventListener("click", () => { $("keysDialog").close(); open(); });
  }
  return { ppm, open, init, calibrated: () => device !== null };
})();
const actualPxPerMm = () => Calibration.ppm();
function pxPerMm() { return state.pane === "stitches" ? state.view.scale * 10 : Shapes.pxPerMm(); }
function updateZoomLabel() {
  const ppm = pxPerMm();
  $("zoomPct").textContent = ppm ? `${Math.round((100 * ppm) / actualPxPerMm())}%` : "–";
}
function zoomBy(f) {
  if (state.pane !== "stitches") return Shapes.zoomBy(f);
  if (!state.preview) return;
  const c = $("canvas"), v = state.view, mx = c.clientWidth / 2, my = c.clientHeight / 2;
  v.x = mx - (mx - v.x) * f;
  v.y = my - (my - v.y) * f;
  v.scale *= f;
  state.viewMoved = true;
  draw();
}
function zoomTo(ppm) {
  const cur = pxPerMm();
  if (cur) zoomBy(ppm / cur);
}

// ------------------------------------------------------------------ hoop
// The hoop chosen under Size, drawn around the design (centred, like the machine file: the
// centre of the design is the centre of the hoop) and checked against the design's size.
function chosenHoop() { return state.config?.hoops[$("hoop").value] || null; }
/** Does a design of w × h mm fit the chosen hoop (also turned 90°)? null without a hoop. */
function hoopFit(w, h) {
  const hp = chosenHoop();
  if (!hp) return null;
  const fits = w <= hp.width && h <= hp.height;
  return { hoop: hp, fits, turned: !fits && w <= hp.height && h <= hp.width };
}
const hoopShown = () => !!chosenHoop() && $("showHoop").checked;
/** The hoop outline in design mm (shapes view), or null. */
function hoopRectMm() {
  const hp = hoopShown() && chosenHoop();
  if (!hp || !state.design) return null;
  const W = state.design.width_mm, H = state.design.height_mm, f = hoopFit(W, H);
  return { x: W / 2 - hp.width / 2, y: H / 2 - hp.height / 2, w: hp.width, h: hp.height, name: hp.name, bad: !f.fits && !f.turned };
}
function hoopChanged() {
  try { localStorage.setItem("showHoop", $("showHoop").checked ? "1" : "0"); } catch (_) {}
  $("hoopToggle").classList.toggle("hidden", !chosenHoop());
  Shapes.fit();
  Shapes.refresh();
  if (state.preview) {
    renderStats();
    if (state.pane === "stitches") fit();
  }
  if ($("exportDialog").open) Export.refresh();
}

// ------------------------------------------------------------------ export dialog
// Choose the files, see a summary and the checks, download. The dialog first brings the
// stitches up to date, so a download always matches the settings on screen. One file
// downloads as it is, several come as one .zip (browsers block several downloads at once).
const Export = (() => {
  const pick = new Set(["dst"]); // kept while the page is open, across re-stitching
  let quota = null; // /api/projects/{id}/quota: charged, usage, features
  let downloading = false;
  const WILCOM = "wilcom"; // a package of its own (DST + artwork), downloaded on its own
  const STITCH_FILES = () => new Set([...Object.keys(state.config.formats), "png", "txt", WILCOM]);

  function items() {
    const machine = Object.entries(state.config.formats).map(([k, f]) => ({ key: k, group: "Machine files", label: `${f.label} (.${k})`, note: f.note, rec: k === "dst" }));
    return machine.concat([
      { key: WILCOM, group: "For editing in other software", label: "Continue in Wilcom (ZIP)",
        note: "The stitches as DST, the artwork as EPS and SVG (also one file per thread), the worksheet and a short how-to. Wilcom rebuilds editable objects from the stitches or the artwork. Not a Wilcom .EMB project. Downloads on its own" },
      { key: "svg", group: "For editing in other software", label: "Artwork (SVG)", note: "The cleaned-up design as a vector file for Illustrator, Inkscape or embroidery software that imports vector artwork. Free: not counted as a design download" },
      { key: "txt", group: "For the operator and customer", label: "Worksheet (TXT)", note: "Thread order, stitch count and time for the machine operator" },
      { key: "png", group: "For the operator and customer", label: "Preview picture (PNG)", note: "A realistic picture of the embroidery – handy for customer approval" },
    ]);
  }
  const lockOf = (key) => (quota && !quota.features?.all_formats && key in state.config.formats && key !== "dst" ? "Not in your plan" : "");

  function render() {
    const list = items();
    for (const k of [...pick]) if (!list.some((i) => i.key === k) || lockOf(k)) pick.delete(k);
    let group = "";
    $("exports").innerHTML = list.map((it) => {
      const lock = lockOf(it.key), on = pick.has(it.key);
      const head = it.group !== group ? `<p class="exp-grp">${esc((group = it.group))}</p>` : "";
      return `${head}<label class="${it.rec ? "rec" : ""}${on ? " on" : ""}${lock ? " locked" : ""}"><input type="checkbox" value="${it.key}" ${on ? "checked" : ""} ${lock ? "disabled" : ""}>` +
        `<span><b>${esc(it.label)}${it.rec ? " ★ recommended" : ""}</b><span>${esc(it.note)}</span></span>${lock ? `<span class="lock-badge">${lock}</span>` : ""}</label>`;
    }).join("");
    summary();
    update();
  }

  function summary() {
    const p = state.preview;
    const facts = [];
    const checks = [];
    if (p) {
      const s = shownStats();
      facts.push(["Size", `${s.width_mm} × ${s.height_mm} mm`]);
      const hf = hoopFit(s.width_mm, s.height_mm);
      if (hf) facts.push(["Hoop", `${hf.hoop.name} · ${hf.fits ? "fits" : hf.turned ? "fits turned 90°" : "too large"}`]);
      facts.push(["Stitches", s.stitches.toLocaleString()], ["Colours · changes", `${s.colors} · ${s.color_changes}`],
        ["Thread cuts", String(s.trims)], ["Sewing time (est.)", `${s.est_minutes} min`]);
      $("warnings3").querySelectorAll("div").forEach((d) => checks.push(d.textContent)); // the same checks as the Checks panel
      if (pick.has("dst") && !pick.has("txt")) checks.push("DST files carry no thread colours: add the worksheet to know which colour goes on which needle.");
    }
    $("expFacts").innerHTML = facts.map(([k, v]) => `<dt>${esc(k)}</dt><dd>${esc(v)}</dd>`).join("");
    $("expChecks").innerHTML = checks.map((c) => `<div>${esc(c)}</div>`).join("");
    if (p) Render.thumbnail($("expThumb"));
  }

  function update() {
    const chosen = items().filter((i) => pick.has(i.key));
    const n = chosen.length;
    const needStitches = chosen.some((i) => STITCH_FILES().has(i.key));
    const busyNow = Jobs.pending() || working.length > 0;
    const notReady = needStitches && (busyNow || Jobs.stale() || !!Jobs.error() || !state.preview);
    const zipLocked = n > 1 && quota && !quota.features?.zip_export;
    const btn = $("btnDownload");
    btn.disabled = !n || notReady || zipLocked || downloading;
    btn.textContent = downloading ? "Preparing…" : !n ? "Choose a file to download" : n === 1 ? `Download ${chosen[0].label}` : `Download ${n} files (.zip)`;
    $("btnExportAll").textContent = items().filter((i) => !lockOf(i.key) && i.key !== WILCOM).every((i) => pick.has(i.key)) ? "Clear all" : "Select all";
    let msg = "";
    if (needStitches && busyNow) msg = "Bringing the stitches up to date…";
    else if (needStitches && Jobs.error()) msg = `The stitches could not be updated: ${Jobs.error()}`;
    else if (needStitches && (Jobs.stale() || !state.preview)) msg = "The stitches are not up to date yet.";
    else if (zipLocked) msg = "Your plan downloads one file at a time: choose one file.";
    $("expState").textContent = msg;
  }

  async function open() {
    if (!state.design || !state.project) return;
    closeSheets(false);
    quota = null;
    render();
    $("exportDialog").showModal();
    // up to date before downloading: run waiting changes now, in manual mode too
    if (Jobs.stale() || Jobs.error() || (!state.preview && state.design)) Jobs.stitch({ force: true, delay: 0 }).catch(() => {});
    const pid = state.project.id;
    const [q] = await Promise.all([Account.quotaNote(pid), Jobs.flush()]);
    if (state.project?.id !== pid || !$("exportDialog").open) return;
    quota = q || null;
    render();
  }

  async function download() {
    const chosen = items().map((i) => i.key).filter((k) => pick.has(k));
    if (!chosen.length || !state.project) return;
    await Jobs.flush();
    if ($("btnDownload").disabled) return;
    const base = `/api/projects/${state.project.id}/export/`;
    downloading = true;
    update();
    try {
      // fetched (not a plain link) so a used-up allowance or a plan limit shows a clear message
      await Account.download(chosen.length === 1 ? base + chosen[0] : `${base}zip?formats=${chosen.join(",")}`, state.project.id);
    } finally {
      downloading = false;
      update();
    }
  }

  function init() {
    $("exports").addEventListener("change", (ev) => {
      const box = ev.target.closest("input[type=checkbox]");
      if (!box) return;
      if (box.checked) {
        // the Wilcom package is a ZIP of its own: it is not combined with other files
        if (box.value === WILCOM) pick.clear(); else pick.delete(WILCOM);
        pick.add(box.value);
      } else pick.delete(box.value);
      // the boxes stay where they are (focus, screen readers): only their state follows
      $("exports").querySelectorAll("input[type=checkbox]").forEach((b) => {
        b.checked = pick.has(b.value);
        b.closest("label").classList.toggle("on", b.checked);
      });
      summary();
      update();
    });
    $("btnExportAll").onclick = () => {
      const free = items().filter((i) => !lockOf(i.key) && i.key !== WILCOM);
      const all = free.every((i) => pick.has(i.key));
      pick.clear();
      if (!all) free.forEach((i) => pick.add(i.key));
      render();
    };
    $("btnDownload").onclick = () => download().catch(() => {});
  }

  return { init, open, refresh: () => { summary(); update(); } };
})();

// ------------------------------------------------------------------ stitch preview: drawing
// The stitches are drawn into an off-screen picture once per view (zoom, position, style)
// and copied to the screen; selection outlines and markers go on top. The sewing simulation
// only adds the new stitches. While you zoom or move, a quick version is drawn and the full
// one follows when you stop. The look is exactly that of drawing stitch by stitch.
const KIND_COLORS = ["#2d6cdf", "#d6336c", "#2f9e44", "#f08c00", "#868e96", "#ae3ec9", "#000"]; // fill satin running underlay travel tie

function shade(hex, f) {
  const n = parseInt(hex.slice(1), 16);
  const ch = [(n >> 16) & 255, (n >> 8) & 255, n & 255].map((v) => Math.max(0, Math.min(255, Math.round(f >= 1 ? v + (255 - v) * (f - 1) : v * f))));
  return `rgb(${ch.join(",")})`;
}

const Render = (() => {
  const base = document.createElement("canvas");
  const bctx = base.getContext("2d");
  let key = "", st = null, version = 0, fast = null;
  let raf = 0, idleTimer = 0, interacting = false;
  const ref = { url: "", img: null, ok: false };

  function reset() { version++; key = ""; st = null; fast = null; }
  /** Draw on the next frame. ``moving``: quick version until the zooming / moving stops. */
  function request(moving = false) {
    if (moving) {
      interacting = true;
      clearTimeout(idleTimer);
      idleTimer = setTimeout(() => { interacting = false; request(); }, 140);
    }
    if (!raf) raf = requestAnimationFrame(() => { raf = 0; frame(); });
  }

  function canvasSize(c) {
    const dpr = window.devicePixelRatio || 1;
    const w = Math.max(1, Math.round(c.clientWidth * dpr)), h = Math.max(1, Math.round(c.clientHeight * dpr));
    if (c.width !== w || c.height !== h) { c.width = w; c.height = h; }
    return { w, h, dpr };
  }

  // the original picture (or first extracted design) under the stitches, aligned in mm
  function refImage(spec) {
    if (!spec) return null;
    if (ref.url !== spec.url) {
      ref.url = spec.url;
      ref.ok = false;
      ref.img = new Image();
      ref.img.onload = () => { ref.ok = true; key = ""; request(); };
      ref.img.src = spec.url;
    }
    return ref.ok ? ref.img : null;
  }
  // split view: left of x (CSS px) the original at full strength instead of the stitches
  function drawSplit(ctx, v, spec, x) {
    ctx.save();
    ctx.beginPath(); ctx.rect(0, 0, x, 1e6); ctx.clip();
    ctx.clearRect(0, 0, x, 1e6);
    drawRef(ctx, v, { ...spec, opacity: 1 });
    ctx.restore();
  }
  function drawRef(ctx, v, spec) {
    const img = refImage(spec);
    if (!img) return;
    ctx.globalAlpha = spec.opacity;
    ctx.drawImage(img, v.x + spec.x * 10 * v.scale, v.y + spec.y * 10 * v.scale, spec.w * 10 * v.scale, spec.h * 10 * v.scale);
    ctx.globalAlpha = 1;
  }

  // stitch by stitch from where the last drawing stopped (st) up to `upto`
  function drawStitches(ctx, v, ops, mode, colors, s, upto) {
    const X = (u) => v.x + u * v.scale, Y = (u) => v.y + u * v.scale;
    const threadW = Math.max(0.6, 3.2 * v.scale); // 0.32 mm thread (units are 0.1 mm)
    ctx.lineCap = "round";
    let { color, px, py, n } = s;
    for (let i = s.i; i < upto * 4; i += 4) {
      const cmd = ops[i], x = ops[i + 1], y = ops[i + 2], kind = ops[i + 3];
      if (cmd === 3) { color++; continue; }
      if (cmd === 4) continue; // machine stop (applique)
      if (cmd === 2) { if (px !== null) s.trims.push([px, py]); continue; }
      if (cmd === 1) { if (px !== null) s.jumps.push([px, py, x, y]); px = x; py = y; continue; }
      if (px === null) { px = x; py = y; continue; }
      const hex = colors[Math.min(color, colors.length - 1)].hex;
      if (mode === "real" || mode === "check" || mode === "density") {
        if (mode !== "real") ctx.globalAlpha = mode === "density" ? 0.3 : 0.35; // faded so markers / the density map stand out
        ctx.lineWidth = threadW;
        ctx.strokeStyle = shade(hex, 0.62);
        ctx.beginPath(); ctx.moveTo(X(px), Y(py)); ctx.lineTo(X(x), Y(y)); ctx.stroke();
        ctx.lineWidth = threadW * 0.7;
        ctx.strokeStyle = hex;
        ctx.stroke();
        if (threadW > 3) { ctx.lineWidth = threadW * 0.22; ctx.strokeStyle = shade(hex, 1.35); ctx.stroke(); }
        ctx.globalAlpha = 1;
      } else {
        ctx.lineWidth = 1;
        ctx.strokeStyle = mode === "type" ? KIND_COLORS[kind] : hex;
        ctx.globalAlpha = mode === "type" && kind === 3 ? 0.6 : 1;
        ctx.beginPath(); ctx.moveTo(X(px), Y(py)); ctx.lineTo(X(x), Y(y)); ctx.stroke();
        ctx.globalAlpha = 1;
        if (mode === "direction" && kind !== 3 && kind !== 5 && n % 14 === 0) arrow(ctx, X(px), Y(py), X(x), Y(y));
      }
      px = x; py = y; n++;
    }
    Object.assign(s, { i: Math.max(s.i, upto * 4), color, px, py, n, upto });
  }

  // quick version while zooming / moving: one path per thread colour (or stitch type)
  function fastPaths(ops, colors) {
    if (fast && fast.version === version && fast.variant === state.variant) return fast;
    const byColor = colors.map(() => new Path2D()), byKind = KIND_COLORS.map(() => new Path2D());
    let color = 0, px = null, py = null;
    for (let i = 0; i < ops.length; i += 4) {
      const cmd = ops[i], x = ops[i + 1], y = ops[i + 2], kind = ops[i + 3];
      if (cmd === 3) { color++; continue; }
      if (cmd === 4 || cmd === 2) continue;
      if (cmd === 1 || px === null) { px = x; py = y; continue; }
      const c = Math.min(color, colors.length - 1);
      byColor[c].moveTo(px, py); byColor[c].lineTo(x, y);
      byKind[kind] && (byKind[kind].moveTo(px, py), byKind[kind].lineTo(x, y));
      px = x; py = y;
    }
    fast = { version, variant: state.variant, byColor, byKind };
    return fast;
  }
  function drawFast(ctx, v, dpr, ops, mode, colors) {
    const f = fastPaths(ops, colors);
    ctx.save();
    ctx.setTransform(dpr * v.scale, 0, 0, dpr * v.scale, dpr * v.x, dpr * v.y);
    ctx.lineCap = "round";
    ctx.lineJoin = "round";
    const unit = 1 / v.scale; // one screen pixel in stitch units
    if (mode === "type") {
      f.byKind.forEach((p, k) => { ctx.globalAlpha = k === 3 ? 0.6 : 1; ctx.strokeStyle = KIND_COLORS[k]; ctx.lineWidth = unit; ctx.stroke(p); });
    } else {
      ctx.globalAlpha = mode === "check" ? 0.35 : mode === "density" ? 0.3 : 1;
      ctx.lineWidth = mode === "real" || mode === "check" || mode === "density" ? Math.max(0.6, 3.2 * v.scale) * 0.7 * unit : unit;
      f.byColor.forEach((p, i) => { ctx.strokeStyle = colors[i].hex; ctx.stroke(p); });
    }
    ctx.restore();
  }

  function frame() {
    const c = $("canvas"), p = state.preview;
    if (!p || !c.clientWidth || !c.clientHeight) return;
    const { w, h, dpr } = canvasSize(c);
    const ctx = c.getContext("2d");
    const v = state.view, ops = activeOps(), mode = state.mode, total = ops.length / 4;
    const upto = Math.min(state.upto, total);
    const refSpec = Shapes.reference();
    const split = Split.at(refSpec); // split view: the original on the left, the stitches (without the faded one) on the right
    const spec = split === null ? refSpec : null;
    const k = [w, h, dpr, v.scale, v.x, v.y, mode, state.variant, version, spec ? `${spec.url}@${spec.opacity}:${ref.ok && ref.url === spec.url}` : ""].join("|");

    if (interacting && k !== key && upto >= total) { // quick version, the full one follows
      ctx.setTransform(1, 0, 0, 1, 0, 0);
      ctx.clearRect(0, 0, w, h);
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
      drawRef(ctx, v, spec);
      drawFast(ctx, v, dpr, ops, mode, p.colors);
      if (split !== null) drawSplit(ctx, v, refSpec, (w / dpr) * split);
      Shapes.drawSelection3(ctx, v, dpr);
      ctx.setTransform(1, 0, 0, 1, 0, 0);
      Split.place(split);
      updateZoomLabel();
      viewMoved();
      return;
    }
    if (k !== key || !st || upto < st.upto) {
      if (base.width !== w || base.height !== h) { base.width = w; base.height = h; }
      bctx.setTransform(1, 0, 0, 1, 0, 0);
      bctx.clearRect(0, 0, w, h);
      bctx.setTransform(dpr, 0, 0, dpr, 0, 0);
      drawRef(bctx, v, spec);
      st = { i: 0, color: 0, px: null, py: null, n: 0, jumps: [], trims: [], upto: 0 };
      key = k;
    }
    if (upto > st.upto) {
      bctx.setTransform(dpr, 0, 0, dpr, 0, 0);
      drawStitches(bctx, v, ops, mode, p.colors, st, upto);
    }

    // on screen: the stitches, then markers and the selection on top
    ctx.setTransform(1, 0, 0, 1, 0, 0);
    ctx.clearRect(0, 0, w, h);
    ctx.drawImage(base, 0, 0);
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    const X = (u) => v.x + u * v.scale, Y = (u) => v.y + u * v.scale;
    if (mode === "check") drawRisks(ctx, X, Y, p.risks || []);
    if (mode === "density") drawDensity(ctx, X, Y, v.scale, ops);
    if (mode !== "real" && mode !== "check" && mode !== "density") {
      ctx.setLineDash([4, 4]); ctx.strokeStyle = "#e03131"; ctx.lineWidth = 1;
      st.jumps.forEach(([a, b, c2, d]) => { ctx.beginPath(); ctx.moveTo(X(a), Y(b)); ctx.lineTo(X(c2), Y(d)); ctx.stroke(); });
      ctx.setLineDash([]);
      ctx.fillStyle = "#e03131";
      st.trims.forEach(([a, b]) => { ctx.beginPath(); ctx.arc(X(a), Y(b), 3, 0, 7); ctx.fill(); });
    }
    const hr = hoopRectUnits(ops);
    if (hr) {
      const s = shownStats(), hf = hoopFit(s.width_mm, s.height_mm);
      drawHoop(ctx, X, Y, v.scale, hr, hf && !hf.fits && !hf.turned);
    }
    if (st.px !== null && upto < total) {
      ctx.strokeStyle = "#e03131"; ctx.lineWidth = 2;
      ctx.beginPath(); ctx.arc(X(st.px), Y(st.py), 6, 0, 7); ctx.stroke();
    }
    if (split !== null) drawSplit(ctx, v, refSpec, (w / dpr) * split);
    Shapes.drawSelection3(ctx, v, dpr);
    ctx.setTransform(1, 0, 0, 1, 0, 0);
    Split.place(split);
    $("legend").innerHTML = mode === "type"
      ? ["fill", "satin", "outline", "foundation", "moving", "lock"].map((k2, i) => `<span style="background:${KIND_COLORS[i]}"></span>${k2}`).join("") + `<span style="background:#e03131"></span>jump / thread cut`
      : mode === "direction" ? "Arrows show which way the needle sews · red dashes = jumps · red dots = thread cuts"
      : mode === "check" ? riskLegend(p.risks || [])
      : mode === "density" ? densityLegend(ops) : "";
    $("legend").classList.toggle("hidden", mode === "real");
    $("scrubInfo").textContent = `${st.n.toLocaleString()} stitches sewn · colour ${Math.min(st.color + 1, p.colors.length)} of ${p.colors.length}`;
    updateZoomLabel();
    viewMoved();
  }

  /** The whole design, realistic, fitted into a small canvas (export summary). Drawn again
   *  only when the stitches, the version shown or the size change. */
  function thumbnail(c) {
    const p = state.preview;
    if (!p || !c.clientWidth) return;
    const { w, h, dpr } = canvasSize(c);
    const tkey = `${version}|${state.variant}|${w}x${h}`;
    if (c.dataset.key === tkey) return;
    c.dataset.key = tkey;
    const ctx = c.getContext("2d");
    const ops = activeOps();
    const v = fitView(ops, w / dpr, h / dpr, 12);
    ctx.setTransform(1, 0, 0, 1, 0, 0);
    ctx.clearRect(0, 0, w, h);
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    drawStitches(ctx, v, ops, "real", p.colors, { i: 0, color: 0, px: null, py: null, n: 0, jumps: [], trims: [], upto: 0 }, ops.length / 4);
    ctx.setTransform(1, 0, 0, 1, 0, 0);
  }

  // density view: needle points per cell, counted like the server's machine check
  // (stitch/safety.py: DENSITY_CELL_MM cells, DENSITY_LIMIT per mm² = "too dense")
  let dens = null;
  function densityCells(ops) {
    if (dens && dens.version === version && dens.ops === ops) return dens;
    const cfg = state.config.density || { cell_mm: 1, limit: 40 };
    const cu = cfg.cell_mm * 10, cells = new Map(); // ops are in 0.1 mm
    for (let i = 0; i < ops.length; i += 4) {
      if (ops[i] !== 0) continue;
      const k = `${Math.floor(ops[i + 1] / cu)},${Math.floor(ops[i + 2] / cu)}`;
      cells.set(k, (cells.get(k) || 0) + 1);
    }
    const perMm2 = 1 / (cfg.cell_mm * cfg.cell_mm);
    dens = { version, ops, cells, cu, limit: cfg.limit, perMm2 };
    return dens;
  }
  const densityBin = (perMm2, limit) => (perMm2 > limit ? 4 : Math.min(3, Math.floor((Math.max(perMm2, 1e-9) / limit) * 4 - 1e-9)));
  function drawDensity(ctx, X, Y, scale, ops) {
    const d = densityCells(ops), size = d.cu * scale;
    ctx.globalAlpha = 0.78;
    for (const [k, n] of d.cells) {
      const [cx, cy] = k.split(",").map(Number);
      ctx.fillStyle = DENSITY_COLORS[densityBin(n * d.perMm2, d.limit)];
      ctx.fillRect(X(cx * d.cu), Y(cy * d.cu), Math.max(1, size - (size > 6 ? 1 : 0)), Math.max(1, size - (size > 6 ? 1 : 0)));
    }
    ctx.globalAlpha = 1;
  }
  function densityLegend(ops) {
    const d = densityCells(ops), q = d.limit / 4;
    const over = [...d.cells.values()].filter((n) => n * d.perMm2 > d.limit).length;
    const labels = [`up to ${q}`, `${q}–${2 * q}`, `${2 * q}–${3 * q}`, `${3 * q}–${d.limit}`, `over ${d.limit} – too dense`];
    return `Needle points per mm²: ` + labels.map((l, i) => `<span style="background:${DENSITY_COLORS[i]}"></span>${l}`).join(", ") +
      ` · ${over ? `${over} mm² over the limit` : "nothing over the limit"}`;
  }
  /** Needle points per mm² at a spot (mm), for the hover note; null outside the stitching. */
  function densityAt(xMm, yMm) {
    const p = state.preview;
    if (!p) return null;
    const d = densityCells(activeOps());
    const n = d.cells.get(`${Math.floor((xMm * 10) / d.cu)},${Math.floor((yMm * 10) / d.cu)}`);
    return n ? { perMm2: n * d.perMm2, over: n * d.perMm2 > d.limit } : null;
  }

  return { request, reset, thumbnail, now: frame, densityAt };
})();
// sequential blue (light -> dark) for the four bands up to the limit, status-critical red above it
const DENSITY_COLORS = ["#b7d3f6", "#6da7ec", "#2a78d6", "#184f95", "#d03b3b"];

/** Redraw the stitch preview (on the next frame). */
function draw() { Render.request(); }

// Split view (stitch preview): a handle divides the original picture (left) from the stitches
// (right), lined up like the faded reference. Drag it or use the arrow keys; remembered per browser.
const Split = (() => {
  let at = 0.5;
  try { const s = parseFloat(localStorage.getItem("splitAt")); if (s >= 0 && s <= 1) at = s; } catch (_) {}
  const handle = () => $("splitHandle");
  /** Where the split is (0..1 of the width), or null when there is none to show. */
  const where = (spec) => ($("refSplit")?.checked && spec && state.pane === "stitches" ? at : null);

  function place(split) {
    const h = handle();
    if (!h) return;
    h.hidden = split === null;
    if (split === null) return;
    const pct = Math.round(at * 100);
    h.style.left = `clamp(12px, ${at * 100}%, calc(100% - 12px))`; // grabbable at the edges too
    h.setAttribute("aria-valuenow", pct);
    h.setAttribute("aria-valuetext", `${pct} % original, ${100 - pct} % stitches`);
  }

  function set(v) {
    at = Math.min(1, Math.max(0, v));
    try { localStorage.setItem("splitAt", at.toFixed(3)); } catch (_) {}
    draw();
  }

  function sync() {
    $("refBar").classList.toggle("split-on", !!$("refSplit").checked);
    draw();
  }

  function init() {
    const h = handle();
    $("refSplit").addEventListener("change", sync);
    sync();
    let drag = null;
    h.addEventListener("pointerdown", (ev) => {
      if (ev.pointerType === "mouse" && ev.button !== 0) return;
      ev.preventDefault();
      h.setPointerCapture(ev.pointerId);
      drag = ev.pointerId;
      h.focus({ preventScroll: true });
    });
    h.addEventListener("pointermove", (ev) => {
      if (drag !== ev.pointerId) return;
      const r = $("canvasWrap").getBoundingClientRect();
      if (r.width) set((ev.clientX - r.left) / r.width);
    });
    const end = (ev) => { if (drag === ev.pointerId) drag = null; };
    h.addEventListener("pointerup", end);
    h.addEventListener("pointercancel", end);
    h.addEventListener("keydown", (ev) => {
      const step = { ArrowLeft: -0.05, ArrowDown: -0.05, ArrowRight: 0.05, ArrowUp: 0.05, PageDown: -0.2, PageUp: 0.2 }[ev.key];
      if (step !== undefined) set(at + step);
      else if (ev.key === "Home") set(0);
      else if (ev.key === "End") set(1);
      else return;
      ev.preventDefault();
    });
    // zooming over the handle zooms the preview, as anywhere else
    h.addEventListener("wheel", (ev) => {
      ev.preventDefault();
      $("canvas").dispatchEvent(new WheelEvent("wheel", ev));
    }, { passive: false });
  }
  return { at: where, place, init };
})();
/** The visible view changed: the grid and the selection toolbar follow (workspace.js, shapetools.js). */
function viewMoved() {
  if (typeof Workspace !== "undefined") Workspace.sync();
  if (typeof ShapeTools !== "undefined") ShapeTools.moved();
}

// the box around every stitch (stitch units, 0.1 mm), kept for the stitches last asked for
let boundsCache = { ops: null, b: null };
function stitchBounds(ops) {
  if (boundsCache.ops === ops) return boundsCache.b;
  let minx = Infinity, miny = Infinity, maxx = -Infinity, maxy = -Infinity;
  for (let i = 0; i < ops.length; i += 4) {
    if (ops[i] !== 0) continue;
    minx = Math.min(minx, ops[i + 1]); maxx = Math.max(maxx, ops[i + 1]);
    miny = Math.min(miny, ops[i + 2]); maxy = Math.max(maxy, ops[i + 2]);
  }
  boundsCache = { ops, b: Number.isFinite(minx) ? { minx, miny, maxx, maxy } : null };
  return boundsCache.b;
}
/** The hoop outline in stitch units, centred on the stitches like the machine file, or null. */
function hoopRectUnits(ops) {
  const hp = hoopShown() && chosenHoop(), b = stitchBounds(ops);
  if (!hp || !b) return null;
  const cx = (b.minx + b.maxx) / 2, cy = (b.miny + b.maxy) / 2;
  return { x: cx - hp.width * 5, y: cy - hp.height * 5, w: hp.width * 10, h: hp.height * 10, hoop: hp };
}
// view that shows every stitch (and ``extra``, e.g. the hoop), with a margin in stitch units
function fitView(ops, w, h, margin = 20, extra = null) {
  const b = stitchBounds(ops);
  if (!b) return { scale: 1, x: 0, y: 0 };
  let { minx, miny, maxx, maxy } = b;
  if (extra) {
    minx = Math.min(minx, extra.x); miny = Math.min(miny, extra.y);
    maxx = Math.max(maxx, extra.x + extra.w); maxy = Math.max(maxy, extra.y + extra.h);
  }
  const s = Math.min(w / (maxx - minx + 2 * margin), h / (maxy - miny + 2 * margin));
  return { scale: s, x: (w - (maxx + minx) * s) / 2, y: (h - (maxy + miny) * s) / 2 };
}
function fit() {
  const c = $("canvas"), p = state.preview;
  if (!p || !c.clientWidth || !c.clientHeight) return; // not on screen
  state.viewMoved = false;
  const ops = activeOps();
  state.view = fitView(ops, c.clientWidth, c.clientHeight, 20, hoopRectUnits(ops));
  draw();
}
function drawHoop(ctx, X, Y, scale, r, bad) {
  const x = X(r.x), y = Y(r.y), w = r.w * scale, h = r.h * scale;
  ctx.save();
  ctx.setLineDash([6, 5]);
  ctx.lineWidth = 1.5;
  ctx.strokeStyle = ctx.fillStyle = bad ? "#e03131" : "rgba(108, 117, 132, .95)";
  ctx.beginPath();
  if (ctx.roundRect) ctx.roundRect(x, y, w, h, Math.min(w, h) * 0.06); else ctx.rect(x, y, w, h);
  ctx.stroke();
  ctx.setLineDash([]);
  ctx.font = "11px system-ui, -apple-system, 'Segoe UI', sans-serif";
  ctx.fillText(`Hoop ${r.hoop.name}${bad ? " – the design does not fit" : ""}`, x + 8, y + 16);
  ctx.restore();
}

// ------------------------------------------------------------------ stitch preview: zoom, move, select, play
function bindCanvas() {
  const c = $("canvas");
  let drag = null;
  // density view: the exact figure under the pointer (mouse / pen; touch shows the legend)
  const tip = $("densityTip");
  c.addEventListener("pointermove", (ev) => {
    if (state.mode !== "density" || ev.pointerType === "touch" || ev.buttons) { tip.hidden = true; return; }
    const r = c.getBoundingClientRect(), v = state.view;
    const d = Render.densityAt((ev.clientX - r.left - v.x) / v.scale / 10, (ev.clientY - r.top - v.y) / v.scale / 10);
    if (!d) { tip.hidden = true; return; }
    tip.textContent = `${d.perMm2 % 1 ? d.perMm2.toFixed(1) : d.perMm2} needle points per mm²${d.over ? " – too dense" : ""}`;
    tip.hidden = false;
    const x = ev.clientX - r.left + 14, y = ev.clientY - r.top + 14;
    tip.style.left = `${Math.min(x, c.clientWidth - tip.offsetWidth - 6)}px`;
    tip.style.top = `${Math.min(y, c.clientHeight - tip.offsetHeight - 6)}px`;
  });
  c.addEventListener("pointerleave", () => (tip.hidden = true));
  c.addEventListener("wheel", (ev) => {
    if (!state.preview) return;
    ev.preventDefault();
    const r = c.getBoundingClientRect();
    const mx = ev.clientX - r.left, my = ev.clientY - r.top;
    const f = ev.deltaY < 0 ? 1.15 : 1 / 1.15;
    const v = state.view;
    v.x = mx - (mx - v.x) * f;
    v.y = my - (my - v.y) * f;
    v.scale *= f;
    state.viewMoved = true;
    Render.request(true);
  }, { passive: false });
  // touch: two fingers pinch to zoom (one finger drags, a tap selects)
  const touches = new Map();
  let pinch = null;
  c.addEventListener("pointerdown", (ev) => {
    touches.set(ev.pointerId, { x: ev.clientX, y: ev.clientY });
    if (touches.size === 2) {
      const [a, b] = [...touches.values()];
      pinch = { d: Math.hypot(a.x - b.x, a.y - b.y) || 1 };
      drag = null;
      return;
    }
    if (ev.button === 2) return; // right button: the shape menu (shapetools.js), never a click-select
    drag = { x: ev.clientX, y: ev.clientY, sx: ev.clientX, sy: ev.clientY };
    c.setPointerCapture(ev.pointerId);
  });
  c.addEventListener("pointermove", (ev) => {
    if (touches.has(ev.pointerId)) touches.set(ev.pointerId, { x: ev.clientX, y: ev.clientY });
    if (pinch && touches.size === 2) {
      const [a, b] = [...touches.values()];
      const d = Math.hypot(a.x - b.x, a.y - b.y) || 1;
      const r = c.getBoundingClientRect();
      const mx = (a.x + b.x) / 2 - r.left, my = (a.y + b.y) / 2 - r.top, f = d / pinch.d, v = state.view;
      v.x = mx - (mx - v.x) * f;
      v.y = my - (my - v.y) * f;
      v.scale *= f;
      pinch.d = d;
      state.viewMoved = true;
      Render.request(true);
      return;
    }
    if (!drag) return;
    state.view.x += ev.clientX - drag.x;
    state.view.y += ev.clientY - drag.y;
    state.viewMoved = true;
    drag = { ...drag, x: ev.clientX, y: ev.clientY };
    Render.request(true);
  });
  const lift = (ev) => {
    touches.delete(ev.pointerId);
    if (touches.size < 2) pinch = null;
  };
  c.addEventListener("pointerup", lift);
  c.addEventListener("pointercancel", lift);
  c.addEventListener("pointerup", (ev) => {
    const d = drag;
    drag = null;
    if (!d || !state.preview || Math.hypot(ev.clientX - d.sx, ev.clientY - d.sy) >= 4) return;
    // a click (not a drag): select the shape under the pointer
    const r = c.getBoundingClientRect(), v = state.view;
    const xMm = (ev.clientX - r.left - v.x) / v.scale / 10, yMm = (ev.clientY - r.top - v.y) / v.scale / 10;
    state.autoPane = false;
    Shapes.click3(xMm, yMm, ev.shiftKey || ev.ctrlKey || ev.metaKey);
  });
  c.addEventListener("pointercancel", () => (drag = null));
  // a new size (window, panels, rotated phone) fits the design again, unless you zoomed or moved it
  new ResizeObserver(() => {
    if (!state.preview || !c.clientWidth || state.pane !== "stitches") return;
    if (state.viewMoved) draw(); else fit();
  }).observe($("canvasWrap"));
  $("showFabric").onchange = () => { $("canvasWrap").classList.toggle("dark", $("showFabric").checked); draw(); };
  try { $("showWeave").checked = localStorage.getItem("weave") === "1"; } catch (_) {}
  $("canvasWrap").classList.toggle("weave", $("showWeave").checked);
  $("showWeave").onchange = () => {
    $("canvasWrap").classList.toggle("weave", $("showWeave").checked);
    try { localStorage.setItem("weave", $("showWeave").checked ? "1" : "0"); } catch (_) {}
  };
  $("viewMode").addEventListener("click", (ev) => {
    const m = ev.target.closest("[data-mode]")?.dataset.mode;
    if (!m) return;
    state.mode = m;
    document.querySelectorAll("#viewMode button").forEach((b) => {
      b.classList.toggle("on", b.dataset.mode === m);
      b.setAttribute("aria-pressed", b.dataset.mode === m);
    });
    draw();
  });
  $("variant").addEventListener("click", (ev) => {
    const v = ev.target.closest("[data-variant]")?.dataset.variant;
    if (v && v !== state.variant) setVariant(v);
  });
  $("scrub").oninput = () => { if (!state.preview) return; state.upto = +$("scrub").value; draw(); };
  $("btnPlay").onclick = togglePlay;
}

// Machine check: spots where the thread may break or a detail may not show (stitch/safety.py)
const RISKS = {
  thin: ["#f08c00", "too thin to sew cleanly"],
  short: ["#e03131", "very short stitches"],
  stack: ["#ae3ec9", "needle hits one hole many times"],
  dense: ["#d6336c", "too many stitches in a small spot"],
};
function drawRisks(ctx, X, Y, risks) {
  ctx.lineWidth = 2;
  risks.forEach((r) => {
    const [col] = RISKS[r.type] || ["#e03131"];
    ctx.strokeStyle = col;
    ctx.beginPath(); ctx.arc(X(r.x * 10), Y(r.y * 10), 9, 0, 7); ctx.stroke();
  });
}
function riskLegend(risks) {
  if (!risks.length) return `<span style="background:#2f9e44"></span>No problem spots found – safe to sew`;
  const n = {};
  risks.forEach((r) => (n[r.type] = (n[r.type] || 0) + 1));
  return Object.entries(RISKS).filter(([k]) => n[k]).map(([k, [col, label]]) => `<span style="background:${col}"></span>${label} (${n[k]})`).join("");
}

function arrow(ctx, x1, y1, x2, y2) {
  const a = Math.atan2(y2 - y1, x2 - x1), mx = (x1 + x2) / 2, my = (y1 + y2) / 2, s = 5;
  ctx.beginPath();
  ctx.moveTo(mx - s * Math.cos(a - 0.5), my - s * Math.sin(a - 0.5));
  ctx.lineTo(mx, my);
  ctx.lineTo(mx - s * Math.cos(a + 0.5), my - s * Math.sin(a + 0.5));
  ctx.stroke();
}

function togglePlay() {
  if (!state.preview && !state.playing) return;
  state.playing = !state.playing;
  $("btnPlay").innerHTML = ico(state.playing ? "pause" : "play");
  $("btnPlay").setAttribute("aria-label", state.playing ? "Pause sewing simulation" : "Play sewing simulation");
  if (!state.playing) return;
  if (state.pane !== "stitches") setPane("stitches", { user: true });
  const total = activeOps().length / 4;
  if (state.upto >= total) state.upto = 0;
  const step = Math.max(20, Math.round(total / 400));
  const tick = () => {
    if (!state.playing || !state.preview) return;
    state.upto = Math.min(total, state.upto + step);
    $("scrub").value = state.upto;
    Render.now();
    if (state.upto >= total) { togglePlay(); return; }
    requestAnimationFrame(tick);
  };
  tick();
}

init().catch((e) => {
  document.body.classList.remove("booting");
  toast("Failed to load: " + e.message);
});
