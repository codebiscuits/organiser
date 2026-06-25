from sqlalchemy import Column, String, Integer, DateTime
from sqlalchemy.sql import func

from app.models import Base


class PushSubscription(Base):
    __tablename__ = "push_subscriptions"

    id = Column(Integer, primary_key=True, autoincrement=True)
    endpoint = Column(String, unique=True, nullable=False)
    p256dh = Column(String, nullable=False)
    auth = Column(String, nullable=False)
    created_at = Column(DateTime, server_default=func.now())
