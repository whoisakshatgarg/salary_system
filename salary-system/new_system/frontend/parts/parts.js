// APEX THERMOCON — Parts & Pricing (📐 tile): the drawing master.
// Drawing no + revision, files, dated rate history, and the costing builder
// (operations × minutes × ₹/hr + material + margin → ₹/piece).

async function api(path, { method = "GET", body, form } = {}) {
  const opts = { method, headers: { "X-Requested-With": "apex-payroll" } };
  if (form) opts.body = form;
  else if (body !== undefined) {
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

function today() {
  const d = new Date();
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
}

function pt() {
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
    money(n) {
      if (n === null || n === undefined || n === "") return "—";
      return "₹" + Number(n).toLocaleString("en-IN");
    },

    lists: { units: [], operations: [], customers: [] },  // /api/parts/refs
    rows: [],
    q: "",
    customerId: "",
    detail: null,
    form: null,
    formError: "",
    rate: null,           // new rate-entry model
    costing: null,        // costing-builder model
    upload: { busy: false },

    async boot() {
      window.addEventListener("unauth", () => { this.authed = false; });
      try {
        this.user = await api("/api/me");
        const mods = await api("/api/modules");
        this.authed = !!(mods.modules || []).find((m) => m.key === "parts" && m.granted);
      } catch (_) { this.authed = false; }
      if (!this.authed) { window.location.href = "/"; return; }
      try {
        this.lists = await api("/api/parts/refs");
        await this.load();
      } catch (e) { this.fail(e); }
      this.booted = true;
    },
    _seq: 0,
    async load() {
      const seq = ++this._seq;   // stale debounced responses must not win
      try {
        const p = new URLSearchParams({ q: this.q });
        if (this.customerId) p.set("customer_id", this.customerId);
        const rows = await api("/api/parts/drawings?" + p.toString());
        if (seq === this._seq) this.rows = rows;
      } catch (e) { if (seq === this._seq) this.fail(e); }
    },

    async open(id) {
      try {
        this.detail = await api(`/api/parts/drawings/${id}`);
        this.rate = null;
        this.costing = null;
      } catch (e) { this.fail(e); }
    },
    closeDetail() { this.detail = null; this.load(); },

    // ---- drawing form ------------------------------------------------------ //
    newDrawing() {
      this.form = { id: null, drawing_no: "", revision: "A", customer_id: "",
                    description: "", material_class: "", grade: "", unit: "Nos",
                    notes: "" };
      this.formError = "";
    },
    editDrawing() {
      this.form = { ...this.detail, customer_id: this.detail.customer_id || "" };
      this.formError = "";
    },
    async saveDrawing() {
      this.formError = "";
      const f = this.form;
      const payload = { ...f, customer_id: f.customer_id ? Number(f.customer_id) : null };
      try {
        const saved = f.id
          ? await api(`/api/parts/drawings/${f.id}`, { method: "PUT", body: payload })
          : await api("/api/parts/drawings", { method: "POST", body: payload });
        this.form = null;
        this.detail = saved;
        await this.load();
        this.flash(`Drawing ${saved.drawing_no} rev ${saved.revision} saved`);
      } catch (e) { this.formError = e.message; }
    },
    async newRevision() {
      const rev = (window.prompt("New revision (e.g. B):", "") || "").trim();
      if (!rev) return;
      try {
        this.detail = await api(`/api/parts/drawings/${this.detail.id}/revise`,
                                { method: "POST", body: { revision: rev } });
        await this.load();
        this.flash(`Revision ${rev} created — rates/files start fresh for it`);
      } catch (e) { this.fail(e); }
    },
    async openRevision(r) { await this.open(r.id); },
    async removeDrawing() {
      if (!window.confirm(`Delete drawing ${this.detail.drawing_no} rev ${this.detail.revision}? Only possible while it's on no order.`)) return;
      try {
        await api(`/api/parts/drawings/${this.detail.id}`, { method: "DELETE" });
        this.flash("Drawing deleted");
        this.closeDetail();
      } catch (e) { this.fail(e); }
    },

    // ---- rate history ------------------------------------------------------ //
    newRate() { this.rate = { kind: "quoted", rate: "", rate_date: today(), note: "" }; },
    async saveRate() {
      try {
        this.detail = await api(`/api/parts/drawings/${this.detail.id}/rates`, {
          method: "POST",
          body: { ...this.rate, rate: Number(this.rate.rate) },
        });
        this.rate = null;
        this.flash("Rate recorded");
      } catch (e) { this.fail(e); }
    },
    async removeRate(r) {
      if (!window.confirm(`Delete this ${r.kind} rate of ₹${r.rate}?`)) return;
      try {
        this.detail = await api(`/api/parts/rates/${r.id}`, { method: "DELETE" });
      } catch (e) { this.fail(e); }
    },
    kindClass(k) {
      return { quoted: "bg-sky-100 text-sky-700", agreed: "bg-emerald-100 text-emerald-700",
               revised: "bg-amber-100 text-amber-800" }[k] || "bg-slate-100";
    },

    // ---- costing builder --------------------------------------------------- //
    // The costing WORKSPACE: a full screen for pricing one revision — part card
    // + revision switcher on the left, the operations table on the right.
    workspace: false,
    openWorkspace() {
      this.workspace = true;
      if (!this.costing) this.newCosting();
    },
    closeWorkspace() { this.workspace = false; this.costing = null; },
    newCosting() {
      this.costing = { material_cost: "", margin_pct: "", notes: "",
                       ops: [this.blankOp()] };
    },
    blankOp() {
      return { operation: "", minutes: "", rate_per_hour: "", weightage: 1, extra_margin_pct: "" };
    },
    // assign / change the customer this drawing belongs to, from the workspace
    async setCustomer(customerId) {
      const d = this.detail;
      try {
        this.detail = await api(`/api/parts/drawings/${d.id}`, {
          method: "PUT",
          body: { drawing_no: d.drawing_no, revision: d.revision,
                  customer_id: customerId ? Number(customerId) : null,
                  description: d.description || "", material_class: d.material_class || "",
                  grade: d.grade || "", unit: d.unit || "Nos", notes: d.notes || "" },
        });
        await this.load();
        this.flash(this.detail.customer_name
          ? `Assigned to ${this.detail.customer_name}` : "Customer cleared");
      } catch (e) { this.fail(e); }
    },
    addOp() { this.costing.ops.push(this.blankOp()); },
    removeOp(i) { this.costing.ops.splice(i, 1); },
    opPicked(op) {
      // choosing an operation prefills its ₹/hour from Settings
      const found = this.lists.operations.find((o) => o.name === op.operation);
      if (found) op.rate_per_hour = found.rate_per_hour;
    },
    // Same formula as the backend (parts.op_cost): time × weightage × (1 + extra margin)
    opCost(op) {
      const m = Number(op.minutes) || 0, r = Number(op.rate_per_hour) || 0;
      const w = op.weightage === "" || op.weightage == null ? 1 : Number(op.weightage) || 0;
      const x = Number(op.extra_margin_pct) || 0;
      return Math.round((m / 60) * r * w * (1 + x / 100) * 100) / 100;
    },
    opTimeCost(op) {   // shown as the "before weighting" figure
      const m = Number(op.minutes) || 0, r = Number(op.rate_per_hour) || 0;
      return Math.round((m / 60) * r * 100) / 100;
    },
    get costingTotals() {
      const c = this.costing;
      if (!c) return { ops: 0, subtotal: 0, total: 0 };
      const ops = c.ops.reduce((s, o) => s + this.opCost(o), 0);
      const subtotal = ops + (Number(c.material_cost) || 0);
      const total = subtotal * (1 + (Number(c.margin_pct) || 0) / 100);
      return { ops: Math.round(ops * 100) / 100,
               subtotal: Math.round(subtotal * 100) / 100,
               total: Math.round(total * 100) / 100 };
    },
    async saveCosting() {
      const c = this.costing;
      try {
        this.detail = await api(`/api/parts/drawings/${this.detail.id}/costings`, {
          method: "POST",
          body: {
            material_cost: Number(c.material_cost) || 0,
            margin_pct: Number(c.margin_pct) || 0,
            notes: c.notes,
            ops: c.ops.filter((o) => (o.operation || "").trim() !== "")
              .map((o) => ({ operation: o.operation, minutes: Number(o.minutes),
                             rate_per_hour: Number(o.rate_per_hour),
                             weightage: o.weightage === "" || o.weightage == null ? 1 : Number(o.weightage),
                             extra_margin_pct: Number(o.extra_margin_pct) || 0 })),
          },
        });
        this.costing = null;
        this.workspace = false;
        this.flash("Costing saved");
      } catch (e) { this.fail(e); }
    },
    async removeCosting(c) {
      if (!window.confirm("Delete this costing?")) return;
      try {
        this.detail = await api(`/api/parts/costings/${c.id}`, { method: "DELETE" });
      } catch (e) { this.fail(e); }
    },
    async costingToRate(c, kind) {
      try {
        this.detail = await api(`/api/parts/costings/${c.id}/to-rate`,
                                { method: "POST", body: { kind } });
        this.flash(`₹${c.total} recorded as ${kind} rate`);
      } catch (e) { this.fail(e); }
    },

    // ---- files -------------------------------------------------------------- //
    async pickFiles(ev) {
      const files = Array.from(ev.target.files || []);
      if (!files.length) return;
      this.upload.busy = true;
      try {
        const fd = new FormData();
        for (const f of files) fd.append("files", f);   // drawings: PDFs mostly
        this.detail.files = await api(`/api/parts/drawings/${this.detail.id}/files`,
                                      { method: "POST", form: fd });
        this.flash(`${files.length} file(s) attached`);
      } catch (e) { this.fail(e); } finally {
        this.upload.busy = false;
        ev.target.value = "";
      }
    },
    viewFile(f) {
      const u = `/api/parts/files/${f.id}`;
      if (window.pywebview && window.pywebview.api && window.pywebview.api.open_path) {
        window.pywebview.api.open_path(u).catch(() => (window.location = u + "?download=1"));
        return;
      }
      const w = window.open(u, "_blank");
      if (!w) window.location = u + "?download=1";
    },
    async removeFile(f) {
      if (!window.confirm(`Delete "${f.filename}"?`)) return;
      try {
        this.detail = await api(`/api/parts/files/${f.id}`, { method: "DELETE" });
      } catch (e) { this.fail(e); }
    },
  };
}
