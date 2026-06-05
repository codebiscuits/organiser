from datetime import date, time, datetime, timedelta
from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, Response
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.task import Task, CompletedTask
from app.models.recurrence import Recurrence, Projection
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


class TaskTimelinePosition(BaseModel):
    task_id: str
    scheduled_hour: int
    scheduled_minute: int


class TimelineReorderRequest(BaseModel):
    tasks: list[TaskTimelinePosition]


@router.get("/", response_class=HTMLResponse)
async def list_tasks(request: Request, db: Session = Depends(get_db)):
    """Get prioritised task list for today."""
    prioritised_tasks, capacity = get_prioritised_tasks_with_metadata(db, date.today())
    return templates.TemplateResponse(
        request,
        "components/task_list.html",
        {"tasks": prioritised_tasks, "capacity": capacity},
    )


@router.get("/current", response_class=HTMLResponse)
async def current_task(request: Request, db: Session = Depends(get_db)):
    """Get the current (highest priority) task for today."""
    prioritised_tasks, _ = get_prioritised_tasks_with_metadata(db, date.today())
    current = prioritised_tasks[0] if prioritised_tasks else None
    return templates.TemplateResponse(
        request,
        "components/current_task.html",
        {"task": current},
    )


@router.get("/upcoming", response_class=HTMLResponse)
async def upcoming_tasks(request: Request, db: Session = Depends(get_db)):
    """Get upcoming tasks (all except current)."""
    prioritised_tasks, capacity = get_prioritised_tasks_with_metadata(db, date.today())
    return templates.TemplateResponse(
        request,
        "components/task_list.html",
        {"tasks": prioritised_tasks[1:], "capacity": capacity},
    )


@router.get("/new", response_class=HTMLResponse)
async def new_task_form(request: Request):
    """Return the task creation form modal."""
    return templates.TemplateResponse(request, "components/task_form.html")


@router.get("/all", response_model=list[TaskResponse])
async def get_all_tasks(db: Session = Depends(get_db)):
    """Get all tasks (API endpoint)."""
    tasks = db.query(Task).all()
    return tasks


@router.get("/timeline", response_class=HTMLResponse)
async def timeline_view(request: Request, db: Session = Depends(get_db)):
    """
    Timeline view for today's scheduled tasks.
    
    Displays tasks on a visual timeline where users can drag and drop
    to reorder flexible tasks.
    """
    prioritised_tasks, _ = get_prioritised_tasks_with_metadata(db, date.today())
    return templates.TemplateResponse(
        request,
        "timeline.html",
        {"tasks": prioritised_tasks, "today": date.today()},
    )


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
    """
    projection_date = datetime.strptime(date, "%Y-%m-%d").date()
    
    deleted = db.query(Projection).filter(
        Projection.task_id == task_id,
        Projection.due_date == projection_date
    ).delete()
    
    db.commit()
    
    if deleted == 0:
        raise HTTPException(status_code=404, detail="Projection not found")
    
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

    if task_data.type in ("recurring", "variable_recurring", "workout") and task_data.recurrence:
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


@router.put("/{task_id}", response_model=TaskResponse)
async def update_task(task_id: str, task_data: TaskUpdate, db: Session = Depends(get_db)):
    """Update an existing task."""
    task = db.query(Task).filter(Task.id == task_id).first()
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")

    update_data = task_data.model_dump(exclude_unset=True)
    for field, value in update_data.items():
        setattr(task, field, value)

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

    completed = CompletedTask(
        task_id=task.id,
        actual_duration=completion_data.actual_duration if completion_data else None,
        notes=completion_data.notes if completion_data else None,
    )
    db.add(completed)

    db.query(Projection).filter(
        Projection.task_id == task_id,
        Projection.due_date == date.today()
    ).delete()

    if task.type in ("errand", "appointment", "deadline"):
        db.delete(task)
    db.commit()
    
    return Response(status_code=200, headers={"HX-Trigger": "taskUpdated"})


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

    completed = CompletedTask(
        task_id=task.id,
        actual_duration=actual_duration,
        notes=notes,
    )
    db.add(completed)

    db.query(Projection).filter(
        Projection.task_id == task_id,
        Projection.due_date == date.today()
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

    db.commit()
    return Response(status_code=200, headers={"HX-Trigger": "taskUpdated"})


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

    completed = CompletedTask(
        task_id=task.id,
    )
    db.add(completed)

    performed_set = PerformedSet(
        exercise_id=exercise_id,
        reps=reps,
        weight_kg=weight_kg,
        num_sets=sets,
        intensity=intensity,
    )
    db.add(performed_set)

    db.query(Projection).filter(
        Projection.task_id == task_id,
        Projection.due_date == date.today()
    ).delete()

    db.commit()
    return Response(status_code=200, headers={"HX-Trigger": "taskUpdated"})


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

    db.commit()
    
    return Response(status_code=200, headers={"HX-Trigger": "taskUpdated"})


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

    db.query(Projection).filter(Projection.task_id == task_id).delete()
    db.query(Recurrence).filter(Recurrence.task_id == task_id).delete()
    db.delete(task)

    db.commit()
    
    # Return refreshed task list
    prioritised_tasks, capacity = get_prioritised_tasks_with_metadata(db, date.today())
    return templates.TemplateResponse(
        request,
        "components/task_list.html",
        {"tasks": prioritised_tasks, "capacity": capacity},
    )


@router.get("/{task_id}/edit", response_class=HTMLResponse)
async def edit_task_form(request: Request, task_id: str, db: Session = Depends(get_db)):
    """Return the edit form modal for a task."""
    task = db.query(Task).filter(Task.id == task_id).first()
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    
    recurrence = db.query(Recurrence).filter(Recurrence.task_id == task_id).first()
    
    return templates.TemplateResponse(
        request,
        "components/task_edit_form.html",
        {"task": task, "recurrence": recurrence},
    )


@router.put("/{task_id}", response_model=TaskResponse)
async def update_task(task_id: str, task_data: TaskUpdate, db: Session = Depends(get_db)):
    """Update an existing task."""
    task = db.query(Task).filter(Task.id == task_id).first()
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    
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
    
    db.commit()
    db.refresh(task)
    return task
