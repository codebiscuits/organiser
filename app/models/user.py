from sqlalchemy import Column, String, Integer, DateTime, Text
from sqlalchemy.sql import func
import uuid

from app.models import Base


class User(Base):
    __tablename__ = "users"
    
    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    email = Column(String)
    push_subscription = Column(Text)  # JSON for web push
    available_hours_per_day = Column(Integer, default=6)
    preferences = Column(Text)  # JSON
    created_at = Column(DateTime, server_default=func.now())
