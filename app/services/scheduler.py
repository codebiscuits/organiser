import logging
from datetime import datetime, timedelta

from apscheduler.schedulers.background import BackgroundScheduler

from app.config import settings
from app.database import SessionLocal
from app.services.push import send_push

logger = logging.getLogger(__name__)

_scheduler = BackgroundScheduler()


def _send_to_all(db, subscriptions, title, body) -> bool:
    """
    Attempt delivery to every subscription — no short-circuiting, so every
    device gets notified even if an earlier one already succeeded.

    Subscriptions the push service reports as gone (404/410) are deleted
    from the session (not committed here — the caller commits alongside
    its own state change).

    Returns True if at least one delivery succeeded.
    """
    sent = False
    for sub in subscriptions:
        result = send_push(sub, title, body)
        if result == "ok":
            sent = True
        elif result == "gone":
            db.delete(sub)
    return sent


def _check_appointment_notifications(db):
    from app.models.task import Task
    from app.models.push_subscription import PushSubscription

    subscriptions = db.query(PushSubscription).all()
    if not subscriptions:
        return

    now = datetime.now()
    appointments = (
        db.query(Task)
        .filter(
            Task.type == "appointment",
            Task.status == "pending",
            Task.scheduled_at.isnot(None),
            Task.push_notified_at.is_(None),
        )
        .all()
    )

    for task in appointments:
        lead = task.prep_duration or settings.notification_lead_minutes
        notify_at = task.scheduled_at - timedelta(minutes=lead)

        if notify_at <= now:
            mins_until = max(0, int((task.scheduled_at - now).total_seconds() / 60))
            body = f"In {mins_until} minutes"
            if task.location:
                body += f" — {task.location}"

            sent = _send_to_all(db, subscriptions, task.title, body)
            if sent:
                task.push_notified_at = now
                logger.info("Sent push for appointment: %s", task.title)
            else:
                logger.warning("Push delivery failed for appointment: %s", task.title)
            db.commit()


def _check_task_notifications(db):
    """
    Fire user-configured per-task notifications (app.models.task_notification).

    Additive to the automatic appointment reminder above — a task can have
    both. Each row fires once, at scheduled_at - offset_minutes.
    """
    from app.models.task import Task
    from app.models.task_notification import TaskNotification
    from app.models.push_subscription import PushSubscription

    subscriptions = db.query(PushSubscription).all()
    if not subscriptions:
        return

    now = datetime.now()
    notifications = (
        db.query(TaskNotification)
        .join(Task, TaskNotification.task_id == Task.id)
        .filter(
            Task.status == "pending",
            Task.scheduled_at.isnot(None),
            TaskNotification.sent_at.is_(None),
        )
        .all()
    )

    for notification in notifications:
        task = notification.task
        fire_at = task.scheduled_at - timedelta(minutes=notification.offset_minutes)

        if fire_at <= now:
            if notification.offset_minutes == 0:
                body = "Now"
            else:
                mins_until = max(0, int((task.scheduled_at - now).total_seconds() / 60))
                body = f"In {mins_until} minutes"
            if task.location:
                body += f" — {task.location}"

            sent = _send_to_all(db, subscriptions, task.title, body)
            if sent:
                notification.sent_at = now
                logger.info(
                    "Sent task notification for: %s (offset=%s)",
                    task.title,
                    notification.offset_minutes,
                )
            else:
                logger.warning("Task notification delivery failed for: %s", task.title)
            db.commit()


def run_notification_checks():
    db = SessionLocal()
    try:
        _check_appointment_notifications(db)
        _check_task_notifications(db)
    except Exception:
        logger.exception("Error in notification check")
        db.rollback()
    finally:
        db.close()


def start():
    _scheduler.add_job(
        run_notification_checks,
        "interval",
        minutes=1,
        id="appointment_notifications",
        replace_existing=True,
    )
    _scheduler.start()
    logger.info("Notification scheduler started")


def stop():
    if _scheduler.running:
        _scheduler.shutdown(wait=False)
