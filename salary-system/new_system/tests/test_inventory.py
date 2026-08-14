"""Inventory module spec: heats, derived stock, usage log, search, options,
attachments. Run:  python -m unittest tests.test_inventory
"""

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend.modules import inventory  # noqa: E402
from backend.core import db  # noqa: E402


def heat_data(**kw):
    base = {
        "heat_number": "H-1001", "date_received": "2026-08-01",
        "supplier": "Bharat Steels", "material_class": "Steel", "grade": "EN8",
        "shape": "Round", "size_section": "Ø25 mm × 3 m", "rods_received": 50,
        "total_weight_kg": 900.0, "rack": "R-4", "price_total": 45000,
        "price_rate_per_kg": 50, "notes": "",
        "composition": [{"element": "C", "percent": 0.40},
                        {"element": "Mn", "percent": 0.75}],
    }
    base.update(kw)
    return base


class InventoryBase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        os.environ["SALARY_DATA_DIR"] = self.tmp.name  # attachments dir
        self.db_path = Path(self.tmp.name) / "test.db"
        db.init_db(self.db_path)
        inventory.ensure_defaults(self.db_path)
        self.conn = db.connect(self.db_path)

    def tearDown(self):
        self.conn.close()
        os.environ.pop("SALARY_DATA_DIR", None)
        self.tmp.cleanup()


class Options(InventoryBase):
    def test_defaults_seeded_and_idempotent(self):
        opts = inventory.list_options(self.conn)
        self.assertIn("Steel", opts["material_class"])
        self.assertIn("Round", opts["shape"])
        self.assertIn("EN8", opts["grade"])
        self.assertIn("C", opts["element"])
        inventory.ensure_defaults(self.db_path)  # re-run must not duplicate
        self.assertEqual(opts, inventory.list_options(self.conn))

    def test_add_and_delete(self):
        opts = inventory.add_option(self.conn, "shape", "  Octagonal ")
        self.assertIn("Octagonal", opts["shape"])
        opts = inventory.delete_option(self.conn, "shape", "Octagonal")
        self.assertNotIn("Octagonal", opts["shape"])

    def test_bad_kind_and_empty_value(self):
        with self.assertRaises(ValueError):
            inventory.add_option(self.conn, "colour", "Blue")
        with self.assertRaises(ValueError):
            inventory.add_option(self.conn, "grade", "   ")

    def test_form_values_learned_into_lists(self):
        inventory.create_heat(self.conn, heat_data(
            grade="EN47", shape="Trapezoid",
            composition=[{"element": "Xx", "percent": 1.0}]))
        opts = inventory.list_options(self.conn)
        self.assertIn("EN47", opts["grade"])
        self.assertIn("Trapezoid", opts["shape"])
        self.assertIn("Xx", opts["element"])


class HeatCrud(InventoryBase):
    def test_create_and_detail(self):
        hid = inventory.create_heat(self.conn, heat_data())
        h = inventory.get_heat(self.conn, hid)
        self.assertEqual(h["heat_number"], "H-1001")
        self.assertEqual(h["remaining"], 50)
        self.assertEqual(h["status"], "in_stock")
        self.assertEqual(len(h["composition"]), 2)
        self.assertEqual(h["stock_value"], 45000)

    def test_validation(self):
        for bad, msg in [
            (heat_data(heat_number=" "), "Heat number"),
            (heat_data(date_received="1/8/2026"), "Date"),
            (heat_data(rods_received=0), "at least 1"),
            (heat_data(rods_received="ten"), "whole number"),
            (heat_data(composition=[{"element": "C", "percent": 0.4},
                                    {"element": "c", "percent": 0.5}]), "twice"),
            (heat_data(composition=[{"element": "C", "percent": 101}]), "between"),
            (heat_data(price_total=-5), "negative"),
        ]:
            with self.assertRaises(ValueError, msg=msg) as cm:
                inventory.create_heat(self.conn, bad)
            self.assertIn(msg, str(cm.exception))

    def test_duplicate_heat_number(self):
        inventory.create_heat(self.conn, heat_data())
        with self.assertRaises(ValueError):
            inventory.create_heat(self.conn, heat_data(supplier="Other"))

    def test_edit_heat_and_rename(self):
        hid = inventory.create_heat(self.conn, heat_data())
        inventory.update_heat(self.conn, hid, heat_data(
            heat_number="H-1001-FIXED", rods_received=60,
            composition=[{"element": "C", "percent": 0.42}]))
        h = inventory.get_heat(self.conn, hid)
        self.assertEqual(h["heat_number"], "H-1001-FIXED")
        self.assertEqual(h["remaining"], 60)
        self.assertEqual(len(h["composition"]), 1)

    def test_rename_to_taken_number(self):
        inventory.create_heat(self.conn, heat_data())
        hid2 = inventory.create_heat(self.conn, heat_data(heat_number="H-2"))
        with self.assertRaises(ValueError):
            inventory.update_heat(self.conn, hid2, heat_data(heat_number="H-1001"))

    def test_shrink_below_moved_fails(self):
        hid = inventory.create_heat(self.conn, heat_data())
        inventory.add_movement(self.conn, hid, {"type": "issue", "rods": 30,
                                                "order_id": "PO-1"})
        with self.assertRaises(ValueError):
            inventory.update_heat(self.conn, hid, heat_data(rods_received=20))
        inventory.update_heat(self.conn, hid, heat_data(rods_received=30))  # == ok

    def test_delete_rules(self):
        hid = inventory.create_heat(self.conn, heat_data())
        inventory.add_movement(self.conn, hid, {"type": "issue", "rods": 1,
                                                "order_id": "PO-1"})
        with self.assertRaises(ValueError):
            inventory.delete_heat(self.conn, hid)
        h = inventory.get_heat(self.conn, hid)
        inventory.delete_movement(self.conn, h["movements"][0]["id"])
        inventory.delete_heat(self.conn, hid)
        with self.assertRaises(ValueError):
            inventory.get_heat(self.conn, hid)
        # composition rows cascaded away
        n = self.conn.execute("SELECT COUNT(*) AS n FROM heat_composition").fetchone()["n"]
        self.assertEqual(n, 0)


class UsageLog(InventoryBase):
    def setUp(self):
        super().setUp()
        self.hid = inventory.create_heat(self.conn, heat_data())

    def test_issue_requires_order_id(self):
        with self.assertRaises(ValueError):
            inventory.add_movement(self.conn, self.hid, {"type": "issue", "rods": 5})

    def test_reject_needs_no_order_id(self):
        h = inventory.add_movement(self.conn, self.hid, {"type": "reject", "rods": 5})
        self.assertEqual(h["remaining"], 45)
        self.assertEqual(h["rejected_rods"], 5)
        self.assertEqual(h["status"], "in_stock")

    def test_cannot_exceed_remaining(self):
        inventory.add_movement(self.conn, self.hid,
                               {"type": "issue", "rods": 48, "order_id": "PO-9"})
        with self.assertRaises(ValueError) as cm:
            inventory.add_movement(self.conn, self.hid,
                                   {"type": "issue", "rods": 3, "order_id": "PO-9"})
        self.assertIn("2 rod(s) remaining", str(cm.exception))

    def test_consumed_then_undo(self):
        h = inventory.add_movement(self.conn, self.hid,
                                   {"type": "issue", "rods": 50, "order_id": "PO-9"})
        self.assertEqual(h["status"], "consumed")
        h = inventory.delete_movement(self.conn, h["movements"][0]["id"])
        self.assertEqual(h["status"], "in_stock")
        self.assertEqual(h["remaining"], 50)

    def test_reject_remaining_and_undo(self):
        inventory.add_movement(self.conn, self.hid,
                               {"type": "issue", "rods": 20, "order_id": "PO-9"})
        h = inventory.reject_remaining(self.conn, self.hid, remarks="rusted")
        self.assertEqual(h["remaining"], 0)
        self.assertEqual(h["status"], "rejected")
        self.assertEqual(h["movements"][0]["rods"], 30)
        with self.assertRaises(ValueError):
            inventory.reject_remaining(self.conn, self.hid)
        h = inventory.delete_movement(self.conn, h["movements"][0]["id"])
        self.assertEqual(h["status"], "in_stock")

    def test_fully_issued_is_consumed(self):
        inventory.add_movement(self.conn, self.hid,
                               {"type": "reject", "rods": 10})
        h = inventory.add_movement(self.conn, self.hid,
                                   {"type": "issue", "rods": 40, "order_id": "PO-9"})
        self.assertEqual(h["status"], "consumed")  # last entry is an issue

    def test_global_log_traces_order(self):
        hid2 = inventory.create_heat(self.conn, heat_data(heat_number="H-2"))
        inventory.add_movement(self.conn, self.hid,
                               {"type": "issue", "rods": 5, "order_id": "PO-77"})
        inventory.add_movement(self.conn, hid2,
                               {"type": "issue", "rods": 7, "order_id": "PO-77"})
        inventory.add_movement(self.conn, hid2,
                               {"type": "issue", "rods": 1, "order_id": "PO-88"})
        rows = inventory.global_log(self.conn, q="PO-77")
        self.assertEqual(len(rows), 2)
        self.assertEqual({r["heat_number"] for r in rows}, {"H-1001", "H-2"})
        self.assertEqual(len(inventory.global_log(self.conn)), 3)


class SearchAndStats(InventoryBase):
    def setUp(self):
        super().setUp()
        self.h1 = inventory.create_heat(self.conn, heat_data(
            heat_number="H-A", date_received="2026-01-05", grade="EN8",
            material_class="Steel", shape="Round", rack="R-1",
            composition=[{"element": "C", "percent": 0.45}]))
        self.h2 = inventory.create_heat(self.conn, heat_data(
            heat_number="H-B", date_received="2026-03-10", grade="SS304",
            material_class="Stainless Steel", shape="Hexagonal",
            supplier="Krishna Metals", rack="R-2", price_total=None,
            composition=[{"element": "C", "percent": 0.08},
                         {"element": "Cr", "percent": 18.2}]))
        inventory.add_movement(self.conn, self.h2,
                               {"type": "issue", "rods": 50, "order_id": "PO-1"})

    def rows(self, **kw):
        return inventory.list_heats(self.conn, **kw)["rows"]

    def test_text_search(self):
        self.assertEqual([r["heat_number"] for r in self.rows(q="krishna")], ["H-B"])
        self.assertEqual([r["heat_number"] for r in self.rows(q="R-1")], ["H-A"])
        self.assertEqual([r["heat_number"] for r in self.rows(q="EN8")], ["H-A"])

    def test_filters(self):
        self.assertEqual([r["heat_number"] for r in self.rows(material_class="Stainless Steel")], ["H-B"])
        self.assertEqual([r["heat_number"] for r in self.rows(shape="Round")], ["H-A"])
        self.assertEqual([r["heat_number"] for r in self.rows(status="consumed")], ["H-B"])
        self.assertEqual([r["heat_number"] for r in self.rows(status="in_stock")], ["H-A"])

    def test_composition_range_inclusive(self):
        self.assertEqual([r["heat_number"] for r in
                          self.rows(element="C", pct_min=0.40, pct_max=0.50)], ["H-A"])
        self.assertEqual([r["heat_number"] for r in
                          self.rows(element="c", pct_min=0.45, pct_max=0.45)], ["H-A"])
        self.assertEqual([r["heat_number"] for r in
                          self.rows(element="Cr", pct_min=10, pct_max="")], ["H-B"])
        self.assertEqual(self.rows(element="Mo", pct_min=0, pct_max=100), [])

    def test_sorts(self):
        self.assertEqual([r["heat_number"] for r in self.rows(sort="newest")], ["H-B", "H-A"])
        self.assertEqual([r["heat_number"] for r in self.rows(sort="oldest")], ["H-A", "H-B"])
        self.assertEqual([r["heat_number"] for r in self.rows(sort="remaining_asc")], ["H-B", "H-A"])
        self.assertEqual([r["heat_number"] for r in self.rows(sort="remaining_desc")], ["H-A", "H-B"])

    def test_stats_strip(self):
        stats = inventory.list_heats(self.conn)["stats"]
        self.assertEqual(stats["total_heats"], 2)
        self.assertEqual(stats["rods_in_stock"], 50)   # H-A full, H-B consumed
        self.assertEqual(stats["rods_issued"], 50)
        self.assertEqual(stats["stock_value"], 45000)  # H-A only (H-B unpriced)


class Attachments(InventoryBase):
    def setUp(self):
        super().setUp()
        self.hid = inventory.create_heat(self.conn, heat_data())

    def test_save_view_delete(self):
        meta = inventory.save_attachment(
            self.conn, self.hid, "certificate", "mtc scan.pdf",
            "application/pdf", b"%PDF-1.4 fake")
        h = inventory.get_heat(self.conn, self.hid)
        self.assertEqual(len(h["attachments"]), 1)
        stored = self.conn.execute(
            "SELECT stored_name FROM heat_attachment WHERE id=?",
            (meta["id"],)).fetchone()["stored_name"]
        from backend.core import paths
        self.assertTrue((paths.inventory_files_dir() / stored).is_file())
        inventory.delete_attachment(self.conn, meta["id"])
        self.assertFalse((paths.inventory_files_dir() / stored).exists())

    def test_rejects_bad_type_kind_and_size(self):
        with self.assertRaises(ValueError):
            inventory.save_attachment(self.conn, self.hid, "certificate",
                                      "x.exe", "application/x-msdownload", b"MZ")
        with self.assertRaises(ValueError):
            inventory.save_attachment(self.conn, self.hid, "selfie",
                                      "a.jpg", "image/jpeg", b"xx")
        with self.assertRaises(ValueError):
            inventory.save_attachment(self.conn, self.hid, "invoice",
                                      "a.jpg", "image/jpeg", b"")
        with self.assertRaises(ValueError):
            inventory.save_attachment(self.conn, self.hid, "invoice", "a.jpg",
                                      "image/jpeg", b"x" * (inventory.MAX_FILE_BYTES + 1))

    def test_deleting_heat_removes_files(self):
        inventory.save_attachment(self.conn, self.hid, "invoice", "inv.jpg",
                                  "image/jpeg", b"jpegbytes")
        from backend.core import paths
        files = list(paths.inventory_files_dir().iterdir())
        self.assertEqual(len(files), 1)
        inventory.delete_heat(self.conn, self.hid)
        self.assertEqual(list(paths.inventory_files_dir().iterdir()), [])


class ReviewRegressions(InventoryBase):
    """Each test pins a defect found (and fixed) in the adversarial review."""

    def test_infinite_price_rejected(self):
        for bad in (float("inf"), float("-inf"), 1e999):
            with self.assertRaises(ValueError):
                inventory.create_heat(self.conn, heat_data(price_total=bad))
        inventory.create_heat(self.conn, heat_data())
        with self.assertRaises(ValueError):
            hid = self.conn.execute("SELECT id FROM heat").fetchone()["id"]
            inventory.add_movement(self.conn, hid, {
                "type": "reject", "rods": 1, "weight_kg": float("inf")})
        # the list/stats endpoint math must still work
        out = inventory.list_heats(self.conn)
        self.assertEqual(out["stats"]["total_heats"], 1)

    def test_huge_price_rejected(self):
        """1e308 is finite, so isfinite() lets it through — but multiplied by the
        rod count it overflows to inf, which is not JSON-serialisable and used to
        500 every list/detail/stats read, hiding the whole register."""
        for bad in (1e308, 1e13):
            with self.assertRaises(ValueError):
                inventory.create_heat(self.conn, heat_data(price_total=bad))
        inventory.create_heat(self.conn, heat_data(price_total=1e11))  # large but sane

    def test_poisoned_row_still_reads(self):
        """A database poisoned before that bound existed must stay READABLE, so
        the bad heat can be opened and corrected instead of bricking the module."""
        inventory.create_heat(self.conn, heat_data(heat_number="H-BAD", rods_received=10,
                                                   price_total=1000))
        inventory.create_heat(self.conn, heat_data(heat_number="H-OK", rods_received=20,
                                                   price_total=8000))
        bad = self.conn.execute(
            "SELECT id FROM heat WHERE heat_number='H-BAD'").fetchone()["id"]
        self.conn.execute("UPDATE heat SET price_total=? WHERE id=?", (1e308, bad))
        self.conn.commit()

        out = inventory.list_heats(self.conn)
        self.assertEqual(len(out["rows"]), 2)            # neither heat disappears
        rows = {r["heat_number"]: r for r in out["rows"]}
        self.assertIsNone(rows["H-BAD"]["stock_value"])   # not inf
        self.assertEqual(rows["H-OK"]["stock_value"], 8000.0)   # unaffected
        self.assertEqual(out["stats"]["stock_value"], 8000)
        self.assertIsNotNone(inventory.get_heat(self.conn, bad))
        json.dumps(out, default=str)                      # must not raise

        inventory.update_heat(self.conn, bad, heat_data(
            heat_number="H-BAD", rods_received=10, price_total=1500))
        self.assertEqual(inventory.get_heat(self.conn, bad)["stock_value"], 1500.0)

    def test_impossible_dates_rejected(self):
        with self.assertRaises(ValueError):
            inventory.create_heat(self.conn, heat_data(date_received="2026-13-45"))
        hid = inventory.create_heat(self.conn, heat_data())
        with self.assertRaises(ValueError):
            inventory.add_movement(self.conn, hid, {
                "type": "reject", "rods": 1, "mv_date": "2026-02-30"})

    def test_reject_drops_order_id(self):
        hid = inventory.create_heat(self.conn, heat_data())
        h = inventory.add_movement(self.conn, hid, {
            "type": "reject", "rods": 2, "order_id": "PO-STALE-FROM-UI"})
        self.assertIsNone(h["movements"][0]["order_id"])

    def test_deleted_default_stays_gone(self):
        inventory.delete_option(self.conn, "material_class", "Brass")
        inventory.ensure_defaults(self.db_path)  # every app start calls this
        self.assertNotIn("Brass", inventory.list_options(self.conn)["material_class"])

    def test_upload_batch_atomicity(self):
        from backend.core import paths
        hid = inventory.create_heat(self.conn, heat_data())
        with self.assertRaises(ValueError):
            inventory.save_attachments(self.conn, hid, "invoice", [
                ("good.pdf", "application/pdf", b"%PDF ok"),
                ("bad.exe", "application/x-msdownload", b"MZ"),
            ])
        h = inventory.get_heat(self.conn, hid)
        self.assertEqual(h["attachments"], [])                       # no DB rows
        self.assertEqual(list(paths.inventory_files_dir().iterdir()), [])  # no files

    def test_response_mime_from_extension(self):
        self.assertEqual(inventory.response_mime("h1-ab.pdf"), "application/pdf")
        self.assertEqual(inventory.response_mime("h1-ab.jpg"), "image/jpeg")
        self.assertEqual(inventory.response_mime("h1-ab.weird"), "application/octet-stream")

    def test_concurrent_oversubscribe(self):
        import threading
        hid = inventory.create_heat(self.conn, heat_data(rods_received=10))
        results = []

        def worker():
            c = db.connect(self.db_path)
            try:
                inventory.add_movement(c, hid, {"type": "issue", "rods": 5,
                                                "order_id": "PO-RACE"})
                results.append("ok")
            except ValueError:
                results.append("blocked")
            except Exception as e:  # busy timeout etc. must not corrupt stock
                results.append(f"err:{e}")
            finally:
                c.close()

        threads = [threading.Thread(target=worker) for _ in range(8)]
        for t in threads: t.start()
        for t in threads: t.join()

        remaining = self.conn.execute(
            "SELECT 10 - COALESCE(SUM(rods),0) AS r FROM heat_movement WHERE heat_id=?",
            (hid,)).fetchone()["r"]
        self.assertGreaterEqual(remaining, 0, f"stock went negative! results={results}")
        self.assertLessEqual(results.count("ok"), 2)   # only 2×5 rods fit in 10


if __name__ == "__main__":
    unittest.main(verbosity=2)


class PiecesAndFeasibility(InventoryBase):
    """Piece-level stock and the manufacturability check."""

    def test_parts_from_piece(self):
        pf = inventory.parts_from_piece
        # The spec's rule: whole parts per PIECE, never total-length / part-length.
        self.assertEqual(pf(10, 3), (3, 1))          # not 3.33
        self.assertEqual(pf(8, 3), (2, 2))
        self.assertEqual(pf(6.5, 3), (2, 0.5))
        self.assertEqual(pf(3, 3), (1, 0))
        self.assertEqual(pf(2.9, 3), (0, 2.9))
        # margin is eaten by EACH part
        self.assertEqual(pf(10, 3, 0.2), (3, 0.4))
        self.assertEqual(pf(10, 4, 1), (2, 0))
        # binary floating point must not steal a part: 9.9/3.3 is 2.9999...
        self.assertEqual(pf(9.9, 3.3), (3, 0.0))
        # degenerate input can't divide by zero or loop
        self.assertEqual(pf(10, 0), (0, 10.0))
        self.assertEqual(pf(0, 3), (0, 0.0))

    def _stock(self):
        for hn, length, qty in (("H1001", 10, 1), ("H1002", 8, 1), ("H1003", 6.5, 2)):
            inventory.create_heat(self.conn, heat_data(
                heat_number=hn, rods_received=qty, material_class="Steel", grade="EN8",
                pieces=[{"length_mm": length, "diameter_mm": 20, "quantity": qty}]))

    def test_spec_example(self):
        """3 rods of 10, part 3 -> 9 parts, and never 10."""
        inventory.create_heat(self.conn, heat_data(
            heat_number="R-3", rods_received=3, material_class="Steel",
            pieces=[{"length_mm": 10, "diameter_mm": 20, "quantity": 3}]))
        r = inventory.check_material(self.conn, {
            "method": "dimension", "material_class": "Steel",
            "required_qty": 9, "part_length": 3, "part_diameter": 20})
        self.assertEqual(r["total_feasible"], 9)
        self.assertEqual(r["status"], "available")

    def test_heat_breakdown_kept(self):
        self._stock()
        r = inventory.check_material(self.conn, {
            "method": "dimension", "material_class": "Steel", "grade": "EN8",
            "required_qty": 9, "part_length": 3, "part_diameter": 20})
        per = {h["heat_number"]: h["feasible"] for h in r["heats"]}
        self.assertEqual(per, {"H1001": 3, "H1002": 2, "H1003": 4})
        self.assertEqual(r["total_feasible"], 9)
        # heats are never merged into one interchangeable pile
        self.assertEqual(len(r["heats"]), 3)

    def test_status_and_shortfall(self):
        self._stock()
        req = {"method": "dimension", "material_class": "Steel", "grade": "EN8",
               "part_length": 3, "part_diameter": 20}
        self.assertEqual(inventory.check_material(
            self.conn, {**req, "required_qty": 9})["status"], "available")
        r = inventory.check_material(self.conn, {**req, "required_qty": 12})
        self.assertEqual((r["status"], r["shortfall"]), ("partial", 3))
        r = inventory.check_material(
            self.conn, {**req, "required_qty": 5, "part_length": 999})
        self.assertEqual((r["status"], r["total_feasible"]), ("none", 0))

    def test_diameter_filters(self):
        self._stock()
        r = inventory.check_material(self.conn, {
            "method": "dimension", "material_class": "Steel", "grade": "EN8",
            "required_qty": 1, "part_length": 3, "part_diameter": 25})
        self.assertEqual(r["total_feasible"], 0)   # Ø20 stock can't make a Ø25 part
        self.assertTrue(any("under" in p["reason"]
                            for h in r["heats"] for p in h["pieces"]))

    def test_margin_costs_a_part(self):
        inventory.create_heat(self.conn, heat_data(
            heat_number="M-1", rods_received=1, material_class="Steel",
            pieces=[{"length_mm": 10, "diameter_mm": 20, "quantity": 1}]))
        req = {"method": "dimension", "material_class": "Steel",
               "required_qty": 1, "part_length": 2.5, "part_diameter": 20}
        self.assertEqual(inventory.check_material(self.conn, req)["total_feasible"], 4)
        self.assertEqual(inventory.check_material(
            self.conn, {**req, "margin": 0.2})["total_feasible"], 3)

    def test_quantity_method(self):
        self._stock()
        r = inventory.check_material(self.conn, {
            "method": "quantity", "material_class": "Steel", "required_qty": 3})
        self.assertEqual(r["total_feasible"], 4)   # 1 + 1 + 2 rods, dimensions ignored
        self.assertEqual(r["status"], "available")

    def test_consumed_stock_drops_out(self):
        self._stock()
        hid = self.conn.execute(
            "SELECT id FROM heat WHERE heat_number='H1003'").fetchone()["id"]
        inventory.add_movement(self.conn, hid,
                               {"type": "issue", "rods": 1, "order_id": "PO-1"})
        r = inventory.check_material(self.conn, {
            "method": "dimension", "material_class": "Steel", "grade": "EN8",
            "required_qty": 9, "part_length": 3, "part_diameter": 20})
        per = {h["heat_number"]: h["feasible"] for h in r["heats"]}
        self.assertEqual(per["H1003"], 2)          # one of the two 6.5s is gone
        self.assertEqual(r["total_feasible"], 7)

    def test_heat_without_pieces_is_reported_not_hidden(self):
        inventory.create_heat(self.conn, heat_data(
            heat_number="NO-DIM", rods_received=5, material_class="Steel"))
        r = inventory.check_material(self.conn, {
            "method": "dimension", "material_class": "Steel",
            "required_qty": 1, "part_length": 3})
        entry = [h for h in r["heats"] if h["heat_number"] == "NO-DIM"][0]
        self.assertEqual(entry["feasible"], 0)
        self.assertIn("no piece dimensions", entry["skipped"])
        # but it IS usable by quantity
        q = inventory.check_material(self.conn, {
            "method": "quantity", "material_class": "Steel", "required_qty": 1})
        self.assertEqual(q["total_feasible"], 5)

    def test_pieces_set_the_rod_count(self):
        hid = inventory.create_heat(self.conn, heat_data(
            heat_number="SUM-1", rods_received=99,   # wrong on purpose
            pieces=[{"length_mm": 10, "quantity": 2},
                    {"length_mm": 4, "quantity": 3}]))
        self.assertEqual(inventory.get_heat(self.conn, hid)["rods_received"], 5)

    def test_bad_pieces_rejected(self):
        for bad in ([{"length_mm": 0, "quantity": 1}],
                    [{"length_mm": -5, "quantity": 1}],
                    [{"length_mm": 10, "quantity": 0}],
                    [{"length_mm": "x", "quantity": 1}]):
            with self.assertRaises(ValueError):
                inventory.create_heat(self.conn, heat_data(
                    heat_number="BAD-1", pieces=bad))

    def test_intake_groups_by_heat_number(self):
        r = inventory.create_intake(self.conn, {
            "date_received": "2026-08-14", "supplier": "Bharat Steels",
            "material_class": "Steel", "grade": "EN8",
            "pieces": [
                {"heat_number": "I-1", "length_mm": 10, "diameter_mm": 20, "quantity": 1},
                {"heat_number": "I-2", "length_mm": 8, "diameter_mm": 20, "quantity": 1},
                {"heat_number": "I-1", "length_mm": 4, "diameter_mm": 20, "quantity": 2},
            ]})
        self.assertEqual(r["count"], 2)      # two heat numbers, three rows
        self.assertEqual(r["rods"], 4)
        hid = self.conn.execute(
            "SELECT id FROM heat WHERE heat_number='I-1'").fetchone()["id"]
        h = inventory.get_heat(self.conn, hid)
        self.assertEqual(len(h["pieces"]), 2)
        self.assertEqual(h["rods_received"], 3)

    def test_intake_is_all_or_nothing(self):
        inventory.create_heat(self.conn, heat_data(heat_number="TAKEN"))
        before = len(inventory.list_heats(self.conn)["rows"])
        with self.assertRaises(ValueError):
            inventory.create_intake(self.conn, {
                "date_received": "2026-08-14", "material_class": "Steel",
                "pieces": [{"heat_number": "FRESH", "length_mm": 5, "quantity": 1},
                           {"heat_number": "TAKEN", "length_mm": 5, "quantity": 1}]})
        self.assertEqual(len(inventory.list_heats(self.conn)["rows"]), before)
        self.assertIsNone(self.conn.execute(
            "SELECT id FROM heat WHERE heat_number='FRESH'").fetchone())

    def test_intake_needs_a_heat_number(self):
        with self.assertRaises(ValueError):
            inventory.create_intake(self.conn, {
                "date_received": "2026-08-14",
                "pieces": [{"heat_number": "", "length_mm": 5, "quantity": 1}]})
        with self.assertRaises(ValueError):
            inventory.create_intake(self.conn, {"date_received": "2026-08-14", "pieces": []})

    def test_check_rejects_bad_input(self):
        with self.assertRaises(ValueError):
            inventory.check_material(self.conn, {"method": "sideways"})
        with self.assertRaises(ValueError):   # dimension check needs a length
            inventory.check_material(self.conn, {"method": "dimension", "required_qty": 5})
        with self.assertRaises(ValueError):
            inventory.check_material(self.conn, {"method": "dimension",
                                                 "part_length": float("inf")})


class SuppliersAndPerHeatComposition(InventoryBase):
    """Supplier is a learned dropdown list; chemistry belongs to each heat."""

    def test_supplier_is_an_option_kind(self):
        self.assertIn("supplier", inventory.OPTION_KINDS)
        opts = inventory.list_options(self.conn)
        self.assertEqual(opts["supplier"], [])      # nothing seeded: shop-specific

    def test_supplier_learned_from_a_heat(self):
        inventory.create_heat(self.conn, heat_data(supplier="Jindal Steel"))
        self.assertIn("Jindal Steel", inventory.list_options(self.conn)["supplier"])

    def test_supplier_list_appends(self):
        inventory.create_heat(self.conn, heat_data(heat_number="S-1", supplier="Jindal Steel"))
        inventory.create_heat(self.conn, heat_data(heat_number="S-2", supplier="Bharat Steels"))
        inventory.create_heat(self.conn, heat_data(heat_number="S-3", supplier="Jindal Steel"))
        self.assertEqual(inventory.list_options(self.conn)["supplier"],
                         ["Bharat Steels", "Jindal Steel"])   # sorted, no duplicate

    def test_supplier_added_and_removed_by_hand(self):
        opts = inventory.add_option(self.conn, "supplier", "  Vishal Metals ")
        self.assertIn("Vishal Metals", opts["supplier"])
        opts = inventory.delete_option(self.conn, "supplier", "Vishal Metals")
        self.assertNotIn("Vishal Metals", opts["supplier"])

    def test_backfill_picks_up_existing_heats(self):
        # a heat written before suppliers were a list
        self.conn.execute(
            "INSERT INTO heat (heat_number, date_received, supplier, rods_received)"
            " VALUES ('OLD-1','2026-08-01','  Legacy Mills  ',5)")
        self.conn.commit()
        self.assertNotIn("Legacy Mills", inventory.list_options(self.conn)["supplier"])
        inventory.backfill_suppliers(self.db_path)
        self.assertIn("Legacy Mills", inventory.list_options(self.conn)["supplier"])

    def test_backfill_ignores_blanks(self):
        self.conn.execute(
            "INSERT INTO heat (heat_number, date_received, supplier, rods_received)"
            " VALUES ('OLD-2','2026-08-01','   ',5)")
        self.conn.execute(
            "INSERT INTO heat (heat_number, date_received, supplier, rods_received)"
            " VALUES ('OLD-3','2026-08-01',NULL,5)")
        self.conn.commit()
        inventory.backfill_suppliers(self.db_path)
        self.assertEqual(inventory.list_options(self.conn)["supplier"], [])

    def test_each_heat_keeps_its_own_chemistry(self):
        inventory.create_intake(self.conn, {
            "date_received": "2026-08-14", "supplier": "Jindal Steel",
            "material_class": "Steel",
            "pieces": [
                {"heat_number": "K-1", "length_mm": 10, "quantity": 1, "grade": "EN8",
                 "composition": [{"element": "C", "percent": 0.42},
                                 {"element": "Mn", "percent": 0.75}]},
                {"heat_number": "K-2", "length_mm": 8, "quantity": 1, "grade": "EN19",
                 "composition": [{"element": "Cr", "percent": 1.05}]},
            ]})
        got = {}
        for hn in ("K-1", "K-2"):
            hid = self.conn.execute(
                "SELECT id FROM heat WHERE heat_number=?", (hn,)).fetchone()["id"]
            h = inventory.get_heat(self.conn, hid)
            got[hn] = ({c["element"]: c["percent"] for c in h["composition"]}, h["grade"])
        self.assertEqual(got["K-1"], ({"C": 0.42, "Mn": 0.75}, "EN8"))
        self.assertEqual(got["K-2"], ({"Cr": 1.05}, "EN19"))

    def test_rows_sharing_a_heat_share_one_analysis(self):
        inventory.create_intake(self.conn, {
            "date_received": "2026-08-14", "material_class": "Steel",
            "pieces": [
                {"heat_number": "K-3", "length_mm": 10, "quantity": 1},
                {"heat_number": "K-3", "length_mm": 4, "quantity": 2,
                 "composition": [{"element": "C", "percent": 0.5}]},
            ]})
        hid = self.conn.execute(
            "SELECT id FROM heat WHERE heat_number='K-3'").fetchone()["id"]
        h = inventory.get_heat(self.conn, hid)
        # the later row supplied the analysis the first row omitted
        self.assertEqual([(c["element"], c["percent"]) for c in h["composition"]],
                         [("C", 0.5)])
        self.assertEqual(len(h["pieces"]), 2)

    def test_row_chemistry_beats_the_delivery_default(self):
        inventory.create_intake(self.conn, {
            "date_received": "2026-08-14", "material_class": "Steel",
            "composition": [{"element": "C", "percent": 0.2}],
            "pieces": [
                {"heat_number": "K-4", "length_mm": 10, "quantity": 1,
                 "composition": [{"element": "C", "percent": 0.9}]},
                {"heat_number": "K-5", "length_mm": 10, "quantity": 1},
            ]})
        def comp(hn):
            hid = self.conn.execute(
                "SELECT id FROM heat WHERE heat_number=?", (hn,)).fetchone()["id"]
            return {c["element"]: c["percent"]
                    for c in inventory.get_heat(self.conn, hid)["composition"]}
        self.assertEqual(comp("K-4"), {"C": 0.9})   # its own wins
        self.assertEqual(comp("K-5"), {"C": 0.2})   # falls back to the delivery's

    def test_intake_learns_supplier_and_elements(self):
        inventory.create_intake(self.conn, {
            "date_received": "2026-08-14", "supplier": "Vishal Metals",
            "material_class": "Steel",
            "pieces": [{"heat_number": "K-6", "length_mm": 10, "quantity": 1,
                        "composition": [{"element": "Zz", "percent": 0.1}]}]})
        opts = inventory.list_options(self.conn)
        self.assertIn("Vishal Metals", opts["supplier"])
        self.assertIn("Zz", opts["element"])

    def test_bad_chemistry_still_rejected_on_a_row(self):
        with self.assertRaises(ValueError):
            inventory.create_intake(self.conn, {
                "date_received": "2026-08-14", "material_class": "Steel",
                "pieces": [{"heat_number": "K-7", "length_mm": 10, "quantity": 1,
                            "composition": [{"element": "C", "percent": 150}]}]})
