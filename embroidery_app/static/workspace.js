"use strict";
/* The editor's workspace: a mm grid with rulers over either view, and resizable /
   collapsible side panels. Both are conveniences kept per browser (localStorage); nothing
   here changes the design. Uses helpers and state from app.js ($, state, toast, announce)
   and Shapes, inside functions, after app.js has loaded. */

const Workspace = (() => {
  // ------------------------------------------------------------------ grid + rulers
  // One overlay canvas for both views: design millimetres -> stage pixels come from the
  // stitch view (state.view) or from the shapes view's SVG (its screen matrix).
  const RULER = 20; // px, rulers along the bottom and left edges (the floating bars sit at the top)
  const STEPS = [1, 2, 5, 10, 20, 50, 100, 200];
  let gridOn = false, lastKey = "";

  function transform() {
    const stage = $("stage"), sr = stage.getBoundingClientRect();
    if (state.pane === "stitches") {
      if (!state.preview) return null;
      const cr = $("canvas").getBoundingClientRect(), v = state.view;
      return { k: v.scale * 10, ox: cr.left - sr.left + v.x, oy: cr.top - sr.top + v.y, w: sr.width, h: sr.height };
    }
    const m = Shapes.ctm();
    if (!m || !m.a) return null;
    return { k: m.a, ox: m.e - sr.left, oy: m.f - sr.top, w: sr.width, h: sr.height };
  }

  /** The view may have moved (stitch frame drawn, shapes view box changed, pane switched,
   *  stage resized): repaint the grid when its mapping changed. */
  function sync() {
    if (!gridOn) return;
    const t = transform();
    const key = t ? [state.pane, t.k.toFixed(4), t.ox.toFixed(1), t.oy.toFixed(1), t.w, t.h, devicePixelRatio].join("|") : "none";
    if (key !== lastKey) { lastKey = key; paint(t); }
  }

  function paint(t) {
    const c = $("gridLayer"), dpr = window.devicePixelRatio || 1;
    const w = Math.round(c.clientWidth * dpr), h = Math.round(c.clientHeight * dpr);
    if (c.width !== w || c.height !== h) { c.width = w; c.height = h; }
    const ctx = c.getContext("2d");
    ctx.setTransform(1, 0, 0, 1, 0, 0);
    ctx.clearRect(0, 0, w, h);
    if (!t) return;
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    const W = c.clientWidth, H = c.clientHeight;
    const minor = STEPS.find((s) => s * t.k >= 8) || 200;
    const major = STEPS.find((s) => s >= minor * 5 && s % minor === 0 && s * t.k >= 50) || minor * 10;
    const sx = (mm) => t.ox + mm * t.k, sy = (mm) => t.oy + mm * t.k;
    const range = (a, b, step) => { const out = []; for (let v = Math.ceil(a / step) * step; v <= b; v += step) out.push(Math.round(v * 1000) / 1000); return out; };
    const xs = range((0 - t.ox) / t.k, (W - t.ox) / t.k, minor), ys = range((0 - t.oy) / t.k, (H - t.oy) / t.k, minor);
    const isMajor = (v) => Math.abs(v / major - Math.round(v / major)) < 1e-6;
    // grid: thin, recessive; every 'major' line a little stronger
    for (const strong of [false, true]) {
      ctx.strokeStyle = strong ? "rgba(45, 91, 215, .34)" : "rgba(45, 91, 215, .14)";
      ctx.lineWidth = 1;
      ctx.beginPath();
      xs.filter((v) => isMajor(v) === strong).forEach((v) => { const x = Math.round(sx(v)) + 0.5; ctx.moveTo(x, 0); ctx.lineTo(x, H); });
      ys.filter((v) => isMajor(v) === strong).forEach((v) => { const y = Math.round(sy(v)) + 0.5; ctx.moveTo(0, y); ctx.lineTo(W, y); });
      ctx.stroke();
    }
    // rulers: bottom (x) and left (y), labelled in mm from the design's top-left corner
    const css = getComputedStyle(document.body);
    const bg = css.getPropertyValue("--card").trim() || "#fff", ink = css.getPropertyValue("--muted").trim() || "#667085";
    ctx.globalAlpha = 0.94;
    ctx.fillStyle = bg;
    ctx.fillRect(0, H - RULER, W, RULER);
    ctx.fillRect(0, 0, RULER, H - RULER);
    ctx.globalAlpha = 1;
    ctx.strokeStyle = ink;
    ctx.fillStyle = ink;
    ctx.font = "10px system-ui, sans-serif";
    ctx.beginPath();
    ctx.moveTo(0, H - RULER + 0.5); ctx.lineTo(W, H - RULER + 0.5);
    ctx.moveTo(RULER - 0.5, 0); ctx.lineTo(RULER - 0.5, H - RULER);
    xs.forEach((v) => { const x = Math.round(sx(v)) + 0.5; if (x < RULER) return; const len = isMajor(v) ? 8 : 4; ctx.moveTo(x, H - RULER); ctx.lineTo(x, H - RULER + len); });
    ys.forEach((v) => { const y = Math.round(sy(v)) + 0.5; if (y > H - RULER) return; const len = isMajor(v) ? 8 : 4; ctx.moveTo(RULER, y); ctx.lineTo(RULER - len, y); });
    ctx.stroke();
    ctx.textBaseline = "bottom";
    xs.filter(isMajor).forEach((v) => { const x = sx(v); if (x > RULER + 2 && x < W - 30) ctx.fillText(String(v), x + 2, H - 2); });
    ctx.save();
    ctx.rotate(-Math.PI / 2);
    ys.filter(isMajor).forEach((v) => { const y = sy(v); if (y > 30 && y < H - RULER - 2) ctx.fillText(String(v), -y + 2, RULER - 9); });
    ctx.restore();
    ctx.textBaseline = "middle";
    ctx.fillText("mm", 3, H - RULER / 2);
  }

  function setGrid(on, save = true) {
    gridOn = !!on;
    $("showGrid").checked = gridOn;
    $("gridLayer").hidden = !gridOn;
    $("stage").classList.toggle("with-grid", gridOn);
    lastKey = "";
    sync();
    if (save) try { localStorage.setItem("showGrid", gridOn ? "1" : "0"); } catch (_) {}
  }

  // ------------------------------------------------------------------ resizable / collapsible panels
  // Inner edge of each side panel: drag, or arrow keys on the separator (Shift: bigger steps),
  // Home / End, Enter or [ / ] to collapse and open again, double-click for the standard width.
  // Only while the panel sits beside the preview (narrow screens show it as a sheet).
  const MIN = 240, MAX = 420;
  const SIDES = {
    left: { panel: "leftPanel", sep: "sepLeft", v: "--ed-left", media: "(min-width: 900px)", name: "settings panel" },
    right: { panel: "rightPanel", sep: "sepRight", v: "--ed-right", media: "(min-width: 1200px)", name: "design panel" },
  };
  let layout = { left: null, right: null, leftOff: false, rightOff: false };

  const inline = (side) => window.matchMedia(SIDES[side].media).matches;
  const width = (side) => $(SIDES[side].panel).getBoundingClientRect().width || layout[side] || 300;
  const save = () => { try { localStorage.setItem("edLayout", JSON.stringify(layout)); } catch (_) {} };

  function apply() {
    const ed = $("editor");
    for (const side of ["left", "right"]) {
      const s = SIDES[side], here = inline(side), off = layout[`${side}Off`] && here;
      if (off) ed.style.setProperty(s.v, "0px");
      else if (layout[side]) ed.style.setProperty(s.v, `${layout[side]}px`);
      else ed.style.removeProperty(s.v);
      document.body.classList.toggle(`${side}-off`, off);
      const sep = $(s.sep), btn = $(`${s.sep}Btn`);
      sep.parentElement.hidden = !here;
      sep.tabIndex = off ? -1 : 0; // closed: the button opens it again
      sep.setAttribute("aria-valuenow", Math.round(off ? 0 : width(side)));
      sep.setAttribute("aria-valuetext", off ? "closed" : `${Math.round(width(side))} pixels wide`);
      btn.textContent = (side === "left") === off ? "›" : "‹";
      btn.setAttribute("aria-expanded", !off);
      btn.setAttribute("aria-label", `${off ? "Open" : "Close"} the ${s.name} (${side === "left" ? "[" : "]"})`);
      btn.title = btn.getAttribute("aria-label");
    }
  }
  function resize(side, px) {
    layout[side] = Math.round(Math.min(MAX, Math.max(MIN, px)));
    layout[`${side}Off`] = false;
    apply();
  }
  function toggle(side) {
    if (!inline(side)) return false;
    layout[`${side}Off`] = !layout[`${side}Off`];
    apply();
    save();
    announce(`${SIDES[side].name[0].toUpperCase()}${SIDES[side].name.slice(1)} ${layout[`${side}Off`] ? "closed" : "open"}.`);
    return true;
  }
  function reset() {
    layout = { left: null, right: null, leftOff: false, rightOff: false };
    apply();
    save();
    setGrid(false);
    toast("Workspace reset: standard panel sizes, grid off.", true);
  }

  function bindSeparator(side) {
    const s = SIDES[side], sep = $(s.sep), dir = side === "left" ? 1 : -1;
    sep.setAttribute("aria-valuemin", MIN);
    sep.setAttribute("aria-valuemax", MAX);
    $(`${s.sep}Btn`).addEventListener("click", () => toggle(side));
    sep.addEventListener("dblclick", () => { layout[side] = null; layout[`${side}Off`] = false; apply(); save(); });
    let drag = null;
    sep.addEventListener("pointerdown", (ev) => {
      if (ev.button !== 0) return;
      drag = { x: ev.clientX, w: layout[`${side}Off`] ? MIN : width(side), id: ev.pointerId };
      sep.setPointerCapture(ev.pointerId);
      sep.classList.add("dragging");
      ev.preventDefault();
    });
    sep.addEventListener("pointermove", (ev) => {
      if (!drag || ev.pointerId !== drag.id) return;
      resize(side, drag.w + dir * (ev.clientX - drag.x));
    });
    const end = (ev) => {
      if (!drag || ev.pointerId !== drag.id) return;
      drag = null;
      sep.classList.remove("dragging");
      save();
    };
    sep.addEventListener("pointerup", end);
    sep.addEventListener("pointercancel", end);
    sep.addEventListener("keydown", (ev) => {
      const step = ev.shiftKey ? 64 : 16, w = layout[`${side}Off`] ? MIN : width(side);
      const grow = { ArrowRight: dir, ArrowLeft: -dir }[ev.key];
      if (grow) resize(side, w + grow * step);
      else if (ev.key === "Home") resize(side, MIN);
      else if (ev.key === "End") resize(side, MAX);
      else if (ev.key === "Enter") toggle(side);
      else return;
      ev.preventDefault();
      save();
    });
  }

  function init() {
    try { layout = { ...layout, ...JSON.parse(localStorage.getItem("edLayout") || "{}") }; } catch (_) {}
    bindSeparator("left");
    bindSeparator("right");
    apply();
    for (const side of ["left", "right"]) window.matchMedia(SIDES[side].media).addEventListener("change", apply);
    new ResizeObserver(() => apply()).observe($("editor"));
    new ResizeObserver(() => { sync(); ShapeTools.moved(); }).observe($("stage"));
    $("showGrid").addEventListener("change", () => setGrid($("showGrid").checked));
    let grid = false;
    try { grid = localStorage.getItem("showGrid") === "1"; } catch (_) {}
    setGrid(grid, false);
    $("btnResetWorkspace").onclick = reset;
  }

  return { init, toggle, reset, setGrid, sync, grid: () => gridOn };
})();
