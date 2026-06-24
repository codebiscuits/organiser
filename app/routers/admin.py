from collections import OrderedDict
from datetime import datetime, date, time
from typing import List, Optional

from fastapi import APIRouter, Depends, Form, HTTPException, Query, Request, Response
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session, joinedload

from app.database import get_db
from app.models.recurrence import Projection, Recurrence
from app.services.recurrence import generate_projections, refresh_projections
from datetime import timedelta
from app.models.task import CompletedTask, Task
from app.models.preset import TaskPreset
from app.models.user import User
from app.models.workout import Exercise, ExerciseMuscle, MuscleGroup, PerformedSet
from app.models.tag import Tag, TaskTag

router = APIRouter()
templates = Jinja2Templates(directory="app/templates")

ITEMS_PER_PAGE = 20


def get_or_create_user(db: Session) -> User:
    user = db.query(User).first()
    if not user:
        user = User()
        db.add(user)
        db.commit()
        db.refresh(user)
    return user


@router.get("/")
async def admin_dashboard(request: Request, db: Session = Depends(get_db)):
    return templates.TemplateResponse(request, "admin/dashboard.html")


@router.post("/refresh-projections")
async def admin_refresh_projections(db: Session = Depends(get_db)):
    """
    Regenerate all projections for recurring tasks.
    Clears existing future projections and regenerates them with corrected day-of-week logic.
    """
    from datetime import date, timedelta
    
    today = date.today()
    
    db.query(Projection).filter(Projection.due_date >= today).delete()
    db.commit()
    
    refresh_projections(db, months_ahead=3)
    
    count = db.query(Projection).filter(Projection.due_date >= today).count()
    
    return {"status": "ok", "projections_created": count}


# ============== TASKS ==============

@router.get("/tasks")
async def admin_tasks(
    request: Request,
    db: Session = Depends(get_db),
    type: Optional[str] = None,
):
    query = db.query(Task)
    if type:
        query = query.filter(Task.type == type)
    tasks = query.order_by(Task.created_at.desc()).all()
    return templates.TemplateResponse(
        request,
        "admin/tasks.html",
        {"tasks": tasks, "filter_type": type},
    )


@router.get("/tasks/new")
async def admin_task_new(request: Request, db: Session = Depends(get_db)):
    return templates.TemplateResponse(
        request,
        "admin/task_form.html",
        {"task": None, "recurrence": None},
    )


@router.post("/tasks")
async def admin_task_create(
    request: Request,
    db: Session = Depends(get_db),
    title: str = Form(...),
    type: str = Form(...),
    notes: Optional[str] = Form(None),
    estimated_duration: Optional[int] = Form(None),
    importance: Optional[int] = Form(None),
    urgency: Optional[int] = Form(None),
    allow_afternoon: Optional[str] = Form(None),
    scheduled_at: Optional[str] = Form(None),
    prep_duration: Optional[int] = Form(None),
    location: Optional[str] = Form(None),
    deadline_at: Optional[str] = Form(None),
    interval_type: Optional[str] = Form(None),
    interval_multiple: Optional[int] = Form(1),
    day_of_week: Optional[List[str]] = Form(None),
    day_of_month: Optional[str] = Form(None),
    month_of_year: Optional[str] = Form(None),
    start_date: Optional[str] = Form(None),
    end_date: Optional[str] = Form(None),
    scheduled_time: Optional[str] = Form(None),
):
    task = Task(
        title=title,
        type=type,
        notes=notes or None,
        estimated_duration=estimated_duration,
        importance=importance,
        urgency=urgency,
        allow_afternoon=allow_afternoon == "true",
        scheduled_at=datetime.fromisoformat(scheduled_at) if scheduled_at else None,
        prep_duration=prep_duration,
        location=location or None,
        deadline_at=datetime.fromisoformat(deadline_at) if deadline_at else None,
        scheduled_time=time.fromisoformat(scheduled_time) if scheduled_time else None,
    )
    db.add(task)
    db.commit()
    db.refresh(task)

    if type in ("recurring", "variable_recurring", "workout") and interval_type:
        recurrence = Recurrence(
            task_id=task.id,
            interval_type=interval_type,
            interval_multiple=interval_multiple or 1,
            day_of_week=",".join(day_of_week) if day_of_week else None,
            day_of_month=day_of_month or None,
            month_of_year=month_of_year or None,
            start_date=date.fromisoformat(start_date) if start_date else None,
            end_date=date.fromisoformat(end_date) if end_date else None,
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
        db.commit()

    return RedirectResponse(url="/admin/tasks", status_code=303)


@router.get("/tasks/{task_id}/edit")
async def admin_task_edit(
    request: Request,
    task_id: str,
    db: Session = Depends(get_db),
):
    task = db.query(Task).filter(Task.id == task_id).first()
    if not task:
        return RedirectResponse(url="/admin/tasks", status_code=303)

    recurrence = db.query(Recurrence).filter(Recurrence.task_id == task_id).first()
    return templates.TemplateResponse(
        request,
        "admin/task_form.html",
        {"task": task, "recurrence": recurrence},
    )


@router.post("/tasks/{task_id}")
async def admin_task_update(
    request: Request,
    task_id: str,
    db: Session = Depends(get_db),
    title: str = Form(...),
    type: str = Form(...),
    notes: Optional[str] = Form(None),
    estimated_duration: Optional[int] = Form(None),
    importance: Optional[int] = Form(None),
    urgency: Optional[int] = Form(None),
    allow_afternoon: Optional[str] = Form(None),
    scheduled_at: Optional[str] = Form(None),
    prep_duration: Optional[int] = Form(None),
    location: Optional[str] = Form(None),
    deadline_at: Optional[str] = Form(None),
    interval_type: Optional[str] = Form(None),
    interval_multiple: Optional[int] = Form(1),
    day_of_week: Optional[List[str]] = Form(None),
    day_of_month: Optional[str] = Form(None),
    month_of_year: Optional[str] = Form(None),
    start_date: Optional[str] = Form(None),
    end_date: Optional[str] = Form(None),
    scheduled_time: Optional[str] = Form(None),
):
    task = db.query(Task).filter(Task.id == task_id).first()
    if not task:
        return RedirectResponse(url="/admin/tasks", status_code=303)

    task.title = title
    task.type = type
    task.notes = notes or None
    task.estimated_duration = estimated_duration
    task.importance = importance
    task.urgency = urgency
    task.allow_afternoon = allow_afternoon == "true"
    task.scheduled_at = datetime.fromisoformat(scheduled_at) if scheduled_at else None
    task.prep_duration = prep_duration
    task.location = location or None
    task.deadline_at = datetime.fromisoformat(deadline_at) if deadline_at else None
    task.scheduled_time = time.fromisoformat(scheduled_time) if scheduled_time else None

    existing_recurrence = db.query(Recurrence).filter(Recurrence.task_id == task_id).first()

    if type in ("recurring", "variable_recurring", "workout") and interval_type:
        if existing_recurrence:
            existing_recurrence.interval_type = interval_type
            existing_recurrence.interval_multiple = interval_multiple or 1
            existing_recurrence.day_of_week = ",".join(day_of_week) if day_of_week else None
            existing_recurrence.day_of_month = day_of_month or None
            existing_recurrence.month_of_year = month_of_year or None
            existing_recurrence.start_date = date.fromisoformat(start_date) if start_date else None
            existing_recurrence.end_date = date.fromisoformat(end_date) if end_date else None
            recurrence = existing_recurrence
        else:
            recurrence = Recurrence(
                task_id=task.id,
                interval_type=interval_type,
                interval_multiple=interval_multiple or 1,
                day_of_week=",".join(day_of_week) if day_of_week else None,
                day_of_month=day_of_month or None,
                month_of_year=month_of_year or None,
                start_date=date.fromisoformat(start_date) if start_date else None,
                end_date=date.fromisoformat(end_date) if end_date else None,
            )
            db.add(recurrence)
        
        db.flush()
        db.query(Projection).filter(Projection.task_id == task_id).delete()
        start = recurrence.start_date or date.today()
        end = start + timedelta(days=90)
        projections = generate_projections(db, recurrence, start, end)
        for projection in projections:
            db.add(projection)
    elif existing_recurrence:
        db.delete(existing_recurrence)
        db.query(Projection).filter(Projection.task_id == task_id).delete()

    db.commit()
    return RedirectResponse(url="/admin/tasks", status_code=303)


@router.delete("/tasks/{task_id}")
async def admin_task_delete(task_id: str, db: Session = Depends(get_db)):
    task = db.query(Task).filter(Task.id == task_id).first()
    if task:
        db.query(Recurrence).filter(Recurrence.task_id == task_id).delete()
        db.query(Projection).filter(Projection.task_id == task_id).delete()
        db.delete(task)
        db.commit()
    return Response(status_code=200)


# ============== PRESETS ==============

@router.get("/presets")
async def admin_presets(request: Request, db: Session = Depends(get_db)):
    presets = db.query(TaskPreset).order_by(TaskPreset.name).all()
    return templates.TemplateResponse(
        request,
        "admin/presets.html",
        {"presets": presets},
    )


@router.get("/presets/new")
async def admin_preset_new(request: Request):
    return templates.TemplateResponse(
        request,
        "admin/preset_form.html",
        {"preset": None},
    )


@router.post("/presets")
async def admin_preset_create(
    request: Request,
    db: Session = Depends(get_db),
    name: str = Form(...),
    type: str = Form(...),
    title: Optional[str] = Form(None),
    notes: Optional[str] = Form(None),
    estimated_duration: Optional[int] = Form(None),
    importance: Optional[int] = Form(None),
    urgency: Optional[int] = Form(None),
    allow_afternoon: Optional[str] = Form(None),
    prep_duration: Optional[int] = Form(None),
    scheduled_time: Optional[str] = Form(None),
    location: Optional[str] = Form(None),
    interval_type: Optional[str] = Form(None),
    interval_multiple: Optional[int] = Form(1),
    day_of_week: Optional[List[str]] = Form(None),
    day_of_month: Optional[str] = Form(None),
    month_of_year: Optional[str] = Form(None),
    allowed_days: Optional[List[str]] = Form(None),
):
    preset = TaskPreset(
        name=name,
        type=type,
        title=title or None,
        notes=notes or None,
        estimated_duration=estimated_duration,
        importance=importance,
        urgency=urgency,
        allow_afternoon=allow_afternoon == "true",
        prep_duration=prep_duration,
        scheduled_time=time.fromisoformat(scheduled_time) if scheduled_time else None,
        location=location or None,
        interval_type=interval_type or None,
        interval_multiple=interval_multiple or 1,
        day_of_week=",".join(day_of_week) if day_of_week else None,
        day_of_month=day_of_month or None,
        month_of_year=month_of_year or None,
        allowed_days=",".join(allowed_days) if allowed_days else None,
    )
    db.add(preset)
    db.commit()
    return RedirectResponse(url="/admin/presets", status_code=303)


@router.get("/presets/{preset_id}/edit")
async def admin_preset_edit(
    request: Request,
    preset_id: int,
    db: Session = Depends(get_db),
):
    preset = db.query(TaskPreset).filter(TaskPreset.id == preset_id).first()
    if not preset:
        return RedirectResponse(url="/admin/presets", status_code=303)
    return templates.TemplateResponse(
        request,
        "admin/preset_form.html",
        {"preset": preset},
    )


@router.post("/presets/{preset_id}")
async def admin_preset_update(
    request: Request,
    preset_id: int,
    db: Session = Depends(get_db),
    name: str = Form(...),
    type: str = Form(...),
    title: Optional[str] = Form(None),
    notes: Optional[str] = Form(None),
    estimated_duration: Optional[int] = Form(None),
    importance: Optional[int] = Form(None),
    urgency: Optional[int] = Form(None),
    allow_afternoon: Optional[str] = Form(None),
    prep_duration: Optional[int] = Form(None),
    scheduled_time: Optional[str] = Form(None),
    location: Optional[str] = Form(None),
    interval_type: Optional[str] = Form(None),
    interval_multiple: Optional[int] = Form(1),
    day_of_week: Optional[List[str]] = Form(None),
    day_of_month: Optional[str] = Form(None),
    month_of_year: Optional[str] = Form(None),
    allowed_days: Optional[List[str]] = Form(None),
):
    preset = db.query(TaskPreset).filter(TaskPreset.id == preset_id).first()
    if not preset:
        return RedirectResponse(url="/admin/presets", status_code=303)

    preset.name = name
    preset.type = type
    preset.title = title or None
    preset.notes = notes or None
    preset.estimated_duration = estimated_duration
    preset.importance = importance
    preset.urgency = urgency
    preset.allow_afternoon = allow_afternoon == "true"
    preset.prep_duration = prep_duration
    preset.scheduled_time = time.fromisoformat(scheduled_time) if scheduled_time else None
    preset.location = location or None
    preset.interval_type = interval_type or None
    preset.interval_multiple = interval_multiple or 1
    preset.day_of_week = ",".join(day_of_week) if day_of_week else None
    preset.day_of_month = day_of_month or None
    preset.month_of_year = month_of_year or None
    preset.allowed_days = ",".join(allowed_days) if allowed_days else None

    db.commit()
    return RedirectResponse(url="/admin/presets", status_code=303)


@router.delete("/presets/{preset_id}")
async def admin_preset_delete(preset_id: int, db: Session = Depends(get_db)):
    preset = db.query(TaskPreset).filter(TaskPreset.id == preset_id).first()
    if preset:
        db.delete(preset)
        db.commit()
    return Response(status_code=200)


# ============== MUSCLE GROUPS ==============

@router.get("/muscle-groups")
async def admin_muscle_groups(request: Request, db: Session = Depends(get_db)):
    muscle_groups = db.query(MuscleGroup).order_by(MuscleGroup.name).all()
    return templates.TemplateResponse(
        request,
        "admin/muscle_groups.html",
        {"muscle_groups": muscle_groups},
    )


@router.post("/muscle-groups")
async def admin_muscle_group_create(
    request: Request,
    db: Session = Depends(get_db),
    name: str = Form(...),
    recovery_time: int = Form(...),
):
    mg = MuscleGroup(name=name, recovery_time=recovery_time)
    db.add(mg)
    db.commit()
    db.refresh(mg)
    html = f"""
    <div class="list-item" id="muscle-group-{mg.id}">
        <span class="item-name">{mg.name}</span>
        <form class="inline-edit" hx-put="/admin/muscle-groups/{mg.id}" hx-swap="none">
            <label>
                <span class="label-text">Recovery:</span>
                <input type="number" name="recovery_time" value="{mg.recovery_time}" min="1" class="small-input" hx-put="/admin/muscle-groups/{mg.id}" hx-trigger="change" hx-swap="none">
                <span class="label-text">days</span>
            </label>
        </form>
        <button class="btn-delete" hx-delete="/admin/muscle-groups/{mg.id}" hx-target="#muscle-group-{mg.id}" hx-swap="outerHTML" hx-confirm="Delete {mg.name}?">×</button>
    </div>
    """
    return Response(content=html, media_type="text/html")


@router.put("/muscle-groups/{mg_id}")
async def admin_muscle_group_update(
    mg_id: int,
    db: Session = Depends(get_db),
    recovery_time: int = Form(...),
):
    mg = db.query(MuscleGroup).filter(MuscleGroup.id == mg_id).first()
    if mg:
        mg.recovery_time = recovery_time
        db.commit()
    return Response(status_code=200)


@router.delete("/muscle-groups/{mg_id}")
async def admin_muscle_group_delete(mg_id: int, db: Session = Depends(get_db)):
    mg = db.query(MuscleGroup).filter(MuscleGroup.id == mg_id).first()
    if mg:
        db.query(ExerciseMuscle).filter(ExerciseMuscle.muscle_id == mg_id).delete()
        db.delete(mg)
        db.commit()
    return Response(status_code=200)


# ============== EXERCISES ==============

@router.get("/exercises")
async def admin_exercises(request: Request, db: Session = Depends(get_db)):
    exercises = db.query(Exercise).order_by(Exercise.name).all()
    for ex in exercises:
        muscle_ids = [em.muscle_id for em in db.query(ExerciseMuscle).filter(ExerciseMuscle.exercise_id == ex.id).all()]
        ex.muscles = db.query(MuscleGroup).filter(MuscleGroup.id.in_(muscle_ids)).all() if muscle_ids else []
    return templates.TemplateResponse(
        request,
        "admin/exercises.html",
        {"exercises": exercises},
    )


@router.get("/exercises/new")
async def admin_exercise_new(request: Request, db: Session = Depends(get_db)):
    muscle_groups = db.query(MuscleGroup).order_by(MuscleGroup.name).all()
    return templates.TemplateResponse(
        request,
        "admin/exercise_form.html",
        {"exercise": None, "muscle_groups": muscle_groups, "selected_muscle_ids": []},
    )


@router.post("/exercises")
async def admin_exercise_create(
    request: Request,
    db: Session = Depends(get_db),
    name: str = Form(...),
    description: Optional[str] = Form(None),
    intensity: str = Form(...),
    muscle_ids: Optional[List[int]] = Form(None),
):
    exercise = Exercise(name=name, description=description or None, intensity=intensity)
    db.add(exercise)
    db.commit()
    db.refresh(exercise)

    if muscle_ids:
        for mid in muscle_ids:
            em = ExerciseMuscle(exercise_id=exercise.id, muscle_id=mid)
            db.add(em)
        db.commit()

    return RedirectResponse(url="/admin/exercises", status_code=303)


@router.get("/exercises/{exercise_id}/edit")
async def admin_exercise_edit(
    request: Request,
    exercise_id: int,
    db: Session = Depends(get_db),
):
    exercise = db.query(Exercise).filter(Exercise.id == exercise_id).first()
    if not exercise:
        return RedirectResponse(url="/admin/exercises", status_code=303)

    muscle_groups = db.query(MuscleGroup).order_by(MuscleGroup.name).all()
    selected_muscle_ids = [
        em.muscle_id
        for em in db.query(ExerciseMuscle).filter(ExerciseMuscle.exercise_id == exercise_id).all()
    ]
    return templates.TemplateResponse(
        request,
        "admin/exercise_form.html",
        {
            "exercise": exercise,
            "muscle_groups": muscle_groups,
            "selected_muscle_ids": selected_muscle_ids,
        },
    )


@router.post("/exercises/{exercise_id}")
async def admin_exercise_update(
    request: Request,
    exercise_id: int,
    db: Session = Depends(get_db),
    name: str = Form(...),
    description: Optional[str] = Form(None),
    intensity: str = Form(...),
    muscle_ids: Optional[List[int]] = Form(None),
):
    exercise = db.query(Exercise).filter(Exercise.id == exercise_id).first()
    if not exercise:
        return RedirectResponse(url="/admin/exercises", status_code=303)

    exercise.name = name
    exercise.description = description or None
    exercise.intensity = intensity

    db.query(ExerciseMuscle).filter(ExerciseMuscle.exercise_id == exercise_id).delete()
    if muscle_ids:
        for mid in muscle_ids:
            em = ExerciseMuscle(exercise_id=exercise_id, muscle_id=mid)
            db.add(em)

    db.commit()
    return RedirectResponse(url="/admin/exercises", status_code=303)


@router.delete("/exercises/{exercise_id}")
async def admin_exercise_delete(exercise_id: int, db: Session = Depends(get_db)):
    exercise = db.query(Exercise).filter(Exercise.id == exercise_id).first()
    if exercise:
        db.query(ExerciseMuscle).filter(ExerciseMuscle.exercise_id == exercise_id).delete()
        db.delete(exercise)
        db.commit()
    return Response(status_code=200)


# ============== USER PREFERENCES ==============

@router.get("/user")
async def user_preferences(request: Request, db: Session = Depends(get_db)):
    user = get_or_create_user(db)
    return templates.TemplateResponse(
        request,
        "admin/user_preferences.html",
        {"user": user},
    )


@router.post("/user")
async def update_user_preferences(
    request: Request,
    email: Optional[str] = Form(None),
    available_hours_per_day: int = Form(6),
    db: Session = Depends(get_db),
):
    user = get_or_create_user(db)
    user.email = email
    user.available_hours_per_day = available_hours_per_day
    db.commit()
    return RedirectResponse(url="/admin/user", status_code=303)


# ============== HISTORY ==============

@router.get("/completed-tasks")
async def admin_completed_tasks(
    request: Request,
    db: Session = Depends(get_db),
    page: int = Query(1, ge=1),
    from_date: Optional[str] = None,
    to_date: Optional[str] = None,
):
    from sqlalchemy import func as sqlfunc
    query = db.query(
        CompletedTask.id,
        CompletedTask.task_id,
        CompletedTask.completed_at,
        CompletedTask.actual_duration,
        CompletedTask.notes,
        CompletedTask.auto_completed,
        sqlfunc.coalesce(CompletedTask.task_title, Task.title).label("task_title"),
    ).outerjoin(Task, CompletedTask.task_id == Task.id)

    if from_date:
        try:
            from_dt = datetime.strptime(from_date, "%Y-%m-%d")
            query = query.filter(CompletedTask.completed_at >= from_dt)
        except ValueError:
            pass
    if to_date:
        try:
            to_dt = datetime.strptime(to_date, "%Y-%m-%d")
            to_dt = to_dt.replace(hour=23, minute=59, second=59)
            query = query.filter(CompletedTask.completed_at <= to_dt)
        except ValueError:
            pass

    total = query.count()
    total_pages = max(1, (total + ITEMS_PER_PAGE - 1) // ITEMS_PER_PAGE)

    completed_tasks = (
        query.order_by(CompletedTask.completed_at.desc())
        .offset((page - 1) * ITEMS_PER_PAGE)
        .limit(ITEMS_PER_PAGE)
        .all()
    )

    return templates.TemplateResponse(
        request,
        "admin/completed_tasks.html",
        {
            "completed_tasks": completed_tasks,
            "page": page,
            "total_pages": total_pages,
            "from_date": from_date,
            "to_date": to_date,
        },
    )


@router.get("/workout-history")
async def admin_workout_history(request: Request, db: Session = Depends(get_db)):
    sets = (
        db.query(
            PerformedSet.id,
            PerformedSet.created_at,
            PerformedSet.reps,
            PerformedSet.weight_kg,
            PerformedSet.num_sets,
            PerformedSet.intensity,
            Exercise.name.label("exercise_name"),
        )
        .join(Exercise, PerformedSet.exercise_id == Exercise.id)
        .order_by(PerformedSet.created_at.desc())
        .all()
    )

    grouped_sets: OrderedDict = OrderedDict()
    for s in sets:
        date_str = s.created_at.strftime("%Y-%m-%d") if s.created_at else "Unknown"
        if date_str not in grouped_sets:
            grouped_sets[date_str] = []
        grouped_sets[date_str].append(s)

    exercises = db.query(Exercise).order_by(Exercise.name).all()
    today = date.today().isoformat()

    return templates.TemplateResponse(
        request,
        "admin/workout_history.html",
        {"grouped_sets": grouped_sets, "exercises": exercises, "today": today},
    )


@router.post("/workout-history/add")
async def admin_add_workout_history(
    request: Request,
    exercise_id: int = Form(...),
    performed_date: str = Form(...),
    performed_time: str = Form(None),
    reps: int = Form(...),
    weight_kg: float = Form(...),
    num_sets: int = Form(...),
    intensity: str = Form(...),
    db: Session = Depends(get_db),
):
    if performed_time:
        created_at = datetime.strptime(f"{performed_date} {performed_time}", "%Y-%m-%d %H:%M")
    else:
        created_at = datetime.strptime(performed_date, "%Y-%m-%d")

    performed_set = PerformedSet(
        exercise_id=exercise_id,
        created_at=created_at,
        reps=reps,
        weight_kg=weight_kg,
        num_sets=num_sets,
        intensity=intensity,
    )
    db.add(performed_set)
    db.commit()

    return RedirectResponse(url="/admin/workout-history", status_code=303)


@router.delete("/workout-history/{set_id}")
async def admin_delete_workout_history(set_id: int, db: Session = Depends(get_db)):
    performed_set = db.query(PerformedSet).filter(PerformedSet.id == set_id).first()
    if performed_set:
        db.delete(performed_set)
        db.commit()
    return Response(status_code=200)


@router.get("/workout-history/{set_id}/edit")
async def admin_edit_workout_history_form(
    request: Request,
    set_id: int,
    db: Session = Depends(get_db),
):
    performed_set = db.query(PerformedSet).filter(PerformedSet.id == set_id).first()
    if not performed_set:
        return Response(status_code=404)
    exercises = db.query(Exercise).order_by(Exercise.name).all()
    return templates.TemplateResponse(
        request,
        "admin/workout_history_edit_row.html",
        {"entry": performed_set, "exercises": exercises},
    )


@router.post("/workout-history/{set_id}")
async def admin_update_workout_history(
    request: Request,
    set_id: int,
    exercise_id: int = Form(...),
    num_sets: int = Form(...),
    reps: int = Form(...),
    weight_kg: float = Form(...),
    intensity: str = Form(...),
    db: Session = Depends(get_db),
):
    performed_set = db.query(PerformedSet).filter(PerformedSet.id == set_id).first()
    if not performed_set:
        return Response(status_code=404)
    performed_set.exercise_id = exercise_id
    performed_set.num_sets = num_sets
    performed_set.reps = reps
    performed_set.weight_kg = weight_kg
    performed_set.intensity = intensity
    db.commit()

    exercise_name = db.query(Exercise.name).filter(Exercise.id == exercise_id).scalar()
    return templates.TemplateResponse(
        request,
        "admin/workout_history_row.html",
        {"item": performed_set, "exercise_name": exercise_name},
    )


# ============== TAGS ==============

@router.get("/tags")
async def admin_tags_list(request: Request, db: Session = Depends(get_db)):
    tags = db.query(Tag).order_by(Tag.name).all()
    tag_use_counts = {
        tag.id: db.query(TaskTag).filter(TaskTag.tag_id == tag.id).count()
        for tag in tags
    }
    return templates.TemplateResponse(
        request,
        "admin/tags.html",
        {"tags": tags, "tag_use_counts": tag_use_counts},
    )


@router.get("/tags/new")
async def admin_tag_new(request: Request):
    return templates.TemplateResponse(request, "admin/tag_form.html", {"tag": None})


@router.post("/tags")
async def admin_tag_create(
    request: Request,
    db: Session = Depends(get_db),
    name: str = Form(...),
    icon: str = Form(...),
    color: str = Form(...),
):
    tag = Tag(name=name.strip(), icon=icon.strip(), color=color)
    db.add(tag)
    db.commit()
    return RedirectResponse(url="/admin/tags", status_code=303)


@router.get("/tags/{tag_id}/edit")
async def admin_tag_edit(request: Request, tag_id: int, db: Session = Depends(get_db)):
    tag = db.query(Tag).filter(Tag.id == tag_id).first()
    if not tag:
        raise HTTPException(status_code=404, detail="Tag not found")
    return templates.TemplateResponse(request, "admin/tag_form.html", {"tag": tag})


@router.post("/tags/{tag_id}")
async def admin_tag_update(
    request: Request,
    tag_id: int,
    db: Session = Depends(get_db),
    name: str = Form(...),
    icon: str = Form(...),
    color: str = Form(...),
):
    tag = db.query(Tag).filter(Tag.id == tag_id).first()
    if not tag:
        raise HTTPException(status_code=404, detail="Tag not found")
    tag.name = name.strip()
    tag.icon = icon.strip()
    tag.color = color
    db.commit()
    return RedirectResponse(url="/admin/tags", status_code=303)


@router.delete("/tags/{tag_id}")
async def admin_tag_delete(tag_id: int, db: Session = Depends(get_db)):
    tag = db.query(Tag).filter(Tag.id == tag_id).first()
    if not tag:
        raise HTTPException(status_code=404, detail="Tag not found")
    db.query(TaskTag).filter(TaskTag.tag_id == tag_id).delete()
    db.delete(tag)
    db.commit()
    return Response(content="", status_code=200)
