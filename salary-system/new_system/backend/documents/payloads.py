"""Auto-fill builders — order + ledger + inventory -> one paper payload.

One builder per kind (SOP-DESIGN §5), all the same shape::

    build_<kind>(conn, order_id, opts: dict) -> payload

The payload IS the paper: ``registry.py``'s module docstring holds the
canonical schema per kind and every builder emits **exactly** those keys —
every key present, nothing missing, nothing extra, blanks as ``''`` and never
``None`` (a ``None`` would render as the template's default instead of the
blank the office asked for).  ``SCHEMAS`` below is that contract in code;
``check_schema`` is what ``service.update_payload`` validates hand edits with.

Auto-fill happens ONCE, at creation (SOP-DESIGN §2): after that the payload is
the editable truth and only an explicit *Refill* re-runs a builder.  So every
default here is a *starting point*, never a lock — currency, terms, ports,
weights and the export constants are all payload fields the office can edit.

What the builders deliberately do NOT do
----------------------------------------
Consume numbers.  ``ack_ref``, the work-order pair, a fresh invoice number and
``bom_no`` come off counters, and a counter must only be burnt by the
transaction that inserts the row (SOP-DESIGN §2/§3).  The builders leave those
fields ``''`` and ``service.create_paper`` injects them inside its
``BEGIN IMMEDIATE``.  Numbers that are *derived* rather than counted (the test
certificate's ``AT/TC/...``, the COC's ``COC-PO-...``) are built here, as is
the quotation's number, which is simply its ledger row's — a paper never
re-numbers a ledger document.

Two documented extensions to the canonical schema, both nested (the top-level
key set is exactly the docstring's):

* ``currency.head`` — ``"USD ($)"``.  The invoice template prints the currency
  as its own column head and the registry reads ``currency.head``; the
  canonical ``{code, symbol, header}`` has nowhere to put it.
* test-certificate items carry ``spare_1..spare_5`` beside ``chem`` — the TC
  grid's five spare element columns read ``item.spare_N`` (registry
  ``_spare_item``), so the values for ``extra_elements`` have to live on the
  item in the same order as the payload's ``extra_elements`` heads.
"""

from __future__ import annotations

import json
import re
from datetime import date

from ..core import currency, numbering
from . import registry

# --------------------------------------------------------------------------
# Company constants (CONVENTIONS §1).  Editable DEFAULTS: they are copied into
# the payload at creation and the office edits them on the paper, which is why
# they are values here and not string literals down in the builders.
# --------------------------------------------------------------------------

COMPANY = {
    "iec": "0509008631",
    "ad_code": "0292085 / 2690009",
    "hts_line": registry.HTS_LINE,
    "gsp_line": registry.GSP_LINE,
    "pre_carriage": "",
    "place_receipt": "GHAZIABAD",
    "port_loading": "NEW DELHI",
    "vessel": "AIR",
    "origin_country": "INDIA",
    "terms": "DDU",
    "remittance_block": registry.REMITTANCE_2026,
    "validity_line": registry.VALIDITY_LINE,
    "approved_by": "Sumesh Garg",
    "prepared_by": "",
    "jurisdiction_note": "Our offer is subject to Delhi, India Jurisdiction.",
    "payment_terms": "N 30 by Wire Transfer",
    "authenticator": "Q.A. MANAGER",
    "plating": "NA",
    "finishing": "NA",
    "marks_lines": ["", "AS ADDRESS", ""],
}

# The chemistry columns the TC grid prints by name; anything else a heat
# carries goes to the five spare columns (CONVENTIONS §5).
CHEM_COLUMNS = ("C", "Mn", "Si", "P", "S", "Cr", "Ni", "Mo")
SPARE_COLUMNS = 5

# CONVENTIONS §9-D: the printed currency is a FIELD defaulted from the
# customer, not the fixed 'U.S.D.' the reference documents mislabel it with.
# The country -> currency map itself is core/currency.py, shared with the
# ledger UI: a paper and its ledger row must never name different money.
# ``head`` is this module's documented extension to the canonical three keys.
_HEADED = {c["code"]: dict(c, head=f"{c['code']} ({c['symbol']})")
           for c in (currency.INR, currency.USD, currency.GBP)}
INR, USD, GBP = _HEADED["INR"], _HEADED["USD"], _HEADED["GBP"]

# Sub-unit names for amount-in-words, by currency.
_SUBUNIT = {"USD": "Cents", "GBP": "Pence", "EUR": "Cents", "INR": "Paise"}

# Words that say nothing about WHICH customer this is (COC short name).
_NOISE = {"pvt", "private", "ltd", "limited", "llp", "inc", "co", "company",
          "corp", "corporation", "incorporated", "gmbh", "plc", "and", "the"}


# --------------------------------------------------------------------------
# The canonical payload schemas (registry.py's module docstring, in code)
# --------------------------------------------------------------------------

_COMMON = ("number", "date_iso", "order_no", "customer", "currency", "items")

# The header block the invoice and its packing list share, field for field.
_EXPORT_HEADER = (
    "number", "date_iso", "order_no", "customer", "currency",
    "invoice_no", "buyer_po_block", "iec", "ad_code", "consignee_lines",
    "buyer_lines", "pre_carriage", "place_receipt", "vessel", "port_loading",
    "port_discharge", "origin_country", "final_destination", "terms",
    "marks_lines", "hts_line", "total_weight_line",
)

SCHEMAS = {
    "quotation": _COMMON + (
        "client_code", "rfq_ref", "rfq_date_iso", "total", "validity_line",
        "price_basis", "lead_time", "payment_terms", "guarantee",
        "taxes_duties", "note", "approved_by", "prepared_by"),
    "ack": _COMMON + (
        "bill_to_lines", "ship_to_lines", "cust_po", "po_date_iso",
        "quotation_ref", "client_code", "ack_ref", "ack_date_iso", "contacts",
        "price_basis", "payment_terms", "ship_date_iso", "wo_no_long",
        "total", "remittance_block", "currency_header"),
    "work_order": _COMMON + ("wo_no_short", "client_code", "cust_po"),
    "invoice": _EXPORT_HEADER + ("items", "gsp_line", "totals", "amount_words"),
    "packing_list": _EXPORT_HEADER + ("boxes", "totals"),
    "coc": _COMMON + (
        "customer_caps", "customer_short", "po", "invoice_no",
        "invoice_date_iso", "part_desc", "material", "plating", "finishing",
        "qty_shipped", "authenticator", "date_shipped_iso"),
    "test_cert": _COMMON + (
        "cert_no", "cert_date_iso", "customer_line", "po", "po_date_iso",
        "invoice_no", "invoice_date_iso", "extra_elements"),
    "bom": _COMMON + (
        "bom_no", "customer_line", "po", "po_date_iso", "wo_no", "part_assy"),
}

KINDS = tuple(sorted(SCHEMAS))

# Keys the app itself keeps on the payload (the paper table has no meta
# column).  Leading underscore = ours, not the office's: '_opts' remembers the
# arguments a Refill has to replay, '_wo_no_short' remembers the shop-floor
# form of the work-order number the ack reserved.
META_PREFIX = "_"


def schema(kind: str) -> tuple:
    try:
        return SCHEMAS[kind]
    except KeyError:
        raise ValueError(f"Unknown paper kind {kind!r}") from None


def check_schema(kind: str, payload: dict) -> None:
    """Refuse a payload that is not exactly this kind's schema (+ meta keys)."""
    keys = {k for k in (payload or {}) if not str(k).startswith(META_PREFIX)}
    want = set(schema(kind))
    missing, extra = sorted(want - keys), sorted(keys - want)
    name = kind.replace("_", " ")
    if missing:
        raise ValueError(f"This {name} is missing {', '.join(missing)}")
    if extra:
        raise ValueError(f"A {name} has no field called {', '.join(extra)}")


# --------------------------------------------------------------------------
# Small shared helpers
# --------------------------------------------------------------------------

def _s(v) -> str:
    """Never None: a blank field prints blank, it does not fall back."""
    return "" if v is None else str(v).strip()


def today_iso() -> str:
    return date.today().isoformat()


def _num(v):
    """A quantity as it should print: 30.0 -> 30, 2.5 stays 2.5."""
    try:
        f = float(v)
    except (TypeError, ValueError):
        return v if v not in (None, "") else ""
    return int(f) if f.is_integer() else round(f, 3)


def _money(v, places: int = 2) -> str:
    try:
        return f"{float(v):.{places}f}"
    except (TypeError, ValueError):
        return ""


def address_lines(text) -> list[str]:
    return [ln.strip() for ln in _s(text).splitlines() if ln.strip()]


def split_country(text) -> tuple[list[str], str]:
    """Address lines and the country they end with.

    Every reference address ends with its country on the last line ('USA',
    'England'), which is exactly where the quotation prints it and where the
    currency default reads it from.  A one-line address falls back to the last
    comma-separated chunk ('Reno, NV 89511, USA').  Heuristic on purpose: both
    fields stay editable on the paper.
    """
    lines = address_lines(text)
    if len(lines) >= 2:
        return lines[:-1], lines[-1]
    if lines and "," in lines[0]:
        head, _, tail = lines[0].rpartition(",")
        return [head.strip()], tail.strip()
    return lines, ""


def currency_for(country) -> dict:
    """The customer's currency (core/currency) plus this module's ``head``."""
    return dict(_HEADED[currency.currency_for(country)["code"]])


# --- amount in words -------------------------------------------------------

_ONES = ("", "One", "Two", "Three", "Four", "Five", "Six", "Seven", "Eight",
         "Nine", "Ten", "Eleven", "Twelve", "Thirteen", "Fourteen", "Fifteen",
         "Sixteen", "Seventeen", "Eighteen", "Nineteen")
_TENS = ("", "", "Twenty", "Thirty", "Forty", "Fifty", "Sixty", "Seventy",
         "Eighty", "Ninety")
_GROUPS = ("", "Thousand", "Million", "Billion", "Trillion")


def _under_thousand(n: int) -> list[str]:
    out = []
    if n >= 100:
        out += [_ONES[n // 100], "Hundred"]
        n %= 100
    if n >= 20:
        out.append(_TENS[n // 10])
        n %= 10
    if n:
        out.append(_ONES[n])
    return out


def _words(n: int) -> str:
    if n == 0:
        return "Zero"
    groups = []
    while n:
        groups.append(n % 1000)
        n //= 1000
    parts = []
    for power in range(len(groups) - 1, -1, -1):
        if not groups[power]:
            continue
        parts += _under_thousand(groups[power])
        if power:
            parts.append(_GROUPS[power])
    return " ".join(parts)


def amount_in_words(amount, code: str = "USD") -> str:
    """``USD Three Thousand Eight Hundred Fifty Eight Only`` (CONVENTIONS §5).

    International grouping (thousand/million), Title Case, ``Only`` suffix.
    The sub-unit is printed only when there IS one: 3858.12 becomes
    ``... Fifty Eight and Cents Twelve Only``.
    """
    code = _s(code).upper() or "USD"
    try:
        value = float(amount or 0)
    except (TypeError, ValueError):
        return ""
    sign = "Minus " if value < 0 else ""
    cents = int(round(abs(value) * 100))
    whole, sub = divmod(cents, 100)
    text = f"{sign}{_words(whole)}"
    if sub:
        text += f" and {_SUBUNIT.get(code, 'Cents')} {_words(sub)}"
    return f"{code} {text} Only"


def weight_line(net="", gross="") -> str:
    """``Total Weight in Kgs: Net Wt: 50.000       Gross Wt: 53.600``."""
    return f"Total Weight in Kgs: Net Wt: {_s(net)}       Gross Wt: {_s(gross)}"


# --------------------------------------------------------------------------
# Sources: customer, order, items
# --------------------------------------------------------------------------

def customer_row(conn, customer_id):
    row = conn.execute("SELECT * FROM customer WHERE id=?", (customer_id,)).fetchone()
    if not row:
        raise ValueError("That customer no longer exists — reload the page")
    return row


def primary_contact(conn, customer_id) -> dict:
    """The first contact on the customer — the ack's CONTACTS block.

    Fax is a real column now (both reference documents print one); a contact
    saved before it existed simply has none, and the field stays editable on
    the paper.
    """
    row = conn.execute(
        "SELECT name, email, phone, fax FROM customer_contact WHERE customer_id=?"
        " ORDER BY id LIMIT 1", (customer_id,)).fetchone()
    if not row:
        return {"name": "", "email": "", "tel": "", "fax": ""}
    return {"name": _s(row["name"]), "email": _s(row["email"]),
            "tel": _s(row["phone"]), "fax": _s(row["fax"])}


def customer_country(customer_row_) -> tuple[list[str], str]:
    """``(address lines, country)`` — the FIELD first, the heuristic second.

    ``customer.country`` is what the office typed, so it wins; when it is
    blank we still read the tail of the address, which is where every
    reference document puts the country.  When the field IS set the address
    keeps all its lines: dropping the last one would delete a street.
    """
    text = customer_row_["address_billing"]
    typed = _s(customer_row_["country"] if "country" in customer_row_.keys() else "")
    if typed:
        return address_lines(text), typed
    return split_country(text)


def customer_block(conn, customer_id) -> dict:
    c = customer_row(conn, customer_id)
    lines, country = customer_country(c)
    return {"name": _s(c["name"]), "country": country, "address_lines": lines,
            "contact": primary_contact(conn, customer_id)}


def _named_block(name: str, address, upper: bool = False) -> list[str]:
    """Customer name on top of their address — the ack's Bill To / Ship To and
    the invoice's Consignee / Buyer are both this shape."""
    head = name.upper() if upper else name
    return [_s(head)] + address_lines(address)


def order_row(conn, order_id):
    row = conn.execute(
        "SELECT o.*, c.name AS customer_name FROM customer_order o"
        " JOIN customer c ON c.id=o.customer_id WHERE o.id=?", (order_id,)).fetchone()
    if not row:
        raise ValueError("Order not found")
    return row


def order_items(conn, order_id) -> list[dict]:
    """An order's items with their drawing's part code and material."""
    return [dict(r) for r in conn.execute(
        """SELECT i.id, i.order_id, i.drawing_id, i.description, i.qty, i.unit,
                  i.rate, d.drawing_no, d.revision, d.description AS drawing_desc,
                  d.grade, d.material_class
           FROM order_item i LEFT JOIN drawing d ON d.id=i.drawing_id
           WHERE i.order_id=? ORDER BY i.id""", (order_id,))]


def part_code(item) -> str:
    """The customer's SKU for this line — the drawing number IS the part code
    (CONVENTIONS §5); a free-text item has none, and prints '-' like the
    references do."""
    return _s(item.get("drawing_no")) or "-"


def item_description(item) -> str:
    return _s(item.get("description")) or _s(item.get("drawing_desc"))


def item_material(item) -> str:
    return _s(item.get("grade")) or _s(item.get("material_class"))


def merged_items(conn, order_ids: list[int], item_ids=None) -> list[dict]:
    """The items of SEVERAL orders in one list — an invoice covers many POs
    (CONVENTIONS §7).  Each row remembers the order it came from, because the
    PO number prints per line."""
    wanted = {int(i) for i in (item_ids or [])} or None
    out = []
    for oid in order_ids:
        o = order_row(conn, oid)
        for it in order_items(conn, oid):
            if wanted is not None and it["id"] not in wanted:
                continue
            it["order_no"] = o["order_no"]
            it["customer_po"] = _s(o["customer_po"])
            it["order_date"] = _s(o["order_date"])
            it["order_pk"] = o["id"]
            out.append(it)
    return out


def issued_heats(conn, order_no: str) -> list[dict]:
    """The heats issued against an order number, chemistry included.

    Same join the order page uses: the usage log records the order NUMBER, so
    the heat register stays the single source of truth for traceability.
    """
    heats = [dict(r) for r in conn.execute(
        """SELECT DISTINCT h.id, h.heat_number, h.grade, h.material_class,
                  h.size_section
           FROM heat_movement m JOIN heat h ON h.id=m.heat_id
           WHERE m.type='issue' AND m.order_id=?
           ORDER BY h.heat_number""", (order_no,))]
    for h in heats:
        h["chem"] = {r["element"]: r["percent"] for r in conn.execute(
            "SELECT element, percent FROM heat_composition WHERE heat_id=?"
            " ORDER BY id", (h["id"],))}
    return heats


def _paper_payload(conn, paper_id, kind=None) -> dict:
    """One paper's stored payload, by id — how the papers that hang off another
    paper (packing list, COC, TC) read their header."""
    row = conn.execute("SELECT kind, payload FROM paper WHERE id=?",
                       (paper_id,)).fetchone()
    if not row:
        raise ValueError("That paper no longer exists — reload the page")
    if kind and row["kind"] != kind:
        raise ValueError(f"Paper {paper_id} is a {row['kind']}, not a {kind}")
    return json.loads(row["payload"])


def latest_paper(conn, order_id, kind: str):
    """The newest live paper of one kind on an order (a void one is not it)."""
    return conn.execute(
        "SELECT * FROM paper WHERE order_id=? AND kind=? AND status<>'void'"
        " ORDER BY id DESC LIMIT 1", (order_id, kind)).fetchone()


def customer_short(name: str) -> str:
    """``SELCO Products Company`` -> ``SELCO`` — the COC's label prefix.

    An acronym first word is the short name on its own; otherwise the name
    minus its legal suffix.  Heuristic, and the field is editable.
    """
    words = [w for w in re.split(r"[^A-Za-z0-9&]+", _s(name)) if w]
    if not words:
        return ""
    if len(words[0]) >= 2 and words[0].isupper():
        return words[0]
    kept = [w for w in words if w.lower().strip(".") not in _NOISE] or words
    return " ".join(kept).upper()


# --------------------------------------------------------------------------
# Builders
# --------------------------------------------------------------------------

def build_quotation(conn, order_id: int, opts: dict) -> dict:
    """The export quotation, from its LEDGER row (SOP-DESIGN §5).

    ``opts['document_id']`` is required: the money lives in the ledger and a
    paper never invents a quotation, it dresses one.  The number is the ledger
    row's, verbatim — including its ' Rev-A' when the ledger has been revised.
    """
    from ..modules import quotations as ledger

    doc_id = (opts or {}).get("document_id")
    if not doc_id:
        raise ValueError("Pick the quotation this paper prints")
    doc = ledger.get_doc(conn, doc_id)
    if doc["kind"] != "quotation":
        raise ValueError("That ledger document is an invoice, not a quotation")
    o = order_row(conn, order_id)
    cust = customer_block(conn, doc["customer_id"])
    money = ledger.totals(sum(ln["amount"] for ln in doc["lines"]), doc["tax_pct"])

    items = []
    for n, ln in enumerate(doc["lines"], 1):
        items.append({
            "sno": n,
            "code": _s(ln.get("drawing_no")) or "-",
            "description": _s(ln.get("description")),
            "qty": _num(ln["qty"]),
            "unit": _s(ln.get("unit")) or "EA",
            "unit_price": round(float(ln["rate"]), 3),
            "total": round(float(ln["amount"]), 2),
        })

    return {
        "number": _s(doc["doc_no"]),
        "date_iso": _s(doc["doc_date"]),
        "order_no": _s(o["order_no"]),
        "customer": cust,
        "currency": currency_for(cust["country"]),
        "items": items,
        "client_code": _s(doc["customer_code"]),
        "rfq_ref": _s(doc.get("reference")) or "Email",
        "rfq_date_iso": "",
        "total": round(money["total"], 2),
        "validity_line": COMPANY["validity_line"],
        "price_basis": "",
        "lead_time": "",
        "payment_terms": _s(doc.get("payment_terms")) or COMPANY["payment_terms"],
        "guarantee": "",
        "taxes_duties": "",
        "note": COMPANY["jurisdiction_note"],
        "approved_by": COMPANY["approved_by"],
        "prepared_by": COMPANY["prepared_by"],
    }


def build_ack(conn, order_id: int, opts: dict) -> dict:
    """The PO acknowledgement: what we are confirming, and when it ships.

    ``ack_ref`` and ``wo_no_long`` stay blank — both come off counters and are
    consumed by ``service.create_paper`` inside the insert's transaction.  The
    Quotation Ref. is the linked quotation's number or the literal
    ``Repeat PO`` (CONVENTIONS §7).
    """
    opts = opts or {}
    o = order_row(conn, order_id)
    c = customer_row(conn, o["customer_id"])
    cust = customer_block(conn, o["customer_id"])
    contact = primary_contact(conn, o["customer_id"])

    billing = c["address_billing"]
    shipping = c["address_shipping"] or billing         # one address = both
    items, total = [], 0.0
    for n, it in enumerate(order_items(conn, order_id), 1):
        line_total = round(float(it["qty"]) * float(it["rate"]), 2)
        total += line_total
        items.append({
            "sno": n, "code": part_code(it), "description": item_description(it),
            "material": item_material(it), "qty": _num(it["qty"]),
            "unit": _s(it["unit"]) or "EA",
            "unit_price": round(float(it["rate"]), 2), "total": line_total,
        })

    currency = currency_for(cust["country"])
    return {
        "number": "",                       # = ack_ref, injected at creation
        "date_iso": today_iso(),
        "order_no": _s(o["order_no"]),
        "customer": cust,
        "currency": currency,
        "items": items,
        "bill_to_lines": _named_block(c["name"], billing),
        "ship_to_lines": _named_block(c["name"], shipping),
        "cust_po": _s(o["customer_po"]),
        "po_date_iso": _s(o["order_date"]),
        "quotation_ref": _quotation_ref(conn, order_id, opts),
        "client_code": _s(c["code"]),
        "ack_ref": "",                      # consumed at creation
        "ack_date_iso": today_iso(),
        "contacts": contact,
        "price_basis": "",
        "payment_terms": _s(c["payment_terms"]) or COMPANY["payment_terms"],
        "ship_date_iso": _s(o["due_date"]),
        "wo_no_long": "",                   # consumed at creation, with the ack
        "total": round(total, 2),
        "remittance_block": COMPANY["remittance_block"],
        "currency_header": currency["header"],
    }


def _quotation_ref(conn, order_id, opts) -> str:
    """'T04/AT/130826/316', or the literal 'Repeat PO' for a re-order."""
    if opts.get("repeat_po"):
        return "Repeat PO"
    if opts.get("quotation_paper_id"):
        row = conn.execute("SELECT paper_no, revision FROM paper WHERE id=?",
                           (opts["quotation_paper_id"],)).fetchone()
        if not row:
            raise ValueError("That quotation paper no longer exists — reload the page")
        return _s(row["paper_no"]) + (f" Rev-{row['revision']}" if row["revision"] else "")
    if opts.get("document_id"):
        row = conn.execute("SELECT doc_no FROM document WHERE id=?",
                           (opts["document_id"],)).fetchone()
        if not row:
            raise ValueError("That quotation no longer exists — reload the page")
        return _s(row["doc_no"])
    return ""


def build_work_order(conn, order_id: int, opts: dict) -> dict:
    """The internal work order — what the shop floor makes, in their words.

    ``wo_no_short`` is blank here: the number was reserved by the ack (a work
    order is born with its ack, CONVENTIONS §7) and ``create_paper`` either
    reuses that reservation or consumes a fresh one.
    """
    o = order_row(conn, order_id)
    c = customer_row(conn, o["customer_id"])
    cust = customer_block(conn, o["customer_id"])
    items = [{
        "sno": n, "part_no": part_code(it), "item": item_description(it),
        "qty": _num(it["qty"]), "material": item_material(it),
        "marking": "-", "remarks": "",
    } for n, it in enumerate(order_items(conn, order_id), 1)]
    return {
        "number": "",                       # = wo_no_short, injected at creation
        "date_iso": today_iso(),
        "order_no": _s(o["order_no"]),
        "customer": cust,
        "currency": currency_for(cust["country"]),
        "items": items,
        "wo_no_short": "",
        "client_code": _s(c["code"]),
        "cust_po": _s(o["customer_po"]),
    }


def build_invoice(conn, order_id: int, opts: dict) -> dict:
    """The export invoice — one invoice, many customer POs (CONVENTIONS §7).

    ``opts['order_ids']`` defaults to this order alone; every order on it must
    belong to the same customer, because one invoice bills one buyer.
    ``opts['item_ids']`` narrows it to hand-picked lines.
    """
    opts = opts or {}
    order_ids = [int(i) for i in (opts.get("order_ids") or [order_id])]
    if order_id not in order_ids:
        order_ids = [order_id] + order_ids
    rows = [order_row(conn, oid) for oid in order_ids]
    customers = {r["customer_id"] for r in rows}
    if len(customers) > 1:
        raise ValueError("One invoice bills one customer — those orders belong "
                         "to different customers")
    o = rows[0]
    c = customer_row(conn, o["customer_id"])
    cust = customer_block(conn, o["customer_id"])
    currency = currency_for(cust["country"])

    items, qty_total, amount_total = [], 0.0, 0.0
    for n, it in enumerate(merged_items(conn, order_ids, opts.get("item_ids")), 1):
        rate = float(it["rate"] or 0)
        free = rate <= 0                     # replacements print '-' (§5)
        amount = round(float(it["qty"]) * rate, 2)
        qty_total += float(it["qty"])
        if not free:
            amount_total += amount
        items.append({
            "sno": n,
            "code_desc": _code_desc(n, it),
            "po": it["customer_po"],
            "qty": _num(it["qty"]),
            "net_wt": "",                    # weighed at packing time
            "rate": "-" if free else _money(rate, 3),
            "amount": "-" if free else _money(amount),
        })

    # One entry per ORDER, de-duplicated: six POs on one invoice is normal.
    buyer_po_block, seen = [], set()
    for r in rows:
        po = _s(r["customer_po"])
        if not po or po in seen:
            continue
        seen.add(po)
        buyer_po_block.append({"po": po, "date_iso": _s(r["order_date"])})

    return {
        "number": "",                        # = invoice_no, injected at creation
        "date_iso": today_iso(),
        "order_no": _s(o["order_no"]),
        "customer": cust,
        "currency": currency,
        "items": items,
        "invoice_no": "",
        "buyer_po_block": buyer_po_block,
        "iec": COMPANY["iec"],
        "ad_code": COMPANY["ad_code"],
        "consignee_lines": _named_block(c["name"],
                                        c["address_shipping"] or c["address_billing"],
                                        upper=True),
        "buyer_lines": _named_block(c["name"], c["address_billing"], upper=True),
        "pre_carriage": COMPANY["pre_carriage"],
        "place_receipt": COMPANY["place_receipt"],
        "vessel": COMPANY["vessel"],
        "port_loading": COMPANY["port_loading"],
        "port_discharge": cust["country"],
        "origin_country": COMPANY["origin_country"],
        "final_destination": cust["country"],
        "terms": COMPANY["terms"],
        "marks_lines": list(COMPANY["marks_lines"]),
        "hts_line": COMPANY["hts_line"],
        "gsp_line": COMPANY["gsp_line"],
        "totals": {"qty": _num(qty_total), "net_wt": "",
                   "amount": _money(amount_total)},
        "total_weight_line": weight_line(),
        "amount_words": amount_in_words(amount_total, currency["code"]),
    }


def _code_desc(sno: int, item: dict) -> str:
    """``1.TWB02000750,Thermowell 1/4"NPT, Matl. Brass`` — the invoice and the
    packing list both print one composed cell per line."""
    code = _s(item.get("drawing_no"))
    desc = item_description(item)
    material = item_material(item)
    head = f"{sno}.{code},{desc}" if code else f"{sno}.{desc}"
    return head + (f", Matl. {material}" if material else "")


def build_packing_list(conn, order_id: int, opts: dict) -> dict:
    """The packing list: its invoice's header, plus boxes.

    Boxes live only in the payload (SOP-DESIGN §5) and there is no honest
    default beyond "it all went in one box" — sizes and weights are typed in
    when the shipment is actually packed.
    """
    opts = opts or {}
    inv_id = opts.get("invoice_paper_id")
    if not inv_id:
        raise ValueError("Pick the invoice this packing list belongs to")
    inv = _paper_payload(conn, inv_id, "invoice")

    box_items = [{"sno": n, "code_desc": it.get("code_desc", ""),
                  "qty": it.get("qty", "")}
                 for n, it in enumerate(inv.get("items") or [], 1)]
    qty_total = sum(float(i["qty"] or 0) for i in box_items)

    payload = {k: inv.get(k, "") for k in _EXPORT_HEADER}
    payload["boxes"] = [{"box_label": "Box No. 1", "size": "", "net_wt": "",
                         "gross_wt": "", "items": box_items}]
    payload["totals"] = {"qty": _num(qty_total), "net_wt": "", "gross_wt": ""}
    payload["total_weight_line"] = weight_line()
    payload["number"] = _s(inv.get("invoice_no"))
    return payload


def build_coc(conn, order_id: int, opts: dict) -> dict:
    """Certificate of Conformance — one shipment, its part and its material.

    CONVENTIONS §9-H: the reference certifies a single part, so the part and
    material fields default from the first line and stay editable (duplicate
    the paper per part when a shipment needs one each).
    """
    opts = opts or {}
    inv_id = opts.get("invoice_paper_id")
    if not inv_id:
        raise ValueError("Pick the invoice this certificate covers")
    inv = _paper_payload(conn, inv_id, "invoice")
    o = order_row(conn, order_id)
    cust = customer_block(conn, o["customer_id"])

    items = order_items(conn, order_id)
    first = items[0] if items else {}
    material = item_material(first) if first else ""
    qty = sum(float(i.get("qty") or 0) for i in (inv.get("items") or []))
    po = _s(o["customer_po"])
    inv_no = _s(inv.get("invoice_no"))

    return {
        "number": coc_no(po, inv_no),
        "date_iso": _s(inv.get("date_iso")) or today_iso(),
        "order_no": _s(o["order_no"]),
        "customer": cust,
        "currency": currency_for(cust["country"]),
        "items": [],
        "customer_caps": cust["name"].upper(),
        "customer_short": customer_short(cust["name"]),
        "po": po,
        "invoice_no": inv_no,
        "invoice_date_iso": _s(inv.get("date_iso")),
        "part_desc": (_s(first.get("drawing_no")) or item_description(first)) if first else "",
        "material": material,
        "plating": COMPANY["plating"],
        "finishing": COMPANY["finishing"],
        "qty_shipped": _num(qty),
        "authenticator": COMPANY["authenticator"],
        "date_shipped_iso": _s(inv.get("date_iso")),
    }


def coc_no(po: str, invoice_no: str) -> str:
    """``COC-PO-02940-EI-122`` — the COC has no counter: it is named after the
    PO and the invoice it certifies (CONVENTIONS §3), and that pair is what
    makes it unique in the paper table.

    The number is the FILE's name minus its extension, taken from the registry
    rather than re-spelled here — the office identifies a COC by the document
    they hand over, so a number that read differently from the filename would
    be a second, wrong identity for the same piece of paper.
    """
    return registry.stem("coc", {"po": _s(po), "invoice_no": _s(invoice_no)})


def build_test_cert(conn, order_id: int, opts: dict) -> dict:
    """Test certificate: one line per (item, heat), chemistry off the heat.

    A certificate covers several heats and materials (CONVENTIONS §7), so the
    items of the invoice are crossed with the heats actually issued against
    their order.  Elements beyond the eight printed columns fill the five
    spare ones, in the order ``extra_elements`` lists them.
    """
    opts = opts or {}
    inv_id = opts.get("invoice_paper_id")
    if not inv_id:
        raise ValueError("Pick the invoice this certificate covers")
    inv = _paper_payload(conn, inv_id, "invoice")
    inv_opts = inv.get("_opts") if isinstance(inv.get("_opts"), dict) else {}

    o = order_row(conn, order_id)
    cust = customer_block(conn, o["customer_id"])
    order_ids = [int(i) for i in (inv_opts.get("order_ids") or [order_id])]
    items = merged_items(conn, order_ids, inv_opts.get("item_ids"))

    heats_by_order: dict[str, list[dict]] = {}
    lines, extras = [], []
    for it in items:
        order_no = it["order_no"]
        if order_no not in heats_by_order:
            heats_by_order[order_no] = issued_heats(conn, order_no)
        for heat in heats_by_order[order_no] or [None]:
            chem = dict(heat["chem"]) if heat else {}
            for element in chem:
                if element not in CHEM_COLUMNS and element not in extras:
                    extras.append(element)
            lines.append({
                "sno": len(lines) + 1,
                "item": part_code(it) if it.get("drawing_no") else item_description(it),
                "size": _s(heat["size_section"]) if heat else "",
                "qty": _num(it["qty"]),
                "component": "-",
                "heat_no": _s(heat["heat_number"]) if heat else "",
                "material": ((_s(heat["grade"]) or _s(heat["material_class"]))
                             if heat else item_material(it)),
                "chem": chem,
            })
    extras = extras[:SPARE_COLUMNS]
    for line in lines:                       # the spare columns, in head order
        for n in range(1, SPARE_COLUMNS + 1):
            element = extras[n - 1] if n <= len(extras) else None
            line[f"spare_{n}"] = line["chem"].get(element, "-") if element else "-"

    inv_no = _s(inv.get("invoice_no"))
    po = _s(o["customer_po"])
    cert_no = numbering.tc_no(po, inv_no) if (po and inv_no) else ""
    inv_date = _s(inv.get("date_iso"))
    return {
        "number": cert_no,
        "date_iso": inv_date or today_iso(),
        "order_no": _s(o["order_no"]),
        "customer": cust,
        "currency": currency_for(cust["country"]),
        "items": lines,
        "cert_no": cert_no,
        "cert_date_iso": inv_date or today_iso(),
        "customer_line": ", ".join(p for p in (cust["name"], cust["country"]) if p),
        "po": po,
        "po_date_iso": _s(o["order_date"]),
        "invoice_no": inv_no,
        "invoice_date_iso": inv_date,
        "extra_elements": extras,
    }


def build_bom(conn, order_id: int, opts: dict) -> dict:
    """Bill of materials: ``orders.order_bom`` rolled out one line per material.

    Outsourced lines are the reason the rollup is not enough on its own — a
    bought-out part has an OS ID and a vendor instead of a heat number
    (SOP-DESIGN §9), and both come off ``costing_material.os_item_id``.
    """
    from ..modules import orders as orders_mod

    o = order_row(conn, order_id)
    cust = customer_block(conn, o["customer_id"])
    rollup = orders_mod.order_bom(conn, order_id)

    parts_by_item = {it["id"]: it for it in order_items(conn, order_id)}
    # material key -> the parts it serves and its per-piece quantity
    detail: dict[tuple, dict] = {}
    costing_ids = []
    for entry in rollup["items"]:
        if entry.get("costing_id"):
            costing_ids.append(entry["costing_id"])
        item = parts_by_item.get(entry["item_id"], {})
        for m in entry["materials"]:
            key = (m["heat_number"] or m["material_label"], m["unit"])
            d = detail.setdefault(key, {"qty_per": m["qty_per_piece"], "parts": []})
            code = part_code(item)
            if code not in d["parts"]:
                d["parts"].append(code)

    outsourced = _outsourced_materials(conn, costing_ids)
    heats = _heat_sizes(conn)

    items = []
    for n, agg in enumerate(rollup["summary"], 1):
        key = (agg["heat_number"] or agg["material_label"], agg["unit"])
        d = detail.get(key, {"qty_per": "", "parts": []})
        os_row = outsourced.get(key)
        heat = heats.get(_s(agg["heat_number"]))
        items.append({
            "sno": n,
            "part_no": ", ".join(d["parts"]) or "-",
            "description": _s(agg["material_label"]),
            "size": _s(os_row["size_section"]) if os_row else (
                _s(heat["size_section"]) if heat else ""),
            "material": (_s(os_row["material"]) if os_row else
                         (_s(heat["grade"]) or _s(heat["material_class"]) if heat
                          else _s(agg["material_label"]))),
            "heat_or_os": (_s(os_row["os_id"]) if os_row
                           else _s(agg["heat_number"])),
            "source": (f"Outsourced - {_s(os_row['vendor_code']) or '?'}"
                       if os_row else "In-House"),
            "qty_per": _num(d["qty_per"]),
            "total_qty": _num(agg["required"]),
            "unit": _s(agg["unit"]),
            "remarks": "",
        })

    order_items_list = order_items(conn, order_id)
    return {
        "number": "",                        # = bom_no, injected at creation
        "date_iso": today_iso(),
        "order_no": _s(o["order_no"]),
        "customer": cust,
        "currency": currency_for(cust["country"]),
        "items": items,
        "bom_no": "",
        "customer_line": ", ".join(p for p in (cust["name"], cust["country"]) if p),
        "po": _s(o["customer_po"]),
        "po_date_iso": _s(o["order_date"]),
        "wo_no": _work_order_no(conn, order_id),
        "part_assy": item_description(order_items_list[0]) if order_items_list else "",
    }


def _outsourced_materials(conn, costing_ids: list[int]) -> dict:
    """``costing_material`` lines that point at bought-out stock, keyed the
    same way ``order_bom`` rolls its summary up."""
    if not costing_ids:
        return {}
    marks = ",".join("?" * len(costing_ids))
    out = {}
    for r in conn.execute(
            f"""SELECT cm.heat_number, cm.material_label, cm.unit,
                       oi.os_id, oi.material, oi.size_section, v.code AS vendor_code
                FROM costing_material cm
                JOIN os_item oi ON oi.id=cm.os_item_id
                LEFT JOIN vendor v ON v.id=oi.vendor_id
                WHERE cm.costing_id IN ({marks}) AND cm.os_item_id IS NOT NULL""",
            costing_ids):
        out[(r["heat_number"] or r["material_label"], r["unit"])] = dict(r)
    return out


def _heat_sizes(conn) -> dict:
    return {r["heat_number"]: dict(r) for r in conn.execute(
        "SELECT heat_number, grade, material_class, size_section FROM heat")}


def _work_order_no(conn, order_id) -> str:
    """The shop-floor number this BOM belongs to, if one has been raised."""
    wo = latest_paper(conn, order_id, "work_order")
    if wo:
        return _s(wo["paper_no"])
    ack = latest_paper(conn, order_id, "ack")
    if ack:
        return _s(json.loads(ack["payload"]).get("_wo_no_short"))
    return ""


BUILDERS = {
    "quotation": build_quotation,
    "ack": build_ack,
    "work_order": build_work_order,
    "invoice": build_invoice,
    "packing_list": build_packing_list,
    "coc": build_coc,
    "test_cert": build_test_cert,
    "bom": build_bom,
}


def build(conn, kind: str, order_id: int, opts: dict | None = None) -> dict:
    """Build one payload and prove it against the schema before it is stored."""
    try:
        builder = BUILDERS[kind]
    except KeyError:
        raise ValueError(f"Unknown paper kind {kind!r}") from None
    payload = builder(conn, order_id, opts or {})
    check_schema(kind, payload)
    return payload
