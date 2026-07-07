from datetime import date, timedelta
from sqlalchemy.orm import Session

from app.models.recurrence import Recurrence, Projection, ProjectionExclusion


def parse_day_list(day_string: str | None) -> list[int]:
    """Parse comma-separated day string into list of integers."""
    if not day_string:
        return []
    return [int(d.strip()) for d in day_string.split(",")]


def generate_projections(
    db: Session,
    recurrence: Recurrence,
    start_date: date,
    end_date: date
) -> list[Projection]:
    """
    Generate projection entries for a recurrence rule between start and end dates.

    Dates recorded in ProjectionExclusion (an occurrence the user deliberately
    removed via "delete today only" / the week-view instance delete) are
    skipped, so regenerating projections never resurrects a deliberate
    deletion. `db` is optional — pure date-math tests call this with db=None
    and get no exclusion filtering, matching prior behaviour.
    """
    projections = []
    current = start_date

    days_of_week = parse_day_list(recurrence.day_of_week)
    days_of_month = parse_day_list(recurrence.day_of_month)
    months_of_year = parse_day_list(recurrence.month_of_year)

    excluded_dates: set[date] = set()
    if db is not None:
        excluded_dates = {
            row.due_date for row in db.query(ProjectionExclusion.due_date)
            .filter(ProjectionExclusion.task_id == recurrence.task_id)
            .all()
        }

    while current <= end_date:
        should_add = False
        
        if recurrence.interval_type == "daily":
            # Check if current date matches the interval
            if recurrence.start_date:
                days_since_start = (current - recurrence.start_date).days
                should_add = days_since_start % recurrence.interval_multiple == 0
            else:
                should_add = True
                
        elif recurrence.interval_type == "weekly":
            # Check if current day of week matches
            # days_of_week uses Sunday=0 convention, Python weekday() uses Monday=0
            # Convert: Sun=0 -> 6, Mon=1 -> 0, Tue=2 -> 1, etc.
            if days_of_week:
                python_weekdays = [(d - 1) % 7 for d in days_of_week]
                should_add = current.weekday() in python_weekdays
            else:
                # Default to same day of week as start
                if recurrence.start_date:
                    weeks_since_start = (current - recurrence.start_date).days // 7
                    should_add = (
                        current.weekday() == recurrence.start_date.weekday() and
                        weeks_since_start % recurrence.interval_multiple == 0
                    )
                    
        elif recurrence.interval_type == "monthly":
            if days_of_month:
                should_add = current.day in days_of_month
            else:
                # Default to same day of month as start
                if recurrence.start_date:
                    should_add = current.day == recurrence.start_date.day
                    
        elif recurrence.interval_type == "yearly":
            if months_of_year and days_of_month:
                should_add = current.month in months_of_year and current.day in days_of_month
            else:
                # Default to same month and day as start
                if recurrence.start_date:
                    should_add = (
                        current.month == recurrence.start_date.month and
                        current.day == recurrence.start_date.day
                    )
        
        if should_add and current not in excluded_dates:
            projection = Projection(task_id=recurrence.task_id, due_date=current)
            projections.append(projection)
        
        current += timedelta(days=1)
    
    return projections


def refresh_projections(db: Session, months_ahead: int = 3):
    """
    Refresh the projection table for all recurring tasks.
    Called periodically to ensure future instances are generated.
    """
    today = date.today()
    end_date = today + timedelta(days=months_ahead * 30)
    
    # Get all recurrence rules
    recurrences = db.query(Recurrence).all()
    
    for recurrence in recurrences:
        # Skip if end_date is in the past
        if recurrence.end_date and recurrence.end_date < today:
            continue
        
        start = max(today, recurrence.start_date) if recurrence.start_date else today
        end = min(end_date, recurrence.end_date) if recurrence.end_date else end_date
        
        # Generate new projections
        new_projections = generate_projections(db, recurrence, start, end)
        
        for projection in new_projections:
            # Check if already exists (unique constraint on task_id + due_date)
            existing = db.query(Projection).filter(
                Projection.task_id == projection.task_id,
                Projection.due_date == projection.due_date
            ).first()
            
            if not existing:
                db.add(projection)
    
    db.commit()
