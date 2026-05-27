"""
Tests for services/recurrence.py — projection generation and refresh.

Day-of-week convention: stored as Sun=0, Mon=1, ..., Sat=6.
Python's weekday(): Mon=0, Tue=1, ..., Sun=6.
Conversion in code: python_weekday = (stored - 1) % 7
"""
import pytest
from datetime import date, timedelta

from app.models.task import Task
from app.models.recurrence import Recurrence, Projection
from app.services.recurrence import generate_projections, refresh_projections


def make_recurrence(**kwargs) -> Recurrence:
    """Build an unsaved Recurrence with sensible defaults."""
    defaults = dict(
        task_id="task-1",
        interval_type="daily",
        interval_multiple=1,
        day_of_week=None,
        day_of_month=None,
        month_of_year=None,
        start_date=date(2026, 1, 1),
        end_date=None,
    )
    defaults.update(kwargs)
    r = Recurrence()
    for k, v in defaults.items():
        setattr(r, k, v)
    return r


# ---------------------------------------------------------------------------
# Daily recurrence
# ---------------------------------------------------------------------------

class TestGenerateProjectionsDaily:
    def test_every_day_generates_all_days_in_range(self):
        r = make_recurrence()
        projections = generate_projections(None, r, date(2026, 1, 1), date(2026, 1, 7))
        assert len(projections) == 7
        dates = {p.due_date for p in projections}
        for i in range(7):
            assert date(2026, 1, 1) + timedelta(days=i) in dates

    def test_every_2_days_skips_odd_days(self):
        r = make_recurrence(interval_multiple=2)
        projections = generate_projections(None, r, date(2026, 1, 1), date(2026, 1, 10))
        dates = {p.due_date for p in projections}
        assert len(projections) == 5  # 1, 3, 5, 7, 9
        assert date(2026, 1, 1) in dates
        assert date(2026, 1, 3) in dates
        assert date(2026, 1, 2) not in dates
        assert date(2026, 1, 4) not in dates

    def test_every_3_days(self):
        r = make_recurrence(interval_multiple=3)
        projections = generate_projections(None, r, date(2026, 1, 1), date(2026, 1, 10))
        dates = {p.due_date for p in projections}
        assert date(2026, 1, 1) in dates
        assert date(2026, 1, 4) in dates
        assert date(2026, 1, 7) in dates
        assert date(2026, 1, 10) in dates
        assert date(2026, 1, 2) not in dates
        assert len(projections) == 4

    def test_task_id_propagated_to_all_projections(self):
        r = make_recurrence(task_id="abc-123")
        projections = generate_projections(None, r, date(2026, 1, 1), date(2026, 1, 3))
        assert all(p.task_id == "abc-123" for p in projections)

    def test_single_day_range(self):
        r = make_recurrence()
        projections = generate_projections(None, r, date(2026, 6, 15), date(2026, 6, 15))
        assert len(projections) == 1
        assert projections[0].due_date == date(2026, 6, 15)

    def test_end_before_start_returns_empty(self):
        r = make_recurrence()
        projections = generate_projections(None, r, date(2026, 1, 10), date(2026, 1, 5))
        assert projections == []


# ---------------------------------------------------------------------------
# Weekly recurrence
# ---------------------------------------------------------------------------

class TestGenerateProjectionsWeekly:
    def test_monday_only(self):
        # Mon=1 in Sun=0 convention → python weekday 0
        # January 2026: Mondays are 5, 12, 19, 26
        r = make_recurrence(interval_type="weekly", day_of_week="1")
        projections = generate_projections(None, r, date(2026, 1, 1), date(2026, 1, 31))
        dates = {p.due_date for p in projections}
        assert dates == {date(2026, 1, 5), date(2026, 1, 12), date(2026, 1, 19), date(2026, 1, 26)}

    def test_sunday_only(self):
        # Sun=0 in convention → python weekday 6
        # January 2026: Sundays are 4, 11, 18, 25
        r = make_recurrence(interval_type="weekly", day_of_week="0")
        projections = generate_projections(None, r, date(2026, 1, 1), date(2026, 1, 31))
        dates = {p.due_date for p in projections}
        assert dates == {date(2026, 1, 4), date(2026, 1, 11), date(2026, 1, 18), date(2026, 1, 25)}

    def test_monday_and_wednesday(self):
        # Mon=1, Wed=3 → python 0, 2
        r = make_recurrence(interval_type="weekly", day_of_week="1,3")
        projections = generate_projections(None, r, date(2026, 1, 5), date(2026, 1, 11))
        dates = {p.due_date for p in projections}
        assert date(2026, 1, 5) in dates   # Monday
        assert date(2026, 1, 7) in dates   # Wednesday
        assert date(2026, 1, 6) not in dates  # Tuesday
        assert date(2026, 1, 8) not in dates  # Thursday

    def test_default_uses_start_date_weekday_weekly(self):
        # No day_of_week → repeat on same weekday as start_date
        # Jan 5, 2026 is a Monday
        r = make_recurrence(
            interval_type="weekly",
            day_of_week=None,
            start_date=date(2026, 1, 5),
        )
        projections = generate_projections(None, r, date(2026, 1, 5), date(2026, 1, 31))
        dates = {p.due_date for p in projections}
        assert dates == {date(2026, 1, 5), date(2026, 1, 12), date(2026, 1, 19), date(2026, 1, 26)}

    def test_biweekly_default(self):
        # Every 2 weeks from Jan 5 (Monday), no day_of_week
        r = make_recurrence(
            interval_type="weekly",
            interval_multiple=2,
            day_of_week=None,
            start_date=date(2026, 1, 5),
        )
        projections = generate_projections(None, r, date(2026, 1, 5), date(2026, 1, 31))
        dates = {p.due_date for p in projections}
        assert date(2026, 1, 5) in dates
        assert date(2026, 1, 12) not in dates  # week 1 — skipped
        assert date(2026, 1, 19) in dates
        assert date(2026, 1, 26) not in dates  # week 3 — skipped


# ---------------------------------------------------------------------------
# Monthly recurrence
# ---------------------------------------------------------------------------

class TestGenerateProjectionsMonthly:
    def test_specific_day_of_month(self):
        r = make_recurrence(interval_type="monthly", day_of_month="15")
        projections = generate_projections(None, r, date(2026, 1, 1), date(2026, 3, 31))
        dates = {p.due_date for p in projections}
        assert dates == {date(2026, 1, 15), date(2026, 2, 15), date(2026, 3, 15)}

    def test_multiple_days_of_month(self):
        r = make_recurrence(interval_type="monthly", day_of_month="1,15")
        projections = generate_projections(None, r, date(2026, 1, 1), date(2026, 1, 31))
        dates = {p.due_date for p in projections}
        assert dates == {date(2026, 1, 1), date(2026, 1, 15)}

    def test_default_monthly_uses_start_day(self):
        # No day_of_month → use start_date's day
        r = make_recurrence(
            interval_type="monthly",
            day_of_month=None,
            start_date=date(2026, 1, 20),
        )
        projections = generate_projections(None, r, date(2026, 1, 1), date(2026, 3, 31))
        dates = {p.due_date for p in projections}
        assert dates == {date(2026, 1, 20), date(2026, 2, 20), date(2026, 3, 20)}


# ---------------------------------------------------------------------------
# Yearly recurrence
# ---------------------------------------------------------------------------

class TestGenerateProjectionsYearly:
    def test_specific_month_and_day(self):
        r = make_recurrence(
            interval_type="yearly",
            month_of_year="3",
            day_of_month="15",
        )
        projections = generate_projections(None, r, date(2026, 1, 1), date(2028, 12, 31))
        dates = {p.due_date for p in projections}
        assert dates == {date(2026, 3, 15), date(2027, 3, 15), date(2028, 3, 15)}

    def test_default_yearly_uses_start_date(self):
        r = make_recurrence(
            interval_type="yearly",
            month_of_year=None,
            day_of_month=None,
            start_date=date(2026, 6, 21),
        )
        projections = generate_projections(None, r, date(2026, 1, 1), date(2028, 12, 31))
        dates = {p.due_date for p in projections}
        assert dates == {date(2026, 6, 21), date(2027, 6, 21), date(2028, 6, 21)}


# ---------------------------------------------------------------------------
# refresh_projections (requires real DB)
# ---------------------------------------------------------------------------

class TestRefreshProjections:
    def test_generates_future_projections_for_daily_task(self, db):
        task = Task(type="recurring", title="Daily task", importance=2, status="pending")
        db.add(task)
        db.commit()
        db.refresh(task)

        recurrence = Recurrence(
            task_id=task.id,
            interval_type="daily",
            interval_multiple=1,
            start_date=date.today(),
        )
        db.add(recurrence)
        db.commit()

        refresh_projections(db, months_ahead=1)

        projections = db.query(Projection).filter(Projection.task_id == task.id).all()
        assert len(projections) >= 28
        assert all(p.due_date >= date.today() for p in projections)

    def test_expired_recurrence_generates_no_projections(self, db):
        task = Task(type="recurring", title="Old task", importance=1, status="pending")
        db.add(task)
        db.commit()
        db.refresh(task)

        recurrence = Recurrence(
            task_id=task.id,
            interval_type="daily",
            interval_multiple=1,
            start_date=date(2020, 1, 1),
            end_date=date(2020, 12, 31),
        )
        db.add(recurrence)
        db.commit()

        refresh_projections(db, months_ahead=3)

        projections = db.query(Projection).filter(Projection.task_id == task.id).all()
        assert len(projections) == 0

    def test_running_twice_creates_no_duplicates(self, db):
        task = Task(type="recurring", title="Task", importance=2, status="pending")
        db.add(task)
        db.commit()
        db.refresh(task)

        recurrence = Recurrence(
            task_id=task.id,
            interval_type="daily",
            interval_multiple=1,
            start_date=date.today(),
        )
        db.add(recurrence)
        db.commit()

        refresh_projections(db, months_ahead=1)
        count_first = db.query(Projection).filter(Projection.task_id == task.id).count()

        refresh_projections(db, months_ahead=1)
        count_second = db.query(Projection).filter(Projection.task_id == task.id).count()

        assert count_first == count_second
