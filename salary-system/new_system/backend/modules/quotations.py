"""Quotations & Invoices — the paperwork that leaves the workshop.

Both are the same shape (customer + dated lines + tax), so they share one
table with a ``kind``: a quotation quotes a price and expires; an invoice bills
for work and can be raised straight from an order (its items become the lines,
so nobody retypes quantities and rates).

Numbering mirrors Order Tracking: a per-financial-year sequence rendered
through a format from Settings (``QUO-{FY}-{SEQ}`` / ``INV-{FY}-{SEQ}``).

Printing is deliberately dependency-free: ``/print`` returns a clean A4-styled
HTML page that the browser prints or saves as PDF. No PDF library to install,
works offline, and the layout is editable by anyone who can read HTML.
"""

from __future__ import annotations

import html
import math
import sqlite3
from datetime import date, datetime

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import HTMLResponse
from pydantic import BaseModel

from ..core.db import row_to_dict
from ..core.deps import get_db, require_module
from ..core.rules import get_rules
from . import settings as settings_mod

router = APIRouter(prefix="/api/quotations",
                   dependencies=[Depends(require_module("quotations"))])

KINDS = ("quotation", "invoice")
STATUSES = ("draft", "sent", "accepted", "paid", "cancelled")
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
    return settings_mod.get_setting(conn, f"{kind}_number_format", DEFAULT_FORMATS[kind])


def next_doc_no(conn, kind: str, doc_date: date) -> str:
    """Atomic per-kind, per-FY sequence (same pattern as order numbers)."""
    fmt = format_for(conn, kind)
    fy = settings_mod.fy_label(doc_date)
    conn.execute("BEGIN IMMEDIATE")
    try:
        row = conn.execute("SELECT seq FROM doc_seq WHERE kind=? AND fy=?",
                           (kind, fy)).fetchone()
        seq = (row["seq"] if row else 0) + 1
        conn.execute("INSERT INTO doc_seq (kind, fy, seq) VALUES (?,?,?)"
                     " ON CONFLICT(kind, fy) DO UPDATE SET seq=excluded.seq",
                     (kind, fy, seq))
        conn.commit()
    except BaseException:
        conn.rollback()
        raise
    return settings_mod.render_order_no(fmt, doc_date, seq)


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
    doc_no = next_doc_no(conn, kind, date.fromisoformat(doc_date))
    try:
        cur = conn.execute(
            """INSERT INTO document (kind, doc_no, customer_id, order_id, doc_date,
                 valid_until, reference, tax_pct, notes, terms, status, created_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,'draft',?)""",
            (kind, doc_no, data["customer_id"], data.get("order_id") or None, doc_date,
             valid_until, _s(data.get("reference")), tax, _s(data.get("notes")),
             _s(data.get("terms")) or DEFAULT_TERMS, _now()))
    except sqlite3.IntegrityError:
        raise ValueError(f"{doc_no} already exists — check the number format in Settings")
    doc_id = cur.lastrowid
    for ln in lines:
        conn.execute(
            "INSERT INTO document_line (document_id, drawing_id, description, qty, unit, rate)"
            " VALUES (?,?,?,?,?,?)",
            (doc_id, ln["drawing_id"], ln["description"], ln["qty"], ln["unit"], ln["rate"]))
    conn.commit()
    return doc_id


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
            conn.execute(
                "INSERT INTO document_line (document_id, drawing_id, description, qty, unit, rate)"
                " VALUES (?,?,?,?,?,?)",
                (doc_id, ln["drawing_id"], ln["description"], ln["qty"], ln["unit"], ln["rate"]))
        conn.commit()
    except BaseException:
        conn.rollback()
        raise


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
    return d


def list_docs(conn, kind: str = "", q: str = "", customer_id=None) -> dict:
    sql = """SELECT d.id, d.kind, d.doc_no, d.doc_date, d.valid_until, d.status,
                    d.tax_pct, c.name AS customer_name, c.code AS customer_code,
                    o.order_no,
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
