from datetime import datetime, date, time, timedelta
from enum import IntEnum
from dataclasses import dataclass, field
from sqlalchemy import or_
from sqlalchemy.orm import Session

from app.models.task import Task
from app.models.recurrence import Projection, Recurrence
from app.config import settings
from app.services.workout_algorithm import select_todays_exercises


class RecurrenceTimescale(IntEnum):
    YEARLY = 4
    MONTHLY = 3
    WEEKLY = 2
    DAILY = 1
    NONE = 0


@dataclass
class TimeSlot:
    start: datetime
    end: datetime
    
    @property
    def duration_minutes(self) -> int:
        return int((self.end - self.start).total_seconds() / 60)


@dataclass
class PrioritisedTask:
    task: Task
    priority_score: int
    calculated_urgency: int
    recurrence_timescale: RecurrenceTimescale
    is_fixed: bool
    scheduled_time: datetime | None = None
    selected_exercise: str | None = None  # For workout tasks: the exercise name selected by the algorithm
    due_today: bool = False  # Deadline whose due date is today: pinned to the top, styled as urgent

    def sort_key(self) -> tuple:
        return (
            1 if self.due_today else 0,
            self.priority_score,
            self.recurrence_timescale.value,
            self.task.deferred_count or 0,
        )


def calculate_urgency_for_deadline(task: Task, available_hours_per_day: int = 6) -> int:
    if not task.deadline_at or not task.estimated_duration:
        return 2
    
    now = datetime.now()
    time_remaining = task.deadline_at - now
    
    if time_remaining.total_seconds() <= 3600:
        return 3
    
    hours_needed = task.estimated_duration / 60
    days_of_work_needed = hours_needed / available_hours_per_day
    buffer_days = time_remaining.total_seconds() / 86400 - days_of_work_needed
    
    if buffer_days > settings.urgency_low_threshold:
        return 1
    elif buffer_days >= settings.urgency_medium_threshold:
        return 2
    else:
        return 3


def calculate_priority_score(importance: int, urgency: int) -> int:
    return (importance or 1) * (urgency or 1)


def get_recurrence_timescale(db: Session, task: Task) -> RecurrenceTimescale:
    if task.type not in ("recurring", "variable_recurring", "workout"):
        return RecurrenceTimescale.NONE
    
    recurrence = db.query(Recurrence).filter(Recurrence.task_id == task.id).first()
    if not recurrence:
        return RecurrenceTimescale.NONE
    
    timescale_map = {
        "yearly": RecurrenceTimescale.YEARLY,
        "monthly": RecurrenceTimescale.MONTHLY,
        "weekly": RecurrenceTimescale.WEEKLY,
        "daily": RecurrenceTimescale.DAILY,
    }
    return timescale_map.get(recurrence.interval_type, RecurrenceTimescale.NONE)


def get_fixed_tasks(db: Session, target_date: date) -> list[PrioritisedTask]:
    """
    Get all fixed (time-bound) tasks for the target date.
    
    Fixed tasks are:
    - Appointments with scheduled_at on target_date
    - Recurring tasks with scheduled_time (time-bound recurring)
    """
    fixed = []
    
    appointments = db.query(Task).filter(
        Task.type == "appointment",
        Task.status == "pending",
    ).all()
    
    for task in appointments:
        if not task.scheduled_at or task.scheduled_at.date() != target_date:
            continue
        fixed.append(PrioritisedTask(
            task=task,
            priority_score=calculate_priority_score(task.importance, 3),
            calculated_urgency=3,
            recurrence_timescale=RecurrenceTimescale.NONE,
            is_fixed=True,
            scheduled_time=task.scheduled_at,
        ))
    
    projections = db.query(Projection).filter(Projection.due_date == target_date).all()
    recurring_task_ids = [p.task_id for p in projections]
    
    if recurring_task_ids:
        recurring_tasks = db.query(Task).filter(
            Task.id.in_(recurring_task_ids),
            Task.status == "pending",
            Task.scheduled_time.isnot(None),
        ).all()
        
        for task in recurring_tasks:
            scheduled_today = datetime.combine(target_date, task.scheduled_time)
            urgency = task.urgency or 2
            timescale = get_recurrence_timescale(db, task)
            fixed.append(PrioritisedTask(
                task=task,
                priority_score=calculate_priority_score(task.importance, urgency),
                calculated_urgency=urgency,
                recurrence_timescale=timescale,
                is_fixed=True,
                scheduled_time=scheduled_today,
            ))
    
    fixed.sort(key=lambda pt: pt.scheduled_time or datetime.max)
    return fixed


def get_flexible_tasks(db: Session, target_date: date, available_hours_per_day: int = 6) -> list[PrioritisedTask]:
    """
    Get all flexible (not time-bound) tasks that could be scheduled today.
    """
    flexible = []
    
    deadlines = db.query(Task).filter(
        Task.type == "deadline",
        Task.status == "pending",
        or_(Task.snooze_until.is_(None), Task.snooze_until <= str(target_date)),
    ).all()
    
    today = date.today()
    for task in deadlines:
        due_today = False
        if task.deadline_at:
            deadline_date = task.deadline_at.date()
            if deadline_date < target_date:
                # Already overdue — handled by the auto-complete sweep.
                continue
            if deadline_date == today:
                urgency = 3
                due_today = True
            else:
                urgency = calculate_urgency_for_deadline(task, available_hours_per_day)
        else:
            urgency = calculate_urgency_for_deadline(task, available_hours_per_day)
        flexible.append(PrioritisedTask(
            task=task,
            priority_score=calculate_priority_score(task.importance, urgency),
            calculated_urgency=urgency,
            recurrence_timescale=RecurrenceTimescale.NONE,
            is_fixed=False,
            due_today=due_today,
        ))
    
    projections = db.query(Projection).filter(Projection.due_date == target_date).all()
    recurring_task_ids = [p.task_id for p in projections]
    
    if recurring_task_ids:
        recurring_tasks = db.query(Task).filter(
            Task.id.in_(recurring_task_ids),
            Task.status == "pending",
            Task.scheduled_time.is_(None),
        ).all()
        
        for task in recurring_tasks:
            urgency = task.urgency or 1
            timescale = get_recurrence_timescale(db, task)
            flexible.append(PrioritisedTask(
                task=task,
                priority_score=calculate_priority_score(task.importance, urgency),
                calculated_urgency=urgency,
                recurrence_timescale=timescale,
                is_fixed=False,
            ))
    
    errands = db.query(Task).filter(
        Task.type == "errand",
        Task.status == "pending",
        or_(Task.snooze_until.is_(None), Task.snooze_until <= str(target_date)),
    ).all()
    
    for task in errands:
        urgency = task.urgency or 1
        flexible.append(PrioritisedTask(
            task=task,
            priority_score=calculate_priority_score(task.importance, urgency),
            calculated_urgency=urgency,
            recurrence_timescale=RecurrenceTimescale.NONE,
            is_fixed=False,
        ))
    
    flexible.sort(key=lambda t: t.sort_key(), reverse=True)
    return flexible


def calculate_end_time(task: Task, start: datetime) -> datetime:
    duration = (task.estimated_duration or 30) + (task.prep_duration or 0)
    return start + timedelta(minutes=duration)


def calculate_gaps(
    fixed_tasks: list[PrioritisedTask],
    target_date: date,
    include_afternoon: bool = True,
) -> list[TimeSlot]:
    """
    Calculate available time gaps after placing fixed tasks.
    
    Returns gaps within main window (9am-3pm) and optionally afternoon (3pm-6pm).
    Evening window (6pm-11pm) is excluded as it's manual-only.
    
    If target_date is today, gaps start from now (not 9am).
    """
    main_start = datetime.combine(target_date, time(settings.main_window_start))
    main_end = datetime.combine(target_date, time(settings.main_window_end))
    afternoon_start = datetime.combine(target_date, time(settings.afternoon_window_start))
    afternoon_end = datetime.combine(target_date, time(settings.afternoon_window_end))
    
    now = datetime.now()
    if target_date == now.date() and now > main_start:
        main_start = now
    
    if include_afternoon:
        day_end = afternoon_end
    else:
        day_end = main_end
    
    if main_start >= day_end:
        return []
    
    blocked_slots: list[TimeSlot] = []
    for pt in fixed_tasks:
        if pt.scheduled_time:
            start = pt.scheduled_time
            end = calculate_end_time(pt.task, start)
            if start < day_end and end > main_start:
                blocked_slots.append(TimeSlot(start=start, end=end))
    
    blocked_slots.sort(key=lambda s: s.start)
    
    gaps: list[TimeSlot] = []
    cursor = main_start
    
    for slot in blocked_slots:
        if slot.start > cursor:
            gap_end = min(slot.start, day_end)
            if gap_end > cursor:
                gaps.append(TimeSlot(start=cursor, end=gap_end))
        cursor = max(cursor, slot.end)
    
    if cursor < day_end:
        gaps.append(TimeSlot(start=cursor, end=day_end))
    
    return gaps


def is_in_afternoon(slot: TimeSlot, target_date: date) -> bool:
    afternoon_start = datetime.combine(target_date, time(settings.afternoon_window_start))
    return slot.start >= afternoon_start


def find_fitting_gap(
    task: Task,
    gaps: list[TimeSlot],
    target_date: date,
) -> tuple[TimeSlot | None, int]:
    """
    Find the first gap that can fit the task.
    
    Respects allow_afternoon flag.
    Returns (gap, index) or (None, -1) if no fit.
    """
    duration = (task.estimated_duration or 30) + (task.prep_duration or 0)
    
    for i, gap in enumerate(gaps):
        if is_in_afternoon(gap, target_date) and not task.allow_afternoon:
            continue
        if gap.duration_minutes >= duration:
            return gap, i
    
    return None, -1


def split_gap(gap: TimeSlot, task: Task) -> list[TimeSlot]:
    """
    Split a gap after placing a task at the start.
    Returns remaining gaps (0 or 1 gap).
    """
    duration = (task.estimated_duration or 30) + (task.prep_duration or 0)
    task_end = gap.start + timedelta(minutes=duration)
    
    if task_end < gap.end:
        return [TimeSlot(start=task_end, end=gap.end)]
    return []


def schedule_tasks_into_timeline(
    fixed_tasks: list[PrioritisedTask],
    flexible_tasks: list[PrioritisedTask],
    target_date: date,
) -> list[PrioritisedTask]:
    """
    Schedule tasks using the timeline/gaps approach.
    
    Algorithm:
    1. Fixed tasks are already sorted by start time
    2. Calculate gaps between fixed tasks
    3. Check for manually scheduled tasks (from timeline drag-and-drop)
    4. For each flexible task (in priority order):
       a. If task has a manual_scheduled_time for today, use that time
       b. Otherwise find a gap that fits (respecting allow_afternoon)
       c. Place task at start of gap
       d. Recalculate the gap
    5. Return all scheduled tasks in time order
    """
    gaps = calculate_gaps(fixed_tasks, target_date, include_afternoon=True)
    
    scheduled: list[PrioritisedTask] = list(fixed_tasks)
    
    # Separate manually scheduled from auto-scheduled tasks
    manual_tasks = []
    auto_tasks = []
    
    for pt in flexible_tasks:
        task = pt.task
        if (task.manual_scheduled_time 
            and task.manual_scheduled_time.date() == target_date):
            # Use the manual scheduled time
            pt.scheduled_time = task.manual_scheduled_time
            manual_tasks.append(pt)
        else:
            auto_tasks.append(pt)
    
    # Add manually scheduled tasks and update gaps
    for pt in manual_tasks:
        scheduled.append(pt)
        # Block this time slot from gaps
        task_start = pt.scheduled_time
        task_end = calculate_end_time(pt.task, task_start)
        new_gaps = []
        for gap in gaps:
            if task_end <= gap.start or task_start >= gap.end:
                # No overlap
                new_gaps.append(gap)
            elif task_start <= gap.start and task_end >= gap.end:
                # Task completely covers gap
                pass
            elif task_start <= gap.start:
                # Task overlaps start of gap
                if task_end < gap.end:
                    new_gaps.append(TimeSlot(start=task_end, end=gap.end))
            elif task_end >= gap.end:
                # Task overlaps end of gap
                if task_start > gap.start:
                    new_gaps.append(TimeSlot(start=gap.start, end=task_start))
            else:
                # Task is in middle of gap, split it
                if task_start > gap.start:
                    new_gaps.append(TimeSlot(start=gap.start, end=task_start))
                if task_end < gap.end:
                    new_gaps.append(TimeSlot(start=task_end, end=gap.end))
        gaps = new_gaps
    
    # Auto-schedule remaining tasks
    for pt in auto_tasks:
        gap, gap_idx = find_fitting_gap(pt.task, gaps, target_date)
        
        if gap is None:
            continue
        
        pt.scheduled_time = gap.start
        scheduled.append(pt)
        
        remaining_gaps = split_gap(gap, pt.task)
        gaps = gaps[:gap_idx] + remaining_gaps + gaps[gap_idx + 1:]
    
    scheduled.sort(key=lambda pt: pt.scheduled_time or datetime.max)
    return scheduled


def populate_workout_exercises(db: Session, scheduled: list[PrioritisedTask]) -> None:
    """
    For any workout tasks in the schedule, run the exercise selection algorithm
    and populate the selected_exercise field.
    """
    workout_tasks = [pt for pt in scheduled if pt.task.type == "workout"]
    if not workout_tasks:
        return
    
    exercises = select_todays_exercises(db, count=1)
    if exercises:
        exercise_name = exercises[0].name
        for pt in workout_tasks:
            pt.selected_exercise = exercise_name


def _schedule_for_date(
    db: Session, target_date: date, available_hours_per_day: int
) -> tuple[list[PrioritisedTask], list[PrioritisedTask], list[PrioritisedTask]]:
    """
    Compute fixed tasks, flexible tasks, and the final scheduled order for target_date.

    Deadlines due today (due_today=True) are pulled out of the normal gap-based
    scheduling and pinned to the front of the result, regardless of capacity.
    """
    fixed = get_fixed_tasks(db, target_date)
    flexible = get_flexible_tasks(db, target_date, available_hours_per_day)

    due_today = [pt for pt in flexible if pt.due_today]
    other_flexible = [pt for pt in flexible if not pt.due_today]

    scheduled = due_today + schedule_tasks_into_timeline(fixed, other_flexible, target_date)
    populate_workout_exercises(db, scheduled)
    return fixed, flexible, scheduled


def get_prioritised_tasks(db: Session, target_date: date, available_hours_per_day: int = 6) -> list[Task]:
    """
    Get all tasks for the target date, scheduled into the timeline.
    """
    _, _, scheduled = _schedule_for_date(db, target_date, available_hours_per_day)
    return [pt.task for pt in scheduled]


def get_prioritised_tasks_with_metadata(
    db: Session,
    target_date: date,
    available_hours_per_day: int = 6
) -> tuple[list[PrioritisedTask], dict]:
    """
    Get all tasks for the target date with full metadata.
    """
    fixed, flexible, scheduled = _schedule_for_date(db, target_date, available_hours_per_day)
    gaps = calculate_gaps(fixed, target_date, include_afternoon=True)
    
    main_start = datetime.combine(target_date, time(settings.main_window_start))
    afternoon_start = datetime.combine(target_date, time(settings.afternoon_window_start))
    
    main_available = sum(
        g.duration_minutes for g in gaps 
        if g.start < afternoon_start
    )
    afternoon_available = sum(
        g.duration_minutes for g in gaps 
        if g.start >= afternoon_start
    )
    
    capacity = {
        "main_available": main_available,
        "afternoon_available": afternoon_available,
        "total_candidate_time": sum(pt.task.estimated_duration or 0 for pt in scheduled if not pt.is_fixed),
        "fixed_count": len(fixed),
        "flexible_count": len(flexible),
        "scheduled_count": len(scheduled),
    }
    
    return scheduled, capacity


def bin_tasks_by_priority(tasks: list[PrioritisedTask]) -> dict[int, list[PrioritisedTask]]:
    """Bin tasks into priority levels by their score."""
    bins: dict[int, list[PrioritisedTask]] = {}
    for task in tasks:
        score = task.priority_score
        if score not in bins:
            bins[score] = []
        bins[score].append(task)
    return bins
