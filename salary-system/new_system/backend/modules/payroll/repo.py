"""Data-access layer: all SQL lives here, so the API handlers stay thin and the
payroll engine stays pure. Functions take an open sqlite3 connection.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import date, datetime

from .engine import (
    DayMark,
    SalaryBreakdown,
    compute_salary,
    days_in_period,
    summarize_attendance,
)


def _period_parts(period: str) -> tuple[int, int]:
    y, m = period.split("-")
    return int(y), int(m)


def row_to_dict(r: sqlite3.Row | None):
    return dict(r) if r is not None else None


# --------------------------------------------------------------------------- #
# Employees
# --------------------------------------------------------------------------- #
def list_employees(conn, *, active_only: bool = True) -> list[dict]:
    q = "SELECT * FROM employee"
    if active_only:
        q += " WHERE active = 1"
    q += " ORDER BY id"
    return [dict(r) for r in conn.execute(q).fetchall()]


def get_employee(conn, emp_id: int) -> dict | None:
    return row_to_dict(
        conn.execute("SELECT * FROM employee WHERE id = ?", (emp_id,)).fetchone()
    )


def _new_employee_leave_seed(rules: dict, overtime_eligible: bool) -> int:
    """Mirror old addEmployee.py: only non-overtime employees get a bank, seeded
    to the months remaining in the year (12 - current month)."""
    if overtime_eligible:
        return 0
    mode = rules["leave"].get("new_employee_seed", "remaining_months_in_year")
    entitlement = int(rules["leave"].get("annual_entitlement_days", 12))
    if mode == "remaining_months_in_year":
        return max(0, entitlement - date.today().month)
    return entitlement


def create_employee(conn, data: dict, rules: dict) -> int:
    ot = bool(data.get("overtime_eligible"))
    leave = _new_employee_leave_seed(rules, ot)
    cur = conn.execute(
        """INSERT INTO employee
           (name, dept, base_salary, pf_applicable, esi_applicable,
            overtime_eligible, shift, rem_advance, leave_balance, date_joined, active)
           VALUES (?,?,?,?,?,?,?,?,?,?,1)""",
        (
            data["name"].strip(),
            data["dept"],
            int(data["base_salary"]),
            int(bool(data.get("pf_applicable"))),
            int(bool(data.get("esi_applicable"))),
            int(ot),
            data.get("shift", "D"),
            int(data.get("rem_advance", 0)),
            int(data.get("leave_balance", leave)),
            data.get("date_joined") or None,
        ),
    )
    conn.commit()
    return cur.lastrowid


def update_employee(conn, emp_id: int, data: dict) -> None:
    conn.execute(
        """UPDATE employee SET
             name=?, dept=?, base_salary=?, pf_applicable=?, esi_applicable=?,
             overtime_eligible=?, shift=?, rem_advance=?, leave_balance=?, date_joined=?
           WHERE id=?""",
        (
            data["name"].strip(),
            data["dept"],
            int(data["base_salary"]),
            int(bool(data.get("pf_applicable"))),
            int(bool(data.get("esi_applicable"))),
            int(bool(data.get("overtime_eligible"))),
            data.get("shift", "D"),
            int(data.get("rem_advance", 0)),
            int(data.get("leave_balance", 0)),
            data.get("date_joined") or None,
            emp_id,
        ),
    )
    conn.commit()


def set_employee_active(conn, emp_id: int, active: bool) -> None:
    conn.execute("UPDATE employee SET active=? WHERE id=?", (int(active), emp_id))
    conn.commit()


def employee_profile(conn, emp_id: int) -> dict:
    """Full profile for one employee: master info, month-by-month series for
    salary / advances / attendance, and lifetime aggregate stats."""
    emp = get_employee(conn, emp_id)
    if not emp:
        raise ValueError("Employee not found")

    pay = [dict(r) for r in conn.execute(
        "SELECT period, total, base, bonus, cheque, cash, overtime_pay, "
        "       refreshment_pay, pf, esi, adv_deducted "
        "FROM pay WHERE employee_id=? ORDER BY period", (emp_id,))]
    att = [dict(r) for r in conn.execute(
        "SELECT period, attendance_percentage, total_overtime_hours, "
        "       refreshment_days, penalty_days "
        "FROM attendance_summary WHERE employee_id=? ORDER BY period", (emp_id,))]
    adv_monthly = [dict(r) for r in conn.execute(
        "SELECT substr(txn_date,1,7) AS month, "
        "  SUM(CASE WHEN type='CR' THEN amount ELSE 0 END) AS issued, "
        "  SUM(CASE WHEN type='DR' THEN amount ELSE 0 END) AS recovered "
        "FROM advance WHERE employee_id=? GROUP BY month ORDER BY month", (emp_id,))]
    adv_log = [dict(r) for r in conn.execute(
        "SELECT txn_date, amount, type, cheque, cash, note FROM advance "
        "WHERE employee_id=? ORDER BY txn_date DESC, id DESC LIMIT 20", (emp_id,))]

    n_att = len(att)
    stats = {
        "total_paid": sum(p["total"] for p in pay),
        "months_paid": len(pay),
        "total_bonus": sum(p["bonus"] for p in pay),
        "total_overtime": round(sum(a["total_overtime_hours"] for a in att), 1),
        "total_penalty_days": sum(a["penalty_days"] for a in att),
        "avg_attendance": round(sum(a["attendance_percentage"] for a in att) / n_att, 1) if n_att else 0,
        "current_advance": emp["rem_advance"],
        "total_advance_taken": sum(r["issued"] for r in adv_monthly),
        "total_advance_recovered": sum(r["recovered"] for r in adv_monthly),
        "leave_balance": emp["leave_balance"],
        "first_pay": pay[0]["period"] if pay else None,
        "last_pay": pay[-1]["period"] if pay else None,
        "avg_salary": round(sum(p["total"] for p in pay) / len(pay)) if pay else 0,
    }
    return {
        "employee": emp,
        "salary_series": [{"period": p["period"], "total": p["total"], "bonus": p["bonus"]} for p in pay],
        "advance_monthly": adv_monthly,
        "advance_log": adv_log,
        "attendance_series": [
            {"period": a["period"], "attendance_percentage": round(a["attendance_percentage"], 1),
             "overtime_hours": a["total_overtime_hours"], "penalty_days": a["penalty_days"]}
            for a in att
        ],
        "stats": stats,
    }


# --------------------------------------------------------------------------- #
# Advances  (CR = issued to employee, DR = recovered during payroll)
# --------------------------------------------------------------------------- #
def issue_advance(conn, employee_id, amount, txn_date, cheque, cash, note=None) -> None:
    amount, cheque, cash = int(amount), int(cheque), int(cash)
    if amount <= 0:
        raise ValueError("Advance amount must be positive")
    if cheque + cash != amount:
        raise ValueError("Cheque + Cash must equal the advance amount")
    conn.execute(
        "INSERT INTO advance (employee_id, amount, txn_date, type, cheque, cash, note)"
        " VALUES (?,?,?,'CR',?,?,?)",
        (employee_id, amount, txn_date, cheque, cash, note),
    )
    conn.execute(
        "UPDATE employee SET rem_advance = rem_advance + ? WHERE id = ?",
        (amount, employee_id),
    )
    conn.commit()


def list_advances(conn, employee_id: int) -> dict:
    rows = [
        dict(r)
        for r in conn.execute(
            "SELECT * FROM advance WHERE employee_id=? ORDER BY txn_date, id",
            (employee_id,),
        ).fetchall()
    ]
    emp = get_employee(conn, employee_id)
    return {"rows": rows, "rem_advance": emp["rem_advance"] if emp else 0}


def advances_by_period(conn, period: str) -> list[dict]:
    return [
        dict(r)
        for r in conn.execute(
            "SELECT a.*, e.name AS emp_name FROM advance a JOIN employee e"
            " ON e.id=a.employee_id WHERE substr(a.txn_date,1,7)=? ORDER BY a.txn_date",
            (period,),
        ).fetchall()
    ]


# --------------------------------------------------------------------------- #
# Attendance
# --------------------------------------------------------------------------- #
def save_attendance(conn, employee_id: int, period: str, days: list[dict], rules: dict) -> dict:
    """Persist daily marks + a recomputed monthly summary, and update the leave
    bank idempotently (re-submitting a month first credits back the prior
    leave_used, then re-applies)."""
    emp = get_employee(conn, employee_id)
    if not emp:
        raise ValueError("Employee not found")

    year, month = _period_parts(period)
    marks = [
        DayMark(
            day=date(year, month, int(d["day"])),
            status=d.get("status", "P"),
            overtime_hours=(
                float(d["overtime"]) if d.get("overtime") not in (None, "") else None
            ),
        )
        for d in days
    ]

    # Idempotent leave bank: undo a previous submission for this period.
    prior = conn.execute(
        "SELECT leave_used FROM attendance_summary WHERE employee_id=? AND period=?",
        (employee_id, period),
    ).fetchone()
    bank = emp["leave_balance"]
    if prior and not emp["overtime_eligible"]:
        bank += prior["leave_used"]

    summary = summarize_attendance(
        marks,
        overtime_eligible=bool(emp["overtime_eligible"]),
        leave_balance=None if emp["overtime_eligible"] else bank,
        rules=rules,
    )

    # Persist daily marks.
    for m in marks:
        conn.execute(
            "INSERT INTO attendance_day (employee_id, work_date, status, overtime_hours)"
            " VALUES (?,?,?,?)"
            " ON CONFLICT(employee_id, work_date) DO UPDATE SET"
            " status=excluded.status, overtime_hours=excluded.overtime_hours",
            (employee_id, m.day.isoformat(), m.status, m.overtime_hours),
        )

    conn.execute(
        """INSERT INTO attendance_summary
             (employee_id, period, present_days, total_days, attendance_percentage,
              total_overtime_hours, refreshment_days, penalty_days, leave_used,
              base_present_days, applied_rules)
           VALUES (?,?,?,?,?,?,?,?,?,?,?)
           ON CONFLICT(employee_id, period) DO UPDATE SET
             present_days=excluded.present_days, total_days=excluded.total_days,
             attendance_percentage=excluded.attendance_percentage,
             total_overtime_hours=excluded.total_overtime_hours,
             refreshment_days=excluded.refreshment_days,
             penalty_days=excluded.penalty_days, leave_used=excluded.leave_used,
             base_present_days=excluded.base_present_days,
             applied_rules=excluded.applied_rules""",
        (
            employee_id, period, summary.present_days, summary.total_days,
            summary.attendance_percentage, summary.total_overtime_hours,
            summary.refreshment_days, summary.penalty_days, summary.leave_used,
            summary.base_present_days, json.dumps(summary.applied_rules),
        ),
    )

    if not emp["overtime_eligible"] and summary.new_leave_balance is not None:
        conn.execute(
            "UPDATE employee SET leave_balance=? WHERE id=?",
            (summary.new_leave_balance, employee_id),
        )
    conn.commit()
    return summary.to_dict()


def get_attendance(conn, employee_id: int, period: str) -> dict:
    rows = conn.execute(
        "SELECT work_date, status, overtime_hours FROM attendance_day"
        " WHERE employee_id=? AND substr(work_date,1,7)=? ORDER BY work_date",
        (employee_id, period),
    ).fetchall()
    summary = row_to_dict(
        conn.execute(
            "SELECT * FROM attendance_summary WHERE employee_id=? AND period=?",
            (employee_id, period),
        ).fetchone()
    )
    if summary and summary.get("applied_rules"):
        summary["applied_rules"] = json.loads(summary["applied_rules"])
    return {"days": [dict(r) for r in rows], "summary": summary}


def override_summary(conn, employee_id: int, period: str, data: dict) -> dict:
    """CEO discretionary adjustment of the computed monthly metrics, made from the
    attendance editor. Updates the summary directly (daily marks untouched);
    attendance_% follows present_days."""
    summ = conn.execute(
        "SELECT * FROM attendance_summary WHERE employee_id=? AND period=?",
        (employee_id, period),
    ).fetchone()
    if not summ:
        raise ValueError("No attendance summary to adjust for this period")
    total = summ["total_days"]
    present = float(data.get("present_days", summ["present_days"]))
    att_pct = (present / total * 100) if total else 0
    conn.execute(
        """UPDATE attendance_summary SET
             present_days=?, attendance_percentage=?, penalty_days=?,
             total_overtime_hours=?, refreshment_days=?
           WHERE employee_id=? AND period=?""",
        (
            present, att_pct,
            int(data.get("penalty_days", summ["penalty_days"])),
            float(data.get("overtime_hours", summ["total_overtime_hours"])),
            int(data.get("refreshment_days", summ["refreshment_days"])),
            employee_id, period,
        ),
    )
    conn.commit()
    return get_attendance(conn, employee_id, period)


def attendance_fy_stats(conn, employee_id: int, fy_start: int) -> dict:
    """Year-to-date attendance totals for one employee across an Indian financial
    year (1 Apr `fy_start` … 31 Mar `fy_start+1`). Present/absent are counted
    from the daily marks; penalty + overtime are summed from the monthly
    summaries."""
    day_start = f"{fy_start}-04-01"
    day_end = f"{fy_start + 1}-03-31"
    counts = conn.execute(
        "SELECT "
        "  SUM(CASE WHEN status='P' THEN 1 ELSE 0 END) AS present, "
        "  SUM(CASE WHEN status='A' THEN 1 ELSE 0 END) AS absent "
        "FROM attendance_day WHERE employee_id=? AND work_date BETWEEN ? AND ?",
        (employee_id, day_start, day_end),
    ).fetchone()
    agg = conn.execute(
        "SELECT COALESCE(SUM(penalty_days),0) AS pen, "
        "       COALESCE(SUM(total_overtime_hours),0) AS ot "
        "FROM attendance_summary WHERE employee_id=? AND period BETWEEN ? AND ?",
        (employee_id, f"{fy_start}-04", f"{fy_start + 1}-03"),
    ).fetchone()
    return {
        "fy_start": fy_start,
        "fy_label": f"Apr {fy_start} – Mar {fy_start + 1}",
        "present_days": counts["present"] or 0,
        "absent_days": counts["absent"] or 0,
        "penalty_days": agg["pen"] or 0,
        "overtime_hours": agg["ot"] or 0,
    }


def attendance_status(conn, period: str) -> dict:
    """How much of a month's attendance is filled (for the operator reminder)."""
    total = conn.execute("SELECT COUNT(*) AS n FROM employee WHERE active=1").fetchone()["n"]
    filled = conn.execute(
        "SELECT COUNT(DISTINCT employee_id) AS n FROM attendance_day WHERE substr(work_date,1,7)=?",
        (period,),
    ).fetchone()["n"]
    return {"period": period, "total_active": total, "filled": filled,
            "complete": total > 0 and filled >= total}


def attendance_grid(conn, period: str, include_stats: bool = False) -> dict:
    """All active employees with their stored daily marks for a period, for the
    bulk grid. Days the operator hasn't touched are simply absent from `marks`
    (the UI defaults them to Present). When `include_stats` (CEO only), each row
    also carries the computed monthly summary and the penalised leave-days."""
    summ: dict[int, dict] = {}
    if include_stats:
        for r in conn.execute("SELECT * FROM attendance_summary WHERE period=?", (period,)):
            d = dict(r)
            rules = json.loads(d["applied_rules"]) if d.get("applied_rules") else []
            pen = sorted({int(x[8:10]) for ru in rules for x in ru.get("dates", [])})
            summ[d["employee_id"]] = {
                "summary": {
                    "present_days": d["present_days"],
                    "overtime_hours": d["total_overtime_hours"],
                    "penalty_days": d["penalty_days"],
                    "attendance_percentage": round(d["attendance_percentage"], 1),
                },
                "penalized_days": pen,
            }

    yy, mm = (int(x) for x in period.split("-"))
    rows = []
    for emp in list_employees(conn, active_only=True):
        marks = {}
        for r in conn.execute(
            "SELECT work_date, status, overtime_hours FROM attendance_day"
            " WHERE employee_id=? AND substr(work_date,1,7)=?",
            (emp["id"], period),
        ):
            marks[int(r["work_date"][8:10])] = {
                "status": r["status"], "overtime": r["overtime_hours"]
            }
        row = {
            "employee_id": emp["id"], "name": emp["name"], "dept": emp["dept"],
            "overtime_eligible": bool(emp["overtime_eligible"]), "marks": marks,
        }
        if include_stats:
            extra = summ.get(emp["id"])
            if extra:
                s = dict(extra["summary"])
                s["holidays"] = sum(
                    1 for day, mk in marks.items()
                    if mk["status"] == "A" and date(yy, mm, day).weekday() != 6
                )
                row["summary"] = s
                row["penalized_days"] = extra["penalized_days"]
            else:
                row["summary"] = None
                row["penalized_days"] = []
        rows.append(row)
    return {"period": period, "rows": rows}


def save_attendance_bulk(conn, period: str, entries: list[dict], rules: dict) -> dict:
    """Save a whole month for many employees at once (one row per employee)."""
    saved = 0
    for e in entries:
        save_attendance(conn, e["employee_id"], period, e["days"], rules)
        saved += 1
    return {"saved": saved, "period": period}


def compute_attendance_grid(conn, period: str, entries: list[dict], rules: dict) -> dict:
    """Run the engine over the CURRENT (unsaved) grid state and return the
    computed stats + penalised days per employee — WITHOUT writing anything.
    Mirrors save_attendance's leave-bank handling so the preview equals what
    Publish would store."""
    year, month = _period_parts(period)
    rows = []
    for e in entries:
        emp = get_employee(conn, e["employee_id"])
        if not emp:
            continue
        marks = [
            DayMark(
                day=date(year, month, int(d["day"])),
                status=d.get("status", "P"),
                overtime_hours=(float(d["overtime"]) if d.get("overtime") not in (None, "") else None),
            )
            for d in e["days"]
        ]
        prior = conn.execute(
            "SELECT leave_used FROM attendance_summary WHERE employee_id=? AND period=?",
            (e["employee_id"], period),
        ).fetchone()
        bank = emp["leave_balance"]
        if prior and not emp["overtime_eligible"]:
            bank += prior["leave_used"]
        summary = summarize_attendance(
            marks,
            overtime_eligible=bool(emp["overtime_eligible"]),
            leave_balance=None if emp["overtime_eligible"] else bank,
            rules=rules,
        )
        holidays = sum(1 for m in marks if not m.is_present and not m.is_sunday)
        pen = sorted({int(x[8:10]) for r in summary.applied_rules for x in r.get("dates", [])})
        rows.append({
            "employee_id": e["employee_id"],
            "summary": {
                "present_days": summary.present_days,
                "overtime_hours": summary.total_overtime_hours,
                "penalty_days": summary.penalty_days,
                "attendance_percentage": round(summary.attendance_percentage, 1),
                "holidays": holidays,
            },
            "penalized_days": pen,
        })
    return {"period": period, "rows": rows}


def attendance_summaries(conn, period: str) -> list[dict]:
    return [
        dict(r)
        for r in conn.execute(
            "SELECT s.*, e.name AS emp_name, e.dept FROM attendance_summary s"
            " JOIN employee e ON e.id=s.employee_id WHERE s.period=? ORDER BY s.employee_id",
            (period,),
        ).fetchall()
    ]


def attendance_history(conn, employee_id: int) -> list[dict]:
    return [
        dict(r)
        for r in conn.execute(
            "SELECT * FROM attendance_summary WHERE employee_id=? ORDER BY period DESC",
            (employee_id,),
        ).fetchall()
    ]


# --------------------------------------------------------------------------- #
# Payroll
# --------------------------------------------------------------------------- #
def prepare_payroll(conn, period: str, rules: dict) -> dict:
    """Build the editable salary table for a period: one row per active
    employee, pre-filled from attendance + advances."""
    year, month = _period_parts(period)
    period_days = days_in_period(year, month)
    rows = []
    for emp in list_employees(conn, active_only=True):
        summ = conn.execute(
            "SELECT * FROM attendance_summary WHERE employee_id=? AND period=?",
            (emp["id"], period),
        ).fetchone()
        published = conn.execute(
            "SELECT * FROM pay WHERE employee_id=? AND period=?",
            (emp["id"], period),
        ).fetchone()
        applied = json.loads(summ["applied_rules"]) if (summ and summ["applied_rules"]) else []
        rows.append(
            {
                "employee_id": emp["id"],
                "name": emp["name"],
                "dept": emp["dept"],
                "overtime_eligible": bool(emp["overtime_eligible"]),
                "pf_applicable": bool(emp["pf_applicable"]),
                "esi_applicable": bool(emp["esi_applicable"]),
                "base": emp["base_salary"],
                "old_advance": emp["rem_advance"],
                "total_days": summ["total_days"] if summ else period_days,
                # CEO-editable attendance metrics (defaults from the operator's entry)
                "attendance_percentage": summ["attendance_percentage"] if summ else None,
                "present_days": summ["present_days"] if summ else None,
                "base_present_days": summ["base_present_days"] if summ else period_days,
                "penalty_days": summ["penalty_days"] if summ else 0,
                "overtime_hours": summ["total_overtime_hours"] if summ else 0,
                "refreshment_days": summ["refreshment_days"] if summ else 0,
                "applied_rules": applied,
                "has_attendance": summ is not None,
                # editable salary inputs
                "pf": 0,
                "esi": 0,
                "new_advance": 0,
                "adj_advance": 0,
                "cheque": 0,
                "published": row_to_dict(published),
            }
        )
    return {"period": period, "period_days": period_days, "rows": rows}


def compute_row(row: dict, period: str, rules: dict) -> dict:
    """Run one editable row through the engine and return the computed result
    (preview, no persistence)."""
    year, month = _period_parts(period)
    period_days = days_in_period(year, month)
    att_pct = float(row.get("attendance_percentage") or 0)
    fraction = att_pct / 100.0
    old_adv = float(row.get("old_advance", 0))
    new_adv = float(row.get("new_advance", 0))
    adj_adv = float(row.get("adj_advance", 0))
    available = old_adv + new_adv
    error = None
    if adj_adv > available:
        error = "Adjusted advance exceeds available advance"

    breakdown: SalaryBreakdown = compute_salary(
        base=float(row["base"]),
        attendance_fraction=fraction,
        overtime_hours=float(row.get("overtime_hours", 0)),
        refreshment_days=int(row.get("refreshment_days", 0)),
        pf=float(row.get("pf", 0)),
        esi=float(row.get("esi", 0)),
        adjusted_advance=adj_adv,
        period_days=period_days,
        dept=row["dept"],
        emp_name=row["name"],
        rules=rules,
    )
    cheque = float(row.get("cheque", 0))
    cash = breakdown.total - cheque
    rem_after = old_adv + new_adv - adj_adv
    out = breakdown.to_dict()
    out.update(
        {
            "employee_id": row["employee_id"],
            "name": row["name"],
            "dept": row["dept"],
            "old_advance": old_adv,
            "new_advance": new_adv,
            "adj_advance": adj_adv,
            "cheque": cheque,
            "cash": cash,
            "rem_advance_after": rem_after,
            # attendance metrics actually used (carried through for persistence)
            "attendance_percentage": att_pct,
            "present_days": row.get("present_days"),
            "penalty_days": int(row.get("penalty_days") or 0),
            "total_days": period_days,
            "error": error,
        }
    )
    return out


def publish_payroll(conn, period: str, rows: list[dict], rules: dict) -> dict:
    """Persist computed pay for a period and post the advance ledger entries."""
    year, month = _period_parts(period)
    today = date.today().isoformat()
    last_day = f"{period}-{days_in_period(year, month):02d}"
    published = 0
    for row in rows:
        comp = compute_row(row, period, rules)
        if comp["error"]:
            raise ValueError(f"{comp['name']}: {comp['error']}")
        cheque = int(comp["cheque"])
        cash = int(comp["cash"])
        if cheque + cash != comp["total"]:
            raise ValueError(f"{comp['name']}: Cheque + Cash must equal Total")

        emp_id = comp["employee_id"]
        conn.execute(
            """INSERT INTO pay
                 (employee_id, period, base, base_att, pf, esi, overtime_hours,
                  overtime_pay, refreshment_days, refreshment_pay,
                  attendance_percentage, penalty_days, adv_deducted,
                  gross, bonus, bonus_status, total, cheque, cash,
                  old_advance, new_advance, published_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
               ON CONFLICT(employee_id, period) DO UPDATE SET
                 base=excluded.base, base_att=excluded.base_att, pf=excluded.pf,
                 esi=excluded.esi, overtime_hours=excluded.overtime_hours,
                 overtime_pay=excluded.overtime_pay,
                 refreshment_days=excluded.refreshment_days,
                 refreshment_pay=excluded.refreshment_pay,
                 attendance_percentage=excluded.attendance_percentage,
                 penalty_days=excluded.penalty_days,
                 adv_deducted=excluded.adv_deducted, gross=excluded.gross,
                 bonus=excluded.bonus, bonus_status=excluded.bonus_status,
                 total=excluded.total, cheque=excluded.cheque, cash=excluded.cash,
                 old_advance=excluded.old_advance, new_advance=excluded.new_advance,
                 published_at=excluded.published_at""",
            (
                emp_id, period, int(comp["base"]), comp["base_att"], int(comp["pf"]),
                int(comp["esi"]), comp["overtime_hours"], comp["overtime_pay"],
                int(comp["refreshment_days"]), comp["refreshment_pay"],
                comp["attendance_percentage"], comp["penalty_days"],
                int(comp["adj_advance"]), comp["gross"], int(comp["bonus"]),
                comp["bonus_status"], comp["total"], cheque, cash,
                int(comp["old_advance"]), int(comp["new_advance"]), today,
            ),
        )
        # Write the CEO's discretionary metrics back to the attendance record
        # (only when a summary already exists for this employee/period). Prefer
        # the CEO's actual day count over recomputing from % (avoids float drift).
        present = comp["present_days"]
        if present is None:
            present = (comp["attendance_percentage"] or 0) / 100 * comp["total_days"]
        conn.execute(
            """UPDATE attendance_summary SET
                 attendance_percentage=?, present_days=?, penalty_days=?,
                 total_overtime_hours=?, refreshment_days=?
               WHERE employee_id=? AND period=?""",
            (
                comp["attendance_percentage"], present, comp["penalty_days"],
                comp["overtime_hours"], int(comp["refreshment_days"]),
                emp_id, period,
            ),
        )
        if comp["new_advance"] > 0:
            conn.execute(
                "INSERT INTO advance (employee_id, amount, txn_date, type, note)"
                " VALUES (?,?,?,'CR','payroll new advance')",
                (emp_id, int(comp["new_advance"]), last_day),
            )
        if comp["adj_advance"] > 0:
            conn.execute(
                "INSERT INTO advance (employee_id, amount, txn_date, type, note)"
                " VALUES (?,?,?,'DR','payroll recovery')",
                (emp_id, int(comp["adj_advance"]), today),
            )
        conn.execute(
            "UPDATE employee SET rem_advance=? WHERE id=?",
            (int(comp["rem_advance_after"]), emp_id),
        )
        published += 1
    conn.commit()
    return {"published": published, "period": period}


def list_pay(conn, *, period: str | None = None, employee_id: int | None = None) -> list[dict]:
    q = ("SELECT p.*, e.name AS emp_name, e.dept FROM pay p"
         " JOIN employee e ON e.id=p.employee_id WHERE 1=1")
    args: list = []
    if period:
        q += " AND p.period=?"
        args.append(period)
    if employee_id:
        q += " AND p.employee_id=?"
        args.append(employee_id)
    q += " ORDER BY p.period DESC, p.employee_id"
    return [dict(r) for r in conn.execute(q, args).fetchall()]


# --------------------------------------------------------------------------- #
# Leave reset (old: every January, UPDATE remaining_holidays SET holidays=12)
# --------------------------------------------------------------------------- #
def leave_reset(conn, year: int, rules: dict) -> dict:
    done = conn.execute("SELECT 1 FROM leave_reset WHERE year=?", (year,)).fetchone()
    if done:
        return {"status": "already_done", "year": year}
    entitlement = int(rules["leave"].get("annual_entitlement_days", 12))
    conn.execute(
        "UPDATE employee SET leave_balance=? WHERE overtime_eligible=0", (entitlement,)
    )
    conn.execute("INSERT INTO leave_reset (year) VALUES (?)", (year,))
    conn.commit()
    return {"status": "reset", "year": year, "entitlement": entitlement}


# --------------------------------------------------------------------------- #
# Offline sync — payload builders, importers, and the import log
# --------------------------------------------------------------------------- #
def export_attendance_data(conn, period: str) -> list[dict]:
    """Raw daily marks for a month, grouped by employee (operator → CEO)."""
    rows = conn.execute(
        "SELECT ad.employee_id, e.name, ad.work_date, ad.status, ad.overtime_hours "
        "FROM attendance_day ad JOIN employee e ON e.id=ad.employee_id "
        "WHERE substr(ad.work_date,1,7)=? ORDER BY ad.employee_id, ad.work_date",
        (period,),
    ).fetchall()
    by_emp: dict[int, dict] = {}
    for r in rows:
        d = by_emp.setdefault(
            r["employee_id"], {"employee_id": r["employee_id"], "name": r["name"], "days": []}
        )
        d["days"].append({
            "day": int(r["work_date"][8:10]),
            "status": r["status"],
            "overtime": r["overtime_hours"],
        })
    return list(by_emp.values())


def export_roster_data(conn) -> list[dict]:
    """Employee master (CEO → operator)."""
    return [dict(r) for r in conn.execute(
        "SELECT id, name, dept, base_salary, pf_applicable, esi_applicable, "
        "overtime_eligible, shift, rem_advance, leave_balance, date_joined, active "
        "FROM employee ORDER BY id")]


def apply_attendance_import(conn, period: str, entries: list[dict], rules: dict) -> dict:
    """CEO side: run each employee's marks through save_attendance (recomputes
    summaries + leave bank on the master DB). Unknown employees are skipped."""
    applied, skipped = 0, []
    for e in entries:
        try:
            save_attendance(conn, e["employee_id"], period, e["days"], rules)
            applied += 1
        except ValueError:
            skipped.append(e.get("employee_id"))
    return {"applied": applied, "skipped": skipped}


def apply_roster_import(conn, employees: list[dict]) -> dict:
    """Operator side: mirror the CEO's employee master (upsert by id)."""
    for e in employees:
        conn.execute(
            """INSERT INTO employee
               (id, name, dept, base_salary, pf_applicable, esi_applicable,
                overtime_eligible, shift, rem_advance, leave_balance, date_joined, active)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
               ON CONFLICT(id) DO UPDATE SET
                 name=excluded.name, dept=excluded.dept, base_salary=excluded.base_salary,
                 pf_applicable=excluded.pf_applicable, esi_applicable=excluded.esi_applicable,
                 overtime_eligible=excluded.overtime_eligible, shift=excluded.shift,
                 rem_advance=excluded.rem_advance, leave_balance=excluded.leave_balance,
                 date_joined=excluded.date_joined, active=excluded.active""",
            (
                e["id"], e["name"], e["dept"], int(e["base_salary"]),
                int(e["pf_applicable"]), int(e["esi_applicable"]), int(e["overtime_eligible"]),
                e.get("shift", "D"), int(e.get("rem_advance", 0)), int(e.get("leave_balance", 0)),
                e.get("date_joined"), int(e.get("active", 1)),
            ),
        )
    conn.commit()
    return {"applied": len(employees)}


def record_import(conn, filename, file_hash, type_, source, period, summary) -> None:
    conn.execute(
        "INSERT INTO sync_log (filename, file_hash, type, source, period, summary, imported_at)"
        " VALUES (?,?,?,?,?,?,?)",
        (filename, file_hash, type_, source, period, summary,
         datetime.now().isoformat(timespec="seconds")),
    )
    conn.commit()


def imported_hashes(conn) -> set[str]:
    return {r["file_hash"] for r in conn.execute("SELECT file_hash FROM sync_log")}
