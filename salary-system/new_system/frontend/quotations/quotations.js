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
    // ADDING gets a full page, EDITING keeps the modal (same fields either way).
    addPage: false,
    // Optional material-availability check, off by default: ticking it must not
    // change anything about how a quotation is written or saved.
    checkOn: false,
    chk: { method: "dimension", material_class: "", grade: "",
           required_qty: "", part_length: "", part_diameter: "", margin: "" },
    chkResult: null, chkBusy: false, chkRefs: { material_class: [], grade: [] },

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
      this.addPage = true;              // adding opens the full page
      this.checkOn = false; this.chkResult = null;
      this.loadCheckRefs();
    },
    closeForm() { this.form = null; this.addPage = false; this.formError = ""; },
    async loadCheckRefs() {
      if (this.chkRefs.material_class.length) return;
      try { this.chkRefs = await api("/api/material/refs"); } catch (_) { /* optional */ }
    },
    // Prefill the requirement from the quotation itself so the common case is
    // one tick and one click.
    toggleCheck() {
      this.checkOn = !this.checkOn;
      if (!this.checkOn) return;
      this.loadCheckRefs();
      if (!this.chk.required_qty) {
        const qty = (this.form?.lines || [])
          .reduce((n, l) => n + (Number(l.qty) || 0), 0);
        if (qty) this.chk.required_qty = qty;
      }
    },
    async runCheck() {
      this.chkResult = null;
      const c = this.chk;
      if (c.method === "dimension" && !(Number(c.part_length) > 0)) {
        this.fail(new Error("Enter the part length to check by dimension")); return;
      }
      this.chkBusy = true;
      try {
        this.chkResult = await api("/api/material/check", { method: "POST", body: {
          method: c.method, material_class: c.material_class, grade: c.grade,
          required_qty: Number(c.required_qty) || 0,
          part_length: c.part_length === "" ? null : Number(c.part_length),
          part_diameter: c.part_diameter === "" ? null : Number(c.part_diameter),
          margin: c.margin === "" ? null : Number(c.margin),
        }});
      } catch (e) { this.fail(e); } finally { this.chkBusy = false; }
    },
    // availLabel/availClass, NOT statusLabel/statusClass: those names are already
    // taken on these pages (heat status, document status) and an object literal
    // silently keeps the LAST definition.
    // Flatten heats -> one display row per piece. Nesting <tbody> inside <tbody>
    // (or <template x-for> inside <template x-for>) is invalid table markup and
    // the browser silently stops aligning the body with the header.
    checkRows() {
      const out = [];
      for (const h of (this.chkResult?.heats || [])) {
        if (!h.pieces || !h.pieces.length) {
          out.push({ key: "h" + h.heat_id, first: true, heat: h, piece: null });
          continue;
        }
        h.pieces.forEach((p, i) => out.push({
          key: "p" + p.piece_id, first: i === 0, heat: h, piece: p }));
      }
      return out;
    },
    availLabel(s) {
      return { available: "Available", partial: "Partially available",
               none: "Not available" }[s] || s;
    },
    availClass(s) {
      return { available: "bg-emerald-100 text-emerald-800",
               partial: "bg-amber-100 text-amber-800",
               none: "bg-rose-100 text-rose-800" }[s] || "bg-slate-100 text-slate-700";
    },
    dim(v) {
      if (v === null || v === undefined || v === "") return "—";
      return String(Math.round(Number(v) * 10000) / 10000);
    },
    editDoc() {
      const d = this.detail;
      this.form = { id: d.id, kind: d.kind, customer_id: d.customer_id, order_id: d.order_id || "",
                    doc_date: d.doc_date, valid_until: d.valid_until || "", reference: d.reference || "",
                    tax_pct: d.tax_pct, notes: d.notes || "", terms: d.terms || "",
                    lines: d.lines.map((l) => ({ drawing_id: l.drawing_id || "", description: l.description || "",
                                                 qty: l.qty, unit: l.unit, rate: l.rate })) };
      this.formError = "";
      this.addPage = false;             // editing stays a modal
      this.checkOn = false; this.chkResult = null;
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
        this.form = null; this.addPage = false; this.detail = saved; await this.load();
        this.flash(`${saved.doc_no} saved`);
      } catch (e) { this.formError = e.message; }
    },
  };
}
