# Schema
3 tables in a sql database; exercises, muscle_groups, exercise_history

## Exercises
The 'exercises' table contains the following columns:
- id
- name
- description
- intensity ('heavy' or 'light')

Each exercise is associated with one or more muscle groups via the `exercise_muscles` junction table. The `intensity` column classifies the exercise as heavy or light, allowing the algorithm to alternate between them on consecutive days.

## Muscle Groups
The 'muscle_groups' table contains the following columns:
- id
- name
- recovery

'recovery' is an integer representing the number of days it takes for the muscle group to recover after a workout.

## Exercise History
The 'exercise_history' table contains the following columns:
- id
- date
- exercise_id
- sets
- reps
- weight
- intensity

# Algorithm
The list of exercises should be sorted according to a score. The score is calculated as follows:
- First look at the previous day's workout to determine the intensity. If yesterday was a heavy workout, today should be light and vice-versa
- After filtering the history for heavy/light workouts as applicable, find the number of days since each muscle group was exercised (maybe create a temporary table to store this), and subtract their recovery times to calculate a score for each. The score is the difference between the current date and the last workout date, minus the recovery time, with 0 being the minimum value.
- For each exercise, calculate the score based on the score of each muscle group it involves. The score is the product of the scores of the muscle groups it involves, with 0 being the minimum value.
- Select the top 1 exercise with the highest score.

# Integration with Task System

Workout tasks are created like regular recurring tasks (with recurrence rules for frequency, days of week, etc.). When a workout task becomes due:

1. The algorithm runs and selects the top exercise
2. The task displays as "Workout: {exercise name}"
3. On completion, the user is prompted for sets, reps, and weight
4. The performed set is recorded in `performed_sets` table with the auto-determined intensity
