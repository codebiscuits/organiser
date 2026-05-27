Every time a task is completed, several actions will be triggered;
- the task is entered into the completed_tasks table
- the task is removed from the projection table
- if it is an irregular task, the next occurrence is asked for
- if it is an errand appointment or deadline, the task is removed from the tasks table
- if it is a workout, the app prompts for sets, reps, and weight. The user can also select the exercise from a dropdown (defaults to the scheduled exercise but allows choosing a different one). The performed exercise is stored in the performed_sets table (see docs/workout-algorithm.md)

## Completed_tasks table
The completed_tasks table is a log of all tasks that have been completed. It should have the following columns:
- task_id: the id of the task that was completed
- completed_at: the timestamp when the task was completed
- actual_duration: the actual duration of the task in minutes
- notes: any notes or comments about the task


