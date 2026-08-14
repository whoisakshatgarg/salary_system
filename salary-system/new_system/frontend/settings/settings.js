// APEX THERMOCON — Settings (⚙ tile): order-number format, units,
// operation rates for costing, departments. Reads for anyone with the
// settings grant; every change is admin-only (server-enforced).

async function api(path, { method = "GET", body } = {}) {
  const opts = { method, headers: { "X-Requested-With": "apex-payroll" } };
  if (body !== undefined) {
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
      detail = Array.isArray(d) ? d.map((x) => x.msg || JSON.stringify(x)).join("; ") : (d || detail);
    } catch (_) {}
    throw new Error(detail);
  }
  const ct = res.headers.get("content-type") || "";
  return ct.includes("application/json") ? res.json() : res;
}

function st() {
  return {
    booted: false,
    authed: false,
    user: null,
    toast: { show: false, msg: "", kind: "ok" },
    flash(msg, kind = "ok") {
      this.toast = { show: true, msg, kind };
      setTimeout(() => (this.toast.show = false), 3200);
    },
    fail(e) { this.flash(e.message || String(e), "err"); },
    get isAdmin() { return this.user && this.user.role === "admin"; },

    data: null,           // /api/settings payload
    fmt: "",
    unitQ: "",
    newUnit: "",
    newOp: { name: "", rate_per_hour: "" },
    newDept: "",

    async boot() {
      window.addEventListener("unauth", () => { this.authed = false; });
      try {
        this.user = await api("/api/me");
        const mods = await api("/api/modules");
        this.authed = !!(mods.modules || []).find((m) => m.key === "settings" && m.granted);
      } catch (_) { this.authed = false; }
      if (!this.authed) { window.location.href = "/"; return; }
      try { await this.load(); } catch (e) { this.fail(e); }
      this.booted = true;
    },
    async load() {
      this.data = await api("/api/settings");
      this.fmt = this.data.order_number_format;
    },
    get filteredUnits() {
      const q = this.unitQ.toLowerCase();
      return (this.data?.units || []).filter((u) => !q || u.toLowerCase().includes(q));
    },

    async saveFormat() {
      try {
        this.data = await api("/api/settings/order-format",
                              { method: "PUT", body: { format: this.fmt } });
        this.flash("Order-number format saved");
      } catch (e) { this.fail(e); }
    },
    async addUnit() {
      const v = this.newUnit.trim();
      if (!v) return;
      try {
        this.data = await api("/api/settings/units", { method: "POST", body: { value: v } });
        this.newUnit = "";
      } catch (e) { this.fail(e); }
    },
    async removeUnit(u) {
      try {
        this.data = await api("/api/settings/units/delete", { method: "POST", body: { value: u } });
      } catch (e) { this.fail(e); }
    },
    async addOp() {
      const name = this.newOp.name.trim();
      if (!name) return;
      try {
        this.data = await api("/api/settings/operations", {
          method: "POST",
          body: { name, rate_per_hour: Number(this.newOp.rate_per_hour) || 0 },
        });
        this.newOp = { name: "", rate_per_hour: "" };
      } catch (e) { this.fail(e); }
    },
    async saveOpRate(op) {
      try {
        this.data = await api(`/api/settings/operations/${op.id}`, {
          method: "PUT", body: { rate_per_hour: Number(op.rate_per_hour) || 0 },
        });
        this.flash(`${op.name} rate saved`);
      } catch (e) { this.fail(e); }
    },
    async removeOp(op) {
      if (!window.confirm(`Remove the operation "${op.name}"? Old costings keep their snapshot.`)) return;
      try {
        this.data = await api(`/api/settings/operations/${op.id}`, { method: "DELETE" });
      } catch (e) { this.fail(e); }
    },
    async addDept() {
      const v = this.newDept.trim();
      if (!v) return;
      try {
        const r = await api("/api/settings/departments", { method: "POST", body: { value: v } });
        this.data.departments = r.departments;
        this.newDept = "";
      } catch (e) { this.fail(e); }
    },
    async removeDept(d) {
      if (!window.confirm(`Remove department "${d}"? Employees already in it are unaffected.`)) return;
      try {
        const r = await api("/api/settings/departments/delete", { method: "POST", body: { value: d } });
        this.data.departments = r.departments;
      } catch (e) { this.fail(e); }
    },
  };
}
