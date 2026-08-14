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

# READ-ONLY material surface, offered from FOUR screens — the inventory page, a
# quotation, an order, and the costing bill of materials in Parts & Pricing — so
# it must not demand the inventory grant. Separate router, mounted alongside in
# main.py.
#
# NOTE this grant set is a real boundary: /search returns derived PURCHASE cost
# (₹/rod, ₹/kg). Anyone holding any of these four grants can read what the stock
# cost. That is intended — you cannot price a job without it — but do not widen
# the list without meaning to.
check_router = APIRouter(
    prefix="/api/material",
    dependencies=[Depends(require_module("inventory", "quotations", "orders", "parts"))],
)

OPTION_KINDS = ("material_class", "shape", "grade", "element", "supplier")
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
    # No seeded suppliers: every shop buys from different mills, so the list is
    # built entirely from what the user types (and backfilled from existing heats).
    "supplier": [],
}


def backfill_suppliers(db_path=None) -> None:
    """Suppliers became a dropdown after heats already existed — seed the list
    from the names already recorded so the first delivery isn't typed twice."""
    conn = db.connect(db_path)
    try:
        for r in conn.execute(
            "SELECT DISTINCT supplier FROM heat"
            " WHERE supplier IS NOT NULL AND TRIM(supplier) <> ''"
        ):
            conn.execute(
                "INSERT OR IGNORE INTO inv_option (kind, value) VALUES ('supplier',?)",
                (r["supplier"].strip(),))
        conn.commit()
    finally:
        conn.close()


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
                        ("shape", "shape"), ("grade", "grade"),
                        ("supplier", "supplier")):
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
    pieces = _validate_pieces(data.get("pieces"))
    if pieces:
        # The piece rows ARE the rod count once they exist — anything else would
        # let the feasibility check and the stock figure disagree.
        rods = sum(p["quantity"] for p in pieces)
        if heat_id is not None:
            moved = conn.execute(
                "SELECT COALESCE(SUM(rods),0) AS n FROM heat_movement WHERE heat_id=?",
                (heat_id,),
            ).fetchone()["n"]
            if rods < moved:
                raise ValueError(
                    f"Those pieces add up to {rods} rod(s), but {moved} have "
                    f"already been issued or rejected from this heat"
                )
    return hn, rods


def _validate_pieces(rows) -> list[dict]:
    """Individual physical pieces: one row per (length, diameter) with a count.

    Returns clean rows; [] means "no piece detail", which is legal — such a heat
    can still be checked by quantity, just not by dimension.
    """
    out: list[dict] = []
    for i, r in enumerate(rows or [], start=1):
        length = _check_number(r.get("length_mm"), f"Piece {i}: length")
        if not length:
            raise ValueError(f"Piece {i}: length is required")
        dia = _check_number(r.get("diameter_mm"), f"Piece {i}: diameter")
        try:
            qty = int(r.get("quantity") if r.get("quantity") not in (None, "") else 1)
        except (TypeError, ValueError):
            raise ValueError(f"Piece {i}: quantity must be a whole number")
        if qty < 1:
            raise ValueError(f"Piece {i}: quantity must be at least 1")
        out.append({"length_mm": length, "diameter_mm": dia,
                    "quantity": qty, "note": _s(r.get("note"))})
    return out


def _write_pieces(conn, heat_id: int, pieces: list[dict]) -> None:
    """Replace the piece rows. Only called when the payload carried a 'pieces'
    key at all, so an edit that doesn't mention pieces leaves them alone."""
    conn.execute("DELETE FROM heat_piece WHERE heat_id=?", (heat_id,))
    for p in pieces:
        conn.execute(
            "INSERT INTO heat_piece (heat_id, length_mm, diameter_mm, quantity,"
            " note, created_at) VALUES (?,?,?,?,?,?)",
            (heat_id, p["length_mm"], p["diameter_mm"], p["quantity"],
             p["note"], _now()))


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


def _insert_heat(conn, data: dict) -> int:
    """Write one heat WITHOUT committing, so an intake of several heats is
    all-or-nothing."""
    hn, rods = _validate_heat(conn, data)
    cur = conn.execute(
        f"INSERT INTO heat ({','.join(_HEAT_FIELDS)}, created_at)"
        f" VALUES ({','.join('?' * len(_HEAT_FIELDS))}, ?)",
        (*_heat_row_values(data, hn, rods), _now()),
    )
    heat_id = cur.lastrowid
    _write_composition(conn, heat_id, data.get("composition") or [])
    if data.get("pieces") is not None:
        _write_pieces(conn, heat_id, _validate_pieces(data.get("pieces")))
    _learn_options(conn, data)
    return heat_id


def create_heat(conn, data: dict) -> int:
    heat_id = _insert_heat(conn, data)
    conn.commit()
    return heat_id


def create_intake(conn, data: dict) -> dict:
    """One incoming shipment, many heats.

    A delivery routinely arrives as an assortment — three bars of one heat, two
    of another, a short offcut of a third — and heat numbers must never be
    merged, so each distinct heat number becomes its own record with its own
    piece rows. The shipment-level fields (date, supplier, rack, …) are just
    defaults every row inherits unless it overrides them.

    All heats are written in ONE transaction: a delivery is either recorded or
    it isn't, never half.
    """
    rows = data.get("pieces") or []
    if not rows:
        raise ValueError("Add at least one piece")

    common = {k: data.get(k, "") for k in
              ("date_received", "supplier", "rack", "notes", "material_class",
               "grade", "shape", "size_section")}

    # group by heat number, preserving the order they were typed in
    grouped: dict[str, dict] = {}
    for i, r in enumerate(rows, start=1):
        hn = _s(r.get("heat_number"))
        if not hn:
            raise ValueError(f"Piece {i}: heat number is required")
        g = grouped.setdefault(hn, {
            **common,
            "heat_number": hn,
            "material_class": _s(r.get("material_class")) or common["material_class"],
            "grade": _s(r.get("grade")) or common["grade"],
            "shape": _s(r.get("shape")) or common["shape"],
            "composition": r.get("composition") or data.get("composition") or [],
            "pieces": [],
        })
        if not g["composition"] and (r.get("composition") or []):
            g["composition"] = r["composition"]
        g["pieces"].append({
            "length_mm": r.get("length_mm"), "diameter_mm": r.get("diameter_mm"),
            "quantity": r.get("quantity"), "note": _s(r.get("note")),
        })

    # BEGIN IMMEDIATE: two people recording deliveries at once must not both
    # pass the "heat number is free" check and then collide on the UNIQUE index.
    conn.execute("BEGIN IMMEDIATE")
    try:
        made = []
        for hn, payload in grouped.items():
            payload["rods_received"] = sum(
                int(p["quantity"] or 1) for p in payload["pieces"])
            heat_id = _insert_heat(conn, payload)
            made.append({"id": heat_id, "heat_number": hn,
                         "pieces": len(payload["pieces"]),
                         "rods": payload["rods_received"]})
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    return {"heats": made, "count": len(made),
            "rods": sum(m["rods"] for m in made)}


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
    if data.get("pieces") is not None:
        _write_pieces(conn, heat_id, _validate_pieces(data.get("pieces")))
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
# Manufacturability — "can we actually make this from what is on the rack?"
# --------------------------------------------------------------------------- #
def parts_from_piece(length: float, part_length: float,
                     margin: float = 0.0) -> tuple[int, float]:
    """Complete parts obtainable from ONE physical piece, and the offcut left.

    This is deliberately NOT total-volume / part-volume: you cannot weld the
    offcuts of three rods together to make a fourth part. Three 10-unit rods
    yield 3 x floor(10/3) = 9 parts of length 3, never 10, and the leftover unit
    on each rod is scrap unless a whole part fits in it.

    Each part consumes `part_length + margin` — the margin is the parting-off
    and facing allowance the saw and the lathe eat per part.
    """
    need = (part_length or 0) + (margin or 0)
    if need <= 0 or not length or length <= 0:
        return 0, float(length or 0)
    # +1e-9 relative slack: 9.9 / 3.3 is 2.9999999999999996 in binary floating
    # point, and a shop that says "three 3.3s fit in 9.9" is right.
    n = int(math.floor(length / need + 1e-9))
    return n, round(max(length - n * need, 0.0), 4)


def _available_pieces(conn, heat_id: int, consumed: int) -> list[dict]:
    """Piece rows still on the rack.

    The usage log records rods against the HEAT, not against a specific bar, so
    consumption is applied to the piece rows in receipt order (FIFO) — the
    conventional stock assumption. It is deterministic and it never invents
    stock: the totals always agree with `remaining` on the heat.
    """
    rows = [dict(r) for r in conn.execute(
        "SELECT * FROM heat_piece WHERE heat_id=? ORDER BY id", (heat_id,))]
    left = max(consumed, 0)
    out = []
    for p in rows:
        take = min(left, p["quantity"])
        left -= take
        p["available"] = p["quantity"] - take
        if p["available"] > 0:
            out.append(p)
    return out


def check_material(conn, req: dict) -> dict:
    """Advisory feasibility check — reports, never reserves.

    Two methods:
      'dimension' — how many complete parts each individual piece yields, at
                    the given part length/diameter and margin.
      'quantity'  — just how many pieces are on the rack, for material whose
                    dimensions don't decide the answer.

    The answer is always broken down heat by heat: two bars of the same size
    from different heats have different compositions, so "9 parts" is only
    useful alongside "3 from H1001, 2 from H1002, 4 from H1003".
    """
    method = _s(req.get("method")) or "dimension"
    if method not in ("dimension", "quantity"):
        raise ValueError("Method must be 'dimension' or 'quantity'")

    try:
        required = int(req.get("required_qty") or 0)
    except (TypeError, ValueError):
        raise ValueError("Required quantity must be a whole number")
    if required < 0:
        raise ValueError("Required quantity can't be negative")

    part_length = _check_number(req.get("part_length"), "Part length") or 0.0
    part_dia = _check_number(req.get("part_diameter"), "Part diameter") or 0.0
    margin = _check_number(req.get("margin"), "Tolerance / margin") or 0.0
    if method == "dimension" and part_length <= 0:
        raise ValueError("Part length is required for a dimension check")

    material = _s(req.get("material_class"))
    grade = _s(req.get("grade"))
    shape = _s(req.get("shape"))
    only = [h for h in (req.get("heat_numbers") or []) if _s(h)]

    sql = "SELECT * FROM heat WHERE 1=1"
    args: list = []
    for col, val in (("material_class", material), ("grade", grade), ("shape", shape)):
        if val:
            sql += f" AND {col}=?"
            args.append(val)
    if only:
        sql += f" AND heat_number IN ({','.join('?' * len(only))})"
        args += [_s(h) for h in only]
    sql += " ORDER BY date_received, id"

    totals = _movement_totals(conn)
    heats_out: list[dict] = []
    total_feasible = 0

    for row in conn.execute(sql, args):
        heat = dict(row)
        mv = totals.get(heat["id"], {"out": 0})
        remaining = heat["rods_received"] - mv["out"]
        if remaining <= 0:
            continue
        pieces = _available_pieces(conn, heat["id"], mv["out"])

        entry = {
            "heat_id": heat["id"], "heat_number": heat["heat_number"],
            "material_class": heat["material_class"], "grade": heat["grade"],
            "shape": heat["shape"], "rack": heat["rack"],
            "rods_remaining": remaining, "pieces": [], "feasible": 0,
            "has_dimensions": bool(pieces),
        }

        if method == "quantity":
            entry["feasible"] = remaining
            for p in pieces:
                entry["pieces"].append({
                    "piece_id": p["id"], "length_mm": p["length_mm"],
                    "diameter_mm": p["diameter_mm"], "available": p["available"],
                    "parts_per_piece": None, "parts": p["available"],
                    "leftover_each": None, "reason": "",
                })
        else:
            if not pieces:
                # Dimensions were never recorded for this heat: say so rather
                # than silently leaving usable stock out of the answer.
                entry["skipped"] = "no piece dimensions recorded"
                heats_out.append(entry)
                continue
            for p in pieces:
                dia = p["diameter_mm"]
                if part_dia and dia and dia < part_dia:
                    entry["pieces"].append({
                        "piece_id": p["id"], "length_mm": p["length_mm"],
                        "diameter_mm": dia, "available": p["available"],
                        "parts_per_piece": 0, "parts": 0, "leftover_each": None,
                        "reason": f"Ø{_fmt(dia)} is under the Ø{_fmt(part_dia)} needed",
                    })
                    continue
                per, leftover = parts_from_piece(p["length_mm"], part_length, margin)
                entry["pieces"].append({
                    "piece_id": p["id"], "length_mm": p["length_mm"],
                    "diameter_mm": dia, "available": p["available"],
                    "parts_per_piece": per, "parts": per * p["available"],
                    "leftover_each": leftover,
                    "reason": "" if per else
                              f"{_fmt(p['length_mm'])} is too short for "
                              f"{_fmt(part_length + margin)} per part",
                })
                entry["feasible"] += per * p["available"]

        total_feasible += entry["feasible"]
        heats_out.append(entry)

    # Biggest contributor first — that is the heat you would cut from.
    heats_out.sort(key=lambda h: (-h["feasible"], h["heat_number"]))

    if total_feasible >= required and required > 0:
        status = "available"
    elif total_feasible > 0:
        status = "partial"
    else:
        status = "none"

    return {
        "method": method,
        "requirement": {
            "material_class": material, "grade": grade, "shape": shape,
            "required_qty": required, "part_length": part_length,
            "part_diameter": part_dia, "margin": margin,
            "length_per_part": round(part_length + margin, 4),
            "heat_numbers": only,
        },
        "heats": heats_out,
        "heats_considered": len(heats_out),
        "total_feasible": total_feasible,
        "shortfall": max(required - total_feasible, 0),
        "status": status,
    }


def _fmt(v) -> str:
    """Trim the pointless decimals: 3.0 -> '3', 6.5 -> '6.5'."""
    if v is None:
        return "—"
    return f"{v:.4f}".rstrip("0").rstrip(".") or "0"


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
    heat["pieces"] = [dict(r) for r in conn.execute(
        "SELECT id, length_mm, diameter_mm, quantity, note FROM heat_piece"
        " WHERE heat_id=? ORDER BY id", (heat_id,))]
    # what is still on the rack, piece by piece (consumption applied FIFO)
    consumed = heat["rods_received"] - heat["remaining"]
    heat["pieces_available"] = [
        {"piece_id": p["id"], "length_mm": p["length_mm"],
         "diameter_mm": p["diameter_mm"], "available": p["available"]}
        for p in _available_pieces(conn, heat_id, consumed)]
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


class PieceRow(BaseModel):
    """One physical piece (or N identical ones) under a heat."""
    length_mm: float
    diameter_mm: float | None = None
    quantity: int = 1
    note: str = ""


class IntakePieceRow(BaseModel):
    """One line on the incoming-material screen. Carries its OWN heat number:
    a single delivery routinely mixes heats, and they must not be merged.

    `composition` is per row because it belongs to the HEAT — that is the whole
    reason heat numbers stay separate. Rows sharing a heat number share one
    composition (the first non-empty one wins; see create_intake).
    """
    heat_number: str
    material_class: str = ""
    grade: str = ""
    shape: str = ""
    length_mm: float
    diameter_mm: float | None = None
    quantity: int = 1
    note: str = ""
    composition: list[CompositionRow] = []


class IntakeIn(BaseModel):
    date_received: str
    supplier: str = ""
    rack: str = ""
    notes: str = ""
    material_class: str = ""
    grade: str = ""
    shape: str = ""
    size_section: str = ""
    composition: list[CompositionRow] = []
    pieces: list[IntakePieceRow] = []


class CheckIn(BaseModel):
    """A material requirement to test against the rack. Advisory only."""
    method: str = "dimension"           # 'dimension' | 'quantity'
    material_class: str = ""
    grade: str = ""
    shape: str = ""
    required_qty: int = 0
    part_length: float | None = None
    part_diameter: float | None = None
    margin: float | None = None
    heat_numbers: list[str] = []


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
    # None = "this payload says nothing about pieces, leave them alone";
    # [] = "this heat has no piece detail". The two are NOT the same.
    pieces: list[PieceRow] | None = None


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


# --------------------------------------------------------------------------- #
# Material availability — shared by Inventory, Quotations and Orders
# --------------------------------------------------------------------------- #
@router.post("/intake")
def intake_create(body: IntakeIn, conn=Depends(get_db)):
    """Record one incoming delivery — several heats, each with its pieces."""
    return _400(create_intake, conn, body.model_dump())


@check_router.post("/check")
def material_check(body: CheckIn, conn=Depends(get_db)):
    """Advisory: what could we actually make from the rack right now?

    Reserves nothing — two quotations checked a minute apart will both be told
    the same bar is free. Stock only ever moves through the usage log.
    """
    return _400(check_material, conn, body.model_dump())


@check_router.get("/search")
def material_search(q: str = "", limit: int = 30, conn=Depends(get_db)):
    """Stock picker for a bill of materials.

    One search box over heat number, grade and material class — an estimator
    thinks "EN8" or "that H1001 bar", not "filter by field". Returns the derived
    unit costs so the BOM line can price itself:
        ₹/rod = price_total / rods_received
        ₹/kg  = price_rate_per_kg, else price_total / total_weight_kg
    """
    like = f"%{_s(q)}%"
    rows = []
    for r in conn.execute(
        "SELECT h.*,"
        " h.rods_received - COALESCE((SELECT SUM(m.rods) FROM heat_movement m"
        "   WHERE m.heat_id=h.id),0) AS remaining"
        " FROM heat h"
        " WHERE (? = '' OR h.heat_number LIKE ? OR h.grade LIKE ?"
        "        OR h.material_class LIKE ? OR h.supplier LIKE ?)"
        " ORDER BY h.date_received DESC, h.id DESC LIMIT ?",
        (_s(q), like, like, like, like, max(1, min(int(limit or 30), 200)))
    ):
        h = dict(r)
        received = h["rods_received"] or 0
        per_rod = (h["price_total"] / received) if (h["price_total"] and received) else None
        per_kg = h["price_rate_per_kg"]
        if not per_kg and h["price_total"] and h["total_weight_kg"]:
            per_kg = h["price_total"] / h["total_weight_kg"]
        pieces = [dict(p) for p in conn.execute(
            "SELECT length_mm, diameter_mm, quantity FROM heat_piece"
            " WHERE heat_id=? ORDER BY id", (h["id"],))]
        rows.append({
            "heat_id": h["id"], "heat_number": h["heat_number"],
            "material_class": h["material_class"], "grade": h["grade"],
            "shape": h["shape"], "supplier": h["supplier"],
            "size_section": h["size_section"], "rack": h["rack"],
            "remaining": h["remaining"],
            "cost_per_rod": round(per_rod, 4) if per_rod else None,
            "cost_per_kg": round(per_kg, 4) if per_kg else None,
            "pieces": pieces,
            "label": " · ".join(x for x in (h["heat_number"], h["material_class"],
                                            h["grade"], h["size_section"]) if x),
        })
    return {"rows": rows}


@check_router.get("/refs")
def material_refs(conn=Depends(get_db)):
    """Dropdown fodder for the check panel, without the inventory grant."""
    opts = list_options(conn)
    heats = [dict(r) for r in conn.execute(
        "SELECT h.id, h.heat_number, h.material_class, h.grade, h.shape,"
        " h.rods_received - COALESCE((SELECT SUM(m.rods) FROM heat_movement m"
        "   WHERE m.heat_id=h.id),0) AS remaining,"
        " (SELECT COUNT(*) FROM heat_piece p WHERE p.heat_id=h.id) AS piece_rows"
        " FROM heat h ORDER BY h.material_class, h.heat_number")]
    return {
        "material_class": opts["material_class"],
        "grade": opts["grade"],
        "shape": opts["shape"],
        "heats": [h for h in heats if h["remaining"] > 0],
    }
