# Theme A: Scheduling / Priority Overhaul — Design Proposal

**Status:** AGREED 2026-07-10 — all open decisions resolved with Ross (see "Decisions" at the end); implementation in progress on branch `theme-a`
**Drafted:** 2026-07-10
**Scope (from roadmap item 2, agreed 2026-07-07):** priority-first scheduling (9s/6s/4s laddering), VRT urgency escalating with overdue-ness, one-off-list-length priority boost, everything-time-bound (one-offs → 1-year deadlines + 6-month prompt), auto-generated prep task at 75% of a deadline's life. Designed as one unit; do not implement piecemeal.

---

## 1. Problems with the current algorithm

The current pipeline (`app/services/prioritisation.py`, documented in `docs/prioritisation.md`):

1. Fixed tasks (appointments, time-bound recurring) define the day's skeleton.
2. Gaps between them are computed inside the 9:00–18:00 windows.
3. Flexible tasks are sorted by `importance × urgency` and **first-fit** into gaps.

Concrete failure modes Theme A targets:

- **Appointments-first crowding.** A score-9 flexible task that doesn't fit any remaining gap is silently dropped (`find_fitting_gap` returns None → `continue`). A day dense with low-importance fixed blocks can show 4s and 2s (which happened to fit) while a 9 is invisible. Only due-today deadlines are exempt (pinning).
- **Overdue VRTs vanish.** The daily list matches projections with `due_date == target_date` exactly. A variable-recurring task not completed on its day keeps its single past-dated projection and **never appears again** — the opposite of what overdue-ness should do. (Recurring tasks self-heal because future projections exist; VRTs have exactly one completion-driven projection.)
- **VRT urgency is static.** `task.urgency or 1` at creation, forever. A VRT 3 weeks overdue scores the same as one due today.
- **One-offs rot.** Errands have no date, so a low-scoring errand can sit pending for a year with nothing pushing it up.
- **Big deadlines get no early signal.** A deadline 3 months out sits at urgency 1 until the buffer maths flips it — often too late to *start* comfortably; there is no "begin prep" nudge.

## 2. Architecture: a single effective-urgency pipeline

All five components slot into one pipeline, replacing today's scattered urgency logic:

```
base urgency (user-set or type-derived)
  → type-specific escalation
      deadline: buffer maths (existing) ....................... unchanged
      VRT: overdue escalation ................................. component A2
      errand: deadline-derived buffer maths (auto-deadline) .... component A4
              + backlog boost ................................. component A3
  → effective_urgency (1–3, clamped)
  → priority_score = importance × effective_urgency
  → banded, displacement-based scheduling ..................... component A1
```

Everything lives in `prioritisation.py`; no new services. New knobs go in `Settings` with sane defaults.

## 3. Component designs

### A1. Priority-first scheduling ("9s/6s/4s laddering")

Appointments are physically immovable, so "priority-first" cannot mean scheduling a 9 *over* an appointment. What it can and should mean:

**Guarantee: no task ever appears on the day while a strictly higher-scoring task is silently absent.**

Mechanism — replace the single first-fit pass with **banded allocation with displacement**:

1. Compute gaps as today (fixed tasks still block out their times).
2. Process flexible tasks in descending score bands (9, 6, 4, 3, 2, 1; ties broken by the existing timescale/deferred-count key).
3. Within a band, first-fit into gaps as now.
4. **Displacement:** if a task in band B fits no remaining gap, look for placed tasks from strictly lower bands whose eviction would free a fitting slot (evict lowest band first, then shortest-fit). Evict, place, re-fit evictees afterwards if space remains.
5. **Overflow shelf:** a 9 or 6 that *still* doesn't fit (day genuinely full of fixed + higher/equal tasks) is not dropped — it's appended to a visible "didn't fit today" section at the bottom of the list, so high scores are never invisible. (Due-today deadline pinning stays exactly as is, above all of this.)

Non-strict laddering: after all bands are processed, leftover slivers of gap may still hold lower-band tasks — we don't waste time slots to honour band order. The *guarantee* is about presence, not about a 6 never starting earlier in the day than a 9.

Implementation note: this is a change inside `schedule_tasks_into_timeline` plus a new `unplaced` return channel; manual-scheduled-time handling and due-today pinning are untouched.

### A2. VRT urgency escalates with overdue-ness

Two changes:

1. **Carry-forward:** the flexible-task query includes VRT projections with `due_date <= target_date` (not just `==`), so an overdue VRT stays on the list every day until completed. (Recurring/workout keep exact-match; their future projections already exist, and showing yesterday's missed instance alongside today's would double them. If Ross wants missed *recurring* instances surfaced too, that's a separate decision — default: no.)
2. **Escalation:** effective urgency grows with overdue-ness *relative to the task's own cadence*, so "3 days late" means more for a weekly task than a quarterly one:

```
interval_days = days between the last completion and the projection due date
                (fallback: recurrence interval, else 30)
overdue_ratio = max(0, target_date - due_date) / interval_days

effective_urgency = base                      if ratio == 0
                    max(base, 2)              if ratio < 0.5
                    3                         if ratio >= 0.5
```

So a monthly-ish VRT hits urgency 3 after ~2 weeks overdue; a weekly one after ~3–4 days. Simple, cadence-aware, no new columns (last completion comes from `completed_tasks`; due date from the projection).

### A3. One-off backlog priority boost

Pressure valve for a growing errand list. When the pending-errand count exceeds a threshold, the **oldest** errands get an urgency bump so the scheduler starts draining the backlog oldest-first:

```
N = count of pending errands
if N > errand_backlog_soft (default 8):   oldest ⌈N/3⌉ errands get +1 urgency
if N > errand_backlog_hard (default 15):  oldest ⌈N/3⌉ get +2 (clamped to 3), next ⌈N/3⌉ get +1
```

Age = `created_at`. The boost is computed at list-build time, never stored — the visible urgency in edit forms remains the user's own setting. Interacts with A4: the auto-deadline handles the long tail (a year out), the backlog boost handles medium-term pile-up; both feed the same clamped effective urgency, take the max rather than stacking.

### A4. Everything time-bound (one-offs → auto-deadlines)

Every errand gets a deadline; nothing floats forever.

- **Creation:** new errands automatically get `deadline_at = created_at + 365 days`. The existing `deadline_at` column is reused; a new boolean `deadline_auto` (default False, True for auto-set ones) distinguishes auto from user-chosen. Errands remain type `errand` — no conversion; the scheduler simply starts feeding errands through the same buffer-based urgency maths as deadlines (using `deadline_at`), replacing today's static `task.urgency or 1`. `max(buffer_urgency, backlog_boost_urgency)` wins.
- **Half-life prompt:** when an errand with `deadline_auto=True` passes 50% of its creation→deadline span (i.e. ~6 months), the daily view shows a nudge: "When will you actually do this?" with a date picker. Choosing a date sets `deadline_at` (and `deadline_auto=False` — now a real commitment, eligible for due-today pinning and the overdue sweep). A "keep floating" option pushes the auto-deadline out another 6 months, so the prompt is snoozable but the task never becomes dateless again.
- **Sweep behaviour:** errands whose *auto* deadline expires are NOT auto-completed by the overdue sweep (that would silently bin things the user never dated). Auto-deadline expiry re-triggers the prompt at urgency 3 instead. User-confirmed errand deadlines behave exactly like deadline tasks: due-today pinning, then sweep.
- **Migration:** existing errands get `deadline_at = max(created_at + 365d, today + 30d)` and `deadline_auto=True`, so nothing appears instantly urgent but old stock starts moving.
- **UI:** prompt surface is a dismissible banner on the daily view listing due-for-prompt errands (max ~3 at a time), each with inline date picker / "+6 months" buttons. No new page.

### A5. Auto-generated prep task at 75% of a deadline's life

For deadline tasks with a long enough runway, generate a prep nudge task at 75% of the creation→deadline span:

- **Eligibility:** deadlines with span ≥ 14 days (below that, buffer urgency already covers it).
- **What's generated:** a real `Task` row — type `deadline`, title `Prep: {parent title}`, `deadline_at` = creation + 0.75 × span, importance inherited from parent, `estimated_duration` = 25% of parent's estimate (min 15). Real row ⇒ complete/defer/snooze/undo all work for free.
- **Linkage:** new nullable column `tasks.generated_from_task_id`. Completing or deleting the parent deletes its pending generated prep task; completing the prep task does nothing to the parent. (This column is deliberately generic — tag-recipe auto-tasks in the later tags-automation feature will reuse it.)
- **When generated:** eagerly at deadline creation (and re-derived on deadline-date edits: pending prep task's date is recomputed; completed ones left alone). No background scheduler needed.
- **Not sub-tasks:** this is a sibling task with a pointer, not a parent/child hierarchy — deliberately so, since sub-tasks are a post-A design with the scheduler needing direct structure access. `generated_from_task_id` doesn't preclude that design.

## 4. Interactions & ordering of implementation

Single unit, but internally sequenced so the suite stays green at each step:

1. **Effective-urgency refactor** — extract urgency computation into one function per type; behaviour identical. Pure refactor + tests.
2. **A2** (VRT carry-forward + escalation) — most valuable bug-adjacent fix.
3. **A4** (auto-deadlines + migration + prompt) — establishes "everything has a date".
4. **A3** (backlog boost) — small once A4's plumbing exists.
5. **A1** (banded displacement scheduling + overflow shelf) — biggest algorithm change, benefits from the richer urgencies already flowing.
6. **A5** (prep tasks + `generated_from_task_id` migration).

New settings: `errand_backlog_soft=8`, `errand_backlog_hard=15`, `errand_auto_deadline_days=365`, `errand_prompt_fraction=0.5`, `prep_task_fraction=0.75`, `prep_task_min_span_days=14`, `vrt_escalation_half_ratio=0.5`.

Schema changes: `tasks.deadline_auto` (bool), `tasks.generated_from_task_id` (nullable FK-ish string), both via the existing lightweight migration pattern. No changes to recurrence/projection tables.

## 5. Decisions (resolved with Ross, 2026-07-10)

1. **A1 overflow:** "didn't fit today" section at the bottom of the daily list — high scorers are never invisible, timeline stays honest. (Not pinned to top; that stays reserved for due-today deadlines.)
2. **A2 scope:** VRTs only. Plain recurring tasks do NOT carry forward when missed — their rhythm self-corrects.
3. **A4 prompt:** the half-life prompt offers a date picker OR a one-tap "+6 months" snooze. 1-year auto-deadline / 6-month prompt horizons confirmed.
4. **A5 shape:** a single prep task at 75% of the deadline's life; no second earlier one.
5. **A1 eviction:** manual drag-and-drop placements are sacred — displacement only ever evicts auto-scheduled lower-priority tasks.
