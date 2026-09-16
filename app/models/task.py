from sqlalchemy import Column, String, Integer, Boolean, DateTime, Time, Text, CheckConstraint, ForeignKey
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
import uuid

from app.models import Base


class Task(Base):
    __tablename__ = "tasks"
    
    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    type = Column(String, nullable=False)  # appointment, deadline, recurring, variable_recurring, errand, workout
    title = Column(String, nullable=False)
    notes = Column(Text)
    estimated_duration = Column(Integer)  # minutes
    importance = Column(Integer, CheckConstraint("importance BETWEEN 1 AND 3"))
    urgency = Column(Integer, CheckConstraint("urgency BETWEEN 1 AND 3"))  # NULL for calculated types
    allow_afternoon = Column(Boolean, default=False)
    deadline_at = Column(DateTime)  # for deadlines (and errands — every errand gets one, see deadline_auto)
    deadline_auto = Column(Boolean, nullable=False, default=False, server_default="0")  # True: deadline_at was auto-set on an errand (Theme A A4); never pinned or swept
    # Optional lead time for deadline-based tasks. While the due date is farther
    # away than this many days, the task is not a daily-list candidate.
    # NULL preserves legacy behaviour (available immediately).
    planning_window_days = Column(Integer, CheckConstraint("planning_window_days >= 0"))
    scheduled_at = Column(DateTime)  # for appointments
    prep_duration = Column(Integer)  # minutes, for appointments
    scheduled_time = Column(Time)  # optional, for recurring tasks with fixed daily time
    location = Column(String)
    status = Column(String, default="pending")
    deferred_count = Column(Integer, default=0)
    snooze_until = Column(String)  # date string YYYY-MM-DD; hide task until this date
    manual_scheduled_time = Column(DateTime)  # user-set time from timeline drag-and-drop
    preset_id = Column(Integer, ForeignKey("task_presets.id"), nullable=True)
    allowed_days = Column(String)  # comma-separated day indices, Sunday=0
    # Theme A A5: id of the task this one was auto-generated from (prep tasks;
    # later reused by tag-recipe auto-tasks). Plain string, no FK — the parent
    # may be hard-deleted while a completed child's history row survives.
    generated_from_task_id = Column(String)
    push_notified_at = Column(DateTime)
    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())

    tags = relationship("Tag", secondary="task_tags", lazy="select")
    notifications = relationship(
        "TaskNotification",
        back_populates="task",
        cascade="all, delete-orphan",
        lazy="select",
    )

    @property
    def notification_offsets(self):
        return sorted(n.offset_minutes for n in self.notifications)

    @property
    def notification_anchor(self):
        """
        The time this task's notifications count back from, or None if it has none.

        An appointment anchors on scheduled_at: the time Ross has to be somewhere.
        A deadline anchors on deadline_at: the time the work has to be finished by.

        Every other type deliberately returns None. Recurring, variable_recurring
        and workout tasks are placed on a day, not at a time. An errand is given an
        automatic far-future deadline_at (see apply_errand_auto_deadline), which is
        a scheduling horizon rather than a real commitment, so counting back from it
        would fire an alert about a date Ross never chose.
        """
        if self.type == "deadline":
            return self.deadline_at
        if self.type == "appointment":
            return self.scheduled_at
        return None


class CompletedTask(Base):
    __tablename__ = "completed_tasks"

    id = Column(Integer, primary_key=True, autoincrement=True)
    task_id = Column(String)  # optional reference; may be null if task was later deleted
    completed_at = Column(DateTime, server_default=func.now())
    actual_duration = Column(Integer)  # minutes
    notes = Column(Text)
    task_type = Column(String)  # snapshot at completion time
    task_title = Column(String)  # snapshot at completion time
    auto_completed = Column(Boolean, default=False)  # True if completed by the overdue sweep, not the user
