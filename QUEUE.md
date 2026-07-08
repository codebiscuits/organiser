# Work Queue

Items move through: **idea → ready → in-progress → done**

- **idea** — added by Ross, not yet discussed or refined
- **ready** — discussed, acceptance criteria agreed, safe to implement autonomously
- **in-progress** — Claude is currently working on it
- **done** — implemented; moved to archive below

Add new items at the top of the relevant section. Use the template below as a guide — rough is fine for `idea`, but `ready` items need the full template filled in.

---

## Template

```
### [Title]
**Status:** idea | ready | in-progress | done
**Added:** YYYY-MM-DD

**Description:**
What you want and why.

**Acceptance criteria:**
- [ ] ...

**Notes / constraints:**
Any gotchas, design decisions made during discussion, or things to watch out for.
```

---

## Roadmap (agreed 2026-07-07)

Synthesised from Ross's notes files (`00 Main Notes.md`, `Big Features and Updates.md`, `Bugs and Small Features.md`) — QUEUE.md is now the canonical source; the note files can be archived.

1. **Session 1 — quick wins**: the seven `ready` items below.
2. **Design session — Theme A: scheduling/priority overhaul**. Must be designed as one unit: priority-first scheduling (9s/6s/4s laddering instead of appointments-first), VRT urgency escalating with overdue-ness, one-off-list-length priority boost, everything-time-bound (one-offs become 1-year deadlines + 6-month "when will you do this?" prompt), auto-generated prep task at 75% of a deadline's life.
3. **Post-A implementation**: Theme A itself → ordering constraints ("not within X min of a meal", before/after relations, preferred time-of-day, clump-vs-spread small tasks) → future-tasks sidebar (drag into today, filter by type/duration) → sub-tasks design (in-core, NOT a companion app — scheduler needs direct access to parent/child structure).
4. **Anytime slots** (independent of A): prep/travel split, tags-automation v1, pomodoro widget, hourly capacity check.
5. **Companion-app track**: companion API first (create/complete task, query today's list, webhook out), then interval timer as first companion app. Meal planning (own design session) and AI delegation via Hermes kanban (deliberately last) ride the same API.

**Dropped (2026-07-07):** workout projection table + manual add button (workouts stay plain recurring tasks); gamification scores/progress bars (live list recalculates too often to quantify progress meaningfully); weekly-view rework (Ross re-checked: looks fine now); "deleted tasks shouldn't appear in history" (verified already solved: soft-deleting a projection writes nothing to `completed_tasks`; `completed_tasks` has no FK so hard deletion preserves history).

---

## Ideas

### Small bugs surfaced by the quick-wins review (not fixed, low priority)
**Status:** idea
**Added:** 2026-07-08

**Description:**
Two leftovers from the review fix round, both pre-existing on main:
- `admin.py:155,257` still anchor projections at `start = recurrence.start_date or date.today()` — same latent past-start_date bug as review finding 9 (fixed in `tasks.py` via `_build_recurrence_and_projections`; admin's create/update paths could reuse it).
- Editing an appointment's `prep_duration` never creates/updates the prep task (prep-task creation is create-only). Noted in the review's refuted section as a real pre-existing gap.

### Hourly capacity check
**Status:** idea (discussed 2026-07-07, near-ready)
**Added:** 2026-07-07

**Description:**
Every hour during the day, check whether the remaining schedulable time (now until `evening_window_end`, 23:00, using the existing three-window config) is enough to complete all pending priority ≥4 tasks. If not, warn via **both** push notification and in-app banner.

### Prep/travel split for appointments
**Status:** idea (discussed 2026-07-07, near-ready)
**Added:** 2026-07-07

**Description:**
Replace single `prep_duration` with separate prep and travel durations; generate two distinct blocks before the appointment so both are visible in the list. Migration: existing appointments get `travel = 0`, prep keeps current value; Ross adjusts the few that matter by hand.

### Tags automation v1 — tag recipes
**Status:** idea (discussed 2026-07-07)
**Added:** 2026-07-07

**Description:**
User-defined "tag recipes": a tag carries a list of auto-generated tasks with day offsets (`#presentable` → "get haircut" 3 days before, "prepare outfit" 1 day before; `#travelling` → fuel/oil/tyres check). Same machinery pointed at *completion* handles follow-up chains (food-prep → fridge → 24h → freezer for resistant starch). Creation form nudges the user to tag untagged tasks. No LLM suggestions in v1 (later phase). Also from old notes, same theme: tag-driven bundling (group housework), per-week category limits, redundancy discard ("hoover downstairs" auto-dropped when "hoover whole house" is due).

### Pomodoro widget
**Status:** idea (discussed 2026-07-07)
**Added:** 2026-07-07

**Description:**
Countdown timer off to the side of the UI, independent of tasks. Set by drag/swipe, typed input, or preset buttons (25m, 55m…). In-app widget, not a companion app.

### Companion API + interval timer
**Status:** idea (discussed 2026-07-07)
**Added:** 2026-07-07

**Description:**
Small stable API on the organiser (create task, complete task, query today's list, webhook out) to support tightly-integrated companion apps. First consumer: standalone workout interval timer app — set count + total time auto-divided, nested repetitions for true supersets. Later consumers: meal planning, AI delegation via Hermes kanban. (Adaptive-intensity-via-pulse-feedback idea: parked indefinitely, moonshot.)

### Theme A: scheduling/priority overhaul
**Status:** idea — needs dedicated design session
**Added:** 2026-07-07

**Description:**
See Roadmap item 2 for the full component list. Do not implement piecemeal.

### Post-A: ordering constraints, future-tasks sidebar, sub-tasks
**Status:** idea — blocked on Theme A
**Added:** 2026-07-07

**Description:**
See Roadmap item 3.

### Meal planning
**Status:** idea — needs dedicated design session, after Theme A
**Added:** 2026-07-07

**Description:**
Recipes with ingredient/prep/cook metadata → weekly meal pick → generated shopping list (tick off what's in the cupboard) → prep time auto-inserted into task list. Candidate companion app.

### AI delegation
**Status:** idea — deliberately last
**Added:** 2026-07-07

**Description:**
'Delegate' button on a task sends it to an agent — most likely the Hermes kanban board via the companion API rather than a built-in agent.

---

## Ready

(nothing ready — next up is the Theme A design session with Ross)

---

## In Progress

### Week view: today's column should mirror the live list
**Status:** in-progress (started 2026-07-08)
**Added:** 2026-07-07

**Description:**
The week view builds purely from the projection table (verified 2026-07-07), so today's column omits live-list-only tasks (errands, deadlines picked by prioritisation). Make the first day reflect the actual live list.

**Acceptance criteria:**
- [ ] Today's column in the week view shows the same task set as the daily live list
- [ ] Remaining days unchanged (still projection-based)

---

## Done

### Quick-wins review fixes
**Status:** done (2026-07-08)
**Added:** 2026-07-07

**Description:**
All 10 findings from `docs/review-quick-wins-findings.md` fixed on `quick-wins` (commits `42a01f1`…`d758c27`, Sonnet implementation + Fable re-review): type-change now preserves snooze/defer/manual-time state and invalidates stale undo logs; admin task list refreshes via the `taskUpdated from:body` pattern (shared action-button macros extracted); VRT completion clears a conflicting tombstone (decision: explicit user-chosen next date overrides a prior single-occurrence delete); undo of a full task delete restores tombstones (new `exclusions_snapshot` column + migration); admin type-change edit no longer leaks orphan tombstones; week-view occurrence delete now has an undo toast; duplicate-title check is Unicode-aware; projection windows anchor at `max(today, start_date)` so past start dates still produce live projections (shared `_build_recurrence_and_projections` helper, ×5 sites); plus the listed helper extractions. 16 regression tests added; suite 269 passing. NOT merged/pushed — Ross merges.

### Session 1 quick wins (batch)
**Status:** done
**Added:** 2026-07-07

**Description:**
The seven `ready` items from the 2026-07-07 roadmap, done in one batch on branch `quick-wins`:
- **Favicon** — `<link rel="icon">` (192/512) + apple-touch-icon added to `base.html`, reusing existing `app/static/icons/`.
- **Pre-fill start dates** — both recurrence `start_date` inputs (and the admin task form's) now default to today via `todayIsoDate()`, still editable.
- **Day-of-week next to date pickers** — every date/datetime-local input in `_task_form_fields.html` now shows a live weekday label (`weekdayShort()` in `app.js`), styled as subtle grey text via `.date-input-row`/`.date-weekday`.
- **Duplicate-title warning** — new `GET /tasks/check-title?title=&exclude_id=` endpoint (registered before the `/{task_id}` catch-all), queried on title-field blur from all three task forms (create modal, edit modal, admin form); shows a non-blocking inline warning, never blocks submission.
- **Task-card action buttons** — `admin/tasks.html` (the all-tasks registry view) was missing Done/Defer; added, reusing the exact endpoints/branching the daily list uses (`/complete`, `/complete/variable`, `/complete/workout`, `/defer`), adapted so a card only disappears when the backend actually deletes that task type.
- **Projection resurrection bug (investigation → fixed)** — confirmed real: `generate_projections`'s "skip if already exists" check can't distinguish "never generated" from "deliberately deleted," so a hard-deleted projection (`DELETE /tasks/week/projection/{id}`, or "delete today only" from the recurring-delete modal) got resurrected by the next `refresh_projections` run or admin regenerate-all. Fixed with a `projection_exclusion` tombstone table consulted inside `generate_projections` itself (so every call site — create, admin create/update, refresh — is covered from one place); both delete routes now record a tombstone, and undoing either delete clears it again. No "individually edited instance" feature exists yet in the codebase, so that half of the original ask was moot. Tests in `test_recurrence.py`/`test_api.py`.
- **Task-type edit bug (investigation → fixed)** — confirmed real: `PUT /tasks/{id}` used to do `task.type = new_type` in place without touching the old type's Recurrence/Projection rows or creating what the new type needed. Depending on direction this either left a stale projection causing the task to render twice (leaving a recurring type), or created a Recurrence with zero Projections so the task never appeared anywhere (entering a recurring type) — matching Ross's "silently fails, duplicate or no-op, unclear which" report exactly. Fixed per the agreed protocol in `_replace_task_for_type_change`: validate the new type's required field up front (422 + untouched original task if invalid), then create a fully-formed replacement task (proper recurrence/projections/tags/notifications) and only then delete the old one. Known, deliberate limitation: a type-change edit doesn't support undo (a generic column-restore can't safely reinstate a deleted task's id/recurrence/projections), so no undo toast is shown for this specific edit — documented in the function's docstring and covered by a test. The admin panel's separate edit route (`admin.py::admin_task_update`) has the same underlying issue but was left alone — the task explicitly named the `tasks.py` update route as the fix target.

All 7 items covered by tests; full suite green (253 passed) at the end of the batch.

### Per-task notifications + push delivery fixes
**Status:** done
**Added:** 2026-07-06

**Description:**
Optional per-task notifications: attach one or more to any appointment, each firing at the scheduled time or N minutes before (stored as offsets in `task_notifications`, so rescheduling moves them). Also hardened delivery: dead subscriptions auto-pruned, `pushsubscriptionchange` re-subscribe, `POST /push/test` debug endpoint, all devices attempted per send. Delivery to Android confirmed working 2026-07-06 — required HTTPS via `tailscale serve` and converting the PEM-formatted VAPID private key in `.env` to raw base64 (see README).

### Push notifications
**Status:** done
**Added:** 2026-06-25

**Description:**
Background push notifications on Android for appointments. Bell icon in header to subscribe/unsubscribe. Notification fires at `prep_duration` minutes before the appointment (or 30 min default). VAPID keys stored in `.env`.

### Task tagging and categorisation
**Status:** done
**Added:** 2026-06-23

### Recurring task delete modal
**Status:** done
**Added:** 2026-06-23

**Description:**
When deleting a recurring/variable_recurring/workout task from the daily view, show a modal with two choices: remove just today's projection row (task persists and continues on future days), or delete the entire task entity (current behaviour). Both actions produce an undo toast.
