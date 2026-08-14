// APEX THERMOCON — Quotations & Invoices (🧾 tile).
// One list for both kinds; invoices can be raised straight from an order.
// "Print / Save as PDF" opens the printable copy in its own window.

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

function today() {
  const d = new Date();
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
}
function plusDays(n) {
  const d = new Date(); d.setDate(d.getDate() + n);
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
}

function qi() {
  return {
    booted: false, authed: false, user: null,
    toast: { show: false, msg: "", kind: "ok" },
    flash(msg, kind = "ok") { this.toast = { show: true, msg, kind }; setTimeout(() => (this.toast.show = false), 3200); },
    fail(e) { this.flash(e.message || String(e), "err"); },
    money(n) {
      if (n === null || n === undefined || n === "") return "—";
      return "₹" + Number(n).toLocaleString("en-IN", { maximumFractionDigits: 2 });
    },
    fmtDate(d) {
      if (!d) return "—";
      const [y, m, dd] = d.split("-").map(Number);
      return new Date(y, m - 1, dd).toLocaleDateString("en-IN", { day: "numeric", month: "short", year: "numeric" });
    },

    refs: { customers: [], drawings: [], units: [], orders: [], statuses: [], default_terms: "" },
    data: { rows: [], counts: {} },
    kind: "", q: "",
    detail: null, form: null, formError: "",

    kindLabel(k) { return k === "quotation" ? "Quotation" : "Invoice"; },
    kindClass(k) { return k === "quotation" ? "bg-sky-100 text-sky-700" : "bg-indigo-100 text-indigo-700"; },
    statusClass(s) {
      return { draft: "bg-slate-200 text-slate-600", sent: "bg-amber-100 text-amber-800",
               accepted: "bg-emerald-100 text-emerald-700", paid: "bg-emerald-100 text-emerald-700",
               cancelled: "bg-rose-100 text-rose-700" }[s] || "bg-slate-100";
    },

    async boot() {
      window.addEventListener("unauth", () => { this.authed = false; });
      try {
        this.user = await api("/api/me");
        const mods = await api("/api/modules");
        this.authed = !!(mods.modules || []).find((m) => m.key === "quotations" && m.granted);
      } catch (_) { this.authed = false; }
      if (!this.authed) { window.location.href = "/"; return; }
      try { this.refs = await api("/api/quotations/refs"); await this.load(); }
      catch (e) { this.fail(e); }
      this.booted = true;
    },
    _seq: 0,
    async load() {
      const seq = ++this._seq;
      try {
        const p = new URLSearchParams({ kind: this.kind, q: this.q });
        const d = await api("/api/quotations?" + p.toString());
        if (seq === this._seq) this.data = d;
      } catch (e) { if (seq === this._seq) this.fail(e); }
    },

    async open(id) { try { this.detail = await api(`/api/quotations/${id}`); } catch (e) { this.fail(e); } },
    closeDetail() { this.detail = null; this.load(); },
    print(d) { window.open(`/api/quotations/${d.id}/print`, "_blank"); },
    async setStatus(d, status) {
      try { this.detail = await api(`/api/quotations/${d.id}/status`, { method: "POST", body: { status } });
            await this.load(); this.flash(`Marked ${status}`); }
      catch (e) { this.fail(e); }
    },
    async remove(d) {
      if (!window.confirm(`Delete ${d.doc_no}?`)) return;
      try { await api(`/api/quotations/${d.id}`, { method: "DELETE" }); this.detail = null;
            await this.load(); this.flash("Deleted"); } catch (e) { this.fail(e); }
    },

    blankLine() { return { drawing_id: "", description: "", qty: "", unit: "Nos", rate: "" }; },
    newDoc(kind) {
      this.form = { id: null, kind, customer_id: "", order_id: "", doc_date: today(),
                    valid_until: kind === "quotation" ? plusDays(30) : "", reference: "",
                    tax_pct: 18, notes: "", terms: this.refs.default_terms,
                    lines: [this.blankLine()] };
      this.formError = "";
    },
    editDoc() {
      const d = this.detail;
      this.form = { id: d.id, kind: d.kind, customer_id: d.customer_id, order_id: d.order_id || "",
                    doc_date: d.doc_date, valid_until: d.valid_until || "", reference: d.reference || "",
                    tax_pct: d.tax_pct, notes: d.notes || "", terms: d.terms || "",
                    lines: d.lines.map((l) => ({ drawing_id: l.drawing_id || "", description: l.description || "",
                                                 qty: l.qty, unit: l.unit, rate: l.rate })) };
      this.formError = "";
    },
    async fromOrder(orderId) {
      if (!orderId) return;
      try {
        const pre = await api(`/api/quotations/from-order/${orderId}?kind=${this.form.kind}`);
        this.form.customer_id = pre.customer_id;
        this.form.order_id = pre.order_id;
        this.form.reference = pre.reference;
        this.form.lines = pre.lines.map((l) => ({ drawing_id: l.drawing_id || "", description: l.description,
                                                  qty: l.qty, unit: l.unit, rate: l.rate }));
        this.flash(`Filled from ${pre.order_no}`);
      } catch (e) { this.fail(e); }
    },
    addLine() { this.form.lines.push(this.blankLine()); },
    removeLine(i) { this.form.lines.splice(i, 1); },
    linePicked(l) {
      const d = this.refs.drawings.find((x) => x.id === Number(l.drawing_id));
      if (!d) return;
      l.description = d.description || d.drawing_no;
      l.unit = d.unit || l.unit;
      l.rate = d.latest_rate || "";
    },
    lineAmount(l) { return Math.round((Number(l.qty) || 0) * (Number(l.rate) || 0) * 100) / 100; },
    get formTotals() {
      const sub = (this.form?.lines || []).reduce((s, l) => s + this.lineAmount(l), 0);
      const tax = sub * (Number(this.form?.tax_pct) || 0) / 100;
      return { subtotal: Math.round(sub * 100) / 100, tax: Math.round(tax * 100) / 100,
               total: Math.round((sub + tax) * 100) / 100 };
    },
    async save() {
      this.formError = "";
      const f = this.form;
      const payload = {
        kind: f.kind, customer_id: Number(f.customer_id) || 0,
        order_id: f.order_id ? Number(f.order_id) : null,
        doc_date: f.doc_date, valid_until: f.valid_until, reference: f.reference,
        tax_pct: Number(f.tax_pct) || 0, notes: f.notes, terms: f.terms,
        lines: f.lines.filter((l) => l.drawing_id || (l.description || "").trim() || l.qty || String(l.rate ?? "").trim())
          .map((l) => ({ drawing_id: l.drawing_id ? Number(l.drawing_id) : null,
                         description: l.description, qty: Number(l.qty),
                         unit: l.unit || "Nos", rate: Number(l.rate) || 0 })),
      };
      try {
        const saved = f.id
          ? await api(`/api/quotations/${f.id}`, { method: "PUT", body: payload })
          : await api("/api/quotations", { method: "POST", body: payload });
        this.form = null; this.detail = saved; await this.load();
        this.flash(`${saved.doc_no} saved`);
      } catch (e) { this.formError = e.message; }
    },
  };
}
