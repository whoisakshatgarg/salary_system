"""Raw-material inventory: one record per incoming HEAT (a mill batch of rods).

Admin-only module, opened from the dashboard in its own window
(``/inventory.html``). Design decisions (per the CEO):

* **Heat number** is the user-facing key (unique) but sits on a surrogate
  ``heat.id`` so even the heat number can be corrected after creation.
* **Stock is never stored** — remaining = received − Σ movements, derived on
  every read. Status: ``in_stock`` while rods remain; at zero it is
  ``rejected`` if the *latest* log entry is a rejection (deleting that entry
  un-rejects the heat), else ``consumed``.
* **Dropdown lists** (material class, shape, grade, element) are
  user-extensible rows in ``inv_option`` and are stored denormalized on each
  heat, so pruning a list never corrupts history.
* **Attachments live on disk** (``inventory_files/`` next to salary.db), the
  DB keeps metadata only. A full backup = salary.db + that folder.
* Order IDs are free text; issues require one, rejections don't.

All data functions take an open sqlite3 connection and raise ``ValueError``
for user mistakes; the router maps those to HTTP 400.
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

from ..core import db, paths
from ..core.attachments import (MAX_FILE_BYTES, header_filename,
                                response_mime, storage_ext, validate_attachment)
from ..core.deps import get_db, require_module

# Grant-gated since the shell landed: any account the owner grants 'inventory'
# can use it (admins always can). Operator edition stays excluded (deps.py).
router = APIRouter(prefix="/api/inventory", dependencies=[Depends(require_module("inventory"))])

OPTION_KINDS = ("material_class", "shape", "grade", "element")
ATTACHMENT_KINDS = ("certificate", "invoice")
MOVEMENT_TYPES = ("issue", "reject")

DEFAULT_OPTIONS = {
    "material_class": ["Steel", "Stainless Steel", "Alloy Steel", "Brass",
                       "Copper", "Bronze", "Aluminium", "Cast Iron"],
    "shape": ["Round", "Square", "Hexagonal", "Flat", "Pipe/Tube", "Angle"],
    "grade": ["EN8", "EN19", "EN24", "EN31", "C45", "20MnCr5",
              "SS304", "SS316", "C36000"],
    "element": ["C", "Si", "Mn", "P", "S", "Cr", "Ni", "Mo",
                "Cu", "Al", "Zn", "Sn", "Pb", "Fe"],
}


def ensure_defaults(db_path=None) -> None:
    """Seed the four dropdown lists — FIRST RUN ONLY. Once any option exists the
    user owns the lists: re-seeding on every launch would resurrect values they
    deliberately deleted."""
    conn = db.connect(db_path)
    try:
        if conn.execute("SELECT 1 FROM inv_option LIMIT 1").fetchone():
            return
        for kind, values in DEFAULT_OPTIONS.items():
            for v in values:
                conn.execute(
                    "INSERT OR IGNORE INTO inv_option (kind, value) VALUES (?,?)",
                    (kind, v),
                )
        conn.commit()
    finally:
        conn.close()


def _s(v) -> str:
    return (v or "").strip()


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


# --------------------------------------------------------------------------- #
# Options (the four user-extensible dropdown lists)
# --------------------------------------------------------------------------- #
def list_options(conn) -> dict:
    out: dict[str, list[str]] = {k: [] for k in OPTION_KINDS}
    for r in conn.execute("SELECT kind, value FROM inv_option ORDER BY value COLLATE NOCASE"):
        if r["kind"] in out:
            out[r["kind"]].append(r["value"])
    return out


def add_option(conn, kind: str, value: str) -> dict:
    if kind not in OPTION_KINDS:
        raise ValueError("Unknown list")
    value = _s(value)
    if not value:
        raise ValueError("Value can't be empty")
    conn.execute("INSERT OR IGNORE INTO inv_option (kind, value) VALUES (?,?)", (kind, value))
    conn.commit()
    return list_options(conn)


def delete_option(conn, kind: str, value: str) -> dict:
    if kind not in OPTION_KINDS:
        raise ValueError("Unknown list")
    conn.execute("DELETE FROM inv_option WHERE kind=? AND value=?", (kind, value))
    conn.commit()
    return list_options(conn)


def _learn_options(conn, data: dict) -> None:
    """Anything typed on the heat form joins its dropdown list automatically."""
    for kind, field in (("material_class", "material_class"),
                        ("shape", "shape"), ("grade", "grade")):
        v = _s(data.get(field))
        if v:
            conn.execute("INSERT OR IGNORE INTO inv_option (kind, value) VALUES (?,?)", (kind, v))
    for c in data.get("composition") or []:
        el = _s(c.get("element"))
        if el:
            conn.execute("INSERT OR IGNORE INTO inv_option (kind, value) VALUES ('element',?)", (el,))


# --------------------------------------------------------------------------- #
# Heats
# --------------------------------------------------------------------------- #
def _validate_heat(conn, data: dict, heat_id: int | None = None) -> tuple[str, int]:
    hn = _s(data.get("heat_number"))
    if not hn:
        raise ValueError("Heat number is required")
    row = conn.execute("SELECT id FROM heat WHERE heat_number=?", (hn,)).fetchone()
    if row and row["id"] != heat_id:
        raise ValueError(f"Heat {hn} already exists")
    _check_date(data.get("date_received"), "Date received")
    try:
        rods = int(data.get("rods_received"))
    except (TypeError, ValueError):
        raise ValueError("Rods received must be a whole number")
    if rods < 1:
        raise ValueError("Rods received must be at least 1")
    if heat_id is not None:
        moved = conn.execute(
            "SELECT COALESCE(SUM(rods),0) AS n FROM heat_movement WHERE heat_id=?",
            (heat_id,),
        ).fetchone()["n"]
        if rods < moved:
            raise ValueError(
                f"Rods received can't be less than the {moved} already issued/rejected"
            )
    seen: set[str] = set()
    for c in data.get("composition") or []:
        el = _s(c.get("element"))
        if not el:
            raise ValueError("Every composition row needs an element")
        if el.lower() in seen:
            raise ValueError(f"Element {el} is listed twice")
        seen.add(el.lower())
        try:
            pct = float(c.get("percent"))
        except (TypeError, ValueError):
            raise ValueError(f"Bad percentage for {el}")
        if not 0 <= pct <= 100:
            raise ValueError(f"{el}: percentage must be between 0 and 100")
    for field, label in (("total_weight_kg", "Total weight"),
                         ("price_total", "Purchase price"),
                         ("price_rate_per_kg", "Rate per kg")):
        _check_number(data.get(field), label)
    return hn, rods


def _check_date(value, label: str) -> str:
    """YYYY-MM-DD and a REAL calendar date (the regex alone lets 2026-13-45 in)."""
    v = _s(value)
    if not re.match(r"^\d{4}-\d{2}-\d{2}$", v):
        raise ValueError(f"{label} is required (YYYY-MM-DD)")
    try:
        date.fromisoformat(v)
    except ValueError:
        raise ValueError(f"{label} isn't a real calendar date")
    return v


# Generously above any real figure this shop will ever type (a hundred thousand
# crore) but ~296 orders of magnitude below the point where the pro-rata
# multiplication in _decorate() overflows to inf.
_MAX_NUMBER = 1e12


def _check_number(v, label: str) -> float | None:
    """Optional non-negative FINITE number, bounded. Infinity is legal JSON
    (1e999) and would poison every stats/list response with an unserialisable
    float — and so would a merely-huge value like 1e308, which survives
    isfinite() but overflows once multiplied by the rod count."""
    if v in (None, ""):
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        raise ValueError(f"{label} must be a number")
    if not math.isfinite(f) or f < 0:
        raise ValueError(f"{label} must be a normal, non-negative number")
    if f > _MAX_NUMBER:
        raise ValueError(f"{label} looks wrong — it must be under {_MAX_NUMBER:,.0f}")
    return f


_HEAT_FIELDS = ("heat_number", "date_received", "supplier", "material_class",
                "grade", "shape", "size_section", "rods_received",
                "total_weight_kg", "rack", "price_total", "price_rate_per_kg",
                "notes")


def _heat_row_values(data: dict, hn: str, rods: int) -> list:
    def num(v):
        return float(v) if v not in (None, "") else None
    return [
        hn, _s(data.get("date_received")), _s(data.get("supplier")),
        _s(data.get("material_class")), _s(data.get("grade")), _s(data.get("shape")),
        _s(data.get("size_section")), rods, num(data.get("total_weight_kg")),
        _s(data.get("rack")), num(data.get("price_total")),
        num(data.get("price_rate_per_kg")), _s(data.get("notes")),
    ]


def _write_composition(conn, heat_id: int, composition: list[dict]) -> None:
    conn.execute("DELETE FROM heat_composition WHERE heat_id=?", (heat_id,))
    for c in composition or []:
        conn.execute(
            "INSERT INTO heat_composition (heat_id, element, percent) VALUES (?,?,?)",
            (heat_id, _s(c["element"]), float(c["percent"])),
        )


def create_heat(conn, data: dict) -> int:
    hn, rods = _validate_heat(conn, data)
    cur = conn.execute(
        f"INSERT INTO heat ({','.join(_HEAT_FIELDS)}, created_at)"
        f" VALUES ({','.join('?' * len(_HEAT_FIELDS))}, ?)",
        (*_heat_row_values(data, hn, rods), _now()),
    )
    heat_id = cur.lastrowid
    _write_composition(conn, heat_id, data.get("composition") or [])
    _learn_options(conn, data)
    conn.commit()
    return heat_id


def update_heat(conn, heat_id: int, data: dict) -> None:
    if not conn.execute("SELECT 1 FROM heat WHERE id=?", (heat_id,)).fetchone():
        raise ValueError("Heat not found")
    hn, rods = _validate_heat(conn, data, heat_id=heat_id)
    sets = ", ".join(f"{f}=?" for f in _HEAT_FIELDS)
    conn.execute(
        f"UPDATE heat SET {sets} WHERE id=?",
        (*_heat_row_values(data, hn, rods), heat_id),
    )
    _write_composition(conn, heat_id, data.get("composition") or [])
    _learn_options(conn, data)
    conn.commit()


def delete_heat(conn, heat_id: int) -> None:
    if not conn.execute("SELECT 1 FROM heat WHERE id=?", (heat_id,)).fetchone():
        raise ValueError("Heat not found")
    n = conn.execute(
        "SELECT COUNT(*) AS n FROM heat_movement WHERE heat_id=?", (heat_id,)
    ).fetchone()["n"]
    if n:
        raise ValueError(
            "This heat has usage-log entries — delete those first if you really "
            "mean to remove the whole heat"
        )
    stored = [r["stored_name"] for r in conn.execute(
        "SELECT stored_name FROM heat_attachment WHERE heat_id=?", (heat_id,))]
    conn.execute("DELETE FROM heat WHERE id=?", (heat_id,))  # cascades comp+attach
    conn.commit()
    for name in stored:
        (paths.inventory_files_dir() / name).unlink(missing_ok=True)


# --------------------------------------------------------------------------- #
# Derived stock
# --------------------------------------------------------------------------- #
def _movement_totals(conn) -> dict[int, dict]:
    """{heat_id: {'out', 'issued', 'rejected', 'last_type'}} in two queries."""
    out: dict[int, dict] = {}
    for r in conn.execute(
        "SELECT heat_id, COALESCE(SUM(rods),0) AS total,"
        " COALESCE(SUM(CASE WHEN type='issue' THEN rods END),0) AS issued,"
        " COALESCE(SUM(CASE WHEN type='reject' THEN rods END),0) AS rejected"
        " FROM heat_movement GROUP BY heat_id"
    ):
        out[r["heat_id"]] = {"out": r["total"], "issued": r["issued"],
                             "rejected": r["rejected"], "last_type": None}
    for r in conn.execute(
        "SELECT heat_id, type FROM heat_movement"
        " WHERE id IN (SELECT MAX(id) FROM heat_movement GROUP BY heat_id)"
    ):
        out[r["heat_id"]]["last_type"] = r["type"]
    return out


def _status(remaining: int, last_type: str | None) -> str:
    if remaining > 0:
        return "in_stock"
    return "rejected" if last_type == "reject" else "consumed"


def _prorata(price, remaining: int, received: int) -> float | None:
    """Value of the rods still on the rack.

    Defence in depth: no single stored row may make the whole list or stats
    endpoint unserialisable. A price that overflows the multiplication (or a
    zero rod count) yields None rather than inf, so a database that was poisoned
    before the write-side bound existed still READS, and the bad heat can be
    opened and corrected from the UI instead of hiding the entire register.
    """
    if not price or not received:
        return None
    v = price * remaining / received
    return v if math.isfinite(v) else None


def _decorate(heat: dict, mv: dict | None) -> dict:
    mv = mv or {"out": 0, "issued": 0, "rejected": 0, "last_type": None}
    remaining = heat["rods_received"] - mv["out"]
    price = heat.get("price_total")
    heat.update(
        remaining=remaining,
        issued=mv["issued"],
        rejected_rods=mv["rejected"],
        status=_status(remaining, mv["last_type"]),
        # pro-rata value of what's still on the rack
        stock_value=_prorata(price, remaining, heat["rods_received"]),
    )
    return heat


# --------------------------------------------------------------------------- #
# List, search, stats
# --------------------------------------------------------------------------- #
SORTS = ("newest", "oldest", "remaining_asc", "remaining_desc")


def list_heats(conn, *, q: str = "", material_class: str = "", shape: str = "",
               status: str = "", element: str = "", pct_min=None, pct_max=None,
               sort: str = "newest") -> dict:
    sql = "SELECT * FROM heat WHERE 1=1"
    args: list = []
    if _s(q):
        like = f"%{_s(q)}%"
        sql += (" AND (heat_number LIKE ? OR grade LIKE ? OR supplier LIKE ?"
                " OR rack LIKE ?)")
        args += [like] * 4
    if _s(material_class):
        sql += " AND material_class=?"
        args.append(material_class)
    if _s(shape):
        sql += " AND shape=?"
        args.append(shape)
    if _s(element):
        lo = float(pct_min) if pct_min not in (None, "") else 0.0
        hi = float(pct_max) if pct_max not in (None, "") else 100.0
        sql += (" AND id IN (SELECT heat_id FROM heat_composition"
                " WHERE element=? COLLATE NOCASE AND percent BETWEEN ? AND ?)")
        args += [_s(element), lo, hi]

    totals = _movement_totals(conn)
    rows = [_decorate(dict(r), totals.get(r["id"]))
            for r in conn.execute(sql, args).fetchall()]
    if _s(status):
        rows = [r for r in rows if r["status"] == status]

    if sort == "oldest":
        rows.sort(key=lambda r: (r["date_received"], r["id"]))
    elif sort == "remaining_asc":
        rows.sort(key=lambda r: (r["remaining"], r["heat_number"]))
    elif sort == "remaining_desc":
        rows.sort(key=lambda r: (-r["remaining"], r["heat_number"]))
    else:  # newest
        rows.sort(key=lambda r: (r["date_received"], r["id"]), reverse=True)

    return {"rows": rows, "stats": overall_stats(conn, totals)}


def overall_stats(conn, totals: dict | None = None) -> dict:
    """The stat strip — always for the whole store room, not the filtered view."""
    totals = totals if totals is not None else _movement_totals(conn)
    heats = [dict(r) for r in conn.execute(
        "SELECT id, rods_received, price_total FROM heat")]
    in_stock = 0
    value = 0.0
    issued = 0
    for h in heats:
        mv = totals.get(h["id"], {"out": 0, "issued": 0})
        remaining = h["rods_received"] - mv["out"]
        in_stock += remaining
        issued += mv["issued"]
        value += _prorata(h["price_total"], remaining, h["rods_received"]) or 0.0
    if not math.isfinite(value):   # many merely-large rows can still sum to inf
        value = 0.0
    return {"total_heats": len(heats), "rods_in_stock": in_stock,
            "rods_issued": issued, "stock_value": round(value)}


def get_heat(conn, heat_id: int) -> dict:
    row = conn.execute("SELECT * FROM heat WHERE id=?", (heat_id,)).fetchone()
    if not row:
        raise ValueError("Heat not found")
    heat = _decorate(dict(row), _movement_totals(conn).get(heat_id))
    heat["composition"] = [dict(r) for r in conn.execute(
        "SELECT element, percent FROM heat_composition WHERE heat_id=? ORDER BY id",
        (heat_id,))]
    heat["movements"] = [dict(r) for r in conn.execute(
        "SELECT * FROM heat_movement WHERE heat_id=? ORDER BY mv_date DESC, id DESC",
        (heat_id,))]
    heat["attachments"] = [dict(r) for r in conn.execute(
        "SELECT id, kind, filename, mime, size_bytes, uploaded_at"
        " FROM heat_attachment WHERE heat_id=? ORDER BY id", (heat_id,))]
    return heat


# --------------------------------------------------------------------------- #
# Usage log (movements)
# --------------------------------------------------------------------------- #
def _remaining(conn, heat_id: int) -> int:
    row = conn.execute(
        "SELECT h.rods_received - COALESCE(SUM(m.rods),0) AS remaining"
        " FROM heat h LEFT JOIN heat_movement m ON m.heat_id=h.id"
        " WHERE h.id=? GROUP BY h.id", (heat_id,)
    ).fetchone()
    if not row:
        raise ValueError("Heat not found")
    return row["remaining"]


def add_movement(conn, heat_id: int, data: dict) -> dict:
    mtype = _s(data.get("type"))
    if mtype not in MOVEMENT_TYPES:
        raise ValueError("Type must be Issued or Rejected")
    try:
        rods = int(data.get("rods"))
    except (TypeError, ValueError):
        raise ValueError("Rods must be a whole number")
    if rods < 1:
        raise ValueError("Rods must be at least 1")
    order_id = _s(data.get("order_id"))
    if mtype == "issue" and not order_id:
        raise ValueError("Order ID is required when issuing rods")
    if mtype == "reject":
        order_id = ""  # a return has no order — drop anything stale the UI sent
    mv_date = _s(data.get("mv_date")) or date.today().isoformat()
    _check_date(mv_date, "Date")
    weight = _check_number(data.get("weight_kg"), "Weight")

    # Atomic check-then-insert: BEGIN IMMEDIATE takes the write lock BEFORE the
    # remaining is read, so two concurrent submits (double-click, second window)
    # can't both pass the check — the loser re-reads after the winner commits.
    conn.execute("BEGIN IMMEDIATE")
    try:
        remaining = _remaining(conn, heat_id)
        if rods > remaining:
            raise ValueError(f"Only {remaining} rod(s) remaining in this heat")
        conn.execute(
            "INSERT INTO heat_movement (heat_id, mv_date, type, order_id, rods,"
            " weight_kg, remarks, created_at) VALUES (?,?,?,?,?,?,?,?)",
            (heat_id, mv_date, mtype, order_id or None, rods, weight,
             _s(data.get("remarks")), _now()),
        )
        conn.commit()
    except BaseException:
        conn.rollback()
        raise
    return get_heat(conn, heat_id)


def reject_remaining(conn, heat_id: int, mv_date: str = "", remarks: str = "") -> dict:
    remaining = _remaining(conn, heat_id)
    if remaining <= 0:
        raise ValueError("Nothing left in this heat to reject")
    return add_movement(conn, heat_id, {
        "type": "reject", "rods": remaining, "mv_date": mv_date,
        "remarks": _s(remarks) or "Full remaining batch rejected & returned to supplier",
    })


def delete_movement(conn, movement_id: int) -> dict:
    row = conn.execute(
        "SELECT heat_id FROM heat_movement WHERE id=?", (movement_id,)
    ).fetchone()
    if not row:
        raise ValueError("Log entry not found")
    conn.execute("DELETE FROM heat_movement WHERE id=?", (movement_id,))
    conn.commit()
    return get_heat(conn, row["heat_id"])


def global_log(conn, q: str = "") -> list[dict]:
    sql = ("SELECT m.*, h.heat_number, h.grade, h.material_class"
           " FROM heat_movement m JOIN heat h ON h.id=m.heat_id")
    args: list = []
    if _s(q):
        like = f"%{_s(q)}%"
        sql += " WHERE m.order_id LIKE ? OR h.heat_number LIKE ?"
        args += [like, like]
    sql += " ORDER BY m.mv_date DESC, m.id DESC LIMIT 500"
    return [dict(r) for r in conn.execute(sql, args).fetchall()]


# --------------------------------------------------------------------------- #
# Attachments (files on disk, metadata in the DB).
# Validation/mime plumbing is shared app-wide — see core/attachments.py.
# --------------------------------------------------------------------------- #
def save_attachments(conn, heat_id: int, kind: str,
                     items: list[tuple[str, str, bytes]]) -> list[dict]:
    """Store a batch of (filename, mime, content) ALL-OR-NOTHING: every file is
    validated before anything is written, and a failure mid-batch rolls back
    the DB rows and removes the files already on disk."""
    if kind not in ATTACHMENT_KINDS:
        raise ValueError("Attachment must be a certificate or an invoice")
    if not conn.execute("SELECT 1 FROM heat WHERE id=?", (heat_id,)).fetchone():
        raise ValueError("Heat not found")
    checked = [(filename, validate_attachment(mime, content), content)
               for filename, mime, content in items]
    written: list[Path] = []
    metas: list[dict] = []
    try:
        for filename, mime, content in checked:
            stored = f"h{heat_id}-{secrets.token_hex(8)}{storage_ext(filename, mime)}"
            path = paths.inventory_files_dir() / stored
            path.write_bytes(content)
            written.append(path)
            cur = conn.execute(
                "INSERT INTO heat_attachment (heat_id, kind, filename, mime,"
                " size_bytes, stored_name, uploaded_at) VALUES (?,?,?,?,?,?,?)",
                (heat_id, kind, _s(filename) or stored, mime, len(content),
                 stored, _now()),
            )
            metas.append({"id": cur.lastrowid, "kind": kind,
                          "filename": _s(filename) or stored,
                          "mime": mime, "size_bytes": len(content)})
        conn.commit()
    except BaseException:
        conn.rollback()
        for p in written:
            p.unlink(missing_ok=True)
        raise
    return metas


def save_attachment(conn, heat_id: int, kind: str, filename: str,
                    mime: str, content: bytes) -> dict:
    """Single-file convenience wrapper over save_attachments."""
    return save_attachments(conn, heat_id, kind, [(filename, mime, content)])[0]


def delete_attachment(conn, attachment_id: int) -> dict:
    row = conn.execute(
        "SELECT heat_id, stored_name FROM heat_attachment WHERE id=?",
        (attachment_id,),
    ).fetchone()
    if not row:
        raise ValueError("Attachment not found")
    conn.execute("DELETE FROM heat_attachment WHERE id=?", (attachment_id,))
    conn.commit()
    (paths.inventory_files_dir() / row["stored_name"]).unlink(missing_ok=True)
    return get_heat(conn, row["heat_id"])


# --------------------------------------------------------------------------- #
# API models
# --------------------------------------------------------------------------- #
class CompositionRow(BaseModel):
    element: str
    percent: float


class HeatIn(BaseModel):
    heat_number: str
    date_received: str
    supplier: str = ""
    material_class: str = ""
    grade: str = ""
    shape: str = ""
    size_section: str = ""
    rods_received: int
    total_weight_kg: float | None = None
    rack: str = ""
    price_total: float | None = None
    price_rate_per_kg: float | None = None
    notes: str = ""
    composition: list[CompositionRow] = []


class MovementIn(BaseModel):
    type: str
    rods: int
    mv_date: str = ""
    order_id: str = ""
    weight_kg: float | None = None
    remarks: str = ""


class RejectIn(BaseModel):
    mv_date: str = ""
    remarks: str = ""


class OptionIn(BaseModel):
    kind: str
    value: str


def _400(fn, *args, **kw):
    try:
        return fn(*args, **kw)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


# --------------------------------------------------------------------------- #
# Routes (admin-only via the router dependency)
# --------------------------------------------------------------------------- #
@router.get("/options")
def options(conn=Depends(get_db)):
    return list_options(conn)


@router.post("/options")
def option_add(body: OptionIn, conn=Depends(get_db)):
    return _400(add_option, conn, body.kind, body.value)


# POST (not DELETE-with-path) because values like 'Pipe/Tube' contain slashes.
@router.post("/options/delete")
def option_delete(body: OptionIn, conn=Depends(get_db)):
    return _400(delete_option, conn, body.kind, body.value)


@router.get("/heats")
def heats(q: str = "", material_class: str = "", shape: str = "",
          status: str = "", element: str = "", pct_min: str = "",
          pct_max: str = "", sort: str = "newest", conn=Depends(get_db)):
    try:
        return list_heats(conn, q=q, material_class=material_class, shape=shape,
                          status=status, element=element, pct_min=pct_min,
                          pct_max=pct_max, sort=sort)
    except ValueError:
        raise HTTPException(status_code=400, detail="Bad composition range")


@router.post("/heats")
def heat_create(body: HeatIn, conn=Depends(get_db)):
    heat_id = _400(create_heat, conn, body.model_dump())
    return get_heat(conn, heat_id)


@router.get("/heats/{heat_id}")
def heat_detail(heat_id: int, conn=Depends(get_db)):
    try:
        return get_heat(conn, heat_id)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.put("/heats/{heat_id}")
def heat_update(heat_id: int, body: HeatIn, conn=Depends(get_db)):
    _400(update_heat, conn, heat_id, body.model_dump())
    return get_heat(conn, heat_id)


@router.delete("/heats/{heat_id}")
def heat_delete(heat_id: int, conn=Depends(get_db)):
    _400(delete_heat, conn, heat_id)
    return {"ok": True}


@router.post("/heats/{heat_id}/movements")
def movement_add(heat_id: int, body: MovementIn, conn=Depends(get_db)):
    return _400(add_movement, conn, heat_id, body.model_dump())


@router.post("/heats/{heat_id}/reject-remaining")
def movement_reject_remaining(heat_id: int, body: RejectIn, conn=Depends(get_db)):
    return _400(reject_remaining, conn, heat_id, body.mv_date, body.remarks)


@router.delete("/movements/{movement_id}")
def movement_delete(movement_id: int, conn=Depends(get_db)):
    return _400(delete_movement, conn, movement_id)


@router.get("/movements")
def movements(q: str = "", conn=Depends(get_db)):
    return global_log(conn, q)


@router.post("/heats/{heat_id}/attachments")
def attachment_upload(heat_id: int, kind: str = Form(...),
                      files: list[UploadFile] = File(...),
                      conn=Depends(get_db)):
    # Sync on purpose (like every route here): the sqlite connection from
    # get_db lives in the threadpool, so the handler must run there too.
    items = [(f.filename or "", f.content_type or "", f.file.read()) for f in files]
    saved = _400(save_attachments, conn, heat_id, kind, items)
    return {"saved": saved, "heat": get_heat(conn, heat_id)}


@router.get("/attachments/{attachment_id}")
def attachment_view(attachment_id: int, download: bool = False, conn=Depends(get_db)):
    row = conn.execute(
        "SELECT * FROM heat_attachment WHERE id=?", (attachment_id,)
    ).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Attachment not found")
    path = paths.inventory_files_dir() / row["stored_name"]
    if not path.is_file():
        raise HTTPException(status_code=404, detail="File is missing on disk")
    safe = header_filename(row["filename"])
    disposition = "attachment" if download else "inline"
    return FileResponse(
        path, media_type=response_mime(row["stored_name"]),
        headers={"Content-Disposition": f'{disposition}; filename="{safe}"'},
    )


@router.delete("/attachments/{attachment_id}")
def attachment_delete(attachment_id: int, conn=Depends(get_db)):
    return _400(delete_attachment, conn, attachment_id)
