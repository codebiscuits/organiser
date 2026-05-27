# Life Organiser App — Design Document

## Overview

A personal task management PWA that automatically builds a prioritised daily todo list. Accessible from Linux computers and Android phones.

## Tech Stack

- **Backend:** FastAPI (Python)
- **Database:** SQLite (upgradeable to PostgreSQL)
- **Frontend:** HTMX + Alpine.js + Jinja2 templates
- **Push Notifications:** Web Push API via `pywebpush`
- **Admin Panel:** Custom admin routes

---

## Task Types

### 1. Appointment
- **Fields:** title, datetime, duration, location (optional), importance (1-3), prep_duration (minutes), notes
- **Urgency:** 0 before scheduled time, max (3) when due
- **Behaviour:** 
  - Blocks time slot
  - Auto-generates a "getting ready/travelling to" task placed before it (same importance level, duration = prep_duration)
  - User prompted for prep_duration when creating appointment
  - Reminder notifications at 1 day and 1 hour before
  - Removed from tasks table on completion
  - Can be manually scheduled in any window (including evening for work shifts)

### 2. Deadline
- **Fields:** title, deadline_datetime, estimated_duration, importance (1-3), notes
- **Urgency:** Calculated dynamically based on time remaining vs estimated duration and available hours per day/week. Translates to 1-3 scale.
- **Behaviour:** 
  - Displays live countdown to deadline
  - Blocks estimated duration
  - Removed from tasks table on completion

### 3. Recurring Task
- **Fields:** title, estimated_duration, importance (1-3), urgency (1-3), allow_afternoon (boolean), scheduled_time (optional), notes
- **Recurrence:** Defined in recurrence table (see Data Model)
- **Behaviour:** 
  - Instances generated in projection table
  - Daily tasks are lower priority than weekly, weekly lower than monthly (if otherwise equal)
  - If `allow_afternoon = true`, can be scheduled in afternoon window (3pm-6pm)
  - If `scheduled_time` is set, task is pinned to that time each day (like an appointment) and other tasks are scheduled around it

### 4. Variable Recurring Task
- **Fields:** Same as Recurring
- **Behaviour:** On completion, prompts user for days until next occurrence

### 5. One-off Errand
- **Fields:** title, estimated_duration, importance (1-3), urgency (1-3), allow_afternoon (boolean), notes
- **Behaviour:** 
  - Removed from tasks table on completion
  - If `allow_afternoon = true`, can be scheduled in afternoon window (3pm-6pm)

### 6. Workout Task (Special Recurring)
- **Fields:** Same as Recurring (title, estimated_duration, importance, urgency, allow_afternoon, scheduled_time, recurrence rules)
- **Behaviour:** 
  - Created like a recurring task with recurrence rules (frequency, days of week, etc.)
  - When due, exercise selection algorithm runs to pick the top exercise (see `workout-algorithm.md`)
  - Task displays as "Workout: {exercise name}" with the selected exercise
  - On completion, prompts for sets, reps, weight; intensity is auto-set based on algorithm
  - Tracks workout history in `performed_sets` table for recovery calculation

---

## Common Task Properties

All tasks have:
- `id` (UUID)
- `type` (appointment, deadline, recurring, variable_recurring, errand, workout)
- `title` (string)
- `estimated_duration` (minutes)
- `importance` (1-3, manually set)
- `urgency` (1-3, calculated or manually set depending on type)
- `allow_afternoon` (boolean, whether task can be auto-scheduled in afternoon window)
- `status` (pending, active, completed, deferred)
- `deferred_count` (int, for tie-breaking)
- `manual_scheduled_time` (datetime, user-set time from timeline drag-and-drop)
- `created_at`, `updated_at` (timestamps)

---

## Scheduling Windows

The day is divided into three windows for auto-scheduling purposes:

| Window    | Time        | Hours | Auto-scheduling behaviour                     |
|-----------|-------------|-------|-----------------------------------------------|
| Main      | 9am - 3pm   | 6     | Default window for all tasks                  |
| Afternoon | 3pm - 6pm   | 3     | Only tasks with `allow_afternoon = true`      |
| Evening   | 6pm - 11pm  | 5     | Manual scheduling only (e.g., work shifts)    |

- Most tasks are auto-scheduled into the **main window** only
- Tasks like cooking, dishes, hoovering can have `allow_afternoon = true` to extend into the afternoon
- **Evening window** is reserved for manually-scheduled appointments (work shifts) — no auto-scheduling

---

## Priority Calculation

See `prioritisation.md` for full details.

### Importance × Urgency Score
Each task scores: `importance × urgency`

Possible scores: 9, 6, 4, 3, 2, 1

Tasks are binned into priority levels by this score.

### Urgency by Task Type

**Appointments:**
- Urgency = 3 when due
- Urgency = 0 before scheduled time

**Deadlines:**
```
available_hours_per_day = user_setting (e.g., 6 hours)
hours_required = estimated_duration / 60 (estimated_duration is recorded in minutes)
days_of_work_needed = hours_required / available_hours_per_day
time_remaining = deadline - now
buffer = time_remaining - days_of_work_needed

Translate buffer to 1-3 scale:
- buffer > 7 days: urgency = 1
- buffer 2-7 days: urgency = 2
- buffer < 2 days: urgency = 3
```

**Other tasks:** Manually set at creation time.

### Tie-breakers (within same priority score)

1. Recurring tasks sorted by timescale: monthly > weekly > daily
2. Deferred tasks prioritised over non-deferred (higher `deferred_count` wins)

Note: Fixed tasks (appointments and time-bound recurring) are placed at their exact times before flexible tasks are scheduled.

---

## Daily List Generation

The day is treated as a **timeline** with gaps that tasks are fitted into.

### Algorithm Overview

1. **Get fixed tasks**: Appointments and time-bound recurring tasks for today, sorted by start time
2. **Calculate end times**: `end_time = start_time + estimated_duration + prep_duration`
3. **Calculate gaps**: Available time slots between fixed tasks (9am-6pm, excluding evening)
4. **Get flexible tasks**: Deadlines, errands, and non-time-bound recurring, sorted by priority
5. **Fit tasks into gaps**: For each flexible task (highest priority first):
   - Find a gap the task fits into (respecting `allow_afternoon` flag)
   - Place task at the start of that gap
   - Recalculate the remaining gap

### Fixed vs Flexible Tasks

**Fixed tasks** (placed at exact times):
- Appointments with `scheduled_at`
- Recurring tasks with `scheduled_at` (time-bound recurring)

**Flexible tasks** (fitted into gaps by priority):
- Deadlines
- Errands
- Recurring tasks without `scheduled_at`

### Scheduling Result

All tasks receive a `scheduled_time` indicating when they should be done:
- Fixed tasks keep their original `scheduled_at` time
- Flexible tasks are assigned the start of the gap they were fitted into

Tasks that cannot fit into any gap are not scheduled for the day.

### Re-calculation Triggers

- Task marked as completed
- Task marked as deferred
- Task deleted
- New task added
- Manual refresh

---

## User Interactions

### Main View
- **Current task:** Large, prominent card at top
- **Next tasks:** Scrollable list below/to the side
- **Deadline tasks:** Show live countdown timer

### Timeline View
- **Visual timeline:** Day view from 9am-11pm with hour markers
- **Window indicators:** Color-coded bands for Main/Afternoon/Evening windows
- **Draggable cards:** Flexible tasks can be repositioned via drag-and-drop
- **Fixed indicators:** Appointments/time-bound recurring marked with 📌 (cannot be moved)
- **Save/Cancel:** Floating action buttons appear when changes are made
- **Sync:** Saved changes are reflected in the main Tasks view

### Week View
- **7-day calendar:** Displays next 7 days starting from today (not calendar week)
- **Hour grid:** Visual grid from 6am-11pm with hour markers
- **Click to add:** Click any time slot to create an appointment or recurring task
- **Task cards:** Shows appointments and recurring task projections
- **Task details:** Click a task to view details or delete it
- **Instance deletion:** Deleting a recurring task removes only that occurrence (projection), not the entire task
- **Navigation:** Arrow buttons to move forward/back by 7 days

### Task Actions

**Done:**
- Records actual duration
- Enters task into `completed_tasks` table
- Removes from `projection` table (if recurring)
- If variable recurring: prompts for days until next occurrence
- If errand/appointment/deadline: removes from `tasks` table
- If workout: prompts for sets, reps, weight, intensity per exercise
- Triggers list recalculation

**Defer:**
- Moves to next day
- Increments `deferred_count`
- Triggers list recalculation

**Delete:**
- Removes task entirely
- Does NOT record in completed_tasks (no historical record)
- Triggers list recalculation

### Capacity Warning

When recalculation shows insufficient time for remaining high-priority (score >= 4) tasks:
- Display warning modal
- List at-risk tasks
- User must manually select which to defer
- High-priority tasks are NEVER auto-deferred

### Workout Completion Flow

1. Display workout completion modal with:
   - Exercise dropdown (defaults to algorithm-selected exercise, but user can choose any exercise)
   - Sets, reps, weight (kg) inputs
   - Intensity is auto-set based on algorithm (alternating heavy/light days)
2. User can override the scheduled exercise if they performed a different one
3. Save to `performed_sets` table (records the exercise actually performed, not the scheduled one)

### Variable Recurring Completion

1. Prompt: "When should this task recur?"
2. Input: number of days
3. Create entry in projection table for that date

---

## Notifications

### Push Notifications (via Web Push)
- **Appointments:** 1 day before, 1 hour before
- **Deadlines:** when calculated urgency changes from 1 to 2
- When a task has run over it's allotted time without being completed, deferred or deleted

### In-App
- Capacity warnings
- Task reminders when idle

---

## Data Model

### Task Tables

```sql
CREATE TABLE tasks (
    id TEXT PRIMARY KEY,
    type TEXT NOT NULL,  -- appointment, deadline, recurring, variable_recurring, errand, workout
    title TEXT NOT NULL,
    notes TEXT,
    estimated_duration INT,  -- minutes
    importance INT CHECK (importance BETWEEN 1 AND 3),
    urgency INT CHECK (urgency BETWEEN 1 AND 3),  -- NULL for calculated types
    allow_afternoon BOOLEAN DEFAULT FALSE,  -- can be scheduled in 3pm-6pm window
    deadline_at TIMESTAMP,  -- for deadlines
    scheduled_at TIMESTAMP,  -- for appointments
    prep_duration INT,  -- minutes, for appointments only
    scheduled_time TIME,  -- optional, for recurring tasks that need a fixed daily time
    location TEXT,
    status TEXT DEFAULT 'pending',
    deferred_count INT DEFAULT 0,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
```

### Recurrence Tables

See `recurrence.md` for full details.

```sql
CREATE TABLE recurrence (
    id INTEGER PRIMARY KEY,
    task_id TEXT REFERENCES tasks(id),
    interval_type TEXT NOT NULL,  -- daily, weekly, monthly, yearly
    interval_multiple INT DEFAULT 1,
    day_of_week TEXT,  -- "0,2,4" = Sun, Tue, Thu
    day_of_month TEXT,  -- "1,15" = 1st and 15th
    month_of_year TEXT,  -- "1,6" = Jan and Jun
    start_date DATE,
    end_date DATE,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE projection (
    instance_id INTEGER PRIMARY KEY,
    task_id TEXT REFERENCES tasks(id),
    due_date DATE NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(task_id, due_date)
);
```

Projection table is auto-populated from recurrence rules. Refreshed weekly to maintain several months of future instances.

### Completion History

```sql
CREATE TABLE completed_tasks (
    id INTEGER PRIMARY KEY,
    task_id TEXT,
    completed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    actual_duration INT,  -- minutes
    notes TEXT
);
```

### Workout Tables

See `workout-tables.md` for full details.

```sql
CREATE TABLE muscle_groups (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    recovery_time INT NOT NULL  -- days
);

CREATE TABLE exercises (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    description TEXT
);

CREATE TABLE exercise_muscles (
    exercise_id INT REFERENCES exercises(id),
    muscle_id INT REFERENCES muscle_groups(id),
    PRIMARY KEY (exercise_id, muscle_id)
);

CREATE TABLE performed_sets (
    id INTEGER PRIMARY KEY,
    exercise_id INT REFERENCES exercises(id),
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    reps INT,
    weight_kg DECIMAL,
    num_sets INT,
    intensity TEXT CHECK (intensity IN ('heavy', 'light'))
);
```

### User Settings

```sql
CREATE TABLE users (
    id TEXT PRIMARY KEY,
    email TEXT,
    push_subscription TEXT,  -- JSON for web push
    available_hours_per_day INT DEFAULT 6,
    preferences TEXT,  -- JSON
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
```

---

## Workout Algorithm

See `workout-algorithm.md` for full details.

Summary:
1. Check yesterday's intensity → today alternates (heavy ↔ light)
2. Filter exercise history by applicable intensity
3. History records exercises completed, so create a temporary table and translate history of exercises into a history of muscle groups used
4. For each muscle group: `score = days_since_last_workout - recovery_time` (min 0)
5. For each exercise: `score = product of muscle group scores`
6. Select the exercise with the highest score

---

## Admin Panel

Accessible at `/admin` (authenticated):
- CRUD for all tasks
- Edit recurrence rules
- Manage projection table
- Edit user preferences
- Manage exercises and muscle groups
- View completion history
- View performed_sets history
- **Refresh Projections:** Regenerate all future projections (fixes day-of-week issues)

---

## Future Considerations

- Calendar sync (Google, iCal)
- Natural language task input
- Mobile app (if PWA limitations arise)
- Shared tasks / household mode
- Voice input
- Subtasks / checklists within a task
- Categories/tags for filtering
- possible agentic task completion for certain tasks


