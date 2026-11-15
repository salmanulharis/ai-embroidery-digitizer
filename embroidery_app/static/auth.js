"use strict";
/* Accounts in the browser: the sign-in screen, the account menu (my account, admin
   console, change password, sign out). The server decides everything (auth.py): this
   file only shows what it allows. User management lives in admin.js, the account page in
   account.js. Uses helpers from app.js ($, api, json, esc, toast) inside functions,
   after app.js has loaded. */

const Auth = (() => {
  let user = null;
  let waiting = null; // resolves the sign-in promise
  let ended = false; // the session ended while the app was open
  const ROLE_LABEL = { super_admin: "Super Admin", user: "User" };
  const isAdmin = () => user?.role === "super_admin";
  const KEEP_LABEL = (d) => (d === 0 ? "Until I close the browser" : d === 1 ? "1 day" : `${d} days`);

  // ------------------------------------------------------------------ sign in / out
  /** The signed-in account; shows the sign-in screen first when there is none. */
  async function start(config) {
    const choices = config.session_choices || [0, config.session_days || 30];
    $("loginKeep").innerHTML = choices.map((d) => `<option value="${d}">${KEEP_LABEL(d)}</option>`).join("");
    try { $("loginKeep").value = localStorage.getItem("keepSignedIn") ?? "0"; } catch (_) {}
    if (!$("loginKeep").value) $("loginKeep").value = "0";
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
    $("loginNote").textContent = note || "Use the email or username and the password from your administrator.";
    $("loginError").classList.add("hidden");
    $("loginPassword").value = "";
    requestAnimationFrame(() => ($("loginEmail").value ? $("loginPassword") : $("loginEmail")).focus());
    return new Promise((resolve) => (waiting = resolve));
  }

  async function submitLogin(ev) {
    ev.preventDefault();
    const login = $("loginEmail").value.trim(), password = $("loginPassword").value;
    if (!login || !password) return loginError("Enter your email or username and your password.");
    const days = +$("loginKeep").value || 0;
    $("loginSubmit").disabled = true;
    $("loginSubmit").textContent = "Signing in…";
    try {
      const r = await api("/api/auth/login", json("POST", { login, password, days }));
      try { localStorage.setItem("keepSignedIn", String(days)); } catch (_) {}
      // another account, or the old session ended (its design was removed on the server): start clean
      if ((user && user.id !== r.user.id) || ended) return location.reload();
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
    ended = true;
    $("loginEmail").value = user.username || user.email;
    showLogin("Your session has ended. Sign in again to continue. For your privacy, a design that was open has been removed from the server.");
  }

  async function signOut() {
    try { await api("/api/auth/logout", { method: "POST" }); } catch (_) {}
    try { sessionStorage.clear(); } catch (_) {}
    location.reload();
  }

  function setUser(u) {
    user = u;
    const shown = u.display_name || u.email;
    $("userEmail").textContent = shown;
    $("userMenuEmail").textContent = u.email;
    $("userMenuName").textContent = [u.display_name, u.username ? `@${u.username}` : ""].filter(Boolean).join(" · ");
    $("userAvatar").textContent = shown.slice(0, 1).toUpperCase();
    $("userRole").textContent = ROLE_LABEL[u.role] || u.role;
    $("btnUser").setAttribute("aria-label", `Account: ${u.email}`);
    $("btnUser").title = u.email;
    $("btnAdmin").classList.toggle("hidden", !isAdmin());
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
      document.dispatchEvent(new CustomEvent("account-changed"));
    } catch (e) {
      formStatus("pwStatus", e.message, true);
    }
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
    $("btnAccount").onclick = () => { toggleMenu(false); Account.open(); };
    $("btnAdmin").onclick = () => { toggleMenu(false); Admin.open(); };
    document.querySelectorAll(".form-dialog [data-close]").forEach((b) => (b.onclick = () => b.closest("dialog").close()));
  }

  return { init, start, sessionEnded, signOut, setUser, generatePassword, formStatus, openOwnPassword, isAdmin,
    user: () => user, ROLE_LABEL };
})();
