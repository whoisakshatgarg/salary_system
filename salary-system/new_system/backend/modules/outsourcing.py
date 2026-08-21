"""Outsourcing — the work that leaves the shop, and the stock that comes back.

Four nouns, in the order the office meets them (SOP-DESIGN §9):

    vendor      who does it, code 'V01'+ from core/numbering
    os_order    the outgoing job order, 'AT/OS/26-27/001', with a DEADLINE
    os_receipt  what came back, inspected and accepted (or not)
    os_item     the resulting bought-out stock, 'OS-0001', with its movements

Outsourced stock is deliberately its OWN world rather than a heat: it has no
heat number and no chemistry, so filing it under the raw-material register
would be a table of empty columns. It surfaces in `/api/material/search`
flagged ``source:"outsourced"`` so a costing or a quotation can still pick it.

Two rules worth knowing before reading the code:

* **Status is derived, not typed.** open → partial → received follows the
  quantities received; only closed / cancelled are set by hand, and once set
  they are never overwritten by a recount — a decision is not a derivation.
* **os_item.qty is STORED, and os_movement.qty is the SIGNED change to it**
  (+ receive, − issue, ± adjust), so the movements always sum to the stock on
  hand. Heats do it the other way round — their stock is derived — because a
  heat can never be adjusted, only consumed.

Every data function takes an open sqlite3 connection and raises ``ValueError``
for a user mistake; the router turns those into HTTP 400. Numbers are consumed
inside the same BEGIN IMMEDIATE that writes the row that owns them, so a save
that rolls back never burns one.
"""

from __future__ import annotations

import math
import re
import secrets
from datetime import date, datetime
from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel

from ..core import numbering, paths
from ..core.attachments import (header_filename, response_mime, storage_ext,
                                validate_attachment)
from ..core.db import row_to_dict
from ..core.deps import get_db, require_module
from . import settings as settings_mod

router = APIRouter(prefix="/api/outsourcing",
                   dependencies=[Depends(require_module("outsourcing"))])

OS_STATUSES = ["open", "partial", "received", "closed", "cancelled"]
STATUS_LABELS = {
    "open": "Open", "partial": "Part received", "received": "Received",
    "closed": "Closed", "cancelled": "Cancelled",
}
# The two an operator sets; the other three follow the receipts.
MANUAL_STATUSES = ("closed", "cancelled")

MOVEMENT_TYPES = ("receive", "issue", "adjust")

# Suggestions for the free-text Purpose field — a datalist, never a constraint:
# the next job that goes out will be something nobody listed here.
PURPOSES = ["Plating", "Heat treatment", "Machining", "Grinding", "Turning",
            "Casting", "Forging", "Painting", "Anodising", "Powder coating",
            "Laser cutting", "Assembly", "Bought-out part"]


def _s(v) -> str:
    return (v or "").strip()


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _clean_code(v) -> str:
    """A typed code as it will be stored: letters and digits, upper case."""
    return re.sub(r"[^A-Z0-9]", "", _s(v).upper())


def _check_date(v, label: str, required: bool = False) -> str | None:
    v = _s(v)
    if not v:
        if required:
            return date.today().isoformat()
        return None
    try:
        date.fromisoformat(v)
    except ValueError:
        raise ValueError(f"{label} isn't a real date (YYYY-MM-DD)")
    return v


def _check_qty(v, label: str) -> float:
    try:
        f = float(v)
    except (TypeError, ValueError):
        raise ValueError(f"{label} must be a number")
    if not math.isfinite(f) or f <= 0 or f > 1e12:
        raise ValueError(f"{label} must be a normal positive number")
    return f


def _check_optional_money(v, label: str):
    if v in (None, ""):
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        raise ValueError(f"{label} must be a number")
    if not math.isfinite(f) or f < 0 or f > 1e12:
        raise ValueError(f"{label} must be a normal, non-negative number")
    return f


def _days_left(deadline, ref: date):
    try:
        return (date.fromisoformat(_s(deadline)) - ref).days
    except (TypeError, ValueError):
        return None                    # a malformed date is not a deadline


# --------------------------------------------------------------------------- #
# Vendors
# --------------------------------------------------------------------------- #
def list_vendors(conn, q: str = "", active_only: bool = True) -> list[dict]:
    sql = """SELECT v.*,
                    (SELECT COUNT(*) FROM os_order o WHERE o.vendor_id=v.id) AS jobs,
                    (SELECT COUNT(*) FROM os_order o WHERE o.vendor_id=v.id
                       AND o.status IN ('open','partial')) AS open_jobs,
                    (SELECT COUNT(*) FROM os_item i WHERE i.vendor_id=v.id
                       AND i.active=1) AS stock_items,
                    (SELECT COUNT(*) FROM os_document d WHERE d.vendor_id=v.id) AS documents
             FROM vendor v WHERE 1=1"""
    args: list = []
    if active_only:
        sql += " AND v.active=1"
    if _s(q):
        like = f"%{_s(q)}%"
        sql += (" AND (v.name LIKE ? OR v.code LIKE ? OR v.services LIKE ?"
                " OR v.contact_name LIKE ? OR v.phone LIKE ? OR v.email LIKE ?)")
        args += [like] * 6
    sql += " ORDER BY v.name COLLATE NOCASE"
    return [dict(r) for r in conn.execute(sql, args)]


def save_vendor(conn, data: dict, vendor_id: int | None = None) -> int:
    """Create or update a vendor. A blank code is assigned by the app; a typed
    one is taken at its word, so a code can be corrected to whatever the office
    already writes on the paperwork. Editing with the field left blank KEEPS
    the existing code — blank means "you decide", not "clear it"."""
    name = _s(data.get("name"))
    if not name:
        raise ValueError("Vendor name is required")
    # The field is labelled "Vendor code", so whatever is typed IS the code —
    # the office may already write something the V01 series would never produce.
    typed = _clean_code(data.get("code"))

    # BEGIN IMMEDIATE: the duplicate check and the code allocation must be
    # atomic, or two vendors added at once could claim the same code.
    conn.execute("BEGIN IMMEDIATE")
    try:
        dup = conn.execute("SELECT id FROM vendor WHERE name=? COLLATE NOCASE",
                           (name,)).fetchone()
        if dup and dup["id"] != vendor_id:
            raise ValueError(f"'{name}' is already a vendor")
        if typed:
            clash = conn.execute("SELECT id, name FROM vendor WHERE code=?",
                                 (typed,)).fetchone()
            if clash and clash["id"] != vendor_id:
                raise ValueError(f"Code {typed} already belongs to {clash['name']}")
        fields = (name, _s(data.get("contact_name")), _s(data.get("phone")),
                  _s(data.get("email")), _s(data.get("address")),
                  _s(data.get("services")), _s(data.get("notes")))
        if vendor_id is None:
            code = typed or numbering.vendor_code(conn)
            cur = conn.execute(
                "INSERT INTO vendor (code, name, contact_name, phone, email, address,"
                " services, notes, active, created_at) VALUES (?,?,?,?,?,?,?,?,1,?)",
                (code, *fields, _now()))
            vendor_id = cur.lastrowid
        else:
            if not conn.execute("SELECT 1 FROM vendor WHERE id=?", (vendor_id,)).fetchone():
                raise ValueError("Vendor not found")
            if typed:
                conn.execute(
                    "UPDATE vendor SET code=?, name=?, contact_name=?, phone=?, email=?,"
                    " address=?, services=?, notes=? WHERE id=?",
                    (typed, *fields, vendor_id))
            else:
                conn.execute(
                    "UPDATE vendor SET name=?, contact_name=?, phone=?, email=?,"
                    " address=?, services=?, notes=? WHERE id=?",
                    (*fields, vendor_id))
        conn.commit()
    except BaseException:
        conn.rollback()
        raise
    return vendor_id


def set_vendor_active(conn, vendor_id: int, active: bool) -> dict:
    cur = conn.execute("UPDATE vendor SET active=? WHERE id=?",
                       (1 if active else 0, vendor_id))
    conn.commit()
    if not cur.rowcount:
        raise ValueError("Vendor not found")
    return get_vendor(conn, vendor_id)


def get_vendor(conn, vendor_id: int) -> dict:
    """The vendor record: profile plus the whole history with them."""
    v = row_to_dict(conn.execute("SELECT * FROM vendor WHERE id=?",
                                 (vendor_id,)).fetchone())
    if not v:
        raise ValueError("Vendor not found")
    today = date.today()
    v["orders"] = []
    for r in conn.execute(
        """SELECT o.*,
                  (SELECT COALESCE(SUM(i.qty),0) FROM os_order_item i
                     WHERE i.os_order_id=o.id) AS qty_total,
                  (SELECT COALESCE(SUM(l.qty),0) FROM os_receipt_line l
                     JOIN os_receipt r ON r.id=l.os_receipt_id
                     WHERE r.os_order_id=o.id) AS qty_received,
                  (SELECT COUNT(*) FROM os_receipt r WHERE r.os_order_id=o.id) AS receipts
           FROM os_order o WHERE o.vendor_id=? ORDER BY o.id DESC""", (vendor_id,)
    ):
        v["orders"].append(_decorate_order(dict(r), today))
    v["receipts"] = sum(o["receipts"] for o in v["orders"])
    v["stock"] = [dict(r) for r in conn.execute(
        "SELECT i.id, i.os_id, i.description, i.part_code, i.material, i.size_section,"
        " i.qty, i.unit, i.unit_cost, i.active FROM os_item i"
        " WHERE i.vendor_id=? ORDER BY i.id DESC", (vendor_id,))]
    v["documents"] = list_os_documents(conn, vendor_id=vendor_id)
    return v


# --------------------------------------------------------------------------- #
# Outgoing job orders
# --------------------------------------------------------------------------- #
def _validate_os_items(items: list[dict]) -> list[dict]:
    if not items:
        raise ValueError("An outgoing order needs at least one line")
    out = []
    for i, it in enumerate(items, 1):
        if not _s(it.get("description")) and not _s(it.get("part_code")):
            raise ValueError(f"Line {i}: type a description or a part code")
        out.append({
            "description": _s(it.get("description")),
            "part_code": _s(it.get("part_code")),
            "qty": _check_qty(it.get("qty"), f"Line {i} quantity"),
            "unit": _s(it.get("unit")) or "Nos",
            "unit_cost": _check_optional_money(it.get("unit_cost"), f"Line {i} rate"),
            "order_item_id": it.get("order_item_id") or None,
        })
    return out


def _check_os_refs(conn, vendor_id, order_id, items: list[dict]) -> None:
    """Turn dangling ids into 400s instead of raw FK IntegrityError 500s."""
    if not conn.execute("SELECT 1 FROM vendor WHERE id=?", (vendor_id,)).fetchone():
        raise ValueError("That vendor no longer exists — reload the page")
    if order_id and not conn.execute(
            "SELECT 1 FROM customer_order WHERE id=?", (order_id,)).fetchone():
        raise ValueError("That order no longer exists — reload the page")
    for i, it in enumerate(items, 1):
        if not it["order_item_id"]:
            continue
        # A line can only name a part of the order this job order is FOR:
        # outsourcing half of order 12 to a vendor booked against order 9 would
        # put the received quantity on the wrong job.
        if not order_id:
            raise ValueError(
                f"Line {i}: link this job order to the internal order first, "
                "then pick its parts")
        row = conn.execute("SELECT order_id FROM order_item WHERE id=?",
                           (it["order_item_id"],)).fetchone()
        if not row or row["order_id"] != order_id:
            raise ValueError(f"Line {i}: that part isn't on the linked order — reload the page")


def _received_by_item(conn, os_order_id: int) -> dict[int, float]:
    return {r["os_order_item_id"]: r["qty"] for r in conn.execute(
        """SELECT l.os_order_item_id, COALESCE(SUM(l.qty),0) AS qty
           FROM os_receipt_line l JOIN os_receipt r ON r.id=l.os_receipt_id
           WHERE r.os_order_id=? AND l.os_order_item_id IS NOT NULL
           GROUP BY l.os_order_item_id""", (os_order_id,))}


def _recompute_status(conn, os_order_id: int) -> str:
    """open → partial → received, from what has actually come back.

    Called inside the caller's transaction and never commits. Closed and
    cancelled are left alone: those were somebody's decision, and a recount
    must not quietly reopen an order the office has written off.
    """
    row = conn.execute("SELECT status FROM os_order WHERE id=?", (os_order_id,)).fetchone()
    if not row:
        return ""
    if row["status"] in MANUAL_STATUSES:
        return row["status"]
    ordered = conn.execute(
        "SELECT COALESCE(SUM(qty),0) AS q FROM os_order_item WHERE os_order_id=?",
        (os_order_id,)).fetchone()["q"]
    got = conn.execute(
        """SELECT COALESCE(SUM(l.qty),0) AS q FROM os_receipt_line l
           JOIN os_receipt r ON r.id=l.os_receipt_id WHERE r.os_order_id=?""",
        (os_order_id,)).fetchone()["q"]
    if got <= 0:
        status = "open"
    elif ordered > 0 and got + 1e-9 >= ordered:
        status = "received"
    else:
        status = "partial"
    conn.execute("UPDATE os_order SET status=? WHERE id=?", (status, os_order_id))
    return status


def create_os_order(conn, data: dict) -> int:
    if not data.get("vendor_id"):
        raise ValueError("Pick a vendor")
    date_sent = _check_date(data.get("date_sent"), "Date sent", required=True)
    deadline = _check_date(data.get("deadline"), "Deadline")
    items = _validate_os_items(data.get("items") or [])
    order_id = data.get("order_id") or None

    # The OS number is taken inside the same lock that writes the row, so a
    # double-clicked Save never burns 'AT/OS/26-27/002' on a rolled-back order.
    conn.execute("BEGIN IMMEDIATE")
    try:
        _check_os_refs(conn, data["vendor_id"], order_id, items)
        os_no = numbering.os_po_no(conn, date.fromisoformat(date_sent))
        cur = conn.execute(
            """INSERT INTO os_order (os_no, vendor_id, order_id, purpose, date_sent,
                 deadline, status, notes, created_at) VALUES (?,?,?,?,?,?,'open',?,?)""",
            (os_no, data["vendor_id"], order_id, _s(data.get("purpose")), date_sent,
             deadline, _s(data.get("notes")), _now()))
        os_order_id = cur.lastrowid
        for it in items:
            conn.execute(
                "INSERT INTO os_order_item (os_order_id, description, part_code, qty,"
                " unit, unit_cost, order_item_id) VALUES (?,?,?,?,?,?,?)",
                (os_order_id, it["description"], it["part_code"], it["qty"],
                 it["unit"], it["unit_cost"], it["order_item_id"]))
        conn.commit()
    except BaseException:
        conn.rollback()
        raise
    return os_order_id


def update_os_order(conn, os_order_id: int, data: dict) -> None:
    """Header + lines (replace-all, like the order editor). A line that has
    already been received can neither vanish nor shrink below what came back."""
    if not conn.execute("SELECT 1 FROM os_order WHERE id=?", (os_order_id,)).fetchone():
        raise ValueError("Outgoing order not found")
    if not data.get("vendor_id"):
        raise ValueError("Pick a vendor")
    date_sent = _check_date(data.get("date_sent"), "Date sent", required=True)
    deadline = _check_date(data.get("deadline"), "Deadline")
    items = _validate_os_items(data.get("items") or [])
    order_id = data.get("order_id") or None

    conn.execute("BEGIN IMMEDIATE")
    try:
        # inside the lock: a receipt can't commit between check and write
        received = _received_by_item(conn, os_order_id)
        _check_os_refs(conn, data["vendor_id"], order_id, items)
        conn.execute(
            """UPDATE os_order SET vendor_id=?, order_id=?, purpose=?, date_sent=?,
                 deadline=?, notes=? WHERE id=?""",
            (data["vendor_id"], order_id, _s(data.get("purpose")), date_sent,
             deadline, _s(data.get("notes")), os_order_id))
        existing = {r["id"]: dict(r) for r in conn.execute(
            "SELECT * FROM os_order_item WHERE os_order_id=?", (os_order_id,))}
        sent_ids = {int(i["id"]) for i in (data.get("items") or []) if i.get("id")}
        for old_id in existing:
            if received.get(old_id, 0) > 0 and old_id not in sent_ids:
                raise ValueError("A line that has already been received can't be removed")
            if old_id not in sent_ids:
                conn.execute("DELETE FROM os_order_item WHERE id=?", (old_id,))
        for raw, it in zip(data.get("items") or [], items):
            iid = raw.get("id")
            if iid and int(iid) in existing:
                if it["qty"] < received.get(int(iid), 0):
                    raise ValueError(
                        f"Line quantity can't go below the "
                        f"{received[int(iid)]:g} already received")
                conn.execute(
                    "UPDATE os_order_item SET description=?, part_code=?, qty=?, unit=?,"
                    " unit_cost=?, order_item_id=? WHERE id=?",
                    (it["description"], it["part_code"], it["qty"], it["unit"],
                     it["unit_cost"], it["order_item_id"], int(iid)))
            else:
                conn.execute(
                    "INSERT INTO os_order_item (os_order_id, description, part_code, qty,"
                    " unit, unit_cost, order_item_id) VALUES (?,?,?,?,?,?,?)",
                    (os_order_id, it["description"], it["part_code"], it["qty"],
                     it["unit"], it["unit_cost"], it["order_item_id"]))
        _recompute_status(conn, os_order_id)     # the ordered total just moved
        conn.commit()
    except BaseException:
        conn.rollback()
        raise


def set_os_status(conn, os_order_id: int, status: str) -> dict:
    """Close or cancel a job order by hand. The other three states are counted,
    not chosen, so asking for one of them is a mistake worth naming."""
    status = _s(status)
    if status == "reopen":
        # Plausible mistake, unhelpful "Unknown status": reopening is a verb
        # whose RESULT is derived, so it is its own action (see reopen_os_order).
        raise ValueError("Reopening a closed or cancelled job is its own action, "
                         "not a status")
    if status not in OS_STATUSES:
        raise ValueError("Unknown status")
    if status not in MANUAL_STATUSES:
        raise ValueError(
            "Open, part received and received follow the quantities received — "
            "record a receipt instead")
    cur = conn.execute("UPDATE os_order SET status=? WHERE id=?", (status, os_order_id))
    conn.commit()
    if not cur.rowcount:
        raise ValueError("Outgoing order not found")
    return get_os_order(conn, os_order_id)


def reopen_os_order(conn, os_order_id: int) -> dict:
    """Take back a Close or a Cancel.

    Terminal states are the only ones a person sets, so they are the only ones
    a person can take back — and what the job becomes is not chosen either: it
    re-derives from what has actually come back, landing exactly where a
    receipt would have left it.
    """
    conn.execute("BEGIN IMMEDIATE")      # check + re-derive atomically
    try:
        row = conn.execute("SELECT status FROM os_order WHERE id=?",
                           (os_order_id,)).fetchone()
        if not row:
            raise ValueError("Outgoing order not found")
        if row["status"] not in MANUAL_STATUSES:
            raise ValueError(
                "Only a closed or cancelled job can be reopened — this one is "
                "already following the quantities received")
        # Step out of the terminal state FIRST: _recompute_status deliberately
        # refuses to overwrite a decision, so it would leave it exactly as is.
        conn.execute("UPDATE os_order SET status='open' WHERE id=?", (os_order_id,))
        _recompute_status(conn, os_order_id)
        conn.commit()
    except BaseException:
        conn.rollback()
        raise
    return get_os_order(conn, os_order_id)


def _decorate_order(o: dict, today: date) -> dict:
    o["qty_total"] = round(o.get("qty_total") or 0, 3)
    o["qty_received"] = round(o.get("qty_received") or 0, 3)
    o["qty_pending"] = round(max(o["qty_total"] - o["qty_received"], 0), 3)
    o["pct_received"] = (round(o["qty_received"] / o["qty_total"] * 100)
                         if o["qty_total"] else 0)
    o["days_left"] = _days_left(o.get("deadline"), today)
    o["status_label"] = STATUS_LABELS.get(o.get("status"), o.get("status"))
    return o


def list_os_orders(conn, q: str = "", status: str = "", vendor_id=None) -> dict:
    sql = """SELECT o.*, v.name AS vendor_name, v.code AS vendor_code,
                    co.order_no AS order_no,
                    (SELECT COUNT(*) FROM os_order_item i WHERE i.os_order_id=o.id) AS items,
                    (SELECT COALESCE(SUM(i.qty),0) FROM os_order_item i
                       WHERE i.os_order_id=o.id) AS qty_total,
                    (SELECT COALESCE(SUM(i.qty * COALESCE(i.unit_cost,0)),0)
                       FROM os_order_item i WHERE i.os_order_id=o.id) AS value,
                    (SELECT COALESCE(SUM(l.qty),0) FROM os_receipt_line l
                       JOIN os_receipt r ON r.id=l.os_receipt_id
                       WHERE r.os_order_id=o.id) AS qty_received,
                    (SELECT COUNT(*) FROM os_receipt r WHERE r.os_order_id=o.id) AS receipts
             FROM os_order o JOIN vendor v ON v.id=o.vendor_id
             LEFT JOIN customer_order co ON co.id=o.order_id WHERE 1=1"""
    args: list = []
    if _s(status):
        sql += " AND o.status=?"
        args.append(_s(status))
    if vendor_id:
        sql += " AND o.vendor_id=?"
        args.append(int(vendor_id))
    if _s(q):
        like = f"%{_s(q)}%"
        sql += (" AND (o.os_no LIKE ? OR o.purpose LIKE ? OR v.name LIKE ?"
                " OR co.order_no LIKE ?"
                " OR EXISTS (SELECT 1 FROM os_order_item i WHERE i.os_order_id=o.id"
                "            AND (i.description LIKE ? OR i.part_code LIKE ?)))")
        args += [like] * 6
    sql += " ORDER BY o.id DESC"
    today = date.today()
    rows = [_decorate_order(dict(r), today) for r in conn.execute(sql, args)]
    counts = {r["status"]: r["n"] for r in conn.execute(
        "SELECT status, COUNT(*) AS n FROM os_order GROUP BY status")}
    return {"rows": rows, "status_counts": counts,
            "statuses": [{"key": s, "label": STATUS_LABELS[s]} for s in OS_STATUSES]}


def get_os_order(conn, os_order_id: int) -> dict:
    o = row_to_dict(conn.execute(
        "SELECT o.*, v.name AS vendor_name, v.code AS vendor_code"
        " FROM os_order o JOIN vendor v ON v.id=o.vendor_id WHERE o.id=?",
        (os_order_id,)).fetchone())
    if not o:
        raise ValueError("Outgoing order not found")
    received = _received_by_item(conn, os_order_id)
    o["items"] = []
    for r in conn.execute(
            "SELECT i.*, oi.description AS order_item_description"
            " FROM os_order_item i LEFT JOIN order_item oi ON oi.id=i.order_item_id"
            " WHERE i.os_order_id=? ORDER BY i.id", (os_order_id,)):
        it = dict(r)
        it["received"] = round(received.get(it["id"], 0), 3)
        it["pending"] = round(max(it["qty"] - it["received"], 0), 3)
        it["pct_received"] = round(it["received"] / it["qty"] * 100) if it["qty"] else 0
        it["amount"] = round(it["qty"] * (it["unit_cost"] or 0), 2)
        o["items"].append(it)
    o["qty_total"] = round(sum(i["qty"] for i in o["items"]), 3)
    o["qty_received"] = round(sum(i["received"] for i in o["items"]), 3)
    o = _decorate_order(o, date.today())
    o["value"] = round(sum(i["amount"] for i in o["items"]), 2)
    o["receipts"] = [dict(r) for r in conn.execute(
        """SELECT r.*, (SELECT COUNT(*) FROM os_receipt_line l
                          WHERE l.os_receipt_id=r.id) AS lines,
                  (SELECT COALESCE(SUM(l.qty),0) FROM os_receipt_line l
                     WHERE l.os_receipt_id=r.id) AS qty
           FROM os_receipt r WHERE r.os_order_id=?
           ORDER BY r.receipt_date DESC, r.id DESC""", (os_order_id,))]
    o["documents"] = list_os_documents(conn, os_order_id=os_order_id)
    # the internal order this job serves, when it serves one
    o["order"] = row_to_dict(conn.execute(
        "SELECT co.id, co.order_no, c.name AS customer_name FROM customer_order co"
        " JOIN customer c ON c.id=co.customer_id WHERE co.id=?",
        (o["order_id"],)).fetchone()) if o["order_id"] else None
    o["statuses"] = [{"key": s, "label": STATUS_LABELS[s]} for s in OS_STATUSES]
    return o


def deadlines(conn, today: str = "") -> dict:
    """Job orders still out at a vendor, for the workspace panel.

    Same three buckets the Home deadline panel uses, over os_order.deadline.
    Only open and part-received jobs appear: once everything is back (or the
    order is closed or cancelled) it is not a deadline any more.
    """
    ref = date.fromisoformat(today) if today else date.today()
    rows = []
    for r in conn.execute(
        """SELECT o.id, o.os_no, o.deadline, o.status, o.purpose,
                  v.name AS vendor_name, v.code AS vendor_code,
                  (SELECT COALESCE(SUM(i.qty),0) FROM os_order_item i
                     WHERE i.os_order_id=o.id) AS qty_total,
                  (SELECT COALESCE(SUM(l.qty),0) FROM os_receipt_line l
                     JOIN os_receipt r ON r.id=l.os_receipt_id
                     WHERE r.os_order_id=o.id) AS qty_received
           FROM os_order o JOIN vendor v ON v.id=o.vendor_id
           WHERE o.deadline IS NOT NULL AND TRIM(o.deadline) <> ''
             AND o.status IN ('open','partial')
           ORDER BY o.deadline"""
    ):
        o = dict(r)
        days = _days_left(o["deadline"], ref)
        if days is None:
            continue
        o["days_left"] = days
        o["qty_total"] = round(o["qty_total"], 3)
        o["qty_received"] = round(o["qty_received"], 3)
        o["qty_pending"] = round(max(o["qty_total"] - o["qty_received"], 0), 3)
        o["status_label"] = STATUS_LABELS.get(o["status"], o["status"])
        rows.append(o)

    return {
        "overdue":    [r for r in rows if r["days_left"] < 0],
        "this_week":  [r for r in rows if 0 <= r["days_left"] <= 7],
        "this_month": [r for r in rows if 7 < r["days_left"] <= 31],
        "as_of": ref.isoformat(),
    }


# --------------------------------------------------------------------------- #
# Receipts — what came back, and the stock it became
# --------------------------------------------------------------------------- #
def _movement(conn, os_item_id: int, mv_date: str, mtype: str, qty: float,
              order_id: str = "", remarks: str = "") -> None:
    """One line in the stock ledger. qty is the SIGNED change (see module docs)."""
    conn.execute(
        "INSERT INTO os_movement (os_item_id, mv_date, type, qty, order_id, remarks,"
        " created_at) VALUES (?,?,?,?,?,?,?)",
        (os_item_id, mv_date, mtype, qty, _s(order_id) or None, _s(remarks), _now()))


def create_receipt(conn, data: dict) -> int:
    """Record a delivery back from the vendor.

    Everything happens inside one BEGIN IMMEDIATE: the over-receipt check, the
    stock top-ups, the OS IDs of any new stock rows, and the parent order's
    recomputed status either all land or none of them do.
    """
    os_order_id = data.get("os_order_id")
    if not os_order_id:
        raise ValueError("Pick the outgoing order this delivery is against")
    receipt_date = _check_date(data.get("receipt_date"), "Receipt date", required=True)

    raw_lines = data.get("lines") or []
    if not raw_lines:
        raise ValueError("Add at least one line to the receipt")
    lines = []
    for n, ln in enumerate(raw_lines, 1):
        item_id = int(ln.get("os_order_item_id") or 0)
        if not item_id:
            raise ValueError(f"Line {n}: which line of the order came back?")
        lines.append({
            "os_order_item_id": item_id,
            "qty": _check_qty(ln.get("qty"), f"Line {n} quantity"),
            "os_item_id": int(ln["os_item_id"]) if ln.get("os_item_id") else None,
            "description": _s(ln.get("description")),
            "part_code": _s(ln.get("part_code")),
            "material": _s(ln.get("material")),
            "size_section": _s(ln.get("size_section")),
            "unit": _s(ln.get("unit")),
            "unit_cost": _check_optional_money(ln.get("unit_cost"), f"Line {n} rate"),
        })
    # Duplicate rows against one order line are summed FIRST: checked one by one
    # they would each pass against the same stored total and over-receive.
    want: dict[int, float] = {}
    for ln in lines:
        want[ln["os_order_item_id"]] = want.get(ln["os_order_item_id"], 0) + ln["qty"]

    conn.execute("BEGIN IMMEDIATE")
    try:
        order = conn.execute("SELECT * FROM os_order WHERE id=?", (os_order_id,)).fetchone()
        if not order:
            raise ValueError("That outgoing order no longer exists — reload the page")
        already = _received_by_item(conn, os_order_id)
        items = {r["id"]: dict(r) for r in conn.execute(
            "SELECT * FROM os_order_item WHERE os_order_id=?", (os_order_id,))}
        for item_id, qty in want.items():
            it = items.get(item_id)
            if not it:
                raise ValueError("A line on this receipt isn't on that outgoing order")
            got = already.get(item_id, 0)
            if qty + got > it["qty"] + 1e-9:
                raise ValueError(
                    f"Only {it['qty'] - got:g} of {it['qty']:g} left to receive on that line")

        cur = conn.execute(
            "INSERT INTO os_receipt (os_order_id, receipt_date, inspection_notes,"
            " accepted, created_at) VALUES (?,?,?,?,?)",
            (os_order_id, receipt_date, _s(data.get("inspection_notes")),
             0 if data.get("accepted") is False else 1, _now()))
        receipt_id = cur.lastrowid

        for ln in lines:
            it = items[ln["os_order_item_id"]]
            os_item_id = ln["os_item_id"]
            if os_item_id:
                stock = conn.execute("SELECT os_id FROM os_item WHERE id=?",
                                     (os_item_id,)).fetchone()
                if not stock:
                    raise ValueError("That stock item no longer exists — reload the page")
                # received_date follows the newest arrival: it answers "when did
                # this last come in", which is what the stock screen is asked.
                conn.execute("UPDATE os_item SET qty=qty+?, received_date=? WHERE id=?",
                             (ln["qty"], receipt_date, os_item_id))
            else:
                os_id = numbering.os_item_id(conn)
                unit_cost = (ln["unit_cost"] if ln["unit_cost"] is not None
                             else it["unit_cost"])
                new = conn.execute(
                    "INSERT INTO os_item (os_id, description, part_code, material,"
                    " size_section, unit, qty, unit_cost, vendor_id, os_order_id,"
                    " received_date, notes, active, created_at)"
                    " VALUES (?,?,?,?,?,?,?,?,?,?,?,'',1,?)",
                    (os_id,
                     ln["description"] or it["description"] or it["part_code"] or os_id,
                     ln["part_code"] or it["part_code"], ln["material"],
                     ln["size_section"], ln["unit"] or it["unit"] or "Nos",
                     ln["qty"], unit_cost, order["vendor_id"], os_order_id,
                     receipt_date, _now()))
                os_item_id = new.lastrowid
            conn.execute(
                "INSERT INTO os_receipt_line (os_receipt_id, os_order_item_id, qty,"
                " os_item_id) VALUES (?,?,?,?)",
                (receipt_id, ln["os_order_item_id"], ln["qty"], os_item_id))
            _movement(conn, os_item_id, receipt_date, "receive", ln["qty"],
                      remarks=f"Received against {order['os_no']}")

        _recompute_status(conn, os_order_id)
        conn.commit()
    except BaseException:
        conn.rollback()
        raise
    return receipt_id


def list_receipts(conn, q: str = "", os_order_id=None) -> list[dict]:
    sql = """SELECT r.*, o.os_no, o.id AS os_order_id, v.name AS vendor_name,
                    v.code AS vendor_code,
                    (SELECT COUNT(*) FROM os_receipt_line l
                       WHERE l.os_receipt_id=r.id) AS lines,
                    (SELECT COALESCE(SUM(l.qty),0) FROM os_receipt_line l
                       WHERE l.os_receipt_id=r.id) AS qty
             FROM os_receipt r JOIN os_order o ON o.id=r.os_order_id
             JOIN vendor v ON v.id=o.vendor_id WHERE 1=1"""
    args: list = []
    if os_order_id:
        sql += " AND r.os_order_id=?"
        args.append(int(os_order_id))
    if _s(q):
        like = f"%{_s(q)}%"
        sql += " AND (o.os_no LIKE ? OR v.name LIKE ? OR r.inspection_notes LIKE ?)"
        args += [like] * 3
    sql += " ORDER BY r.receipt_date DESC, r.id DESC"
    return [dict(r) for r in conn.execute(sql, args)]


def get_receipt(conn, receipt_id: int) -> dict:
    r = row_to_dict(conn.execute(
        "SELECT r.*, o.os_no, v.name AS vendor_name FROM os_receipt r"
        " JOIN os_order o ON o.id=r.os_order_id JOIN vendor v ON v.id=o.vendor_id"
        " WHERE r.id=?", (receipt_id,)).fetchone())
    if not r:
        raise ValueError("Receipt not found")
    r["lines"] = [dict(x) for x in conn.execute(
        """SELECT l.*, i.description, i.part_code, i.unit,
                  s.os_id, s.description AS stock_description
           FROM os_receipt_line l
           LEFT JOIN os_order_item i ON i.id=l.os_order_item_id
           LEFT JOIN os_item s ON s.id=l.os_item_id
           WHERE l.os_receipt_id=? ORDER BY l.id""", (receipt_id,))]
    return r


def delete_receipt(conn, receipt_id: int) -> None:
    """Undo a delivery: the stock it created goes back out again.

    Refused when the goods have already been issued — the stock is on a job,
    and pretending the delivery never happened would leave the shelf negative.
    The reversal is logged rather than erased: the movement log should say that
    something arrived and was then withdrawn, not quietly forget both.

    SIMPLIFICATION, ratified by the owner: stock is a running quantity, not a
    set of lots, so the guard below can only ask "does the shelf hold enough?",
    never "are THESE pieces still here". Undoing an early receipt can therefore
    succeed against stock a later one brought in. Per-receipt lot tracking is
    deliberately out of scope.
    """
    conn.execute("BEGIN IMMEDIATE")
    try:
        r = conn.execute("SELECT * FROM os_receipt WHERE id=?", (receipt_id,)).fetchone()
        if not r:
            raise ValueError("Receipt not found")
        back: dict[int, float] = {}
        for ln in conn.execute(
                "SELECT os_item_id, qty FROM os_receipt_line WHERE os_receipt_id=?",
                (receipt_id,)):
            if ln["os_item_id"]:
                back[ln["os_item_id"]] = back.get(ln["os_item_id"], 0) + ln["qty"]
        for item_id, qty in back.items():
            row = conn.execute("SELECT os_id, qty FROM os_item WHERE id=?",
                               (item_id,)).fetchone()
            if not row:
                continue
            if row["qty"] - qty < -1e-9:
                raise ValueError(
                    f"{row['os_id']} is down to {row['qty']:g} — {qty:g} came in on this "
                    "receipt and some of it has already gone out, so it can't be undone")
        for item_id, qty in back.items():
            conn.execute("UPDATE os_item SET qty=qty-? WHERE id=?", (qty, item_id))
            # Dated to the RECEIPT, not to today: the delivery is being unsaid,
            # so every balance from that day on has to read as if it never
            # arrived — and the pair sits together in the log.
            _movement(conn, item_id, r["receipt_date"], "adjust", -qty,
                      remarks=f"Receipt of {r['receipt_date']} deleted")
        conn.execute("DELETE FROM os_receipt WHERE id=?", (receipt_id,))   # lines cascade
        _recompute_status(conn, r["os_order_id"])
        conn.commit()
    except BaseException:
        conn.rollback()
        raise


# --------------------------------------------------------------------------- #
# Outsourced stock
# --------------------------------------------------------------------------- #
def list_os_items(conn, q: str = "", active_only: bool = True, vendor_id=None) -> dict:
    sql = """SELECT i.*, v.name AS vendor_name, v.code AS vendor_code,
                    o.os_no AS source_os_no, o.id AS source_os_order_id
             FROM os_item i LEFT JOIN vendor v ON v.id=i.vendor_id
             LEFT JOIN os_order o ON o.id=i.os_order_id WHERE 1=1"""
    args: list = []
    if active_only:
        sql += " AND i.active=1"
    if vendor_id:
        sql += " AND i.vendor_id=?"
        args.append(int(vendor_id))
    if _s(q):
        like = f"%{_s(q)}%"
        sql += (" AND (i.os_id LIKE ? OR i.description LIKE ? OR i.part_code LIKE ?"
                " OR i.material LIKE ? OR i.size_section LIKE ? OR v.name LIKE ?)")
        args += [like] * 6
    sql += " ORDER BY i.id DESC"
    rows = [dict(r) for r in conn.execute(sql, args)]
    for r in rows:
        r["value"] = round(r["qty"] * (r["unit_cost"] or 0), 2)
    return {"rows": rows, "stats": {
        "items": len(rows),
        "qty": round(sum(r["qty"] for r in rows), 3),
        "value": round(sum(r["value"] for r in rows), 2),
    }}


def get_os_item(conn, os_item_id: int) -> dict:
    it = row_to_dict(conn.execute(
        "SELECT i.*, v.name AS vendor_name, v.code AS vendor_code,"
        " o.os_no AS source_os_no, o.id AS source_os_order_id FROM os_item i"
        " LEFT JOIN vendor v ON v.id=i.vendor_id"
        " LEFT JOIN os_order o ON o.id=i.os_order_id WHERE i.id=?",
        (os_item_id,)).fetchone())
    if not it:
        raise ValueError("Stock item not found")
    it["value"] = round(it["qty"] * (it["unit_cost"] or 0), 2)
    it["movements"] = os_movements(conn, os_item_id)
    return it


def os_movements(conn, os_item_id: int) -> list[dict]:
    return [dict(r) for r in conn.execute(
        "SELECT * FROM os_movement WHERE os_item_id=? ORDER BY mv_date DESC, id DESC",
        (os_item_id,))]


def update_os_item(conn, os_item_id: int, data: dict) -> dict:
    """The item's master fields. NOT the quantity — stock only ever moves
    through a receipt, an issue or an adjustment, so it stays auditable."""
    if not conn.execute("SELECT 1 FROM os_item WHERE id=?", (os_item_id,)).fetchone():
        raise ValueError("Stock item not found")
    description = _s(data.get("description"))
    if not description:
        raise ValueError("Description is required")
    vendor_id = data.get("vendor_id") or None
    if vendor_id and not conn.execute(
            "SELECT 1 FROM vendor WHERE id=?", (vendor_id,)).fetchone():
        raise ValueError("That vendor no longer exists — reload the page")
    conn.execute(
        "UPDATE os_item SET description=?, part_code=?, material=?, size_section=?,"
        " unit=?, unit_cost=?, vendor_id=?, notes=?, active=? WHERE id=?",
        (description, _s(data.get("part_code")), _s(data.get("material")),
         _s(data.get("size_section")), _s(data.get("unit")) or "Nos",
         _check_optional_money(data.get("unit_cost"), "Unit cost"), vendor_id,
         _s(data.get("notes")), 0 if data.get("active") is False else 1, os_item_id))
    conn.commit()
    return get_os_item(conn, os_item_id)


def adjust_os_item(conn, os_item_id: int, data: dict) -> dict:
    """A stock correction: a recount, a breakage, a piece found on a shelf.
    Signed — that is the whole point of the endpoint."""
    try:
        qty = float(data.get("qty"))
    except (TypeError, ValueError):
        raise ValueError("Adjustment must be a number")
    if not math.isfinite(qty) or qty == 0:
        raise ValueError("An adjustment of zero changes nothing")
    if abs(qty) > 1e12:
        raise ValueError("That adjustment looks wrong")
    mv_date = _check_date(data.get("mv_date"), "Date", required=True)
    remarks = _s(data.get("remarks"))
    if not remarks:
        raise ValueError("Say why the count is changing")

    # Atomic check-then-write: two adjustments at once must not both pass the
    # "stays above zero" check against the same stored quantity.
    conn.execute("BEGIN IMMEDIATE")
    try:
        row = conn.execute("SELECT os_id, qty FROM os_item WHERE id=?",
                           (os_item_id,)).fetchone()
        if not row:
            raise ValueError("Stock item not found")
        if row["qty"] + qty < -1e-9:
            raise ValueError(f"{row['os_id']} only has {row['qty']:g} — "
                             "an adjustment can't take it below zero")
        conn.execute("UPDATE os_item SET qty=qty+? WHERE id=?", (qty, os_item_id))
        _movement(conn, os_item_id, mv_date, "adjust", qty, remarks=remarks)
        conn.commit()
    except BaseException:
        conn.rollback()
        raise
    return get_os_item(conn, os_item_id)


def issue_os_item(conn, os_item_id: int, data: dict) -> dict:
    """Send bought-out stock out to a job. Order ID is required for the same
    reason it is on a heat issue: an issue that names nothing traces nothing."""
    qty = _check_qty(data.get("qty"), "Quantity")
    order_id = _s(data.get("order_id"))
    if not order_id:
        raise ValueError("Order ID is required when issuing stock")
    mv_date = _check_date(data.get("mv_date"), "Date", required=True)

    conn.execute("BEGIN IMMEDIATE")
    try:
        row = conn.execute("SELECT os_id, qty, unit FROM os_item WHERE id=?",
                           (os_item_id,)).fetchone()
        if not row:
            raise ValueError("Stock item not found")
        if qty > row["qty"] + 1e-9:
            raise ValueError(
                f"Only {row['qty']:g} {row['unit'] or ''}".rstrip()
                + f" of {row['os_id']} in stock")
        conn.execute("UPDATE os_item SET qty=qty-? WHERE id=?", (qty, os_item_id))
        _movement(conn, os_item_id, mv_date, "issue", -qty, order_id=order_id,
                  remarks=_s(data.get("remarks")))
        conn.commit()
    except BaseException:
        conn.rollback()
        raise
    return get_os_item(conn, os_item_id)


# --------------------------------------------------------------------------- #
# Vendor paperwork (uploaded, never generated — owner brief).
# Validation/mime plumbing is shared app-wide — see core/attachments.py.
# --------------------------------------------------------------------------- #
def save_os_documents(conn, vendor_id, os_order_id, label: str,
                      items: list[tuple[str, str, bytes]]) -> list[dict]:
    """Store a batch of (filename, mime, content) ALL-OR-NOTHING: every file is
    validated before anything is written, and a failure mid-batch rolls back
    the DB rows and removes the files already on disk."""
    vendor_id = int(vendor_id) if vendor_id else None
    os_order_id = int(os_order_id) if os_order_id else None
    if not vendor_id and not os_order_id:
        raise ValueError("File this against a vendor, an outgoing order, or both")
    if vendor_id and not conn.execute(
            "SELECT 1 FROM vendor WHERE id=?", (vendor_id,)).fetchone():
        raise ValueError("Vendor not found")
    if os_order_id and not conn.execute(
            "SELECT 1 FROM os_order WHERE id=?", (os_order_id,)).fetchone():
        raise ValueError("Outgoing order not found")
    if not items:
        raise ValueError("Pick at least one file")
    checked = [(filename, validate_attachment(mime, content), content)
               for filename, mime, content in items]
    written: list[Path] = []
    metas: list[dict] = []
    try:
        for filename, mime, content in checked:
            stored = (f"os{os_order_id or 0}v{vendor_id or 0}"
                      f"-{secrets.token_hex(8)}{storage_ext(filename, mime)}")
            path = paths.outsourcing_files_dir() / stored
            path.write_bytes(content)
            written.append(path)
            cur = conn.execute(
                "INSERT INTO os_document (vendor_id, os_order_id, label, filename, mime,"
                " size_bytes, stored_name, uploaded_at) VALUES (?,?,?,?,?,?,?,?)",
                (vendor_id, os_order_id, _s(label), _s(filename) or stored, mime,
                 len(content), stored, _now()))
            metas.append({"id": cur.lastrowid, "label": _s(label),
                          "filename": _s(filename) or stored,
                          "mime": mime, "size_bytes": len(content)})
        conn.commit()
    except BaseException:
        conn.rollback()
        for p in written:
            p.unlink(missing_ok=True)
        raise
    return metas


def list_os_documents(conn, vendor_id=None, os_order_id=None) -> list[dict]:
    sql = """SELECT d.*, v.name AS vendor_name, v.code AS vendor_code, o.os_no
             FROM os_document d LEFT JOIN vendor v ON v.id=d.vendor_id
             LEFT JOIN os_order o ON o.id=d.os_order_id WHERE 1=1"""
    args: list = []
    if vendor_id:
        sql += " AND d.vendor_id=?"
        args.append(int(vendor_id))
    if os_order_id:
        sql += " AND d.os_order_id=?"
        args.append(int(os_order_id))
    sql += " ORDER BY d.id DESC"
    return [dict(r) for r in conn.execute(sql, args)]


def delete_os_document(conn, document_id: int) -> None:
    row = conn.execute("SELECT stored_name FROM os_document WHERE id=?",
                       (document_id,)).fetchone()
    if not row:
        raise ValueError("Document not found")
    conn.execute("DELETE FROM os_document WHERE id=?", (document_id,))
    conn.commit()
    (paths.outsourcing_files_dir() / row["stored_name"]).unlink(missing_ok=True)


# --------------------------------------------------------------------------- #
# Reference data for the forms
# --------------------------------------------------------------------------- #
def refs(conn) -> dict:
    return {
        "vendors": [dict(r) for r in conn.execute(
            "SELECT id, code, name FROM vendor WHERE active=1"
            " ORDER BY name COLLATE NOCASE")],
        "units": settings_mod.units(conn),
        "purposes": PURPOSES,
        "statuses": [{"key": s, "label": STATUS_LABELS[s]} for s in OS_STATUSES],
        # internal orders still live, for the optional link
        "orders": [dict(r) for r in conn.execute(
            "SELECT o.id, o.order_no, c.name AS customer_name FROM customer_order o"
            " JOIN customer c ON c.id=o.customer_id"
            " WHERE o.stage <> 'payment' ORDER BY o.id DESC")],
    }


def order_items(conn, order_id: int) -> list[dict]:
    """One internal order's items, for outsourcing part of it to a vendor."""
    return [dict(r) for r in conn.execute(
        "SELECT i.id, i.description, i.qty, i.unit, d.drawing_no, d.revision"
        " FROM order_item i LEFT JOIN drawing d ON d.id=i.drawing_id"
        " WHERE i.order_id=? ORDER BY i.id", (order_id,))]


# --------------------------------------------------------------------------- #
# API models — every column a route writes appears HERE too, or Pydantic drops
# it silently and the field never reaches the database.
# --------------------------------------------------------------------------- #
class VendorIn(BaseModel):
    code: str = ""
    name: str
    contact_name: str = ""
    phone: str = ""
    email: str = ""
    address: str = ""
    services: str = ""
    notes: str = ""


class OsOrderItemIn(BaseModel):
    id: int | None = None
    description: str = ""
    part_code: str = ""
    qty: float
    unit: str = "Nos"
    unit_cost: float | None = None
    order_item_id: int | None = None


class OsOrderIn(BaseModel):
    vendor_id: int
    order_id: int | None = None
    purpose: str = ""
    date_sent: str = ""
    deadline: str = ""
    notes: str = ""
    items: list[OsOrderItemIn] = []


class StatusIn(BaseModel):
    status: str


class ReceiptLineIn(BaseModel):
    os_order_item_id: int
    qty: float
    os_item_id: int | None = None
    description: str = ""
    part_code: str = ""
    material: str = ""
    size_section: str = ""
    unit: str = ""
    unit_cost: float | None = None


class ReceiptIn(BaseModel):
    os_order_id: int
    receipt_date: str = ""
    inspection_notes: str = ""
    accepted: bool = True
    lines: list[ReceiptLineIn] = []


class OsItemIn(BaseModel):
    description: str
    part_code: str = ""
    material: str = ""
    size_section: str = ""
    unit: str = "Nos"
    unit_cost: float | None = None
    vendor_id: int | None = None
    notes: str = ""
    active: bool = True


class AdjustIn(BaseModel):
    qty: float                    # signed: + found, - lost
    mv_date: str = ""
    remarks: str = ""


class IssueIn(BaseModel):
    qty: float
    order_id: str = ""
    mv_date: str = ""
    remarks: str = ""


def _400(fn, *args, **kw):
    try:
        return fn(*args, **kw)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


# --------------------------------------------------------------------------- #
# Routes. Literal prefixes first, /{id} last inside every subtree — '/vendors'
# must never be read as an id.
# --------------------------------------------------------------------------- #
@router.get("/refs")
def outsourcing_refs(conn=Depends(get_db)):
    return refs(conn)


@router.get("/refs/order-items/{order_id}")
def outsourcing_order_items(order_id: int, conn=Depends(get_db)):
    return {"rows": order_items(conn, order_id)}


@router.get("/deadlines")
def outsourcing_deadlines(conn=Depends(get_db)):
    """What is still out at a vendor, and how late it is."""
    return deadlines(conn)


# ---- vendors ---- #
@router.get("/vendors")
def vendors(q: str = "", active: bool = True, conn=Depends(get_db)):
    return {"rows": list_vendors(conn, q=q, active_only=active)}


@router.post("/vendors")
def vendor_create(body: VendorIn, conn=Depends(get_db)):
    vid = _400(save_vendor, conn, body.model_dump())
    return get_vendor(conn, vid)


@router.get("/vendors/{vendor_id}")
def vendor_detail(vendor_id: int, conn=Depends(get_db)):
    try:
        return get_vendor(conn, vendor_id)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.put("/vendors/{vendor_id}")
def vendor_update(vendor_id: int, body: VendorIn, conn=Depends(get_db)):
    _400(save_vendor, conn, body.model_dump(), vendor_id)
    return get_vendor(conn, vendor_id)


@router.post("/vendors/{vendor_id}/active")
def vendor_active(vendor_id: int, active: bool, conn=Depends(get_db)):
    return _400(set_vendor_active, conn, vendor_id, active)


# ---- outgoing orders ---- #
@router.get("/orders")
def os_orders(q: str = "", status: str = "", vendor_id: int = 0, conn=Depends(get_db)):
    return list_os_orders(conn, q=q, status=status, vendor_id=vendor_id or None)


@router.post("/orders")
def os_order_create(body: OsOrderIn, conn=Depends(get_db)):
    oid = _400(create_os_order, conn, body.model_dump())
    return get_os_order(conn, oid)


@router.get("/orders/{os_order_id}")
def os_order_detail(os_order_id: int, conn=Depends(get_db)):
    try:
        return get_os_order(conn, os_order_id)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.put("/orders/{os_order_id}")
def os_order_update(os_order_id: int, body: OsOrderIn, conn=Depends(get_db)):
    _400(update_os_order, conn, os_order_id, body.model_dump())
    return get_os_order(conn, os_order_id)


@router.post("/orders/{os_order_id}/status")
def os_order_status(os_order_id: int, body: StatusIn, conn=Depends(get_db)):
    return _400(set_os_status, conn, os_order_id, body.status)


@router.post("/orders/{os_order_id}/reopen")
def os_order_reopen(os_order_id: int, conn=Depends(get_db)):
    """Take back a Close or a Cancel; the status re-derives from the receipts."""
    return _400(reopen_os_order, conn, os_order_id)


# ---- receipts ---- #
@router.get("/receipts")
def receipts(q: str = "", os_order_id: int = 0, conn=Depends(get_db)):
    return {"rows": list_receipts(conn, q=q, os_order_id=os_order_id or None)}


@router.post("/receipts")
def receipt_create(body: ReceiptIn, conn=Depends(get_db)):
    rid = _400(create_receipt, conn, body.model_dump())
    return get_receipt(conn, rid)


@router.get("/receipts/{receipt_id}")
def receipt_detail(receipt_id: int, conn=Depends(get_db)):
    try:
        return get_receipt(conn, receipt_id)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.delete("/receipts/{receipt_id}")
def receipt_delete(receipt_id: int, conn=Depends(get_db)):
    _400(delete_receipt, conn, receipt_id)
    return {"ok": True}


# ---- stock ---- #
@router.get("/stock")
def stock(q: str = "", active: bool = True, vendor_id: int = 0, conn=Depends(get_db)):
    return list_os_items(conn, q=q, active_only=active, vendor_id=vendor_id or None)


@router.get("/stock/{os_item_id}")
def stock_detail(os_item_id: int, conn=Depends(get_db)):
    try:
        return get_os_item(conn, os_item_id)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.put("/stock/{os_item_id}")
def stock_update(os_item_id: int, body: OsItemIn, conn=Depends(get_db)):
    return _400(update_os_item, conn, os_item_id, body.model_dump())


@router.get("/stock/{os_item_id}/movements")
def stock_movements(os_item_id: int, conn=Depends(get_db)):
    return {"rows": os_movements(conn, os_item_id)}


@router.post("/stock/{os_item_id}/adjust")
def stock_adjust(os_item_id: int, body: AdjustIn, conn=Depends(get_db)):
    return _400(adjust_os_item, conn, os_item_id, body.model_dump())


@router.post("/stock/{os_item_id}/issue")
def stock_issue(os_item_id: int, body: IssueIn, conn=Depends(get_db)):
    return _400(issue_os_item, conn, os_item_id, body.model_dump())


# ---- documents ---- #
@router.get("/documents")
def documents(vendor_id: int = 0, os_order_id: int = 0, conn=Depends(get_db)):
    return {"rows": list_os_documents(conn, vendor_id=vendor_id or None,
                                      os_order_id=os_order_id or None)}


@router.post("/documents")
def document_upload(vendor_id: int = Form(0), os_order_id: int = Form(0),
                    label: str = Form(""), files: list[UploadFile] = File(...),
                    conn=Depends(get_db)):
    # Sync on purpose (like every route here): the sqlite connection from
    # get_db lives in the threadpool, so the handler must run there too.
    items = [(f.filename or "", f.content_type or "", f.file.read()) for f in files]
    saved = _400(save_os_documents, conn, vendor_id or None, os_order_id or None,
                 label, items)
    return {"saved": saved,
            "rows": list_os_documents(conn, vendor_id=vendor_id or None,
                                      os_order_id=os_order_id or None)}


@router.get("/documents/{document_id}")
def document_view(document_id: int, download: bool = False, conn=Depends(get_db)):
    row = conn.execute("SELECT * FROM os_document WHERE id=?", (document_id,)).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Document not found")
    path = paths.outsourcing_files_dir() / row["stored_name"]
    if not path.is_file():
        raise HTTPException(status_code=404, detail="File is missing on disk")
    safe = header_filename(row["filename"])
    disposition = "attachment" if download else "inline"
    return FileResponse(
        path, media_type=response_mime(row["stored_name"]),
        headers={"Content-Disposition": f'{disposition}; filename="{safe}"'},
    )


@router.delete("/documents/{document_id}")
def document_delete(document_id: int, conn=Depends(get_db)):
    _400(delete_os_document, conn, document_id)
    return {"ok": True}
