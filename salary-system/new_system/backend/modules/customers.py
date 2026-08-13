"""Customers — the thin master that Orders, Parts & Pricing and consignments
reference: name, GSTIN, addresses, contact persons, payment terms.

Deactivation instead of deletion once referenced (orders/drawings keep their
history); a customer with no references can be deleted outright.
"""

from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from ..core.db import row_to_dict
from ..core.deps import get_db, require_module

router = APIRouter(prefix="/api/customers",
                   dependencies=[Depends(require_module("customers"))])


def _s(v) -> str:
    return (v or "").strip()


# --------------------------------------------------------------------------- #
# Data
# --------------------------------------------------------------------------- #
def list_customers(conn, q: str = "", active_only: bool = True) -> list[dict]:
    sql = """SELECT c.*,
                    (SELECT COUNT(*) FROM customer_contact k WHERE k.customer_id=c.id) AS contacts,
                    (SELECT COUNT(*) FROM customer_order o WHERE o.customer_id=c.id) AS orders,
                    (SELECT COUNT(*) FROM drawing d WHERE d.customer_id=c.id) AS drawings
             FROM customer c WHERE 1=1"""
    args: list = []
    if active_only:
        sql += " AND c.active=1"
    if _s(q):
        like = f"%{_s(q)}%"
        sql += " AND (c.name LIKE ? OR c.gstin LIKE ?)"
        args += [like, like]
    sql += " ORDER BY c.name COLLATE NOCASE"
    return [dict(r) for r in conn.execute(sql, args)]


def get_customer(conn, customer_id: int) -> dict:
    row = row_to_dict(conn.execute(
        "SELECT * FROM customer WHERE id=?", (customer_id,)).fetchone())
    if not row:
        raise ValueError("Customer not found")
    row["contacts"] = [dict(r) for r in conn.execute(
        "SELECT * FROM customer_contact WHERE customer_id=? ORDER BY id",
        (customer_id,))]
    return row


def save_customer(conn, data: dict, customer_id: int | None = None) -> int:
    name = _s(data.get("name"))
    if not name:
        raise ValueError("Customer name is required")
    dup = conn.execute("SELECT id FROM customer WHERE name=?", (name,)).fetchone()
    if dup and dup["id"] != customer_id:
        raise ValueError(f"'{name}' already exists")
    fields = (name, _s(data.get("gstin")).upper(), _s(data.get("address_billing")),
              _s(data.get("address_shipping")), _s(data.get("payment_terms")),
              _s(data.get("notes")))
    if customer_id is None:
        cur = conn.execute(
            """INSERT INTO customer (name, gstin, address_billing, address_shipping,
                 payment_terms, notes, active, created_at) VALUES (?,?,?,?,?,?,1,?)""",
            (*fields, datetime.now().isoformat(timespec="seconds")))
        customer_id = cur.lastrowid
    else:
        conn.execute(
            """UPDATE customer SET name=?, gstin=?, address_billing=?,
                 address_shipping=?, payment_terms=?, notes=? WHERE id=?""",
            (*fields, customer_id))
    conn.commit()
    return customer_id


def delete_customer(conn, customer_id: int) -> None:
    refs = conn.execute(
        "SELECT (SELECT COUNT(*) FROM customer_order WHERE customer_id=?)"
        " + (SELECT COUNT(*) FROM drawing WHERE customer_id=?) AS n",
        (customer_id, customer_id)).fetchone()["n"]
    if refs:
        raise ValueError("This customer has orders or drawings — deactivate instead")
    cur = conn.execute("DELETE FROM customer WHERE id=?", (customer_id,))
    conn.commit()
    if not cur.rowcount:
        raise ValueError("Customer not found")


def add_contact(conn, customer_id: int, data: dict) -> dict:
    if not _s(data.get("name")):
        raise ValueError("Contact name is required")
    conn.execute(
        "INSERT INTO customer_contact (customer_id, name, phone, email, role)"
        " VALUES (?,?,?,?,?)",
        (customer_id, _s(data.get("name")), _s(data.get("phone")),
         _s(data.get("email")), _s(data.get("role"))))
    conn.commit()
    return get_customer(conn, customer_id)


def delete_contact(conn, contact_id: int) -> dict:
    row = conn.execute("SELECT customer_id FROM customer_contact WHERE id=?",
                       (contact_id,)).fetchone()
    if not row:
        raise ValueError("Contact not found")
    conn.execute("DELETE FROM customer_contact WHERE id=?", (contact_id,))
    conn.commit()
    return get_customer(conn, row["customer_id"])


# --------------------------------------------------------------------------- #
# Routes
# --------------------------------------------------------------------------- #
class CustomerIn(BaseModel):
    name: str
    gstin: str = ""
    address_billing: str = ""
    address_shipping: str = ""
    payment_terms: str = ""
    notes: str = ""


class ContactIn(BaseModel):
    name: str
    phone: str = ""
    email: str = ""
    role: str = ""


def _400(fn, *args, **kw):
    try:
        return fn(*args, **kw)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("")
def customers(q: str = "", active_only: bool = True, conn=Depends(get_db)):
    return list_customers(conn, q=q, active_only=active_only)


@router.post("")
def create(body: CustomerIn, conn=Depends(get_db)):
    cid = _400(save_customer, conn, body.model_dump())
    return get_customer(conn, cid)


@router.get("/{customer_id}")
def detail(customer_id: int, conn=Depends(get_db)):
    try:
        return get_customer(conn, customer_id)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.put("/{customer_id}")
def update(customer_id: int, body: CustomerIn, conn=Depends(get_db)):
    _400(save_customer, conn, body.model_dump(), customer_id)
    return get_customer(conn, customer_id)


@router.post("/{customer_id}/active")
def set_active(customer_id: int, active: bool, conn=Depends(get_db)):
    conn.execute("UPDATE customer SET active=? WHERE id=?", (int(active), customer_id))
    conn.commit()
    return get_customer(conn, customer_id)


@router.delete("/{customer_id}")
def remove(customer_id: int, conn=Depends(get_db)):
    _400(delete_customer, conn, customer_id)
    return {"ok": True}


@router.post("/{customer_id}/contacts")
def contact_add(customer_id: int, body: ContactIn, conn=Depends(get_db)):
    return _400(add_contact, conn, customer_id, body.model_dump())


@router.delete("/contacts/{contact_id}")
def contact_delete(contact_id: int, conn=Depends(get_db)):
    return _400(delete_contact, conn, contact_id)
