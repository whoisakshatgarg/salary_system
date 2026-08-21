"""The paper lifecycle — create, edit, refill, finalise, revise, render.

A **paper** is one generated document instance hanging off an order
(SOP-DESIGN §2).  This module is the whole of its life:

    create   auto-fill once from the order/ledger/inventory, CONSUMING the
             document number inside the same BEGIN IMMEDIATE that inserts the
             row — a save that rolls back never burns a number
    edit     drafts are freely editable; the payload is the truth from then on
    refill   an EXPLICIT re-sync from source, replaying the original opts, so
             a hand edit is never silently clobbered
    final    freezes the payload (still viewable, still downloadable)
    revise   copies a final paper to a new draft ' Rev-A', supersedes the
             parent, and — for quotations — revises the ledger row with it, so
             paper and ledger keep the same number on both sides
    render   payload + template -> bytes, on demand, nothing stale on disk

Everything is a module function taking a connection, like the rest of the
backend; the FastAPI layer is ``router.py`` and holds no logic.

Meta keys
---------
The ``paper`` table has no column for the app's own bookkeeping, so it lives
in the payload under keys prefixed ``_`` (``payloads.META_PREFIX``), which are
excluded from the schema contract and never shown as fields:

``_opts``           the arguments the paper was built from — what Refill replays
``_wo_no_short``    (ack only) the shop-floor form of the work-order number the
                    ack reserved, so the work order can reuse it rather than
                    burn a second serial
"""

from __future__ import annotations

import json
import sqlite3
from datetime import date, datetime

from ..core import numbering
from ..core.db import row_to_dict
from . import engine, payloads, registry

STATUSES = ("draft", "final", "sent", "superseded", "void")

# Who may become what (SOP-DESIGN §2).  'superseded' is set by revise() alone,
# and nothing comes back from void: a voided number stays spent.
TRANSITIONS = {
    "draft": {"final", "void"},
    "final": {"sent", "void"},
    "sent": {"void"},
    "superseded": set(),
    "void": set(),
}

# The payload fields that carry the paper's OWN number.  They are owned by the
# paper (the counter was spent on them), so a Refill copies them back over the
# freshly built payload instead of letting a builder blank or re-derive them.
NUMBER_FIELDS = {
    "quotation": ("number",),
    "ack": ("number", "ack_ref", "wo_no_long"),
    "work_order": ("number", "wo_no_short"),
    "invoice": ("number", "invoice_no"),
    "packing_list": ("number", "invoice_no"),
    "coc": ("number",),
    "test_cert": ("number", "cert_no"),
    "bom": ("number", "bom_no"),
}

# Which field the printed number lands in when a revision suffix is added.
PRINTED_NUMBER = {
    "quotation": "number", "ack": "ack_ref", "work_order": "wo_no_short",
    "invoice": "invoice_no", "packing_list": "invoice_no", "coc": "number",
    "test_cert": "cert_no", "bom": "bom_no",
}

MIME = {
    "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
}

# What each kind needs before it can be built — drives /api/papers/refs and
# the create form, and is checked here so a bad call fails with a sentence.
KIND_LABELS = {
    "quotation": "Quotation",
    "ack": "PO acknowledgement",
    "work_order": "Work order",
    "invoice": "Export invoice",
    "packing_list": "Packing list",
    "coc": "Certificate of conformance",
    "test_cert": "Test certificate",
    "bom": "Bill of materials",
}

KIND_OPTS = {
    "quotation": {"required": ["document_id"], "optional": []},
    "ack": {"required": [],
            "optional": ["quotation_paper_id", "document_id", "repeat_po"]},
    "work_order": {"required": [], "optional": []},
    "invoice": {"required": [], "optional": ["order_ids", "item_ids", "document_id"]},
    "packing_list": {"required": ["invoice_paper_id"], "optional": []},
    "coc": {"required": ["invoice_paper_id"], "optional": []},
    "test_cert": {"required": ["invoice_paper_id"], "optional": []},
    "bom": {"required": [], "optional": []},
}


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _s(v) -> str:
    return "" if v is None else str(v).strip()


def _meta(payload: dict) -> dict:
    return {k: v for k, v in (payload or {}).items()
            if str(k).startswith(payloads.META_PREFIX)}


# --------------------------------------------------------------------------
# Create
# --------------------------------------------------------------------------

def create_paper(conn, kind: str, order_id: int, opts: dict | None = None) -> dict:
    """Build a paper's payload, take its number, and insert the row — once.

    The counter bump and the INSERT share one ``BEGIN IMMEDIATE`` (SOP-DESIGN
    §2): two clicks on Create cannot hand out the same acknowledgement
    reference, and a failure anywhere gives the number back.
    """
    if kind not in payloads.BUILDERS:
        raise ValueError(f"Unknown paper kind {kind!r}")
    opts = dict(opts or {})
    order = payloads.order_row(conn, order_id)          # 404s before we lock

    conn.execute("BEGIN IMMEDIATE")
    try:
        payload = payloads.build(conn, kind, order_id, opts)
        paper_no, revision, document_id = _take_number(
            conn, kind, order_id, order, opts, payload)
        payload["_opts"] = opts
        payloads.check_schema(kind, payload)
        today = date.today().isoformat()
        try:
            cur = conn.execute(
                """INSERT INTO paper (kind, paper_no, revision, order_id, customer_id,
                     document_id, based_on_id, status, paper_date, payload,
                     created_at, updated_at)
                   VALUES (?,?,?,?,?,?,?,'draft',?,?,?,?)""",
                (kind, paper_no, revision, order_id, order["customer_id"],
                 document_id, opts.get("based_on_id"), today,
                 json.dumps(payload), _now(), _now()))
        except sqlite3.IntegrityError:
            raise ValueError(
                f"{KIND_LABELS.get(kind, kind)} {paper_no} already exists on this "
                "order — open that one, or revise it")
        paper_id = cur.lastrowid
        conn.commit()
    except BaseException:
        conn.rollback()
        raise
    return get_paper(conn, paper_id)


def _take_number(conn, kind, order_id, order, opts, payload):
    """Consume this kind's number and write it into the payload.

    Returns ``(paper_no, revision, document_id)``.  Runs INSIDE the caller's
    transaction — that is the whole point (SOP-DESIGN §2/§3).
    """
    from ..modules import quotations as ledger

    today = date.today()

    if kind == "quotation":
        # Papers never re-number a ledger document: the quotation's number IS
        # its ledger row's, revision marker and all.
        doc_id = opts.get("document_id")
        doc = conn.execute("SELECT id, doc_no, kind FROM document WHERE id=?",
                           (doc_id,)).fetchone()
        if not doc:
            raise ValueError("Pick the quotation this paper prints")
        base, letter = _split_revision(doc["doc_no"])
        payload["number"] = _s(doc["doc_no"])
        return base, letter, doc["id"]

    if kind == "ack":
        # The acknowledgement and its work order are numbered together: the WO
        # number embeds the ack's date, so it is born here (CONVENTIONS §7).
        client = ledger.client_code(conn, order["customer_id"])
        ack_ref = numbering.ack_ref(conn, client, today)
        wo = numbering.work_order_no(conn, client, today)
        payload["ack_ref"] = payload["number"] = ack_ref
        payload["wo_no_long"] = wo["long"]
        payload["_wo_no_short"] = wo["short"]
        return ack_ref, "", None

    if kind == "work_order":
        short = _reserved_wo(conn, order_id)
        if not short:
            client = ledger.client_code(conn, order["customer_id"])
            short = numbering.work_order_no(conn, client, today)["short"]
        payload["wo_no_short"] = payload["number"] = short
        return short, "", None

    if kind == "invoice":
        doc_id = opts.get("document_id")
        if doc_id:
            doc = conn.execute("SELECT id, doc_no, kind FROM document WHERE id=?",
                               (doc_id,)).fetchone()
            if not doc:
                raise ValueError("That invoice no longer exists — reload the page")
            if doc["kind"] != "invoice":
                raise ValueError("That ledger document is a quotation, not an invoice")
            invoice_no = _s(doc["doc_no"])
            document_id = doc["id"]
        else:
            invoice_no = numbering.invoice_no(conn, today)
            document_id = _create_ledger_invoice(conn, order, opts, invoice_no, today)
        payload["invoice_no"] = payload["number"] = invoice_no
        return invoice_no, "", document_id

    if kind == "packing_list":
        number = _s(payload.get("invoice_no"))
        if not number:
            raise ValueError("That invoice has no number yet")
        return number, "", None

    if kind == "coc":
        # No counter: a COC is identified by the file it produces, so the
        # number is the registry's own filename stem and the two are one
        # string (CONVENTIONS §3).  The PO and the invoice are what that name
        # is made of, so a missing one is a missing identity, not a blank.
        if not _s(payload.get("po")) or not _s(payload.get("invoice_no")):
            raise ValueError(
                "A certificate needs the customer's PO number and the invoice's "
                "— fill those in on the order and the invoice first")
        number = registry.stem("coc", payload)
        payload["number"] = number
        return number, "", None

    if kind == "test_cert":
        number = _s(payload.get("number"))
        if not number:
            raise ValueError(
                "A certificate needs the customer's PO number and the invoice's "
                "— fill those in on the order and the invoice first")
        return number, "", None

    if kind == "bom":
        bom_no = numbering.bom_no(conn, today)
        payload["bom_no"] = payload["number"] = bom_no
        return bom_no, "", None

    raise ValueError(f"Unknown paper kind {kind!r}")      # pragma: no cover


def _reserved_wo(conn, order_id) -> str:
    """The work-order number this order's acknowledgement already reserved.

    The ack consumed the pair, so raising the work order must NOT take a
    second serial — the shop floor and the customer have to be looking at the
    same job number.
    """
    ack = payloads.latest_paper(conn, order_id, "ack")
    if not ack:
        return ""
    stored = json.loads(ack["payload"])
    short = _s(stored.get("_wo_no_short"))
    if short:
        return short
    # An ack from before the meta key existed still prints the long form, and
    # the short one is its last two segments (CONVENTIONS §3).
    return short_from_long(stored.get("wo_no_long"))


def short_from_long(long_no) -> str:
    """``E01.04.08.26.252.26`` -> ``252/26`` (CONVENTIONS §3)."""
    bits = _s(long_no).split(".")
    return f"{bits[-2]}/{bits[-1]}" if len(bits) >= 2 else ""


def _create_ledger_invoice(conn, order, opts, invoice_no, today) -> int:
    """The invoice paper's twin in the money ledger, same number.

    ``quotations.create_doc`` cannot be used: it opens its own transaction and
    consumes a SECOND invoice number, and then the paper and the ledger would
    disagree about what the customer was billed.  So the row is written here
    with the number this transaction already took, and its lines through the
    ledger's own line writer.
    """
    from ..modules import quotations as ledger

    order_ids = [int(i) for i in (opts.get("order_ids") or [order["id"]])]
    if order["id"] not in order_ids:
        order_ids = [order["id"]] + order_ids
    items = payloads.merged_items(conn, order_ids, opts.get("item_ids"))
    try:
        cur = conn.execute(
            """INSERT INTO document (kind, doc_no, customer_id, order_id, doc_date,
                 valid_until, reference, tax_pct, notes, terms, status, created_at)
               VALUES ('invoice',?,?,?,?,NULL,?,0,'',?,'draft',?)""",
            (invoice_no, order["customer_id"], order["id"], today.isoformat(),
             _s(order["customer_po"]) or _s(order["order_no"]),
             ledger.DEFAULT_TERMS, _now()))
    except sqlite3.IntegrityError:
        raise ValueError(f"{invoice_no} already exists — correct the counter in "
                         "Settings → Numbering")
    doc_id = cur.lastrowid
    for it in items:
        ledger._insert_line(conn, doc_id, {
            "drawing_id": it.get("drawing_id"), "os_item_id": None,
            "description": payloads.item_description(it),
            "qty": float(it["qty"]), "unit": _s(it["unit"]) or "Nos",
            "rate": float(it["rate"] or 0)})
    return doc_id


_REV_LETTERS = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"


def _split_revision(doc_no) -> tuple[str, str]:
    """``T04/AT/130826/316 Rev-A`` -> ``('T04/AT/130826/316', 'A')``."""
    from ..modules.quotations import _REV_SUFFIX
    text = _s(doc_no)
    m = _REV_SUFFIX.search(text)
    if not m:
        return text, ""
    return text[:m.start()], m.group(0).strip()[4:]


def next_revision(current) -> str:
    """``'' -> 'A' -> 'B'`` … ``'Z' -> 'AA'`` (CONVENTIONS §9-B)."""
    cur = _s(current).upper()
    if not cur:
        return "A"
    n = 0
    for ch in cur:
        n = n * 26 + (_REV_LETTERS.index(ch) + 1 if ch in _REV_LETTERS else 0)
    out = ""
    n += 1
    while n:
        n, r = divmod(n - 1, 26)
        out = _REV_LETTERS[r] + out
    return out


# --------------------------------------------------------------------------
# Read
# --------------------------------------------------------------------------

def get_paper(conn, paper_id: int) -> dict:
    row = row_to_dict(conn.execute(
        """SELECT p.*, o.order_no, c.name AS customer_name, d.doc_no AS document_no
           FROM paper p
           JOIN customer_order o ON o.id=p.order_id
           LEFT JOIN customer c ON c.id=p.customer_id
           LEFT JOIN document d ON d.id=p.document_id
           WHERE p.id=?""", (paper_id,)).fetchone())
    if not row:
        raise ValueError("Paper not found")
    payload = json.loads(row["payload"])
    row["payload"] = {k: v for k, v in payload.items()
                      if not str(k).startswith(payloads.META_PREFIX)}
    row["opts"] = payload.get("_opts") or {}
    row["label"] = KIND_LABELS.get(row["kind"], row["kind"])
    row["display_no"] = display_no(row["paper_no"], row["revision"])
    row["filename"] = download_name(row["kind"], payload, row["paper_no"],
                                    row["revision"])
    row["revisions"] = revision_chain(conn, paper_id)
    return row


def display_no(paper_no, revision) -> str:
    return f"{_s(paper_no)} Rev-{revision}" if _s(revision) else _s(paper_no)


def list_papers(conn, kind: str = "", order_id=None, q: str = "",
                status: str = "") -> dict:
    """The papers list.  ``kind`` accepts a comma list so one tile can open
    several kinds at once (SOP-DESIGN §7: ``?kind=work_order,bom``)."""
    sql = """SELECT p.id, p.kind, p.paper_no, p.revision, p.status, p.paper_date,
                    p.updated_at, p.order_id, p.document_id,
                    o.order_no, c.name AS customer_name
             FROM paper p
             JOIN customer_order o ON o.id=p.order_id
             LEFT JOIN customer c ON c.id=p.customer_id
             WHERE 1=1"""
    args: list = []
    kinds = [k.strip() for k in _s(kind).split(",") if k.strip()]
    if kinds:
        sql += f" AND p.kind IN ({','.join('?' * len(kinds))})"
        args += kinds
    if order_id:
        sql += " AND p.order_id=?"
        args.append(int(order_id))
    if _s(status):
        sql += " AND p.status=?"
        args.append(_s(status))
    if _s(q):
        like = f"%{_s(q)}%"
        sql += " AND (p.paper_no LIKE ? OR o.order_no LIKE ? OR c.name LIKE ?)"
        args += [like] * 3
    sql += " ORDER BY p.id DESC"
    rows = []
    for r in conn.execute(sql, args):
        row = dict(r)
        row["label"] = KIND_LABELS.get(row["kind"], row["kind"])
        row["display_no"] = display_no(row["paper_no"], row["revision"])
        rows.append(row)
    counts = {k: 0 for k in payloads.KINDS}
    for r in conn.execute("SELECT kind, COUNT(*) AS n FROM paper GROUP BY kind"):
        counts[r["kind"]] = r["n"]
    return {"rows": rows, "counts": counts, "kinds": kind_refs()}


def kind_refs() -> list[dict]:
    """Kind labels and the options each one needs — the create form reads this
    rather than hard-coding a second copy of the rules."""
    return [{"kind": k, "label": KIND_LABELS[k],
             "requires": KIND_OPTS[k]["required"],
             "accepts": KIND_OPTS[k]["optional"],
             "format": registry.entry(k)["format"]}
            for k in payloads.KINDS]


def revision_chain(conn, paper_id: int) -> list[dict]:
    """Every revision of one paper, oldest first; the tip is not superseded."""
    root, seen = paper_id, {paper_id}
    while True:
        r = conn.execute("SELECT based_on_id FROM paper WHERE id=?", (root,)).fetchone()
        parent = r["based_on_id"] if r else None
        if not parent or parent in seen:
            break
        seen.add(parent)
        root = parent
    out, cur, walked = [], root, set()
    while cur and cur not in walked:
        walked.add(cur)
        r = conn.execute(
            "SELECT id, paper_no, revision, status, paper_date FROM paper WHERE id=?",
            (cur,)).fetchone()
        if not r:
            break
        out.append({"id": r["id"], "display_no": display_no(r["paper_no"], r["revision"]),
                    "revision": r["revision"], "status": r["status"],
                    "paper_date": r["paper_date"],
                    "active": r["status"] != "superseded"})
        nxt = conn.execute("SELECT id FROM paper WHERE based_on_id=? ORDER BY id LIMIT 1",
                           (r["id"],)).fetchone()
        cur = nxt["id"] if nxt else None
    return out if len(out) > 1 else []


def _row(conn, paper_id: int):
    row = conn.execute("SELECT * FROM paper WHERE id=?", (paper_id,)).fetchone()
    if not row:
        raise ValueError("Paper not found")
    return row


# --------------------------------------------------------------------------
# Edit
# --------------------------------------------------------------------------

def update_payload(conn, paper_id: int, payload: dict) -> dict:
    """Save a hand-edited draft.  Final papers are frozen (SOP-DESIGN §2)."""
    row = _row(conn, paper_id)
    if row["status"] != "draft":
        raise ValueError("final papers are frozen — revise or void")
    incoming = {k: v for k, v in (payload or {}).items()
                if not str(k).startswith(payloads.META_PREFIX)}
    payloads.check_schema(row["kind"], incoming)
    # the app's own keys survive an edit: the UI never sends them back
    incoming.update(_meta(json.loads(row["payload"])))
    conn.execute("UPDATE paper SET payload=?, updated_at=? WHERE id=?",
                 (json.dumps(incoming), _now(), paper_id))
    conn.commit()
    return get_paper(conn, paper_id)


def refill(conn, paper_id: int) -> dict:
    """Re-run the builder with the paper's ORIGINAL options.

    Explicit on purpose: auto-fill happens once, at creation, and a refill is
    the office saying "the order moved on, take it again".  The number is not
    re-taken — it already belongs to this paper.
    """
    row = _row(conn, paper_id)
    if row["status"] != "draft":
        raise ValueError("final papers are frozen — revise or void")
    stored = json.loads(row["payload"])
    meta = _meta(stored)
    opts = stored.get("_opts") or {}

    conn.execute("BEGIN IMMEDIATE")
    try:
        fresh = payloads.build(conn, row["kind"], row["order_id"], opts)
        for field in NUMBER_FIELDS.get(row["kind"], ("number",)):
            if _s(stored.get(field)):
                fresh[field] = stored[field]
        fresh.update(meta)
        payloads.check_schema(row["kind"], fresh)
        conn.execute("UPDATE paper SET payload=?, updated_at=? WHERE id=?",
                     (json.dumps(fresh), _now(), paper_id))
        conn.commit()
    except BaseException:
        conn.rollback()
        raise
    return get_paper(conn, paper_id)


def set_status(conn, paper_id: int, status: str) -> dict:
    """draft → final → sent, and void from any of them.  Nothing else."""
    if status not in STATUSES:
        raise ValueError(f"Unknown status {status!r}")
    row = _row(conn, paper_id)
    current = row["status"]
    if status == current:
        return get_paper(conn, paper_id)
    if status not in TRANSITIONS.get(current, set()):
        raise ValueError(f"A {current} paper can't become {status}")
    conn.execute("UPDATE paper SET status=?, updated_at=? WHERE id=?",
                 (status, _now(), paper_id))
    conn.commit()
    return get_paper(conn, paper_id)


def _check_revisable(conn, row) -> None:
    if row["status"] not in ("final", "sent"):
        raise ValueError("Only a final or sent paper is revised — a draft is "
                         "simply edited")
    if conn.execute("SELECT id FROM paper WHERE based_on_id=?",
                    (row["id"],)).fetchone():
        raise ValueError("This one has already been revised — revise the latest "
                         "revision")


def revise(conn, paper_id: int) -> dict:
    """A fresh draft ' Rev-A' carrying this paper's payload; parent superseded.

    Only an issued paper is worth revising, and only the tip of a chain: a
    superseded paper is history, and branching it would leave two papers
    claiming to be the latest.  A quotation revises its LEDGER row in the same
    breath, so the money and the paperwork keep the same number.
    """
    from ..modules import quotations as ledger

    row = _row(conn, paper_id)
    _check_revisable(conn, row)
    revision = next_revision(row["revision"])
    payload = json.loads(row["payload"])
    document_id = row["document_id"]
    if row["kind"] == "quotation" and document_id:
        # The ledger revision is the LEDGER's act and owns its own
        # transaction, so it commits just before ours; the tip check is
        # re-run under our lock below and UNIQUE(kind, paper_no, revision)
        # is the backstop against a double click.
        document_id = ledger.revise_doc(conn, document_id)
        new_doc = conn.execute("SELECT doc_no FROM document WHERE id=?",
                               (document_id,)).fetchone()
        payload["number"] = _s(new_doc["doc_no"])
    else:
        field = PRINTED_NUMBER.get(row["kind"], "number")
        payload[field] = display_no(row["paper_no"], revision)
        payload["number"] = display_no(row["paper_no"], revision)

    conn.execute("BEGIN IMMEDIATE")
    try:
        row = _row(conn, paper_id)
        _check_revisable(conn, row)
        try:
            cur = conn.execute(
                """INSERT INTO paper (kind, paper_no, revision, order_id, customer_id,
                     document_id, based_on_id, status, paper_date, payload,
                     created_at, updated_at)
                   VALUES (?,?,?,?,?,?,?,'draft',?,?,?,?)""",
                (row["kind"], row["paper_no"], revision, row["order_id"],
                 row["customer_id"], document_id, paper_id,
                 date.today().isoformat(), json.dumps(payload), _now(), _now()))
        except sqlite3.IntegrityError:
            raise ValueError(f"Revision {revision} of {row['paper_no']} already "
                             "exists")
        new_id = cur.lastrowid
        conn.execute("UPDATE paper SET status='superseded', updated_at=? WHERE id=?",
                     (_now(), paper_id))
        conn.commit()
    except BaseException:
        conn.rollback()
        raise
    return get_paper(conn, new_id)


def delete_paper(conn, paper_id: int) -> None:
    """Drafts only: an issued number stays on the record, voided if need be."""
    row = _row(conn, paper_id)
    if row["status"] != "draft":
        raise ValueError("Only a draft can be deleted — void the paper instead")
    conn.execute("DELETE FROM paper WHERE id=?", (paper_id,))
    conn.commit()


# --------------------------------------------------------------------------
# Render
# --------------------------------------------------------------------------

def _for_render(kind, payload, paper_no, revision) -> dict:
    """The payload as the template should see it: the paper's own number, plus
    its revision marker when it has one."""
    out = dict(payload)
    number = display_no(paper_no, revision)
    field = PRINTED_NUMBER.get(kind, "number")
    if _s(revision) or not _s(out.get(field)):
        out[field] = number
    if _s(revision) or not _s(out.get("number")):
        out["number"] = number
    return out


def download_name(kind, payload, paper_no, revision) -> str:
    """The company's own file name for this paper, revision marker and all.

    ``registry.filename`` reads the serial off the number, so it is handed the
    UNSUFFIXED number — ``AT/EI/26-27/169 Rev-A`` has no trailing digits and
    would name the file ``…-EI-.docx``.  The marker goes on the stem instead.
    """
    plain = dict(payload)
    plain[PRINTED_NUMBER.get(kind, "number")] = _s(paper_no)
    plain["number"] = _s(paper_no)
    base = registry.filename(kind, plain)
    if not _s(revision):
        return base
    stem, dot, ext = base.rpartition(".")
    return f"{stem} Rev-{revision}{dot}{ext}" if dot else f"{base} Rev-{revision}"


def render_paper(conn, paper_id: int) -> tuple[bytes, str, str]:
    """``(bytes, download name, mime)`` — rendered on demand, never stored."""
    row = _row(conn, paper_id)
    stored = json.loads(row["payload"])
    payload = _for_render(row["kind"], stored, row["paper_no"], row["revision"])
    data, _ = engine.render(row["kind"], payload)
    fmt = registry.entry(row["kind"])["format"]
    return (data, download_name(row["kind"], stored, row["paper_no"], row["revision"]),
            MIME.get(fmt, "application/octet-stream"))
