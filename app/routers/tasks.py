import json
from datetime import date, time, datetime, timedelta
from fastapi import APIRouter, Depends, Form, HTTPException, Request, Response
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.task import Task, CompletedTask
from app.models.recurrence import Recurrence, Projection, ProjectionExclusion
from app.models.action_log import ActionLog
from app.models.tag import Tag, TaskTag
from app.models.task_notification import TaskNotification
from app.services.undo import task_to_dict, recurrence_to_dict, projections_to_list
from app.schemas.task import (
    TaskCreate,
    TaskUpdate,
    TaskResponse,
    TaskCompleteRequest,
    VariableRecurringCompleteRequest,
    WorkoutCompleteRequest,
)
from app.models.workout import PerformedSet
from app.services.workout_algorithm import select_todays_exercises, get_todays_intensity
from app.services.recurrence import generate_projections
from app.services.prioritisation import get_prioritised_tasks_with_metadata

router = APIRouter()
templates = Jinja2Templates(directory="app/templates")


def _sync_task_notifications(db: Session, task: Task, offsets: list[int]) -> None:
    """
    Replace task.notifications with one row per (deduped) offset.

    Past-time guard: if scheduled_at - offset is already in the past at the
    time of creation/update, the row is stamped sent_at=now immediately so
    the scheduler's fire-when-overdue check doesn't instantly spam it.
    """
    db.query(TaskNotification).filter(TaskNotification.task_id == task.id).delete()

    now = datetime.now()
    for offset in sorted(set(offsets)):
        sent_at = None
        if task.scheduled_at is not None and (task.scheduled_at - timedelta(minutes=offset)) <= now:
            sent_at = now
        db.add(TaskNotification(task_id=task.id, offset_minutes=offset, sent_at=sent_at))


def _prune_old_logs(db):
    cutoff = datetime.utcnow() - timedelta(minutes=30)
    db.query(ActionLog).filter(ActionLog.performed_at < cutoff).delete()


def _undo_trigger(log_ids: int | list[int], label: str) -> str:
    if isinstance(log_ids, int):
        log_ids = [log_ids]
    return json.dumps({"showUndo": {"ids": log_ids, "label": label}})


def auto_complete_overdue_tasks(db: Session) -> list[tuple[int, str]]:
    """
    Auto-complete appointments and deadlines whose date has fully passed
    while still pending.

    - Appointments: scheduled_at.date() < today
    - Deadlines: deadline_at.date() < today (deadlines due today get a full
      day pinned to the top of the live list before this applies)

    Returns a list of (log_id, title) for any tasks swept, so callers can
    surface an undo toast.
    """
    today = date.today()
    overdue = db.query(Task).filter(
        Task.status == "pending",
        Task.type.in_(("appointment", "deadline")),
    ).all()

    swept = []
    for task in overdue:
        cutoff = task.scheduled_at if task.type == "appointment" else task.deadline_at
        if not cutoff or cutoff.date() >= today:
            continue

        task_title = task.title
        task_snap = json.dumps(task_to_dict(task))
        proj_snap = json.dumps(projections_to_list(
            db.query(Projection).filter(Projection.task_id == task.id).all()
        ))

        completed = CompletedTask(
            task_id=task.id,
            task_type=task.type,
            task_title=task.title,
            auto_completed=True,
        )
        db.add(completed)
        db.flush()

        db.delete(task)

        log = ActionLog(
            action_type="complete",
            task_id=task.id,
            task_title=task_title,
            task_snapshot=task_snap,
            projections_snapshot=proj_snap,
            completed_task_id=completed.id,
        )
        db.add(log)
        db.flush()

        swept.append((log.id, task_title))

    if swept:
        _prune_old_logs(db)
        db.commit()

    return swept


def _auto_complete_trigger(db: Session) -> str | None:
    swept = auto_complete_overdue_tasks(db)
    if not swept:
        return None
    if len(swept) == 1:
        label = f"'{swept[0][1]}' auto-completed (overdue)"
    else:
        label = f"{len(swept)} tasks auto-completed (overdue)"
    return _undo_trigger([log_id for log_id, _ in swept], label)


class TaskTimelinePosition(BaseModel):
    task_id: str
    scheduled_hour: int
    scheduled_minute: int


class TimelineReorderRequest(BaseModel):
    tasks: list[TaskTimelinePosition]


@router.get("/", response_class=HTMLResponse)
async def list_tasks(request: Request, db: Session = Depends(get_db)):
    """Get prioritised task list for today."""
    trigger = _auto_complete_trigger(db)
    prioritised_tasks, capacity = get_prioritised_tasks_with_metadata(db, date.today())
    response = templates.TemplateResponse(
        request,
        "components/task_list.html",
        {"tasks": prioritised_tasks, "capacity": capacity},
    )
    if trigger:
        response.headers["HX-Trigger"] = trigger
    return response


@router.get("/current", response_class=HTMLResponse)
async def current_task(request: Request, db: Session = Depends(get_db)):
    """Get the current (highest priority) task for today."""
    trigger = _auto_complete_trigger(db)
    prioritised_tasks, _ = get_prioritised_tasks_with_metadata(db, date.today())
    current = prioritised_tasks[0] if prioritised_tasks else None
    response = templates.TemplateResponse(
        request,
        "components/current_task.html",
        {"task": current},
    )
    if trigger:
        response.headers["HX-Trigger"] = trigger
    return response


@router.get("/upcoming", response_class=HTMLResponse)
async def upcoming_tasks(request: Request, db: Session = Depends(get_db)):
    """Get upcoming tasks (all except current)."""
    trigger = _auto_complete_trigger(db)
    prioritised_tasks, capacity = get_prioritised_tasks_with_metadata(db, date.today())
    response = templates.TemplateResponse(
        request,
        "components/task_list.html",
        {"tasks": prioritised_tasks[1:], "capacity": capacity},
    )
    if trigger:
        response.headers["HX-Trigger"] = trigger
    return response


@router.get("/new", response_class=HTMLResponse)
async def new_task_form(request: Request, db: Session = Depends(get_db)):
    """Return the task creation form modal."""
    tags = db.query(Tag).order_by(Tag.name).all()
    return templates.TemplateResponse(request, "components/task_form.html", {"tags": tags})


@router.get("/check-title")
async def check_title(
    title: str,
    exclude_id: str | None = None,
    db: Session = Depends(get_db),
):
    """
    Case-insensitive check for an existing task with this title.

    Used by the create/edit task forms to show a non-blocking duplicate
    warning — awareness only, creation is never prevented. `exclude_id` lets
    the edit form ignore a match against the task being edited itself.

    NOTE: registered before /{task_id} — a literal path here would otherwise
    never be reached (FastAPI matches routes in registration order and the
    catch-all /{task_id} would swallow "/check-title" as a task_id).
    """
    normalized = title.strip().lower()
    if not normalized:
        return {"duplicate": False}

    query = db.query(Task).filter(func.lower(Task.title) == normalized)
    if exclude_id:
        query = query.filter(Task.id != exclude_id)
    match = query.first()
    return {"duplicate": match is not None, "title": match.title if match else None}


@router.get("/all", response_model=list[TaskResponse])
async def get_all_tasks(db: Session = Depends(get_db)):
    """Get all tasks (API endpoint)."""
    tasks = db.query(Task).all()
    return tasks


@router.get("/today")
async def get_today_tasks(db: Session = Depends(get_db)):
    """Get today's prioritised and scheduled task list as JSON."""
    _auto_complete_trigger(db)
    scheduled, capacity = get_prioritised_tasks_with_metadata(db, date.today())
    tasks = []
    for pt in scheduled:
        tasks.append({
            "id": pt.task.id,
            "type": pt.task.type,
            "title": pt.task.title,
            "notes": pt.task.notes,
            "estimated_duration": pt.task.estimated_duration,
            "importance": pt.task.importance,
            "urgency": pt.task.urgency,
            "calculated_urgency": pt.calculated_urgency,
            "priority_score": pt.priority_score,
            "is_fixed": pt.is_fixed,
            "scheduled_time": pt.scheduled_time.isoformat() if pt.scheduled_time else None,
            "due_today": pt.due_today,
            "selected_exercise": pt.selected_exercise,
            "deadline_at": pt.task.deadline_at.isoformat() if pt.task.deadline_at else None,
            "scheduled_at": pt.task.scheduled_at.isoformat() if pt.task.scheduled_at else None,
            "location": pt.task.location,
            "allow_afternoon": pt.task.allow_afternoon,
        })
    return {"tasks": tasks, "capacity": capacity}


@router.get("/timeline", response_class=HTMLResponse)
async def timeline_view(request: Request, db: Session = Depends(get_db)):
    """
    Timeline view for today's scheduled tasks.
    
    Displays tasks on a visual timeline where users can drag and drop
    to reorder flexible tasks.
    """
    trigger = _auto_complete_trigger(db)
    prioritised_tasks, _ = get_prioritised_tasks_with_metadata(db, date.today())
    start_hour = max(9, datetime.now().hour)
    visible_tasks = [
        pt for pt in prioritised_tasks
        if pt.scheduled_time and pt.scheduled_time.hour >= start_hour
    ]
    response = templates.TemplateResponse(
        request,
        "timeline.html",
        {"tasks": visible_tasks, "start_hour": start_hour, "today": date.today()},
    )
    if trigger:
        response.headers["HX-Trigger"] = trigger
    return response


@router.get("/week", response_class=HTMLResponse)
async def week_view(
    request: Request,
    start: str | None = None,
    end: str | None = None,
    db: Session = Depends(get_db)
):
    """
    Week view for 7-day scheduling.
    
    If start/end query params are provided, returns JSON list of schedulable tasks
    (appointments and recurring) for that date range.
    Otherwise, renders the week view template.
    """
    if start and end:
        start_date = datetime.strptime(start, "%Y-%m-%d").date()
        end_date = datetime.strptime(end, "%Y-%m-%d").date()
        
        appointments = db.query(Task).filter(
            Task.type == "appointment",
            Task.scheduled_at >= datetime.combine(start_date, time(0, 0)),
            Task.scheduled_at <= datetime.combine(end_date, time(23, 59, 59))
        ).all()
        
        projections = db.query(Projection, Task).join(
            Task, Projection.task_id == Task.id
        ).filter(
            Projection.due_date >= start_date,
            Projection.due_date <= end_date,
            Task.type.in_(["recurring", "variable_recurring", "workout"])
        ).all()
        
        result = []
        
        for task in appointments:
            result.append({
                "id": task.id,
                "title": task.title,
                "type": task.type,
                "scheduled_at": task.scheduled_at.isoformat() if task.scheduled_at else None,
                "duration": task.estimated_duration or 30,
                "location": task.location,
                "notes": task.notes
            })
        
        for projection, task in projections:
            scheduled_time = task.scheduled_time or time(9, 0)
            scheduled_at = datetime.combine(projection.due_date, scheduled_time)
            result.append({
                "id": task.id,
                "title": task.title,
                "type": task.type,
                "scheduled_at": scheduled_at.isoformat(),
                "duration": task.estimated_duration or 30,
                "location": task.location,
                "notes": task.notes,
                "projection_date": projection.due_date.isoformat()
            })
        
        from fastapi.responses import JSONResponse
        return JSONResponse(content=result)
    
    return templates.TemplateResponse(request, "week.html")


@router.delete("/week/projection/{task_id}")
async def delete_projection(
    task_id: str,
    date: str,
    db: Session = Depends(get_db)
):
    """
    Delete a single projection instance (one occurrence of a recurring task).

    Does not delete the task itself, only removes the projection for the specified date.

    Also records a ProjectionExclusion tombstone so a later regeneration
    (refresh_projections, admin's regenerate-all, etc.) doesn't resurrect
    this specific occurrence — see app/services/recurrence.py.
    """
    projection_date = datetime.strptime(date, "%Y-%m-%d").date()

    deleted = db.query(Projection).filter(
        Projection.task_id == task_id,
        Projection.due_date == projection_date
    ).delete()

    if deleted == 0:
        raise HTTPException(status_code=404, detail="Projection not found")

    if not db.query(ProjectionExclusion).filter(
        ProjectionExclusion.task_id == task_id,
        ProjectionExclusion.due_date == projection_date,
    ).first():
        db.add(ProjectionExclusion(task_id=task_id, due_date=projection_date))

    db.commit()

    return {"status": "ok", "deleted": deleted}


@router.post("/timeline/reorder")
async def reorder_timeline(
    request: TimelineReorderRequest,
    db: Session = Depends(get_db)
):
    """
    Save the user's manual reordering of tasks on the timeline.
    
    Updates each task's manual_order field based on their position
    in the timeline. This order is used as a tie-breaker during
    prioritisation for flexible tasks.
    """
    today = date.today()
    
    for position in request.tasks:
        task = db.query(Task).filter(Task.id == position.task_id).first()
        if not task:
            continue
        
        scheduled_datetime = datetime.combine(
            today,
            time(position.scheduled_hour, position.scheduled_minute)
        )
        
        task.manual_scheduled_time = scheduled_datetime
    
    db.commit()
    return {"status": "ok", "updated": len(request.tasks)}


@router.get("/{task_id}", response_model=TaskResponse)
async def get_task(task_id: str, db: Session = Depends(get_db)):
    """Get a single task by ID."""
    task = db.query(Task).filter(Task.id == task_id).first()
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    return task


@router.post("/", response_model=TaskResponse)
async def create_task(task_data: TaskCreate, db: Session = Depends(get_db)):
    """
    Create a new task.
    
    Handles:
    - Recurring/variable_recurring: creates recurrence rule and projections
    - Appointments: auto-generates prep task if prep_duration is set
    """
    task = Task(
        type=task_data.type.value,
        title=task_data.title,
        notes=task_data.notes,
        estimated_duration=task_data.estimated_duration,
        importance=task_data.importance,
        urgency=task_data.urgency,
        allow_afternoon=task_data.allow_afternoon,
        deadline_at=task_data.deadline_at,
        scheduled_at=task_data.scheduled_at,
        prep_duration=task_data.prep_duration,
        scheduled_time=task_data.scheduled_time,
        location=task_data.location,
        preset_id=task_data.preset_id,
        allowed_days=task_data.allowed_days,
        status="pending",
    )
    db.add(task)
    db.flush()

    if task_data.type in ("recurring", "workout") and task_data.recurrence:
        recurrence = Recurrence(
            task_id=task.id,
            interval_type=task_data.recurrence.interval_type,
            interval_multiple=task_data.recurrence.interval_multiple,
            day_of_week=task_data.recurrence.day_of_week,
            day_of_month=task_data.recurrence.day_of_month,
            month_of_year=task_data.recurrence.month_of_year,
            start_date=task_data.recurrence.start_date.date() if task_data.recurrence.start_date else date.today(),
            end_date=task_data.recurrence.end_date.date() if task_data.recurrence.end_date else None,
        )
        db.add(recurrence)
        db.flush()

        start = recurrence.start_date or date.today()
        end = start + timedelta(days=90)
        projections = generate_projections(db, recurrence, start, end)
        for projection in projections:
            existing = db.query(Projection).filter(
                Projection.task_id == projection.task_id,
                Projection.due_date == projection.due_date
            ).first()
            if not existing:
                db.add(projection)

    elif task_data.type == "variable_recurring" and task_data.recurrence:
        recurrence = Recurrence(
            task_id=task.id,
            interval_type=task_data.recurrence.interval_type,
            interval_multiple=task_data.recurrence.interval_multiple,
            day_of_week=task_data.recurrence.day_of_week,
            day_of_month=task_data.recurrence.day_of_month,
            month_of_year=task_data.recurrence.month_of_year,
            start_date=task_data.recurrence.start_date.date() if task_data.recurrence.start_date else date.today(),
            end_date=task_data.recurrence.end_date.date() if task_data.recurrence.end_date else None,
        )
        db.add(recurrence)
        db.flush()

        start = recurrence.start_date or date.today()
        db.add(Projection(task_id=task.id, due_date=start))

    for tag_id in task_data.tag_ids:
        db.add(TaskTag(task_id=task.id, tag_id=tag_id))

    if task_data.notification_offsets:
        _sync_task_notifications(db, task, task_data.notification_offsets)

    if task_data.type == "appointment" and task_data.prep_duration and task_data.scheduled_at:
        prep_task = Task(
            type="appointment",
            title=f"Getting ready for: {task_data.title}",
            estimated_duration=task_data.prep_duration,
            importance=task_data.importance,
            urgency=3,
            scheduled_at=task_data.scheduled_at - timedelta(minutes=task_data.prep_duration),
            status="pending",
        )
        db.add(prep_task)

    db.commit()
    db.refresh(task)
    return task


@router.post("/{task_id}/complete", response_class=HTMLResponse)
async def complete_task(
    request: Request,
    task_id: str,
    completion_data: TaskCompleteRequest | None = None,
    db: Session = Depends(get_db)
):
    """
    Complete a task.
    
    Actions:
    - Records in completed_tasks table
    - Removes from projection table (for recurring)
    - Removes from tasks table (for errand, appointment, deadline)
    - Returns prompt_for_next=True for variable_recurring tasks
    """
    task = db.query(Task).filter(Task.id == task_id).first()
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")

    task_title = task.title
    task_snap = json.dumps(task_to_dict(task))
    proj_snap = json.dumps(projections_to_list(
        db.query(Projection).filter(Projection.task_id == task_id).all()
    ))

    completed = CompletedTask(
        task_id=task.id,
        actual_duration=completion_data.actual_duration if completion_data else None,
        notes=completion_data.notes if completion_data else None,
        task_type=task.type,
        task_title=task.title,
    )
    db.add(completed)
    db.flush()

    db.query(Projection).filter(
        Projection.task_id == task_id,
        Projection.due_date == date.today()
    ).delete()

    if task.type in ("errand", "appointment", "deadline"):
        db.delete(task)

    log = ActionLog(
        action_type="complete",
        task_id=task_id,
        task_title=task_title,
        task_snapshot=task_snap,
        projections_snapshot=proj_snap,
        completed_task_id=completed.id,
    )
    db.add(log)
    _prune_old_logs(db)
    db.flush()
    log_id = log.id

    db.commit()
    return Response(status_code=200, headers={"HX-Trigger": _undo_trigger(log_id, f"'{task_title}' completed")})


@router.get("/{task_id}/complete/variable", response_class=HTMLResponse)
async def get_variable_complete_form(
    request: Request,
    task_id: str,
    db: Session = Depends(get_db)
):
    """Return the 'when next?' modal for a variable recurring task."""
    from app.models.preset import TaskPreset

    task = db.query(Task).filter(Task.id == task_id).first()
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    if task.type != "variable_recurring":
        raise HTTPException(status_code=400, detail="Task is not variable recurring")

    preset = None
    if task.preset_id:
        preset = db.query(TaskPreset).filter(TaskPreset.id == task.preset_id).first()

    response = templates.TemplateResponse(
        request,
        "components/variable_complete_form.html",
        {"task": task, "preset": preset},
    )
    response.headers["Cache-Control"] = "no-store"
    return response


@router.post("/{task_id}/complete/variable", response_class=HTMLResponse)
async def complete_variable_recurring_task(
    task_id: str,
    days_until_next: int = Form(..., ge=1),
    actual_duration: int | None = Form(None),
    notes: str | None = Form(None),
    db: Session = Depends(get_db)
):
    """Complete a variable recurring task and schedule the next occurrence."""
    task = db.query(Task).filter(Task.id == task_id).first()
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    if task.type != "variable_recurring":
        raise HTTPException(status_code=400, detail="Task is not variable recurring")

    task_title = task.title
    task_snap = json.dumps(task_to_dict(task))
    proj_snap = json.dumps(projections_to_list(
        db.query(Projection).filter(Projection.task_id == task_id).all()
    ))

    completed = CompletedTask(
        task_id=task.id,
        actual_duration=actual_duration,
        notes=notes,
        task_type=task.type,
        task_title=task.title,
    )
    db.add(completed)
    db.flush()

    db.query(Projection).filter(
        Projection.task_id == task_id,
        Projection.due_date >= date.today()
    ).delete()

    next_date = date.today() + timedelta(days=days_until_next)
    if task.allowed_days:
        allowed = [int(d.strip()) for d in task.allowed_days.split(",")]
        python_allowed = [(d - 1) % 7 for d in allowed]
        for _ in range(7):
            if next_date.weekday() in python_allowed:
                break
            next_date += timedelta(days=1)
    db.add(Projection(task_id=task_id, due_date=next_date))

    log = ActionLog(
        action_type="complete",
        task_id=task_id,
        task_title=task_title,
        task_snapshot=task_snap,
        projections_snapshot=proj_snap,
        completed_task_id=completed.id,
    )
    db.add(log)
    _prune_old_logs(db)
    db.flush()
    log_id = log.id

    db.commit()
    return Response(status_code=200, headers={"HX-Trigger": json.dumps({
        "taskUpdated": True,
        "showUndo": {"ids": [log_id], "label": f"'{task_title}' completed"},
    })})


@router.get("/{task_id}/complete/workout", response_class=HTMLResponse)
async def get_workout_completion_form(
    request: Request,
    task_id: str,
    db: Session = Depends(get_db)
):
    """
    Return the workout completion form with today's selected exercise.
    """
    from app.models.workout import Exercise
    
    task = db.query(Task).filter(Task.id == task_id).first()
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    if task.type != "workout":
        raise HTTPException(status_code=400, detail="Task is not a workout")
    
    scheduled_exercises = select_todays_exercises(db, count=1)
    scheduled_exercise = scheduled_exercises[0] if scheduled_exercises else None
    intensity = get_todays_intensity(db)
    all_exercises = db.query(Exercise).order_by(Exercise.name).all()
    
    response = templates.TemplateResponse(
        request,
        "components/workout_complete_form.html",
        {
            "task": task,
            "scheduled_exercise": scheduled_exercise,
            "all_exercises": all_exercises,
            "intensity": intensity,
        },
    )
    response.headers["Cache-Control"] = "no-store"
    return response


@router.post("/{task_id}/complete/workout")
async def complete_workout_task(
    task_id: str,
    exercise_id: int = Form(...),
    sets: int = Form(..., ge=1),
    reps: int = Form(..., ge=1),
    weight_kg: float = Form(..., ge=0),
    intensity: str = Form(...),
    db: Session = Depends(get_db)
):
    """
    Complete a workout task and record the performed set.
    """
    task = db.query(Task).filter(Task.id == task_id).first()
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    if task.type != "workout":
        raise HTTPException(status_code=400, detail="Task is not a workout")

    task_title = task.title
    task_snap = json.dumps(task_to_dict(task))
    proj_snap = json.dumps(projections_to_list(
        db.query(Projection).filter(Projection.task_id == task_id).all()
    ))

    completed = CompletedTask(
        task_id=task.id,
        task_type=task.type,
        task_title=task.title,
    )
    db.add(completed)
    db.flush()

    performed_set = PerformedSet(
        exercise_id=exercise_id,
        reps=reps,
        weight_kg=weight_kg,
        num_sets=sets,
        intensity=intensity,
    )
    db.add(performed_set)
    db.flush()

    db.query(Projection).filter(
        Projection.task_id == task_id,
        Projection.due_date == date.today()
    ).delete()

    log = ActionLog(
        action_type="complete",
        task_id=task_id,
        task_title=task_title,
        task_snapshot=task_snap,
        projections_snapshot=proj_snap,
        completed_task_id=completed.id,
        performed_set_id=performed_set.id,
    )
    db.add(log)
    _prune_old_logs(db)
    db.flush()
    log_id = log.id

    db.commit()
    return Response(status_code=200, headers={"HX-Trigger": json.dumps({
        "taskUpdated": True,
        "showUndo": {"ids": [log_id], "label": f"'{task_title}' completed"},
    })})


@router.post("/{task_id}/defer", response_class=HTMLResponse)
async def defer_task(request: Request, task_id: str, db: Session = Depends(get_db)):
    """
    Defer a task to the next day.
    
    - Moves projection to tomorrow (for recurring tasks)
    - Increments deferred_count
    """
    task = db.query(Task).filter(Task.id == task_id).first()
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")

    task_title = task.title
    task_snap = json.dumps(task_to_dict(task))
    proj_snap = json.dumps(projections_to_list(
        db.query(Projection).filter(Projection.task_id == task_id).all()
    ))

    task.deferred_count = (task.deferred_count or 0) + 1

    today_projection = db.query(Projection).filter(
        Projection.task_id == task_id,
        Projection.due_date == date.today()
    ).first()

    if today_projection:
        today_projection.due_date = date.today() + timedelta(days=1)
    else:
        # Errands and deadlines have no projection — snooze them until tomorrow
        task.snooze_until = str(date.today() + timedelta(days=1))

    log = ActionLog(
        action_type="defer",
        task_id=task_id,
        task_title=task_title,
        task_snapshot=task_snap,
        projections_snapshot=proj_snap,
    )
    db.add(log)
    _prune_old_logs(db)
    db.flush()
    log_id = log.id

    db.commit()
    return Response(status_code=200, headers={"HX-Trigger": _undo_trigger(log_id, f"'{task_title}' deferred")})


@router.get("/{task_id}/delete-confirm", response_class=HTMLResponse)
async def delete_confirm_modal(request: Request, task_id: str, db: Session = Depends(get_db)):
    task = db.query(Task).filter(Task.id == task_id).first()
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    return templates.TemplateResponse(
        request,
        "components/delete_confirm_modal.html",
        {"task": task},
    )


@router.post("/{task_id}/delete-instance")
async def delete_task_instance(task_id: str, db: Session = Depends(get_db)):
    """
    "Delete today only" from the recurring-delete-modal flow.

    Records a ProjectionExclusion tombstone (like /week/projection/{task_id})
    so today's occurrence isn't resurrected by a later projection
    regeneration. Undo removes the tombstone again — see
    app/routers/undo.py::_undo_delete_instance.
    """
    task = db.query(Task).filter(Task.id == task_id).first()
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")

    today = date.today()
    deleted = db.query(Projection).filter(
        Projection.task_id == task_id,
        Projection.due_date == today,
    ).delete()

    if deleted == 0:
        raise HTTPException(status_code=404, detail="No projection for today")

    if not db.query(ProjectionExclusion).filter(
        ProjectionExclusion.task_id == task_id,
        ProjectionExclusion.due_date == today,
    ).first():
        db.add(ProjectionExclusion(task_id=task_id, due_date=today))

    task_title = task.title
    log = ActionLog(
        action_type="delete_instance",
        task_id=task_id,
        task_title=task_title,
        projections_snapshot=json.dumps([today.isoformat()]),
    )
    db.add(log)
    _prune_old_logs(db)
    db.flush()
    log_id = log.id

    db.commit()
    return Response(
        status_code=200,
        headers={"HX-Trigger": _undo_trigger(log_id, f"'{task_title}' skipped today")},
    )


@router.delete("/{task_id}", response_class=HTMLResponse)
async def delete_task(request: Request, task_id: str, db: Session = Depends(get_db)):
    """
    Delete a task entirely.
    
    Does NOT record in completed_tasks (no historical record).
    Also removes associated recurrence rules and projections.
    """
    task = db.query(Task).filter(Task.id == task_id).first()
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")

    task_title = task.title
    task_snap = json.dumps(task_to_dict(task))
    rec = db.query(Recurrence).filter(Recurrence.task_id == task_id).first()
    rec_snap = json.dumps(recurrence_to_dict(rec)) if rec else None
    proj_snap = json.dumps(projections_to_list(
        db.query(Projection).filter(Projection.task_id == task_id).all()
    ))

    db.query(Projection).filter(Projection.task_id == task_id).delete()
    db.query(ProjectionExclusion).filter(ProjectionExclusion.task_id == task_id).delete()
    db.query(Recurrence).filter(Recurrence.task_id == task_id).delete()
    db.delete(task)

    log = ActionLog(
        action_type="delete",
        task_id=task_id,
        task_title=task_title,
        task_snapshot=task_snap,
        recurrence_snapshot=rec_snap,
        projections_snapshot=proj_snap,
    )
    db.add(log)
    _prune_old_logs(db)
    db.flush()
    log_id = log.id

    db.commit()
    return Response(status_code=200, headers={"HX-Trigger": _undo_trigger(log_id, f"'{task_title}' deleted")})


@router.get("/{task_id}/edit", response_class=HTMLResponse)
async def edit_task_form(request: Request, task_id: str, db: Session = Depends(get_db)):
    """Return the edit form modal for a task."""
    task = db.query(Task).filter(Task.id == task_id).first()
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")

    recurrence = db.query(Recurrence).filter(Recurrence.task_id == task_id).first()
    tags = db.query(Tag).order_by(Tag.name).all()
    task_tag_ids = [tt.tag_id for tt in db.query(TaskTag).filter(TaskTag.task_id == task_id).all()]

    return templates.TemplateResponse(
        request,
        "components/task_edit_form.html",
        {"task": task, "recurrence": recurrence, "tags": tags, "task_tag_ids": task_tag_ids},
    )


def _replace_task_for_type_change(db: Session, old_task: Task, task_data: TaskUpdate) -> Task:
    """
    Handle a task *type* change (e.g. errand -> recurring) safely.

    Root cause this works around: the previous in-place `task.type = new_type`
    update left behind whatever type-specific rows the OLD type had, and
    never created what the NEW type needed:
    - Switching AWAY from recurring/variable_recurring/workout left its
      Recurrence + Projection rows in place. get_fixed_tasks/get_flexible_tasks
      (app/services/prioritisation.py) match projections to tasks by
      task_id alone, with no task.type filter, so the task could keep
      appearing in the daily list via the stale projection *in addition* to
      appearing under its new type — a duplicate.
    - Switching INTO recurring/variable_recurring/workout never called
      generate_projections, so the task got a Recurrence rule but zero
      Projection rows and simply never appeared anywhere — a silent no-op.
    Ross's "silently fails, unclear if duplicate or no-op" report matches
    both symptoms, depending on the direction of the change.

    Fix follows the agreed protocol: build and validate the new task's
    fields *before* writing anything, so an invalid change (e.g. switching
    to "appointment" with no scheduled_at) fails loudly with a 422 and
    leaves the original task completely untouched. Once validated, a new
    Task row is created with a fresh id and proper recurrence/projections/
    tags/notifications (mirroring create_task's logic exactly), and only
    then is the old task — and its recurrence/projections/exclusions/
    notifications/tags — deleted.

    Limitation (documented, not silently swallowed): this path does not
    support undo. The generic "edit" undo only restores plain columns on
    whatever row still has the logged task_id; it has no way to safely
    reinstate a deleted task's id, recurrence rows or projections. Rather
    than offer a half-working undo button, a type-change edit simply
    doesn't set X-Undo-Log-Id, so the frontend shows no undo toast for it.
    """
    new_type = task_data.type.value

    if new_type == "appointment" and not task_data.scheduled_at:
        raise HTTPException(
            status_code=422,
            detail="Changing this task to an appointment requires a scheduled date & time.",
        )
    if new_type == "deadline" and not task_data.deadline_at:
        raise HTTPException(
            status_code=422,
            detail="Changing this task to a deadline requires a deadline date & time.",
        )
    if new_type in ("recurring", "workout", "variable_recurring") and not task_data.recurrence:
        raise HTTPException(
            status_code=422,
            detail="Changing this task to a recurring type requires a recurrence rule.",
        )

    existing_tag_ids = [tt.tag_id for tt in db.query(TaskTag).filter(TaskTag.task_id == old_task.id).all()]
    tag_ids = task_data.tag_ids if task_data.tag_ids is not None else existing_tag_ids

    new_task = Task(
        type=new_type,
        title=task_data.title if task_data.title is not None else old_task.title,
        notes=task_data.notes if task_data.notes is not None else old_task.notes,
        estimated_duration=(
            task_data.estimated_duration if task_data.estimated_duration is not None
            else old_task.estimated_duration
        ),
        importance=task_data.importance if task_data.importance is not None else old_task.importance,
        urgency=task_data.urgency if task_data.urgency is not None else old_task.urgency,
        allow_afternoon=(
            task_data.allow_afternoon if task_data.allow_afternoon is not None
            else old_task.allow_afternoon
        ),
        # Type-specific date fields don't carry across a type change — an
        # old deadline_at is meaningless once the task is an appointment.
        deadline_at=task_data.deadline_at if new_type == "deadline" else None,
        scheduled_at=task_data.scheduled_at if new_type == "appointment" else None,
        prep_duration=task_data.prep_duration if task_data.prep_duration is not None else old_task.prep_duration,
        scheduled_time=(
            task_data.scheduled_time if task_data.scheduled_time is not None else old_task.scheduled_time
        ),
        location=task_data.location if task_data.location is not None else old_task.location,
        preset_id=old_task.preset_id,
        allowed_days=task_data.allowed_days if task_data.allowed_days is not None else old_task.allowed_days,
        status="pending",
    )
    db.add(new_task)
    db.flush()

    if new_type in ("recurring", "workout"):
        recurrence = Recurrence(
            task_id=new_task.id,
            interval_type=task_data.recurrence.interval_type,
            interval_multiple=task_data.recurrence.interval_multiple,
            day_of_week=task_data.recurrence.day_of_week,
            day_of_month=task_data.recurrence.day_of_month,
            month_of_year=task_data.recurrence.month_of_year,
            start_date=task_data.recurrence.start_date.date() if task_data.recurrence.start_date else date.today(),
            end_date=task_data.recurrence.end_date.date() if task_data.recurrence.end_date else None,
        )
        db.add(recurrence)
        db.flush()
        start = recurrence.start_date or date.today()
        end = start + timedelta(days=90)
        for projection in generate_projections(db, recurrence, start, end):
            db.add(projection)
    elif new_type == "variable_recurring":
        recurrence = Recurrence(
            task_id=new_task.id,
            interval_type=task_data.recurrence.interval_type,
            interval_multiple=task_data.recurrence.interval_multiple,
            day_of_week=task_data.recurrence.day_of_week,
            day_of_month=task_data.recurrence.day_of_month,
            month_of_year=task_data.recurrence.month_of_year,
            start_date=task_data.recurrence.start_date.date() if task_data.recurrence.start_date else date.today(),
            end_date=task_data.recurrence.end_date.date() if task_data.recurrence.end_date else None,
        )
        db.add(recurrence)
        db.flush()
        start = recurrence.start_date or date.today()
        db.add(Projection(task_id=new_task.id, due_date=start))

    for tag_id in tag_ids:
        db.add(TaskTag(task_id=new_task.id, tag_id=tag_id))

    if new_type == "appointment" and task_data.notification_offsets:
        _sync_task_notifications(db, new_task, task_data.notification_offsets)

    # The old task is fully superseded by new_task above — remove it and
    # everything keyed to its id.
    db.query(TaskNotification).filter(TaskNotification.task_id == old_task.id).delete()
    db.query(TaskTag).filter(TaskTag.task_id == old_task.id).delete()
    db.query(Projection).filter(Projection.task_id == old_task.id).delete()
    db.query(ProjectionExclusion).filter(ProjectionExclusion.task_id == old_task.id).delete()
    db.query(Recurrence).filter(Recurrence.task_id == old_task.id).delete()
    db.delete(old_task)

    db.commit()
    db.refresh(new_task)
    return new_task


@router.put("/{task_id}", response_model=TaskResponse)
async def update_task(task_id: str, task_data: TaskUpdate, response: Response, db: Session = Depends(get_db)):
    """
    Update an existing task.

    A change to `type` is delegated to _replace_task_for_type_change, which
    replaces the task rather than mutating it in place — see that
    function's docstring for why an in-place type change was unsafe.
    """
    task = db.query(Task).filter(Task.id == task_id).first()
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")

    if task_data.type is not None and task_data.type.value != task.type:
        return _replace_task_for_type_change(db, task, task_data)

    task_snap = json.dumps(task_to_dict(task))
    task_title = task.title

    if task_data.title is not None:
        task.title = task_data.title
    if task_data.type is not None:
        task.type = task_data.type.value
    if task_data.notes is not None:
        task.notes = task_data.notes
    if task_data.estimated_duration is not None:
        task.estimated_duration = task_data.estimated_duration
    if task_data.importance is not None:
        task.importance = task_data.importance
    if task_data.urgency is not None:
        task.urgency = task_data.urgency
    if task_data.allow_afternoon is not None:
        task.allow_afternoon = task_data.allow_afternoon
    if task_data.deadline_at is not None:
        task.deadline_at = task_data.deadline_at
    if task_data.scheduled_at is not None:
        task.scheduled_at = task_data.scheduled_at
    if task_data.prep_duration is not None:
        task.prep_duration = task_data.prep_duration
    if task_data.scheduled_time is not None:
        task.scheduled_time = task_data.scheduled_time
    if task_data.location is not None:
        task.location = task_data.location
    if task_data.allowed_days is not None:
        task.allowed_days = task_data.allowed_days
    if task_data.preset_id is not None:
        task.preset_id = task_data.preset_id
    if task_data.tag_ids is not None:
        db.query(TaskTag).filter(TaskTag.task_id == task_id).delete()
        for tag_id in task_data.tag_ids:
            db.add(TaskTag(task_id=task_id, tag_id=tag_id))
    if task_data.notification_offsets is not None:
        _sync_task_notifications(db, task, task_data.notification_offsets)

    if task_data.recurrence:
        existing_recurrence = db.query(Recurrence).filter(Recurrence.task_id == task_id).first()
        if existing_recurrence:
            existing_recurrence.interval_type = task_data.recurrence.interval_type
            existing_recurrence.interval_multiple = task_data.recurrence.interval_multiple
            existing_recurrence.day_of_week = task_data.recurrence.day_of_week
            existing_recurrence.day_of_month = task_data.recurrence.day_of_month
            existing_recurrence.month_of_year = task_data.recurrence.month_of_year
            if task_data.recurrence.start_date:
                existing_recurrence.start_date = task_data.recurrence.start_date.date()
            if task_data.recurrence.end_date:
                existing_recurrence.end_date = task_data.recurrence.end_date.date()
        else:
            # Task had no recurrence row before (e.g. it's newly recurring
            # via a same-type edit that only just added a recurrence).
            # Also generate its projections here — a bare Recurrence row
            # with no Projection rows would never show up in the daily or
            # weekly view.
            recurrence = Recurrence(
                task_id=task.id,
                interval_type=task_data.recurrence.interval_type,
                interval_multiple=task_data.recurrence.interval_multiple,
                day_of_week=task_data.recurrence.day_of_week,
                day_of_month=task_data.recurrence.day_of_month,
                month_of_year=task_data.recurrence.month_of_year,
                start_date=task_data.recurrence.start_date.date() if task_data.recurrence.start_date else date.today(),
                end_date=task_data.recurrence.end_date.date() if task_data.recurrence.end_date else None,
            )
            db.add(recurrence)
            db.flush()
            start = recurrence.start_date or date.today()
            end = start + timedelta(days=90)
            for projection in generate_projections(db, recurrence, start, end):
                db.add(projection)

    log = ActionLog(
        action_type="edit",
        task_id=task_id,
        task_title=task_title,
        task_snapshot=task_snap,
    )
    db.add(log)
    _prune_old_logs(db)
    db.flush()
    response.headers["X-Undo-Log-Id"] = str(log.id)

    db.commit()
    db.refresh(task)
    return task
