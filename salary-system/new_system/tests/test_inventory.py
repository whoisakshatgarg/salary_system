"""Inventory module spec: heats, derived stock, usage log, search, options,
attachments. Run:  python -m unittest tests.test_inventory
"""

import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend import db, inventory  # noqa: E402


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
        from backend import paths
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
        from backend import paths
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
        from backend import paths
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
