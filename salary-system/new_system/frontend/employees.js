// APEX THERMOCON — Employee Management (own page behind the 👥 tile).
// Owns the people side: profile, status, documents, leave bank, attendance
// stats. The money side (base pay, PF/ESI, advances) lives in Salary → Pay
// Setup; this page shows those read-only with a pointer.

async function api(path, { method = "GET", body, form } = {}) {
  const opts = { method, headers: { "X-Requested-With": "apex-payroll" } };
  if (form) {
    opts.body = form;
  } else if (body !== undefined) {
    opts.headers["Content-Type"] = "application/json";
    opts.body = JSON.stringify(body);
  }
  const res = await fetch(path, opts);
  if (res.status === 401) {
    window.dispatchEvent(new CustomEvent("unauth"));
    throw new Error("Session ended — sign in from the Home screen");
  }
  if (!res.ok) {
    let detail = res.statusText;
    try {
      const d = (await res.json()).detail;
      // FastAPI validation errors (422) are an array of objects — flatten them
      // so the toast reads like a sentence, not "[object Object]".
      detail = Array.isArray(d) ? d.map((x) => x.msg || JSON.stringify(x)).join("; ") : (d || detail);
    } catch (_) {}
    throw new Error(detail);
  }
  const ct = res.headers.get("content-type") || "";
  return ct.includes("application/json") ? res.json() : res;
}

function today() {
  const d = new Date();
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
}

function em() {
  return {
    booted: false,
    authed: false,
    user: null,
    meta: { departments: [] },
    toast: { show: false, msg: "", kind: "ok" },

    flash(msg, kind = "ok") {
      this.toast = { show: true, msg, kind };
      setTimeout(() => (this.toast.show = false), 3200);
    },
    fail(e) { this.flash(e.message || String(e), "err"); },
    get isAdmin() { return this.user && this.user.role === "admin"; },

    rows: [],
    q: "",
    dept: "",
    status: "active",          // 'active' | 'inactive' | 'all'
    detail: null,              // {emp, documents, fy, history}
    form: null,
    formError: "",
    upload: { label: "", busy: false },
    leaveDelta: 1,

    async boot() {
      window.addEventListener("unauth", () => { this.authed = false; });
      try {
        this.user = await api("/api/me");
        const mods = await api("/api/modules");
        this.authed = !!(mods.modules || []).find((m) => m.key === "employees" && m.granted);
      } catch (_) { this.authed = false; }
      if (!this.authed) { window.location.href = "/"; return; }
      try {
        this.meta = await api("/api/meta");
        await this.load();
      } catch (e) { this.fail(e); }
      this.booted = true;
    },

    money(n) {
      if (n === null || n === undefined || n === "") return "—";
      return "₹" + Math.round(Number(n)).toLocaleString("en-IN");
    },
    fmtDate(d) {
      if (!d) return "—";
      const [y, m, dd] = d.split("-").map(Number);
      return new Date(y, m - 1, dd).toLocaleDateString("en-IN",
        { day: "numeric", month: "short", year: "numeric" });
    },
    sizeLabel(b) {
      if (!b && b !== 0) return "";
      return b > 1048576 ? (b / 1048576).toFixed(1) + " MB" : Math.max(1, Math.round(b / 1024)) + " KB";
    },
    fyStart() {
      const d = new Date();
      return d.getMonth() + 1 >= 4 ? d.getFullYear() : d.getFullYear() - 1;
    },

    async load() {
      this.rows = await api("/api/employees?active_only=false");
    },
    get filtered() {
      const q = this.q.toLowerCase();
      return this.rows.filter((e) =>
        (this.status === "all" || (this.status === "active") === !!e.active)
        && (!this.dept || e.dept === this.dept)
        && (!q || e.name.toLowerCase().includes(q) || e.dept.toLowerCase().includes(q)
            || String(e.id) === q));
    },

    // ---- detail ----------------------------------------------------------- //
    async open(id) {
      try {
        const [emp, documents, fy, history] = await Promise.all([
          api(`/api/employees/${id}`),
          api(`/api/employees/${id}/documents`),
          api(`/api/attendance-fy/${id}?fy_start=${this.fyStart()}`),
          api(`/api/attendance-history/${id}`),
        ]);
        this.detail = { emp, documents, fy, history: history.slice(0, 6) };
      } catch (e) { this.fail(e); }
    },
    closeDetail() { this.detail = null; this.load(); },

    async toggleActive() {
      const e = this.detail.emp;
      const verb = e.active ? "Deactivate" : "Reactivate";
      if (!window.confirm(`${verb} ${e.name}? History is always kept.`)) return;
      try {
        await api(`/api/employees/${e.id}/active?active=${!e.active}`, { method: "POST" });
        this.detail.emp = await api(`/api/employees/${e.id}`);
        this.flash(`${e.name} ${e.active ? "deactivated" : "reactivated"}`);
      } catch (err) { this.fail(err); }
    },

    async adjustLeave(sign) {
      // whole days only — the API's delta is an integer
      const delta = sign * Math.trunc(Math.abs(Number(this.leaveDelta) || 0));
      if (!delta) return;
      try {
        this.detail.emp = await api(`/api/employees/${this.detail.emp.id}/leave-adjust`,
                                    { method: "POST", body: { delta } });
        this.flash(`Leave bank ${delta > 0 ? "+" : ""}${delta} → ${this.detail.emp.leave_balance} day(s)`);
      } catch (e) { this.fail(e); }
    },

    // ---- add / edit (non-financial; financial passthrough) --------------- //
    newEmp() {
      this.form = {
        id: null, name: "", dept: this.meta.departments[0] || "", shift: "D",
        date_joined: today(), overtime_eligible: false, leave_balance: null,
        // one-time payroll setup on creation; managed in Salary afterwards
        base_salary: "", pf_applicable: false, esi_applicable: false, rem_advance: 0,
      };
      this.formError = "";
    },
    editEmp() {
      const e = this.detail.emp;
      this.form = { ...e, date_joined: e.date_joined || "" };
      this.formError = "";
    },
    async saveEmp() {
      this.formError = "";
      const f = this.form;
      if (!(f.name || "").trim()) { this.formError = "Name is required"; return; }
      if (!f.id && !(Number(f.base_salary) > 0)) {
        this.formError = "A starting base salary is required for a new employee"; return;
      }
      try {
        // Edits send ONLY profile fields (PUT is profile-only server-side);
        // creation sends everything once, including the starting pay.
        const saved = f.id
          ? await api(`/api/employees/${f.id}`, {
              method: "PUT",
              body: { name: f.name, dept: f.dept, shift: f.shift,
                      overtime_eligible: f.overtime_eligible,
                      date_joined: f.date_joined || null },
            })
          : await api("/api/employees", {
              method: "POST",
              body: { ...f, base_salary: Number(f.base_salary) || 0 },
            });
        this.form = null;
        if (this.detail) this.detail.emp = saved;
        await this.load();
        this.flash(`${saved.name} saved`);
      } catch (e) { this.formError = e.message; }
    },

    // ---- documents --------------------------------------------------------- //
    async pickFiles(ev) {
      const files = Array.from(ev.target.files || []);
      if (!files.length) return;
      this.upload.busy = true;
      try {
        const fd = new FormData();
        fd.append("label", this.upload.label || "");
        for (const f of files) fd.append("files", await this.compressIfImage(f));
        this.detail.documents = await api(
          `/api/employees/${this.detail.emp.id}/documents`, { method: "POST", form: fd });
        this.upload.label = "";
        this.flash(`${files.length} document(s) attached`);
      } catch (e) { this.fail(e); } finally {
        this.upload.busy = false;
        ev.target.value = "";
      }
    },
    async compressIfImage(file) {
      if (!file.type.startsWith("image/") || file.size < 300 * 1024) return file;
      try {
        const img = await createImageBitmap(file);
        const scale = Math.min(1, 1600 / Math.max(img.width, img.height));
        const canvas = document.createElement("canvas");
        canvas.width = Math.round(img.width * scale);
        canvas.height = Math.round(img.height * scale);
        canvas.getContext("2d").drawImage(img, 0, 0, canvas.width, canvas.height);
        const blob = await new Promise((r) => canvas.toBlob(r, "image/jpeg", 0.82));
        if (!blob || blob.size >= file.size) return file;
        return new File([blob], file.name.replace(/\.\w+$/, "") + ".jpg", { type: "image/jpeg" });
      } catch (_) { return file; }
    },
    viewDoc(d) {
      const u = `/api/employee-documents/${d.id}`;
      // Packaged app: window.open is dead inside pywebview — ask the shell
      // for a real window; browsers get a normal tab (download as fallback).
      if (window.pywebview && window.pywebview.api && window.pywebview.api.open_path) {
        window.pywebview.api.open_path(u).catch(() => this.downloadDoc(d));
        return;
      }
      const w = window.open(u, "_blank");
      if (!w) this.downloadDoc(d);
    },
    downloadDoc(d) { window.location = `/api/employee-documents/${d.id}?download=1`; },
    async deleteDoc(d) {
      if (!window.confirm(`Delete "${d.label || d.filename}"?`)) return;
      try {
        this.detail.documents = await api(`/api/employee-documents/${d.id}`, { method: "DELETE" });
        this.flash("Document deleted");
      } catch (e) { this.fail(e); }
    },
  };
}
