// APEX THERMOCON — SOP Documents (/papers/): every generated document that
// hangs off an order — quotation, ack, work order, BOM, invoice, packing list,
// COC, test certificate (SOP-DESIGN §8).
//
// Two screens, never a popup (owner: popups are disruptive):
//   list    framed instrument table, filtered by ?kind= (a comma list, which
//           is how the four SOP home tiles arrive: ?kind=work_order,bom)
//   editor  a FULL PAGE per paper — every payload field editable, no field
//           locked, the document's own number read-only because the counter
//           already spent it.
//
// Deep links: ?kind=… ?open=<id> ?new=<kind>&order=<id> (the last is what the
// order page's pipeline strip ＋ buttons point at). boot() honours them and
// strips them with replaceState, so right-click → "open in new tab" works and
// reload doesn't re-fire a create.

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
  // LOCAL date — toISOString() is UTC and says "yesterday" before 05:30 IST.
  const d = new Date();
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
}

// Any ONE of the four SOP grants opens this workspace — the same set the
// backend guard accepts (documents/router.SOP_KEYS, SOP-DESIGN §10).
const PAPER_KEYS = ["acks", "production_docs", "shipping_docs", "quality_docs"];

// The pill nav. Real <a href="?kind=…"> links, so every filter is a URL.
const PAPER_NAV = [
  { kind: "", label: "All" },
  { kind: "quotation", label: "Quotations" },
  { kind: "ack", label: "Acks" },
  { kind: "work_order", label: "Work Orders" },
  { kind: "bom", label: "BOMs" },
  { kind: "invoice", label: "Invoices" },
  { kind: "packing_list", label: "Packing Lists" },
  { kind: "coc", label: "COCs" },
  { kind: "test_cert", label: "Test Certs" },
];

// Payload fields that must stay NUMBERS. The engine writes a payload value
// straight into its cell, so a string "0.675" lands as text in a money cell —
// right number, wrong type, and the column stops adding up. Coerced on save;
// a value that isn't a number (the invoice's '-' for a free replacement line)
// is left exactly as typed.
const NUM_FIELDS = {
  quotation:    { top: ["total"], items: ["sno", "qty", "unit_price", "total"] },
  ack:          { top: ["total"], items: ["sno", "qty", "unit_price", "total"] },
  work_order:   { top: [], items: ["sno", "qty"] },
  invoice:      { top: [], items: ["sno", "qty"] },      // rate/amount: '-' allowed
  packing_list: { top: [], items: ["sno", "qty"] },
  coc:          { top: ["qty_shipped"], items: [] },
  test_cert:    { top: [], items: ["sno", "qty"] },
  bom:          { top: [], items: ["sno", "qty_per", "total_qty"] },
};

const CHEM = ["C", "Mn", "Si", "P", "S", "Cr", "Ni", "Mo"];

// amount-in-words, mirroring payloads.amount_in_words (CONVENTIONS §5)
const W_ONES = ["", "One", "Two", "Three", "Four", "Five", "Six", "Seven", "Eight",
  "Nine", "Ten", "Eleven", "Twelve", "Thirteen", "Fourteen", "Fifteen", "Sixteen",
  "Seventeen", "Eighteen", "Nineteen"];
const W_TENS = ["", "", "Twenty", "Thirty", "Forty", "Fifty", "Sixty", "Seventy",
  "Eighty", "Ninety"];
const W_GROUPS = ["", "Thousand", "Million", "Billion", "Trillion"];
const W_SUB = { USD: "Cents", GBP: "Pence", EUR: "Cents", INR: "Paise" };

function wordsUnderThousand(n) {
  const out = [];
  if (n >= 100) { out.push(W_ONES[Math.floor(n / 100)], "Hundred"); n %= 100; }
  if (n >= 20) { out.push(W_TENS[Math.floor(n / 10)]); n %= 10; }
  if (n) out.push(W_ONES[n]);
  return out;
}

function numberWords(n) {
  if (!n) return "Zero";
  const groups = [];
  while (n) { groups.push(n % 1000); n = Math.floor(n / 1000); }
  const parts = [];
  for (let p = groups.length - 1; p >= 0; p--) {
    if (!groups[p]) continue;
    parts.push(...wordsUnderThousand(groups[p]));
    if (p) parts.push(W_GROUPS[p]);
  }
  return parts.join(" ");
}

function amountWords(amount, code = "USD") {
  const value = Number(amount);
  if (!isFinite(value)) return "";
  const sign = value < 0 ? "Minus " : "";
  const cents = Math.round(Math.abs(value) * 100);
  const whole = Math.floor(cents / 100), sub = cents % 100;
  let text = sign + numberWords(whole);
  if (sub) text += ` and ${W_SUB[code] || "Cents"} ${numberWords(sub)}`;
  return `${code} ${text} Only`;
}

function pw() {
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

    nav: PAPER_NAV,
    chem: CHEM,
    refs: { kinds: [], statuses: [], transitions: {} },
    data: { rows: [], counts: {}, kinds: [] },
    kindFilter: "",
    statusFilter: "",
    q: "",
    view: "list",          // 'list' | 'editor' | 'create'
    ed: null,              // the open paper
    edBusy: false,
    manual: {},            // grid cells the office typed over — see recalc()
    form: null,            // the create form

    // ---- formatting -------------------------------------------------------- //
    fmtDate(d) {
      if (!d) return "—";
      const [y, m, dd] = String(d).slice(0, 10).split("-").map(Number);
      if (!y || !m || !dd) return "—";
      return new Date(y, m - 1, dd).toLocaleDateString("en-IN",
        { day: "numeric", month: "short", year: "numeric" });
    },
    fmtWhen(t) {
      if (!t) return "—";
      const d = new Date(t);
      return isNaN(d) ? "—" : d.toLocaleDateString("en-IN",
        { day: "numeric", month: "short", year: "numeric" });
    },

    // ---- chips (page-prefixed: an object literal keeps only the LAST key) -- //
    pwKindLabel(k) {
      return (this.refs.kinds.find((x) => x.kind === k) || {}).label || k;
    },
    pwKindClass(k) {
      return {
        quotation:    "bg-sky-50 text-sky-700 ring-1 ring-inset ring-sky-600/20",
        ack:          "bg-indigo-50 text-indigo-700 ring-1 ring-inset ring-indigo-600/20",
        work_order:   "bg-amber-50 text-amber-700 ring-1 ring-inset ring-amber-600/20",
        bom:          "bg-amber-50 text-amber-700 ring-1 ring-inset ring-amber-600/20",
        invoice:      "bg-indigo-50 text-indigo-700 ring-1 ring-inset ring-indigo-600/20",
        packing_list: "bg-sky-50 text-sky-700 ring-1 ring-inset ring-sky-600/20",
        coc:          "bg-violet-50 text-violet-700 ring-1 ring-inset ring-violet-600/20",
        test_cert:    "bg-violet-50 text-violet-700 ring-1 ring-inset ring-violet-600/20",
      }[k] || "bg-slate-100 text-slate-600 ring-1 ring-inset ring-slate-500/20";
    },
    pwKindDot(k) {
      return {
        quotation: "bg-sky-500", ack: "bg-indigo-500", work_order: "bg-amber-500",
        bom: "bg-amber-500", invoice: "bg-indigo-500", packing_list: "bg-sky-500",
        coc: "bg-violet-500", test_cert: "bg-violet-500",
      }[k] || "bg-slate-400";
    },
    pwStatusClass(s) {
      return {
        draft:      "bg-slate-100 text-slate-600 ring-1 ring-inset ring-slate-500/20",
        final:      "bg-emerald-50 text-emerald-700 ring-1 ring-inset ring-emerald-600/20",
        sent:       "bg-brand-50 text-brand-700 ring-1 ring-inset ring-brand-600/20",
        superseded: "bg-amber-50 text-amber-700 ring-1 ring-inset ring-amber-600/20",
        void:       "bg-rose-50 text-rose-700 ring-1 ring-inset ring-rose-600/20",
      }[s] || "bg-slate-100 text-slate-600 ring-1 ring-inset ring-slate-500/20";
    },
    pwStatusDot(s) {
      return { draft: "bg-slate-400", final: "bg-emerald-500", sent: "bg-brand-600",
               superseded: "bg-amber-500", void: "bg-rose-500" }[s] || "bg-slate-400";
    },

    // ---- boot -------------------------------------------------------------- //
    async boot() {
      window.addEventListener("unauth", () => { this.authed = false; });
      try {
        this.user = await api("/api/me");
        const mods = await api("/api/modules");
        const granted = new Set((mods.modules || [])
          .filter((m) => m.granted).map((m) => m.key));
        this.authed = PAPER_KEYS.some((k) => granted.has(k));
      } catch (_) { this.authed = false; }
      if (!this.authed) { window.location.href = "/"; return; }

      const qs = new URLSearchParams(window.location.search);
      const kind = qs.get("kind"), want = qs.get("open");
      const wantNew = qs.get("new"), wantOrder = qs.get("order");
      if (kind !== null) this.kindFilter = kind;
      if (kind !== null || want || wantNew || wantOrder) {
        window.history.replaceState({}, "", window.location.pathname);
      }
      try {
        this.refs = await api("/api/papers/refs");
        await this.load();
      } catch (e) { this.fail(e); }
      this.booted = true;
      if (want) await this.openPaper(Number(want));
      else if (wantNew !== null) {
        await this.startCreate(wantNew || "", wantOrder ? Number(wantOrder) : null);
      }
    },

    _seq: 0,
    async load() {
      const seq = ++this._seq;             // a stale debounced answer must not win
      try {
        const p = new URLSearchParams({ kind: this.kindFilter, q: this.q,
                                        status: this.statusFilter });
        const d = await api("/api/papers?" + p.toString());
        if (seq === this._seq) this.data = d;
      } catch (e) { if (seq === this._seq) this.fail(e); }
    },
    goKind(kind) {
      this.kindFilter = kind;
      this.view = "list";
      this.ed = null;
      this.form = null;
      this.load();
    },
    navHref(kind) { return kind ? `?kind=${encodeURIComponent(kind)}` : "?kind="; },
    navActive(kind) { return this.kindFilter === kind; },
    backHref() { return this.navHref(this.kindFilter); },
    // How many of the listed rows a nav pill would keep — the count only means
    // something for a single kind, so the comma tiles just show their total.
    kindCount(kind) {
      if (!kind) return Object.values(this.data.counts || {}).reduce((a, b) => a + b, 0);
      return (this.data.counts || {})[kind] || 0;
    },
    get filterTitle() {
      if (!this.kindFilter) return "All documents";
      return this.kindFilter.split(",").map((k) => this.pwKindLabel(k.trim())).join(" · ");
    },

    // ---- the editor -------------------------------------------------------- //
    async openPaper(id) {
      try {
        this.ed = await api(`/api/papers/${id}`);
        this.manual = {};
        this.view = "editor";
        window.scrollTo(0, 0);
      } catch (e) { this.fail(e); }
    },
    backToList() {
      this.ed = null;
      this.view = "list";
      this.load();
    },
    get isDraft() { return this.ed && this.ed.status === "draft"; },
    can(status) {
      const t = this.refs.transitions || {};
      return !!this.ed && (t[this.ed.status] || []).includes(status);
    },

    // A payload value is a number when the schema says the cell is numeric —
    // otherwise it is left as typed, so the invoice's '-' survives.
    _numify(v) {
      const s = String(v ?? "").trim();
      if (s === "") return "";
      const n = Number(s);
      return isFinite(n) && /^-?\d*\.?\d+$/.test(s) ? n : v;
    },
    _coerce(kind, payload) {
      const spec = NUM_FIELDS[kind] || { top: [], items: [] };
      const out = JSON.parse(JSON.stringify(payload));
      for (const f of spec.top) if (f in out) out[f] = this._numify(out[f]);
      const fixItems = (list) => (list || []).forEach((it) => {
        for (const f of spec.items) if (f in it) it[f] = this._numify(it[f]);
      });
      fixItems(out.items);
      (out.boxes || []).forEach((b) => fixItems(b.items));
      if (out.totals) {
        for (const f of ["qty", "net_wt", "gross_wt"]) {
          if (f in out.totals) out.totals[f] = this._numify(out.totals[f]);
        }
      }
      return out;
    },

    async save() {
      if (!this.ed) return;
      this.edBusy = true;
      try {
        this.ed = await api(`/api/papers/${this.ed.id}`, {
          method: "PUT", body: { payload: this._coerce(this.ed.kind, this.ed.payload) } });
        this.flash("Draft saved");
      } catch (e) { this.fail(e); } finally { this.edBusy = false; }
    },
    async refillPaper() {
      if (!window.confirm(
        "Refill rebuilds this draft from the order, the ledger and the heat "
        + "register. Anything typed by hand on it is replaced. Continue?")) return;
      this.edBusy = true;
      try {
        this.ed = await api(`/api/papers/${this.ed.id}/refill`, { method: "POST" });
        this.manual = {};
        this.flash("Refilled from the order");
      } catch (e) { this.fail(e); } finally { this.edBusy = false; }
    },
    async setStatus(status, note) {
      if (note && !window.confirm(note)) return;
      this.edBusy = true;
      try {
        this.ed = await api(`/api/papers/${this.ed.id}/status`, {
          method: "POST", body: { status } });
        this.flash(`Marked ${status}`);
      } catch (e) { this.fail(e); } finally { this.edBusy = false; }
    },
    async finalise() {
      await this.save();
      await this.setStatus("final",
        "Finalise freezes this paper — it can still be downloaded, but editing "
        + "it afterwards means raising a revision. Continue?");
    },
    async revisePaper() {
      this.edBusy = true;
      try {
        const fresh = await api(`/api/papers/${this.ed.id}/revise`, { method: "POST" });
        this.flash(`Revision ${fresh.revision} opened`);
        await this.openPaper(fresh.id);
      } catch (e) { this.fail(e); } finally { this.edBusy = false; }
    },
    async removePaper() {
      if (!window.confirm(
        `Delete draft ${this.ed.display_no}? Nothing has been issued under it yet.`)) return;
      try {
        await api(`/api/papers/${this.ed.id}`, { method: "DELETE" });
        this.flash("Draft deleted");
        this.backToList();
      } catch (e) { this.fail(e); }
    },
    download() {
      window.location = `/api/papers/${this.ed.id}/file?download=1`;
    },

    // ---- payload editing helpers ------------------------------------------- //
    // A textarea over a list of lines: the join/split round-trips exactly, so
    // typing a newline doesn't fight the cursor.
    lines(obj, key) { return ((obj || {})[key] || []).join("\n"); },
    setLines(obj, key, text) { obj[key] = String(text).split("\n"); },

    blankItem(kind) {
      const sno = ((this.ed?.payload?.items) || []).length + 1;
      return {
        quotation:  { sno, code: "", description: "", qty: "", unit: "EA",
                      unit_price: "", total: "" },
        ack:        { sno, code: "", description: "", material: "", qty: "",
                      unit: "EA", unit_price: "", total: "" },
        work_order: { sno, part_no: "", item: "", qty: "", material: "",
                      marking: "-", remarks: "" },
        invoice:    { sno, code_desc: "", po: "", qty: "", net_wt: "",
                      rate: "-", amount: "-" },
        coc:        { sno },
        test_cert:  { sno, item: "", size: "", qty: "", component: "-", heat_no: "",
                      material: "", chem: {}, spare_1: "-", spare_2: "-",
                      spare_3: "-", spare_4: "-", spare_5: "-" },
        bom:        { sno, part_no: "", description: "", size: "", material: "",
                      heat_or_os: "", source: "In-House", qty_per: "",
                      total_qty: "", unit: "", remarks: "" },
      }[kind] || { sno };
    },
    addItem() {
      this.ed.payload.items.push(this.blankItem(this.ed.kind));
    },
    removeItem(i) {
      this.ed.payload.items.splice(i, 1);
      this.renumber(this.ed.payload.items);
    },
    renumber(list) { (list || []).forEach((it, n) => { it.sno = n + 1; }); },

    // A total the office typed over is never recomputed again — the grid helps,
    // it does not overrule.
    markManual(key) { this.manual[key] = true; },
    lineTotal(it, i) {
      if (this.manual["t" + i]) return;
      const qty = Number(it.qty), price = Number(it.unit_price);
      if (!isFinite(qty) || !isFinite(price)) return;
      it.total = Math.round(qty * price * 100) / 100;
      this.sumTotal();
    },
    sumTotal() {
      if (this.manual.total) return;
      const n = (this.ed.payload.items || [])
        .reduce((a, it) => a + (Number(it.total) || 0), 0);
      this.ed.payload.total = Math.round(n * 100) / 100;
    },
    recalcAll() {
      this.manual = {};
      const p = this.ed.payload;
      if (this.ed.kind === "quotation" || this.ed.kind === "ack") {
        (p.items || []).forEach((it, i) => this.lineTotal(it, i));
        this.sumTotal();
      }
      if (this.ed.kind === "invoice") this.invoiceTotals();
      if (this.ed.kind === "packing_list") this.boxTotals();
      this.flash("Totals recalculated");
    },
    // The invoice sums quantity, net weight and money; a '-' line (a free
    // replacement, CONVENTIONS §5) counts for none of them.
    invoiceTotals() {
      const p = this.ed.payload;
      const num = (v) => { const n = Number(v); return isFinite(n) ? n : 0; };
      if (!this.manual.tqty) {
        p.totals.qty = (p.items || []).reduce((a, it) => a + num(it.qty), 0);
      }
      if (!this.manual.twt) {
        const wt = (p.items || []).reduce((a, it) => a + num(it.net_wt), 0);
        p.totals.net_wt = wt ? wt.toFixed(3) : "";
      }
      if (!this.manual.tamt) {
        const amt = (p.items || []).reduce((a, it) => a + num(it.amount), 0);
        p.totals.amount = amt.toFixed(2);
      }
    },
    suggestWords() {
      const p = this.ed.payload;
      p.amount_words = amountWords(p.totals.amount, (p.currency || {}).code || "USD");
    },
    // ---- packing list boxes ------------------------------------------------ //
    addBox() {
      const n = (this.ed.payload.boxes || []).length + 1;
      this.ed.payload.boxes.push({ box_label: `Box No. ${n}`, size: "", net_wt: "",
                                   gross_wt: "", items: [] });
    },
    removeBox(i) {
      const boxes = this.ed.payload.boxes;
      const gone = boxes.splice(i, 1)[0];
      // never lose a line: whatever was in it moves to the first remaining box
      if (gone && (gone.items || []).length && boxes.length) {
        boxes[0].items.push(...gone.items);
      }
      this.boxTotals();
    },
    addBoxItem(box) {
      box.items.push({ sno: (box.items || []).length + 1, code_desc: "", qty: "" });
    },
    removeBoxItem(box, i) { box.items.splice(i, 1); this.boxTotals(); },
    moveItemToBox(fromBox, i, toIndex) {
      const target = this.ed.payload.boxes[Number(toIndex)];
      if (!target || target === fromBox) return;
      target.items.push(fromBox.items.splice(i, 1)[0]);
      this.boxTotals();
    },
    boxIndex(box) { return (this.ed.payload.boxes || []).indexOf(box); },
    boxTotals() {
      const p = this.ed.payload;
      const num = (v) => { const n = Number(v); return isFinite(n) ? n : 0; };
      if (!this.manual.tqty) {
        p.totals.qty = (p.boxes || [])
          .reduce((a, b) => a + (b.items || []).reduce((x, it) => x + num(it.qty), 0), 0);
      }
      if (!this.manual.twt) {
        const net = (p.boxes || []).reduce((a, b) => a + num(b.net_wt), 0);
        p.totals.net_wt = net ? net.toFixed(3) : "";
      }
      if (!this.manual.tgross) {
        const gross = (p.boxes || []).reduce((a, b) => a + num(b.gross_wt), 0);
        p.totals.gross_wt = gross ? gross.toFixed(3) : "";
      }
    },
    // ---- invoice buyer PO rows --------------------------------------------- //
    addPo() { this.ed.payload.buyer_po_block.push({ po: "", date_iso: "" }); },
    removePo(i) { this.ed.payload.buyer_po_block.splice(i, 1); },
    // ---- test certificate extra elements ----------------------------------- //
    extraHead(n) { return (this.ed.payload.extra_elements || [])[n - 1] || ""; },
    setExtraHead(n, v) {
      const list = this.ed.payload.extra_elements || (this.ed.payload.extra_elements = []);
      while (list.length < n) list.push("");
      list[n - 1] = v;
      while (list.length && !list[list.length - 1]) list.pop();
    },
    chemOf(it, el) { return (it.chem || {})[el] ?? ""; },
    setChem(it, el, v) {
      if (!it.chem) it.chem = {};
      if (String(v).trim() === "") delete it.chem[el];
      else it.chem[el] = this._numify(v);
    },

    // ---- revision rail ------------------------------------------------------ //
    get revRail() { return (this.ed && this.ed.revisions) || []; },
    get hasRail() { return this.revRail.length > 1; },

    // ---- create ------------------------------------------------------------- //
    kindRef(kind) { return this.refs.kinds.find((k) => k.kind === kind) || null; },
    needs(kind, opt) {
      const r = this.kindRef(kind);
      if (!r) return false;
      return r.requires.includes(opt) || r.accepts.includes(opt);
    },
    async startCreate(kind = "", orderId = null) {
      this.form = {
        kind, order_id: "", order: null, orderQ: "",
        document_id: "", quotation_paper_id: "", repeat_po: false,
        invoice_paper_id: "", extra_orders: [], busy: false, error: "",
      };
      this.ed = null;
      this.view = "create";
      window.scrollTo(0, 0);
      if (orderId) await this.pickOrder(orderId);
    },
    async findOrder() {
      const q = (this.form.orderQ || "").trim();
      if (!q) return;
      try {
        const d = await api("/api/orders?q=" + encodeURIComponent(q));
        if (!d.rows.length) { this.form.error = "No order matches that"; return; }
        this.form.error = "";
        this.form.hits = d.rows.slice(0, 8);
      } catch (e) { this.fail(e); }
    },
    async pickOrder(id) {
      try {
        const o = await api(`/api/orders/${id}`);
        this.form.order = o;
        this.form.order_id = o.id;
        this.form.hits = null;
        this.form.orderQ = "";
        this.form.error = "";
        // what this order already has to point at
        this.form.quotationPapers = (o.papers || [])
          .filter((p) => p.kind === "quotation" && p.status !== "void");
        this.form.invoicePapers = (o.papers || [])
          .filter((p) => p.kind === "invoice" && p.status !== "void");
        this.form.quotationDocs = (o.documents || []).filter((d) => d.kind === "quotation");
        this.form.invoiceDocs = (o.documents || []).filter((d) => d.kind === "invoice");
        if (this.form.kind === "quotation" && this.form.quotationDocs.length === 1) {
          this.form.document_id = String(this.form.quotationDocs[0].id);
        }
        if (["packing_list", "coc", "test_cert"].includes(this.form.kind)
            && this.form.invoicePapers.length === 1) {
          this.form.invoice_paper_id = String(this.form.invoicePapers[0].id);
        }
      } catch (e) { this.fail(e); }
    },
    clearOrder() {
      Object.assign(this.form, { order: null, order_id: "", document_id: "",
        quotation_paper_id: "", invoice_paper_id: "", extra_orders: [] });
    },
    // An invoice can bill several orders of the SAME customer (CONVENTIONS §7).
    async addExtraOrder() {
      const q = (this.form.extraQ || "").trim();
      if (!q) return;
      try {
        const d = await api("/api/orders?q=" + encodeURIComponent(q));
        const hit = d.rows[0];
        if (!hit) return this.flash("No order matches that number", "err");
        if (hit.id === this.form.order.id
            || this.form.extra_orders.some((o) => o.id === hit.id)) {
          return this.flash("That order is already on this invoice", "err");
        }
        if (hit.customer_name !== this.form.order.customer_name) {
          return this.flash("One invoice bills one customer", "err");
        }
        this.form.extra_orders.push(hit);
        this.form.extraQ = "";
      } catch (e) { this.fail(e); }
    },
    removeExtraOrder(i) { this.form.extra_orders.splice(i, 1); },

    get createReady() {
      const f = this.form;
      if (!f || !f.kind || !f.order_id) return false;
      const r = this.kindRef(f.kind);
      if (!r) return false;
      if (r.requires.includes("document_id") && !f.document_id) return false;
      if (r.requires.includes("invoice_paper_id") && !f.invoice_paper_id) return false;
      return true;
    },
    async createPaper() {
      const f = this.form;
      f.error = "";
      f.busy = true;
      const opts = {};
      if (f.document_id && this.needs(f.kind, "document_id")) {
        opts.document_id = Number(f.document_id);
      }
      if (f.quotation_paper_id) opts.quotation_paper_id = Number(f.quotation_paper_id);
      if (f.repeat_po) opts.repeat_po = true;
      if (f.invoice_paper_id) opts.invoice_paper_id = Number(f.invoice_paper_id);
      if (f.kind === "invoice" && f.extra_orders.length) {
        opts.order_ids = [Number(f.order_id), ...f.extra_orders.map((o) => o.id)];
      }
      try {
        const made = await api("/api/papers", { method: "POST",
          body: { kind: f.kind, order_id: Number(f.order_id), opts } });
        this.form = null;
        await this.load();
        await this.openPaper(made.id);
        this.flash(`${made.label} ${made.display_no} created`);
      } catch (e) { f.error = e.message; } finally { f.busy = false; }
    },
  };
}
