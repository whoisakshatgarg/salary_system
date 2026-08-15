"""SQLite data layer — replaces the old MySQL server.

Why SQLite: a single `data/salary.db` file, zero server to install or run,
real SQL, trivial backup (copy the file). The schema below is a cleaned-up,
de-duplicated version of the reverse-engineered MySQL schema, with the legacy
quirks fixed:

* `employee.id` is a real AUTOINCREMENT primary key (old: client-side
  MAX(id)+1, race-prone).
* Periods are stored uniformly as ``'YYYY-MM'`` everywhere (old: a mix of
  numeric 'YYYY-MM' for attendance and English month *names* for pay/save_data,
  which broke the export's month filter).
* The leave bank lives on the employee row (old: a separate
  `remaining_holidays` table, 1:1 anyway).
* Daily attendance is preserved in `attendance_day` (old: only the monthly
  summary survived, so nothing could be recomputed or audited).
* Passwords are hashed, never plaintext.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from . import paths

# Writable per-user location (see paths.py): the project's data/ in dev, the
# per-user app folder once packaged as an .exe.
DB_PATH = paths.db_path()

SCHEMA = """
CREATE TABLE IF NOT EXISTS app_user (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    username      TEXT UNIQUE NOT NULL,
    password_hash TEXT NOT NULL,
    role          TEXT NOT NULL DEFAULT 'temp'      -- 'admin' | 'temp'
);

CREATE TABLE IF NOT EXISTS employee (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    name              TEXT NOT NULL,
    dept              TEXT NOT NULL,
    base_salary       INTEGER NOT NULL,
    pf_applicable     INTEGER NOT NULL DEFAULT 0,    -- 0/1  (old PF 'Y'/'N')
    esi_applicable    INTEGER NOT NULL DEFAULT 0,    -- 0/1
    overtime_eligible INTEGER NOT NULL DEFAULT 0,    -- 0/1
    shift             TEXT NOT NULL DEFAULT 'D',     -- 'D' day / 'N' night
    rem_advance       INTEGER NOT NULL DEFAULT 0,    -- outstanding advance balance
    leave_balance     INTEGER NOT NULL DEFAULT 0,    -- paid-leave bank (non-OT only)
    date_joined       TEXT,                          -- 'YYYY-MM-DD' (nullable)
    active            INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS attendance_day (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    employee_id    INTEGER NOT NULL REFERENCES employee(id),
    work_date      TEXT NOT NULL,                    -- 'YYYY-MM-DD'
    status         TEXT NOT NULL DEFAULT 'P',        -- 'P' | 'A'
    overtime_hours REAL,
    UNIQUE(employee_id, work_date)
);

CREATE TABLE IF NOT EXISTS attendance_summary (
    id                    INTEGER PRIMARY KEY AUTOINCREMENT,
    employee_id           INTEGER NOT NULL REFERENCES employee(id),
    period                TEXT NOT NULL,             -- 'YYYY-MM'
    present_days          REAL NOT NULL,
    total_days            INTEGER NOT NULL,
    attendance_percentage REAL NOT NULL,
    total_overtime_hours  REAL NOT NULL DEFAULT 0,
    refreshment_days      INTEGER NOT NULL DEFAULT 0,
    penalty_days          INTEGER NOT NULL DEFAULT 0,
    leave_used            INTEGER NOT NULL DEFAULT 0,
    base_present_days     REAL NOT NULL DEFAULT 0,
    applied_rules         TEXT,                          -- JSON: penalty rules that fired
    UNIQUE(employee_id, period)
);

CREATE TABLE IF NOT EXISTS advance (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    employee_id INTEGER NOT NULL REFERENCES employee(id),
    amount      INTEGER NOT NULL,
    txn_date    TEXT NOT NULL,                       -- 'YYYY-MM-DD'
    type        TEXT NOT NULL,                        -- 'CR' issued | 'DR' recovered
    cheque      INTEGER NOT NULL DEFAULT 0,
    cash        INTEGER NOT NULL DEFAULT 0,
    note        TEXT
);

CREATE TABLE IF NOT EXISTS pay (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    employee_id      INTEGER NOT NULL REFERENCES employee(id),
    period           TEXT NOT NULL,                   -- 'YYYY-MM'
    base             INTEGER NOT NULL,
    base_att         REAL NOT NULL,
    pf               INTEGER NOT NULL DEFAULT 0,
    esi              INTEGER NOT NULL DEFAULT 0,
    overtime_hours   REAL NOT NULL DEFAULT 0,
    overtime_pay     REAL NOT NULL DEFAULT 0,
    refreshment_days INTEGER NOT NULL DEFAULT 0,
    refreshment_pay  REAL NOT NULL DEFAULT 0,
    attendance_percentage REAL NOT NULL DEFAULT 0,
    penalty_days     INTEGER NOT NULL DEFAULT 0,
    adv_deducted     INTEGER NOT NULL DEFAULT 0,
    gross            REAL NOT NULL DEFAULT 0,
    bonus            INTEGER NOT NULL DEFAULT 0,
    bonus_status     TEXT NOT NULL DEFAULT 'NA',
    total            INTEGER NOT NULL,
    cheque           INTEGER NOT NULL DEFAULT 0,
    cash             INTEGER NOT NULL DEFAULT 0,
    old_advance      INTEGER NOT NULL DEFAULT 0,
    new_advance      INTEGER NOT NULL DEFAULT 0,
    published_at     TEXT,
    UNIQUE(employee_id, period)
);

CREATE TABLE IF NOT EXISTS leave_reset (
    year INTEGER PRIMARY KEY
);

-- ---------- Inventory (raw-material heats) — admin-only module ---------- --
-- Dropdown values live in inv_option but are stored DENORMALIZED on the heat,
-- so deleting an option never corrupts existing records. Stock is always
-- derived from heat_movement (never stored). heat.id is a surrogate key so the
-- user-facing heat_number stays editable (typo fixes) yet unique.

CREATE TABLE IF NOT EXISTS inv_option (
    id    INTEGER PRIMARY KEY AUTOINCREMENT,
    kind  TEXT NOT NULL,                  -- material_class | shape | grade | element
    value TEXT NOT NULL,
    UNIQUE(kind, value)
);

CREATE TABLE IF NOT EXISTS heat (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    heat_number       TEXT NOT NULL UNIQUE,
    date_received     TEXT NOT NULL,      -- 'YYYY-MM-DD'
    supplier          TEXT,
    material_class    TEXT,
    grade             TEXT,
    shape             TEXT,
    size_section      TEXT,               -- free text, e.g. 'Ø25 mm × 3 m'
    rods_received     INTEGER NOT NULL,
    total_weight_kg   REAL,
    rack              TEXT,
    price_total       REAL,               -- ₹
    price_rate_per_kg REAL,               -- ₹/kg
    notes             TEXT,
    created_at        TEXT
);

CREATE TABLE IF NOT EXISTS heat_composition (
    id      INTEGER PRIMARY KEY AUTOINCREMENT,
    heat_id INTEGER NOT NULL REFERENCES heat(id) ON DELETE CASCADE,
    element TEXT NOT NULL,
    percent REAL NOT NULL,
    UNIQUE(heat_id, element)
);

-- Individual physical pieces under a heat. One row per (length, diameter) with
-- a quantity, so "4 rods of Ø25 × 3000, plus 1 short 1200 offcut" is two rows.
--
-- Heat numbers are NEVER merged: two steel bars of the same size from different
-- heats have different compositions, so they stay separate records and the
-- feasibility check reports its answer heat by heat.
--
-- Optional: a heat recorded before this existed (or one where the dimensions
-- genuinely don't matter) simply has no piece rows and can only be checked by
-- quantity. When piece rows DO exist their quantities sum to heat.rods_received.
CREATE TABLE IF NOT EXISTS heat_piece (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    heat_id     INTEGER NOT NULL REFERENCES heat(id) ON DELETE CASCADE,
    length_mm   REAL NOT NULL,
    diameter_mm REAL,
    quantity    INTEGER NOT NULL DEFAULT 1,
    note        TEXT,
    created_at  TEXT
);

CREATE TABLE IF NOT EXISTS heat_movement (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    heat_id    INTEGER NOT NULL REFERENCES heat(id),   -- no cascade: guard deletes
    mv_date    TEXT NOT NULL,
    type       TEXT NOT NULL,             -- 'issue' | 'reject'
    order_id   TEXT,                      -- required (in code) when type='issue'
    rods       INTEGER NOT NULL,
    weight_kg  REAL,
    remarks    TEXT,
    created_at TEXT
);

CREATE TABLE IF NOT EXISTS heat_attachment (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    heat_id     INTEGER NOT NULL REFERENCES heat(id) ON DELETE CASCADE,
    kind        TEXT NOT NULL,            -- 'certificate' | 'invoice'
    filename    TEXT NOT NULL,            -- original name, shown to the user
    mime        TEXT,
    size_bytes  INTEGER,
    stored_name TEXT NOT NULL UNIQUE,     -- file on disk under inventory_files/
    uploaded_at TEXT
);

CREATE INDEX IF NOT EXISTS idx_heat_piece_heat     ON heat_piece(heat_id);
CREATE INDEX IF NOT EXISTS idx_heat_movement_heat  ON heat_movement(heat_id);
CREATE INDEX IF NOT EXISTS idx_heat_movement_order ON heat_movement(order_id);
CREATE INDEX IF NOT EXISTS idx_heat_comp_heat      ON heat_composition(heat_id);

-- Employee documents (Aadhaar, agreements, …) — files on disk under
-- employee_files/, metadata here; same pattern as heat_attachment.
CREATE TABLE IF NOT EXISTS employee_document (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    employee_id INTEGER NOT NULL REFERENCES employee(id) ON DELETE CASCADE,
    label       TEXT,                             -- e.g. 'Aadhaar', 'Agreement'
    filename    TEXT NOT NULL,                    -- original name, shown to user
    mime        TEXT,
    size_bytes  INTEGER,
    stored_name TEXT NOT NULL UNIQUE,             -- file under employee_files/
    uploaded_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_emp_doc_emp ON employee_document(employee_id);

-- ---------- Settings (app-wide configuration) ---------- --
CREATE TABLE IF NOT EXISTS app_setting (
    key   TEXT PRIMARY KEY,                       -- e.g. 'order_number_format'
    value TEXT                                     -- JSON-encoded
);

CREATE TABLE IF NOT EXISTS unit (
    id   INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE                      -- 'Nos', 'kg', 'mm', …
);

-- A customer can have its own price for an operation (negotiated machine rate,
-- or a standing extra). Falls back to the global operation rate when absent.
CREATE TABLE IF NOT EXISTS customer_operation_rate (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    customer_id   INTEGER NOT NULL REFERENCES customer(id) ON DELETE CASCADE,
    operation     TEXT NOT NULL,
    rate_per_hour REAL NOT NULL DEFAULT 0,
    extra_rate    REAL NOT NULL DEFAULT 0,          -- ₹/hour on top, for this customer
    note          TEXT,
    UNIQUE(customer_id, operation)
);

CREATE TABLE IF NOT EXISTS operation (             -- machining ops for costing
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    name          TEXT NOT NULL UNIQUE,            -- 'Turning', 'Milling', …
    rate_per_hour REAL NOT NULL DEFAULT 0          -- ₹/hr, editable in Settings
);

-- ---------- Customers ---------- --
CREATE TABLE IF NOT EXISTS customer (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    code             TEXT UNIQUE,                  -- 'AC01' = abbreviation + serial
    name             TEXT NOT NULL UNIQUE,
    gstin            TEXT,
    address_billing  TEXT,
    address_shipping TEXT,
    payment_terms    TEXT,
    notes            TEXT,
    active           INTEGER NOT NULL DEFAULT 1,
    created_at       TEXT
);

CREATE TABLE IF NOT EXISTS customer_contact (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    customer_id INTEGER NOT NULL REFERENCES customer(id) ON DELETE CASCADE,
    name        TEXT NOT NULL,
    phone       TEXT,
    email       TEXT,
    role        TEXT
);

-- ---------- Parts & Pricing (drawing master) ---------- --
CREATE TABLE IF NOT EXISTS drawing (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    drawing_no     TEXT NOT NULL,
    revision       TEXT NOT NULL DEFAULT 'A',
    customer_id    INTEGER REFERENCES customer(id),
    description    TEXT,
    part_type      TEXT,                           -- broad family ("Piston rod"), learned from use
    overall_length_mm REAL,                        -- finished-part envelope, off the drawing
    overall_width_mm  REAL,                        -- width / Ø across, whichever the drawing gives
    material_class TEXT,                           -- denormalized (inventory lists)
    grade          TEXT,
    unit           TEXT,                           -- from the units list
    notes          TEXT,
    active         INTEGER NOT NULL DEFAULT 1,
    created_at     TEXT,
    UNIQUE(drawing_no, revision)
);

CREATE TABLE IF NOT EXISTS drawing_file (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    drawing_id  INTEGER NOT NULL REFERENCES drawing(id) ON DELETE CASCADE,
    filename    TEXT NOT NULL,
    mime        TEXT,
    size_bytes  INTEGER,
    stored_name TEXT NOT NULL UNIQUE,              -- file under drawing_files/
    uploaded_at TEXT
);

CREATE TABLE IF NOT EXISTS drawing_rate (          -- the rate/quote HISTORY
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    drawing_id INTEGER NOT NULL REFERENCES drawing(id) ON DELETE CASCADE,
    kind       TEXT NOT NULL,                      -- 'quoted' | 'agreed' | 'revised'
    rate       REAL NOT NULL,                      -- ₹/piece (per drawing.unit)
    rate_date  TEXT NOT NULL,
    note       TEXT,
    created_at TEXT
);

CREATE TABLE IF NOT EXISTS costing (               -- per-operation build-up
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    drawing_id    INTEGER NOT NULL REFERENCES drawing(id) ON DELETE CASCADE,
    material_cost REAL NOT NULL DEFAULT 0,
    margin_pct    REAL NOT NULL DEFAULT 0,
    notes         TEXT,
    created_at    TEXT
);

-- Bill of materials for a costing: which stock the part is cut from, and what
-- that costs per piece. heat_number / material_label / unit_cost are SNAPSHOTS
-- for the same reason the operation rates are — reopening an old costing must
-- never silently reprice it when stock or prices change. heat_id is kept for
-- traceability and goes NULL if the heat is ever deleted.
CREATE TABLE IF NOT EXISTS costing_material (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    costing_id     INTEGER NOT NULL REFERENCES costing(id) ON DELETE CASCADE,
    heat_id        INTEGER REFERENCES heat(id) ON DELETE SET NULL,
    heat_number    TEXT,
    material_label TEXT,
    unit           TEXT,                          -- 'rod' | 'kg'
    unit_cost      REAL NOT NULL DEFAULT 0,       -- ₹ per unit, snapshot
    qty_per_piece  REAL NOT NULL DEFAULT 0,
    cost           REAL NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS costing_op (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    costing_id       INTEGER NOT NULL REFERENCES costing(id) ON DELETE CASCADE,
    operation        TEXT NOT NULL,
    minutes          REAL NOT NULL,
    rate_per_hour    REAL NOT NULL,                -- snapshot of the rate used
    weightage        REAL NOT NULL DEFAULT 1,      -- multiplier for weighted addition
    extra_rate       REAL NOT NULL DEFAULT 0,      -- ₹/HOUR added on top of rate_per_hour
    cost             REAL NOT NULL                 -- see parts.op_cost() for the formula
);

-- ---------- Order Tracking ---------- --
CREATE TABLE IF NOT EXISTS order_seq (             -- per-FY order numbering
    fy  TEXT PRIMARY KEY,                          -- '26-27'
    seq INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS customer_order (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    order_no    TEXT NOT NULL UNIQUE,              -- from the configurable format
    customer_id INTEGER NOT NULL REFERENCES customer(id),
    customer_po TEXT,                              -- the customer's own PO number
    stage       TEXT NOT NULL DEFAULT 'enquiry',   -- 7 skippable stages
    order_date  TEXT NOT NULL,
    due_date    TEXT,
    notes       TEXT,
    created_at  TEXT
);

CREATE TABLE IF NOT EXISTS order_item (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    order_id    INTEGER NOT NULL REFERENCES customer_order(id) ON DELETE CASCADE,
    drawing_id  INTEGER REFERENCES drawing(id),    -- nullable: free-text items ok
    description TEXT,
    qty         REAL NOT NULL,
    unit        TEXT,
    rate        REAL NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS order_stage_log (
    id       INTEGER PRIMARY KEY AUTOINCREMENT,
    order_id INTEGER NOT NULL REFERENCES customer_order(id) ON DELETE CASCADE,
    stage    TEXT NOT NULL,
    at       TEXT NOT NULL,
    note     TEXT
);

-- Delivery plan for a long-running order: "250 by the 10th, 100 by the 24th,
-- the rest before the deadline". Lines hang off the ITEM, because a quantity
-- only means something against the item it is a quantity OF. Whatever is left
-- unplanned is derived (item qty - sum of lines), never stored.
-- A MATERIAL REQUISITION: the bill of materials for an order, issued as a
-- numbered document and FROZEN. Everything here is a snapshot — re-costing a
-- drawing afterwards must not change a sheet that is already on the shop floor;
-- you issue a new one instead. Numbered from doc_seq with kind='material',
-- the same per-financial-year machinery as quotations and invoices.
CREATE TABLE IF NOT EXISTS material_doc (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    doc_no        TEXT NOT NULL UNIQUE,
    order_id      INTEGER REFERENCES customer_order(id) ON DELETE SET NULL,
    order_no      TEXT NOT NULL,                 -- snapshot: survives order delete
    customer_name TEXT,                          -- snapshot
    issued_on     TEXT NOT NULL,                 -- 'YYYY-MM-DD'
    issued_by     TEXT,
    notes         TEXT,
    total_cost    REAL NOT NULL DEFAULT 0,
    created_at    TEXT
);

CREATE TABLE IF NOT EXISTS material_doc_line (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    material_doc_id INTEGER NOT NULL REFERENCES material_doc(id) ON DELETE CASCADE,
    heat_id         INTEGER REFERENCES heat(id) ON DELETE SET NULL,
    heat_number     TEXT,
    material_label  TEXT,
    unit            TEXT,
    required        REAL NOT NULL,
    already_issued  REAL NOT NULL DEFAULT 0,     -- what the log showed AT ISSUE time
    unit_cost       REAL NOT NULL DEFAULT 0,
    cost            REAL NOT NULL DEFAULT 0,
    from_parts      TEXT                         -- which drawings called for it
);

CREATE INDEX IF NOT EXISTS idx_material_doc_order ON material_doc(order_id);
CREATE INDEX IF NOT EXISTS idx_material_doc_line  ON material_doc_line(material_doc_id);

CREATE TABLE IF NOT EXISTS order_schedule (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    order_item_id INTEGER NOT NULL REFERENCES order_item(id) ON DELETE CASCADE,
    due_date      TEXT NOT NULL,                  -- 'YYYY-MM-DD'
    qty           REAL NOT NULL,
    note          TEXT,
    created_at    TEXT
);

CREATE INDEX IF NOT EXISTS idx_order_schedule_item ON order_schedule(order_item_id);

CREATE TABLE IF NOT EXISTS consignment (           -- shipments; lines may span orders
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    consign_date TEXT NOT NULL,
    transporter  TEXT,
    lr_no        TEXT,                             -- Lorry Receipt
    eway_no      TEXT,                             -- e-way bill
    invoice_no   TEXT,
    vehicle_no   TEXT,
    freight      REAL,
    delivered    INTEGER NOT NULL DEFAULT 0,
    notes        TEXT,
    created_at   TEXT
);

CREATE TABLE IF NOT EXISTS consignment_line (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    consignment_id INTEGER NOT NULL REFERENCES consignment(id) ON DELETE CASCADE,
    order_item_id  INTEGER NOT NULL REFERENCES order_item(id),
    qty            REAL NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_drawing_rate_drawing ON drawing_rate(drawing_id);
CREATE INDEX IF NOT EXISTS idx_order_item_order     ON order_item(order_id);
CREATE INDEX IF NOT EXISTS idx_stage_log_order      ON order_stage_log(order_id);
CREATE INDEX IF NOT EXISTS idx_cons_line_cons       ON consignment_line(consignment_id);
CREATE INDEX IF NOT EXISTS idx_cons_line_item       ON consignment_line(order_item_id);

-- ---------- Quotations & Invoices ---------- --
CREATE TABLE IF NOT EXISTS doc_seq (               -- per-FY numbering, per kind
    kind TEXT NOT NULL,                            -- 'quotation' | 'invoice'
    fy   TEXT NOT NULL,
    seq  INTEGER NOT NULL,
    PRIMARY KEY (kind, fy)
);

CREATE TABLE IF NOT EXISTS document (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    kind         TEXT NOT NULL,                    -- 'quotation' | 'invoice'
    doc_no       TEXT NOT NULL UNIQUE,
    customer_id  INTEGER NOT NULL REFERENCES customer(id),
    order_id     INTEGER REFERENCES customer_order(id),  -- invoices usually
    doc_date     TEXT NOT NULL,
    valid_until  TEXT,                             -- quotations
    reference    TEXT,                             -- their enquiry / PO no.
    tax_pct      REAL NOT NULL DEFAULT 0,          -- GST %
    notes        TEXT,
    terms        TEXT,
    status       TEXT NOT NULL DEFAULT 'draft',    -- draft|sent|accepted|paid|cancelled
    created_at   TEXT
);

CREATE TABLE IF NOT EXISTS document_line (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    document_id INTEGER NOT NULL REFERENCES document(id) ON DELETE CASCADE,
    drawing_id  INTEGER REFERENCES drawing(id),
    description TEXT,
    qty         REAL NOT NULL,
    unit        TEXT,
    rate        REAL NOT NULL DEFAULT 0
);

CREATE INDEX IF NOT EXISTS idx_document_customer ON document(customer_id);
CREATE INDEX IF NOT EXISTS idx_document_order    ON document(order_id);
CREATE INDEX IF NOT EXISTS idx_doc_line_doc      ON document_line(document_id);

CREATE TABLE IF NOT EXISTS sync_log (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    filename    TEXT,
    file_hash   TEXT,
    type        TEXT,                                -- 'attendance' | 'roster'
    source      TEXT,                                -- 'operator' | 'ceo'
    period      TEXT,
    summary     TEXT,
    imported_at TEXT
);

CREATE INDEX IF NOT EXISTS idx_att_day_emp_period
    ON attendance_day(employee_id, work_date);
CREATE INDEX IF NOT EXISTS idx_advance_emp ON advance(employee_id);
CREATE INDEX IF NOT EXISTS idx_pay_period ON pay(period);
"""


def row_to_dict(r: sqlite3.Row | None):
    """sqlite3.Row -> plain dict (None passes through)."""
    return dict(r) if r is not None else None


def connect(db_path: str | Path | None = None) -> sqlite3.Connection:
    """Open a connection with row access by column name and FK enforcement."""
    path = Path(db_path) if db_path else DB_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    # check_same_thread=False: FastAPI may create a request's connection in one
    # threadpool thread and run the handler in another. Each request still gets
    # its OWN connection (never shared concurrently), so this is safe.
    conn = sqlite3.connect(path, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


# Columns added after the first schema shipped — applied to existing DBs so
# upgrades are non-destructive (SQLite CREATE IF NOT EXISTS won't alter tables).
_MIGRATIONS = {
    "customer": {
        "code": "TEXT",
    },
    "drawing": {
        "part_type": "TEXT",
        "overall_length_mm": "REAL",
        "overall_width_mm": "REAL",
    },
    "costing_op": {
        "weightage": "REAL NOT NULL DEFAULT 1",
        "extra_rate": "REAL NOT NULL DEFAULT 0",
    },
    "app_user": {
        "grants": "TEXT",  # JSON list of module keys (see core/registry.py)
    },
    "employee": {
        "date_joined": "TEXT",
    },
    "attendance_summary": {
        "leave_used": "INTEGER NOT NULL DEFAULT 0",
        "base_present_days": "REAL NOT NULL DEFAULT 0",
        "applied_rules": "TEXT",
    },
    "pay": {
        "attendance_percentage": "REAL NOT NULL DEFAULT 0",
        "penalty_days": "INTEGER NOT NULL DEFAULT 0",
    },
}


# Columns that existed only between two same-day commits and were renamed
# before release. Dropping is best-effort: an old SQLite just keeps them.
_RETIRED = {"costing_op": ["extra_margin_pct"]}


def _migrate(conn) -> None:
    for table, cols in _RETIRED.items():
        existing = {r["name"] for r in conn.execute(f"PRAGMA table_info({table})")}
        for col in cols:
            if col in existing:
                try:
                    conn.execute(f"ALTER TABLE {table} DROP COLUMN {col}")
                except Exception:
                    pass
    for table, cols in _MIGRATIONS.items():
        existing = {r["name"] for r in conn.execute(f"PRAGMA table_info({table})")}
        for col, decl in cols.items():
            if col not in existing:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN {col} {decl}")


def init_db(db_path: str | Path | None = None) -> None:
    """Create all tables if they don't exist, then apply column migrations."""
    conn = connect(db_path)
    try:
        conn.executescript(SCHEMA)
        _migrate(conn)
        conn.commit()
    finally:
        conn.close()


def backup_to(dest_path: str | Path, db_path: str | Path | None = None) -> str:
    """Consistent online backup (safe while the DB is in use) to dest_path."""
    Path(dest_path).parent.mkdir(parents=True, exist_ok=True)
    src = connect(db_path)
    dest = sqlite3.connect(str(dest_path))
    try:
        src.backup(dest)
    finally:
        dest.close()
        src.close()
    return str(dest_path)


if __name__ == "__main__":
    init_db()
    print(f"Initialised SQLite schema at {DB_PATH}")
