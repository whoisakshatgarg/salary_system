"""Customers — the master that Orders, Parts & Pricing, quotations, invoices
and consignments reference.

Each customer gets a short CODE the office can say out loud: an abbreviation of
the name plus a serial within that abbreviation — Acme Castings -> AC01, and
the next AC… customer becomes AC02. The abbreviation can be overridden when the
automatic one reads badly; the serial is always assigned by the app.

The customer record also answers "how much business have we done with them":
order-by-order history, a monthly series for the growth chart, lifetime totals,
and their quotations/invoices for download.

Deactivation instead of deletion once referenced (orders/drawings keep their
history); a customer with no references can be deleted outright.
"""

from __future__ import annotations

import re
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from ..core.db import row_to_dict
from ..core.deps import get_db, require_module

router = APIRouter(prefix="/api/customers",
                   dependencies=[Depends(require_module("customers"))])


def _s(v) -> str:
    return (v or "").strip()


# Words that say nothing about WHICH customer this is.
_NOISE = {"pvt", "private", "ltd", "limited", "llp", "inc", "co", "company",
          "corp", "corporation", "and", "the"}
# 'M/s Tata Steel' is Messrs-Tata-Steel: the prefix must go BEFORE tokenising,
# or it splits into M + s and steals the initials.
_PREFIX = re.compile(r"^\s*(m\s*/\s*s|messrs)\b[.\s]*", re.I)


def abbreviate(name: str) -> str:
    """'Bharat Hydraulics Pvt Ltd' -> 'BH'; 'Acme' -> 'AC'; falls back to 'XX'."""
    words = [w for w in re.split(r"[^A-Za-z0-9]+", _PREFIX.sub("", _s(name))) if w]
    words = [w for w in words if w.lower() not in _NOISE] or words
    if not words:
        return "XX"
    if len(words) == 1:
        return (words[0][:2] or "XX").upper()
    return (words[0][:1] + words[1][:1]).upper()


def next_code(conn, name: str, abbr: str = "") -> str:
    """Abbreviation + the next free serial for that abbreviation (AC01, AC02…).

    Called inside the caller's transaction so two customers can't take the
    same code; customer.code is UNIQUE as the backstop.
    """
    prefix = re.sub(r"[^A-Z0-9]", "", _s(abbr).upper()) or abbreviate(name)
    used = []
    for r in conn.execute("SELECT code FROM customer WHERE code LIKE ?", (prefix + "%",)):
        m = re.fullmatch(re.escape(prefix) + r"(\d+)", r["code"] or "")
        if m:
            used.append(int(m.group(1)))
    return f"{prefix}{max(used) + 1 if used else 1:02d}"


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
        sql += " AND (c.name LIKE ? OR c.gstin LIKE ? OR c.code LIKE ?)"
        args += [like, like, like]
    sql += """ ORDER BY c.name COLLATE NOCASE"""
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
    fields = (name, _s(data.get("gstin")).upper(), _s(data.get("address_billing")),
              _s(data.get("address_shipping")), _s(data.get("payment_terms")),
              _s(data.get("notes")))
    # BEGIN IMMEDIATE: the duplicate check and the code allocation must be
    # atomic, or two customers added at once could claim the same code.
    conn.execute("BEGIN IMMEDIATE")
    try:
        dup = conn.execute("SELECT id FROM customer WHERE name=?", (name,)).fetchone()
        if dup and dup["id"] != customer_id:
            raise ValueError(f"'{name}' already exists")
        if customer_id is None:
            code = next_code(conn, name, _s(data.get("abbr")))
            cur = conn.execute(
                """INSERT INTO customer (code, name, gstin, address_billing,
                     address_shipping, payment_terms, notes, active, created_at)
                   VALUES (?,?,?,?,?,?,?,1,?)""",
                (code, *fields, datetime.now().isoformat(timespec="seconds")))
            customer_id = cur.lastrowid
        else:
            conn.execute(
                """UPDATE customer SET name=?, gstin=?, address_billing=?,
                     address_shipping=?, payment_terms=?, notes=? WHERE id=?""",
                (*fields, customer_id))
        conn.commit()
    except BaseException:
        conn.rollback()
        raise
    return customer_id


def backfill_codes(conn) -> int:
    """Give a code to customers created before codes existed (idempotent)."""
    rows = conn.execute(
        "SELECT id, name FROM customer WHERE code IS NULL OR code='' ORDER BY id").fetchall()
    for r in rows:
        conn.execute("UPDATE customer SET code=? WHERE id=?",
                     (next_code(conn, r["name"]), r["id"]))
    conn.commit()
    return len(rows)


def business(conn, customer_id: int) -> dict:
    """Everything the 'how are we doing with them' view needs: order-by-order
    history, a monthly series for the chart, lifetime totals, and their
    quotations/invoices."""
    get_customer(conn, customer_id)   # 404s if unknown
    orders = [dict(r) for r in conn.execute(
        """SELECT o.id, o.order_no, o.order_date, o.stage, o.customer_po,
                  (SELECT COALESCE(SUM(i.qty * i.rate), 0) FROM order_item i
                     WHERE i.order_id = o.id) AS amount,
                  (SELECT COUNT(*) FROM order_item i WHERE i.order_id = o.id) AS items
           FROM customer_order o WHERE o.customer_id=?
           ORDER BY o.order_date DESC, o.id DESC""", (customer_id,))]
    monthly: dict[str, dict] = {}
    for o in orders:
        m = o["order_date"][:7]
        cell = monthly.setdefault(m, {"month": m, "amount": 0.0, "orders": 0})
        cell["amount"] += o["amount"]
        cell["orders"] += 1
    series = sorted(monthly.values(), key=lambda x: x["month"])
    running = 0.0
    for s in series:
        s["amount"] = round(s["amount"], 2)
        running += s["amount"]
        s["cumulative"] = round(running, 2)
    docs = [dict(r) for r in conn.execute(
        """SELECT id, kind, doc_no, doc_date, status,
                  (SELECT COALESCE(SUM(l.qty * l.rate), 0) FROM document_line l
                     WHERE l.document_id = d.id) AS subtotal, tax_pct
           FROM document d WHERE d.customer_id=?
           ORDER BY d.doc_date DESC, d.id DESC""", (customer_id,))]
    for d in docs:
        d["total"] = round(d["subtotal"] * (1 + (d["tax_pct"] or 0) / 100), 2)
    total = round(sum(o["amount"] for o in orders), 2)
    return {
        "orders": orders,
        "series": series,
        "documents": docs,
        "stats": {
            "total_business": total,
            "order_count": len(orders),
            "avg_order": round(total / len(orders), 2) if orders else 0,
            "first_order": orders[-1]["order_date"] if orders else None,
            "last_order": orders[0]["order_date"] if orders else None,
            "quotations": sum(1 for d in docs if d["kind"] == "quotation"),
            "invoices": sum(1 for d in docs if d["kind"] == "invoice"),
            "open_value": round(sum(o["amount"] for o in orders
                                    if o["stage"] not in ("payment",)), 2),
        },
    }


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
    abbr: str = ""          # optional override for the auto abbreviation
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


@router.get("/{customer_id}/business")
def customer_business(customer_id: int, conn=Depends(get_db)):
    try:
        return business(conn, customer_id)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


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
