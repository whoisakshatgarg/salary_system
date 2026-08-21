"""Document numbering — every number the company prints, in one place.

CONVENTIONS.md §3 is the registry this module implements; when the two
disagree the reference document wins and this file is corrected. Callers ask
for a NUMBER, never for a format string, so changing a scheme is changing one
function here rather than hunting format templates through the modules.

Counters live in ``doc_counter`` (scope -> next serial) and are consumed by
``_take`` inside the CALLER's transaction: the serial and the row that owns it
commit together, so a save that rolls back never burns a number. Previews use
``peek``, which reads without consuming.

Seeds (``ensure_seeds``) carry the real-world counts read off the reference
documents; Settings -> Numbering edits them, which is what lets the office
correct a counter without a code change.

Self-contained on purpose — no imports from backend.modules. The ledger,
the papers engine and outsourcing all number through here, and borrowing
settings.fy_label would buy an import cycle for four lines of arithmetic.
"""

from __future__ import annotations

import re
from datetime import date, datetime

# Counter starting points read off the reference documents (SOP-DESIGN §3).
# INSERT OR IGNORE only — see ensure_seeds.
SEEDS = {
    "qtn:T04": 317,      # Thermosense, next after quotation 316
    "qtn:E01": 595,      # East Coast Sensors, next after 594
    "wo:26": 253,        # work orders, next after 252/26
    "inv:26-27": 169,    # export invoices, next after EI-168
}

_MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun",
           "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")

# Friendly names for the Settings screen, keyed by the scope's prefix.
_LABELS = {
    "qtn": "Quotation", "ack": "PO acknowledgement", "wo": "Work order",
    "inv": "Export invoice", "bom": "Bill of materials",
    "os_po": "Outsourced PO", "os_item": "Outsourced item ID",
    "vendor": "Vendor code",
}

# An invoice number ends '{FY}/{serial}' — the test certificate quotes both.
_INV_TAIL = re.compile(r"(\d{2}-\d{2})/(\d+)\s*$")


# --------------------------------------------------------------------------- #
# Dates
# --------------------------------------------------------------------------- #
def _date(v) -> date:
    """A date, a datetime, or an ISO 'YYYY-MM-DD' string -> date."""
    if isinstance(v, datetime):
        return v.date()
    if isinstance(v, date):
        return v
    try:
        return date.fromisoformat(str(v)[:10])
    except ValueError:
        raise ValueError(f"{v!r} isn't a date (YYYY-MM-DD)")


def fy(d) -> str:
    """Indian fiscal year: Apr 2026 – Mar 2027 -> '26-27'."""
    d = _date(d)
    start = d.year if d.month >= 4 else d.year - 1
    return f"{start % 100:02d}-{(start + 1) % 100:02d}"


def yy(d) -> str:
    """Two-digit calendar year: 2026 -> '26'."""
    return f"{_date(d).year % 100:02d}"


def ddmmyy(d) -> str:
    """Compact date inside a document number: 21 Aug 2026 -> '210826'."""
    d = _date(d)
    return f"{d.day:02d}{d.month:02d}{d.year % 100:02d}"


def ordinal_apostrophe(d) -> str:
    """Quotation / acknowledgement / COC dates: "04th Aug' 2026".

    The day is always two digits (that is how every reference prints it) and
    11/12/13 take 'th', not 'st'/'nd'/'rd'.
    """
    d = _date(d)
    suffix = "th" if 11 <= d.day % 100 <= 13 else {1: "st", 2: "nd", 3: "rd"}.get(d.day % 10, "th")
    return f"{d.day:02d}{suffix} {_MONTHS[d.month - 1]}' {d.year}"


def ddmmyyyy(d) -> str:
    """Invoice header date: '14/08/2026'."""
    d = _date(d)
    return f"{d.day:02d}/{d.month:02d}/{d.year}"


def dtd_ddmmyy(d) -> str:
    """Buyer's PO date on the invoice / packing list: 'Dtd. 06.03.26'."""
    d = _date(d)
    return f"Dtd. {d.day:02d}.{d.month:02d}.{d.year % 100:02d}"


def us_mmddyy(d) -> str:
    """Test-certificate dates, US style as the spreadsheet prints them: '05-10-24'."""
    d = _date(d)
    return f"{d.month:02d}-{d.day:02d}-{d.year % 100:02d}"


# --------------------------------------------------------------------------- #
# Counters
# --------------------------------------------------------------------------- #
def _take(conn, scope: str, start: int = 1) -> int:
    """Consume one serial from `scope` and return it.

    Read-then-write, so it has to be serialised: it joins the caller's
    transaction when there is one (the number then commits with the row that
    owns it) and opens its own BEGIN IMMEDIATE when there isn't.
    """
    owns = not conn.in_transaction
    if owns:
        conn.execute("BEGIN IMMEDIATE")
    try:
        row = conn.execute("SELECT next_seq FROM doc_counter WHERE scope=?",
                           (scope,)).fetchone()
        seq = int(row[0]) if row else start
        conn.execute("INSERT INTO doc_counter (scope, next_seq) VALUES (?,?)"
                     " ON CONFLICT(scope) DO UPDATE SET next_seq=excluded.next_seq",
                     (scope, seq + 1))
        if owns:
            conn.commit()
    except BaseException:
        if owns:
            conn.rollback()
        raise
    return seq


def peek(conn, scope: str, start: int = 1) -> int:
    """The serial `scope` would hand out next, WITHOUT consuming it."""
    row = conn.execute("SELECT next_seq FROM doc_counter WHERE scope=?", (scope,)).fetchone()
    return int(row[0]) if row else start


def ensure_seeds(conn) -> None:
    """Plant the reference counts. INSERT OR IGNORE: a counter the office has
    already moved (or corrected in Settings) is never clobbered."""
    for scope, next_seq in SEEDS.items():
        conn.execute("INSERT OR IGNORE INTO doc_counter (scope, next_seq) VALUES (?,?)",
                     (scope, next_seq))
    conn.commit()


def label_for(scope: str) -> str:
    """'qtn:T04' -> 'Quotation — T04', for the Settings → Numbering list."""
    head, _, rest = (scope or "").partition(":")
    name = _LABELS.get(head, head or scope)
    return f"{name} — {rest.replace(':', ' ')}" if rest else name


def _client(code: str) -> str:
    """Client codes are printed inside document numbers, so they are normalised
    to the same shape the counter scope uses."""
    c = re.sub(r"[^A-Z0-9]", "", (code or "").upper())
    if not c:
        raise ValueError("This document is numbered per client — the customer needs a code")
    return c


# --------------------------------------------------------------------------- #
# The registry (CONVENTIONS §3)
# --------------------------------------------------------------------------- #
def quotation_no(conn, client_code: str, d) -> str:
    """'T04/AT/130826/317' — the serial runs per client and never resets (§9-A)."""
    c, d = _client(client_code), _date(d)
    return f"{c}/AT/{ddmmyy(d)}/{_take(conn, f'qtn:{c}')}"


def ack_ref(conn, client_code: str, d) -> str:
    """'E01.21.08.26.01' — the sequence restarts for each client each day."""
    c, d = _client(client_code), _date(d)
    seq = _take(conn, f"ack:{c}:{ddmmyy(d)}")
    return f"{c}.{d.day:02d}.{d.month:02d}.{yy(d)}.{seq:02d}"


def work_order_no(conn, client_code: str, ack_date) -> dict:
    """{'long': 'E01.21.08.26.253.26', 'short': '253/26'}.

    The date part is the ACKNOWLEDGEMENT's date — a work order is born with the
    ack it belongs to — while the serial resets each calendar year (§9-C). The
    short form is what the shop floor writes on the job.
    """
    c, d = _client(client_code), _date(ack_date)
    seq = _take(conn, f"wo:{yy(d)}")
    return {"long": f"{c}.{d.day:02d}.{d.month:02d}.{yy(d)}.{seq}.{yy(d)}",
            "short": f"{seq}/{yy(d)}"}


def invoice_no(conn, d) -> str:
    """'AT/EI/26-27/169' — resets every fiscal year."""
    d = _date(d)
    return f"AT/EI/{fy(d)}/{_take(conn, f'inv:{fy(d)}'):03d}"


def bom_no(conn, d) -> str:
    """'AT/BOM/26-27/001' — placeholder format (§9-G), per fiscal year."""
    d = _date(d)
    return f"AT/BOM/{fy(d)}/{_take(conn, f'bom:{fy(d)}'):03d}"


def tc_no(cust_po: str, invoice_no: str) -> str:
    """'AT/TC/59812/EI-047/24-25' — DERIVED, no counter of its own: a test
    certificate is identified by the PO and the invoice it certifies."""
    m = _INV_TAIL.search(invoice_no or "")
    if not m:
        raise ValueError(f"{invoice_no or 'The invoice number'} isn't an invoice number "
                         "(expected …/{FY}/{serial})")
    po = (cust_po or "").strip()
    if not po:
        raise ValueError("A test certificate needs the customer's PO number")
    return f"AT/TC/{po}/EI-{m.group(2)}/{m.group(1)}"


def os_item_id(conn) -> str:
    """'OS-0001' — the outsourced-inventory ID.

    Deliberately the only place this scheme is written: outsourced stock is an
    isolated module and the owner may want a different ID entirely.
    """
    return f"OS-{_take(conn, 'os_item'):04d}"


def os_po_no(conn, d) -> str:
    """'AT/OS/26-27/001' — outgoing job order to a vendor, per fiscal year."""
    d = _date(d)
    return f"AT/OS/{fy(d)}/{_take(conn, f'os_po:{fy(d)}'):03d}"


def vendor_code(conn) -> str:
    """'V01' — one running series for every vendor."""
    return f"V{_take(conn, 'vendor'):02d}"
