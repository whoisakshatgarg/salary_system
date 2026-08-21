// APEX THERMOCON — Customers (🏢 tile): the master that Orders and
// Parts & Pricing reference. Name, GSTIN, addresses, contacts, terms.

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

function cu() {
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

    rows: [],
    q: "",
    activeOnly: true,
    detail: null,
    biz: null,            // order history + growth + documents
    tab: "profile",       // 'profile' | 'business'
    form: null,
    formError: "",
    contact: { name: "", phone: "", email: "", fax: "", role: "" },

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
    monthLabel(m) {
      const [y, mm] = m.split("-").map(Number);
      return new Date(y, mm - 1, 1).toLocaleDateString("en", { month: "short", year: "2-digit" });
    },
    // chart scaling: tallest bar = the biggest month
    get chartMax() {
      const s = this.biz?.series || [];
      return s.length ? Math.max(1, ...s.map((x) => x.amount)) : 1;
    },
    barPct(v) { return Math.max(2, Math.round((v / this.chartMax) * 100)); },
    // dot-chips: the pill (bg-50 / text-700 / ring-600/20) and its dot (the 500)
    stageClass(s) {
      return { payment: "bg-emerald-50 text-emerald-700 ring-1 ring-inset ring-emerald-600/20",
               dispatch: "bg-teal-50 text-teal-700 ring-1 ring-inset ring-teal-600/20",
               production: "bg-amber-50 text-amber-700 ring-1 ring-inset ring-amber-600/20",
               qc: "bg-violet-50 text-violet-700 ring-1 ring-inset ring-violet-600/20",
               po: "bg-indigo-50 text-indigo-700 ring-1 ring-inset ring-indigo-600/20",
               quote: "bg-sky-50 text-sky-700 ring-1 ring-inset ring-sky-600/20",
             }[s] || "bg-slate-100 text-slate-600 ring-1 ring-inset ring-slate-500/20";
    },
    cuDot(s) {
      return { payment: "bg-emerald-500", dispatch: "bg-teal-500", production: "bg-amber-500",
               qc: "bg-violet-500", po: "bg-indigo-500", quote: "bg-sky-500",
             }[s] || "bg-slate-400";
    },
    // the same words the Orders board uses, so a stage reads the same everywhere
    cuStageLabel(s) {
      return { enquiry: "Enquiry", quote: "Quote", po: "PO received", production: "Production",
               qc: "QC", dispatch: "Dispatch", payment: "Payment received" }[s] || s;
    },
    cuKindClass(k) {
      return k === "invoice" ? "bg-indigo-50 text-indigo-700 ring-1 ring-inset ring-indigo-600/20"
                             : "bg-sky-50 text-sky-700 ring-1 ring-inset ring-sky-600/20";
    },
    cuKindDot(k) { return k === "invoice" ? "bg-indigo-500" : "bg-sky-500"; },

    // footer count lines
    get cuListLine() {
      const n = this.rows.length;
      if (!n) return "";
      const withOrders = this.rows.filter((c) => c.orders).length;
      return `${n} customer${n === 1 ? "" : "s"} · ${withOrders} with orders`;
    },
    get cuOrderLine() {
      const n = (this.biz?.orders || []).length;
      return n ? `${n} order${n === 1 ? "" : "s"}, newest first` : "";
    },
    // In progress = the order still owes something (any stage before Payment
    // received). Past = fully done and paid — the archive with its paperwork.
    get cuOpenOrders() {
      return (this.biz?.orders || []).filter((o) => o.stage !== "payment");
    },
    get cuPastOrders() {
      return (this.biz?.orders || []).filter((o) => o.stage === "payment");
    },
    // the documents raised against ONE order — drawn as chips on its row
    cuDocsFor(orderId) {
      return (this.biz?.documents || []).filter((d) => d.order_id === orderId);
    },
    get cuDocLine() {
      const d = this.biz?.documents || [];
      if (!d.length) return "";
      const q = d.filter((x) => x.kind === "quotation").length;
      return `${d.length} document${d.length === 1 ? "" : "s"} · ${q} quotation${q === 1 ? "" : "s"} · ${d.length - q} invoice${d.length - q === 1 ? "" : "s"}`;
    },
    get cuRateLine() {
      const n = (this.detail?.operation_rates || []).length;
      return n ? `${n} negotiated rate${n === 1 ? "" : "s"}` : "";
    },
    printDoc(d) { window.open(`/api/quotations/${d.id}/print`, "_blank"); },

    // ---- per-customer operation rates ------------------------------------ //
    ops: [],                                  // the standard operation list
    rateRow: { operation: "", rate_per_hour: "", extra_rate: "", note: "" },
    stdRate(name) {
      const o = this.ops.find((x) => x.name === name);
      return o ? o.rate_per_hour : null;
    },
    effRate(r) { return (Number(r.rate_per_hour) || 0) + (Number(r.extra_rate) || 0); },
    pickOperation(name) {
      this.rateRow.operation = name;
      const std = this.stdRate(name);
      if (std !== null && this.rateRow.rate_per_hour === "") this.rateRow.rate_per_hour = std;
    },
    async saveRate() {
      if (!this.rateRow.operation) return this.flash("Pick an operation", "err");
      try {
        this.detail.operation_rates = await api(
          `/api/customers/${this.detail.id}/operation-rates`,
          { method: "POST", body: { ...this.rateRow,
              rate_per_hour: Number(this.rateRow.rate_per_hour) || 0,
              extra_rate: Number(this.rateRow.extra_rate) || 0 } });
        this.rateRow = { operation: "", rate_per_hour: "", extra_rate: "", note: "" };
        this.flash("Rate saved for this customer");
      } catch (e) { this.fail(e); }
    },
    editRate(r) { this.rateRow = { ...r }; },
    async removeRate(r) {
      if (!window.confirm(`Remove the custom ${r.operation} rate? Standard rates will apply.`)) return;
      try {
        this.detail.operation_rates = await api(
          `/api/customers/${this.detail.id}/operation-rates/delete`,
          { method: "POST", body: { operation: r.operation, rate_per_hour: 0 } });
      } catch (e) { this.fail(e); }
    },

    async boot() {
      window.addEventListener("unauth", () => { this.authed = false; });
      try {
        this.user = await api("/api/me");
        const mods = await api("/api/modules");
        this.authed = !!(mods.modules || []).find((m) => m.key === "customers" && m.granted);
      } catch (_) { this.authed = false; }
      if (!this.authed) { window.location.href = "/"; return; }
      try {
        this.ops = (await api("/api/customers/refs")).operations;
        await this.load();
      } catch (e) { this.fail(e); }
      this.booted = true;
      // ?open=3&tab=orders — a right-clicked record tab in its own browser tab
      const qs = new URLSearchParams(window.location.search);
      const want = qs.get("open"), tab = qs.get("tab");
      if (want) {
        window.history.replaceState({}, "", window.location.pathname);
        await this.open(Number(want),
          ["profile", "business", "orders", "rates"].includes(tab) ? tab : "profile");
      }
    },
    _seq: 0,
    async load() {
      const seq = ++this._seq;   // stale debounced responses must not win
      try {
        const rows = await api(`/api/customers?q=${encodeURIComponent(this.q)}&active_only=${this.activeOnly}`);
        if (seq === this._seq) this.rows = rows;
      } catch (e) { if (seq === this._seq) this.fail(e); }
    },

    async open(id, tab = "profile") {
      try {
        this.tab = tab;
        const [detail, biz] = await Promise.all([
          api(`/api/customers/${id}`),
          api(`/api/customers/${id}/business`),
        ]);
        this.detail = detail;
        this.biz = biz;
        this.contact = { name: "", phone: "", email: "", fax: "", role: "" };
      } catch (e) { this.fail(e); }
    },
    closeDetail() { this.detail = null; this.biz = null; this.load(); },

    newCust() {
      this.form = { id: null, name: "", abbr: "", gstin: "", address_billing: "",
                    address_shipping: "", country: "", payment_terms: "", notes: "" };
      this.formError = "";
    },
    // live preview of the code the server will assign: first letter of the name
    // (noise words dropped) + a serial, or a whole code typed in as-is
    get codePreview() {
      const f = this.form;
      if (!f || f.id) return "";
      const manual = (f.abbr || "").replace(/[^A-Za-z0-9]/g, "").toUpperCase();
      if (/^[A-Z][0-9]{2,}$/.test(manual)) return manual;
      if (manual) return manual[0] + "nn";
      const noise = ["pvt", "private", "ltd", "limited", "llp", "inc", "co", "company",
                     "corp", "corporation", "and", "the", "ms"];
      let w = (f.name || "").split(/[^A-Za-z0-9]+/).filter(Boolean);
      const sig = w.filter((x) => !noise.includes(x.toLowerCase()));
      w = sig.length ? sig : w;
      const letter = w.join("").match(/[A-Za-z]/);
      return letter ? letter[0].toUpperCase() + "nn" : "";
    },
    editCust() {
      this.form = { ...this.detail };
      this.formError = "";
    },
    async saveCust() {
      this.formError = "";
      const f = this.form;
      try {
        const saved = f.id
          ? await api(`/api/customers/${f.id}`, { method: "PUT", body: f })
          : await api("/api/customers", { method: "POST", body: f });
        this.form = null;
        this.detail = saved;   // open the record (new or updated) right away
        await this.load();
        this.flash(`${saved.name} saved`);
      } catch (e) { this.formError = e.message; }
    },
    async toggleActive() {
      const c = this.detail;
      try {
        this.detail = await api(`/api/customers/${c.id}/active?active=${!c.active}`,
                                { method: "POST" });
        this.flash(c.active ? "Customer deactivated" : "Customer reactivated");
      } catch (e) { this.fail(e); }
    },
    async removeCust() {
      if (!window.confirm(`Delete ${this.detail.name}? Only possible while they have no orders or drawings.`)) return;
      try {
        await api(`/api/customers/${this.detail.id}`, { method: "DELETE" });
        this.flash("Customer deleted");
        this.closeDetail();
      } catch (e) { this.fail(e); }
    },
    async addContact() {
      if (!this.contact.name.trim()) return this.flash("Contact name is required", "err");
      try {
        this.detail = await api(`/api/customers/${this.detail.id}/contacts`,
                                { method: "POST", body: this.contact });
        this.contact = { name: "", phone: "", email: "", fax: "", role: "" };
      } catch (e) { this.fail(e); }
    },
    async removeContact(k) {
      try {
        this.detail = await api(`/api/customers/contacts/${k.id}`, { method: "DELETE" });
      } catch (e) { this.fail(e); }
    },
  };
}
