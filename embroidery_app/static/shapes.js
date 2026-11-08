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
  let paths3 = []; // step-3 hit-test shapes [{id, order, kind, width, path}]
  let down = null; // pointer state for step-2 preview

  const STYLE_OPTIONS = [
    ["inherit", "Same as colour"], ["auto", "Automatic"], ["fill", "Fill (large areas)"],
    ["satin", "Satin (smooth, narrow)"], ["running", "Outline only"], ["skip", "Don't stitch"],
  ];
  const COLOUR_STYLE = { auto: "Automatic", fill: "Fill", satin: "Satin", running: "Outline only" };
  const METHOD_LABEL = {
    fill: "fill", satin: "satin", satin_band: "satin that follows the shape", satin_line: "satin that follows the line",
    running: "outline", outline: "outline", mixed: "mixed styles", none: "too small to stitch",
  };
  // angle convention matches the stitch engine: y points down, so 45° runs down-right
  const PRESETS = [[0, "—", "Horizontal"], [45, "╲", "Diagonal, top-left to bottom-right"], [90, "│", "Vertical"], [135, "╱", "Diagonal, bottom-left to top-right"]];

  const svgEl = () => $("svgBox").querySelector("svg");
  const onStep3 = () => !$("step3").classList.contains("hidden");
  const layerOf = (o) => state.design?.layers.find((l) => l.index === o.layer);
  const layerOn = (o) => layerOf(o)?.enabled !== false;
  const isCustom = (o) => o.stitch_type != null || o.angle != null || o.enabled === false
    || o.spacing_mm != null || o.pull_mm != null || o.bold != null;
  // thickness presets = gap between fill rows / satin stitches (mm)
  const THICKNESS = [["light", 0.5, "Light"], ["normal", 0.4, "Normal"], ["dense", 0.33, "Dense"]];
  const thicknessName = (mm) => THICKNESS.find(([, v]) => Math.abs(v - mm) < 1e-6)?.[2].toLowerCase() || "custom";
  const same = (vals) => { const s = new Set(vals); return s.size === 1 ? [...s][0] : undefined; };

  // ------------------------------------------------------------------ data from the server
  function afterRender(payload) {
    objects = payload.objects || [];
    byId = new Map(objects.map((o) => [o.id, o]));
    for (const id of [...sel]) if (!byId.has(id) || !layerOn(byId.get(id))) sel.delete(id);
    const key = `${payload.design.width_mm}x${payload.design.height_mm}`;
    if (key !== vbKey) { vbKey = key; vb = null; }
    applyViewBox();
    buildList();
    refresh();
  }

  function reset() {
    sel.clear();
    objects = []; byId = new Map(); vb = null; vbKey = ""; paths3 = [];
    setDrawMode(false);
  }

  function refresh() {
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
      dyn.innerHTML = `<div class="sp-empty small muted">${step === 2
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
    const inheritLabel = colourStyles.size === 1 ? `Same as colour (${COLOUR_STYLE[[...colourStyles][0]]})` : "Same as each shape's colour";
    const styleOpts = (style ? "" : `<option value="" selected disabled>– different styles –</option>`) +
      STYLE_OPTIONS.map(([v, label]) => `<option value="${v}" ${v === style ? "selected" : ""}>${v === "inherit" ? inheritLabel : label}</option>`).join("");

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
        </select></label>`;
    const thickNote = !thickApplies && boldApplies
      ? `<p class="small muted">Thickness and extra width apply to fill and satin; outlines use “Bold outline” instead.</p>`
      : "";

    const followNote = !dirApplies
      ? `<p class="small muted">${chosen.some((o) => o.method === "satin_band" || o.method === "satin_line")
        ? "This satin follows the shape's curve, so its direction is set automatically. Choose <i>Fill</i> as the stitch style to set a direction."
        : "Direction does not apply to outlines or shapes that are not stitched."}</p>`
      : "";

    dyn.innerHTML = `
      <div class="sp-head">
        <b>${chosen.length === 1 ? "1 shape selected" : `${chosen.length} shapes selected`}</b>
        <span class="small">${names}</span>
        <button type="button" class="ghost small" data-action="clear">Clear selection</button>
      </div>
      <p class="small sp-now">${now}</p>
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
      if (action === "preset") return update({ ids, angle: Number(t.dataset.angle) }, "Changing direction…");
      if (action === "draw") return setDrawMode(!drawMode);
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

  function decorate() {
    const svg = svgEl();
    if (!svg) return;
    svg.querySelectorAll(".sp-defs, .sp-overlay").forEach((n) => n.remove());
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
    svg.prepend(defs);
    svg.append(overlay);
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

    box.addEventListener("pointerdown", (ev) => {
      const svg = svgEl();
      if (!svg || ev.button !== 0) return;
      down = { cx: ev.clientX, cy: ev.clientY, start: svgPoint(svg, ev), oid: ev.target.closest?.("[data-oid]")?.dataset.oid, moved: false };
      box.setPointerCapture(ev.pointerId);
    });
    box.addEventListener("pointermove", (ev) => {
      const svg = svgEl();
      if (!down || !svg) return;
      const dx = ev.clientX - down.cx, dy = ev.clientY - down.cy;
      if (!down.moved && Math.hypot(dx, dy) < 4) return;
      down.moved = true;
      if (drawMode) { // live direction arrow
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
    box.addEventListener("pointerup", async (ev) => {
      const svg = svgEl();
      if (!down || !svg) return;
      const d = down;
      down = null;
      svg.querySelector(".sp-arrow")?.remove();
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
    box.addEventListener("pointercancel", () => { down = null; });
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
    $("btnFitSvg").addEventListener("click", fitSvg);
    bindSvgBox();
    document.addEventListener("keydown", (ev) => {
      if (ev.key !== "Escape" || ev.target.matches("input, select, textarea")) return;
      if (drawMode) setDrawMode(false);
      else if (sel.size) select([]);
    });
  }

  return { init, afterRender, reset, select, selectLayer, setStitchShapes, click3, drawSelection3, refresh };
})();
