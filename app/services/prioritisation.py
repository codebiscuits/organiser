import math
from datetime import datetime, date, time, timedelta
from enum import IntEnum
from dataclasses import dataclass, field
from sqlalchemy import or_
from sqlalchemy.orm import Session

from app.models.task import Task, CompletedTask
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


def effective_urgency_for_appointment() -> int:
    """Appointments are always urgency 3 — they're fixed, immovable commitments."""
    return 3


def effective_urgency_for_recurring(task: Task, default: int = 1) -> int:
    """
    Effective urgency for a plain recurring (or workout) task: the user's own
    urgency setting, or `default` if unset.

    `default` differs by placement: time-bound recurring tasks (fixed, in
    get_fixed_tasks) default to 2; non-time-bound ones (flexible, in
    get_flexible_tasks) default to 1. This mirrors the pre-refactor inline
    `task.urgency or 2` / `task.urgency or 1`.
    """
    return task.urgency or default


def compute_errand_backlog_boosts(db: Session) -> dict[str, int]:
    """
    Theme A component A3 — pressure valve for a growing errand list.

    N = count of ALL pending errands (snooze is deliberately ignored: a
    snoozed errand still contributes to the pile). When N exceeds the soft
    threshold, the oldest ⌈N/3⌉ errands (by created_at) get +1 urgency;
    past the hard threshold the oldest ⌈N/3⌉ get +2 and the next ⌈N/3⌉ +1,
    so the scheduler drains the backlog oldest-first.

    Computed at list-build time and never stored — the urgency shown in
    edit forms stays the user's own setting. Returns {task_id: boost} for
    boosted errands only; one query regardless of N.
    """
    rows = (
        db.query(Task.id)
        .filter(Task.type == "errand", Task.status == "pending")
        .order_by(Task.created_at.asc(), Task.id.asc())
        .all()
    )
    n = len(rows)
    boosts: dict[str, int] = {}
    if n <= settings.errand_backlog_soft:
        return boosts

    third = math.ceil(n / 3)
    if n > settings.errand_backlog_hard:
        for (task_id,) in rows[:third]:
            boosts[task_id] = 2
        for (task_id,) in rows[third:2 * third]:
            boosts[task_id] = 1
    else:
        for (task_id,) in rows[:third]:
            boosts[task_id] = 1
    return boosts


def effective_urgency_for_errand(
    task: Task, available_hours_per_day: int = 6, backlog_boost: int = 0
) -> tuple[int, bool]:
    """
    Effective urgency for an errand, returning (urgency, due_today).

    Theme A component A4 — every errand is time-bound, so urgency now flows
    through the same buffer maths as deadlines, keyed off deadline_at:

    - No deadline_at (legacy row the migration hasn't touched): the user's
      static urgency, as before.
    - Auto deadline (deadline_auto=True): buffer-based urgency, but NEVER
      due-today pinned; a fully expired auto-deadline escalates to 3 and
      stays on the list (the half-life prompt handles it) rather than being
      treated as due/overdue.
    - User-confirmed deadline (deadline_auto=False): full deadline semantics
      — due today ⇒ urgency 3 + pinned. (Overdue ones never reach here:
      get_flexible_tasks skips them, the auto-complete sweep handles them.)

    In all buffer-based cases the user's own urgency setting acts as a
    floor: max(base, computed), so a hand-set urgency-3 errand is never
    downgraded by a comfortably distant auto-deadline.

    `backlog_boost` (Theme A component A3, see
    compute_errand_backlog_boosts) combines by taking the max of the
    deadline-derived urgency and base + boost — the two pressures don't
    stack — and the result is clamped to 3.
    """
    base = task.urgency or 1
    today = date.today()
    due_today = False

    if not task.deadline_at:
        urgency = base
    elif task.deadline_auto:
        if task.deadline_at.date() < today:
            urgency = 3
        else:
            urgency = max(base, calculate_urgency_for_deadline(task, available_hours_per_day))
    elif task.deadline_at.date() == today:
        urgency, due_today = 3, True
    else:
        urgency = max(base, calculate_urgency_for_deadline(task, available_hours_per_day))

    effective = min(3, max(urgency, base + backlog_boost))
    return effective, due_today


def effective_urgency_for_deadline(
    task: Task, target_date: date, available_hours_per_day: int = 6
) -> tuple[int, bool]:
    """
    Effective urgency for a deadline task, returning (urgency, due_today).

    due_today is computed against the real `date.today()` (not target_date)
    — matches the pre-refactor behaviour: a deadline due today is forced to
    urgency 3 and pinned, regardless of which date's list is being built.
    """
    today = date.today()
    if task.deadline_at and task.deadline_at.date() == today:
        return 3, True
    return calculate_urgency_for_deadline(task, available_hours_per_day), False


def _vrt_interval_days(db: Session, task: Task, due_date: date) -> int:
    """
    Cadence length (in days) used as the denominator of a VRT's overdue_ratio.

    Prefers the gap between the task's most recent completion and this
    projection's due date (how long the task actually took last cycle);
    falls back to the Recurrence's nominal interval (interval_multiple ×
    {daily:1, weekly:7, monthly:30, yearly:365}), then to a flat 30 days.
    Always >= 1.
    """
    latest_completion = (
        db.query(CompletedTask)
        .filter(CompletedTask.task_id == task.id)
        .order_by(CompletedTask.completed_at.desc())
        .first()
    )
    if latest_completion and latest_completion.completed_at:
        completed_at = latest_completion.completed_at
        completed_date = completed_at.date() if isinstance(completed_at, datetime) else completed_at
        interval_days = (due_date - completed_date).days
        if interval_days >= 1:
            return interval_days

    recurrence = db.query(Recurrence).filter(Recurrence.task_id == task.id).first()
    if recurrence:
        days_per_unit = {"daily": 1, "weekly": 7, "monthly": 30, "yearly": 365}
        unit_days = days_per_unit.get(recurrence.interval_type)
        if unit_days:
            interval_days = (recurrence.interval_multiple or 1) * unit_days
            if interval_days >= 1:
                return interval_days

    return 30


def effective_urgency_for_vrt(
    db: Session, task: Task, projection: Projection, target_date: date
) -> int:
    """
    Effective urgency for a variable_recurring task (Theme A component A2):
    escalates with overdue-ness *relative to the task's own cadence*, so
    "N days late" means more for a weekly task than a quarterly one.

        overdue_ratio = max(0, target_date - due_date) / interval_days

        effective_urgency = base                if ratio == 0
                             max(base, 2)        if 0 < ratio < vrt_escalation_half_ratio
                             3                   if ratio >= vrt_escalation_half_ratio
    """
    base = task.urgency or 1
    overdue_days = max(0, (target_date - projection.due_date).days)
    if overdue_days == 0:
        return base

    interval_days = max(1, _vrt_interval_days(db, task, projection.due_date))
    overdue_ratio = overdue_days / interval_days

    if overdue_ratio >= settings.vrt_escalation_half_ratio:
        return 3
    return max(base, 2)


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
        urgency = effective_urgency_for_appointment()
        fixed.append(PrioritisedTask(
            task=task,
            priority_score=calculate_priority_score(task.importance, urgency),
            calculated_urgency=urgency,
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
            urgency = effective_urgency_for_recurring(task, default=2)
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
    
    for task in deadlines:
        if task.deadline_at and task.deadline_at.date() < target_date:
            # Already overdue — handled by the auto-complete sweep.
            continue
        urgency, due_today = effective_urgency_for_deadline(task, target_date, available_hours_per_day)
        flexible.append(PrioritisedTask(
            task=task,
            priority_score=calculate_priority_score(task.importance, urgency),
            calculated_urgency=urgency,
            recurrence_timescale=RecurrenceTimescale.NONE,
            is_fixed=False,
            due_today=due_today,
        ))
    
    # Plain recurring/workout: exact-date match only. Their future projections
    # already exist (generated up to 90 days out), so a missed instance
    # self-heals on its own next occurrence rather than carrying forward.
    projections = db.query(Projection).filter(Projection.due_date == target_date).all()
    recurring_task_ids = [p.task_id for p in projections]

    if recurring_task_ids:
        recurring_tasks = db.query(Task).filter(
            Task.id.in_(recurring_task_ids),
            Task.status == "pending",
            Task.scheduled_time.is_(None),
            Task.type != "variable_recurring",
        ).all()

        for task in recurring_tasks:
            urgency = effective_urgency_for_recurring(task, default=1)
            timescale = get_recurrence_timescale(db, task)
            flexible.append(PrioritisedTask(
                task=task,
                priority_score=calculate_priority_score(task.importance, urgency),
                calculated_urgency=urgency,
                recurrence_timescale=timescale,
                is_fixed=False,
            ))

    # Variable recurring: carry-forward. A VRT has exactly one
    # completion-driven Projection at a time, so an uncompleted one must
    # stay visible every day until completed — due_date <= target_date
    # (not ==), otherwise it vanishes forever the day after it's missed
    # (Theme A component A2; see docs/design-theme-a.md §3).
    vrt_rows = (
        db.query(Projection, Task)
        .join(Task, Projection.task_id == Task.id)
        .filter(
            Projection.due_date <= target_date,
            Task.type == "variable_recurring",
            Task.status == "pending",
            Task.scheduled_time.is_(None),
        )
        .order_by(Projection.due_date.asc())
        .all()
    )
    seen_vrt_task_ids: set[str] = set()
    for projection, task in vrt_rows:
        if task.id in seen_vrt_task_ids:
            # A VRT should only ever have one open projection; if stragglers
            # exist, use the earliest uncompleted one and include it once.
            continue
        seen_vrt_task_ids.add(task.id)
        urgency = effective_urgency_for_vrt(db, task, projection, target_date)
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

    backlog_boosts = compute_errand_backlog_boosts(db)

    for task in errands:
        if (
            task.deadline_at
            and not task.deadline_auto
            and task.deadline_at.date() < target_date
        ):
            # Overdue USER-CONFIRMED errand deadline — handled by the
            # auto-complete sweep, same as deadline tasks. (Auto deadlines
            # never take this branch: expiry escalates urgency instead.)
            continue
        urgency, due_today = effective_urgency_for_errand(
            task, available_hours_per_day, backlog_boosts.get(task.id, 0)
        )
        flexible.append(PrioritisedTask(
            task=task,
            priority_score=calculate_priority_score(task.importance, urgency),
            calculated_urgency=urgency,
            recurrence_timescale=RecurrenceTimescale.NONE,
            is_fixed=False,
            due_today=due_today,
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


def _merge_slots(slots: list[TimeSlot]) -> list[TimeSlot]:
    """
    Coalesce a list of free time slots: sorted by start, with touching or
    overlapping slots merged into one. Returns fresh TimeSlot objects (the
    inputs are never mutated). Used by displacement to answer "if this
    occupied slot were freed, how big would the combined hole be?" — an
    eviction adjacent to an existing gap frees one larger slot.
    """
    ordered = sorted(slots, key=lambda s: s.start)
    merged: list[TimeSlot] = []
    for s in ordered:
        if merged and s.start <= merged[-1].end:
            if s.end > merged[-1].end:
                merged[-1] = TimeSlot(start=merged[-1].start, end=s.end)
        else:
            merged.append(TimeSlot(start=s.start, end=s.end))
    return merged


def _occupied_slot(pt: PrioritisedTask) -> TimeSlot:
    return TimeSlot(start=pt.scheduled_time, end=calculate_end_time(pt.task, pt.scheduled_time))


def _place_in_gaps(
    pt: PrioritisedTask,
    gaps: list[TimeSlot],
    target_date: date,
    scheduled: list[PrioritisedTask],
    auto_placed: list[PrioritisedTask],
) -> tuple[bool, list[TimeSlot]]:
    """First-fit `pt` into `gaps`; on success, record it and split the gap."""
    gap, gap_idx = find_fitting_gap(pt.task, gaps, target_date)
    if gap is None:
        return False, gaps
    pt.scheduled_time = gap.start
    scheduled.append(pt)
    auto_placed.append(pt)
    return True, gaps[:gap_idx] + split_gap(gap, pt.task) + gaps[gap_idx + 1:]


def _try_displacement(
    incoming: PrioritisedTask,
    auto_placed: list[PrioritisedTask],
    scheduled: list[PrioritisedTask],
    gaps: list[TimeSlot],
    overflow: list[PrioritisedTask],
    target_date: date,
) -> tuple[bool, list[TimeSlot]]:
    """
    Theme A component A1 — displacement. `incoming` fits no remaining gap;
    try to free room by evicting already-placed AUTO tasks with strictly
    lower priority_score. Never touches fixed tasks, manual drag-and-drop
    placements (they're not in auto_placed), or due-today pins.

    Strategy (simple and correct over optimal, per the design):
    1. Candidates: strictly lower score, sorted lowest score first, then
       shortest duration (cheapest eviction) — ids as a final deterministic
       tie-break aren't needed since list order is stable.
    2. Pass 1: try each candidate ALONE — merge its occupied slot with the
       adjacent free gaps and test whether incoming then fits.
    3. Pass 2: greedily accumulate candidates in the same order, testing
       after each addition; stop at the first workable set.
    4. Commit: evict the set, place incoming, then re-fit each evictee (in
       priority order) into what remains; evictees that no longer fit go to
       overflow. If no set works, nothing is evicted.

    Returns (placed?, updated_gaps). Mutates scheduled/auto_placed/overflow
    in place on success.
    """
    candidates = [
        c for c in auto_placed
        if c.priority_score < incoming.priority_score and not c.due_today
    ]
    if not candidates:
        return False, gaps

    def duration_of(c: PrioritisedTask) -> int:
        return (c.task.estimated_duration or 30) + (c.task.prep_duration or 0)

    candidates.sort(key=lambda c: (c.priority_score, duration_of(c)))

    def fits_after_evicting(evictees: list[PrioritisedTask]) -> bool:
        merged = _merge_slots(gaps + [_occupied_slot(e) for e in evictees])
        return find_fitting_gap(incoming.task, merged, target_date)[0] is not None

    eviction_set: list[PrioritisedTask] | None = None
    for c in candidates:
        if fits_after_evicting([c]):
            eviction_set = [c]
            break
    if eviction_set is None:
        accumulated: list[PrioritisedTask] = []
        for c in candidates:
            accumulated.append(c)
            if fits_after_evicting(accumulated):
                eviction_set = list(accumulated)
                break
    if eviction_set is None:
        return False, gaps

    # Commit the eviction: pull the set off the timeline and reclaim its time.
    for e in eviction_set:
        scheduled[:] = [s for s in scheduled if s is not e]
        auto_placed[:] = [s for s in auto_placed if s is not e]
    gaps = _merge_slots(gaps + [_occupied_slot(e) for e in eviction_set])
    for e in eviction_set:
        e.scheduled_time = None

    placed, gaps = _place_in_gaps(incoming, gaps, target_date, scheduled, auto_placed)
    assert placed  # guaranteed: fits_after_evicting used the same computation

    # Re-fit evictees into the remaining space, highest priority first.
    for e in sorted(eviction_set, key=lambda c: c.sort_key(), reverse=True):
        refit, gaps = _place_in_gaps(e, gaps, target_date, scheduled, auto_placed)
        if not refit:
            overflow.append(e)

    return True, gaps


def schedule_tasks_into_timeline(
    fixed_tasks: list[PrioritisedTask],
    flexible_tasks: list[PrioritisedTask],
    target_date: date,
) -> tuple[list[PrioritisedTask], list[PrioritisedTask]]:
    """
    Schedule tasks using the timeline/gaps approach with banded displacement
    (Theme A component A1). Returns (placed, overflow).

    Guarantee: no task appears on the day while a strictly higher-scoring
    task is silently absent — a task that fits nothing may displace placed
    lower-band tasks, and anything that still doesn't fit is returned in
    `overflow` (the "didn't fit today" shelf) instead of being dropped.

    Algorithm:
    1. Fixed tasks are already sorted by start time
    2. Calculate gaps between fixed tasks
    3. Manually scheduled tasks (timeline drag-and-drop) keep their exact
       times and block gaps first — they are sacred and never evicted
    4. For each remaining flexible task, in descending priority order
       (the input is sorted by sort_key, which leads with priority_score):
       a. First-fit into a gap (respecting allow_afternoon)
       b. If nothing fits, attempt displacement of strictly-lower-score
          auto-placed tasks (_try_displacement); displaced tasks are
          re-fitted afterwards or overflow
       c. If displacement can't help either, the task goes to overflow
    5. Placed tasks are returned in time order; overflow in priority order
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

    # Auto-schedule remaining tasks in priority (band) order, displacing
    # lower-band placements when out of room.
    auto_placed: list[PrioritisedTask] = []
    overflow: list[PrioritisedTask] = []

    for pt in auto_tasks:
        placed, gaps = _place_in_gaps(pt, gaps, target_date, scheduled, auto_placed)
        if placed:
            continue
        placed, gaps = _try_displacement(pt, auto_placed, scheduled, gaps, overflow, target_date)
        if not placed:
            overflow.append(pt)

    scheduled.sort(key=lambda pt: pt.scheduled_time or datetime.max)
    overflow.sort(key=lambda pt: pt.sort_key(), reverse=True)
    return scheduled, overflow


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
) -> tuple[list[PrioritisedTask], list[PrioritisedTask], list[PrioritisedTask], list[PrioritisedTask]]:
    """
    Compute (fixed, flexible, scheduled, overflow) for target_date.

    Deadlines due today (due_today=True) are pulled out of the normal gap-based
    scheduling and pinned to the front of the result, regardless of capacity —
    they can neither overflow nor be displaced. `overflow` holds auto flexible
    tasks that fit nothing even after displacement (Theme A A1).
    """
    fixed = get_fixed_tasks(db, target_date)
    flexible = get_flexible_tasks(db, target_date, available_hours_per_day)

    due_today = [pt for pt in flexible if pt.due_today]
    other_flexible = [pt for pt in flexible if not pt.due_today]

    placed, overflow = schedule_tasks_into_timeline(fixed, other_flexible, target_date)
    scheduled = due_today + placed
    populate_workout_exercises(db, scheduled)
    return fixed, flexible, scheduled, overflow


def get_prioritised_tasks(db: Session, target_date: date, available_hours_per_day: int = 6) -> list[Task]:
    """
    Get all placed tasks for the target date, scheduled into the timeline.
    (Overflow tasks are not included — same contract as before A1; callers
    wanting them use get_prioritised_tasks_with_metadata.)
    """
    _, _, scheduled, _ = _schedule_for_date(db, target_date, available_hours_per_day)
    return [pt.task for pt in scheduled]


def get_prioritised_tasks_with_metadata(
    db: Session,
    target_date: date,
    available_hours_per_day: int = 6
) -> tuple[list[PrioritisedTask], dict]:
    """
    Get all tasks for the target date with full metadata.

    The capacity dict includes "overflow" (list of PrioritisedTask that
    didn't fit today even after displacement, priority order) and
    "overflow_count". NOTE: "overflow" holds live objects — JSON endpoints
    must serialise or strip it (see /tasks/today).
    """
    fixed, flexible, scheduled, overflow = _schedule_for_date(db, target_date, available_hours_per_day)
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
        "overflow": overflow,
        "overflow_count": len(overflow),
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
