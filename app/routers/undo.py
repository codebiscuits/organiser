import json
from datetime import date, datetime

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.action_log import ActionLog
from app.models.task import Task, CompletedTask
from app.models.recurrence import Recurrence, Projection, ProjectionExclusion
from app.models.workout import PerformedSet
from app.services.undo import task_from_dict, apply_task_dict

router = APIRouter()


@router.post("/{log_id}")
async def undo_action(log_id: int, db: Session = Depends(get_db)):
    log = db.query(ActionLog).filter(ActionLog.id == log_id).first()
    if not log:
        raise HTTPException(status_code=404, detail="Undo entry not found or expired")

    if log.action_type == "complete":
        _undo_complete(log, db)
    elif log.action_type == "defer":
        _undo_defer(log, db)
    elif log.action_type == "edit":
        _undo_edit(log, db)
    elif log.action_type == "delete":
        _undo_delete(log, db)
    elif log.action_type == "delete_instance":
        _undo_delete_instance(log, db)

    db.delete(log)
    db.commit()

    return Response(status_code=200, headers={"HX-Trigger": "taskUpdated"})


@router.post("/batch/{ids}")
async def undo_batch(ids: str, db: Session = Depends(get_db)):
    """Undo multiple action_log entries at once (e.g. a batch of auto-completions)."""
    log_ids = [int(part) for part in ids.split(",") if part]

    for log_id in log_ids:
        log = db.query(ActionLog).filter(ActionLog.id == log_id).first()
        if not log:
            continue

        if log.action_type == "complete":
            _undo_complete(log, db)
        elif log.action_type == "defer":
            _undo_defer(log, db)
        elif log.action_type == "edit":
            _undo_edit(log, db)
        elif log.action_type == "delete":
            _undo_delete(log, db)
        elif log.action_type == "delete_instance":
            _undo_delete_instance(log, db)

        db.delete(log)

    db.commit()
    return Response(status_code=200, headers={"HX-Trigger": "taskUpdated"})


def _undo_complete(log, db):
    auto_completed = False
    if log.completed_task_id:
        ct = db.query(CompletedTask).filter(CompletedTask.id == log.completed_task_id).first()
        if ct:
            auto_completed = bool(ct.auto_completed)
            db.delete(ct)

    if log.performed_set_id:
        ps = db.query(PerformedSet).filter(PerformedSet.id == log.performed_set_id).first()
        if ps:
            db.delete(ps)

    snap = json.loads(log.task_snapshot)
    task = db.query(Task).filter(Task.id == log.task_id).first()
    if not task:
        task = task_from_dict(snap)
        db.add(task)
        db.flush()

    if auto_completed:
        # The task was overdue when auto-completed — give it a fresh "due today"
        # so it's pinned and visible again rather than silently overdue once more.
        today = date.today()
        if task.type == "appointment" and task.scheduled_at and task.scheduled_at.date() < today:
            task.scheduled_at = datetime.combine(today, task.scheduled_at.time())
        elif task.type == "deadline" and task.deadline_at and task.deadline_at.date() < today:
            task.deadline_at = datetime.combine(today, task.deadline_at.time())

    db.query(Projection).filter(Projection.task_id == log.task_id).delete()
    for due_date_str in json.loads(log.projections_snapshot or "[]"):
        db.add(Projection(task_id=log.task_id, due_date=date.fromisoformat(due_date_str)))


def _undo_defer(log, db):
    task = db.query(Task).filter(Task.id == log.task_id).first()
    if not task:
        return

    snap = json.loads(log.task_snapshot)
    task.deferred_count = snap["deferred_count"]
    task.snooze_until = snap["snooze_until"]

    db.query(Projection).filter(Projection.task_id == log.task_id).delete()
    for due_date_str in json.loads(log.projections_snapshot or "[]"):
        db.add(Projection(task_id=log.task_id, due_date=date.fromisoformat(due_date_str)))


def _undo_edit(log, db):
    task = db.query(Task).filter(Task.id == log.task_id).first()
    if not task:
        return
    apply_task_dict(task, json.loads(log.task_snapshot))


def _undo_delete_instance(log, db):
    for due_date_str in json.loads(log.projections_snapshot or "[]"):
        d = date.fromisoformat(due_date_str)
        exists = db.query(Projection).filter(
            Projection.task_id == log.task_id,
            Projection.due_date == d,
        ).first()
        if not exists:
            db.add(Projection(task_id=log.task_id, due_date=d))

        # The delete recorded a ProjectionExclusion tombstone so regeneration
        # wouldn't resurrect it; undoing the delete means that tombstone no
        # longer applies.
        db.query(ProjectionExclusion).filter(
            ProjectionExclusion.task_id == log.task_id,
            ProjectionExclusion.due_date == d,
        ).delete()


def _undo_delete(log, db):
    snap = json.loads(log.task_snapshot)
    if not db.query(Task).filter(Task.id == log.task_id).first():
        db.add(task_from_dict(snap))
        db.flush()

    if log.recurrence_snapshot:
        if not db.query(Recurrence).filter(Recurrence.task_id == log.task_id).first():
            r = json.loads(log.recurrence_snapshot)
            db.add(Recurrence(
                task_id=log.task_id,
                interval_type=r["interval_type"],
                interval_multiple=r["interval_multiple"],
                day_of_week=r["day_of_week"],
                day_of_month=r["day_of_month"],
                month_of_year=r["month_of_year"],
                start_date=date.fromisoformat(r["start_date"]) if r["start_date"] else None,
                end_date=date.fromisoformat(r["end_date"]) if r["end_date"] else None,
            ))

    if log.projections_snapshot:
        db.query(Projection).filter(Projection.task_id == log.task_id).delete()
        for due_date_str in json.loads(log.projections_snapshot):
            db.add(Projection(task_id=log.task_id, due_date=date.fromisoformat(due_date_str)))

    if log.exclusions_snapshot:
        # Restore ProjectionExclusion tombstones the delete removed, so a
        # regeneration after undo doesn't resurrect an occurrence the user
        # had deliberately skipped before deleting the task entirely.
        db.query(ProjectionExclusion).filter(ProjectionExclusion.task_id == log.task_id).delete()
        for due_date_str in json.loads(log.exclusions_snapshot):
            db.add(ProjectionExclusion(task_id=log.task_id, due_date=date.fromisoformat(due_date_str)))
