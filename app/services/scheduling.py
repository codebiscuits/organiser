from datetime import datetime, date, time, timedelta
from typing import Literal
from dataclasses import dataclass
from sqlalchemy.orm import Session

from app.config import settings
from app.models.task import Task
from app.services.prioritisation import (
    PrioritisedTask,
    get_fixed_tasks,
    get_flexible_tasks,
    schedule_tasks_into_timeline,
    calculate_gaps,
    TimeSlot,
)


WindowType = Literal["main", "afternoon", "evening"]


@dataclass
class ScheduledTask:
    task: Task
    start_time: time
    end_time: time
    window: WindowType
    priority_score: int
    is_fixed: bool


def get_current_window() -> WindowType:
    now = datetime.now().time()
    
    if time(settings.main_window_start) <= now < time(settings.main_window_end):
        return "main"
    elif time(settings.afternoon_window_start) <= now < time(settings.afternoon_window_end):
        return "afternoon"
    elif time(settings.evening_window_start) <= now < time(settings.evening_window_end):
        return "evening"
    else:
        return "main"


def get_window_bounds(window: WindowType) -> tuple[time, time]:
    if window == "main":
        return time(settings.main_window_start), time(settings.main_window_end)
    elif window == "afternoon":
        return time(settings.afternoon_window_start), time(settings.afternoon_window_end)
    elif window == "evening":
        return time(settings.evening_window_start), time(settings.evening_window_end)
    return time(9), time(15)


def get_available_minutes(window: WindowType) -> int:
    if window == "main":
        return (settings.main_window_end - settings.main_window_start) * 60
    elif window == "afternoon":
        return (settings.afternoon_window_end - settings.afternoon_window_start) * 60
    elif window == "evening":
        return (settings.evening_window_end - settings.evening_window_start) * 60
    return 0


def determine_window(dt: datetime) -> WindowType:
    hour = dt.hour
    if settings.main_window_start <= hour < settings.main_window_end:
        return "main"
    elif settings.afternoon_window_start <= hour < settings.afternoon_window_end:
        return "afternoon"
    elif settings.evening_window_start <= hour < settings.evening_window_end:
        return "evening"
    return "main"


def add_minutes_to_time(t: time, minutes: int) -> time:
    dt = datetime.combine(date.today(), t)
    dt += timedelta(minutes=minutes)
    return dt.time()


def build_daily_schedule(db: Session, target_date: date) -> list[ScheduledTask]:
    """
    Build a concrete schedule for the day with specific time slots.
    
    Uses the timeline/gaps approach from prioritisation.
    """
    fixed = get_fixed_tasks(db, target_date)
    flexible = get_flexible_tasks(db, target_date)
    
    scheduled_prioritised = schedule_tasks_into_timeline(fixed, flexible, target_date)
    
    scheduled: list[ScheduledTask] = []
    for pt in scheduled_prioritised:
        if pt.scheduled_time is None:
            continue
        
        duration = (pt.task.estimated_duration or 30) + (pt.task.prep_duration or 0)
        end_dt = pt.scheduled_time + timedelta(minutes=duration)
        
        scheduled.append(ScheduledTask(
            task=pt.task,
            start_time=pt.scheduled_time.time(),
            end_time=end_dt.time(),
            window=determine_window(pt.scheduled_time),
            priority_score=pt.priority_score,
            is_fixed=pt.is_fixed,
        ))
    
    return scheduled


def get_remaining_capacity(db: Session, target_date: date) -> dict:
    """
    Get remaining capacity in each window for the target date.
    """
    fixed = get_fixed_tasks(db, target_date)
    gaps = calculate_gaps(fixed, target_date, include_afternoon=True)
    
    afternoon_start = datetime.combine(target_date, time(settings.afternoon_window_start))
    
    main_remaining = sum(
        g.duration_minutes for g in gaps 
        if g.start < afternoon_start
    )
    afternoon_remaining = sum(
        g.duration_minutes for g in gaps 
        if g.start >= afternoon_start
    )
    
    total_capacity = get_available_minutes("main") + get_available_minutes("afternoon")
    total_remaining = main_remaining + afternoon_remaining
    
    return {
        "main": main_remaining,
        "afternoon": afternoon_remaining,
        "is_overbooked": total_remaining <= 0,
    }


def get_next_available_slot(
    db: Session, 
    target_date: date, 
    duration_minutes: int,
    allow_afternoon: bool = False
) -> datetime | None:
    """
    Find the next available time slot for a task of given duration.
    """
    fixed = get_fixed_tasks(db, target_date)
    gaps = calculate_gaps(fixed, target_date, include_afternoon=allow_afternoon)
    
    afternoon_start = datetime.combine(target_date, time(settings.afternoon_window_start))
    
    for gap in gaps:
        if gap.start >= afternoon_start and not allow_afternoon:
            continue
        if gap.duration_minutes >= duration_minutes:
            return gap.start
    
    return None
