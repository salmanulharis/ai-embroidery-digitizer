// Theme: System (follows the device), Light or Dark - remembered in this browser.
// Loaded in <head>, before the page is drawn, so it never flashes the other theme.
// The stylesheet reads <html data-theme="light|dark">; without it the device setting wins.
const Theme = (() => {
  const KEY = "theme", CHOICES = ["system", "light", "dark"], root = document.documentElement;
  const get = () => {
    try { const t = localStorage.getItem(KEY); return CHOICES.includes(t) && t !== "system" ? t : "system"; } catch (_) { return "system"; }
  };
  function apply(t) {
    if (t === "system") delete root.dataset.theme; else root.dataset.theme = t;
  }
  function sync() {
    const t = get();
    document.querySelectorAll("[data-theme-choice]").forEach((b) => {
      const on = b.dataset.themeChoice === t;
      b.setAttribute("aria-checked", on);
      b.tabIndex = on ? 0 : -1;
      b.classList.toggle("on", on);
    });
  }
  function set(t) {
    if (!CHOICES.includes(t)) return;
    try { if (t === "system") localStorage.removeItem(KEY); else localStorage.setItem(KEY, t); } catch (_) {}
    apply(t);
    sync();
    window.dispatchEvent(new Event("themechange")); // canvases that read theme colours repaint
  }
  /** True when the page is dark now (chosen, or the device's setting). */
  const dark = () => (root.dataset.theme ? root.dataset.theme === "dark" : matchMedia("(prefers-color-scheme: dark)").matches);

  function init() {
    const group = document.getElementById("themePick");
    if (group) {
      group.addEventListener("click", (ev) => { const b = ev.target.closest("[data-theme-choice]"); if (b) set(b.dataset.themeChoice); });
      group.addEventListener("keydown", (ev) => { // radio group: arrows move and choose
        const step = { ArrowRight: 1, ArrowDown: 1, ArrowLeft: -1, ArrowUp: -1 }[ev.key];
        if (!step) return;
        ev.preventDefault();
        const next = CHOICES[(CHOICES.indexOf(get()) + step + CHOICES.length) % CHOICES.length];
        set(next);
        group.querySelector(`[data-theme-choice="${next}"]`)?.focus();
      });
    }
    // following the device: its switch to dark / light repaints the canvases too
    matchMedia("(prefers-color-scheme: dark)").addEventListener("change", () => { if (get() === "system") window.dispatchEvent(new Event("themechange")); });
    sync();
  }
  apply(get());
  return { get, set, dark, init };
})();
