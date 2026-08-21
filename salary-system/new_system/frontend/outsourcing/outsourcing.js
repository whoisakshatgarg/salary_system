// APEX THERMOCON — Outsourcing (🚚 tile): vendors, the job orders that leave
// the shop with a deadline, what comes back, and the bought-out stock it
// becomes. Five tabs, all deep-linkable: ?tab=outgoing|receipts|stock|vendors|
// documents and ?open=<id> (an os_order on Outgoing, a vendor on Vendors, an
// os_item on Stock).

async function api(path, { method = "GET", body, form } = {}) {
  const opts = { method, headers: { "X-Requested-With": "apex-payroll" } };
  if (form) {
    opts.body = form;                 // browser sets the multipart boundary itself
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
      detail = Array.isArray(d) ? d.map((x) => x.msg || JSON.stringify(x)).join("; ") : (d || detail);
    } catch (_) {}
    throw new Error(detail);
  }
  const ct = res.headers.get("content-type") || "";
  return ct.includes("application/json") ? res.json() : res;
}

function today() {
  // LOCAL date — toISOString() is UTC and says "yesterday" before 05:30 IST.
  const d = new Date();
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
}

const OS_TABS = ["outgoing", "receipts", "stock", "vendors", "documents"];

function osw() {
  return {
    booted: false,
    authed: false,
    user: null,
    tab: "outgoing",
    toast: { show: false, msg: "", kind: "ok" },
    flash(msg, kind = "ok") {
      this.toast = { show: true, msg, kind };
      setTimeout(() => (this.toast.show = false), 3200);
    },
    fail(e) { this.flash(e.message || String(e), "err"); },

    // Whole rupees stay clean (₹17,115); anything with paise in it shows both
    // (₹11.50), because a rate printed as "₹11.5" reads like a typo.
    money(n) {
      if (n === null || n === undefined || n === "") return "—";
      const v = Number(n);
      return "₹" + v.toLocaleString("en-IN", {
        minimumFractionDigits: Number.isInteger(v) ? 0 : 2,
        maximumFractionDigits: 2 });
    },
    num(v) {
      if (v === null || v === undefined || v === "") return "—";
      return String(Math.round(Number(v) * 1000) / 1000);
    },
    fmtDate(d) {
      if (!d) return "—";
      const [y, m, dd] = String(d).split("-").map(Number);
      if (!y || !m || !dd) return "—";
      return new Date(y, m - 1, dd).toLocaleDateString("en-IN",
        { day: "numeric", month: "short", year: "numeric" });
    },
    sizeLabel(b) {
      if (!b) return "";
      return b > 1048576 ? (b / 1048576).toFixed(1) + " MB" : Math.round(b / 1024) + " KB";
    },

    // ---- chips (page-prefixed: an object literal keeps only the LAST key) -- //
    osStatusLabel(k) {
      return (this.lists.statuses.find((s) => s.key === k) || {}).label || k;
    },
    osStatusClass(k) {
      return {
        open:      "bg-sky-50 text-sky-700 ring-1 ring-inset ring-sky-600/20",
        partial:   "bg-amber-50 text-amber-700 ring-1 ring-inset ring-amber-600/20",
        received:  "bg-emerald-50 text-emerald-700 ring-1 ring-inset ring-emerald-600/20",
        closed:    "bg-slate-100 text-slate-600 ring-1 ring-inset ring-slate-500/20",
        cancelled: "bg-rose-50 text-rose-700 ring-1 ring-inset ring-rose-600/20",
      }[k] || "bg-slate-100 text-slate-600 ring-1 ring-inset ring-slate-500/20";
    },
    osStatusDot(k) {
      return { open: "bg-sky-500", partial: "bg-amber-500", received: "bg-emerald-500",
               closed: "bg-slate-400", cancelled: "bg-rose-500" }[k] || "bg-slate-400";
    },
    // Deadlines: overdue rose, within a week amber, further out plain — so the
    // urgent rows are the ones that carry the eye.
    osDueClass(d) {
      if (d === null || d === undefined) return "text-slate-300";
      if (d < 0) return "text-rose-700 font-semibold";
      if (d <= 7) return "text-amber-700 font-medium";
      return "text-slate-500";
    },
    osDueLabel(d) {
      if (d === null || d === undefined) return "";
      if (d < 0) return `${-d}d late`;
      if (d === 0) return "today";
      return `in ${d}d`;
    },
    // A job that is back, closed or cancelled has no deadline left to miss, so
    // its date drops to plain text — otherwise a finished order shouts in rose
    // for ever and the rows that DO need chasing stop standing out.
    osLive(o) { return ["open", "partial"].includes(o.status); },
    osDueCell(o) {
      if (!o.deadline) return "text-slate-300";
      return this.osLive(o) ? this.osDueClass(o.days_left) : "text-slate-500";
    },
    osDueChip(o) {
      if (!this.osLive(o)) return "bg-slate-100 text-slate-600 ring-1 ring-inset ring-slate-900/10";
      if (o.days_left < 0) return "bg-rose-50 text-rose-700 ring-1 ring-inset ring-rose-600/20";
      if (o.days_left <= 7) return "bg-amber-50 text-amber-700 ring-1 ring-inset ring-amber-600/20";
      return "bg-slate-100 text-slate-600 ring-1 ring-inset ring-slate-900/10";
    },
    osDueChipDot(o) {
      if (!this.osLive(o)) return "bg-slate-400";
      if (o.days_left < 0) return "bg-rose-500";
      if (o.days_left <= 7) return "bg-amber-500";
      return "bg-slate-400";
    },
    osDueChipText(o) {
      return this.osLive(o) ? `${this.fmtDate(o.deadline)} · ${this.osDueLabel(o.days_left)}`
                            : this.fmtDate(o.deadline);
    },
    osMoveClass(t) {
      return { receive: "text-emerald-700", issue: "text-slate-700",
               adjust: "text-amber-700" }[t] || "text-slate-700";
    },
    osBarFill(pct) { return pct >= 100 ? "bg-emerald-500" : "bg-brand-600"; },

    lists: { vendors: [], units: [], purposes: [], statuses: [], orders: [] },

    async boot() {
      window.addEventListener("unauth", () => { this.authed = false; });
      try {
        this.user = await api("/api/me");
        const mods = await api("/api/modules");
        this.authed = !!(mods.modules || []).find(
          (m) => m.key === "outsourcing" && m.granted);
      } catch (_) { this.authed = false; }
      if (!this.authed) { window.location.href = "/"; return; }
      try {
        this.lists = await api("/api/outsourcing/refs");
        await this.loadDue();
        await this.load();
      } catch (e) { this.fail(e); }
      this.booted = true;
      // Deep links: /outsourcing/?tab=stock&open=4 lands on that item — which is
      // what makes right-click → "open in new tab" give the same screen. The
      // params are then stripped so Back/reload behave.
      const qs = new URLSearchParams(window.location.search);
      const view = qs.get("tab"), want = qs.get("open");
      if (view || want) window.history.replaceState({}, "", window.location.pathname);
      if (OS_TABS.includes(view)) await this.goTab(view);
      if (want) {
        const id = Number(want);
        if (this.tab === "vendors") await this.openVendor(id);
        else if (this.tab === "stock") await this.openItem(id);
        else await this.open(id);
      }
    },

    async goTab(t) {
      this.tab = t;
      if (t === "receipts") await this.loadReceipts();
      if (t === "stock") await this.loadStock();
      if (t === "vendors") await this.loadVendors();
      if (t === "documents") await this.loadDocs();
    },

    // ====================== OUTGOING ORDERS ============================== //
    due: null,
    data: { rows: [], status_counts: {}, statuses: [] },
    q: "", statusFilter: "", vendorFilter: "",
    detail: null,
    dseg: "items",

    async loadDue() {
      try { this.due = await api("/api/outsourcing/deadlines"); }
      catch (_) { this.due = null; }
    },
    dueCount() {
      const d = this.due;
      if (!d) return 0;
      return d.overdue.length + d.this_week.length + d.this_month.length;
    },
    _seq: 0,
    async load() {
      const seq = ++this._seq;         // stale debounced responses must not win
      try {
        const p = new URLSearchParams({ q: this.q, status: this.statusFilter,
                                        vendor_id: this.vendorFilter || 0 });
        const d = await api("/api/outsourcing/orders?" + p.toString());
        if (seq === this._seq) this.data = d;
      } catch (e) { if (seq === this._seq) this.fail(e); }
    },
    async open(id) {
      this.dseg = "items";
      try { this.detail = await api(`/api/outsourcing/orders/${id}`); }
      catch (e) { this.fail(e); }
    },
    closeDetail() { this.detail = null; this.load(); this.loadDue(); },
    dsegs: [
      { k: "items", label: "Lines" },
      { k: "receipts", label: "Receipts" },
      { k: "documents", label: "Documents" },
    ],
    dsegCount(k) {
      const d = this.detail;
      if (!d) return 0;
      return { items: d.items.length, receipts: d.receipts.length,
               documents: d.documents.length }[k] || 0;
    },
    // Terminal states are the only ones a person set, so they are the only ones
    // a person can take back — and where it lands is counted, not chosen.
    async reopen() {
      if (!window.confirm(
        `Reopen ${this.detail.os_no}? It goes back to following what has come back.`)) return;
      try {
        this.detail = await api(`/api/outsourcing/orders/${this.detail.id}/reopen`,
                                { method: "POST" });
        await this.load(); await this.loadDue();
        this.flash(`${this.detail.os_no} reopened as `
                   + this.osStatusLabel(this.detail.status).toLowerCase());
      } catch (e) { this.fail(e); }
    },
    async setStatus(status) {
      const what = status === "closed" ? "Close" : "Cancel";
      if (!window.confirm(`${what} ${this.detail.os_no}?`)) return;
      try {
        this.detail = await api(`/api/outsourcing/orders/${this.detail.id}/status`,
                                { method: "POST", body: { status } });
        await this.load(); await this.loadDue();
        this.flash(`${this.detail.os_no} ${status}`);
      } catch (e) { this.fail(e); }
    },

    // ---- the outgoing-order form (a full page, never a dialog) ----------- //
    form: null, formError: "", linkItems: [], inlineVendor: null,
    blankLine() {
      return { id: null, description: "", part_code: "", qty: "", unit: "Nos",
               unit_cost: "", order_item_id: null, order_item_label: "" };
    },
    newOrder() {
      this.form = { id: null, os_no: "", vendor_id: "", order_id: "", purpose: "",
                    date_sent: today(), deadline: "", notes: "",
                    items: [this.blankLine()] };
      this.formError = ""; this.linkItems = []; this.inlineVendor = null;
    },
    editOrder() {
      const d = this.detail;
      // a deactivated vendor must still show in the edit dropdown
      if (!this.lists.vendors.find((v) => v.id === d.vendor_id))
        this.lists.vendors.push({ id: d.vendor_id, code: d.vendor_code,
                                  name: d.vendor_name + " (inactive)" });
      this.form = {
        id: d.id, os_no: d.os_no, vendor_id: d.vendor_id, order_id: d.order_id || "",
        purpose: d.purpose || "", date_sent: d.date_sent || today(),
        deadline: d.deadline || "", notes: d.notes || "",
        items: d.items.map((i) => ({
          id: i.id, description: i.description || "", part_code: i.part_code || "",
          qty: i.qty, unit: i.unit || "Nos", unit_cost: i.unit_cost ?? "",
          order_item_id: i.order_item_id || null,
          order_item_label: i.order_item_description || "" })),
      };
      this.formError = ""; this.inlineVendor = null;
      this.loadLinkItems();
    },
    closeForm() { this.form = null; this.formError = ""; this.inlineVendor = null; },
    addLine() { this.form.items.push(this.blankLine()); },
    removeLine(i) { this.form.items.splice(i, 1); },
    lineAmount(l) {
      return Math.round((Number(l.qty) || 0) * (Number(l.unit_cost) || 0) * 100) / 100;
    },
    get formTotal() {
      return (this.form?.items || []).reduce((s, l) => s + this.lineAmount(l), 0);
    },
    // Inline vendor add: a new vendor mid-form is one row that appears under the
    // select, not a dialog stacked on a dialog.
    startVendor() { this.inlineVendor = { name: "", services: "", phone: "" }; },
    async saveInlineVendor() {
      const v = this.inlineVendor;
      if (!(v.name || "").trim()) return this.flash("Vendor name is required", "err");
      try {
        const saved = await api("/api/outsourcing/vendors", { method: "POST", body: v });
        this.lists.vendors.push({ id: saved.id, code: saved.code, name: saved.name });
        this.form.vendor_id = saved.id;
        this.inlineVendor = null;
        this.flash(`${saved.code} ${saved.name} added`);
      } catch (e) { this.fail(e); }
    },
    async loadLinkItems() {
      const oid = this.form?.order_id;
      if (!oid) { this.linkItems = []; return; }
      try {
        const r = await api(`/api/outsourcing/refs/order-items/${oid}`);
        this.linkItems = r.rows.map((x) => ({ ...x, send: "" }));
      } catch (_) { this.linkItems = []; }
    },
    async orderLinkChanged() {
      // Dropping the link would leave lines pointing at parts of an order this
      // job no longer serves, which the backend refuses — so clear them here
      // rather than let the save fail with a puzzle.
      for (const l of this.form.items) { l.order_item_id = null; l.order_item_label = ""; }
      await this.loadLinkItems();
    },
    linkLabel(it) {
      return (it.drawing_no ? it.drawing_no + (it.revision ? ` rev ${it.revision}` : "") + " · " : "")
             + (it.description || "part");
    },
    sendPart(it) {
      const qty = Number(it.send);
      if (!(qty > 0)) return this.flash("How many of that part are going out?", "err");
      if (qty > it.qty) return this.flash(`The order only has ${it.qty} of that part`, "err");
      const blank = this.form.items.find(
        (l) => !l.order_item_id && !(l.description || "").trim() && !l.qty);
      const line = blank || this.blankLine();
      line.description = this.linkLabel(it);
      line.qty = qty;
      line.unit = it.unit || "Nos";
      line.order_item_id = it.id;
      line.order_item_label = it.description || "";
      if (!blank) this.form.items.push(line);
      it.send = "";
    },
    async saveOrder() {
      this.formError = "";
      const f = this.form;
      const payload = {
        vendor_id: Number(f.vendor_id) || 0,
        order_id: f.order_id ? Number(f.order_id) : null,
        purpose: f.purpose, date_sent: f.date_sent, deadline: f.deadline,
        notes: f.notes,
        items: f.items
          .filter((l) => (l.description || "").trim() || (l.part_code || "").trim() || l.qty)
          .map((l) => ({ id: l.id || null, description: l.description,
                         part_code: l.part_code, qty: Number(l.qty),
                         unit: l.unit || "Nos",
                         unit_cost: l.unit_cost === "" ? null : Number(l.unit_cost),
                         order_item_id: l.order_item_id || null })),
      };
      try {
        const saved = f.id
          ? await api(`/api/outsourcing/orders/${f.id}`, { method: "PUT", body: payload })
          : await api("/api/outsourcing/orders", { method: "POST", body: payload });
        this.form = null;
        this.detail = saved;
        await this.load(); await this.loadDue();
        this.flash(`${saved.os_no} saved`);
      } catch (e) { this.formError = e.message; }
    },

    // ====================== RECEIPTS ===================================== //
    rec: { q: "", rows: [] },
    recForm: null, recError: "",
    _recSeq: 0,
    async loadReceipts() {
      const seq = ++this._recSeq;
      try {
        const r = await api("/api/outsourcing/receipts?q=" + encodeURIComponent(this.rec.q));
        if (seq === this._recSeq) this.rec.rows = r.rows;
      } catch (e) { if (seq === this._recSeq) this.fail(e); }
    },
    // A receipt has no screen of its own: it only means anything against the
    // job order it closes out, so the list opens that.
    async openReceiptOrder(r) { this.tab = "outgoing"; await this.open(r.os_order_id); },

    newReceipt() {
      const d = this.detail;
      this.recError = "";
      this.recForm = {
        os_order_id: d.id, os_no: d.os_no, vendor_name: d.vendor_name,
        receipt_date: today(), inspection_notes: "", accepted: true,
        // prefilled with what is still outstanding: that is what arrives
        lines: d.items.map((i) => ({
          os_order_item_id: i.id, label: i.description || i.part_code || "line",
          part_code: i.part_code || "", ordered: i.qty, already: i.received,
          pending: i.pending, unit: i.unit || "Nos", qty: i.pending || "",
          os_item_id: "", material: "", size_section: "",
          unit_cost: i.unit_cost ?? "" })),
      };
      this.loadStockOptions();
    },
    stockOptions: [],
    async loadStockOptions() {
      try { this.stockOptions = (await api("/api/outsourcing/stock")).rows; }
      catch (_) { this.stockOptions = []; }
    },
    recTotal() {
      return (this.recForm?.lines || []).reduce((n, l) => n + (Number(l.qty) || 0), 0);
    },
    async saveReceipt() {
      this.recError = "";
      const f = this.recForm;
      const lines = f.lines.filter((l) => Number(l.qty) > 0).map((l) => ({
        os_order_item_id: l.os_order_item_id, qty: Number(l.qty),
        os_item_id: l.os_item_id ? Number(l.os_item_id) : null,
        description: l.os_item_id ? "" : l.label,
        part_code: l.part_code, material: l.material, size_section: l.size_section,
        unit: l.unit, unit_cost: l.unit_cost === "" ? null : Number(l.unit_cost),
      }));
      if (!lines.length) { this.recError = "Nothing on this receipt yet"; return; }
      try {
        await api("/api/outsourcing/receipts", { method: "POST", body: {
          os_order_id: f.os_order_id, receipt_date: f.receipt_date,
          inspection_notes: f.inspection_notes, accepted: f.accepted, lines } });
        this.recForm = null;
        await this.open(f.os_order_id);
        await this.load(); await this.loadDue();
        this.flash("Receipt recorded");
      } catch (e) { this.recError = e.message; }
    },
    async removeReceipt(r) {
      if (!window.confirm(
        "Delete this receipt? The stock it brought in goes back out again.")) return;
      try {
        await api(`/api/outsourcing/receipts/${r.id}`, { method: "DELETE" });
        if (this.detail) await this.open(this.detail.id);
        await this.loadReceipts(); await this.load(); await this.loadDue();
        this.flash("Receipt deleted");
      } catch (e) { this.fail(e); }
    },

    // ====================== STOCK ======================================== //
    stock: { q: "", rows: [], stats: {} },
    item: null, itemEdit: null, adj: null, iss: null,
    _stockSeq: 0,
    async loadStock() {
      const seq = ++this._stockSeq;
      try {
        const r = await api("/api/outsourcing/stock?q=" + encodeURIComponent(this.stock.q));
        if (seq === this._stockSeq) this.stock = { q: this.stock.q, ...r };
      } catch (e) { if (seq === this._stockSeq) this.fail(e); }
    },
    async openItem(id) {
      this.itemEdit = null; this.adj = null; this.iss = null;
      try { this.item = await api(`/api/outsourcing/stock/${id}`); }
      catch (e) { this.fail(e); }
    },
    closeItem() { this.item = null; this.loadStock(); },
    startItemEdit() {
      const i = this.item;
      this.itemEdit = {
        description: i.description || "", part_code: i.part_code || "",
        material: i.material || "", size_section: i.size_section || "",
        unit: i.unit || "Nos", unit_cost: i.unit_cost ?? "",
        vendor_id: i.vendor_id || "", notes: i.notes || "", active: !!i.active };
    },
    async saveItem() {
      try {
        this.item = await api(`/api/outsourcing/stock/${this.item.id}`, {
          method: "PUT", body: { ...this.itemEdit,
            vendor_id: this.itemEdit.vendor_id ? Number(this.itemEdit.vendor_id) : null,
            unit_cost: this.itemEdit.unit_cost === "" ? null : Number(this.itemEdit.unit_cost) } });
        this.itemEdit = null;
        this.flash("Item saved");
      } catch (e) { this.fail(e); }
    },
    startAdjust() {
      this.iss = null;
      this.adj = { qty: "", mv_date: today(), remarks: "" };
    },
    async saveAdjust() {
      try {
        this.item = await api(`/api/outsourcing/stock/${this.item.id}/adjust`, {
          method: "POST", body: { ...this.adj, qty: Number(this.adj.qty) } });
        this.adj = null;
        await this.loadStock();
        this.flash("Stock adjusted");
      } catch (e) { this.fail(e); }
    },
    startIssue() {
      this.adj = null;
      this.iss = { qty: "", order_id: "", mv_date: today(), remarks: "" };
    },
    async saveIssue() {
      try {
        this.item = await api(`/api/outsourcing/stock/${this.item.id}/issue`, {
          method: "POST", body: { ...this.iss, qty: Number(this.iss.qty) } });
        this.iss = null;
        await this.loadStock();
        this.flash("Issued");
      } catch (e) { this.fail(e); }
    },

    // ====================== VENDORS ====================================== //
    ven: { q: "", active: true, rows: [] },
    vendor: null, vendorForm: null, vendorError: "",
    _venSeq: 0,
    async loadVendors() {
      const seq = ++this._venSeq;
      try {
        const p = new URLSearchParams({ q: this.ven.q, active: this.ven.active });
        const r = await api("/api/outsourcing/vendors?" + p.toString());
        if (seq === this._venSeq) this.ven.rows = r.rows;
      } catch (e) { if (seq === this._venSeq) this.fail(e); }
    },
    async openVendor(id) {
      this.vendorForm = null;
      try { this.vendor = await api(`/api/outsourcing/vendors/${id}`); }
      catch (e) { this.fail(e); }
    },
    closeVendor() { this.vendor = null; this.loadVendors(); },
    newVendor() {
      this.vendorError = "";
      this.vendorForm = { id: null, code: "", name: "", contact_name: "", phone: "",
                          email: "", address: "", services: "", notes: "" };
    },
    editVendor() {
      const v = this.vendor;
      this.vendorError = "";
      this.vendorForm = { id: v.id, code: v.code || "", name: v.name,
                          contact_name: v.contact_name || "", phone: v.phone || "",
                          email: v.email || "", address: v.address || "",
                          services: v.services || "", notes: v.notes || "" };
    },
    async saveVendor() {
      this.vendorError = "";
      const f = this.vendorForm;
      try {
        const saved = f.id
          ? await api(`/api/outsourcing/vendors/${f.id}`, { method: "PUT", body: f })
          : await api("/api/outsourcing/vendors", { method: "POST", body: f });
        this.vendorForm = null;
        this.vendor = saved;
        this.lists = await api("/api/outsourcing/refs");
        await this.loadVendors();
        this.flash(`${saved.code} ${saved.name} saved`);
      } catch (e) { this.vendorError = e.message; }
    },
    async toggleVendorActive(v) {
      try {
        const r = await api(`/api/outsourcing/vendors/${v.id}/active?active=${!v.active}`,
                            { method: "POST" });
        if (this.vendor && this.vendor.id === v.id) this.vendor = r;
        this.lists = await api("/api/outsourcing/refs");
        await this.loadVendors();
      } catch (e) { this.fail(e); }
    },

    // ====================== DOCUMENTS ==================================== //
    docs: { rows: [] },
    up: { vendor_id: "", os_order_id: "", label: "", busy: false },
    async loadDocs() {
      try { this.docs = await api("/api/outsourcing/documents"); }
      catch (e) { this.fail(e); }
    },
    // The picker needs every job order, not just the filtered list, so it is
    // read once here rather than borrowed from whatever the table is showing.
    osOrderOptions: [],
    async loadOrderOptions() {
      try { this.osOrderOptions = (await api("/api/outsourcing/orders")).rows; }
      catch (_) { this.osOrderOptions = []; }
    },
    async pickDocs(ev, ctx = {}) {
      const files = Array.from(ev.target.files || []);
      if (!files.length) return;
      const vendor_id = ctx.vendor_id ?? this.up.vendor_id;
      const os_order_id = ctx.os_order_id ?? this.up.os_order_id;
      if (!vendor_id && !os_order_id) {
        ev.target.value = "";
        return this.flash("Choose a vendor or an outgoing order first", "err");
      }
      this.up.busy = true;
      try {
        const fd = new FormData();
        fd.append("vendor_id", vendor_id || 0);
        fd.append("os_order_id", os_order_id || 0);
        fd.append("label", ctx.label ?? this.up.label);
        for (const f of files) fd.append("files", await this.osCompress(f));
        const res = await api("/api/outsourcing/documents", { method: "POST", form: fd });
        this.flash(`${res.saved.length} file(s) filed`);
        if (this.vendor) await this.openVendor(this.vendor.id);
        if (this.detail) await this.open(this.detail.id);
        if (this.tab === "documents") await this.loadDocs();
      } catch (e) { this.fail(e); } finally {
        this.up.busy = false;
        ev.target.value = "";
      }
    },
    // Shrink big photos client-side (a challan shot on a phone): max 1600px,
    // JPEG q0.82. PDFs and small images pass through untouched.
    async osCompress(file) {
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
        return new File([blob], file.name.replace(/\.\w+$/, "") + ".jpg",
                        { type: "image/jpeg" });
      } catch (_) { return file; }        // exotic format — upload the original
    },
    viewDoc(d) {
      const u = `/api/outsourcing/documents/${d.id}`;
      // Packaged app: window.open is dead inside pywebview — use the shell
      // bridge; browsers get a normal tab (download as fallback).
      if (window.pywebview && window.pywebview.api && window.pywebview.api.open_path) {
        window.pywebview.api.open_path(u).catch(() => this.downloadDoc(d));
        return;
      }
      const w = window.open(u, "_blank");
      if (!w) this.downloadDoc(d);
    },
    downloadDoc(d) { window.location = `/api/outsourcing/documents/${d.id}?download=1`; },
    async deleteDoc(d) {
      if (!window.confirm(`Delete "${d.filename}"?`)) return;
      try {
        await api(`/api/outsourcing/documents/${d.id}`, { method: "DELETE" });
        if (this.vendor) await this.openVendor(this.vendor.id);
        if (this.detail) await this.open(this.detail.id);
        if (this.tab === "documents") await this.loadDocs();
        this.flash("Document deleted");
      } catch (e) { this.fail(e); }
    },
  };
}
