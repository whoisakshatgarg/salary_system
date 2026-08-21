"""Customers — the master that Orders, Parts & Pricing, quotations, invoices
and consignments reference.

Each customer gets the CLIENT CODE their documents are numbered with: the
first letter of the name plus a serial within that letter — Thermosense ->
T04, East Coast Sensors -> E01 (CONVENTIONS §2). It is the company's own
scheme, printed on quotations and acknowledgements, so it is editable: the
letter can be overridden, the whole code can be typed in, and the serial is
assigned by the app whenever the field is left blank.

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
# The document scheme: one letter, two digits. Three digits is the same scheme
# overflowing past the 99th customer under one letter — still conforming, or
# recode_legacy_codes would rewrite it on every start.
_CODE_RE = re.compile(r"^[A-Z][0-9]{2,}$")


def abbreviate(name: str) -> str:
    """'Bharat Hydraulics Pvt Ltd' -> 'B'; 'M/s Tata Steel' -> 'T'; else 'X'."""
    words = [w for w in re.split(r"[^A-Za-z0-9]+", _PREFIX.sub("", _s(name))) if w]
    words = [w for w in words if w.lower() not in _NOISE] or words
    for w in words:
        for ch in w:
            if ch.isalpha():
                return ch.upper()      # '3M Company' still gets a letter
    return "X"


def clean_code(v) -> str:
    """A typed code as it will be stored: letters and digits, upper case."""
    return re.sub(r"[^A-Z0-9]", "", _s(v).upper())


def next_code(conn, name: str, abbr: str = "") -> str:
    """One letter + the next free serial for that letter (T01, T02…).

    Called inside the caller's transaction so two customers can't take the
    same code; customer.code is UNIQUE as the backstop.
    """
    prefix = re.sub(r"[^A-Z]", "", _s(abbr).upper())[:1] or abbreviate(name)
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
    row["operation_rates"] = list(operation_rates(conn, customer_id).values())
    return row


def save_customer(conn, data: dict, customer_id: int | None = None) -> int:
    name = _s(data.get("name"))
    if not name:
        raise ValueError("Customer name is required")
    fields = (name, _s(data.get("gstin")).upper(), _s(data.get("address_billing")),
              _s(data.get("address_shipping")), _s(data.get("country")),
              _s(data.get("payment_terms")), _s(data.get("notes")))
    # An abbreviation that is already a whole code ('T04') is one: the field is
    # labelled "Customer code", so a typed code is taken at its word.
    typed = clean_code(data.get("code"))
    abbr = clean_code(data.get("abbr"))
    if not typed and _CODE_RE.fullmatch(abbr):
        typed, abbr = abbr, ""
    # BEGIN IMMEDIATE: the duplicate check and the code allocation must be
    # atomic, or two customers added at once could claim the same code.
    conn.execute("BEGIN IMMEDIATE")
    try:
        dup = conn.execute("SELECT id FROM customer WHERE name=?", (name,)).fetchone()
        if dup and dup["id"] != customer_id:
            raise ValueError(f"'{name}' already exists")
        if typed:
            clash = conn.execute("SELECT id, name FROM customer WHERE code=?",
                                 (typed,)).fetchone()
            if clash and clash["id"] != customer_id:
                raise ValueError(f"Code {typed} already belongs to {clash['name']}")
        if customer_id is None:
            code = typed or next_code(conn, name, abbr)
            cur = conn.execute(
                """INSERT INTO customer (code, name, gstin, address_billing,
                     address_shipping, country, payment_terms, notes, active,
                     created_at)
                   VALUES (?,?,?,?,?,?,?,?,1,?)""",
                (code, *fields, datetime.now().isoformat(timespec="seconds")))
            customer_id = cur.lastrowid
        else:
            # Blank means "leave it alone" for a customer that already has a
            # code — their quotations are numbered under it, so it is never
            # silently reissued — and "assign one" for a customer without.
            row = conn.execute("SELECT code FROM customer WHERE id=?",
                               (customer_id,)).fetchone()
            if not row:
                raise ValueError("Customer not found")
            code = typed or _s(row["code"]) or next_code(conn, name, abbr)
            conn.execute(
                """UPDATE customer SET name=?, gstin=?, address_billing=?,
                     address_shipping=?, country=?, payment_terms=?, notes=?,
                     code=? WHERE id=?""",
                (*fields, code, customer_id))
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


def recode_legacy_codes(conn) -> int:
    """Move customers off the old two-letter scheme (AC01) onto the document
    scheme (A01), oldest customer first.

    A one-off: a code that already conforms is never touched, so this is
    idempotent and safe to run on every start. Collisions can't happen —
    next_code reads the codes already assigned, including the ones this loop
    has just written.
    """
    n = 0
    for r in conn.execute("SELECT id, name, code FROM customer ORDER BY id").fetchall():
        if _CODE_RE.fullmatch(_s(r["code"])):
            continue
        conn.execute("UPDATE customer SET code=? WHERE id=?",
                     (next_code(conn, r["name"]), r["id"]))
        n += 1
    conn.commit()
    return n


# --------------------------------------------------------------------------- #
# Per-customer operation rates — what WE charge THEM for an operation
# --------------------------------------------------------------------------- #
def operation_rates(conn, customer_id: int) -> dict:
    """{operation: {rate_per_hour, extra_rate, note}} for one customer."""
    return {r["operation"]: dict(r) for r in conn.execute(
        "SELECT operation, rate_per_hour, extra_rate, note"
        " FROM customer_operation_rate WHERE customer_id=?"
        " ORDER BY operation COLLATE NOCASE", (customer_id,))}


def set_operation_rate(conn, customer_id: int, data: dict) -> list[dict]:
    op = _s(data.get("operation"))
    if not op:
        raise ValueError("Pick an operation")
    if not conn.execute("SELECT 1 FROM customer WHERE id=?", (customer_id,)).fetchone():
        raise ValueError("Customer not found")
    rate = _num(data.get("rate_per_hour"), "Rate")
    extra = _num(data.get("extra_rate") or 0, "Additional ₹/hour")
    conn.execute(
        """INSERT INTO customer_operation_rate (customer_id, operation, rate_per_hour,
             extra_rate, note) VALUES (?,?,?,?,?)
           ON CONFLICT(customer_id, operation) DO UPDATE SET
             rate_per_hour=excluded.rate_per_hour, extra_rate=excluded.extra_rate,
             note=excluded.note""",
        (customer_id, op, rate, extra, _s(data.get("note"))))
    conn.commit()
    return list(operation_rates(conn, customer_id).values())


def delete_operation_rate(conn, customer_id: int, operation: str) -> list[dict]:
    conn.execute("DELETE FROM customer_operation_rate WHERE customer_id=? AND operation=?",
                 (customer_id, operation))
    conn.commit()
    return list(operation_rates(conn, customer_id).values())


def _num(v, label: str) -> float:
    import math
    try:
        f = float(v)
    except (TypeError, ValueError):
        raise ValueError(f"{label} must be a number")
    if not math.isfinite(f) or f < 0 or f > 1e9:
        raise ValueError(f"{label} must be a normal, non-negative number")
    return round(f, 2)


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
        """SELECT id, kind, doc_no, doc_date, status, order_id,
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
        "INSERT INTO customer_contact (customer_id, name, phone, email, fax, role)"
        " VALUES (?,?,?,?,?,?)",
        (customer_id, _s(data.get("name")), _s(data.get("phone")),
         _s(data.get("email")), _s(data.get("fax")), _s(data.get("role"))))
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
    code: str = ""          # the client code itself; blank = assign one
    abbr: str = ""          # or just the letter to number under
    gstin: str = ""
    address_billing: str = ""
    address_shipping: str = ""
    country: str = ""       # printed on the quotation; picks the currency
    payment_terms: str = ""
    notes: str = ""


class OperationRateIn(BaseModel):
    operation: str
    rate_per_hour: float
    extra_rate: float = 0
    note: str = ""


class ContactIn(BaseModel):
    name: str
    phone: str = ""
    email: str = ""
    fax: str = ""           # the ack's CONTACTS block prints one
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


@router.get("/refs")
def refs(conn=Depends(get_db)):
    """Reference data for this module's forms (customers grant): the standard
    operation list, so a customer rate can be set against a known operation."""
    from . import settings as settings_mod
    return {"operations": settings_mod.operations(conn)}


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


@router.get("/{customer_id}/operation-rates")
def op_rates(customer_id: int, conn=Depends(get_db)):
    return list(operation_rates(conn, customer_id).values())


@router.post("/{customer_id}/operation-rates")
def op_rate_set(customer_id: int, body: OperationRateIn, conn=Depends(get_db)):
    return _400(set_operation_rate, conn, customer_id, body.model_dump())


@router.post("/{customer_id}/operation-rates/delete")
def op_rate_delete(customer_id: int, body: OperationRateIn, conn=Depends(get_db)):
    return _400(delete_operation_rate, conn, customer_id, body.operation)


@router.post("/{customer_id}/contacts")
def contact_add(customer_id: int, body: ContactIn, conn=Depends(get_db)):
    return _400(add_contact, conn, customer_id, body.model_dump())


@router.delete("/contacts/{contact_id}")
def contact_delete(contact_id: int, conn=Depends(get_db)):
    return _400(delete_contact, conn, contact_id)
