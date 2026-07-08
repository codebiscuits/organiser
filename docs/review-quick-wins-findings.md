# Code review findings — `quick-wins` branch (2026-07-07)

Status: **review complete, fix round NOT started.** Branch `quick-wins` (HEAD `ee36828`, 7 commits off `main` at `a05740c`), all 253 tests passing, not merged, not pushed. Review ran at high effort: 8 finder angles → 31 candidates → dedup → 12 verifiers. Next session: apply fixes below (delegate to Sonnet), re-verify, re-run suite, then Ross merges.

## Confirmed bugs (fix before merge)

1. **Type-change drops task state** — `app/routers/tasks.py:1004`. `_replace_task_for_type_change`'s `Task(...)` constructor omits `snooze_until`, `deferred_count`, `manual_scheduled_time`. A snoozed/deferred task that gets a type edit reappears in today's list and loses defer weight (prioritisation reads all three: `prioritisation.py:47,157,208`). **Fix:** copy the three fields.

2. **Admin tasks view stale rows** — `app/templates/admin/tasks.html:60,69,77`. New Done/Defer/Delete buttons omit `htmx.trigger(document.body, 'taskUpdated')` (present in `components/task_list.html:58,67,94`) AND the page has no `taskUpdated from:body` listener. Defer never refreshes (any type); Done for recurring/workout/variable_recurring and Delete-via-modal leave dead rows until reload. Plain Done/Delete on one-shot types work (direct row swap). **Fix:** add the trigger calls + a `taskUpdated` listener on the admin list container (see `index.html:6,13` for the pattern).

3. **VRT completion bypasses tombstones** — `app/routers/tasks.py:654`. `complete_variable_recurring_task` does `db.add(Projection(...))` directly, never checking `ProjectionExclusion`. Can recreate a deliberately-deleted occurrence, and then a Projection + Exclusion coexist for the same task+date forever (refresh never deletes rows). **Fix decision needed:** either check the tombstone and roll `next_date` forward, or (probably better) treat an explicit user-chosen next date as overriding — then DELETE the tombstone for that date so state stays consistent.

4. **Stale undo resurrects old task after type change** — `app/routers/tasks.py:1080` + `app/routers/undo.py:82-86`. New bug from the replace-with-new-id strategy: complete a recurring task (undo toast lives 15s, log 30min), edit its type before undoing, click undo → `_undo_complete` recreates OLD_ID from snapshot → two live tasks. **Fix:** in `_replace_task_for_type_change`, delete/invalidate ActionLog rows referencing `old_task.id` (or remap them to the new id).

5. **Undo of task delete loses tombstones** — `app/routers/undo.py:142` + `tasks.py:908`. `delete_task` hard-deletes `ProjectionExclusion` rows; ActionLog snapshots don't include them; `_undo_delete` can't restore them → next regeneration resurrects deliberately-skipped occurrences. **Fix:** add an `exclusions_snapshot` to ActionLog (same pattern as `projections_snapshot`) and restore in `_undo_delete`.

6. **Admin edit leaks orphan tombstones** — `app/routers/admin.py:252-254`. The `elif existing_recurrence:` branch (type changed away from recurring, admin keeps same task id) deletes Recurrence+Projections but not exclusions. Switching back to recurring later silently suppresses those dates. **Fix:** delete `ProjectionExclusion` rows in that branch too.

7. **Week-view occurrence delete is permanent with no undo** — `app/routers/tasks.py:352` (`DELETE /tasks/week/projection/{task_id}`, called from `week.html:313`). Writes a tombstone, no ActionLog, no undo toast; no endpoint exists to remove a single tombstone. Sibling `POST /{task_id}/delete-instance` does the identical action WITH undo. **Fix:** give this route the same ActionLog + undo treatment (undo already clears tombstones for delete-instance), or make week.html call delete-instance.

8. **ASCII-only duplicate-title match** — `app/routers/tasks.py:219`. `func.lower(Task.title)` (SQLite, ASCII-only) vs Python `str.lower()`. Titles with uppercase non-ASCII letters never match. Low severity. **Fix:** compare in Python (fetch candidates, filter with `.lower()`), or `COLLATE NOCASE` + accept its ASCII limits knowingly.

## Latent (pre-existing, fix opportunistically)

9. **Past `start_date` → zero live projections** — new sites `tasks.py:1047,1064,1171` copied the anchor `start = recurrence.start_date or date.today()` verbatim from `create_task` (`:457,:482` — same on main). With start_date >90d past, all projections predate today; daily view matches `due_date == target_date` exactly; nothing auto-refreshes projections (only manual admin endpoint). **Fix all five sites together:** anchor window at `max(today, start_date)`.

## Cleanup (same fix round or next session)

10. Extract shared helpers — verified duplication:
    - `Recurrence(...)` construction ×5: `tasks.py:457,482,1035,1052,1159` → `_build_recurrence_and_projections(db, task_id, recurrence_data)`
    - tombstone insert-guard ×2: `tasks.py:369-381, 855-867` → `_record_exclusion(db, task_id, due_date)`
    - Projection/Exclusion/Recurrence delete triad ×3: `tasks.py:908,1077`, `admin.py:264` → `_delete_task_children(db, task_id)`
    - `checkTitleDuplicate()` ×3: `task_form.html:12`, `task_edit_form.html:10`, `admin/task_form.html:79` → shared fn in `app/static/js/app.js` (beside `todayIsoDate`/`weekdayShort`); per-form `exclude_id` differences are correct, keep them as call args
    - dead no-op: `tasks.py:1108` (`task.type` reassignment unreachable with a changed value after the early return at `:1100-1101`) — delete
    - admin action buttons duplicate `task_list.html:33-98` per-type branching — worth a shared partial when fixing finding 2

## Refuted during review (do NOT re-flag)

- Type change to appointment "misses prep task": prep-task creation is create-only everywhere; edit paths never did it. (Separate pre-existing gap: editing `prep_duration` never creates/updates a prep task — candidate QUEUE idea, not a branch bug.)
- Notification offsets "lost" on type change: notifications are appointment-only and the old task by definition isn't an appointment — nothing to lose.

## Minor efficiency notes (optional)

- `refresh_projections` does N+1 exclusion SELECTs (one per recurrence) — fine at current scale.
- No index on `tasks.title` for check-title — fine at current scale.
- `tasks.py:1001` loads existing tag ids before checking `task_data.tag_ids is None` — move inside the branch.
