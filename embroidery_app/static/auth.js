"use strict";
/* Accounts in the browser: the sign-in screen, the account menu (change password, sign
   out), and the Super Admin's user management. The server decides everything (auth.py):
   this file only shows what it allows. Uses helpers from app.js ($, api, json, esc, toast)
   inside functions, after app.js has loaded. */

const Auth = (() => {
  let user = null;
  let waiting = null; // resolves the sign-in promise
  let users = [];
  const ROLE_LABEL = { super_admin: "Super Admin", user: "User" };
  const isAdmin = () => user?.role === "super_admin";

  // ------------------------------------------------------------------ sign in / out
  /** The signed-in account; shows the sign-in screen first when there is none. */
  async function start(config) {
    $("loginDays").textContent = config.session_days || 30;
    try {
      setUser((await api("/api/auth/me")).user);
      return user;
    } catch (e) {
      if (e.status !== 401) throw e;
    }
    return showLogin();
  }

  function showLogin(note = "") {
    document.body.classList.remove("booting");
    document.body.classList.add("signed-out");
    $("loginNote").textContent = note || "Use the email and password from your administrator.";
    $("loginError").classList.add("hidden");
    $("loginPassword").value = "";
    requestAnimationFrame(() => ($("loginEmail").value ? $("loginPassword") : $("loginEmail")).focus());
    return new Promise((resolve) => (waiting = resolve));
  }

  async function submitLogin(ev) {
    ev.preventDefault();
    const email = $("loginEmail").value.trim(), password = $("loginPassword").value;
    if (!email || !password) return loginError("Enter your email and password.");
    $("loginSubmit").disabled = true;
    $("loginSubmit").textContent = "Signing in…";
    try {
      const r = await api("/api/auth/login", json("POST", { email, password, remember: $("loginRemember").checked }));
      if (user && user.id !== r.user.id) return location.reload(); // another account: start clean
      setUser(r.user);
      document.body.classList.remove("signed-out");
      $("loginPassword").value = "";
      waiting?.(user);
      waiting = null;
    } catch (e) {
      loginError(e.message);
    } finally {
      $("loginSubmit").disabled = false;
      $("loginSubmit").textContent = "Sign in";
    }
  }
  function loginError(msg) {
    $("loginError").textContent = msg;
    $("loginError").classList.remove("hidden");
    $("loginPassword").focus();
  }

  /** Any request answered 401 while signed in: the session expired or was ended. */
  function sessionEnded() {
    if (!user || document.body.classList.contains("signed-out")) return;
    $("loginEmail").value = user.email;
    showLogin("Your session has ended. Sign in again to continue where you left off.");
  }

  async function signOut() {
    try { await api("/api/auth/logout", { method: "POST" }); } catch (_) {}
    try { sessionStorage.clear(); } catch (_) {}
    location.reload();
  }

  function setUser(u) {
    user = u;
    $("userEmail").textContent = u.email;
    $("userMenuEmail").textContent = u.email;
    $("userAvatar").textContent = u.email.slice(0, 1).toUpperCase();
    $("userRole").textContent = ROLE_LABEL[u.role] || u.role;
    $("btnUser").setAttribute("aria-label", `Account: ${u.email}`);
    $("btnUser").title = u.email;
    $("btnUsers").classList.toggle("hidden", !isAdmin());
  }

  // ------------------------------------------------------------------ account menu
  function toggleMenu(open = $("userMenu").classList.contains("hidden")) {
    $("userMenu").classList.toggle("hidden", !open);
    $("btnUser").setAttribute("aria-expanded", open);
    if (open) $("userMenu").querySelector("button:not(.hidden)").focus();
  }

  // ------------------------------------------------------------------ change own password
  function openOwnPassword() {
    toggleMenu(false);
    ["pwCurrent", "pwNew", "pwRepeat"].forEach((id) => ($(id).value = ""));
    formStatus("pwStatus", "");
    $("pwDialog").showModal();
  }
  async function submitOwnPassword(ev) {
    ev.preventDefault();
    const current = $("pwCurrent").value, password = $("pwNew").value;
    if (password.length < 10) return formStatus("pwStatus", "The new password needs at least 10 characters.", true);
    if (password !== $("pwRepeat").value) return formStatus("pwStatus", "The new passwords are not the same.", true);
    try {
      await api("/api/auth/password", json("POST", { current, password }));
      $("pwDialog").close();
      toast("Password changed. Other devices have been signed out.", true);
    } catch (e) {
      formStatus("pwStatus", e.message, true);
    }
  }

  // ------------------------------------------------------------------ users (Super Admin)
  function openUsers() {
    toggleMenu(false);
    document.body.classList.add("users-open");
    window.scrollTo(0, 0);
    loadUsers();
    $("usersTitle").setAttribute("tabindex", "-1");
    $("usersTitle").focus();
  }
  function closeUsers() {
    document.body.classList.remove("users-open");
    $("btnUser").focus();
  }

  async function loadUsers() {
    try {
      users = (await api("/api/admin/users")).users;
    } catch (e) {
      return toast(e.message);
    }
    renderUsers();
  }

  const when = (t) => (t ? new Date(t * 1000).toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short" }) : "Never");
  function renderUsers() {
    const active = users.filter((u) => u.active).length;
    $("usersCount").textContent = `${users.length} account${users.length === 1 ? "" : "s"} · ${active} active`;
    $("usersTable").innerHTML =
      `<tr><th scope="col">Email</th><th scope="col">Role</th><th scope="col">Status</th><th scope="col">Last sign-in</th><th scope="col"><span class="visually-hidden">Actions</span></th></tr>` +
      users.map((u) => {
        const e = esc(u.email), boss = u.role === "super_admin", me = u.id === user.id;
        const actions = boss
          ? `<span class="small muted">${me ? "You · " : ""}set in the deployment secrets</span>`
          : `<button type="button" class="ghost small" data-act="edit" aria-label="Edit ${e}">Edit</button>
             <button type="button" class="ghost small" data-act="password" aria-label="Reset password of ${e}">Reset password</button>
             <button type="button" class="ghost small" data-act="active" aria-label="${u.active ? "Deactivate" : "Activate"} ${e}">${u.active ? "Deactivate" : "Activate"}</button>
             <button type="button" class="ghost small danger" data-act="delete" aria-label="Delete ${e}">Delete</button>`;
        return `<tr data-id="${u.id}" class="${u.active ? "" : "off"}">
          <td data-label="Email"><b>${e}</b></td>
          <td data-label="Role"><span class="badge ${boss ? "on" : ""}">${ROLE_LABEL[u.role]}</span></td>
          <td data-label="Status"><span class="status ${u.active ? "good" : "bad"}">${u.active ? "&#9679; Active" : "&#9675; Deactivated"}</span></td>
          <td data-label="Last sign-in" class="small muted">${when(u.last_login_at)}</td>
          <td class="user-actions">${actions}</td></tr>`;
      }).join("");
  }

  async function userAction(ev) {
    const b = ev.target.closest("[data-act]");
    if (!b) return;
    const u = users.find((x) => x.id === +b.closest("tr").dataset.id);
    if (!u) return;
    const act = b.dataset.act;
    if (act === "edit") return openUserDialog("edit", u);
    if (act === "password") return openUserDialog("password", u);
    try {
      if (act === "active") {
        await api(`/api/admin/users/${u.id}`, json("PATCH", { active: !u.active }));
        toast(u.active ? `${u.email} is deactivated and signed out everywhere.` : `${u.email} can sign in again.`, true);
      } else if (act === "delete") {
        if (!confirm(`Delete ${u.email}?\n\nTheir designs and AI settings are deleted too. This cannot be undone.`)) return;
        await api(`/api/admin/users/${u.id}`, { method: "DELETE" });
        toast(`${u.email} was deleted.`, true);
      }
    } catch (e) {
      return toast(e.message);
    }
    await loadUsers();
  }

  // one dialog: add a user, edit an email, or set a new password
  let dialog = null; // { mode, user, done }
  function openUserDialog(mode, u = null) {
    dialog = { mode, user: u, done: false };
    const titles = { create: "Add user", edit: `Edit ${u?.email}`, password: `New password for ${u?.email}` };
    const notes = {
      create: "The user signs in with this email and password. There is no public sign-up.",
      edit: "The user signs in with the new email from now on.",
      password: "The user is signed out everywhere and signs in with the new password.",
    };
    $("userDialogTitle").textContent = titles[mode];
    $("userDialogNote").textContent = notes[mode];
    $("userEmailRow").classList.toggle("hidden", mode === "password");
    $("userPasswordRow").classList.toggle("hidden", mode === "edit");
    $("userFormEmail").value = u?.email || "";
    $("userFormPassword").value = mode === "edit" ? "" : generatePassword();
    [$("userFormEmail"), $("userFormPassword")].forEach((el) => (el.readOnly = false));
    $("userGenerate").disabled = false;
    $("userSave").classList.remove("hidden");
    $("userSave").textContent = { create: "Create user", edit: "Save", password: "Set password" }[mode];
    $("userDialog").querySelector(".row.end [data-close]").textContent = "Cancel";
    formStatus("userDialogStatus", "");
    $("userDialog").showModal();
    (mode === "password" ? $("userFormPassword") : $("userFormEmail")).focus();
  }

  async function submitUserDialog(ev) {
    ev.preventDefault();
    if (!dialog || dialog.done) return $("userDialog").close();
    const email = $("userFormEmail").value.trim(), password = $("userFormPassword").value;
    if (dialog.mode !== "password" && !/^[^@\s]+@[^@\s]+\.[^@\s]{2,}$/.test(email)) return formStatus("userDialogStatus", "Enter a valid email address.", true);
    if (dialog.mode !== "edit" && password.length < 10) return formStatus("userDialogStatus", "The password needs at least 10 characters.", true);
    $("userSave").disabled = true;
    try {
      if (dialog.mode === "create") await api("/api/admin/users", json("POST", { email, password }));
      else if (dialog.mode === "edit") await api(`/api/admin/users/${dialog.user.id}`, json("PATCH", { email }));
      else await api(`/api/admin/users/${dialog.user.id}/password`, json("POST", { password }));
    } catch (e) {
      return formStatus("userDialogStatus", e.message, true);
    } finally {
      $("userSave").disabled = false;
    }
    await loadUsers();
    if (dialog.mode === "edit") {
      $("userDialog").close();
      return toast("Email changed.", true);
    }
    // the password is shown once: keep the dialog open so it can still be copied
    dialog.done = true;
    [$("userFormEmail"), $("userFormPassword")].forEach((el) => (el.readOnly = true));
    $("userGenerate").disabled = true;
    $("userSave").classList.add("hidden");
    $("userDialog").querySelector(".row.end [data-close]").textContent = "Done";
    formStatus("userDialogStatus", dialog.mode === "create"
      ? "User created. Copy the password now and share it privately – it is not shown again."
      : "Password set. Copy it now and share it privately – it is not shown again.");
  }

  // 16 characters from an alphabet without look-alikes (0/O, 1/l/I), from the browser's CSPRNG
  function generatePassword(length = 16) {
    const chars = "ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz23456789!@#$%*-_+=?";
    const out = [];
    const buf = new Uint8Array(1);
    while (out.length < length) {
      crypto.getRandomValues(buf);
      if (buf[0] < 256 - (256 % chars.length)) out.push(chars[buf[0] % chars.length]); // no modulo bias
    }
    return out.join("");
  }
  async function copyPassword() {
    const v = $("userFormPassword").value;
    if (!v) return;
    try {
      await navigator.clipboard.writeText(v);
    } catch (_) {
      $("userFormPassword").select();
      document.execCommand("copy");
    }
    formStatus("userDialogStatus", "Password copied to the clipboard.");
  }

  function formStatus(id, text, bad = false) {
    $(id).textContent = text;
    $(id).className = "small " + (text ? (bad ? "bad" : "good") : "");
  }

  // ------------------------------------------------------------------ wiring
  function init() {
    $("loginForm").addEventListener("submit", submitLogin);
    $("loginShow").onclick = () => {
      const show = $("loginPassword").type === "password";
      $("loginPassword").type = show ? "text" : "password";
      $("loginShow").textContent = show ? "Hide" : "Show";
      $("loginShow").setAttribute("aria-pressed", show);
    };
    $("btnUser").onclick = () => toggleMenu();
    document.addEventListener("click", (ev) => {
      if (!ev.target.closest(".user-menu") && !$("userMenu").classList.contains("hidden")) toggleMenu(false);
    });
    $("userMenu").addEventListener("keydown", (ev) => {
      if (ev.key === "Escape") { toggleMenu(false); $("btnUser").focus(); }
    });
    $("btnLogout").onclick = signOut;
    $("btnMyPassword").onclick = openOwnPassword;
    $("pwForm").addEventListener("submit", submitOwnPassword);
    $("btnUsers").onclick = openUsers;
    $("btnUsersBack").onclick = closeUsers;
    $("btnAddUser").onclick = () => openUserDialog("create");
    $("usersTable").addEventListener("click", userAction);
    $("userForm").addEventListener("submit", submitUserDialog);
    $("userGenerate").onclick = () => { $("userFormPassword").value = generatePassword(); formStatus("userDialogStatus", "New password generated."); };
    $("userCopy").onclick = copyPassword;
    document.querySelectorAll(".form-dialog [data-close]").forEach((b) => (b.onclick = () => b.closest("dialog").close()));
  }

  return { init, start, sessionEnded, user: () => user };
})();
