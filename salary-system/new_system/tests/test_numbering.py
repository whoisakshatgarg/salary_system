"""Spec for the document numbering registry (CONVENTIONS §3/§4) and for the
wave-1 schema reaching a database that already holds live data.

Every pattern here is pinned against the EXACT example printed on a reference
document — that is the whole point of the file: if a format drifts, the
company's paperwork stops matching itself.
"""

import os
import sqlite3
import sys
import tempfile
import unittest
from datetime import date, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend.core import db, numbering  # noqa: E402
from backend.modules import settings  # noqa: E402

ADMIN = {"username": "admin", "role": "admin"}


class NumberingBase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        os.environ["SALARY_DATA_DIR"] = self.tmp.name
        self.db_path = Path(self.tmp.name) / "test.db"
        db.init_db(self.db_path)
        self.conn = db.connect(self.db_path)

    def tearDown(self):
        self.conn.close()
        os.environ.pop("SALARY_DATA_DIR", None)
        self.tmp.cleanup()


class Quotations(NumberingBase):
    def test_the_seeded_client_carries_on_from_the_reference(self):
        numbering.ensure_seeds(self.conn)
        self.assertEqual(numbering.quotation_no(self.conn, "T04", date(2026, 8, 21)),
                         "T04/AT/210826/317")
        self.assertEqual(numbering.quotation_no(self.conn, "T04", date(2026, 8, 21)),
                         "T04/AT/210826/318")

    def test_a_new_client_starts_at_one(self):
        self.assertEqual(numbering.quotation_no(self.conn, "S01", date(2026, 8, 13)),
                         "S01/AT/130826/1")

    def test_clients_are_isolated_from_each_other(self):
        numbering.ensure_seeds(self.conn)
        self.assertEqual(numbering.quotation_no(self.conn, "E01", date(2026, 8, 3)),
                         "E01/AT/030826/595")
        self.assertEqual(numbering.quotation_no(self.conn, "T04", date(2026, 8, 13)),
                         "T04/AT/130826/317")   # later date, smaller serial (§9-A)

    def test_the_serial_does_not_reset_with_the_fiscal_year(self):
        numbering.quotation_no(self.conn, "S01", date(2026, 8, 13))
        self.assertEqual(numbering.quotation_no(self.conn, "S01", date(2027, 4, 2)),
                         "S01/AT/020427/2")

    def test_a_client_code_is_required(self):
        with self.assertRaises(ValueError):
            numbering.quotation_no(self.conn, "", date(2026, 8, 13))


class Acknowledgements(NumberingBase):
    def test_the_reference_example(self):
        self.assertEqual(numbering.ack_ref(self.conn, "E01", date(2026, 8, 21)),
                         "E01.21.08.26.01")

    def test_the_sequence_runs_within_a_day_and_resets_the_next(self):
        self.assertEqual(numbering.ack_ref(self.conn, "E01", date(2026, 8, 21)),
                         "E01.21.08.26.01")
        self.assertEqual(numbering.ack_ref(self.conn, "E01", date(2026, 8, 21)),
                         "E01.21.08.26.02")
        self.assertEqual(numbering.ack_ref(self.conn, "E01", date(2026, 8, 22)),
                         "E01.22.08.26.01")

    def test_each_client_has_its_own_day(self):
        numbering.ack_ref(self.conn, "E01", date(2026, 8, 21))
        self.assertEqual(numbering.ack_ref(self.conn, "T04", date(2026, 8, 21)),
                         "T04.21.08.26.01")


class WorkOrders(NumberingBase):
    def test_long_and_short_forms_from_the_ack_date(self):
        numbering.ensure_seeds(self.conn)
        wo = numbering.work_order_no(self.conn, "E01", date(2026, 8, 21))
        self.assertEqual(wo, {"long": "E01.21.08.26.253.26", "short": "253/26"})

    def test_the_serial_resets_each_calendar_year(self):
        numbering.ensure_seeds(self.conn)
        numbering.work_order_no(self.conn, "E01", date(2026, 8, 21))
        self.assertEqual(numbering.work_order_no(self.conn, "E01", date(2026, 12, 31))["short"],
                         "254/26")
        # January is a new counter, not the next number (§9-C)
        self.assertEqual(numbering.work_order_no(self.conn, "E01", date(2027, 1, 2))["short"],
                         "1/27")
        self.assertEqual(numbering.work_order_no(self.conn, "S01", date(2023, 5, 16))["long"],
                         "S01.16.05.23.1.23")


class Invoices(NumberingBase):
    def test_the_seeded_series_carries_on(self):
        numbering.ensure_seeds(self.conn)
        self.assertEqual(numbering.invoice_no(self.conn, date(2026, 8, 14)),
                         "AT/EI/26-27/169")

    def test_it_resets_in_april(self):
        numbering.ensure_seeds(self.conn)
        numbering.invoice_no(self.conn, date(2026, 8, 14))
        self.assertEqual(numbering.invoice_no(self.conn, date(2027, 3, 31)),
                         "AT/EI/26-27/170")     # March is still the same year
        self.assertEqual(numbering.invoice_no(self.conn, date(2027, 4, 1)),
                         "AT/EI/27-28/001")

    def test_bom_numbers_run_per_fiscal_year(self):
        self.assertEqual(numbering.bom_no(self.conn, date(2026, 8, 21)),
                         "AT/BOM/26-27/001")
        self.assertEqual(numbering.bom_no(self.conn, date(2026, 8, 22)),
                         "AT/BOM/26-27/002")


class TestCertificates(unittest.TestCase):
    """Derived from the invoice it certifies — no counter of its own."""

    def test_the_reference_example(self):
        self.assertEqual(numbering.tc_no("59812", "AT/EI/24-25/047"),
                         "AT/TC/59812/EI-047/24-25")

    def test_it_follows_whatever_the_invoice_says(self):
        self.assertEqual(numbering.tc_no("60543", "AT/EI/26-27/169"),
                         "AT/TC/60543/EI-169/26-27")

    def test_nonsense_in_means_an_error_not_a_wrong_number(self):
        for po, inv in (("59812", "EI-047"), ("59812", ""), ("", "AT/EI/24-25/047")):
            with self.assertRaises(ValueError):
                numbering.tc_no(po, inv)


class OutsourcingAndVendors(NumberingBase):
    def test_outsourced_item_ids(self):
        self.assertEqual(numbering.os_item_id(self.conn), "OS-0001")
        self.assertEqual(numbering.os_item_id(self.conn), "OS-0002")

    def test_outsourced_purchase_orders_run_per_fiscal_year(self):
        self.assertEqual(numbering.os_po_no(self.conn, date(2026, 8, 21)),
                         "AT/OS/26-27/001")
        self.assertEqual(numbering.os_po_no(self.conn, date(2027, 4, 1)),
                         "AT/OS/27-28/001")

    def test_vendor_codes(self):
        self.assertEqual(numbering.vendor_code(self.conn), "V01")
        self.assertEqual(numbering.vendor_code(self.conn), "V02")


class CounterMechanics(NumberingBase):
    def test_peek_does_not_consume(self):
        numbering.ensure_seeds(self.conn)
        self.assertEqual(numbering.peek(self.conn, "inv:26-27"), 169)
        self.assertEqual(numbering.peek(self.conn, "inv:26-27"), 169)
        self.assertEqual(numbering.invoice_no(self.conn, date(2026, 8, 14)),
                         "AT/EI/26-27/169")
        self.assertEqual(numbering.peek(self.conn, "inv:26-27"), 170)

    def test_peek_on_an_untouched_scope_is_one(self):
        self.assertEqual(numbering.peek(self.conn, "qtn:R01"), 1)

    def test_seeds_never_clobber_a_counter_that_has_moved(self):
        numbering.ensure_seeds(self.conn)
        numbering.invoice_no(self.conn, date(2026, 8, 14))       # 169 -> next is 170
        numbering.ensure_seeds(self.conn)
        self.assertEqual(numbering.peek(self.conn, "inv:26-27"), 170)

    def test_a_rolled_back_caller_does_not_burn_a_serial(self):
        """The take joins the caller's transaction, so a save that fails
        leaves the counter where it was."""
        self.conn.execute("BEGIN IMMEDIATE")
        numbering.quotation_no(self.conn, "T04", date(2026, 8, 21))
        self.conn.rollback()
        self.assertEqual(numbering.peek(self.conn, "qtn:T04"), 1)

    def test_a_take_of_its_own_commits(self):
        numbering.quotation_no(self.conn, "T04", date(2026, 8, 21))
        other = db.connect(self.db_path)
        try:
            self.assertEqual(numbering.peek(other, "qtn:T04"), 2)
        finally:
            other.close()


class DateRenderers(unittest.TestCase):
    def test_ordinal_apostrophe(self):
        cases = [(date(2026, 8, 4), "04th Aug' 2026"),
                 (date(2026, 8, 13), "13th Aug' 2026"),
                 (date(2025, 6, 30), "30th Jun' 2025"),
                 (date(2026, 1, 1), "01st Jan' 2026"),
                 (date(2026, 1, 2), "02nd Jan' 2026"),
                 (date(2026, 1, 3), "03rd Jan' 2026"),
                 (date(2026, 1, 11), "11th Jan' 2026"),   # not 11st
                 (date(2026, 1, 12), "12th Jan' 2026"),
                 (date(2026, 1, 13), "13th Jan' 2026"),
                 (date(2026, 1, 21), "21st Jan' 2026"),
                 (date(2026, 1, 22), "22nd Jan' 2026"),
                 (date(2026, 1, 23), "23rd Jan' 2026"),
                 (date(2026, 12, 31), "31st Dec' 2026")]
        for d, expect in cases:
            self.assertEqual(numbering.ordinal_apostrophe(d), expect, d)

    def test_the_other_three(self):
        self.assertEqual(numbering.ddmmyyyy(date(2026, 8, 14)), "14/08/2026")
        self.assertEqual(numbering.dtd_ddmmyy(date(2026, 3, 6)), "Dtd. 06.03.26")
        self.assertEqual(numbering.us_mmddyy(date(2024, 5, 10)), "05-10-24")

    def test_fiscal_and_two_digit_years(self):
        self.assertEqual(numbering.fy(date(2026, 8, 14)), "26-27")
        self.assertEqual(numbering.fy(date(2027, 3, 31)), "26-27")
        self.assertEqual(numbering.fy(date(2027, 4, 1)), "27-28")
        self.assertEqual(numbering.yy(date(2026, 8, 14)), "26")
        self.assertEqual(numbering.ddmmyy(date(2026, 8, 4)), "040826")

    def test_iso_strings_and_datetimes_are_accepted(self):
        """Dates come out of the DB as ISO text; a renderer that only took
        date objects would push str parsing into every payload builder."""
        self.assertEqual(numbering.ordinal_apostrophe("2026-08-04"), "04th Aug' 2026")
        self.assertEqual(numbering.ddmmyyyy(datetime(2026, 8, 14, 9, 30)), "14/08/2026")
        with self.assertRaises(ValueError):
            numbering.ddmmyyyy("not a date")


class SettingsNumbering(NumberingBase):
    """Settings → Numbering: the seeds, editable without a code change."""

    def test_every_live_counter_is_listed_with_a_friendly_label(self):
        numbering.ensure_seeds(self.conn)
        rows = {r["scope"]: r for r in settings.numbering_counters(self.conn)}
        self.assertEqual(rows["qtn:T04"]["next_seq"], 317)
        self.assertEqual(rows["qtn:T04"]["label"], "Quotation — T04")
        self.assertEqual(rows["inv:26-27"]["label"], "Export invoice — 26-27")
        self.assertEqual(rows["wo:26"]["label"], "Work order — 26")

    def test_labels_for_the_scopes_with_no_suffix(self):
        self.assertEqual(numbering.label_for("vendor"), "Vendor code")
        self.assertEqual(numbering.label_for("os_item"), "Outsourced item ID")
        self.assertEqual(numbering.label_for("ack:E01:210826"),
                         "PO acknowledgement — E01 210826")

    def test_an_edit_changes_the_next_number_handed_out(self):
        numbering.ensure_seeds(self.conn)
        settings.set_numbering_counter(
            settings.CounterIn(scope="inv:26-27", next_seq=200), ADMIN, self.conn)
        self.assertEqual(numbering.invoice_no(self.conn, date(2026, 8, 14)),
                         "AT/EI/26-27/200")

    def test_a_scope_that_does_not_exist_yet_can_be_seeded(self):
        """Reotemp's first quotation hasn't been raised — the counter still
        has to be settable (CONVENTIONS §9-F)."""
        out = settings.set_numbering_counter(
            settings.CounterIn(scope="qtn:R01", next_seq=12), ADMIN, self.conn)
        self.assertIn("qtn:R01", {r["scope"] for r in out})
        self.assertEqual(numbering.quotation_no(self.conn, "R01", date(2026, 8, 21)),
                         "R01/AT/210826/12")

    def test_nonsense_is_refused(self):
        from fastapi import HTTPException
        for scope, seq in (("qtn:T04", 0), ("qtn:T04", -3), ("", 5),
                           ("a scope with spaces", 5), ("qtn:T04", 9_000_000)):
            with self.assertRaises(HTTPException):
                settings.set_numbering_counter(
                    settings.CounterIn(scope=scope, next_seq=seq), ADMIN, self.conn)


# The shape of the owner's database BEFORE wave 1: no doc_counter, no paper, no
# outsourcing, and `document` without its revision columns.
_OLD_SHAPE = """
CREATE TABLE customer (
    id INTEGER PRIMARY KEY AUTOINCREMENT, code TEXT UNIQUE,
    name TEXT NOT NULL UNIQUE, gstin TEXT, address_billing TEXT,
    address_shipping TEXT, payment_terms TEXT, notes TEXT,
    active INTEGER NOT NULL DEFAULT 1, created_at TEXT
);
CREATE TABLE customer_order (
    id INTEGER PRIMARY KEY AUTOINCREMENT, order_no TEXT NOT NULL UNIQUE,
    customer_id INTEGER NOT NULL REFERENCES customer(id), customer_po TEXT,
    stage TEXT NOT NULL DEFAULT 'enquiry', order_date TEXT NOT NULL,
    due_date TEXT, notes TEXT, created_at TEXT
);
CREATE TABLE document (
    id INTEGER PRIMARY KEY AUTOINCREMENT, kind TEXT NOT NULL,
    doc_no TEXT NOT NULL UNIQUE,
    customer_id INTEGER NOT NULL REFERENCES customer(id),
    order_id INTEGER REFERENCES customer_order(id), doc_date TEXT NOT NULL,
    valid_until TEXT, reference TEXT, tax_pct REAL NOT NULL DEFAULT 0,
    notes TEXT, terms TEXT, status TEXT NOT NULL DEFAULT 'draft', created_at TEXT
);
CREATE TABLE document_line (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    document_id INTEGER NOT NULL REFERENCES document(id) ON DELETE CASCADE,
    drawing_id INTEGER, description TEXT, qty REAL NOT NULL, unit TEXT,
    rate REAL NOT NULL DEFAULT 0
);
CREATE TABLE costing_material (
    id INTEGER PRIMARY KEY AUTOINCREMENT, costing_id INTEGER NOT NULL,
    heat_id INTEGER, heat_number TEXT, material_label TEXT, unit TEXT,
    unit_cost REAL NOT NULL DEFAULT 0, qty_per_piece REAL NOT NULL DEFAULT 0,
    cost REAL NOT NULL DEFAULT 0
);
"""

_NEW_TABLES = ("doc_counter", "paper", "order_attachment", "vendor", "os_order",
               "os_order_item", "os_receipt", "os_receipt_line", "os_item",
               "os_movement", "os_document")


class MigratingALiveDatabase(unittest.TestCase):
    """The owner's salary.db already holds orders and documents. Wave 1 has to
    land on it without a rebuild: new TABLES arrive because SCHEMA is re-run
    (every statement IF NOT EXISTS), new COLUMNS through _MIGRATIONS."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        os.environ["SALARY_DATA_DIR"] = self.tmp.name
        self.db_path = Path(self.tmp.name) / "existing.db"
        old = sqlite3.connect(self.db_path)
        old.executescript(_OLD_SHAPE)
        old.execute("INSERT INTO customer (code, name) VALUES ('AC01','Acme Pumps')")
        old.execute("INSERT INTO document (kind, doc_no, customer_id, doc_date)"
                    " VALUES ('quotation','QUO-25-26-004',1,'2025-06-01')")
        old.execute("INSERT INTO document_line (document_id, description, qty, rate)"
                    " VALUES (1,'Shaft',10,250)")
        old.commit()
        old.close()

    def tearDown(self):
        os.environ.pop("SALARY_DATA_DIR", None)
        self.tmp.cleanup()

    def cols(self, conn, table):
        return {r["name"] for r in conn.execute(f"PRAGMA table_info({table})")}

    def test_the_upgrade_adds_everything_and_keeps_the_data(self):
        db.init_db(self.db_path)
        conn = db.connect(self.db_path)
        try:
            names = {r["name"] for r in conn.execute("SELECT name FROM sqlite_master")}
            for t in _NEW_TABLES:
                self.assertIn(t, names, t)
            self.assertIn("idx_paper_order", names)
            self.assertIn("idx_order_att_order", names)
            self.assertTrue({"revises_document_id", "superseded_by"}
                            <= self.cols(conn, "document"))
            self.assertIn("os_item_id", self.cols(conn, "document_line"))
            self.assertIn("os_item_id", self.cols(conn, "costing_material"))
            # the live rows are exactly as they were
            row = conn.execute("SELECT doc_no, status FROM document").fetchone()
            self.assertEqual((row["doc_no"], row["status"]), ("QUO-25-26-004", "draft"))
            self.assertEqual(conn.execute("SELECT code FROM customer").fetchone()["code"],
                             "AC01")
            # and the new machinery works on the upgraded file
            numbering.ensure_seeds(conn)
            self.assertEqual(numbering.invoice_no(conn, date(2026, 8, 14)),
                             "AT/EI/26-27/169")
        finally:
            conn.close()

    def test_running_it_twice_changes_nothing(self):
        db.init_db(self.db_path)
        conn = db.connect(self.db_path)
        numbering.ensure_seeds(conn)
        numbering.invoice_no(conn, date(2026, 8, 14))
        conn.close()
        db.init_db(self.db_path)             # a second start
        conn = db.connect(self.db_path)
        try:
            self.assertEqual(numbering.peek(conn, "inv:26-27"), 170)
            self.assertEqual(conn.execute("SELECT COUNT(*) AS n FROM document"
                                          ).fetchone()["n"], 1)
        finally:
            conn.close()


if __name__ == "__main__":
    unittest.main()
