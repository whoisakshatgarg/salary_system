"""Users & Access spec: grants, module gating, and account-management guards."""

import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fastapi import HTTPException  # noqa: E402

from backend.core import db  # noqa: E402
from backend.core.deps import require_module  # noqa: E402
from backend.core.registry import ALL_KEYS  # noqa: E402
from backend.modules import users  # noqa: E402
from backend.modules.employees import seed  # noqa: E402

ADMIN = {"username": "admin", "role": "admin"}


class UsersBase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        os.environ["SALARY_DATA_DIR"] = self.tmp.name
        self.db_path = Path(self.tmp.name) / "test.db"
        db.init_db(self.db_path)
        self.conn = db.connect(self.db_path)
        seed.ensure_users(self.conn)

    def tearDown(self):
        self.conn.close()
        os.environ.pop("SALARY_DATA_DIR", None)
        self.tmp.cleanup()

    def row(self, username):
        return self.conn.execute(
            "SELECT * FROM app_user WHERE username=?", (username,)).fetchone()


class GrantsAndSeed(UsersBase):
    def test_two_default_accounts(self):
        names = [r["username"] for r in self.conn.execute("SELECT username FROM app_user")]
        self.assertEqual(names, ["admin", "operator"])

    def test_admin_implicitly_holds_every_grant(self):
        self.assertEqual(users.user_grants(self.row("admin")), ALL_KEYS)

    def test_operator_seeded_with_salary_only(self):
        self.assertEqual(users.user_grants(self.row("operator")), ["salary"])

    def test_pre_shell_accounts_backfilled(self):
        self.conn.execute(
            "INSERT INTO app_user (username, password_hash, role) VALUES ('old','x','operator')")
        self.conn.commit()
        seed.ensure_users(self.conn)
        self.assertEqual(users.user_grants(self.row("old")), ["salary"])

    def test_garbage_grants_parse_to_empty(self):
        self.conn.execute(
            "INSERT INTO app_user (username, password_hash, role, grants)"
            " VALUES ('bad','x','operator','not-json')")
        self.conn.commit()
        self.assertEqual(users.user_grants(self.row("bad")), [])


class ModuleGate(UsersBase):
    def check(self, key, username, role):
        dep = require_module(key)
        return dep(user={"username": username, "role": role}, conn=self.conn)

    def test_admin_passes_everything(self):
        for key in ALL_KEYS:
            self.assertEqual(self.check(key, "admin", "admin")["username"], "admin")

    def test_operator_passes_only_granted(self):
        self.assertEqual(self.check("salary", "operator", "operator")["username"], "operator")
        with self.assertRaises(HTTPException) as cm:
            self.check("inventory", "operator", "operator")
        self.assertEqual(cm.exception.status_code, 403)

    def test_unknown_key_fails_at_definition(self):
        with self.assertRaises(ValueError):
            require_module("no-such-module")


class AccountGuards(UsersBase):
    def test_create_validates(self):
        with self.assertRaises(HTTPException):  # short password
            users.create_user(users.UserIn(username="x", password="123"), ADMIN, self.conn)
        with self.assertRaises(HTTPException):  # duplicate
            users.create_user(users.UserIn(username="admin", password="123456"), ADMIN, self.conn)
        with self.assertRaises(HTTPException):  # unknown grant
            users.create_user(users.UserIn(username="y", password="123456",
                                           grants=["nope"]), ADMIN, self.conn)
        out = users.create_user(users.UserIn(username="office", password="secret1",
                                             grants=["inventory", "salary"]), ADMIN, self.conn)
        office = next(u for u in out if u["username"] == "office")
        self.assertEqual(sorted(office["grants"]), ["inventory", "salary"])

    def test_grant_edit_and_gate_together(self):
        users.create_user(users.UserIn(username="store", password="secret1",
                                       grants=["inventory"]), ADMIN, self.conn)
        dep = require_module("inventory")
        self.assertTrue(dep(user={"username": "store", "role": "operator"}, conn=self.conn))
        uid = self.row("store")["id"]
        users.update_user(uid, users.UserUpdateIn(grants=[]), ADMIN, self.conn)
        with self.assertRaises(HTTPException):
            dep(user={"username": "store", "role": "operator"}, conn=self.conn)

    def test_cannot_remove_last_admin(self):
        uid = self.row("admin")["id"]
        with self.assertRaises(HTTPException):  # demote
            users.update_user(uid, users.UserUpdateIn(role="operator"), ADMIN, self.conn)
        with self.assertRaises(HTTPException):  # delete self
            users.delete_user(uid, ADMIN, self.conn)
        # a second admin unlocks the demotion
        users.create_user(users.UserIn(username="boss2", password="secret1",
                                       role="admin"), ADMIN, self.conn)
        users.update_user(uid, users.UserUpdateIn(role="operator"), ADMIN, self.conn)
        self.assertEqual(self.row("admin")["role"], "operator")


class ReviewRegressions(UsersBase):
    """Each test pins a defect confirmed (and fixed) in the adversarial review."""

    def test_salary_grant_gates_the_whole_employees_router(self):
        # A grants-less account must not reach roster/attendance/sync routes.
        from backend.modules.employees.router import router as emp_router
        self.assertTrue(emp_router.dependencies, "employees router must carry a gate")
        users.create_user(users.UserIn(username="nogrants", password="secret1",
                                       grants=[]), ADMIN, self.conn)
        dep = require_module("salary")
        with self.assertRaises(HTTPException) as cm:
            dep(user={"username": "nogrants", "role": "operator"}, conn=self.conn)
        self.assertEqual(cm.exception.status_code, 403)

    def test_deleted_account_loses_access_instantly(self):
        from backend.core.auth import create_token
        from backend.core.deps import current_user
        users.create_user(users.UserIn(username="gone", password="secret1"),
                          ADMIN, self.conn)
        token = create_token("gone", "operator")
        self.assertEqual(current_user(session=token, conn=self.conn)["username"], "gone")
        uid = self.row("gone")["id"]
        users.delete_user(uid, ADMIN, self.conn)
        with self.assertRaises(HTTPException) as cm:  # valid cookie, dead account
            current_user(session=token, conn=self.conn)
        self.assertEqual(cm.exception.status_code, 401)

    def test_demoted_admin_role_comes_from_db_not_cookie(self):
        from backend.core.auth import create_token
        from backend.core.deps import current_user
        users.create_user(users.UserIn(username="boss2", password="secret1",
                                       role="admin"), ADMIN, self.conn)
        token = create_token("boss2", "admin")   # 7-day cookie stamped 'admin'
        uid = self.row("boss2")["id"]
        users.update_user(uid, users.UserUpdateIn(role="operator"), ADMIN, self.conn)
        self.assertEqual(current_user(session=token, conn=self.conn)["role"], "operator")

    def test_username_charset_protects_token_format(self):
        for bad in ("a|b", "x\ny", "há¡"):
            with self.assertRaises(HTTPException):
                users.create_user(users.UserIn(username=bad, password="secret1"),
                                  ADMIN, self.conn)
        users.create_user(users.UserIn(username="R.K. Sharma-2", password="secret1"),
                          ADMIN, self.conn)  # spaces, dots, dashes stay legal


class EmployeeModule(UsersBase):
    """Employee Management additions: multi-key gate, documents, leave bank."""

    def setUp(self):
        super().setUp()
        self.conn.execute(
            "INSERT INTO employee (id, name, dept, base_salary, overtime_eligible,"
            " leave_balance, active) VALUES (1,'Test Man','QA',10000,0,5,1),"
            " (2,'OT Man','CNC',9000,1,0,1)")
        self.conn.commit()
        from backend.modules.employees import repo as emp_repo
        self.repo = emp_repo

    def test_multi_key_gate(self):
        users.create_user(users.UserIn(username="hr", password="secret1",
                                       grants=["employees"]), ADMIN, self.conn)
        shared = require_module("salary", "employees")
        em_only = require_module("employees")
        hr = {"username": "hr", "role": "operator"}
        op = {"username": "operator", "role": "operator"}
        self.assertTrue(shared(user=hr, conn=self.conn))     # HR reads the roster
        self.assertTrue(em_only(user=hr, conn=self.conn))    # and EM surfaces
        self.assertTrue(shared(user=op, conn=self.conn))     # operator: attendance ok
        with self.assertRaises(HTTPException):               # but not documents
            em_only(user=op, conn=self.conn)

    def test_documents_roundtrip_and_atomicity(self):
        from backend.core import paths
        docs = self.repo.save_documents(self.conn, 1, "Aadhaar",
                                        [("card.pdf", "application/pdf", b"%PDF x")])
        self.assertEqual(docs[0]["label"], "Aadhaar")
        stored = self.conn.execute(
            "SELECT stored_name FROM employee_document").fetchone()["stored_name"]
        self.assertTrue((paths.employee_files_dir() / stored).is_file())
        with self.assertRaises(ValueError):  # one bad file -> nothing saved
            self.repo.save_documents(self.conn, 1, "", [
                ("ok.pdf", "application/pdf", b"%PDF y"),
                ("bad.exe", "application/x-msdownload", b"MZ")])
        self.assertEqual(len(self.repo.list_documents(self.conn, 1)), 1)
        self.repo.delete_document(self.conn, docs[0]["id"])
        self.assertFalse((paths.employee_files_dir() / stored).exists())

    def test_profile_update_never_touches_pay_or_leave(self):
        # The split exists so a stale EM form can't revert Pay Setup edits.
        self.repo.update_employee_profile(self.conn, 1, {
            "name": "Renamed Man", "dept": "CNC", "shift": "N",
            "overtime_eligible": False, "date_joined": "2020-01-01"})
        emp = self.repo.get_employee(self.conn, 1)
        self.assertEqual(emp["name"], "Renamed Man")
        self.assertEqual(emp["base_salary"], 10000)   # untouched
        self.assertEqual(emp["leave_balance"], 5)     # untouched

    def test_pay_update_never_touches_profile_or_leave(self):
        self.repo.update_employee_pay(self.conn, 1, {
            "base_salary": 12345, "pf_applicable": True,
            "esi_applicable": False, "rem_advance": 700})
        emp = self.repo.get_employee(self.conn, 1)
        self.assertEqual(emp["base_salary"], 12345)
        self.assertEqual(emp["name"], "Test Man")     # untouched
        self.assertEqual(emp["leave_balance"], 5)     # untouched

    def test_create_with_null_leave_balance_seeds_default(self):
        # EmployeeIn allows leave_balance=None ("seed for me") — the repo must
        # not crash on int(None). Caught by the EM-page E2E.
        from backend.core.rules import load_rules
        new_id = self.repo.create_employee(self.conn, {
            "name": "Null Leave", "dept": "QA", "base_salary": 9000,
            "overtime_eligible": False, "leave_balance": None,
        }, load_rules())
        emp = self.repo.get_employee(self.conn, new_id)
        self.assertIsInstance(emp["leave_balance"], int)

    def test_leave_adjust(self):
        emp = self.repo.adjust_leave(self.conn, 1, 3)
        self.assertEqual(emp["leave_balance"], 8)
        emp = self.repo.adjust_leave(self.conn, 1, -2)
        self.assertEqual(emp["leave_balance"], 6)
        with self.assertRaises(ValueError):   # bank can't go negative
            self.repo.adjust_leave(self.conn, 1, -99)
        with self.assertRaises(ValueError):   # OT employees have no bank
            self.repo.adjust_leave(self.conn, 2, 1)


if __name__ == "__main__":
    unittest.main(verbosity=2)
