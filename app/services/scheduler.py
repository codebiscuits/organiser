import logging
from datetime import datetime, timedelta

from apscheduler.schedulers.background import BackgroundScheduler

from app.config import settings
from app.database import SessionLocal
from app.services.push import send_push

logger = logging.getLogger(__name__)

_scheduler = BackgroundScheduler()


def _check_appointment_notifications():
    from app.models.task import Task
    from app.models.push_subscription import PushSubscription

    db = SessionLocal()
    try:
        now = datetime.now()
        subscriptions = db.query(PushSubscription).all()
        if not subscriptions:
            return

        # Find appointments whose notification window falls within the next minute
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

            if now <= notify_at < now + timedelta(minutes=1):
                mins_until = int((task.scheduled_at - now).total_seconds() / 60)
                body = f"In {mins_until} minutes"
                if task.location:
                    body += f" — {task.location}"

                for sub in subscriptions:
                    send_push(sub, task.title, body)

                task.push_notified_at = now
                db.commit()
                logger.info("Sent push for appointment: %s", task.title)
    except Exception:
        logger.exception("Error in appointment notification check")
        db.rollback()
    finally:
        db.close()


def start():
    _scheduler.add_job(
        _check_appointment_notifications,
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
