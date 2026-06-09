import json
from datetime import datetime, date, time as time_type

from app.models.task import Task
from app.models.recurrence import Projection


def task_to_dict(task) -> dict:
    return {
        "id": task.id,
        "type": task.type,
        "title": task.title,
        "notes": task.notes,
        "estimated_duration": task.estimated_duration,
        "importance": task.importance,
        "urgency": task.urgency,
        "allow_afternoon": task.allow_afternoon,
        "deadline_at": task.deadline_at.isoformat() if task.deadline_at else None,
        "scheduled_at": task.scheduled_at.isoformat() if task.scheduled_at else None,
        "prep_duration": task.prep_duration,
        "scheduled_time": task.scheduled_time.strftime("%H:%M:%S") if task.scheduled_time else None,
        "location": task.location,
        "status": task.status,
        "deferred_count": task.deferred_count,
        "snooze_until": task.snooze_until,
        "manual_scheduled_time": task.manual_scheduled_time.isoformat() if task.manual_scheduled_time else None,
        "preset_id": task.preset_id,
        "allowed_days": task.allowed_days,
    }


def recurrence_to_dict(rec) -> dict:
    return {
        "interval_type": rec.interval_type,
        "interval_multiple": rec.interval_multiple,
        "day_of_week": rec.day_of_week,
        "day_of_month": rec.day_of_month,
        "month_of_year": rec.month_of_year,
        "start_date": rec.start_date.isoformat() if rec.start_date else None,
        "end_date": rec.end_date.isoformat() if rec.end_date else None,
    }


def projections_to_list(projections) -> list:
    return [p.due_date.isoformat() for p in projections]


def task_from_dict(snap: dict) -> Task:
    return Task(
        id=snap["id"],
        type=snap["type"],
        title=snap["title"],
        notes=snap["notes"],
        estimated_duration=snap["estimated_duration"],
        importance=snap["importance"],
        urgency=snap["urgency"],
        allow_afternoon=snap["allow_afternoon"],
        deadline_at=datetime.fromisoformat(snap["deadline_at"]) if snap["deadline_at"] else None,
        scheduled_at=datetime.fromisoformat(snap["scheduled_at"]) if snap["scheduled_at"] else None,
        prep_duration=snap["prep_duration"],
        scheduled_time=time_type.fromisoformat(snap["scheduled_time"]) if snap["scheduled_time"] else None,
        location=snap["location"],
        status=snap["status"],
        deferred_count=snap["deferred_count"],
        snooze_until=snap["snooze_until"],
        manual_scheduled_time=datetime.fromisoformat(snap["manual_scheduled_time"]) if snap["manual_scheduled_time"] else None,
        preset_id=snap["preset_id"],
        allowed_days=snap["allowed_days"],
    )


def apply_task_dict(task, snap: dict):
    task.type = snap["type"]
    task.title = snap["title"]
    task.notes = snap["notes"]
    task.estimated_duration = snap["estimated_duration"]
    task.importance = snap["importance"]
    task.urgency = snap["urgency"]
    task.allow_afternoon = snap["allow_afternoon"]
    task.deadline_at = datetime.fromisoformat(snap["deadline_at"]) if snap["deadline_at"] else None
    task.scheduled_at = datetime.fromisoformat(snap["scheduled_at"]) if snap["scheduled_at"] else None
    task.prep_duration = snap["prep_duration"]
    task.scheduled_time = time_type.fromisoformat(snap["scheduled_time"]) if snap["scheduled_time"] else None
    task.location = snap["location"]
    task.status = snap["status"]
    task.deferred_count = snap["deferred_count"]
    task.snooze_until = snap["snooze_until"]
    task.manual_scheduled_time = datetime.fromisoformat(snap["manual_scheduled_time"]) if snap["manual_scheduled_time"] else None
    task.preset_id = snap["preset_id"]
    task.allowed_days = snap["allowed_days"]
