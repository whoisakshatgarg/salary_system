// APEX THERMOCON salary system — single-page app (Alpine.js, zero build).

async function api(path, { method = "GET", body } = {}) {
  // The custom header marks requests as coming from this app: a hostile web
  // page can't attach it cross-origin without a CORS preflight (which this
  // server never grants), so state-changing routes can require it.
  const opts = { method, headers: { "X-Requested-With": "apex-payroll" } };
  if (body !== undefined) {
    opts.headers["Content-Type"] = "application/json";
    opts.body = JSON.stringify(body);
  }
  const res = await fetch(path, opts);
  if (res.status === 401) {
    window.dispatchEvent(new CustomEvent("unauth"));
    throw new Error("Not logged in");
  }
  if (!res.ok) {
    let detail = res.statusText;
    try { detail = (await res.json()).detail || detail; } catch (_) {}
    throw new Error(detail);
  }
  const ct = res.headers.get("content-type") || "";
  return ct.includes("application/json") ? res.json() : res;
}

function currentMonth() {
  const d = new Date();
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}`;
}
function prevMonth() {
  const d = new Date();
  d.setDate(1);
  d.setMonth(d.getMonth() - 1);
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}`;
}
function today() {
  // LOCAL date — toISOString() is UTC and says "yesterday" before 05:30 IST.
  const d = new Date();
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
}

function app() {
  return {
    // ---- session / chrome ------------------------------------------------ //
    booted: false,
    user: null,
    edition: "admin",          // "admin" (full CEO app) | "operator" (kiosk app)
    meta: { company_name: "APEX THERMOCON", currency_symbol: "₹", departments: [] },
    view: "dashboard",
    login: { username: "", password: "" },
    loginError: "",
    toast: { show: false, msg: "", kind: "ok" },

    get isAdmin() { return this.user && this.user.role === "admin"; },
    get isOperatorEdition() { return this.edition === "operator"; },
    money(n) {
      if (n === null || n === undefined || n === "") return "";
      return this.meta.currency_symbol + Number(n).toLocaleString("en-IN");
    },
    flash(msg, kind = "ok") {
      this.toast = { show: true, msg, kind };
      setTimeout(() => (this.toast.show = false), 3200);
    },
    fail(e) { this.flash(e.message || String(e), "err"); },

    async boot() {
      window.addEventListener("unauth", () => { this.user = null; });
      try {
        const ed = await api("/api/edition");
        this.edition = ed.edition;
        this.version = ed.version;
      } catch (_) {}
      this.checkUpdates();   // deliberately not awaited — never delays startup
      try {
        // Operator app: sign in automatically (kiosk). CEO app: resume any session.
        this.user = this.isOperatorEdition
          ? await api("/api/kiosk-login", { method: "POST" })
          : await api("/api/me");
        await this.afterLogin();
      } catch (_) { /* show login / Start screen */ }
      this.booted = true;
    },
    async doLogin() {
      this.loginError = "";
      try {
        this.user = await api("/api/login", { method: "POST", body: this.login });
        this.login.password = "";
        await this.afterLogin();
      } catch (e) { this.loginError = e.message; }
    },
    // Operator app: manual retry if the automatic kiosk sign-in didn't take.
    async kioskSignIn() {
      this.loginError = "";
      try {
        this.user = await api("/api/kiosk-login", { method: "POST" });
        await this.afterLogin();
      } catch (e) { this.loginError = e.message; }
    },
    // Wipe all view/sub-view state so every login starts fresh (no leftover
    // page, selected employee, loaded grid, filters, etc. from a prior session).
    resetUiState() {
      this.view = "dashboard";
      this.attMode = "grid";
      this.att = { empId: null, period: prevMonth(), days: [], summary: null, emp: null, search: "", dept: "", fyStats: null, statsShown: false };
      this.grid = { period: prevMonth(), rows: [], days: [], dept: "", search: "", loading: false, calculated: false, opStatsShown: false };
      this.pay = { period: prevMonth(), rows: [], computed: false, publishing: false };
      this.adv = { empId: null, amount: 0, txn_date: today(), cheque: 0, cash: 0, ledger: null };
      this.history = { period: prevMonth(), rows: [] };
      this.exp = { period: prevMonth() };
      this.empForm = null;
      this.empSearch = "";
      this.profile = null;
      this.profileOpen = false;
      this.attendancePrompt = null;
      this.reminder = null;
      this.rulesText = "";
      this.rulesError = "";
      this.sync = { status: null, incoming: [], period: prevMonth(), busy: false };
      this.backup = { busy: false, recent: [] };
    },
    async afterLogin() {
      this.resetUiState();
      this.meta = await api("/api/meta");
      if (this.meta.edition) this.edition = this.meta.edition;
      await this.loadEmployees();
      await this.refreshSync();
      await this.postLoginSync();
      this.go(this.isAdmin ? "dashboard" : "attendance");
    },
    async logout() {
      await api("/api/logout", { method: "POST" });
      this.user = null;
      this.reminder = null;
      this.attendancePrompt = null;
      this.sync.incoming = [];
    },
    go(v) { this.view = v; if (this[`enter_${v}`]) this[`enter_${v}`](); },

    // ---- shared data ----------------------------------------------------- //
    employees: [],
    async loadEmployees() { this.employees = await api("/api/employees"); },
    empName(id) { const e = this.employees.find((x) => x.id === id); return e ? e.name : id; },

    // ---- dashboard ------------------------------------------------------- //
    openInventory() {
      // Packaged app (pywebview): window.open is dead / loses the session —
      // ask the native shell for a second window instead (see desktop.py).
      if (window.pywebview && window.pywebview.api && window.pywebview.api.open_inventory) {
        window.pywebview.api.open_inventory()
          .catch(() => { window.location.href = "/inventory.html?back=1"; });
        return;
      }
      // Browser: named window, so clicking again focuses the existing one.
      const w = window.open("/inventory.html", "apex_inventory");
      if (!w) window.location.href = "/inventory.html?back=1";  // popup blocked
    },
    dash: { total: 0, cnc: 0, ot: 0, advance: 0 },
    enter_dashboard() {
      this.dash.total = this.employees.length;
      this.dash.cnc = this.employees.filter((e) => e.dept === "CNC").length;
      this.dash.ot = this.employees.filter((e) => e.overtime_eligible).length;
      this.dash.advance = this.employees.reduce((s, e) => s + e.rem_advance, 0);
    },

    // ---- employees ------------------------------------------------------- //
    empForm: null,
    empSearch: "",
    get filteredEmployees() {
      const q = this.empSearch.toLowerCase();
      return this.employees.filter(
        (e) => !q || e.name.toLowerCase().includes(q) || e.dept.toLowerCase().includes(q)
        || String(e.id) === q
      );
    },
    newEmployee() {
      this.empForm = {
        id: null, name: "", dept: this.meta.departments[0] || "CNC",
        base_salary: 0, pf_applicable: false, esi_applicable: false,
        overtime_eligible: false, shift: "D", rem_advance: 0, leave_balance: 0,
        date_joined: "",
      };
    },
    editEmployee(e) { this.empForm = { ...e, date_joined: e.date_joined || "" }; },

    // ---- employee profile (CEO) ----------------------------------------- //
    profile: null,
    profileOpen: false,
    async openProfile(id) {
      try {
        this.profile = await api(`/api/employee-profile/${id}`);
        this.profileOpen = true;
      } catch (e) { this.fail(e); }
    },
    // chart scaling helpers (CSS bar charts — no external lib)
    maxField(arr, key) { return arr && arr.length ? Math.max(1, ...arr.map((x) => Number(x[key]) || 0)) : 1; },
    maxAdvance(arr) { return arr && arr.length ? Math.max(1, ...arr.map((x) => Math.max(x.issued, x.recovered))) : 1; },
    mLabel(period) { return period ? period.slice(2) : ""; },  // 2026-05 -> 26-05
    async saveEmployee() {
      try {
        const f = this.empForm;
        if (!f.name.trim()) return this.flash("Name is required", "err");
        if (f.id) await api(`/api/employees/${f.id}`, { method: "PUT", body: f });
        else await api("/api/employees", { method: "POST", body: f });
        this.empForm = null;
        await this.loadEmployees();
        this.flash("Employee saved");
      } catch (e) { this.fail(e); }
    },
    async toggleActive(e) {
      try {
        await api(`/api/employees/${e.id}/active?active=${!e.active}`, { method: "POST" });
        await this.loadEmployees();
      } catch (err) { this.fail(err); }
    },

    // ---- attendance ------------------------------------------------------ //
    attMode: "grid",   // 'grid' (all employees) | 'single' (one employee, calendar)
    att: { empId: null, period: prevMonth(), days: [], summary: null, emp: null, search: "", dept: "", fyStats: null, statsShown: false },
    // Indian financial year start (Apr–Mar) for a given YYYY-MM period.
    fyStartOf(period) { const [y, m] = period.split("-").map(Number); return m >= 4 ? y : y - 1; },
    async loadFyStats() {
      if (!this.att.empId) return;
      this.att.fyStats = await api(`/api/attendance-fy/${this.att.empId}?fy_start=${this.fyStartOf(this.att.period)}`);
    },
    enter_attendance() {
      if (!this.att.empId && this.employees[0]) this.att.empId = this.employees[0].id;
      if (this.attMode === "grid" && !this.grid.rows.length) this.loadGrid();
    },
    // Single-view employee picker, filtered by name/id + department.
    get singleEmployees() {
      const q = this.att.search.toLowerCase();
      return this.employees.filter(
        (e) => (!this.att.dept || e.dept === this.att.dept)
          && (!q || e.name.toLowerCase().includes(q) || String(e.id) === q)
      );
    },
    // Open one employee in the single-employee calendar (from the grid name link).
    async openSingle(empId) {
      this.attMode = "single";
      this.att.search = "";
      this.att.dept = "";
      this.att.empId = empId;
      this.att.period = this.grid.period;
      await this.loadAttendance();
    },
    // Single-employee editor laid out as a vertical calendar: each column is a
    // week, rows are Mon→Sun. Reading a column top→bottom then moving right is
    // chronological. Cells reference the same att.days objects (edits are live).
    calendarWeeks() {
      const days = this.att.days;
      if (!days.length) return [];
      const [y, m] = this.att.period.split("-").map(Number);
      const firstRow = (new Date(y, m - 1, 1).getDay() + 6) % 7; // Mon=0..Sun=6
      const weeks = [];
      let week = new Array(7).fill(null);
      let row = firstRow;
      for (const d of days) {
        week[row] = d;
        if (++row === 7) { weeks.push(week); week = new Array(7).fill(null); row = 0; }
      }
      if (row > 0) weeks.push(week);
      return weeks;
    },
    // CEO audit helpers (single-employee view) ----------------------------- //
    // This month's computed figures for the loaded employee (2×2 grid).
    get monthStats() {
      const s = this.att.summary;
      const holidays = this.att.days.filter(
        (d) => d.status === "A" && !this.isSunday(this.att.period, d.day)
      ).length;
      return {
        present: s ? s.present_days : "—",
        ot: s ? s.total_overtime_hours : "—",
        holidays,
        penalty: s ? s.penalty_days : "—",
      };
    },
    // Operator current-month aggregate (live from the loaded marks).
    get opMonthPresent() { return this.att.days.filter((d) => d.status === "P").length; },
    get opMonthOvertime() {
      return this.att.days.reduce(
        (s, d) => s + (d.status === "P" && d.overtime !== "" && d.overtime != null ? Number(d.overtime) : 0),
        0,
      );
    },
    // Day-numbers of the leaves that triggered a penalty rule (for red boxes).
    get penalizedDays() {
      const set = new Set();
      for (const r of this.att.summary?.applied_rules || [])
        for (const d of r.dates || []) set.add(Number(d.slice(8, 10)));
      return set;
    },
    daysInMonth(period) {
      const [y, m] = period.split("-").map(Number);
      return new Date(y, m, 0).getDate();
    },
    weekdayLabel(period, day) {
      const [y, m] = period.split("-").map(Number);
      return ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"][new Date(y, m - 1, day).getDay()];
    },
    isSunday(period, day) {
      const [y, m] = period.split("-").map(Number);
      return new Date(y, m - 1, day).getDay() === 0;
    },
    // Display only: a present Sunday shows "S" (paid weekly-off). The stored
    // status stays "P", so it still counts as Present everywhere.
    statusLabel(period, day, status) {
      return status === "P" && this.isSunday(period, day) ? "S" : status;
    },
    statusClass(period, day, status) {
      if (status !== "P") return "bg-rose-100 text-rose-700";
      return this.isSunday(period, day)
        ? "bg-amber-200 text-amber-800"
        : "bg-emerald-100 text-emerald-700";
    },
    async loadAttendance() {
      try {
        this.att.statsShown = false;   // hide stats until the operator clicks Calculate
        this.att.emp = await api(`/api/employees/${this.att.empId}`);
        const existing = await api(`/api/attendance/${this.att.empId}?period=${this.att.period}`);
        const n = this.daysInMonth(this.att.period);
        const byDate = {};
        for (const d of existing.days) byDate[d.work_date.slice(8, 10)] = d;
        this.att.days = [];
        for (let day = 1; day <= n; day++) {
          const key = String(day).padStart(2, "0");
          const ex = byDate[key];
          this.att.days.push({
            day,
            status: ex ? ex.status : "P",
            overtime: ex && ex.overtime_hours != null ? ex.overtime_hours : "",
          });
        }
        this.att.summary = existing.summary;
        await this.loadFyStats();
      } catch (e) { this.fail(e); }
    },
    async submitAttendance() {
      try {
        const payload = {
          employee_id: this.att.empId, period: this.att.period,
          days: this.att.days.map((d) => ({
            day: d.day, status: d.status,
            // OT is enterable for anyone now; only present days carry it.
            overtime: d.status === "P" && d.overtime !== "" && d.overtime != null ? Number(d.overtime) : null,
          })),
        };
        this.att.summary = await api("/api/attendance", { method: "POST", body: payload });
        await this.loadEmployees();
        this.att.emp = await api(`/api/employees/${this.att.empId}`); // bank may have changed
        await this.loadFyStats();
        this.flash("Attendance saved & summarised");
      } catch (e) { this.fail(e); }
    },
    bulkSetOT() {
      const v = window.prompt("Overtime hours to apply to every PRESENT day (blank to clear):", "");
      if (v === null) return;
      const n = v === "" ? "" : Number(v);
      this.att.days.forEach((d) => { if (d.status === "P") d.overtime = n; });
    },
    // CEO editor: editing penalty recomputes paid days from the pre-penalty baseline.
    editorRecalcPresent() {
      const s = this.att.summary;
      if (!s || s.base_present_days == null) return;
      s.present_days = Math.max(0, Math.round((s.base_present_days - Number(s.penalty_days || 0)) * 100) / 100);
    },
    async saveAttendanceOverride() {
      try {
        const s = this.att.summary;
        const res = await api(`/api/attendance/${this.att.empId}/${this.att.period}/override`, {
          method: "POST",
          body: {
            present_days: Number(s.present_days),
            penalty_days: Number(s.penalty_days || 0),
            overtime_hours: Number(s.total_overtime_hours || 0),
            refreshment_days: Number(s.refreshment_days || 0),
          },
        });
        this.att.summary = res.summary;
        await this.loadFyStats();
        this.flash("Attendance metrics adjusted");
      } catch (e) { this.fail(e); }
    },

    // ---- attendance GRID (all employees × all days, one screen) ---------- //
    grid: { period: prevMonth(), rows: [], days: [], dept: "", search: "", loading: false, calculated: false, opStatsShown: false },
    async loadGrid() {
      try {
        this.grid.opStatsShown = false;   // hide operator stats until Calculate
        const n = this.daysInMonth(this.grid.period);
        this.grid.days = Array.from({ length: n }, (_, i) => ({
          day: i + 1,
          wd: this.weekdayLabel(this.grid.period, i + 1),
          sun: this.isSunday(this.grid.period, i + 1),
        }));
        const data = await api(`/api/attendance-grid/${this.grid.period}`);
        this.grid.rows = data.rows.map((r) => {
          const cells = {};
          for (let d = 1; d <= n; d++) {
            const mk = r.marks[d];
            cells[d] = {
              status: mk ? mk.status : "P",
              overtime: mk && mk.overtime != null ? mk.overtime : "",
            };
          }
          return { ...r, cells };
        });
      } catch (e) { this.fail(e); }
    },
    get gridFiltered() {
      const q = this.grid.search.toLowerCase();
      return this.grid.rows.filter(
        (r) => (!this.grid.dept || r.dept === this.grid.dept)
          && (!q || r.name.toLowerCase().includes(q) || String(r.employee_id) === q)
      );
    },
    gridToggle(cell) { cell.status = cell.status === "P" ? "A" : "P"; },
    // Operator tally: per-employee Present/Absent/OT from the current entry
    // (client-side, no penalty info). Shown only after the operator clicks Calculate.
    calculateGridOperator() {
      this.grid.rows = this.grid.rows.map((r) => {
        let present = 0, absent = 0, overtime = 0;
        for (const dm of this.grid.days) {
          const c = r.cells[dm.day];
          if (c.status === "P") {
            present += 1;
            if (c.overtime !== "" && c.overtime != null) overtime += Number(c.overtime);
          } else {
            absent += 1;
          }
        }
        return { ...r, opStats: { present, absent, overtime } };
      });
      this.grid.opStatsShown = true;
      this.flash("Calculated — per-employee totals shown for tallying");
    },
    gridHolidays(r) {
      return this.grid.days.filter(
        (dm) => !dm.sun && r.cells[dm.day] && r.cells[dm.day].status === "A"
      ).length;
    },
    gridRowAllPresent(r) { for (const d in r.cells) r.cells[d].status = "P"; },
    gridRowOT(r) {
      const v = window.prompt(`OT hours for every present day of ${r.name} (blank to clear):`, "");
      if (v === null) return;
      const n = v === "" ? "" : Number(v);
      for (const d in r.cells) if (r.cells[d].status === "P") r.cells[d].overtime = n;
    },
    gridEntries() {
      return this.grid.rows.map((r) => ({
        employee_id: r.employee_id,
        days: this.grid.days.map((dm) => {
          const c = r.cells[dm.day];
          return {
            day: dm.day, status: c.status,
            overtime: c.status === "P" && c.overtime !== "" && c.overtime != null ? Number(c.overtime) : null,
          };
        }),
      }));
    },
    // CEO: recompute stats + penalty highlights from the CURRENT edits, no save.
    async calculateGrid() {
      try {
        const res = await api("/api/attendance/calculate",
          { method: "POST", body: { period: this.grid.period, entries: this.gridEntries() } });
        const byId = {};
        for (const c of res.rows) byId[c.employee_id] = c;
        this.grid.rows = this.grid.rows.map((r) =>
          byId[r.employee_id]
            ? { ...r, summary: byId[r.employee_id].summary, penalized_days: byId[r.employee_id].penalized_days }
            : r);
        this.grid.calculated = true;
        this.flash("Calculated — review the figures, then Publish to save");
      } catch (e) { this.fail(e); }
    },
    // Discard unsaved edits and reload the last saved state.
    async reloadGrid() {
      if (!window.confirm("Discard unsaved changes and reload the last saved attendance?")) return;
      await this.loadGrid();
      this.grid.calculated = false;
      this.flash("Reloaded the last saved attendance");
    },
    async saveGrid() {
      this.grid.loading = true;
      try {
        const res = await api("/api/attendance/bulk",
          { method: "POST", body: { period: this.grid.period, entries: this.gridEntries() } });
        await this.loadEmployees();
        await this.loadGrid();   // refresh computed stats + penalty highlights
        this.grid.calculated = false;
        this.flash(`${this.isAdmin ? "Published" : "Saved"} attendance for ${res.saved} employees`);
      } catch (e) { this.fail(e); } finally { this.grid.loading = false; }
    },

    // ---- advances -------------------------------------------------------- //
    adv: { empId: null, amount: 0, txn_date: today(), cheque: 0, cash: 0, ledger: null },
    enter_advances() { if (!this.adv.empId && this.employees[0]) this.adv.empId = this.employees[0].id; },
    async issueAdvance() {
      try {
        if (Number(this.adv.cheque) + Number(this.adv.cash) !== Number(this.adv.amount))
          return this.flash("Cheque + Cash must equal Amount", "err");
        this.adv.ledger = await api("/api/advances", {
          method: "POST",
          body: {
            employee_id: this.adv.empId, amount: Number(this.adv.amount),
            txn_date: this.adv.txn_date, cheque: Number(this.adv.cheque), cash: Number(this.adv.cash),
          },
        });
        this.adv.amount = 0; this.adv.cheque = 0; this.adv.cash = 0;
        await this.loadEmployees();
        this.flash("Advance issued");
      } catch (e) { this.fail(e); }
    },
    async viewLedger() {
      try { this.adv.ledger = await api(`/api/advances/employee/${this.adv.empId}`); }
      catch (e) { this.fail(e); }
    },

    // ---- salary ---------------------------------------------------------- //
    pay: { period: prevMonth(), rows: [], computed: false, publishing: false },
    async preparePay() {
      try {
        const data = await api(`/api/payroll/prepare/${this.pay.period}`);
        this.pay.rows = data.rows.map((r) => ({ ...r, total: null, cash: null, bonus_status: null, rem_advance_after: null, error: null }));
        this.pay.computed = false;
      } catch (e) { this.fail(e); }
    },
    // Set PF/ESI for every eligible employee at once, then edit individuals.
    setPfAll() {
      const v = window.prompt("PF amount to apply to all PF-eligible employees:", "");
      if (v === null) return;
      const n = Number(v) || 0;
      this.pay.rows.forEach((r) => { if (r.pf_applicable) r.pf = n; });
    },
    setEsiAll() {
      const v = window.prompt("ESI amount to apply to all ESI-eligible employees:", "");
      if (v === null) return;
      const n = Number(v) || 0;
      this.pay.rows.forEach((r) => { if (r.esi_applicable) r.esi = n; });
    },
    // Attendance % shown in the salary table is derived from the (editable) paid days.
    attPctText(r) {
      if (r.present_days == null || !r.total_days) return r.has_attendance ? "0.0" : "—";
      return (Number(r.present_days) / r.total_days * 100).toFixed(1);
    },
    // Editing penalty/punishment days recomputes paid days from the pre-penalty baseline.
    recalcFromPenalty(r) {
      if (r.base_present_days == null || !r.total_days) return;
      const v = Number(r.base_present_days) - Number(r.penalty_days || 0);
      r.present_days = Math.round(v * 100) / 100;
    },
    rulesSummary(r) { return (r.applied_rules || []).map((x) => x.detail).join("\n"); },
    async calculatePay() {
      try {
        // Send the CEO's overridden metrics; attendance % follows the edited paid days.
        const rows = this.pay.rows.map((r) => ({
          ...r,
          attendance_percentage:
            r.present_days != null && r.total_days ? Number(r.present_days) / r.total_days * 100 : 0,
        }));
        const res = await api("/api/payroll/calculate", { method: "POST", body: { period: this.pay.period, rows } });
        const byId = {};
        for (const c of res.rows) byId[c.employee_id] = c;
        this.pay.rows = this.pay.rows.map((r) => ({ ...r, ...byId[r.employee_id] }));
        this.pay.computed = true;
      } catch (e) { this.fail(e); }
    },
    payErrors() {
      return this.pay.rows.filter((r) => r.error || (r.total != null && Number(r.cheque) + Number(r.cash) !== r.total));
    },
    async publishPay() {
      try {
        if (!this.pay.computed) return this.flash("Calculate first", "err");
        if (this.payErrors().length) return this.flash("Fix highlighted rows before publishing", "err");
        this.pay.publishing = true;
        const rows = this.pay.rows.map((r) => ({
          ...r,
          attendance_percentage:
            r.present_days != null && r.total_days ? Number(r.present_days) / r.total_days * 100 : 0,
        }));
        const res = await api("/api/payroll/publish", { method: "POST", body: { period: this.pay.period, rows } });
        await this.loadEmployees();
        this.flash(`Published ${res.published} salaries for ${this.pay.period}`);
      } catch (e) { this.fail(e); } finally { this.pay.publishing = false; }
    },

    // ---- pay history ----------------------------------------------------- //
    history: { period: prevMonth(), rows: [] },
    async loadHistory() {
      try { this.history.rows = await api(`/api/pay?period=${this.history.period}`); }
      catch (e) { this.fail(e); }
    },

    // ---- exports --------------------------------------------------------- //
    exp: { period: prevMonth() },
    download(kind) { window.location = `/api/export/${kind}/${this.exp.period}`; },

    // ---- rules ----------------------------------------------------------- //
    rulesText: "",
    rulesError: "",
    async enter_rules() {
      try { this.rulesText = JSON.stringify(await api("/api/rules"), null, 2); this.rulesError = ""; }
      catch (e) { this.fail(e); }
    },
    async saveRules() {
      try {
        const parsed = JSON.parse(this.rulesText);
        await api("/api/rules", { method: "PUT", body: parsed });
        this.meta = await api("/api/meta");
        this.flash("Rules saved");
      } catch (e) { this.rulesError = e.message; }
    },

    // ---- sync / offline exchange ---------------------------------------- //
    sync: { status: null, incoming: [], period: prevMonth(), busy: false },
    async refreshSync() {
      try {
        this.sync.status = await api("/api/sync/status");
        this.sync.incoming = this.sync.status.auto_check ? await api("/api/sync/available") : [];
      } catch (_) { /* sync is optional — ignore if unavailable */ }
    },
    enter_sync() { this.refreshSync(); if (this.isAdmin) this.loadBackups(); },
    incomingLabel(i) { return i.type === "attendance" ? "attendance " + (i.period || "") : "roster"; },
    async exportAttendance() {
      try {
        this.sync.busy = true;
        const r = await api(`/api/sync/export/attendance/${this.sync.period}`, { method: "POST" });
        if (r.written_to_folder) this.flash(`Attendance for ${this.sync.period} saved to the shared folder`);
        else { this.downloadEnvelope(r.filename, r.envelope); this.flash("No shared folder set — file downloaded to send manually"); }
      } catch (e) { this.fail(e); } finally { this.sync.busy = false; }
    },
    async exportRoster() {
      try {
        this.sync.busy = true;
        const r = await api("/api/sync/export/roster", { method: "POST" });
        if (r.written_to_folder) this.flash("Roster saved to the shared folder");
        else { this.downloadEnvelope(r.filename, r.envelope); this.flash("No shared folder set — roster downloaded to send manually"); }
      } catch (e) { this.fail(e); } finally { this.sync.busy = false; }
    },
    async importIncoming(item) {
      try {
        const r = await api("/api/sync/import", { method: "POST", body: { filename: item.filename } });
        await this.loadEmployees();
        await this.refreshSync();
        if (this.grid.rows.length) await this.loadGrid();   // reflect imported attendance in the grid
        this.flash(`Imported ${r.type}: ${r.summary}`);
      } catch (e) { this.fail(e); }
    },
    async importUpload(ev) {
      const file = ev.target.files[0];
      if (!file) return;
      try {
        const envelope = JSON.parse(await file.text());
        const r = await api("/api/sync/import-file", { method: "POST", body: { envelope } });
        await this.loadEmployees();
        await this.refreshSync();
        this.flash(`Imported ${r.type}: ${r.summary}`);
      } catch (e) { this.fail(e instanceof SyntaxError ? new Error("That file isn't a valid sync file") : e); }
      ev.target.value = "";
    },
    downloadEnvelope(filename, envelope) {
      const blob = new Blob([JSON.stringify(envelope, null, 2)], { type: "application/json" });
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url; a.download = filename; a.click();
      URL.revokeObjectURL(url);
    },
    periodName(p) {
      if (!p) return "";
      const [y, m] = p.split("-").map(Number);
      return new Date(y, m - 1, 1).toLocaleString("en", { month: "long", year: "numeric" });
    },

    // ---- startup notifications (role-specific) -------------------------- //
    attendancePrompt: null,   // CEO: incoming attendance awaiting yes/no
    reminder: null,           // operator: attendance-deadline reminder
    async postLoginSync() {
      this.reminder = null;          // never carry these across a role switch
      this.attendancePrompt = null;
      if (this.isAdmin) {
        // CEO: if last month's attendance arrived, prompt to import (yes/no)
        const last = prevMonth();
        this.attendancePrompt =
          this.sync.incoming.find((i) => i.type === "attendance" && i.period === last)
          || this.sync.incoming.find((i) => i.type === "attendance") || null;
      } else {
        // Operator: silently pull any roster update, then check the reminder
        const roster = this.sync.incoming.find((i) => i.type === "roster");
        if (roster) {
          try {
            await api("/api/sync/import", { method: "POST", body: { filename: roster.filename } });
            await this.loadEmployees();
            await this.refreshSync();
            this.flash("Employee list updated from CEO");
          } catch (_) { /* ignore */ }
        }
        await this.loadReminder();
      }
    },
    async confirmImportAttendance() {
      const item = this.attendancePrompt;
      this.attendancePrompt = null;
      if (item) await this.importIncoming(item);
    },
    async loadReminder() {
      // Attendance for the previous month is due by the 7th of the current month.
      const period = prevMonth();
      try {
        const st = await api(`/api/attendance-status/${period}`);
        this.reminder = st.complete ? null : { ...st, daysLeft: 7 - new Date().getDate() };
      } catch (_) { /* ignore */ }
    },

    // ---- self-update (GitHub Releases) ----------------------------------- //
    // Kept OUT of resetUiState: the popup belongs to the app instance, not to a
    // login session (it must survive logins and show on the login screen too).
    version: "",
    upd: { info: null, show: false, busy: false, done: false },
    async checkUpdates(manual = false) {
      try {
        const info = await api("/api/update/check");
        this.upd.info = info;
        if (info.update_available && (manual || info.auto_check)) {
          this.upd.show = true;
        } else if (manual) {
          if (!info.configured) this.flash("Updates aren't set up (config/update.json)", "err");
          else if (info.error) this.flash(info.error, "err");
          else this.flash(`You're up to date (v${info.current})`);
        }
      } catch (e) { if (manual) this.fail(e); }
    },
    async applyUpdate() {
      this.upd.busy = true;
      try {
        // The server exits itself right after answering; the updater script
        // swaps the .exe and relaunches. This window will close on its own.
        await api("/api/update/apply", { method: "POST" });
        this.upd.done = true;
      } catch (e) {
        this.upd.busy = false;
        this.fail(e);
      }
    },

    // ---- backup (CEO) --------------------------------------------------- //
    backup: { busy: false, recent: [] },
    async loadBackups() { try { this.backup.recent = (await api("/api/backup/list")).backups; } catch (_) {} },
    async backupNow() {
      try {
        this.backup.busy = true;
        const r = await api("/api/backup", { method: "POST" });
        await this.loadBackups();
        this.flash(`Backup saved: ${r.file} (${r.size_kb} KB)`);
      } catch (e) { this.fail(e); } finally { this.backup.busy = false; }
    },
    downloadBackup() { window.location = "/api/backup/download"; },
  };
}
