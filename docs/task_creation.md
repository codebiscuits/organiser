There should be an 'add task' button in the ui that opens a form for the user to input the details of the new task. All of this information is stored in the tasks table in the database.

The form should include the following fields:
- Title
- Description/notes
- Importance (1-3)
- Type (errand/appointment/deadline/recurring/irregular/workout)
- Due Date (if applicable)
- Estimated duration

## Types
- Errand: on-ff tasks that have no time constraints
- Appointment: tasks that must be *started at* a specific time and date
- Deadline: tasks that must be *completed before* a specific time and date
- Recurring: tasks that repeat at a specific interval, governed by the rules in docs/recurrence.md
- Irregular: tasks that repeat but are not on a fixed schedule. Whenever an irregular task is created or completed, the app must ask the user when the next occurrence is.
- Workouts are recurring, so they use the same recurrence fields as recurring tasks. When a workout task is due, the exercise selection algorithm runs to determine which exercise to include (see docs/workout-algorithm.md).

## Importance
This is an input to the priority calculation, it creates differentiation between tasks that must be completed and tasks that don't matter as much. All task types have an importance level.
- 1: Low
- 2: Medium
- 3: High

## Due Date
Only appointment and deadline tasks have a due date.

## Estimated duration
All task types must have an estimated duration. This is used to calculate how many tasks can be fit into a given window of time, and also as an offset from the due date in deadline tasks to calculate the latest time a task can be started.