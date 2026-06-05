# Test Suite Expansion Plan

## Context

The Life Organiser PWA has 4 existing test files with solid coverage of basic CRUD, recurrence generation, prioritisation scoring, and workout selection. The gaps are in scheduling algorithm edge cases, recurrence corner cases, snooze/capacity logic, preset CRUD, admin endpoints, task update flows, and scenario-level integration tests. This plan adds ~45 new test cases across the existing 3 test files.

## Files to Modify

- `tests/test_prioritisation.py` — scheduling edge cases, snooze filtering, capacity calculation, helper functions
- `tests/test_recurrence.py` — monthly/yearly edge cases, parse_day_list, biweekly+explicit days
- `tests/test_api.py` — preset CRUD, admin endpoints, task updates, lifecycle edge cases, workout admin, scenario tests

No new files needed. All tests use the existing `db` and `client` fixtures from `conftest.py`.

## Conventions

- **FUTURE_DATE**: `date(2099, 1, 15)` for deterministic gap calculations (avoids the "today" branch in `calculate_gaps`)
- **Mocked tasks**: Unit tests in `test_prioritisation.py` use `MagicMock(spec=Task)` for task objects
- **Admin endpoints**: Use `data={...}` (form data), expect `303` redirects, verify DB state directly
- **API endpoints**: Use `json={...}`, expect `200` or `422`/`404`

---

## Phase 1: Scheduling Algorithm Edge Cases

**File: `tests/test_prioritisation.py`**

New imports needed: `get_remaining_capacity`, `build_daily_schedule`, `add_minutes_to_time`, `determine_window` from `app.services.scheduling`.

### Class: `TestCalculateGapsOverlapping`

| Test | Setup | Assert |
|------|-------|--------|
| `test_overlapping_fixed_tasks_handled_without_crash` | Two fixed tasks both at hour 11, 60 min each | 2 gaps: [9:00-11:00, 12:00-18:00]. Cursor logic in `calculate_gaps` merges overlaps. |
| `test_partially_overlapping_fixed_tasks` | Fixed at 10:00 (90 min), fixed at 11:00 (60 min). Overlap from 11:00-11:30. | Gaps: [9:00-10:00, 12:00-18:00]. |

### Class: `TestScheduleFullDay`

| Test | Setup | Assert |
|------|-------|--------|
| `test_full_day_no_flexible_tasks_scheduled` | Fixed tasks covering 9:00-18:00 entirely. One flexible task (60 min). | Result contains only fixed tasks. Flexible is not in the list (code uses `continue` when no gap fits). |

### Class: `TestMultipleManualScheduled`

| Test | Setup | Assert |
|------|-------|--------|
| `test_two_manual_scheduled_tasks_both_placed` | Two flexible tasks with `manual_scheduled_time` at 10:00 and 13:00. One auto-scheduled task. | Both manual tasks at their specified times. Auto task in a remaining gap. Total 3 scheduled. |
| `test_manual_task_overlapping_fixed_task_both_placed` | Fixed at 10:00 (60 min). Flexible with `manual_scheduled_time` at 10:30. | Both appear in result. Manual task uses its time regardless of overlap (algorithm doesn't validate this). |

### Class: `TestTaskExactlyFillingGap`

| Test | Setup | Assert |
|------|-------|--------|
| `test_exact_fit_leaves_no_remaining_gap` | Fixed at 11:00 (60 min) and 14:00 (60 min). Gap 12:00-14:00 = 120 min. Flexible task 120 min. | Flexible scheduled at 12:00. |

### Class: `TestMultipleGapsSomeTooSmall`

| Test | Setup | Assert |
|------|-------|--------|
| `test_skips_small_gaps_uses_first_fitting` | Fixed tasks creating gaps: [9:00-9:30, 10:00-10:30, 11:00-12:00, 13:00-18:00]. Flexible needs 60 min. | Flexible scheduled at 11:00 (first gap >= 60 min). |

### Class: `TestPrepDurationInFlexibleScheduling`

| Test | Setup | Assert |
|------|-------|--------|
| `test_flexible_task_with_prep_duration_consumes_extra_time` | No fixed tasks. Flex A: `estimated_duration=30, prep_duration=30` (total 60 min, higher priority). Flex B: `estimated_duration=30, prep_duration=0`. | A at 9:00, B at 10:00 (not 9:30). |

### Class: `TestAllowAfternoonInteraction`

| Test | Setup | Assert |
|------|-------|--------|
| `test_afternoon_only_gaps_task_not_allowed` | Fixed tasks fill 9:00-15:00. Only gap is 15:00-18:00. Flexible with `allow_afternoon=False`. | Flexible not scheduled. |
| `test_afternoon_only_gaps_task_allowed` | Same setup, `allow_afternoon=True`. | Flexible scheduled at 15:00. |

### Class: `TestSchedulingHelpers`

| Test | Input | Expected |
|------|-------|----------|
| `test_add_minutes_to_time_basic` | `add_minutes_to_time(time(9, 0), 90)` | `time(10, 30)` |
| `test_add_minutes_to_time_crosses_hour` | `add_minutes_to_time(time(14, 45), 30)` | `time(15, 15)` |
| `test_determine_window_main` | `determine_window(datetime(2099, 1, 15, 10, 0))` | `"main"` |
| `test_determine_window_afternoon` | `determine_window(datetime(2099, 1, 15, 16, 0))` | `"afternoon"` |
| `test_determine_window_evening` | `determine_window(datetime(2099, 1, 15, 20, 0))` | `"evening"` |
| `test_determine_window_outside_returns_main` | `determine_window(datetime(2099, 1, 15, 7, 0))` | `"main"` |

---

## Phase 2: Recurrence Edge Cases

**File: `tests/test_recurrence.py`**

### Class: `TestGenerateProjectionsMonthlyEdgeCases`

| Test | Setup | Assert |
|------|-------|--------|
| `test_monthly_day_31_skips_short_months` | `make_recurrence(interval_type="monthly", day_of_month="31")`, range Jan-Jun 2026 | Projections only for Jan 31, Mar 31, May 31. `len == 3`. Feb/Apr/Jun have no 31st day so the iterator naturally skips them. |
| `test_monthly_day_29_feb_leap_year` | `day_of_month="29"`, range Jan-Mar 2028 (leap year) | Jan 29, Feb 29, Mar 29. `len == 3`. |
| `test_monthly_day_29_feb_non_leap_year` | `day_of_month="29"`, range Jan-Mar 2027 | Jan 29, Mar 29 only. `len == 2`. |

### Biweekly with explicit days (add to `TestGenerateProjectionsWeekly`)

| Test | Setup | Assert |
|------|-------|--------|
| `test_biweekly_with_explicit_days_ignores_multiple` | `interval_type="weekly", interval_multiple=2, day_of_week="1,3"`, range Jan 5-31 2026 | `interval_multiple` is ignored when `day_of_week` is set (code path doesn't check it). All Mon+Wed in range appear. `len == 8`. Documents this as actual behavior. |

### `parse_day_list` edge cases (new class: `TestParseDayList`)

Import `parse_day_list` from `app.services.recurrence`.

| Test | Input | Expected |
|------|-------|----------|
| `test_empty_string_returns_empty_list` | `parse_day_list("")` | `[]` |
| `test_none_returns_empty_list` | `parse_day_list(None)` | `[]` |
| `test_single_value` | `parse_day_list("3")` | `[3]` |
| `test_multiple_values` | `parse_day_list("1,3,5")` | `[1, 3, 5]` |
| `test_whitespace_handled` | `parse_day_list("1, 3, 5")` | `[1, 3, 5]` |

---

## Phase 3: DB-Backed Unit Tests

**File: `tests/test_prioritisation.py`**

### Class: `TestSnoozeFilteringDB` (requires `db` fixture)

| Test | Setup | Assert |
|------|-------|--------|
| `test_snoozed_errand_hidden_today` | Errand with `snooze_until=str(date.today() + timedelta(days=1))` | Not in `get_flexible_tasks(db, date.today())` results. |
| `test_expired_snooze_appears_today` | Errand with `snooze_until=str(date.today() - timedelta(days=1))` | IS in results. |
| `test_snooze_until_today_appears` | Errand with `snooze_until=str(date.today())` | IS in results. Filter is `<=`, so today equals today passes. |

### Class: `TestGetRemainingCapacityDB` (requires `db` fixture)

Import `get_remaining_capacity` from `app.services.scheduling`.

| Test | Setup | Assert |
|------|-------|--------|
| `test_no_fixed_tasks_full_capacity` | Empty DB | `main == 360`, `afternoon == 180`, `is_overbooked == False` |
| `test_with_fixed_tasks_reduced_capacity` | Appointment at 10:00, 60 min, on FUTURE_DATE | `main == 300`, `afternoon == 180`, `is_overbooked == False` |
| `test_is_overbooked_when_full` | Appointments filling 9:00-15:00 and 15:00-18:00 | `main == 0`, `afternoon == 0`, `is_overbooked == True` |

---

## Phase 4: API Integration Tests

**File: `tests/test_api.py`**

New imports needed: `TaskPreset` from `app.models.preset`, `MuscleGroup`, `Exercise`, `ExerciseMuscle`, `PerformedSet` from `app.models.workout` (some already imported).

### Class: `TestDeferSetsSnooze`

| Test | Setup | Assert |
|------|-------|--------|
| `test_defer_errand_sets_snooze_until_tomorrow` | Create errand, defer it | `task.snooze_until == str(date.today() + timedelta(days=1))` and `task.deferred_count == 1` |

### Class: `TestVariableRecurringAllowedDays`

Helper: `_make_variable_recurring_with_allowed_days(self, client, db, allowed_days: str)` — creates a variable_recurring task with the given `allowed_days` field.

| Test | Setup | Assert |
|------|-------|--------|
| `test_allowed_days_shifts_to_next_allowed` | Variable recurring with `allowed_days="0"` (Sunday). Add today's projection. Complete with `days_until_next=1`. | Next projection's weekday is 6 (Python Sunday). If tomorrow happens to be Sunday, it's exactly 1 day out; otherwise shifted forward. |
| `test_without_allowed_days_exact_days` | Variable recurring without `allowed_days`. Complete with `days_until_next=5`. | Next projection date is exactly `date.today() + timedelta(days=5)`. |

### Class: `TestPresetCRUD`

Constant: `PRESET_PAYLOAD = {"name": "Weekly Standup", "type": "recurring", "title": "Team Standup", "estimated_duration": 15, "importance": 2, "urgency": 2, "allow_afternoon": False, "interval_type": "weekly", "interval_multiple": 1, "day_of_week": "1,3,5"}`

| Test | Action | Assert |
|------|--------|--------|
| `test_create_preset` | `POST /presets/` with JSON payload | 200, fields match, exists in DB |
| `test_get_preset_by_id` | Create then `GET /presets/{id}` | 200, fields match |
| `test_delete_preset` | Create then `DELETE /presets/{id}` | 200, gone from DB |
| `test_get_nonexistent_preset_returns_404` | `GET /presets/999` | 404 |
| `test_list_presets_ordered_by_name` | Create "Zebra" and "Alpha" | 200, Alpha before Zebra |

### Class: `TestUpdateTask`

| Test | Action | Assert |
|------|--------|--------|
| `test_update_errand_fields` | Create errand, `PUT /tasks/{id}` with `{"title": "Buy bread", "importance": 3}` | 200, title and importance changed, urgency unchanged |
| `test_update_recurring_task_recurrence` | Create recurring, `PUT /tasks/{id}` with new recurrence fields | Recurrence row updated in DB |
| `test_update_nonexistent_task_returns_404` | `PUT /tasks/does-not-exist` | 404 |

### Class: `TestAdminRefreshProjections`

| Test | Setup | Assert |
|------|-------|--------|
| `test_refresh_projections_creates_projections` | Create recurring Task + Recurrence directly in DB (no projections). `POST /admin/refresh-projections`. | 200, `projections_created > 0`, projections exist in DB |

### Class: `TestAdminTaskCRUD`

| Test | Action | Assert |
|------|--------|--------|
| `test_admin_create_recurring_task` | `POST /admin/tasks` with form data | 303 redirect. Task + Recurrence + Projections in DB. |
| `test_admin_update_task` | Create task in DB, `POST /admin/tasks/{id}` | 303. Fields updated in DB. |
| `test_admin_delete_task` | Create recurring task, `DELETE /admin/tasks/{id}` | 200. Task, Recurrence, Projections all gone. |
| `test_completed_tasks_history` | Create + complete errand. `GET /admin/completed-tasks?from_date=today&to_date=today` | 200. CompletedTask exists in DB. |

### Class: `TestTaskLifecycleEdgeCases`

| Test | Action | Assert |
|------|--------|--------|
| `test_complete_already_deleted_task_returns_404` | Create errand, complete it (deletes it), complete again | 404 |
| `test_defer_recurring_no_today_projection` | Create recurring task (no today projection), defer | 200, `deferred_count == 1`, `snooze_until` set (falls through to else branch) |
| `test_create_appointment_without_scheduled_at` | `POST /tasks/` appointment with no `scheduled_at` | 200 (schema doesn't require it). Documents actual behavior. |
| `test_create_deadline_without_deadline_at` | `POST /tasks/` deadline with no `deadline_at` | 200. Documents actual behavior. |

### Class: `TestAdminMuscleGroups`

| Test | Action | Assert |
|------|--------|--------|
| `test_create_muscle_group` | `POST /admin/muscle-groups` form data | 200, exists in DB |
| `test_update_muscle_group_recovery_time` | Create in DB, `PUT /admin/muscle-groups/{id}` | 200, recovery_time updated |
| `test_delete_muscle_group_cascades` | Create MG + Exercise + ExerciseMuscle. Delete MG. | MG gone, ExerciseMuscle joins gone |

### Class: `TestAdminExercises`

| Test | Action | Assert |
|------|--------|--------|
| `test_create_exercise_with_muscles` | Create 2 MGs. `POST /admin/exercises` with both muscle_ids. | 303. Exercise created. 2 ExerciseMuscle rows. |
| `test_update_exercise_changes_muscles` | Create exercise with mg1. Update to mg2. | 303. Associations updated. |
| `test_delete_exercise_cascades` | Create exercise + associations. Delete. | Exercise gone, ExerciseMuscle rows gone. |

### Class: `TestAdminWorkoutHistory`

| Test | Action | Assert |
|------|--------|--------|
| `test_add_workout_history` | `POST /admin/workout-history/add` form data | 303. PerformedSet in DB. |
| `test_delete_workout_history` | Create PerformedSet in DB. `DELETE /admin/workout-history/{id}` | 200. Gone from DB. |
| `test_edit_workout_history` | Create PerformedSet. `POST /admin/workout-history/{id}` with new values. | 200. Fields updated. |

---

## Phase 5: Scenario-Level Tests

**File: `tests/test_api.py`**

### Class: `TestFullDaySchedulingScenario`

| Test | Setup | Assert |
|------|-------|--------|
| `test_full_day_mix_of_task_types` | In DB: 2 appointments (10:00 and 14:00), 1 time-bound recurring (9:00, with Projection + Recurrence), 2 errands (30 min each), 1 deadline (60 min). Call `build_daily_schedule(db, FUTURE_DATE)`. | Fixed tasks at correct times. Flexible tasks placed in gaps by priority. No overlapping time slots (check all pairs). `len >= 5`. |
| `test_week_view_returns_tasks_in_range` | Create recurring task with projections + appointment in date range. `GET /tasks/week?start=2099-01-10&end=2099-01-16` | 200. Response includes tasks within the date range. |

---

## Implementation Notes

1. **Admin form data**: Admin endpoints use `Form(...)` not JSON. Use `client.post(url, data={...})`. For multi-value fields like `muscle_ids`, pass a list: `data={"muscle_ids": [1, 2]}`.
2. **Admin redirects**: Most admin POST endpoints return `303`. Don't follow redirects in assertions — check `resp.status_code == 303` and verify DB state.
3. **Existing helpers**: Reuse `create_task()`, `add_todays_projection()`, and the payload constants already in `test_api.py`.
4. **`manual_scheduled_time` date**: Must match `FUTURE_DATE` for the scheduling algorithm to recognize it.
5. **TestClient redirect behavior**: FastAPI's TestClient follows redirects by default. Use `client.post(url, data={...}, follow_redirects=False)` when checking for 303 status codes.

## Verification

Run the full test suite after implementation:
```bash
cd /home/ross/Stuff/Documents/Coding/2026/organiser
python -m pytest tests/ -v
```

All existing tests must continue to pass. New tests should pass against the current codebase (they test existing behavior, not new features). Any test that fails reveals either a bug in the code or an incorrect assumption in this plan — investigate before adjusting.
