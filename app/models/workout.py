from sqlalchemy import Column, String, Integer, DateTime, Numeric, Text, ForeignKey, CheckConstraint
from sqlalchemy.sql import func

from app.models import Base


class MuscleGroup(Base):
    __tablename__ = "muscle_groups"
    
    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String, nullable=False)
    recovery_time = Column(Integer, nullable=False)  # days


class Exercise(Base):
    __tablename__ = "exercises"
    
    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String, nullable=False)
    description = Column(Text)
    intensity = Column(String, CheckConstraint("intensity IN ('heavy', 'light')"), nullable=False)


class ExerciseMuscle(Base):
    __tablename__ = "exercise_muscles"
    
    exercise_id = Column(Integer, ForeignKey("exercises.id"), primary_key=True)
    muscle_id = Column(Integer, ForeignKey("muscle_groups.id"), primary_key=True)


class PerformedSet(Base):
    __tablename__ = "performed_sets"
    
    id = Column(Integer, primary_key=True, autoincrement=True)
    exercise_id = Column(Integer, ForeignKey("exercises.id"))
    created_at = Column(DateTime, server_default=func.now())
    reps = Column(Integer)
    weight_kg = Column(Numeric)
    num_sets = Column(Integer)
    intensity = Column(String, CheckConstraint("intensity IN ('heavy', 'light')"))
