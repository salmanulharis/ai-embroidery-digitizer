"use strict";
/* Settings presets in the editor: the picker at the top of the settings panel (built-in
   presets by plan tier, the account's own presets, back to the app defaults), "Save as
   preset…" and the Manage dialog. The server validates and limits everything
   (presets.py, entitlements.py); this file only shows what it allows.

   Applying a preset fills in the settings it holds (a fabric first, then the preset's own
   values), records one undo step for the stitch settings (a changed picture records its own
   when the design is prepared again), and makes the preset the baseline: the "changed"
   marks then show what differs from the preset. Uses helpers and state from app.js inside
   functions, after app.js has loaded. */

const Presets = (() => {
  let builtins = [], keys = {}, mine = [], status = { count: 0, limit: 0, feature: false, can_save: false };
  let applied = null; // {name, values: {"s:key" | "e:id": value}} of the last applied preset
  let editing = null; // preset being edited in the save dialog (null = new)
  let pendingClash = null; // {id, body} after a 409 name_taken
  let confirmDelete = null;
  const IMAGE_IDS = ["background", "text_mode", "brightness", "contrast", "sharpen", "denoise", "min_feature_mm", "min_area_mm2"];
  const SIZE_IDS = ["width_mm", "num_colors"];
  const SECTION_LABEL = { stitch: "stitch", fabric: "fabric & thread", image: "picture", size: "size" };
  const KIND_LABEL = { stitch: "Stitches", image: "Picture", size: "Size" };
  const TIER_LABEL = { basic: "Basic", premium: "Premium" };
  const isRaster = () => state.project?.kind === "raster";
  const plural = (n, w) => `${n} ${w}${n === 1 ? "" : "s"}`;
  const countText = () => (status.limit == null ? plural(status.count, "preset") : `${status.count} of ${status.limit}`);
  const countPhrase = () => (status.limit == null ? plural(status.count, "preset") : `${status.count} of ${status.limit} presets`);

  // ------------------------------------------------------------------ data
  async function load() {
    try {
      const [b, m] = await Promise.all([api("/api/presets"), api("/api/me/presets")]);
      builtins = b.presets;
      keys = b.keys;
      mine = m.presets;
      status = { count: m.count, limit: m.limit, feature: m.feature, can_save: m.can_save };
    } catch (_) {
      return; // presets are a convenience: the editor works without them
    }
    renderPicker();
    if (asked && !suggested) offer(asked);
    if ($("presetsDialog").open) renderManage();
  }
  const find = (value) => {
    if (!value) return null;
    const [t, id] = [value.slice(0, 2), value.slice(2)];
    if (t === "b:") return builtins.find((p) => p.id === id) && { kind: "builtin", p: builtins.find((p) => p.id === id) };
    if (t === "u:") return mine.find((p) => String(p.id) === id) && { kind: "mine", p: mine.find((p) => String(p.id) === id) };
    return null;
  };
  const nameOf = (p) => p.label || p.name;

  // ------------------------------------------------------------------ picker
  function renderPicker() {
    const sel = $("presetPick"), keep = sel.value;
    const opt = (v, label, extra = "") => `<option value="${esc(v)}" ${extra}>${esc(label)}</option>`;
    sel.innerHTML = opt("", "Choose a preset…") +
      `<optgroup label="Built-in">${builtins.map((p) => opt(`b:${p.id}`, p.locked ? `${p.label} – ${TIER_LABEL[p.tier] || "plan"} 🔒` : p.id === suggested?.id ? `${p.label} · suggested` : p.label)).join("")}</optgroup>` +
      (mine.length || status.feature ? `<optgroup label="My presets · ${countText()}">${mine.map((p) => opt(`u:${p.id}`, p.name)).join("") ||
        opt("@none", "None saved yet", "disabled")}</optgroup>` : "") +
      `<optgroup label="Other">${opt("@defaults", "App defaults (stitch settings)")}</optgroup>`;
    sel.value = [...sel.options].some((o) => o.value === keep) ? keep : "";
    showChoice();
    const save = $("btnPresetSave");
    save.textContent = status.feature ? "Save as preset…" : "Save as preset… 🔒";
    save.disabled = status.feature && !status.can_save;
    save.title = !status.feature ? "Saving your own presets is not included in your plan"
      : status.can_save ? "" : `You have ${countPhrase()}. Delete one in Manage… to save a new one.`;
    sync();
  }
  function showChoice() {
    const v = $("presetPick").value, hint = $("presetHint");
    const f = find(v);
    let text = "", can = !!state.project;
    if (v === "@defaults") text = "Puts every stitch setting back to the app's standard values. Picture settings stay as they are.";
    else if (f?.p.locked) {
      hint.innerHTML = `${lockBadge(f.p)} Not included in your plan.`;
      $("btnPresetReapply").disabled = true;
      return;
    } else if (f) {
      const kinds = (f.p.kinds || []).map((k) => KIND_LABEL[k]).filter(Boolean).join(" + ");
      text = [f.p.description, kinds && `Sets: ${kinds}.`].filter(Boolean).join(" ");
    } else can = false;
    if (!v) text = !status.can_save && status.feature ? `You have ${countPhrase()}.` : "";
    hint.textContent = text;
    $("btnPresetReapply").disabled = !can;
  }

  /** The form's state, so a changed setting after applying can be detected. */
  function snapshot() {
    const v = {};
    Object.entries(stitchValues()).forEach(([k, x]) => (v[`s:${k}`] = String(x)));
    Object.entries(extractValues()).forEach(([k, x]) => (v[`e:${k}`] = String(x)));
    return v;
  }
  /** Called by markModified(): shows "Changed" while the settings differ from the applied preset. */
  function sync() {
    const badge = $("presetModified");
    if (!badge) return;
    let changed = false;
    if (applied) {
      const now = snapshot();
      changed = Object.entries(applied.values).some(([k, v]) => now[k] !== undefined && now[k] !== v);
    }
    badge.classList.toggle("hidden", !changed);
  }

  // ------------------------------------------------------------------ locked presets: badge + popover
  const lockText = (p) => `${p.label} is included in ${p.plans?.length ? `the ${p.plans.join(" and ")} plan${p.plans.length === 1 ? "" : "s"}`
    : `a plan with ${p.needs.join(" and ")}`}. Ask your administrator if you need it.`;
  const lockBadge = (p) => `<button type="button" class="lock-badge" data-lock="${esc(p.id)}" aria-expanded="false"
    aria-controls="presetLockPop" aria-label="${esc(p.label)}: not in your plan. Details">🔒 ${esc(TIER_LABEL[p.tier] || "Plan")}</button>`;
  function lockPop(host) {
    let pop = $("presetLockPop");
    if (!pop) {
      pop = document.createElement("div");
      pop.id = "presetLockPop";
      pop.className = "lock-pop";
      pop.setAttribute("role", "note");
      if ("popover" in HTMLElement.prototype) {
        pop.popover = "auto"; // Escape and a click elsewhere close it
        pop.addEventListener("toggle", (e) => {
          if (e.newState === "closed") document.querySelectorAll('[data-lock][aria-expanded="true"]').forEach((x) => x.setAttribute("aria-expanded", "false"));
        });
      }
    }
    if (pop.parentElement !== host) host.append(pop); // inside an open modal dialog, or it would be inert
    return pop;
  }
  function hidePop() {
    const pop = $("presetLockPop");
    if (pop?.matches?.(":popover-open")) pop.hidePopover();
  }
  function showLock(btn) {
    const p = builtins.find((x) => x.id === btn.dataset.lock);
    if (!p) return;
    const pop = lockPop(btn.closest("dialog") || document.body);
    if (!pop.showPopover) return toast(lockText(p), true); // old browsers
    if (pop.matches(":popover-open") && btn.getAttribute("aria-expanded") === "true") return hidePop();
    hidePop();
    pop.innerHTML = `<b>🔒 ${esc(p.label)}</b><p>${esc(lockText(p))}</p>`;
    pop.showPopover();
    btn.setAttribute("aria-expanded", "true");
    const r = btn.getBoundingClientRect(), m = 8;
    const w = pop.offsetWidth, h = pop.offsetHeight;
    pop.style.left = `${Math.min(Math.max(m, r.left), window.innerWidth - w - m)}px`;
    pop.style.top = `${r.bottom + h + m > window.innerHeight ? Math.max(m, r.top - h - 6) : r.bottom + 6}px`;
  }

  // ------------------------------------------------------------------ suggestion after the upload
  /** The smart analysis found a preset that suits the picture: offer it, never apply it. */
  // A preset that suits the picture (smart analysis): marked in the picker and named under
  // it with one Apply button - the one place for that decision. Never applied by itself.
  let suggested = null, asked = null; // asked: kept until the built-ins have loaded (they may arrive later)
  function offer(s) {
    asked = s || null;
    suggested = s && builtins.some((p) => p.id === s.id && !p.locked) ? s : null;
    const box = $("presetSuggest");
    box.hidden = !suggested;
    box.innerHTML = suggested ? `<span class="preset-offer">Suggested preset: <b>${esc(suggested.label)}</b> – ${esc(suggested.reason)}.</span>
      <button type="button" class="ghost small" data-suggest="${esc(suggested.id)}">Apply</button>` : "";
    renderPicker();
  }

  /** The analysis again after a size change (SmartRefresh): the offer follows it (another
   *  preset, a new reason, or none) until a preset has been applied to this design. */
  function reoffer(s) {
    if (applied) return;
    const now = suggested || asked;
    if ((s?.id || null) === (now?.id || null) && (s?.reason || "") === (now?.reason || "")) return;
    offer(s);
  }

  // ------------------------------------------------------------------ applying
  const formKey = (k) => (k === "running_repeats" ? "triple_run" : k);
  const formValue = (k, v) => (k === "running_repeats" ? v >= 3 : k === "thread_weight" ? String(v) : v);

  async function apply(value) {
    if (!state.project) return toast("Open a design first.", true);
    let label, sections;
    if (value === "@defaults") {
      label = "the app defaults";
      sections = null;
    } else {
      const f = find(value);
      if (!f) return;
      if (f.p.locked) return toast(lockText(f.p), true);
      label = nameOf(f.p);
      sections = f.p.sections || {};
    }
    await Jobs.flush(); // earlier changes are recorded on their own
    const before = { stitch: stitchValues(), extract: extractValues() };
    const { touchedStitch, touchedExtract, notes } = fill(sections);

    const after = { stitch: stitchValues(), extract: extractValues() };
    const stitchChanged = changedKeys(before.stitch, after.stitch), extractChanged = changedKeys(before.extract, after.extract);
    // the preset is the new starting point for the settings it holds
    if (state.baseline) {
      touchedStitch.forEach((k) => (state.baseline.stitch[k] = after.stitch[k]));
      touchedExtract.forEach((k) => (state.baseline.extract[k] = after.extract[k]));
    }
    applied = sections ? { name: label, values: snapshot() } : null; // any change after this shows "Changed"
    markModified();

    // one undo step: with the picture (the design is prepared again), or the stitch settings alone
    if (extractChanged.length) Jobs.extract({ step: { label: `preset ${label}`, stitch: { before: before.stitch, after: after.stitch } } });
    else if (stitchChanged.length) {
      History.stitchChange(`preset ${label}`);
      if (state.design) Jobs.stitch();
    }
    const n = stitchChanged.length + extractChanged.length;
    toast(`Applied ${label} · ${n ? plural(n, "setting") + " changed" : "no settings changed"}.` +
      (n ? " Undo with Ctrl+Z or the Undo button." : "") + (notes.length ? ` ${notes.join(" ")}` : ""), true);
  }

  /** Puts a preset's settings in the form: the fabric's values first, then the preset's own;
   *  picture settings for pictures only. ``sections`` null = the app defaults. */
  function fill(sections) {
    const touchedStitch = new Set(), touchedExtract = new Set();
    const notes = [];

    // stitch side: the fabric's values first, then the preset's own
    const st = {};
    if (!sections) Object.assign(st, defaultStitch);
    else {
      const fabric = sections.fabric?.fabric;
      const fb = fabric && (state.config.fabrics || []).find((x) => x.id === fabric);
      if (fb) Object.entries(fb.settings).forEach(([k, v]) => (st[formKey(k)] = formValue(k, v)));
      ["fabric", "stitch"].forEach((s) => Object.entries(sections[s] || {}).forEach(([k, v]) => (st[formKey(k)] = formValue(k, v))));
    }
    Object.keys(st).forEach((k) => $("stitchForm").elements[k] && touchedStitch.add(k));
    setStitchValues(st);

    // picture + size (raster designs only for the picture)
    const ex = {};
    if (sections?.image) {
      if (isRaster()) {
        if (sections.image.text_mode) {
          $("text_mode").value = sections.image.text_mode;
          applyTextMode(); // its two detail fields; explicit values below win
          ex.min_feature_mm = $("min_feature_mm").value;
          ex.min_area_mm2 = $("min_area_mm2").value;
        }
        Object.assign(ex, sections.image);
      } else notes.push("Picture settings were skipped: this design is a vector file.");
    }
    if (sections?.size) Object.entries(sections.size).forEach(([k, v]) => (ex[k] = v == null ? "" : v));
    Object.keys(ex).forEach((k) => touchedExtract.add(k));
    if (Object.keys(ex).length) setExtractValues(ex);
    return { touchedStitch, touchedExtract, notes };
  }

  /** A new design starts with the account's usual preset (prefs "preset", My account): its
   *  settings become starting values - no undo step, nothing marked as changed. The size is left
   *  to the placement or the width typed on the start screen. Null when it is not there (deleted,
   *  not loaded) or not in the plan; else {label, stitch: [setting keys it set]}. */
  function applyStart(value) {
    const f = value && find(value);
    if (!f || f.p.locked || !f.p.sections) return null;
    const { size, ...sections } = f.p.sections; // eslint-disable-line no-unused-vars
    const { touchedStitch } = fill(sections);
    applied = { name: nameOf(f.p), values: snapshot() };
    $("presetPick").value = value;
    showChoice();
    return { label: nameOf(f.p), stitch: [...touchedStitch] };
  }
  /** After the starting values are all in: "Changed" compares with them. */
  function settle() {
    if (applied) applied.values = snapshot();
    sync();
  }
  /** Presets to start new designs with (My account): the built-ins in the plan, then your own. */
  function choices() {
    return {
      builtins: builtins.filter((p) => !p.locked).map((p) => ({ key: `b:${p.id}`, label: p.label })),
      mine: mine.map((p) => ({ key: `u:${p.id}`, label: p.name })),
    };
  }

  // ------------------------------------------------------------------ saving
  function currentSections(inc) {
    const out = {};
    if (inc.stitch || inc.fabric) {
      Object.entries(stitchParams()).forEach(([k, v]) => {
        const s = keys[k];
        if (s && inc[s]) (out[s] ||= {})[k] = v;
      });
    }
    if (inc.image && isRaster()) {
      out.image = {};
      IMAGE_IDS.forEach((id) => {
        const el = $(id);
        out.image[id] = el.tagName === "SELECT" ? el.value : +el.value;
      });
    }
    if (inc.size) out.size = { width_mm: +$("width_mm").value, num_colors: $("num_colors").value ? +$("num_colors").value : null };
    return out;
  }
  const included = () => {
    const inc = {};
    $("presetInclude").querySelectorAll("[data-inc]").forEach((c) => (inc[c.dataset.inc] = c.checked && !c.disabled));
    return inc;
  };
  function changedFromDefaults(sections) {
    let n = 0;
    const st = stitchValues();
    ["stitch", "fabric"].forEach((s) => Object.keys(sections[s] || {}).forEach((k) => {
      const fk = formKey(k);
      if (fk in defaultStitch && String(st[fk]) !== String(defaultStitch[fk])) n++;
    }));
    const ex = extractValues(), base = state.baseline?.extract || {};
    ["image", "size"].forEach((s) => Object.keys(sections[s] || {}).forEach((k) => { if (k in base && String(ex[k]) !== String(base[k])) n++; }));
    return n;
  }
  function summary() {
    const replacing = !editing || $("presetReplace").checked;
    $("presetInclude").disabled = !replacing;
    if (!replacing) return ($("presetSummary").textContent = "Only the name and description change.");
    const sec = currentSections(included());
    const total = Object.values(sec).reduce((a, s) => a + Object.keys(s).length, 0);
    const groups = Object.keys(sec).map((s) => SECTION_LABEL[s]).join(", ");
    $("presetSummary").textContent = total
      ? `${plural(total, "setting")} (${groups}) · ${changedFromDefaults(sec)} changed from the defaults${editing ? "" : ` · you have ${countPhrase()}`}.`
      : "Choose at least one group of settings.";
  }

  function openSave(preset = null) {
    if (!preset && !status.feature) return toast("Saving your own presets is not included in your plan. Ask your administrator if you need it.", true);
    if (!preset && !status.can_save) return toast(`You have ${countPhrase()}. Delete one to save a new preset.`, true);
    editing = preset;
    pendingClash = null;
    $("presetDialogTitle").textContent = preset ? `Edit ${preset.name}` : "Save as preset";
    $("presetName").value = preset ? preset.name : "";
    $("presetDesc").value = preset?.description || "";
    $("presetReplaceRow").classList.toggle("hidden", !preset);
    $("presetReplace").checked = false;
    $("presetReplace").disabled = !state.project;
    const img = $("presetInclude").querySelector('[data-inc="image"]');
    img.disabled = !isRaster();
    img.closest("label").title = isRaster() ? "" : "Picture settings exist for pictures (PNG, JPG…) only";
    if (preset) $("presetInclude").querySelectorAll("[data-inc]").forEach((c) => (c.checked = !!preset.sections[c.dataset.inc]));
    else $("presetInclude").querySelectorAll("[data-inc]").forEach((c) => (c.checked = c.dataset.inc === "stitch" || c.dataset.inc === "fabric"));
    $("presetSubmit").textContent = preset ? "Save changes" : "Save preset";
    showClash(false);
    Auth.formStatus("presetStatus", "");
    summary();
    $("presetDialog").showModal();
    $("presetName").focus();
  }
  function showClash(on) {
    $("presetClash").classList.toggle("hidden", !on);
    $("presetButtons").classList.toggle("hidden", on);
  }

  async function submitSave(ev) {
    ev.preventDefault();
    const name = $("presetName").value.trim();
    if (!name) return Auth.formStatus("presetStatus", "Enter a name for the preset.", true);
    const body = { name, description: $("presetDesc").value.trim() };
    if (!editing || $("presetReplace").checked) {
      body.sections = currentSections(included());
      if (!Object.keys(body.sections).length) return Auth.formStatus("presetStatus", "Choose at least one group of settings.", true);
    }
    try {
      const r = editing ? await api(`/api/me/presets/${editing.id}`, json("PUT", body)) : await api("/api/me/presets", json("POST", body));
      done(r, editing ? "Preset saved." : `Saved ${r.name}.`);
    } catch (e) {
      if (e.status === 409) {
        const other = mine.find((p) => p.name.toLowerCase() === name.toLowerCase());
        pendingClash = { id: other?.id, body };
        Auth.formStatus("presetStatus", `${e.message} Replace its settings, or keep both?`, true);
        if (other && !editing) showClash(true);
        return;
      }
      Auth.formStatus("presetStatus", e.message, true);
    }
  }
  async function resolveClash(replace) {
    if (!pendingClash) return;
    const { id, body } = pendingClash;
    try {
      let r;
      if (replace) r = await api(`/api/me/presets/${id}`, json("PUT", body));
      else {
        const taken = new Set(mine.map((p) => p.name.toLowerCase()));
        let n = 2, name = `${body.name} (${n})`;
        while (taken.has(name.toLowerCase())) name = `${body.name} (${++n})`;
        r = await api("/api/me/presets", json("POST", { ...body, name: name.slice(0, 60) }));
      }
      done(r, replace ? `Replaced ${r.name}.` : `Saved ${r.name}.`);
    } catch (e) {
      showClash(false);
      Auth.formStatus("presetStatus", e.message, true);
    }
  }
  async function done(r, msg) {
    $("presetDialog").close();
    const skipped = r.skipped?.length ? ` ${plural(r.skipped.length, "setting")} could not be saved and ${r.skipped.length === 1 ? "was" : "were"} left out.` : "";
    toast(msg + skipped, true);
    await load();
    $("presetPick").value = `u:${r.id}`;
    showChoice();
    Account.refresh?.();
  }

  // ------------------------------------------------------------------ manage
  async function openManage() {
    confirmDelete = null;
    renderManage();
    if (!$("presetsDialog").open) $("presetsDialog").showModal();
    await load(); // the plan may have changed since the page was opened
  }
  function renderManage() {
    const canApply = !!state.project;
    const applyBtn = (v, label) => `<button type="button" data-apply="${esc(v)}" ${canApply ? "" : "disabled"} aria-label="Apply ${esc(label)}">Apply</button>`;
    const kindsText = (p) => (p.kinds || []).map((k) => KIND_LABEL[k]).filter(Boolean).join(" + ");
    const when = (t) => new Date(t * 1000).toLocaleDateString(undefined, { dateStyle: "medium" });
    const own = mine.map((p) => `<li>
        <div class="p-main"><b>${esc(p.name)}</b> <span class="small muted">· ${esc(kindsText(p))} · ${when(p.updated_at)}</span>
          ${p.description ? `<br><span class="small muted">${esc(p.description)}</span>` : ""}</div>
        <div class="p-actions">${confirmDelete === p.id
          ? `<span class="small">Delete ${esc(p.name)}?</span><button type="button" class="danger" data-del-yes="${p.id}">Delete</button><button type="button" class="ghost" data-del-no>Keep</button>`
          : `${applyBtn(`u:${p.id}`, p.name)}
          <button type="button" class="ghost" data-edit="${p.id}" aria-label="Edit ${esc(p.name)}">Edit</button>
          <button type="button" class="ghost" data-update="${p.id}" ${canApply ? "" : "disabled"} aria-label="Update ${esc(p.name)} from the current settings">Update from current</button>
          <button type="button" class="ghost" data-dup="${p.id}" ${status.can_save ? "" : "disabled"} aria-label="Duplicate ${esc(p.name)}">Duplicate</button>
          <button type="button" class="ghost danger" data-del="${p.id}" aria-label="Delete ${esc(p.name)}">Delete</button>`}</div></li>`).join("");
    const limitLine = !status.feature
      ? (mine.length ? "Your plan no longer includes saving presets: you can still apply, edit and delete these." : "Saving your own presets is not included in your plan. Ask your administrator if you need it.")
      : status.can_save ? `You have ${countPhrase()}.` : `You have ${countPhrase()}. Delete one to save a new preset; the others stay usable.`;
    $("presetsBody").innerHTML = `
      ${canApply ? "" : '<p class="small muted">Open a design to apply a preset.</p>'}
      <h3>Built-in</h3>
      <ul class="preset-list">${builtins.map((p) => `<li class="${p.locked ? "locked" : ""}">
        <div class="p-main"><b>${esc(p.label)}</b> <span class="badge">${TIER_LABEL[p.tier] || p.tier}</span>
          <span class="small muted">· ${esc(kindsText(p))}</span><br><span class="small muted">${esc(p.description)}</span>
          </div>
        <div class="p-actions">${p.locked ? lockBadge(p) : applyBtn(`b:${p.id}`, p.label)}</div></li>`).join("")}</ul>
      <div class="row between"><h3>My presets</h3>${status.feature ? `<button type="button" class="primary" data-new ${status.can_save && canApply ? "" : "disabled"}>Save current settings…</button>` : ""}</div>
      <p class="small muted">${limitLine}</p>
      ${own ? `<ul class="preset-list">${own}</ul>` : ""}`;
  }
  async function manageClick(ev) {
    const b = ev.target.closest("button");
    if (!b || !$("presetsBody").contains(b) || b.dataset.lock) return;
    const d = b.dataset, byId = (id) => mine.find((p) => p.id === +id);
    try {
      if (d.apply) {
        $("presetsDialog").close();
        $("presetPick").value = d.apply;
        showChoice();
        return apply(d.apply);
      }
      if (d.new !== undefined) { $("presetsDialog").close(); return openSave(); }
      if (d.edit) { $("presetsDialog").close(); return openSave(byId(d.edit)); }
      if (d.update) {
        const p = byId(d.update);
        const inc = Object.fromEntries(Object.keys(SECTION_LABEL).map((s) => [s, !!p.sections[s]]));
        const sections = currentSections(inc);
        if (!Object.keys(sections).length) return toast("This design has none of the settings this preset holds (picture settings need a picture).");
        await api(`/api/me/presets/${p.id}`, json("PUT", { sections }));
        toast(`${p.name} now holds the current settings.`, true);
      } else if (d.dup) {
        const r = await api(`/api/me/presets/${d.dup}/duplicate`, { method: "POST" });
        toast(`Saved ${r.name}.`, true);
      } else if (d.del) {
        confirmDelete = +d.del;
        renderManage();
        return $("presetsBody").querySelector("[data-del-no]")?.focus();
      } else if (d.delYes) {
        const p = byId(d.delYes);
        await api(`/api/me/presets/${d.delYes}`, { method: "DELETE" });
        confirmDelete = null;
        toast(`Deleted ${p?.name || "the preset"}.`, true);
        Account.refresh?.();
      } else if (d.delNo !== undefined) {
        confirmDelete = null;
        renderManage();
        return;
      } else return;
    } catch (e) {
      return toast(e.message);
    }
    await load();
  }

  // ------------------------------------------------------------------ wiring
  function init() {
    // choosing a preset applies it (one undo step); "Apply again" in the menu re-applies it
    // after settings were changed
    $("presetPick").onchange = () => {
      hidePop();
      showChoice();
      const v = $("presetPick").value, f = find(v);
      if (v && !f?.p.locked) apply(v).catch((e) => toast(e.message));
    };
    $("btnPresetReapply").onclick = () => apply($("presetPick").value).catch((e) => toast(e.message));
    bindPop($("btnPresetMenu"), $("presetMenu"));
    $("btnPresetSave").onclick = () => openSave();
    $("btnPresetManage").onclick = openManage;
    $("presetForm").addEventListener("submit", submitSave);
    $("presetForm").addEventListener("change", summary);
    $("presetOverwrite").onclick = () => resolveClash(true);
    $("presetKeepBoth").onclick = () => resolveClash(false);
    $("presetsBody").addEventListener("click", manageClick);
    document.addEventListener("click", (ev) => {
      const lock = ev.target.closest?.("[data-lock]");
      if (lock) return showLock(lock);
      const sug = ev.target.closest?.("[data-suggest]");
      if (sug) {
        $("presetPick").value = `b:${sug.dataset.suggest}`;
        showChoice();
        offer(null);
        $("presetPick").value = `b:${sug.dataset.suggest}`;
        apply(`b:${sug.dataset.suggest}`).catch((e) => toast(e.message));
      }
    });
    window.addEventListener("resize", hidePop);
    $("leftPanel").querySelector(".ed-scroll")?.addEventListener("scroll", hidePop, { passive: true });
    loading = load();
  }
  let loading = null;
  /** Resolves once the presets have loaded (or failed to). */
  const ready = () => loading || Promise.resolve();
  /** A new or reopened design: no preset applied yet. */
  function reset() {
    applied = null;
    if (suggested) offer(null);
    if ($("presetPick")) { $("presetPick").value = ""; showChoice(); }
    sync();
  }

  return { init, load, ready, reset, sync, settle, openManage, apply, applyStart, choices, offer, reoffer, status: () => status };
})();
