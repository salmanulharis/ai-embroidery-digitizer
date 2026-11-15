"use strict";
/* Small, dependency-free SVG charts for the admin console (analytics.py sends daily
   aggregates; nothing here sees raw usage records).

   - columns(): daily values, optionally stacked series (2 px surface gap between
     segments, 4 px rounded data end, one y axis, recessive grid, hover tooltip per day);
   - line(): one daily series with a crosshair tooltip;
   - hbars(): ranked categories as labelled horizontal bars (HTML);
   - every chart gets a "Show as table" view, and a legend when it has 2+ series.
   Colours come from CSS tokens (--series-N, --good, --critical) so dark mode is set in
   style.css; text always uses text colours, never the series colour. */

const Charts = (() => {
  const NS = "http://www.w3.org/2000/svg";
  const H = 180, PAD = { t: 10, r: 8, b: 22, l: 34 };
  let tip = null;

  const fmtNum = (v) => (v >= 10000 ? `${Math.round(v / 1000)}k` : v >= 1000 ? `${(v / 1000).toFixed(1)}k` : String(Math.round(v * 10) / 10));
  const fmtDay = (iso, long = false) => new Date(iso + "T00:00:00Z").toLocaleDateString(undefined,
    long ? { weekday: "short", day: "numeric", month: "short", year: "numeric", timeZone: "UTC" } : { day: "numeric", month: "short", timeZone: "UTC" });

  function niceMax(v) {
    if (v <= 4) return 4;
    const p = 10 ** Math.floor(Math.log10(v)), n = v / p;
    return (n <= 1 ? 1 : n <= 2 ? 2 : n <= 5 ? 5 : 10) * p;
  }
  function el(tag, attrs = {}, parent) {
    const e = document.createElementNS(NS, tag);
    for (const [k, v] of Object.entries(attrs)) e.setAttribute(k, v);
    if (parent) parent.append(e);
    return e;
  }
  function showTip(ev, html) {
    if (!tip) { tip = document.createElement("div"); tip.className = "chart-tip"; tip.setAttribute("role", "tooltip"); document.body.append(tip); }
    tip.innerHTML = html;
    tip.hidden = false;
    const r = tip.getBoundingClientRect();
    let x = ev.clientX + 14, y = ev.clientY - r.height - 10;
    if (x + r.width > innerWidth - 8) x = ev.clientX - r.width - 14;
    if (y < 8) y = ev.clientY + 16;
    tip.style.left = `${x}px`;
    tip.style.top = `${y}px`;
  }
  const hideTip = () => tip && (tip.hidden = true);

  function legend(series) {
    if (series.length < 2) return "";
    return `<div class="chart-legend">${series.map((s) => `<span><i style="background:var(${s.color})"></i>${esc(s.label)}</span>`).join("")}</div>`;
  }
  function tableView(data, series, caption) {
    const rows = data.map((d) => `<tr><td>${fmtDay(d.day)}</td>${series.map((s) => `<td>${d[s.key] || 0}</td>`).join("")}</tr>`).join("");
    return `<details class="chart-table"><summary>Show as table</summary><div class="tableWrap"><table class="data">
      <caption class="visually-hidden">${esc(caption)}</caption>
      <tr><th scope="col">Day (UTC)</th>${series.map((s) => `<th scope="col">${esc(s.label)}</th>`).join("")}</tr>${rows}</table></div></details>`;
  }

  /** Frame: grid lines + y labels + sparse x labels. Returns {svg, x(i), y(v), bw}. */
  function frame(box, data, max) {
    const W = Math.max(280, box.clientWidth || 600);
    const svg = el("svg", { viewBox: `0 0 ${W} ${H}`, width: "100%", height: H, class: "chart-svg", role: "img" });
    const iw = W - PAD.l - PAD.r, ih = H - PAD.t - PAD.b, n = Math.max(1, data.length);
    const step = iw / n;
    const x = (i) => PAD.l + i * step;
    const y = (v) => PAD.t + ih - (v / max) * ih;
    for (let k = 0; k <= 4; k++) {
      const v = (max / 4) * k, yy = y(v);
      el("line", { x1: PAD.l, x2: W - PAD.r, y1: yy, y2: yy, class: k ? "grid" : "axis" }, svg);
      el("text", { x: PAD.l - 6, y: yy + 3, "text-anchor": "end", class: "tick" }, svg).textContent = fmtNum(v);
    }
    const every = Math.ceil(n / Math.max(2, Math.floor(iw / 64)));
    data.forEach((d, i) => {
      if (i % every === 0) el("text", { x: x(i) + step / 2, y: H - 6, "text-anchor": "middle", class: "tick" }, svg).textContent = fmtDay(d.day);
    });
    return { svg, x, y, step, ih, W };
  }

  function columns(box, { data, series, title }) {
    const totals = data.map((d) => series.reduce((a, s) => a + (d[s.key] || 0), 0));
    const max = niceMax(Math.max(0, ...totals));
    box.innerHTML = legend(series);
    const { svg, x, y, step } = frame(box, data, max);
    svg.setAttribute("aria-label", `${title}: ${totals.reduce((a, b) => a + b, 0)} in total over ${data.length} days`);
    const bw = Math.max(2, Math.min(28, step * 0.62));
    const R = Math.min(4, bw / 2);
    data.forEach((d, i) => {
      let base = 0;
      const cx = x(i) + (step - bw) / 2;
      const segs = series.filter((s) => d[s.key] > 0);
      segs.forEach((s, j) => {
        const v = d[s.key], y0 = y(base), y1 = y(base + v);
        const top = j === segs.length - 1;
        const gap = j > 0 ? 2 : 0; // surface gap between stacked segments
        const h = Math.max(1, y0 - y1 - gap);
        const yy = y0 - gap - h;
        const r = top ? Math.min(R, h) : 0;
        const p = `M${cx},${yy + h} V${yy + r} Q${cx},${yy} ${cx + r},${yy} H${cx + bw - r} Q${cx + bw},${yy} ${cx + bw},${yy + r} V${yy + h} Z`;
        el("path", { d: p, fill: `var(${s.color})` }, svg);
        base += v;
      });
      const hit = el("rect", { x: x(i), y: PAD.t, width: step, height: H - PAD.t - PAD.b, class: "hit" }, svg);
      const html = `<b>${fmtDay(d.day, true)}</b>` + series.map((s) => `<div><i style="background:var(${s.color})"></i>${esc(s.label)} <b>${d[s.key] || 0}</b></div>`).join("");
      hit.addEventListener("pointermove", (ev) => { hit.classList.add("on"); showTip(ev, html); });
      hit.addEventListener("pointerleave", () => { hit.classList.remove("on"); hideTip(); });
    });
    box.append(svg);
    box.insertAdjacentHTML("beforeend", tableView(data, series, title));
  }

  function line(box, { data, key, label, color = "--series-1", title }) {
    const max = niceMax(Math.max(0, ...data.map((d) => d[key] || 0)));
    box.innerHTML = "";
    const { svg, x, y, step } = frame(box, data, max);
    svg.setAttribute("aria-label", `${title}: highest ${Math.max(0, ...data.map((d) => d[key] || 0))} per day`);
    const pts = data.map((d, i) => [x(i) + step / 2, y(d[key] || 0)]);
    el("path", { d: pts.map((p, i) => `${i ? "L" : "M"}${p[0].toFixed(1)},${p[1].toFixed(1)}`).join(" "), class: "line", stroke: `var(${color})` }, svg);
    const cross = el("line", { y1: PAD.t, y2: H - PAD.b, class: "cross", visibility: "hidden" }, svg);
    const dot = el("circle", { r: 4.5, fill: `var(${color})`, class: "dot", visibility: "hidden" }, svg);
    const hit = el("rect", { x: PAD.l, y: PAD.t, width: step * data.length, height: H - PAD.t - PAD.b, class: "hit" }, svg);
    hit.addEventListener("pointermove", (ev) => {
      const r = svg.getBoundingClientRect(), sx = (ev.clientX - r.left) * (svg.viewBox.baseVal.width / r.width);
      const i = Math.max(0, Math.min(data.length - 1, Math.floor((sx - PAD.l) / step)));
      const [px, py] = pts[i];
      cross.setAttribute("x1", px); cross.setAttribute("x2", px); cross.setAttribute("visibility", "visible");
      dot.setAttribute("cx", px); dot.setAttribute("cy", py); dot.setAttribute("visibility", "visible");
      showTip(ev, `<b>${fmtDay(data[i].day, true)}</b><div>${esc(label)} <b>${data[i][key] || 0}</b></div>`);
    });
    hit.addEventListener("pointerleave", () => { cross.setAttribute("visibility", "hidden"); dot.setAttribute("visibility", "hidden"); hideTip(); });
    box.append(svg);
    box.insertAdjacentHTML("beforeend", tableView(data, [{ key, label }], title));
  }

  /** rows: [{label, value, note?}] ranked; one colour (it is one measure). */
  function hbars(box, rows, { color = "--series-1", fmt = String, empty = "No data in this period." } = {}) {
    if (!rows.length) { box.innerHTML = `<p class="muted small">${esc(empty)}</p>`; return; }
    const max = Math.max(1, ...rows.map((r) => r.value));
    box.innerHTML = `<ul class="hbars">${rows.map((r) => `<li>
      <span class="hb-label" title="${esc(r.label)}">${esc(r.label)}</span>
      <span class="hb-track">${r.value > 0 ? `<span class="hb-bar" style="width:${Math.max(1.5, (100 * r.value) / max)}%;background:var(${color})"></span>` : ""}</span>
      <span class="hb-val">${fmt(r.value)}${r.note ? ` <span class="muted">${esc(r.note)}</span>` : ""}</span></li>`).join("")}</ul>`;
  }

  return { columns, line, hbars, fmtNum, fmtDay };
})();
