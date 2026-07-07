from sqlalchemy import Column, String, Integer, Date, DateTime, ForeignKey, UniqueConstraint
from sqlalchemy.sql import func

from app.models import Base


class Recurrence(Base):
    __tablename__ = "recurrence"
    
    id = Column(Integer, primary_key=True, autoincrement=True)
    task_id = Column(String, ForeignKey("tasks.id"))
    interval_type = Column(String, nullable=False)  # daily, weekly, monthly, yearly
    interval_multiple = Column(Integer, default=1)
    day_of_week = Column(String)  # "0,2,4" = Sun, Tue, Thu
    day_of_month = Column(String)  # "1,15" = 1st and 15th
    month_of_year = Column(String)  # "1,6" = Jan and Jun
    start_date = Column(Date)
    end_date = Column(Date)
    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())


class Projection(Base):
    __tablename__ = "projection"

    instance_id = Column(Integer, primary_key=True, autoincrement=True)
    task_id = Column(String, ForeignKey("tasks.id"))
    due_date = Column(Date, nullable=False)
    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())

    __table_args__ = (
        UniqueConstraint("task_id", "due_date", name="uq_task_due_date"),
    )


class ProjectionExclusion(Base):
    """
    Tombstone recording that a specific (task_id, due_date) occurrence was
    deliberately removed (via "delete today only" / week-view instance
    delete) and must not be regenerated the next time projections are
    (re)computed for this recurrence.

    Projection rows themselves are hard-deleted, so without this table a
    regeneration pass (create_task's initial loop, admin's regenerate-all,
    or the periodic refresh_projections job) has no way to distinguish
    "this date was never generated" from "this date was deleted on purpose"
    — it just sees a missing row either way and recreates it.
    """
    __tablename__ = "projection_exclusion"

    id = Column(Integer, primary_key=True, autoincrement=True)
    task_id = Column(String, ForeignKey("tasks.id"))
    due_date = Column(Date, nullable=False)
    created_at = Column(DateTime, server_default=func.now())

    __table_args__ = (
        UniqueConstraint("task_id", "due_date", name="uq_excl_task_due_date"),
    )
