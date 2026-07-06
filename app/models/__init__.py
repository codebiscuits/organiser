from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    pass


from app.models.task import Task, CompletedTask
from app.models.recurrence import Recurrence, Projection
from app.models.workout import MuscleGroup, Exercise, ExerciseMuscle, PerformedSet
from app.models.user import User
from app.models.preset import TaskPreset
from app.models.action_log import ActionLog
from app.models.tag import Tag, TaskTag
from app.models.push_subscription import PushSubscription
from app.models.task_notification import TaskNotification
