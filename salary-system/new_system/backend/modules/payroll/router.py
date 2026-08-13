"""Payroll module routes — the FINANCIAL side of employees.

Owns: payroll rules config, advances, salary prepare/calculate/publish, pay
history, and the two Excel exports. The employee master and attendance live in
modules/employees (this module consumes them). Routes moved verbatim from
main.py during the 2026-08 reorganization; paths unchanged.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel

from ...core.deps import get_db, require_admin
from ...core.rules import get_rules, load_rules, save_rules
from . import repo
from .exporters import build_ceo, build_distribution

XLSX_MIME = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"

router = APIRouter()


class AdvanceIn(BaseModel):
    employee_id: int
    amount: int
    txn_date: str
    cheque: int = 0
    cash: int = 0
    note: str | None = None


class PayrollIn(BaseModel):
    period: str
    rows: list[dict]


# --------------------------------------------------------------------------- #
# Rules (admin) — payroll policy lives in config/rules.json
# --------------------------------------------------------------------------- #
@router.get("/api/rules")
def read_rules(user: dict = Depends(require_admin)):
    return load_rules()


@router.put("/api/rules")
def write_rules(body: dict, user: dict = Depends(require_admin)):
    save_rules(body)
    return {"ok": True}


# --------------------------------------------------------------------------- #
# Advances
# --------------------------------------------------------------------------- #
@router.post("/api/advances")
def issue_advance(body: AdvanceIn, user: dict = Depends(require_admin), conn=Depends(get_db)):
    try:
        repo.issue_advance(conn, body.employee_id, body.amount, body.txn_date,
                           body.cheque, body.cash, body.note)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return repo.list_advances(conn, body.employee_id)


@router.get("/api/advances/employee/{emp_id}")
def advances_for_employee(emp_id: int, user: dict = Depends(require_admin), conn=Depends(get_db)):
    return repo.list_advances(conn, emp_id)


@router.get("/api/advances")
def advances_by_period(period: str, user: dict = Depends(require_admin), conn=Depends(get_db)):
    return repo.advances_by_period(conn, period)


# --------------------------------------------------------------------------- #
# Payroll (admin)
# --------------------------------------------------------------------------- #
@router.get("/api/payroll/prepare/{period}")
def prepare_payroll(period: str, user: dict = Depends(require_admin), conn=Depends(get_db)):
    return repo.prepare_payroll(conn, period, get_rules())


@router.post("/api/payroll/calculate")
def calculate_payroll(body: PayrollIn, user: dict = Depends(require_admin)):
    rules = get_rules()
    return {"rows": [repo.compute_row(r, body.period, rules) for r in body.rows]}


@router.post("/api/payroll/publish")
def publish_payroll(body: PayrollIn, user: dict = Depends(require_admin), conn=Depends(get_db)):
    try:
        return repo.publish_payroll(conn, body.period, body.rows, get_rules())
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/api/pay")
def pay(period: str | None = None, employee_id: int | None = None,
        user: dict = Depends(require_admin), conn=Depends(get_db)):
    return repo.list_pay(conn, period=period, employee_id=employee_id)


# --------------------------------------------------------------------------- #
# Exports (admin)
# --------------------------------------------------------------------------- #
@router.get("/api/export/{kind}/{period}")
def export(kind: str, period: str, user: dict = Depends(require_admin), conn=Depends(get_db)):
    if kind == "ceo":
        content, fname = build_ceo(conn, period)
    elif kind == "distribution":
        content, fname = build_distribution(conn, period)
    else:
        raise HTTPException(status_code=404, detail="Unknown export type")
    return Response(
        content=content, media_type=XLSX_MIME,
        headers={"Content-Disposition": f'attachment; filename="{fname}"'},
    )
