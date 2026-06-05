from sqlalchemy import Column, String, Integer, Boolean, DateTime, Time, Text, CheckConstraint, ForeignKey
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
    deadline_at = Column(DateTime)  # for deadlines
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
    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())


class CompletedTask(Base):
    __tablename__ = "completed_tasks"

    id = Column(Integer, primary_key=True, autoincrement=True)
    task_id = Column(String)  # optional reference; may be null if task was later deleted
    completed_at = Column(DateTime, server_default=func.now())
    actual_duration = Column(Integer)  # minutes
    notes = Column(Text)
    task_type = Column(String)  # snapshot at completion time
    task_title = Column(String)  # snapshot at completion time
