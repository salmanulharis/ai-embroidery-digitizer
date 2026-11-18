"use strict";
/* The shape menu (right-click, long-press, or the Menu key / Shift+F10 with shapes
   selected) and the floating toolbar next to the selection. Both change shapes through
   Shapes.tools: the same server calls and undo steps as the selection panel, so they add
   no behaviour of their own. Uses helpers from app.js ($, esc, state, toast, openSheet,
   sheetOpen) inside functions. */

const ShapeTools = (() => {
  const T = () => Shapes.tools;
  const LONG_PRESS_MS = 550;
  const WIDE = "(min-width: 900px)"; // the toolbar needs room; narrow screens use the Design panel sheet

  // ------------------------------------------------------------------ actions (shared by menu + toolbar)
  const act = {
    style: (v) => T().update(v === "skip" ? { enabled: false } : { enabled: true, stitch_type: v }, "Changing stitch style…"),
    angle: (a) => T().update({ angle: a }, "Changing direction…"),
    thick: (mm) => T().update({ spacing_mm: mm }, "Changing thickness…"),
    skip: (off) => T().update({ enabled: !off }, off ? "Leaving out…" : "Stitching again…"),
    reset: () => T().reset(),
    sameColour: () => {
      const st = T().state();
      const on = new Set((state.design?.layers || []).filter((l) => l.enabled).map((l) => l.index));
      T().select(Shapes.objects().filter((o) => st.layers.includes(o.layer) && on.has(o.layer)).map((o) => o.id));
    },
    details: () => {
      if (!window.matchMedia("(min-width: 1200px)").matches && !sheetOpen("right")) openSheet("right");
      const p = $("shapePanel");
      p.scrollIntoView({ block: "start", behavior: reducedMotion() ? "auto" : "smooth" });
      requestAnimationFrame(() => p.querySelector('select[data-action="style"]')?.focus({ preventScroll: true }));
    },
  };

  // ------------------------------------------------------------------ the menu
  let menu = null, sub = null, returnTo = null, openedAt = 0, openPt = null;

  function model() {
    const t = T(), st = t.state();
    const names = st.layers.map((i) => t.layerOf({ layer: i })?.name).filter(Boolean);
    const radio = (label, checked, run) => ({ label, checked: !!checked, radio: true, run });
    return [
      { label: "Stitch style", sub: t.styleMenu().flatMap(([group, items]) =>
        [...(group ? [{ heading: group }] : []), ...items.map(([v, l]) => radio(l, v === st.style, () => act.style(v)))]) },
      { label: "Direction", disabled: !st.dirApplies, sub: [
        radio("Same as colour", st.angle === "inherit", () => act.angle("inherit")),
        ...t.presets.map(([a, glyph, name]) => radio(`${glyph}  ${name} (${a}°)`, st.angle === a, () => act.angle(a))),
        { label: "Draw direction…", run: () => t.drawDirection() },
      ] },
      { label: "Thickness", disabled: !st.thickApplies, sub: [
        radio("Same as design", st.spacing === "inherit", () => act.thick("inherit")),
        ...t.thickness.map(([, mm, l]) => radio(`${l} (${mm} mm gap)`, st.spacing === mm, () => act.thick(mm))),
      ] },
      "-",
      { label: names.length === 1 ? `Select all ${names[0]}` : "Select all of these colours", run: act.sameColour },
      { label: st.skipped ? "Stitch again" : "Don't stitch", run: () => act.skip(!st.skipped) },
      { label: "Reset to colour settings", disabled: !st.custom, run: act.reset },
      "-",
      { label: "Details…", run: act.details },
    ];
  }

  function build(items, label) {
    const m = document.createElement("div");
    m.className = "ctx-menu";
    m.setAttribute("role", "menu");
    m.setAttribute("aria-label", label);
    m.tabIndex = -1;
    // "-" = a line; {heading} = a group name (not an item: the arrow keys pass over it)
    m.innerHTML = items.map((it, i) => (it === "-" ? '<div role="separator" class="ctx-sep"></div>'
      : it.heading ? `<div class="ctx-head" role="presentation">${esc(it.heading)}</div>`
      : `<button type="button" role="${it.radio ? "menuitemradio" : "menuitem"}" data-i="${i}" tabindex="-1"
          ${it.radio ? `aria-checked="${it.checked}"` : ""} ${it.sub ? 'aria-haspopup="menu" aria-expanded="false"' : ""} ${it.disabled ? 'aria-disabled="true"' : ""}>
          <span class="ctx-check" aria-hidden="true">${it.radio && it.checked ? "✓" : ""}</span><span class="ctx-label">${esc(it.label)}</span>${it.sub ? '<span class="ctx-more" aria-hidden="true">›</span>' : ""}</button>`)).join("");
    m._items = items;
    document.body.append(m);
    return m;
  }
  const entries = (m) => [...m.querySelectorAll('[role^="menuitem"]')];
  function place(m, x, y, flipX = null) {
    const r = m.getBoundingClientRect(), pad = 6;
    let left = x, top = y;
    if (left + r.width > innerWidth - pad) left = flipX != null ? flipX - r.width : innerWidth - r.width - pad;
    if (top + r.height > innerHeight - pad) top = Math.max(pad, innerHeight - r.height - pad);
    m.style.left = `${Math.max(pad, left)}px`;
    m.style.top = `${Math.max(pad, top)}px`;
  }
  function focusItem(m, i) {
    const list = entries(m);
    if (!list.length) return;
    list[(i + list.length) % list.length].focus();
  }

  function open(x, y, label) {
    close(false);
    returnTo = document.activeElement && document.activeElement !== document.body ? document.activeElement : null;
    menu = build(model(), label);
    place(menu, x, y);
    openedAt = performance.now();
    openPt = { x, y };
    hideBar();
    focusItem(menu, 0);
  }
  function close(restore = true) {
    sub?.remove(); sub = null;
    if (!menu) return;
    menu.remove(); menu = null;
    if (restore) returnTo?.focus?.({ preventScroll: true });
    changed();
  }
  function openSub(btn) {
    const it = menu._items[+btn.dataset.i];
    if (!it?.sub || btn.getAttribute("aria-disabled") === "true") return false;
    sub?.remove();
    menu.querySelectorAll('[aria-expanded="true"]').forEach((b) => b.setAttribute("aria-expanded", "false"));
    sub = build(it.sub, it.label);
    sub.classList.add("ctx-sub");
    sub._parent = btn;
    btn.setAttribute("aria-expanded", "true");
    const r = btn.getBoundingClientRect();
    place(sub, r.right - 2, r.top - 4, r.left + 2);
    const checked = entries(sub).findIndex((b) => b.getAttribute("aria-checked") === "true");
    focusItem(sub, Math.max(0, checked));
    return true;
  }
  function closeSub() {
    if (!sub) return;
    const p = sub._parent;
    sub.remove(); sub = null;
    p?.setAttribute("aria-expanded", "false");
    p?.focus();
  }
  function run(m, btn) {
    const it = m._items[+btn.dataset.i];
    if (!it || btn.getAttribute("aria-disabled") === "true") return;
    if (it.sub) { openSub(btn); return; }
    close();
    it.run?.();
  }

  function onMenuKey(ev) {
    const m = ev.target.closest(".ctx-menu");
    if (!m) return;
    const list = entries(m), i = list.indexOf(document.activeElement);
    const k = ev.key;
    if (k === "ArrowDown" || k === "ArrowUp") { ev.preventDefault(); focusItem(m, i + (k === "ArrowDown" ? 1 : -1)); }
    else if (k === "Home" || k === "End") { ev.preventDefault(); focusItem(m, k === "Home" ? 0 : list.length - 1); }
    else if (k === "ArrowRight" && m === menu) { ev.preventDefault(); if (document.activeElement?.getAttribute("aria-haspopup")) openSub(document.activeElement); }
    else if ((k === "ArrowLeft" || k === "Escape") && m === sub) { ev.preventDefault(); ev.stopPropagation(); closeSub(); }
    else if (k === "Escape") { ev.preventDefault(); ev.stopPropagation(); close(); }
    else if (k === "Tab") { ev.preventDefault(); close(); }
    else if (k === "Enter" || k === " ") { ev.preventDefault(); if (document.activeElement?.dataset.i != null) run(m, document.activeElement); }
    else if (k.length === 1 && /\S/.test(k)) { // type-ahead: the next item starting with that letter
      const n = list.length, start = i + 1;
      for (let j = 0; j < n; j++) {
        const b = list[(start + j) % n];
        if (b.querySelector(".ctx-label").textContent.trim().toLowerCase().startsWith(k.toLowerCase())) { b.focus(); break; }
      }
    }
  }

  /** Open the menu for the shape at a screen point (selecting it first when it is not selected). */
  function openAt(cx, cy) {
    if (!state.design || T().busy()) return false;
    const id = T().hitAt(cx, cy);
    if (!id) return false;
    if (!T().has(id)) T().select([id]);
    const n = T().state().count;
    open(cx, cy, n === 1 ? "Selected shape" : `${n} selected shapes`);
    return true;
  }

  function bindMenu() {
    for (const el of [$("svgBox"), $("canvas")]) {
      el.addEventListener("contextmenu", (ev) => {
        if (performance.now() - openedAt < 800 && menu) { ev.preventDefault(); return; } // a long-press opened it already
        if (openAt(ev.clientX, ev.clientY)) ev.preventDefault(); // empty space: the browser's own menu
      });
      // touch: long-press (iOS sends no contextmenu)
      let lp = null;
      const cancel = () => { if (lp) { clearTimeout(lp.t); lp = null; } };
      el.addEventListener("pointerdown", (ev) => {
        cancel();
        if (ev.pointerType !== "touch" || !ev.isPrimary) return;
        lp = { x: ev.clientX, y: ev.clientY, t: setTimeout(() => { const p = lp; lp = null; if (p) openAt(p.x, p.y); }, LONG_PRESS_MS) };
      });
      el.addEventListener("pointermove", (ev) => { if (lp && Math.hypot(ev.clientX - lp.x, ev.clientY - lp.y) > 10) cancel(); });
      ["pointerup", "pointercancel", "pointerleave"].forEach((t) => el.addEventListener(t, cancel));
    }
    document.addEventListener("keydown", onMenuKey, true);
    document.addEventListener("click", (ev) => {
      const b = ev.target.closest(".ctx-menu [data-i]");
      if (b) { run(b.closest(".ctx-menu"), b); return; }
      if (menu && !ev.target.closest(".ctx-menu")) close(false);
    }, true);
    document.addEventListener("pointerdown", (ev) => { if (menu && !ev.target.closest(".ctx-menu")) close(false); }, true);
    // pointing at an entry opens its submenu, like a desktop menu - only once the pointer really moves
    // (the menu opens under it, and the browser reports "hover" without any movement)
    document.addEventListener("mousemove", (ev) => {
      if (!menu) return;
      if (openPt && Math.hypot(ev.clientX - openPt.x, ev.clientY - openPt.y) < 4) return;
      openPt = null;
      const b = ev.target.closest(".ctx-menu [data-i]");
      if (!b || b === document.activeElement) return;
      if (b.closest(".ctx-menu") === menu) { if (b.getAttribute("aria-haspopup")) openSub(b); else if (sub) { sub.remove(); sub = null; menu.querySelectorAll('[aria-expanded="true"]').forEach((x) => x.setAttribute("aria-expanded", "false")); } }
      b.focus({ preventScroll: true });
    });
    window.addEventListener("resize", () => close(false));
    window.addEventListener("wheel", (ev) => { if (menu && !ev.target.closest(".ctx-menu")) close(false); }, { passive: true });
    // keyboard: Menu key or Shift+F10 opens it for the selection (from the shapes list or the preview)
    document.addEventListener("keydown", (ev) => {
      if (!(ev.key === "ContextMenu" || (ev.key === "F10" && ev.shiftKey)) || menu) return;
      if (!state.project || currentPage !== "editor" || document.querySelector("dialog[open]")) return;
      if (ev.target.matches?.("input, select, textarea") || !T().state().count) return;
      const active = ev.target.closest?.("#objList") && $("objList").querySelector("[role=option].active");
      const r = active ? active.getBoundingClientRect() : T().selectionRect();
      if (!r) return;
      ev.preventDefault();
      const n = T().state().count;
      open(active ? r.left + 24 : (r.left + r.right) / 2, active ? r.bottom : (r.top + r.bottom) / 2, n === 1 ? "Selected shape" : `${n} selected shapes`);
    });
  }

  // ------------------------------------------------------------------ the floating selection toolbar
  let bar = null, barKey = "", posKey = "", raf = 0, quietUntil = 0, quietTimer = 0;

  function viewKey() {
    const v = state.view, m = state.pane === "shapes" ? Shapes.ctm() : null, s = $("stage");
    return [state.pane, v.x, v.y, v.scale, m?.a, m?.e, m?.f, s.clientWidth, s.clientHeight].join("|");
  }
  function wanted() {
    return !!(state.design && T().count() && !menu && !T().busy() && window.matchMedia(WIDE).matches
      && currentPage === "editor" && !document.body.matches(".view-account, .view-admin") && performance.now() >= quietUntil);
  }
  function hideBar() { if (bar) bar.hidden = true; }

  function render(st) {
    const t = T();
    const btn = (k, label, attrs = "") => `<button type="button" class="ghost small" data-sb="${k}" tabindex="-1" ${attrs}>${label}</button>`;
    bar.innerHTML = `
      <label class="sb-style"><span class="visually-hidden">Stitch style</span><select data-sb="style" tabindex="-1" aria-label="Stitch style of the selection">
        ${st.style ? "" : '<option value="" selected disabled>– different –</option>'}
        ${t.styleMenuOptions((v, l) => `<option value="${v}" ${v === st.style ? "selected" : ""}>${esc(l)}</option>`)}</select></label>
      ${st.dirApplies ? `<span class="sb-grp" role="group" aria-label="Direction">${t.presets.map(([a, glyph, name]) =>
        btn(`angle:${a}`, glyph, `aria-pressed="${st.angle === a}" title="${name} (${a}°)" aria-label="Direction: ${name}, ${a} degrees"`)).join("")}</span>` : ""}
      ${st.thickApplies ? `<span class="sb-grp" role="group" aria-label="Thickness">${t.thickness.map(([k, mm, l]) =>
        btn(`thick:${mm}`, l[0], `aria-pressed="${st.spacing === mm}" title="${l} (${mm} mm gap)" aria-label="Thickness: ${l}, ${mm} millimetre gap"`)).join("")}</span>` : ""}
      ${btn("skip", "Don't stitch", `aria-pressed="${st.skipped}"`)}
      ${btn("details", "Details →", `title="All settings of the selection (Design panel)"`)}`;
    const first = bar.querySelector("[data-sb]");
    if (first) first.tabIndex = 0;
  }

  function position() {
    const r = T().selectionRect();
    if (!r) return hideBar();
    const sr = $("stage").getBoundingClientRect(), gap = 10, top0 = 56; // below the floating view switch
    bar.hidden = false;
    const w = bar.offsetWidth, h = bar.offsetHeight;
    let top = r.top - sr.top - h - gap;
    if (top < top0) top = r.bottom - sr.top + gap; // no room above: below the selection
    if (top + h > sr.height - 8) top = Math.max(top0, Math.min(r.top - sr.top - h - gap, sr.height - h - 8));
    let left = (r.left + r.right) / 2 - sr.left - w / 2;
    left = Math.max(8, Math.min(left, sr.width - w - 8));
    if (r.right < sr.left || r.left > sr.right || r.bottom < sr.top || r.top > sr.bottom) return hideBar(); // selection off screen
    bar.style.left = `${Math.round(left)}px`;
    bar.style.top = `${Math.round(top)}px`;
  }

  // Event-driven (no polling): rebuilt when the selection changes, moved when the view moves.
  function update() {
    raf = 0;
    if (!wanted()) { hideBar(); posKey = ""; return; }
    if (!barKey) {
      const st = T().state();
      const focusKey = bar.contains(document.activeElement) ? document.activeElement.dataset.sb : null;
      barKey = JSON.stringify(st); posKey = "";
      render(st);
      if (focusKey) { const f = bar.querySelector(`[data-sb="${CSS.escape(focusKey)}"]`); if (f) { f.tabIndex = 0; f.focus({ preventScroll: true }); } }
    }
    const vk = viewKey();
    if (vk !== posKey || bar.hidden) { posKey = vk; position(); }
  }
  const schedule = () => {
    if (!bar) return;
    if (!wanted()) { hideBar(); posKey = ""; return; } // hide at once (editing, menu, narrow screen, nothing selected)
    if (!raf) raf = requestAnimationFrame(update);
  };
  /** The selection, the shapes, the pane or the window changed. */
  function changed() { barKey = ""; schedule(); }
  /** The view moved (stitch frame drawn, shapes view box changed, stage resized). */
  function moved() { schedule(); }

  function bindBar() {
    bar = document.createElement("div");
    bar.id = "selBar";
    bar.className = "selbar ed-float";
    bar.setAttribute("role", "toolbar");
    bar.setAttribute("aria-label", "Selected shapes");
    bar.hidden = true;
    $("stage").append(bar);
    bar.addEventListener("change", (ev) => { if (ev.target.dataset.sb === "style" && ev.target.value) act.style(ev.target.value); });
    bar.addEventListener("click", (ev) => {
      const b = ev.target.closest("button[data-sb]");
      if (!b) return;
      const [k, v] = b.dataset.sb.split(":");
      if (k === "angle") act.angle(Number(v));
      else if (k === "thick") act.thick(Number(v));
      else if (k === "skip") act.skip(b.getAttribute("aria-pressed") !== "true");
      else if (k === "details") act.details();
    });
    bar.addEventListener("keydown", (ev) => { // toolbar: one tab stop, ← → move between the controls
      if (ev.key !== "ArrowLeft" && ev.key !== "ArrowRight" && ev.key !== "Home" && ev.key !== "End") return;
      if (ev.target.tagName === "SELECT" && (ev.key === "Home" || ev.key === "End")) return;
      const list = [...bar.querySelectorAll("[data-sb]")], i = list.indexOf(ev.target);
      const j = ev.key === "Home" ? 0 : ev.key === "End" ? list.length - 1 : (i + (ev.key === "ArrowRight" ? 1 : -1) + list.length) % list.length;
      ev.preventDefault();
      list.forEach((e, n) => (e.tabIndex = n === j ? 0 : -1));
      list[j].focus();
    });
    // out of the way while the view moves (wheel zoom, dragging), back a moment later
    const quiet = () => { quietUntil = performance.now() + 260; hideBar(); clearTimeout(quietTimer); quietTimer = setTimeout(moved, 280); };
    $("stage").addEventListener("wheel", quiet, { passive: true });
    $("stage").addEventListener("pointermove", (ev) => { if (ev.buttons && !bar.contains(ev.target)) quiet(); });
    window.addEventListener("resize", changed);
    document.addEventListener("click", (ev) => { if (ev.target.closest?.(".ed-views, [data-close-sheet], #btnBackToOpen")) changed(); });
  }

  function init() {
    bindMenu();
    bindBar();
  }

  return { init, changed, moved, close: () => close(false), isOpen: () => !!menu };
})();
