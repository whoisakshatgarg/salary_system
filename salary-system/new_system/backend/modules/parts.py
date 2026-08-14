"""Parts & Pricing — the drawing master everything hangs off.

One record per customer drawing (drawing number + revision): what the part
is, its material spec, the drawing files, the RATE HISTORY (dated quoted /
agreed / revised entries — "what did we charge last time"), and the costing
builder: operations × minutes × ₹/hour (+ material + margin) rolling up to a
₹/piece total that can be recorded straight into the rate history.

Operation names/rates come from Settings; each costing row snapshots the rate
it used, so later Settings edits never rewrite old costings.
"""

from __future__ import annotations

import math
import secrets
from datetime import date, datetime

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel

from ..core import paths
from ..core.attachments import (header_filename, response_mime, storage_ext,
                                validate_attachment)
from ..core.db import row_to_dict
from ..core.deps import get_db, require_module

router = APIRouter(prefix="/api/parts",
                   dependencies=[Depends(require_module("parts"))])

RATE_KINDS = ("quoted", "agreed", "revised")


def _s(v) -> str:
    return (v or "").strip()


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def op_cost(minutes: float, rate_per_hour: float, weightage: float = 1,
            extra_rate: float = 0) -> float:
    """Cost of ONE operation row, in the order the columns read:

        effective ₹/hr = rate per hour + additional margin (also ₹ PER HOUR)
        row total      = minutes / 60 * effective ₹/hr * weightage

    The additional margin is money added to the hourly rate — ₹400/hr plus a
    ₹50/hr margin is charged at ₹450/hr — not a percentage. Weightage covers
    "this operation counts more/less than its clock time" (setup spread over a
    batch, a second spindle, a scrap allowance).
    """
    return round(minutes / 60 * (rate_per_hour + extra_rate) * weightage, 2)


def bom_cost(unit_cost: float, qty_per_piece: float) -> float:
    """Material cost this line contributes to ONE piece.

    Rounded to paise only at the end: 1/3 of a ₹4500 rod is ₹1500 exactly, and
    rounding the 0.333333 first would have made it ₹1485.
    """
    return round((unit_cost or 0) * (qty_per_piece or 0), 2)


def costing_total(ops_total: float, material_cost: float, margin_pct: float) -> float:
    """THE rollup — display and costing_to_rate must always agree."""
    return round((ops_total + material_cost) * (1 + margin_pct / 100), 2)


def _check_date(v, label: str) -> str:
    v = _s(v) or date.today().isoformat()
    try:
        date.fromisoformat(v)
    except ValueError:
        raise ValueError(f"{label} isn't a real date (YYYY-MM-DD)")
    return v


def _check_money(v, label: str, allow_zero: bool = True) -> float:
    try:
        f = float(v)
    except (TypeError, ValueError):
        raise ValueError(f"{label} must be a number")
    if not math.isfinite(f) or f < 0 or f > 1e12 or (f == 0 and not allow_zero):
        raise ValueError(f"{label} must be a normal{'' if allow_zero else ', non-zero'} positive number")
    return round(f, 2)


def _check_ratio(v, label: str) -> float:
    """A quantity per piece, NOT money.

    Deliberately not _check_money: that rounds to 2 decimals, and one third of a
    rod is 0.333333 — rounding it to 0.33 quietly underprices every piece by 1%.
    """
    try:
        f = float(v)
    except (TypeError, ValueError):
        raise ValueError(f"{label} must be a number")
    if not math.isfinite(f) or f <= 0 or f > 1e9:
        raise ValueError(f"{label} must be a positive number")
    return round(f, 8)


# --------------------------------------------------------------------------- #
# Drawings
# --------------------------------------------------------------------------- #
def list_drawings(conn, q: str = "", customer_id=None, active_only: bool = True) -> list[dict]:
    sql = """SELECT d.*, c.name AS customer_name,
                    (SELECT COUNT(*) FROM drawing_file f WHERE f.drawing_id=d.id) AS files,
                    (SELECT r.rate FROM drawing_rate r WHERE r.drawing_id=d.id
                       ORDER BY r.rate_date DESC, r.id DESC LIMIT 1) AS latest_rate,
                    (SELECT r.kind FROM drawing_rate r WHERE r.drawing_id=d.id
                       ORDER BY r.rate_date DESC, r.id DESC LIMIT 1) AS latest_rate_kind
             FROM drawing d LEFT JOIN customer c ON c.id=d.customer_id WHERE 1=1"""
    args: list = []
    if active_only:
        sql += " AND d.active=1"
    if customer_id:
        sql += " AND d.customer_id=?"
        args.append(customer_id)
    if _s(q):
        like = f"%{_s(q)}%"
        sql += " AND (d.drawing_no LIKE ? OR d.description LIKE ? OR d.grade LIKE ? OR c.name LIKE ?)"
        args += [like] * 4
    sql += " ORDER BY d.drawing_no COLLATE NOCASE, d.revision"
    return [dict(r) for r in conn.execute(sql, args)]


def _validate_drawing(conn, data: dict, drawing_id: int | None = None) -> tuple[str, str]:
    dno = _s(data.get("drawing_no"))
    rev = _s(data.get("revision")) or "A"
    if not dno:
        raise ValueError("Drawing number is required")
    dup = conn.execute("SELECT id FROM drawing WHERE drawing_no=? AND revision=?",
                       (dno, rev)).fetchone()
    if dup and dup["id"] != drawing_id:
        raise ValueError(f"Drawing {dno} rev {rev} already exists")
    return dno, rev


def save_drawing(conn, data: dict, drawing_id: int | None = None) -> int:
    # BEGIN IMMEDIATE: the duplicate check and the write are one atomic step.
    conn.execute("BEGIN IMMEDIATE")
    try:
        dno, rev = _validate_drawing(conn, data, drawing_id)
        fields = (dno, rev, data.get("customer_id") or None, _s(data.get("description")),
                  _s(data.get("material_class")), _s(data.get("grade")),
                  _s(data.get("unit")) or "Nos", _s(data.get("notes")))
        if drawing_id is None:
            cur = conn.execute(
                """INSERT INTO drawing (drawing_no, revision, customer_id, description,
                     material_class, grade, unit, notes, active, created_at)
                   VALUES (?,?,?,?,?,?,?,?,1,?)""", (*fields, _now()))
            drawing_id = cur.lastrowid
        else:
            conn.execute(
                """UPDATE drawing SET drawing_no=?, revision=?, customer_id=?,
                     description=?, material_class=?, grade=?, unit=?, notes=?
                   WHERE id=?""", (*fields, drawing_id))
        conn.commit()
    except BaseException:
        conn.rollback()
        raise
    return drawing_id


def revise_drawing(conn, drawing_id: int, new_revision: str) -> int:
    """New revision = a fresh drawing row copying the master data (rates,
    costings and files stay with the old revision — pricing history is per
    revision)."""
    old = row_to_dict(conn.execute("SELECT * FROM drawing WHERE id=?",
                                   (drawing_id,)).fetchone())
    if not old:
        raise ValueError("Drawing not found")
    new_revision = _s(new_revision)
    if not new_revision or new_revision == old["revision"]:
        raise ValueError("Give the new revision a different letter/number")
    data = {**old, "revision": new_revision}
    return save_drawing(conn, data)


def get_drawing(conn, drawing_id: int) -> dict:
    d = row_to_dict(conn.execute(
        "SELECT d.*, c.name AS customer_name FROM drawing d"
        " LEFT JOIN customer c ON c.id=d.customer_id WHERE d.id=?",
        (drawing_id,)).fetchone())
    if not d:
        raise ValueError("Drawing not found")
    d["files"] = [dict(r) for r in conn.execute(
        "SELECT id, filename, mime, size_bytes, uploaded_at FROM drawing_file"
        " WHERE drawing_id=? ORDER BY id", (drawing_id,))]
    d["rates"] = [dict(r) for r in conn.execute(
        "SELECT * FROM drawing_rate WHERE drawing_id=?"
        " ORDER BY rate_date DESC, id DESC", (drawing_id,))]
    d["costings"] = []
    for c in conn.execute("SELECT * FROM costing WHERE drawing_id=? ORDER BY id DESC",
                          (drawing_id,)):
        c = dict(c)
        c["ops"] = [dict(r) for r in conn.execute(
            "SELECT * FROM costing_op WHERE costing_id=? ORDER BY id", (c["id"],))]
        c["materials"] = [dict(r) for r in conn.execute(
            "SELECT * FROM costing_material WHERE costing_id=? ORDER BY id", (c["id"],))]
        c["bom_total"] = round(sum(m["cost"] for m in c["materials"]), 2)
        ops_total = sum(o["cost"] for o in c["ops"])
        c["ops_total"] = round(ops_total, 2)
        c["subtotal"] = round(ops_total + c["material_cost"], 2)
        c["total"] = costing_total(ops_total, c["material_cost"], c["margin_pct"])
        d["costings"].append(c)
    # other revisions of the same drawing number, for quick navigation
    d["revisions"] = [dict(r) for r in conn.execute(
        "SELECT id, revision, active FROM drawing WHERE drawing_no=? ORDER BY revision",
        (d["drawing_no"],))]
    return d


def delete_drawing(conn, drawing_id: int) -> None:
    conn.execute("BEGIN IMMEDIATE")  # check + delete atomically
    try:
        n = conn.execute("SELECT COUNT(*) AS n FROM order_item WHERE drawing_id=?",
                         (drawing_id,)).fetchone()["n"]
        if n:
            raise ValueError("This drawing is used on orders — deactivate it instead")
        stored = [r["stored_name"] for r in conn.execute(
            "SELECT stored_name FROM drawing_file WHERE drawing_id=?", (drawing_id,))]
        cur = conn.execute("DELETE FROM drawing WHERE id=?", (drawing_id,))
        if not cur.rowcount:
            raise ValueError("Drawing not found")
        conn.commit()
    except BaseException:
        conn.rollback()
        raise
    for name in stored:
        (paths.drawing_files_dir() / name).unlink(missing_ok=True)


# --------------------------------------------------------------------------- #
# Rate history
# --------------------------------------------------------------------------- #
def add_rate(conn, drawing_id: int, data: dict) -> dict:
    kind = _s(data.get("kind"))
    if kind not in RATE_KINDS:
        raise ValueError("Rate kind must be quoted, agreed or revised")
    rate = _check_money(data.get("rate"), "Rate", allow_zero=False)
    rate_date = _check_date(data.get("rate_date"), "Rate date")
    if not conn.execute("SELECT 1 FROM drawing WHERE id=?", (drawing_id,)).fetchone():
        raise ValueError("Drawing not found")
    conn.execute(
        "INSERT INTO drawing_rate (drawing_id, kind, rate, rate_date, note, created_at)"
        " VALUES (?,?,?,?,?,?)",
        (drawing_id, kind, rate, rate_date, _s(data.get("note")), _now()))
    conn.commit()
    return get_drawing(conn, drawing_id)


def delete_rate(conn, rate_id: int) -> dict:
    row = conn.execute("SELECT drawing_id FROM drawing_rate WHERE id=?",
                       (rate_id,)).fetchone()
    if not row:
        raise ValueError("Rate entry not found")
    conn.execute("DELETE FROM drawing_rate WHERE id=?", (rate_id,))
    conn.commit()
    return get_drawing(conn, row["drawing_id"])


# --------------------------------------------------------------------------- #
# Costing builder
# --------------------------------------------------------------------------- #
def save_costing(conn, drawing_id: int, data: dict) -> dict:
    if not conn.execute("SELECT 1 FROM drawing WHERE id=?", (drawing_id,)).fetchone():
        raise ValueError("Drawing not found")
    margin = _check_money(data.get("margin_pct") or 0, "Margin %")
    ops = data.get("ops") or []

    # Bill of materials: priced from inventory, snapshotted here. When BOM lines
    # exist they ARE the material cost — a typed-in figure alongside them would
    # be a second source of truth for the same number.
    bom = []
    for n, m in enumerate(data.get("materials") or [], start=1):
        label = _s(m.get("heat_number")) or _s(m.get("material_label"))
        if not label:
            raise ValueError(f"Material line {n}: pick a material")
        unit_cost = _check_money(m.get("unit_cost") or 0, f"{label}: unit cost")
        qty = _check_ratio(m.get("qty_per_piece"), f"{label}: quantity per piece")
        hid = m.get("heat_id")
        bom.append((int(hid) if hid else None, _s(m.get("heat_number")),
                    _s(m.get("material_label")), _s(m.get("unit")) or "rod",
                    unit_cost, qty, bom_cost(unit_cost, qty)))

    if bom:
        material = round(sum(b[6] for b in bom), 2)
    else:
        material = _check_money(data.get("material_cost") or 0, "Material cost")
    if not ops and not material:
        raise ValueError("Add at least one operation or a material cost")
    checked = []
    for o in ops:
        name = _s(o.get("operation"))
        if not name:
            raise ValueError("Every row needs an operation")
        minutes = _check_money(o.get("minutes"), f"{name}: minutes", allow_zero=False)
        rate = _check_money(o.get("rate_per_hour"), f"{name}: ₹/hour")
        weightage = _check_money(o.get("weightage") if o.get("weightage") not in (None, "") else 1,
                                 f"{name}: weightage", allow_zero=False)
        extra = _check_money(o.get("extra_rate") or 0, f"{name}: additional ₹/hour")
        checked.append((name, minutes, rate, weightage, extra,
                        op_cost(minutes, rate, weightage, extra)))
    cur = conn.execute(
        "INSERT INTO costing (drawing_id, material_cost, margin_pct, notes, created_at)"
        " VALUES (?,?,?,?,?)",
        (drawing_id, material, margin, _s(data.get("notes")), _now()))
    for name, minutes, rate, weightage, extra, cost in checked:
        conn.execute(
            "INSERT INTO costing_op (costing_id, operation, minutes, rate_per_hour,"
            " weightage, extra_rate, cost) VALUES (?,?,?,?,?,?,?)",
            (cur.lastrowid, name, minutes, rate, weightage, extra, cost))
    for hid, hn, label, unit, unit_cost, qty, cost in bom:
        conn.execute(
            "INSERT INTO costing_material (costing_id, heat_id, heat_number,"
            " material_label, unit, unit_cost, qty_per_piece, cost)"
            " VALUES (?,?,?,?,?,?,?,?)",
            (cur.lastrowid, hid, hn, label, unit, unit_cost, qty, cost))
    conn.commit()
    return get_drawing(conn, drawing_id)


def delete_costing(conn, costing_id: int) -> dict:
    row = conn.execute("SELECT drawing_id FROM costing WHERE id=?",
                       (costing_id,)).fetchone()
    if not row:
        raise ValueError("Costing not found")
    conn.execute("DELETE FROM costing WHERE id=?", (costing_id,))
    conn.commit()
    return get_drawing(conn, row["drawing_id"])


def costing_to_rate(conn, costing_id: int, kind: str, note: str = "") -> dict:
    row = conn.execute("SELECT * FROM costing WHERE id=?", (costing_id,)).fetchone()
    if not row:
        raise ValueError("Costing not found")
    ops_total = conn.execute(
        "SELECT COALESCE(SUM(cost),0) AS t FROM costing_op WHERE costing_id=?",
        (costing_id,)).fetchone()["t"]
    total = costing_total(ops_total, row["material_cost"], row["margin_pct"])
    return add_rate(conn, row["drawing_id"], {
        "kind": kind, "rate": total, "rate_date": date.today().isoformat(),
        "note": _s(note) or f"From costing #{costing_id}",
    })


# --------------------------------------------------------------------------- #
# Drawing files (shared attachment plumbing)
# --------------------------------------------------------------------------- #
def save_files(conn, drawing_id: int, items: list[tuple[str, str, bytes]]) -> list[dict]:
    if not conn.execute("SELECT 1 FROM drawing WHERE id=?", (drawing_id,)).fetchone():
        raise ValueError("Drawing not found")
    checked = [(fn, validate_attachment(mime, content), content)
               for fn, mime, content in items]
    written = []
    try:
        for filename, mime, content in checked:
            stored = f"d{drawing_id}-{secrets.token_hex(8)}{storage_ext(filename, mime)}"
            path = paths.drawing_files_dir() / stored
            path.write_bytes(content)
            written.append(path)
            conn.execute(
                "INSERT INTO drawing_file (drawing_id, filename, mime, size_bytes,"
                " stored_name, uploaded_at) VALUES (?,?,?,?,?,?)",
                (drawing_id, _s(filename) or stored, mime, len(content), stored, _now()))
        conn.commit()
    except BaseException:
        conn.rollback()
        for p in written:
            p.unlink(missing_ok=True)
        raise
    return [dict(r) for r in conn.execute(
        "SELECT id, filename, mime, size_bytes, uploaded_at FROM drawing_file"
        " WHERE drawing_id=? ORDER BY id", (drawing_id,))]


def delete_file(conn, file_id: int) -> dict:
    row = row_to_dict(conn.execute("SELECT * FROM drawing_file WHERE id=?",
                                   (file_id,)).fetchone())
    if not row:
        raise ValueError("File not found")
    conn.execute("DELETE FROM drawing_file WHERE id=?", (file_id,))
    conn.commit()
    (paths.drawing_files_dir() / row["stored_name"]).unlink(missing_ok=True)
    return get_drawing(conn, row["drawing_id"])


# --------------------------------------------------------------------------- #
# Routes
# --------------------------------------------------------------------------- #
class DrawingIn(BaseModel):
    drawing_no: str
    revision: str = "A"
    customer_id: int | None = None
    description: str = ""
    material_class: str = ""
    grade: str = ""
    unit: str = "Nos"
    notes: str = ""


class RateEntryIn(BaseModel):
    kind: str
    rate: float
    rate_date: str = ""
    note: str = ""


class CostingOpIn(BaseModel):
    operation: str
    minutes: float
    rate_per_hour: float
    weightage: float = 1
    extra_rate: float = 0


class BomLineIn(BaseModel):
    """One stock item the part is made from, priced from inventory."""
    heat_id: int | None = None
    heat_number: str = ""
    material_label: str = ""
    unit: str = "rod"             # 'rod' | 'kg'
    unit_cost: float = 0          # ₹ per unit, snapshotted at save
    qty_per_piece: float = 0


class CostingIn(BaseModel):
    material_cost: float = 0      # ignored when `materials` is non-empty
    margin_pct: float = 0
    notes: str = ""
    ops: list[CostingOpIn] = []
    materials: list[BomLineIn] = []


class ReviseIn(BaseModel):
    revision: str


class ToRateIn(BaseModel):
    kind: str = "quoted"
    note: str = ""


def _400(fn, *args, **kw):
    try:
        return fn(*args, **kw)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/refs")
def refs(customer_id: int | None = None, conn=Depends(get_db)):
    """Form reference data for THIS module (parts grant).

    When a customer is given, their negotiated per-operation rates replace the
    standard ones (and each row says so), because a costing is always priced
    for somebody.
    """
    from . import customers as customers_mod
    from . import settings as settings_mod
    ops = [dict(o, extra_rate=0.0, custom=False) for o in settings_mod.operations(conn)]
    if customer_id:
        override = customers_mod.operation_rates(conn, customer_id)
        for o in ops:
            c = override.get(o["name"])
            if c:
                o.update(rate_per_hour=c["rate_per_hour"], extra_rate=c["extra_rate"],
                         custom=True, note=c.get("note") or "")
    return {
        "customers": settings_mod.active_customers(conn),
        "units": settings_mod.units(conn),
        "operations": ops,
    }


@router.get("/drawings")
def drawings(q: str = "", customer_id: int | None = None, active_only: bool = True,
             conn=Depends(get_db)):
    return list_drawings(conn, q=q, customer_id=customer_id, active_only=active_only)


@router.post("/drawings")
def drawing_create(body: DrawingIn, conn=Depends(get_db)):
    did = _400(save_drawing, conn, body.model_dump())
    return get_drawing(conn, did)


@router.get("/drawings/{drawing_id}")
def drawing_detail(drawing_id: int, conn=Depends(get_db)):
    try:
        return get_drawing(conn, drawing_id)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.put("/drawings/{drawing_id}")
def drawing_update(drawing_id: int, body: DrawingIn, conn=Depends(get_db)):
    _400(save_drawing, conn, body.model_dump(), drawing_id)
    return get_drawing(conn, drawing_id)


@router.post("/drawings/{drawing_id}/revise")
def drawing_revise(drawing_id: int, body: ReviseIn, conn=Depends(get_db)):
    new_id = _400(revise_drawing, conn, drawing_id, body.revision)
    return get_drawing(conn, new_id)


@router.post("/drawings/{drawing_id}/active")
def drawing_active(drawing_id: int, active: bool, conn=Depends(get_db)):
    conn.execute("UPDATE drawing SET active=? WHERE id=?", (int(active), drawing_id))
    conn.commit()
    return get_drawing(conn, drawing_id)


@router.delete("/drawings/{drawing_id}")
def drawing_delete(drawing_id: int, conn=Depends(get_db)):
    _400(delete_drawing, conn, drawing_id)
    return {"ok": True}


@router.post("/drawings/{drawing_id}/rates")
def rate_add(drawing_id: int, body: RateEntryIn, conn=Depends(get_db)):
    return _400(add_rate, conn, drawing_id, body.model_dump())


@router.delete("/rates/{rate_id}")
def rate_delete(rate_id: int, conn=Depends(get_db)):
    return _400(delete_rate, conn, rate_id)


@router.post("/drawings/{drawing_id}/costings")
def costing_add(drawing_id: int, body: CostingIn, conn=Depends(get_db)):
    return _400(save_costing, conn, drawing_id, body.model_dump())


@router.delete("/costings/{costing_id}")
def costing_delete(costing_id: int, conn=Depends(get_db)):
    return _400(delete_costing, conn, costing_id)


@router.post("/costings/{costing_id}/to-rate")
def costing_apply(costing_id: int, body: ToRateIn, conn=Depends(get_db)):
    return _400(costing_to_rate, conn, costing_id, body.kind, body.note)


@router.post("/drawings/{drawing_id}/files")
def files_upload(drawing_id: int, files: list[UploadFile] = File(...),
                 conn=Depends(get_db)):
    items = [(f.filename or "", f.content_type or "", f.file.read()) for f in files]
    return _400(save_files, conn, drawing_id, items)


@router.get("/files/{file_id}")
def file_view(file_id: int, download: bool = False, conn=Depends(get_db)):
    row = conn.execute("SELECT * FROM drawing_file WHERE id=?", (file_id,)).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="File not found")
    path = paths.drawing_files_dir() / row["stored_name"]
    if not path.is_file():
        raise HTTPException(status_code=404, detail="File is missing on disk")
    disposition = "attachment" if download else "inline"
    return FileResponse(
        path, media_type=response_mime(row["stored_name"]),
        headers={"Content-Disposition":
                 f'{disposition}; filename="{header_filename(row["filename"])}"'})


@router.delete("/files/{file_id}")
def file_delete(file_id: int, conn=Depends(get_db)):
    return _400(delete_file, conn, file_id)
