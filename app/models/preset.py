from sqlalchemy import Column, String, Integer, Boolean, DateTime, Time, Text
from sqlalchemy.sql import func

from app.models import Base


class TaskPreset(Base):
    __tablename__ = "task_presets"

    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String, nullable=False)
    type = Column(String, nullable=False)
    title = Column(String)
    notes = Column(Text)
    estimated_duration = Column(Integer)
    importance = Column(Integer)
    urgency = Column(Integer)
    allow_afternoon = Column(Boolean, default=False)
    deadline_at = Column(DateTime)
    scheduled_at = Column(DateTime)
    prep_duration = Column(Integer)
    scheduled_time = Column(Time)
    location = Column(String)
    # Recurrence fields (flattened)
    interval_type = Column(String)
    interval_multiple = Column(Integer, default=1)
    day_of_week = Column(String)
    day_of_month = Column(String)
    month_of_year = Column(String)
    # VRT-specific
    allowed_days = Column(String)
    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())
