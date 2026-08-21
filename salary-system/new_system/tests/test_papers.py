"""Spec for the payload builders and the paper lifecycle (SOP-DESIGN §2/§5).

The engine spec (test_documents_engine.py) proves a payload renders into the
company's own format; THIS file proves the payload is right in the first
place: every schema key present, every number taken exactly once and only at
creation, every derivation (chemistry, outsourced BOM lines, amount in words,
multi-PO invoices) coming from the order rather than from a guess.

Fixture: one US customer with two orders, drawings, two heats issued against
the first order, a costing whose bill of materials mixes a heat with a
bought-out part, and a ledger quotation — i.e. enough of a workshop for all
eight builders to have something true to say.
"""

import json
import os
import sys
import tempfile
import unittest
import zipfile
from datetime import date
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend.core import db, numbering, paths                       # noqa: E402
from backend.documents import payloads, registry, router, service   # noqa: E402
from backend.modules import customers, orders, parts, quotations, settings  # noqa: E402

# --------------------------------------------------------------------------
# The canonical schemas, transcribed by hand from registry.py's module
# docstring.  Deliberately NOT imported from payloads.SCHEMAS: a copy that
# agreed with itself would prove nothing.
# --------------------------------------------------------------------------
COMMON = {"number", "date_iso", "order_no", "customer", "currency", "items"}

EXPORT_HEADER = {
    "invoice_no", "buyer_po_block", "iec", "ad_code", "consignee_lines",
    "buyer_lines", "pre_carriage", "place_receipt", "vessel", "port_loading",
    "port_discharge", "origin_country", "final_destination", "terms",
    "marks_lines", "hts_line", "total_weight_line"}

SCHEMA = {
    "quotation": COMMON | {
        "client_code", "rfq_ref", "rfq_date_iso", "total", "validity_line",
        "price_basis", "lead_time", "payment_terms", "guarantee",
        "taxes_duties", "note", "approved_by", "prepared_by"},
    "ack": COMMON | {
        "bill_to_lines", "ship_to_lines", "cust_po", "po_date_iso",
        "quotation_ref", "client_code", "ack_ref", "ack_date_iso", "contacts",
        "price_basis", "payment_terms", "ship_date_iso", "wo_no_long", "total",
        "remittance_block", "currency_header"},
    "work_order": COMMON | {"wo_no_short", "client_code", "cust_po"},
    "invoice": COMMON | EXPORT_HEADER | {"gsp_line", "totals", "amount_words"},
    "packing_list": (COMMON - {"items"}) | EXPORT_HEADER | {"boxes", "totals"},
    "coc": COMMON | {
        "customer_caps", "customer_short", "po", "invoice_no",
        "invoice_date_iso", "part_desc", "material", "plating", "finishing",
        "qty_shipped", "authenticator", "date_shipped_iso"},
    "test_cert": COMMON | {
        "cert_no", "cert_date_iso", "customer_line", "po", "po_date_iso",
        "invoice_no", "invoice_date_iso", "extra_elements"},
    "bom": COMMON | {
        "bom_no", "customer_line", "po", "po_date_iso", "wo_no", "part_assy"},
}

CHEM_4515 = {"C": 0.022, "Mn": 1.32, "Si": 0.5, "P": 0.036, "S": 0.018,
             "Cr": 16.89, "Ni": 10.51, "Mo": 2.18, "Cu": 0.31, "N": 0.052}
CHEM_4443 = {"C": 0.019, "Mn": 1.48, "Si": 0.39, "P": 0.044, "S": 0.011,
             "Cr": 16.66, "Ni": 10.05, "Mo": 2.02}


class PapersBase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        os.environ["SALARY_DATA_DIR"] = self.tmp.name
        self.db_path = Path(self.tmp.name) / "test.db"
        db.init_db(self.db_path)
        settings.ensure_defaults(self.db_path)
        self.conn = db.connect(self.db_path)
        numbering.ensure_seeds(self.conn)

        self.cust = customers.save_customer(self.conn, {
            "name": "SELCO Products Company", "code": "S01",
            "address_billing": "8780, Technology Way\nReno, NV 89521-5908\nUSA",
            "address_shipping": "640, Maestro Drive, Suite 102\nReno, NV 89511\nUSA",
            "payment_terms": "N 45 by Wire Transfer"})
        customers.add_contact(self.conn, self.cust, {
            "name": "Cassie Halverson", "phone": "001-775-6745100",
            "email": "cassie@selco.com"})
        customers.add_contact(self.conn, self.cust, {"name": "Second Contact"})

        self.uk = customers.save_customer(self.conn, {
            "name": "Thermosense Ltd.", "code": "T04",
            "address_billing": "Sunrise Business Park\nMarlow\nEngland"})

        self.drg_a = parts.save_drawing(self.conn, {
            "drawing_no": "TWB02000750", "revision": "A", "customer_id": self.cust,
            "description": 'Thermowell, 1/4"NPT (P01)', "material_class": "Brass",
            "grade": "Naval Brass Grade 1", "unit": "EA"})
        self.drg_b = parts.save_drawing(self.conn, {
            "drawing_no": "TPS-0353", "revision": "A", "customer_id": self.cust,
            "description": "Thermistor Probe Housing", "material_class": "Steel",
            "grade": "A479-316L", "unit": "EA"})

        self.order_a = orders.create_order(self.conn, {
            "customer_id": self.cust, "customer_po": "PO03864",
            "order_date": "2026-03-06", "due_date": "2026-09-16",
            "items": [{"drawing_id": self.drg_a, "qty": 600, "unit": "EA", "rate": 0.675},
                      {"drawing_id": self.drg_b, "qty": 300, "unit": "EA", "rate": 0.32}]})
        self.order_b = orders.create_order(self.conn, {
            "customer_id": self.cust, "customer_po": "PO03956",
            "order_date": "2026-03-27", "due_date": "2026-10-01",
            "items": [{"drawing_id": self.drg_b, "qty": 100, "unit": "EA", "rate": 0}]})
        self.order_uk = orders.create_order(self.conn, {
            "customer_id": self.uk, "customer_po": "TS-9001",
            "order_date": "2026-04-01",
            "items": [{"description": "Collar Assy.", "qty": 120000, "unit": "EA",
                       "rate": 0.53}]})
        self.order_a_no = self.conn.execute(
            "SELECT order_no FROM customer_order WHERE id=?", (self.order_a,)
        ).fetchone()["order_no"]

        self.heat_a = self.heat("H4515", "A479-316L", '1-3/8" Hex.', CHEM_4515)
        self.heat_b = self.heat("H4443", "A479-316L", '9/8" Hex.', CHEM_4443)
        self.issue(self.heat_a, self.order_a_no)
        self.issue(self.heat_b, self.order_a_no)

        self.vendor = self.make_vendor("Plastico Mouldings")
        self.os_item = self.make_os_item(self.vendor)
        parts.save_costing(self.conn, self.drg_a, {"margin_pct": 10, "materials": [
            {"heat_id": self.heat_a, "heat_number": "H4515",
             "material_label": "SS Rod 25mm", "unit": "rod", "unit_cost": 400,
             "qty_per_piece": 0.02},
            {"material_label": 'Plastic Cap, 1/4"NPT', "unit": "EA",
             "unit_cost": 3, "qty_per_piece": 1, "os_item_id": self.os_item}]})

        self.doc = quotations.create_doc(self.conn, "quotation", {
            "customer_id": self.cust, "order_id": self.order_a,
            "doc_date": "2026-08-13", "reference": "Email",
            "lines": [{"drawing_id": self.drg_a, "description": "Collar Assy.",
                       "qty": 120000, "unit": "EA", "rate": 0.53}]})

    def tearDown(self):
        self.conn.close()
        os.environ.pop("SALARY_DATA_DIR", None)
        self.tmp.cleanup()

    # -- fixture helpers (raw SQL: the inventory/outsourcing modules are
    #    another wave's territory, and the papers only ever READ these rows)
    def heat(self, number, grade, size, chem):
        cur = self.conn.execute(
            "INSERT INTO heat (heat_number, date_received, material_class, grade,"
            " size_section, rods_received) VALUES (?,?,?,?,?,?)",
            (number, "2026-01-04", "Steel", grade, size, 20))
        heat_id = cur.lastrowid
        for element, percent in chem.items():
            self.conn.execute(
                "INSERT INTO heat_composition (heat_id, element, percent) VALUES (?,?,?)",
                (heat_id, element, percent))
        self.conn.commit()
        return heat_id

    def issue(self, heat_id, order_no, rods=3):
        self.conn.execute(
            "INSERT INTO heat_movement (heat_id, mv_date, type, order_id, rods)"
            " VALUES (?,?,'issue',?,?)", (heat_id, "2026-04-02", order_no, rods))
        self.conn.commit()

    def make_vendor(self, name):
        code = numbering.vendor_code(self.conn)
        cur = self.conn.execute("INSERT INTO vendor (code, name) VALUES (?,?)",
                                (code, name))
        self.conn.commit()
        return cur.lastrowid

    def make_os_item(self, vendor_id):
        os_id = numbering.os_item_id(self.conn)
        cur = self.conn.execute(
            "INSERT INTO os_item (os_id, description, material, size_section, unit,"
            " qty, vendor_id) VALUES (?,?,?,?,?,?,?)",
            (os_id, 'Plastic Cap, 1/4"NPT', "HDPE", '1/4" NPT', "EA", 500, vendor_id))
        self.conn.commit()
        return cur.lastrowid

    # -- shorthands
    def make(self, kind, order_id=None, **opts):
        return service.create_paper(self.conn, kind, order_id or self.order_a, opts)

    def invoice(self, *order_ids, **opts):
        return self.make("invoice", order_ids=list(order_ids) or [self.order_a], **opts)


# ==========================================================================
# Every builder fills the whole schema
# ==========================================================================

class BuilderSchemas(PapersBase):
    def test_payloads_schemas_match_the_registry_docstring(self):
        """payloads.SCHEMAS is the docstring's contract, key for key."""
        self.assertEqual({k: set(v) for k, v in payloads.SCHEMAS.items()}, SCHEMA)

    def test_every_kind_has_a_builder_a_schema_and_a_template(self):
        self.assertEqual(sorted(payloads.BUILDERS), sorted(SCHEMA))
        self.assertEqual(sorted(payloads.BUILDERS), registry.kinds())

    def test_every_builder_emits_exactly_its_schema(self):
        inv = self.invoice()
        made = {
            "quotation": {"document_id": self.doc},
            "ack": {"document_id": self.doc},
            "work_order": {},
            "invoice": {"order_ids": [self.order_a]},
            "packing_list": {"invoice_paper_id": inv["id"]},
            "coc": {"invoice_paper_id": inv["id"]},
            "test_cert": {"invoice_paper_id": inv["id"]},
            "bom": {},
        }
        for kind, opts in made.items():
            payload = payloads.build(self.conn, kind, self.order_a, opts)
            self.assertEqual(set(payload), SCHEMA[kind], kind)

    def test_no_field_is_ever_none(self):
        """A None would render as the template's default; a blank must be ''."""
        inv = self.invoice()
        for kind, opts in (("quotation", {"document_id": self.doc}),
                           ("ack", {}), ("work_order", {}),
                           ("invoice", {}),
                           ("packing_list", {"invoice_paper_id": inv["id"]}),
                           ("coc", {"invoice_paper_id": inv["id"]}),
                           ("test_cert", {"invoice_paper_id": inv["id"]}),
                           ("bom", {})):
            payload = payloads.build(self.conn, kind, self.order_a, opts)
            for key, value in payload.items():
                self.assertIsNotNone(value, f"{kind}.{key}")

    def test_customer_block_and_currency_default(self):
        payload = payloads.build(self.conn, "ack", self.order_a, {})
        self.assertEqual(payload["customer"]["country"], "USA")
        self.assertEqual(payload["customer"]["address_lines"],
                         ["8780, Technology Way", "Reno, NV 89521-5908"])
        self.assertEqual(payload["customer"]["contact"]["name"], "Cassie Halverson")
        self.assertEqual(payload["currency"], dict(payloads.USD))
        # CONVENTIONS §9-D: a British customer prints sterling, header and all
        uk = payloads.build(self.conn, "ack", self.order_uk, {})
        self.assertEqual(uk["currency"]["code"], "GBP")
        self.assertEqual(uk["currency"]["header"], "Prices (G.B.P.)")
        self.assertEqual(uk["currency_header"], "Prices (G.B.P.)")

    def test_unknown_kind_is_refused(self):
        with self.assertRaises(ValueError):
            payloads.build(self.conn, "delivery_note", self.order_a, {})


# ==========================================================================
# Quotation — the ledger owns the number
# ==========================================================================

class QuotationPaper(PapersBase):
    def test_number_and_lines_come_from_the_ledger(self):
        doc = quotations.get_doc(self.conn, self.doc)
        payload = payloads.build(self.conn, "quotation", self.order_a,
                                 {"document_id": self.doc})
        self.assertEqual(payload["number"], doc["doc_no"])
        self.assertEqual(payload["number"], "S01/AT/130826/1")
        self.assertEqual(payload["client_code"], "S01")
        self.assertEqual(payload["rfq_ref"], "Email")
        self.assertEqual(payload["items"][0]["code"], "TWB02000750")
        self.assertEqual(payload["items"][0]["qty"], 120000)
        self.assertEqual(payload["total"], doc["total"])
        self.assertEqual(payload["approved_by"], "Sumesh Garg")
        self.assertEqual(payload["validity_line"], registry.VALIDITY_LINE)

    def test_the_paper_reuses_the_ledger_number_it_never_takes_a_new_one(self):
        before = numbering.peek(self.conn, "qtn:S01")
        paper = self.make("quotation", document_id=self.doc)
        self.assertEqual(paper["paper_no"], "S01/AT/130826/1")
        self.assertEqual(paper["revision"], "")
        self.assertEqual(paper["document_id"], self.doc)
        self.assertEqual(numbering.peek(self.conn, "qtn:S01"), before)

    def test_a_quotation_paper_needs_its_ledger_row(self):
        with self.assertRaises(ValueError):
            self.make("quotation")

    def test_quantities_stay_numbers_the_grouping_is_the_templates_job(self):
        """CONVENTIONS §5: '1,20,000' is printed by the sheet, not stored."""
        payload = payloads.build(self.conn, "quotation", self.order_a,
                                 {"document_id": self.doc})
        self.assertEqual(payload["items"][0]["qty"], 120000)
        self.assertNotIsInstance(payload["items"][0]["qty"], str)


# ==========================================================================
# Acknowledgement — the ack and its work order are numbered together
# ==========================================================================

class AckNumbers(PapersBase):
    def test_ack_takes_its_reference_and_a_work_order_number_as_a_pair(self):
        wo_before = numbering.peek(self.conn, f"wo:{numbering.yy(date.today())}")
        ack = self.make("ack", document_id=self.doc)
        today = date.today()
        stamp = f"{today.day:02d}.{today.month:02d}.{numbering.yy(today)}"
        self.assertEqual(ack["paper_no"], f"S01.{stamp}.01")
        self.assertEqual(ack["payload"]["ack_ref"], ack["paper_no"])
        self.assertEqual(ack["payload"]["wo_no_long"],
                         f"S01.{stamp}.{wo_before}.{numbering.yy(today)}")
        # exactly ONE work-order serial per acknowledgement
        self.assertEqual(numbering.peek(self.conn, f"wo:{numbering.yy(today)}"),
                         wo_before + 1)

    def test_two_acks_on_the_same_day_are_01_and_02(self):
        first = self.make("ack")
        second = self.make("ack", order_id=self.order_b)
        self.assertTrue(first["paper_no"].endswith(".01"), first["paper_no"])
        self.assertTrue(second["paper_no"].endswith(".02"), second["paper_no"])
        firsts = first["payload"]["wo_no_long"].split(".")[-2]
        seconds = second["payload"]["wo_no_long"].split(".")[-2]
        self.assertEqual(int(seconds), int(firsts) + 1)

    def counters(self):
        today = date.today()
        return (numbering.peek(self.conn, f"ack:S01:{numbering.ddmmyy(today)}"),
                numbering.peek(self.conn, f"wo:{numbering.yy(today)}"))

    def test_a_failed_ack_gives_its_numbers_back(self):
        """The counter bump and the INSERT are one act (SOP-DESIGN §2).

        The failure is forced AFTER the pair has been taken — that is the case
        that matters: a rollback there must un-spend both serials, or the next
        acknowledgement skips a number the office will never account for.
        """
        before = self.counters()
        with mock.patch.object(service, "_now", side_effect=RuntimeError("disk full")):
            with self.assertRaises(RuntimeError):
                self.make("ack")
        self.assertEqual(self.counters(), before)
        self.assertEqual(service.list_papers(self.conn)["rows"], [])
        # and the numbers really were still there to take
        self.assertEqual(self.make("ack")["paper_no"].split(".")[-1], "01")
        self.assertEqual(self.counters(), (before[0] + 1, before[1] + 1))

    def test_an_ack_for_an_order_that_is_gone_takes_nothing(self):
        before = self.counters()
        with self.assertRaises(ValueError):
            service.create_paper(self.conn, "ack", 9999, {})
        self.assertEqual(self.counters(), before)

    def test_blocks_dates_terms_and_items_come_off_the_order(self):
        ack = self.make("ack", document_id=self.doc)
        p = ack["payload"]
        self.assertEqual(p["bill_to_lines"][0], "SELCO Products Company")
        self.assertEqual(p["bill_to_lines"][1], "8780, Technology Way")
        self.assertEqual(p["ship_to_lines"][1], "640, Maestro Drive, Suite 102")
        self.assertEqual(p["cust_po"], "PO03864")
        self.assertEqual(p["po_date_iso"], "2026-03-06")
        self.assertEqual(p["ship_date_iso"], "2026-09-16")
        self.assertEqual(p["payment_terms"], "N 45 by Wire Transfer")
        self.assertEqual(p["quotation_ref"], "S01/AT/130826/1")
        self.assertEqual(p["remittance_block"], registry.REMITTANCE_2026)
        self.assertEqual([i["code"] for i in p["items"]],
                         ["TWB02000750", "TPS-0353"])
        self.assertEqual(p["items"][0]["material"], "Naval Brass Grade 1")
        self.assertEqual(p["total"], round(600 * 0.675 + 300 * 0.32, 2))

    def test_payment_terms_fall_back_to_the_house_default(self):
        ack = payloads.build(self.conn, "ack", self.order_uk, {})
        self.assertEqual(ack["payment_terms"], "N 30 by Wire Transfer")

    def test_a_repeat_order_prints_repeat_po(self):
        """CONVENTIONS §7 — the Quotation Ref. may be a literal."""
        ack = self.make("ack", repeat_po=True)
        self.assertEqual(ack["payload"]["quotation_ref"], "Repeat PO")

    def test_the_ref_can_point_at_a_quotation_paper(self):
        qp = self.make("quotation", document_id=self.doc)
        ack = self.make("ack", quotation_paper_id=qp["id"])
        self.assertEqual(ack["payload"]["quotation_ref"], qp["paper_no"])


class WorkOrderPaper(PapersBase):
    def test_it_reuses_the_number_the_ack_reserved(self):
        ack = self.make("ack")
        short = service.short_from_long(ack["payload"]["wo_no_long"])
        before = numbering.peek(self.conn, f"wo:{numbering.yy(date.today())}")
        wo = self.make("work_order")
        self.assertEqual(wo["paper_no"], short)
        self.assertEqual(wo["payload"]["wo_no_short"], short)
        self.assertEqual(numbering.peek(self.conn, f"wo:{numbering.yy(date.today())}"),
                         before, "raising the work order must not burn a serial")

    def test_without_an_ack_it_takes_a_fresh_serial(self):
        before = numbering.peek(self.conn, f"wo:{numbering.yy(date.today())}")
        wo = self.make("work_order")
        self.assertEqual(wo["paper_no"], f"{before}/{numbering.yy(date.today())}")
        self.assertEqual(numbering.peek(self.conn, f"wo:{numbering.yy(date.today())}"),
                         before + 1)

    def test_items_read_like_the_shop_floor_sheet(self):
        wo = self.make("work_order")
        self.assertEqual(wo["payload"]["client_code"], "S01")
        self.assertEqual(wo["payload"]["cust_po"], "PO03864")
        self.assertEqual(wo["payload"]["items"][0], {
            "sno": 1, "part_no": "TWB02000750",
            "item": 'Thermowell, 1/4"NPT (P01)', "qty": 600,
            "material": "Naval Brass Grade 1", "marking": "-", "remarks": ""})


# ==========================================================================
# Invoice — one bill, many customer POs
# ==========================================================================

class InvoicePaper(PapersBase):
    def test_it_merges_two_orders_and_prints_the_po_per_line(self):
        inv = self.invoice(self.order_a, self.order_b)
        p = inv["payload"]
        self.assertEqual([b["po"] for b in p["buyer_po_block"]],
                         ["PO03864", "PO03956"])
        self.assertEqual([b["date_iso"] for b in p["buyer_po_block"]],
                         ["2026-03-06", "2026-03-27"])
        self.assertEqual([i["po"] for i in p["items"]],
                         ["PO03864", "PO03864", "PO03956"])
        self.assertEqual([i["sno"] for i in p["items"]], [1, 2, 3])
        self.assertEqual(p["items"][0]["code_desc"],
                         '1.TWB02000750,Thermowell, 1/4"NPT (P01), '
                         "Matl. Naval Brass Grade 1")
        self.assertEqual(p["items"][0]["rate"], "0.675")     # 3 dp (§5)
        self.assertEqual(p["items"][0]["amount"], "405.00")  # 2 dp
        self.assertEqual(p["items"][0]["net_wt"], "")        # weighed at packing

    def test_a_free_line_prints_a_dash_and_is_left_out_of_the_total(self):
        inv = self.invoice(self.order_a, self.order_b)
        p = inv["payload"]
        self.assertEqual(p["items"][2]["rate"], "-")
        self.assertEqual(p["items"][2]["amount"], "-")
        self.assertEqual(p["totals"]["amount"], "501.00")     # 405 + 96
        self.assertEqual(p["totals"]["qty"], 1000)
        self.assertEqual(p["amount_words"], "USD Five Hundred One Only")

    def test_hand_picked_lines(self):
        item_ids = [i["id"] for i in payloads.order_items(self.conn, self.order_a)]
        inv = self.invoice(self.order_a, item_ids=[item_ids[1]])
        self.assertEqual(len(inv["payload"]["items"]), 1)
        self.assertEqual(inv["payload"]["items"][0]["qty"], 300)

    def test_one_invoice_bills_one_customer(self):
        with self.assertRaises(ValueError):
            self.invoice(self.order_a, self.order_uk)

    def test_a_quotation_row_is_not_an_invoice(self):
        with self.assertRaises(ValueError):
            self.invoice(self.order_a, document_id=self.doc)

    def test_export_constants_and_addresses(self):
        p = self.invoice()["payload"]
        self.assertEqual(p["iec"], "0509008631")
        self.assertEqual(p["ad_code"], "0292085 / 2690009")
        self.assertEqual(p["hts_line"], registry.HTS_LINE)
        self.assertEqual(p["gsp_line"], registry.GSP_LINE)
        self.assertEqual(p["place_receipt"], "GHAZIABAD")
        self.assertEqual(p["port_loading"], "NEW DELHI")
        self.assertEqual(p["vessel"], "AIR")
        self.assertEqual(p["origin_country"], "INDIA")
        self.assertEqual(p["terms"], "DDU")
        self.assertEqual(p["marks_lines"], ["", "AS ADDRESS", ""])
        self.assertEqual(p["consignee_lines"][:2],
                         ["SELCO PRODUCTS COMPANY", "640, Maestro Drive, Suite 102"])
        self.assertEqual(p["buyer_lines"][:2],
                         ["SELCO PRODUCTS COMPANY", "8780, Technology Way"])

    def test_a_fresh_invoice_creates_the_matching_ledger_row(self):
        before = numbering.peek(self.conn, "inv:26-27")
        inv = self.invoice(self.order_a)
        self.assertEqual(inv["paper_no"], f"AT/EI/26-27/{before:03d}")
        self.assertIsNotNone(inv["document_id"])
        doc = quotations.get_doc(self.conn, inv["document_id"])
        self.assertEqual(doc["doc_no"], inv["paper_no"])
        self.assertEqual(doc["kind"], "invoice")
        self.assertEqual(doc["customer_id"], self.cust)
        self.assertEqual([(ln["qty"], ln["rate"]) for ln in doc["lines"]],
                         [(600.0, 0.675), (300.0, 0.32)])
        self.assertEqual(numbering.peek(self.conn, "inv:26-27"), before + 1)

    def test_an_existing_ledger_invoice_is_reused_not_renumbered(self):
        doc_id = quotations.create_doc(self.conn, "invoice", {
            "customer_id": self.cust, "order_id": self.order_a,
            "doc_date": "2026-08-20",
            "lines": [{"drawing_id": self.drg_a, "qty": 600, "unit": "EA",
                       "rate": 0.675}]})
        doc_no = quotations.get_doc(self.conn, doc_id)["doc_no"]
        before = numbering.peek(self.conn, "inv:26-27")
        inv = self.invoice(self.order_a, document_id=doc_id)
        self.assertEqual(inv["paper_no"], doc_no)
        self.assertEqual(inv["document_id"], doc_id)
        self.assertEqual(numbering.peek(self.conn, "inv:26-27"), before)


class PackingListPaper(PapersBase):
    def test_one_box_by_default_with_the_invoice_header(self):
        inv = self.invoice(self.order_a, self.order_b)
        pl = self.make("packing_list", invoice_paper_id=inv["id"])
        p = pl["payload"]
        self.assertEqual(pl["paper_no"], inv["paper_no"])       # same number (§3)
        self.assertEqual(p["invoice_no"], inv["payload"]["invoice_no"])
        self.assertEqual(p["buyer_po_block"], inv["payload"]["buyer_po_block"])
        self.assertEqual(p["consignee_lines"], inv["payload"]["consignee_lines"])
        self.assertEqual(len(p["boxes"]), 1)
        box = p["boxes"][0]
        self.assertEqual(box["box_label"], "Box No. 1")
        self.assertEqual((box["size"], box["net_wt"], box["gross_wt"]), ("", "", ""))
        self.assertEqual([i["sno"] for i in box["items"]], [1, 2, 3])
        self.assertEqual([i["code_desc"] for i in box["items"]],
                         [i["code_desc"] for i in inv["payload"]["items"]])
        self.assertEqual(p["totals"], {"qty": 1000, "net_wt": "", "gross_wt": ""})
        self.assertIn("Total Weight in Kgs", p["total_weight_line"])

    def test_it_needs_an_invoice(self):
        with self.assertRaises(ValueError):
            self.make("packing_list")
        wo = self.make("work_order")
        with self.assertRaises(ValueError):
            self.make("packing_list", invoice_paper_id=wo["id"])


class CocPaper(PapersBase):
    def test_every_field_is_derived_from_the_order_and_its_invoice(self):
        inv = self.invoice(self.order_a)
        coc = self.make("coc", invoice_paper_id=inv["id"])
        p = coc["payload"]
        self.assertEqual(p["customer_caps"], "SELCO PRODUCTS COMPANY")
        self.assertEqual(p["customer_short"], "SELCO")
        self.assertEqual(p["po"], "PO03864")
        self.assertEqual(p["invoice_no"], inv["payload"]["invoice_no"])
        self.assertEqual(p["invoice_date_iso"], inv["payload"]["date_iso"])
        self.assertEqual(p["date_shipped_iso"], inv["payload"]["date_iso"])
        self.assertEqual(p["part_desc"], "TWB02000750")
        self.assertEqual(p["material"], "Naval Brass Grade 1")
        self.assertEqual((p["plating"], p["finishing"]), ("NA", "NA"))
        self.assertEqual(p["qty_shipped"], 900)
        self.assertEqual(p["authenticator"], "Q.A. MANAGER")
        self.assertEqual(coc["paper_no"], f"COC-PO-03864-EI-"
                                          f"{inv['paper_no'].rsplit('/', 1)[1]}")

    def test_the_number_is_the_filename(self):
        """A COC has no counter — the document IS its identity (CONV §3), so
        the number the register holds and the file the office sends out must
        be the same string, not two spellings of it."""
        inv = self.invoice(self.order_a)
        coc = self.make("coc", invoice_paper_id=inv["id"])
        stem, _, ext = coc["filename"].rpartition(".")
        self.assertEqual(ext, "docx")
        self.assertEqual(coc["paper_no"], stem)
        self.assertEqual(coc["payload"]["number"], stem)
        self.assertEqual(coc["display_no"], stem)
        self.assertTrue(stem.startswith("COC-PO-"), stem)

    def test_a_certificate_without_a_po_says_so(self):
        order = orders.create_order(self.conn, {
            "customer_id": self.cust, "customer_po": "",
            "order_date": "2026-08-01", "due_date": "2026-09-01",
            "items": [{"drawing_id": self.drg_a, "qty": 10, "unit": "EA",
                       "rate": 5}]})
        inv = self.make("invoice", order_id=order)
        with self.assertRaises(ValueError) as e:
            self.make("coc", order_id=order, invoice_paper_id=inv["id"])
        self.assertIn("PO number", str(e.exception))

    def test_short_name_heuristic(self):
        self.assertEqual(payloads.customer_short("SELCO Products Company"), "SELCO")
        self.assertEqual(payloads.customer_short("Thermosense Ltd."), "THERMOSENSE")
        self.assertEqual(payloads.customer_short("East Coast Sensors"),
                         "EAST COAST SENSORS")
        self.assertEqual(payloads.customer_short(""), "")


class TestCertPaper(PapersBase):
    def test_one_line_per_item_and_heat_with_the_chemistry(self):
        inv = self.invoice(self.order_a)
        tc = self.make("test_cert", invoice_paper_id=inv["id"])
        p = tc["payload"]
        self.assertEqual(len(p["items"]), 4)            # 2 items x 2 heats
        self.assertEqual([i["heat_no"] for i in p["items"]],
                         ["H4443", "H4515", "H4443", "H4515"])
        first = p["items"][1]
        self.assertEqual(first["item"], "TWB02000750")
        self.assertEqual(first["size"], '1-3/8" Hex.')
        self.assertEqual(first["qty"], 600)
        self.assertEqual(first["component"], "-")
        self.assertEqual(first["material"], "A479-316L")
        self.assertEqual(first["chem"]["Cr"], 16.89)
        self.assertEqual(first["chem"]["C"], 0.022)

    def test_elements_beyond_the_eight_columns_fill_the_spares(self):
        inv = self.invoice(self.order_a)
        p = self.make("test_cert", invoice_paper_id=inv["id"])["payload"]
        self.assertEqual(p["extra_elements"], ["Cu", "N"])
        h4515 = next(i for i in p["items"] if i["heat_no"] == "H4515")
        h4443 = next(i for i in p["items"] if i["heat_no"] == "H4443")
        self.assertEqual((h4515["spare_1"], h4515["spare_2"]), (0.31, 0.052))
        self.assertEqual(h4515["spare_3"], "-")
        # the heat WITHOUT those elements leaves the spare columns dashed
        self.assertEqual([h4443[f"spare_{n}"] for n in range(1, 6)], ["-"] * 5)

    def test_the_number_is_derived_from_the_po_and_the_invoice(self):
        inv = self.invoice(self.order_a)
        tc = self.make("test_cert", invoice_paper_id=inv["id"])
        expected = numbering.tc_no("PO03864", inv["payload"]["invoice_no"])
        self.assertEqual(tc["paper_no"], expected)
        self.assertEqual(tc["payload"]["cert_no"], expected)
        self.assertEqual(tc["payload"]["customer_line"],
                         "SELCO Products Company, USA")
        self.assertEqual(tc["payload"]["po_date_iso"], "2026-03-06")

    def test_an_item_with_no_heat_issued_still_appears(self):
        inv = self.invoice(self.order_b)
        p = self.make("test_cert", self.order_b,
                      invoice_paper_id=inv["id"])["payload"]
        self.assertEqual(len(p["items"]), 1)
        self.assertEqual(p["items"][0]["heat_no"], "")
        self.assertEqual(p["items"][0]["material"], "A479-316L")   # off the drawing


class BomPaper(PapersBase):
    def test_in_house_and_outsourced_lines(self):
        bom = self.make("bom")
        p = bom["payload"]
        self.assertEqual(p["bom_no"], "AT/BOM/26-27/001")
        self.assertEqual(bom["paper_no"], p["bom_no"])
        by_source = {i["source"]: i for i in p["items"]}
        self.assertEqual(sorted(by_source), ["In-House", "Outsourced - V01"])
        in_house = by_source["In-House"]
        self.assertEqual(in_house["heat_or_os"], "H4515")
        self.assertEqual(in_house["part_no"], "TWB02000750")
        self.assertEqual(in_house["size"], '1-3/8" Hex.')
        self.assertEqual(in_house["qty_per"], 0.02)
        self.assertEqual(in_house["total_qty"], 12)          # 0.02 x 600
        self.assertEqual(in_house["unit"], "rod")
        bought = by_source["Outsourced - V01"]
        self.assertEqual(bought["heat_or_os"], "OS-0001")
        self.assertEqual(bought["material"], "HDPE")
        self.assertEqual(bought["total_qty"], 600)

    def test_header_names_the_order_and_its_work_order(self):
        self.make("ack")
        wo = self.make("work_order")
        p = self.make("bom")["payload"]
        self.assertEqual(p["customer_line"], "SELCO Products Company, USA")
        self.assertEqual(p["po"], "PO03864")
        self.assertEqual(p["po_date_iso"], "2026-03-06")
        self.assertEqual(p["wo_no"], wo["paper_no"])
        self.assertEqual(p["part_assy"], 'Thermowell, 1/4"NPT (P01)')

    def test_the_ack_alone_already_names_the_work_order(self):
        ack = self.make("ack")
        p = self.make("bom")["payload"]
        self.assertEqual(p["wo_no"],
                         service.short_from_long(ack["payload"]["wo_no_long"]))


# ==========================================================================
# Amount in words (CONVENTIONS §5)
# ==========================================================================

class AmountInWords(unittest.TestCase):
    def test_the_reference_invoice_total(self):
        self.assertEqual(payloads.amount_in_words(3858, "USD"),
                         "USD Three Thousand Eight Hundred Fifty Eight Only")

    def test_cents_appear_only_when_there_are_some(self):
        self.assertEqual(payloads.amount_in_words(3858.12, "USD"),
                         "USD Three Thousand Eight Hundred Fifty Eight and "
                         "Cents Twelve Only")
        self.assertEqual(payloads.amount_in_words(3858.00, "USD"),
                         "USD Three Thousand Eight Hundred Fifty Eight Only")

    def test_international_grouping_and_other_currencies(self):
        self.assertEqual(payloads.amount_in_words(120000, "USD"),
                         "USD One Hundred Twenty Thousand Only")
        self.assertEqual(payloads.amount_in_words(1234567, "USD"),
                         "USD One Million Two Hundred Thirty Four Thousand Five "
                         "Hundred Sixty Seven Only")
        self.assertEqual(payloads.amount_in_words(64080, "GBP"),
                         "GBP Sixty Four Thousand Eighty Only")
        self.assertEqual(payloads.amount_in_words(10.05, "GBP"),
                         "GBP Ten and Pence Five Only")

    def test_edges(self):
        self.assertEqual(payloads.amount_in_words(0, "USD"), "USD Zero Only")
        self.assertEqual(payloads.amount_in_words(15, "USD"), "USD Fifteen Only")
        self.assertEqual(payloads.amount_in_words(100, "USD"),
                         "USD One Hundred Only")
        self.assertEqual(payloads.amount_in_words("", "USD"), "USD Zero Only")


# ==========================================================================
# Lifecycle: draft -> final -> revision
# ==========================================================================

class Lifecycle(PapersBase):
    def test_a_new_paper_is_a_draft_that_remembers_its_options(self):
        inv = self.invoice(self.order_a, self.order_b)
        self.assertEqual(inv["status"], "draft")
        self.assertEqual(inv["opts"], {"order_ids": [self.order_a, self.order_b]})
        raw = json.loads(self.conn.execute(
            "SELECT payload FROM paper WHERE id=?", (inv["id"],)).fetchone()["payload"])
        self.assertEqual(raw["_opts"]["order_ids"], [self.order_a, self.order_b])
        self.assertNotIn("_opts", inv["payload"])        # meta is not a field

    def test_a_draft_is_edited_freely_and_the_edit_survives(self):
        wo = self.make("work_order")
        payload = dict(wo["payload"])
        payload["cust_po"] = "PO-CORRECTED"
        payload["items"][0]["marking"] = "BATCH 7"
        saved = service.update_payload(self.conn, wo["id"], payload)
        self.assertEqual(saved["payload"]["cust_po"], "PO-CORRECTED")
        self.assertEqual(saved["payload"]["items"][0]["marking"], "BATCH 7")
        self.assertEqual(service.get_paper(self.conn, wo["id"])["opts"], {})

    def test_an_edit_must_be_the_whole_schema(self):
        wo = self.make("work_order")
        short = {k: v for k, v in wo["payload"].items() if k != "cust_po"}
        with self.assertRaises(ValueError):
            service.update_payload(self.conn, wo["id"], short)
        extra = dict(wo["payload"], invented_field="x")
        with self.assertRaises(ValueError):
            service.update_payload(self.conn, wo["id"], extra)

    def test_a_final_paper_is_frozen(self):
        wo = self.make("work_order")
        service.set_status(self.conn, wo["id"], "final")
        with self.assertRaises(ValueError) as e:
            service.update_payload(self.conn, wo["id"], wo["payload"])
        self.assertIn("frozen", str(e.exception))
        with self.assertRaises(ValueError):
            service.refill(self.conn, wo["id"])
        with self.assertRaises(ValueError):
            service.delete_paper(self.conn, wo["id"])

    def test_refill_restores_what_the_order_says_and_keeps_the_number(self):
        wo = self.make("work_order")
        payload = dict(wo["payload"], cust_po="TYPO")
        service.update_payload(self.conn, wo["id"], payload)
        again = service.refill(self.conn, wo["id"])
        self.assertEqual(again["payload"]["cust_po"], "PO03864")
        self.assertEqual(again["paper_no"], wo["paper_no"])
        self.assertEqual(again["payload"]["wo_no_short"], wo["paper_no"])

    def test_refill_replays_the_original_options(self):
        inv = self.invoice(self.order_a, self.order_b)
        # a hand edit must not lose the app's own bookkeeping
        edited = service.update_payload(self.conn, inv["id"],
                                        dict(inv["payload"], terms="CIF"))
        self.assertEqual(edited["opts"], inv["opts"])
        again = service.refill(self.conn, inv["id"])
        self.assertEqual(again["payload"]["terms"], "DDU")
        self.assertEqual(len(again["payload"]["items"]), 3)
        self.assertEqual(again["payload"]["invoice_no"], inv["paper_no"])
        self.assertEqual(again["opts"], inv["opts"])

    def test_status_transitions(self):
        wo = self.make("work_order")
        self.assertEqual(service.set_status(self.conn, wo["id"], "final")["status"],
                         "final")
        self.assertEqual(service.set_status(self.conn, wo["id"], "sent")["status"],
                         "sent")
        self.assertEqual(service.set_status(self.conn, wo["id"], "void")["status"],
                         "void")
        for bad in ("draft", "final", "sent", "superseded"):
            with self.assertRaises(ValueError, msg=bad):
                service.set_status(self.conn, wo["id"], bad)
        other = self.make("bom")
        with self.assertRaises(ValueError):
            service.set_status(self.conn, other["id"], "sent")      # draft -> sent
        with self.assertRaises(ValueError):
            service.set_status(self.conn, other["id"], "posted")    # unknown
        self.assertEqual(service.set_status(self.conn, other["id"], "void")["status"],
                         "void")

    def test_only_a_draft_is_deleted(self):
        wo = self.make("work_order")
        service.delete_paper(self.conn, wo["id"])
        with self.assertRaises(ValueError):
            service.get_paper(self.conn, wo["id"])

    def test_revise_chains_letters_and_supersedes_the_parent(self):
        bom = self.make("bom")
        with self.assertRaises(ValueError):
            service.revise(self.conn, bom["id"])            # a draft is just edited
        service.set_status(self.conn, bom["id"], "final")
        rev_a = service.revise(self.conn, bom["id"])
        self.assertEqual((rev_a["revision"], rev_a["status"]), ("A", "draft"))
        self.assertEqual(rev_a["paper_no"], bom["paper_no"])
        self.assertEqual(rev_a["based_on_id"], bom["id"])
        self.assertEqual(rev_a["display_no"], f"{bom['paper_no']} Rev-A")
        self.assertEqual(service.get_paper(self.conn, bom["id"])["status"],
                         "superseded")
        with self.assertRaises(ValueError):
            service.revise(self.conn, bom["id"])            # not the tip any more
        service.set_status(self.conn, rev_a["id"], "final")
        rev_b = service.revise(self.conn, rev_a["id"])
        self.assertEqual(rev_b["revision"], "B")
        self.assertEqual([r["revision"] for r in rev_b["revisions"]], ["", "A", "B"])

    def test_revision_letters(self):
        self.assertEqual(service.next_revision(""), "A")
        self.assertEqual(service.next_revision("A"), "B")
        self.assertEqual(service.next_revision("Z"), "AA")

    def test_a_quotation_revises_its_ledger_row_with_it(self):
        paper = self.make("quotation", document_id=self.doc)
        service.set_status(self.conn, paper["id"], "final")
        rev = service.revise(self.conn, paper["id"])
        self.assertNotEqual(rev["document_id"], self.doc)
        doc = quotations.get_doc(self.conn, rev["document_id"])
        self.assertEqual(doc["doc_no"], f"{paper['paper_no']} Rev-A")
        self.assertEqual(rev["payload"]["number"], doc["doc_no"])
        self.assertEqual(rev["display_no"], doc["doc_no"])
        self.assertIsNotNone(quotations.get_doc(self.conn, self.doc)["superseded_by"])
        self.assertEqual(len(doc["lines"]), 1)

    def test_the_list_view(self):
        self.make("bom")
        inv = self.invoice(self.order_a)
        self.make("work_order", order_id=self.order_b)
        rows = service.list_papers(self.conn)["rows"]
        self.assertEqual(len(rows), 3)
        self.assertEqual(set(rows[0]),
                         {"id", "kind", "paper_no", "revision", "status",
                          "paper_date", "updated_at", "order_id", "document_id",
                          "order_no", "customer_name", "label", "display_no"})
        self.assertEqual(rows[0]["customer_name"], "SELCO Products Company")
        self.assertEqual(len(service.list_papers(self.conn, kind="bom")["rows"]), 1)
        self.assertEqual(
            len(service.list_papers(self.conn, kind="bom,invoice")["rows"]), 2)
        self.assertEqual(
            len(service.list_papers(self.conn, order_id=self.order_b)["rows"]), 1)
        self.assertEqual(len(service.list_papers(self.conn, status="draft")["rows"]), 3)
        self.assertEqual(
            len(service.list_papers(self.conn, q=inv["paper_no"])["rows"]), 1)
        self.assertEqual(service.list_papers(self.conn)["counts"]["bom"], 1)


# ==========================================================================
# Rendering
# ==========================================================================

class Rendering(PapersBase):
    def all_kinds(self):
        inv = self.invoice(self.order_a, self.order_b)
        return {
            "quotation": self.make("quotation", document_id=self.doc),
            "ack": self.make("ack", document_id=self.doc),
            "work_order": self.make("work_order"),
            "invoice": inv,
            "packing_list": self.make("packing_list", invoice_paper_id=inv["id"]),
            "coc": self.make("coc", invoice_paper_id=inv["id"]),
            "test_cert": self.make("test_cert", invoice_paper_id=inv["id"]),
            "bom": self.make("bom"),
        }

    def test_every_kind_renders_to_a_real_file_with_no_tokens_left(self):
        for kind, paper in self.all_kinds().items():
            data, filename, mime = service.render_paper(self.conn, paper["id"])
            self.assertGreater(len(data), 4000, kind)
            self.assertTrue(data.startswith(b"PK"), kind)
            self.assertEqual(mime, service.MIME[registry.entry(kind)["format"]], kind)
            self.assertEqual(filename, paper["filename"], kind)
            path = Path(self.tmp.name) / filename
            path.write_bytes(data)
            with zipfile.ZipFile(path) as z:
                for name in z.namelist():
                    if name.endswith(".xml"):
                        self.assertNotIn(b"{{", z.read(name), f"{kind}:{name}")

    def test_filenames_mirror_the_companys_own(self):
        made = self.all_kinds()
        serial = made["invoice"]["paper_no"].rsplit("/", 1)[1]
        self.assertEqual(made["invoice"]["filename"],
                         f"Apex-Export Invoice-EI-{serial}.docx")
        self.assertEqual(made["packing_list"]["filename"],
                         f"Apex-Export Packing List-EI-{serial}.docx")
        self.assertEqual(made["coc"]["filename"],
                         f"COC-PO-03864-EI-{serial}.docx")
        self.assertEqual(made["test_cert"]["filename"],
                         f"Test Certificate PO03864-EI-{serial}.xlsx")
        self.assertEqual(made["bom"]["filename"], "Apex-BOM-26-27-001.xlsx")
        self.assertEqual(made["work_order"]["filename"],
                         f"Apex-Work-Order-{made['work_order']['paper_no'].replace('/', '-')}.xlsx")

    def test_a_revision_prints_its_marker_in_the_number_and_the_filename(self):
        bom = self.make("bom")
        service.set_status(self.conn, bom["id"], "final")
        rev = service.revise(self.conn, bom["id"])
        data, filename, _ = service.render_paper(self.conn, rev["id"])
        self.assertEqual(filename, "Apex-BOM-26-27-001 Rev-A.xlsx")
        self.assertGreater(len(data), 4000)
        stored = json.loads(self.conn.execute(
            "SELECT payload FROM paper WHERE id=?", (rev["id"],)).fetchone()["payload"])
        self.assertEqual(stored["bom_no"], "AT/BOM/26-27/001 Rev-A")


class OrderEmbed(PapersBase):
    def test_get_order_lists_the_papers_newest_first(self):
        self.assertEqual(orders.get_order(self.conn, self.order_a)["papers"], [])
        ack = self.make("ack")
        bom = self.make("bom")
        papers = orders.get_order(self.conn, self.order_a)["papers"]
        self.assertEqual([p["id"] for p in papers], [bom["id"], ack["id"]])
        self.assertEqual(set(papers[0]),
                         {"id", "kind", "paper_no", "revision", "status",
                          "paper_date"})
        self.assertEqual(papers[0]["kind"], "bom")
        self.assertEqual(papers[1]["paper_no"], ack["paper_no"])
        self.assertEqual(orders.get_order(self.conn, self.order_b)["papers"], [])


# ==========================================================================
# The HTTP surface (no logic here — these pin the shape)
# ==========================================================================

class RouterSurface(unittest.TestCase):
    def test_routes_and_their_order(self):
        paths = [(r.path, sorted(r.methods - {"HEAD"}))
                 for r in router.router.routes]
        self.assertEqual(paths, [
            ("/api/papers/refs", ["GET"]),
            ("/api/papers", ["GET"]),
            ("/api/papers", ["POST"]),
            ("/api/papers/{paper_id}", ["GET"]),
            ("/api/papers/{paper_id}", ["PUT"]),
            ("/api/papers/{paper_id}/refill", ["POST"]),
            ("/api/papers/{paper_id}/status", ["POST"]),
            ("/api/papers/{paper_id}/revise", ["POST"]),
            ("/api/papers/{paper_id}/file", ["GET"]),
            ("/api/papers/{paper_id}", ["DELETE"]),
        ])
        literal = paths.index(("/api/papers/refs", ["GET"]))
        wildcard = paths.index(("/api/papers/{paper_id}", ["GET"]))
        self.assertLess(literal, wildcard, "/refs would be read as a paper id")

    def test_every_option_a_builder_understands_is_declared_on_the_model(self):
        """The silent-drop trap: an undeclared field is dropped, not refused."""
        declared = set(router.PaperOptsIn.model_fields)
        used = {o for spec in service.KIND_OPTS.values()
                for o in spec["required"] + spec["optional"]}
        self.assertTrue(used <= declared, sorted(used - declared))
        self.assertEqual(declared, used | {"based_on_id"})
        self.assertEqual(set(router.PaperIn.model_fields), {"kind", "order_id", "opts"})
        self.assertEqual(set(router.PayloadIn.model_fields), {"payload"})
        self.assertEqual(set(router.StatusIn.model_fields), {"status"})

    def test_refs_tells_the_ui_what_each_kind_needs(self):
        refs = router.refs()
        self.assertEqual([k["kind"] for k in refs["kinds"]], list(payloads.KINDS))
        by_kind = {k["kind"]: k for k in refs["kinds"]}
        self.assertEqual(by_kind["quotation"]["requires"], ["document_id"])
        self.assertEqual(by_kind["coc"]["requires"], ["invoice_paper_id"])
        self.assertEqual(by_kind["bom"]["requires"], [])
        self.assertEqual(by_kind["ack"]["label"], "PO acknowledgement")
        self.assertEqual(by_kind["invoice"]["format"], "docx")
        self.assertIn("draft", refs["statuses"])

    def test_the_guard_is_the_four_sop_module_keys(self):
        """SOP-DESIGN §7/§10: any ONE of the four SOP grants opens /api/papers.

        Wave 3 registered the keys, so the fallback the router shipped with is
        gone; this pins that it stays gone.
        """
        from backend.core.registry import ALL_KEYS
        self.assertEqual(router.SOP_KEYS,
                         ("acks", "production_docs", "shipping_docs", "quality_docs"))
        for key in router.SOP_KEYS:
            self.assertIn(key, ALL_KEYS)
        self.assertEqual(router.GUARD_KEYS, router.SOP_KEYS)
        self.assertFalse(router.GUARD_IS_FALLBACK)
        self.assertNotIn("orders", router.GUARD_KEYS)

    def test_the_router_is_mounted_on_the_app(self):
        """A router nobody includes is a router nobody can call."""
        from backend.main import app
        served = set(app.openapi()["paths"])
        for path in ("/api/papers", "/api/papers/refs", "/api/papers/{paper_id}",
                     "/api/papers/{paper_id}/file", "/api/papers/{paper_id}/refill",
                     "/api/papers/{paper_id}/status", "/api/papers/{paper_id}/revise"):
            self.assertIn(path, served)


# --------------------------------------------------------------------------- #
class LauncherRegistry(unittest.TestCase):
    """The homepage tiles read like the SOP, in this exact order (§7)."""

    SOP_ORDER = ["orders", "quotations", "acks", "production_docs",
                 "shipping_docs", "quality_docs", "outsourcing", "inventory",
                 "parts", "customers", "employees", "salary", "settings"]

    def test_the_tiles_are_in_sop_order(self):
        from backend.core.registry import ALL_KEYS, MODULES
        self.assertEqual(ALL_KEYS, self.SOP_ORDER)
        self.assertEqual([m["key"] for m in MODULES], self.SOP_ORDER)

    def test_the_four_sop_tiles_open_papers_pre_filtered(self):
        from backend.core.registry import MODULES
        by_key = {m["key"]: m for m in MODULES}
        want = {
            "acks": ("PO Acknowledgements", "/papers/?kind=ack"),
            "production_docs": ("Production — WO & BOM",
                                "/papers/?kind=work_order,bom"),
            "shipping_docs": ("Shipping — Invoice & Packing",
                              "/papers/?kind=invoice,packing_list"),
            "quality_docs": ("Quality — COC & Test Certs",
                             "/papers/?kind=coc,test_cert"),
        }
        for key, (label, path) in want.items():
            entry = by_key[key]
            self.assertEqual(entry["label"], label)
            self.assertEqual(entry["path"], path)
            self.assertTrue(entry["built"])
            self.assertTrue(entry["icon"].strip(), f"{key} needs a glyph")
            self.assertTrue(entry["desc"].strip(), f"{key} needs a description")
        # every kind the papers engine knows is reachable from some tile
        filtered = {k for _, path in want.values()
                    for k in path.split("kind=")[1].split(",")}
        self.assertEqual(filtered | {"quotation"}, set(payloads.KINDS))

    def test_the_older_tiles_kept_their_identity(self):
        """Reordering must not have rewritten anything else."""
        from backend.core.registry import MODULES
        by_key = {m["key"]: m for m in MODULES}
        for key, label, path in (
                ("orders", "Order Tracking", "/orders/"),
                ("quotations", "Quotations & Invoices", "/quotations/"),
                ("outsourcing", "Outsourcing", "/outsourcing/"),
                ("inventory", "Raw Material Inventory", "/inventory/"),
                ("parts", "Parts & Pricing", "/parts/"),
                ("customers", "Customers", "/customers/"),
                ("employees", "Employee Management", "/employees/"),
                ("salary", "Salary & Attendance", "/payroll/"),
                ("settings", "Settings", "/settings/")):
            self.assertEqual(by_key[key]["label"], label)
            self.assertEqual(by_key[key]["path"], path)

    def test_the_papers_page_exists(self):
        page = paths.frontend_dir() / "papers"
        self.assertTrue((page / "index.html").is_file())
        self.assertTrue((page / "papers.js").is_file())

    def test_a_tile_path_with_a_querystring_survives_the_shell(self):
        """The shell navigates with the path verbatim — no split, no encode."""
        shell = (paths.frontend_dir() / "shell" / "shell.js").read_text("utf-8")
        self.assertIn("window.location.href = m.path", shell)
        home = (paths.frontend_dir() / "index.html").read_text("utf-8")
        self.assertIn(':href="m.built && m.path ? m.path : null"', home)


# --------------------------------------------------------------------------- #
class Screens(unittest.TestCase):
    """The surfaces wave 3 built, pinned so a refactor can't quietly drop one.

    Cheap string checks on purpose — the behaviour is proved by the E2E walk;
    what this guards is that the wiring between pages still EXISTS (a renamed
    helper or a dropped deep-link param is invisible until someone clicks it).
    """

    def page(self, *parts):
        return (paths.frontend_dir().joinpath(*parts)).read_text("utf-8")

    def test_the_papers_workspace_reads_its_deep_links(self):
        js = self.page("papers", "papers.js")
        for param in ('qs.get("kind")', 'qs.get("open")', 'qs.get("new")',
                      'qs.get("order")'):
            self.assertIn(param, js)
        self.assertIn("window.history.replaceState", js)
        # any ONE of the four grants opens it — same set as the backend guard
        self.assertIn('PAPER_KEYS = ["acks", "production_docs", "shipping_docs", '
                      '"quality_docs"]', js)
        self.assertEqual(sorted(router.SOP_KEYS),
                         sorted(["acks", "production_docs", "shipping_docs",
                                 "quality_docs"]))

    def test_the_papers_editor_covers_every_kind(self):
        html = self.page("papers", "index.html")
        for kind in payloads.KINDS:
            self.assertIn(f"'{kind}'", html, f"no editor branch for {kind}")
        # a full page, never a modal (owner brief)
        self.assertNotIn("bg-slate-900/50", html)

    def test_the_papers_editor_keeps_numbers_numeric(self):
        """A payload string in a money cell writes TEXT into the spreadsheet."""
        js = self.page("papers", "papers.js")
        self.assertIn("NUM_FIELDS", js)
        for kind in payloads.KINDS:
            self.assertIn(f"{kind}:", js.split("NUM_FIELDS")[1][:900],
                          f"{kind} has no numeric-field spec")

    def test_the_order_pipeline_strip_links_to_papers(self):
        js = self.page("orders", "orders.js")
        html = self.page("orders", "index.html")
        self.assertIn("stagePaperKinds", js)
        strip = js.split("stagePaperKinds")[1][:400]
        for stage, kind in (("quote", "quotation"), ("po", "ack"),
                            ("production", "work_order"), ("qc", "coc"),
                            ("dispatch", "invoice")):
            self.assertIn(f"{stage}:", strip)
            self.assertIn(kind, strip)
        self.assertIn("'/papers/?open=' + p.id", html)
        self.assertIn("'/papers/?new=' + k + '&order=' + detail.id", html)

    def test_the_order_intake_card_uploads_and_lists(self):
        js = self.page("orders", "orders.js")
        html = self.page("orders", "index.html")
        self.assertIn("/attachments", js)
        self.assertIn("uploadAttachments", js)
        self.assertIn("removeAttachment", js)
        self.assertIn("'/api/orders/attachments/' + a.id", html)
        self.assertIn("'/api/parts/files/' + f.id", html)   # drawings on the order
        self.assertIn('{ k: "intake"', js)

    def test_the_widest_grids_scroll_instead_of_clipping(self):
        """21 analysis columns in 1100px showed '0.0' where the heat says
        0.021 — a wide grid scrolls inside its frame (UI-STYLE §2)."""
        html = self.page("papers", "index.html")
        for grid in ("min-width:1560px", "min-width:1320px"):   # test cert, BOM
            self.assertIn(grid, html)
        self.assertEqual(html.count("overflow-x-auto"), html.count("<table"))

    def test_a_three_decimal_rate_is_shown_with_three_decimals(self):
        """money() caps at 2dp; a unit rate needs 3 or £0.534 reads £0.53."""
        js = self.page("quotations", "quotations.js")
        html = self.page("quotations", "index.html")
        self.assertIn("maximumFractionDigits: 3", js)
        self.assertIn('x-text="qiRate(l.rate)"', html)
        self.assertIn('step="0.001"', html)

    def test_quotations_can_revise_and_export(self):
        js = self.page("quotations", "quotations.js")
        html = self.page("quotations", "index.html")
        self.assertIn("/revise", js)
        self.assertIn("exportPaper", js)
        self.assertIn("/api/papers?kind=", js)          # look before you create
        self.assertIn("d.superseded_by", html)          # dimmed + amber chip
        self.assertIn("revRail", html)

    def test_settings_edits_the_document_counters(self):
        js = self.page("settings", "settings.js")
        html = self.page("settings", "index.html")
        self.assertIn("/api/settings/numbering", js)
        self.assertIn("saveCounter", js)
        self.assertIn("Counters follow the paperwork", html)

    def test_customers_carries_country_and_fax(self):
        html = self.page("customers", "index.html")
        js = self.page("customers", "customers.js")
        self.assertIn('x-model="form.country"', html)
        self.assertIn('x-model="contact.fax"', html)
        self.assertIn("country:", js)
        self.assertIn("fax:", js)


if __name__ == "__main__":
    unittest.main()
