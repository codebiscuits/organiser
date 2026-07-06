from sqlalchemy import Column, Integer, String, DateTime, ForeignKey
from sqlalchemy.orm import relationship

from app.models import Base


class TaskNotification(Base):
    """
    A single user-configured notification attached to a task.

    Stored as an offset (not an absolute time) so rescheduling the task
    automatically moves any unfired notification:
    - offset_minutes == 0  -> fire at the task's scheduled_at
    - offset_minutes == N  -> fire N minutes before scheduled_at
    """

    __tablename__ = "task_notifications"

    id = Column(Integer, primary_key=True, autoincrement=True)
    task_id = Column(String, ForeignKey("tasks.id", ondelete="CASCADE"), nullable=False)
    offset_minutes = Column(Integer, nullable=False)
    sent_at = Column(DateTime, nullable=True)

    task = relationship("Task", back_populates="notifications")
