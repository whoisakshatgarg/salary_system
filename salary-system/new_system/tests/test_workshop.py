"""Spec for the workshop modules: Settings, Customers, Parts & Pricing, Orders."""

import os
import sys
import tempfile
import unittest
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend.core import db  # noqa: E402
from backend.modules import (customers, orders, parts, quotations,  # noqa: E402
                             settings)


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
    def test_abbreviation_rules(self):
        cases = [("Acme Castings", "AC"), ("Bharat Hydraulics Pvt Ltd", "BH"),
                 ("Sterling", "ST"), ("M/s Tata Steel", "TS"), ("", "XX")]
        for name, expect in cases:
            self.assertEqual(customers.abbreviate(name), expect, name)

    def test_serial_within_abbreviation(self):
        a = customers.save_customer(self.conn, {"name": "Acme Castings"})
        b = customers.save_customer(self.conn, {"name": "Anand Components"})
        c = customers.save_customer(self.conn, {"name": "Bharat Gears"})
        codes = {customers.get_customer(self.conn, i)["code"] for i in (a, b, c)}
        self.assertEqual(codes, {"AC01", "AC02", "BG01"})

    def test_manual_abbreviation_and_backfill(self):
        cid = customers.save_customer(self.conn, {"name": "Zenith Works", "abbr": "zw"})
        self.assertEqual(customers.get_customer(self.conn, cid)["code"], "ZW01")
        self.conn.execute("UPDATE customer SET code=NULL WHERE id=?", (cid,))
        self.conn.commit()
        self.assertEqual(customers.backfill_codes(self.conn), 1)
        self.assertTrue(customers.get_customer(self.conn, cid)["code"])

    def test_business_view(self):
        self.order(); self.order()
        biz = customers.business(self.conn, self.cust)
        self.assertEqual(biz["stats"]["order_count"], 2)
        self.assertEqual(biz["stats"]["total_business"], 5000.0)   # 2 x 10 x 250
        self.assertEqual(biz["stats"]["avg_order"], 2500.0)
        self.assertEqual(len(biz["series"]), 1)                    # both in 2026-08
        self.assertEqual(biz["series"][0]["cumulative"], 5000.0)


class CostingWeighting(WorkshopBase):
    def test_weightage_and_extra_margin(self):
        self.assertEqual(parts.op_cost(12, 400), 80.0)              # plain
        self.assertEqual(parts.op_cost(12, 400, 1.25), 100.0)       # weighted
        self.assertEqual(parts.op_cost(12, 400, 1.25, 10), 110.0)   # + row margin

    def test_saved_costing_uses_the_columns(self):
        did = self.drawing()
        d = parts.save_costing(self.conn, did, {
            "material_cost": 80, "margin_pct": 20,
            "ops": [{"operation": "Turning", "minutes": 12, "rate_per_hour": 400,
                     "weightage": 1.25, "extra_margin_pct": 10}]})
        c = d["costings"][0]
        self.assertEqual(c["ops"][0]["cost"], 110.0)
        self.assertEqual(c["ops"][0]["weightage"], 1.25)
        self.assertEqual(c["total"], 228.0)                          # (110+80) * 1.2
        d = parts.costing_to_rate(self.conn, c["id"], "agreed")
        self.assertEqual(d["rates"][0]["rate"], 228.0)               # recorded == shown

    def test_defaults_when_columns_left_blank(self):
        did = self.drawing()
        d = parts.save_costing(self.conn, did, {
            "ops": [{"operation": "Turning", "minutes": 12, "rate_per_hour": 400}]})
        self.assertEqual(d["costings"][0]["ops"][0]["cost"], 80.0)   # weightage defaults to 1


class QuotationsAndInvoices(WorkshopBase):
    def doc(self, kind="quotation", **kw):
        data = {"customer_id": self.cust, "doc_date": "2026-08-14", "tax_pct": 18,
                "lines": [{"description": "Shaft", "qty": 10, "unit": "Nos", "rate": 250}]}
        data.update(kw)
        return quotations.create_doc(self.conn, kind, data)

    def test_numbering_per_kind_and_fy(self):
        q1 = quotations.get_doc(self.conn, self.doc())
        q2 = quotations.get_doc(self.conn, self.doc())
        i1 = quotations.get_doc(self.conn, self.doc("invoice"))
        self.assertEqual(q1["doc_no"], "QUO-26-27-001")
        self.assertEqual(q2["doc_no"], "QUO-26-27-002")
        self.assertEqual(i1["doc_no"], "INV-26-27-001")   # its own sequence
        nxt = quotations.get_doc(self.conn, self.doc(doc_date="2027-04-02"))
        self.assertEqual(nxt["doc_no"], "QUO-27-28-001")  # new FY restarts

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
