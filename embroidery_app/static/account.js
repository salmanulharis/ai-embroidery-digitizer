"use strict";
/* The signed-in user's side of plans and usage: the allowance chip in the header, the
   "My account" page (plan, allowance, features, AI status, profile, sessions), downloads
   that explain a used-up allowance instead of failing, and the switch between the design
   editor and the account / admin pages. The server decides every limit (entitlements.py,
   usage.py); this file only shows them. Uses helpers from app.js ($, api, json, esc,
   toast, busy) and Auth, inside functions. */

const Views = (() => {
  let current = null;
  function show(name) {
    current = name;
    document.body.classList.toggle("view-account", name === "account");
    document.body.classList.toggle("view-admin", name === "admin");
    window.scrollTo(0, 0);
    const title = name === "account" ? $("accountTitle") : name === "admin" ? $("adminTitle") : null;
    title?.focus({ preventScroll: true });
  }
  function close() {
    show(null);
    $("btnUser").focus();
  }
  return { show, close, current: () => current };
})();

const Account = (() => {
  let data = null; // /api/me/account

  const when = (t, opts = { dateStyle: "medium" }) => (t ? new Date(t * 1000).toLocaleString(undefined, opts) : "");
  const whenLong = (t) => when(t, { dateStyle: "medium", timeStyle: "short" });

  /** "ok" | "approaching" | "exhausted" | "unlimited" - always shown with words, not colour alone. */
  function level(u) {
    if (u.unlimited) return "unlimited";
    if (u.remaining <= 0) return "exhausted";
    return u.used / Math.max(1, u.limit) >= 0.8 ? "approaching" : "ok";
  }
  const periodWord = (u) => (u.period === "month" ? "this month" : "on your plan");

  async function refresh() {
    try { data = await api("/api/me/account"); } catch (_) { return; }
    renderChip();
    if (Views.current() === "account") render();
  }

  function renderChip() {
    const u = data.usage, chip = $("usageChip");
    const lv = level(u);
    $("usageChipText").textContent = u.unlimited ? `${data.plan.name} · Unlimited`
      : lv === "exhausted" ? `${data.plan.name} · Limit reached` : `${data.plan.name} · ${u.remaining} of ${u.limit} left`;
    chip.dataset.level = lv;
    chip.title = u.unlimited ? "Unlimited design downloads" : `${u.used} of ${u.limit} design downloads used ${periodWord(u)}`;
    chip.setAttribute("aria-label", `Your plan: ${chip.title}. Open my account`);
    chip.classList.remove("hidden");
  }

  function meter(u) {
    if (u.unlimited) return `<p class="big-num">Unlimited</p><p class="muted small">Downloads are not limited on your plan. ${u.used} design${u.used === 1 ? "" : "s"} ${periodWord(u)}.</p>`;
    const pct = Math.min(100, (100 * u.used) / Math.max(1, u.limit));
    const lv = level(u);
    const words = { ok: "", approaching: "Almost used up", exhausted: "Limit reached" }[lv];
    return `<p class="big-num">${u.remaining} <span class="muted">of ${u.limit} left</span></p>
      <div class="meter" role="progressbar" aria-valuemin="0" aria-valuemax="${u.limit}" aria-valuenow="${u.used}"
        aria-label="Designs used ${periodWord(u)}" data-level="${lv}"><span style="width:${pct}%"></span></div>
      <p class="small muted">${u.used} used ${periodWord(u)}${u.resets_at ? ` · resets ${when(u.resets_at)}` : ""}${words ? ` · <b class="lv-${lv}">${words}</b>` : ""}</p>`;
  }

  function validity(s) {
    if (s.expired) return `<p class="small warn-text">Your ${esc(s.assigned_plan || "")} plan ended${s.expires_at ? ` on ${when(s.expires_at)}` : ""}. The ${esc(data.plan.name)} plan applies until your administrator renews it.</p>`;
    return `<p class="small muted">${s.expires_at ? `Valid until ${when(s.expires_at)}` : "No end date"}${s.started_at ? ` · since ${when(s.started_at)}` : ""}</p>`;
  }

  function render() {
    const u = data.user;
    $("accountTitle").textContent = `Welcome${u.display_name ? `, ${u.display_name}` : ""}`;
    $("accountSub").textContent = `Signed in as ${u.email}${u.username ? ` (@${u.username})` : ""}`;
    $("accPlan").innerHTML = `
      <div class="row between"><h3 id="accPlanTitle">Your plan</h3><span class="badge on">${esc(data.plan.name)}</span></div>
      ${data.plan.description ? `<p class="small muted">${esc(data.plan.description)}</p>` : ""}
      <h4 class="sub">Design downloads <i data-help="quota"></i></h4>
      ${meter(data.usage)}
      ${validity(data.subscription)}
      <h4 class="sub">Included</h4>
      <ul class="feature-check">${data.features.map((f) => `<li class="${f.on ? "on" : "off"}"><span aria-hidden="true">${f.on ? "✓" : "–"}</span>
        ${esc(f.label)}<span class="visually-hidden">: ${f.on ? "included" : "not included"}</span></li>`).join("")}</ul>
      <p class="small muted">Need more designs or features? Contact your administrator about an upgrade.</p>`;
    const ai = data.ai;
    const aiLine = (label, x) => x
      ? `<li><b>${esc(label)}:</b> ${esc(x.label)} · ${esc(x.model)} ${x.ready ? `<span class="status good">✓ ${x.own_key ? "your key" : "app access"}</span>` : `<span class="status bad">✗ not set up</span>`}</li>`
      : `<li><b>${esc(label)}:</b> <span class="muted">built-in (no AI service)</span></li>`;
    $("accAi").innerHTML = `<h3>AI settings</h3>
      ${ai.allowed ? `<ul class="plain">${aiLine("Advice & stitch checks", ai.advice)}${aiLine("Picture clean-up", ai.cleanup)}</ul>
        <p class="small muted">Your keys are stored encrypted and used only for your requests. They are never shown again.</p>`
        : `<p class="small">AI services are not included in your plan. The built-in assistant still suggests settings and checks stitches.</p>`}
      <button type="button" id="accAiOpen">&#9881; Open AI settings</button>`;
    $("accAiOpen").onclick = () => AI.open();
    $("accProfile").innerHTML = `<h3>Profile</h3>
      <form id="profileForm" class="stack">
        <label><span class="lbl">Name</span><input type="text" id="profileName" maxlength="80" value="${esc(u.display_name || "")}" autocomplete="name"></label>
        <p class="small muted">Email ${esc(u.email)}${u.username ? ` · username ${esc(u.username)}` : ""} – your administrator can change these.</p>
        <div class="row"><button type="submit" class="primary">Save name</button><button type="button" class="ghost" id="profilePw">&#128273; Change password</button></div>
      </form>`;
    $("profileForm").onsubmit = saveProfile;
    $("profilePw").onclick = () => Auth.openOwnPassword();
    const others = data.sessions.filter((s) => !s.current).length;
    $("accSessions").innerHTML = `<h3>Where you are signed in</h3>
      <ul class="sessions">${data.sessions.map((s) => `<li>
        <span><b>${esc(s.device)}</b>${s.current ? ' <span class="badge on">This device</span>' : ""}<br>
        <span class="small muted">Active ${whenLong(s.last_seen_at)} · ${s.kept ? `signed in until ${when(s.expires_at)}` : "until the browser closes"}</span></span>
        ${s.current ? "" : `<button type="button" class="ghost small" data-end="${esc(s.id)}">Sign out</button>`}</li>`).join("")}</ul>
      <div class="row">${others ? '<button type="button" class="ghost" id="endOthers">Sign out all other devices</button>' : ""}
        <button type="button" class="ghost" id="accLogout">&#8618; Sign out here</button></div>
      <p class="small muted">Signing out removes the design you have open from the server.</p>`;
    $("accSessions").querySelectorAll("[data-end]").forEach((b) => (b.onclick = () => endSession(b.dataset.end)));
    $("endOthers")?.addEventListener("click", endOthers);
    $("accLogout").onclick = () => Auth.signOut();
  }

  async function saveProfile(ev) {
    ev.preventDefault();
    try {
      const r = await api("/api/me/profile", json("PATCH", { display_name: $("profileName").value }));
      Auth.setUser(r.user);
      toast("Your name was saved.", true);
      await refresh();
    } catch (e) { toast(e.message); }
  }
  async function endSession(sid) {
    try { await api(`/api/me/sessions/${encodeURIComponent(sid)}`, { method: "DELETE" }); toast("That device was signed out.", true); } catch (e) { toast(e.message); }
    refresh();
  }
  async function endOthers() {
    if (!confirm("Sign out on every other device? Designs open there are removed from the server.")) return;
    try { const r = await api("/api/me/sessions/revoke-others", { method: "POST" }); toast(`${r.ended} other session${r.ended === 1 ? "" : "s"} signed out.`, true); } catch (e) { toast(e.message); }
    refresh();
  }

  async function open() {
    Views.show("account");
    if (!data) await refresh();
    render();
  }

  // ------------------------------------------------------------------ downloads
  /** Note in the export dialog: what this download costs. Returns the server's answer
   *  (charged, usage, plan features) for the dialog, or null when it could not be asked. */
  async function quotaNote(pid) {
    const note = $("quotaNote");
    let q;
    try { q = await api(`/api/projects/${pid}/quota`); } catch (_) { note.classList.add("hidden"); return null; }
    const u = q.usage;
    note.dataset.level = q.charged ? "ok" : level(u);
    note.textContent = q.charged ? "Already counted – download this design in any format as often as you like."
      : u.unlimited ? "Your plan has unlimited design downloads."
      : u.remaining > 0 ? `Your first download of this design uses 1 of your ${u.remaining} remaining design${u.remaining === 1 ? "" : "s"} ${periodWord(u)} (${u.plan} plan). Other formats of it are then free.`
      : `You have used ${u.limit === 1 ? "your 1 design download" : `all ${u.limit} design downloads`} ${periodWord(u)}. Contact your administrator about an upgrade.`;
    note.classList.remove("hidden");
    return q;
  }

  async function download(url, pid) {
    const res = await busy("Preparing your files…", async () => {
      const r = await fetch(url);
      if (r.ok) return r;
      let body = {};
      try { body = await r.json(); } catch (_) {}
      if (r.status === 401) Auth.sessionEnded();
      const err = new Error(body.detail || r.statusText);
      Object.assign(err, { status: r.status, code: body.code, usage: body.usage });
      throw err;
    }, { quiet: true }).catch((e) => {
      if (e.code === "quota_exceeded") showLimit(e);
      else toast(e.message);
      return null;
    });
    if (res) {
      const name = /filename="([^"]+)"/.exec(res.headers.get("content-disposition") || "")?.[1] || "design";
      const blob = await res.blob();
      const a = document.createElement("a");
      a.href = URL.createObjectURL(blob);
      a.download = name;
      document.body.append(a);
      a.click();
      a.remove();
      setTimeout(() => URL.revokeObjectURL(a.href), 30000);
    }
    await refresh();
    if (pid) quotaNote(pid);
  }

  function showLimit(e) {
    const u = e.usage || {};
    $("quotaBody").innerHTML = `<p>${esc(e.message)}</p>
      ${u.limit != null ? `<div class="meter" data-level="exhausted" aria-hidden="true"><span style="width:100%"></span></div>
      <p class="small muted">${u.used} of ${u.limit} designs used ${u.period === "month" ? "this month" : "on your plan"}${u.resets_at ? ` · resets ${when(u.resets_at)}` : ""}.</p>` : ""}
      <p class="small">You can still prepare designs and create stitch previews. Ask your administrator if you need more downloads.</p>`;
    $("quotaDialog").showModal();
  }

  function init() {
    $("usageChip").onclick = open;
    document.querySelectorAll("[data-view-close]").forEach((b) => (b.onclick = Views.close));
    document.addEventListener("ai-saved", refresh);
    document.addEventListener("account-changed", refresh);
    refresh();
  }

  return { init, open, refresh, download, quotaNote, level };
})();
