"use strict";

const $ = (id) => document.getElementById(id);
const state = { config: null, project: null, design: null, preview: null, view: { scale: 1, x: 0, y: 0 }, mode: "real", variant: "optimized", upto: 0, playing: false };
// stitches currently shown: optimised (default) or the original, unreduced version
const activeOps = () => (state.variant === "original" && state.preview.original_ops) || state.preview.ops;

// ------------------------------------------------------------------ helpers
async function api(path, opts = {}) {
  const res = await fetch(path, opts);
  if (!res.ok) {
    let msg = res.statusText;
    try { msg = (await res.json()).detail || msg; } catch (_) {}
    throw new Error(msg);
  }
  return res.headers.get("content-type")?.includes("json") ? res.json() : res;
}
const json = (method, body) => ({ method, headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });

function toast(msg, info = false) {
  const t = $("toast");
  t.textContent = msg;
  t.className = "toast" + (info ? " info" : "");
  clearTimeout(toast.timer);
  toast.timer = setTimeout(() => t.classList.add("hidden"), info ? 2500 : 6000);
}
async function busy(text, fn) {
  $("busyText").textContent = text;
  $("busy").classList.remove("hidden");
  Tip.hide();
  try { return await fn(); } catch (e) { toast(e.message); throw e; } finally { $("busy").classList.add("hidden"); }
}
function showStep(n) {
  ["step1", "step2", "step3"].forEach((id, i) => $(id).classList.toggle("hidden", (n >= 3 ? 3 : n) !== i + 1));
  document.querySelectorAll("#steps li").forEach((li) => {
    const s = +li.dataset.step;
    li.classList.toggle("active", s === n);
    li.classList.toggle("done", s < n);
  });
}
const esc = (s) => String(s).replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));

// ------------------------------------------------------------------ init
const AI_NAMES = { openai: "OpenAI", gemini: "Google Gemini", openrouter: "OpenRouter", ollama: "Ollama (on this computer)" };
const DENOISE_LEVELS = ["Off", "Low", "Medium", "High"];

async function init() {
  Tip.init();
  Shapes.init();
  state.config = await api("/api/config");
  const hoop = $("hoop");
  hoop.innerHTML = '<option value="">Custom size</option>' + state.config.hoops.map((h, i) => `<option value="${i}">${esc(h.name)}</option>`).join("");
  hoop.onchange = () => {
    const h = state.config.hoops[hoop.value];
    if (h) $("width_mm").value = Math.round(Math.min(h.width, h.height * aspect()) * 0.9);
  };
  const nc = $("num_colors");
  for (let i = 1; i <= 15; i++) nc.insertAdjacentHTML("beforeend", `<option value="${i}">${i}</option>`);
  $("denoise").oninput = () => $("denoise").setAttribute("aria-valuetext", DENOISE_LEVELS[+$("denoise").value]);

  const ai = state.config.ai.filter((p) => p.available);
  $("aiProvider").innerHTML = ai.map((p) => `<option value="${p.name}" data-clean="${p.clean}">${AI_NAMES[p.name] || p.name}${p.clean ? "" : " – advice only"}</option>`).join("");
  $("aiNone").textContent = ai.length ? "" : "The optional AI helper is switched off. To use it, add an OpenAI, Gemini or OpenRouter key (or a local Ollama picture model) to the .env file.";
  buildStitchForm();
  bindUpload();
  bindCanvas();
}

function aspect() {
  const img = $("srcImg");
  return img.naturalWidth && img.naturalHeight ? img.naturalWidth / img.naturalHeight : 1;
}

// ------------------------------------------------------------------ step 1: upload
function bindUpload() {
  const drop = $("drop"), input = $("file");
  input.onchange = () => input.files[0] && upload(input.files[0]);
  ["dragenter", "dragover"].forEach((e) => drop.addEventListener(e, (ev) => { ev.preventDefault(); drop.classList.add("over"); }));
  ["dragleave", "drop"].forEach((e) => drop.addEventListener(e, (ev) => { ev.preventDefault(); drop.classList.remove("over"); }));
  drop.addEventListener("drop", (ev) => ev.dataTransfer.files[0] && upload(ev.dataTransfer.files[0]));
}

async function upload(file) {
  const fd = new FormData();
  fd.append("file", file);
  const meta = await busy("Uploading…", () => api("/api/projects", { method: "POST", body: fd }));
  state.project = meta;
  state.preview = null;
  Shapes.reset();
  const raster = meta.kind === "raster";
  $("rasterOpts").classList.toggle("hidden", !raster);
  $("vectorOpts").classList.toggle("hidden", raster);
  $("aiBox").classList.toggle("hidden", !raster || !$("aiProvider").options.length);
  $("fileInfo").textContent = `${meta.filename} · ${raster ? `picture, ${meta.info.width_px} × ${meta.info.height_px} pixels` : "artwork file"}`;
  $("layers").innerHTML = "";
  $("svgBox").innerHTML = "";
  $("btnApprove").disabled = true;
  $("aiResult").textContent = "";
  await loadSource();
  showStep(2);
  await extract();
}

async function loadSource() {
  await new Promise((resolve) => {
    const img = $("srcImg");
    img.onload = img.onerror = resolve;
    img.src = `/api/projects/${state.project.id}/source?t=${Date.now()}`;
  });
}

// ------------------------------------------------------------------ step 2: prepare design
function extractOptions() {
  const o = { width_mm: +$("width_mm").value, num_colors: $("num_colors").value ? +$("num_colors").value : null };
  if (state.project.kind === "raster") {
    Object.assign(o, {
      background: $("background").value,
      min_feature_mm: +$("min_feature_mm").value,
      min_area_mm2: +$("min_area_mm2").value,
      denoise: +$("denoise").value,
    });
  } else {
    o.knockout = $("knockout").checked;
  }
  return o;
}

async function extract() {
  const res = await busy("Preparing your design…", () => api(`/api/projects/${state.project.id}/extract`, json("POST", { options: extractOptions() })));
  renderDesign(res);
}

function renderDesign(res) {
  state.design = res.design;
  $("svgBox").innerHTML = res.svg.replace(/<\?xml[^>]*>/, "");
  $("warnings2").innerHTML = res.design.warnings.map((w) => `<div>${esc(w)}</div>`).join("");
  $("dlSvg").href = `/api/projects/${state.project.id}/export/svg`;
  renderLayers();
  $("btnApprove").disabled = !res.design.layers.some((l) => l.enabled);
  // dim disabled layers in the vector preview
  res.design.layers.forEach((l) => {
    const g = $("svgBox").querySelector(`#layer-${l.index}`);
    if (g) {
      g.style.opacity = l.enabled ? 1 : 0.12;
      g.classList.toggle("layer-off", !l.enabled);
    }
  });
  $("btnUndo").disabled = !res.can_undo;
  Shapes.afterRender(res);
}

const STITCH_STYLES = [["auto", "Automatic"], ["fill", "Fill (large areas)"], ["satin", "Satin (smooth, narrow)"], ["running", "Outline only"]];

function renderLayers() {
  const L = state.design.layers;
  const th = (label, key) => `<th scope="col">${label} ${Tip.icon(key)}</th>`;
  $("layers").innerHTML =
    `<tr>${th("Stitch", "layers_sew")}${th("Thread", "layers_thread")}${th("Name", "layers_name")}${th("Area", "layers_area")}` +
    `${th("Stitch style", "layers_stitch")}${th("Direction", "layers_angle")}${th("Shapes", "layers_shapes")}${th("Order", "layers_order")}${th("Merge into", "layers_merge")}</tr>` +
    L.map((l, i) => {
      const n = esc(l.name);
      return `
      <tr class="${l.enabled ? "" : "off"}" data-index="${l.index}">
        <td><input type="checkbox" data-k="enabled" ${l.enabled ? "checked" : ""} aria-label="Stitch ${n}"></td>
        <td><input type="color" data-k="hex" value="${l.hex}" aria-label="Thread colour for ${n}"></td>
        <td><input data-k="name" value="${n}" size="10" aria-label="Name for ${n}"></td>
        <td class="small muted">${l.area_mm2} mm²</td>
        <td><select data-k="stitch_type" aria-label="Stitch style for ${n}">${STITCH_STYLES.map(([v, label]) => `<option value="${v}" ${v === l.stitch_type ? "selected" : ""}>${label}</option>`).join("")}</select></td>
        <td><input type="number" data-k="angle" placeholder="auto" min="0" max="180" value="${l.angle ?? ""}" aria-label="Stitch direction in degrees for ${n}"></td>
        <td class="small nowrap">${l.objects}${l.customised ? ` <span class="muted">(${l.customised} own)</span>` : ""}
          <button type="button" class="ghost small" data-select-layer="${l.index}" ${l.enabled ? "" : "disabled"} aria-label="Select all shapes of ${n}">Select</button></td>
        <td><button data-move="-1" ${i === 0 ? "disabled" : ""} aria-label="Sew ${n} earlier">↑</button><button data-move="1" ${i === L.length - 1 ? "disabled" : ""} aria-label="Sew ${n} later">↓</button></td>
        <td><select data-merge aria-label="Merge ${n} into another colour"><option value="">–</option>${L.filter((o) => o.index !== l.index).map((o) => `<option value="${o.index}">${esc(o.name)}</option>`).join("")}</select></td>
      </tr>`;
    }).join("");
}

$("layers").addEventListener("change", async (ev) => {
  const tr = ev.target.closest("tr");
  if (!tr) return;
  const idx = +tr.dataset.index;
  if (ev.target.dataset.merge !== undefined) {
    if (!ev.target.value) return;
    const res = await busy("Combining colours…", () => api(`/api/projects/${state.project.id}/layers/merge`, json("POST", { source: idx, target: +ev.target.value })));
    return renderDesign(res);
  }
  const layer = state.design.layers.find((l) => l.index === idx);
  const k = ev.target.dataset.k;
  layer[k] = ev.target.type === "checkbox" ? ev.target.checked : k === "angle" ? (ev.target.value === "" ? null : +ev.target.value) : ev.target.value;
  await saveLayers();
});
$("layers").addEventListener("click", async (ev) => {
  const pick = ev.target.closest("[data-select-layer]");
  if (pick) return Shapes.selectLayer(+pick.dataset.selectLayer);
  const mv = ev.target.dataset.move;
  if (!mv) return;
  const L = state.design.layers;
  const i = L.findIndex((l) => l.index === +ev.target.closest("tr").dataset.index);
  const j = i + +mv;
  [L[i], L[j]] = [L[j], L[i]];
  await saveLayers();
});
async function saveLayers() {
  const layers = state.design.layers.map(({ index, hex, name, enabled, stitch_type, angle }) => ({ index, hex, name, enabled, stitch_type, angle }));
  const res = await api(`/api/projects/${state.project.id}/layers`, json("PUT", { layers })).catch((e) => toast(e.message));
  if (res) renderDesign(res);
}

async function undoLast() {
  if ($("btnUndo").disabled || !state.project) return;
  const res = await busy("Undoing…", () => api(`/api/projects/${state.project.id}/undo`, { method: "POST" }));
  renderDesign(res);
  if (!$("step3").classList.contains("hidden")) await generate({ keepView: true });
}
$("btnUndo").onclick = undoLast;
document.addEventListener("keydown", (ev) => {
  // Ctrl/Cmd+Z on the design screens, but never while typing in a field
  if ((ev.ctrlKey || ev.metaKey) && !ev.shiftKey && ev.key.toLowerCase() === "z" && !ev.target.matches("input, select, textarea")
      && state.project && $("step1").classList.contains("hidden")) {
    ev.preventDefault();
    undoLast();
  }
});

$("btnExtract").onclick = extract;
$("btnApprove").onclick = async () => { showStep(3); await generate(); };
$("btnBack").onclick = () => showStep(2);

// AI
$("btnAiClean").onclick = async () => {
  const provider = $("aiProvider").value;
  const opt = $("aiProvider").selectedOptions[0];
  if (opt && opt.dataset.clean !== "true") return toast("This AI service can only give advice. Pick another service to redraw pictures.");
  const colors = $("num_colors").value ? +$("num_colors").value : 6;
  await busy("AI is redrawing your picture… (can take about 30 seconds)", () => api(`/api/projects/${state.project.id}/ai/clean`, json("POST", { provider, colors })));
  await loadSource();
  $("aiResult").textContent = "Your picture was redrawn by AI. Compare it carefully with your original before continuing.";
  await extract();
};
$("btnAiRevert").onclick = async () => {
  await busy("Restoring your original…", () => api(`/api/projects/${state.project.id}/ai/revert`, { method: "POST" }));
  await loadSource();
  $("aiResult").textContent = "";
  await extract();
};
$("btnAiAnalyze").onclick = async () => {
  const provider = $("aiProvider").value;
  const r = await busy("Getting advice…", () => api(`/api/projects/${state.project.id}/ai/analyze`, json("POST", { provider })));
  if (r.suggested_colors) $("num_colors").value = Math.min(15, Math.max(1, r.suggested_colors));
  $("aiResult").innerHTML = `<b>${esc(r.design_type || "")}</b> · suggested colours: ${esc(r.suggested_colors ?? "?")}` +
    (r.needs_ai_cleanup ? " · “Redraw cleanly” is recommended" : "") + `<br>${esc(r.notes || "")}`;
};

// ------------------------------------------------------------------ step 3: stitch settings
// [parameter name (sent to the server), plain-language label, min, max, step]
const PARAMS = [
  ["fill_spacing_mm", "Fill thickness – gap between rows (mm)", 0.25, 1.0, 0.05],
  ["fill_stitch_length_mm", "Fill stitch length (mm)", 2, 7, 0.1],
  ["fill_angle", "Fill direction (°)", 0, 180, 5],
  ["satin_spacing_mm", "Satin thickness – gap between stitches (mm)", 0.25, 1.0, 0.05],
  ["satin_max_width_mm", "Widest satin shape (mm)", 3, 12, 0.5],
  ["pull_compensation_mm", "Shrink compensation (mm)", 0, 0.8, 0.05],
  ["running_stitch_length_mm", "Outline stitch length (mm)", 1, 5, 0.1],
  ["underlay_spacing_mm", "Foundation spacing (mm)", 1.5, 5, 0.1],
  ["trim_threshold_mm", "Cut thread for moves longer than (mm)", 1, 20, 0.5],
  ["min_object_area_mm2", "Skip shapes smaller than (mm²)", 0, 10, 0.1],
];
const CHECKS = [
  ["optimize", "Smart stitch reduction"],
  ["underlay", "Foundation stitches (recommended)"],
  ["vary_fill_angle", "Change fill direction for each colour"],
  ["tie_stitches", "Lock stitches at every thread cut"],
  ["triple_run", "Bold outlines (sew lines 3 times)"],
];
function buildStitchForm() {
  const d = state.config.stitch_defaults;
  const checkbox = ([k, label]) => {
    const on = k === "triple_run" ? d.running_repeats >= 3 : !!d[k];
    return `<label class="check"><input type="checkbox" name="${k}" ${on ? "checked" : ""}> ${label} ${Tip.icon(k)}</label>`;
  };
  $("stitchForm").innerHTML =
    CHECKS.slice(0, 1).map(checkbox).join("") +
    PARAMS.map(([k, label, min, max, step]) => `<label><span class="lbl">${label} ${Tip.icon(k)}</span><input type="number" name="${k}" min="${min}" max="${max}" step="${step}" value="${d[k]}"></label>`).join("") +
    CHECKS.slice(1).map(checkbox).join("");
}
function stitchParams() {
  const f = $("stitchForm"), p = {};
  PARAMS.forEach(([k]) => (p[k] = +f.elements[k].value));
  CHECKS.forEach(([k]) => (p[k] = f.elements[k].checked));
  p.running_repeats = p.triple_run ? 3 : 1;
  delete p.triple_run;
  return p;
}
$("btnStitch").onclick = () => generate();

async function generate(opts = {}) {
  const prev = await busy("Creating stitches…", () => api(`/api/projects/${state.project.id}/stitch`, json("POST", { params: stitchParams() })));
  state.preview = prev;
  Shapes.setStitchShapes(prev.shapes);
  const canCompare = !!(prev.comparison && !prev.comparison.reverted && prev.original_ops);
  $("variant").classList.toggle("hidden", !canCompare);
  $("variantHelp")?.classList.toggle("hidden", !canCompare);
  setVariant("optimized", false);
  resetScrub();
  renderOptimizeInfo();
  renderStats();
  renderExports();
  showStep(4);
  Shapes.refresh();
  if (opts.keepView) draw(); else fit(); // keep the zoom while editing shapes
}

function resetScrub() {
  state.upto = activeOps().length / 4;
  $("scrub").max = state.upto;
  $("scrub").value = state.upto;
}

function setVariant(v, redraw = true) {
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
    `Use <i>Optimised</i> / <i>Original</i> above the preview to compare.</span>`;
}

function renderStats() {
  const c = state.preview.comparison;
  const s = c && !c.reverted ? (state.variant === "original" ? c.original : c.optimized) : state.preview.stats;
  const items = [
    [s.stitches.toLocaleString(), "stitches", "stats_stitches"], [`${s.width_mm} × ${s.height_mm}`, "size (mm)", "stats_size"],
    [s.colors, "colours", "stats_colours"], [s.trims, "thread cuts", "stats_trims"], [s.color_changes, "colour changes", "stats_changes"],
    [`${s.est_minutes} min`, "sewing time (est.)", "stats_time"], [`${s.thread_m} m`, "thread used", "stats_thread"],
  ];
  $("stats").innerHTML = items.map(([v, l, k]) => `<div><b>${v}</b><span>${l} ${Tip.icon(k)}</span></div>`).join("");
  $("warnings3").innerHTML = state.preview.warnings.map((w) => `<div>${esc(w)}</div>`).join("");
  $("sequenceTitle").classList.toggle("hidden", !state.preview.colors.length);
  $("sequence").innerHTML = state.preview.colors.map((c, i) => `<div><i style="background:${c.hex}"></i>${i + 1}. ${esc(c.name)} <span class="muted">${c.hex}</span></div>`).join("");
}

function renderExports() {
  const id = state.project.id;
  const fmts = state.config.formats;
  const items = Object.entries(fmts).map(([k, f]) => [k, `${f.label} (.${k})`, f.note, k === "dst"]);
  items.push(["svg", "Artwork (SVG)", "The cleaned-up design as a vector file – open in Wilcom, Illustrator or Inkscape", false]);
  items.push(["png", "Preview picture (PNG)", "A realistic picture of the embroidery – handy for customer approval", false]);
  items.push(["txt", "Worksheet (TXT)", "Thread order, stitch count and time for the machine operator", false]);
  items.push(["zip", "Everything (.zip)", "DST + EXP + PES + artwork + preview + worksheet in one download", false]);
  $("exports").innerHTML = items.map(([k, label, note, rec]) =>
    `<a href="/api/projects/${id}/export/${k}" class="${rec ? "rec" : ""}"><b>${label}${rec ? " ★ recommended" : ""}</b><span>${esc(note)}</span></a>`).join("");
  $("exportBox").classList.remove("hidden");
}

// ------------------------------------------------------------------ preview canvas
const KIND_COLORS = ["#2d6cdf", "#d6336c", "#2f9e44", "#f08c00", "#868e96", "#ae3ec9", "#000"]; // fill satin running underlay travel tie

function bindCanvas() {
  const c = $("canvas");
  let drag = null;
  c.addEventListener("wheel", (ev) => {
    ev.preventDefault();
    const r = c.getBoundingClientRect();
    const mx = ev.clientX - r.left, my = ev.clientY - r.top;
    const f = ev.deltaY < 0 ? 1.15 : 1 / 1.15;
    const v = state.view;
    v.x = mx - (mx - v.x) * f;
    v.y = my - (my - v.y) * f;
    v.scale *= f;
    draw();
  }, { passive: false });
  c.addEventListener("pointerdown", (ev) => {
    drag = { x: ev.clientX, y: ev.clientY, sx: ev.clientX, sy: ev.clientY };
    c.setPointerCapture(ev.pointerId);
  });
  c.addEventListener("pointermove", (ev) => {
    if (!drag) return;
    state.view.x += ev.clientX - drag.x;
    state.view.y += ev.clientY - drag.y;
    drag = { ...drag, x: ev.clientX, y: ev.clientY };
    draw();
  });
  c.addEventListener("pointerup", (ev) => {
    const d = drag;
    drag = null;
    if (!d || !state.preview || Math.hypot(ev.clientX - d.sx, ev.clientY - d.sy) >= 4) return;
    // a click (not a drag): select the shape under the pointer
    const r = c.getBoundingClientRect(), v = state.view;
    const xMm = (ev.clientX - r.left - v.x) / v.scale / 10, yMm = (ev.clientY - r.top - v.y) / v.scale / 10;
    Shapes.click3(xMm, yMm, ev.shiftKey || ev.ctrlKey || ev.metaKey);
  });
  c.addEventListener("pointercancel", () => (drag = null));
  window.addEventListener("resize", () => state.preview && draw());
  $("btnFit").onclick = fit;
  $("showFabric").onchange = () => { $("canvas").parentElement.classList.toggle("dark", $("showFabric").checked); draw(); };
  $("viewMode").addEventListener("click", (ev) => {
    const m = ev.target.dataset.mode;
    if (!m) return;
    state.mode = m;
    document.querySelectorAll("#viewMode button").forEach((b) => {
      b.classList.toggle("on", b.dataset.mode === m);
      b.setAttribute("aria-pressed", b.dataset.mode === m);
    });
    draw();
  });
  $("variant").addEventListener("click", (ev) => {
    const v = ev.target.dataset.variant;
    if (v && v !== state.variant) setVariant(v);
  });
  $("scrub").oninput = () => { state.upto = +$("scrub").value; draw(); };
  $("btnPlay").onclick = togglePlay;
}

function fit() {
  const c = $("canvas"), p = state.preview;
  if (!p) return;
  const w = c.clientWidth, h = c.clientHeight;
  const ops = activeOps();
  let minx = Infinity, miny = Infinity, maxx = -Infinity, maxy = -Infinity;
  for (let i = 0; i < ops.length; i += 4) {
    if (ops[i] !== 0) continue;
    minx = Math.min(minx, ops[i + 1]); maxx = Math.max(maxx, ops[i + 1]);
    miny = Math.min(miny, ops[i + 2]); maxy = Math.max(maxy, ops[i + 2]);
  }
  const s = Math.min(w / (maxx - minx + 40), h / (maxy - miny + 40));
  state.view = { scale: s, x: (w - (maxx + minx) * s) / 2, y: (h - (maxy + miny) * s) / 2 };
  draw();
}

function shade(hex, f) {
  const n = parseInt(hex.slice(1), 16);
  const ch = [(n >> 16) & 255, (n >> 8) & 255, n & 255].map((v) => Math.max(0, Math.min(255, Math.round(f >= 1 ? v + (255 - v) * (f - 1) : v * f))));
  return `rgb(${ch.join(",")})`;
}

function draw() {
  const c = $("canvas"), p = state.preview;
  if (!p) return;
  const dpr = window.devicePixelRatio || 1;
  c.width = c.clientWidth * dpr;
  c.height = c.clientHeight * dpr;
  const ctx = c.getContext("2d");
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  ctx.clearRect(0, 0, c.clientWidth, c.clientHeight);
  const v = state.view, ops = activeOps(), mode = state.mode;
  const X = (u) => v.x + u * v.scale, Y = (u) => v.y + u * v.scale;
  const threadW = Math.max(0.6, 3.2 * v.scale); // 0.32 mm thread (units are 0.1 mm)
  ctx.lineCap = "round";
  let color = 0, px = null, py = null, n = 0;
  const upto = Math.min(state.upto, ops.length / 4);
  const jumps = [], trims = [];

  for (let i = 0; i < upto * 4; i += 4) {
    const cmd = ops[i], x = ops[i + 1], y = ops[i + 2], kind = ops[i + 3];
    if (cmd === 3) { color++; continue; }
    if (cmd === 2) { if (px !== null) trims.push([px, py]); continue; }
    if (cmd === 1) { if (px !== null) jumps.push([px, py, x, y]); px = x; py = y; continue; }
    if (px === null) { px = x; py = y; continue; }
    const hex = p.colors[Math.min(color, p.colors.length - 1)].hex;
    if (mode === "real") {
      ctx.lineWidth = threadW;
      ctx.strokeStyle = shade(hex, 0.62);
      ctx.beginPath(); ctx.moveTo(X(px), Y(py)); ctx.lineTo(X(x), Y(y)); ctx.stroke();
      ctx.lineWidth = threadW * 0.7;
      ctx.strokeStyle = hex;
      ctx.stroke();
      if (threadW > 3) { ctx.lineWidth = threadW * 0.22; ctx.strokeStyle = shade(hex, 1.35); ctx.stroke(); }
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
  if (mode !== "real") {
    ctx.setLineDash([4, 4]); ctx.strokeStyle = "#e03131"; ctx.lineWidth = 1;
    jumps.forEach(([a, b, c2, d]) => { ctx.beginPath(); ctx.moveTo(X(a), Y(b)); ctx.lineTo(X(c2), Y(d)); ctx.stroke(); });
    ctx.setLineDash([]);
    ctx.fillStyle = "#e03131";
    trims.forEach(([a, b]) => { ctx.beginPath(); ctx.arc(X(a), Y(b), 3, 0, 7); ctx.fill(); });
  }
  if (px !== null && upto < ops.length / 4) {
    ctx.strokeStyle = "#e03131"; ctx.lineWidth = 2;
    ctx.beginPath(); ctx.arc(X(px), Y(py), 6, 0, 7); ctx.stroke();
  }
  Shapes.drawSelection3(ctx, v, dpr);
  $("legend").innerHTML = mode === "type"
    ? ["fill", "satin", "outline", "foundation", "moving", "lock"].map((k, i) => `<span style="background:${KIND_COLORS[i]}"></span>${k}`).join("") + `<span style="background:#e03131"></span>jump / thread cut`
    : mode === "direction" ? "Arrows show which way the needle sews · red dashes = jumps · red dots = thread cuts" : "";
  $("legend").classList.toggle("hidden", mode === "real");
  $("scrubInfo").textContent = `${n.toLocaleString()} stitches sewn · colour ${Math.min(color + 1, p.colors.length)} of ${p.colors.length}`;
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
  state.playing = !state.playing;
  $("btnPlay").innerHTML = state.playing ? "&#10074;&#10074;" : "&#9654;";
  $("btnPlay").setAttribute("aria-label", state.playing ? "Pause sewing simulation" : "Play sewing simulation");
  if (!state.playing) return;
  const total = activeOps().length / 4;
  if (state.upto >= total) state.upto = 0;
  const step = Math.max(20, Math.round(total / 400));
  const tick = () => {
    if (!state.playing) return;
    state.upto = Math.min(total, state.upto + step);
    $("scrub").value = state.upto;
    draw();
    if (state.upto >= total) { togglePlay(); return; }
    requestAnimationFrame(tick);
  };
  tick();
}

init().catch((e) => toast("Failed to load: " + e.message));
