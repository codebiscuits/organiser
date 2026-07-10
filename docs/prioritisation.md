# Prioritisation & Scheduling

The app uses a **timeline-based scheduling algorithm** to build the daily task list. The day is treated as a timeline with gaps that tasks are fitted into.

## Overview

1. **Fixed tasks** (appointments and time-bound recurring) are placed at their exact scheduled times
2. **Gaps** are calculated between fixed tasks within the scheduling windows
3. **Flexible tasks** are sorted by priority and fitted into gaps in order

## Priority Score

Each task is scored as `importance × urgency` (both 1-3 scale):

| Score | Meaning |
|-------|---------|
| 9 | Critical (3×3) |
| 6 | High (3×2 or 2×3) |
| 4 | Medium-high (2×2) |
| 3 | Medium (3×1 or 1×3) |
| 2 | Low-medium (2×1 or 1×2) |
| 1 | Low (1×1) |

## Urgency Calculation by Task Type

### Appointments
- Urgency = 3 when scheduled for today
- Appointments are always "fixed" tasks placed at their exact time

### Deadlines
If the deadline's date is **today**, urgency is forced to 3 and the task is pinned (see "Due-Today Pinning" below) instead of using the buffer calculation.

Otherwise, urgency is calculated dynamically based on buffer time:

```
available_hours_per_day = 6 (configurable)
hours_needed = estimated_duration / 60
days_of_work_needed = hours_needed / available_hours_per_day
buffer = time_remaining - days_of_work_needed

Urgency mapping:
- buffer > 7 days → urgency = 1
- buffer 2-7 days → urgency = 2  
- buffer < 2 days → urgency = 3
```

A deadline whose date is **before** today is excluded from `get_flexible_tasks` entirely — it's handled by the overdue auto-complete sweep instead (see `docs/task_completion.md`).

### Variable Recurring Tasks (VRTs)
Unlike plain recurring tasks, a VRT's single completion-driven projection **carries forward**: `get_flexible_tasks` matches `due_date <= target_date` (not `==`) for VRTs specifically, so an uncompleted VRT stays on the list every day until completed instead of vanishing once its date passes. Plain recurring/workout tasks keep exact-date matching — their future projections already exist, so a missed instance self-heals on its own.

Effective urgency escalates with overdue-ness *relative to the task's own cadence*:

```
interval_days = days between the task's last completion and the projection's due date
                (fallback: recurrence interval in days, else 30; always >= 1)
overdue_ratio = max(0, target_date - due_date) / interval_days

effective_urgency = base                if ratio == 0
                    max(base, 2)        if 0 < ratio < vrt_escalation_half_ratio (default 0.5)
                    3                   if ratio >= vrt_escalation_half_ratio
```

So a monthly-ish VRT hits urgency 3 after ~2 weeks overdue; a weekly one after ~3–4 days. See `effective_urgency_for_vrt` in `app/services/prioritisation.py`.

### Other Tasks (errands, plain recurring)
Manually set at creation time.

## Tie-Breaking (within same priority score)

1. **Recurrence timescale**: Monthly > Weekly > Daily (daily tasks are easiest to defer if missed)
2. **Deferred count**: Previously deferred tasks get priority (higher count wins)

## Timeline Scheduling Algorithm

### Step 1: Get Fixed Tasks

Fixed tasks are:
- Appointments with a `scheduled_at` time on the target date
- Recurring tasks with a `scheduled_at` time (time-bound recurring)

These are sorted by their start times.

### Step 2: Calculate End Times

For each fixed task:
```
end_time = scheduled_time + estimated_duration + prep_duration
```

### Step 3: Calculate Gaps

Gaps are the sections of available time not occupied by fixed tasks:

```
day_start = 9:00 (main window start)
day_end = 18:00 (afternoon window end)

For each gap between fixed tasks:
  if gap.start < fixed_task.start:
    gaps.append(TimeSlot(gap.start, fixed_task.start))
  gap.start = fixed_task.end
```

### Step 4: Sort Flexible Tasks

Flexible tasks (deadlines, errands, non-time-bound recurring) are sorted by:
1. **Due today** (deadlines due today first — see "Due-Today Pinning" below)
2. Priority score (highest first)
3. Recurrence timescale (monthly > weekly > daily)
4. Deferred count (higher first)

## Due-Today Pinning

Deadlines due today are *not* fitted into a gap at all. `_schedule_for_date()` splits the sorted flexible list into `due_today` and `other_flexible`, runs the normal timeline algorithm (steps 5-6) on `other_flexible` only, then prepends the `due_today` tasks to the front of the result:

```
scheduled = due_today + schedule_tasks_into_timeline(fixed, other_flexible, target_date)
```

Effects:
- A due-today deadline always appears, regardless of how full today's schedule is (it doesn't count against gap capacity)
- It's always `scheduled[0]` unless another due-today deadline has higher priority
- If multiple deadlines are due today, they keep their relative priority order from Step 4 (highest first)
- It's styled red in the UI and stays pinned until completed or deferred — once the date rolls over, it's picked up by the overdue auto-complete sweep instead (`docs/task_completion.md`)

### Step 5: Handle Manually Scheduled Tasks

Before auto-scheduling, check for tasks with `manual_scheduled_time` set for the target date (from the Timeline view):

1. These tasks use their user-set time instead of being fitted into gaps
2. Block their time slots from the available gaps
3. This allows users to override automatic prioritisation

### Step 6: Fit Remaining Tasks into Gaps

For each flexible task without a manual time (in priority order):

1. **Find a fitting gap**: First gap where `gap.duration >= task.duration`
   - Respect `allow_afternoon` flag (if false, skip gaps starting after 3pm)
2. **Place the task**: Assign `scheduled_time = gap.start`
3. **Recalculate the gap**: 
   - `new_gap.start = gap.start + task.duration`
   - If gap is now empty, remove it

Continue until no more tasks fit or all tasks are scheduled.

## Scheduling Windows

| Window | Time | Auto-scheduling |
|--------|------|-----------------|
| Main | 9am-3pm | All tasks |
| Afternoon | 3pm-6pm | Only tasks with `allow_afternoon=True` |
| Evening | 6pm-11pm | Manual appointments only (excluded from gap calculation) |

## Example

Given:
- Appointment at 11:00 (1 hour)
- Appointment at 14:00 (30 min)
- Flexible tasks: A (60 min, score 9), B (90 min, score 6), C (45 min, score 3)

Gap calculation:
- Gap 1: 9:00 - 11:00 (120 min)
- Gap 2: 12:00 - 14:00 (120 min)
- Gap 3: 14:30 - 18:00 (210 min)

Scheduling:
1. Task A (60 min, score 9) → Gap 1 at 9:00, Gap 1 becomes 10:00-11:00 (60 min)
2. Task B (90 min, score 6) → Gap 2 at 12:00, Gap 2 becomes 13:30-14:00 (30 min)
3. Task C (45 min, score 3) → Gap 1 at 10:00, Gap 1 becomes 10:45-11:00 (15 min)

Final schedule:
- 9:00 - Task A
- 10:00 - Task C  
- 11:00 - Appointment
- 12:00 - Task B
- 14:00 - Appointment
