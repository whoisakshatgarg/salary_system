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
    form: null,
    formError: "",
    contact: { name: "", phone: "", email: "", role: "" },

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
        this.detail = await api(`/api/customers/${id}`);
        this.contact = { name: "", phone: "", email: "", role: "" };
      } catch (e) { this.fail(e); }
    },
    closeDetail() { this.detail = null; this.load(); },

    newCust() {
      this.form = { id: null, name: "", gstin: "", address_billing: "",
                    address_shipping: "", payment_terms: "", notes: "" };
      this.formError = "";
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
