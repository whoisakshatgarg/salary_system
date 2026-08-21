"""Quotations & Invoices — the paperwork that leaves the workshop.

Both are the same shape (customer + dated lines + tax), so they share one
table with a ``kind``: a quotation quotes a price and expires; an invoice bills
for work and can be raised straight from an order (its items become the lines,
so nobody retypes quantities and rates).

Numbering is the company's own (CONVENTIONS §3), owned by core/numbering:
quotations run per client code (``T04/AT/130826/317``), invoices per fiscal
year (``AT/EI/26-27/169``). Rows numbered before that switch keep their
``QUO-``/``INV-`` numbers forever.

A quotation can be REVISED: the header and lines are copied to a fresh draft
numbered ``… Rev-A``, ``Rev-B``…, the parent is marked superseded, and the
chain stays browsable. Only the tip of a chain can be revised.

Printing is deliberately dependency-free: ``/print`` returns a clean A4-styled
HTML page that the browser prints or saves as PDF. No PDF library to install,
works offline, and the layout is editable by anyone who can read HTML.
"""

from __future__ import annotations

import html
import math
import re
import sqlite3
from datetime import date, datetime

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import HTMLResponse
from pydantic import BaseModel

from ..core import numbering
from ..core.db import row_to_dict
from ..core.deps import get_db, require_module
from ..core.rules import get_rules
from . import customers as customers_mod
from . import settings as settings_mod

router = APIRouter(prefix="/api/quotations",
                   dependencies=[Depends(require_module("quotations"))])

KINDS = ("quotation", "invoice")
STATUSES = ("draft", "sent", "accepted", "paid", "cancelled")
# DEPRECATED, kept harmless: numbers now come from core/numbering, so the
# settings keys quotation_number_format / invoice_number_format are dead. Old
# documents keep the numbers these formats gave them.
DEFAULT_FORMATS = {"quotation": "QUO-{FY}-{SEQ}", "invoice": "INV-{FY}-{SEQ}"}
DEFAULT_TERMS = ("Prices are ex-works unless stated otherwise.\n"
                 "GST extra as applicable.\n"
                 "Delivery: as mutually agreed after receipt of a firm order.")


def _s(v) -> str:
    return (v or "").strip()


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _check_date(v, label: str, required: bool = False):
    v = _s(v)
    if not v:
        return date.today().isoformat() if required else None
    try:
        date.fromisoformat(v)
    except ValueError:
        raise ValueError(f"{label} isn't a real date (YYYY-MM-DD)")
    return v


def _check_num(v, label: str, positive: bool = False) -> float:
    try:
        f = float(v)
    except (TypeError, ValueError):
        raise ValueError(f"{label} must be a number")
    if not math.isfinite(f) or f < 0 or f > 1e12 or (positive and f <= 0):
        raise ValueError(f"{label} must be a normal{'  positive' if positive else ''} number")
    return round(f, 2)


# --------------------------------------------------------------------------- #
# Numbering
# --------------------------------------------------------------------------- #
def format_for(conn, kind: str) -> str:
    """DEPRECATED — the number formats moved to core/numbering (see above)."""
    return settings_mod.get_setting(conn, f"{kind}_number_format", DEFAULT_FORMATS[kind])


def client_code(conn, customer_id) -> str:
    """The customer's client code, assigning one if they have none.

    Called inside the numbering transaction: a quotation number is built from
    the code, so the two have to be decided together or a second quotation
    could be numbered under a different code.
    """
    row = conn.execute("SELECT id, name, code FROM customer WHERE id=?",
                       (customer_id,)).fetchone()
    if not row:
        raise ValueError("That customer no longer exists — reload the page")
    code = (row["code"] or "").strip()
    if not code:
        code = customers_mod.next_code(conn, row["name"])
        conn.execute("UPDATE customer SET code=? WHERE id=?", (code, row["id"]))
    return code


def next_doc_no(conn, kind: str, doc_date: date, customer_id=None) -> str:
    """The company's own numbering (CONVENTIONS §3), consumed atomically.

    BEGIN IMMEDIATE covers the client-code lookup, the code assignment it may
    have to make, and the counter bump: the take joins this transaction
    rather than opening one of its own.
    """
    conn.execute("BEGIN IMMEDIATE")
    try:
        if kind == "quotation":
            doc_no = numbering.quotation_no(conn, client_code(conn, customer_id), doc_date)
        else:
            doc_no = numbering.invoice_no(conn, doc_date)
        conn.commit()
    except BaseException:
        conn.rollback()
        raise
    return doc_no


# --------------------------------------------------------------------------- #
# Documents
# --------------------------------------------------------------------------- #
def _validate_lines(lines: list[dict]) -> list[dict]:
    if not lines:
        raise ValueError("Add at least one line")
    out = []
    for i, ln in enumerate(lines, 1):
        if not ln.get("drawing_id") and not _s(ln.get("description")):
            raise ValueError(f"Line {i}: pick a part or type a description")
        out.append({
            "drawing_id": ln.get("drawing_id") or None,
            "os_item_id": ln.get("os_item_id") or None,
            "description": _s(ln.get("description")),
            "qty": _check_num(ln.get("qty"), f"Line {i} quantity", positive=True),
            "unit": _s(ln.get("unit")) or "Nos",
            "rate": _check_num(ln.get("rate") or 0, f"Line {i} rate"),
        })
    return out


def _check_refs(conn, customer_id, lines: list[dict], order_id=None) -> None:
    if not conn.execute("SELECT 1 FROM customer WHERE id=?", (customer_id,)).fetchone():
        raise ValueError("That customer no longer exists — reload the page")
    if order_id and not conn.execute(
            "SELECT 1 FROM customer_order WHERE id=?", (order_id,)).fetchone():
        raise ValueError("That order no longer exists — reload the page")
    for i, ln in enumerate(lines, 1):
        if ln["drawing_id"] and not conn.execute(
                "SELECT 1 FROM drawing WHERE id=?", (ln["drawing_id"],)).fetchone():
            raise ValueError(f"Line {i}: that drawing no longer exists — reload the page")
        if ln.get("os_item_id") and not conn.execute(
                "SELECT 1 FROM os_item WHERE id=?", (ln["os_item_id"],)).fetchone():
            raise ValueError(f"Line {i}: that outsourced item no longer exists — reload the page")


def create_doc(conn, kind: str, data: dict) -> int:
    if kind not in KINDS:
        raise ValueError("Unknown document type")
    if not data.get("customer_id"):
        raise ValueError("Pick a customer")
    doc_date = _check_date(data.get("doc_date"), "Date", required=True)
    valid_until = _check_date(data.get("valid_until"), "Valid until")
    tax = _check_num(data.get("tax_pct") or 0, "Tax %")
    lines = _validate_lines(data.get("lines") or [])
    _check_refs(conn, data["customer_id"], lines, data.get("order_id"))
    doc_no = next_doc_no(conn, kind, date.fromisoformat(doc_date), data["customer_id"])
    try:
        cur = conn.execute(
            """INSERT INTO document (kind, doc_no, customer_id, order_id, doc_date,
                 valid_until, reference, tax_pct, notes, terms, status, created_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,'draft',?)""",
            (kind, doc_no, data["customer_id"], data.get("order_id") or None, doc_date,
             valid_until, _s(data.get("reference")), tax, _s(data.get("notes")),
             _s(data.get("terms")) or DEFAULT_TERMS, _now()))
    except sqlite3.IntegrityError:
        raise ValueError(f"{doc_no} already exists — correct the counter in "
                         "Settings → Numbering")
    doc_id = cur.lastrowid
    for ln in lines:
        _insert_line(conn, doc_id, ln)
    conn.commit()
    return doc_id


def _insert_line(conn, doc_id: int, ln: dict) -> None:
    conn.execute(
        "INSERT INTO document_line (document_id, drawing_id, os_item_id, description,"
        " qty, unit, rate) VALUES (?,?,?,?,?,?,?)",
        (doc_id, ln["drawing_id"], ln.get("os_item_id"), ln["description"],
         ln["qty"], ln["unit"], ln["rate"]))


def update_doc(conn, doc_id: int, data: dict) -> None:
    doc = row_to_dict(conn.execute("SELECT * FROM document WHERE id=?", (doc_id,)).fetchone())
    if not doc:
        raise ValueError("Document not found")
    if doc["status"] in ("paid", "cancelled"):
        raise ValueError(f"A {doc['status']} document can't be edited")
    if not data.get("customer_id"):
        raise ValueError("Pick a customer")
    doc_date = _check_date(data.get("doc_date"), "Date", required=True)
    valid_until = _check_date(data.get("valid_until"), "Valid until")
    tax = _check_num(data.get("tax_pct") or 0, "Tax %")
    lines = _validate_lines(data.get("lines") or [])
    _check_refs(conn, data["customer_id"], lines, data.get("order_id"))
    conn.execute("BEGIN IMMEDIATE")
    try:
        conn.execute(
            """UPDATE document SET customer_id=?, order_id=?, doc_date=?, valid_until=?,
                 reference=?, tax_pct=?, notes=?, terms=? WHERE id=?""",
            (data["customer_id"], data.get("order_id") or None, doc_date, valid_until,
             _s(data.get("reference")), tax, _s(data.get("notes")),
             _s(data.get("terms")), doc_id))
        conn.execute("DELETE FROM document_line WHERE document_id=?", (doc_id,))
        for ln in lines:
            _insert_line(conn, doc_id, ln)
        conn.commit()
    except BaseException:
        conn.rollback()
        raise


# --------------------------------------------------------------------------- #
# Revisions — a copy that supersedes its parent (SOP-DESIGN §2, CONVENTIONS §9-B)
# --------------------------------------------------------------------------- #
_REV_SUFFIX = re.compile(r"\s+Rev-[A-Z]+$")


def _revision_base(doc_no: str) -> str:
    """The number without its revision marker: revisions of a revision still
    hang off the ORIGINAL number, they don't stack ' Rev-A Rev-B'."""
    return _REV_SUFFIX.sub("", _s(doc_no))


def _rev_letter(n: int) -> str:
    """0 -> 'A', 25 -> 'Z', 26 -> 'AA' — a chain that long is a filing problem,
    but it must still produce a number."""
    out = ""
    n += 1
    while n:
        n, r = divmod(n - 1, 26)
        out = chr(65 + r) + out
    return out


def revision_chain(conn, doc_id: int) -> list[dict]:
    """Every revision of one document, oldest first; `active` is the tip."""
    root, seen = doc_id, {doc_id}
    while True:
        r = conn.execute("SELECT revises_document_id FROM document WHERE id=?",
                         (root,)).fetchone()
        parent = r["revises_document_id"] if r else None
        if not parent or parent in seen:
            break
        seen.add(parent)
        root = parent
    out, cur, walked = [], root, set()
    while cur and cur not in walked:
        walked.add(cur)
        r = conn.execute(
            "SELECT id, doc_no, status, doc_date, superseded_by FROM document WHERE id=?",
            (cur,)).fetchone()
        if not r:
            break
        out.append({"id": r["id"], "doc_no": r["doc_no"], "status": r["status"],
                    "doc_date": r["doc_date"], "active": r["superseded_by"] is None})
        nxt = conn.execute(
            "SELECT id FROM document WHERE revises_document_id=? ORDER BY id LIMIT 1",
            (r["id"],)).fetchone()
        cur = nxt["id"] if nxt else None
    return out


def revise_doc(conn, doc_id: int) -> int:
    """Copy a document to a new draft revision and supersede the original.

    Only the tip of a chain can be revised: a superseded document is history,
    and branching it would leave two documents claiming to be the latest.
    """
    conn.execute("BEGIN IMMEDIATE")     # the tip check and the copy are one act
    try:
        doc = row_to_dict(conn.execute(
            "SELECT * FROM document WHERE id=?", (doc_id,)).fetchone())
        if not doc:
            raise ValueError("Document not found")
        if doc["superseded_by"]:
            raise ValueError("This one has already been revised — revise the latest revision")
        doc_no = (f"{_revision_base(doc['doc_no'])} "
                  f"Rev-{_rev_letter(max(len(revision_chain(conn, doc_id)) - 1, 0))}")
        try:
            cur = conn.execute(
                """INSERT INTO document (kind, doc_no, customer_id, order_id, doc_date,
                     valid_until, reference, tax_pct, notes, terms, status,
                     revises_document_id, created_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?,'draft',?,?)""",
                (doc["kind"], doc_no, doc["customer_id"], doc["order_id"], doc["doc_date"],
                 doc["valid_until"], doc["reference"], doc["tax_pct"], doc["notes"],
                 doc["terms"], doc_id, _now()))
        except sqlite3.IntegrityError:
            raise ValueError(f"{doc_no} already exists — renumber that one first")
        new_id = cur.lastrowid
        conn.execute(
            """INSERT INTO document_line (document_id, drawing_id, os_item_id,
                 description, qty, unit, rate)
               SELECT ?, drawing_id, os_item_id, description, qty, unit, rate
               FROM document_line WHERE document_id=? ORDER BY id""", (new_id, doc_id))
        conn.execute("UPDATE document SET superseded_by=? WHERE id=?", (new_id, doc_id))
        conn.commit()
    except BaseException:
        conn.rollback()
        raise
    return new_id


def totals(subtotal: float, tax_pct: float) -> dict:
    tax = round(subtotal * (tax_pct or 0) / 100, 2)
    return {"subtotal": round(subtotal, 2), "tax": tax, "total": round(subtotal + tax, 2)}


def get_doc(conn, doc_id: int) -> dict:
    d = row_to_dict(conn.execute(
        """SELECT d.*, c.name AS customer_name, c.code AS customer_code, c.gstin,
                  c.address_billing, c.address_shipping, c.payment_terms,
                  o.order_no
           FROM document d JOIN customer c ON c.id=d.customer_id
           LEFT JOIN customer_order o ON o.id=d.order_id
           WHERE d.id=?""", (doc_id,)).fetchone())
    if not d:
        raise ValueError("Document not found")
    d["lines"] = [dict(r) for r in conn.execute(
        """SELECT l.*, dr.drawing_no, dr.revision FROM document_line l
           LEFT JOIN drawing dr ON dr.id=l.drawing_id
           WHERE l.document_id=? ORDER BY l.id""", (doc_id,))]
    for ln in d["lines"]:
        ln["amount"] = round(ln["qty"] * ln["rate"], 2)
    d.update(totals(sum(ln["amount"] for ln in d["lines"]), d["tax_pct"]))
    # only when there IS a chain: a document nobody revised has no history rail
    d["revisions"] = (revision_chain(conn, doc_id)
                      if d["revises_document_id"] or d["superseded_by"] else [])
    return d


def list_docs(conn, kind: str = "", q: str = "", customer_id=None) -> dict:
    sql = """SELECT d.id, d.kind, d.doc_no, d.doc_date, d.valid_until, d.status,
                    d.tax_pct, c.name AS customer_name, c.code AS customer_code,
                    o.order_no, d.superseded_by,
                    (SELECT COALESCE(SUM(l.qty*l.rate),0) FROM document_line l
                       WHERE l.document_id=d.id) AS subtotal
             FROM document d JOIN customer c ON c.id=d.customer_id
             LEFT JOIN customer_order o ON o.id=d.order_id WHERE 1=1"""
    args: list = []
    if _s(kind):
        sql += " AND d.kind=?"
        args.append(kind)
    if customer_id:
        sql += " AND d.customer_id=?"
        args.append(customer_id)
    if _s(q):
        like = f"%{_s(q)}%"
        sql += " AND (d.doc_no LIKE ? OR c.name LIKE ? OR c.code LIKE ? OR d.reference LIKE ?)"
        args += [like] * 4
    sql += " ORDER BY d.doc_date DESC, d.id DESC"
    rows = []
    for r in conn.execute(sql, args):
        row = dict(r)
        row.update(totals(row.pop("subtotal"), row["tax_pct"]))
        rows.append(row)
    counts = {k: 0 for k in KINDS}
    for r in conn.execute("SELECT kind, COUNT(*) AS n FROM document GROUP BY kind"):
        counts[r["kind"]] = r["n"]
    return {"rows": rows, "counts": counts}


def set_status(conn, doc_id: int, status: str) -> dict:
    if status not in STATUSES:
        raise ValueError("Unknown status")
    cur = conn.execute("UPDATE document SET status=? WHERE id=?", (status, doc_id))
    conn.commit()
    if not cur.rowcount:
        raise ValueError("Document not found")
    return get_doc(conn, doc_id)


def delete_doc(conn, doc_id: int) -> None:
    cur = conn.execute("DELETE FROM document WHERE id=?", (doc_id,))
    conn.commit()
    if not cur.rowcount:
        raise ValueError("Document not found")


def from_order(conn, order_id: int, kind: str = "invoice") -> dict:
    """Prefill a document from an order — its items become the lines."""
    o = row_to_dict(conn.execute(
        "SELECT o.*, c.name AS customer_name FROM customer_order o"
        " JOIN customer c ON c.id=o.customer_id WHERE o.id=?", (order_id,)).fetchone())
    if not o:
        raise ValueError("Order not found")
    lines = [{
        "drawing_id": r["drawing_id"], "description": r["description"] or r["drawing_no"] or "",
        "qty": r["qty"], "unit": r["unit"], "rate": r["rate"],
    } for r in conn.execute(
        "SELECT i.*, d.drawing_no FROM order_item i LEFT JOIN drawing d ON d.id=i.drawing_id"
        " WHERE i.order_id=? ORDER BY i.id", (order_id,))]
    return {"kind": kind, "customer_id": o["customer_id"], "customer_name": o["customer_name"],
            "order_id": o["id"], "order_no": o["order_no"],
            "reference": o["customer_po"] or o["order_no"], "lines": lines}


# --------------------------------------------------------------------------- #
# Print view — plain HTML, printed/saved as PDF by the browser
# --------------------------------------------------------------------------- #
def _money(n) -> str:
    neg = n < 0
    whole, frac = divmod(round(abs(n) * 100), 100)
    s = str(whole)
    if len(s) > 3:                       # Indian grouping: 12,34,567
        head, tail = s[:-3], s[-3:]
        parts = []
        while len(head) > 2:
            parts.insert(0, head[-2:])
            head = head[:-2]
        if head:
            parts.insert(0, head)
        s = ",".join(parts + [tail])
    return f"{'-' if neg else ''}{s}.{frac:02d}"


def render_print(conn, doc_id: int) -> str:
    d = get_doc(conn, doc_id)
    r = get_rules()
    company = r.get("company_name", "APEX THERMOCON")
    company_addr = settings_mod.get_setting(conn, "company_address", "") or ""
    company_gstin = settings_mod.get_setting(conn, "company_gstin", "") or ""
    title = "QUOTATION" if d["kind"] == "quotation" else "TAX INVOICE"
    e = html.escape

    rows = "".join(
        f"<tr><td>{i}</td><td><b>{e(ln['drawing_no'] or ln['description'] or '')}</b>"
        + (f"<div class='sub'>{e(ln['description'])}</div>"
           if ln['drawing_no'] and ln['description'] else "")
        + f"</td><td class='r'>{ln['qty']:g}</td><td>{e(ln['unit'] or '')}</td>"
          f"<td class='r'>{_money(ln['rate'])}</td>"
          f"<td class='r'>{_money(ln['amount'])}</td></tr>"
        for i, ln in enumerate(d["lines"], 1))

    meta = [("Date", d["doc_date"])]
    if d["kind"] == "quotation" and d["valid_until"]:
        meta.append(("Valid until", d["valid_until"]))
    if d["order_no"]:
        meta.append(("Against order", d["order_no"]))
    if d["reference"]:
        meta.append(("Reference", d["reference"]))
    if d["payment_terms"]:
        meta.append(("Payment terms", d["payment_terms"]))
    meta_html = "".join(f"<tr><td>{e(k)}</td><td><b>{e(str(v))}</b></td></tr>" for k, v in meta)

    tax_row = (f"<tr><td>GST @ {d['tax_pct']:g}%</td><td class='r'>{_money(d['tax'])}</td></tr>"
               if d["tax_pct"] else "")
    NL = chr(10)
    addr_block = e(company_addr) + (f"{NL}GSTIN: {e(company_gstin)}" if company_gstin else "")
    cust_gstin = f'<div class="sub">GSTIN: {e(d["gstin"])}</div>' if d["gstin"] else ""
    who_label = "Quotation for" if d["kind"] == "quotation" else "Billed to"
    notes_block = (f'<div class="note"><b>Notes</b>{NL}{e(d["notes"])}</div>'
                   if d["notes"] else "")
    terms_block = (f'<div class="note"><b>Terms</b>{NL}{e(d["terms"])}</div>'
                   if d["terms"] else "")

    return f"""<!DOCTYPE html>
<html><head><meta charset="utf-8"><title>{e(d['doc_no'])}</title>
<style>
  @page {{ size: A4; margin: 14mm; }}
  * {{ box-sizing: border-box; }}
  body {{ font: 12px/1.5 ui-sans-serif, system-ui, "Segoe UI", Roboto, sans-serif;
         color: #0f172a; margin: 0; padding: 24px; max-width: 800px; }}
  .bar {{ display: flex; align-items: flex-start; border-bottom: 2px solid #0f172a;
          padding-bottom: 12px; margin-bottom: 18px; }}
  .co {{ font-size: 20px; font-weight: 800; letter-spacing: .5px; }}
  .addr {{ color: #475569; white-space: pre-line; font-size: 11px; margin-top: 2px; }}
  .doc {{ margin-left: auto; text-align: right; }}
  .doc .t {{ font-size: 17px; font-weight: 800; letter-spacing: 2px; }}
  .doc .no {{ font-family: ui-monospace, Menlo, Consolas, monospace; font-size: 14px;
              font-weight: 700; margin-top: 2px; }}
  .cols {{ display: flex; gap: 24px; margin-bottom: 16px; }}
  .card {{ flex: 1; }}
  .lbl {{ font-size: 10px; text-transform: uppercase; letter-spacing: .1em;
          color: #64748b; margin-bottom: 4px; }}
  .who {{ font-weight: 700; }}
  .who .sub {{ font-weight: 400; color: #475569; white-space: pre-line; font-size: 11px; }}
  table {{ width: 100%; border-collapse: collapse; }}
  .meta td {{ padding: 2px 0; font-size: 11px; color: #475569; }}
  .meta td:last-child {{ text-align: right; color: #0f172a; }}
  .items th {{ text-align: left; background: #f1f5f9; padding: 7px 8px;
               font-size: 10px; text-transform: uppercase; letter-spacing: .06em;
               color: #475569; border-bottom: 1px solid #cbd5e1; }}
  .items td {{ padding: 7px 8px; border-bottom: 1px solid #e2e8f0; vertical-align: top; }}
  .items .sub {{ color: #64748b; font-size: 11px; font-weight: 400; }}
  .r {{ text-align: right; }}
  .tot {{ margin-left: auto; width: 260px; margin-top: 10px; }}
  .tot td {{ padding: 4px 0; }}
  .tot tr:last-child td {{ border-top: 2px solid #0f172a; font-weight: 800;
                           font-size: 14px; padding-top: 7px; }}
  .note {{ margin-top: 22px; font-size: 11px; color: #475569; white-space: pre-line; }}
  .sign {{ margin-top: 46px; text-align: right; font-size: 11px; }}
  .sign .line {{ margin-top: 40px; border-top: 1px solid #94a3b8;
                 display: inline-block; padding-top: 4px; min-width: 200px; }}
  .print {{ position: fixed; top: 14px; right: 14px; }}
  .print button {{ font: inherit; padding: 8px 16px; border-radius: 8px; border: 0;
                   background: #1d4ed8; color: #fff; font-weight: 600; cursor: pointer; }}
  @media print {{ .print {{ display: none; }} body {{ padding: 0; }} }}
</style></head>
<body>
  <div class="print"><button onclick="window.print()">Print / Save as PDF</button></div>
  <div class="bar">
    <div>
      <div class="co">{e(company)}</div>
      <div class="addr">{addr_block}</div>
    </div>
    <div class="doc"><div class="t">{title}</div><div class="no">{e(d['doc_no'])}</div></div>
  </div>
  <div class="cols">
    <div class="card">
      <div class="lbl">{who_label}</div>
      <div class="who">{e(d['customer_name'])}
        <span style="color:#64748b">({e(d['customer_code'] or '')})</span>
        <div class="sub">{e(d['address_billing'] or '')}</div>
        {cust_gstin}
      </div>
    </div>
    <div class="card"><table class="meta">{meta_html}</table></div>
  </div>
  <table class="items">
    <thead><tr><th style="width:28px">#</th><th>Part / description</th>
      <th class="r" style="width:70px">Qty</th><th style="width:56px">Unit</th>
      <th class="r" style="width:90px">Rate</th><th class="r" style="width:100px">Amount</th></tr></thead>
    <tbody>{rows}</tbody>
  </table>
  <table class="tot">
    <tr><td>Subtotal</td><td class="r">{_money(d['subtotal'])}</td></tr>
    {tax_row}
    <tr><td>Total (₹)</td><td class="r">{_money(d['total'])}</td></tr>
  </table>
  {notes_block}
  {terms_block}
  <div class="sign">For {e(company)}<div class="line">Authorised signatory</div></div>
</body></html>"""


# --------------------------------------------------------------------------- #
# Routes
# --------------------------------------------------------------------------- #
class LineIn(BaseModel):
    drawing_id: int | None = None
    os_item_id: int | None = None    # a bought-out part instead of a drawing
    description: str = ""
    qty: float
    unit: str = "Nos"
    rate: float = 0


class DocIn(BaseModel):
    kind: str = "quotation"
    customer_id: int
    order_id: int | None = None
    doc_date: str = ""
    valid_until: str = ""
    reference: str = ""
    tax_pct: float = 0
    notes: str = ""
    terms: str = ""
    lines: list[LineIn] = []


class StatusIn(BaseModel):
    status: str


def _400(fn, *args, **kw):
    try:
        return fn(*args, **kw)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/refs")
def refs(conn=Depends(get_db)):
    """Everything the document form needs (this module's grant)."""
    return {
        "customers": [dict(r) for r in conn.execute(
            "SELECT id, name, code, gstin, payment_terms FROM customer WHERE active=1"
            " ORDER BY name COLLATE NOCASE")],
        "units": settings_mod.units(conn),
        "drawings": [dict(r) for r in conn.execute(
            """SELECT d.id, d.drawing_no, d.revision, d.description, d.unit,
                      (SELECT r.rate FROM drawing_rate r WHERE r.drawing_id=d.id
                         ORDER BY r.rate_date DESC, r.id DESC LIMIT 1) AS latest_rate
               FROM drawing d WHERE d.active=1
               ORDER BY d.drawing_no COLLATE NOCASE, d.revision""")],
        "orders": [dict(r) for r in conn.execute(
            """SELECT o.id, o.order_no, o.customer_id, c.name AS customer_name
               FROM customer_order o JOIN customer c ON c.id=o.customer_id
               ORDER BY o.id DESC LIMIT 200""")],
        "default_terms": DEFAULT_TERMS,
        "statuses": list(STATUSES),
    }


@router.get("")
def docs(kind: str = "", q: str = "", customer_id: int | None = None, conn=Depends(get_db)):
    return list_docs(conn, kind=kind, q=q, customer_id=customer_id)


@router.post("")
def create(body: DocIn, conn=Depends(get_db)):
    doc_id = _400(create_doc, conn, body.kind, body.model_dump())
    return get_doc(conn, doc_id)


@router.get("/from-order/{order_id}")
def prefill_from_order(order_id: int, kind: str = "invoice", conn=Depends(get_db)):
    return _400(from_order, conn, order_id, kind)


@router.get("/{doc_id}")
def detail(doc_id: int, conn=Depends(get_db)):
    try:
        return get_doc(conn, doc_id)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.put("/{doc_id}")
def update(doc_id: int, body: DocIn, conn=Depends(get_db)):
    _400(update_doc, conn, doc_id, body.model_dump())
    return get_doc(conn, doc_id)


@router.post("/{doc_id}/status")
def status(doc_id: int, body: StatusIn, conn=Depends(get_db)):
    return _400(set_status, conn, doc_id, body.status)


@router.post("/{doc_id}/revise")
def revise(doc_id: int, conn=Depends(get_db)):
    """A fresh draft ' Rev-A' carrying this document's header and lines."""
    return get_doc(conn, _400(revise_doc, conn, doc_id))


@router.delete("/{doc_id}")
def remove(doc_id: int, conn=Depends(get_db)):
    _400(delete_doc, conn, doc_id)
    return {"ok": True}


@router.get("/{doc_id}/print", response_class=HTMLResponse)
def print_view(doc_id: int, conn=Depends(get_db)):
    try:
        return HTMLResponse(render_print(conn, doc_id))
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
