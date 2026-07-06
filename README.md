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

#### HTTPS via `tailscale serve` (required for push notifications on Android)

Plain HTTP is not a secure context, so Android Chrome won't expose `serviceWorker`/`PushManager` at `http://<pi-tailscale-ip>:8888` — the notification bell stays hidden and no subscription can ever be created. `tailscale serve` puts a TLS terminator in front of uvicorn with no code changes needed:

1. In the Tailscale admin console: enable **MagicDNS** and **HTTPS Certificates** (Settings → tailnet features), if not already on.
2. On the Pi: `sudo tailscale serve --bg --https=443 http://localhost:8888` (`--bg` persists across reboots; verify with `tailscale serve status`).
3. On the phone: open `https://<pi-hostname>.<tailnet>.ts.net`, optionally install the PWA, tap the bell, and grant notification permission.

The phone URL changes from `http://<pi-tailscale-ip>:8888` to `https://<pi-hostname>.<tailnet>.ts.net`. Use `POST /push/test` (or the bell's subscribe flow) to confirm delivery once subscribed.

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
│   ├── task_notification.py # TaskNotification (per-task push notification offsets)
│   ├── recurrence.py     # Recurrence, Projection
│   ├── workout.py        # MuscleGroup, Exercise, ExerciseMuscle, PerformedSet
│   ├── push_subscription.py # PushSubscription (Web Push endpoint/keys)
│   └── user.py           # User (settings)
├── schemas/
│   └── task.py           # Pydantic schemas for API request/response
├── routers/
│   ├── tasks.py          # /tasks/* endpoints (main user-facing API)
│   ├── push.py           # /push/* (subscribe/unsubscribe/public-key/test)
│   ├── workouts.py       # /workouts/* (stub - workout flow is in tasks.py)
│   └── admin.py          # /admin/* (CRUD, history, muscle groups, exercises)
├── services/
│   ├── prioritisation.py # Core scheduling algorithm (fixed + flexible tasks, gap filling)
│   ├── scheduling.py     # Window helpers, build_daily_schedule, capacity
│   ├── recurrence.py     # Projection table generation
│   ├── scheduler.py      # APScheduler job: appointment + per-task push notifications
│   ├── push.py           # send_push() — pywebpush wrapper, "ok"/"gone"/"failed"
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
- **Overdue:** Once `scheduled_at` falls before today, it's auto-completed by the overdue sweep (see [Overdue Handling & Undo](#overdue-handling--undo))
- **Notes:** Can be placed in evening window (work shifts) via manual scheduling
- **Notifications:** Can have one or more push notifications attached (see [Per-Task Notifications](#per-task-notifications))

### 2. Deadline
A task with a firm due date and time.

- **Key fields:** `deadline_at` (datetime), `estimated_duration`
- **Urgency:** Calculated dynamically — see Priority Calculation below
- **Scheduling:** Flexible — fitted into gaps by priority
- **Due today:** A deadline due today is pinned to the top of the schedule (red styling, urgency 3) instead of being fitted into a gap — see [Overdue Handling & Undo](#overdue-handling--undo)
- **Completion:** Removed from tasks table; recorded in `completed_tasks`
- **Overdue:** Once the due date is in the past, it's auto-completed by the overdue sweep

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

### Due-Today Deadline Pinning

Deadlines whose `deadline_at` date is today are flagged `due_today=True`, given urgency 3, and pulled out of normal gap-scheduling entirely — they're prepended to the front of the schedule regardless of capacity. If multiple deadlines are due today, they're sorted among themselves by priority score (then deferred count); only the top one becomes the big "current task" focus card, the rest appear red-styled at the top of "Up Next". A due-today deadline stays pinned for the whole day until completed or deferred — see [Overdue Handling & Undo](#overdue-handling--undo).

---

## Views

### Main View (`/`)
- **Current task:** Largest card at top — the highest-priority scheduled task for now
- **Up Next:** Scrollable list of remaining tasks in schedule order
- **Due-today deadlines:** Pinned to the top (red styling) until completed or deferred
- **Add task:** Floating `+` button opens creation modal
- Auto-refreshes on `taskUpdated` HTMX event (fired after complete/defer/delete)
- On load, runs the overdue auto-complete sweep — see [Overdue Handling & Undo](#overdue-handling--undo)

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
- **Completed Tasks** (`/admin/completed-tasks`) — paginated history with date filters; auto-completed (overdue sweep) entries are marked with an "Auto" badge
- **Workout History** (`/admin/workout-history`) — performed sets, editable, grouped by day
- **Refresh Projections** — regenerates all future projection entries (use if recurrence rules change)

---

## Overdue Handling & Undo

Appointments and deadlines used to silently disappear from the live list once their time/date passed, leaving stale `pending` rows behind forever. Two mechanisms now handle this:

### Due-Today Pinning (Deadlines)
A deadline whose `deadline_at` date is today is flagged `due_today=True`:
- Urgency forced to 3, pulled out of gap-scheduling, prepended to the front of the schedule
- Styled red (`.task-card-focus--due-today` / `.task-card-mini--due-today`)
- Stays pinned for the entire day regardless of capacity, until completed or deferred
- If several deadlines are due today, they're sorted among themselves by priority — only the highest becomes the focus card, the rest show as red mini-cards at the top of "Up Next"

### Auto-Complete Sweep (Overdue)
On every load of `/`, `/tasks/current`, `/tasks/upcoming`, or `/tasks/timeline`, `auto_complete_overdue_tasks()` runs:
- **Appointments** with `scheduled_at.date() < today` → auto-completed
- **Deadlines** with `deadline_at.date() < today` (i.e. the day *after* their "due today" red day) → auto-completed
- Errands are untouched (no date field, never silently overdue)

Each swept task is snapshotted into `completed_tasks` (with `auto_completed=True`) and `action_log`, then deleted from `tasks`.

### Toast + Undo
If the sweep removes anything, the response carries an `HX-Trigger: showUndo` header with a combined label (`"'X' auto-completed (overdue)"` or `"{N} tasks auto-completed (overdue)"`) and the list of `action_log` ids. The toast's single Undo button posts to `/undo/{id}` (single) or `/undo/batch/{id1,id2,...}` (multiple).

Undoing an **auto-completed** item gives it a "second chance": its `scheduled_at`/`deadline_at` is bumped to today, so it reappears (pinned/due-today for deadlines) instead of being swept again immediately.

### action_log Table
Generic undo log for complete/defer/edit/delete actions, pruned after 30 minutes:

| Column | Description |
|--------|-------------|
| `action_type` | `complete` \| `defer` \| `edit` \| `delete` |
| `task_id`, `task_title` | Identify the affected task |
| `task_snapshot` | JSON dump of the task row before the action |
| `projections_snapshot` | JSON list of projection due-dates before the action |
| `completed_task_id` | FK to `completed_tasks`, set for `complete` actions |
| `recurrence_snapshot` | JSON dump of the recurrence row, set for `delete` actions |
| `performed_set_id` | FK to `performed_sets`, set for workout completions |
| `performed_at` | Used for the 30-minute pruning cutoff |

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

### Undo (`/undo`)

| Method | Path | Returns | Description |
|--------|------|---------|-------------|
| POST | `/undo/{log_id}` | 200, `HX-Trigger: taskUpdated` | Undo a single `action_log` entry |
| POST | `/undo/batch/{id1,id2,...}` | 200, `HX-Trigger: taskUpdated` | Undo multiple `action_log` entries at once (e.g. an overdue sweep) |

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

The service worker (`app/static/sw.js`) does no asset caching (deliberately stripped — a failing cache install silently blocked activation); it exists for push notifications. Web Push notifications are fully implemented server-side via `pywebpush` + VAPID:

- Subscriptions are stored in the `push_subscriptions` table (one row per device/browser), managed via `POST /push/subscribe` and `DELETE /push/unsubscribe` (see `app/routers/push.py`, `app/static/js/app.js`).
- A 1-minute APScheduler job (`app/services/scheduler.py`) checks for due notifications and sends them via `send_push` (`app/services/push.py`).
- `send_push` returns `"ok"`, `"gone"` (404/410 — dead subscription, deleted automatically), or `"failed"` (retried on the next tick). Delivery is attempted to every stored subscription every tick — one device failing or being pruned never blocks another.
- `POST /push/test` sends a test notification to every stored subscription and reports per-endpoint results — the main tool for debugging delivery on a given device.
- `app/static/sw.js` re-subscribes automatically on `pushsubscriptionchange` (e.g. when Android/FCM rotates the subscription) and focuses an already-open window on notification click instead of always opening a new tab.

Notification triggers:
- **Appointment reminders** — fires once per appointment, `prep_duration` (or the default `notification_lead_minutes`) before `scheduled_at`.
- **Per-task notifications** — see below.

Not yet implemented: deadline urgency escalation, task overrun alerts.

### Per-Task Notifications

Any appointment can have one or more additional push notifications attached, independent of the automatic appointment reminder above:

- Each notification is stored as an **offset in minutes** from the task's `scheduled_at` (`app/models/task_notification.py`, table `task_notifications`) — `0` means "at the scheduled time", `N` means "N minutes before". Storing an offset rather than an absolute time means rescheduling the task automatically moves any notification that hasn't fired yet.
- Configured from the task create/edit form's "Notifications" section — add/remove rows, each either "At scheduled time" or "Minutes before" with a number input.
- `TaskCreate.notification_offsets` / `TaskUpdate.notification_offsets` (`app/schemas/task.py`) drive the API: on create, one row is made per (deduplicated) offset; on update, providing the field replaces all existing rows, while omitting it (`None`) leaves them untouched and `[]` removes them all.
- If an offset's fire time is already in the past at creation/update time, it's stamped `sent_at` immediately so the scheduler doesn't instantly fire it.
- Deleting a task cascades to delete its notifications.

---

## Known Incomplete Features

The following are stubbed or partially implemented:

- **`/workouts` router** — `GET /workouts/today`, `POST /workouts/log`, `GET /workouts/history` all return stubs. The actual workout flow goes through `/tasks/{id}/complete/workout`.
- **`deferred_count` tie-breaking in sort** — `PrioritisedTask.sort_key()` includes `deferred_count` but `get_flexible_tasks` sorts before deferred tasks are separated.
- **Deadline/overdue notification triggers** — only appointment reminders and per-task notifications are implemented; deadline urgency escalation and task-overrun alerts are not.

---

## Tests

```bash
uv run pytest
```

Tests cover the prioritisation service, recurrence logic, workout algorithm, API routes, and push notifications/per-task notifications (239 tests).

---

## Further Documentation

- `docs/design.md` — Full system design specification
- `docs/prioritisation.md` — Scheduling algorithm detail
- `docs/recurrence.md` — Recurrence + projection table design
- `docs/workout-algorithm.md` — Exercise selection algorithm
- `docs/workout-tables.md` — Workout DB schema
- `docs/task_completion.md` — Task completion flow
