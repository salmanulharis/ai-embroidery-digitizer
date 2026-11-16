"use strict";
/* The Super Admin console: overview, users, plans, per-user usage, analytics, AI overview,
   settings and the audit log. Every action is checked again on the server
   (admin_api.py, auth.require_admin) and written to the audit log there. Uses helpers from
   app.js ($, api, json, esc, toast, busy), Auth, Views, Charts and Account inside
   functions. */

const Admin = (() => {
  let tab = "overview";
  let plans = [], features = [], limits = [];
  let range = { preset: "30", start: "", end: "" };
  const users = { q: "", plan: "", status: "", usage_status: "", sort: "email", page: 1, list: [], total: 0 };
  const usageView = { q: "", plan: "", usage_status: "", sort: "used", page: 1 };
  let auditBefore = null, auditQ = "";
  let detailId = null;
  const main = () => $("adminMain");

  // ------------------------------------------------------------------ formatting
  const day = (t) => (t ? new Date(t * 1000).toLocaleDateString(undefined, { dateStyle: "medium" }) : "–");
  const dayTime = (t) => (t ? new Date(t * 1000).toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short" }) : "Never");
  const isoUTC = (d) => d.toISOString().slice(0, 10);
  const num = (v) => (v == null ? "–" : Number(v).toLocaleString());
  const STATUS_WORD = { ok: "OK", approaching: "Approaching limit", exhausted: "Limit reached", unlimited: "Unlimited" };
  const STATUS_ICON = { ok: "●", approaching: "▲", exhausted: "■", unlimited: "∞" };
  const statusChip = (s) => `<span class="ustatus" data-level="${s}"><span aria-hidden="true">${STATUS_ICON[s]}</span> ${STATUS_WORD[s]}</span>`;
  const allowance = (u) => (u.unlimited ? "Unlimited" : `${num(u.used)} / ${num(u.limit)}`);
  const stat = (label, value, note = "") => `<div class="stat"><span class="stat-label">${esc(label)}</span><span class="stat-value">${value}</span>${note ? `<span class="stat-note">${note}</span>` : ""}</div>`;
  const section = (title, body, extra = "") => `<section class="card admin-card"><div class="row between"><h3>${esc(title)}</h3>${extra}</div>${body}</section>`;

  function rangeParams() {
    if (range.preset === "custom") return { start: range.start, end: range.end };
    const end = new Date();
    const days = range.preset === "today" ? 1 : +range.preset;
    const start = new Date(end.getTime() - (days - 1) * 86400000);
    return { start: isoUTC(start), end: isoUTC(end) };
  }
  const qs = (o) => Object.entries(o).filter(([, v]) => v !== "" && v != null).map(([k, v]) => `${k}=${encodeURIComponent(v)}`).join("&");

  function rangeBar(onChange) {
    const p = rangeParams();
    const btn = (v, label) => `<button type="button" data-range="${v}" aria-pressed="${range.preset === v}" class="${range.preset === v ? "on" : ""}">${label}</button>`;
    const html = `<div class="filters" role="group" aria-label="Date range">
      <div class="seg">${btn("today", "Today")}${btn("7", "Last 7 days")}${btn("30", "Last 30 days")}${btn("90", "Last 90 days")}${btn("custom", "Custom")}</div>
      <span class="custom-range ${range.preset === "custom" ? "" : "hidden"}">
        <label class="small"><span class="visually-hidden">From</span><input type="date" id="rangeStart" value="${p.start}"></label> –
        <label class="small"><span class="visually-hidden">To</span><input type="date" id="rangeEnd" value="${p.end}"></label>
        <button type="button" class="small" id="rangeApply">Apply</button></span>
      <span class="small muted">Days in UTC</span></div>`;
    setTimeout(() => {
      main().querySelectorAll("[data-range]").forEach((b) => (b.onclick = () => {
        range.preset = b.dataset.range;
        if (range.preset === "custom") { const q = rangeParams(); range.start = range.start || q.start; range.end = range.end || q.end; }
        onChange();
      }));
      $("rangeApply")?.addEventListener("click", () => { range.start = $("rangeStart").value; range.end = $("rangeEnd").value; onChange(); });
    });
    return html;
  }

  // ------------------------------------------------------------------ navigation
  async function open(to) {
    Views.show("admin");
    await go(to || tab);
  }
  async function go(t) {
    tab = t;
    $("adminNav").querySelectorAll("[data-tab]").forEach((b) => {
      const on = b.dataset.tab === t;
      b.classList.toggle("on", on);
      if (on) b.setAttribute("aria-current", "page"); else b.removeAttribute("aria-current");
    });
    main().innerHTML = '<p class="muted small">Loading…</p>';
    try {
      await ({ overview, users: usersTab, plans: plansTab, usage: usageTab, analytics: analyticsTab, ai: aiTab, settings: settingsTab, audit: auditTab })[t]();
    } catch (e) {
      main().innerHTML = `<p class="bad">${esc(e.message)}</p>`;
    }
  }
  async function loadPlans() {
    const r = await api("/api/admin/plans");
    plans = r.plans;
    features = r.features;
    limits = r.limits || [];
  }

  // ------------------------------------------------------------------ overview
  async function overview() {
    const a = await api("/api/admin/analytics");
    const u = a.users, g = a.generations, s = a.subscriptions;
    main().innerHTML = `
      <div class="stats-row">
        ${stat("Accounts", num(u.total), `${num(u.active)} active · ${num(u.inactive)} deactivated`)}
        ${stat("Active today", num(u.dau_today), `${num(u.mau)} in the last 30 days`)}
        ${stat("Designs downloaded", num(g.succeeded), "last 30 days")}
        ${stat("Download success", g.success_rate == null ? "–" : `${g.success_rate}%`, `${num(g.failed + g.cancelled)} failed · ${num(g.rejected)} at the limit`)}
        ${stat("At their limit", num(s.exhausted), `${num(s.approaching)} approaching (≥ ${s.approaching_pct}%)`)}
        ${stat("Open designs", num(a.open_projects), "temporary, on the server now")}
      </div>
      ${section("Designs downloaded per day", '<div id="ovChart"></div>', '<button type="button" class="ghost small" data-goto-tab="analytics">All analytics &rarr;</button>')}
      <div class="two-col">
        ${section("Plans", '<div id="ovPlans"></div>', '<button type="button" class="ghost small" data-goto-tab="plans">Manage plans &rarr;</button>')}
        ${section("Most active (30 days)", '<div id="ovTop"></div>', '<button type="button" class="ghost small" data-goto-tab="usage">User usage &rarr;</button>')}
      </div>`;
    Charts.columns($("ovChart"), { data: g.series, title: "Designs downloaded per day", series: SERIES_GEN });
    Charts.hbars($("ovPlans"), s.by_plan.map((p) => ({ label: p.plan + (p.enabled ? "" : " (disabled)"), value: p.n })), { fmt: (v) => `${v} account${v === 1 ? "" : "s"}` });
    Charts.hbars($("ovTop"), g.top_users.map((t) => ({ label: t.name || t.email, value: t.n })), { fmt: num, empty: "No downloads yet." });
    bindTabLinks();
  }
  const SERIES_GEN = [{ key: "succeeded", label: "Downloaded", color: "--good" }, { key: "failed", label: "Failed (not counted)", color: "--critical" },
    { key: "rejected", label: "Refused at the limit", color: "--series-4" }];
  function bindTabLinks() {
    main().querySelectorAll("[data-goto-tab]").forEach((b) => (b.onclick = () => go(b.dataset.gotoTab)));
  }

  // ------------------------------------------------------------------ users
  async function usersTab() {
    if (!plans.length) await loadPlans();
    main().innerHTML = `
      <div class="filters">
        <label class="grow"><span class="visually-hidden">Search users</span><input type="search" id="uq" placeholder="Search name, email or username" value="${esc(users.q)}"></label>
        <label><span class="visually-hidden">Plan</span><select id="uplan"><option value="">All plans</option>${plans.map((p) => `<option value="${esc(p.code)}">${esc(p.name)}</option>`).join("")}</select></label>
        <label><span class="visually-hidden">Account status</span><select id="ustatus"><option value="">Any status</option><option value="active">Active</option><option value="inactive">Deactivated</option></select></label>
        <label><span class="visually-hidden">Usage</span><select id="uusage"><option value="">Any usage</option>${Object.entries(STATUS_WORD).map(([k, v]) => `<option value="${k}">${v}</option>`).join("")}</select></label>
        <button type="button" class="primary" id="addUser">&#10010; Add user</button>
      </div>
      <p class="small muted" id="ucount"></p>
      <div class="tableWrap"><table class="users" id="utable"></table></div>
      <div class="row between pager"><button type="button" class="ghost small" id="uprev">&larr; Previous</button><span class="small muted" id="upage"></span><button type="button" class="ghost small" id="unext">Next &rarr;</button></div>`;
    $("uplan").value = users.plan; $("ustatus").value = users.status; $("uusage").value = users.usage_status;
    let timer;
    $("uq").oninput = () => { clearTimeout(timer); timer = setTimeout(() => { users.q = $("uq").value; users.page = 1; loadUsers(); }, 250); };
    $("uplan").onchange = () => { users.plan = $("uplan").value; users.page = 1; loadUsers(); };
    $("ustatus").onchange = () => { users.status = $("ustatus").value; users.page = 1; loadUsers(); };
    $("uusage").onchange = () => { users.usage_status = $("uusage").value; users.page = 1; loadUsers(); };
    $("uprev").onclick = () => { users.page--; loadUsers(); };
    $("unext").onclick = () => { users.page++; loadUsers(); };
    $("addUser").onclick = () => openUserDialog("create");
    $("utable").onclick = userAction;
    await loadUsers();
  }
  async function loadUsers() {
    const r = await api(`/api/admin/users?${qs({ q: users.q, plan: users.plan, status: users.status, usage_status: users.usage_status, sort: users.sort, page: users.page, per_page: 50 })}`);
    users.list = r.users; users.total = r.total;
    $("ucount").textContent = `${r.total} account${r.total === 1 ? "" : "s"}${users.q || users.plan || users.status || users.usage_status ? " match" : ""}`;
    const me = Auth.user();
    $("utable").innerHTML = `<tr><th scope="col">Account</th><th scope="col">Plan</th><th scope="col">Designs</th><th scope="col">Status</th><th scope="col">Last active</th><th scope="col"><span class="visually-hidden">Actions</span></th></tr>` +
      r.users.map((u) => {
        const e = esc(u.email), self = u.id === me.id;
        const actions = `<button type="button" class="ghost small" data-act="details" aria-label="Details of ${e}">Details</button>` + (u.env_admin
          ? `<span class="small muted">${self ? "You · " : ""}set in the deployment secrets</span>`
          : `<button type="button" class="ghost small" data-act="edit" aria-label="Edit ${e}">Edit</button>
             <button type="button" class="ghost small" data-act="password" aria-label="Reset password of ${e}">Password</button>
             ${self ? "" : `<button type="button" class="ghost small" data-act="active" aria-label="${u.active ? "Deactivate" : "Activate"} ${e}">${u.active ? "Deactivate" : "Activate"}</button>
             <button type="button" class="ghost small danger" data-act="delete" aria-label="Delete ${e}">Delete</button>`}`);
        return `<tr data-id="${u.id}" class="${u.active ? "" : "off"}">
          <td data-label="Account"><b>${esc(u.name || u.email)}</b>${u.name ? `<br><span class="small muted">${e}</span>` : ""}${u.username ? `<br><span class="small muted">@${esc(u.username)}</span>` : ""}
            ${u.role === "super_admin" ? ' <span class="badge on">Super Admin</span>' : ""}</td>
          <td data-label="Plan">${esc(u.plan)}${u.expired ? ` <span class="small warn-text">(${esc(u.assigned_plan)} ended)</span>` : ""}${u.expires_at && !u.expired ? `<br><span class="small muted">until ${day(u.expires_at)}</span>` : ""}</td>
          <td data-label="Designs">${allowance(u)} ${u.limit_source === "override" ? '<span class="badge" title="Individual allowance">custom</span>' : ""}<br>${statusChip(u.usage_status)}</td>
          <td data-label="Status"><span class="status ${u.active ? "good" : "bad"}">${u.active ? "&#9679; Active" : "&#9675; Deactivated"}</span></td>
          <td data-label="Last active" class="small muted">${dayTime(u.last_active_at)}<br>joined ${day(u.created_at)}</td>
          <td class="user-actions">${actions}</td></tr>`;
      }).join("");
    const pages = Math.max(1, Math.ceil(r.total / r.per_page));
    $("upage").textContent = `Page ${r.page} of ${pages}`;
    $("uprev").disabled = r.page <= 1; $("unext").disabled = r.page >= pages;
  }
  async function userAction(ev) {
    const b = ev.target.closest("[data-act]");
    if (!b) return;
    const id = +b.closest("tr").dataset.id;
    const u = users.list.find((x) => x.id === id) || usageList.find((x) => x.id === id);
    if (!u) return;
    const act = b.dataset.act;
    if (act === "details") return openDetail(id);
    if (act === "edit") return openUserDialog("edit", u);
    if (act === "password") return openUserDialog("password", u);
    try {
      if (act === "active") {
        if (u.active && !confirm(`Deactivate ${u.email}?\n\nThey are signed out everywhere and their open design is removed.`)) return;
        await api(`/api/admin/users/${u.id}`, json("PATCH", { active: !u.active }));
        toast(u.active ? `${u.email} is deactivated and signed out everywhere.` : `${u.email} can sign in again.`, true);
      } else if (act === "delete") {
        if (!confirm(`Delete ${u.email}?\n\nTheir account, plan, usage history and AI settings are deleted. This cannot be undone.`)) return;
        await api(`/api/admin/users/${u.id}`, { method: "DELETE" });
        toast(`${u.email} was deleted.`, true);
      } else if (act === "reset") {
        if (!confirm(`Reset the usage counter of ${u.email}?\n\nThey can download ${u.limit ?? "their allowance of"} new designs again ${"this period"}.`)) return;
        await api(`/api/admin/users/${u.id}/usage/reset`, { method: "POST" });
        toast(`Usage of ${u.email} was reset.`, true);
      }
    } catch (e) {
      return toast(e.message);
    }
    tab === "usage" ? loadUsage() : loadUsers();
  }

  // one dialog: add a user, edit them, or set a new password
  let dialog = null; // { mode, user, done }
  function openUserDialog(mode, u = null) {
    dialog = { mode, user: u, done: false };
    const titles = { create: "Add user", edit: `Edit ${u?.email}`, password: `New password for ${u?.email}` };
    const notes = {
      create: "The user signs in with this email (or username) and password. There is no public sign-up. New accounts get the default plan unless you choose another.",
      edit: "The user signs in with the new email or username from now on. A new role takes effect at their next sign-in (they are signed out).",
      password: "The user is signed out everywhere and signs in with the new password.",
    };
    $("userDialogTitle").textContent = titles[mode];
    $("userDialogNote").textContent = notes[mode];
    const pw = mode === "password";
    ["userEmailRow", "userNameRow", "userDisplayRow", "userRolePlanRow"].forEach((id) => $(id).classList.toggle("hidden", pw));
    $("userPlanRow").classList.toggle("hidden", mode !== "create");
    $("userFormPlan").innerHTML = `<option value="">Default plan</option>` + plans.filter((p) => p.enabled).map((p) => `<option value="${p.id}">${esc(p.name)}</option>`).join("");
    $("userFormName").value = u?.username || "";
    $("userFormDisplay").value = u?.name || u?.display_name || "";
    $("userFormRole").value = u?.role || "user";
    $("userFormRole").disabled = !!u && u.id === Auth.user().id;
    $("userPasswordRow").classList.toggle("hidden", mode === "edit");
    $("userFormEmail").value = u?.email || "";
    $("userFormPassword").value = mode === "edit" ? "" : Auth.generatePassword();
    [$("userFormEmail"), $("userFormName"), $("userFormPassword"), $("userFormDisplay")].forEach((el) => (el.readOnly = false));
    $("userGenerate").disabled = false;
    $("userSave").classList.remove("hidden");
    $("userSave").textContent = { create: "Create user", edit: "Save", password: "Set password" }[mode];
    $("userDialog").querySelector(".row.end [data-close]").textContent = "Cancel";
    Auth.formStatus("userDialogStatus", "");
    $("userDialog").showModal();
    (pw ? $("userFormPassword") : $("userFormEmail")).focus();
  }
  async function submitUserDialog(ev) {
    ev.preventDefault();
    if (!dialog || dialog.done) return $("userDialog").close();
    const email = $("userFormEmail").value.trim(), password = $("userFormPassword").value;
    const st = (m) => Auth.formStatus("userDialogStatus", m, true);
    if (dialog.mode !== "password" && !/^[^@\s]+@[^@\s]+\.[^@\s]{2,}$/.test(email)) return st("Enter a valid email address.");
    const username = $("userFormName").value.trim().toLowerCase();
    if (dialog.mode !== "password" && username && !/^[a-z0-9][a-z0-9._-]{2,31}$/.test(username))
      return st("A username has 3–32 characters: letters, digits, dot, dash or underscore, starting with a letter or digit.");
    if (dialog.mode !== "edit" && password.length < 10) return st("The password needs at least 10 characters.");
    const display_name = $("userFormDisplay").value.trim(), role = $("userFormRole").value;
    $("userSave").disabled = true;
    try {
      if (dialog.mode === "create") {
        const plan = $("userFormPlan").value;
        await api("/api/admin/users", json("POST", { email, password, username: username || null, display_name: display_name || null, role, plan_id: plan ? +plan : null }));
      } else if (dialog.mode === "edit") {
        const body = { email, username, display_name };
        if (!$("userFormRole").disabled && role !== dialog.user.role) body.role = role;
        await api(`/api/admin/users/${dialog.user.id}`, json("PATCH", body));
      } else await api(`/api/admin/users/${dialog.user.id}/password`, json("POST", { password }));
    } catch (e) {
      return st(e.message);
    } finally {
      $("userSave").disabled = false;
    }
    refreshCurrent();
    if (dialog.mode === "edit") {
      $("userDialog").close();
      return toast("Changes saved.", true);
    }
    // the password is shown once: keep the dialog open so it can still be copied
    dialog.done = true;
    [$("userFormEmail"), $("userFormName"), $("userFormPassword"), $("userFormDisplay")].forEach((el) => (el.readOnly = true));
    $("userGenerate").disabled = true;
    $("userSave").classList.add("hidden");
    $("userDialog").querySelector(".row.end [data-close]").textContent = "Done";
    Auth.formStatus("userDialogStatus", dialog.mode === "create"
      ? "User created. Copy the password now and share it privately – it is not shown again."
      : "Password set. Copy it now and share it privately – it is not shown again.");
  }
  async function copyPassword() {
    const v = $("userFormPassword").value;
    if (!v) return;
    try { await navigator.clipboard.writeText(v); } catch (_) { $("userFormPassword").select(); document.execCommand("copy"); }
    Auth.formStatus("userDialogStatus", "Password copied to the clipboard.");
  }
  function refreshCurrent() {
    if (tab === "users") loadUsers(); else if (tab === "usage") loadUsage(); else if (tab === "overview") overview();
    if (detailId && $("detailDialog").open) openDetail(detailId);
  }

  // ------------------------------------------------------------------ one account: details, plan, allowance
  const expiryInput = (t) => (t ? new Date(t * 1000).toISOString().slice(0, 10) : "");
  const endOfDayUTC = (v) => (v ? Date.parse(v + "T23:59:59Z") / 1000 : null);

  async function openDetail(id) {
    detailId = id;
    if (!plans.length) await loadPlans();
    let d;
    try { d = await api(`/api/admin/users/${id}`); } catch (e) { return toast(e.message); }
    const u = d.user, e = d.entitlement, ov = e.override || {};
    const ovf = ov.features || {};
    const assigned = e.subscription.plan || e.plan;
    const st = e.unlimited ? "unlimited" : e.used >= e.limit ? "exhausted" : e.used / Math.max(1, e.limit) >= 0.8 ? "approaching" : "ok";
    const isSuper = u.role === "super_admin";
    $("detailTitle").textContent = u.name || u.email;
    $("detailBody").innerHTML = `
      <p class="small muted">${esc(u.email)}${u.username ? ` · @${esc(u.username)}` : ""} · ${Auth.ROLE_LABEL[u.role]} ·
        <span class="status ${u.active ? "good" : "bad"}">${u.active ? "Active" : "Deactivated"}</span> · joined ${day(u.created_at)} · last active ${dayTime(u.last_active_at)}</p>
      <div class="stats-row">
        ${stat("Plan in effect", esc(e.plan.name), e.subscription.expired ? `${esc(assigned.name)} ended ${day(e.subscription.expires_at)}` : e.subscription.expires_at ? `until ${day(e.subscription.expires_at)}` : "no end date")}
        ${stat(e.period === "month" ? "Designs this month" : "Designs on this plan", e.unlimited ? `${num(e.used)} · ∞` : `${num(e.used)} / ${num(e.limit)}`, statusChip(st))}
        ${stat("Active days", num(d.active_days_30), "last 30 days")}
        ${stat("Signed in", `${d.sessions.length}`, `session${d.sessions.length === 1 ? "" : "s"} · ${d.open_projects} open design${d.open_projects === 1 ? "" : "s"}`)}
      </div>
      ${isSuper ? '<p class="small muted">Super Admins always have every feature and no limit; plan changes apply if the role changes.</p>' : ""}
      <form class="detail-form" id="dPlanForm">
        <h3>Plan</h3>
        <div class="form-row">
          <label><span class="lbl">Plan</span><select id="dPlan">${plans.map((p) => `<option value="${p.id}" ${p.id === assigned.id ? "selected" : ""} ${!p.enabled && p.id !== assigned.id ? "disabled" : ""}>${esc(p.name)}${p.enabled ? "" : " (disabled)"}</option>`).join("")}</select></label>
          <label><span class="lbl">Valid until <span class="muted">(empty = no end)</span></span><input type="date" id="dExpiry" value="${expiryInput(e.subscription.expires_at)}"></label>
          <button type="submit" class="primary">Save plan</button>
        </div>
        <p class="small muted">No payment is taken: changes are manual and recorded in the plan history and audit log.</p>
      </form>
      <form class="detail-form" id="dLimitForm">
        <h3>Design allowance</h3>
        <div class="form-row">
          <label><span class="lbl">Allowance</span><select id="dLimitType">
            <option value="">From the plan (${e.plan.limit_type === "unlimited" ? "unlimited" : `${e.plan.generation_limit} per ${e.plan.period === "month" ? "month" : "plan"}`})</option>
            <option value="limited" ${ov.limit_type === "limited" ? "selected" : ""}>Custom number</option>
            <option value="unlimited" ${ov.limit_type === "unlimited" ? "selected" : ""}>Unlimited</option></select></label>
          <label id="dLimitRow" class="${ov.limit_type === "limited" ? "" : "hidden"}"><span class="lbl">Designs</span><input type="number" id="dLimit" min="0" max="1000000" step="1" value="${ov.generation_limit ?? e.plan.generation_limit ?? 0}"></label>
          <button type="submit" class="primary">Save allowance</button>
          <button type="button" class="ghost" id="dReset">Reset usage counter</button>
        </div>
      </form>
      <form class="detail-form" id="dFeatForm">
        <h3>Features</h3>
        <div class="feature-grid">${features.map((f) => {
          const planOn = e.plan.features[f.key] ?? f.default;
          const cur = ovf[f.key] === undefined ? "" : String(ovf[f.key]);
          return `<label><span class="lbl">${esc(f.label)} <i class="help-inline" title="${esc(f.help)}">?</i></span><select data-feature="${f.key}">
            <option value="" ${cur === "" ? "selected" : ""}>From the plan (${planOn ? "on" : "off"})</option>
            <option value="true" ${cur === "true" ? "selected" : ""}>On for this account</option>
            <option value="false" ${cur === "false" ? "selected" : ""}>Off for this account</option></select></label>`;
        }).join("")}</div>
        ${limits.length ? `<h3>Limits</h3><div class="feature-grid">${limits.map((l) => {
          const planVal = e.plan.limits?.[l.key] ?? l.default;
          return `<label><span class="lbl">${esc(l.label)} <i class="help-inline" title="${esc(l.help)}">?</i></span>
            <input type="number" data-limit="${l.key}" min="${l.min}" max="${l.max}" step="1" value="${ov.limits?.[l.key] ?? ""}"
              placeholder="From the plan (${planVal})" aria-describedby="dLimitsHint"></label>`;
        }).join("")}</div><p class="small muted" id="dLimitsHint">Empty = the plan's value.</p>` : ""}
        <label><span class="lbl">Note <span class="muted">(why; visible to admins only)</span></span><input type="text" id="dNote" maxlength="300" value="${esc(ov.note || "")}"></label>
        <button type="submit" class="primary">Save features and limits</button>
      </form>
      <h3>Designs downloaded – last 30 days</h3>
      <div id="dChart"></div>
      <div class="two-col">
        <div><h3>Totals</h3>${d.totals.length ? `<table class="data"><tr><th scope="col">Activity</th><th scope="col">Requests</th><th scope="col">Succeeded</th><th scope="col">Failed</th></tr>
          ${d.totals.map((t) => `<tr><td>${esc(t.label)}</td><td>${num(t.n)}</td><td>${num(t.succeeded)}</td><td>${num(t.failed + t.rejected)}</td></tr>`).join("")}</table>` : '<p class="small muted">No activity yet.</p>'}</div>
        <div><h3>Plan history</h3><ul class="history">${d.history.map((h) => `<li><b>${esc(h.event.replace("_", " "))}</b>
          ${h.from_plan && h.from_plan !== h.to_plan ? `${esc(h.from_plan)} → ` : ""}${esc(h.to_plan || "")}
          ${h.event === "extended" ? ` · ${h.to_expires_at ? `until ${day(h.to_expires_at)}` : "no end date"}` : ""}
          <span class="small muted">· ${dayTime(h.at)} · ${esc(h.by)}</span></li>`).join("")}</ul></div>
      </div>
      <h3>Recent activity</h3>
      ${d.recent.length ? `<div class="tableWrap"><table class="data"><tr><th scope="col">When</th><th scope="col">Activity</th><th scope="col">Result</th><th scope="col">AI</th><th scope="col">Time</th><th scope="col">Tokens</th></tr>
        ${d.recent.map((r) => `<tr><td>${dayTime(r.created_at)}</td><td>${esc(r.kind === "generation" ? `Download ${(r.feature || "").split(":")[1]?.toUpperCase() || ""}` : r.kind.replace("ai_", "AI "))}${r.counted ? ' <span class="badge">counted</span>' : ""}</td>
          <td>${esc(r.status)}${r.error ? ` <span class="small muted">(${esc(r.error)})</span>` : ""}</td><td>${esc([r.provider, r.model].filter(Boolean).join(" · ") || "–")}</td>
          <td>${r.duration_ms != null ? `${(r.duration_ms / 1000).toFixed(1)} s` : "–"}</td><td>${r.tokens_in || r.tokens_out ? `${num(r.tokens_in)} / ${num(r.tokens_out)}` : "–"}</td></tr>`).join("")}</table></div>`
        : '<p class="small muted">No activity yet.</p>'}
      <div class="row end detail-actions">
        ${d.sessions.length && u.id !== Auth.user().id ? '<button type="button" class="ghost" id="dRevoke">Sign out everywhere</button>' : ""}
      </div>`;
    Charts.columns($("dChart"), { data: d.daily, title: "Designs downloaded", series: [{ key: "succeeded", label: "Downloaded", color: "--series-1" }] });
    $("dLimitType").onchange = () => $("dLimitRow").classList.toggle("hidden", $("dLimitType").value !== "limited");
    $("dPlanForm").onsubmit = (ev) => detailSave(ev, async () => {
      const planId = +$("dPlan").value, exp = endOfDayUTC($("dExpiry").value);
      if (planId !== assigned.id) await api(`/api/admin/users/${id}/subscription`, json("PUT", { plan_id: planId, keep_expiry: false, expires_at: exp }));
      else if (exp !== (e.subscription.expires_at ? endOfDayUTC(expiryInput(e.subscription.expires_at)) : null) || e.subscription.expired)
        await api(`/api/admin/users/${id}/expiry`, json("PUT", { expires_at: exp }));
      return "Plan saved.";
    });
    $("dLimitForm").onsubmit = (ev) => detailSave(ev, async () => {
      const t = $("dLimitType").value;
      await api(`/api/admin/users/${id}/overrides`, json("PUT", { limit_type: t || null, generation_limit: t === "limited" ? +$("dLimit").value : null }));
      return t === "unlimited" ? "Unlimited downloads granted." : t ? "Custom allowance saved." : "The plan's allowance applies again.";
    });
    $("dReset").onclick = () => {
      if (!confirm(`Reset the usage counter of ${u.email}? Their ${num(e.used)} counted design${e.used === 1 ? "" : "s"} no longer count against the allowance. Usage history is kept for analytics.`)) return;
      detailSave(null, async () => { await api(`/api/admin/users/${id}/usage/reset`, { method: "POST" }); return "Usage counter reset."; });
    };
    $("dFeatForm").onsubmit = (ev) => detailSave(ev, async () => {
      const f = {};
      $("dFeatForm").querySelectorAll("[data-feature]").forEach((s) => (f[s.dataset.feature] = s.value === "" ? null : s.value === "true"));
      const lim = {};
      $("dFeatForm").querySelectorAll("[data-limit]").forEach((i) => (lim[i.dataset.limit] = i.value === "" ? null : +i.value));
      await api(`/api/admin/users/${id}/overrides`, json("PUT", { features: f, limits: lim, note: $("dNote").value }));
      return "Features and limits saved.";
    });
    $("dRevoke")?.addEventListener("click", () => {
      if (!confirm(`Sign ${u.email} out on every device? Their open design is removed.`)) return;
      detailSave(null, async () => { const r = await api(`/api/admin/users/${id}/sessions/revoke`, { method: "POST" }); return `${r.ended} session${r.ended === 1 ? "" : "s"} ended.`; });
    });
    if (!$("detailDialog").open) $("detailDialog").showModal();
  }
  async function detailSave(ev, fn) {
    ev?.preventDefault();
    try {
      const msg = await fn();
      toast(msg, true);
    } catch (e) {
      return toast(e.message);
    }
    refreshCurrent();
  }

  // ------------------------------------------------------------------ plans
  async function plansTab() {
    await loadPlans();
    const featLabel = (p) => (features.filter((f) => (p.features[f.key] ?? f.default)).map((f) => esc(f.label)).join(", ") || "None") +
      limits.map((l) => `<br><span class="muted">${esc(l.label)}: ${num(p.limits?.[l.key] ?? l.default)}</span>`).join("");
    const pv = await api("/api/admin/presets");
    main().innerHTML = `
      <div class="row between"><p class="small muted">Plans decide each account's design allowance and features. Accounts can also get individual overrides (Users &rarr; Details).
        No payments are processed yet: plan changes are manual.</p>
        <button type="button" class="primary" id="addPlan">&#10010; Add plan</button></div>
      <div class="tableWrap"><table class="users"><tr><th scope="col">Plan</th><th scope="col">Designs</th><th scope="col">Features</th><th scope="col">Accounts</th><th scope="col">Status</th><th scope="col"><span class="visually-hidden">Actions</span></th></tr>
      ${plans.map((p) => `<tr class="${p.enabled ? "" : "off"}">
        <td data-label="Plan"><b>${esc(p.name)}</b> <span class="small muted">${esc(p.code)}</span>${p.is_default ? ' <span class="badge on">Default</span>' : ""}<br><span class="small muted">${esc(p.description)}</span></td>
        <td data-label="Designs">${p.limit_type === "unlimited" ? "Unlimited" : `${num(p.generation_limit)} ${p.period === "month" ? "per month" : "in total"}`}</td>
        <td data-label="Features" class="small">${featLabel(p)}</td>
        <td data-label="Accounts">${num(p.subscribers)}</td>
        <td data-label="Status"><span class="status ${p.enabled ? "good" : "bad"}">${p.enabled ? "&#9679; Enabled" : "&#9675; Disabled"}</span></td>
        <td class="user-actions"><button type="button" class="ghost small" data-plan="${p.id}">Edit</button></td></tr>`).join("")}</table></div>
      ${section("Built-in presets", `<p class="small muted">Accounts get the Basic presets with the "Built-in presets" feature and the Premium ones
        with "Premium presets". Move a preset to the other tier here. Saved presets: ${num(pv.saved.presets)} by ${num(pv.saved.accounts)}
        account${pv.saved.accounts === 1 ? "" : "s"} (their settings are private and not shown).</p>
        <div class="tableWrap"><table class="users"><tr><th scope="col">Preset</th><th scope="col">Sets</th><th scope="col">Tier</th></tr>
        ${pv.builtins.map((b) => `<tr><td data-label="Preset"><b>${esc(b.label)}</b><br><span class="small muted">${esc(b.description)}</span>
          ${b.needs.length ? `<br><span class="small muted">Also needs: ${esc(b.needs.join(", "))}</span>` : ""}</td>
          <td data-label="Sets" class="small">${b.settings} setting${b.settings === 1 ? "" : "s"} · ${esc(b.kinds.join(", "))}</td>
          <td data-label="Tier"><select class="preset-tier" data-tier="${esc(b.id)}" aria-label="Tier of ${esc(b.label)}">
            ${["basic", "premium"].map((t) => `<option value="${t}" ${t === b.tier ? "selected" : ""}>${t === "basic" ? "Basic" : "Premium"}${t === b.default_tier ? " (its own)" : ""}</option>`).join("")}
          </select></td></tr>`).join("")}</table></div>`)}`;
    main().querySelectorAll("[data-tier]").forEach((sel) => (sel.onchange = async () => {
      try {
        await api("/api/admin/presets", json("PUT", { access: { [sel.dataset.tier]: sel.value } }));
        toast("Preset tier saved.", true);
      } catch (e) { toast(e.message); plansTab(); }
    }));
    $("addPlan").onclick = () => openPlan(null);
    main().querySelectorAll("[data-plan]").forEach((b) => (b.onclick = () => openPlan(plans.find((p) => p.id === +b.dataset.plan))));
  }
  let planEditing = null;
  function openPlan(p) {
    planEditing = p;
    $("planDialogTitle").textContent = p ? `Edit ${p.name}` : "Add plan";
    $("planName").value = p?.name || "";
    $("planCode").value = p?.code || "";
    $("planDesc").value = p?.description || "";
    $("planLimitType").value = p?.limit_type || "limited";
    $("planLimit").value = p?.generation_limit ?? 10;
    $("planPeriod").value = p?.period || "month";
    $("planEnabled").checked = p ? p.enabled : true;
    $("planDefault").checked = !!p?.is_default;
    $("planDefault").disabled = !!p?.is_default;
    $("planOrder").value = p?.sort_order ?? (Math.max(0, ...plans.map((x) => x.sort_order)) + 1);
    $("planPrice").value = p?.price_cents ?? "";
    $("planCurrency").value = p?.currency || "";
    $("planPriceRef").value = p?.provider_price_ref || "";
    $("planFeatures").innerHTML = features.map((f) => `<label class="check"><input type="checkbox" data-pf="${f.key}" ${(p ? p.features[f.key] ?? f.default : f.default) ? "checked" : ""}>
      <span>${esc(f.label)}<br><span class="small muted">${esc(f.help)}</span></span></label>`).join("");
    $("planLimits").innerHTML = limits.map((l) => `<label><span class="lbl">${esc(l.label)}</span>
      <input type="number" data-pl="${l.key}" min="${l.min}" max="${l.max}" step="1" value="${p?.limits?.[l.key] ?? l.default}">
      <span class="small muted">${esc(l.help)}</span></label>`).join("");
    $("planDelete").classList.toggle("hidden", !p || p.is_default || p.subscribers > 0);
    syncPlanForm();
    Auth.formStatus("planStatus", p && p.subscribers ? `${p.subscribers} account${p.subscribers === 1 ? "" : "s"} use this plan; changes apply to them at once.` : "");
    $("planDialog").showModal();
    $("planName").focus();
  }
  function syncPlanForm() {
    const unl = $("planLimitType").value === "unlimited";
    $("planLimitRow").classList.toggle("hidden", unl);
  }
  async function savePlan(ev) {
    ev.preventDefault();
    const f = {};
    $("planFeatures").querySelectorAll("[data-pf]").forEach((c) => (f[c.dataset.pf] = c.checked));
    const lim = {};
    $("planLimits").querySelectorAll("[data-pl]").forEach((i) => (lim[i.dataset.pl] = i.value === "" ? 0 : +i.value));
    const body = {
      name: $("planName").value.trim(), code: $("planCode").value.trim().toLowerCase(), description: $("planDesc").value.trim(),
      limit_type: $("planLimitType").value, generation_limit: +$("planLimit").value || 0, period: $("planPeriod").value,
      features: f, limits: lim, enabled: $("planEnabled").checked, sort_order: +$("planOrder").value || 0,
      price_cents: $("planPrice").value === "" ? null : +$("planPrice").value, currency: $("planCurrency").value.trim() || null,
      provider_price_ref: $("planPriceRef").value.trim() || null,
    };
    if (!planEditing || !planEditing.is_default) body.is_default = $("planDefault").checked;
    if (body.is_default && !body.enabled) return Auth.formStatus("planStatus", "The default plan must be enabled.", true);
    try {
      if (planEditing) await api(`/api/admin/plans/${planEditing.id}`, json("PATCH", body));
      else await api("/api/admin/plans", json("POST", body));
    } catch (e) {
      return Auth.formStatus("planStatus", e.message, true);
    }
    $("planDialog").close();
    toast(planEditing ? "Plan saved." : "Plan added.", true);
    plansTab();
  }
  async function deletePlan() {
    if (!planEditing || !confirm(`Delete the ${planEditing.name} plan? This cannot be undone.`)) return;
    try { await api(`/api/admin/plans/${planEditing.id}`, { method: "DELETE" }); } catch (e) { return Auth.formStatus("planStatus", e.message, true); }
    $("planDialog").close();
    toast("Plan deleted.", true);
    plansTab();
  }

  // ------------------------------------------------------------------ usage per user
  let usageList = [];
  async function usageTab() {
    if (!plans.length) await loadPlans();
    main().innerHTML = `
      <div class="filters">
        <label class="grow"><span class="visually-hidden">Search users</span><input type="search" id="gq" placeholder="Search name, email or username" value="${esc(usageView.q)}"></label>
        <label><span class="visually-hidden">Plan</span><select id="gplan"><option value="">All plans</option>${plans.map((p) => `<option value="${esc(p.code)}">${esc(p.name)}</option>`).join("")}</select></label>
        <label><span class="visually-hidden">Usage</span><select id="gusage"><option value="">Any usage</option>${Object.entries(STATUS_WORD).map(([k, v]) => `<option value="${k}">${v}</option>`).join("")}</select></label>
        <label><span class="visually-hidden">Sort</span><select id="gsort"><option value="used">Most used</option><option value="remaining">Least remaining</option><option value="active">Recently active</option><option value="email">Email</option></select></label>
      </div>
      <p class="small muted" id="gcount"></p>
      <div class="tableWrap"><table class="users" id="gtable"></table></div>
      <div class="row between pager"><button type="button" class="ghost small" id="gprev">&larr; Previous</button><span class="small muted" id="gpage"></span><button type="button" class="ghost small" id="gnext">Next &rarr;</button></div>`;
    $("gplan").value = usageView.plan; $("gusage").value = usageView.usage_status; $("gsort").value = usageView.sort;
    let timer;
    $("gq").oninput = () => { clearTimeout(timer); timer = setTimeout(() => { usageView.q = $("gq").value; usageView.page = 1; loadUsage(); }, 250); };
    for (const [id, k] of [["gplan", "plan"], ["gusage", "usage_status"], ["gsort", "sort"]]) $(id).onchange = () => { usageView[k] = $(id).value; usageView.page = 1; loadUsage(); };
    $("gprev").onclick = () => { usageView.page--; loadUsage(); };
    $("gnext").onclick = () => { usageView.page++; loadUsage(); };
    $("gtable").onclick = userAction;
    await loadUsage();
  }
  async function loadUsage() {
    const r = await api(`/api/admin/users?${qs({ ...usageView, per_page: 50 })}`);
    usageList = r.users;
    $("gcount").textContent = `${r.total} account${r.total === 1 ? "" : "s"}`;
    $("gtable").innerHTML = `<tr><th scope="col">Account</th><th scope="col">Plan</th><th scope="col">Used</th><th scope="col">Limit</th><th scope="col">Remaining</th><th scope="col">All time</th><th scope="col">Last active</th><th scope="col"><span class="visually-hidden">Actions</span></th></tr>` +
      r.users.map((u) => `<tr data-id="${u.id}" class="${u.active ? "" : "off"}">
        <td data-label="Account"><b>${esc(u.name || u.email)}</b>${u.name ? `<br><span class="small muted">${esc(u.email)}</span>` : ""}</td>
        <td data-label="Plan">${esc(u.plan)}</td>
        <td data-label="Used">${num(u.used)}</td>
        <td data-label="Limit">${u.unlimited ? "Unlimited" : num(u.limit)}${u.limit_source === "override" ? ' <span class="badge">custom</span>' : ""}</td>
        <td data-label="Remaining">${u.unlimited ? "∞" : num(u.remaining)} ${statusChip(u.usage_status)}</td>
        <td data-label="All time">${num(u.total_generations)}</td>
        <td data-label="Last active" class="small muted">${dayTime(u.last_active_at)}</td>
        <td class="user-actions"><button type="button" class="ghost small" data-act="details">Details</button>
          ${!u.unlimited && u.used ? '<button type="button" class="ghost small" data-act="reset">Reset usage</button>' : ""}</td></tr>`).join("");
    const pages = Math.max(1, Math.ceil(r.total / r.per_page));
    $("gpage").textContent = `Page ${r.page} of ${pages}`;
    $("gprev").disabled = r.page <= 1; $("gnext").disabled = r.page >= pages;
  }

  // ------------------------------------------------------------------ analytics
  async function analyticsTab() {
    const p = rangeParams();
    const a = await api(`/api/admin/analytics?${qs(p)}`);
    const u = a.users, g = a.generations, s = a.subscriptions, ai = a.ai;
    const span = `${Charts.fmtDay(a.range.from)} – ${Charts.fmtDay(a.range.to)}`;
    main().innerHTML = rangeBar(analyticsTab) + `
      <p class="small muted">${esc(span)} · ${a.range.days} day${a.range.days === 1 ? "" : "s"}. Built from usage metadata only – never files, prompts or designs.</p>
      ${section("Users", `<div class="stats-row">
          ${stat("Accounts", num(u.total), `${num(u.active)} active · ${num(u.inactive)} deactivated`)}
          ${stat("New accounts", num(u.new), "in this period")}
          ${stat("Active in period", num(u.active_in_range), `${u.dau_avg} per day on average`)}
          ${stat("Monthly active", num(u.mau), "30 days to the end date")}
        </div><div class="two-col"><div><h4 class="sub">Daily active users</h4><div id="aDau"></div></div><div><h4 class="sub">New accounts per day</h4><div id="aNew"></div></div></div>`)}
      ${section("Designs", `<div class="stats-row">
          ${stat("Requests", num(g.requests), "first downloads of a design")}
          ${stat("Downloaded", num(g.succeeded), g.success_rate == null ? "no attempts" : `${g.success_rate}% of attempts`)}
          ${stat("Failed", num(g.failed + g.cancelled), "never counted")}
          ${stat("Refused at the limit", num(g.rejected))}
          ${stat("Per active user", g.per_active_user)}
        </div><h4 class="sub">Per day</h4><div id="aGen"></div>
        <div class="two-col"><div><h4 class="sub">Top users</h4><div id="aTop"></div></div><div><h4 class="sub">Most used features</h4><div id="aFeat"></div></div></div>`)}
      ${section("Subscriptions", `<div class="stats-row">
          ${stat("Unlimited", num(s.unlimited), "plan, override or Super Admin")}
          ${stat("Approaching the limit", num(s.approaching), `≥ ${s.approaching_pct}% used`)}
          ${stat("Limit reached", num(s.exhausted))}
          ${stat("Ended subscriptions", num(s.expired), "default plan applies")}
        </div><h4 class="sub">Accounts per plan</h4><div id="aPlans"></div>`)}
      ${section("AI services", aiBlock(ai))}`;
    Charts.line($("aDau"), { data: u.dau_series, key: "n", label: "Active users", title: "Daily active users" });
    Charts.columns($("aNew"), { data: u.new_series, title: "New accounts", series: [{ key: "n", label: "New accounts", color: "--series-1" }] });
    Charts.columns($("aGen"), { data: g.series, title: "Designs per day", series: SERIES_GEN });
    Charts.hbars($("aTop"), g.top_users.map((t) => ({ label: t.name || t.email, value: t.n })), { fmt: num, empty: "No downloads in this period." });
    Charts.hbars($("aFeat"), a.features.map((f) => ({ label: f.label, value: f.n })), { fmt: num });
    Charts.hbars($("aPlans"), s.by_plan.map((x) => ({ label: x.plan + (x.enabled ? "" : " (disabled)"), value: x.n })), { fmt: num });
    bindAiBlock(ai);
  }
  function aiBlock(ai) {
    const t = ai.total;
    return `<div class="stats-row">
        ${stat("AI requests", num(t.requests), "with an AI service (built-in answers not counted)")}
        ${stat("Succeeded", num(t.succeeded), t.requests ? `${Math.round((100 * t.succeeded) / t.requests)}%` : "")}
        ${stat("Failed", num(t.failed), "fell back to the built-in result")}
        ${stat("Average time", t.avg_ms ? `${(t.avg_ms / 1000).toFixed(1)} s` : "–")}
        ${stat("Tokens", t.tokens_in || t.tokens_out ? `${Charts.fmtNum(t.tokens_in)} in · ${Charts.fmtNum(t.tokens_out)} out` : "–", "where the provider reports them")}
      </div>
      <div class="two-col"><div><h4 class="sub">Requests by service</h4><div id="aiProv"></div></div><div><h4 class="sub">Requests by task</h4><div id="aiTask"></div></div></div>
      <h4 class="sub">By model</h4>
      ${ai.by_model.length ? `<div class="tableWrap"><table class="data"><tr><th scope="col">Service</th><th scope="col">Model</th><th scope="col">Requests</th><th scope="col">Succeeded</th><th scope="col">Failed</th><th scope="col">Avg time</th><th scope="col">Tokens in / out</th></tr>
        ${ai.by_model.map((m) => `<tr><td>${esc(m.provider)}</td><td>${esc(m.model)}</td><td>${num(m.requests)}</td><td>${num(m.succeeded)}</td><td>${num(m.failed)}</td>
          <td>${m.avg_ms ? `${(m.avg_ms / 1000).toFixed(1)} s` : "–"}</td><td>${m.tokens_in || m.tokens_out ? `${num(m.tokens_in)} / ${num(m.tokens_out)}` : "–"}</td></tr>`).join("")}</table></div>`
        : '<p class="small muted">No AI requests in this period.</p>'}`;
  }
  function bindAiBlock(ai) {
    Charts.hbars($("aiProv"), ai.by_provider.map((p) => ({ label: p.provider, value: p.requests, note: p.failed ? `${p.failed} failed` : "" })), { fmt: num, empty: "No AI requests in this period." });
    Charts.hbars($("aiTask"), ai.by_task.map((p) => ({ label: p.label, value: p.requests })), { fmt: num, empty: "No AI requests in this period." });
  }

  // ------------------------------------------------------------------ AI overview
  async function aiTab() {
    const r = await api(`/api/admin/ai?${qs(rangeParams())}`);
    const acc = r.accounts.providers;
    main().innerHTML = rangeBar(aiTab) + section("Services", `
      <p class="small muted">Each account uses its own keys, stored encrypted. Keys are never shown here. The app's own keys (from the server's environment)
        are used only for accounts whose plan or override includes "Use the app's AI keys".</p>
      <div class="tableWrap"><table class="data"><tr><th scope="col">Service</th><th scope="col">App key on the server</th><th scope="col">Accounts with their own key</th><th scope="col">Used for advice</th><th scope="col">Used for clean-up</th></tr>
      ${r.providers.map((p) => { const a = acc[p.id] || {}; return `<tr><td><b>${esc(p.label)}</b> <span class="small muted">${p.capabilities.map((c) => (c === "edit" ? "clean-up" : "advice")).join(", ")}</span></td>
        <td>${p.server_configured ? '<span class="status good">✓ configured</span>' : '<span class="muted">–</span>'}</td>
        <td>${num(a.accounts_with_key || 0)}</td><td>${num(a.used_for_advice || 0)}</td><td>${num(a.used_for_cleanup || 0)}</td></tr>`; }).join("")}</table></div>
      <p class="small muted">${num(r.accounts.accounts_configured)} account${r.accounts.accounts_configured === 1 ? "" : "s"} use an AI service.</p>`) +
      section("Usage", aiBlock({ ...r.usage }));
    bindAiBlock(r.usage);
  }

  // ------------------------------------------------------------------ settings
  async function settingsTab() {
    const r = await api("/api/admin/settings");
    const groups = {};
    r.settings.forEach((s) => (groups[s.group] = groups[s.group] || []).push(s));
    main().innerHTML = `<form id="setForm">${Object.entries(groups).map(([g, list]) => section(g, `<div class="settings-grid">${list.map((s) => `
        <label><span class="lbl">${esc(s.label)}</span><input type="number" name="${s.key}" min="${s.min}" max="${s.max}" step="1" value="${s.value}" required>
        <span class="small muted">${esc(s.help)} (${s.min}–${s.max}, default ${s.default})</span></label>`).join("")}</div>`)).join("")}
      <div class="row end"><button type="submit" class="primary">Save settings</button></div></form>
      ${section("Clean-up", `<p class="small muted">${num(r.open_projects)} temporary design${r.open_projects === 1 ? " is" : "s are"} on the server now. Clean-up runs automatically every 5 minutes;
        run it now to remove closed, idle and expired designs at once.</p><button type="button" id="runClean">Run clean-up now</button>`)}
      ${section("What is stored", `<ul class="small plain-list">
        <li><b>Kept:</b> accounts (email, username, name, role, password hash), sign-in sessions (hashed), plans and plan history, per-account overrides,
          encrypted AI keys, saved presets (settings, name and description only; deleted with the account), usage metadata (what was done, when, success, AI service and model, duration, token counts) and this audit log.</li>
        <li><b>Never kept:</b> uploaded files, generated stitch files, designs, prompts or AI answers. Designs exist only as temporary files while in use.</li>
        <li>Usage records and audit entries are deleted after the retention periods above.</li></ul>`)}`;
    $("setForm").onsubmit = async (ev) => {
      ev.preventDefault();
      const body = {};
      new FormData($("setForm")).forEach((v, k) => (body[k] = +v));
      try { await api("/api/admin/settings", json("PUT", body)); toast("Settings saved.", true); } catch (e) { toast(e.message); }
    };
    $("runClean").onclick = async () => {
      try {
        const r2 = await busy("Cleaning up…", () => api("/api/admin/maintenance/run", { method: "POST" }));
        toast(`Clean-up done: ${r2.projects || 0} design${r2.projects === 1 ? "" : "s"} and ${r2.sessions || 0} expired session${r2.sessions === 1 ? "" : "s"} removed.`, true);
        settingsTab();
      } catch (_) {}
    };
  }

  // ------------------------------------------------------------------ audit log
  const AUDIT_LABEL = {
    "user.create": "Created account", "user.update": "Edited account", "user.activate": "Activated account", "user.deactivate": "Deactivated account",
    "user.delete": "Deleted account", "user.password_reset": "Set a new password", "user.sessions_revoked": "Signed out everywhere",
    "subscription.upgraded": "Upgraded plan", "subscription.downgraded": "Downgraded plan", "subscription.changed": "Changed plan",
    "subscription.expiry": "Changed plan end date", "entitlement.update": "Changed allowance / features", "entitlement.unlimited": "Granted unlimited",
    "usage.reset": "Reset usage", "plan.create": "Created plan", "plan.update": "Edited plan", "plan.delete": "Deleted plan",
    "settings.update": "Changed settings", "maintenance.run": "Ran clean-up", "presets.access": "Moved built-in presets",
  };
  async function auditTab(more = false) {
    if (!more) {
      auditBefore = null;
      main().innerHTML = `<div class="filters"><label class="grow"><span class="visually-hidden">Search the audit log</span>
          <input type="search" id="aq" placeholder="Search action, admin or account" value="${esc(auditQ)}"></label></div>
        <div class="tableWrap"><table class="data" id="atable"><tr><th scope="col">When</th><th scope="col">Admin</th><th scope="col">Action</th><th scope="col">Account / item</th><th scope="col">Details</th></tr></table></div>
        <div class="row"><button type="button" class="ghost" id="amore">Load older entries</button></div>`;
      let timer;
      $("aq").oninput = () => { clearTimeout(timer); timer = setTimeout(() => { auditQ = $("aq").value; auditTab(); }, 250); };
      $("amore").onclick = () => auditTab(true);
    }
    const r = await api(`/api/admin/audit?${qs({ q: auditQ, before: auditBefore, limit: 100 })}`);
    $("atable").insertAdjacentHTML("beforeend", r.entries.map((e) => `<tr><td class="nowrap">${dayTime(e.at)}</td><td>${esc(e.actor)}</td>
      <td>${esc(AUDIT_LABEL[e.action] || e.action)}</td><td>${esc(e.target || e.target_type)}</td>
      <td class="small">${Object.entries(e.detail).map(([k, v]) => `${esc(k)}: ${esc(v)}`).join("<br>")}</td></tr>`).join(""));
    auditBefore = r.entries.length ? r.entries[r.entries.length - 1].id : auditBefore;
    $("amore").disabled = r.entries.length < 100;
    if (!more && !r.entries.length) $("atable").insertAdjacentHTML("beforeend", '<tr><td colspan="5" class="muted">No entries.</td></tr>');
  }

  // ------------------------------------------------------------------ wiring
  function init() {
    $("adminNav").addEventListener("click", (ev) => { const b = ev.target.closest("[data-tab]"); if (b) go(b.dataset.tab); });
    $("userForm").addEventListener("submit", submitUserDialog);
    $("userGenerate").onclick = () => { $("userFormPassword").value = Auth.generatePassword(); Auth.formStatus("userDialogStatus", "New password generated."); };
    $("userCopy").onclick = copyPassword;
    $("planForm").addEventListener("submit", savePlan);
    $("planLimitType").onchange = syncPlanForm;
    $("planDelete").onclick = deletePlan;
    $("detailDialog").addEventListener("close", () => (detailId = null));
  }

  return { init, open, go };
})();
