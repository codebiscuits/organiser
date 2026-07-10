"""
Theme A component A5 — auto-generated prep task at 75% of a deadline's life.

A deadline task with a long enough runway (>= prep_task_min_span_days between
creation and deadline) gets a sibling "Prep: {title}" deadline task dated at
prep_task_fraction of that span, so big deadlines produce an early start
signal instead of sitting at urgency 1 until the buffer maths flips.

The prep task is a real Task row (complete/defer/snooze/undo all work for
free) linked to its parent via tasks.generated_from_task_id — a sibling with
a pointer, deliberately NOT a parent/child hierarchy (sub-tasks are a
post-Theme-A design). See docs/design-theme-a.md §3 A5.
"""
from datetime import datetime, date, timedelta

from sqlalchemy.orm import Session

from app.config import settings
from app.models.task import Task


def delete_pending_prep_tasks(db: Session, parent_task_id: str) -> None:
    """
    Remove any pending auto-generated tasks pointing at this parent.

    Called when the parent is completed or deleted — a prep nudge for a
    finished/gone deadline is noise. Completed prep tasks are untouched
    (their Task rows are already gone; history lives in completed_tasks).
    """
    db.query(Task).filter(
        Task.generated_from_task_id == parent_task_id,
        Task.status == "pending",
    ).delete()


def sync_prep_task(db: Session, task: Task) -> None:
    """
    Create, update, or remove the auto prep task to match `task`'s current
    deadline. Call after creating a deadline task or editing one in place.

    Rules:
    - Only deadline tasks with a deadline_at qualify; anything else (or a
      task that is itself auto-generated — no chaining) clears any pending
      prep task it may have and stops.
    - Span = creation -> deadline. Below prep_task_min_span_days, no prep
      task (buffer urgency already covers short deadlines).
    - Prep date = created + prep_task_fraction × span. If that lands today
      or earlier (e.g. the deadline was edited much closer), the prep task
      is pointless — it would only be swept as overdue — so none is kept.
    - An existing pending prep task is updated in place (date, title,
      importance follow the parent). A completed prep task's row is gone
      (deadline completion deletes the Task row), so a later deadline edit
      creates a fresh prep task — deliberate: if the deadline moved far
      enough out that a new prep point exists, a new nudge is warranted.
    """
    existing = db.query(Task).filter(
        Task.generated_from_task_id == task.id,
        Task.status == "pending",
    ).first()

    eligible = (
        task.type == "deadline"
        and not task.generated_from_task_id
        and task.deadline_at is not None
    )

    prep_deadline = None
    if eligible:
        created = task.created_at or datetime.now()
        span = task.deadline_at - created
        if span < timedelta(days=settings.prep_task_min_span_days):
            eligible = False
        else:
            prep_deadline = created + span * settings.prep_task_fraction
            if prep_deadline.date() <= date.today():
                eligible = False

    if not eligible:
        if existing:
            db.delete(existing)
        return

    if existing:
        existing.deadline_at = prep_deadline
        existing.title = f"Prep: {task.title}"
        existing.importance = task.importance
        return

    db.add(Task(
        type="deadline",
        title=f"Prep: {task.title}",
        estimated_duration=max(15, (task.estimated_duration or 60) // 4),
        importance=task.importance,
        allow_afternoon=task.allow_afternoon,
        deadline_at=prep_deadline,
        status="pending",
        generated_from_task_id=task.id,
    ))
