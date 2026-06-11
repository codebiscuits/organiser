Every time a task is completed, several actions will be triggered;
- the task is entered into the completed_tasks table
- the task is removed from the projection table
- if it is an irregular task, the next occurrence is asked for
- if it is an errand appointment or deadline, the task is removed from the tasks table
- if it is a workout, the app prompts for sets, reps, and weight. The user can also select the exercise from a dropdown (defaults to the scheduled exercise but allows choosing a different one). The performed exercise is stored in the performed_sets table (see docs/workout-algorithm.md)
- an `action_log` entry is written and a "showUndo" toast is triggered, so the completion can be undone within 30 minutes

## Completed_tasks table
The completed_tasks table is a log of all tasks that have been completed. It has the following columns:
- task_id: the id of the task that was completed
- completed_at: the timestamp when the task was completed
- actual_duration: the actual duration of the task in minutes
- notes: any notes or comments about the task
- task_type / task_title: snapshots taken at completion time, so history survives task deletion
- auto_completed: True if the task was swept up by the overdue auto-complete sweep rather than completed by the user

## Overdue appointments and deadlines

Appointments and deadlines used to silently vanish from the live list once their time/date passed, leaving stale `pending` rows behind forever with no record of what happened. Two mechanisms now address this:

### Due-today pinning (deadlines)
A deadline whose `deadline_at` falls on today's date is **not** scheduled into a gap like a normal flexible task. Instead it's flagged `due_today=True`, given urgency 3, and pinned to the very front of the schedule (red styling) for the whole day. If several deadlines are due today, the highest-priority one becomes the big "current task" card and the rest appear red at the top of "Up Next". It stays pinned until the user completes or defers it.

### Auto-complete sweep (overdue)
Once per load of the main task views, `auto_complete_overdue_tasks()` checks for:
- pending **appointments** with `scheduled_at.date() < today`
- pending **deadlines** with `deadline_at.date() < today` (i.e. the day *after* their due-today red day)

Any matches are auto-completed: snapshotted into `completed_tasks` (`auto_completed=True`), logged to `action_log`, and removed from `tasks`. Errands have no date field and are never swept this way.

### Undo
The sweep surfaces a toast with a single Undo button (`"'X' auto-completed (overdue)"` or `"{N} tasks auto-completed (overdue)"`), backed by `POST /undo/{id}` or `POST /undo/batch/{id1,id2,...}`. Undoing an auto-completed item bumps its `scheduled_at`/`deadline_at` to today, so it reappears (pinned as due-today for deadlines) instead of being swept again on the next load.


