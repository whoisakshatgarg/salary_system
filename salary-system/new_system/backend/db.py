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


def connect(db_path: str | Path | None = None) -> sqlite3.Connection:
    """Open a connection with row access by column name and FK enforcement."""
    path = Path(db_path) if db_path else DB_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


# Columns added after the first schema shipped — applied to existing DBs so
# upgrades are non-destructive (SQLite CREATE IF NOT EXISTS won't alter tables).
_MIGRATIONS = {
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


def _migrate(conn) -> None:
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
