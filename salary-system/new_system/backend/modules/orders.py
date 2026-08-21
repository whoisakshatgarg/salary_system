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

Intake paperwork attaches to the order (the enquiry e-mail, their PO scan):
files on disk under order_files/, metadata in order_attachment, the shared
plumbing in core/attachments.py.
"""

from __future__ import annotations

import html
import math
import re
import secrets
import sqlite3
from datetime import date, datetime
from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, HTMLResponse
from pydantic import BaseModel

from ..core import paths
from ..core.attachments import (header_filename, response_mime, storage_ext,
                                validate_attachment)
from ..core.db import row_to_dict
from ..core.deps import current_user, get_db, require_module
from ..core.rules import get_rules
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


def _check_refs(conn, customer_id, items: list[dict]) -> None:
    """Turn dangling ids into 400s instead of raw FK IntegrityError 500s."""
    if not conn.execute("SELECT 1 FROM customer WHERE id=?", (customer_id,)).fetchone():
        raise ValueError("That customer no longer exists — reload the page")
    for i, it in enumerate(items, 1):
        if it["drawing_id"] and not conn.execute(
                "SELECT 1 FROM drawing WHERE id=?", (it["drawing_id"],)).fetchone():
            raise ValueError(f"Item {i}: that drawing no longer exists — reload the page")


def create_order(conn, data: dict) -> int:
    if not data.get("customer_id"):
        raise ValueError("Pick a customer")
    stage = _s(data.get("stage")) or "enquiry"
    if stage not in STAGES:
        raise ValueError("Unknown stage")
    order_date = _check_date(data.get("order_date"), "Order date", required=True)
    due_date = _check_date(data.get("due_date"), "Due date")
    items = _validate_items(data.get("items") or [])
    _check_refs(conn, data["customer_id"], items)
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

    conn.execute("BEGIN IMMEDIATE")
    try:
        # inside the lock: a consignment can't commit between check and write
        shipped = _shipped_by_item(conn, order_id)
        _check_refs(conn, data["customer_id"], items)
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
                       WHERE i.order_id=o.id) AS amount,
                    (SELECT COALESCE(SUM(i.qty),0) FROM order_item i
                       WHERE i.order_id=o.id) AS qty_total,
                    -- what has actually left the shop, across every consignment
                    (SELECT COALESCE(SUM(l.qty),0) FROM consignment_line l
                       JOIN order_item i ON i.id=l.order_item_id
                       WHERE i.order_id=o.id) AS qty_shipped,
                    c.code AS customer_code
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
    rows = []
    for r in conn.execute(sql, args):
        o = dict(r)
        o["qty_total"] = round(o["qty_total"], 3)
        o["qty_shipped"] = round(o["qty_shipped"], 3)
        o["qty_pending"] = round(o["qty_total"] - o["qty_shipped"], 3)
        o["pct_shipped"] = (round(o["qty_shipped"] / o["qty_total"] * 100)
                            if o["qty_total"] else 0)
        rows.append(o)
    # the deliveries each order is split into, so the list can draw one bar per
    # segment rather than one averaged bar per order
    drops = _order_drops(conn, [o["id"] for o in rows])
    for o in rows:
        o["drops"] = drops.get(o["id"], [])
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
        it["pending"] = round(it["qty"] - it["shipped"], 3)
        it["amount"] = round(it["qty"] * it["rate"], 2)
        it["schedule"] = [dict(s) for s in conn.execute(
            "SELECT id, due_date, qty, note FROM order_schedule"
            " WHERE order_item_id=? ORDER BY due_date, id", (it["id"],))]
        planned = sum(s["qty"] for s in it["schedule"])
        it["planned"] = round(planned, 3)
        # what no delivery date has been promised for yet
        it["unplanned"] = round(it["qty"] - planned, 3)
        # segments = the drops plus that unpromised balance; the schedule rows
        # are the same objects, so both carry the allocation
        it["segments"], it["over_delivered"] = _segments(
            it["schedule"], it["qty"], it["shipped"])
        o["items"].append(it)
    _drawing_files(conn, o["items"])
    o["amount"] = round(sum(i["amount"] for i in o["items"]), 2)
    o["qty_total"] = round(sum(i["qty"] for i in o["items"]), 3)
    o["qty_shipped"] = round(sum(i["shipped"] for i in o["items"]), 3)
    o["qty_pending"] = round(o["qty_total"] - o["qty_shipped"], 3)
    o["stage_log"] = [dict(r) for r in conn.execute(
        "SELECT * FROM order_stage_log WHERE order_id=? ORDER BY id DESC", (order_id,))]
    # the paper trail: every quotation/invoice raised against this order, so
    # the record answers "what did we send them?" without a trip to Quotations
    o["documents"] = [dict(r) for r in conn.execute(
        """SELECT d.id, d.kind, d.doc_no, d.doc_date, d.status, d.tax_pct,
                  (SELECT COALESCE(SUM(l.qty * l.rate), 0) FROM document_line l
                     WHERE l.document_id = d.id) AS subtotal
           FROM document d WHERE d.order_id=?
           ORDER BY d.doc_date DESC, d.id DESC""", (order_id,))]
    for d in o["documents"]:
        d["total"] = round(d["subtotal"] * (1 + (d["tax_pct"] or 0) / 100), 2)
    # the GENERATED paperwork (SOP-DESIGN §6): what the pipeline strip draws a
    # chip for, newest first. One query — the strip shows every stage at once.
    o["papers"] = [dict(r) for r in conn.execute(
        """SELECT id, kind, paper_no, revision, status, paper_date
           FROM paper WHERE order_id=? ORDER BY id DESC""", (order_id,))]
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
    # intake paperwork: what the customer sent us for THIS order
    o["attachments"] = [dict(r) for r in conn.execute(
        "SELECT id, label, filename, size_bytes, uploaded_at FROM order_attachment"
        " WHERE order_id=? ORDER BY id", (order_id,))]
    o["stages"] = [{"key": s, "label": STAGE_LABELS[s]} for s in STAGES]
    return o


def _drawing_files(conn, items: list[dict]) -> None:
    """Hang each item's drawing files on it, in ONE query — the order page
    lists them per row, and asking per row would be a query per item."""
    ids = [i["drawing_id"] for i in items if i["drawing_id"]]
    files: dict[int, list[dict]] = {}
    if ids:
        marks = ",".join("?" * len(ids))
        for r in conn.execute(
                f"SELECT id, drawing_id, filename FROM drawing_file"
                f" WHERE drawing_id IN ({marks}) ORDER BY id", ids):
            files.setdefault(r["drawing_id"], []).append(
                {"id": r["id"], "filename": r["filename"]})
    for it in items:
        it["drawing_files"] = files.get(it["drawing_id"], [])


# --------------------------------------------------------------------------- #
# Intake attachments (files on disk, metadata in the DB).
# Validation/mime plumbing is shared app-wide — see core/attachments.py.
# --------------------------------------------------------------------------- #
def save_order_attachments(conn, order_id: int, label: str,
                           items: list[tuple[str, str, bytes]]) -> list[dict]:
    """Store a batch of (filename, mime, content) ALL-OR-NOTHING: every file is
    validated before anything is written, and a failure mid-batch rolls back
    the DB rows and removes the files already on disk."""
    if not conn.execute("SELECT 1 FROM customer_order WHERE id=?", (order_id,)).fetchone():
        raise ValueError("Order not found")
    if not items:
        raise ValueError("Pick at least one file")
    checked = [(filename, validate_attachment(mime, content), content)
               for filename, mime, content in items]
    written: list[Path] = []
    metas: list[dict] = []
    try:
        for filename, mime, content in checked:
            stored = f"o{order_id}-{secrets.token_hex(8)}{storage_ext(filename, mime)}"
            path = paths.order_files_dir() / stored
            path.write_bytes(content)
            written.append(path)
            cur = conn.execute(
                "INSERT INTO order_attachment (order_id, label, filename, mime,"
                " size_bytes, stored_name, uploaded_at) VALUES (?,?,?,?,?,?,?)",
                (order_id, _s(label), _s(filename) or stored, mime, len(content),
                 stored, _now()),
            )
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


def delete_order_attachment(conn, attachment_id: int) -> dict:
    row = conn.execute(
        "SELECT order_id, stored_name FROM order_attachment WHERE id=?",
        (attachment_id,),
    ).fetchone()
    if not row:
        raise ValueError("Attachment not found")
    conn.execute("DELETE FROM order_attachment WHERE id=?", (attachment_id,))
    conn.commit()
    (paths.order_files_dir() / row["stored_name"]).unlink(missing_ok=True)
    return get_order(conn, row["order_id"])


def delete_order(conn, order_id: int) -> None:
    conn.execute("BEGIN IMMEDIATE")  # check + delete atomically
    try:
        n = conn.execute(
            """SELECT COUNT(*) AS n FROM consignment_line l
               JOIN order_item i ON i.id=l.order_item_id WHERE i.order_id=?""",
            (order_id,)).fetchone()["n"]
        if n:
            raise ValueError("This order has consignments — delete those first")
        cur = conn.execute("DELETE FROM customer_order WHERE id=?", (order_id,))
        if not cur.rowcount:
            raise ValueError("Order not found")
        conn.commit()
    except BaseException:
        conn.rollback()
        raise


# --------------------------------------------------------------------------- #
# Consignments (may carry items from SEVERAL orders; partials fine)
# --------------------------------------------------------------------------- #
def order_bom(conn, order_id: int) -> dict:
    """What raw material this order COMMITS us to, part by part.

    The order itself has no bill of materials — the PARTS do. So this walks each
    order item to its drawing, takes that drawing's most recent costing, and
    multiplies the costing's per-piece material by the quantity ordered:

        required = qty_per_piece x item qty

    then rolls the lines up by heat number, because the heat is what has to come
    off the rack. Alongside it sits what has ACTUALLY been issued against this
    order number (the usage log), so the two can be read together: committed,
    issued, still to issue.

    Derived live, not snapshotted: re-costing a drawing changes what the order
    needs, and the costing it used is named in the result so the figure can
    always be traced. Items with no drawing, or a drawing with no costing, are
    listed with a reason rather than silently contributing nothing.
    """
    o = conn.execute(
        "SELECT o.id, o.order_no FROM customer_order o WHERE o.id=?", (order_id,)
    ).fetchone()
    if not o:
        raise ValueError("Order not found")

    # what has already left the rack against this order number
    issued: dict[str, dict] = {}
    for r in conn.execute(
        """SELECT h.heat_number, h.id AS heat_id,
                  COALESCE(SUM(m.rods),0) AS rods,
                  COALESCE(SUM(m.weight_kg),0) AS weight_kg
           FROM heat_movement m JOIN heat h ON h.id=m.heat_id
           WHERE m.type='issue' AND m.order_id=?
           GROUP BY h.id""", (o["order_no"],)
    ):
        issued[r["heat_number"]] = dict(r)

    items_out: list[dict] = []
    rolled: dict[tuple, dict] = {}

    for it in conn.execute(
        """SELECT i.*, d.drawing_no, d.revision FROM order_item i
           LEFT JOIN drawing d ON d.id=i.drawing_id
           WHERE i.order_id=? ORDER BY i.id""", (order_id,)
    ):
        item = dict(it)
        label = (f"{item['drawing_no']} rev {item['revision']}"
                 if item["drawing_no"] else (item["description"] or "item"))
        entry = {"item_id": item["id"], "label": label, "qty": item["qty"],
                 "unit": item["unit"], "materials": [], "reason": ""}

        if not item["drawing_id"]:
            entry["reason"] = "free-text item — no drawing, so no bill of materials"
            items_out.append(entry)
            continue

        costing = conn.execute(
            "SELECT * FROM costing WHERE drawing_id=? ORDER BY id DESC LIMIT 1",
            (item["drawing_id"],)).fetchone()
        if not costing:
            entry["reason"] = "this drawing has no costing yet"
            items_out.append(entry)
            continue

        entry["costing_id"] = costing["id"]
        entry["costing_date"] = costing["created_at"]
        mats = [dict(m) for m in conn.execute(
            "SELECT * FROM costing_material WHERE costing_id=? ORDER BY id",
            (costing["id"],))]
        if not mats:
            entry["reason"] = "its costing prices material by hand, not from stock"
            items_out.append(entry)
            continue

        for m in mats:
            required = round(m["qty_per_piece"] * item["qty"], 4)
            line = {"heat_id": m["heat_id"], "heat_number": m["heat_number"],
                    "material_label": m["material_label"], "unit": m["unit"],
                    "qty_per_piece": m["qty_per_piece"], "required": required,
                    "unit_cost": m["unit_cost"],
                    "cost": round(m["unit_cost"] * required, 2)}
            entry["materials"].append(line)

            key = (m["heat_number"] or m["material_label"], m["unit"])
            agg = rolled.setdefault(key, {
                "heat_id": m["heat_id"], "heat_number": m["heat_number"],
                "material_label": m["material_label"], "unit": m["unit"],
                "required": 0.0, "cost": 0.0, "from_items": []})
            agg["required"] = round(agg["required"] + required, 4)
            agg["cost"] = round(agg["cost"] + line["cost"], 2)
            agg["from_items"].append(label)

        items_out.append(entry)

    summary = []
    for agg in rolled.values():
        got = issued.get(agg["heat_number"] or "", {})
        agg["issued"] = round(
            got.get("rods", 0) if agg["unit"] == "rod" else got.get("weight_kg", 0), 4)
        agg["outstanding"] = round(max(agg["required"] - agg["issued"], 0), 4)
        summary.append(agg)
    summary.sort(key=lambda a: (-a["cost"], a["heat_number"] or ""))

    # material issued against this order that no part actually calls for
    committed_heats = {a["heat_number"] for a in summary}
    unexpected = [dict(v, heat_number=k) for k, v in issued.items()
                  if k not in committed_heats]

    return {
        "order_id": o["id"], "order_no": o["order_no"],
        "items": items_out,
        "summary": summary,
        "unexpected_issues": unexpected,
        "total_cost": round(sum(a["cost"] for a in summary), 2),
        "items_without_bom": sum(1 for i in items_out if i["reason"]),
    }


def _allocate_drops(schedule: list[dict], shipped: float) -> float:
    """Spread what has ACTUALLY shipped across the planned drops, earliest first.

    Nothing records which drop a consignment was meant for, and asking would be
    a lie anyway — a lorry leaves with a quantity, not with an intention. So the
    order's shipped total is poured into the drops in due-date order: fill the
    first, overflow into the second, and so on.

    That is also what makes an over-delivery behave sensibly. Ship 300 against a
    250 drop and the first drop closes while the extra 50 lands on the next one,
    which now needs 50 fewer — the later plans update themselves instead of the
    user having to rewrite them. Anything left after every drop is full is
    returned as over_delivered.

    Mutates each row in place, adding delivered / remaining / pct.
    """
    left = max(shipped or 0, 0)
    for s in schedule:
        take = min(left, s["qty"])
        left = round(left - take, 6)
        s["delivered"] = round(take, 3)
        s["remaining"] = round(s["qty"] - take, 3)
        s["pct"] = round(take / s["qty"] * 100) if s["qty"] else 0
        s["done"] = s["remaining"] <= 0
    return round(left, 3)


def _segments(schedule: list[dict], qty: float, shipped: float) -> tuple[list[dict], float]:
    """Every stretch of an item's quantity that can be shipped against.

    The planned drops, in date order, and then whatever quantity carries no
    promised date yet — a 600-piece item planned as 250 + 150 still owes 200
    that has to appear somewhere, or the segments would not add up to the order
    and a bar drawn from them would lie.

    An item with no plan at all comes back as ONE segment for the whole
    quantity, so every order has something to draw and something to ship
    against. Returns the segments and anything shipped beyond all of them.
    """
    segs = list(schedule)
    for s in segs:
        s["planned"] = True
    balance = round((qty or 0) - sum(s["qty"] for s in segs), 3)
    if balance > 0:
        segs.append({"id": None, "due_date": None, "qty": balance,
                     "note": None, "planned": False})
    return segs, _allocate_drops(segs, shipped)


def _order_drops(conn, order_ids: list[int]) -> dict[int, list[dict]]:
    """The delivery segments of every listed order, keyed by order id.

    Bulk on purpose: the tracking list draws a bar per segment on every row,
    and doing it per order would be three queries for each line of a page whose
    whole job is to be scanned at once.
    """
    if not order_ids:
        return {}
    marks = ",".join("?" * len(order_ids))
    items: dict[int, dict] = {}
    for r in conn.execute(
            f"""SELECT i.id, i.order_id, i.qty, i.description,
                       d.drawing_no, d.revision
                FROM order_item i LEFT JOIN drawing d ON d.id=i.drawing_id
                WHERE i.order_id IN ({marks}) ORDER BY i.id""", order_ids):
        items[r["id"]] = dict(r, shipped=0.0, schedule=[])
    if not items:
        return {}
    ids = list(items)
    imarks = ",".join("?" * len(ids))
    for r in conn.execute(
            f"""SELECT order_item_id, COALESCE(SUM(qty),0) AS qty
                FROM consignment_line WHERE order_item_id IN ({imarks})
                GROUP BY order_item_id""", ids):
        items[r["order_item_id"]]["shipped"] = r["qty"]
    for r in conn.execute(
            f"""SELECT id, order_item_id, due_date, qty, note FROM order_schedule
                WHERE order_item_id IN ({imarks}) ORDER BY due_date, id""", ids):
        items[r["order_item_id"]]["schedule"].append(dict(r))

    out: dict[int, list[dict]] = {}
    for it in items.values():
        segs, _ = _segments(it["schedule"], it["qty"], it["shipped"])
        part = (f"{it['drawing_no']} rev {it['revision']}" if it["drawing_no"]
                else (it["description"] or "item"))
        planned = [s for s in segs if s["planned"]]
        for s in segs:
            n = planned.index(s) + 1 if s["planned"] else 0
            out.setdefault(it["order_id"], []).append({
                "id": s["id"], "item_id": it["id"], "part": part,
                "label": (f"Drop {n} of {len(planned)}" if s["planned"]
                          else "Not yet scheduled"),
                "due_date": s["due_date"], "note": s["note"],
                "planned": s["planned"], "qty": s["qty"],
                "delivered": s["delivered"], "remaining": s["remaining"],
                "pct": s["pct"], "done": s["done"],
            })
    return out


def set_schedule(conn, order_item_id: int, rows: list[dict]) -> dict:
    """Replace the delivery plan for one order item.

    "600 pieces: 250 by the 10th, 100 by the 24th, the rest before the deadline"
    — the rest is NOT stored, it is item qty minus what is planned, so the two
    can never drift apart.
    """
    item = conn.execute(
        "SELECT i.*, o.due_date, o.order_no FROM order_item i"
        " JOIN customer_order o ON o.id=i.order_id WHERE i.id=?",
        (order_item_id,)).fetchone()
    if not item:
        raise ValueError("Order item not found")

    clean = []
    for n, r in enumerate(rows or [], start=1):
        # NOT _check_date(..., required=True): that silently substitutes today,
        # which would quietly promise a delivery for this afternoon.
        raw = _s(r.get("due_date"))
        if not raw:
            raise ValueError(f"Line {n}: a delivery date is required")
        date = _check_date(raw, f"Line {n}: date")
        try:
            qty = float(r.get("qty"))
        except (TypeError, ValueError):
            raise ValueError(f"Line {n}: quantity must be a number")
        if not math.isfinite(qty) or qty <= 0:
            raise ValueError(f"Line {n}: quantity must be greater than 0")
        clean.append({"due_date": date, "qty": qty, "note": (r.get("note") or "").strip()})

    total = sum(c["qty"] for c in clean)
    if total > item["qty"] + 1e-9:
        raise ValueError(
            f"The plan adds up to {total:g} but the item is only {item['qty']:g}")

    conn.execute("DELETE FROM order_schedule WHERE order_item_id=?", (order_item_id,))
    for c in clean:
        conn.execute(
            "INSERT INTO order_schedule (order_item_id, due_date, qty, note, created_at)"
            " VALUES (?,?,?,?,?)",
            (order_item_id, c["due_date"], c["qty"], c["note"], _now()))
    conn.commit()
    return {"ok": True, "planned": round(total, 3),
            "unplanned": round(item["qty"] - total, 3)}


def issue_material_doc(conn, order_id: int, data: dict, issued_by: str = "") -> dict:
    """Issue the order's bill of materials as a NUMBERED, FROZEN document.

    The on-screen rollup is live — it changes the moment anyone re-costs a
    drawing. A requisition handed to the store keeper must not, so every figure
    is copied in here at issue time. Re-costing later does not touch a sheet
    that is already out; you issue another one.

    Numbered from `doc_seq` with kind='material', the same per-financial-year
    machinery as quotations and invoices.
    """
    bom = order_bom(conn, order_id)
    if not bom["summary"]:
        raise ValueError(
            "Nothing to requisition — no part on this order prices its material "
            "from stock yet")

    issued_on = _check_date(data.get("issued_on"), "Issue date", required=True)
    notes = _s(data.get("notes"))
    cust = conn.execute(
        "SELECT c.name FROM customer_order o JOIN customer c ON c.id=o.customer_id"
        " WHERE o.id=?", (order_id,)).fetchone()

    doc_no = _next_material_no(conn, date.fromisoformat(issued_on))

    conn.execute("BEGIN IMMEDIATE")
    try:
        cur = conn.execute(
            "INSERT INTO material_doc (doc_no, order_id, order_no, customer_name,"
            " issued_on, issued_by, notes, total_cost, created_at)"
            " VALUES (?,?,?,?,?,?,?,?,?)",
            (doc_no, order_id, bom["order_no"], cust["name"] if cust else "",
             issued_on, _s(issued_by), notes, bom["total_cost"], _now()))
        doc_id = cur.lastrowid
        for m in bom["summary"]:
            conn.execute(
                "INSERT INTO material_doc_line (material_doc_id, heat_id, heat_number,"
                " material_label, unit, required, already_issued, unit_cost, cost,"
                " from_parts) VALUES (?,?,?,?,?,?,?,?,?,?)",
                (doc_id, m["heat_id"], m["heat_number"], m["material_label"],
                 m["unit"], m["required"], m["issued"],
                 round(m["cost"] / m["required"], 4) if m["required"] else 0,
                 m["cost"], ", ".join(dict.fromkeys(m["from_items"]))))
        conn.commit()
    except BaseException:
        conn.rollback()
        raise
    return get_material_doc(conn, doc_id)


def _next_material_no(conn, on: date) -> str:
    """Per-FY sequence for material requisitions (doc_seq kind='material')."""
    fmt = settings_mod.get_setting(conn, "material_number_format", "MRQ-{FY}-{SEQ}")
    fy = settings_mod.fy_label(on)
    conn.execute("BEGIN IMMEDIATE")
    try:
        row = conn.execute("SELECT seq FROM doc_seq WHERE kind='material' AND fy=?",
                           (fy,)).fetchone()
        seq = (row["seq"] if row else 0) + 1
        conn.execute("INSERT INTO doc_seq (kind, fy, seq) VALUES ('material',?,?)"
                     " ON CONFLICT(kind, fy) DO UPDATE SET seq=excluded.seq",
                     (fy, seq))
        conn.commit()
    except BaseException:
        conn.rollback()
        raise
    return settings_mod.render_order_no(fmt, on, seq)


def get_material_doc(conn, doc_id: int) -> dict:
    row = conn.execute("SELECT * FROM material_doc WHERE id=?", (doc_id,)).fetchone()
    if not row:
        raise ValueError("Requisition not found")
    d = dict(row)
    d["lines"] = [dict(r) for r in conn.execute(
        "SELECT * FROM material_doc_line WHERE material_doc_id=? ORDER BY id",
        (doc_id,))]
    return d


def list_material_docs(conn, order_id: int | None = None, q: str = "") -> list[dict]:
    sql = ("SELECT d.*, (SELECT COUNT(*) FROM material_doc_line l"
           "   WHERE l.material_doc_id=d.id) AS lines"
           " FROM material_doc d WHERE 1=1")
    args: list = []
    if order_id:
        sql += " AND d.order_id=?"
        args.append(order_id)
    if _s(q):
        like = f"%{_s(q)}%"
        sql += " AND (d.doc_no LIKE ? OR d.order_no LIKE ? OR d.customer_name LIKE ?)"
        args += [like] * 3
    sql += " ORDER BY d.id DESC"
    return [dict(r) for r in conn.execute(sql, args)]


def render_material_doc(conn, doc_id: int) -> str:
    """A4 requisition for the store keeper. Dependency-free HTML: the browser's
    own Save-as-PDF is the PDF engine, exactly like quotations and invoices."""
    d = get_material_doc(conn, doc_id)
    rules = get_rules()
    company = rules.get("company_name") or "APEX THERMOCON"

    rows = []
    for L in d["lines"]:
        balance = round(max(L["required"] - L["already_issued"], 0), 4)
        rows.append(
            f"<tr><td class='mono'>{_esc(L['heat_number'])}</td>"
            f"<td>{_esc(L['material_label'])}</td>"
            f"<td class='r'>{_num(L['required'])} {_esc(L['unit'])}</td>"
            f"<td class='r'>{_num(L['already_issued'])}</td>"
            f"<td class='r b'>{_num(balance)}</td>"
            f"<td class='r'>{_money(L['cost'])}</td></tr>"
            f"<tr class='src'><td></td><td colspan='5'>for {_esc(L['from_parts'])}</td></tr>")

    notes_block = (f"<div class='notes'><b>Notes</b><br>{_esc(d['notes'])}</div>"
                   if d["notes"] else "")
    issued_by = f" by {_esc(d['issued_by'])}" if d["issued_by"] else ""

    return f"""<!DOCTYPE html>
<html lang="en"><head><meta charset="UTF-8">
<title>{_esc(d['doc_no'])} — Material Requisition</title>
<style>
  @page {{ size: A4; margin: 14mm; }}
  body {{ font-family: ui-sans-serif, system-ui, "Segoe UI", Roboto, sans-serif;
         color: #111; font-size: 12px; margin: 0; }}
  .head {{ display: flex; justify-content: space-between; align-items: flex-start;
          border-bottom: 2px solid #111; padding-bottom: 10px; }}
  .co {{ font-size: 20px; font-weight: 800; letter-spacing: .01em; }}
  .kind {{ font-size: 15px; font-weight: 700; text-transform: uppercase;
          letter-spacing: .08em; }}
  .no {{ font-family: ui-monospace, Menlo, monospace; font-size: 15px; font-weight: 700; }}
  .meta {{ text-align: right; line-height: 1.5; }}
  .for {{ margin: 14px 0 10px; line-height: 1.5; }}
  .lbl {{ font-size: 9px; text-transform: uppercase; letter-spacing: .08em; color: #666; }}
  table {{ width: 100%; border-collapse: collapse; margin-top: 6px; }}
  th {{ text-align: left; font-size: 9px; text-transform: uppercase; letter-spacing: .06em;
       color: #444; border-bottom: 1px solid #111; padding: 6px 6px; }}
  td {{ padding: 6px; border-bottom: 1px solid #e5e5e5; vertical-align: top; }}
  td.r, th.r {{ text-align: right; }}
  td.b {{ font-weight: 700; }}
  td.mono {{ font-family: ui-monospace, Menlo, monospace; font-weight: 700; }}
  tr.src td {{ border-bottom: 1px solid #e5e5e5; font-size: 10px; color: #777;
              padding-top: 0; }}
  .tot {{ margin-top: 10px; text-align: right; font-size: 13px; }}
  .notes {{ margin-top: 14px; font-size: 11px; line-height: 1.5; }}
  .sign {{ margin-top: 34px; display: flex; gap: 40px; }}
  .sign div {{ flex: 1; border-top: 1px solid #111; padding-top: 5px; font-size: 10px;
              color: #444; }}
  .frozen {{ margin-top: 18px; font-size: 10px; color: #666; border-top: 1px dashed #bbb;
            padding-top: 8px; }}
  @media screen {{ body {{ max-width: 820px; margin: 24px auto; padding: 0 16px; }} }}
</style></head><body>
<div class="head">
  <div><div class="co">{_esc(company)}</div>
       <div class="kind">Material Requisition</div></div>
  <div class="meta"><div class="no">{_esc(d['doc_no'])}</div>
       <div>Issued {_esc(d['issued_on'])}{issued_by}</div></div>
</div>

<div class="for">
  <span class="lbl">Against order</span><br>
  <b class="no">{_esc(d['order_no'])}</b> &nbsp; {_esc(d['customer_name'] or '')}
</div>

<table>
  <thead><tr>
    <th>Heat</th><th>Material</th><th class="r">Required</th>
    <th class="r">Already issued</th><th class="r">To issue now</th><th class="r">Value</th>
  </tr></thead>
  <tbody>{''.join(rows)}</tbody>
</table>

<div class="tot">Committed value <b>{_money(d['total_cost'])}</b></div>
{notes_block}

<div class="sign">
  <div>Issued by</div><div>Store keeper</div><div>Received by</div>
</div>

<div class="frozen">
  These figures were frozen when this requisition was issued. Re-costing a
  drawing afterwards does not change this sheet — a new requisition is issued
  instead.
</div>
</body></html>"""


def _esc(v) -> str:
    return html.escape(str(v or ""), quote=False)


def _num(v) -> str:
    f = float(v or 0)
    return f"{f:.4f}".rstrip("0").rstrip(".") or "0"


def _money(v) -> str:
    """Indian grouping, e.g. 12,34,567.89 — the printed sheet must read locally."""
    f = float(v or 0)
    whole, dec = f"{abs(f):.2f}".split(".")
    if len(whole) > 3:
        head, tail = whole[:-3], whole[-3:]
        head = re.sub(r"(\d)(?=(\d\d)+$)", r"\1,", head)
        whole = f"{head},{tail}"
    return ("-" if f < 0 else "") + "\u20b9" + whole + "." + dec


def deadlines(conn, today: str = "") -> dict:
    """Orders whose delivery date is close, for the Home warning panel.

    Three buckets so the panel can say what is urgent without arithmetic in the
    template: already overdue, due within 7 days, and due in the rest of the
    next 31 days. Only orders that still have something left to ship appear —
    a fully delivered order is not a deadline any more.
    """
    ref = date.fromisoformat(today) if today else date.today()
    rows = []
    for r in conn.execute(
        "SELECT o.id, o.order_no, o.due_date, o.stage, c.name AS customer_name,"
        "       c.code AS customer_code"
        " FROM customer_order o JOIN customer c ON c.id=o.customer_id"
        " WHERE o.due_date IS NOT NULL AND TRIM(o.due_date) <> ''"
        "   AND o.stage NOT IN ('payment')"
        " ORDER BY o.due_date"
    ):
        o = dict(r)
        try:
            due = date.fromisoformat(o["due_date"])
        except ValueError:
            continue                      # a malformed date is not a deadline
        shipped = _shipped_by_item(conn, o["id"])
        qty = conn.execute(
            "SELECT COALESCE(SUM(qty),0) AS q FROM order_item WHERE order_id=?",
            (o["id"],)).fetchone()["q"]
        pending = round(qty - sum(shipped.values()), 3)
        if pending <= 0:
            continue                      # everything has gone out
        o["days_left"] = (due - ref).days
        o["qty_total"] = round(qty, 3)
        o["qty_pending"] = pending
        rows.append(o)

    return {
        "overdue":   [r for r in rows if r["days_left"] < 0],
        "this_week": [r for r in rows if 0 <= r["days_left"] <= 7],
        "this_month": [r for r in rows if 7 < r["days_left"] <= 31],
        "as_of": ref.isoformat(),
    }


def open_items(conn, order_id: int) -> list[dict]:
    """Items of one order with their still-unshipped quantity (for the form)."""
    shipped = _shipped_by_item(conn, order_id)
    out = []
    for r in conn.execute(
            "SELECT i.*, d.drawing_no, d.revision FROM order_item i LEFT JOIN drawing d"
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
    # Merge duplicate rows for the same item FIRST: checking them one by one
    # would let each pass against the same stored total and over-ship.
    want: dict[int, float] = {}
    for ln in lines:
        item_id = int(ln.get("order_item_id") or 0)
        want[item_id] = want.get(item_id, 0) + _check_qty(ln.get("qty"), "Line quantity")

    conn.execute("BEGIN IMMEDIATE")
    try:
        checked = []
        for item_id, qty in want.items():
            item = row_to_dict(conn.execute(
                "SELECT * FROM order_item WHERE id=?", (item_id,)).fetchone())
            if not item:
                raise ValueError("An order item on this consignment no longer exists")
            already = conn.execute(
                "SELECT COALESCE(SUM(qty),0) AS q FROM consignment_line WHERE order_item_id=?",
                (item_id,)).fetchone()["q"]
            if qty + already > item["qty"] + 1e-9:
                raise ValueError(
                    f"Only {item['qty'] - already:g} of {item['qty']:g} left to ship on that item")
            checked.append((item_id, qty))
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


class ScheduleLine(BaseModel):
    due_date: str
    qty: float
    note: str = ""


class ScheduleIn(BaseModel):
    lines: list[ScheduleLine] = []


class RequisitionIn(BaseModel):
    issued_on: str = ""          # blank = today
    notes: str = ""


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


@router.get("/deadlines")
def order_deadlines(conn=Depends(get_db)):
    """Home-screen warning panel: what is due, and what is already late."""
    return deadlines(conn)


@router.put("/items/{order_item_id}/schedule")
def item_schedule(order_item_id: int, body: ScheduleIn, conn=Depends(get_db)):
    return _400(set_schedule, conn, order_item_id,
                [r.model_dump() for r in body.lines])


@router.get("/requisitions")
def requisitions(q: str = "", conn=Depends(get_db)):
    """Every material requisition issued, newest first."""
    return {"rows": list_material_docs(conn, q=q)}


@router.get("/requisitions/{doc_id}")
def requisition_detail(doc_id: int, conn=Depends(get_db)):
    return _400(get_material_doc, conn, doc_id)


@router.get("/requisitions/{doc_id}/print", response_class=HTMLResponse)
def requisition_print(doc_id: int, conn=Depends(get_db)):
    """A4 sheet for the store keeper — print or Save as PDF from the browser."""
    try:
        return HTMLResponse(render_material_doc(conn, doc_id))
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


# Declared BEFORE the /{order_id} routes: '/attachments/3' must not be read as
# an order id.
@router.get("/attachments/{attachment_id}")
def order_attachment_view(attachment_id: int, download: bool = False, conn=Depends(get_db)):
    row = conn.execute(
        "SELECT * FROM order_attachment WHERE id=?", (attachment_id,)
    ).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Attachment not found")
    path = paths.order_files_dir() / row["stored_name"]
    if not path.is_file():
        raise HTTPException(status_code=404, detail="File is missing on disk")
    safe = header_filename(row["filename"])
    disposition = "attachment" if download else "inline"
    return FileResponse(
        path, media_type=response_mime(row["stored_name"]),
        headers={"Content-Disposition": f'{disposition}; filename="{safe}"'},
    )


@router.delete("/attachments/{attachment_id}")
def order_attachment_delete(attachment_id: int, conn=Depends(get_db)):
    return _400(delete_order_attachment, conn, attachment_id)


@router.post("/{order_id}/attachments")
def order_attachment_upload(order_id: int, label: str = Form(""),
                            files: list[UploadFile] = File(...),
                            conn=Depends(get_db)):
    # Sync on purpose (like every route here): the sqlite connection from
    # get_db lives in the threadpool, so the handler must run there too.
    items = [(f.filename or "", f.content_type or "", f.file.read()) for f in files]
    saved = _400(save_order_attachments, conn, order_id, label, items)
    return {"saved": saved, "order": get_order(conn, order_id)}


@router.post("/{order_id}/bom/issue")
def order_bom_issue(order_id: int, body: RequisitionIn,
                    user: dict = Depends(current_user), conn=Depends(get_db)):
    """Freeze the order's bill of materials into a numbered requisition."""
    return _400(issue_material_doc, conn, order_id, body.model_dump(),
                user.get("username", ""))


@router.get("/{order_id}/bom")
def order_bill_of_materials(order_id: int, conn=Depends(get_db)):
    """What material this order commits, rolled up from its parts' costings."""
    return _400(order_bom, conn, order_id)


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
