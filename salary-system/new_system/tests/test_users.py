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


if __name__ == "__main__":
    unittest.main(verbosity=2)
