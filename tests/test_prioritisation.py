import pytest
from datetime import datetime, date, time, timedelta
from unittest.mock import MagicMock, patch
from sqlalchemy.orm import Session

FUTURE_DATE = date(2099, 1, 15)

from app.models.task import Task, CompletedTask
from app.models.recurrence import Recurrence, Projection
from app.config import settings
from app.services.prioritisation import (
    get_fixed_tasks,
    get_flexible_tasks,
    get_prioritised_tasks_with_metadata,
    populate_workout_exercises,
    calculate_urgency_for_deadline,
    calculate_priority_score,
    get_recurrence_timescale,
    get_fixed_tasks,
    get_flexible_tasks,
    calculate_gaps,
    find_fitting_gap,
    split_gap,
    schedule_tasks_into_timeline,
    bin_tasks_by_priority,
    effective_urgency_for_vrt,
    compute_errand_backlog_boosts,
    effective_urgency_for_recurring,
    effective_urgency_for_errand,
    effective_urgency_for_deadline,
    effective_urgency_for_appointment,
    PrioritisedTask,
    RecurrenceTimescale,
    TimeSlot,
)
from app.services.scheduling import get_remaining_capacity, add_minutes_to_time, determine_window


class TestCalculateUrgencyForDeadline:
    def test_no_deadline_returns_2(self):
        task = MagicMock(spec=Task)
        task.deadline_at = None
        task.estimated_duration = 60
        
        assert calculate_urgency_for_deadline(task) == 2
    
    def test_no_duration_returns_2(self):
        task = MagicMock(spec=Task)
        task.deadline_at = datetime.now() + timedelta(days=3)
        task.estimated_duration = None
        
        assert calculate_urgency_for_deadline(task) == 2
    
    def test_deadline_within_hour_returns_3(self):
        task = MagicMock(spec=Task)
        task.deadline_at = datetime.now() + timedelta(minutes=30)
        task.estimated_duration = 60
        
        assert calculate_urgency_for_deadline(task) == 3
    
    def test_buffer_over_7_days_returns_1(self):
        task = MagicMock(spec=Task)
        task.deadline_at = datetime.now() + timedelta(days=10)
        task.estimated_duration = 60
        
        assert calculate_urgency_for_deadline(task) == 1
    
    def test_buffer_2_to_7_days_returns_2(self):
        task = MagicMock(spec=Task)
        task.deadline_at = datetime.now() + timedelta(days=5)
        task.estimated_duration = 120
        
        assert calculate_urgency_for_deadline(task) == 2
    
    def test_buffer_under_2_days_returns_3(self):
        task = MagicMock(spec=Task)
        task.deadline_at = datetime.now() + timedelta(days=1)
        task.estimated_duration = 60
        
        assert calculate_urgency_for_deadline(task) == 3
    
    def test_large_duration_increases_urgency(self):
        task = MagicMock(spec=Task)
        task.deadline_at = datetime.now() + timedelta(days=10)
        task.estimated_duration = 60 * 50
        
        assert calculate_urgency_for_deadline(task, available_hours_per_day=6) == 3


class TestCalculatePriorityScore:
    def test_max_score(self):
        assert calculate_priority_score(3, 3) == 9
    
    def test_min_score(self):
        assert calculate_priority_score(1, 1) == 1
    
    def test_mixed_scores(self):
        assert calculate_priority_score(3, 2) == 6
        assert calculate_priority_score(2, 2) == 4
        assert calculate_priority_score(3, 1) == 3
        assert calculate_priority_score(1, 2) == 2
    
    def test_none_treated_as_1(self):
        assert calculate_priority_score(None, 3) == 3
        assert calculate_priority_score(2, None) == 2


class TestGetRecurrenceTimescale:
    def test_non_recurring_task(self):
        db = MagicMock(spec=Session)
        task = MagicMock(spec=Task)
        task.type = "errand"
        
        assert get_recurrence_timescale(db, task) == RecurrenceTimescale.NONE
    
    def test_recurring_task_daily(self):
        db = MagicMock(spec=Session)
        task = MagicMock(spec=Task)
        task.type = "recurring"
        task.id = "test-id"
        
        recurrence = MagicMock(spec=Recurrence)
        recurrence.interval_type = "daily"
        db.query.return_value.filter.return_value.first.return_value = recurrence
        
        assert get_recurrence_timescale(db, task) == RecurrenceTimescale.DAILY
    
    def test_recurring_task_weekly(self):
        db = MagicMock(spec=Session)
        task = MagicMock(spec=Task)
        task.type = "recurring"
        task.id = "test-id"
        
        recurrence = MagicMock(spec=Recurrence)
        recurrence.interval_type = "weekly"
        db.query.return_value.filter.return_value.first.return_value = recurrence
        
        assert get_recurrence_timescale(db, task) == RecurrenceTimescale.WEEKLY
    
    def test_recurring_task_monthly(self):
        db = MagicMock(spec=Session)
        task = MagicMock(spec=Task)
        task.type = "recurring"
        task.id = "test-id"
        
        recurrence = MagicMock(spec=Recurrence)
        recurrence.interval_type = "monthly"
        db.query.return_value.filter.return_value.first.return_value = recurrence
        
        assert get_recurrence_timescale(db, task) == RecurrenceTimescale.MONTHLY


class TestTimeSlot:
    def test_duration_minutes(self):
        slot = TimeSlot(
            start=datetime(2026, 2, 5, 9, 0),
            end=datetime(2026, 2, 5, 11, 30),
        )
        assert slot.duration_minutes == 150


class TestCalculateGaps:
    def _make_fixed_pt(self, start_hour: int, duration: int = 60) -> PrioritisedTask:
        task = MagicMock(spec=Task)
        task.estimated_duration = duration
        task.prep_duration = 0
        return PrioritisedTask(
            task=task,
            priority_score=9,
            calculated_urgency=3,
            recurrence_timescale=RecurrenceTimescale.NONE,
            is_fixed=True,
            scheduled_time=datetime(FUTURE_DATE.year, FUTURE_DATE.month, FUTURE_DATE.day, start_hour, 0),
        )
    
    def test_no_fixed_tasks_full_day_available(self):
        gaps = calculate_gaps([], FUTURE_DATE, include_afternoon=True)
        
        assert len(gaps) == 1
        assert gaps[0].start == datetime(FUTURE_DATE.year, FUTURE_DATE.month, FUTURE_DATE.day, 9, 0)
        assert gaps[0].end == datetime(FUTURE_DATE.year, FUTURE_DATE.month, FUTURE_DATE.day, 18, 0)
    
    def test_no_fixed_tasks_main_only(self):
        gaps = calculate_gaps([], FUTURE_DATE, include_afternoon=False)
        
        assert len(gaps) == 1
        assert gaps[0].start == datetime(FUTURE_DATE.year, FUTURE_DATE.month, FUTURE_DATE.day, 9, 0)
        assert gaps[0].end == datetime(FUTURE_DATE.year, FUTURE_DATE.month, FUTURE_DATE.day, 15, 0)
    
    def test_single_fixed_task_creates_two_gaps(self):
        fixed = [self._make_fixed_pt(11, 60)]
        gaps = calculate_gaps(fixed, FUTURE_DATE, include_afternoon=True)
        
        assert len(gaps) == 2
        assert gaps[0].start == datetime(FUTURE_DATE.year, FUTURE_DATE.month, FUTURE_DATE.day, 9, 0)
        assert gaps[0].end == datetime(FUTURE_DATE.year, FUTURE_DATE.month, FUTURE_DATE.day, 11, 0)
        assert gaps[1].start == datetime(FUTURE_DATE.year, FUTURE_DATE.month, FUTURE_DATE.day, 12, 0)
        assert gaps[1].end == datetime(FUTURE_DATE.year, FUTURE_DATE.month, FUTURE_DATE.day, 18, 0)
    
    def test_adjacent_fixed_tasks_single_gap(self):
        fixed = [
            self._make_fixed_pt(9, 60),
            self._make_fixed_pt(10, 60),
        ]
        gaps = calculate_gaps(fixed, FUTURE_DATE, include_afternoon=True)
        
        assert len(gaps) == 1
        assert gaps[0].start == datetime(FUTURE_DATE.year, FUTURE_DATE.month, FUTURE_DATE.day, 11, 0)


class TestFindFittingGap:
    def test_finds_first_fitting_gap(self):
        task = MagicMock(spec=Task)
        task.estimated_duration = 60
        task.prep_duration = 0
        task.allow_afternoon = False
        
        gaps = [
            TimeSlot(datetime(FUTURE_DATE.year, FUTURE_DATE.month, FUTURE_DATE.day, 9, 0), datetime(FUTURE_DATE.year, FUTURE_DATE.month, FUTURE_DATE.day, 9, 30)),
            TimeSlot(datetime(FUTURE_DATE.year, FUTURE_DATE.month, FUTURE_DATE.day, 11, 0), datetime(FUTURE_DATE.year, FUTURE_DATE.month, FUTURE_DATE.day, 13, 0)),
        ]
        
        gap, idx = find_fitting_gap(task, gaps, FUTURE_DATE)
        
        assert gap == gaps[1]
        assert idx == 1
    
    def test_respects_allow_afternoon_false(self):
        task = MagicMock(spec=Task)
        task.estimated_duration = 60
        task.prep_duration = 0
        task.allow_afternoon = False
        
        gaps = [
            TimeSlot(datetime(FUTURE_DATE.year, FUTURE_DATE.month, FUTURE_DATE.day, 16, 0), datetime(FUTURE_DATE.year, FUTURE_DATE.month, FUTURE_DATE.day, 18, 0)),
        ]
        
        gap, idx = find_fitting_gap(task, gaps, FUTURE_DATE)
        
        assert gap is None
        assert idx == -1
    
    def test_allows_afternoon_when_flag_true(self):
        task = MagicMock(spec=Task)
        task.estimated_duration = 60
        task.prep_duration = 0
        task.allow_afternoon = True
        
        gaps = [
            TimeSlot(datetime(FUTURE_DATE.year, FUTURE_DATE.month, FUTURE_DATE.day, 16, 0), datetime(FUTURE_DATE.year, FUTURE_DATE.month, FUTURE_DATE.day, 18, 0)),
        ]
        
        gap, idx = find_fitting_gap(task, gaps, FUTURE_DATE)
        
        assert gap == gaps[0]


class TestSplitGap:
    def test_splits_gap_with_remaining_time(self):
        task = MagicMock(spec=Task)
        task.estimated_duration = 60
        task.prep_duration = 0
        
        gap = TimeSlot(datetime(FUTURE_DATE.year, FUTURE_DATE.month, FUTURE_DATE.day, 9, 0), datetime(FUTURE_DATE.year, FUTURE_DATE.month, FUTURE_DATE.day, 12, 0))
        remaining = split_gap(gap, task)
        
        assert len(remaining) == 1
        assert remaining[0].start == datetime(FUTURE_DATE.year, FUTURE_DATE.month, FUTURE_DATE.day, 10, 0)
        assert remaining[0].end == datetime(FUTURE_DATE.year, FUTURE_DATE.month, FUTURE_DATE.day, 12, 0)
    
    def test_no_remaining_when_exact_fit(self):
        task = MagicMock(spec=Task)
        task.estimated_duration = 60
        task.prep_duration = 0
        
        gap = TimeSlot(datetime(FUTURE_DATE.year, FUTURE_DATE.month, FUTURE_DATE.day, 9, 0), datetime(FUTURE_DATE.year, FUTURE_DATE.month, FUTURE_DATE.day, 10, 0))
        remaining = split_gap(gap, task)
        
        assert len(remaining) == 0


class TestScheduleTasksIntoTimeline:
    def _make_fixed_pt(self, start_hour: int, duration: int = 60, title: str = "Fixed") -> PrioritisedTask:
        task = MagicMock(spec=Task)
        task.title = title
        task.estimated_duration = duration
        task.prep_duration = 0
        return PrioritisedTask(
            task=task,
            priority_score=9,
            calculated_urgency=3,
            recurrence_timescale=RecurrenceTimescale.NONE,
            is_fixed=True,
            scheduled_time=datetime(FUTURE_DATE.year, FUTURE_DATE.month, FUTURE_DATE.day, start_hour, 0),
        )
    
    def _make_flexible_pt(
        self, 
        duration: int = 60, 
        score: int = 6, 
        allow_afternoon: bool = False,
        title: str = "Flexible"
    ) -> PrioritisedTask:
        task = MagicMock(spec=Task)
        task.title = title
        task.estimated_duration = duration
        task.prep_duration = 0
        task.allow_afternoon = allow_afternoon
        return PrioritisedTask(
            task=task,
            priority_score=score,
            calculated_urgency=2,
            recurrence_timescale=RecurrenceTimescale.NONE,
            is_fixed=False,
        )
    
    def test_fixed_tasks_keep_their_times(self):
        fixed = [self._make_fixed_pt(11)]
        flexible = []
        
        scheduled = schedule_tasks_into_timeline(fixed, flexible, FUTURE_DATE)
        
        assert len(scheduled) == 1
        assert scheduled[0].scheduled_time == datetime(FUTURE_DATE.year, FUTURE_DATE.month, FUTURE_DATE.day, 11, 0)
    
    def test_flexible_task_scheduled_in_first_gap(self):
        fixed = [self._make_fixed_pt(11)]
        flexible = [self._make_flexible_pt(60)]
        
        scheduled = schedule_tasks_into_timeline(fixed, flexible, FUTURE_DATE)
        
        assert len(scheduled) == 2
        assert scheduled[0].scheduled_time == datetime(FUTURE_DATE.year, FUTURE_DATE.month, FUTURE_DATE.day, 9, 0)
        assert scheduled[1].scheduled_time == datetime(FUTURE_DATE.year, FUTURE_DATE.month, FUTURE_DATE.day, 11, 0)
    
    def test_higher_priority_scheduled_first(self):
        fixed = []
        flexible = [
            self._make_flexible_pt(60, score=9, title="High"),
            self._make_flexible_pt(60, score=3, title="Low"),
        ]
        
        scheduled = schedule_tasks_into_timeline(fixed, flexible, FUTURE_DATE)
        
        assert scheduled[0].task.title == "High"
        assert scheduled[0].scheduled_time == datetime(FUTURE_DATE.year, FUTURE_DATE.month, FUTURE_DATE.day, 9, 0)
        assert scheduled[1].task.title == "Low"
        assert scheduled[1].scheduled_time == datetime(FUTURE_DATE.year, FUTURE_DATE.month, FUTURE_DATE.day, 10, 0)
    
    def test_task_too_large_not_scheduled(self):
        fixed = [
            self._make_fixed_pt(9, 120),
            self._make_fixed_pt(12, 180),
        ]
        flexible = [self._make_flexible_pt(90)]
        
        scheduled = schedule_tasks_into_timeline(fixed, flexible, FUTURE_DATE)
        
        assert len(scheduled) == 2


class TestCalculateGapsToday:
    def test_today_gaps_start_from_now(self):
        today = date.today()
        now = datetime.now()
        
        gaps = calculate_gaps([], today, include_afternoon=True)
        
        if now.hour < 18:
            assert len(gaps) >= 1
            assert gaps[0].start >= now or gaps[0].start.hour >= 9
    
    def test_today_no_gaps_if_day_ended(self):
        today = date.today()
        now = datetime.now()
        
        if now.hour >= 18:
            gaps = calculate_gaps([], today, include_afternoon=True)
            assert len(gaps) == 0


class TestBinTasksByPriority:
    def _make_pt(self, score: int, title: str = "Task") -> PrioritisedTask:
        task = MagicMock(spec=Task)
        task.title = title
        task.deferred_count = 0
        return PrioritisedTask(
            task=task,
            priority_score=score,
            calculated_urgency=1,
            recurrence_timescale=RecurrenceTimescale.NONE,
            is_fixed=False,
        )
    
    def test_bins_by_score(self):
        tasks = [
            self._make_pt(9, "A"),
            self._make_pt(6, "B"),
            self._make_pt(9, "C"),
            self._make_pt(3, "D"),
        ]
        
        bins = bin_tasks_by_priority(tasks)

        assert len(bins[9]) == 2
        assert len(bins[6]) == 1
        assert len(bins[3]) == 1


# ===========================================================================
# DB-backed tests — use the `db` fixture from conftest.py
# ===========================================================================

class TestGetFixedTasksDB:
    """get_fixed_tasks against a real in-memory database."""

    def test_appointment_on_target_date_is_fixed(self, db):
        task = Task(
            type="appointment",
            title="Doctor",
            scheduled_at=datetime(FUTURE_DATE.year, FUTURE_DATE.month, FUTURE_DATE.day, 10, 0),
            estimated_duration=60,
            importance=3,
            status="pending",
        )
        db.add(task)
        db.commit()

        result = get_fixed_tasks(db, FUTURE_DATE)
        assert len(result) == 1
        assert result[0].task.title == "Doctor"
        assert result[0].is_fixed is True
        assert result[0].scheduled_time == datetime(FUTURE_DATE.year, FUTURE_DATE.month, FUTURE_DATE.day, 10, 0)

    def test_appointment_on_different_date_excluded(self, db):
        task = Task(
            type="appointment",
            title="Other day",
            scheduled_at=datetime(2099, 2, 1, 10, 0),
            estimated_duration=30,
            importance=2,
            status="pending",
        )
        db.add(task)
        db.commit()

        result = get_fixed_tasks(db, FUTURE_DATE)
        assert len(result) == 0

    def test_recurring_with_scheduled_time_and_projection_is_fixed(self, db):
        task = Task(
            type="recurring",
            title="Standup",
            scheduled_time=time(9, 30),
            estimated_duration=15,
            importance=2,
            urgency=2,
            status="pending",
        )
        db.add(task)
        db.commit()
        db.refresh(task)

        proj = Projection(task_id=task.id, due_date=FUTURE_DATE)
        db.add(proj)
        db.commit()

        result = get_fixed_tasks(db, FUTURE_DATE)
        titles = [pt.task.title for pt in result]
        assert "Standup" in titles
        standup = next(pt for pt in result if pt.task.title == "Standup")
        assert standup.scheduled_time == datetime(FUTURE_DATE.year, FUTURE_DATE.month, FUTURE_DATE.day, 9, 30)

    def test_recurring_without_scheduled_time_not_in_fixed(self, db):
        task = Task(
            type="recurring",
            title="Walk",
            scheduled_time=None,
            estimated_duration=30,
            importance=2,
            urgency=1,
            status="pending",
        )
        db.add(task)
        db.commit()
        db.refresh(task)

        proj = Projection(task_id=task.id, due_date=FUTURE_DATE)
        db.add(proj)
        db.commit()

        result = get_fixed_tasks(db, FUTURE_DATE)
        titles = [pt.task.title for pt in result]
        assert "Walk" not in titles

    def test_fixed_tasks_sorted_by_start_time(self, db):
        t1 = Task(type="appointment", title="Late", scheduled_at=datetime(FUTURE_DATE.year, FUTURE_DATE.month, FUTURE_DATE.day, 14, 0), estimated_duration=30, importance=2, status="pending")
        t2 = Task(type="appointment", title="Early", scheduled_at=datetime(FUTURE_DATE.year, FUTURE_DATE.month, FUTURE_DATE.day, 9, 0), estimated_duration=30, importance=2, status="pending")
        db.add_all([t1, t2])
        db.commit()

        result = get_fixed_tasks(db, FUTURE_DATE)
        assert result[0].task.title == "Early"
        assert result[1].task.title == "Late"


class TestGetFlexibleTasksDB:
    """get_flexible_tasks against a real in-memory database."""

    def test_deadline_returned_as_flexible(self, db):
        task = Task(
            type="deadline",
            title="Submit report",
            deadline_at=datetime(2099, 3, 1, 17, 0),
            estimated_duration=120,
            importance=3,
            status="pending",
        )
        db.add(task)
        db.commit()

        result = get_flexible_tasks(db, FUTURE_DATE)
        titles = [pt.task.title for pt in result]
        assert "Submit report" in titles

    def test_errand_returned_as_flexible(self, db):
        task = Task(
            type="errand",
            title="Post letter",
            estimated_duration=15,
            importance=1,
            urgency=1,
            status="pending",
        )
        db.add(task)
        db.commit()

        result = get_flexible_tasks(db, FUTURE_DATE)
        titles = [pt.task.title for pt in result]
        assert "Post letter" in titles

    def test_recurring_without_scheduled_time_and_with_projection_is_flexible(self, db):
        task = Task(
            type="recurring",
            title="Evening read",
            scheduled_time=None,
            estimated_duration=30,
            importance=2,
            urgency=1,
            status="pending",
        )
        db.add(task)
        db.commit()
        db.refresh(task)

        proj = Projection(task_id=task.id, due_date=FUTURE_DATE)
        db.add(proj)
        db.commit()

        result = get_flexible_tasks(db, FUTURE_DATE)
        titles = [pt.task.title for pt in result]
        assert "Evening read" in titles

    def test_deadline_due_today_is_pinned(self, db):
        """A deadline whose time has passed today is still 'due today': included,
        flagged due_today, and given urgency 3 — not silently dropped."""
        task = Task(
            type="deadline",
            title="Due today task",
            deadline_at=datetime.now() - timedelta(hours=2),
            estimated_duration=60,
            importance=2,
            status="pending",
        )
        db.add(task)
        db.commit()
        db.refresh(task)
        task_id = task.id

        result = get_flexible_tasks(db, date.today())
        matching = [pt for pt in result if pt.task.id == task_id]
        assert len(matching) == 1
        assert matching[0].due_today is True
        assert matching[0].calculated_urgency == 3

    def test_deadline_from_previous_day_excluded(self, db):
        """A deadline whose due date has already passed (yesterday or earlier)
        is excluded from flexible tasks — it's handled by the auto-complete sweep."""
        task = Task(
            type="deadline",
            title="Overdue task",
            deadline_at=datetime.now() - timedelta(days=1),
            estimated_duration=60,
            importance=2,
            status="pending",
        )
        db.add(task)
        db.commit()
        db.refresh(task)
        task_id = task.id

        result = get_flexible_tasks(db, date.today())
        ids = [pt.task.id for pt in result]
        assert task_id not in ids

    def test_flexible_tasks_sorted_by_priority_descending(self, db):
        low = Task(type="errand", title="Low", estimated_duration=15, importance=1, urgency=1, status="pending")
        high = Task(type="errand", title="High", estimated_duration=15, importance=3, urgency=3, status="pending")
        db.add_all([low, high])
        db.commit()

        result = get_flexible_tasks(db, FUTURE_DATE)
        # Higher priority should come first
        titles = [pt.task.title for pt in result]
        assert titles.index("High") < titles.index("Low")


class TestDueTodayPinningDB:
    """Deadlines due today are pulled out of gap-scheduling and pinned to the
    front of the schedule, ahead of everything else."""

    def test_due_today_deadline_is_first_in_schedule(self, db):
        deadline = Task(
            type="deadline",
            title="Due today",
            deadline_at=datetime.now() - timedelta(hours=1),
            estimated_duration=60,
            importance=2,
            status="pending",
        )
        errand = Task(
            type="errand",
            title="Some errand",
            estimated_duration=15,
            importance=3,
            urgency=3,
            status="pending",
        )
        db.add_all([deadline, errand])
        db.commit()

        scheduled, _ = get_prioritised_tasks_with_metadata(db, date.today())
        assert scheduled[0].task.title == "Due today"
        assert scheduled[0].due_today is True

    def test_multiple_due_today_sorted_by_priority_among_themselves(self, db):
        low = Task(
            type="deadline",
            title="Low importance, due today",
            deadline_at=datetime.now() - timedelta(hours=1),
            estimated_duration=30,
            importance=1,
            status="pending",
        )
        high = Task(
            type="deadline",
            title="High importance, due today",
            deadline_at=datetime.now() - timedelta(hours=1),
            estimated_duration=30,
            importance=3,
            status="pending",
        )
        db.add_all([low, high])
        db.commit()

        scheduled, _ = get_prioritised_tasks_with_metadata(db, date.today())
        due_today_tasks = [pt for pt in scheduled if pt.due_today]
        assert len(due_today_tasks) == 2
        assert due_today_tasks[0].task.title == "High importance, due today"
        assert due_today_tasks[1].task.title == "Low importance, due today"


class TestPrepDurationInGaps:
    """prep_duration should be included when calculating how much time a fixed task blocks."""

    def _make_fixed_pt_with_prep(self, start_hour: int, duration: int, prep: int) -> PrioritisedTask:
        task = MagicMock(spec=Task)
        task.estimated_duration = duration
        task.prep_duration = prep
        return PrioritisedTask(
            task=task,
            priority_score=9,
            calculated_urgency=3,
            recurrence_timescale=RecurrenceTimescale.NONE,
            is_fixed=True,
            scheduled_time=datetime(FUTURE_DATE.year, FUTURE_DATE.month, FUTURE_DATE.day, start_hour, 0),
        )

    def test_prep_duration_extends_blocked_time(self):
        # Appointment at 11am, 30 min duration, 30 min prep
        # Blocks 11:00–12:00, leaving 9:00–11:00 and 12:00–18:00
        fixed = [self._make_fixed_pt_with_prep(11, 30, 30)]
        gaps = calculate_gaps(fixed, FUTURE_DATE, include_afternoon=True)

        assert gaps[0].start == datetime(FUTURE_DATE.year, FUTURE_DATE.month, FUTURE_DATE.day, 9, 0)
        assert gaps[0].end == datetime(FUTURE_DATE.year, FUTURE_DATE.month, FUTURE_DATE.day, 11, 0)
        assert gaps[1].start == datetime(FUTURE_DATE.year, FUTURE_DATE.month, FUTURE_DATE.day, 12, 0)

    def test_split_gap_accounts_for_prep_duration(self):
        task = MagicMock(spec=Task)
        task.estimated_duration = 30
        task.prep_duration = 30

        gap = TimeSlot(
            datetime(FUTURE_DATE.year, FUTURE_DATE.month, FUTURE_DATE.day, 9, 0),
            datetime(FUTURE_DATE.year, FUTURE_DATE.month, FUTURE_DATE.day, 12, 0),
        )
        remaining = split_gap(gap, task)

        assert len(remaining) == 1
        # 60 min total (30 + 30 prep) consumed, remaining starts at 10:00
        assert remaining[0].start == datetime(FUTURE_DATE.year, FUTURE_DATE.month, FUTURE_DATE.day, 10, 0)


class TestManualScheduledTimeOverride:
    """Tasks with manual_scheduled_time for target_date bypass the gap algorithm."""

    def _make_flexible_with_manual_time(self, manual_hour: int, manual_minute: int = 0) -> PrioritisedTask:
        task = MagicMock(spec=Task)
        task.estimated_duration = 60
        task.prep_duration = 0
        task.allow_afternoon = False
        manual_dt = datetime(FUTURE_DATE.year, FUTURE_DATE.month, FUTURE_DATE.day, manual_hour, manual_minute)
        task.manual_scheduled_time = manual_dt
        return PrioritisedTask(
            task=task,
            priority_score=6,
            calculated_urgency=2,
            recurrence_timescale=RecurrenceTimescale.NONE,
            is_fixed=False,
        )

    def _make_flexible_no_manual(self, duration: int = 60, score: int = 3) -> PrioritisedTask:
        task = MagicMock(spec=Task)
        task.estimated_duration = duration
        task.prep_duration = 0
        task.allow_afternoon = False
        task.manual_scheduled_time = None
        return PrioritisedTask(
            task=task,
            priority_score=score,
            calculated_urgency=1,
            recurrence_timescale=RecurrenceTimescale.NONE,
            is_fixed=False,
        )

    def test_task_with_manual_time_scheduled_at_that_time(self):
        manual = self._make_flexible_with_manual_time(13)
        scheduled = schedule_tasks_into_timeline([], [manual], FUTURE_DATE)

        manual_result = next(pt for pt in scheduled if pt.task is manual.task)
        assert manual_result.scheduled_time == datetime(FUTURE_DATE.year, FUTURE_DATE.month, FUTURE_DATE.day, 13, 0)

    def test_manual_task_blocks_time_from_auto_task(self):
        # Manual task at 9:00 (60 min). Auto task (60 min) should go to 10:00 or later.
        manual = self._make_flexible_with_manual_time(9)
        auto = self._make_flexible_no_manual(60, score=9)

        scheduled = schedule_tasks_into_timeline([], [manual, auto], FUTURE_DATE)

        auto_result = next(pt for pt in scheduled if pt.task is auto.task)
        assert auto_result.scheduled_time is not None
        assert auto_result.scheduled_time >= datetime(FUTURE_DATE.year, FUTURE_DATE.month, FUTURE_DATE.day, 10, 0)


class TestRecurrenceTimescaleTieBreaking:
    """Monthly > weekly > daily for tasks with the same priority score."""

    def _make_recurring_pt(self, timescale: RecurrenceTimescale, deferred: int = 0) -> PrioritisedTask:
        task = MagicMock(spec=Task)
        task.estimated_duration = 30
        task.prep_duration = 0
        task.allow_afternoon = False
        task.manual_scheduled_time = None
        task.deferred_count = deferred
        return PrioritisedTask(
            task=task,
            priority_score=6,
            calculated_urgency=2,
            recurrence_timescale=timescale,
            is_fixed=False,
        )

    def test_monthly_before_weekly_before_daily(self):
        daily = self._make_recurring_pt(RecurrenceTimescale.DAILY)
        weekly = self._make_recurring_pt(RecurrenceTimescale.WEEKLY)
        monthly = self._make_recurring_pt(RecurrenceTimescale.MONTHLY)

        # sort_key returns (priority_score, timescale.value, deferred_count)
        tasks = [daily, weekly, monthly]
        tasks.sort(key=lambda t: t.sort_key(), reverse=True)

        assert tasks[0].recurrence_timescale == RecurrenceTimescale.MONTHLY
        assert tasks[1].recurrence_timescale == RecurrenceTimescale.WEEKLY
        assert tasks[2].recurrence_timescale == RecurrenceTimescale.DAILY

    def test_higher_deferred_count_wins_within_same_timescale(self):
        not_deferred = self._make_recurring_pt(RecurrenceTimescale.WEEKLY, deferred=0)
        deferred = self._make_recurring_pt(RecurrenceTimescale.WEEKLY, deferred=3)

        tasks = [not_deferred, deferred]
        tasks.sort(key=lambda t: t.sort_key(), reverse=True)

        assert tasks[0] is deferred


# ===========================================================================
# Phase 1: Scheduling algorithm edge cases
# ===========================================================================

class TestCalculateGapsOverlapping:
    def _make_fixed_pt(self, start_hour: int, duration: int = 60) -> PrioritisedTask:
        task = MagicMock(spec=Task)
        task.estimated_duration = duration
        task.prep_duration = 0
        return PrioritisedTask(
            task=task,
            priority_score=9,
            calculated_urgency=3,
            recurrence_timescale=RecurrenceTimescale.NONE,
            is_fixed=True,
            scheduled_time=datetime(FUTURE_DATE.year, FUTURE_DATE.month, FUTURE_DATE.day, start_hour, 0),
        )

    def test_overlapping_fixed_tasks_handled_without_crash(self):
        fixed = [self._make_fixed_pt(11, 60), self._make_fixed_pt(11, 60)]
        gaps = calculate_gaps(fixed, FUTURE_DATE, include_afternoon=True)
        assert len(gaps) == 2
        assert gaps[0].start == datetime(FUTURE_DATE.year, FUTURE_DATE.month, FUTURE_DATE.day, 9, 0)
        assert gaps[0].end == datetime(FUTURE_DATE.year, FUTURE_DATE.month, FUTURE_DATE.day, 11, 0)
        assert gaps[1].start == datetime(FUTURE_DATE.year, FUTURE_DATE.month, FUTURE_DATE.day, 12, 0)
        assert gaps[1].end == datetime(FUTURE_DATE.year, FUTURE_DATE.month, FUTURE_DATE.day, 18, 0)

    def test_partially_overlapping_fixed_tasks(self):
        # 10:00 (90 min → ends 11:30) overlaps with 11:00 (60 min → ends 12:00)
        fixed = [self._make_fixed_pt(10, 90), self._make_fixed_pt(11, 60)]
        gaps = calculate_gaps(fixed, FUTURE_DATE, include_afternoon=True)
        assert len(gaps) == 2
        assert gaps[0].start == datetime(FUTURE_DATE.year, FUTURE_DATE.month, FUTURE_DATE.day, 9, 0)
        assert gaps[0].end == datetime(FUTURE_DATE.year, FUTURE_DATE.month, FUTURE_DATE.day, 10, 0)
        assert gaps[1].start == datetime(FUTURE_DATE.year, FUTURE_DATE.month, FUTURE_DATE.day, 12, 0)
        assert gaps[1].end == datetime(FUTURE_DATE.year, FUTURE_DATE.month, FUTURE_DATE.day, 18, 0)


class TestScheduleFullDay:
    def _make_fixed_pt(self, start_hour: int, duration: int) -> PrioritisedTask:
        task = MagicMock(spec=Task)
        task.estimated_duration = duration
        task.prep_duration = 0
        return PrioritisedTask(
            task=task,
            priority_score=9,
            calculated_urgency=3,
            recurrence_timescale=RecurrenceTimescale.NONE,
            is_fixed=True,
            scheduled_time=datetime(FUTURE_DATE.year, FUTURE_DATE.month, FUTURE_DATE.day, start_hour, 0),
        )

    def _make_flexible_pt(self, duration: int = 60, score: int = 6) -> PrioritisedTask:
        task = MagicMock(spec=Task)
        task.estimated_duration = duration
        task.prep_duration = 0
        task.allow_afternoon = False
        task.manual_scheduled_time = None
        return PrioritisedTask(
            task=task,
            priority_score=score,
            calculated_urgency=2,
            recurrence_timescale=RecurrenceTimescale.NONE,
            is_fixed=False,
        )

    def test_full_day_no_flexible_tasks_scheduled(self):
        # Fixed tasks cover entire 9:00–18:00
        fixed = [
            self._make_fixed_pt(9, 120),
            self._make_fixed_pt(11, 120),
            self._make_fixed_pt(13, 120),
            self._make_fixed_pt(15, 180),
        ]
        flex = self._make_flexible_pt(60)
        scheduled = schedule_tasks_into_timeline(fixed, [flex], FUTURE_DATE)

        assert len(scheduled) == 4
        assert not any(pt.task is flex.task for pt in scheduled)


class TestMultipleManualScheduled:
    def _make_manual(self, hour: int, minute: int = 0, duration: int = 60, score: int = 6) -> PrioritisedTask:
        task = MagicMock(spec=Task)
        task.estimated_duration = duration
        task.prep_duration = 0
        task.allow_afternoon = False
        task.manual_scheduled_time = datetime(
            FUTURE_DATE.year, FUTURE_DATE.month, FUTURE_DATE.day, hour, minute
        )
        return PrioritisedTask(
            task=task,
            priority_score=score,
            calculated_urgency=2,
            recurrence_timescale=RecurrenceTimescale.NONE,
            is_fixed=False,
        )

    def _make_auto(self, duration: int = 60, score: int = 3) -> PrioritisedTask:
        task = MagicMock(spec=Task)
        task.estimated_duration = duration
        task.prep_duration = 0
        task.allow_afternoon = False
        task.manual_scheduled_time = None
        return PrioritisedTask(
            task=task,
            priority_score=score,
            calculated_urgency=1,
            recurrence_timescale=RecurrenceTimescale.NONE,
            is_fixed=False,
        )

    def test_two_manual_scheduled_tasks_both_placed(self):
        manual_a = self._make_manual(10, score=9)
        manual_b = self._make_manual(13, score=8)
        auto_c = self._make_auto(60, score=3)

        scheduled = schedule_tasks_into_timeline([], [manual_a, manual_b, auto_c], FUTURE_DATE)

        assert len(scheduled) == 3

        a_result = next(pt for pt in scheduled if pt.task is manual_a.task)
        b_result = next(pt for pt in scheduled if pt.task is manual_b.task)
        assert a_result.scheduled_time == datetime(FUTURE_DATE.year, FUTURE_DATE.month, FUTURE_DATE.day, 10, 0)
        assert b_result.scheduled_time == datetime(FUTURE_DATE.year, FUTURE_DATE.month, FUTURE_DATE.day, 13, 0)

    def test_manual_task_overlapping_fixed_task_both_placed(self):
        fixed_task = MagicMock(spec=Task)
        fixed_task.estimated_duration = 60
        fixed_task.prep_duration = 0
        fixed = PrioritisedTask(
            task=fixed_task,
            priority_score=9,
            calculated_urgency=3,
            recurrence_timescale=RecurrenceTimescale.NONE,
            is_fixed=True,
            scheduled_time=datetime(FUTURE_DATE.year, FUTURE_DATE.month, FUTURE_DATE.day, 10, 0),
        )
        manual = self._make_manual(10, minute=30, score=6)

        scheduled = schedule_tasks_into_timeline([fixed], [manual], FUTURE_DATE)

        assert len(scheduled) == 2
        fixed_result = next(pt for pt in scheduled if pt.is_fixed)
        manual_result = next(pt for pt in scheduled if not pt.is_fixed)
        assert fixed_result.scheduled_time == datetime(FUTURE_DATE.year, FUTURE_DATE.month, FUTURE_DATE.day, 10, 0)
        assert manual_result.scheduled_time == datetime(FUTURE_DATE.year, FUTURE_DATE.month, FUTURE_DATE.day, 10, 30)


class TestTaskExactlyFillingGap:
    def _make_fixed_pt(self, start_hour: int, duration: int) -> PrioritisedTask:
        task = MagicMock(spec=Task)
        task.estimated_duration = duration
        task.prep_duration = 0
        return PrioritisedTask(
            task=task,
            priority_score=9,
            calculated_urgency=3,
            recurrence_timescale=RecurrenceTimescale.NONE,
            is_fixed=True,
            scheduled_time=datetime(FUTURE_DATE.year, FUTURE_DATE.month, FUTURE_DATE.day, start_hour, 0),
        )

    def test_exact_fit_leaves_no_remaining_gap(self):
        # Fixed at 9:00 (60 min) and 12:00 (360 min) → single gap 10:00-12:00 = 120 min
        fixed = [self._make_fixed_pt(9, 60), self._make_fixed_pt(12, 360)]
        flex_task = MagicMock(spec=Task)
        flex_task.estimated_duration = 120
        flex_task.prep_duration = 0
        flex_task.allow_afternoon = False
        flex_task.manual_scheduled_time = None
        flex = PrioritisedTask(
            task=flex_task,
            priority_score=6,
            calculated_urgency=2,
            recurrence_timescale=RecurrenceTimescale.NONE,
            is_fixed=False,
        )

        scheduled = schedule_tasks_into_timeline(fixed, [flex], FUTURE_DATE)

        assert len(scheduled) == 3
        flex_result = next(pt for pt in scheduled if not pt.is_fixed)
        assert flex_result.scheduled_time == datetime(FUTURE_DATE.year, FUTURE_DATE.month, FUTURE_DATE.day, 10, 0)


class TestMultipleGapsSomeTooSmall:
    def _make_fixed_pt(self, start_hour: int, start_minute: int, duration: int) -> PrioritisedTask:
        task = MagicMock(spec=Task)
        task.estimated_duration = duration
        task.prep_duration = 0
        return PrioritisedTask(
            task=task,
            priority_score=9,
            calculated_urgency=3,
            recurrence_timescale=RecurrenceTimescale.NONE,
            is_fixed=True,
            scheduled_time=datetime(
                FUTURE_DATE.year, FUTURE_DATE.month, FUTURE_DATE.day, start_hour, start_minute
            ),
        )

    def test_skips_small_gaps_uses_first_fitting(self):
        # Fixed tasks at 9:30 (30 min), 10:30 (30 min), 12:00 (60 min)
        # Gaps: [9:00-9:30, 10:00-10:30, 11:00-12:00, 13:00-18:00]
        fixed = [
            self._make_fixed_pt(9, 30, 30),
            self._make_fixed_pt(10, 30, 30),
            self._make_fixed_pt(12, 0, 60),
        ]
        flex_task = MagicMock(spec=Task)
        flex_task.estimated_duration = 60
        flex_task.prep_duration = 0
        flex_task.allow_afternoon = False
        flex_task.manual_scheduled_time = None
        flex = PrioritisedTask(
            task=flex_task,
            priority_score=6,
            calculated_urgency=2,
            recurrence_timescale=RecurrenceTimescale.NONE,
            is_fixed=False,
        )

        scheduled = schedule_tasks_into_timeline(fixed, [flex], FUTURE_DATE)

        flex_result = next(pt for pt in scheduled if not pt.is_fixed)
        assert flex_result.scheduled_time == datetime(FUTURE_DATE.year, FUTURE_DATE.month, FUTURE_DATE.day, 11, 0)


class TestPrepDurationInFlexibleScheduling:
    def test_flexible_task_with_prep_duration_consumes_extra_time(self):
        # Flex A: 30 min duration + 30 min prep = 60 min total, higher priority
        task_a = MagicMock(spec=Task)
        task_a.estimated_duration = 30
        task_a.prep_duration = 30
        task_a.allow_afternoon = False
        task_a.manual_scheduled_time = None
        flex_a = PrioritisedTask(
            task=task_a,
            priority_score=9,
            calculated_urgency=3,
            recurrence_timescale=RecurrenceTimescale.NONE,
            is_fixed=False,
        )

        # Flex B: 30 min duration, 0 prep, lower priority
        task_b = MagicMock(spec=Task)
        task_b.estimated_duration = 30
        task_b.prep_duration = 0
        task_b.allow_afternoon = False
        task_b.manual_scheduled_time = None
        flex_b = PrioritisedTask(
            task=task_b,
            priority_score=6,
            calculated_urgency=2,
            recurrence_timescale=RecurrenceTimescale.NONE,
            is_fixed=False,
        )

        scheduled = schedule_tasks_into_timeline([], [flex_a, flex_b], FUTURE_DATE)

        a_result = next(pt for pt in scheduled if pt.task is task_a)
        b_result = next(pt for pt in scheduled if pt.task is task_b)
        assert a_result.scheduled_time == datetime(FUTURE_DATE.year, FUTURE_DATE.month, FUTURE_DATE.day, 9, 0)
        # B starts at 10:00 (A consumed 60 min total), not 9:30
        assert b_result.scheduled_time == datetime(FUTURE_DATE.year, FUTURE_DATE.month, FUTURE_DATE.day, 10, 0)


class TestAllowAfternoonInteraction:
    def _make_fixed_covering_main_window(self) -> PrioritisedTask:
        task = MagicMock(spec=Task)
        task.estimated_duration = 360  # 9:00-15:00
        task.prep_duration = 0
        return PrioritisedTask(
            task=task,
            priority_score=9,
            calculated_urgency=3,
            recurrence_timescale=RecurrenceTimescale.NONE,
            is_fixed=True,
            scheduled_time=datetime(FUTURE_DATE.year, FUTURE_DATE.month, FUTURE_DATE.day, 9, 0),
        )

    def _make_flex(self, allow_afternoon: bool) -> PrioritisedTask:
        task = MagicMock(spec=Task)
        task.estimated_duration = 60
        task.prep_duration = 0
        task.allow_afternoon = allow_afternoon
        task.manual_scheduled_time = None
        return PrioritisedTask(
            task=task,
            priority_score=6,
            calculated_urgency=2,
            recurrence_timescale=RecurrenceTimescale.NONE,
            is_fixed=False,
        )

    def test_afternoon_only_gaps_task_not_allowed(self):
        fixed = [self._make_fixed_covering_main_window()]
        flex = self._make_flex(allow_afternoon=False)
        scheduled = schedule_tasks_into_timeline(fixed, [flex], FUTURE_DATE)

        # Only the fixed task; flex not scheduled (no main-window gap)
        assert len(scheduled) == 1
        assert not any(pt.task is flex.task for pt in scheduled)

    def test_afternoon_only_gaps_task_allowed(self):
        fixed = [self._make_fixed_covering_main_window()]
        flex = self._make_flex(allow_afternoon=True)
        scheduled = schedule_tasks_into_timeline(fixed, [flex], FUTURE_DATE)

        assert len(scheduled) == 2
        flex_result = next(pt for pt in scheduled if pt.task is flex.task)
        assert flex_result.scheduled_time == datetime(FUTURE_DATE.year, FUTURE_DATE.month, FUTURE_DATE.day, 15, 0)


class TestSchedulingHelpers:
    def test_add_minutes_to_time_basic(self):
        assert add_minutes_to_time(time(9, 0), 90) == time(10, 30)

    def test_add_minutes_to_time_crosses_hour(self):
        assert add_minutes_to_time(time(14, 45), 30) == time(15, 15)

    def test_determine_window_main(self):
        assert determine_window(datetime(2099, 1, 15, 10, 0)) == "main"

    def test_determine_window_afternoon(self):
        assert determine_window(datetime(2099, 1, 15, 16, 0)) == "afternoon"

    def test_determine_window_evening(self):
        assert determine_window(datetime(2099, 1, 15, 20, 0)) == "evening"

    def test_determine_window_outside_returns_main(self):
        assert determine_window(datetime(2099, 1, 15, 7, 0)) == "main"


# ===========================================================================
# Phase 3: DB-backed tests
# ===========================================================================

class TestSnoozeFilteringDB:
    def test_snoozed_errand_hidden_today(self, db):
        task = Task(
            type="errand",
            title="Snoozed",
            estimated_duration=15,
            importance=1,
            urgency=1,
            status="pending",
            snooze_until=str(date.today() + timedelta(days=1)),
        )
        db.add(task)
        db.commit()

        result = get_flexible_tasks(db, date.today())
        ids = [pt.task.id for pt in result]
        assert task.id not in ids

    def test_expired_snooze_appears_today(self, db):
        task = Task(
            type="errand",
            title="Expired Snooze",
            estimated_duration=15,
            importance=1,
            urgency=1,
            status="pending",
            snooze_until=str(date.today() - timedelta(days=1)),
        )
        db.add(task)
        db.commit()

        result = get_flexible_tasks(db, date.today())
        ids = [pt.task.id for pt in result]
        assert task.id in ids

    def test_snooze_until_today_appears(self, db):
        task = Task(
            type="errand",
            title="Snooze Today",
            estimated_duration=15,
            importance=1,
            urgency=1,
            status="pending",
            snooze_until=str(date.today()),
        )
        db.add(task)
        db.commit()

        result = get_flexible_tasks(db, date.today())
        ids = [pt.task.id for pt in result]
        assert task.id in ids


class TestGetRemainingCapacityDB:
    """
    Note: get_remaining_capacity assigns a gap to main if gap.start < afternoon_start (15:00).
    With no fixed tasks, calculate_gaps returns a single gap 9:00-18:00 whose start is 9:00 < 15:00,
    so all 540 minutes are assigned to main and afternoon == 0.
    """

    def test_no_fixed_tasks_full_capacity(self, db):
        result = get_remaining_capacity(db, FUTURE_DATE)
        assert result["main"] == 540
        assert result["afternoon"] == 0
        assert result["is_overbooked"] is False

    def test_with_fixed_tasks_reduced_capacity(self, db):
        task = Task(
            type="appointment",
            title="Meeting",
            scheduled_at=datetime(FUTURE_DATE.year, FUTURE_DATE.month, FUTURE_DATE.day, 10, 0),
            estimated_duration=60,
            importance=2,
            status="pending",
        )
        db.add(task)
        db.commit()

        result = get_remaining_capacity(db, FUTURE_DATE)
        # Gaps: [9:00-10:00 (60 min), 11:00-18:00 (420 min)]; both start < 15:00 → main=480, afternoon=0
        assert result["main"] == 480
        assert result["afternoon"] == 0
        assert result["is_overbooked"] is False

    def test_is_overbooked_when_full(self, db):
        appt1 = Task(
            type="appointment",
            title="Block Main",
            scheduled_at=datetime(FUTURE_DATE.year, FUTURE_DATE.month, FUTURE_DATE.day, 9, 0),
            estimated_duration=360,
            importance=3,
            status="pending",
        )
        appt2 = Task(
            type="appointment",
            title="Block Afternoon",
            scheduled_at=datetime(FUTURE_DATE.year, FUTURE_DATE.month, FUTURE_DATE.day, 15, 0),
            estimated_duration=180,
            importance=3,
            status="pending",
        )
        db.add_all([appt1, appt2])
        db.commit()

        result = get_remaining_capacity(db, FUTURE_DATE)
        assert result["main"] == 0
        assert result["afternoon"] == 0
        assert result["is_overbooked"] is True


# ===========================================================================
# Theme A, step 2 — component A2: VRT carry-forward + cadence-relative
# escalation. Per the test hazard noted in the design brief, these test
# urgency functions directly and list-presence via get_flexible_tasks —
# never get_prioritised_tasks / gap scheduling, which reads the real clock.
# ===========================================================================

class TestVrtCarryForwardDB:
    """get_flexible_tasks: a VRT projection with due_date <= target_date
    carries forward (stays visible until completed); plain recurring tasks
    keep exact-date matching and do NOT carry forward a missed instance."""

    def test_overdue_vrt_appears_in_flexible_for_today(self, db):
        task = Task(
            type="variable_recurring",
            title="Overdue VRT",
            estimated_duration=30,
            importance=2,
            urgency=1,
            status="pending",
        )
        db.add(task)
        db.commit()
        db.refresh(task)

        proj = Projection(task_id=task.id, due_date=date.today() - timedelta(days=5))
        db.add(proj)
        db.commit()

        result = get_flexible_tasks(db, date.today())
        ids = [pt.task.id for pt in result]
        assert task.id in ids

    def test_plain_recurring_with_past_projection_does_not_carry_forward(self, db):
        task = Task(
            type="recurring",
            title="Missed recurring",
            scheduled_time=None,
            estimated_duration=30,
            importance=2,
            urgency=1,
            status="pending",
        )
        db.add(task)
        db.commit()
        db.refresh(task)

        proj = Projection(task_id=task.id, due_date=date.today() - timedelta(days=5))
        db.add(proj)
        db.commit()

        result = get_flexible_tasks(db, date.today())
        ids = [pt.task.id for pt in result]
        assert task.id not in ids

    def test_vrt_on_target_date_still_appears(self, db):
        """Sanity check: carry-forward (<=) must not break the ordinary
        due-today case."""
        task = Task(
            type="variable_recurring",
            title="Due today VRT",
            estimated_duration=30,
            importance=2,
            urgency=1,
            status="pending",
        )
        db.add(task)
        db.commit()
        db.refresh(task)

        db.add(Projection(task_id=task.id, due_date=date.today()))
        db.commit()

        result = get_flexible_tasks(db, date.today())
        ids = [pt.task.id for pt in result]
        assert task.id in ids

    def test_vrt_with_multiple_past_projections_included_once_using_earliest(self, db):
        """Defensive: if a VRT somehow has multiple uncompleted past
        projections, get_flexible_tasks includes the task exactly once."""
        task = Task(
            type="variable_recurring",
            title="Straggler VRT",
            estimated_duration=30,
            importance=2,
            urgency=1,
            status="pending",
        )
        db.add(task)
        db.commit()
        db.refresh(task)

        db.add_all([
            Projection(task_id=task.id, due_date=date.today() - timedelta(days=10)),
            Projection(task_id=task.id, due_date=date.today() - timedelta(days=3)),
        ])
        db.commit()

        result = get_flexible_tasks(db, date.today())
        matching = [pt for pt in result if pt.task.id == task.id]
        assert len(matching) == 1

    def test_overdue_vrt_priority_score_reflects_escalated_urgency(self, db):
        task = Task(
            type="variable_recurring",
            title="Escalated VRT",
            estimated_duration=30,
            importance=2,
            urgency=1,
            status="pending",
        )
        db.add(task)
        db.commit()
        db.refresh(task)

        db.add(Recurrence(
            task_id=task.id, interval_type="weekly", interval_multiple=1,
            start_date=date(2026, 1, 1),
        ))
        due_date = date.today() - timedelta(days=5)
        db.add(Projection(task_id=task.id, due_date=due_date))
        db.commit()

        result = get_flexible_tasks(db, date.today())
        pt = next(p for p in result if p.task.id == task.id)
        # overdue_days=5, interval_days=7 (weekly) -> ratio ~0.71 >= 0.5 -> urgency 3
        assert pt.calculated_urgency == 3
        assert pt.priority_score == 2 * 3


class TestEffectiveUrgencyForVrt:
    """Direct tests of the escalation tiers (component A2)."""

    def _make_vrt(self, db, urgency: int = 1) -> Task:
        task = Task(
            type="variable_recurring",
            title="VRT",
            estimated_duration=30,
            importance=2,
            urgency=urgency,
            status="pending",
        )
        db.add(task)
        db.commit()
        db.refresh(task)
        return task

    def test_ratio_zero_keeps_base_urgency(self, db):
        task = self._make_vrt(db, urgency=1)
        projection = Projection(task_id=task.id, due_date=FUTURE_DATE)

        assert effective_urgency_for_vrt(db, task, projection, FUTURE_DATE) == 1

    def test_small_ratio_bumps_to_at_least_2(self, db):
        task = self._make_vrt(db, urgency=1)
        db.add(Recurrence(
            task_id=task.id, interval_type="weekly", interval_multiple=1,
            start_date=date(2026, 1, 1),
        ))
        db.commit()
        due_date = FUTURE_DATE - timedelta(days=2)
        projection = Projection(task_id=task.id, due_date=due_date)

        # interval_days=7, overdue_days=2, ratio ~0.286 < 0.5 -> max(1, 2) = 2
        assert effective_urgency_for_vrt(db, task, projection, FUTURE_DATE) == 2

    def test_ratio_past_threshold_gives_3(self, db):
        task = self._make_vrt(db, urgency=1)
        db.add(Recurrence(
            task_id=task.id, interval_type="weekly", interval_multiple=1,
            start_date=date(2026, 1, 1),
        ))
        db.commit()
        due_date = FUTURE_DATE - timedelta(days=5)
        projection = Projection(task_id=task.id, due_date=due_date)

        # interval_days=7, overdue_days=5, ratio ~0.714 >= 0.5 -> 3
        assert effective_urgency_for_vrt(db, task, projection, FUTURE_DATE) == 3

    def test_small_ratio_respects_higher_base_urgency(self, db):
        task = self._make_vrt(db, urgency=3)
        db.add(Recurrence(
            task_id=task.id, interval_type="weekly", interval_multiple=1,
            start_date=date(2026, 1, 1),
        ))
        db.commit()
        due_date = FUTURE_DATE - timedelta(days=2)
        projection = Projection(task_id=task.id, due_date=due_date)

        # base=3, max(base, 2) = 3
        assert effective_urgency_for_vrt(db, task, projection, FUTURE_DATE) == 3

    def test_ratio_exactly_at_threshold_gives_3(self, db):
        task = self._make_vrt(db, urgency=1)
        # Daily recurrence with interval_multiple=10 -> interval_days=10, so
        # an exact half-cadence (5 days) overdue lands ratio == 0.5 exactly.
        db.add(Recurrence(
            task_id=task.id, interval_type="daily", interval_multiple=10,
            start_date=date(2026, 1, 1),
        ))
        db.commit()
        interval_days = 10
        due_date = FUTURE_DATE - timedelta(days=int(interval_days * settings.vrt_escalation_half_ratio))
        projection = Projection(task_id=task.id, due_date=due_date)

        assert effective_urgency_for_vrt(db, task, projection, FUTURE_DATE) == 3


class TestVrtIntervalFallback:
    """interval_days: completion history > recurrence interval > flat 30."""

    def _make_vrt(self, db, urgency: int = 1) -> Task:
        task = Task(
            type="variable_recurring",
            title="VRT",
            estimated_duration=30,
            importance=2,
            urgency=urgency,
            status="pending",
        )
        db.add(task)
        db.commit()
        db.refresh(task)
        return task

    def test_no_completion_no_recurrence_uses_30_day_fallback_high_ratio(self, db):
        task = self._make_vrt(db)
        due_date = FUTURE_DATE - timedelta(days=20)
        projection = Projection(task_id=task.id, due_date=due_date)

        # interval_days=30 (final fallback), overdue=20, ratio ~0.667 >= 0.5 -> 3
        assert effective_urgency_for_vrt(db, task, projection, FUTURE_DATE) == 3

    def test_no_completion_no_recurrence_uses_30_day_fallback_low_ratio(self, db):
        task = self._make_vrt(db)
        due_date = FUTURE_DATE - timedelta(days=5)
        projection = Projection(task_id=task.id, due_date=due_date)

        # interval_days=30, overdue=5, ratio ~0.167 < 0.5 -> max(1, 2) = 2
        assert effective_urgency_for_vrt(db, task, projection, FUTURE_DATE) == 2

    def test_recurrence_interval_used_when_no_completion_row(self, db):
        task = self._make_vrt(db)
        db.add(Recurrence(
            task_id=task.id, interval_type="monthly", interval_multiple=1,
            start_date=date(2026, 1, 1),
        ))
        db.commit()
        due_date = FUTURE_DATE - timedelta(days=16)
        projection = Projection(task_id=task.id, due_date=due_date)

        # interval_days=30 (monthly), overdue=16, ratio ~0.533 >= 0.5 -> 3
        assert effective_urgency_for_vrt(db, task, projection, FUTURE_DATE) == 3

    def test_completion_history_interval_overrides_recurrence(self, db):
        task = self._make_vrt(db)
        db.add(Recurrence(
            task_id=task.id, interval_type="monthly", interval_multiple=1,
            start_date=date(2026, 1, 1),
        ))
        due_date = FUTURE_DATE - timedelta(days=3)
        completed_at = due_date - timedelta(days=7)  # actual last cadence: 7 days
        db.add(CompletedTask(
            task_id=task.id,
            completed_at=datetime.combine(completed_at, time(9, 0)),
            task_type="variable_recurring",
            task_title="VRT",
        ))
        db.commit()
        projection = Projection(task_id=task.id, due_date=due_date)

        # interval_days=7 (from completion, not the monthly recurrence),
        # overdue=3, ratio ~0.43 < 0.5 -> max(1, 2) = 2
        assert effective_urgency_for_vrt(db, task, projection, FUTURE_DATE) == 2

    def test_zero_interval_from_completion_falls_back_to_recurrence(self, db):
        """If completed_at == due_date (interval_days would be 0), the guard
        rejects it and falls back to the recurrence interval instead."""
        task = self._make_vrt(db)
        db.add(Recurrence(
            task_id=task.id, interval_type="weekly", interval_multiple=1,
            start_date=date(2026, 1, 1),
        ))
        due_date = FUTURE_DATE - timedelta(days=5)
        db.add(CompletedTask(
            task_id=task.id,
            completed_at=datetime.combine(due_date, time(9, 0)),
            task_type="variable_recurring",
            task_title="VRT",
        ))
        db.commit()
        projection = Projection(task_id=task.id, due_date=due_date)

        # interval_days falls back to 7 (weekly), overdue=5, ratio ~0.714 >= 0.5 -> 3
        assert effective_urgency_for_vrt(db, task, projection, FUTURE_DATE) == 3


# ===========================================================================
# Theme A, component A4 — errand auto-deadlines: buffer-based urgency,
# auto vs user-confirmed deadline semantics. Direct function tests plus
# get_flexible_tasks presence tests (never gap scheduling — see hazard note).
# ===========================================================================

class TestEffectiveUrgencyForErrand:
    def _make_errand(
        self,
        urgency: int | None = 1,
        deadline_at: datetime | None = None,
        deadline_auto: bool = False,
        estimated_duration: int = 30,
    ) -> Task:
        task = MagicMock(spec=Task)
        task.type = "errand"
        task.urgency = urgency
        task.deadline_at = deadline_at
        task.deadline_auto = deadline_auto
        task.estimated_duration = estimated_duration
        return task

    def test_no_deadline_falls_back_to_base_urgency(self):
        task = self._make_errand(urgency=2, deadline_at=None)
        assert effective_urgency_for_errand(task) == (2, False)

    def test_no_deadline_none_urgency_defaults_to_1(self):
        task = self._make_errand(urgency=None, deadline_at=None)
        assert effective_urgency_for_errand(task) == (1, False)

    def test_far_auto_deadline_gives_buffer_urgency_1(self):
        task = self._make_errand(
            urgency=1,
            deadline_at=datetime.now() + timedelta(days=300),
            deadline_auto=True,
        )
        assert effective_urgency_for_errand(task) == (1, False)

    def test_user_base_urgency_is_a_floor_over_buffer(self):
        """A hand-set urgency 3 must not be downgraded by a comfy deadline."""
        task = self._make_errand(
            urgency=3,
            deadline_at=datetime.now() + timedelta(days=300),
            deadline_auto=True,
        )
        assert effective_urgency_for_errand(task) == (3, False)

    def test_near_auto_deadline_raises_urgency(self):
        task = self._make_errand(
            urgency=1,
            deadline_at=datetime.now() + timedelta(days=1),
            deadline_auto=True,
        )
        urgency, due_today = effective_urgency_for_errand(task)
        assert urgency == 3
        assert due_today is False

    def test_expired_auto_deadline_escalates_to_3_but_never_pins(self):
        task = self._make_errand(
            urgency=1,
            deadline_at=datetime.now() - timedelta(days=10),
            deadline_auto=True,
        )
        assert effective_urgency_for_errand(task) == (3, False)

    def test_auto_deadline_today_is_not_pinned(self):
        """Even on its final day an auto-deadline errand is never due-today
        red — only user-confirmed deadlines get pinning."""
        task = self._make_errand(
            urgency=1,
            deadline_at=datetime.now() + timedelta(hours=2),
            deadline_auto=True,
        )
        urgency, due_today = effective_urgency_for_errand(task)
        assert due_today is False

    def test_user_confirmed_deadline_due_today_is_pinned(self):
        task = self._make_errand(
            urgency=1,
            deadline_at=datetime.now() + timedelta(hours=2),
            deadline_auto=False,
        )
        assert effective_urgency_for_errand(task) == (3, True)

    def test_user_confirmed_future_deadline_uses_buffer_maths(self):
        task = self._make_errand(
            urgency=1,
            deadline_at=datetime.now() + timedelta(days=5),
            estimated_duration=120,
            deadline_auto=False,
        )
        # Matches calculate_urgency_for_deadline's 2-7 day buffer band
        assert effective_urgency_for_errand(task) == (2, False)


class TestErrandDeadlineSemanticsDB:
    """get_flexible_tasks: auto vs user-confirmed errand deadline edges."""

    def _add_errand(self, db, title, deadline_at, deadline_auto, urgency=1):
        task = Task(
            type="errand",
            title=title,
            estimated_duration=15,
            importance=2,
            urgency=urgency,
            status="pending",
            deadline_at=deadline_at,
            deadline_auto=deadline_auto,
        )
        db.add(task)
        db.commit()
        db.refresh(task)
        return task

    def test_expired_auto_deadline_errand_stays_listed_at_urgency_3(self, db):
        task = self._add_errand(
            db, "Expired auto", datetime.now() - timedelta(days=5), deadline_auto=True
        )

        result = get_flexible_tasks(db, date.today())
        pt = next(p for p in result if p.task.id == task.id)
        assert pt.calculated_urgency == 3
        assert pt.due_today is False

    def test_overdue_user_confirmed_errand_excluded_for_sweep(self, db):
        task = self._add_errand(
            db, "Overdue confirmed", datetime.now() - timedelta(days=2), deadline_auto=False
        )

        result = get_flexible_tasks(db, date.today())
        ids = [p.task.id for p in result]
        assert task.id not in ids

    def test_user_confirmed_errand_due_today_is_pinned(self, db):
        task = self._add_errand(
            db, "Confirmed due today",
            datetime.combine(date.today(), time(23, 59)),
            deadline_auto=False,
        )

        result = get_flexible_tasks(db, date.today())
        pt = next(p for p in result if p.task.id == task.id)
        assert pt.due_today is True
        assert pt.calculated_urgency == 3

    def test_dateless_errand_still_listed_with_base_urgency(self, db):
        """Legacy row the migration hasn't touched: old behaviour intact."""
        task = self._add_errand(db, "Dateless", None, deadline_auto=False, urgency=2)

        result = get_flexible_tasks(db, date.today())
        pt = next(p for p in result if p.task.id == task.id)
        assert pt.calculated_urgency == 2
        assert pt.due_today is False


# ===========================================================================
# Theme A, component A3 — errand backlog boost: oldest errands get an
# urgency bump when the pending pile grows. Computed at list-build time,
# combines with A4's deadline urgency by max (never stacking), clamped at 3.
# ===========================================================================

class TestErrandBacklogBoosts:
    def _add_errands(self, db, count, snoozed_indices=()):
        """Create `count` pending errands, oldest first (staggered created_at)."""
        tasks = []
        base_created = datetime.now() - timedelta(days=count)
        for i in range(count):
            task = Task(
                type="errand",
                title=f"Errand {i}",
                estimated_duration=15,
                importance=1,
                urgency=1,
                status="pending",
                created_at=base_created + timedelta(days=i),
                snooze_until=(
                    str(date.today() + timedelta(days=2))
                    if i in snoozed_indices else None
                ),
            )
            db.add(task)
            tasks.append(task)
        db.commit()
        for t in tasks:
            db.refresh(t)
        return tasks

    def test_at_soft_threshold_no_boost(self, db):
        self._add_errands(db, 8)  # N == soft (8), not >
        assert compute_errand_backlog_boosts(db) == {}

    def test_above_soft_oldest_third_gets_plus_1(self, db):
        tasks = self._add_errands(db, 9)  # ceil(9/3) = 3
        boosts = compute_errand_backlog_boosts(db)

        assert {t.id for t in tasks[:3]} == set(boosts.keys())
        assert all(b == 1 for b in boosts.values())

    def test_above_hard_two_tiers(self, db):
        tasks = self._add_errands(db, 16)  # ceil(16/3) = 6
        boosts = compute_errand_backlog_boosts(db)

        assert all(boosts[t.id] == 2 for t in tasks[:6])
        assert all(boosts[t.id] == 1 for t in tasks[6:12])
        assert all(t.id not in boosts for t in tasks[12:])

    def test_snoozed_errands_count_toward_n_and_can_be_boosted(self, db):
        # 9 pending errands, the oldest snoozed: N is still 9 (> soft) and
        # the snoozed one is among the boosted oldest third.
        tasks = self._add_errands(db, 9, snoozed_indices={0})
        boosts = compute_errand_backlog_boosts(db)

        assert tasks[0].id in boosts

    def test_completed_errands_do_not_count(self, db):
        tasks = self._add_errands(db, 9)
        tasks[0].status = "completed"
        db.commit()

        # N drops to 8 == soft -> no boosts at all
        assert compute_errand_backlog_boosts(db) == {}


class TestBacklogBoostUrgencyCombination:
    def _make_errand(
        self,
        urgency: int | None = 1,
        deadline_at: datetime | None = None,
        deadline_auto: bool = False,
        estimated_duration: int = 30,
    ) -> Task:
        task = MagicMock(spec=Task)
        task.type = "errand"
        task.urgency = urgency
        task.deadline_at = deadline_at
        task.deadline_auto = deadline_auto
        task.estimated_duration = estimated_duration
        return task

    def test_boost_lifts_base_urgency(self):
        task = self._make_errand(urgency=1, deadline_at=None)
        assert effective_urgency_for_errand(task, backlog_boost=1) == (2, False)

    def test_boost_does_not_stack_with_buffer_urgency(self):
        """Deadline says 3, boost says base+2=3: max wins, no 4."""
        task = self._make_errand(
            urgency=1,
            deadline_at=datetime.now() + timedelta(days=1),
            deadline_auto=True,
        )
        assert effective_urgency_for_errand(task, backlog_boost=2) == (3, False)

    def test_boost_clamped_at_3(self):
        task = self._make_errand(urgency=2, deadline_at=None)
        assert effective_urgency_for_errand(task, backlog_boost=2) == (3, False)

    def test_max_of_buffer_and_boost_when_boost_wins(self):
        """Far deadline (buffer urgency 1), boost +1 on base 1 -> 2."""
        task = self._make_errand(
            urgency=1,
            deadline_at=datetime.now() + timedelta(days=300),
            deadline_auto=True,
        )
        assert effective_urgency_for_errand(task, backlog_boost=1) == (2, False)

    def test_due_today_pinning_unaffected_by_boost(self):
        task = self._make_errand(
            urgency=1,
            deadline_at=datetime.now() + timedelta(hours=2),
            deadline_auto=False,
        )
        assert effective_urgency_for_errand(task, backlog_boost=2) == (3, True)


class TestBacklogBoostInFlexibleListDB:
    def test_oldest_errand_scores_higher_when_backlog_exceeds_soft(self, db):
        base_created = datetime.now() - timedelta(days=20)
        tasks = []
        for i in range(9):
            task = Task(
                type="errand",
                title=f"Backlog errand {i}",
                estimated_duration=15,
                importance=1,
                urgency=1,
                status="pending",
                created_at=base_created + timedelta(days=i),
                # Far auto-deadline so A4's buffer urgency stays at 1 and
                # any change here is attributable to the boost alone.
                deadline_at=datetime.now() + timedelta(days=300),
                deadline_auto=True,
            )
            db.add(task)
            tasks.append(task)
        db.commit()
        for t in tasks:
            db.refresh(t)

        result = get_flexible_tasks(db, date.today())
        by_id = {pt.task.id: pt for pt in result}

        oldest = by_id[tasks[0].id]
        newest = by_id[tasks[-1].id]
        assert oldest.calculated_urgency == 2   # base 1 + boost 1
        assert oldest.priority_score == 2       # importance 1 x urgency 2
        assert newest.calculated_urgency == 1   # unboosted
        assert newest.priority_score == 1
