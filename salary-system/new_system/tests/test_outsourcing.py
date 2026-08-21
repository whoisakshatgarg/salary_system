"""Spec for the outsourcing module (SOP-DESIGN §9).

The shape of the world: a vendor, a numbered job order sent out with a
deadline, receipts back against its lines, and the bought-out stock those
receipts create. What these tests actually guard is the arithmetic that can
lose money or invent it — over-receipt, over-issue, a deleted receipt taking
stock negative, a status that stops following the quantities — plus the two
regressions that bite silently: a burned document number and a Pydantic field
that never reaches the database.
"""

import os
import sys
import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend.core import db, numbering, paths  # noqa: E402
from backend.modules import (customers, inventory, orders, outsourcing,  # noqa: E402
                             settings)


class OutsourcingBase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        os.environ["SALARY_DATA_DIR"] = self.tmp.name
        self.db_path = Path(self.tmp.name) / "test.db"
        db.init_db(self.db_path)
        settings.ensure_defaults(self.db_path)
        self.conn = db.connect(self.db_path)
        self.cust = customers.save_customer(self.conn, {"name": "Acme Pumps"})

    def tearDown(self):
        self.conn.close()
        os.environ.pop("SALARY_DATA_DIR", None)
        self.tmp.cleanup()

    # ---- fixtures ---------------------------------------------------------- #
    def vendor(self, **kw) -> int:
        data = {"name": "Sharma Plating Works", "services": "Zinc plating",
                "phone": "9810000000"}
        data.update(kw)
        return outsourcing.save_vendor(self.conn, data)

    def os_order(self, **kw) -> int:
        data = {"vendor_id": kw.pop("vendor_id", None) or self.vendor(),
                "purpose": "Zinc plating", "date_sent": "2026-08-14",
                "deadline": "2026-08-28",
                "items": [{"description": "Plastic cap", "part_code": "TPS-0353",
                           "qty": 100, "unit": "Nos", "unit_cost": 12.5}]}
        data.update(kw)
        return outsourcing.create_os_order(self.conn, data)

    def internal_order(self, **kw) -> int:
        data = {"customer_id": self.cust, "stage": "po", "order_date": "2026-08-14",
                "items": [{"description": "Shaft", "qty": 10, "unit": "Nos", "rate": 250}]}
        data.update(kw)
        return orders.create_order(self.conn, data)

    def receive(self, os_order_id: int, qty: float, **kw) -> int:
        items = outsourcing.get_os_order(self.conn, os_order_id)["items"]
        line = {"os_order_item_id": kw.pop("item_id", None) or items[0]["id"], "qty": qty}
        line.update(kw.pop("line", {}))
        data = {"os_order_id": os_order_id, "receipt_date": "2026-08-20",
                "lines": [line]}
        data.update(kw)
        return outsourcing.create_receipt(self.conn, data)


# --------------------------------------------------------------------------- #
class Vendors(OutsourcingBase):
    def test_a_blank_code_is_assigned_from_the_series(self):
        self.assertEqual(outsourcing.get_vendor(self.conn, self.vendor())["code"], "V01")
        self.assertEqual(
            outsourcing.get_vendor(self.conn, self.vendor(name="Delta Heat"))["code"], "V02")

    def test_a_typed_code_is_taken_at_its_word(self):
        """The office may already write something the V01 series would never
        produce, so the field is normalised, not policed."""
        self.assertEqual(outsourcing.get_vendor(
            self.conn, self.vendor(code=" v-7 "))["code"], "V7")
        self.assertEqual(outsourcing.get_vendor(
            self.conn, self.vendor(name="Delta Heat", code="plat01"))["code"], "PLAT01")

    def test_a_code_can_be_corrected_later(self):
        vid = self.vendor()
        outsourcing.save_vendor(self.conn, {"name": "Sharma Plating Works",
                                            "code": "V44"}, vid)
        self.assertEqual(outsourcing.get_vendor(self.conn, vid)["code"], "V44")

    def test_editing_with_the_code_blank_keeps_it(self):
        vid = self.vendor()
        outsourcing.save_vendor(self.conn, {"name": "Sharma Plating", "code": ""}, vid)
        v = outsourcing.get_vendor(self.conn, vid)
        self.assertEqual((v["code"], v["name"]), ("V01", "Sharma Plating"))

    def test_a_taken_code_names_who_holds_it(self):
        self.vendor()
        with self.assertRaises(ValueError) as e:
            self.vendor(name="Delta Heat", code="V01")
        self.assertIn("Sharma Plating Works", str(e.exception))

    def test_a_duplicate_name_is_refused(self):
        self.vendor()
        with self.assertRaises(ValueError):
            self.vendor()

    def test_a_nameless_vendor_is_refused(self):
        with self.assertRaises(ValueError):
            outsourcing.save_vendor(self.conn, {"name": "  "})
        with self.assertRaises(ValueError):
            outsourcing.save_vendor(self.conn, {"name": "Ghost"}, 9999)

    def test_deactivating_hides_it_from_the_list_but_keeps_the_record(self):
        vid = self.vendor()
        outsourcing.set_vendor_active(self.conn, vid, False)
        self.assertEqual(outsourcing.list_vendors(self.conn), [])
        self.assertEqual(len(outsourcing.list_vendors(self.conn, active_only=False)), 1)
        self.assertEqual(outsourcing.get_vendor(self.conn, vid)["active"], 0)

    def test_the_record_carries_the_history(self):
        vid = self.vendor()
        oid = self.os_order(vendor_id=vid)
        self.receive(oid, 40)
        v = outsourcing.get_vendor(self.conn, vid)
        self.assertEqual([o["os_no"] for o in v["orders"]], ["AT/OS/26-27/001"])
        self.assertEqual(v["orders"][0]["status"], "partial")
        self.assertEqual(v["orders"][0]["deadline"], "2026-08-28")
        self.assertEqual(v["receipts"], 1)
        self.assertEqual([s["os_id"] for s in v["stock"]], ["OS-0001"])

    def test_the_list_counts_open_jobs(self):
        vid = self.vendor()
        self.os_order(vendor_id=vid)
        oid = self.os_order(vendor_id=vid)
        outsourcing.set_os_status(self.conn, oid, "cancelled")
        row = outsourcing.list_vendors(self.conn)[0]
        self.assertEqual((row["jobs"], row["open_jobs"]), (2, 1))


# --------------------------------------------------------------------------- #
class OutgoingOrders(OutsourcingBase):
    def test_the_os_number_comes_from_the_registry(self):
        self.assertEqual(
            outsourcing.get_os_order(self.conn, self.os_order())["os_no"],
            "AT/OS/26-27/001")

    def test_a_double_save_consumes_the_number_once_each(self):
        """The double-clicked Save: two orders, two consecutive numbers, no
        collision and no gap."""
        vid = self.vendor()
        a = outsourcing.get_os_order(self.conn, self.os_order(vendor_id=vid))
        b = outsourcing.get_os_order(self.conn, self.os_order(vendor_id=vid))
        self.assertEqual([a["os_no"], b["os_no"]],
                         ["AT/OS/26-27/001", "AT/OS/26-27/002"])
        self.assertEqual(numbering.peek(self.conn, "os_po:26-27"), 3)

    def test_a_refused_save_does_not_burn_a_number(self):
        vid = self.vendor()
        self.os_order(vendor_id=vid)
        with self.assertRaises(ValueError):
            self.os_order(vendor_id=9999)        # dangling vendor, rolled back
        self.assertEqual(
            outsourcing.get_os_order(self.conn, self.os_order(vendor_id=vid))["os_no"],
            "AT/OS/26-27/002")

    def test_it_needs_a_vendor_a_line_and_a_real_deadline(self):
        with self.assertRaises(ValueError):
            outsourcing.create_os_order(self.conn, {"date_sent": "2026-08-14",
                                                    "items": [{"description": "x", "qty": 1}]})
        with self.assertRaises(ValueError):
            outsourcing.create_os_order(self.conn, {"vendor_id": self.vendor(),
                                                    "date_sent": "2026-08-14", "items": []})
        with self.assertRaises(ValueError):
            self.os_order(deadline="2026-13-45")

    def test_a_line_needs_something_to_call_it_and_a_positive_quantity(self):
        with self.assertRaises(ValueError):
            self.os_order(items=[{"description": "", "part_code": "", "qty": 5}])
        with self.assertRaises(ValueError):
            self.os_order(items=[{"description": "Cap", "qty": 0}])

    def test_the_list_reports_progress_and_days_left(self):
        oid = self.os_order(deadline=(date.today() + timedelta(days=3)).isoformat())
        self.receive(oid, 25)
        row = outsourcing.list_os_orders(self.conn)["rows"][0]
        self.assertEqual((row["qty_total"], row["qty_received"], row["pct_received"]),
                         (100.0, 25.0, 25))
        self.assertEqual(row["days_left"], 3)
        self.assertEqual(row["vendor_name"], "Sharma Plating Works")
        self.assertEqual(row["value"], 1250.0)          # 100 x 12.50 agreed rate

    def test_the_list_filters(self):
        v1, v2 = self.vendor(), self.vendor(name="Delta Heat")
        self.os_order(vendor_id=v1, purpose="Zinc plating")
        b = self.os_order(vendor_id=v2, purpose="Hardening",
                          items=[{"description": "Spindle", "qty": 5}])
        outsourcing.set_os_status(self.conn, b, "closed")
        self.assertEqual(len(outsourcing.list_os_orders(self.conn, vendor_id=v2)["rows"]), 1)
        self.assertEqual(len(outsourcing.list_os_orders(self.conn, status="closed")["rows"]), 1)
        self.assertEqual(len(outsourcing.list_os_orders(self.conn, q="Spindle")["rows"]), 1)
        self.assertEqual(len(outsourcing.list_os_orders(self.conn, q="Zinc")["rows"]), 1)
        self.assertEqual(outsourcing.list_os_orders(self.conn)["status_counts"],
                         {"open": 1, "closed": 1})

    def test_it_can_serve_part_of_an_internal_order(self):
        oid = self.internal_order()
        item = orders.get_order(self.conn, oid)["items"][0]["id"]
        osid = self.os_order(order_id=oid,
                             items=[{"description": "Shaft — plating", "qty": 4,
                                     "order_item_id": item}])
        d = outsourcing.get_os_order(self.conn, osid)
        self.assertEqual(d["order"]["order_no"], orders.get_order(self.conn, oid)["order_no"])
        self.assertEqual(d["order"]["customer_name"], "Acme Pumps")
        self.assertEqual(d["items"][0]["order_item_id"], item)

    def test_a_part_of_an_unlinked_order_is_refused(self):
        oid = self.internal_order()
        item = orders.get_order(self.conn, oid)["items"][0]["id"]
        with self.assertRaises(ValueError):        # no header link
            self.os_order(items=[{"description": "Shaft", "qty": 4,
                                  "order_item_id": item}])
        other = self.internal_order()
        with self.assertRaises(ValueError):        # linked to the WRONG order
            self.os_order(order_id=other,
                          items=[{"description": "Shaft", "qty": 4,
                                  "order_item_id": item}])

    def test_the_detail_embeds_lines_receipts_and_documents(self):
        oid = self.os_order()
        self.receive(oid, 30)
        outsourcing.save_os_documents(self.conn, None, oid, "Vendor challan",
                                      [("challan.pdf", "application/pdf", b"%PDF")])
        d = outsourcing.get_os_order(self.conn, oid)
        self.assertEqual((d["items"][0]["received"], d["items"][0]["pending"]), (30.0, 70.0))
        self.assertEqual(len(d["receipts"]), 1)
        self.assertEqual(d["receipts"][0]["lines"], 1)
        self.assertEqual([x["label"] for x in d["documents"]], ["Vendor challan"])
        self.assertIsNone(d["order"])


# --------------------------------------------------------------------------- #
class Editing(OutsourcingBase):
    def test_lines_keep_their_ids_across_a_save(self):
        oid = self.os_order()
        first = outsourcing.get_os_order(self.conn, oid)["items"][0]["id"]
        outsourcing.update_os_order(self.conn, oid, {
            "vendor_id": outsourcing.get_os_order(self.conn, oid)["vendor_id"],
            "date_sent": "2026-08-14",
            "items": [{"id": first, "description": "Plastic cap", "qty": 120},
                      {"description": "Gasket", "qty": 20}]})
        items = outsourcing.get_os_order(self.conn, oid)["items"]
        self.assertEqual([i["id"] for i in items], [first, first + 1])
        self.assertEqual(items[0]["qty"], 120.0)

    def test_a_line_cannot_shrink_below_what_came_back(self):
        oid = self.os_order()
        d = outsourcing.get_os_order(self.conn, oid)
        self.receive(oid, 60)
        with self.assertRaises(ValueError) as e:
            outsourcing.update_os_order(self.conn, oid, {
                "vendor_id": d["vendor_id"], "date_sent": "2026-08-14",
                "items": [{"id": d["items"][0]["id"], "description": "Plastic cap",
                           "qty": 50}]})
        self.assertIn("60", str(e.exception))
        self.assertEqual(outsourcing.get_os_order(self.conn, oid)["items"][0]["qty"], 100.0)

    def test_a_received_line_cannot_be_removed(self):
        oid = self.os_order()
        d = outsourcing.get_os_order(self.conn, oid)
        self.receive(oid, 10)
        with self.assertRaises(ValueError):
            outsourcing.update_os_order(self.conn, oid, {
                "vendor_id": d["vendor_id"], "date_sent": "2026-08-14",
                "items": [{"description": "Something else", "qty": 5}]})

    def test_an_untouched_line_can_still_be_dropped(self):
        oid = self.os_order(items=[{"description": "Cap", "qty": 10},
                                   {"description": "Gasket", "qty": 5}])
        d = outsourcing.get_os_order(self.conn, oid)
        outsourcing.update_os_order(self.conn, oid, {
            "vendor_id": d["vendor_id"], "date_sent": "2026-08-14",
            "items": [{"id": d["items"][0]["id"], "description": "Cap", "qty": 10}]})
        self.assertEqual(len(outsourcing.get_os_order(self.conn, oid)["items"]), 1)

    def test_shrinking_the_order_re_derives_the_status(self):
        oid = self.os_order()
        d = outsourcing.get_os_order(self.conn, oid)
        self.receive(oid, 60)
        self.assertEqual(outsourcing.get_os_order(self.conn, oid)["status"], "partial")
        outsourcing.update_os_order(self.conn, oid, {
            "vendor_id": d["vendor_id"], "date_sent": "2026-08-14",
            "items": [{"id": d["items"][0]["id"], "description": "Plastic cap",
                       "qty": 60}]})
        self.assertEqual(outsourcing.get_os_order(self.conn, oid)["status"], "received")


# --------------------------------------------------------------------------- #
class Status(OutsourcingBase):
    def test_it_walks_open_partial_received_on_its_own(self):
        oid = self.os_order()
        self.assertEqual(outsourcing.get_os_order(self.conn, oid)["status"], "open")
        self.receive(oid, 30)
        self.assertEqual(outsourcing.get_os_order(self.conn, oid)["status"], "partial")
        self.receive(oid, 70)
        self.assertEqual(outsourcing.get_os_order(self.conn, oid)["status"], "received")

    def test_closed_and_cancelled_are_set_by_hand(self):
        oid = self.os_order()
        self.assertEqual(outsourcing.set_os_status(self.conn, oid, "closed")["status"],
                         "closed")
        self.assertEqual(outsourcing.set_os_status(self.conn, oid, "cancelled")["status"],
                         "cancelled")

    def test_the_derived_three_are_refused_by_hand(self):
        oid = self.os_order()
        for bad in ("open", "partial", "received"):
            with self.assertRaises(ValueError) as e:
                outsourcing.set_os_status(self.conn, oid, bad)
            self.assertIn("receipt", str(e.exception))
        with self.assertRaises(ValueError):
            outsourcing.set_os_status(self.conn, oid, "posted")

    def test_a_recount_never_reopens_a_closed_order(self):
        oid = self.os_order()
        outsourcing.set_os_status(self.conn, oid, "closed")
        self.receive(oid, 20)
        self.assertEqual(outsourcing.get_os_order(self.conn, oid)["status"], "closed")

    def test_an_unknown_order_is_not_found(self):
        with self.assertRaises(ValueError):
            outsourcing.set_os_status(self.conn, 9999, "closed")


class Reopening(OutsourcingBase):
    """Taking back a Close or a Cancel. What it becomes is still counted, not
    chosen — reopen lands exactly where a receipt would have left it."""

    def test_closed_reopens_as_partial_when_some_came_back(self):
        oid = self.os_order()
        self.receive(oid, 60)
        outsourcing.set_os_status(self.conn, oid, "closed")
        self.assertEqual(outsourcing.reopen_os_order(self.conn, oid)["status"], "partial")
        self.assertEqual(outsourcing.get_os_order(self.conn, oid)["status"], "partial")

    def test_cancelled_reopens_as_open_when_nothing_came_back(self):
        oid = self.os_order()
        outsourcing.set_os_status(self.conn, oid, "cancelled")
        self.assertEqual(outsourcing.reopen_os_order(self.conn, oid)["status"], "open")

    def test_it_reopens_as_received_when_everything_is_already_back(self):
        """A job closed after a late delivery re-derives to 'received', not to
        the 'open' it was parked at on the way through."""
        oid = self.os_order()
        outsourcing.set_os_status(self.conn, oid, "closed")
        self.receive(oid, 100)            # a recount never reopens it by itself
        self.assertEqual(outsourcing.get_os_order(self.conn, oid)["status"], "closed")
        self.assertEqual(outsourcing.reopen_os_order(self.conn, oid)["status"], "received")

    def test_a_live_job_has_nothing_to_reopen(self):
        oid = self.os_order()
        for _ in range(2):                # open, then partial
            with self.assertRaises(ValueError) as e:
                outsourcing.reopen_os_order(self.conn, oid)
            self.assertIn("closed or cancelled", str(e.exception))
            self.receive(oid, 30)
        self.assertEqual(outsourcing.get_os_order(self.conn, oid)["status"], "partial")

    def test_reopen_is_an_action_not_a_status(self):
        oid = self.os_order()
        outsourcing.set_os_status(self.conn, oid, "closed")
        with self.assertRaises(ValueError) as e:
            outsourcing.set_os_status(self.conn, oid, "reopen")
        self.assertIn("its own action", str(e.exception))
        self.assertEqual(outsourcing.get_os_order(self.conn, oid)["status"], "closed")

    def test_an_unknown_order_is_not_found(self):
        with self.assertRaises(ValueError):
            outsourcing.reopen_os_order(self.conn, 9999)

    def test_a_reopened_job_is_back_on_the_deadline_panel(self):
        oid = self.os_order(deadline="2026-08-24")
        outsourcing.set_os_status(self.conn, oid, "cancelled")
        out = outsourcing.deadlines(self.conn, today="2026-08-21")
        self.assertEqual(out["this_week"], [])
        outsourcing.reopen_os_order(self.conn, oid)
        out = outsourcing.deadlines(self.conn, today="2026-08-21")
        self.assertEqual([r["id"] for r in out["this_week"]], [oid])


# --------------------------------------------------------------------------- #
class Receipts(OutsourcingBase):
    def test_the_first_receipt_creates_os_0001_then_os_0002(self):
        oid = self.os_order(items=[{"description": "Cap", "qty": 10, "unit_cost": 4},
                                   {"description": "Gasket", "qty": 10}])
        items = outsourcing.get_os_order(self.conn, oid)["items"]
        outsourcing.create_receipt(self.conn, {
            "os_order_id": oid, "receipt_date": "2026-08-20",
            "lines": [{"os_order_item_id": items[0]["id"], "qty": 10},
                      {"os_order_item_id": items[1]["id"], "qty": 10}]})
        rows = outsourcing.list_os_items(self.conn)["rows"]
        self.assertEqual(sorted(r["os_id"] for r in rows), ["OS-0001", "OS-0002"])

    def test_a_new_stock_row_inherits_the_order_line(self):
        oid = self.os_order()
        self.receive(oid, 40)
        s = outsourcing.list_os_items(self.conn)["rows"][0]
        self.assertEqual(s["description"], "Plastic cap")
        self.assertEqual(s["part_code"], "TPS-0353")
        self.assertEqual(s["unit"], "Nos")
        self.assertEqual(s["unit_cost"], 12.5)          # the vendor's agreed rate
        self.assertEqual(s["qty"], 40.0)
        self.assertEqual(s["received_date"], "2026-08-20")
        self.assertEqual(s["vendor_name"], "Sharma Plating Works")
        self.assertEqual(s["source_os_no"], "AT/OS/26-27/001")

    def test_the_receipt_line_can_describe_the_stock_itself(self):
        oid = self.os_order()
        self.receive(oid, 40, line={"description": "Plastic cap, plated",
                                    "material": "HDPE", "size_section": '1/4"NPT',
                                    "unit_cost": 15})
        s = outsourcing.list_os_items(self.conn)["rows"][0]
        self.assertEqual((s["description"], s["material"], s["size_section"],
                          s["unit_cost"]),
                         ("Plastic cap, plated", "HDPE", '1/4"NPT', 15.0))

    def test_the_top_up_path_adds_to_the_same_row_and_logs_it(self):
        oid = self.os_order()
        self.receive(oid, 40)
        stock = outsourcing.list_os_items(self.conn)["rows"][0]
        self.receive(oid, 25, line={"os_item_id": stock["id"]},
                     receipt_date="2026-08-25")
        rows = outsourcing.list_os_items(self.conn)["rows"]
        self.assertEqual(len(rows), 1)                  # topped up, not duplicated
        self.assertEqual(rows[0]["qty"], 65.0)
        self.assertEqual(rows[0]["received_date"], "2026-08-25")
        mv = outsourcing.os_movements(self.conn, stock["id"])
        self.assertEqual([(m["type"], m["qty"]) for m in mv],
                         [("receive", 25.0), ("receive", 40.0)])
        self.assertIn("AT/OS/26-27/001", mv[0]["remarks"])

    def test_over_receipt_is_refused(self):
        oid = self.os_order()
        self.receive(oid, 60)
        with self.assertRaises(ValueError) as e:
            self.receive(oid, 41)
        self.assertIn("Only 40 of 100 left to receive", str(e.exception))
        self.assertEqual(outsourcing.get_os_order(self.conn, oid)["qty_received"], 60.0)

    def test_two_rows_for_one_line_are_summed_before_the_check(self):
        """One by one they would each pass against the same stored total."""
        oid = self.os_order()
        item = outsourcing.get_os_order(self.conn, oid)["items"][0]["id"]
        with self.assertRaises(ValueError):
            outsourcing.create_receipt(self.conn, {
                "os_order_id": oid, "receipt_date": "2026-08-20",
                "lines": [{"os_order_item_id": item, "qty": 60},
                          {"os_order_item_id": item, "qty": 60}]})
        self.assertEqual(outsourcing.list_os_items(self.conn)["rows"], [])

    def test_a_zero_or_negative_quantity_is_refused(self):
        oid = self.os_order()
        for bad in (0, -5):
            with self.assertRaises(ValueError):
                self.receive(oid, bad)

    def test_a_line_from_another_order_is_refused(self):
        a, b = self.os_order(), self.os_order(vendor_id=self.vendor(name="Delta Heat"))
        stray = outsourcing.get_os_order(self.conn, b)["items"][0]["id"]
        with self.assertRaises(ValueError):
            outsourcing.create_receipt(self.conn, {
                "os_order_id": a, "receipt_date": "2026-08-20",
                "lines": [{"os_order_item_id": stray, "qty": 1}]})

    def test_a_rejected_delivery_is_still_recorded(self):
        oid = self.os_order()
        rid = outsourcing.create_receipt(self.conn, {
            "os_order_id": oid, "receipt_date": "2026-08-20", "accepted": False,
            "inspection_notes": "Plating peeling on 6 pieces",
            "lines": [{"os_order_item_id":
                       outsourcing.get_os_order(self.conn, oid)["items"][0]["id"],
                       "qty": 20}]})
        r = outsourcing.get_receipt(self.conn, rid)
        self.assertEqual(r["accepted"], 0)
        self.assertEqual(r["inspection_notes"], "Plating peeling on 6 pieces")
        self.assertEqual(r["lines"][0]["os_id"], "OS-0001")

    def test_the_list_carries_the_vendor_and_the_line_count(self):
        oid = self.os_order()
        self.receive(oid, 10)
        row = outsourcing.list_receipts(self.conn)[0]
        self.assertEqual((row["os_no"], row["vendor_name"], row["lines"], row["qty"]),
                         ("AT/OS/26-27/001", "Sharma Plating Works", 1, 10.0))
        self.assertEqual(len(outsourcing.list_receipts(self.conn, q="Sharma")), 1)
        self.assertEqual(len(outsourcing.list_receipts(self.conn, os_order_id=oid)), 1)


# --------------------------------------------------------------------------- #
class DeletingAReceipt(OutsourcingBase):
    def test_it_puts_the_stock_back_out_again(self):
        oid = self.os_order()
        rid = self.receive(oid, 40)
        stock = outsourcing.list_os_items(self.conn)["rows"][0]
        outsourcing.delete_receipt(self.conn, rid)
        self.assertEqual(outsourcing.get_os_item(self.conn, stock["id"])["qty"], 0.0)
        self.assertEqual(outsourcing.get_os_order(self.conn, oid)["status"], "open")
        self.assertEqual(outsourcing.list_receipts(self.conn), [])

    def test_the_reversal_is_logged_not_erased(self):
        oid = self.os_order()
        rid = self.receive(oid, 40)
        stock = outsourcing.list_os_items(self.conn)["rows"][0]
        outsourcing.delete_receipt(self.conn, rid)
        mv = outsourcing.os_movements(self.conn, stock["id"])
        self.assertEqual([(m["type"], m["qty"]) for m in mv],
                         [("adjust", -40.0), ("receive", 40.0)])
        self.assertIn("2026-08-20", mv[0]["remarks"])

    def test_it_is_refused_when_the_goods_have_gone_out(self):
        oid = self.os_order()
        rid = self.receive(oid, 40)
        stock = outsourcing.list_os_items(self.conn)["rows"][0]
        outsourcing.issue_os_item(self.conn, stock["id"], {
            "qty": 35, "order_id": "ORD-26-27-001", "mv_date": "2026-08-21"})
        with self.assertRaises(ValueError) as e:
            outsourcing.delete_receipt(self.conn, rid)
        self.assertIn("OS-0001", str(e.exception))
        self.assertEqual(outsourcing.get_os_item(self.conn, stock["id"])["qty"], 5.0)
        self.assertEqual(len(outsourcing.list_receipts(self.conn)), 1)

    def test_an_unknown_receipt_is_not_found(self):
        with self.assertRaises(ValueError):
            outsourcing.delete_receipt(self.conn, 9999)


# --------------------------------------------------------------------------- #
class Stock(OutsourcingBase):
    def setUp(self):
        super().setUp()
        self.oid = self.os_order()
        self.receive(self.oid, 40)
        self.item = outsourcing.list_os_items(self.conn)["rows"][0]["id"]

    def test_issuing_moves_the_stock_and_names_the_job(self):
        outsourcing.issue_os_item(self.conn, self.item, {
            "qty": 12, "order_id": "ORD-26-27-004", "mv_date": "2026-08-22",
            "remarks": "to assembly"})
        it = outsourcing.get_os_item(self.conn, self.item)
        self.assertEqual(it["qty"], 28.0)
        self.assertEqual((it["movements"][0]["type"], it["movements"][0]["qty"]),
                         ("issue", -12.0))
        self.assertEqual(it["movements"][0]["order_id"], "ORD-26-27-004")

    def test_over_issue_is_refused(self):
        with self.assertRaises(ValueError) as e:
            outsourcing.issue_os_item(self.conn, self.item, {
                "qty": 41, "order_id": "ORD-26-27-004"})
        self.assertIn("Only 40", str(e.exception))
        self.assertEqual(outsourcing.get_os_item(self.conn, self.item)["qty"], 40.0)

    def test_an_issue_must_name_an_order(self):
        with self.assertRaises(ValueError):
            outsourcing.issue_os_item(self.conn, self.item, {"qty": 1, "order_id": ""})

    def test_adjusting_works_both_ways_and_needs_a_reason(self):
        outsourcing.adjust_os_item(self.conn, self.item, {
            "qty": 3, "mv_date": "2026-08-23", "remarks": "found on the shelf"})
        outsourcing.adjust_os_item(self.conn, self.item, {
            "qty": -5, "mv_date": "2026-08-24", "remarks": "damaged in handling"})
        it = outsourcing.get_os_item(self.conn, self.item)
        self.assertEqual(it["qty"], 38.0)
        self.assertEqual([(m["type"], m["qty"]) for m in it["movements"][:2]],
                         [("adjust", -5.0), ("adjust", 3.0)])
        with self.assertRaises(ValueError):
            outsourcing.adjust_os_item(self.conn, self.item, {"qty": 1, "remarks": ""})
        with self.assertRaises(ValueError):
            outsourcing.adjust_os_item(self.conn, self.item,
                                       {"qty": 0, "remarks": "nothing"})

    def test_an_adjustment_cannot_take_the_shelf_negative(self):
        with self.assertRaises(ValueError):
            outsourcing.adjust_os_item(self.conn, self.item,
                                       {"qty": -41, "remarks": "recount"})
        self.assertEqual(outsourcing.get_os_item(self.conn, self.item)["qty"], 40.0)

    def test_the_movements_always_sum_to_the_stock_on_hand(self):
        outsourcing.issue_os_item(self.conn, self.item,
                                  {"qty": 10, "order_id": "ORD-1"})
        outsourcing.adjust_os_item(self.conn, self.item,
                                   {"qty": -2, "remarks": "scrapped"})
        it = outsourcing.get_os_item(self.conn, self.item)
        self.assertEqual(sum(m["qty"] for m in it["movements"]), it["qty"])

    def test_the_master_fields_are_editable_but_the_quantity_is_not(self):
        outsourcing.update_os_item(self.conn, self.item, {
            "description": "Plated cap", "part_code": "TPS-0353-P",
            "material": "HDPE", "size_section": '1/4"NPT', "unit": "Nos",
            "unit_cost": 14, "notes": "second source", "active": True,
            "qty": 999})
        it = outsourcing.get_os_item(self.conn, self.item)
        self.assertEqual(it["description"], "Plated cap")
        self.assertEqual(it["unit_cost"], 14.0)
        self.assertEqual(it["qty"], 40.0)               # untouched by the edit
        with self.assertRaises(ValueError):
            outsourcing.update_os_item(self.conn, self.item, {"description": ""})

    def test_the_list_filters_and_totals(self):
        out = outsourcing.list_os_items(self.conn, q="OS-0001")
        self.assertEqual(len(out["rows"]), 1)
        self.assertEqual(out["stats"], {"items": 1, "qty": 40.0, "value": 500.0})
        self.assertEqual(len(outsourcing.list_os_items(self.conn, q="Plastic")["rows"]), 1)
        self.assertEqual(len(outsourcing.list_os_items(self.conn, q="nothing")["rows"]), 0)
        outsourcing.update_os_item(self.conn, self.item,
                                   {"description": "Plastic cap", "active": False})
        self.assertEqual(outsourcing.list_os_items(self.conn)["rows"], [])
        self.assertEqual(
            len(outsourcing.list_os_items(self.conn, active_only=False)["rows"]), 1)

    def test_an_unknown_item_is_not_found(self):
        for fn, arg in ((outsourcing.get_os_item, None),
                        (outsourcing.update_os_item, {"description": "x"}),
                        (outsourcing.adjust_os_item, {"qty": 1, "remarks": "x"}),
                        (outsourcing.issue_os_item, {"qty": 1, "order_id": "x"})):
            with self.assertRaises(ValueError):
                fn(self.conn, 9999) if arg is None else fn(self.conn, 9999, arg)


# --------------------------------------------------------------------------- #
class Deadlines(OutsourcingBase):
    """Same three buckets the Home panel uses, over os_order.deadline."""

    def _at(self, days: int, **kw) -> int:
        return self.os_order(
            deadline=(date(2026, 8, 21) + timedelta(days=days)).isoformat(), **kw)

    def test_the_buckets_split_at_their_boundaries(self):
        vid = self.vendor()
        for d in (-1, 0, 7, 8, 31, 32):
            self._at(d, vendor_id=vid)
        out = outsourcing.deadlines(self.conn, today="2026-08-21")
        self.assertEqual([r["days_left"] for r in out["overdue"]], [-1])
        self.assertEqual([r["days_left"] for r in out["this_week"]], [0, 7])
        self.assertEqual([r["days_left"] for r in out["this_month"]], [8, 31])
        self.assertEqual(out["as_of"], "2026-08-21")

    def test_a_blank_or_broken_deadline_is_not_a_deadline(self):
        vid = self.vendor()
        self.os_order(vendor_id=vid, deadline="")
        oid = self._at(2, vendor_id=vid)
        self.conn.execute("UPDATE os_order SET deadline='not a date' WHERE id=?", (oid,))
        self.conn.commit()
        out = outsourcing.deadlines(self.conn, today="2026-08-21")
        self.assertEqual(out["overdue"] + out["this_week"] + out["this_month"], [])

    def test_only_open_and_part_received_jobs_appear(self):
        vid = self.vendor()
        done = self._at(2, vendor_id=vid)
        self.receive(done, 100)                     # -> received
        closed = self._at(3, vendor_id=vid)
        outsourcing.set_os_status(self.conn, closed, "closed")
        cancelled = self._at(4, vendor_id=vid)
        outsourcing.set_os_status(self.conn, cancelled, "cancelled")
        live = self._at(5, vendor_id=vid)
        self.receive(live, 20)                      # -> partial, still out there
        out = outsourcing.deadlines(self.conn, today="2026-08-21")
        self.assertEqual([r["id"] for r in out["this_week"]], [live])

    def test_a_row_says_what_the_panel_needs(self):
        oid = self._at(-3)
        self.receive(oid, 30)
        row = outsourcing.deadlines(self.conn, today="2026-08-21")["overdue"][0]
        self.assertEqual(row["os_no"], "AT/OS/26-27/001")
        self.assertEqual(row["vendor_name"], "Sharma Plating Works")
        self.assertEqual(row["purpose"], "Zinc plating")
        self.assertEqual((row["qty_total"], row["qty_received"], row["qty_pending"]),
                         (100.0, 30.0, 70.0))
        self.assertEqual(row["status"], "partial")
        self.assertEqual(row["days_left"], -3)


# --------------------------------------------------------------------------- #
class Documents(OutsourcingBase):
    def test_a_batch_lands_against_a_vendor_an_order_or_both(self):
        vid = self.vendor()
        oid = self.os_order(vendor_id=vid)
        outsourcing.save_os_documents(self.conn, vid, None, "Rate card",
                                      [("rates.pdf", "application/pdf", b"%PDF r")])
        outsourcing.save_os_documents(self.conn, None, oid, "Challan",
                                      [("challan.jpg", "image/jpeg", b"jpeg")])
        outsourcing.save_os_documents(self.conn, vid, oid, "Invoice",
                                      [("bill.pdf", "application/pdf", b"%PDF b")])
        self.assertEqual(len(outsourcing.list_os_documents(self.conn)), 3)
        self.assertEqual(
            {d["label"] for d in outsourcing.list_os_documents(self.conn, vendor_id=vid)},
            {"Rate card", "Invoice"})
        self.assertEqual(
            {d["label"] for d in outsourcing.list_os_documents(self.conn, os_order_id=oid)},
            {"Challan", "Invoice"})

    def test_it_must_be_filed_against_something(self):
        with self.assertRaises(ValueError):
            outsourcing.save_os_documents(self.conn, None, None, "Orphan",
                                          [("x.pdf", "application/pdf", b"%PDF")])
        with self.assertRaises(ValueError):
            outsourcing.save_os_documents(self.conn, 9999, None, "Ghost vendor",
                                          [("x.pdf", "application/pdf", b"%PDF")])
        with self.assertRaises(ValueError):
            outsourcing.save_os_documents(self.conn, None, 9999, "Ghost order",
                                          [("x.pdf", "application/pdf", b"%PDF")])
        with self.assertRaises(ValueError):
            outsourcing.save_os_documents(self.conn, self.vendor(), None, "Nothing", [])

    def test_the_batch_is_all_or_nothing(self):
        vid = self.vendor()
        with self.assertRaises(ValueError):
            outsourcing.save_os_documents(self.conn, vid, None, "Mixed", [
                ("ok.pdf", "application/pdf", b"%PDF ok"),
                ("bad.exe", "application/x-msdownload", b"MZ")])
        self.assertEqual(outsourcing.list_os_documents(self.conn), [])
        self.assertEqual(list(paths.outsourcing_files_dir().iterdir()), [])

    def test_the_file_lands_under_the_outsourcing_folder(self):
        vid = self.vendor()
        meta = outsourcing.save_os_documents(
            self.conn, vid, None, "Rate card",
            [("rates.pdf", "application/pdf", b"%PDF r")])[0]
        stored = self.conn.execute("SELECT stored_name FROM os_document WHERE id=?",
                                   (meta["id"],)).fetchone()["stored_name"]
        self.assertTrue(stored.startswith("os0v"))
        self.assertTrue(stored.endswith(".pdf"))
        self.assertTrue((paths.outsourcing_files_dir() / stored).is_file())

    def test_serving_names_the_file_and_its_own_mime(self):
        vid = self.vendor()
        meta = outsourcing.save_os_documents(
            self.conn, vid, None, "Rate card",
            [("rate card (2026).pdf", "application/pdf", b"%PDF r")])[0]
        res = outsourcing.document_view(meta["id"], conn=self.conn)
        self.assertEqual(res.media_type, "application/pdf")
        self.assertEqual(res.headers["content-disposition"],
                         'inline; filename="rate card (2026).pdf"')
        down = outsourcing.document_view(meta["id"], download=True, conn=self.conn)
        self.assertTrue(down.headers["content-disposition"].startswith("attachment;"))

    def test_delete_removes_the_row_and_the_file(self):
        vid = self.vendor()
        meta = outsourcing.save_os_documents(
            self.conn, vid, None, "Rate card",
            [("rates.pdf", "application/pdf", b"%PDF r")])[0]
        outsourcing.delete_os_document(self.conn, meta["id"])
        self.assertEqual(outsourcing.list_os_documents(self.conn), [])
        self.assertEqual(list(paths.outsourcing_files_dir().iterdir()), [])
        with self.assertRaises(ValueError):
            outsourcing.delete_os_document(self.conn, meta["id"])


# --------------------------------------------------------------------------- #
class Refs(OutsourcingBase):
    def test_the_form_gets_what_it_needs(self):
        self.vendor()
        oid = self.internal_order()
        r = outsourcing.refs(self.conn)
        self.assertEqual([(v["code"], v["name"]) for v in r["vendors"]],
                         [("V01", "Sharma Plating Works")])
        self.assertIn("Nos", r["units"])
        self.assertIn("Plating", r["purposes"])
        self.assertEqual([s["key"] for s in r["statuses"]], outsourcing.OS_STATUSES)
        self.assertEqual([(o["id"], o["customer_name"]) for o in r["orders"]],
                         [(oid, "Acme Pumps")])

    def test_a_paid_order_is_not_offered_for_linking(self):
        oid = self.internal_order()
        orders.set_stage(self.conn, oid, "payment")
        self.assertEqual(outsourcing.refs(self.conn)["orders"], [])

    def test_a_deactivated_vendor_is_not_offered(self):
        outsourcing.set_vendor_active(self.conn, self.vendor(), False)
        self.assertEqual(outsourcing.refs(self.conn)["vendors"], [])

    def test_one_order_hands_over_its_items(self):
        oid = self.internal_order(items=[
            {"description": "Shaft", "qty": 10, "unit": "Nos", "rate": 250},
            {"description": "Bush", "qty": 4, "unit": "Nos", "rate": 90}])
        rows = outsourcing.order_items(self.conn, oid)
        self.assertEqual([(r["description"], r["qty"]) for r in rows],
                         [("Shaft", 10.0), ("Bush", 4.0)])


# --------------------------------------------------------------------------- #
class MaterialPicker(OutsourcingBase):
    """Bought-out parts answer the same question as the rack, so they appear in
    the shared /api/material/search — flagged, never mixed up with a heat."""

    def _stock(self):
        oid = self.os_order()
        self.receive(oid, 40, line={"description": "Plastic Cap",
                                    "size_section": '1/4"NPT', "material": "HDPE"})
        return outsourcing.list_os_items(self.conn)["rows"][0]

    def test_heats_are_flagged_too(self):
        inventory.create_heat(self.conn, {
            "heat_number": "H-500", "date_received": "2026-08-01",
            "material_class": "Steel", "grade": "EN8", "rods_received": 10})
        row = inventory.material_search(q="H-500", conn=self.conn)["rows"][0]
        self.assertEqual(row["source"], "heat")
        self.assertEqual(row["heat_number"], "H-500")

    def test_an_outsourced_row_carries_its_own_keys(self):
        s = self._stock()
        rows = inventory.material_search(q="OS-0001", conn=self.conn)["rows"]
        self.assertEqual(len(rows), 1)
        r = rows[0]
        self.assertEqual(r["source"], "outsourced")
        self.assertEqual(r["os_id"], "OS-0001")
        self.assertEqual(r["os_item_id"], s["id"])
        self.assertEqual(r["description"], "Plastic Cap")
        self.assertEqual(r["qty_remaining"], 40.0)
        self.assertEqual(r["unit"], "Nos")
        self.assertEqual(r["unit_cost"], 12.5)
        self.assertEqual(r["label"],
                         'OS-0001 · Plastic Cap, 1/4"NPT · HDPE (V01 Sharma Plating Works)')

    def test_it_is_searched_over_id_description_part_code_and_material(self):
        self._stock()
        for q in ("OS-0001", "Plastic", "TPS-0353", "HDPE"):
            got = [r["os_id"] for r in inventory.material_search(q=q, conn=self.conn)["rows"]
                   if r["source"] == "outsourced"]
            self.assertEqual(got, ["OS-0001"], q)

    def test_a_deactivated_item_drops_out_of_the_picker(self):
        s = self._stock()
        outsourcing.update_os_item(self.conn, s["id"],
                                   {"description": "Plastic Cap", "active": False})
        self.assertEqual(inventory.material_search(q="OS-0001", conn=self.conn)["rows"], [])

    def test_both_kinds_come_back_from_one_empty_search(self):
        inventory.create_heat(self.conn, {
            "heat_number": "H-500", "date_received": "2026-08-01",
            "rods_received": 10})
        self._stock()
        rows = inventory.material_search(conn=self.conn)["rows"]
        self.assertEqual({r["source"] for r in rows}, {"heat", "outsourced"})


# --------------------------------------------------------------------------- #
class RouteModelPins(OutsourcingBase):
    """A column the route model does not declare is dropped by Pydantic before
    the handler ever sees it — silently, with a 200. Every field the DB stores
    is pinned here."""

    def test_the_order_line_model_carries_the_agreed_rate(self):
        self.assertEqual(outsourcing.OsOrderItemIn(qty=1, unit_cost=12.5).unit_cost, 12.5)
        self.assertIsNone(outsourcing.OsOrderItemIn(qty=1).unit_cost)
        self.assertEqual(outsourcing.OsOrderItemIn(qty=1, order_item_id=7).order_item_id, 7)

    def test_the_receipt_line_model_carries_the_rate_and_the_stock_fields(self):
        ln = outsourcing.ReceiptLineIn(os_order_item_id=1, qty=2, unit_cost=9.5,
                                       material="HDPE", size_section='1/4"NPT',
                                       os_item_id=3)
        self.assertEqual((ln.unit_cost, ln.material, ln.size_section, ln.os_item_id),
                         (9.5, "HDPE", '1/4"NPT', 3))
        self.assertIsNone(outsourcing.ReceiptLineIn(os_order_item_id=1, qty=2).unit_cost)

    def test_the_stock_model_carries_the_rate(self):
        self.assertEqual(outsourcing.OsItemIn(description="x", unit_cost=4).unit_cost, 4)
        self.assertIsNone(outsourcing.OsItemIn(description="x").unit_cost)

    def test_the_agreed_rate_survives_the_round_trip(self):
        """Model → create → DB → detail. This is the trip the drop happens on."""
        body = outsourcing.OsOrderIn(
            vendor_id=self.vendor(), date_sent="2026-08-14",
            items=[outsourcing.OsOrderItemIn(description="Cap", qty=10, unit_cost=7.25)])
        oid = outsourcing.create_os_order(self.conn, body.model_dump())
        self.assertEqual(outsourcing.get_os_order(self.conn, oid)["items"][0]["unit_cost"],
                         7.25)
        rid = outsourcing.create_receipt(self.conn, outsourcing.ReceiptIn(
            os_order_id=oid, receipt_date="2026-08-20",
            lines=[outsourcing.ReceiptLineIn(
                os_order_item_id=outsourcing.get_os_order(self.conn, oid)["items"][0]["id"],
                qty=10)]).model_dump())
        self.assertTrue(rid)
        self.assertEqual(outsourcing.list_os_items(self.conn)["rows"][0]["unit_cost"], 7.25)

    def test_both_new_columns_reach_an_existing_database(self):
        """The migration path: these two arrived after wave 1 shipped."""
        cols = {t: {r["name"] for r in self.conn.execute(f"PRAGMA table_info({t})")}
                for t in ("os_item", "os_order_item")}
        self.assertIn("unit_cost", cols["os_item"])
        self.assertIn("unit_cost", cols["os_order_item"])
        self.assertIn("os_item", db._MIGRATIONS)
        self.assertIn("os_order_item", db._MIGRATIONS)


# --------------------------------------------------------------------------- #
class Registry(OutsourcingBase):
    def test_the_module_is_on_the_launcher(self):
        from backend.core.registry import ALL_KEYS, MODULES
        self.assertIn("outsourcing", ALL_KEYS)
        entry = next(m for m in MODULES if m["key"] == "outsourcing")
        self.assertEqual(entry["path"], "/outsourcing/")
        self.assertTrue(entry["built"])
        # Wave 3 reordered the launcher to read like the SOP (SOP-DESIGN §7):
        # outsourcing closes the paperwork run, ahead of the standing masters.
        self.assertLess(ALL_KEYS.index("quality_docs"), ALL_KEYS.index("outsourcing"))
        self.assertLess(ALL_KEYS.index("outsourcing"), ALL_KEYS.index("inventory"))
        self.assertLess(ALL_KEYS.index("outsourcing"), ALL_KEYS.index("settings"))

    def test_the_page_exists(self):
        page = paths.frontend_dir() / "outsourcing"
        self.assertTrue((page / "index.html").is_file())
        self.assertTrue((page / "outsourcing.js").is_file())


if __name__ == "__main__":
    unittest.main()
