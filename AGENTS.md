# Life Organiser — Agent Guide

Personal task management PWA. Read this before making changes.

## Quick Start

```bash
uv sync                                        # create .venv and install deps
uv run uvicorn app.main:app --reload --port 8888  # http://localhost:8888
uv run pytest                                  # run tests
```

Database auto-creates on first run (`organiser.db` in project root). No migrations — SQLAlchemy `create_all` handles schema.

---

## Architecture

```
FastAPI app
│
├── /tasks      → routers/tasks.py       (main user-facing routes)
├── /workouts   → routers/workouts.py    (stubs — real workout flow is in tasks.py)
├── /admin      → routers/admin.py       (CRUD, history, exercise management)
└── /           → main.py               (index page)
```

**Request flow:** Browser → HTMX → FastAPI route → service layer → SQLAlchemy → SQLite. Routes return HTML fragments (Jinja2 templates) for HTMX, or JSON for API endpoints. The `taskUpdated` HTMX event is fired after mutations to trigger list refresh.

**No frontend build step.** HTMX and Alpine.js are loaded from CDN in `base.html`. CSS is hand-written in `app/static/css/style.css`.

---

## Key Files

| File | Purpose |
|------|---------|
| `app/main.py` | App factory, router registration, DB table creation |
| `app/config.py` | All config (window hours, urgency thresholds, DB URL) — reads `.env` |
| `app/database.py` | SQLAlchemy engine + `get_db` dependency |
| `app/models/task.py` | `Task`, `CompletedTask` models |
| `app/models/recurrence.py` | `Recurrence`, `Projection` models |
| `app/models/workout.py` | `MuscleGroup`, `Exercise`, `ExerciseMuscle`, `PerformedSet` |
| `app/models/user.py` | `User` (single-user app, auto-created on first admin visit) |
| `app/schemas/task.py` | Pydantic schemas: `TaskCreate`, `TaskUpdate`, `TaskResponse`, completion schemas |
| `app/services/prioritisation.py` | Core scheduling engine — the most important file |
| `app/services/scheduling.py` | Window helpers, `build_daily_schedule`, `get_remaining_capacity` |
| `app/services/recurrence.py` | `generate_projections`, `refresh_projections` |
| `app/services/workout_algorithm.py` | `select_todays_exercises` (recovery scoring) |
| `app/routers/tasks.py` | All `/tasks/*` routes including workout completion |
| `app/routers/admin.py` | Full CRUD for tasks, exercises, muscle groups, history |
| `app/static/sw.js` | PWA service worker (caching + push handler) |
| `app/static/js/app.js` | Alpine countdown component |

---

## Data Model

### tasks

The central table. `type` drives all behaviour.

```
id              TEXT (UUID)   PK
type            TEXT          appointment | deadline | recurring | variable_recurring | errand | workout
title           TEXT
notes           TEXT
estimated_duration  INT       minutes
importance      INT           1-3
urgency         INT           1-3 | NULL (deadlines: calculated; others: manual)
allow_afternoon BOOL          can be auto-scheduled in 3pm-6pm window
deadline_at     DATETIME      for type=deadline
scheduled_at    DATETIME      for type=appointment (exact time)
prep_duration   INT           minutes; for appointments; triggers auto-prep task creation
scheduled_time  TIME          optional fixed daily time for recurring tasks (makes them "fixed")
location        TEXT
status          TEXT          pending | active | completed | deferred
deferred_count  INT           incremented on defer; used as tie-breaker
manual_scheduled_time DATETIME user-set time from timeline drag-and-drop
```

### recurrence

One row per recurring/variable_recurring/workout task.

```
task_id         TEXT          FK → tasks.id
interval_type   TEXT          daily | weekly | monthly | yearly
interval_multiple INT         e.g. 2 = every 2 weeks
day_of_week     TEXT          "1,3,5" = Mon/Wed/Fri (Sun=0 convention)
day_of_month    TEXT          "1,15" = 1st and 15th
month_of_year   TEXT          "3,9" = Mar and Sep
start_date      DATE
end_date        DATE          NULL = no end
```

### projection

Pre-computed future occurrences of recurring tasks. Generated 90 days ahead.

```
task_id         TEXT          FK → tasks.id
due_date        DATE
UNIQUE(task_id, due_date)
```

Completing a recurring task deletes today's projection row (task persists).  
Deferring moves today's projection to `today + 1`.

### workout tables

```
muscle_groups:   id, name, recovery_time (days)
exercises:       id, name, description, intensity ('heavy'|'light')
exercise_muscles: exercise_id FK, muscle_id FK  (junction)
performed_sets:  id, exercise_id FK, created_at, reps, weight_kg, num_sets, intensity
```

### completed_tasks

Historical log. Written on completion; never modified (except deletion on undo).

```
id, task_id, completed_at, actual_duration (minutes), notes,
task_type, task_title,       -- snapshots, survive task deletion
auto_completed (bool)        -- True if swept by auto_complete_overdue_tasks(), not user-completed
```

### action_log

Generic undo log for complete/defer/edit/delete actions. Pruned after 30 minutes (`_prune_old_logs`).

```
id, action_type ('complete'|'defer'|'edit'|'delete'),
task_id, task_title,
task_snapshot (JSON of task row before the action),
recurrence_snapshot (JSON, delete only),
projections_snapshot (JSON array of due_date strings before the action),
completed_task_id (FK -> completed_tasks.id, complete actions),
performed_set_id (FK -> performed_sets.id, workout completions),
performed_at
```

### users

Single-user. Auto-created on first `/admin/user` visit.

```
id, email, push_subscription (JSON), available_hours_per_day (default 6), preferences (JSON)
```

---

## Scheduling Algorithm (`services/prioritisation.py`)

This is the core of the app. Understand this before touching anything scheduling-related.

### Types

```python
@dataclass
class TimeSlot:
    start: datetime
    end: datetime
    # duration_minutes property

@dataclass  
class PrioritisedTask:
    task: Task
    priority_score: int
    calculated_urgency: int
    recurrence_timescale: RecurrenceTimescale  # YEARLY=4 > MONTHLY=3 > WEEKLY=2 > DAILY=1 > NONE=0
    is_fixed: bool
    scheduled_time: datetime | None       # assigned by algorithm
    selected_exercise: str | None         # workout tasks only
    due_today: bool = False               # deadline due today: pinned to front, styled red
```

### Main entry point

`get_prioritised_tasks_with_metadata(db, target_date, available_hours_per_day=6)` returns `(list[PrioritisedTask], capacity_dict)`. Both this and `get_prioritised_tasks()` delegate to `_schedule_for_date(db, target_date, available_hours_per_day)`.

### Algorithm steps

1. **`get_fixed_tasks(db, date)`** — Appointments on `target_date` + recurring tasks with `scheduled_time` that have a projection for `target_date`. Sorted by `scheduled_time`.

2. **`get_flexible_tasks(db, date)`** — Deadlines + recurring/errand tasks from projection table without `scheduled_at`. Sorted by `sort_key()` descending: `(due_today, priority_score, recurrence_timescale, deferred_count)`.
   - Deadlines with `deadline_date < target_date` are skipped entirely (handled by the auto-complete sweep, not the scheduler).
   - Deadlines with `deadline_date == today` get `due_today=True` and `urgency=3` (forced), regardless of how much buffer time remains.
   - All other deadlines use `calculate_urgency_for_deadline()` as before.

3. **`calculate_gaps(fixed, date, include_afternoon=True)`** — Computes free `TimeSlot` list between fixed tasks within 9am–6pm. If date == today and now > 9am, gaps start from now.

4. **`schedule_tasks_into_timeline(fixed, flexible, date)`**:
   - Starts with all fixed tasks already scheduled
   - Separates flexible tasks into `manual_tasks` (have `manual_scheduled_time` for today) and `auto_tasks`
   - Places manual tasks, updates gaps (splits/removes overlapping gaps)
   - For each auto task: `find_fitting_gap()` → first gap with enough minutes (skips afternoon gaps if `allow_afternoon=False`) → places task at gap start → `split_gap()` trims the gap

5. **`populate_workout_exercises(db, scheduled)`** — Runs `select_todays_exercises(count=1)` and assigns result to all workout tasks in the schedule.

### `_schedule_for_date(db, target_date, available_hours_per_day)`

Wraps steps 1-5. Splits `get_flexible_tasks()` output into `due_today` and `other_flexible`, then builds the final schedule as `due_today + schedule_tasks_into_timeline(fixed, other_flexible, target_date)` — so due-today deadlines always lead the list, ahead of fixed appointments and everything else. Returns `(fixed, flexible, scheduled)`.

### Priority score

`importance × urgency` (both 1-3). Deadline urgency is calculated by `calculate_urgency_for_deadline()`.

### Urgency thresholds (configurable in settings)

```
URGENCY_LOW_THRESHOLD = 7 days buffer → urgency 1
URGENCY_MEDIUM_THRESHOLD = 2 days buffer → urgency 2
< 2 days → urgency 3
< 1 hour → urgency 3 (override)
```

---

## Recurrence System (`services/recurrence.py`)

`generate_projections(db, recurrence, start_date, end_date)` — walks day-by-day and checks each recurrence rule. Returns a list of `Projection` objects.

**Day-of-week encoding note:** The recurrence table uses Sun=0 convention. Python's `weekday()` uses Mon=0. The conversion in `generate_projections` is: `python_weekday = (stored_day - 1) % 7`.

`refresh_projections(db, months_ahead=3)` — regenerates everything from today forward. Called from admin "Refresh Projections" button.

When creating a task via API or admin:
1. Task created and flushed
2. Recurrence row created and flushed
3. Projections generated for 90 days and inserted (with duplicate check)

---

## Workout Algorithm (`services/workout_algorithm.py`)

1. `get_todays_intensity(db)` — checks most recent `PerformedSet.intensity`, returns opposite
2. `get_muscle_group_scores(db, intensity)` — for each muscle group: `max(0, days_since_last_workout_of_intensity - recovery_time)`. Unworked muscles get `recovery_time × 2`.
3. `get_exercise_scores(db, muscle_scores)` — for each exercise: `product of its muscle group scores`
4. `select_todays_exercises(db, count=5)` — sorts exercises by score desc, returns top N

---

## Frontend Patterns

**HTMX:** Most interactions are `hx-get`/`hx-post`/`hx-delete` with `hx-swap="innerHTML"` or `hx-target`. After task mutations, routes return a 200 response with `HX-Trigger: taskUpdated` header. The main page listens for `taskUpdated from:body` to refresh both the current task card and the task list.

**Alpine.js:** Used for the countdown timer on deadline tasks (`x-data="countdown(deadline)"`). The `alpine:init` event handler is in `app.js`.

**Modal pattern:** Two variants. Task creation/editing uses `hx-target="#modal-container"`. Workout and variable recurring completion modals use `hx-target="body" hx-swap="beforeend"` (appended directly to body); they self-remove via `onclick="this.closest('.modal-overlay').remove()"` or `hx-on::after-request`.

**Capacity warning:** Rendered server-side in `task_list.html` — shown when `total_candidate_time > main_available + afternoon_available` (from the `capacity` dict passed by every route that renders this template).

**Admin panel:** Uses standard form POST with redirect (not HTMX). Exception: muscle group create/delete uses HTMX partial swaps.

---

## Task Lifecycle

### Creation
- All types: insert into `tasks`
- Recurring/variable_recurring/workout: also insert into `recurrence`, generate 90 days of `projection`
- Appointment with `prep_duration`: auto-creates a second appointment task "Getting ready for: ..."

### Completion

**`POST /tasks/{id}/complete`** — standard completion for errand/appointment/deadline/recurring
1. Insert into `completed_tasks`
2. Delete today's `projection` row (recurring tasks)
3. Type-specific:
   - `errand | appointment | deadline` → delete the task
   - `recurring` → task persists, projection deleted

**Variable recurring** — uses a separate two-step flow (like workout):
1. Complete button does `GET /tasks/{id}/complete/variable` → returns "When next?" modal (`variable_complete_form.html`)
2. Form submits `POST /tasks/{id}/complete/variable` (form data: `days_until_next`) → logs completion, deletes today's projection, creates new projection at `today + days_until_next`

**Workout** → `GET /tasks/{id}/complete/workout` → modal → `POST /tasks/{id}/complete/workout` for set logging

### Deferral
- Increment `task.deferred_count`
- If there's a projection for today, move it to tomorrow

### Deletion (admin or user)
- Delete projections, recurrence, then the task
- Does NOT write to `completed_tasks`

### Auto-Complete Sweep (Overdue)

`auto_complete_overdue_tasks(db)` (in `routers/tasks.py`) runs at the top of `GET /tasks/`, `/tasks/current`, `/tasks/upcoming`, and `/tasks/timeline`:
- Queries `pending` appointments/deadlines
- Appointments: `scheduled_at.date() < today` → swept
- Deadlines: `deadline_at.date() < today` → swept (deadlines due *today* are handled by due-today pinning instead, see above)
- For each swept task: insert into `completed_tasks` (`auto_completed=True`), insert an `action_log` row (`action_type="complete"`), delete the task
- Returns `[(log_id, title), ...]`; `_auto_complete_trigger(db)` turns this into an `HX-Trigger: showUndo` header (single or combined label) on the response

---

## Undo System (`routers/undo.py`)

Every mutating action (`complete`, `defer`, `edit`, `delete` — including the auto-complete sweep) writes an `action_log` row and returns/triggers `showUndo` with the log id(s). The toast (`base.html`) shows a single Undo button:
- `POST /undo/{log_id}` — undo one entry
- `POST /undo/batch/{id1,id2,...}` — undo multiple entries (used for the overdue sweep's combined toast)

Both delegate to the same per-type handlers:
- `_undo_complete` — restores the task from `task_snapshot` (recreating it if deleted), removes the `completed_tasks`/`performed_sets` rows, restores projections. If `completed_tasks.auto_completed` was `True`, also bumps `scheduled_at`/`deadline_at` to today (a "second chance" so it doesn't get swept again immediately).
- `_undo_defer` — restores `deferred_count`/`snooze_until` and projections from the snapshot
- `_undo_edit` — re-applies the pre-edit `task_snapshot` via `apply_task_dict`
- `_undo_delete` — recreates the task (and recurrence/projections if present)

`action_log` rows are pruned after 30 minutes (`_prune_old_logs`), so undo is only available briefly after the action.

---

## What's Incomplete / TODO

Be aware of these gaps when making changes:

| Area | Status | Details |
|------|--------|---------|
| Push notifications (server-side) | Not implemented | SW handles incoming push events; no server-side sending code exists |
| `/workouts` router | Stub | All three routes return empty/None — real workout flow is in `/tasks/{id}/complete/workout` |
| Deferred count in sort | Minor bug | `get_flexible_tasks` sorts correctly, but manual tasks are separated before sorting, so deferred_count may not fully apply |

---

## Conventions

- **DB session:** Always use `db: Session = Depends(get_db)` in route functions. Never import `SessionLocal` directly in routes.
- **IDs:** Tasks use UUID strings. Workout/recurrence tables use auto-increment integers.
- **Pydantic v2:** Models use `model_dump(exclude_unset=True)` for partial updates.
- **HTML responses:** Use `response_class=HTMLResponse` on routes returning templates. Return `Response(status_code=200, headers={"HX-Trigger": "taskUpdated"})` for mutation endpoints that don't return HTML.
- **Admin routes:** Use form POST + `RedirectResponse(303)` pattern, not HTMX.
- **Template context:** Always include `"request": request` in template context.

---

## Running Tests

```bash
uv run pytest
```

Tests live in `tests/` (4 files: `test_api.py`, `test_prioritisation.py`, `test_recurrence.py`, `test_workout_algorithm.py`). Uses pytest with an in-memory SQLite DB (`conftest.py` provides `db` and `client` fixtures).

---

## Common Tasks

**Add a new task field:**
1. Add column to `app/models/task.py`
2. Add to `TaskCreate`/`TaskUpdate`/`TaskResponse` in `app/schemas/task.py`
3. Update admin task form in `app/routers/admin.py` and `app/templates/admin/task_form.html`
4. Drop and recreate DB (no migrations), or use `ALTER TABLE` manually

**Change scheduling window hours:**
Update `app/config.py` — the settings values propagate everywhere via `settings` import.

**Add a new task type:**
1. Add the type string to `TaskType` enum in `app/schemas/task.py`
2. Handle it in `get_fixed_tasks` and/or `get_flexible_tasks` in `services/prioritisation.py`
3. Handle completion in `POST /tasks/{id}/complete` in `routers/tasks.py`
4. Add UI for type-specific fields in task form templates

**Regenerate projections after changing recurrence rules:**
POST to `/admin/refresh-projections` or visit Admin → Dashboard → Refresh Projections.
