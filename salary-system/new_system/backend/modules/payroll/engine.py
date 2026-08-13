"""Payroll calculation engine for the APEX THERMOCON salary system.

This module is the reusable, hard-won core of the system: the exact business
rules ported from the legacy `old_system/Payroll-System` scripts
(`attendance.py`, `attend_save.py`, `calculateSalary.py`,
`calculateSalaryTable.py`). It is intentionally **pure** — no database, no UI,
no I/O — so the rules can be unit-tested and reused from the API, the exporters,
and a CLI alike.

Every rule is driven by `config/rules.json` (see `rules.py`), so policy can
change without editing this file. Where this rewrite deliberately diverges from
the old code (to fix a clear bug), the divergence is called out in a comment
tagged `DIVERGENCE:` and is guarded by a config flag so the old behaviour can be
restored.

Conventions
-----------
- A day's status is the string ``"P"`` (present) or ``"A"`` (absent), matching
  the old data. ``overtime`` is hours as a float (or ``None``/empty).
- ``weekday()``: Monday=0 .. Sunday=6 (Python's convention, as used by the old
  code).
- Money is rounded only at the final total, as the old system did.
"""

from __future__ import annotations

import calendar
from dataclasses import dataclass, field, asdict
from datetime import date


# --------------------------------------------------------------------------- #
# Inputs / outputs
# --------------------------------------------------------------------------- #
@dataclass
class DayMark:
    """One day of attendance for one employee."""

    day: date
    status: str = "P"            # "P" present, "A" absent
    overtime_hours: float | None = None

    @property
    def is_present(self) -> bool:
        return self.status == "P"

    @property
    def is_sunday(self) -> bool:
        return self.day.weekday() == 6


@dataclass
class AttendanceSummary:
    """Result of summarising a month of daily marks for one employee."""

    total_days: int
    raw_present_days: int          # 'P' count before any adjustment
    paid_sundays: int              # absent Sundays credited back
    base_present_days: float       # paid days BEFORE penalties (CEO's lever baseline)
    penalty_days: int              # excess-absence / leave penalty applied
    leave_used: int                # days drawn from the leave bank (non-OT)
    present_days: float            # final paid days after all adjustments
    attendance_percentage: float   # present_days / total_days * 100
    total_overtime_hours: float
    refreshment_days: int
    new_leave_balance: int | None  # remaining bank after this month (non-OT)
    applied_rules: list = field(default_factory=list)  # which penalty rules fired

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class SalaryBreakdown:
    """Itemised salary computation for one employee for one period."""

    base: float
    attendance_fraction: float
    base_att: float                # base * attendance_fraction
    overtime_hours: float
    overtime_pay: float
    refreshment_days: int
    refreshment_pay: float
    gross: float                   # base_att + overtime_pay + refreshment_pay
    pf: float
    esi: float
    adjusted_advance: float
    bonus: float
    bonus_status: str              # "Y" / "N" / "NA"
    total: int                     # final, rounded

    def to_dict(self) -> dict:
        return asdict(self)


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
def days_in_period(year: int, month: int) -> int:
    """Calendar days in a month. (Old: ``calendar.monthrange``.)"""
    return calendar.monthrange(year, month)[1]


def _maximal_runs(sorted_days: list[date], min_len: int) -> list[list[date]]:
    """Maximal runs of consecutive calendar days with length >= min_len.

    Each qualifying run counts once (a 3-in-a-row and a 6-in-a-row are both one
    run → one penalty). Absent Sundays are excluded by the caller, so a Sunday
    sitting between two absent days breaks the run.
    """
    runs: list[list[date]] = []
    cur: list[date] = []
    for d in sorted_days:
        if cur and (d - cur[-1]).days == 1:
            cur.append(d)
        else:
            if len(cur) >= min_len:
                runs.append(cur)
            cur = [d]
    if len(cur) >= min_len:
        runs.append(cur)
    return runs


def _highest_tier(tiers: list[dict], n: int) -> dict | None:
    """The tier with the largest ``min`` that is <= n. Using a lower-bound (not
    a range) means counts above the last tier still land in the top tier
    (e.g. 19 absences → the 13+ tier), avoiding the old 'fall through to zero'
    bug for very high absence counts.
    """
    best = None
    for t in tiers:
        if n >= t["min"] and (best is None or t["min"] > best["min"]):
            best = t
    return best


# --------------------------------------------------------------------------- #
# Attendance
# --------------------------------------------------------------------------- #
def summarize_attendance(
    marks: list[DayMark],
    *,
    overtime_eligible: bool,
    leave_balance: int | None,
    rules: dict,
) -> AttendanceSummary:
    """Collapse a month of daily marks into the paid-days summary.

    Faithfully reproduces the legacy logic from ``attendance.py`` /
    ``attend_save.py`` (the two old copies that disagreed only on a December
    date bug — this one is correct year-round), parameterised by `rules`.

    Two distinct policies, exactly as in the old system:

    * **Overtime-eligible** employees have *no* leave bank — every weekday
      absence is unpaid — but incur extra *penalty days* when absences pile up
      (consecutive-run rule + tiered thresholds).
    * **Non-overtime** employees draw absences from a paid-leave bank
      (`leave_balance`); only absences beyond the bank are unpaid.

    Absent Sundays are credited back as present when ``leave.sunday_is_paid``.
    """
    leave_cfg = rules["leave"]
    refresh_cfg = rules["refreshment"]
    pen_cfg = rules["absence_penalty"]

    total_days = len(marks)
    if total_days == 0:
        raise ValueError("No attendance days supplied")

    raw_present = sum(1 for m in marks if m.is_present)

    # Sundays that were marked absent are paid (not counted as leave).
    paid_sundays = 0
    if leave_cfg.get("sunday_is_paid", True):
        paid_sundays = sum(1 for m in marks if not m.is_present and m.is_sunday)

    present_days: float = raw_present + paid_sundays

    # Overtime hours: only on present days, and never on absent days.
    total_overtime_hours = sum(
        float(m.overtime_hours)
        for m in marks
        if m.is_present and m.overtime_hours not in (None, "")
    )

    # Refreshment days: present, non-Sunday, OT >= threshold.
    min_ot = float(refresh_cfg.get("min_overtime_hours", 3.0))
    exclude_sun = refresh_cfg.get("exclude_sunday", True)
    refreshment_days = sum(
        1
        for m in marks
        if m.is_present
        and m.overtime_hours not in (None, "")
        and (not (exclude_sun and m.is_sunday))
        and float(m.overtime_hours) >= min_ot
    )

    penalty_days = 0
    leave_used = 0
    new_leave_balance = leave_balance
    applied_rules: list[dict] = []

    absent_marks = [m for m in marks if not m.is_present]
    # The "holidays" that penalties act on: non-Sunday absent days (absent
    # Sundays are paid weekly-offs, already credited present above).
    holiday_dates = sorted(m.day for m in absent_marks if m.day.weekday() != 6)

    if overtime_eligible:
        # ---- Penalty days — single escalating scale, HIGHEST applies ------- #
        # They do NOT add. The monthly-volume tier governs from its `min` upward;
        # the consecutive-run rule only matters below the lowest tier (e.g. a
        # 3-in-a-row when there are too few total leaves to reach a tier).
        if pen_cfg.get("enabled", True):
            n = len(holiday_dates)
            tier = _highest_tier(pen_cfg.get("weekday_absence_tiers", []), n)
            run_cfg = pen_cfg.get("consecutive_run", {})
            if tier:
                penalty_days = int(tier["penalty_days"])
                applied_rules.append({
                    "rule": "weekday_tier",
                    "penalty": penalty_days,
                    # all non-Sunday leaves count toward the tier → all flagged
                    "dates": [d.isoformat() for d in holiday_dates],
                    "detail": f"{n} absences this month (tier {tier['min']}+): "
                              f"{penalty_days} penalty day(s)",
                })
            elif run_cfg.get("enabled", True):
                length = int(run_cfg.get("length", 3))
                per = int(run_cfg.get("penalty_days", 1))
                runs = _maximal_runs(holiday_dates, length)
                if runs:
                    penalty_days = per
                    run = runs[0]
                    applied_rules.append({
                        "rule": "consecutive_run",
                        "penalty": per,
                        "dates": [d.isoformat() for d in run],
                        "detail": f"{len(run)} consecutive absences "
                                  f"({run[0].isoformat()}…{run[-1].isoformat()}): {per} penalty day",
                    })
    else:
        # ---- Paid-leave bank (non-overtime employees) --------------------- #
        bank = leave_balance if leave_balance is not None else 0
        absent_count = total_days - present_days  # excludes paid Sundays
        if bank - absent_count >= 0:
            new_leave_balance = int(bank - absent_count)
            leave_used = int(absent_count)
            penalty_days = 0
        else:
            penalty_days = int(absent_count - bank)
            leave_used = int(bank)
            new_leave_balance = 0
            applied_rules.append({
                "rule": "leave_bank_exceeded",
                "penalty": penalty_days,
                "dates": [d.isoformat() for d in holiday_dates],
                "detail": f"Absences beyond {int(bank)}-day leave bank: "
                          f"+{penalty_days} unpaid day(s)",
            })
        # All absences are "covered" (paid from bank); the unpaid shortfall is
        # captured as penalty_days and subtracted below — matching the old code.
        present_days = total_days

    base_present_days = present_days  # paid days before penalties applied
    present_days -= penalty_days
    attendance_percentage = present_days / total_days * 100

    return AttendanceSummary(
        total_days=total_days,
        raw_present_days=raw_present,
        paid_sundays=paid_sundays,
        base_present_days=base_present_days,
        penalty_days=penalty_days,
        leave_used=leave_used,
        present_days=present_days,
        attendance_percentage=attendance_percentage,
        total_overtime_hours=total_overtime_hours,
        refreshment_days=refreshment_days,
        new_leave_balance=new_leave_balance,
        applied_rules=applied_rules,
    )


# --------------------------------------------------------------------------- #
# Salary
# --------------------------------------------------------------------------- #
def _qualifies_for_bonus(
    *, dept: str, emp_name: str, attendance_fraction: float, rules: dict
) -> bool:
    cfg = rules["bonus"]
    if dept not in cfg.get("departments", []):
        return False
    if emp_name in cfg.get("excluded_employees", []):
        return False
    if cfg.get("require_full_attendance", True) and attendance_fraction < 1.0:
        return False
    return True


def compute_salary(
    *,
    base: float,
    attendance_fraction: float,
    overtime_hours: float,
    refreshment_days: int,
    pf: float,
    esi: float,
    adjusted_advance: float,
    period_days: int,
    dept: str,
    emp_name: str,
    rules: dict,
) -> SalaryBreakdown:
    """Compute net salary for one employee for one pay period.

    Mirrors the old formula (``calculateSalary.py`` / ``calculateSalaryTable``):

        base_att      = base * attendance_fraction
        overtime_pay  = (overtime_hours / hours_per_workday) / period_days * base
        refresh_pay   = refreshment_days * refreshment_rate
        gross         = base_att + overtime_pay + refresh_pay
        total         = round(gross + bonus - pf - esi - adjusted_advance)

    `attendance_fraction` is 0..1 (i.e. attendance_percentage / 100).
    """
    ot_cfg = rules["overtime"]
    refresh_cfg = rules["refreshment"]
    bonus_cfg = rules["bonus"]

    hours_per_day = float(ot_cfg.get("hours_per_workday", 8))
    refresh_rate = float(refresh_cfg.get("rate_per_day", 8))

    base_att = base * attendance_fraction
    overtime_pay = (overtime_hours / hours_per_day) / period_days * base
    refreshment_pay = refreshment_days * refresh_rate
    gross = base_att + overtime_pay + refreshment_pay

    if dept not in bonus_cfg.get("departments", []):
        bonus_status = "NA"
        bonus = 0.0
    elif _qualifies_for_bonus(
        dept=dept,
        emp_name=emp_name,
        attendance_fraction=attendance_fraction,
        rules=rules,
    ):
        bonus = float(bonus_cfg.get("amount", 0))
        bonus_status = "Y"
    else:
        bonus = 0.0
        bonus_status = "N"

    total = round(gross + bonus - pf - esi - adjusted_advance)

    return SalaryBreakdown(
        base=base,
        attendance_fraction=attendance_fraction,
        base_att=base_att,
        overtime_hours=overtime_hours,
        overtime_pay=overtime_pay,
        refreshment_days=refreshment_days,
        refreshment_pay=refreshment_pay,
        gross=gross,
        pf=pf,
        esi=esi,
        adjusted_advance=adjusted_advance,
        bonus=bonus,
        bonus_status=bonus_status,
        total=int(total),
    )
