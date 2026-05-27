This structure is a Relational Database Schema designed using Third Normal Form (3NF) principles. It separates static definitions (the "blueprints") from transactional data (the "actions").

Verbal Description
The system is built on a Many-to-Many (M:M) relationship and a One-to-Many (1:M) logging hierarchy.

The Anatomy Layer: At the base, we have MuscleGroups and Exercises. These share an M:M relationship via a junction table, as one exercise targets many muscles, and one muscle is targeted by many exercises.

The Transactional Layer: When an exercise is performed, a record is created in Performed_Sets. Each entry in Performed_Sets functions as a mapping node; it links back to a specific Exercise to inherit its muscle-group data while storing unique, instance-specific metrics like weight, sets and reps.

Technical Implementation Plan
To build this from scratch, follow these three steps to ensure referential integrity (meaning you can't have a set for an exercise that doesn't exist).

1. Define Core Entities
Create the "Source of Truth" tables that don't depend on others.

MuscleGroups: id, name, recovery_time

Exercises: id, name, description, intensity ('heavy' or 'light')

'recovery_time' is an integer representing the number of days it takes for the muscle group to recover after a workout.

2. Establish Many-to-Many Junctions
Create the associative table to link your core entities.

Exercise_Muscles: exercise_id, muscle_id. Use a Composite Primary Key on both IDs to prevent duplicate mappings.

3. Create the Transactional Log
Create the table that captures the granular performance data.

Performed_Sets: id, exercise_id (FK), created_at, weight, reps, sets, intensity.

'intensity' represents whether it was a high intensity or high rep workout, and can have one of two values; "heavy" or "light". This is used to make sure that the algorithm alternates between heavy and light from one day to the next.

The Final SQL Blueprint
```SQL

-- Step 1 & 2: Definitions & Junctions
CREATE TABLE muscle_groups (id SERIAL PRIMARY KEY, name TEXT, recovery_time INT);
CREATE TABLE exercises (id SERIAL PRIMARY KEY, name TEXT, description TEXT, intensity VARCHAR(5) CHECK (intensity IN ('heavy', 'light')));

CREATE TABLE exercise_muscles (
    exercise_id INT REFERENCES exercises(id),
    muscle_id INT REFERENCES muscle_groups(id),
    PRIMARY KEY (exercise_id, muscle_id)
);

-- Step 3: Transactions (The Log)
CREATE TABLE performed_sets (
    id SERIAL PRIMARY KEY,
    exercise_id INT REFERENCES exercises(id),
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    reps INT,
    weight_kg DECIMAL,
    num_sets INT,
    intensity VARCHAR(5)
);
```