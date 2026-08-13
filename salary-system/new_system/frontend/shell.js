// APEX THERMOCON — app shell: one login, then a launcher of module tiles.
// Tiles come from /api/modules and reflect the signed-in account's grants;
// the owner manages accounts + grants in the Users & Access screen (admin).

async function api(path, { method = "GET", body } = {}) {
  const opts = { method, headers: { "X-Requested-With": "apex-payroll" } };
  if (body !== undefined) {
    opts.headers["Content-Type"] = "application/json";
    opts.body = JSON.stringify(body);
  }
  const res = await fetch(path, opts);
  if (res.status === 401) {
    window.dispatchEvent(new CustomEvent("unauth"));
    throw new Error("Not signed in");
  }
  if (!res.ok) {
    let detail = res.statusText;
    try { detail = (await res.json()).detail || detail; } catch (_) {}
    throw new Error(detail);
  }
  const ct = res.headers.get("content-type") || "";
  return ct.includes("application/json") ? res.json() : res;
}

function shell() {
  return {
    booted: false,
    user: null,
    edition: "admin",
    version: "",
    view: "home",            // 'home' | 'users' | 'ph' (placeholder)
    data: null,              // /api/modules response
    ph: null,                // module shown in the placeholder view
    login: { username: "", password: "" },
    loginError: "",
    toast: { show: false, msg: "", kind: "ok" },

    flash(msg, kind = "ok") {
      this.toast = { show: true, msg, kind };
      setTimeout(() => (this.toast.show = false), 3200);
    },
    fail(e) { this.flash(e.message || String(e), "err"); },

    get grantedModules() {
      return this.data ? this.data.modules.filter((m) => m.granted) : [];
    },
    get isAdmin() { return this.data ? this.data.is_admin : false; },

    async boot() {
      window.addEventListener("unauth", () => { this.user = null; this.data = null; });
      try {
        const ed = await api("/api/edition");
        this.edition = ed.edition;
        this.version = ed.version;
      } catch (_) {}
      if (this.edition === "operator") {
        // Kiosk laptop: no launcher ceremony — straight into attendance.
        try {
          await api("/api/kiosk-login", { method: "POST" });
          window.location.href = "/payroll.html";
          return;
        } catch (_) { /* fall through to the Start screen below */ }
      }
      try {
        this.user = await api("/api/me");
        await this.enter();
      } catch (_) { /* show login */ }
      this.checkUpdates();   // not awaited — never delays startup
      this.booted = true;
    },
    async doLogin() {
      this.loginError = "";
      try {
        this.user = await api("/api/login", { method: "POST", body: this.login });
        this.login.password = "";
      } catch (e) { this.loginError = e.message; return; }
      await this.enter();   // login succeeded — enter() reports its own failures
    },
    async kioskRetry() {
      try {
        await api("/api/kiosk-login", { method: "POST" });
        window.location.href = "/payroll.html";
      } catch (e) { this.loginError = e.message; }
    },
    homeError: "",
    async enter() {
      // A signed-in user must never face a silent blank Home — surface the
      // failure with a retry instead.
      this.homeError = "";
      try {
        this.data = await api("/api/modules");
      } catch (e) { this.homeError = e.message || "Couldn't load your modules"; }
      this.view = "home";
    },
    open(m) {
      if (!m.granted) return;
      if (m.built && m.path) window.location.href = m.path;
      else { this.ph = m; this.view = "ph"; }
    },
    async logout() {
      await api("/api/logout", { method: "POST" });
      this.user = null;
      this.data = null;
      this.view = "home";
    },

    // ---- Users & Access (admin) ------------------------------------------ //
    users: [],
    uform: null,
    uformError: "",
    async openUsers() {
      try {
        this.users = await api("/api/users");
        this.view = "users";
      } catch (e) { this.fail(e); }
    },
    newUser() {
      this.uform = { id: null, username: "", password: "", role: "operator", grants: ["salary"] };
      this.uformError = "";
    },
    editUser(u) {
      this.uform = { id: u.id, username: u.username, password: "", role: u.role, grants: [...u.grants] };
      this.uformError = "";
    },
    toggleGrant(key) {
      const g = this.uform.grants;
      const i = g.indexOf(key);
      if (i >= 0) g.splice(i, 1); else g.push(key);
    },
    async saveUser() {
      this.uformError = "";
      const f = this.uform;
      try {
        if (f.id) {
          this.users = await api(`/api/users/${f.id}`, {
            method: "PUT",
            body: { role: f.role, grants: f.grants, password: f.password || null },
          });
        } else {
          this.users = await api("/api/users", { method: "POST", body: f });
        }
        this.uform = null;
        this.flash("Account saved");
      } catch (e) { this.uformError = e.message; }
    },
    async deleteUser(u) {
      if (!window.confirm(`Delete the account '${u.username}'? They will no longer be able to sign in.`)) return;
      try {
        this.users = await api(`/api/users/${u.id}`, { method: "DELETE" });
        this.flash("Account deleted");
      } catch (e) { this.fail(e); }
    },

    // ---- self-update (the shell owns the launch-time popup) --------------- //
    upd: { info: null, show: false, busy: false, done: false },
    async checkUpdates(manual = false) {
      try {
        const info = await api("/api/update/check");
        this.upd.info = info;
        if (info.update_available && (manual || info.auto_check)) {
          this.upd.show = true;
        } else if (manual) {
          if (!info.configured) this.flash("Updates aren't set up (config/update.json)", "err");
          else if (info.error) this.flash(info.error, "err");
          else this.flash(`You're up to date (v${info.current})`);
        }
      } catch (e) { if (manual) this.fail(e); }
    },
    async applyUpdate() {
      this.upd.busy = true;
      try {
        await api("/api/update/apply", { method: "POST" });
        this.upd.done = true;   // server exits itself; the window closes shortly
      } catch (e) {
        this.upd.busy = false;
        this.fail(e);
      }
    },
  };
}
