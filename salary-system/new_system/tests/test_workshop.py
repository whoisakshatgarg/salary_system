"""Spec for the workshop modules: Settings, Customers, Parts & Pricing, Orders."""

import os
import sys
import tempfile
import unittest
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend.core import db, numbering  # noqa: E402
from backend.modules import (customers, inventory, orders, parts, quotations,  # noqa: E402
                             settings)

ADMIN = {"username": "admin", "role": "admin"}


class WorkshopBase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        os.environ["SALARY_DATA_DIR"] = self.tmp.name
        self.db_path = Path(self.tmp.name) / "test.db"
        db.init_db(self.db_path)
        settings.ensure_defaults(self.db_path)
        self.conn = db.connect(self.db_path)
        self.cust = customers.save_customer(self.conn, {"name": "Acme Pumps",
                                                        "gstin": "07abcde1234f1z5"})

    def tearDown(self):
        self.conn.close()
        os.environ.pop("SALARY_DATA_DIR", None)
        self.tmp.cleanup()

    def drawing(self, **kw):
        data = {"drawing_no": "DRG-100", "revision": "A", "customer_id": self.cust,
                "description": "Pump shaft", "material_class": "Steel",
                "grade": "EN8", "unit": "Nos"}
        data.update(kw)
        return parts.save_drawing(self.conn, data)

    def order(self, **kw):
        data = {"customer_id": self.cust, "stage": "po",
                "order_date": "2026-08-14",
                "items": [{"drawing_id": None, "description": "Shaft", "qty": 10,
                           "unit": "Nos", "rate": 250}]}
        data.update(kw)
        return orders.create_order(self.conn, data)


class SettingsSpec(WorkshopBase):
    def test_defaults_seeded(self):
        self.assertIn("kg", settings.units(self.conn))
        self.assertIn("Nos", settings.units(self.conn))
        ops = {o["name"]: o["rate_per_hour"] for o in settings.operations(self.conn)}
        self.assertIn("Turning", ops)
        self.assertGreater(ops["CNC Milling"], 0)

    def test_fy_label(self):
        self.assertEqual(settings.fy_label(date(2026, 8, 14)), "26-27")
        self.assertEqual(settings.fy_label(date(2027, 2, 1)), "26-27")
        self.assertEqual(settings.fy_label(date(2027, 4, 1)), "27-28")

    def test_render_order_no(self):
        self.assertEqual(settings.render_order_no("ORD-{FY}-{SEQ}", date(2026, 8, 14), 7),
                         "ORD-26-27-007")
        self.assertEqual(settings.render_order_no("{YYYY}/{SEQ}", date(2026, 8, 14), 123),
                         "2026/123")


class CustomersSpec(WorkshopBase):
    def test_crud_and_contacts(self):
        with self.assertRaises(ValueError):     # duplicate name
            customers.save_customer(self.conn, {"name": "Acme Pumps"})
        customers.save_customer(self.conn, {"name": "Acme Pumps Ltd",
                                            "gstin": "07abcde1234f1z5",
                                            "payment_terms": "30 days"}, self.cust)
        c = customers.get_customer(self.conn, self.cust)
        self.assertEqual(c["payment_terms"], "30 days")
        self.assertEqual(c["gstin"], "07ABCDE1234F1Z5")   # upper-cased
        c = customers.add_contact(self.conn, self.cust, {"name": "Ravi", "phone": "98"})
        self.assertEqual(len(c["contacts"]), 1)
        c = customers.delete_contact(self.conn, c["contacts"][0]["id"])
        self.assertEqual(c["contacts"], [])

    def test_delete_guard(self):
        self.order()
        with self.assertRaises(ValueError):
            customers.delete_customer(self.conn, self.cust)
        clean = customers.save_customer(self.conn, {"name": "No Refs"})
        customers.delete_customer(self.conn, clean)   # fine


class PartsSpec(WorkshopBase):
    def test_drawing_crud_and_revise(self):
        did = self.drawing()
        with self.assertRaises(ValueError):     # duplicate no+rev
            self.drawing()
        rev_id = parts.revise_drawing(self.conn, did, "B")
        d = parts.get_drawing(self.conn, rev_id)
        self.assertEqual(d["revision"], "B")
        self.assertEqual(len(d["revisions"]), 2)

    def test_rate_history(self):
        did = self.drawing()
        with self.assertRaises(ValueError):
            parts.add_rate(self.conn, did, {"kind": "guessed", "rate": 100})
        with self.assertRaises(ValueError):
            parts.add_rate(self.conn, did, {"kind": "quoted", "rate": 0})
        d = parts.add_rate(self.conn, did, {"kind": "quoted", "rate": 260,
                                            "rate_date": "2026-08-01"})
        d = parts.add_rate(self.conn, did, {"kind": "agreed", "rate": 250,
                                            "rate_date": "2026-08-10"})
        self.assertEqual([r["kind"] for r in d["rates"]], ["agreed", "quoted"])
        rows = parts.list_drawings(self.conn)
        self.assertEqual(rows[0]["latest_rate"], 250)

    def test_costing_math(self):
        did = self.drawing()
        d = parts.save_costing(self.conn, did, {
            "material_cost": 80, "margin_pct": 20,
            "ops": [{"operation": "Turning", "minutes": 12, "rate_per_hour": 400},
                    {"operation": "Milling", "minutes": 6, "rate_per_hour": 500}]})
        c = d["costings"][0]
        self.assertEqual(c["ops"][0]["cost"], 80.0)     # 12/60*400
        self.assertEqual(c["ops"][1]["cost"], 50.0)     # 6/60*500
        self.assertEqual(c["subtotal"], 210.0)          # 130 + 80 material
        self.assertEqual(c["total"], 252.0)             # +20%
        d = parts.costing_to_rate(self.conn, c["id"], "agreed")
        self.assertEqual(d["rates"][0]["rate"], 252.0)

    def test_costing_validation(self):
        did = self.drawing()
        with self.assertRaises(ValueError):     # nothing costed
            parts.save_costing(self.conn, did, {"ops": []})
        with self.assertRaises(ValueError):     # zero minutes
            parts.save_costing(self.conn, did, {
                "ops": [{"operation": "Turning", "minutes": 0, "rate_per_hour": 400}]})

    def test_files_atomicity(self):
        from backend.core import paths
        did = self.drawing()
        files = parts.save_files(self.conn, did, [("d.pdf", "application/pdf", b"%PDF")])
        self.assertEqual(len(files), 1)
        with self.assertRaises(ValueError):
            parts.save_files(self.conn, did, [
                ("ok.pdf", "application/pdf", b"%PDF"),
                ("bad.exe", "application/x-msdownload", b"MZ")])
        d = parts.get_drawing(self.conn, did)
        self.assertEqual(len(d["files"]), 1)
        self.assertEqual(len(list(paths.drawing_files_dir().iterdir())), 1)

    def test_delete_guard(self):
        did = self.drawing()
        self.order(items=[{"drawing_id": did, "qty": 5, "rate": 250}])
        with self.assertRaises(ValueError):
            parts.delete_drawing(self.conn, did)


class OrdersSpec(WorkshopBase):
    def test_numbering_per_fy(self):
        o1 = orders.get_order(self.conn, self.order())
        o2 = orders.get_order(self.conn, self.order())
        self.assertEqual(o1["order_no"], "ORD-26-27-001")
        self.assertEqual(o2["order_no"], "ORD-26-27-002")
        o3 = orders.get_order(self.conn, self.order(order_date="2027-04-02"))
        self.assertEqual(o3["order_no"], "ORD-27-28-001")   # new FY restarts

    def test_validation(self):
        with self.assertRaises(ValueError):
            self.order(items=[])
        with self.assertRaises(ValueError):
            self.order(items=[{"qty": 5, "rate": 10}])      # no drawing, no text
        with self.assertRaises(ValueError):
            self.order(stage="shipped")

    def test_stages_logged(self):
        oid = self.order()
        o = orders.set_stage(self.conn, oid, "production", "started on VMC-2")
        self.assertEqual(o["stage"], "production")
        self.assertEqual([l["stage"] for l in o["stage_log"]], ["production", "po"])
        with self.assertRaises(ValueError):
            orders.set_stage(self.conn, oid, "nope")

    def test_heat_linkage(self):
        oid = self.order()
        o = orders.get_order(self.conn, oid)
        self.conn.execute(
            "INSERT INTO heat (heat_number, date_received, rods_received)"
            " VALUES ('HT-1','2026-08-01',50)")
        hid = self.conn.execute("SELECT id FROM heat").fetchone()["id"]
        self.conn.execute(
            "INSERT INTO heat_movement (heat_id, mv_date, type, order_id, rods)"
            " VALUES (?, '2026-08-14', 'issue', ?, 8)", (hid, o["order_no"]))
        self.conn.commit()
        o = orders.get_order(self.conn, oid)
        self.assertEqual(len(o["heats"]), 1)
        self.assertEqual(o["heats"][0]["heat_number"], "HT-1")

    def test_consignment_flow(self):
        oid = self.order()          # 10 Nos
        item = orders.get_order(self.conn, oid)["items"][0]
        cid = orders.create_consignment(self.conn, {
            "consign_date": "2026-08-14", "transporter": "VRL", "lr_no": "LR123",
            "lines": [{"order_item_id": item["id"], "qty": 4}]})
        pending = orders.open_items(self.conn, oid)[0]
        self.assertEqual(pending["shipped"], 4)
        self.assertEqual(pending["pending"], 6)
        with self.assertRaises(ValueError):     # over-ship the remaining 6
            orders.create_consignment(self.conn, {
                "lines": [{"order_item_id": item["id"], "qty": 7}]})
        cn = orders.set_delivered(self.conn, cid, True)
        self.assertEqual(cn["delivered"], 1)
        with self.assertRaises(ValueError):     # order locked by consignment
            orders.delete_order(self.conn, oid)
        orders.delete_consignment(self.conn, cid)
        orders.delete_order(self.conn, oid)     # now fine

    def test_multi_order_consignment(self):
        o1 = self.order()
        o2 = self.order()
        i1 = orders.get_order(self.conn, o1)["items"][0]
        i2 = orders.get_order(self.conn, o2)["items"][0]
        cid = orders.create_consignment(self.conn, {
            "lines": [{"order_item_id": i1["id"], "qty": 10},
                      {"order_item_id": i2["id"], "qty": 3}]})
        cn = orders.get_consignment(self.conn, cid)
        self.assertEqual(len(cn["lines"]), 2)
        nos = {l["order_no"] for l in cn["lines"]}
        self.assertEqual(len(nos), 2)           # one truck, two orders

    def test_shrink_below_shipped(self):
        oid = self.order()
        o = orders.get_order(self.conn, oid)
        item = o["items"][0]
        orders.create_consignment(self.conn, {
            "lines": [{"order_item_id": item["id"], "qty": 4}]})
        with self.assertRaises(ValueError):
            orders.update_order(self.conn, oid, {
                "customer_id": self.cust, "order_date": o["order_date"],
                "items": [{"id": item["id"], "description": "Shaft", "qty": 3,
                           "unit": "Nos", "rate": 250}]})


class CustomerCodes(WorkshopBase):
    """The client code the company's documents are numbered with (CONVENTIONS
    §2): one letter, two digits."""

    def test_abbreviation_rules(self):
        cases = [("Acme Castings", "A"), ("Bharat Hydraulics Pvt Ltd", "B"),
                 ("Sterling", "S"), ("M/s Tata Steel", "T"), ("3M Company", "M"),
                 ("", "X")]
        for name, expect in cases:
            self.assertEqual(customers.abbreviate(name), expect, name)

    def test_serial_within_the_letter(self):
        # the fixture's 'Acme Pumps' already holds A01
        a = customers.save_customer(self.conn, {"name": "Acme Castings"})
        b = customers.save_customer(self.conn, {"name": "Anand Components"})
        c = customers.save_customer(self.conn, {"name": "Bharat Gears"})
        codes = {customers.get_customer(self.conn, i)["code"] for i in (a, b, c)}
        self.assertEqual(codes, {"A02", "A03", "B01"})
        self.assertEqual(customers.get_customer(self.conn, self.cust)["code"], "A01")

    def test_manual_letter_and_backfill(self):
        cid = customers.save_customer(self.conn, {"name": "Zenith Works", "abbr": "z"})
        self.assertEqual(customers.get_customer(self.conn, cid)["code"], "Z01")
        self.conn.execute("UPDATE customer SET code=NULL WHERE id=?", (cid,))
        self.conn.commit()
        self.assertEqual(customers.backfill_codes(self.conn), 1)
        self.assertTrue(customers.get_customer(self.conn, cid)["code"])

    def test_a_typed_code_is_taken_at_its_word(self):
        cid = customers.save_customer(self.conn, {"name": "Thermosense", "code": "t04"})
        self.assertEqual(customers.get_customer(self.conn, cid)["code"], "T04")
        # …and the abbreviation box accepts a whole code too (it is labelled
        # "Customer code" on the form)
        other = customers.save_customer(self.conn, {"name": "East Coast", "abbr": "E01"})
        self.assertEqual(customers.get_customer(self.conn, other)["code"], "E01")

    def test_the_code_is_editable_and_unique(self):
        customers.save_customer(self.conn, {"name": "Thermosense", "code": "T04"})
        customers.save_customer(self.conn, {"name": "Acme Pumps", "code": "T09"}, self.cust)
        self.assertEqual(customers.get_customer(self.conn, self.cust)["code"], "T09")
        with self.assertRaises(ValueError) as e:                 # someone else's code
            customers.save_customer(self.conn, {"name": "Acme Pumps", "code": "T04"},
                                    self.cust)
        self.assertIn("T04", str(e.exception))
        self.assertIn("Thermosense", str(e.exception))
        self.assertEqual(customers.get_customer(self.conn, self.cust)["code"], "T09")

    def test_a_blank_code_leaves_an_existing_one_alone(self):
        """Quotations are numbered under the code — an edit that doesn't
        mention it must never reissue it."""
        customers.save_customer(self.conn, {"name": "Acme Pumps", "notes": "x"}, self.cust)
        self.assertEqual(customers.get_customer(self.conn, self.cust)["code"], "A01")

    def test_a_blank_code_on_a_customer_without_one_assigns(self):
        self.conn.execute("UPDATE customer SET code='' WHERE id=?", (self.cust,))
        self.conn.commit()
        customers.save_customer(self.conn, {"name": "Acme Pumps"}, self.cust)
        self.assertEqual(customers.get_customer(self.conn, self.cust)["code"], "A01")

    def test_the_route_model_carries_the_code(self):
        """Pydantic drops what it doesn't declare — the code would vanish."""
        self.assertEqual(customers.CustomerIn(name="X", code="T04").code, "T04")

    def test_legacy_two_letter_codes_are_recoded_once(self):
        a = customers.save_customer(self.conn, {"name": "Bharat Gears"})
        b = customers.save_customer(self.conn, {"name": "Bright Metals"})
        self.conn.execute("UPDATE customer SET code='BG01' WHERE id=?", (a,))
        self.conn.execute("UPDATE customer SET code='BM01' WHERE id=?", (b,))
        self.conn.execute("UPDATE customer SET code='AP01' WHERE id=?", (self.cust,))
        self.conn.commit()
        self.assertEqual(customers.recode_legacy_codes(self.conn), 3)
        self.assertEqual(customers.get_customer(self.conn, self.cust)["code"], "A01")
        # id order decides who gets B01; both keep a distinct conforming code
        self.assertEqual(customers.get_customer(self.conn, a)["code"], "B01")
        self.assertEqual(customers.get_customer(self.conn, b)["code"], "B02")
        # idempotent: a second pass has nothing left to do
        self.assertEqual(customers.recode_legacy_codes(self.conn), 0)

    def test_recoding_never_collides_with_a_conforming_code(self):
        keeper = customers.save_customer(self.conn, {"name": "Zenith", "code": "B01"})
        legacy = customers.save_customer(self.conn, {"name": "Bharat Gears"})
        self.conn.execute("UPDATE customer SET code='BG01' WHERE id=?", (legacy,))
        self.conn.commit()
        customers.recode_legacy_codes(self.conn)
        self.assertEqual(customers.get_customer(self.conn, keeper)["code"], "B01")
        self.assertEqual(customers.get_customer(self.conn, legacy)["code"], "B02")

    def test_business_view(self):
        self.order(); self.order()
        biz = customers.business(self.conn, self.cust)
        self.assertEqual(biz["stats"]["order_count"], 2)
        self.assertEqual(biz["stats"]["total_business"], 5000.0)   # 2 x 10 x 250
        self.assertEqual(biz["stats"]["avg_order"], 2500.0)
        self.assertEqual(len(biz["series"]), 1)                    # both in 2026-08
        self.assertEqual(biz["series"][0]["cumulative"], 5000.0)


class CostingWeighting(WorkshopBase):
    def test_additional_margin_is_rupees_per_hour(self):
        # The extra is money ADDED TO THE HOURLY RATE, not a percentage:
        # ₹400/hr + ₹50/hr is charged at ₹450/hr.
        self.assertEqual(parts.op_cost(12, 400), 80.0)               # plain
        self.assertEqual(parts.op_cost(12, 400, 1.25), 100.0)        # weighted
        self.assertEqual(parts.op_cost(12, 400, 1, 50), 90.0)        # 12min @ ₹450
        self.assertEqual(parts.op_cost(12, 400, 1.25, 50), 112.5)    # both
        self.assertEqual(parts.op_cost(60, 400, 1, 50), 450.0)       # one hour == the rate

    def test_saved_costing_uses_the_columns(self):
        did = self.drawing()
        d = parts.save_costing(self.conn, did, {
            "material_cost": 80, "margin_pct": 20,
            "ops": [{"operation": "Turning", "minutes": 12, "rate_per_hour": 400,
                     "weightage": 1.25, "extra_rate": 50}]})
        c = d["costings"][0]
        self.assertEqual(c["ops"][0]["cost"], 112.5)
        self.assertEqual(c["ops"][0]["weightage"], 1.25)
        self.assertEqual(c["ops"][0]["extra_rate"], 50)
        self.assertEqual(c["total"], 231.0)                          # (112.5+80) * 1.2
        d = parts.costing_to_rate(self.conn, c["id"], "agreed")
        self.assertEqual(d["rates"][0]["rate"], 231.0)               # recorded == shown

    def test_defaults_when_columns_left_blank(self):
        did = self.drawing()
        d = parts.save_costing(self.conn, did, {
            "ops": [{"operation": "Turning", "minutes": 12, "rate_per_hour": 400}]})
        self.assertEqual(d["costings"][0]["ops"][0]["cost"], 80.0)   # weightage defaults to 1


class CustomerOperationRates(WorkshopBase):
    def test_set_update_and_delete(self):
        rates = customers.set_operation_rate(self.conn, self.cust, {
            "operation": "Turning", "rate_per_hour": 520, "extra_rate": 30,
            "note": "agreed Apr 2026"})
        self.assertEqual(rates[0]["rate_per_hour"], 520)
        self.assertEqual(rates[0]["extra_rate"], 30)
        again = customers.set_operation_rate(self.conn, self.cust, {
            "operation": "Turning", "rate_per_hour": 545})       # upsert, not duplicate
        self.assertEqual(len(again), 1)
        self.assertEqual(again[0]["rate_per_hour"], 545)
        self.assertEqual(customers.delete_operation_rate(self.conn, self.cust, "Turning"), [])

    def test_validation(self):
        with self.assertRaises(ValueError):
            customers.set_operation_rate(self.conn, self.cust, {"operation": "",
                                                                 "rate_per_hour": 10})
        with self.assertRaises(ValueError):
            customers.set_operation_rate(self.conn, 9999, {"operation": "Turning",
                                                            "rate_per_hour": 10})
        with self.assertRaises(ValueError):
            customers.set_operation_rate(self.conn, self.cust, {"operation": "Turning",
                                                                 "rate_per_hour": float("inf")})

    def test_customer_rate_overrides_the_standard_one(self):
        std = {o["name"]: o["rate_per_hour"] for o in settings.operations(self.conn)}
        customers.set_operation_rate(self.conn, self.cust, {
            "operation": "Turning", "rate_per_hour": 520, "extra_rate": 30})
        override = customers.operation_rates(self.conn, self.cust)
        self.assertEqual(override["Turning"]["rate_per_hour"], 520)
        self.assertNotEqual(std["Turning"], 520)          # genuinely different
        # a costing priced at their rate: 12min @ (520+30) = ₹110
        self.assertEqual(parts.op_cost(12, override["Turning"]["rate_per_hour"], 1,
                                        override["Turning"]["extra_rate"]), 110.0)

    def test_rates_ride_along_on_the_customer_record(self):
        customers.set_operation_rate(self.conn, self.cust, {"operation": "Milling",
                                                             "rate_per_hour": 600})
        c = customers.get_customer(self.conn, self.cust)
        self.assertEqual([r["operation"] for r in c["operation_rates"]], ["Milling"])


class QuotationsAndInvoices(WorkshopBase):
    def doc(self, kind="quotation", **kw):
        data = {"customer_id": self.cust, "doc_date": "2026-08-14", "tax_pct": 18,
                "lines": [{"description": "Shaft", "qty": 10, "unit": "Nos", "rate": 250}]}
        data.update(kw)
        return quotations.create_doc(self.conn, kind, data)

    def test_numbering_follows_the_document_scheme(self):
        """CONVENTIONS §3: quotations run per client code and never reset,
        invoices run per fiscal year."""
        q1 = quotations.get_doc(self.conn, self.doc())
        q2 = quotations.get_doc(self.conn, self.doc())
        i1 = quotations.get_doc(self.conn, self.doc("invoice"))
        self.assertEqual(q1["doc_no"], "A01/AT/140826/1")
        self.assertEqual(q2["doc_no"], "A01/AT/140826/2")
        self.assertEqual(i1["doc_no"], "AT/EI/26-27/001")   # its own sequence
        nxt = quotations.get_doc(self.conn, self.doc(doc_date="2027-04-02"))
        self.assertEqual(nxt["doc_no"], "A01/AT/020427/3")  # per client, no FY reset
        inv2 = quotations.get_doc(self.conn, self.doc("invoice", doc_date="2027-04-02"))
        self.assertEqual(inv2["doc_no"], "AT/EI/27-28/001")  # new FY restarts

    def test_a_customer_without_a_code_gets_one_while_numbering(self):
        self.conn.execute("UPDATE customer SET code=NULL WHERE id=?", (self.cust,))
        self.conn.commit()
        d = quotations.get_doc(self.conn, self.doc())
        self.assertTrue(d["doc_no"].startswith("A01/AT/"))
        self.assertEqual(customers.get_customer(self.conn, self.cust)["code"], "A01")

    def test_the_seeded_counter_is_where_the_real_numbers_carry_on(self):
        numbering.ensure_seeds(self.conn)
        customers.save_customer(self.conn, {"name": "Thermosense", "code": "T04"})
        tid = [c for c in customers.list_customers(self.conn)
               if c["code"] == "T04"][0]["id"]
        d = quotations.get_doc(self.conn, quotations.create_doc(self.conn, "quotation", {
            "customer_id": tid, "doc_date": "2026-08-21",
            "lines": [{"description": "Sensor", "qty": 1, "rate": 10}]}))
        self.assertEqual(d["doc_no"], "T04/AT/210826/317")
        inv = quotations.get_doc(self.conn, self.doc("invoice"))
        self.assertEqual(inv["doc_no"], "AT/EI/26-27/169")

    def test_totals_with_tax(self):
        d = quotations.get_doc(self.conn, self.doc())
        self.assertEqual(d["subtotal"], 2500.0)
        self.assertEqual(d["tax"], 450.0)
        self.assertEqual(d["total"], 2950.0)

    def test_validation(self):
        with self.assertRaises(ValueError):
            self.doc(lines=[])
        with self.assertRaises(ValueError):
            self.doc(lines=[{"qty": 1, "rate": 10}])          # no part, no text
        with self.assertRaises(ValueError):
            self.doc(customer_id=9999)                         # dangling ref
        with self.assertRaises(ValueError):
            quotations.create_doc(self.conn, "receipt", {"customer_id": self.cust,
                                                          "lines": [{"qty": 1}]})

    def test_invoice_from_order_carries_the_items(self):
        did = self.drawing()
        oid = self.order(items=[{"drawing_id": did, "qty": 40, "unit": "Nos", "rate": 270}])
        pre = quotations.from_order(self.conn, oid, "invoice")
        self.assertEqual(pre["customer_id"], self.cust)
        self.assertEqual(pre["lines"][0]["qty"], 40)
        inv = quotations.get_doc(self.conn, quotations.create_doc(self.conn, "invoice",
                                                                  {**pre, "doc_date": "2026-08-14"}))
        self.assertEqual(inv["subtotal"], 10800.0)
        self.assertEqual(inv["order_no"], "ORD-26-27-001")

    def test_status_and_edit_guard(self):
        did = self.doc()
        quotations.set_status(self.conn, did, "paid")
        with self.assertRaises(ValueError):
            quotations.update_doc(self.conn, did, {"customer_id": self.cust,
                                                    "doc_date": "2026-08-14",
                                                    "lines": [{"description": "x", "qty": 1}]})
        with self.assertRaises(ValueError):
            quotations.set_status(self.conn, did, "posted")

    def test_print_view_renders(self):
        d = quotations.get_doc(self.conn, self.doc())
        html = quotations.render_print(self.conn, d["id"])
        self.assertIn("QUOTATION", html)
        self.assertIn(d["doc_no"], html)
        self.assertIn("2,950.00", html)          # Indian grouping in the total
        self.assertIn("window.print()", html)

    def test_money_grouping(self):
        self.assertEqual(quotations._money(1234567.5), "12,34,567.50")
        self.assertEqual(quotations._money(999), "999.00")


class ReviewRegressions(WorkshopBase):
    """Pins for the adversarial-review findings on the workshop modules."""

    def test_costing_money_bounds(self):
        did = parts.save_drawing(self.conn, {"drawing_no": "B-1"})
        for bad in (float("inf"), float("nan"), 1e308):
            with self.assertRaises(ValueError):
                parts.save_costing(self.conn, did, {
                    "material_cost": bad, "margin_pct": 0,
                    "ops": [{"operation": "T", "minutes": 1, "rate_per_hour": 100}]})

    def test_recorded_rate_equals_displayed_total(self):
        did = parts.save_drawing(self.conn, {"drawing_no": "B-2"})
        d = parts.save_costing(self.conn, did, {
            "material_cost": 33.335, "margin_pct": 17.5,
            "ops": [{"operation": "T", "minutes": 7, "rate_per_hour": 333}]})
        shown = d["costings"][0]["total"]
        d = parts.costing_to_rate(self.conn, d["costings"][0]["id"], "quoted")
        self.assertEqual(d["rates"][0]["rate"], shown)   # never differ

    def test_order_no_collision_is_friendly(self):
        # a format without {FY} written directly (bypassing the API guard)
        settings.set_setting(self.conn, "order_number_format", "JOB-{SEQ}")
        self.order()                                     # JOB-001 (fy 26-27)
        with self.assertRaises(ValueError) as cm:        # fy 27-28 restarts seq
            self.order(order_date="2027-04-02")
        self.assertIn("already exists", str(cm.exception))

    def test_duplicate_lines_cannot_bypass_over_ship(self):
        oid = self.order()                       # 10 Nos
        item = orders.get_order(self.conn, oid)["items"][0]
        with self.assertRaises(ValueError):      # 6+6 > 10, in ONE payload
            orders.create_consignment(self.conn, {"lines": [
                {"order_item_id": item["id"], "qty": 6},
                {"order_item_id": item["id"], "qty": 6}]})
        cid = orders.create_consignment(self.conn, {"lines": [   # 4+4 = 8 <= 10
            {"order_item_id": item["id"], "qty": 4},
            {"order_item_id": item["id"], "qty": 4}]})
        self.assertEqual(orders.open_items(self.conn, oid)[0]["shipped"], 8)
        self.assertEqual(len(orders.get_consignment(self.conn, cid)["lines"]), 1)  # merged

    def test_dangling_refs_are_friendly_errors(self):
        with self.assertRaises(ValueError):
            self.order(customer_id=9999)
        with self.assertRaises(ValueError):
            self.order(items=[{"drawing_id": 9999, "qty": 1, "rate": 10}])

    def test_freight_zero_ok_infinite_refused(self):
        oid = self.order()
        item = orders.get_order(self.conn, oid)["items"][0]
        cid = orders.create_consignment(self.conn, {
            "freight": 0, "lines": [{"order_item_id": item["id"], "qty": 1}]})
        self.assertEqual(orders.get_consignment(self.conn, cid)["freight"], 0)
        with self.assertRaises(ValueError):
            orders.create_consignment(self.conn, {
                "freight": float("inf"),
                "lines": [{"order_item_id": item["id"], "qty": 1}]})


if __name__ == "__main__":
    unittest.main(verbosity=2)


class DeliveryPlanning(WorkshopBase):
    """A long order shipped in instalments: the plan, and what is still owed."""

    def _item(self, oid):
        return orders.get_order(self.conn, oid)["items"][0]

    def test_plan_and_derived_remainder(self):
        oid = self.order(due_date="2026-11-30",
                         items=[{"description": "Flange", "qty": 600,
                                 "unit": "Nos", "rate": 120}])
        it = self._item(oid)
        r = orders.set_schedule(self.conn, it["id"], [
            {"due_date": "2026-09-15", "qty": 250, "note": "first drop"},
            {"due_date": "2026-10-15", "qty": 100},
            {"due_date": "2026-11-30", "qty": 250, "note": "balance"},
        ])
        self.assertEqual((r["planned"], r["unplanned"]), (600.0, 0.0))
        it = self._item(oid)
        self.assertEqual(len(it["schedule"]), 3)
        self.assertEqual(it["planned"], 600.0)
        self.assertEqual(it["unplanned"], 0.0)
        # dates come back in order, whatever order they went in
        self.assertEqual([s["due_date"] for s in it["schedule"]],
                         ["2026-09-15", "2026-10-15", "2026-11-30"])

    def test_partial_plan_leaves_a_remainder(self):
        oid = self.order(items=[{"description": "Flange", "qty": 600,
                                 "unit": "Nos", "rate": 120}])
        it = self._item(oid)
        orders.set_schedule(self.conn, it["id"], [{"due_date": "2026-09-15", "qty": 250}])
        it = self._item(oid)
        self.assertEqual((it["planned"], it["unplanned"]), (250.0, 350.0))

    def test_cannot_plan_more_than_ordered(self):
        oid = self.order(items=[{"description": "Flange", "qty": 600,
                                 "unit": "Nos", "rate": 120}])
        it = self._item(oid)
        with self.assertRaises(ValueError) as cm:
            orders.set_schedule(self.conn, it["id"], [
                {"due_date": "2026-09-15", "qty": 400},
                {"due_date": "2026-10-15", "qty": 300}])
        self.assertIn("600", str(cm.exception))
        self.assertEqual(self._item(oid)["planned"], 0.0)   # nothing written

    def test_plan_line_needs_a_real_date_and_qty(self):
        oid = self.order()
        it = self._item(oid)
        for bad in ([{"due_date": "", "qty": 5}],
                    [{"due_date": "not-a-date", "qty": 5}],
                    [{"due_date": "2026-09-15", "qty": 0}],
                    [{"due_date": "2026-09-15", "qty": -3}],
                    [{"due_date": "2026-09-15", "qty": "many"}]):
            with self.assertRaises(ValueError):
                orders.set_schedule(self.conn, it["id"], bad)

    def test_saving_a_plan_replaces_the_old_one(self):
        oid = self.order()
        it = self._item(oid)
        orders.set_schedule(self.conn, it["id"], [{"due_date": "2026-09-15", "qty": 5}])
        orders.set_schedule(self.conn, it["id"], [{"due_date": "2026-10-15", "qty": 2}])
        it = self._item(oid)
        self.assertEqual(len(it["schedule"]), 1)
        self.assertEqual(it["schedule"][0]["qty"], 2.0)

    def test_plan_dies_with_the_item(self):
        oid = self.order()
        it = self._item(oid)
        orders.set_schedule(self.conn, it["id"], [{"due_date": "2026-09-15", "qty": 5}])
        orders.delete_order(self.conn, oid)
        n = self.conn.execute("SELECT COUNT(*) AS n FROM order_schedule").fetchone()["n"]
        self.assertEqual(n, 0)

    def test_unknown_item(self):
        with self.assertRaises(ValueError):
            orders.set_schedule(self.conn, 999999, [{"due_date": "2026-09-15", "qty": 1}])


class Deadlines(WorkshopBase):
    """The Home warning panel's three buckets."""

    def _order_due(self, days, qty=10, stage="production"):
        from datetime import date, timedelta
        due = (date(2026, 8, 14) + timedelta(days=days)).isoformat()
        return self.order(due_date=due, stage=stage,
                          items=[{"description": "Shaft", "qty": qty,
                                  "unit": "Nos", "rate": 100}])

    def test_buckets(self):
        self._order_due(-3)     # overdue
        self._order_due(0)      # today
        self._order_due(5)      # this week
        self._order_due(20)     # this month
        self._order_due(90)     # far away: in no bucket
        d = orders.deadlines(self.conn, today="2026-08-14")
        self.assertEqual(len(d["overdue"]), 1)
        self.assertEqual(len(d["this_week"]), 2)     # today + 5 days
        self.assertEqual(len(d["this_month"]), 1)
        self.assertEqual(d["as_of"], "2026-08-14")

    def test_days_left_and_pending(self):
        self._order_due(5, qty=600)
        d = orders.deadlines(self.conn, today="2026-08-14")
        row = d["this_week"][0]
        self.assertEqual(row["days_left"], 5)
        self.assertEqual(row["qty_pending"], 600.0)
        self.assertEqual(row["customer_name"], "Acme Pumps")

    def test_orders_with_no_deadline_are_ignored(self):
        self.order(due_date="")
        d = orders.deadlines(self.conn, today="2026-08-14")
        self.assertEqual(sum(len(d[k]) for k in ("overdue", "this_week", "this_month")), 0)

    def test_fully_shipped_orders_drop_out(self):
        oid = self._order_due(3, qty=10)
        it = orders.get_order(self.conn, oid)["items"][0]
        orders.create_consignment(self.conn, {
            "consign_date": "2026-08-14", "lr_no": "LR-1",
            "lines": [{"order_item_id": it["id"], "qty": 10}]})
        d = orders.deadlines(self.conn, today="2026-08-14")
        self.assertEqual(len(d["this_week"]), 0)     # nothing left to send

    def test_partly_shipped_orders_stay(self):
        oid = self._order_due(3, qty=10)
        it = orders.get_order(self.conn, oid)["items"][0]
        orders.create_consignment(self.conn, {
            "consign_date": "2026-08-14", "lr_no": "LR-2",
            "lines": [{"order_item_id": it["id"], "qty": 4}]})
        d = orders.deadlines(self.conn, today="2026-08-14")
        self.assertEqual(d["this_week"][0]["qty_pending"], 6.0)


class ShipmentProgress(WorkshopBase):
    """The Shipments tab's per-order fulfilment figures."""

    def test_list_carries_shipped_and_pending(self):
        oid = self.order(items=[{"description": "Shaft", "qty": 100,
                                 "unit": "Nos", "rate": 10}])
        it = orders.get_order(self.conn, oid)["items"][0]
        orders.create_consignment(self.conn, {
            "consign_date": "2026-08-14", "lr_no": "LR-1",
            "lines": [{"order_item_id": it["id"], "qty": 30}]})
        orders.create_consignment(self.conn, {
            "consign_date": "2026-08-20", "lr_no": "LR-2",
            "lines": [{"order_item_id": it["id"], "qty": 20}]})
        row = [r for r in orders.list_orders(self.conn)["rows"] if r["id"] == oid][0]
        self.assertEqual(row["qty_total"], 100.0)
        self.assertEqual(row["qty_shipped"], 50.0)   # across BOTH consignments
        self.assertEqual(row["qty_pending"], 50.0)
        self.assertEqual(row["pct_shipped"], 50)

    def test_order_totals_on_the_record(self):
        oid = self.order(items=[{"description": "A", "qty": 10, "unit": "Nos", "rate": 1},
                                {"description": "B", "qty": 5, "unit": "Nos", "rate": 1}])
        o = orders.get_order(self.conn, oid)
        self.assertEqual((o["qty_total"], o["qty_shipped"], o["qty_pending"]),
                         (15.0, 0.0, 15.0))


class BillOfMaterials(WorkshopBase):
    """Material priced from inventory rather than typed from memory."""

    def _heat(self, **kw):
        data = {"heat_number": "H-500", "date_received": "2026-08-01",
                "material_class": "Steel", "grade": "EN8", "rods_received": 10,
                "price_total": 45000, "total_weight_kg": 900,
                "pieces": [{"length_mm": 3000, "diameter_mm": 25, "quantity": 10}]}
        data.update(kw)
        return inventory.create_heat(self.conn, data)

    def test_search_by_heat_grade_and_material(self):
        self._heat()
        self._heat(heat_number="H-501", grade="EN19", material_class="Alloy Steel")
        for q, expect in (("H-500", {"H-500"}), ("EN19", {"H-501"}),
                          ("Alloy", {"H-501"}), ("Steel", {"H-500", "H-501"})):
            got = {r["heat_number"] for r in
                   inventory.material_search(q=q, conn=self.conn)["rows"]}
            self.assertEqual(got, expect, q)

    def test_search_derives_unit_costs(self):
        self._heat()
        r = inventory.material_search(q="H-500", conn=self.conn)["rows"][0]
        self.assertEqual(r["cost_per_rod"], 4500.0)      # 45000 / 10 rods
        self.assertEqual(r["cost_per_kg"], 50.0)         # 45000 / 900 kg
        self.assertEqual(r["remaining"], 10)

    def test_no_price_means_no_rate(self):
        self._heat(heat_number="H-FREE", price_total=None, total_weight_kg=None)
        r = [x for x in inventory.material_search(q="H-FREE", conn=self.conn)["rows"]][0]
        self.assertIsNone(r["cost_per_rod"])
        self.assertIsNone(r["cost_per_kg"])

    def test_bom_sets_the_material_cost(self):
        hid = self._heat()
        did = self.drawing()
        d = parts.save_costing(self.conn, did, {
            "margin_pct": 10,
            "ops": [{"operation": "Turning", "minutes": 12, "rate_per_hour": 400,
                     "weightage": 1, "extra_rate": 0}],
            "materials": [{"heat_id": hid, "heat_number": "H-500",
                           "material_label": "Steel · EN8", "unit": "rod",
                           "unit_cost": 4500, "qty_per_piece": 1 / 3}]})
        c = d["costings"][0]
        self.assertEqual(c["bom_total"], 1500.0)         # exactly a third of 4500
        self.assertEqual(c["material_cost"], 1500.0)     # BOM wins over any typed figure
        self.assertEqual(c["ops_total"], 80.0)
        self.assertEqual(c["total"], round((80 + 1500) * 1.1, 2))

    def test_thirds_do_not_lose_a_percent(self):
        """qty per piece is NOT money: rounding 0.333333 to 0.33 underprices."""
        self.assertEqual(parts.bom_cost(4500, 1 / 3), 1500.0)
        self.assertEqual(parts.bom_cost(4500, 0.33), 1485.0)

    def test_typed_material_cost_still_works_without_a_bom(self):
        did = self.drawing()
        d = parts.save_costing(self.conn, did, {"material_cost": 250, "ops": []})
        self.assertEqual(d["costings"][0]["material_cost"], 250.0)
        self.assertEqual(d["costings"][0]["materials"], [])

    def test_bom_is_snapshotted(self):
        hid = self._heat()
        did = self.drawing()
        parts.save_costing(self.conn, did, {
            "ops": [], "materials": [{"heat_id": hid, "heat_number": "H-500",
                                      "material_label": "Steel · EN8", "unit": "rod",
                                      "unit_cost": 4500, "qty_per_piece": 0.5}]})
        # the stock gets more expensive later
        inventory.update_heat(self.conn, hid, {
            "heat_number": "H-500", "date_received": "2026-08-01",
            "material_class": "Steel", "rods_received": 10, "price_total": 90000})
        c = parts.get_drawing(self.conn, did)["costings"][0]
        self.assertEqual(c["materials"][0]["unit_cost"], 4500.0)   # unchanged
        self.assertEqual(c["material_cost"], 2250.0)

    def test_bom_validation(self):
        did = self.drawing()
        with self.assertRaises(ValueError):      # nothing identifying the material
            parts.save_costing(self.conn, did, {"ops": [], "materials": [
                {"unit_cost": 100, "qty_per_piece": 1}]})
        with self.assertRaises(ValueError):      # zero quantity per piece
            parts.save_costing(self.conn, did, {"ops": [], "materials": [
                {"heat_number": "H-500", "unit_cost": 100, "qty_per_piece": 0}]})
        with self.assertRaises(ValueError):      # infinity
            parts.save_costing(self.conn, did, {"ops": [], "materials": [
                {"heat_number": "H-500", "unit_cost": 100,
                 "qty_per_piece": float("inf")}]})


class OrderBillOfMaterials(WorkshopBase):
    """What an order commits, rolled up from its parts' costings."""

    def setUp(self):
        super().setUp()
        self.h1 = inventory.create_heat(self.conn, {
            "heat_number": "OB-1", "date_received": "2026-08-01",
            "material_class": "Steel", "grade": "EN8", "rods_received": 50,
            "price_total": 225000})                      # ₹4 500 a rod
        self.h2 = inventory.create_heat(self.conn, {
            "heat_number": "OB-2", "date_received": "2026-08-01",
            "material_class": "Brass", "rods_received": 20,
            "price_total": 60000})                       # ₹3 000 a rod

    def _costed(self, drawing_no, lines):
        did = self.drawing(drawing_no=drawing_no)
        parts.save_costing(self.conn, did, {"ops": [], "materials": lines})
        return did

    def test_required_is_per_piece_times_ordered(self):
        did = self._costed("DRG-A", [
            {"heat_id": self.h1, "heat_number": "OB-1", "material_label": "Steel · EN8",
             "unit": "rod", "unit_cost": 4500, "qty_per_piece": 1 / 3}])
        oid = self.order(items=[{"drawing_id": did, "description": "shaft",
                                 "qty": 60, "unit": "Nos", "rate": 300}])
        b = orders.order_bom(self.conn, oid)
        self.assertEqual(len(b["summary"]), 1)
        row = b["summary"][0]
        self.assertEqual(row["heat_number"], "OB-1")
        self.assertEqual(row["required"], 20.0)        # 60 / 3
        self.assertEqual(row["cost"], 90000.0)
        self.assertEqual(b["total_cost"], 90000.0)

    def test_one_heat_used_by_two_parts_is_added_up(self):
        a = self._costed("DRG-A", [
            {"heat_id": self.h1, "heat_number": "OB-1", "material_label": "Steel",
             "unit": "rod", "unit_cost": 4500, "qty_per_piece": 1 / 3}])
        c = self._costed("DRG-C", [
            {"heat_id": self.h1, "heat_number": "OB-1", "material_label": "Steel",
             "unit": "rod", "unit_cost": 4500, "qty_per_piece": 0.1},
            {"heat_id": self.h2, "heat_number": "OB-2", "material_label": "Brass",
             "unit": "rod", "unit_cost": 3000, "qty_per_piece": 0.25}])
        oid = self.order(items=[
            {"drawing_id": a, "description": "shaft", "qty": 60, "unit": "Nos", "rate": 1},
            {"drawing_id": c, "description": "bush", "qty": 40, "unit": "Nos", "rate": 1}])
        b = orders.order_bom(self.conn, oid)
        got = {r["heat_number"]: r["required"] for r in b["summary"]}
        self.assertEqual(got, {"OB-1": 24.0, "OB-2": 10.0})   # 20 + 4, and 10

    def test_issued_and_outstanding(self):
        did = self._costed("DRG-A", [
            {"heat_id": self.h1, "heat_number": "OB-1", "material_label": "Steel",
             "unit": "rod", "unit_cost": 4500, "qty_per_piece": 1 / 3}])
        oid = self.order(items=[{"drawing_id": did, "description": "shaft",
                                 "qty": 60, "unit": "Nos", "rate": 1}])
        order_no = orders.get_order(self.conn, oid)["order_no"]
        inventory.add_movement(self.conn, self.h1,
                               {"type": "issue", "rods": 12, "order_id": order_no})
        row = orders.order_bom(self.conn, oid)["summary"][0]
        self.assertEqual((row["required"], row["issued"], row["outstanding"]),
                         (20.0, 12, 8.0))

    def test_over_issue_never_goes_negative(self):
        did = self._costed("DRG-A", [
            {"heat_id": self.h1, "heat_number": "OB-1", "material_label": "Steel",
             "unit": "rod", "unit_cost": 4500, "qty_per_piece": 1 / 3}])
        oid = self.order(items=[{"drawing_id": did, "description": "shaft",
                                 "qty": 3, "unit": "Nos", "rate": 1}])   # needs 1 rod
        order_no = orders.get_order(self.conn, oid)["order_no"]
        inventory.add_movement(self.conn, self.h1,
                               {"type": "issue", "rods": 5, "order_id": order_no})
        row = orders.order_bom(self.conn, oid)["summary"][0]
        self.assertEqual(row["outstanding"], 0.0)

    def test_material_issued_that_no_part_calls_for_is_flagged(self):
        did = self._costed("DRG-A", [
            {"heat_id": self.h1, "heat_number": "OB-1", "material_label": "Steel",
             "unit": "rod", "unit_cost": 4500, "qty_per_piece": 1 / 3}])
        oid = self.order(items=[{"drawing_id": did, "description": "shaft",
                                 "qty": 60, "unit": "Nos", "rate": 1}])
        order_no = orders.get_order(self.conn, oid)["order_no"]
        inventory.add_movement(self.conn, self.h2,
                               {"type": "issue", "rods": 2, "order_id": order_no})
        b = orders.order_bom(self.conn, oid)
        self.assertEqual([u["heat_number"] for u in b["unexpected_issues"]], ["OB-2"])

    def test_items_without_a_bom_say_why(self):
        plain = self.drawing(drawing_no="DRG-NOCOST")          # no costing at all
        manual = self.drawing(drawing_no="DRG-MANUAL")
        parts.save_costing(self.conn, manual, {"ops": [], "material_cost": 250})
        oid = self.order(items=[
            {"drawing_id": None, "description": "packing crate", "qty": 2,
             "unit": "Nos", "rate": 1},
            {"drawing_id": plain, "description": "x", "qty": 5, "unit": "Nos", "rate": 1},
            {"drawing_id": manual, "description": "y", "qty": 5, "unit": "Nos", "rate": 1}])
        b = orders.order_bom(self.conn, oid)
        self.assertEqual(b["summary"], [])
        self.assertEqual(b["items_without_bom"], 3)
        reasons = " ".join(i["reason"] for i in b["items"])
        self.assertIn("no drawing", reasons)
        self.assertIn("no costing", reasons)
        self.assertIn("by hand", reasons)

    def test_uses_the_latest_costing(self):
        did = self._costed("DRG-A", [
            {"heat_id": self.h1, "heat_number": "OB-1", "material_label": "Steel",
             "unit": "rod", "unit_cost": 4500, "qty_per_piece": 1 / 3}])
        oid = self.order(items=[{"drawing_id": did, "description": "shaft",
                                 "qty": 60, "unit": "Nos", "rate": 1}])
        # re-costed onto different stock
        parts.save_costing(self.conn, did, {"ops": [], "materials": [
            {"heat_id": self.h2, "heat_number": "OB-2", "material_label": "Brass",
             "unit": "rod", "unit_cost": 3000, "qty_per_piece": 0.5}]})
        b = orders.order_bom(self.conn, oid)
        self.assertEqual([r["heat_number"] for r in b["summary"]], ["OB-2"])
        self.assertEqual(b["summary"][0]["required"], 30.0)

    def test_unknown_order(self):
        with self.assertRaises(ValueError):
            orders.order_bom(self.conn, 999999)


class MaterialRequisitions(WorkshopBase):
    """The BOM issued as a numbered, frozen document."""

    def setUp(self):
        super().setUp()
        self.h1 = inventory.create_heat(self.conn, {
            "heat_number": "MR-1", "date_received": "2026-08-01",
            "material_class": "Steel", "grade": "EN8", "rods_received": 50,
            "price_total": 225000})
        self.did = self.drawing(drawing_no="DRG-MR")
        parts.save_costing(self.conn, self.did, {"ops": [], "materials": [
            {"heat_id": self.h1, "heat_number": "MR-1", "material_label": "Steel · EN8",
             "unit": "rod", "unit_cost": 4500, "qty_per_piece": 1 / 3}]})
        self.oid = self.order(items=[{"drawing_id": self.did, "description": "shaft",
                                      "qty": 60, "unit": "Nos", "rate": 300}])

    def test_issue_snapshots_the_rollup(self):
        doc = orders.issue_material_doc(self.conn, self.oid,
                                        {"issued_on": "2026-08-15"}, "admin")
        self.assertTrue(doc["doc_no"].startswith("MRQ-26-27-"))
        self.assertEqual(doc["issued_by"], "admin")
        self.assertEqual(len(doc["lines"]), 1)
        line = doc["lines"][0]
        self.assertEqual((line["heat_number"], line["required"], line["cost"]),
                         ("MR-1", 20.0, 90000.0))
        self.assertEqual(line["from_parts"], "DRG-MR rev A")

    def test_numbering_is_per_financial_year(self):
        a = orders.issue_material_doc(self.conn, self.oid, {"issued_on": "2026-08-15"})
        b = orders.issue_material_doc(self.conn, self.oid, {"issued_on": "2026-08-15"})
        c = orders.issue_material_doc(self.conn, self.oid, {"issued_on": "2027-04-02"})
        self.assertEqual(a["doc_no"], "MRQ-26-27-001")
        self.assertEqual(b["doc_no"], "MRQ-26-27-002")
        self.assertEqual(c["doc_no"], "MRQ-27-28-001")      # new FY restarts

    def test_it_is_frozen(self):
        """The whole point: paper already on the shop floor must not move."""
        doc = orders.issue_material_doc(self.conn, self.oid, {"issued_on": "2026-08-15"})
        parts.save_costing(self.conn, self.did, {"ops": [], "materials": [
            {"heat_id": self.h1, "heat_number": "MR-1", "material_label": "Steel · EN8",
             "unit": "rod", "unit_cost": 9999, "qty_per_piece": 1.0}]})
        live = orders.order_bom(self.conn, self.oid)["summary"][0]
        frozen = orders.get_material_doc(self.conn, doc["id"])["lines"][0]
        self.assertEqual(live["required"], 60.0)            # the live view moved
        self.assertEqual(frozen["required"], 20.0)          # the issued sheet did not
        self.assertEqual(frozen["cost"], 90000.0)

    def test_records_what_had_already_been_issued(self):
        order_no = orders.get_order(self.conn, self.oid)["order_no"]
        inventory.add_movement(self.conn, self.h1,
                               {"type": "issue", "rods": 12, "order_id": order_no})
        doc = orders.issue_material_doc(self.conn, self.oid, {"issued_on": "2026-08-15"})
        self.assertEqual(doc["lines"][0]["already_issued"], 12)

    def test_nothing_to_requisition_is_refused(self):
        bare = self.order(items=[{"drawing_id": None, "description": "crate",
                                  "qty": 1, "unit": "Nos", "rate": 1}])
        with self.assertRaises(ValueError) as cm:
            orders.issue_material_doc(self.conn, bare, {"issued_on": "2026-08-15"})
        self.assertIn("Nothing to requisition", str(cm.exception))

    def test_listing_and_filtering(self):
        orders.issue_material_doc(self.conn, self.oid, {"issued_on": "2026-08-15"})
        other = self.order(items=[{"drawing_id": self.did, "description": "shaft",
                                   "qty": 3, "unit": "Nos", "rate": 1}])
        orders.issue_material_doc(self.conn, other, {"issued_on": "2026-08-15"})
        self.assertEqual(len(orders.list_material_docs(self.conn)), 2)
        self.assertEqual(len(orders.list_material_docs(self.conn, order_id=self.oid)), 1)
        self.assertEqual(len(orders.list_material_docs(self.conn, q="MRQ-26-27-002")), 1)

    def test_printable_sheet(self):
        doc = orders.issue_material_doc(self.conn, self.oid,
                                        {"issued_on": "2026-08-15", "notes": "first batch"},
                                        "admin")
        page = orders.render_material_doc(self.conn, doc["id"])
        self.assertIn(doc["doc_no"], page)
        self.assertIn("Material Requisition", page)
        self.assertIn("MR-1", page)
        self.assertIn("first batch", page)
        self.assertIn("Store keeper", page)
        self.assertIn("₹90,000.00", page)        # Indian grouping on the sheet
        self.assertIn("@page", page)             # A4 print styling
        self.assertNotIn("<script", page)        # nothing to load, works offline

    def test_print_escapes_user_text(self):
        doc = orders.issue_material_doc(
            self.conn, self.oid,
            {"issued_on": "2026-08-15", "notes": "<script>alert(1)</script>"})
        page = orders.render_material_doc(self.conn, doc["id"])
        self.assertNotIn("<script>alert", page)
        self.assertIn("&lt;script&gt;", page)

    def test_survives_the_order_being_deleted(self):
        doc = orders.issue_material_doc(self.conn, self.oid, {"issued_on": "2026-08-15"})
        order_no = orders.get_order(self.conn, self.oid)["order_no"]
        orders.delete_order(self.conn, self.oid)
        kept = orders.get_material_doc(self.conn, doc["id"])
        self.assertEqual(kept["order_no"], order_no)   # snapshot, not a join
        self.assertIsNone(kept["order_id"])

    def test_unknown_doc(self):
        with self.assertRaises(ValueError):
            orders.get_material_doc(self.conn, 999999)


class DropAllocation(WorkshopBase):
    """What has shipped is poured into the planned drops, earliest first."""

    def _plan(self, *drops):
        return [{"qty": q, "due_date": d} for d, q in drops]

    def test_fills_in_date_order(self):
        rows = self._plan(("2026-09-05", 250), ("2026-09-19", 150), ("2026-11-30", 200))
        over = orders._allocate_drops(rows, 300)
        self.assertEqual([r["delivered"] for r in rows], [250.0, 50.0, 0.0])
        self.assertEqual([r["remaining"] for r in rows], [0.0, 100.0, 200.0])
        self.assertEqual([r["pct"] for r in rows], [100, 33, 0])
        self.assertEqual([r["done"] for r in rows], [True, False, False])
        self.assertEqual(over, 0)

    def test_over_shipping_one_drop_reduces_the_next(self):
        """The behaviour the owner asked for: a bigger delivery than planned
        updates the later plans instead of needing them rewritten."""
        rows = self._plan(("2026-09-05", 250), ("2026-09-19", 150))
        orders._allocate_drops(rows, 300)
        self.assertEqual(rows[1]["remaining"], 100.0)   # was 150 to go, now 100

    def test_surplus_beyond_every_drop_is_reported(self):
        rows = self._plan(("2026-09-05", 250), ("2026-09-19", 150))
        self.assertEqual(orders._allocate_drops(rows, 700), 300.0)
        self.assertTrue(all(r["done"] for r in rows))

    def test_nothing_shipped(self):
        rows = self._plan(("2026-09-05", 250))
        self.assertEqual(orders._allocate_drops(rows, 0), 0)
        self.assertEqual(rows[0]["delivered"], 0)
        self.assertEqual(rows[0]["pct"], 0)

    def test_no_plan_at_all(self):
        self.assertEqual(orders._allocate_drops([], 120), 120.0)

    def test_a_zero_quantity_drop_does_not_divide_by_zero(self):
        rows = [{"qty": 0, "due_date": "2026-09-05"}]
        orders._allocate_drops(rows, 10)
        self.assertEqual(rows[0]["pct"], 0)

    def test_it_reaches_the_order_record(self):
        oid = self.order(items=[{"description": "Flange", "qty": 600,
                                 "unit": "Nos", "rate": 1}])
        item = orders.get_order(self.conn, oid)["items"][0]
        orders.set_schedule(self.conn, item["id"], [
            {"due_date": "2026-09-05", "qty": 250},
            {"due_date": "2026-09-19", "qty": 150},
            {"due_date": "2026-11-30", "qty": 200}])
        orders.create_consignment(self.conn, {
            "consign_date": "2026-09-01", "lr_no": "LR-1",
            "lines": [{"order_item_id": item["id"], "qty": 300}]})
        it = orders.get_order(self.conn, oid)["items"][0]
        self.assertEqual([s["delivered"] for s in it["schedule"]], [250.0, 50.0, 0.0])
        self.assertEqual(it["over_delivered"], 0)


class OrderPaperTrail(WorkshopBase):
    """The order record carries every document raised against it."""

    def test_documents_ride_along_on_the_order(self):
        oid = self.order()
        quotations.create_doc(self.conn, "invoice", {
            "customer_id": self.cust, "doc_date": "2026-08-15", "tax_pct": 18,
            "order_id": oid,
            "lines": [{"description": "Shaft", "qty": 10, "unit": "Nos", "rate": 250}]})
        o = orders.get_order(self.conn, oid)
        self.assertEqual(len(o["documents"]), 1)
        d = o["documents"][0]
        self.assertEqual(d["kind"], "invoice")
        self.assertEqual(d["total"], round(10 * 250 * 1.18, 2))   # tax included
        self.assertTrue(d["doc_no"].startswith("AT/EI/"))

    def test_an_order_with_no_documents_says_so(self):
        self.assertEqual(orders.get_order(self.conn, self.order())["documents"], [])

    def test_customer_documents_name_their_order(self):
        """business() docs carry order_id so the past-orders table can hang
        each document on its order row."""
        oid = self.order(stage="payment")
        quotations.create_doc(self.conn, "invoice", {
            "customer_id": self.cust, "doc_date": "2026-08-15", "tax_pct": 0,
            "order_id": oid,
            "lines": [{"description": "Shaft", "qty": 1, "unit": "Nos", "rate": 99}]})
        biz = customers.business(self.conn, self.cust)
        linked = [d for d in biz["documents"] if d["order_id"] == oid]
        self.assertEqual(len(linked), 1)

    def test_deadline_rows_carry_the_order_id(self):
        """The home panel links each line to /orders/?open=<id> — no id, no link."""
        oid = self.order(due_date="2026-08-20")
        dl = orders.deadlines(self.conn, today="2026-08-15")
        every = dl["overdue"] + dl["this_week"] + dl["this_month"]
        self.assertIn(oid, [r["id"] for r in every])


class PartTypes(WorkshopBase):
    """A broad, additive family name on the drawing."""

    def test_round_trips_and_is_offered_back(self):
        did = parts.save_drawing(self.conn, {
            "drawing_no": "PT-1", "revision": "A", "part_type": "Piston rod"})
        self.assertEqual(parts.get_drawing(self.conn, did)["part_type"], "Piston rod")
        offered = [r["part_type"] for r in self.conn.execute(
            "SELECT DISTINCT part_type FROM drawing"
            " WHERE part_type IS NOT NULL AND part_type != ''")]
        self.assertIn("Piston rod", offered)

    def test_the_route_model_accepts_it(self):
        """Regression: DrawingIn silently DROPPED unknown fields, so the UI
        saved and the value vanished. The model must carry it."""
        self.assertEqual(parts.DrawingIn(
            drawing_no="X", part_type="Bush").part_type, "Bush")

    def test_search_finds_by_part_type(self):
        parts.save_drawing(self.conn, {
            "drawing_no": "PT-2", "revision": "A", "part_type": "Impeller"})
        hits = parts.list_drawings(self.conn, q="impell")
        self.assertEqual([h["drawing_no"] for h in hits], ["PT-2"])


class OverallDimensions(WorkshopBase):
    """The finished part's envelope, straight off the drawing."""

    def test_round_trip_and_blank(self):
        did = parts.save_drawing(self.conn, {
            "drawing_no": "OD-1", "overall_length_mm": "120.5",
            "overall_width_mm": 40})
        d = parts.get_drawing(self.conn, did)
        self.assertEqual((d["overall_length_mm"], d["overall_width_mm"]), (120.5, 40.0))
        # blank means "not on the drawing", never zero
        did2 = parts.save_drawing(self.conn, {"drawing_no": "OD-2",
                                              "overall_length_mm": ""})
        self.assertIsNone(parts.get_drawing(self.conn, did2)["overall_length_mm"])

    def test_nonsense_is_refused(self):
        for bad in (-5, 0, "abc", float("inf"), 1e9):
            with self.assertRaises(ValueError):
                parts.save_drawing(self.conn, {"drawing_no": "OD-BAD",
                                               "overall_length_mm": bad})

    def test_the_route_model_carries_them(self):
        """Same Pydantic trap as part_type: unknown fields vanish silently."""
        m = parts.DrawingIn(drawing_no="X", overall_length_mm=120,
                            overall_width_mm="40")
        self.assertEqual((m.overall_length_mm, m.overall_width_mm), (120, "40"))

    def test_a_new_revision_inherits_the_envelope(self):
        did = parts.save_drawing(self.conn, {
            "drawing_no": "OD-3", "overall_length_mm": 90})
        rid = parts.revise_drawing(self.conn, did, "B")
        self.assertEqual(parts.get_drawing(self.conn, rid)["overall_length_mm"], 90.0)


class DeliverySegments(WorkshopBase):
    """The stretches an order is split into — one progress bar each."""

    def test_the_unpromised_balance_becomes_its_own_segment(self):
        """A 600 item planned as 250 + 150 still owes 200; if that never
        appeared, the bars would not add up to the order."""
        segs, over = orders._segments(
            [{"qty": 250, "due_date": "2026-09-05"},
             {"qty": 150, "due_date": "2026-09-19"}], 600, 0)
        self.assertEqual([s["qty"] for s in segs], [250, 150, 200])
        self.assertEqual([s["planned"] for s in segs], [True, True, False])
        self.assertEqual(over, 0)

    def test_no_plan_is_one_segment_for_the_whole_item(self):
        segs, over = orders._segments([], 120, 30)
        self.assertEqual(len(segs), 1)
        self.assertEqual((segs[0]["qty"], segs[0]["delivered"], segs[0]["pct"]), (120, 30.0, 25))
        self.assertFalse(segs[0]["planned"])
        self.assertEqual(over, 0)

    def test_a_full_plan_adds_no_balance_segment(self):
        segs, _ = orders._segments([{"qty": 600, "due_date": "2026-09-05"}], 600, 0)
        self.assertEqual(len(segs), 1)
        self.assertTrue(segs[0]["planned"])

    def test_shipping_past_the_plan_fills_the_balance_before_over_delivering(self):
        segs, over = orders._segments(
            [{"qty": 250, "due_date": "2026-09-05"}], 400, 400)
        self.assertTrue(all(s["done"] for s in segs))
        self.assertEqual(over, 0)                       # 400 of 400 is not a surplus
        segs, over = orders._segments(
            [{"qty": 250, "due_date": "2026-09-05"}], 400, 450)
        self.assertEqual(over, 50.0)                    # past the ORDER, though

    def test_the_list_carries_one_drop_per_delivery(self):
        oid = self.order(items=[{"description": "Flange", "qty": 600,
                                 "unit": "Nos", "rate": 1}])
        item = orders.get_order(self.conn, oid)["items"][0]
        orders.set_schedule(self.conn, item["id"], [
            {"due_date": "2026-09-05", "qty": 250},
            {"due_date": "2026-09-19", "qty": 150},
            {"due_date": "2026-11-30", "qty": 200}])
        orders.create_consignment(self.conn, {
            "consign_date": "2026-09-01", "lr_no": "LR-2",
            "lines": [{"order_item_id": item["id"], "qty": 300}]})
        row = next(r for r in orders.list_orders(self.conn)["rows"] if r["id"] == oid)
        self.assertEqual([d["label"] for d in row["drops"]],
                         ["Drop 1 of 3", "Drop 2 of 3", "Drop 3 of 3"])
        self.assertEqual([d["pct"] for d in row["drops"]], [100, 33, 0])
        self.assertEqual(sum(d["qty"] for d in row["drops"]), row["qty_total"])

    def test_an_unplanned_order_still_gets_a_segment_to_draw(self):
        oid = self.order(items=[{"description": "Flange", "qty": 90,
                                 "unit": "Nos", "rate": 1}])
        row = next(r for r in orders.list_orders(self.conn)["rows"] if r["id"] == oid)
        self.assertEqual(len(row["drops"]), 1)
        self.assertEqual(row["drops"][0]["qty"], 90)
        self.assertFalse(row["drops"][0]["planned"])

    def test_drops_are_labelled_with_their_part(self):
        oid = self.order(items=[{"description": "Flange", "qty": 10,
                                 "unit": "Nos", "rate": 1},
                                {"description": "Cover", "qty": 20,
                                 "unit": "Nos", "rate": 1}])
        row = next(r for r in orders.list_orders(self.conn)["rows"] if r["id"] == oid)
        self.assertEqual(sorted(d["part"] for d in row["drops"]), ["Cover", "Flange"])

    def test_no_orders_means_no_queries(self):
        self.assertEqual(orders._order_drops(self.conn, []), {})


class QuotationRevisions(WorkshopBase):
    """A revision is a copy that supersedes its parent; the chain is history."""

    def quote(self, **kw):
        data = {"customer_id": self.cust, "doc_date": "2026-08-14", "tax_pct": 18,
                "lines": [{"description": "Shaft", "qty": 10, "unit": "Nos", "rate": 250},
                          {"description": "Cover", "qty": 4, "unit": "Nos", "rate": 90}]}
        data.update(kw)
        return quotations.create_doc(self.conn, "quotation", data)

    def test_the_letters_run_a_b_c_off_the_base_number(self):
        base = self.quote()
        base_no = quotations.get_doc(self.conn, base)["doc_no"]
        a = quotations.revise_doc(self.conn, base)
        b = quotations.revise_doc(self.conn, a)
        self.assertEqual(quotations.get_doc(self.conn, a)["doc_no"], f"{base_no} Rev-A")
        self.assertEqual(quotations.get_doc(self.conn, b)["doc_no"], f"{base_no} Rev-B")

    def test_only_the_tip_can_be_revised(self):
        base = self.quote()
        quotations.revise_doc(self.conn, base)
        with self.assertRaises(ValueError):
            quotations.revise_doc(self.conn, base)

    def test_the_copy_carries_the_header_and_every_line(self):
        base = self.quote(reference="RFQ-9", notes="hand written")
        rev = quotations.get_doc(self.conn, quotations.revise_doc(self.conn, base))
        self.assertEqual(rev["status"], "draft")
        self.assertEqual(rev["reference"], "RFQ-9")
        self.assertEqual(rev["notes"], "hand written")
        self.assertEqual([(l["description"], l["qty"], l["rate"]) for l in rev["lines"]],
                         [("Shaft", 10.0, 250.0), ("Cover", 4.0, 90.0)])
        self.assertEqual(rev["total"], quotations.get_doc(self.conn, base)["total"])
        self.assertEqual(rev["revises_document_id"], base)

    def test_the_parent_is_marked_superseded_and_the_chain_reads_forwards(self):
        base = self.quote()
        a = quotations.revise_doc(self.conn, base)
        b = quotations.revise_doc(self.conn, a)
        chain = quotations.get_doc(self.conn, base)["revisions"]
        self.assertEqual([c["id"] for c in chain], [base, a, b])      # oldest first
        self.assertEqual([c["active"] for c in chain], [False, False, True])
        self.assertEqual(quotations.get_doc(self.conn, base)["superseded_by"], a)
        # the same chain hangs off every member, not just the root
        self.assertEqual([c["id"] for c in quotations.get_doc(self.conn, b)["revisions"]],
                         [base, a, b])

    def test_a_document_nobody_revised_has_no_chain(self):
        self.assertEqual(quotations.get_doc(self.conn, self.quote())["revisions"], [])

    def test_the_list_says_which_rows_are_superseded(self):
        base = self.quote()
        a = quotations.revise_doc(self.conn, base)
        rows = {r["id"]: r for r in quotations.list_docs(self.conn)["rows"]}
        self.assertEqual(rows[base]["superseded_by"], a)
        self.assertIsNone(rows[a]["superseded_by"])

    def test_editing_a_revision_leaves_the_original_alone(self):
        base = self.quote()
        rev = quotations.revise_doc(self.conn, base)
        quotations.update_doc(self.conn, rev, {
            "customer_id": self.cust, "doc_date": "2026-08-20",
            "lines": [{"description": "Shaft", "qty": 10, "unit": "Nos", "rate": 300}]})
        self.assertEqual(quotations.get_doc(self.conn, base)["total"], 3374.8)
        self.assertEqual(len(quotations.get_doc(self.conn, base)["lines"]), 2)

    def test_the_marker_never_stacks(self):
        self.assertEqual(quotations._revision_base("T04/AT/130826/316 Rev-B"),
                         "T04/AT/130826/316")
        self.assertEqual([quotations._rev_letter(n) for n in (0, 1, 25, 26)],
                         ["A", "B", "Z", "AA"])


class OrderAttachments(WorkshopBase):
    """The intake paperwork: what the customer sent us, filed on the order."""

    def test_upload_shows_up_on_the_order(self):
        oid = self.order()
        saved = orders.save_order_attachments(self.conn, oid, "Enquiry e-mail", [
            ("enquiry.pdf", "application/pdf", b"%PDF-1.4 enquiry")])
        self.assertEqual(len(saved), 1)
        att = orders.get_order(self.conn, oid)["attachments"]
        self.assertEqual([(a["label"], a["filename"], a["size_bytes"]) for a in att],
                         [("Enquiry e-mail", "enquiry.pdf", 16)])
        self.assertTrue(att[0]["uploaded_at"])

    def test_the_batch_is_all_or_nothing(self):
        from backend.core import paths
        oid = self.order()
        with self.assertRaises(ValueError):
            orders.save_order_attachments(self.conn, oid, "PO", [
                ("ok.pdf", "application/pdf", b"%PDF ok"),
                ("bad.exe", "application/x-msdownload", b"MZ")])
        self.assertEqual(orders.get_order(self.conn, oid)["attachments"], [])
        self.assertEqual(list(paths.order_files_dir().iterdir()), [])

    def test_delete_removes_the_row_and_the_file(self):
        from backend.core import paths
        oid = self.order()
        meta = orders.save_order_attachments(
            self.conn, oid, "PO", [("po.pdf", "application/pdf", b"%PDF")])[0]
        self.assertEqual(len(list(paths.order_files_dir().iterdir())), 1)
        o = orders.delete_order_attachment(self.conn, meta["id"])
        self.assertEqual(o["attachments"], [])
        self.assertEqual(list(paths.order_files_dir().iterdir()), [])
        with self.assertRaises(ValueError):
            orders.delete_order_attachment(self.conn, meta["id"])

    def test_an_unknown_order_is_refused(self):
        with self.assertRaises(ValueError):
            orders.save_order_attachments(self.conn, 9999, "", [
                ("x.pdf", "application/pdf", b"%PDF")])

    def test_the_items_list_their_drawings_files(self):
        did = self.drawing()
        parts.save_files(self.conn, did, [("shaft.pdf", "application/pdf", b"%PDF"),
                                          ("shaft.png", "image/png", b"\x89PNG")])
        other = self.drawing(drawing_no="DRG-200")
        oid = self.order(items=[{"drawing_id": did, "qty": 5, "rate": 10},
                                {"drawing_id": other, "qty": 5, "rate": 10},
                                {"description": "free text", "qty": 1, "rate": 10}])
        items = orders.get_order(self.conn, oid)["items"]
        self.assertEqual([f["filename"] for f in items[0]["drawing_files"]],
                         ["shaft.pdf", "shaft.png"])
        self.assertEqual(items[1]["drawing_files"], [])
        self.assertEqual(items[2]["drawing_files"], [])   # no drawing, no files


class OutsourcedLineFields(WorkshopBase):
    """os_item_id rides on the line models now, so the pickers can land later
    without a migration. Pydantic drops what it doesn't declare."""

    def test_the_quotation_line_model_carries_it(self):
        self.assertEqual(quotations.LineIn(qty=1, os_item_id=7).os_item_id, 7)
        self.assertIsNone(quotations.LineIn(qty=1).os_item_id)

    def test_the_costing_bom_line_model_carries_it(self):
        self.assertEqual(parts.BomLineIn(os_item_id=7).os_item_id, 7)
        self.assertIsNone(parts.BomLineIn().os_item_id)

    def test_a_quotation_line_persists_it(self):
        osid = self.os_item()
        did = quotations.create_doc(self.conn, "quotation", {
            "customer_id": self.cust, "doc_date": "2026-08-14",
            "lines": [{"description": "Bought-out bush", "qty": 2, "rate": 40,
                       "os_item_id": osid}]})
        self.assertEqual(quotations.get_doc(self.conn, did)["lines"][0]["os_item_id"], osid)

    def test_a_costing_material_persists_it(self):
        osid = self.os_item()
        did = self.drawing()
        d = parts.save_costing(self.conn, did, {
            "materials": [{"material_label": "Bought-out bush", "unit_cost": 40,
                           "qty_per_piece": 1, "os_item_id": osid}]})
        self.assertEqual(d["costings"][0]["materials"][0]["os_item_id"], osid)

    def test_a_dangling_outsourced_item_is_a_400_not_a_500(self):
        with self.assertRaises(ValueError):
            quotations.create_doc(self.conn, "quotation", {
                "customer_id": self.cust, "doc_date": "2026-08-14",
                "lines": [{"description": "x", "qty": 1, "rate": 1, "os_item_id": 9999}]})
        with self.assertRaises(ValueError):
            parts.save_costing(self.conn, self.drawing(), {
                "materials": [{"material_label": "x", "unit_cost": 1,
                               "qty_per_piece": 1, "os_item_id": 9999}]})

    def os_item(self) -> int:
        """A row in the outsourced-stock table the modules land on in wave 4."""
        cur = self.conn.execute(
            "INSERT INTO os_item (os_id, description, qty) VALUES (?,?,?)",
            (numbering.os_item_id(self.conn), "Bought-out bush", 10))
        self.conn.commit()
        return cur.lastrowid
