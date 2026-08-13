"""Order Tracking — the operational spine.

An order moves through 7 SKIPPABLE stages (Enquiry → Quote → PO received →
Production → QC → Dispatch → Payment); every stage change is logged. Items
reference the drawing master (or free text); order numbers come from the
configurable format in Settings, sequenced per Indian financial year.

Material traceability: inventory usage-log entries whose Order ID equals this
order's number appear on the order (read-only join — the heat register stays
the source of truth). Shipping lives HERE as consignments: GST fields
(transporter, LR, e-way, invoice), lines referencing order items with
quantities — so partial shipments and one truck carrying several orders both
work. Over-shipping an item is refused.
"""

from __future__ import annotations

import math
import sqlite3
from datetime import date, datetime

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from ..core.db import row_to_dict
from ..core.deps import get_db, require_module
from . import settings as settings_mod

router = APIRouter(prefix="/api/orders",
                   dependencies=[Depends(require_module("orders"))])

STAGES = ["enquiry", "quote", "po", "production", "qc", "dispatch", "payment"]
STAGE_LABELS = {
    "enquiry": "Enquiry", "quote": "Quote", "po": "PO received",
    "production": "Production", "qc": "QC", "dispatch": "Dispatch",
    "payment": "Payment received",
}


def _s(v) -> str:
    return (v or "").strip()


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


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


# --------------------------------------------------------------------------- #
# Order numbering (atomic, per financial year, format from Settings)
# --------------------------------------------------------------------------- #
def next_order_no(conn, order_date: date) -> str:
    fmt = settings_mod.get_setting(conn, "order_number_format",
                                   settings_mod.DEFAULT_ORDER_FORMAT)
    fy = settings_mod.fy_label(order_date)
    # BEGIN IMMEDIATE serialises the bump; UNIQUE(order_no) is the backstop.
    conn.execute("BEGIN IMMEDIATE")
    try:
        row = conn.execute("SELECT seq FROM order_seq WHERE fy=?", (fy,)).fetchone()
        seq = (row["seq"] if row else 0) + 1
        conn.execute(
            "INSERT INTO order_seq (fy, seq) VALUES (?,?)"
            " ON CONFLICT(fy) DO UPDATE SET seq=excluded.seq", (fy, seq))
        conn.commit()
    except BaseException:
        conn.rollback()
        raise
    return settings_mod.render_order_no(fmt, order_date, seq)


# --------------------------------------------------------------------------- #
# Orders
# --------------------------------------------------------------------------- #
def _validate_items(items: list[dict]) -> list[dict]:
    if not items:
        raise ValueError("An order needs at least one item")
    out = []
    for i, it in enumerate(items, 1):
        if not it.get("drawing_id") and not _s(it.get("description")):
            raise ValueError(f"Item {i}: pick a drawing or type a description")
        out.append({
            "drawing_id": it.get("drawing_id") or None,
            "description": _s(it.get("description")),
            "qty": _check_qty(it.get("qty"), f"Item {i} quantity"),
            "unit": _s(it.get("unit")) or "Nos",
            "rate": _check_qty(it.get("rate"), f"Item {i} rate") if it.get("rate") not in (None, "", 0) else 0,
        })
    return out


def create_order(conn, data: dict) -> int:
    if not data.get("customer_id"):
        raise ValueError("Pick a customer")
    stage = _s(data.get("stage")) or "enquiry"
    if stage not in STAGES:
        raise ValueError("Unknown stage")
    order_date = _check_date(data.get("order_date"), "Order date", required=True)
    due_date = _check_date(data.get("due_date"), "Due date")
    items = _validate_items(data.get("items") or [])
    order_no = next_order_no(conn, date.fromisoformat(order_date))
    try:
        cur = conn.execute(
        """INSERT INTO customer_order (order_no, customer_id, customer_po, stage,
             order_date, due_date, notes, created_at) VALUES (?,?,?,?,?,?,?,?)""",
            (order_no, data["customer_id"], _s(data.get("customer_po")), stage,
             order_date, due_date, _s(data.get("notes")), _now()))
    except sqlite3.IntegrityError:
        raise ValueError(
            f"Order number {order_no} already exists — this happens when the "
            "number format in Settings has no {FY} or was changed; adjust it there")
    order_id = cur.lastrowid
    for it in items:
        conn.execute(
            "INSERT INTO order_item (order_id, drawing_id, description, qty, unit, rate)"
            " VALUES (?,?,?,?,?,?)",
            (order_id, it["drawing_id"], it["description"], it["qty"], it["unit"], it["rate"]))
    conn.execute("INSERT INTO order_stage_log (order_id, stage, at, note) VALUES (?,?,?,?)",
                 (order_id, stage, _now(), "Order created"))
    conn.commit()
    return order_id


def update_order(conn, order_id: int, data: dict) -> None:
    """Header + items (replace-all, like the attendance grid). Items already on
    a consignment can shrink only down to the shipped quantity."""
    if not conn.execute("SELECT 1 FROM customer_order WHERE id=?", (order_id,)).fetchone():
        raise ValueError("Order not found")
    if not data.get("customer_id"):
        raise ValueError("Pick a customer")
    order_date = _check_date(data.get("order_date"), "Order date", required=True)
    due_date = _check_date(data.get("due_date"), "Due date")
    items = _validate_items(data.get("items") or [])
    shipped = _shipped_by_item(conn, order_id)

    conn.execute("BEGIN IMMEDIATE")
    try:
        conn.execute(
            """UPDATE customer_order SET customer_id=?, customer_po=?, order_date=?,
                 due_date=?, notes=? WHERE id=?""",
            (data["customer_id"], _s(data.get("customer_po")), order_date, due_date,
             _s(data.get("notes")), order_id))
        # Items sent WITH an id (belonging to this order) are updated in place;
        # unshipped items not re-sent are deleted; the rest insert fresh.
        # Shipped items can neither vanish nor shrink below the shipped qty.
        existing = {r["id"]: dict(r) for r in conn.execute(
            "SELECT * FROM order_item WHERE order_id=?", (order_id,))}
        sent_ids = {int(i["id"]) for i in (data.get("items") or []) if i.get("id")}
        for old_id in existing:
            if shipped.get(old_id, 0) > 0 and old_id not in sent_ids:
                raise ValueError("An item that already shipped can't be removed")
            if old_id not in sent_ids:
                conn.execute("DELETE FROM order_item WHERE id=?", (old_id,))
        for raw, it in zip(data.get("items") or [], items):
            iid = raw.get("id")
            if iid and int(iid) in existing:
                if it["qty"] < shipped.get(int(iid), 0):
                    raise ValueError(
                        f"Item quantity can't go below the {shipped[int(iid)]:g} already shipped")
                conn.execute(
                    "UPDATE order_item SET drawing_id=?, description=?, qty=?, unit=?, rate=?"
                    " WHERE id=?",
                    (it["drawing_id"], it["description"], it["qty"], it["unit"],
                     it["rate"], int(iid)))
            else:
                conn.execute(
                    "INSERT INTO order_item (order_id, drawing_id, description, qty, unit, rate)"
                    " VALUES (?,?,?,?,?,?)",
                    (order_id, it["drawing_id"], it["description"], it["qty"],
                     it["unit"], it["rate"]))
        conn.commit()
    except BaseException:
        conn.rollback()
        raise


def set_stage(conn, order_id: int, stage: str, note: str = "") -> dict:
    if stage not in STAGES:
        raise ValueError("Unknown stage")
    cur = conn.execute("UPDATE customer_order SET stage=? WHERE id=?", (stage, order_id))
    if not cur.rowcount:
        raise ValueError("Order not found")
    conn.execute("INSERT INTO order_stage_log (order_id, stage, at, note) VALUES (?,?,?,?)",
                 (order_id, stage, _now(), _s(note)))
    conn.commit()
    return get_order(conn, order_id)


def _shipped_by_item(conn, order_id: int) -> dict[int, float]:
    return {r["order_item_id"]: r["qty"] for r in conn.execute(
        """SELECT l.order_item_id, COALESCE(SUM(l.qty),0) AS qty
           FROM consignment_line l JOIN order_item i ON i.id=l.order_item_id
           WHERE i.order_id=? GROUP BY l.order_item_id""", (order_id,))}


def list_orders(conn, q: str = "", stage: str = "") -> dict:
    sql = """SELECT o.*, c.name AS customer_name,
                    (SELECT COUNT(*) FROM order_item i WHERE i.order_id=o.id) AS items,
                    (SELECT COALESCE(SUM(i.qty*i.rate),0) FROM order_item i
                       WHERE i.order_id=o.id) AS amount
             FROM customer_order o JOIN customer c ON c.id=o.customer_id WHERE 1=1"""
    args: list = []
    if _s(q):
        like = f"%{_s(q)}%"
        sql += " AND (o.order_no LIKE ? OR o.customer_po LIKE ? OR c.name LIKE ?)"
        args += [like] * 3
    if _s(stage):
        sql += " AND o.stage=?"
        args.append(stage)
    sql += " ORDER BY o.id DESC"
    rows = [dict(r) for r in conn.execute(sql, args)]
    counts = {r["stage"]: r["n"] for r in conn.execute(
        "SELECT stage, COUNT(*) AS n FROM customer_order GROUP BY stage")}
    return {"rows": rows, "stage_counts": counts,
            "stages": [{"key": s, "label": STAGE_LABELS[s]} for s in STAGES]}


def get_order(conn, order_id: int) -> dict:
    o = row_to_dict(conn.execute(
        "SELECT o.*, c.name AS customer_name FROM customer_order o"
        " JOIN customer c ON c.id=o.customer_id WHERE o.id=?", (order_id,)).fetchone())
    if not o:
        raise ValueError("Order not found")
    shipped = _shipped_by_item(conn, order_id)
    o["items"] = []
    for r in conn.execute(
            "SELECT i.*, d.drawing_no, d.revision FROM order_item i"
            " LEFT JOIN drawing d ON d.id=i.drawing_id WHERE i.order_id=? ORDER BY i.id",
            (order_id,)):
        it = dict(r)
        it["shipped"] = shipped.get(it["id"], 0)
        it["amount"] = round(it["qty"] * it["rate"], 2)
        o["items"].append(it)
    o["amount"] = round(sum(i["amount"] for i in o["items"]), 2)
    o["stage_log"] = [dict(r) for r in conn.execute(
        "SELECT * FROM order_stage_log WHERE order_id=? ORDER BY id DESC", (order_id,))]
    # material traceability: inventory issues recorded against this order number
    o["heats"] = [dict(r) for r in conn.execute(
        """SELECT m.mv_date, m.rods, m.weight_kg, m.remarks,
                  h.heat_number, h.grade, h.material_class
           FROM heat_movement m JOIN heat h ON h.id=m.heat_id
           WHERE m.type='issue' AND m.order_id=? ORDER BY m.mv_date DESC""",
        (o["order_no"],))]
    o["consignments"] = [dict(r) for r in conn.execute(
        """SELECT DISTINCT cn.* FROM consignment cn
           JOIN consignment_line l ON l.consignment_id=cn.id
           JOIN order_item i ON i.id=l.order_item_id
           WHERE i.order_id=? ORDER BY cn.consign_date DESC, cn.id DESC""", (order_id,))]
    o["stages"] = [{"key": s, "label": STAGE_LABELS[s]} for s in STAGES]
    return o


def delete_order(conn, order_id: int) -> None:
    n = conn.execute(
        """SELECT COUNT(*) AS n FROM consignment_line l
           JOIN order_item i ON i.id=l.order_item_id WHERE i.order_id=?""",
        (order_id,)).fetchone()["n"]
    if n:
        raise ValueError("This order has consignments — delete those first")
    cur = conn.execute("DELETE FROM customer_order WHERE id=?", (order_id,))
    conn.commit()
    if not cur.rowcount:
        raise ValueError("Order not found")


# --------------------------------------------------------------------------- #
# Consignments (may carry items from SEVERAL orders; partials fine)
# --------------------------------------------------------------------------- #
def open_items(conn, order_id: int) -> list[dict]:
    """Items of one order with their still-unshipped quantity (for the form)."""
    shipped = _shipped_by_item(conn, order_id)
    out = []
    for r in conn.execute(
            "SELECT i.*, d.drawing_no FROM order_item i LEFT JOIN drawing d"
            " ON d.id=i.drawing_id WHERE i.order_id=? ORDER BY i.id", (order_id,)):
        it = dict(r)
        it["shipped"] = shipped.get(it["id"], 0)
        it["pending"] = round(it["qty"] - it["shipped"], 3)
        out.append(it)
    return out


def create_consignment(conn, data: dict) -> int:
    lines = data.get("lines") or []
    if not lines:
        raise ValueError("Add at least one item to the consignment")
    cdate = _check_date(data.get("consign_date"), "Consignment date", required=True)
    freight = _check_optional_money(data.get("freight"), "Freight")
    conn.execute("BEGIN IMMEDIATE")
    try:
        checked = []
        for ln in lines:
            item = row_to_dict(conn.execute(
                "SELECT * FROM order_item WHERE id=?", (ln.get("order_item_id"),)).fetchone())
            if not item:
                raise ValueError("An order item on this consignment no longer exists")
            qty = _check_qty(ln.get("qty"), "Line quantity")
            already = conn.execute(
                "SELECT COALESCE(SUM(qty),0) AS q FROM consignment_line WHERE order_item_id=?",
                (item["id"],)).fetchone()["q"]
            if qty + already > item["qty"] + 1e-9:
                raise ValueError(
                    f"Only {item['qty'] - already:g} of {item['qty']:g} left to ship on that item")
            checked.append((item["id"], qty))
        cur = conn.execute(
            """INSERT INTO consignment (consign_date, transporter, lr_no, eway_no,
                 invoice_no, vehicle_no, freight, delivered, notes, created_at)
               VALUES (?,?,?,?,?,?,?,0,?,?)""",
            (cdate, _s(data.get("transporter")), _s(data.get("lr_no")),
             _s(data.get("eway_no")), _s(data.get("invoice_no")),
             _s(data.get("vehicle_no")).upper(), freight, _s(data.get("notes")), _now()))
        for item_id, qty in checked:
            conn.execute(
                "INSERT INTO consignment_line (consignment_id, order_item_id, qty)"
                " VALUES (?,?,?)", (cur.lastrowid, item_id, qty))
        conn.commit()
        return cur.lastrowid
    except BaseException:
        conn.rollback()
        raise


def list_consignments(conn, q: str = "") -> list[dict]:
    sql = """SELECT cn.*,
                    (SELECT COUNT(*) FROM consignment_line l WHERE l.consignment_id=cn.id) AS lines,
                    (SELECT GROUP_CONCAT(DISTINCT o.order_no) FROM consignment_line l
                       JOIN order_item i ON i.id=l.order_item_id
                       JOIN customer_order o ON o.id=i.order_id
                       WHERE l.consignment_id=cn.id) AS order_nos
             FROM consignment cn WHERE 1=1"""
    args: list = []
    if _s(q):
        like = f"%{_s(q)}%"
        sql += """ AND (cn.lr_no LIKE ? OR cn.invoice_no LIKE ? OR cn.transporter LIKE ?
                   OR EXISTS (SELECT 1 FROM consignment_line l
                              JOIN order_item i ON i.id=l.order_item_id
                              JOIN customer_order o ON o.id=i.order_id
                              WHERE l.consignment_id=cn.id AND o.order_no LIKE ?))"""
        args += [like] * 4
    sql += " ORDER BY cn.consign_date DESC, cn.id DESC"
    return [dict(r) for r in conn.execute(sql, args)]


def get_consignment(conn, consignment_id: int) -> dict:
    cn = row_to_dict(conn.execute("SELECT * FROM consignment WHERE id=?",
                                  (consignment_id,)).fetchone())
    if not cn:
        raise ValueError("Consignment not found")
    cn["lines"] = [dict(r) for r in conn.execute(
        """SELECT l.id, l.qty, i.description, i.unit, o.order_no, d.drawing_no
           FROM consignment_line l
           JOIN order_item i ON i.id=l.order_item_id
           JOIN customer_order o ON o.id=i.order_id
           LEFT JOIN drawing d ON d.id=i.drawing_id
           WHERE l.consignment_id=? ORDER BY l.id""", (consignment_id,))]
    return cn


def set_delivered(conn, consignment_id: int, delivered: bool) -> dict:
    cur = conn.execute("UPDATE consignment SET delivered=? WHERE id=?",
                       (int(delivered), consignment_id))
    conn.commit()
    if not cur.rowcount:
        raise ValueError("Consignment not found")
    return get_consignment(conn, consignment_id)


def delete_consignment(conn, consignment_id: int) -> None:
    cur = conn.execute("DELETE FROM consignment WHERE id=?", (consignment_id,))
    conn.commit()
    if not cur.rowcount:
        raise ValueError("Consignment not found")


# --------------------------------------------------------------------------- #
# Routes
# --------------------------------------------------------------------------- #
class OrderItemIn(BaseModel):
    id: int | None = None
    drawing_id: int | None = None
    description: str = ""
    qty: float
    unit: str = "Nos"
    rate: float = 0


class OrderIn(BaseModel):
    customer_id: int
    customer_po: str = ""
    stage: str = "enquiry"
    order_date: str = ""
    due_date: str = ""
    notes: str = ""
    items: list[OrderItemIn] = []


class StageIn(BaseModel):
    stage: str
    note: str = ""


class ConsignmentLineIn(BaseModel):
    order_item_id: int
    qty: float


class ConsignmentIn(BaseModel):
    consign_date: str = ""
    transporter: str = ""
    lr_no: str = ""
    eway_no: str = ""
    invoice_no: str = ""
    vehicle_no: str = ""
    freight: float | None = None
    notes: str = ""
    lines: list[ConsignmentLineIn] = []


def _400(fn, *args, **kw):
    try:
        return fn(*args, **kw)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/refs")
def refs(conn=Depends(get_db)):
    """Form reference data for THIS module (orders grant): customers, units,
    drawings with their latest rate for item prefill."""
    return {
        "customers": settings_mod.active_customers(conn),
        "units": settings_mod.units(conn),
        "drawings": [dict(r) for r in conn.execute(
            """SELECT d.id, d.drawing_no, d.revision, d.description, d.unit,
                      (SELECT r.rate FROM drawing_rate r WHERE r.drawing_id=d.id
                         ORDER BY r.rate_date DESC, r.id DESC LIMIT 1) AS latest_rate
               FROM drawing d WHERE d.active=1
               ORDER BY d.drawing_no COLLATE NOCASE, d.revision""")],
    }


@router.get("")
def orders(q: str = "", stage: str = "", conn=Depends(get_db)):
    return list_orders(conn, q=q, stage=stage)


@router.post("")
def order_create(body: OrderIn, conn=Depends(get_db)):
    oid = _400(create_order, conn, body.model_dump())
    return get_order(conn, oid)


@router.get("/consignments")
def consignments(q: str = "", conn=Depends(get_db)):
    return list_consignments(conn, q=q)


@router.post("/consignments")
def consignment_create(body: ConsignmentIn, conn=Depends(get_db)):
    cid = _400(create_consignment, conn, body.model_dump())
    return get_consignment(conn, cid)


@router.get("/consignments/{consignment_id}")
def consignment_detail(consignment_id: int, conn=Depends(get_db)):
    try:
        return get_consignment(conn, consignment_id)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.post("/consignments/{consignment_id}/delivered")
def consignment_delivered(consignment_id: int, delivered: bool, conn=Depends(get_db)):
    return _400(set_delivered, conn, consignment_id, delivered)


@router.delete("/consignments/{consignment_id}")
def consignment_delete(consignment_id: int, conn=Depends(get_db)):
    _400(delete_consignment, conn, consignment_id)
    return {"ok": True}


@router.get("/{order_id}")
def order_detail(order_id: int, conn=Depends(get_db)):
    try:
        return get_order(conn, order_id)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.put("/{order_id}")
def order_update(order_id: int, body: OrderIn, conn=Depends(get_db)):
    _400(update_order, conn, order_id, body.model_dump())
    return get_order(conn, order_id)


@router.post("/{order_id}/stage")
def order_stage(order_id: int, body: StageIn, conn=Depends(get_db)):
    return _400(set_stage, conn, order_id, body.stage, body.note)


@router.get("/{order_id}/open-items")
def order_open_items(order_id: int, conn=Depends(get_db)):
    return open_items(conn, order_id)


@router.delete("/{order_id}")
def order_delete(order_id: int, conn=Depends(get_db)):
    _400(delete_order, conn, order_id)
    return {"ok": True}
