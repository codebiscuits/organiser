from sqlalchemy import Column, String, Integer, ForeignKey
from app.models import Base


class Tag(Base):
    __tablename__ = "tags"

    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String, nullable=False)
    icon = Column(String, nullable=False, default="tag")
    color = Column(String, nullable=False, default="#7eb8d4")


class TaskTag(Base):
    __tablename__ = "task_tags"

    task_id = Column(String, ForeignKey("tasks.id"), primary_key=True)
    tag_id = Column(Integer, ForeignKey("tags.id"), primary_key=True)
