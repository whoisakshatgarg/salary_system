"""One-time data migration: bring the 70 real APEX THERMOCON employees and the
default users forward from the legacy `old_system` into the new SQLite store.

Ported verbatim from ``old_system/Payroll-System/add_emp_data_sys.py`` (employee
master + per-employee leave balances). Run once:  ``python -m backend.seed``.

Employees are inserted in their original emp_id order into an empty table, so
each new ``employee.id`` equals the old ``emp_id`` (preserving every reference
in historical exports). Re-running is a no-op once the table is populated.
"""

from __future__ import annotations

from ...core import db
from ...core.auth import hash_password

# (name, base_salary, pf, esi, overtime_eligible, dept) — all shift 'D'.
# Ported from add_emp_data_sys.py; 'Y'->True, 'N'->False.
EMPLOYEES = [
    ("Sunil Singh", 57000, True, True, False, "CNC"),
    ("Shaiju Kakkariyal", 49000, True, True, False, "Production"),
    ("Rajpati Yadav", 38000, False, False, False, "Admin"),
    ("Anil Gupta", 35000, False, False, False, "QA"),
    ("Aditya Kumar Singh", 38700, True, True, False, "QA"),
    ("Dilip Kumar Tiwari", 26500, True, True, False, "QA"),
    ("Sandeep", 17600, True, True, True, "QA"),
    ("Harish Vishwakarma", 16500, True, True, False, "QA"),
    ("Dilip Kumar Giri", 12300, True, True, True, "QA"),
    ("Vijay Kumar", 10200, False, False, True, "QA"),
    ("Govind - QA", 10200, False, False, True, "QA"),
    ("Brikesh", 9500, False, False, True, "QA"),
    ("Harsh Verma", 15000, False, False, False, "QA"),
    ("Pawan", 18200, True, True, True, "Stores"),
    ("Sachin Sharma", 26000, False, False, False, "Accounts"),
    ("Gaurav Sharma", 24300, True, True, False, "Accounts"),
    ("Shambhu Sharma", 16000, True, True, False, "Accounts"),
    ("Bahadur Singh", 27200, False, False, True, "CNC"),
    ("Shobhit Sharma", 17100, True, True, True, "CNC"),
    ("Krishna", 15600, True, True, True, "CNC"),
    ("Satish Kumar", 11400, True, True, True, "CNC"),
    ("Anwar Hussain Khan", 12100, True, True, True, "CNC"),
    ("Bhagat Singh", 11400, True, True, True, "CNC"),
    ("Vijay Mal Yadav", 11000, True, True, True, "CNC"),
    ("Vinesh", 10500, False, False, True, "CNC"),
    ("Jatin", 11700, False, False, True, "CNC"),
    ("Pawan Kumar", 10500, False, False, True, "CNC"),
    ("Akash", 10200, False, False, True, "CNC"),
    ("Amit Kumar", 11200, False, False, True, "CNC"),
    ("Vivek Kumar", 10200, False, False, True, "CNC"),
    ("Monu Rajbhar", 10000, False, False, True, "CNC"),
    ("Akhilesh", 10500, False, False, True, "CNC"),
    ("Soman Bera", 9500, False, False, True, "CNC"),
    ("Tanuj Pachauri", 9500, False, False, True, "CNC"),
    ("Karan Sharma", 9000, False, False, True, "CNC"),
    ("Rohitash Tiwari", 28500, True, True, True, "Conventional"),
    ("Lokesh Kumar", 22400, True, True, True, "Conventional"),
    ("Jila Singh", 21400, True, True, True, "Conventional"),
    ("Rinku Kumar", 18000, True, True, True, "Conventional"),
    ("Neeraj Kumar", 16800, False, False, True, "Conventional"),
    ("Uttam Dalapati", 14100, False, False, True, "Conventional"),
    ("Prabhu Dayal", 14900, True, True, True, "Electrical"),
    ("Dharmender", 12400, True, True, True, "Conventional"),
    ("Govind", 11600, True, True, True, "Conventional"),
    ("Vipin Kumar", 14500, True, True, True, "Buffing"),
    ("Risi Kumar", 12500, True, True, True, "Buffing"),
    ("Manoj Kumar", 13900, True, True, True, "Buffing"),
    ("Dheerendra", 10000, False, False, True, "Buffing"),
    ("Lotan", 11000, False, False, True, "Buffing"),
    ("Varnit", 10700, False, False, False, "Admin"),
    ("Daud", 12000, False, False, True, "Conventional"),
    ("Ramnaresh", 11100, False, False, True, "Conventional"),
    ("Santosh", 11200, False, False, True, "Conventional"),
    ("Ravinder", 11700, False, False, True, "Conventional"),
    ("Alok", 11100, False, False, True, "Conventional"),
    ("Rajesh", 11100, False, False, True, "Conventional"),
    ("Brijbhan Pal", 11300, False, False, True, "Conventional"),
    ("Anish Kumar", 11500, False, False, True, "Cutting"),
    ("Narayan", 10500, False, False, True, "Conventional"),
    ("Munendra Singh", 10200, False, False, True, "Conventional"),
    ("Raj Mangal", 10000, False, False, True, "Conventional"),
    ("Sukru Bage", 11000, False, False, True, "Conventional"),
    ("Karan Pal", 9500, False, False, True, "Conventional"),
    ("Bharat Mahto", 9500, False, False, True, "Cutting"),
    ("Nikas", 15000, False, False, False, "Admin"),
    ("Ansh", 9000, False, False, True, "CNC"),
    ("Lallu", 9000, False, False, True, "Conventional"),
    ("Arjun", 12000, False, False, True, "Buffing"),
    ("Rahul", 12000, False, False, False, "QA"),
    ("Ajay Kumar", 9500, False, False, True, "CNC"),
]

# old emp_id -> current remaining-leave balance (from remaining_holidays seed).
LEAVE_BALANCE = {
    1: 12, 2: 0, 3: 7, 4: 9, 5: 11, 6: 12, 8: 10, 13: 6,
    15: 7, 16: 3, 17: 12, 50: 6, 65: 12, 69: 7,
}

# Default logins (replaces the old plaintext `admin` table). CHANGE THESE.
#   admin    = the CEO (full access: salary, advances, exports, rules)
#   operator = attendance-entry employee (attendance only)
DEFAULT_USERS = [
    ("admin", "admin123", "admin"),
    ("operator", "operator123", "operator"),
    ("temp", "temp123", "operator"),
]


def ensure_users(conn) -> int:
    """Create any missing default users (idempotent). Runs on every startup."""
    created = 0
    for username, password, role in DEFAULT_USERS:
        cur = conn.execute(
            "INSERT OR IGNORE INTO app_user (username, password_hash, role) VALUES (?,?,?)",
            (username, hash_password(password), role),
        )
        created += cur.rowcount
    conn.commit()
    return created


def seed(db_path=None) -> dict:
    db.init_db(db_path)
    conn = db.connect(db_path)
    try:
        ensure_users(conn)
        existing = conn.execute("SELECT COUNT(*) AS n FROM employee").fetchone()["n"]
        if existing:
            return {"status": "skipped", "employees": existing}

        for idx, (name, salary, pf, esi, ot, dept) in enumerate(EMPLOYEES, start=1):
            conn.execute(
                """INSERT INTO employee
                   (id, name, dept, base_salary, pf_applicable, esi_applicable,
                    overtime_eligible, shift, rem_advance, leave_balance, active)
                   VALUES (?,?,?,?,?,?,?,?,?,?,1)""",
                (
                    idx, name, dept, salary,
                    int(pf), int(esi), int(ot), "D",
                    0, LEAVE_BALANCE.get(idx, 0),
                ),
            )
        conn.commit()
        return {"status": "seeded", "employees": len(EMPLOYEES), "users": len(DEFAULT_USERS)}
    finally:
        conn.close()


if __name__ == "__main__":
    result = seed()
    print(result)
