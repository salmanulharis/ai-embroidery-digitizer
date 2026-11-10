"use strict";
/* AI settings (user-provided keys, kept in this browser only) and the smart assistant:
   "Suggest best settings", picture clean-up and "Check my stitches". Without an AI service
   the server's built-in algorithms answer, so every button works without a key.
   Uses helpers from app.js ($, api, json, busy, esc, toast, state, loadSource, extract,
   generate, PARAMS, CHECKS) - only inside functions, after app.js has loaded. */

const AI = (() => {
  const STORE = "embroidery.ai.v1";
  const CLEAN_LABELS = { redraw: "Redraw as clean artwork", remove_background: "Remove background only", enhance: "Sharpen & enhance" };
  const TYPE_LABELS = { logo: "Logo", text: "Lettering", illustration: "Illustration", photo: "Photo / colour fades", mixed: "Mixed" };
  const BG_LABELS = { auto: "Remove plain background", color: "Remove background colour everywhere", none: "Keep everything" };
  const DENOISE = ["Off", "Low", "Medium", "High"];
  let catalog = [];
  let cfg = load();
  const loaded = {}; // provider id -> {models, recommended}
  let picked = ""; // provider shown in the dialog

  // ------------------------------------------------------------------ storage
  function defaults() { return { remember: false, vision: "builtin", edit: "", providers: {} }; }
  function load() {
    for (const store of [() => localStorage, () => sessionStorage]) {
      try {
        const raw = store().getItem(STORE);
        if (raw) return { ...defaults(), ...JSON.parse(raw) };
      } catch (_) { /* storage blocked: keep settings in memory only */ }
    }
    return defaults();
  }
  function persist() {
    const raw = JSON.stringify(cfg);
    try {
      (cfg.remember ? localStorage : sessionStorage).setItem(STORE, raw);
      (cfg.remember ? sessionStorage : localStorage).removeItem(STORE);
    } catch (_) { /* in-memory only */ }
  }

  // ------------------------------------------------------------------ helpers
  const prov = (id) => catalog.find((p) => p.id === id);
  const can = (id, cap) => !!prov(id)?.capabilities.includes(cap);
  function ready(id) {
    const p = prov(id);
    if (!p) return false;
    const c = cfg.providers[id] || {};
    if (p.server_configured) return true;
    return p.needs_key ? !!c.api_key : !!(c.base_url || p.default_base_url);
  }
  /** The ``ai`` block for an API request, or null for the built-in algorithms. */
  function payload(cap) {
    const id = cap === "edit" ? cfg.edit : cfg.vision;
    if (!id || id === "builtin" || !ready(id) || !can(id, cap)) return null;
    const c = cfg.providers[id] || {};
    return {
      provider: id, api_key: c.api_key || null, base_url: c.base_url || null,
      vision_model: c.vision_model || null, edit_model: c.edit_model || null,
    };
  }
  function usingText(cap) {
    const p = payload(cap);
    if (!p) return cap === "edit" ? "" : "Using the built-in smart analysis (no AI service needed).";
    const model = cap === "edit" ? p.edit_model : p.vision_model;
    return `Using ${prov(p.provider).label}${model ? ` (${model})` : ""}.`;
  }

  // ------------------------------------------------------------------ settings dialog
  function fillUse() {
    const vision = catalog.filter((p) => can(p.id, "vision") && ready(p.id));
    const edit = catalog.filter((p) => can(p.id, "edit") && ready(p.id));
    if (cfg.vision !== "builtin" && !vision.some((p) => p.id === cfg.vision)) cfg.vision = "builtin";
    if (cfg.edit && !edit.some((p) => p.id === cfg.edit)) cfg.edit = "";
    $("aiUseVision").innerHTML = `<option value="builtin">Built-in smart analysis (no AI)</option>` +
      vision.map((p) => `<option value="${p.id}" ${p.id === cfg.vision ? "selected" : ""}>${esc(p.label)}</option>`).join("");
    $("aiUseEdit").innerHTML = `<option value="">Off – no picture clean-up</option>` +
      edit.map((p) => `<option value="${p.id}" ${p.id === cfg.edit ? "selected" : ""}>${esc(p.label)}</option>`).join("");
  }

  function modelOptions(id, cap, current) {
    const info = loaded[id];
    const rec = info?.recommended?.[cap];
    const auto = `<option value="">Automatic${rec ? ` (recommended: ${esc(rec)})` : ""}</option>`;
    const list = (info?.models || []).filter((m) => m.capabilities.includes(cap));
    const ids = new Set(list.map((m) => m.id));
    const extra = current && !ids.has(current) ? `<option value="${esc(current)}" selected>${esc(current)}</option>` : "";
    return auto + extra + list.map((m) => `<option value="${esc(m.id)}" ${m.id === current ? "selected" : ""}>${esc(m.label !== m.id ? `${m.label} – ${m.id}` : m.id)}</option>`).join("");
  }

  function showProvider(id) {
    picked = id;
    const p = prov(id), c = cfg.providers[id] || {};
    $("aiProviderHelp").textContent = (p.help || "") + (p.server_configured ? " Already set up on this server - a key here is optional." : "");
    $("aiKeyRow").classList.toggle("hidden", !p.needs_key && id !== "custom" && id !== "ollama");
    $("aiKey").value = c.api_key || "";
    $("aiKey").placeholder = p.needs_key && !p.server_configured ? "Paste your key" : "Optional";
    $("aiKeyLink").classList.toggle("hidden", !p.key_help_url);
    if (p.key_help_url) $("aiKeyLink").href = p.key_help_url;
    $("aiKeyLink").textContent = id === "ollama" ? "Find vision models" : "Get a key";
    $("aiUrlRow").classList.toggle("hidden", !p.supports_base_url);
    $("aiUrl").value = c.base_url || p.default_base_url || "";
    $("aiVisionModelRow").classList.toggle("hidden", !can(id, "vision"));
    $("aiEditModelRow").classList.toggle("hidden", !can(id, "edit"));
    $("aiVisionModel").innerHTML = modelOptions(id, "vision", c.vision_model);
    $("aiEditModel").innerHTML = modelOptions(id, "edit", c.edit_model);
    $("aiStatus").textContent = loaded[id] ? `Connected – ${loaded[id].models.length} suitable models.` : "";
    $("aiStatus").className = "small";
  }

  function readForm() {
    // keep what is typed for the provider shown, so "Use for" lists can include it
    const id = picked, p = prov(id);
    if (!p) return;
    const c = { ...(cfg.providers[id] || {}) };
    c.api_key = $("aiKey").value.trim();
    if (p.supports_base_url) c.base_url = $("aiUrl").value.trim() === (p.default_base_url || "") ? "" : $("aiUrl").value.trim();
    if (can(id, "vision")) c.vision_model = $("aiVisionModel").value;
    if (can(id, "edit")) c.edit_model = $("aiEditModel").value;
    Object.keys(c).forEach((k) => !c[k] && delete c[k]);
    if (Object.keys(c).length) cfg.providers[id] = c; else delete cfg.providers[id];
    // first service set up: use it straight away
    if (ready(id)) {
      if (cfg.vision === "builtin" && can(id, "vision")) cfg.vision = id;
      if (!cfg.edit && can(id, "edit")) cfg.edit = id;
    }
    fillUse();
  }

  async function loadModels() {
    readForm();
    const p = prov(picked), c = cfg.providers[picked] || {};
    if (p.needs_key && !p.server_configured && !c.api_key) return status("Paste your API key first.", true);
    status("Connecting…");
    try {
      const res = await api("/api/ai/models", json("POST", { ai: { provider: picked, api_key: c.api_key || null, base_url: c.base_url || null }, refresh: true }));
      loaded[picked] = res;
      showProvider(picked);
      const n = res.models.length;
      status(n ? `Connected – ${n} suitable model${n === 1 ? "" : "s"} found.` : "Connected, but no models that can work with pictures were found.", !n);
    } catch (e) {
      status(e.message.replace(/^AI service: /, ""), true);
    }
  }

  function status(text, bad = false) {
    $("aiStatus").textContent = text;
    $("aiStatus").className = "small " + (bad ? "bad" : "good");
  }

  function openDialog() {
    cfg = load();
    $("aiProviderPick").innerHTML = catalog.map((p) => `<option value="${p.id}">${esc(p.label)}${p.server_configured ? " ✓" : ""}</option>`).join("");
    const first = (cfg.vision !== "builtin" && cfg.vision) || cfg.edit || catalog[0]?.id;
    $("aiProviderPick").value = first;
    showProvider(first);
    $("aiRemember").checked = !!cfg.remember;
    fillUse();
    $("aiDialog").showModal();
  }

  function saveDialog() {
    readForm();
    cfg.vision = $("aiUseVision").value || "builtin";
    cfg.edit = $("aiUseEdit").value || "";
    cfg.remember = $("aiRemember").checked;
    persist();
    $("aiDialog").close();
    refresh();
    toast(cfg.vision === "builtin" && !cfg.edit ? "Saved – using the built-in smart processing." : "AI settings saved.", true);
  }

  function forget() {
    if (!picked) return;
    delete cfg.providers[picked];
    delete loaded[picked];
    if (cfg.vision === picked) cfg.vision = "builtin";
    if (cfg.edit === picked) cfg.edit = "";
    persist();
    showProvider(picked);
    fillUse();
    status("Removed from this browser.");
  }

  // ------------------------------------------------------------------ assistant (step 2)
  function refresh() {
    const vis = payload("vision"), edit = payload("edit");
    $("aiBadge").textContent = vis || edit ? prov((vis || edit).provider).label.split(" ")[0] : "Built-in";
    $("aiBadge").classList.toggle("on", !!(vis || edit));
    $("aiUsing").textContent = usingText("vision");
    $("aiReviewUsing").textContent = usingText("vision");
    $("btnAiClean").disabled = !edit;
    $("aiCleanNote").innerHTML = edit
      ? `${esc(usingText("edit"))} AI may change small details - always compare with your original.`
      : `Clean-up needs an AI service that can edit pictures. <a href="#" data-ai-open>Set one up</a> – or rely on the built-in preparation, which already removes noise and plain backgrounds.`;
  }

  function onProject(meta) {
    const raster = meta.kind === "raster";
    $("aiBox").classList.toggle("hidden", !raster);
    $("aiSuggestResult").innerHTML = "";
    $("btnAiRevert").classList.toggle("hidden", !meta.ai_cleaned);
    refresh();
  }

  async function suggest() {
    const width = +$("width_mm").value || 100;
    const r = await busy(payload("vision") ? "Asking the AI for advice…" : "Analysing your picture…",
      () => api(`/api/projects/${state.project.id}/ai/suggest`, json("POST", { ai: payload("vision"), width_mm: width })));
    const e = r.extract || {};
    const rows = [
      ["Thread colours", e.num_colors, $("num_colors").value || "Automatic"],
      ["Background", BG_LABELS[e.background], BG_LABELS[$("background").value]],
      ["Smallest detail", `${e.min_feature_mm} mm`, `${$("min_feature_mm").value} mm`],
      ["Noise clean-up", DENOISE[e.denoise], DENOISE[+$("denoise").value]],
    ];
    const cleanTip = r.cleanup && r.cleanup !== "none"
      ? `<p class="small">Tip: <b>${esc(CLEAN_LABELS[r.cleanup])}</b> could improve this picture.${payload("edit") ? ` <button type="button" class="ghost small" data-clean-tip="${r.cleanup}">Try it</button>` : ""}</p>` : "";
    $("aiSuggestResult").innerHTML = `
      ${r.ai_error ? `<div class="warnings"><div>${esc(r.ai_error)}</div></div>` : ""}
      <p class="small muted">${r.source === "ai" ? `Advice from ${esc(r.provider)} (${esc(r.model)})` : "Built-in analysis"} · looks like: <b>${esc(TYPE_LABELS[r.design_type] || r.design_type)}</b></p>
      <table class="ai-table"><tr><th>Setting</th><th>Suggested</th><th>Now</th></tr>
        ${rows.map(([n, s, c]) => `<tr class="${String(s) !== String(c) ? "diff" : ""}"><td>${n}</td><td><b>${esc(s)}</b></td><td>${esc(c)}</td></tr>`).join("")}
      </table>
      ${(r.notes || []).map((n) => `<p class="small">• ${esc(n)}</p>`).join("")}
      ${cleanTip}
      <button type="button" class="primary" data-apply-suggest>Apply &amp; update preview</button>`;
    $("aiSuggestResult").querySelector("[data-apply-suggest]").onclick = async () => {
      if (e.num_colors) $("num_colors").value = String(Math.min(15, e.num_colors));
      if (e.background) $("background").value = e.background;
      if (e.min_feature_mm != null) $("min_feature_mm").value = e.min_feature_mm;
      if (e.denoise != null) { $("denoise").value = e.denoise; $("denoise").setAttribute("aria-valuetext", DENOISE[e.denoise]); }
      $("aiSuggestResult").innerHTML = `<p class="small good">Suggested settings applied.</p>`;
      await extract();
    };
    $("aiSuggestResult").querySelector("[data-clean-tip]")?.addEventListener("click", (ev) => {
      $("aiCleanMode").value = ev.target.dataset.cleanTip;
      clean();
    });
  }

  async function clean() {
    const mode = $("aiCleanMode").value;
    const colors = $("num_colors").value ? +$("num_colors").value : null;
    const meta = await busy("The AI is cleaning up your picture… (can take about 30 seconds)",
      () => api(`/api/projects/${state.project.id}/ai/clean`, json("POST", { ai: payload("edit"), mode, colors })));
    state.project.ai_cleaned = meta.ai_cleaned;
    await loadSource();
    $("btnAiRevert").classList.remove("hidden");
    $("aiCleanNote").textContent = `${CLEAN_LABELS[mode]} done by ${meta.ai_cleaned.provider} (${meta.ai_cleaned.model}). Compare it carefully with your original.`;
    await extract();
  }

  async function revert() {
    await busy("Restoring your original…", () => api(`/api/projects/${state.project.id}/ai/revert`, { method: "POST" }));
    state.project.ai_cleaned = false;
    await loadSource();
    $("btnAiRevert").classList.add("hidden");
    refresh();
    await extract();
  }

  // ------------------------------------------------------------------ stitch check (step 3)
  function onStitched() {
    $("aiReviewResult").innerHTML = "";
    refresh();
  }

  function settingLabel(key) {
    const row = PARAMS.find(([k]) => k === key) || CHECKS.find(([k]) => k === key);
    return row ? row[1] : key;
  }

  async function review() {
    const r = await busy(payload("vision") ? "The AI is checking your stitches…" : "Checking your stitches…",
      () => api(`/api/projects/${state.project.id}/ai/review`, json("POST", { ai: payload("vision") })));
    const verdict = { good: ["good", "Looks good to sew"], ok: ["ok", "OK – a few things to consider"], needs_work: ["bad", "Needs attention before sewing"] }[r.overall] || ["ok", r.overall];
    const icon = { high: "⚠", medium: "●", low: "ℹ" };
    const form = $("stitchForm");
    const changes = Object.entries(r.changes || {});
    const now = (k) => { const el = form.elements[k]; return el ? (el.type === "checkbox" ? (el.checked ? "on" : "off") : el.value) : "–"; };
    const fmt = (v) => (v === true ? "on" : v === false ? "off" : v);
    $("aiReviewResult").innerHTML = `
      ${r.ai_error ? `<div class="warnings"><div>${esc(r.ai_error)}</div></div>` : ""}
      <p><span class="verdict ${verdict[0]}">${verdict[1]}</span>
        <span class="small muted">${r.source === "ai" ? `Checked by ${esc(r.provider)} (${esc(r.model)}) and built-in rules` : "Built-in check"}</span></p>
      ${r.issues.length ? `<ul class="issues">${r.issues.map((i) => `<li class="${i.severity}"><span aria-hidden="true">${icon[i.severity]}</span>
        <b>${esc(i.title)}</b>${i.by_ai ? ` <span class="badge">AI</span>` : ""} – <span class="small">${esc(i.detail)}</span></li>`).join("")}</ul>`
        : `<p class="small">No problems found.</p>`}
      ${changes.length ? `<table class="ai-table"><tr><th>Setting</th><th>Suggested</th><th>Now</th></tr>
        ${changes.map(([k, v]) => `<tr class="diff"><td>${esc(settingLabel(k))}</td><td><b>${esc(fmt(v))}</b></td><td>${esc(now(k))}</td></tr>`).join("")}</table>
        <button type="button" class="primary" data-apply-review>Apply suggestions &amp; recreate stitches</button>` : ""}`;
    $("aiReviewResult").querySelector("[data-apply-review]")?.addEventListener("click", async () => {
      for (const [k, v] of changes) {
        const el = form.elements[k];
        if (!el) continue;
        if (el.type === "checkbox") el.checked = !!v; else el.value = v;
      }
      await generate({ keepView: true });
      toast("Suggestions applied – stitches recreated.", true);
    });
  }

  // ------------------------------------------------------------------ init
  function init(config) {
    catalog = config.ai || [];
    $("btnAiSettings").onclick = openDialog;
    $("aiClose").onclick = () => { cfg = load(); $("aiDialog").close(); };
    $("aiDialog").addEventListener("cancel", () => { cfg = load(); });
    $("aiProviderPick").onchange = () => { readForm(); showProvider($("aiProviderPick").value); };
    for (const id of ["aiKey", "aiUrl", "aiVisionModel", "aiEditModel"]) $(id).addEventListener("change", readForm);
    $("aiKeyShow").onclick = () => {
      const show = $("aiKey").type === "password";
      $("aiKey").type = show ? "text" : "password";
      $("aiKeyShow").textContent = show ? "Hide" : "Show";
      $("aiKeyShow").setAttribute("aria-pressed", show);
    };
    $("aiLoadModels").onclick = loadModels;
    $("aiSave").onclick = saveDialog;
    $("aiForget").onclick = forget;
    $("btnAiSuggest").onclick = suggest;
    $("btnAiClean").onclick = clean;
    $("btnAiRevert").onclick = revert;
    $("btnAiReview").onclick = review;
    document.addEventListener("click", (ev) => {
      if (ev.target.closest("[data-ai-open]")) { ev.preventDefault(); openDialog(); }
    });
    refresh();
  }

  return { init, onProject, onStitched, payload };
})();
