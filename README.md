# Life Organiser

A personal task management PWA that automatically builds a prioritised daily schedule. Designed for use across Linux desktop and Android via the browser.

## Quick Start

```bash
uv sync
uv run uvicorn app.main:app --reload --port 8888
# Open http://localhost:8888
```

`uv sync` creates `.venv` and installs all dependencies on the first run. The SQLite database (`organiser.db`) is created automatically when the server starts.

If you prefer to activate the virtual environment manually:

```bash
uv sync
source .venv/bin/activate
uvicorn app.main:app --reload --port 8888
```

---

## Production Deployment (Raspberry Pi)

The app runs as a systemd service on a Raspberry Pi, accessible remotely via Tailscale.

### Setup

```bash
# On the Pi
mkdir -p ~/Apps && cd ~/Apps
git clone <repo-url> organiser
cd organiser
curl -LsSf https://astral.sh/uv/install.sh | sh
source ~/.local/bin/env
uv sync --no-dev
```

Create `/etc/systemd/system/organiser.service`:

```ini
[Unit]
Description=Life Organiser
After=network.target

[Service]
Type=simple
User=ross
WorkingDirectory=/home/ross/Apps/organiser
ExecStart=/home/ross/.local/bin/uv run uvicorn app.main:app --host 0.0.0.0 --port 8888
Restart=on-failure
RestartSec=5

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now organiser
```

### Remote Access (Tailscale)

```bash
curl -fsSL https://tailscale.com/install.sh | sh
sudo tailscale up
```

Install Tailscale on your phone and sign in with the same account. Access the app at `http://<pi-tailscale-ip>:8888`.

### Auto-deploy

A systemd timer runs `scripts/update.sh` every 60 seconds. It pulls from `origin/main` and restarts the service only when new commits are detected.

Create `/etc/systemd/system/organiser-update.service`:

```ini
[Unit]
Description=Check for and apply Life Organiser updates

[Service]
Type=oneshot
User=ross
ExecStart=/home/ross/Apps/organiser/scripts/update.sh
StandardOutput=append:/var/log/organiser-update.log
StandardError=append:/var/log/organiser-update.log
```

Create `/etc/systemd/system/organiser-update.timer`:

```ini
[Unit]
Description=Poll for Life Organiser updates every minute

[Timer]
OnBootSec=60
OnUnitActiveSec=60

[Install]
WantedBy=timers.target
```

Allow the script to restart the service without a password (`sudo visudo -f /etc/sudoers.d/organiser`):

```
ross ALL=(ALL) NOPASSWD: /bin/systemctl restart organiser
```

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now organiser-update.timer
```

Deploy log: `cat /var/log/organiser-update.log`

---

## Tech Stack

| Layer | Technology |
|-------|-----------|
| Backend | FastAPI (Python) |
| Database | SQLite via SQLAlchemy |
| Templates | Jinja2 |
| Frontend reactivity | HTMX + Alpine.js |
| PWA | Service Worker + Web Push (`pywebpush`) |

---

## Project Structure

```
app/
├── main.py               # FastAPI app, router registration, static files
├── config.py             # Settings (windows, urgency thresholds, DB URL)
├── database.py           # SQLAlchemy engine + session factory
├── models/
│   ├── task.py           # Task, CompletedTask
│   ├── recurrence.py     # Recurrence, Projection
│   ├── workout.py        # MuscleGroup, Exercise, ExerciseMuscle, PerformedSet
│   └── user.py           # User (settings, push subscription)
├── schemas/
│   └── task.py           # Pydantic schemas for API request/response
├── routers/
│   ├── tasks.py          # /tasks/* endpoints (main user-facing API)
│   ├── workouts.py       # /workouts/* (stub - workout flow is in tasks.py)
│   └── admin.py          # /admin/* (CRUD, history, muscle groups, exercises)
├── services/
│   ├── prioritisation.py # Core scheduling algorithm (fixed + flexible tasks, gap filling)
│   ├── scheduling.py     # Window helpers, build_daily_schedule, capacity
│   ├── recurrence.py     # Projection table generation
│   └── workout_algorithm.py # Exercise selection (recovery scoring)
├── templates/
│   ├── base.html         # Base layout (nav, HTMX/Alpine includes)
│   ├── index.html        # Main view (current task + upcoming list)
│   ├── timeline.html     # Day timeline with drag-and-drop
│   ├── week.html         # 7-day calendar view
│   ├── workout.html      # Workout page (stub)
│   ├── components/       # HTMX partial templates
│   └── admin/            # Admin panel templates
└── static/
    ├── css/style.css
    ├── js/app.js         # Alpine countdown component
    ├── sw.js             # Service Worker (caching + push)
    └── manifest.json     # PWA manifest

docs/                     # Design documentation
tests/                    # pytest tests
```

---

## Task Types

The system supports six task types, each with different behaviour:

### 1. Appointment
A fixed-time commitment.

- **Key fields:** `scheduled_at` (datetime), `prep_duration` (minutes), `location`
- **Urgency:** Always 3 (maximum) when scheduled for today
- **Scheduling:** Fixed — placed at exact `scheduled_at` time, other tasks scheduled around it
- **Auto-prep task:** When `prep_duration` is set, a "Getting ready for: ..." task is automatically created at `scheduled_at - prep_duration`
- **Completion:** Removed from tasks table; recorded in `completed_tasks`
- **Notes:** Can be placed in evening window (work shifts) via manual scheduling

### 2. Deadline
A task with a firm due date and time.

- **Key fields:** `deadline_at` (datetime), `estimated_duration`
- **Urgency:** Calculated dynamically — see Priority Calculation below
- **Scheduling:** Flexible — fitted into gaps by priority
- **Completion:** Removed from tasks table; recorded in `completed_tasks`

### 3. Recurring Task
A task that repeats on a schedule.

- **Key fields:** `scheduled_time` (optional fixed daily time), `allow_afternoon`, recurrence rule
- **Urgency:** Manually set at creation (1-3)
- **Scheduling:** Fixed if `scheduled_time` is set; flexible otherwise
- **Recurrence:** Defined in `recurrence` table; instances pre-generated in `projection` table
- **Completion:** Removes today's projection entry; task persists for future occurrences

### 4. Variable Recurring
Like Recurring, but the next occurrence date is chosen by the user at completion time.

- **Completion:** Prompts "days until next occurrence" and creates a single projection entry for that date

### 5. Errand
A one-off task without a deadline.

- **Key fields:** `urgency` (manual 1-3), `allow_afternoon`
- **Scheduling:** Flexible, fitted into gaps by priority score
- **Completion:** Removed from tasks table; recorded in `completed_tasks`

### 6. Workout (Special Recurring)
A recurring task that integrates with the exercise selection algorithm.

- **Recurrence:** Same as Recurring (can recur daily, weekly, etc.)
- **Exercise selection:** When due, the algorithm picks the highest-scoring exercise based on muscle recovery
- **Display:** Shows as "Workout: {exercise name}"
- **Completion flow:** Prompts for exercise (defaults to algorithm pick), sets, reps, weight; records in `performed_sets`
- **Intensity:** Auto-alternates heavy/light based on previous workout

---

## Priority & Scheduling

### Priority Score

`priority_score = importance × urgency` (each 1-3)

| Score | Meaning |
|-------|---------|
| 9 | Critical |
| 6 | High |
| 4 | Medium-high |
| 3 | Medium |
| 2 | Low-medium |
| 1 | Low |

### Urgency Calculation

**Deadlines** — calculated dynamically:
```
buffer = time_remaining - (estimated_hours / available_hours_per_day)
buffer > 7 days  → urgency 1
buffer 2-7 days  → urgency 2
buffer < 2 days  → urgency 3
(< 1 hour remaining → urgency 3 regardless)
```

**Appointments** — urgency 3 always.  
**All other types** — set manually at creation.

### Tie-Breaking (same score)
1. Recurrence timescale: Monthly > Weekly > Daily
2. Deferred count: higher = higher priority

### Scheduling Windows

| Window | Hours | Auto-scheduling |
|--------|-------|-----------------|
| Main | 9am–3pm | All tasks |
| Afternoon | 3pm–6pm | Only `allow_afternoon=True` tasks |
| Evening | 6pm–11pm | Manual-only (excluded from gap calculation) |

### Timeline Algorithm

1. **Fixed tasks** (appointments + time-bound recurring with `scheduled_time`) placed at their exact times
2. **Gaps** calculated between fixed tasks within 9am–6pm
3. **Manually-scheduled flexible tasks** (from drag-and-drop) placed at their user-set times, gaps updated
4. **Remaining flexible tasks** fitted into gaps in priority order — first gap that fits wins
5. Tasks that don't fit any gap are excluded from today's schedule

If the target date is today, gaps start from `now` rather than 9am.

---

## Views

### Main View (`/`)
- **Current task:** Largest card at top — the highest-priority scheduled task for now
- **Up Next:** Scrollable list of remaining tasks in schedule order
- **Add task:** Floating `+` button opens creation modal
- Auto-refreshes on `taskUpdated` HTMX event (fired after complete/defer/delete)

### Timeline View (`/tasks/timeline`)
- Visual day timeline from 9am–11pm
- Colour-coded windows (green/yellow/red)
- Drag flexible tasks to new time slots (15-minute snapping)
- Fixed tasks (📌) cannot be moved
- Save/Cancel buttons appear when changes are made
- Saved positions update `manual_scheduled_time` on the task

### Week View (`/tasks/week`)
- 7-day calendar from today, 6am–11pm grid
- Shows appointments and recurring task projections
- Click a time slot to create a task
- Deleting a recurring task from week view removes only that occurrence (projection), not the whole task
- Forward/back navigation by 7 days

### Admin Panel (`/admin`)
- **Dashboard** — links to all admin sections
- **Tasks** (`/admin/tasks`) — CRUD for all tasks, filterable by type
- **Exercises** (`/admin/exercises`) — manage exercise library
- **Muscle Groups** (`/admin/muscle-groups`) — manage muscle groups + recovery times
- **User Preferences** (`/admin/user`) — email, available hours per day
- **Completed Tasks** (`/admin/completed-tasks`) — paginated history with date filters
- **Workout History** (`/admin/workout-history`) — performed sets, editable, grouped by day
- **Refresh Projections** — regenerates all future projection entries (use if recurrence rules change)

---

## API Reference

All endpoints return HTML fragments (HTMX) unless noted as JSON.

### Tasks (`/tasks`)

| Method | Path | Returns | Description |
|--------|------|---------|-------------|
| GET | `/tasks/` | HTML | Today's prioritised task list |
| GET | `/tasks/current` | HTML | Current (highest priority) task card |
| GET | `/tasks/upcoming` | HTML | Upcoming tasks (all except current) |
| GET | `/tasks/all` | JSON | All tasks in DB |
| GET | `/tasks/new` | HTML | Task creation form modal |
| POST | `/tasks/` | JSON | Create a task |
| GET | `/tasks/timeline` | HTML | Timeline view page |
| POST | `/tasks/timeline/reorder` | JSON | Save drag-and-drop positions |
| GET | `/tasks/week` | HTML | Week view page |
| GET | `/tasks/week?start=&end=` | JSON | Tasks for date range (week view data) |
| DELETE | `/tasks/week/projection/{id}?date=` | JSON | Delete single recurring occurrence |
| GET | `/tasks/{id}` | JSON | Single task |
| PUT | `/tasks/{id}` | JSON | Update task |
| GET | `/tasks/{id}/edit` | HTML | Edit form modal |
| DELETE | `/tasks/{id}` | HTML | Delete task, returns updated list |
| POST | `/tasks/{id}/complete` | HTML (204) | Complete a task |
| GET | `/tasks/{id}/complete/variable` | HTML | "When next?" form modal for variable recurring |
| POST | `/tasks/{id}/complete/variable` | HTML (204) | Complete variable recurring + set next date |
| GET | `/tasks/{id}/complete/workout` | HTML | Workout completion form |
| POST | `/tasks/{id}/complete/workout` | HTML (204) | Save workout completion |
| POST | `/tasks/{id}/defer` | HTML (204) | Defer to tomorrow |

### Create Task — JSON body example

```json
POST /tasks/
{
  "type": "recurring",
  "title": "Morning walk",
  "estimated_duration": 30,
  "importance": 2,
  "urgency": 2,
  "allow_afternoon": false,
  "recurrence": {
    "interval_type": "daily",
    "interval_multiple": 1,
    "start_date": "2026-01-01"
  }
}
```

---

## Configuration

`app/config.py` reads from environment / `.env` file:

| Setting | Default | Description |
|---------|---------|-------------|
| `DATABASE_URL` | `sqlite:///./organiser.db` | SQLAlchemy DB URL |
| `MAIN_WINDOW_START` | `9` | Main window start hour |
| `MAIN_WINDOW_END` | `15` | Main window end hour |
| `AFTERNOON_WINDOW_START` | `15` | Afternoon window start hour |
| `AFTERNOON_WINDOW_END` | `18` | Afternoon window end hour |
| `EVENING_WINDOW_START` | `18` | Evening window start hour |
| `EVENING_WINDOW_END` | `23` | Evening window end hour |
| `URGENCY_LOW_THRESHOLD` | `7` | Buffer days for urgency=1 |
| `URGENCY_MEDIUM_THRESHOLD` | `2` | Buffer days for urgency=2 |

---

## Workout System

### Setup
1. Go to `/admin/muscle-groups` and add muscle groups (e.g. Chest, Back, Legs) with their recovery times in days
2. Go to `/admin/exercises` and add exercises, assigning intensity (heavy/light) and the muscle groups they work
3. Create a Workout task type with a recurrence rule

### Algorithm
1. Check the most recent `performed_sets` entry to determine last intensity → today alternates
2. For each muscle group: `score = days_since_last_workout - recovery_time` (min 0; uses today's intensity)
3. For each exercise: `score = product of its muscle group scores`
4. Select the exercise with the highest score (unworked muscles score highest)

### Completion
When completing a workout task:
- A form shows the algorithm-selected exercise and today's intensity
- User can override the exercise choice
- Sets, reps, weight recorded in `performed_sets`

---

## Recurrence System

Recurrence rules live in the `recurrence` table. Pre-computed occurrences live in `projection`.

### Recurrence Fields

| Field | Format | Example |
|-------|--------|---------|
| `interval_type` | `daily\|weekly\|monthly\|yearly` | `weekly` |
| `interval_multiple` | integer | `2` (every 2 weeks) |
| `day_of_week` | comma-separated 0-6 (Sun=0) | `1,3,5` (Mon/Wed/Fri) |
| `day_of_month` | comma-separated 1-31 | `1,15` |
| `month_of_year` | comma-separated 1-12 | `3,9` |
| `start_date` | date | `2026-01-01` |
| `end_date` | date or null | null = no end |

### Projection Table
- Generated 90 days ahead when a task is created
- `UNIQUE(task_id, due_date)` prevents duplicates
- Completing a recurring task deletes today's projection row (the task remains for future dates)
- Deferring a recurring task moves today's projection to tomorrow
- Admin "Refresh Projections" wipes future rows and regenerates from scratch

---

## PWA

The service worker (`app/static/sw.js`) uses network-first with cache fallback. Web Push is wired up via `pywebpush` — the push subscription is stored in `users.push_subscription` (JSON).

Planned notification triggers (partially implemented):
- Appointment reminders (1 day and 1 hour before)
- Deadline urgency escalation
- Task overrun (task not completed past its estimated duration)

---

## Known Incomplete Features

The following are stubbed or partially implemented:

- **`/workouts` router** — `GET /workouts/today`, `POST /workouts/log`, `GET /workouts/history` all return stubs. The actual workout flow goes through `/tasks/{id}/complete/workout`.
- **Push notifications** — service worker handles incoming pushes, but the server-side sending logic isn't implemented.
- **`deferred_count` tie-breaking in sort** — `PrioritisedTask.sort_key()` includes `deferred_count` but `get_flexible_tasks` sorts before deferred tasks are separated.

---

## Tests

```bash
uv run pytest
```

Tests cover the prioritisation service, recurrence logic, workout algorithm, and API routes (119 tests).

---

## Further Documentation

- `docs/design.md` — Full system design specification
- `docs/prioritisation.md` — Scheduling algorithm detail
- `docs/recurrence.md` — Recurrence + projection table design
- `docs/workout-algorithm.md` — Exercise selection algorithm
- `docs/workout-tables.md` — Workout DB schema
- `docs/task_completion.md` — Task completion flow
