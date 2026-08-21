"""Settings — app-wide configuration the owner can change without code.

Owns: the order-number format (used by Order Tracking), the units list
(searchable dropdowns in Parts/Orders), the machining-operation list with
₹/hour rates (the costing builder in Parts & Pricing), and the departments
list (stored in config/rules.json, shared with payroll).

Order-number format tokens: {FY} → Indian financial year like '26-27',
{YYYY} → calendar year of the order date, {SEQ} → per-FY running number,
zero-padded to 3 (e.g. 'ORD-{FY}-{SEQ}' → ORD-26-27-014). Sequence state
lives in the order_seq table, bumped atomically by the orders module.

Numbering also owns the DOCUMENT counters (core/numbering, CONVENTIONS §3):
every live scope with the serial it will hand out next, editable by the owner
so a real-world count — "our quotations are at 317, not 1" — can be corrected
without a code change.
"""

from __future__ import annotations

import json
import re
from datetime import date

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from ..core import db, numbering
from ..core.deps import current_user, get_db, require_admin, require_module
from ..core.rules import load_rules, save_rules

router = APIRouter(prefix="/api/settings",
                   dependencies=[Depends(require_module("settings"))])

# NOTE: reference data for other modules' form pickers (customer dropdowns,
# drawing pickers with rates, operation rates) is served by GRANT-GATED
# endpoints inside those modules (/api/orders/refs, /api/parts/refs) — a
# shared open endpoint would leak pricing to accounts without those grants.


def active_customers(conn) -> list[dict]:
    return [dict(r) for r in conn.execute(
        "SELECT id, name FROM customer WHERE active=1 ORDER BY name COLLATE NOCASE")]

DEFAULT_ORDER_FORMAT = "ORD-{FY}-{SEQ}"

DEFAULT_UNITS = [
    "Nos", "Pieces", "Set", "Pair", "Dozen", "Gross",
    "kg", "g", "mg", "Tonne", "Quintal", "lb",
    "mm", "cm", "m", "km", "inch", "ft", "yard",
    "mm²", "cm²", "m²", "ft²", "inch²",
    "mm³", "cm³", "m³", "Litre", "mL", "Gallon",
    "Sheet", "Rod", "Bar", "Coil", "Roll", "Bundle", "Box", "Packet",
    "Drum", "Bag", "Carton", "Pallet", "Length", "Strip", "Tube",
    "Hour", "Day", "Job", "Lot",
]

# (name, default ₹/hour) — every rate is editable in the Settings screen.
DEFAULT_OPERATIONS = [
    ("Turning", 400), ("CNC Turning", 550), ("Facing", 350), ("Milling", 500),
    ("CNC Milling", 650), ("Drilling", 300), ("Boring", 400), ("Reaming", 350),
    ("Threading", 350), ("Tapping", 300), ("Knurling", 300), ("Grinding", 450),
    ("Buffing", 250), ("Cutting", 250), ("Bending", 300), ("Welding", 400),
    ("Deburring", 200), ("Heat Treatment", 500), ("Plating/Coating", 400),
    ("Inspection", 300), ("Assembly", 250),
]


def ensure_defaults(db_path=None) -> None:
    """Seed units/operations/format — FIRST RUN ONLY per list (user-owned after)."""
    conn = db.connect(db_path)
    try:
        if not conn.execute("SELECT 1 FROM unit LIMIT 1").fetchone():
            for u in DEFAULT_UNITS:
                conn.execute("INSERT OR IGNORE INTO unit (name) VALUES (?)", (u,))
        if not conn.execute("SELECT 1 FROM operation LIMIT 1").fetchone():
            for name, rate in DEFAULT_OPERATIONS:
                conn.execute(
                    "INSERT OR IGNORE INTO operation (name, rate_per_hour) VALUES (?,?)",
                    (name, rate))
        conn.execute(
            "INSERT OR IGNORE INTO app_setting (key, value) VALUES (?,?)",
            ("order_number_format", json.dumps(DEFAULT_ORDER_FORMAT)))
        conn.commit()
    finally:
        conn.close()


# --------------------------------------------------------------------------- #
# Values
# --------------------------------------------------------------------------- #
def get_setting(conn, key: str, default=None):
    row = conn.execute("SELECT value FROM app_setting WHERE key=?", (key,)).fetchone()
    if not row:
        return default
    try:
        return json.loads(row["value"])
    except ValueError:
        return default


def set_setting(conn, key: str, value) -> None:
    conn.execute(
        "INSERT INTO app_setting (key, value) VALUES (?,?)"
        " ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (key, json.dumps(value)))
    conn.commit()


def fy_label(d: date) -> str:
    """Indian financial year: 2026-08-14 -> '26-27'; 2027-02-01 -> '26-27'."""
    start = d.year if d.month >= 4 else d.year - 1
    return f"{start % 100:02d}-{(start + 1) % 100:02d}"


def render_order_no(fmt: str, d: date, seq: int) -> str:
    return (fmt.replace("{FY}", fy_label(d))
               .replace("{YYYY}", str(d.year))
               .replace("{SEQ}", f"{seq:03d}"))


def units(conn) -> list[str]:
    return [r["name"] for r in conn.execute("SELECT name FROM unit ORDER BY name COLLATE NOCASE")]


def operations(conn) -> list[dict]:
    return [dict(r) for r in conn.execute(
        "SELECT id, name, rate_per_hour FROM operation ORDER BY name COLLATE NOCASE")]


# --------------------------------------------------------------------------- #
# Routes — reads for anyone with the settings grant; writes admin-only
# --------------------------------------------------------------------------- #
class FormatIn(BaseModel):
    format: str


class ValueIn(BaseModel):
    value: str


class OperationIn(BaseModel):
    name: str
    rate_per_hour: float = 0


class RateIn(BaseModel):
    rate_per_hour: float


class CounterIn(BaseModel):
    scope: str                 # 'qtn:T04', 'inv:26-27', 'vendor', …
    next_seq: int


# Scope names are keys, not prose — anything else is a typo, not a new counter.
_SCOPE_RE = re.compile(r"[A-Za-z0-9_:.\-]{1,40}")


def _check_rate(v: float) -> float:
    import math
    if not math.isfinite(v) or v < 0 or v > 1e9:
        raise HTTPException(status_code=400,
                            detail="Rate must be a normal, non-negative number")
    return v


@router.get("")
def all_settings(conn=Depends(get_db)):
    fmt = get_setting(conn, "order_number_format", DEFAULT_ORDER_FORMAT)
    fy = fy_label(date.today())
    seq_row = conn.execute("SELECT seq FROM order_seq WHERE fy=?", (fy,)).fetchone()
    next_seq = (seq_row["seq"] if seq_row else 0) + 1
    return {
        "order_number_format": fmt,
        "order_number_preview": render_order_no(fmt, date.today(), next_seq),
        "units": units(conn),
        "operations": operations(conn),
        "departments": load_rules().get("departments", []),
    }


@router.put("/order-format")
def set_order_format(body: FormatIn, user: dict = Depends(require_admin),
                     conn=Depends(get_db)):
    fmt = body.format.strip()
    if "{SEQ}" not in fmt:
        raise HTTPException(status_code=400,
                            detail="The format must contain {SEQ} (the running number)")
    if "{FY}" not in fmt:
        # The sequence restarts every April; without {FY} in the number,
        # next year's ORD-001 would collide with this year's.
        raise HTTPException(status_code=400,
                            detail="The format must contain {FY} — the running number "
                                   "restarts every financial year")
    if len(fmt) > 40:
        raise HTTPException(status_code=400, detail="Keep the format under 40 characters")
    set_setting(conn, "order_number_format", fmt)
    return all_settings(conn)


@router.get("/numbering")
def numbering_counters(conn=Depends(get_db)):
    """Every live document counter with the serial it will hand out next."""
    return [{"scope": r["scope"], "next_seq": r["next_seq"],
             "label": numbering.label_for(r["scope"])}
            for r in conn.execute("SELECT scope, next_seq FROM doc_counter ORDER BY scope")]


@router.put("/numbering")
def set_numbering_counter(body: CounterIn, user: dict = Depends(require_admin),
                          conn=Depends(get_db)):
    """Set a counter's next serial — new scopes included, so a client whose
    first quotation hasn't been raised yet can still be seeded."""
    scope = body.scope.strip()
    if not _SCOPE_RE.fullmatch(scope):
        raise HTTPException(status_code=400,
                            detail="A counter name looks like 'qtn:T04' — letters, "
                                   "digits, ':', '-' and '_' only")
    if not 1 <= body.next_seq <= 1_000_000:
        raise HTTPException(status_code=400,
                            detail="The next number must be between 1 and 1000000")
    conn.execute("INSERT INTO doc_counter (scope, next_seq) VALUES (?,?)"
                 " ON CONFLICT(scope) DO UPDATE SET next_seq=excluded.next_seq",
                 (scope, body.next_seq))
    conn.commit()
    return numbering_counters(conn)


@router.post("/units")
def add_unit(body: ValueIn, user: dict = Depends(require_admin), conn=Depends(get_db)):
    v = body.value.strip()
    if not v:
        raise HTTPException(status_code=400, detail="Unit can't be empty")
    conn.execute("INSERT OR IGNORE INTO unit (name) VALUES (?)", (v,))
    conn.commit()
    return all_settings(conn)


@router.post("/units/delete")
def delete_unit(body: ValueIn, user: dict = Depends(require_admin), conn=Depends(get_db)):
    conn.execute("DELETE FROM unit WHERE name=?", (body.value,))
    conn.commit()
    return all_settings(conn)


@router.post("/operations")
def add_operation(body: OperationIn, user: dict = Depends(require_admin),
                  conn=Depends(get_db)):
    name = body.name.strip()
    if not name:
        raise HTTPException(status_code=400, detail="Operation name can't be empty")
    _check_rate(body.rate_per_hour)
    conn.execute(
        "INSERT INTO operation (name, rate_per_hour) VALUES (?,?)"
        " ON CONFLICT(name) DO UPDATE SET rate_per_hour=excluded.rate_per_hour",
        (name, body.rate_per_hour))
    conn.commit()
    return all_settings(conn)


@router.put("/operations/{op_id}")
def set_operation_rate(op_id: int, body: RateIn, user: dict = Depends(require_admin),
                       conn=Depends(get_db)):
    _check_rate(body.rate_per_hour)
    cur = conn.execute("UPDATE operation SET rate_per_hour=? WHERE id=?",
                       (body.rate_per_hour, op_id))
    conn.commit()
    if not cur.rowcount:
        raise HTTPException(status_code=404, detail="Operation not found")
    return all_settings(conn)


@router.delete("/operations/{op_id}")
def delete_operation(op_id: int, user: dict = Depends(require_admin), conn=Depends(get_db)):
    conn.execute("DELETE FROM operation WHERE id=?", (op_id,))
    conn.commit()
    return all_settings(conn)


@router.post("/departments")
def add_department(body: ValueIn, user: dict = Depends(require_admin)):
    v = body.value.strip()
    if not v:
        raise HTTPException(status_code=400, detail="Department can't be empty")
    rules = load_rules()
    if v not in rules.get("departments", []):
        rules.setdefault("departments", []).append(v)
        save_rules(rules)
    return {"departments": rules["departments"]}


@router.post("/departments/delete")
def delete_department(body: ValueIn, user: dict = Depends(require_admin)):
    rules = load_rules()
    rules["departments"] = [d for d in rules.get("departments", []) if d != body.value]
    save_rules(rules)
    return {"departments": rules["departments"]}
