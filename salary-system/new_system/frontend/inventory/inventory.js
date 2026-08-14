// APEX THERMOCON — Raw Material Inventory (separate window, admin only).
// Same zero-build stack as the main app: Alpine.js + Tailwind, vendored.

async function api(path, { method = "GET", body, form } = {}) {
  const opts = { method, headers: { "X-Requested-With": "apex-payroll" } };
  if (form) {
    opts.body = form; // browser sets the multipart boundary itself
  } else if (body !== undefined) {
    opts.headers["Content-Type"] = "application/json";
    opts.body = JSON.stringify(body);
  }
  const res = await fetch(path, opts);
  if (res.status === 401 || res.status === 403) {
    window.dispatchEvent(new CustomEvent("unauth"));
    throw new Error("Sign in as admin in the main APEX Payroll window first");
  }
  if (!res.ok) {
    let detail = res.statusText;
    try { detail = (await res.json()).detail || detail; } catch (_) {}
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

function blankHeat() {
  return {
    id: null, heat_number: "", date_received: today(), supplier: "",
    material_class: "", grade: "", shape: "", size_section: "",
    rods_received: "", total_weight_kg: "", rack: "", price_total: "",
    price_rate_per_kg: "", notes: "",
    composition: [{ element: "", percent: "" }],
  };
}

function inv() {
  return {
    booted: false,
    authed: false,
    user: null,
    tab: "stock",
    toast: { show: false, msg: "", kind: "ok" },

    flash(msg, kind = "ok") {
      this.toast = { show: true, msg, kind };
      setTimeout(() => (this.toast.show = false), 3200);
    },
    fail(e) { this.flash(e.message || String(e), "err"); },

    options: { material_class: [], shape: [], grade: [], element: [] },
    stats: null,
    rows: [],
    loading: false,
    _heatsSeq: 0,
    _logSeq: 0,
    f: { q: "", material_class: "", shape: "", status: "", element: "",
         pct_min: "", pct_max: "", sort: "newest" },

    detail: null,        // open heat (full record)
    form: null,          // create/edit model
    formError: "",
    // Initialised (not null) so bindings are always safe to evaluate.
    mv: { type: "issue", mv_date: "", order_id: "", rods: "", weight_kg: "", remarks: "" },
    log: { q: "", rows: [] },
    upload: { kind: "certificate", busy: false },
    newOpt: { material_class: "", shape: "", grade: "", element: "" },

    async boot() {
      window.addEventListener("unauth", () => { this.authed = false; });
      try {
        this.user = await api("/api/me");
        // Grant-based since the shell landed: any account the owner granted
        // 'inventory' may use this page (admins always can).
        const mods = await api("/api/modules");
        this.authed = !!(mods.modules || []).find((m) => m.key === "inventory" && m.granted);
      } catch (_) { this.authed = false; }
      if (!this.authed) { window.location.href = "/"; return; }  // back to launcher
      try {
        await this.loadOptions();
        await this.loadHeats();
      } catch (e) { this.fail(e); }
      this.booted = true;
    },

    // ---- formatting ------------------------------------------------------ //
    money(n) {
      if (n === null || n === undefined || n === "") return "—";
      return "₹" + Math.round(Number(n)).toLocaleString("en-IN");
    },
    num(n) { return n === null || n === undefined ? "—" : Number(n).toLocaleString("en-IN"); },
    fmtDate(d) {
      if (!d) return "—";
      const [y, m, dd] = d.split("-").map(Number);
      return new Date(y, m - 1, dd).toLocaleDateString("en-IN",
        { day: "numeric", month: "short", year: "numeric" });
    },
    statusLabel(s) {
      return { in_stock: "In stock", consumed: "Consumed", rejected: "Rejected" }[s] || s;
    },
    statusClass(s) {
      return {
        in_stock: "bg-emerald-100 text-emerald-700",
        consumed: "bg-slate-200 text-slate-600",
        rejected: "bg-rose-100 text-rose-700",
      }[s] || "bg-slate-100";
    },
    kindLabel(k) { return k === "certificate" ? "Spectroscopy / mill certificates" : "Purchase receipts / invoices"; },
    sizeLabel(b) {
      if (!b && b !== 0) return "";
      return b > 1024 * 1024 ? (b / 1048576).toFixed(1) + " MB" : Math.max(1, Math.round(b / 1024)) + " KB";
    },

    // ---- stock list ------------------------------------------------------ //
    async loadOptions() { this.options = await api("/api/inventory/options"); },
    async loadHeats() {
      // Sequence token: debounced filter edits can put two requests in flight;
      // only the LATEST one may write the results.
      const seq = ++this._heatsSeq;
      this.loading = true;
      try {
        const p = new URLSearchParams();
        for (const [k, v] of Object.entries(this.f)) if (v !== "" && v != null) p.set(k, v);
        const d = await api("/api/inventory/heats?" + p.toString());
        if (seq !== this._heatsSeq) return;
        this.rows = d.rows;
        this.stats = d.stats;
      } catch (e) { if (seq === this._heatsSeq) this.fail(e); }
      finally { if (seq === this._heatsSeq) this.loading = false; }
    },
    clearFilters() {
      this.f = { q: "", material_class: "", shape: "", status: "", element: "",
                 pct_min: "", pct_max: "", sort: "newest" };
      this.loadHeats();
    },

    // ---- heat detail ----------------------------------------------------- //
    async openHeat(id) {
      try {
        this.detail = await api(`/api/inventory/heats/${id}`);
        this.resetMv();
      } catch (e) { this.fail(e); }
    },
    closeDetail() { this.detail = null; this.loadHeats(); },
    resetMv() {
      this.mv = { type: "issue", mv_date: today(), order_id: "", rods: "",
                  weight_kg: "", remarks: "" };
    },

    // ---- create / edit form ---------------------------------------------- //
    newHeat() { this.form = blankHeat(); this.formError = ""; },
    editHeat() {
      const d = this.detail;
      this.form = {
        id: d.id, heat_number: d.heat_number, date_received: d.date_received,
        supplier: d.supplier || "", material_class: d.material_class || "",
        grade: d.grade || "", shape: d.shape || "", size_section: d.size_section || "",
        rods_received: d.rods_received, total_weight_kg: d.total_weight_kg ?? "",
        rack: d.rack || "", price_total: d.price_total ?? "",
        price_rate_per_kg: d.price_rate_per_kg ?? "", notes: d.notes || "",
        composition: d.composition.length
          ? d.composition.map((c) => ({ element: c.element, percent: c.percent }))
          : [{ element: "", percent: "" }],
      };
      this.formError = "";
    },
    addComp() { this.form.composition.push({ element: "", percent: "" }); },
    removeComp(i) { this.form.composition.splice(i, 1); },
    // "+ Add new…" sentinel in any dropdown: prompt, save, select it.
    async inlineAdd(kind, obj, field) {
      const labels = { material_class: "material class", shape: "rod shape",
                       grade: "grade", element: "element symbol" };
      const v = (window.prompt(`Add a new ${labels[kind]}:`, "") || "").trim();
      if (obj[field] === "__add__") obj[field] = "";
      if (!v) return;
      try {
        await api("/api/inventory/options", { method: "POST", body: { kind, value: v } });
        await this.loadOptions();
        obj[field] = v;
      } catch (e) { this.fail(e); }
    },
    async saveHeat() {
      this.formError = "";
      const f = this.form;
      // A half-filled composition row would silently become 0% (Number('')
      // is 0) — refuse it instead of guessing.
      const comp = f.composition.filter(
        (c) => (c.element || "").trim() !== "" || String(c.percent).trim() !== "");
      if (comp.some((c) => (c.element || "").trim() === "" || String(c.percent).trim() === "")) {
        this.formError = "Each composition row needs both an element and a percentage (remove empty rows with ✕)";
        return;
      }
      const payload = {
        heat_number: f.heat_number, date_received: f.date_received,
        supplier: f.supplier, material_class: f.material_class, grade: f.grade,
        shape: f.shape, size_section: f.size_section,
        rods_received: Number(f.rods_received),
        total_weight_kg: f.total_weight_kg === "" ? null : Number(f.total_weight_kg),
        rack: f.rack,
        price_total: f.price_total === "" ? null : Number(f.price_total),
        price_rate_per_kg: f.price_rate_per_kg === "" ? null : Number(f.price_rate_per_kg),
        notes: f.notes,
        composition: comp.map((c) => ({ element: c.element, percent: Number(c.percent) })),
      };
      try {
        const saved = f.id
          ? await api(`/api/inventory/heats/${f.id}`, { method: "PUT", body: payload })
          : await api("/api/inventory/heats", { method: "POST", body: payload });
        this.form = null;
        if (this.detail) this.detail = saved;
        await this.loadOptions();   // form may have taught new dropdown values
        await this.loadHeats();
        this.flash(`Heat ${saved.heat_number} saved`);
      } catch (e) { this.formError = e.message; }
    },
    async deleteHeat() {
      if (!window.confirm(`Delete heat ${this.detail.heat_number}? This removes its composition and attachments.`)) return;
      try {
        await api(`/api/inventory/heats/${this.detail.id}`, { method: "DELETE" });
        this.flash("Heat deleted");
        this.closeDetail();
      } catch (e) { this.fail(e); }
    },

    // ---- usage log (per heat) -------------------------------------------- //
    async saveMovement() {
      try {
        const m = this.mv;
        this.detail = await api(`/api/inventory/heats/${this.detail.id}/movements`, {
          method: "POST",
          body: { type: m.type, mv_date: m.mv_date, order_id: m.order_id,
                  rods: Number(m.rods),
                  weight_kg: m.weight_kg === "" ? null : Number(m.weight_kg),
                  remarks: m.remarks },
        });
        this.resetMv();
        this.flash("Log entry added");
      } catch (e) { this.fail(e); }
    },
    async rejectRemaining() {
      const n = this.detail.remaining;
      if (!window.confirm(`Reject the remaining ${n} rod(s) of heat ${this.detail.heat_number} and mark it as returned to the supplier?`)) return;
      try {
        this.detail = await api(`/api/inventory/heats/${this.detail.id}/reject-remaining`, {
          method: "POST", body: { mv_date: today(), remarks: "" },
        });
        this.flash("Remaining batch rejected — delete the entry to undo");
      } catch (e) { this.fail(e); }
    },
    async deleteMovement(id) {
      if (!window.confirm("Delete this log entry? The rods go back into the remaining stock.")) return;
      try {
        this.detail = await api(`/api/inventory/movements/${id}`, { method: "DELETE" });
        this.flash("Log entry removed");
      } catch (e) { this.fail(e); }
    },

    // ---- global log ------------------------------------------------------ //
    async loadLog() {
      const seq = ++this._logSeq;
      try {
        const rows = await api("/api/inventory/movements?q=" + encodeURIComponent(this.log.q));
        if (seq === this._logSeq) this.log.rows = rows;
      } catch (e) { if (seq === this._logSeq) this.fail(e); }
    },
    async openFromLog(heatId) { this.tab = "stock"; await this.openHeat(heatId); },

    // ---- attachments ------------------------------------------------------ //
    async pickFiles(ev) {
      const files = Array.from(ev.target.files || []);
      if (!files.length) return;
      this.upload.busy = true;
      try {
        const fd = new FormData();
        fd.append("kind", this.upload.kind);
        // No explicit filename arg: the File from compressIfImage already
        // carries the right name (renamed to .jpg when it was compressed).
        for (const f of files) fd.append("files", await this.compressIfImage(f));
        const res = await api(`/api/inventory/heats/${this.detail.id}/attachments`,
                              { method: "POST", form: fd });
        this.detail = res.heat;
        this.flash(`${res.saved.length} file(s) attached`);
      } catch (e) { this.fail(e); } finally {
        this.upload.busy = false;
        ev.target.value = "";
      }
    },
    // Shrink big photos client-side (certificates shot on a phone): max 1600px,
    // JPEG q0.82. PDFs and small images pass through untouched.
    async compressIfImage(file) {
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
      } catch (_) { return file; }   // exotic format — upload the original
    },
    viewAtt(a) {
      const u = `/api/inventory/attachments/${a.id}`;
      // Packaged app: window.open is dead inside pywebview — use the shell
      // bridge; browsers get a normal tab (download as fallback).
      if (window.pywebview && window.pywebview.api && window.pywebview.api.open_path) {
        window.pywebview.api.open_path(u).catch(() => this.downloadAtt(a));
        return;
      }
      const w = window.open(u, "_blank");
      if (!w) this.downloadAtt(a);
    },
    downloadAtt(a) { window.location = `/api/inventory/attachments/${a.id}?download=1`; },
    async deleteAtt(a) {
      if (!window.confirm(`Delete "${a.filename}"?`)) return;
      try {
        this.detail = await api(`/api/inventory/attachments/${a.id}`, { method: "DELETE" });
        this.flash("Attachment deleted");
      } catch (e) { this.fail(e); }
    },

    // ---- settings (dropdown admin) ---------------------------------------- //
    async addOpt(kind) {
      const v = (this.newOpt[kind] || "").trim();
      if (!v) return;
      try {
        this.options = await api("/api/inventory/options", { method: "POST", body: { kind, value: v } });
        this.newOpt[kind] = "";
      } catch (e) { this.fail(e); }
    },
    async removeOpt(kind, value) {
      try {
        this.options = await api("/api/inventory/options/delete", { method: "POST", body: { kind, value } });
      } catch (e) { this.fail(e); }
    },
  };
}
