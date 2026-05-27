from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    pass


from app.models.task import Task, CompletedTask
from app.models.recurrence import Recurrence, Projection
from app.models.workout import MuscleGroup, Exercise, ExerciseMuscle, PerformedSet
from app.models.user import User
