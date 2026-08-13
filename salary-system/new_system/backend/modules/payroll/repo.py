"""Payroll data layer — the FINANCIAL side only: advances (CR/DR ledger) and
the prepare → compute → publish salary pipeline.

The employee master and attendance belong to modules/employees; this module
reads them (allowed direction: payroll → employees). The pure calculation
rules live in engine.py.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import date

from ..employees import repo as employees_repo
from .engine import SalaryBreakdown, compute_salary, days_in_period


def _period_parts(period: str) -> tuple[int, int]:
    y, m = period.split("-")
    return int(y), int(m)


def row_to_dict(r: sqlite3.Row | None):
    return dict(r) if r is not None else None


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
    emp = employees_repo.get_employee(conn, employee_id)
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
# Payroll
# --------------------------------------------------------------------------- #
def prepare_payroll(conn, period: str, rules: dict) -> dict:
    """Build the editable salary table for a period: one row per active
    employee, pre-filled from attendance + advances."""
    year, month = _period_parts(period)
    period_days = days_in_period(year, month)
    rows = []
    for emp in employees_repo.list_employees(conn, active_only=True):
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
