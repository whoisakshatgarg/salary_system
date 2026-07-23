"""Lock the ported business rules. Run:  python -m unittest discover -s tests

These tests are the executable specification of the legacy rules. If a future
config change or refactor alters payroll behaviour, one of these breaks — which
is exactly what the old system lacked.
"""

import calendar
import sys
import unittest
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend.payroll import (  # noqa: E402
    DayMark,
    compute_salary,
    days_in_period,
    summarize_attendance,
)
from backend.rules import load_rules  # noqa: E402

RULES = load_rules()
YEAR, MONTH = 2025, 6  # June 2025, 30 days


def make_month(absences=None):
    """Full month of present days; `absences` maps day-number -> overtime hours
    (or None) and marks that day absent."""
    absences = absences or {}
    marks = []
    for d in range(1, days_in_period(YEAR, MONTH) + 1):
        day = date(YEAR, MONTH, d)
        if d in absences:
            marks.append(DayMark(day, "A", absences[d]))
        else:
            marks.append(DayMark(day, "P", None))
    return marks


def present_all(overrides=None):
    """Full month present; `overrides` maps day-number -> overtime hours."""
    overrides = overrides or {}
    return [
        DayMark(date(YEAR, MONTH, d), "P", overrides.get(d))
        for d in range(1, days_in_period(YEAR, MONTH) + 1)
    ]


def non_sunday_days(count, gap=2):
    """First `count` day-numbers in the month that are not Sundays and are
    spaced >= `gap` apart (so they never form a 3-consecutive run)."""
    out, last = [], -10
    d = 1
    while len(out) < count and d <= days_in_period(YEAR, MONTH):
        if date(YEAR, MONTH, d).weekday() != 6 and d - last >= gap + 1:
            out.append(d)
            last = d
        d += 1
    return out


class SalaryFormula(unittest.TestCase):
    def base(self, **kw):
        defaults = dict(
            base=10000, attendance_fraction=1.0, overtime_hours=0,
            refreshment_days=0, pf=0, esi=0, adjusted_advance=0,
            period_days=30, dept="QA", emp_name="Test", rules=RULES,
        )
        defaults.update(kw)
        return compute_salary(**defaults)

    def test_plain_full_attendance(self):
        self.assertEqual(self.base().total, 10000)

    def test_attendance_fraction(self):
        self.assertEqual(self.base(attendance_fraction=0.5).total, 5000)

    def test_overtime_eight_hours_is_one_day(self):
        # 8 OT hours == one day's base == base/period_days.
        r = self.base(base=30000, overtime_hours=8, period_days=30)
        self.assertAlmostEqual(r.overtime_pay, 1000.0)
        self.assertEqual(r.total, 31000)

    def test_refreshment_pay(self):
        r = self.base(refreshment_days=3)  # rate 8/day -> +24
        self.assertEqual(r.refreshment_pay, 24)
        self.assertEqual(r.total, 10024)

    def test_deductions(self):
        r = self.base(pf=500, esi=200, adjusted_advance=1000)
        self.assertEqual(r.total, 10000 - 500 - 200 - 1000)


class BonusRule(unittest.TestCase):
    def calc(self, dept, name, frac=1.0):
        return compute_salary(
            base=10000, attendance_fraction=frac, overtime_hours=0,
            refreshment_days=0, pf=0, esi=0, adjusted_advance=0,
            period_days=30, dept=dept, emp_name=name, rules=RULES,
        )

    def test_cnc_full_attendance_gets_bonus(self):
        r = self.calc("CNC", "Krishna")
        self.assertEqual(r.bonus_status, "Y")
        self.assertEqual(r.bonus, 200)
        self.assertEqual(r.total, 10200)

    def test_excluded_employee_no_bonus(self):
        for name in ("Sunil Singh", "Bahadur Singh"):
            r = self.calc("CNC", name)
            self.assertEqual(r.bonus_status, "N")
            self.assertEqual(r.bonus, 0)
            self.assertEqual(r.total, 10000)

    def test_cnc_partial_attendance_no_bonus(self):
        r = self.calc("CNC", "Krishna", frac=0.9)
        self.assertEqual(r.bonus_status, "N")
        self.assertEqual(r.bonus, 0)

    def test_non_cnc_is_na(self):
        r = self.calc("QA", "Anybody")
        self.assertEqual(r.bonus_status, "NA")


class Attendance(unittest.TestCase):
    def test_absent_sunday_is_paid(self):
        # One absent Sunday on an overtime-eligible employee (no bank).
        sunday = next(
            d for d in range(1, 31) if date(YEAR, MONTH, d).weekday() == 6
        )
        s = summarize_attendance(
            make_month({sunday: None}),
            overtime_eligible=True, leave_balance=None, rules=RULES,
        )
        self.assertEqual(s.paid_sundays, 1)
        self.assertEqual(s.present_days, 30)
        self.assertEqual(s.attendance_percentage, 100.0)
        self.assertEqual(s.penalty_days, 0)

    def test_non_overtime_leave_bank_covers_absences(self):
        days = non_sunday_days(3)
        s = summarize_attendance(
            make_month({d: None for d in days}),
            overtime_eligible=False, leave_balance=10, rules=RULES,
        )
        self.assertEqual(s.present_days, 30)        # fully paid from bank
        self.assertEqual(s.attendance_percentage, 100.0)
        self.assertEqual(s.leave_used, 3)
        self.assertEqual(s.new_leave_balance, 7)
        self.assertEqual(s.penalty_days, 0)

    def test_non_overtime_absences_beyond_bank_are_unpaid(self):
        days = non_sunday_days(5)
        s = summarize_attendance(
            make_month({d: None for d in days}),
            overtime_eligible=False, leave_balance=2, rules=RULES,
        )
        self.assertEqual(s.penalty_days, 3)         # 5 absent - 2 bank
        self.assertEqual(s.new_leave_balance, 0)
        self.assertEqual(s.present_days, 27)
        self.assertEqual(s.attendance_percentage, 90.0)

    def test_overtime_eligible_tier_penalty(self):
        days = non_sunday_days(5)                    # spaced -> no 3-run
        s = summarize_attendance(
            make_month({d: None for d in days}),
            overtime_eligible=True, leave_balance=None, rules=RULES,
        )
        # 5 scattered absences -> tier 4..6 -> 2 penalty days (single scale).
        self.assertEqual(s.penalty_days, 2)
        self.assertEqual(s.present_days, 23)         # 25 present - 2 penalty
        self.assertEqual(s.base_present_days, 25)    # pre-penalty paid days
        self.assertEqual([r["rule"] for r in s.applied_rules], ["weekday_tier"])
        self.assertEqual(len(s.applied_rules[0]["dates"]), 5)  # all 5 leaves flagged

    def test_overtime_eligible_consecutive_run_penalty(self):
        # Find a Tue-Wed-Thu run (weekdays 1,2,3) — not an excluded pattern.
        start = next(
            d for d in range(1, 28)
            if date(YEAR, MONTH, d).weekday() == 1
        )
        run = {start: None, start + 1: None, start + 2: None}
        s = summarize_attendance(
            make_month(run),
            overtime_eligible=True, leave_balance=None, rules=RULES,
        )
        self.assertEqual(s.penalty_days, 1)          # one 3-day run
        self.assertEqual(s.present_days, 26)         # 27 present - 1
        self.assertEqual([r["rule"] for r in s.applied_rules], ["consecutive_run"])
        self.assertEqual(len(s.applied_rules[0]["dates"]), 3)

    def test_no_penalty_no_applied_rules(self):
        s = summarize_attendance(
            present_all(), overtime_eligible=True, leave_balance=None, rules=RULES,
        )
        self.assertEqual(s.applied_rules, [])
        self.assertEqual(s.penalty_days, 0)

    def test_single_scale_tier_governs(self):
        # 7 total absences (incl. a 3-run) -> tier 7–12 -> 3; the run does NOT add.
        start = next(d for d in range(1, 20) if date(YEAR, MONTH, d).weekday() == 1)
        absent = {start, start + 1, start + 2}
        d = start + 5
        while len(absent) < 7 and d <= days_in_period(YEAR, MONTH):
            if (date(YEAR, MONTH, d).weekday() != 6
                    and (d - 1) not in absent and (d + 1) not in absent):
                absent.add(d); d += 2
            else:
                d += 1
        s = summarize_attendance(
            make_month({x: None for x in absent}),
            overtime_eligible=True, leave_balance=None, rules=RULES,
        )
        self.assertEqual(s.penalty_days, 3)                       # tier 7–12 only
        self.assertEqual([r["rule"] for r in s.applied_rules], ["weekday_tier"])
        self.assertEqual(len(s.applied_rules[0]["dates"]), 7)     # all 7 flagged

    def test_three_scattered_no_penalty(self):
        # 3 NON-consecutive absences -> below the tier, no run -> 0.
        s = summarize_attendance(
            make_month({d: None for d in non_sunday_days(3)}),
            overtime_eligible=True, leave_balance=None, rules=RULES,
        )
        self.assertEqual(s.penalty_days, 0)
        self.assertEqual(s.applied_rules, [])

    def test_six_in_a_row_uses_tier(self):
        # Mon→Sat 6-in-a-row -> 6 total -> tier 4–6 -> 2 (run does not add).
        start = next(d for d in range(1, 18) if date(YEAR, MONTH, d).weekday() == 0)
        absent = {start + i for i in range(6)}      # Mon..Sat, all non-Sunday
        s = summarize_attendance(
            make_month({x: None for x in absent}),
            overtime_eligible=True, leave_balance=None, rules=RULES,
        )
        self.assertEqual([r["rule"] for r in s.applied_rules], ["weekday_tier"])
        self.assertEqual(s.penalty_days, 2)


class DerivedFields(unittest.TestCase):
    def test_refreshment_day_counting(self):
        sunday = next(d for d in range(1, 31) if date(YEAR, MONTH, d).weekday() == 6)
        weekday = next(d for d in range(1, 31) if date(YEAR, MONTH, d).weekday() != 6)
        other = next(d for d in range(weekday + 1, 31)
                     if date(YEAR, MONTH, d).weekday() != 6)
        s = summarize_attendance(
            present_all({weekday: 3.0, other: 2.0, sunday: 5.0}),
            overtime_eligible=True, leave_balance=None, rules=RULES,
        )
        # Only `weekday` qualifies: present, non-Sunday, OT>=3.
        self.assertEqual(s.refreshment_days, 1)
        self.assertEqual(s.total_overtime_hours, 10.0)  # 3+2+5

    def test_overtime_on_absent_day_ignored(self):
        weekday = next(d for d in range(1, 31) if date(YEAR, MONTH, d).weekday() != 6)
        s = summarize_attendance(
            make_month({weekday: 6.0}),  # absent but OT entered
            overtime_eligible=True, leave_balance=None, rules=RULES,
        )
        self.assertEqual(s.total_overtime_hours, 0.0)
        self.assertEqual(s.refreshment_days, 0)

    def test_days_in_period(self):
        self.assertEqual(days_in_period(2025, 2), 28)
        self.assertEqual(days_in_period(2024, 2), 29)  # leap
        self.assertEqual(days_in_period(2025, 12), 31)  # the old Dec bug


if __name__ == "__main__":
    unittest.main(verbosity=2)
