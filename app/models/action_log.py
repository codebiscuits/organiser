from sqlalchemy import Column, String, Integer, Text, DateTime
from sqlalchemy.sql import func

from app.models import Base


class ActionLog(Base):
    __tablename__ = "action_log"

    id = Column(Integer, primary_key=True, autoincrement=True)
    action_type = Column(String, nullable=False)  # complete, defer, edit, delete
    task_id = Column(String, nullable=False)
    task_title = Column(String)
    task_snapshot = Column(Text)           # JSON of all task fields before action
    recurrence_snapshot = Column(Text)     # JSON of recurrence record (delete only)
    projections_snapshot = Column(Text)    # JSON array of due_date strings before action
    completed_task_id = Column(Integer)    # completed_tasks.id (complete actions)
    performed_set_id = Column(Integer)     # performed_sets.id (workout complete)
    performed_at = Column(DateTime, server_default=func.now())
