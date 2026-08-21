// APEX THERMOCON — Order Tracking (🗂 tile): 7 skippable stages, items by
// drawing, material traceability (heats issued against the order number) and
// consignments (GST fields; partial shipments; one truck, several orders).

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

function od() {
  return {
    booted: false,
    authed: false,
    user: null,
    tab: "orders",           // 'orders' | 'consignments'
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
    fmtDate(d) {
      if (!d) return "—";
      const [y, m, dd] = d.split("-").map(Number);
      return new Date(y, m - 1, dd).toLocaleDateString("en-IN",
        { day: "numeric", month: "short", year: "numeric" });
    },

    lists: { customers: [], drawings: [], units: [] },
    data: { rows: [], stage_counts: {}, stages: [] },
    q: "",
    stageFilter: "",
    detail: null,
    form: null,
    addPage: false,      // the order form fills the screen for add AND edit
    plan: null,          // delivery-plan editor for one order item
    bom: null,           // what the open order commits, rolled up by heat
    reqs: [],            // requisitions already issued against it
    reqBusy: false,
    planError: "",
    checkOn: false,      // optional material check, off by default
    chk: { method: "dimension", material_class: "", grade: "",
           required_qty: "", part_length: "", part_diameter: "", margin: "" },
    chkResult: null, chkBusy: false, chkRefs: { material_class: [], grade: [] },
    formError: "",
    cons: { q: "", rows: [] },
    consDetail: null,
    consForm: null,
    consError: "",
    consOrderQ: "",

    stageLabel(k) { return (this.data.stages.find((s) => s.key === k) || {}).label || k; },
    // Dot-chips: one shape everywhere. bg-50 / text-700 / ring-600-20, dot = 500.
    stageClass(k) {
      return {
        enquiry:    "bg-slate-100 text-slate-600 ring-1 ring-inset ring-slate-500/20",
        quote:      "bg-sky-50 text-sky-700 ring-1 ring-inset ring-sky-600/20",
        po:         "bg-indigo-50 text-indigo-700 ring-1 ring-inset ring-indigo-600/20",
        production: "bg-amber-50 text-amber-700 ring-1 ring-inset ring-amber-600/20",
        qc:         "bg-violet-50 text-violet-700 ring-1 ring-inset ring-violet-600/20",
        dispatch:   "bg-sky-50 text-sky-700 ring-1 ring-inset ring-sky-600/20",
        payment:    "bg-emerald-50 text-emerald-700 ring-1 ring-inset ring-emerald-600/20",
      }[k] || "bg-slate-100 text-slate-600 ring-1 ring-inset ring-slate-500/20";
    },
    // the chip's dot — page-prefixed so it can never collide with the other modules
    odStageDot(k) {
      return {
        enquiry: "bg-slate-400", quote: "bg-sky-500", po: "bg-indigo-500",
        production: "bg-amber-500", qc: "bg-violet-500", dispatch: "bg-sky-500",
        payment: "bg-emerald-500",
      }[k] || "bg-slate-400";
    },

    async boot() {
      window.addEventListener("unauth", () => { this.authed = false; });
      try {
        this.user = await api("/api/me");
        const mods = await api("/api/modules");
        this.authed = !!(mods.modules || []).find((m) => m.key === "orders" && m.granted);
      } catch (_) { this.authed = false; }
      if (!this.authed) { window.location.href = "/"; return; }
      try {
        this.lists = await api("/api/orders/refs");
        await this.load();
      } catch (e) { this.fail(e); }
      this.booted = true;
      // deep links: /orders/?open=6 lands on that order's record (optionally
      // ?seg=material for a segment); ?tab=shipments lands on a view — this is
      // what makes right-click → "open in new tab" give the same screen. The
      // params are then cleaned off so Back/reload behave.
      const qs = new URLSearchParams(window.location.search);
      const want = qs.get("open"), view = qs.get("tab"), seg = qs.get("seg");
      if (want || view) window.history.replaceState({}, "", window.location.pathname);
      if (["orders", "consignments", "shipments"].includes(view)) {
        this.tab = view;
        if (view === "consignments") this.loadCons();
      }
      if (want) {
        await this.open(Number(want));
        if (seg && this.orderSegs.some((s) => s.k === seg)) this.oseg = seg;
      }
    },
    _seq: 0,
    async load() {
      const seq = ++this._seq;   // stale debounced responses must not win
      try {
        const p = new URLSearchParams({ q: this.q, stage: this.stageFilter });
        const d = await api("/api/orders?" + p.toString());
        if (seq === this._seq) this.data = d;
      } catch (e) { if (seq === this._seq) this.fail(e); }
    },

    // ---- order detail ------------------------------------------------------ //
    async open(id) {
      this.bom = null;
      this.oseg = "items";
      try { this.detail = await api(`/api/orders/${id}`); }
      catch (e) { this.fail(e); return; }
      this.loadBom(id);   // not awaited: the record must not wait on the rollup
    },
    async loadBom(id) {
      // Needs the parts grant (it reads drawings' costings). An account without
      // it just doesn't see the section rather than seeing an error.
      try { this.bom = await api(`/api/orders/${id}/bom`); }
      catch (_) { this.bom = null; }
      try { this.reqs = (await api(`/api/orders/requisitions`))
              .rows.filter((r) => r.order_id === id); }
      catch (_) { this.reqs = []; }
    },
    async issueReq() {
      const id = this.detail.id;
      this.reqBusy = true;
      try {
        const doc = await api(`/api/orders/${id}/bom/issue`, {
          method: "POST", body: { issued_on: today(), notes: "" } });
        await this.loadBom(id);
        this.flash(`Requisition ${doc.doc_no} issued`);
        // straight to the printable sheet — that is the point of issuing one
        window.open(`/api/orders/requisitions/${doc.id}/print`, "_blank");
      } catch (e) { this.fail(e); } finally { this.reqBusy = false; }
    },
    closeDetail() { this.detail = null; this.bom = null; this.reqs = []; this.load(); },
    async setStage(stage) {
      if (stage === this.detail.stage) return;
      const note = window.prompt(`Moving to "${this.stageLabel(stage)}" — note (optional):`, "");
      if (note === null) return;
      try {
        this.detail = await api(`/api/orders/${this.detail.id}/stage`,
                                { method: "POST", body: { stage, note } });
        this.flash(`Stage: ${this.stageLabel(stage)}`);
      } catch (e) { this.fail(e); }
    },
    async removeOrder() {
      if (!window.confirm(`Delete order ${this.detail.order_no}? Only possible while nothing has shipped.`)) return;
      try {
        await api(`/api/orders/${this.detail.id}`, { method: "DELETE" });
        this.flash("Order deleted");
        this.closeDetail();
      } catch (e) { this.fail(e); }
    },

    // ---- order form -------------------------------------------------------- //
    blankItem() { return { id: null, drawing_id: "", description: "", qty: "", unit: "Nos", rate: "" }; },
    newOrder() {
      this.form = { id: null, customer_id: "", customer_po: "", stage: "enquiry",
                    order_date: today(), due_date: "", notes: "",
                    items: [this.blankItem()] };
      this.formError = "";
      this.addPage = true;
      this.checkOn = false; this.chkResult = null;
      this.loadCheckRefs();
    },
    closeForm() { this.form = null; this.addPage = false; this.formError = ""; },

    // ---- order record segments ---------------------------------------------- //
    oseg: "items",
    orderSegs: [
      { k: "items",     label: "Items & delivery plan" },
      { k: "material",  label: "Material" },
      { k: "shipments", label: "Shipments & history" },
      { k: "documents", label: "Documents" },
      { k: "intake",    label: "Intake" },
    ],
    segCount(k) {
      const d = this.detail;
      if (!d) return 0;
      if (k === "items") return d.items.length;
      if (k === "material") return (this.bom?.summary || []).length;
      if (k === "shipments") return d.consignments.length;
      if (k === "documents") return (d.documents || []).length + (d.papers || []).length;
      if (k === "intake") return (d.attachments || []).length;
      return 0;
    },

    // ---- the pipeline strip (SOP-DESIGN §6) --------------------------------- //
    // Which stage issues which paperwork. Enquiry and payment issue none — the
    // enquiry has nothing to send yet and payment is the ledger's business.
    stagePaperKinds: {
      quote: ["quotation"],
      po: ["ack"],
      production: ["work_order", "bom"],
      qc: ["coc", "test_cert"],
      dispatch: ["invoice", "packing_list"],
    },
    stageKinds(key) { return this.stagePaperKinds[key] || []; },
    // A voided paper is not the stage's paper any more, but it stays in the
    // register — it just no longer counts as "this stage is covered".
    stagePapers(key) {
      const kinds = this.stageKinds(key);
      return (this.detail?.papers || [])
        .filter((p) => kinds.includes(p.kind) && p.status !== "void")
        .slice().reverse();
    },
    stageMissing(key) {
      const have = new Set(this.stagePapers(key).map((p) => p.kind));
      return this.stageKinds(key).filter((k) => !have.has(k));
    },
    odPaperShort(kind) {
      return { quotation: "Quote", ack: "Ack", work_order: "WO", bom: "BOM",
               invoice: "Invoice", packing_list: "Packing", coc: "COC",
               test_cert: "TC" }[kind] || kind;
    },
    odPaperName(kind) {
      return { quotation: "quotation", ack: "acknowledgement", work_order: "work order",
               bom: "bill of materials", invoice: "export invoice",
               packing_list: "packing list", coc: "certificate of conformance",
               test_cert: "test certificate" }[kind] || kind;
    },
    odPaperClass(st) {
      return {
        draft:      "bg-slate-100 text-slate-600 ring-1 ring-inset ring-slate-500/20",
        final:      "bg-emerald-50 text-emerald-700 ring-1 ring-inset ring-emerald-600/20",
        sent:       "bg-brand-50 text-brand-700 ring-1 ring-inset ring-brand-600/20",
        superseded: "bg-amber-50 text-amber-700 ring-1 ring-inset ring-amber-600/20",
        void:       "bg-rose-50 text-rose-700 ring-1 ring-inset ring-rose-600/20",
      }[st] || "bg-slate-100 text-slate-600 ring-1 ring-inset ring-slate-500/20";
    },
    odPaperDot(st) {
      return { draft: "bg-slate-400", final: "bg-emerald-500", sent: "bg-brand-600",
               superseded: "bg-amber-500", void: "bg-rose-500" }[st] || "bg-slate-400";
    },

    // ---- intake: what the customer sent us ---------------------------------- //
    att: { label: "", files: null, busy: false },
    async uploadAttachments() {
      const input = document.getElementById("od-att-files");
      const files = input && input.files;
      if (!files || !files.length) return this.flash("Choose at least one file", "err");
      const fd = new FormData();
      fd.append("label", this.att.label || "");
      for (const f of files) fd.append("files", f);
      this.att.busy = true;
      try {
        const res = await fetch(`/api/orders/${this.detail.id}/attachments`, {
          method: "POST", headers: { "X-Requested-With": "apex-payroll" }, body: fd });
        if (!res.ok) {
          let detail = res.statusText;
          try { detail = (await res.json()).detail || detail; } catch (_) {}
          throw new Error(detail);
        }
        this.att.label = "";
        input.value = "";
        this.detail = await api(`/api/orders/${this.detail.id}`);
        this.flash("Paperwork attached");
      } catch (e) { this.fail(e); } finally { this.att.busy = false; }
    },
    async removeAttachment(a) {
      if (!window.confirm(`Delete ${a.filename}?`)) return;
      try {
        await api(`/api/orders/attachments/${a.id}`, { method: "DELETE" });
        this.detail = await api(`/api/orders/${this.detail.id}`);
        this.flash("Attachment deleted");
      } catch (e) { this.fail(e); }
    },
    fileSize(b) {
      if (!b) return "";
      return b > 1048576 ? (b / 1048576).toFixed(1) + " MB" : Math.round(b / 1024) + " KB";
    },
    itemLabel(it) {
      return it.drawing_no
        ? it.drawing_no + (it.revision ? ` rev ${it.revision}` : "")
        : (it.description || "item");
    },
    get drawingRows() {
      return (this.detail?.items || []).filter((i) => (i.drawing_files || []).length);
    },
    // quotation/invoice chips on the order record — same colours Quotations uses
    odDocClass(kind) {
      return kind === "invoice"
        ? "bg-indigo-50 text-indigo-700 ring-1 ring-inset ring-indigo-600/20"
        : "bg-sky-50 text-sky-700 ring-1 ring-inset ring-sky-600/20";
    },
    odDocDot(kind) { return kind === "invoice" ? "bg-indigo-500" : "bg-sky-500"; },
    odDocStatus(st) {
      return {
        sent: "bg-amber-50 text-amber-700 ring-1 ring-inset ring-amber-600/20",
        accepted: "bg-emerald-50 text-emerald-700 ring-1 ring-inset ring-emerald-600/20",
        paid: "bg-emerald-50 text-emerald-700 ring-1 ring-inset ring-emerald-600/20",
        rejected: "bg-rose-50 text-rose-700 ring-1 ring-inset ring-rose-600/20",
        cancelled: "bg-rose-50 text-rose-700 ring-1 ring-inset ring-rose-600/20",
      }[st] || "bg-slate-100 text-slate-600 ring-1 ring-inset ring-slate-500/20";
    },
    odDocStatusDot(st) {
      return { sent: "bg-amber-500", accepted: "bg-emerald-500", paid: "bg-emerald-500",
               rejected: "bg-rose-500", cancelled: "bg-rose-500" }[st] || "bg-slate-400";
    },

    // ---- deliveries ---------------------------------------------------------- //
    // One row per delivery segment, flattened across items. The backend already
    // splits each item into its planned drops plus any quantity with no date
    // yet, so an item with no plan arrives as a single segment and every order
    // has something to ship against.
    shipRows(item) {
      const out = [];
      for (const it of (this.detail?.items || [])) {
        if (item && it.id !== item.id) continue;
        const part = it.drawing_no
          ? it.drawing_no + (it.revision ? ` rev ${it.revision}` : "")
          : (it.description || "item");
        const drops = it.segments.filter((s) => s.planned);
        it.segments.forEach((s, n) => {
          const nth = s.planned ? drops.indexOf(s) + 1 : 0;
          out.push({
            key: s.id ? `s${s.id}` : `i${it.id}`, item: it, drop: s,
            title: s.planned ? `${part} · drop ${nth} of ${drops.length}` : part,
            subtitle: s.planned
              ? [s.note, "by " + this.fmtDate(s.due_date)].filter(Boolean).join(" · ")
              : (drops.length ? "no delivery date promised yet" : "no delivery plan"),
            label: s.planned ? `Drop ${nth} of ${drops.length}` : "Not yet scheduled",
            qty: s.qty, delivered: s.delivered, remaining: s.remaining,
            pct: s.pct, done: s.done,
          });
        });
      }
      return out;
    },
    overDelivered() {
      return (this.detail?.items || [])
        .reduce((n, i) => n + (i.over_delivered || 0), 0) || 0;
    },
    // Ship exactly this drop: the form opens with that part chosen and the
    // drop's outstanding quantity filled in.
    async shipDrop(row) {
      await this.newCons(this.detail);
      const it = (this.consPick.items || []).find((x) => x.id === row.item.id);
      if (!it) return this.flash("That part has nothing left to send", "err");
      this.consPick.item_id = String(it.id);
      this.consPick.qty = Math.min(row.remaining, it.pending);
    },

    // position of a stage in the pipeline — drives the chain visual
    stageIndex(key) {
      return (this.detail?.stages || []).findIndex((s) => s.key === key);
    },

    // ---- shipments view ---------------------------------------------------- //
    openOnly: true,
    expanded: {},
    toggleRow(id) { this.expanded[id] = !this.expanded[id]; },
    // Each delivery gets its own share of the strip, proportional to the
    // quantity it covers: a 250-piece drop draws wider than a 150-piece one, so
    // the segments together still read as the whole order.
    segWidth(o, d) { return o.qty_total ? (d.qty / o.qty_total * 100) : 0; },
    // Complete is green; a promised drop is solid; quantity with no promised
    // date is the same colour softened, so it still reads as progress but never
    // looks like a commitment that was made.
    segFill(d) {
      return d.done ? "bg-emerald-500" : (d.planned ? "bg-brand-600" : "bg-brand-600/60");
    },
    // An order with two parts and no plan also draws two bars, but calling
    // those "deliveries" would report a promise nobody made — so the caption
    // counts planned drops, and says so plainly when there is no plan.
    dropNote(o) {
      const planned = o.drops.filter((d) => d.planned).length;
      if (planned) return ` · ${planned} planned deliver${planned > 1 ? "ies" : "y"}`;
      return o.drops.length > 1 ? ` · ${o.drops.length} parts, no plan yet` : " · no plan yet";
    },
    shipmentRows() {
      const rows = this.data?.rows || [];
      return this.openOnly ? rows.filter((o) => o.qty_pending > 0) : rows;
    },
    totalOutstanding() {
      const n = this.shipmentRows().reduce((a, o) => a + (Number(o.qty_pending) || 0), 0);
      return Math.round(n * 1000) / 1000;
    },

    // ---- deadline colouring ----------------------------------------------- //
    daysTo(d) {
      if (!d) return null;
      const due = new Date(d + "T00:00:00");
      if (isNaN(due)) return null;
      const now = new Date(); now.setHours(0, 0, 0, 0);
      return Math.round((due - now) / 86400000);
    },
    // Urgency stays tinted, but as a dot-chip: overdue rose, within a week amber,
    // anything further out is plain text so the urgent rows carry the eye.
    dueClass(d) {
      const n = this.daysTo(d);
      if (n === null) return "text-slate-300";
      if (n < 0) return "bg-rose-50 text-rose-700 ring-1 ring-inset ring-rose-600/20";
      if (n <= 7) return "bg-amber-50 text-amber-700 ring-1 ring-inset ring-amber-600/20";
      return "text-slate-600";
    },
    odDueDot(d) {
      const n = this.daysTo(d);
      if (n === null) return "";
      if (n < 0) return "bg-rose-500";
      if (n <= 7) return "bg-amber-500";
      return "";
    },
    dueChipClass(d) {
      const n = this.daysTo(d);
      if (n === null) return "bg-slate-100 text-slate-600 ring-1 ring-inset ring-slate-500/20";
      if (n < 0) return "bg-rose-50 text-rose-700 ring-1 ring-inset ring-rose-600/20";
      if (n <= 7) return "bg-amber-50 text-amber-700 ring-1 ring-inset ring-amber-600/20";
      return "bg-slate-100 text-slate-600 ring-1 ring-inset ring-slate-500/20";
    },
    dueLabel(d) {
      const n = this.daysTo(d);
      if (n === null) return "";
      if (n < 0) return `${-n} ${-n === 1 ? "day" : "days"} overdue`;
      if (n === 0) return "due today";
      return `due in ${n} ${n === 1 ? "day" : "days"}`;
    },

    // ---- delivery plan ----------------------------------------------------- //
    openPlan(it) {
      this.planError = "";
      this.plan = {
        item_id: it.id,
        qty: it.qty,
        due_date: this.detail?.due_date || "",
        label: (it.drawing_no ? `${it.drawing_no} rev ${it.revision}` : it.description)
               + ` · ${it.qty} ${it.unit || ""}`.trimEnd(),
        lines: (it.schedule || []).map((s) => ({
          due_date: s.due_date, qty: s.qty, note: s.note || "" })),
      };
    },
    planTotal() {
      const n = (this.plan?.lines || []).reduce((a, l) => a + (Number(l.qty) || 0), 0);
      return Math.round(n * 1000) / 1000;
    },
    planLeft() {
      return Math.round(((this.plan?.qty || 0) - this.planTotal()) * 1000) / 1000;
    },
    addPlanLine() {
      this.plan.lines.push({ due_date: this.plan.due_date || "", qty: "", note: "" });
    },
    planRest() {
      // the "and the rest before the deadline" line, filled in for you
      this.plan.lines.push({ due_date: this.plan.due_date || "",
                             qty: this.planLeft(), note: "balance" });
    },
    async savePlan() {
      this.planError = "";
      const lines = this.plan.lines.filter(
        (l) => String(l.due_date).trim() !== "" || String(l.qty).trim() !== "");
      for (const [i, l] of lines.entries()) {
        if (!String(l.due_date).trim()) { this.planError = `Line ${i + 1}: pick a date`; return; }
        if (!(Number(l.qty) > 0)) { this.planError = `Line ${i + 1}: quantity must be more than 0`; return; }
      }
      try {
        await api(`/api/orders/items/${this.plan.item_id}/schedule`, {
          method: "PUT",
          body: { lines: lines.map((l) => ({
            due_date: l.due_date, qty: Number(l.qty), note: l.note || "" })) },
        });
        const id = this.detail.id;
        this.plan = null;
        this.detail = await api(`/api/orders/${id}`);   // refresh planned/unplanned
        this.loadBom(id);
        this.flash("Delivery plan saved");
      } catch (e) { this.planError = e.message; }
    },
    async loadCheckRefs() {
      if (this.chkRefs.material_class.length) return;
      try { this.chkRefs = await api("/api/material/refs"); } catch (_) { /* optional */ }
    },
    toggleCheck() {
      this.checkOn = !this.checkOn;
      if (!this.checkOn) return;
      this.loadCheckRefs();
      if (!this.chk.required_qty) {
        const qty = (this.form?.items || []).reduce((n, i) => n + (Number(i.qty) || 0), 0);
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
      return { available: "bg-emerald-50 text-emerald-700 ring-1 ring-inset ring-emerald-600/20",
               partial: "bg-amber-50 text-amber-700 ring-1 ring-inset ring-amber-600/20",
               none: "bg-rose-50 text-rose-700 ring-1 ring-inset ring-rose-600/20" }[s]
             || "bg-slate-100 text-slate-600 ring-1 ring-inset ring-slate-500/20";
    },
    odAvailDot(s) {
      return { available: "bg-emerald-500", partial: "bg-amber-500",
               none: "bg-rose-500" }[s] || "bg-slate-400";
    },
    dim(v) {
      if (v === null || v === undefined || v === "") return "—";
      return String(Math.round(Number(v) * 10000) / 10000);
    },
    _ensureRef(list, id, label) {
      if (!id) return;
      if (!list.find((x) => x.id === Number(id))) list.push({ id: Number(id), ...label });
    },
    editOrder() {
      const d = this.detail;
      // a deactivated customer/drawing must still show in the edit dropdowns
      this._ensureRef(this.lists.customers, d.customer_id,
                      { name: d.customer_name + " (inactive)" });
      for (const i of d.items) {
        if (i.drawing_id) this._ensureRef(this.lists.drawings, i.drawing_id,
          { drawing_no: (i.drawing_no || "?") + " (inactive)", revision: i.revision || "",
            description: i.description, unit: i.unit, latest_rate: i.rate });
      }
      this.form = {
        id: d.id, customer_id: d.customer_id, customer_po: d.customer_po || "",
        stage: d.stage, order_date: d.order_date, due_date: d.due_date || "",
        notes: d.notes || "",
        items: d.items.map((i) => ({ id: i.id, drawing_id: i.drawing_id || "",
                                     description: i.description || "", qty: i.qty,
                                     unit: i.unit, rate: i.rate })),
      };
      this.formError = "";
      this.addPage = true;    // edit gets the same full window as add
      this.checkOn = false; this.chkResult = null;
    },
    addItem() { this.form.items.push(this.blankItem()); },
    removeItem(i) { this.form.items.splice(i, 1); },
    drawingPicked(item) {
      // picking a drawing prefills description/unit and its latest rate;
      // SWITCHING drawings replaces the prefill (no stale rate rides along)
      const d = this.lists.drawings.find((x) => x.id === Number(item.drawing_id));
      if (!d) return;
      item.description = d.description || d.drawing_no;
      item.unit = d.unit || item.unit;
      item.rate = d.latest_rate || "";
    },
    drawingLabel(id) {
      const d = this.lists.drawings.find((x) => x.id === Number(id));
      return d ? `${d.drawing_no} rev ${d.revision}` : "";
    },
    itemAmount(it) { return Math.round(((Number(it.qty) || 0) * (Number(it.rate) || 0)) * 100) / 100; },
    get formTotal() {
      return (this.form?.items || []).reduce((s, it) => s + this.itemAmount(it), 0);
    },
    async saveOrder() {
      this.formError = "";
      const f = this.form;
      const payload = {
        customer_id: Number(f.customer_id) || 0,
        customer_po: f.customer_po, stage: f.stage, order_date: f.order_date,
        due_date: f.due_date, notes: f.notes,
        items: f.items
          .filter((i) => i.drawing_id || (i.description || "").trim() || i.qty
                         || String(i.rate ?? "").trim())
          .map((i) => ({ id: i.id || null,
                         drawing_id: i.drawing_id ? Number(i.drawing_id) : null,
                         description: i.description, qty: Number(i.qty),
                         unit: i.unit || "Nos", rate: Number(i.rate) || 0 })),
      };
      try {
        const saved = f.id
          ? await api(`/api/orders/${f.id}`, { method: "PUT", body: payload })
          : await api("/api/orders", { method: "POST", body: payload });
        this.form = null; this.addPage = false;
        this.detail = saved;
        await this.load();
        this.flash(`Order ${saved.order_no} saved`);
      } catch (e) { this.formError = e.message; }
    },

    // ---- consignments ------------------------------------------------------ //
    _consSeq: 0,
    async loadCons() {
      const seq = ++this._consSeq;
      try {
        const rows = await api("/api/orders/consignments?q=" + encodeURIComponent(this.cons.q));
        if (seq === this._consSeq) this.cons.rows = rows;
      } catch (e) { if (seq === this._consSeq) this.fail(e); }
    },
    async openCons(id) {
      try { this.consDetail = await api(`/api/orders/consignments/${id}`); }
      catch (e) { this.fail(e); }
    },
    async toggleDelivered(cn) {
      try {
        const r = await api(`/api/orders/consignments/${cn.id}/delivered?delivered=${!cn.delivered}`,
                            { method: "POST" });
        if (this.consDetail && this.consDetail.id === cn.id) this.consDetail = r;
        await this.loadCons();
        if (this.detail) await this.open(this.detail.id);
      } catch (e) { this.fail(e); }
    },
    async removeCons(cn) {
      if (!window.confirm("Delete this consignment? The quantities go back to 'pending'.")) return;
      try {
        await api(`/api/orders/consignments/${cn.id}`, { method: "DELETE" });
        this.consDetail = null;
        await this.loadCons();
        if (this.detail) await this.open(this.detail.id);
        this.flash("Consignment deleted");
      } catch (e) { this.fail(e); }
    },

    // consignment form: lines start from one order's pending items; more
    // orders can be pulled in (one truck, several orders).
    async newCons(fromOrder = null) {
      this.consForm = { consign_date: today(), transporter: "", lr_no: "",
                        eway_no: "", invoice_no: "", vehicle_no: "", freight: "",
                        notes: "", lines: [] };
      this.consError = "";
      this.consOrderQ = "";
      this.consPick = { order_id: null, order_no: "", items: [], item_id: "", qty: "" };
      if (fromOrder) {
        // Load the order into the PICKER rather than pushing every pending item
        // onto the truck. A long order goes out in instalments, so "which part,
        // how many" is the question — not "delete the rows you didn't mean".
        try {
          const items = (await api(`/api/orders/${fromOrder.id}/open-items`))
            .filter((i) => i.pending > 0);
          if (items.length) {
            this.consPick = { order_id: fromOrder.id, order_no: fromOrder.order_no,
                              items, item_id: String(items[0].id), qty: items[0].pending };
          } else {
            this.flash(`${fromOrder.order_no} has nothing left to send`, "err");
          }
        } catch (e) { this.fail(e); }
      }
    },
    async pullOrderItems(orderId, orderNo) {
      try {
        const items = await api(`/api/orders/${orderId}/open-items`);
        let added = 0;
        for (const it of items) {
          if (it.pending <= 0) continue;
          if (this.consForm.lines.find((l) => l.order_item_id === it.id)) continue;
          this.consForm.lines.push({
            order_item_id: it.id, order_no: orderNo,
            label: it.drawing_no || it.description, unit: it.unit,
            pending: it.pending, qty: it.pending,
          });
          added++;
        }
        if (!added) this.flash(`${orderNo}: nothing pending to ship`, "err");
      } catch (e) { this.fail(e); }
    },
    // Pick ONE part of an order onto this truck. An order split across two
    // deliveries rarely sends everything at once, so pulling in every pending
    // item and expecting the user to zero the rest is backwards.
    consPick: { order_id: null, order_no: "", items: [], item_id: "", qty: "" },
    get consPickItem() {
      return (this.consPick.items || []).find(
        (i) => String(i.id) === String(this.consPick.item_id)) || null;
    },
    async findOrderForCons() {
      const q = this.consOrderQ.trim();
      if (!q) return;
      try {
        const d = await api("/api/orders?q=" + encodeURIComponent(q));
        const hit = d.rows[0];
        if (!hit) return this.flash("No order matches that number", "err");
        const items = await api(`/api/orders/${hit.id}/open-items`);
        const open = items.filter((i) => i.pending > 0);
        if (!open.length) return this.flash(`${hit.order_no} has nothing left to send`, "err");
        this.consPick = { order_id: hit.id, order_no: hit.order_no, items: open,
                          item_id: String(open[0].id), qty: open[0].pending };
        this.consOrderQ = "";
      } catch (e) { this.fail(e); }
    },
    pickedPart() {
      const it = this.consPickItem;
      if (it) this.consPick.qty = it.pending;   // default to all of that part
    },
    addPickedToCons() {
      const it = this.consPickItem;
      if (!it) return this.flash("Choose which part is going", "err");
      const qty = Number(this.consPick.qty);
      if (!(qty > 0)) return this.flash("Enter how many are going", "err");
      if (qty > it.pending) return this.flash(`Only ${it.pending} of that part are left to send`, "err");
      if (this.consForm.lines.some((l) => l.order_item_id === it.id))
        return this.flash("That part is already on this truck", "err");
      this.consForm.lines.push({
        order_item_id: it.id, order_no: this.consPick.order_no,
        label: it.label ?? (it.drawing_no
          ? it.drawing_no + (it.revision ? ` rev ${it.revision}` : "")
          : it.description),
        pending: it.pending, unit: it.unit, qty,
      });
      // move to the next part still to send, or clear
      const left = this.consPick.items.filter(
        (x) => !this.consForm.lines.some((l) => l.order_item_id === x.id));
      if (left.length) { this.consPick.item_id = String(left[0].id); this.pickedPart(); }
      else this.consPick = { order_id: null, order_no: "", items: [], item_id: "", qty: "" };
    },

    async addOrderToCons() {
      if (this.consPick.order_id) {          // an order is already on screen
        await this.pullOrderItems(this.consPick.order_id, this.consPick.order_no);
        this.consPick = { order_id: null, order_no: "", items: [], item_id: "", qty: "" };
        return;
      }
      const q = this.consOrderQ.trim();
      if (!q) return;
      try {   // search server-side so stage filters/search never hide an order
        const d = await api("/api/orders?q=" + encodeURIComponent(q));
        const hit = d.rows[0];
        if (!hit) return this.flash("No order matches that number", "err");
        await this.pullOrderItems(hit.id, hit.order_no);
        this.consOrderQ = "";
      } catch (e) { this.fail(e); }
    },
    removeConsLine(i) { this.consForm.lines.splice(i, 1); },
    async saveCons() {
      this.consError = "";
      const f = this.consForm;
      const payload = {
        ...f,
        freight: f.freight === "" ? null : Number(f.freight),
        lines: f.lines.filter((l) => Number(l.qty) > 0)
          .map((l) => ({ order_item_id: l.order_item_id, qty: Number(l.qty) })),
      };
      try {
        await api("/api/orders/consignments", { method: "POST", body: payload });
        this.consForm = null;
        await this.loadCons();
        if (this.detail) await this.open(this.detail.id);
        this.flash("Consignment created");
      } catch (e) { this.consError = e.message; }
    },
  };
}
