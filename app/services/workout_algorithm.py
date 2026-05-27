from datetime import date, timedelta
from sqlalchemy.orm import Session
from sqlalchemy import func

from app.models.workout import Exercise, MuscleGroup, ExerciseMuscle, PerformedSet


def get_last_workout_intensity(db: Session) -> str | None:
    """Get the intensity of the most recent workout."""
    last_set = db.query(PerformedSet).order_by(PerformedSet.created_at.desc()).first()
    return last_set.intensity if last_set else None


def get_todays_intensity(db: Session) -> str:
    """Determine today's intensity (alternates heavy/light)."""
    last_intensity = get_last_workout_intensity(db)
    if last_intensity == "heavy":
        return "light"
    return "heavy"


def get_muscle_group_scores(db: Session, intensity: str) -> dict[int, int]:
    """
    Calculate recovery score for each muscle group.
    
    Score = days since last workout - recovery time (minimum 0)
    Higher score = more recovered = better candidate
    """
    today = date.today()
    scores = {}
    
    muscle_groups = db.query(MuscleGroup).all()
    
    for mg in muscle_groups:
        # Find last workout for this muscle group with matching intensity
        last_workout = (
            db.query(func.max(PerformedSet.created_at))
            .join(ExerciseMuscle, PerformedSet.exercise_id == ExerciseMuscle.exercise_id)
            .filter(
                ExerciseMuscle.muscle_id == mg.id,
                PerformedSet.intensity == intensity
            )
            .scalar()
        )
        
        if last_workout:
            days_since = (today - last_workout.date()).days
            score = max(0, days_since - mg.recovery_time)
        else:
            # Never worked out - high score
            score = mg.recovery_time * 2
        
        scores[mg.id] = score
    
    return scores


def get_exercise_scores(db: Session, muscle_scores: dict[int, int]) -> list[tuple[Exercise, int]]:
    """
    Calculate score for each exercise based on its muscle groups.
    
    Score = product of muscle group scores (minimum 0)
    """
    exercises = db.query(Exercise).all()
    scored_exercises = []
    
    for exercise in exercises:
        # Get muscle groups for this exercise
        muscle_ids = (
            db.query(ExerciseMuscle.muscle_id)
            .filter(ExerciseMuscle.exercise_id == exercise.id)
            .all()
        )
        muscle_ids = [m[0] for m in muscle_ids]
        
        if not muscle_ids:
            continue
        
        # Calculate product of muscle scores
        score = 1
        for muscle_id in muscle_ids:
            muscle_score = muscle_scores.get(muscle_id, 0)
            score *= muscle_score
        
        scored_exercises.append((exercise, score))
    
    return scored_exercises


def select_todays_exercises(db: Session, count: int = 5) -> list[Exercise]:
    """
    Select exercises for today's workout using the algorithm.
    
    1. Determine today's intensity (alternates heavy/light)
    2. Calculate recovery score for each muscle group
    3. Score each exercise based on its muscle groups
    4. Select top scoring exercises
    """
    intensity = get_todays_intensity(db)
    muscle_scores = get_muscle_group_scores(db, intensity)
    exercise_scores = get_exercise_scores(db, muscle_scores)
    
    # Sort by score descending and take top N
    exercise_scores.sort(key=lambda x: x[1], reverse=True)
    
    return [ex for ex, score in exercise_scores[:count]]
