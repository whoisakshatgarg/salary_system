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
    contact: { name: "", phone: "", email: "", role: "" },

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
    stageClass(s) {
      return { payment: "bg-emerald-100 text-emerald-700", dispatch: "bg-teal-100 text-teal-700",
               production: "bg-amber-100 text-amber-800", qc: "bg-purple-100 text-purple-700",
               po: "bg-indigo-100 text-indigo-700", quote: "bg-sky-100 text-sky-700",
             }[s] || "bg-slate-200 text-slate-600";
    },
    printDoc(d) { window.open(`/api/quotations/${d.id}/print`, "_blank"); },

    async boot() {
      window.addEventListener("unauth", () => { this.authed = false; });
      try {
        this.user = await api("/api/me");
        const mods = await api("/api/modules");
        this.authed = !!(mods.modules || []).find((m) => m.key === "customers" && m.granted);
      } catch (_) { this.authed = false; }
      if (!this.authed) { window.location.href = "/"; return; }
      try { await this.load(); } catch (e) { this.fail(e); }
      this.booted = true;
    },
    _seq: 0,
    async load() {
      const seq = ++this._seq;   // stale debounced responses must not win
      try {
        const rows = await api(`/api/customers?q=${encodeURIComponent(this.q)}&active_only=${this.activeOnly}`);
        if (seq === this._seq) this.rows = rows;
      } catch (e) { if (seq === this._seq) this.fail(e); }
    },

    async open(id) {
      try {
        this.tab = "profile";
        const [detail, biz] = await Promise.all([
          api(`/api/customers/${id}`),
          api(`/api/customers/${id}/business`),
        ]);
        this.detail = detail;
        this.biz = biz;
        this.contact = { name: "", phone: "", email: "", role: "" };
      } catch (e) { this.fail(e); }
    },
    closeDetail() { this.detail = null; this.biz = null; this.load(); },

    newCust() {
      this.form = { id: null, name: "", abbr: "", gstin: "", address_billing: "",
                    address_shipping: "", payment_terms: "", notes: "" };
      this.formError = "";
    },
    // live preview of the code the server will assign (initials, noise words dropped)
    get codePreview() {
      const f = this.form;
      if (!f || f.id) return "";
      const manual = (f.abbr || "").replace(/[^A-Za-z0-9]/g, "").toUpperCase();
      if (manual) return manual + "nn";
      const noise = ["pvt", "private", "ltd", "limited", "llp", "inc", "co", "company",
                     "corp", "corporation", "and", "the", "ms"];
      let w = (f.name || "").split(/[^A-Za-z0-9]+/).filter(Boolean);
      const sig = w.filter((x) => !noise.includes(x.toLowerCase()));
      w = sig.length ? sig : w;
      if (!w.length) return "";
      const abbr = w.length === 1 ? w[0].slice(0, 2) : w[0][0] + w[1][0];
      return abbr.toUpperCase() + "nn";
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
        this.contact = { name: "", phone: "", email: "", role: "" };
      } catch (e) { this.fail(e); }
    },
    async removeContact(k) {
      try {
        this.detail = await api(`/api/customers/contacts/${k.id}`, { method: "DELETE" });
      } catch (e) { this.fail(e); }
    },
  };
}
