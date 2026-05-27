# Recurrence Table
There should be a table in the database that stores the recurrence rules for recurring tasks.

The table should have the following columns:
- id (primary key)
- task_id (foreign key to the tasks table)
- interval_type (string)
- interval_multiple (integer)
- day_of_week (string)
- day_of_month (string)
- month_of_year (string)
- start_date (date)
- end_date (date)
- created_at (timestamp)
- updated_at (timestamp)

interval_type should be one of the following:
- daily
- weekly
- monthly
- yearly

interval_multiple should be a positive integer, so the user can set a task to recur every 2 days or 3 weeks, for example.

day_of_week should be a string representation of an integer between 0 and 6, or several integers separated by commas, where 0 is Sunday and 6 is Saturday.

day_of_month should be a string representation of an integer between 1 and 31, or several integers separated by commas, where 1 is the first day of the month and 31 is the last day of the month. This could be NULL if the interval_type is not monthly.

month_of_year should be a string representation of an integer between 1 and 12, or several integers separated by commas, where 1 is January and 12 is December. This could be NULL if the interval_type is not yearly.

If day_of_week, day_of_month or month_of_year have a string of multiple numbers separated by commas, this should be interpreted as more than one day of the week, day of the month, or month of the year, respectively. so '2, 4' in 'day_of_week' would mean every Tuesday and Thursday.

Whenever the user creates a recurring, variable_recurring, or workout task, it should of course be entered into the tasks table, but an entry in the recurrence table should also be created automatically. Workout tasks use the same recurrence system as regular recurring tasks.

# Projection Table
Based on the rules in the Recurrence Table, the Projection Table should store the next few months' worth of occurrence of each task, automatically generated. this saves calculating the next occurrence of each task every time it is needed, and it allows other functionality like manually adding or removing a single occurrence of a task without adjusting the recurrence rules.

The Projection Table should have the following columns:
- instance_id (integer, primary key)
- task_id (foreign key to the tasks table)
- due_date (date)
- created_at (timestamp)
- updated_at (timestamp)

The CREATE TABLE statement for the Projection Table should include the line `UNIQUE(task_id, due_date)` to ensure that there is only one instance of each task on each due date.

Whenever a new recurring, variable_recurring, or workout task is created, its entries in the projection table should be calculated and inserted immediately to make sure they appear the following day if necessary.
Also, every week or so, the recurrence table should be updated to make sure it doesn't run out of pending tasks.

# Time-Bound Recurring Tasks

Recurring tasks can optionally have a `scheduled_time` field (stored in the tasks table) specifying the exact time of day the task should occur. When set:

- The task is pinned to that time slot each day it recurs (similar to an appointment)
- During daily list generation, time-bound recurring tasks are placed first alongside appointments
- Other tasks are scheduled around these fixed time slots
- The `allow_afternoon` field is ignored when `scheduled_time` is set, since the time is explicit

This is useful for tasks that must happen at a specific time (e.g., medication at 8am, daily standup at 10am).