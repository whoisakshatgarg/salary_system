"""One entry per paper kind: template file, token map, item region, filename.

A new official format = swap the file in ``templates/``, adjust the entry
here, **no engine change** (SOP-DESIGN §4).

Entry keys
----------
``kind`` / ``format`` / ``template`` / ``sheet``
    what to load.
``tokens``
    ``{token: spec}``.  A spec is ``{"path": <dotted payload path>}`` plus
    optionally ``index`` (nth element of a list), ``render`` (one of the four
    CONVENTIONS §4 date renderers or ``date_value``), ``default``,
    ``required``, ``const``, ``upper``.  ``cell`` (xlsx) pins the coordinate
    the token must occupy - the engine asserts it, so template drift fails
    loudly instead of rendering a blank; ``where`` is the docx equivalent and
    is documentation only.
``items``
    xlsx: ``first_row`` / ``last_row`` / ``cells`` ({column letter: spec}).
    docx: ``table`` / ``marker_row`` / ``row_builder``.
``images``
    pictures the engine must guarantee (see engine ``_ensure_images``).
``currency_numfmt_cells``
    money cells whose ``"$"`` number format follows the payload currency
    (CONVENTIONS §9-D).
``filename``
    callable ``payload -> download name``, mirroring the company's own file
    names.

CANONICAL PAYLOAD SCHEMAS
=========================
Every payload is a plain ``dict`` (it is stored as JSON in ``paper.payload``).
Fields common to all kinds::

    number        str    the fully formatted document number (CONVENTIONS §3)
    date_iso      str    "YYYY-MM-DD", the document's own date
    order_no      str    the internal order this paper hangs off
    customer      {name, country, address_lines[],
                   contact: {name, email, tel, fax}}
    currency      {code, symbol, header}      e.g. {"USD", "$", "Prices (U.S.D.)"}
    items         list, per-kind shape below

Per kind::

    quotation      client_code, rfq_ref, rfq_date_iso,
                   items[{sno, code, description, qty, unit, unit_price, total}],
                   total, validity_line, price_basis, lead_time, payment_terms,
                   guarantee, taxes_duties, note, approved_by, prepared_by

    ack            bill_to_lines[], ship_to_lines[], cust_po, po_date_iso,
                   quotation_ref, client_code, ack_ref, ack_date_iso,
                   contacts{name, email, tel, fax}, price_basis, payment_terms,
                   ship_date_iso, wo_no_long,
                   items[{sno, code, description, material, qty, unit,
                          unit_price, total}],
                   total, remittance_block, currency_header

    work_order     wo_no_short, client_code, cust_po, date_iso,
                   items[{sno, part_no, item, qty, material, marking, remarks}]

    invoice        invoice_no, date_iso, buyer_po_block (list[{po, date_iso}]),
                   iec, ad_code, consignee_lines[], buyer_lines[], pre_carriage,
                   place_receipt, vessel, port_loading, port_discharge,
                   origin_country, final_destination, terms, marks_lines[],
                   hts_line,
                   items[{sno, code_desc, po, qty, net_wt, rate, amount}],
                   gsp_line, totals{qty, net_wt, amount}, total_weight_line,
                   amount_words

    packing_list   invoice header fields (above) plus
                   boxes[{box_label, size, net_wt, gross_wt,
                          items[{sno, code_desc, qty}]}],
                   totals{qty, net_wt, gross_wt}, total_weight_line

    coc            customer_caps, customer_short, po, invoice_no,
                   invoice_date_iso, part_desc, material, plating, finishing,
                   qty_shipped, authenticator, date_shipped_iso

    test_cert      cert_no, cert_date_iso, customer_line, po, po_date_iso,
                   invoice_no, invoice_date_iso, extra_elements[],
                   items[{sno, item, size, qty, component, heat_no, material,
                          chem{C, Mn, Si, P, S, Cr, Ni, Mo, ...}}]

    bom            bom_no, date_iso, customer_line, po, po_date_iso, wo_no,
                   part_assy,
                   items[{sno, part_no, description, size, material,
                          heat_or_os, source, qty_per, total_qty, unit,
                          remarks}]
"""

from __future__ import annotations

import re

# --------------------------------------------------------------------------
# editable defaults that live on the paper, not in code paths
# (CONVENTIONS §1 - the current 2026 wording)
# --------------------------------------------------------------------------

REMITTANCE_2026 = (
    "BANK OF AMERICA, NEWYORK, U.S.A. SWIFT CODE : BOFAUS3N, ACCOUNT NUMBER : "
    "6550692276 OF “UNION BANK OF INDIA, MUMBAI. (INDIA)”  TO THE CREDIT OF "
    "\"UNION BANK OF INDIA, S.S.I. BRANCH \",  H-6, PATEL NAGAR-III, "
    "GHAZIABAD-201001 (U.P),INDIA. FAX : 91-120-2835993 / PHONES: 91-120-2834293 "
    "SWIFT CODE : UBININBBGHZ")
REMIT_INTRO = "Please remit payment by wire transfer per following bank details:-"
BENEFICIARY_LABEL = "BENEFICIARY : "
BENEFICIARY_ADDRESS = ("APEX THERMOCON PVT. LTD., A-2/15, SECTOR-17, KAVI NAGAR "
                       "INDUSTRIAL AREA, GHAZIABAD-201002 (U.P.), INDIA")
BENEFICIARY_ACCOUNT = ("A/C NO. 508505010000249 OF UNION BANK OF INDIA, S.S.I. BRANCH, "
                       "H-6, PATEL NAGAR-III, GHAZIABAD-201001 (U.P.), INDIA")

CURRENCY_HEADER = "Prices (U.S.D.)"          # CONVENTIONS §9-D, a field not a constant
VALIDITY_LINE = "THIS QUOTATION IS VALID FOR 30 DAYS"
QUOTATION_INTRO = ("Apex Thermocon is pleased to submit this quotation in response to "
                   "your Request For Quote. We trust the quote meets with your "
                   "Technical, Price and Delivery requirements and look forward to "
                   "your business.")
HTS_LINE = ("Parts & Accessories for Automatic Regulating & Controlling Instruments "
            "– (H.T. Code) (9032.90.6080)")
GSP_LINE = "GOODS OF INDIAN ORIGIN ELIGIBLE FOR NIL         DUTY UNDER GSP (A)"
DECLARATION = ("WE DECLARE THAT THIS INVOICE SHOWS THE ACTUAL PRICE OF THE GOODS "
               "DESCRIBED AND THAT ALL PARTICULARS ARE TRUE AND CORRECT.")

APEX_LOGO = {"file": "apex_logo.png", "anchor": "A1"}


# --------------------------------------------------------------------------
# helpers used by the entries
# --------------------------------------------------------------------------

def _serial(number, default=""):
    """Trailing numeric segment of a document number: AT/EI/26-27/168 -> 168."""
    if not number:
        return default
    m = re.search(r"(\d+)\s*$", str(number))
    return m.group(1) if m else default


def _digits(value, default=""):
    m = re.search(r"(\d+)", str(value or ""))
    return m.group(1) if m else default


def _slug(text):
    return re.sub(r"[^A-Za-z0-9]+", "-", str(text or "")).strip("-")


def _packing_list_rows(payload):
    """boxes[] -> table rows, with a vertical-merge group per box.

    A box of one line gets no ``w:vMerge`` at all - exactly what the reference
    does for its single-line Box No. 2.
    """
    rows = []
    for box in payload.get("boxes") or []:
        items = box.get("items") or []
        multi = len(items) > 1
        for i, item in enumerate(items):
            first = i == 0
            mode = ("restart" if first else "continue") if multi else None
            show = first or not multi
            rows.append({
                "cells": {
                    "code_desc": item.get("code_desc", ""),
                    "qty": item.get("qty", ""),
                    "box_label": box.get("box_label", "") if show else "",
                    "box_size": box.get("size", "") if show else "",
                    "net_wt": box.get("net_wt", "") if show else "",
                    "gross_wt": box.get("gross_wt", "") if show else "",
                },
                "vmerge": {0: "continue", 3: mode, 4: mode, 5: mode, 6: mode},
            })
    return rows


def _invoice_rows(payload):
    return [{
        "cells": {
            "code_desc": item.get("code_desc", ""),
            "po": item.get("po", ""),
            "qty": item.get("qty", ""),
            "net_wt": item.get("net_wt", ""),
            "rate": item.get("rate", "-"),
            "amount": item.get("amount", "-"),
        },
        "vmerge": {0: "continue"},
    } for item in (payload.get("items") or [])]


def _buyer_po_block(payload):
    """``PO03864   Dtd.  06.03.26, PO03956   Dtd.  27.03.26`` (CONVENTIONS §4)."""
    from .dates import dtd_ddmmyy
    parts = []
    for po in payload.get("buyer_po_block") or []:
        if isinstance(po, str):
            parts.append(po)
        else:
            parts.append(f"{po.get('po', '')}   {dtd_ddmmyy(po.get('date_iso'))}")
    return ", ".join(parts)


def _invoice_no_date(payload):
    from .dates import ddmmyyyy
    return f"{payload.get('invoice_no', '')}  {ddmmyyyy(payload.get('date_iso'))}"


# --------------------------------------------------------------------------
# entries
# --------------------------------------------------------------------------

def _spare_head(n):
    return {"path": "extra_elements", "index": n - 1, "default": "-"}


def _spare_item(n):
    return {"path": f"spare_{n}", "default": "-"}


ENTRIES = {

    # ---------------------------------------------------------------- quotation
    "quotation": {
        "kind": "quotation",
        "format": "xlsx",
        "template": "quotation.xlsx",
        "sheet": "Quotation",
        "date_renderers": {"rfq_date_iso": "ordinal_apostrophe",
                           "date_iso": "ordinal_apostrophe"},
        "tokens": {
            "customer_name": {"cell": "A6", "path": "customer.name"},
            "customer_line_2": {"cell": "A7", "path": "customer.address_lines",
                                "index": 0, "default": ""},
            "customer_line_3": {"cell": "A8", "path": "customer.address_lines",
                                "index": 1, "default": ""},
            "customer_country": {"cell": "A9", "path": "customer.country"},
            "rfq_ref": {"cell": "F6", "path": "rfq_ref"},
            "rfq_date": {"cell": "F7", "path": "rfq_date_iso",
                         "render": "ordinal_apostrophe"},
            "quotation_date": {"cell": "F8", "path": "date_iso",
                               "render": "ordinal_apostrophe"},
            "number": {"cell": "F9", "path": "number"},
            "client_code": {"cell": "C10", "path": "client_code"},
            "attn": {"cell": "C11", "path": "customer.contact.name"},
            "tel": {"cell": "C12", "path": "customer.contact.tel"},
            "fax": {"cell": "C13", "path": "customer.contact.fax"},
            "email": {"cell": "C14", "path": "customer.contact.email"},
            "intro": {"cell": "A15", "path": "intro", "default": QUOTATION_INTRO},
            "currency_header": {"cell": "F17", "path": "currency.header",
                                "default": CURRENCY_HEADER},
            "validity_line": {"cell": "C49", "path": "validity_line",
                              "default": VALIDITY_LINE},
            "total": {"cell": "G50", "path": "total"},
            "price_basis": {"cell": "C51", "path": "price_basis"},
            "lead_time": {"cell": "C52", "path": "lead_time"},
            "payment_terms": {"cell": "C53", "path": "payment_terms"},
            "guarantee": {"cell": "C54", "path": "guarantee"},
            "taxes_duties": {"cell": "C55", "path": "taxes_duties"},
            "note": {"cell": "C56", "path": "note"},
            "approved_by": {"cell": "A59", "path": "approved_by", "default": ""},
            "prepared_by": {"cell": "D59", "path": "prepared_by", "default": ""},
        },
        "items": {
            "first_row": 19, "last_row": 48,
            "cells": {
                "A": {"path": "sno"}, "B": {"path": "code"},
                "C": {"path": "description"}, "D": {"path": "qty"},
                "E": {"path": "unit"}, "F": {"path": "unit_price"},
                "G": {"path": "total"},
            },
        },
        "currency_numfmt_cells": ["F19:G48", "G50"],
        "filename": lambda p: (
            f"{_slug((p.get('customer') or {}).get('name') or 'Customer')}"
            f"-Quotation-{_serial(p.get('number'))}.xlsx"),
    },

    # --------------------------------------------------------------------- ack
    "ack": {
        "kind": "ack",
        "format": "xlsx",
        "template": "ack.xlsx",
        "sheet": "01",
        "date_renderers": {"po_date_iso": "ordinal_apostrophe",
                           "ack_date_iso": "ordinal_apostrophe",
                           "ship_date_iso": "ordinal_apostrophe"},
        "tokens": {
            **{f"bill_to_{i}": {"cell": f"A{6 + i}", "path": "bill_to_lines",
                                "index": i - 1, "default": ""} for i in range(1, 6)},
            **{f"ship_to_{i}": {"cell": f"G{6 + i}", "path": "ship_to_lines",
                                "index": i - 1, "default": ""} for i in range(1, 6)},
            "cust_po": {"cell": "F6", "path": "cust_po"},
            "po_date": {"cell": "F7", "path": "po_date_iso",
                        "render": "ordinal_apostrophe"},
            "quotation_ref": {"cell": "F9", "path": "quotation_ref"},
            "client_code": {"cell": "C12", "path": "client_code"},
            "ack_ref": {"cell": "F12", "path": "ack_ref"},
            "ack_date": {"cell": "F13", "path": "ack_date_iso",
                         "render": "ordinal_apostrophe"},
            "price_basis": {"cell": "F14", "path": "price_basis"},
            "payment_terms": {"cell": "F15", "path": "payment_terms"},
            "ship_date": {"cell": "F16", "path": "ship_date_iso",
                          "render": "ordinal_apostrophe"},
            "wo_no_long": {"cell": "F17", "path": "wo_no_long"},
            "contact_name": {"cell": "B14", "path": "contacts.name"},
            "contact_email": {"cell": "B15", "path": "contacts.email"},
            "contact_tel": {"cell": "B16", "path": "contacts.tel"},
            "contact_fax": {"cell": "B17", "path": "contacts.fax"},
            "currency_header": {"cell": "I18", "path": "currency_header",
                                "default": CURRENCY_HEADER},
            "total": {"cell": "I38", "path": "total"},
            "remit_intro": {"cell": "A44", "path": "remit_intro",
                            "default": REMIT_INTRO},
            "remittance_block": {"cell": "A45", "path": "remittance_block",
                                 "default": REMITTANCE_2026},
            "beneficiary_label": {"cell": "A48", "path": "beneficiary_label",
                                  "default": BENEFICIARY_LABEL},
            "beneficiary_address": {"cell": "A49", "path": "beneficiary_address",
                                    "default": BENEFICIARY_ADDRESS},
            "beneficiary_account": {"cell": "A50", "path": "beneficiary_account",
                                    "default": BENEFICIARY_ACCOUNT},
        },
        "items": {
            "first_row": 20, "last_row": 37,
            "cells": {
                "A": {"path": "sno"}, "B": {"path": "code"},
                "C": {"path": "description"}, "F": {"path": "material"},
                "G": {"path": "qty"}, "H": {"path": "unit"},
                "I": {"path": "unit_price"}, "J": {"path": "total"},
            },
        },
        "currency_numfmt_cells": ["I20:J37", "I38"],
        "filename": lambda p: f"{p.get('wo_no_long') or p.get('ack_ref') or 'ACK'}.xlsx",
    },

    # -------------------------------------------------------------- work_order
    "work_order": {
        "kind": "work_order",
        "format": "xlsx",
        "template": "work_order.xlsx",
        "sheet": "Sheet1",
        "date_renderers": {"date_iso": "date_value"},     # numfmt dd/mm/yyyy
        "tokens": {
            "wo_no_short": {"cell": "B6", "path": "wo_no_short"},
            "client_code": {"cell": "G6", "path": "client_code"},
            "cust_po": {"cell": "B8", "path": "cust_po"},
            "wo_date": {"cell": "G8", "path": "date_iso", "render": "date_value"},
        },
        "items": {
            "first_row": 12, "last_row": 38,
            "cells": {
                "A": {"path": "sno"}, "B": {"path": "part_no"},
                "C": {"path": "item"}, "D": {"path": "qty"},
                "E": {"path": "material"}, "F": {"path": "marking"},
                "G": {"path": "remarks"},
            },
        },
        "images": [{"file": "apex_logo.png", "anchor": "F1",
                    "width": 57, "height": 58}],
        "filename": lambda p: (
            f"Apex-Work-Order-{_slug(p.get('wo_no_short'))}.xlsx"),
    },

    # --------------------------------------------------------------- test_cert
    "test_cert": {
        "kind": "test_cert",
        "format": "xlsx",
        "template": "test_cert.xlsx",
        "sheet": "Sheet2",
        "date_renderers": {"cert_date_iso": "date_value",     # numfmt mm-dd-yy
                           "po_date_iso": "date_value",
                           "invoice_date_iso": "date_value"},
        "tokens": {
            "cert_no": {"cell": "Q3", "path": "cert_no"},
            "cert_date": {"cell": "Q4", "path": "cert_date_iso", "render": "date_value"},
            "customer_line": {"cell": "C5", "path": "customer_line"},
            "po": {"cell": "C6", "path": "po"},
            "po_date": {"cell": "F6", "path": "po_date_iso", "render": "date_value"},
            "invoice_no": {"cell": "K6", "path": "invoice_no"},
            "invoice_date": {"cell": "Q6", "path": "invoice_date_iso",
                             "render": "date_value"},
            **{f"spare_head_{n}": dict(_spare_head(n), cell=f"{c}8")
               for n, c in enumerate("PQRST", start=1)},
        },
        "items": {
            "first_row": 9, "last_row": 28,
            "cells": {
                "A": {"path": "sno"}, "B": {"path": "item"},
                "C": {"path": "size"}, "D": {"path": "qty"},
                "E": {"path": "component", "default": "-"},
                "F": {"path": "heat_no"}, "G": {"path": "material"},
                "H": {"path": "chem.C"}, "I": {"path": "chem.Mn"},
                "J": {"path": "chem.Si"}, "K": {"path": "chem.P"},
                "L": {"path": "chem.S"}, "M": {"path": "chem.Cr"},
                "N": {"path": "chem.Ni"}, "O": {"path": "chem.Mo"},
                **{c: _spare_item(n) for n, c in enumerate("PQRST", start=1)},
            },
        },
        # sizes in px that convert to the reference's own EMU extents
        "images": [
            {"file": "apex_logo.png", "anchor": "A1", "width": 102.2, "height": 88.4},
            {"file": "apex_iso.png", "anchor": "K1", "width": 98.0, "height": 50.0},
        ],
        "filename": lambda p: (
            f"Test Certificate PO{_digits(p.get('po'))}"
            f"-EI-{_serial(p.get('invoice_no'))}.xlsx"),
    },

    # --------------------------------------------------------------------- bom
    "bom": {
        "kind": "bom",
        "format": "xlsx",
        "template": "bom.xlsx",
        "sheet": "BOM",
        "date_renderers": {"date_iso": "date_value", "po_date_iso": "date_value"},
        "tokens": {
            "bom_no": {"cell": "Q3", "path": "bom_no"},
            "bom_date": {"cell": "Q4", "path": "date_iso", "render": "date_value"},
            "customer_line": {"cell": "C5", "path": "customer_line"},
            "po": {"cell": "C6", "path": "po"},
            "po_date": {"cell": "F6", "path": "po_date_iso", "render": "date_value"},
            "wo_no": {"cell": "K6", "path": "wo_no"},
            "part_assy": {"cell": "Q6", "path": "part_assy"},
        },
        "items": {
            "first_row": 9, "last_row": 32,
            "cells": {
                "A": {"path": "sno"}, "B": {"path": "part_no"},
                "C": {"path": "description"}, "F": {"path": "size"},
                "G": {"path": "material"}, "H": {"path": "heat_or_os"},
                "J": {"path": "source"}, "L": {"path": "qty_per"},
                "N": {"path": "total_qty"}, "P": {"path": "unit"},
                "Q": {"path": "remarks"},
            },
        },
        "images": [
            {"file": "apex_logo.png", "anchor": "A1", "width": 203, "height": 175},
            {"file": "apex_iso.png", "anchor": "K1", "width": 225, "height": 115},
        ],
        "filename": lambda p: f"Apex-BOM-{_slug(str(p.get('bom_no', '')).replace('AT/BOM/', ''))}.xlsx",
    },

    # ------------------------------------------------------------ packing_list
    "packing_list": {
        "kind": "packing_list",
        "format": "docx",
        "template": "packing_list.docx",
        "date_renderers": {"date_iso": "ddmmyyyy",
                           "buyer_po_block[].date_iso": "dtd_ddmmyy"},
        "tokens": {
            "invoice_no_date": {"where": "t0.r0.c1.p1", "path": "__invoice_no_date"},
            "buyer_po_block": {"where": "t0.r0.c2.p1", "path": "__buyer_po_block"},
            "iec": {"where": "t0.r1.c1.p0", "path": "iec"},
            "ad_code": {"where": "t0.r1.c1.p1", "path": "ad_code"},
            **{f"consignee_{i}": {"where": f"t0.r2.c0.p{i}", "path": "consignee_lines",
                                  "index": i - 1, "default": ""} for i in range(1, 6)},
            **{f"buyer_{i}": {"where": f"t0.r2.c1.p{i}", "path": "buyer_lines",
                              "index": i - 1, "default": ""} for i in range(1, 5)},
            "pre_carriage": {"where": "t0.r3.c0.p1", "path": "pre_carriage",
                             "default": ""},
            "place_receipt": {"where": "t0.r3.c1.p1", "path": "place_receipt",
                              "default": ""},
            "origin_country": {"where": "t0.r3.c2.p1", "path": "origin_country",
                               "default": ""},
            "country_final_destination": {"where": "t0.r3.c3.p1",
                                          "path": "final_destination"},
            "vessel": {"where": "t0.r4.c0.p1", "path": "vessel"},
            "port_loading": {"where": "t0.r4.c1.p1", "path": "port_loading",
                             "default": ""},
            "terms": {"where": "t0.r4.c2.p1", "path": "terms"},
            "port_discharge": {"where": "t0.r5.c0.p1", "path": "port_discharge"},
            "final_destination": {"where": "t0.r5.c1.p1", "path": "final_destination"},
            **{f"marks_{i}": {"where": f"t0.r7.c0.p{i}", "path": "marks_lines",
                              "index": i - 1, "default": ""} for i in range(1, 4)},
            "hts_line": {"where": "t0.r7.c1.p0", "path": "hts_line",
                         "default": HTS_LINE},
            "total_weight_line": {"where": "t0.r9.c0.p0", "path": "total_weight_line"},
            "totals_qty": {"where": "t0.r9.c1.p0", "path": "totals.qty"},
            "totals_net_wt": {"where": "t0.r9.c4.p0", "path": "totals.net_wt"},
            "totals_gross_wt": {"where": "t0.r9.c5.p0", "path": "totals.gross_wt"},
        },
        "items": {"table": 0, "marker_row": 8, "row_builder": _packing_list_rows},
        "filename": lambda p: (
            f"Apex-Export Packing List-EI-{_serial(p.get('invoice_no'))}.docx"),
    },

    # ----------------------------------------------------------------- invoice
    "invoice": {
        "kind": "invoice",
        "format": "docx",
        "template": "invoice.docx",
        "date_renderers": {"date_iso": "ddmmyyyy",
                           "buyer_po_block[].date_iso": "dtd_ddmmyy"},
        "tokens": {
            "invoice_no_date": {"where": "t0.r0.c1.p1", "path": "__invoice_no_date"},
            "buyer_po_block": {"where": "t0.r0.c2.p1", "path": "__buyer_po_block"},
            "iec": {"where": "t0.r1.c1.p0", "path": "iec"},
            "ad_code": {"where": "t0.r1.c1.p1", "path": "ad_code"},
            **{f"consignee_{i}": {"where": f"t0.r2.c0.p{i}", "path": "consignee_lines",
                                  "index": i - 1, "default": ""} for i in range(1, 6)},
            **{f"buyer_{i}": {"where": f"t0.r2.c1.p{i}", "path": "buyer_lines",
                              "index": i - 1, "default": ""} for i in range(1, 5)},
            "pre_carriage": {"where": "t0.r3.c0.p1", "path": "pre_carriage",
                             "default": ""},
            "place_receipt": {"where": "t0.r3.c1.p1", "path": "place_receipt",
                              "default": ""},
            "origin_country": {"where": "t0.r3.c2.p1", "path": "origin_country"},
            "country_final_destination": {"where": "t0.r3.c3.p1",
                                          "path": "final_destination"},
            "vessel": {"where": "t0.r4.c0.p1", "path": "vessel"},
            "port_loading": {"where": "t0.r4.c1.p1", "path": "port_loading"},
            "terms": {"where": "t0.r4.c2.p1", "path": "terms"},
            "port_discharge": {"where": "t0.r5.c0.p1", "path": "port_discharge"},
            "final_destination": {"where": "t0.r5.c1.p1", "path": "final_destination"},
            "currency_head": {"where": "t0.r6.c5/c6", "path": "currency.head",
                              "default": "USD ($)"},
            **{f"marks_{i}": {"where": f"t0.r7.c0.p{i}", "path": "marks_lines",
                              "index": i - 1, "default": ""} for i in range(1, 4)},
            "hts_line": {"where": "t0.r7.c1.p0", "path": "hts_line",
                         "default": HTS_LINE},
            "gsp_line": {"where": "t0.r9.c1.p0", "path": "gsp_line",
                         "default": GSP_LINE},
            "total_weight_line": {"where": "t0.r10.c0.p0", "path": "total_weight_line"},
            "totals_qty": {"where": "t0.r10.c2.p0", "path": "totals.qty"},
            "totals_net_wt": {"where": "t0.r10.c3.p0", "path": "totals.net_wt"},
            "totals_amount": {"where": "t0.r10.c5.p0", "path": "totals.amount"},
            "amount_words": {"where": "t0.r11.c0.p0", "path": "amount_words"},
            "totals_amount_words_value": {"where": "t0.r11.c5.p0",
                                          "path": "totals.amount"},
            "declaration": {"where": "t0.r12.c0.p0", "path": "declaration",
                            "default": DECLARATION},
        },
        "items": {"table": 0, "marker_row": 8, "row_builder": _invoice_rows},
        "filename": lambda p: (
            f"Apex-Export Invoice-EI-{_serial(p.get('invoice_no'))}.docx"),
    },

    # --------------------------------------------------------------------- coc
    "coc": {
        "kind": "coc",
        "format": "docx",
        "template": "coc.docx",
        "date_renderers": {"invoice_date_iso": "ordinal_apostrophe",
                           "date_shipped_iso": "ordinal_apostrophe"},
        "tokens": {
            "customer_caps": {"where": "p10 certifying sentence",
                              "path": "customer_caps", "upper": True},
            "customer_short": {"where": "p13 label prefix", "path": "customer_short"},
            "po": {"where": "p13 value", "path": "po"},
            "invoice_no": {"where": "p14 value", "path": "invoice_no"},
            "invoice_date": {"where": "p14 after 'Dtd. '", "path": "invoice_date_iso",
                             "render": "ordinal_apostrophe"},
            "part_desc": {"where": "p15 value", "path": "part_desc"},
            "material": {"where": "p16 value", "path": "material"},
            "plating": {"where": "p17 value", "path": "plating", "default": "NA"},
            "finishing": {"where": "p18 value", "path": "finishing", "default": "NA"},
            "qty_shipped": {"where": "p19 value ('{{qty_shipped}} Nos.')",
                            "path": "qty_shipped"},
            "authenticator": {"where": "p20 value", "path": "authenticator",
                              "default": "Q.A. MANAGER"},
            "date_shipped": {"where": "p21 value", "path": "date_shipped_iso",
                             "render": "ordinal_apostrophe"},
        },
        "filename": lambda p: (
            f"COC-PO-{_digits(p.get('po'))}-EI-{_serial(p.get('invoice_no'))}.docx"),
    },
}


# --------------------------------------------------------------------------
# derived tokens the payload does not carry literally
# --------------------------------------------------------------------------

_DERIVED = {
    "__invoice_no_date": _invoice_no_date,
    "__buyer_po_block": _buyer_po_block,
}


def prepare(kind, payload):
    """Payload plus the handful of composed strings the templates need."""
    if kind not in ("invoice", "packing_list"):
        return payload
    out = dict(payload)
    for key, fn in _DERIVED.items():
        out[key] = fn(payload)
    return out


def entry(kind):
    try:
        return ENTRIES[kind]
    except KeyError:
        raise KeyError(f"unknown paper kind {kind!r}; known: {sorted(ENTRIES)}") from None


def kinds():
    return sorted(ENTRIES)


def filename(kind, payload):
    return entry(kind)["filename"](payload)
