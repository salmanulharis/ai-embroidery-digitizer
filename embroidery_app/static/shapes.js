"use strict";
/* Shape editor: pick shapes in the design (step 2) or the stitch preview (step 3) and
   give them their own stitch style / direction, or one direction for the whole design.
   Uses helpers from app.js ($, api, json, busy, esc, state, renderDesign, generate, draw)
   - they are only called inside functions, after app.js has loaded. */

const Shapes = (() => {
  const sel = new Set(); // selected shape ids
  let objects = [];
  let byId = new Map();
  let vb = null; // step-2 viewBox {x, y, w, h}
  let vbKey = "";
  let drawMode = false;
  let guideMode = false; // drawing a guide line for the selected shapes
  let paths3 = []; // step-3 hit-test shapes [{id, order, kind, width, path}]
  let down = null; // pointer state for step-2 preview
  // shape editing (step 2): paint / erase / lasso the outline of the selected areas
  const edit = { on: false, tool: "add", size: 1.0, refAuto: true };
  let refInfo = null; // {picture, box, design, size, version} - see service.reference_info
  let spaceDown = false;
  const EDIT_TOOLS = [
    ["move", "✋", "Move", "Move and select (no drawing)"],
    ["add", "🖌", "Paint", "Paint area on"],
    ["erase", "⌫", "Erase", "Erase area"],
    ["lasso-add", "⬚+", "Lasso +", "Draw an outline to add"],
    ["lasso-erase", "⬚−", "Lasso −", "Draw an outline to cut away"],
  ];

  // stitch styles come from the server (models.STITCH_STYLES), shared with the colour table
  const styleOptions = () => [["inherit", "Same as colour"], ...stitchStyles(), ["skip", "Don't stitch"]];
  const styleName = (v) => (stitchStyles().find(([k]) => k === v)?.[1] || v).replace(/ \(.*\)$/, "");
  const METHOD_LABEL = {
    fill: "fill", satin: "satin", satin_band: "satin that follows the shape", satin_line: "satin that follows the line",
    column: "satin that follows the shape", stroke: "satin that follows each stroke", dot: "satin dot",
    running: "outline", outline: "outline", mixed: "mixed styles", none: "too small to stitch",
    double_run: "double run outline", backstitch: "backstitch outline", stemstitch: "stemstitch outline",
    sculpture_run: "sculpture run outline", zigzag: "zigzag border", estitch: "E-stitch border",
    guided_satin: "satin along the guide line", florentine_fill: "fill along the guide line",
  };
  // angle convention matches the stitch engine: y points down, so 45° runs down-right
  const GUIDED = ["guided_satin", "florentine_fill"]; // styles that follow a drawn guide line
  const PRESETS = [[0, "—", "Horizontal"], [45, "╲", "Diagonal, top-left to bottom-right"], [90, "│", "Vertical"], [135, "╱", "Diagonal, bottom-left to top-right"]];

  const svgEl = () => $("svgBox").querySelector("svg");
  const onStep3 = () => !$("step3").classList.contains("hidden");
  const layerOf = (o) => state.design?.layers.find((l) => l.index === o.layer);
  const layerOn = (o) => layerOf(o)?.enabled !== false;
  const isCustom = (o) => o.stitch_type != null || o.angle != null || o.enabled === false
    || o.spacing_mm != null || o.pull_mm != null || o.bold != null || o.underlay_style != null || o.guide != null;
  // thickness presets = gap between fill rows / satin stitches (mm)
  const THICKNESS = [["light", 0.5, "Light"], ["normal", 0.4, "Normal"], ["dense", 0.33, "Dense"]];
  const thicknessName = (mm) => THICKNESS.find(([, v]) => Math.abs(v - mm) < 1e-6)?.[2].toLowerCase() || "custom";
  const same = (vals) => { const s = new Set(vals); return s.size === 1 ? [...s][0] : undefined; };

  // ------------------------------------------------------------------ data from the server
  function afterRender(payload) {
    refInfo = payload.reference || null;
    syncRefControls();
    objects = payload.objects || [];
    byId = new Map(objects.map((o) => [o.id, o]));
    for (const id of [...sel]) if (!byId.has(id) || !layerOn(byId.get(id))) sel.delete(id);
    const key = `${payload.design.width_mm}x${payload.design.height_mm}`;
    if (key !== vbKey) { vbKey = key; vb = null; }
    applyViewBox();
    buildList();
    refresh();
  }

  // shape info recomputed with the stitch settings just used (the stitch method and
  // direction of a shape depend on them, e.g. on "Smart direction")
  function setObjects(list) {
    if (!Array.isArray(list)) return;
    objects = list;
    byId = new Map(objects.map((o) => [o.id, o]));
    for (const id of [...sel]) if (!byId.has(id)) sel.delete(id);
    buildList();
  }

  function reset() {
    sel.clear();
    objects = []; byId = new Map(); vb = null; vbKey = ""; paths3 = [];
    setDrawMode(false);
    setEdit(false);
    edit.refAuto = true;
  }

  function refresh() {
    if (edit.on && !editable().length) setEdit(false);
    decorate();
    renderPanel($("shapePanel"), 2);
    renderPanel($("shapePanel3"), 3);
    syncList();
    if (state.preview && onStep3()) draw();
  }

  function select(ids, additive = false) {
    if (!additive) sel.clear();
    for (const id of ids) {
      const o = byId.get(id);
      if (!o || !layerOn(o)) continue;
      if (additive && sel.has(id)) sel.delete(id); else sel.add(id);
    }
    refresh();
  }

  function selectLayer(index) {
    select(objects.filter((o) => o.layer === index).map((o) => o.id));
    $("shapePanel").scrollIntoView({ block: "nearest", behavior: "smooth" });
  }

  // ------------------------------------------------------------------ saving
  async function update(body, label = "Saving…") {
    const res = await busy(label, () => api(`/api/projects/${state.project.id}/objects`, json("PUT", body)));
    await applied(res);
  }

  async function resetShapes(ids) {
    const res = await busy("Resetting…", () => api(`/api/projects/${state.project.id}/objects/reset`, json("POST", { ids })));
    await applied(res);
  }

  async function applied(res) {
    const focusKey = document.activeElement?.dataset?.action;
    const panelId = document.activeElement?.closest?.(".shapepanel")?.id;
    if (Array.isArray(res.selected)) { // the server merged / split the edited areas: keep them selected
      sel.clear();
      res.selected.forEach((id) => sel.add(id));
    }
    renderDesign(res);
    if (onStep3()) {
      const before = state.preview?.stats;
      await generate({ keepView: true });
      const after = state.preview?.stats;
      if (before && after && before.stitches !== after.stitches) {
        const d = after.stitches - before.stitches, m = Math.round((after.est_minutes - before.est_minutes) * 10) / 10;
        toast(`Stitches: ${before.stitches.toLocaleString()} → ${after.stitches.toLocaleString()} (${d > 0 ? "+" : ""}${d.toLocaleString()})` +
          (m ? ` · about ${Math.abs(m)} min ${m > 0 ? "longer" : "shorter"}` : ""), true);
      }
    }
    if (focusKey) (panelId ? $(panelId) : document).querySelector(`[data-action="${focusKey}"]`)?.focus();
  }

  async function recolor(body, label) {
    const res = await busy(label, () => api(`/api/projects/${state.project.id}/objects/recolor`, json("POST", body)));
    await applied(res);
  }

  async function setLayerHex(index, hex) {
    const layers = state.design.layers.map(({ index: i, hex: h, name, enabled, stitch_type, angle }) =>
      ({ index: i, hex: i === index ? hex : h, name, enabled, stitch_type, angle }));
    const res = await busy("Changing thread colour…", () => api(`/api/projects/${state.project.id}/layers`, json("PUT", { layers })));
    await applied(res);
  }

  async function sendEdit(body, label = "Editing shape…") {
    const ids = editable().map((o) => o.id);
    if (!ids.length) return toast("Select a filled shape to edit.");
    const res = await busy(label, () => api(`/api/projects/${state.project.id}/objects/edit`, json("POST", { ids, ...body })));
    await applied(res);
  }

  // ------------------------------------------------------------------ colours: select every area of a thread colour
  const layerObjects = (idx) => objects.filter((o) => o.layer === idx);
  const layerById = (idx) => state.design?.layers.find((l) => l.index === idx);
  const fullySelected = (idx) => { const items = layerObjects(idx); return items.length > 0 && items.every((o) => sel.has(o.id)); };

  function chipsHtml() {
    const ls = (state.design?.layers || []).filter((l) => l.enabled && layerObjects(l.index).length);
    if (!ls.length) return "";
    return `<div class="chips" role="group" aria-label="Select every area of a thread colour">
      <span class="small muted">Select by colour ${Tip.icon("colour_select")}</span>
      ${ls.map((l) => {
        const on = fullySelected(l.index);
        return `<button type="button" class="chip ${on ? "on" : ""}" data-action="pick-colour" data-layer="${l.index}" data-drag-layer="${l.index}" data-merge-target="${l.index}" aria-pressed="${on}" title="Click: select every ${esc(l.name)} area (Shift: add). Drag onto another colour to merge.">
          <i style="background:${l.hex}"></i>${esc(l.name)} <span class="muted">${layerObjects(l.index).length}</span></button>`;
      }).join("")}</div>`;
  }

  function colourHtml(perLayer) {
    const idxs = [...perLayer.keys()];
    if (idxs.length === 1 && fullySelected(idxs[0])) {
      const l = layerById(idxs[0]);
      const n = layerObjects(l.index).length;
      return `<div class="sp-colour"><span class="lbl">Thread colour ${Tip.icon("shape_colour")}</span>
        <span class="row"><input type="color" data-action="layer-hex" value="${l.hex}" aria-label="Thread colour for every ${esc(l.name)} area">
        <span class="small">Changes all <b>${n}</b> ${esc(l.name)} area${n === 1 ? "" : "s"} together</span></span></div>`;
    }
    const single = idxs.length === 1 ? layerById(idxs[0]) : null;
    const others = (state.design?.layers || []).filter((l) => !single || l.index !== single.index);
    return `<div class="sp-colour"><span class="lbl">Thread colour of the selected areas ${Tip.icon("shape_colour")}</span>
      <span class="row">
        <select data-action="move-to" aria-label="Move the selected areas to another thread colour">
          <option value="">Move to colour…</option>${others.map((l) => `<option value="${l.index}">${esc(l.name)} (${l.hex})</option>`).join("")}
        </select>
        <label class="check small">or a new colour <input type="color" data-action="new-hex" value="${single?.hex || "#000000"}" aria-label="New thread colour for the selected areas"></label>
      </span>
      ${single ? `<button type="button" class="ghost small" data-action="select-colour" data-layer="${single.index}">Select every ${esc(single.name)} area (${layerObjects(single.index).length})</button>` : ""}
    </div>`;
  }

  // ------------------------------------------------------------------ selection panel
  function renderPanel(el, step) {
    if (!el) return;
    const dyn = el.querySelector(".sp-dyn") || el;
    if (step === 3 && !state.preview) { dyn.innerHTML = ""; return; }
    const chosen = [...sel].map((id) => byId.get(id)).filter(Boolean);
    const customCount = objects.filter(isCustom).length;
    const resetAll = customCount
      ? `<button type="button" class="ghost small" data-action="reset-all">Reset all shape settings (${customCount})</button>${Tip.icon("shape_reset")}`
      : "";
    if (!chosen.length) {
      dyn.innerHTML = chipsHtml() + `<div class="sp-empty small muted">${step === 2
        ? "No shape selected. Click a shape in the design above to give it its own stitch style or direction."
        : "No shape selected. Click a shape in the stitch preview to change it."} ${resetAll}</div>`;
      el.classList.toggle("active", false);
      return;
    }
    el.classList.toggle("active", true);

    // summary: "3 shapes selected · Black (2), Red (1)"
    const perLayer = new Map();
    chosen.forEach((o) => perLayer.set(o.layer, (perLayer.get(o.layer) || 0) + 1));
    const names = [...perLayer].map(([idx, n]) => {
      const l = state.design.layers.find((x) => x.index === idx);
      return `<span class="sp-sw" style="background:${l?.hex || "#999"}"></span>${esc(l?.name || "")} (${n})`;
    }).join(", ");

    // stitch style value
    const styles = new Set(chosen.map((o) => (o.enabled === false ? "skip" : o.stitch_type || "inherit")));
    const style = styles.size === 1 ? [...styles][0] : "";
    const colourStyles = new Set(chosen.map((o) => layerOf(o)?.stitch_type || "auto"));
    const inheritLabel = colourStyles.size === 1 ? `Same as colour (${styleName([...colourStyles][0])})` : "Same as each shape's colour";
    const styleOpts = (style ? "" : `<option value="" selected disabled>– different styles –</option>`) +
      styleOptions().map(([v, label]) => `<option value="${v}" ${v === style ? "selected" : ""}>${v === "inherit" ? inheritLabel : label}</option>`).join("");

    // direction
    const dirApplies = chosen.some((o) => o.direction_applies && o.enabled !== false);
    const angles = new Set(chosen.filter((o) => o.kind === "area").map((o) => (o.angle == null ? "inherit" : o.angle)));
    const ownAngle = angles.size === 1 && !angles.has("inherit") ? [...angles][0] : null;
    const allInherit = angles.size === 1 && angles.has("inherit");
    const shown = ownAngle ?? chosen.find((o) => o.effective_angle != null)?.effective_angle ?? "";

    let now;
    if (chosen.length === 1) {
      const o = chosen[0];
      now = o.enabled === false ? "This shape will not be stitched."
        : `Stitched as <b>${METHOD_LABEL[o.method] || o.method}</b>${o.effective_angle != null ? ` at <b>${o.effective_angle}°</b>` : ""} · ${o.area_mm2} mm²`;
    } else {
      now = `${chosen.length} shapes · ${chosen.reduce((s, o) => s + o.area_mm2, 0).toFixed(1)} mm²`;
    }
    // thickness / extra width (fill + satin) and bold (outlines)
    const live = chosen.filter((o) => o.enabled !== false);
    const thickApplies = live.some((o) => o.thickness_applies);
    const boldApplies = live.some((o) => o.bold_applies);
    const ownSpacing = same(chosen.map((o) => o.spacing_mm ?? "inherit"));
    const spacingShown = (ownSpacing !== "inherit" && ownSpacing !== undefined ? ownSpacing : live.find((o) => o.effective_spacing_mm != null)?.effective_spacing_mm) ?? "";
    const ownPull = same(chosen.map((o) => o.pull_mm ?? "inherit"));
    const pullShown = (ownPull !== "inherit" && ownPull !== undefined ? ownPull : live.find((o) => o.effective_pull_mm != null)?.effective_pull_mm) ?? "";
    const ownBold = same(chosen.map((o) => (o.bold == null ? "inherit" : o.bold ? "on" : "off")));
    // foundation: one list for fills, another for single-angle satin (see stitch/underlay.py)
    const underKinds = new Set(live.map((o) => o.underlay_applies));
    const underKind = underKinds.size === 1 ? [...underKinds][0] : null;
    const underList = underKind ? UNDERLAY_LABELS[underKind] : [];
    const ownUnder = same(chosen.map((o) => o.underlay_style ?? "inherit"));
    const effUnders = new Set(live.map((o) => o.effective_underlay));
    const effUnder = effUnders.size === 1 ? underList.find(([v]) => v === [...effUnders][0]) : null;
    const underHtml = `
      <label class="sp-bold"><span class="lbl">Foundation ${Tip.icon("shape_underlay")}</span>
        <select data-action="underlay" ${underKind ? "" : "disabled"} aria-label="Foundation stitches for the selected shapes">
          ${ownUnder === undefined || (ownUnder !== "inherit" && !underList.some(([v]) => v === ownUnder)) ? `<option value="" selected disabled>– different –</option>` : ""}
          <option value="inherit" ${ownUnder === "inherit" ? "selected" : ""}>Same as design${effUnder && ownUnder === "inherit" ? ` (${effUnder[1]})` : ""}</option>
          ${underList.map(([v, l]) => `<option value="${v}" ${ownUnder === v ? "selected" : ""}>${l}</option>`).join("")}
        </select></label>`;
    let thickNow = "";
    if (chosen.length === 1 && chosen[0].effective_spacing_mm != null) {
      const o = chosen[0];
      thickNow = `Now: ${o.effective_spacing_mm} mm gap (${thicknessName(o.effective_spacing_mm)}) · ${o.spacing_mm != null ? "own setting" : "design setting"}`;
    }
    const thicknessHtml = `
      <fieldset class="sp-dir" ${thickApplies ? "" : "disabled"}>
        <legend class="lbl">Thickness ${Tip.icon("shape_thickness")}</legend>
        <div class="sp-presets" role="group" aria-label="Thickness">
          <button type="button" data-action="thick" data-value="inherit" class="${ownSpacing === "inherit" ? "on" : ""}">Same as design</button>
          ${THICKNESS.map(([key, mm, label]) => `<button type="button" data-action="thick" data-value="${mm}" class="${ownSpacing === mm ? "on" : ""}" title="${mm} mm gap">${label}</button>`).join("")}
        </div>
        <span class="sp-anglebox">Custom <input type="number" min="0.25" max="1" step="0.01" data-action="spacing" value="${spacingShown}" aria-label="Thickness: gap in millimetres" class="sp-angle"> mm gap</span>
        ${thickNow ? `<p class="small muted sp-now">${thickNow}</p>` : ""}
      </fieldset>
      <fieldset class="sp-dir" ${thickApplies ? "" : "disabled"}>
        <legend class="lbl">Extra width ${Tip.icon("shape_width")}</legend>
        <div class="row sp-dirrow">
          <label class="check"><input type="radio" name="pullmode${step}" data-action="pull-inherit" ${ownPull === "inherit" ? "checked" : ""}> Same as design</label>
          <label class="check"><input type="radio" name="pullmode${step}" data-action="pull-custom" ${ownPull !== "inherit" && ownPull !== undefined ? "checked" : ""}> Custom:</label>
          <span class="sp-anglebox"><input type="number" min="0" max="0.8" step="0.05" data-action="pull" value="${pullShown}" aria-label="Extra width in millimetres" class="sp-angle"> mm</span>
        </div>
      </fieldset>
      <label class="sp-bold"><span class="lbl">Bold outline ${Tip.icon("shape_bold")}</span>
        <select data-action="bold" ${boldApplies ? "" : "disabled"} aria-label="Bold outline for the selected shapes">
          ${ownBold === undefined ? `<option value="" selected disabled>– different –</option>` : ""}
          <option value="inherit" ${ownBold === "inherit" ? "selected" : ""}>Same as design</option>
          <option value="off" ${ownBold === "off" ? "selected" : ""}>Single line</option>
          <option value="on" ${ownBold === "on" ? "selected" : ""}>Bold (sewn 3 times)</option>
        </select></label>
      ${underHtml}`;
    const thickNote = !thickApplies && boldApplies
      ? `<p class="small muted">Thickness and extra width apply to fill and satin; outlines use “Bold outline” instead.</p>`
      : "";

    const followNote = !dirApplies
      ? `<p class="small muted">${chosen.some((o) => ["satin_band", "satin_line", "column", "stroke", "dot"].includes(o.method))
        ? "This satin follows the shape's curve, so its direction is set automatically. Choose <i>Fill</i> as the stitch style to set a direction."
        : "Direction does not apply to outlines or shapes that are not stitched."}</p>`
      : "";

    const canEdit = step === 2 && chosen.some((o) => o.kind === "area");
    const guided = chosen.some((o) => GUIDED.includes(o.stitch_type || layerOf(o)?.stitch_type));
    const hasGuide = chosen.some((o) => o.guide);
    const guideHtml = guided || hasGuide ? `
      <div class="row sp-guide">
        ${step === 2 ? `<button type="button" class="small ${guideMode ? "on" : ""}" data-action="guide" aria-pressed="${guideMode}">✎ ${hasGuide ? "Redraw guide line" : "Draw guide line"}</button>` : ""}
        ${hasGuide ? `<button type="button" class="ghost small" data-action="guide-clear">Clear guide line</button>` : ""}
        ${Tip.icon("shape_guide")}
      </div>
      ${guided && !chosen.every((o) => o.guide) ? `<p class="small muted">${step === 2 ? "Draw a guide line across the shape: the satin or the fill rows follow it." : "Draw a guide line in step 2 (Edit design): until then the shape is sewn the usual way."}</p>` : ""}` : "";
    dyn.innerHTML = `${chipsHtml()}
      <div class="sp-head">
        <b>${chosen.length === 1 ? "1 shape selected" : `${chosen.length} shapes selected`}</b>
        <span class="small">${names}</span>
        ${canEdit ? `<button type="button" class="small ${edit.on ? "on" : ""}" data-action="edit" aria-pressed="${edit.on}">✎ ${edit.on ? "Editing shape" : "Edit shape"}</button>${Tip.icon("shape_edit")}` : ""}
        <button type="button" class="ghost small" data-action="clear">Clear selection</button>
      </div>
      <p class="small sp-now">${now}</p>
      ${colourHtml(perLayer)}
      <div class="sp-grid">
        <label><span class="lbl">Stitch style ${Tip.icon("shape_style")}</span>
          <select data-action="style" aria-label="Stitch style for the selected shapes">${styleOpts}</select></label>
        <fieldset class="sp-dir" ${dirApplies ? "" : "disabled"}>
          <legend class="lbl">Direction ${Tip.icon("shape_direction")}</legend>
          <div class="row sp-dirrow">
            <label class="check"><input type="radio" name="dirmode${step}" data-action="dir-inherit" ${allInherit ? "checked" : ""}> Same as colour</label>
            <label class="check"><input type="radio" name="dirmode${step}" data-action="dir-custom" ${ownAngle != null ? "checked" : ""}> Custom:</label>
            <span class="sp-anglebox"><input type="number" min="0" max="180" step="1" data-action="angle" value="${shown}" aria-label="Direction in degrees" class="sp-angle">°</span>
          </div>
          <div class="sp-presets" role="group" aria-label="Quick directions">
            ${PRESETS.map(([a, glyph, name]) => `<button type="button" data-action="preset" data-angle="${a}" class="${ownAngle === a ? "on" : ""}" title="${name} (${a}°)" aria-label="${name}, ${a} degrees">${glyph}<span class="small"> ${a}°</span></button>`).join("")}
            ${step === 2 ? `<button type="button" data-action="draw" class="${drawMode ? "on" : ""}" aria-pressed="${drawMode}">✎ Draw direction</button>${Tip.icon("shape_draw")}` : ""}
          </div>
          <button type="button" class="ghost small" data-action="whole">Use this direction for the whole design</button>${Tip.icon("shape_whole")}
        </fieldset>
      </div>
      ${followNote}
      ${guideHtml}
      <div class="sp-grid sp-thickgrid">${thicknessHtml}</div>
      ${thickNote}
      <div class="row">
        ${chosen.some(isCustom) ? `<button type="button" class="ghost small" data-action="reset-sel">Reset selected to colour settings</button>` : ""}
        ${resetAll}
      </div>`;
  }

  function currentAngle(panel) {
    const v = panel.querySelector('[data-action="angle"]')?.value;
    return v === "" || v == null ? null : Number(v);
  }

  async function onPanelEvent(ev) {
    const t = ev.target.closest("[data-action]");
    if (!t) return;
    const panel = ev.currentTarget;
    const action = t.dataset.action;
    const ids = [...sel];
    if (ev.type === "click") {
      if (action === "clear") return select([]);
      if (action === "pick-colour" || action === "select-colour") {
        const idx = Number(t.dataset.layer);
        const ids2 = layerObjects(idx).filter(layerOn).map((o) => o.id);
        if (action === "pick-colour" && (ev.shiftKey || ev.ctrlKey || ev.metaKey)) {
          const all = ids2.every((id) => sel.has(id));
          ids2.forEach((id) => (all ? sel.delete(id) : sel.add(id)));
          return refresh();
        }
        if (action === "pick-colour" && fullySelected(idx) && sel.size === ids2.length) return select([]);
        return select(ids2);
      }
      if (action === "edit") return setEdit(!edit.on);
      if (action === "preset") return update({ ids, angle: Number(t.dataset.angle) }, "Changing direction…");
      if (action === "draw") { if (!drawMode) setEdit(false); return setDrawMode(!drawMode); }
      if (action === "guide") { if (!guideMode) { setEdit(false); setDrawMode(false); } return setGuideMode(!guideMode); }
      if (action === "guide-clear") return update({ ids, guide: "inherit" }, "Removing guide line…");
      if (action === "reset-sel") return resetShapes(ids);
      if (action === "reset-all") {
        if (confirm("Reset every shape to its colour's settings?")) return resetShapes([]);
        return;
      }
      if (action === "whole") {
        const a = currentAngle(panel);
        if (a == null || !Number.isFinite(a)) return toast("Enter a direction (0–180°) first.");
        const n = objects.filter((o) => o.kind === "area").length;
        if (!confirm(`Use ${a}° for all ${n} shapes in the design?\n\nSatin shapes such as lettering turn too; satin that follows a curve keeps following it. You can undo or reset this anytime.`)) return;
        return update({ scope: "all", angle: a }, "Changing direction of the whole design…");
      }
      if (action === "dir-custom") {
        const a = currentAngle(panel);
        if (a != null && Number.isFinite(a)) return update({ ids, angle: a }, "Changing direction…");
        panel.querySelector('[data-action="angle"]')?.focus();
        return;
      }
      if (action === "dir-inherit") return update({ ids, angle: "inherit" }, "Changing direction…");
      if (action === "thick") {
        const v = t.dataset.value;
        return update({ ids, spacing_mm: v === "inherit" ? "inherit" : Number(v) }, "Changing thickness…");
      }
      if (action === "pull-inherit") return update({ ids, pull_mm: "inherit" }, "Changing extra width…");
      if (action === "pull-custom") {
        const v = numberIn(panel, "pull");
        if (v != null && v >= 0 && v <= 0.8) return update({ ids, pull_mm: v }, "Changing extra width…");
        panel.querySelector('[data-action="pull"]')?.focus();
        return;
      }
      return;
    }
    if (ev.type === "change") {
      if (action === "layer-hex") {
        const idx = [...new Set([...sel].map((id) => byId.get(id)?.layer))][0];
        return setLayerHex(idx, t.value);
      }
      if (action === "move-to") {
        if (t.value === "") return;
        return recolor({ ids, target: Number(t.value) }, "Changing thread colour…");
      }
      if (action === "new-hex") return recolor({ ids, hex: t.value }, "Changing thread colour…");
      if (action === "style") {
        const v = t.value;
        if (!v) return;
        return update(v === "skip" ? { ids, enabled: false } : { ids, enabled: true, stitch_type: v }, "Changing stitch style…");
      }
      if (action === "angle") {
        const a = currentAngle(panel);
        if (a == null) return;
        if (!Number.isFinite(a) || a < 0 || a > 360) return toast("Direction must be between 0 and 180 degrees.");
        return update({ ids, angle: a }, "Changing direction…");
      }
      if (action === "spacing") {
        const v = numberIn(panel, "spacing");
        if (v == null) return;
        if (v < 0.25 || v > 1) return toast("Thickness must be a gap between 0.25 and 1 mm.");
        return update({ ids, spacing_mm: v }, "Changing thickness…");
      }
      if (action === "pull") {
        const v = numberIn(panel, "pull");
        if (v == null) return;
        if (v < 0 || v > 0.8) return toast("Extra width must be between 0 and 0.8 mm.");
        return update({ ids, pull_mm: v }, "Changing extra width…");
      }
      if (action === "bold") {
        const v = t.value;
        if (!v) return;
        return update({ ids, bold: v === "inherit" ? "inherit" : v === "on" }, "Changing outline…");
      }
      if (action === "underlay") {
        const v = t.value;
        if (!v) return;
        return update({ ids, underlay_style: v }, "Changing foundation…");
      }
    }
  }

  function numberIn(panel, action) {
    const raw = panel.querySelector(`[data-action="${action}"]`)?.value;
    if (raw === "" || raw == null) return null;
    const v = Number(raw);
    return Number.isFinite(v) ? v : null;
  }

  // ------------------------------------------------------------------ keyboard-friendly list
  function buildList() {
    const sl = $("shapePanel")?.querySelector(".sp-list select");
    if (!sl) return;
    const groups = (state.design?.layers || []).filter((l) => l.enabled).map((l) => {
      const items = objects.filter((o) => o.layer === l.index).sort((a, b) => b.area_mm2 - a.area_mm2);
      if (!items.length) return "";
      return `<optgroup label="${esc(l.name)}">${items.map((o, i) =>
        `<option value="${o.id}">${esc(l.name)} ${i + 1} · ${o.kind === "line" ? "line" : `${o.area_mm2} mm²`} · ${o.enabled === false ? "not stitched" : METHOD_LABEL[o.method] || o.method}${o.effective_angle != null ? ` ${o.effective_angle}°` : ""}${o.spacing_mm != null ? ` · ${thicknessName(o.spacing_mm)}` : ""}${o.effective_bold ? " · bold" : ""}${isCustom(o) ? " · own settings" : ""}</option>`).join("")}</optgroup>`;
    }).join("");
    sl.innerHTML = groups;
    sl.size = Math.min(8, Math.max(3, objects.length));
  }

  function syncList() {
    const sl = $("shapePanel")?.querySelector(".sp-list select");
    if (!sl) return;
    for (const opt of sl.options) opt.selected = sel.has(opt.value);
  }

  // ------------------------------------------------------------------ step 2 preview: view box, overlays
  function applyViewBox() {
    const svg = svgEl();
    if (!svg) return;
    const [, , w, h] = (svg.getAttribute("viewBox") || "0 0 100 100").split(/\s+/).map(Number);
    if (!vb) { const m = Math.max(w, h) * 0.03; vb = { x: -m, y: -m, w: w + 2 * m, h: h + 2 * m }; }
    svg.setAttribute("viewBox", `${vb.x} ${vb.y} ${vb.w} ${vb.h}`);
  }

  function fitSvg() { vb = null; applyViewBox(); }

  function svgPoint(svg, ev) {
    const pt = svg.createSVGPoint();
    pt.x = ev.clientX; pt.y = ev.clientY;
    return pt.matrixTransform(svg.getScreenCTM().inverse());
  }

  const NS = "http://www.w3.org/2000/svg";
  function el(tag, attrs) {
    const e = document.createElementNS(NS, tag);
    for (const [k, v] of Object.entries(attrs)) e.setAttribute(k, v);
    return e;
  }

  // ------------------------------------------------------------------ reference (faded original under the shapes)
  function syncRefControls() {
    const kind = $("refKind");
    if (!kind) return;
    const pic = !!refInfo?.picture, des = !!refInfo?.design;
    kind.querySelector('option[value="picture"]').disabled = !pic;
    kind.querySelector('option[value="design"]').disabled = !des;
    if (kind.value === "picture" && !pic) kind.value = "design";
    $("showRef").disabled = !pic && !des;
    $("refBar").classList.toggle("hidden", !$("showRef").checked || $("showRef").disabled);
  }

  function referenceImage() {
    if (!$("showRef")?.checked || !refInfo || !state.project) return null;
    const kind = $("refKind").value;
    const base = `/api/projects/${state.project.id}/reference/${kind}?v=${refInfo.version || 0}`;
    const op = (+$("refOpacity").value || 40) / 100;
    let attrs = null;
    if (kind === "picture" && refInfo.picture && refInfo.box) {
      const [x, y, w, h] = refInfo.box;
      attrs = { x, y, width: w, height: h };
    } else if (refInfo.design) {
      const [w, h] = refInfo.size || [state.design.width_mm, state.design.height_mm];
      attrs = { x: 0, y: 0, width: w, height: h };
    }
    if (!attrs) return null;
    // inside the design's own SVG: it zooms and pans with the shapes, always aligned
    const img = el("image", { ...attrs, class: "sp-ref", preserveAspectRatio: "none", opacity: op, "pointer-events": "none" });
    img.setAttribute("href", base);
    return img;
  }

  function decorate() {
    const svg = svgEl();
    if (!svg) return;
    svg.querySelectorAll(".sp-defs, .sp-overlay, .sp-ref").forEach((n) => n.remove());
    const ref = referenceImage();
    $("svgBox").classList.toggle("with-ref", !!ref);
    if (ref) svg.insertBefore(ref, svg.querySelector("g[id^='layer-']") || svg.firstChild);
    svg.querySelectorAll("[data-oid]").forEach((p) => p.classList.toggle("sel", sel.has(p.dataset.oid)));
    const defs = el("defs", { class: "sp-defs" });
    const overlay = el("g", { class: "sp-overlay", "pointer-events": "none" });
    const pathOf = (id) => svg.querySelector(`[data-oid="${CSS.escape(id)}"]`);

    if ($("showDirections")?.checked) {
      const s = Math.max(0.8, Math.min(state.design.width_mm, state.design.height_mm) / 45); // hatch spacing (mm)
      const made = new Set();
      for (const o of objects) {
        if (o.effective_angle == null || !o.direction_applies || o.enabled === false || !layerOn(o)) continue;
        const p = pathOf(o.id);
        if (!p) continue;
        const a = o.effective_angle, pid = `sp-h-${String(a).replace(".", "_")}`;
        if (!made.has(pid)) {
          made.add(pid);
          const pat = el("pattern", { id: pid, patternUnits: "userSpaceOnUse", width: s, height: s, patternTransform: `rotate(${a})` });
          pat.append(
            el("line", { x1: 0, y1: s / 2, x2: s, y2: s / 2, stroke: "#fff", "stroke-width": s * 0.3, "stroke-opacity": 0.55 }),
            el("line", { x1: 0, y1: s / 2, x2: s, y2: s / 2, stroke: "#000", "stroke-width": s * 0.12, "stroke-opacity": 0.75 }),
          );
          defs.append(pat);
        }
        overlay.append(el("path", { d: p.getAttribute("d"), fill: `url(#${pid})`, "fill-rule": "evenodd" }));
      }
    }
    for (const o of objects) { // dashed outline = shape has its own settings
      if (!isCustom(o) || sel.has(o.id) || !layerOn(o)) continue;
      const p = pathOf(o.id);
      if (!p) continue;
      overlay.append(el("path", { d: p.getAttribute("d"), fill: "none", stroke: "#fff", "stroke-width": 2.5, "vector-effect": "non-scaling-stroke" }));
      overlay.append(el("path", { d: p.getAttribute("d"), fill: "none", stroke: "#222", "stroke-width": 1.2, "stroke-dasharray": "4 3", "vector-effect": "non-scaling-stroke" }));
    }
    for (const id of sel) { // selection outline
      const p = pathOf(id);
      if (!p) continue;
      overlay.append(el("path", { d: p.getAttribute("d"), fill: "none", stroke: "#fff", "stroke-width": 5, "vector-effect": "non-scaling-stroke", "stroke-linejoin": "round" }));
      overlay.append(el("path", { d: p.getAttribute("d"), fill: "none", stroke: "#2d5bd7", "stroke-width": 2.5, "vector-effect": "non-scaling-stroke", "stroke-linejoin": "round" }));
    }
    for (const id of sel) { // guide lines (guided satin / Florentine fill)
      const g = byId.get(id)?.guide;
      if (!g || g.length < 2) continue;
      const d = g.map(([x, y], i) => `${i ? "L" : "M"}${x} ${y}`).join("");
      overlay.append(el("path", { d, fill: "none", stroke: "#fff", "stroke-width": 5, "vector-effect": "non-scaling-stroke", "stroke-linecap": "round", "stroke-linejoin": "round" }));
      overlay.append(el("path", { d, fill: "none", stroke: "#e8590c", "stroke-width": 2.5, "stroke-dasharray": "6 4", "vector-effect": "non-scaling-stroke", "stroke-linecap": "round", "stroke-linejoin": "round" }));
    }
    svg.prepend(defs);
    svg.append(overlay);
  }

  // ------------------------------------------------------------------ shape editing
  const editable = () => [...sel].map((id) => byId.get(id)).filter((o) => o && o.kind === "area");

  function setEdit(on) {
    on = !!on && editable().length > 0;
    edit.on = on;
    if (on) {
      setDrawMode(false);
      if (edit.refAuto && $("showRef") && !$("showRef").disabled) { // first time: show the reference to edit against
        $("showRef").checked = true;
        edit.refAuto = false;
        syncRefControls();
      }
    }
    renderEditBar();
    document.querySelectorAll('[data-action="edit"]').forEach((b) => { b.classList.toggle("on", on); b.setAttribute("aria-pressed", on); });
    decorate();
  }

  function renderEditBar() {
    const bar = $("editBar");
    if (!bar) return;
    bar.classList.toggle("hidden", !edit.on);
    const box = $("svgBox");
    box.classList.toggle("editing", edit.on);
    box.dataset.tool = edit.on ? edit.tool : "";
    if (!edit.on) { bar.innerHTML = ""; return; }
    bar.innerHTML = `
      <div class="seg" role="group" aria-label="Editing tool">${EDIT_TOOLS.map(([k, g, label, title]) =>
        `<button type="button" data-tool="${k}" class="${edit.tool === k ? "on" : ""}" aria-pressed="${edit.tool === k}" title="${title}">${g} <span>${label}</span></button>`).join("")}</div>
      <label class="small brush"><span class="lbl">Brush <output data-out="size">${edit.size.toFixed(1)} mm</output></span>
        <input type="range" data-edit="size" min="0.3" max="10" step="0.1" value="${edit.size}" aria-label="Brush size in millimetres"></label>
      <span class="row nowrap">
        <button type="button" class="small" data-edit="grow" title="Make the selected areas 0.2 mm bigger all round">Grow</button>
        <button type="button" class="small" data-edit="shrink" title="Make the selected areas 0.2 mm smaller all round">Shrink</button>
        <button type="button" class="small" data-edit="smooth" title="Round off jagged edges and tiny notches">Smooth</button>
        ${Tip.icon("shape_edit_tools")}
      </span>
      <button type="button" class="primary small" data-edit="done">Done</button>`;
  }

  function onEditBar(ev) {
    const t = ev.target.closest("[data-tool], [data-edit]");
    if (!t) return;
    if (ev.type === "input" && t.dataset.edit === "size") {
      edit.size = Number(t.value);
      $("editBar").querySelector('[data-out="size"]').textContent = `${edit.size.toFixed(1)} mm`;
      return;
    }
    if (ev.type !== "click") return;
    if (t.dataset.tool) { edit.tool = t.dataset.tool; return renderEditBar(); }
    const op = t.dataset.edit;
    if (op === "done") return setEdit(false);
    if (op === "grow" || op === "shrink") return sendEdit({ op, size_mm: 0.2 }, op === "grow" ? "Growing…" : "Shrinking…");
    if (op === "smooth") return sendEdit({ op, size_mm: 0.4 }, "Smoothing…");
  }

  // live stroke while painting / drawing an outline
  function strokeColour(op) {
    if (op === "erase") return "#ff3b30";
    const l = layerById(editable()[0]?.layer);
    return l?.hex || "#2d5bd7";
  }

  function drawStroke(svg, d) {
    let g = svg.querySelector(".sp-stroke");
    if (!g) { g = el("path", { class: "sp-stroke", "pointer-events": "none" }); svg.append(g); }
    const pts = d.stroke;
    const lasso = edit.tool.startsWith("lasso");
    const op = edit.tool.endsWith("erase") ? "erase" : "add";
    const path = pts.map(([x, y], i) => `${i ? "L" : "M"}${x.toFixed(3)} ${y.toFixed(3)}`).join("") + (lasso ? "Z" : "");
    g.setAttribute("d", pts.length === 1 && !lasso ? `M${pts[0][0]} ${pts[0][1]}l0.001 0` : path);
    if (lasso) {
      Object.entries({ fill: strokeColour(op), "fill-opacity": 0.3, stroke: op === "erase" ? "#ff3b30" : "#2d5bd7", "stroke-width": 1.5,
        "stroke-dasharray": "5 3", "vector-effect": "non-scaling-stroke", "stroke-linejoin": "round" }).forEach(([k, v]) => g.setAttribute(k, v));
    } else {
      Object.entries({ fill: "none", stroke: strokeColour(op), "stroke-opacity": 0.55, "stroke-width": edit.size,
        "stroke-linecap": "round", "stroke-linejoin": "round" }).forEach(([k, v]) => g.setAttribute(k, v));
    }
  }

  async function finishStroke(svg, d) {
    svg.querySelector(".sp-stroke")?.remove();
    const lasso = edit.tool.startsWith("lasso");
    const op = edit.tool.endsWith("erase") ? "erase" : "add";
    const pts = d.stroke.map(([x, y]) => [Math.round(x * 1000) / 1000, Math.round(y * 1000) / 1000]);
    if (lasso && pts.length < 3) return toast("Draw an outline around the area.");
    await sendEdit({ op, tool: lasso ? "lasso" : "brush", points: pts.slice(0, 5000), size_mm: edit.size },
      op === "add" ? "Adding area…" : "Erasing area…");
  }

  function setGuideMode(on) {
    guideMode = on;
    $("svgBox")?.classList.toggle("drawing", on || drawMode);
    document.querySelectorAll('[data-action="guide"]').forEach((b) => { b.classList.toggle("on", on); b.setAttribute("aria-pressed", on); });
    if (on) toast("Drag a line across the selected shape: the satin or the fill rows will follow it. Press Esc to cancel.", true);
  }

  function drawGuide(svg, pts) {
    let g = svg.querySelector(".sp-guide-live");
    if (!g) { g = el("path", { class: "sp-guide-live", "pointer-events": "none", fill: "none", stroke: "#e8590c", "stroke-width": 3, "vector-effect": "non-scaling-stroke", "stroke-linecap": "round", "stroke-linejoin": "round" }); svg.append(g); }
    g.setAttribute("d", pts.map(([x, y], i) => `${i ? "L" : "M"}${x.toFixed(3)} ${y.toFixed(3)}`).join(""));
  }

  function setDrawMode(on) {
    drawMode = on;
    $("svgBox")?.classList.toggle("drawing", on);
    document.querySelectorAll('[data-action="draw"]').forEach((b) => { b.classList.toggle("on", on); b.setAttribute("aria-pressed", on); });
    if (on) toast("Drag across the design in the direction the stitches should run. Press Esc to cancel.", true);
  }

  function bindSvgBox() {
    const box = $("svgBox");
    box.addEventListener("wheel", (ev) => {
      const svg = svgEl();
      if (!svg || !vb) return;
      ev.preventDefault();
      const p = svgPoint(svg, ev);
      const f = ev.deltaY < 0 ? 1 / 1.15 : 1.15;
      vb = { x: p.x - (p.x - vb.x) * f, y: p.y - (p.y - vb.y) * f, w: vb.w * f, h: vb.h * f };
      applyViewBox();
    }, { passive: false });

    const fingers = new Map();
    let pinch = null;
    box.addEventListener("pointerdown", (ev) => {
      const svg = svgEl();
      if (!svg) return;
      fingers.set(ev.pointerId, { x: ev.clientX, y: ev.clientY });
      if (fingers.size === 2) { // two fingers: zoom + pan, never draw
        svg.querySelector(".sp-stroke")?.remove();
        down = null;
        const [a, b] = [...fingers.values()];
        pinch = { d: Math.hypot(a.x - b.x, a.y - b.y) || 1, mx: (a.x + b.x) / 2, my: (a.y + b.y) / 2 };
        return;
      }
      const pan = ev.button === 1 || (ev.button === 0 && spaceDown);
      if (ev.button !== 0 && !pan) return;
      if (pan) ev.preventDefault();
      const start = svgPoint(svg, ev);
      const drawing = !pan && edit.on && edit.tool !== "move" && editable().length > 0;
      down = { cx: ev.clientX, cy: ev.clientY, start, oid: ev.target.closest?.("[data-oid]")?.dataset.oid, moved: false, pan,
        stroke: drawing ? [[start.x, start.y]] : null, guide: !pan && guideMode && sel.size ? [[start.x, start.y]] : null };
      box.setPointerCapture(ev.pointerId);
      if (drawing) drawStroke(svg, down);
    });
    box.addEventListener("pointermove", (ev) => {
      const svg = svgEl();
      if (fingers.has(ev.pointerId)) fingers.set(ev.pointerId, { x: ev.clientX, y: ev.clientY });
      if (pinch && fingers.size === 2 && svg && vb) {
        const [a, b] = [...fingers.values()];
        const d = Math.hypot(a.x - b.x, a.y - b.y) || 1, mx = (a.x + b.x) / 2, my = (a.y + b.y) / 2;
        const k = 1 / (svg.getScreenCTM()?.a || 1);
        vb = { ...vb, x: vb.x - (mx - pinch.mx) * k, y: vb.y - (my - pinch.my) * k };
        applyViewBox();
        const p = svgPoint(svg, { clientX: mx, clientY: my });
        const f = pinch.d / d;
        vb = { x: p.x - (p.x - vb.x) * f, y: p.y - (p.y - vb.y) * f, w: vb.w * f, h: vb.h * f };
        applyViewBox();
        pinch = { d, mx, my };
        return;
      }
      if (!down || !svg) return;
      if (down.guide) {
        const p = svgPoint(svg, ev);
        const last = down.guide[down.guide.length - 1];
        const k = 1 / (svg.getScreenCTM()?.a || 1);
        if (Math.hypot(p.x - last[0], p.y - last[1]) >= Math.max(3 * k, 0.3) && down.guide.length < 2000) {
          down.guide.push([p.x, p.y]);
          drawGuide(svg, down.guide);
        }
        return;
      }
      if (down.stroke) {
        const p = svgPoint(svg, ev);
        const last = down.stroke[down.stroke.length - 1];
        const k = 1 / (svg.getScreenCTM()?.a || 1); // mm per screen px
        const step = edit.tool.startsWith("lasso") ? 2 * k : Math.max(2 * k, edit.size * 0.15);
        if (Math.hypot(p.x - last[0], p.y - last[1]) >= step && down.stroke.length < 5000) {
          down.stroke.push([p.x, p.y]);
          down.moved = true;
          drawStroke(svg, down);
        }
        return;
      }
      const dx = ev.clientX - down.cx, dy = ev.clientY - down.cy;
      if (!down.moved && Math.hypot(dx, dy) < 4) return;
      down.moved = true;
      if (drawMode && !down.pan) { // live direction arrow
        const p = svgPoint(svg, ev);
        svg.querySelector(".sp-arrow")?.remove();
        const g = el("g", { class: "sp-arrow", "pointer-events": "none" });
        g.append(
          el("line", { x1: down.start.x, y1: down.start.y, x2: p.x, y2: p.y, stroke: "#fff", "stroke-width": 6, "vector-effect": "non-scaling-stroke", "stroke-linecap": "round" }),
          el("line", { x1: down.start.x, y1: down.start.y, x2: p.x, y2: p.y, stroke: "#2d5bd7", "stroke-width": 3, "vector-effect": "non-scaling-stroke", "stroke-linecap": "round" }),
        );
        svg.append(g);
        return;
      }
      const k = 1 / (svg.getScreenCTM()?.a || 1); // svg units per screen px (uniform with "meet")
      vb = { ...vb, x: vb.x - dx * k, y: vb.y - dy * k };
      down.cx = ev.clientX; down.cy = ev.clientY;
      applyViewBox();
    });
    const lift = (ev) => {
      fingers.delete(ev.pointerId);
      if (fingers.size < 2) pinch = null;
    };
    box.addEventListener("pointerup", async (ev) => {
      lift(ev);
      const svg = svgEl();
      if (!down || !svg) return;
      const d = down;
      down = null;
      svg.querySelector(".sp-arrow")?.remove();
      svg.querySelector(".sp-guide-live")?.remove();
      if (d.guide) {
        let len = 0;
        for (let i = 1; i < d.guide.length; i++) len += Math.hypot(d.guide[i][0] - d.guide[i - 1][0], d.guide[i][1] - d.guide[i - 1][1]);
        if (len < 2) return toast("Drag a longer line across the shape.");
        setGuideMode(false);
        return update({ ids: [...sel], guide: d.guide }, "Setting guide line…");
      }
      if (d.stroke) return finishStroke(svg, d);
      if (d.pan) return;
      if (drawMode) {
        const p = svgPoint(svg, ev);
        const dx = p.x - d.start.x, dy = p.y - d.start.y;
        if (Math.hypot(dx, dy) < Math.max(vb.w, vb.h) * 0.02) {
          if (d.oid && !sel.size) select([d.oid]);
          return;
        }
        if (!sel.size && d.oid) select([d.oid]);
        if (!sel.size) return toast("Select a shape first, then draw its direction.");
        const a = Math.round(((Math.atan2(dy, dx) * 180) / Math.PI + 180) % 180);
        setDrawMode(false);
        return update({ ids: [...sel], angle: a }, "Changing direction…");
      }
      if (d.moved) return;
      const additive = ev.shiftKey || ev.ctrlKey || ev.metaKey;
      if (d.oid) select([d.oid], additive);
      else if (!additive) select([]);
    });
    box.addEventListener("pointercancel", (ev) => {
      lift(ev);
      svgEl()?.querySelector(".sp-stroke")?.remove();
      down = null;
    });
    box.addEventListener("auxclick", (ev) => ev.button === 1 && ev.preventDefault());
  }

  // ------------------------------------------------------------------ step 3: click shapes in the stitch preview
  function setStitchShapes(shapes) {
    paths3 = (shapes || []).map((s) => ({ ...s, path: new Path2D(s.d) })).sort((a, b) => b.order - a.order);
  }

  const hitCtx = document.createElement("canvas").getContext("2d");
  function hit3(xMm, yMm) {
    for (const s of paths3) {
      if (s.kind === "area" ? hitCtx.isPointInPath(s.path, xMm, yMm, "evenodd") : (hitCtx.lineWidth = Math.max(s.width, 1.5), hitCtx.isPointInStroke(s.path, xMm, yMm))) return s.id;
    }
    return null;
  }

  function click3(xMm, yMm, additive) {
    const id = hit3(xMm, yMm);
    if (id) select([id], additive);
    else if (!additive) select([]);
  }

  function drawSelection3(ctx, view, dpr) {
    if (!sel.size || !paths3.length) return;
    const k = view.scale * 10; // canvas px per mm (preview units are 0.1 mm)
    ctx.save();
    ctx.setTransform(dpr * k, 0, 0, dpr * k, dpr * view.x, dpr * view.y);
    ctx.lineJoin = "round";
    for (const s of paths3) {
      if (!sel.has(s.id)) continue;
      ctx.setLineDash([]);
      ctx.strokeStyle = "rgba(255,255,255,.9)"; ctx.lineWidth = 5 / k; ctx.stroke(s.path);
      ctx.setLineDash([6 / k, 4 / k]);
      ctx.strokeStyle = "#2d5bd7"; ctx.lineWidth = 2.5 / k; ctx.stroke(s.path);
    }
    ctx.restore();
  }

  // ------------------------------------------------------------------ init
  function init() {
    const p2 = $("shapePanel");
    p2.innerHTML = `<div class="sp-dyn"></div>
      <details class="sp-list"><summary class="small">Choose shapes from a list ${Tip.icon("shape_list")}</summary>
        <select multiple aria-label="Shapes in the design"></select></details>`;
    p2.querySelector(".sp-list select").addEventListener("change", (ev) => {
      sel.clear();
      for (const opt of ev.target.selectedOptions) sel.add(opt.value);
      refresh();
    });
    for (const panel of [p2, $("shapePanel3")]) {
      panel.addEventListener("click", onPanelEvent);
      panel.addEventListener("change", onPanelEvent);
    }
    $("showDirections").addEventListener("change", decorate);
    $("showRef").addEventListener("change", () => { edit.refAuto = false; syncRefControls(); decorate(); });
    $("refKind").addEventListener("change", decorate);
    $("refOpacity").addEventListener("input", () => {
      $("refOpOut").textContent = `${$("refOpacity").value}%`;
      const img = svgEl()?.querySelector(".sp-ref");
      if (img) img.setAttribute("opacity", $("refOpacity").value / 100);
    });
    $("editBar").addEventListener("click", onEditBar);
    $("editBar").addEventListener("input", onEditBar);
    document.addEventListener("keydown", (ev) => { if (ev.code === "Space" && !ev.target.matches("input, select, textarea, button")) { spaceDown = true; if (edit.on) ev.preventDefault(); } });
    document.addEventListener("keyup", (ev) => { if (ev.code === "Space") spaceDown = false; });
    $("btnFitSvg").addEventListener("click", fitSvg);
    bindSvgBox();
    document.addEventListener("keydown", (ev) => {
      if (ev.key !== "Escape" || ev.target.matches("input, select, textarea")) return;
      if (down?.stroke) { svgEl()?.querySelector(".sp-stroke")?.remove(); down = null; return; }
      if (down?.guide) { svgEl()?.querySelector(".sp-guide-live")?.remove(); down = null; return; }
      if (guideMode) { setGuideMode(false); return; }
      if (edit.on) setEdit(false);
      else if (drawMode) setDrawMode(false);
      else if (sel.size) select([]);
    });
  }

  return { init, afterRender, reset, select, selectLayer, setObjects, setStitchShapes, click3, drawSelection3, refresh };
})();
