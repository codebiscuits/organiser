from datetime import datetime, time
from enum import Enum
from pydantic import BaseModel, Field, field_validator


class TaskType(str, Enum):
    APPOINTMENT = "appointment"
    DEADLINE = "deadline"
    RECURRING = "recurring"
    VARIABLE_RECURRING = "variable_recurring"
    ERRAND = "errand"
    WORKOUT = "workout"


class RecurrenceCreate(BaseModel):
    interval_type: str = Field(..., pattern="^(daily|weekly|monthly|yearly)$")
    interval_multiple: int = Field(default=1, ge=1)
    day_of_week: str | None = None
    day_of_month: str | None = None
    month_of_year: str | None = None
    start_date: datetime | None = None
    end_date: datetime | None = None


class TagCreate(BaseModel):
    name: str
    icon: str
    color: str


class TagUpdate(BaseModel):
    name: str | None = None
    icon: str | None = None
    color: str | None = None


class TagResponse(BaseModel):
    id: int
    name: str
    icon: str
    color: str

    class Config:
        from_attributes = True


class TaskCreate(BaseModel):
    type: TaskType
    title: str
    notes: str | None = None
    estimated_duration: int | None = None
    importance: int = Field(..., ge=1, le=3)
    urgency: int | None = Field(default=None, ge=1, le=3)
    allow_afternoon: bool = False
    deadline_at: datetime | None = None
    scheduled_at: datetime | None = None
    prep_duration: int | None = None
    scheduled_time: time | None = None
    location: str | None = None
    recurrence: RecurrenceCreate | None = None
    preset_id: int | None = None
    allowed_days: str | None = None
    tag_ids: list[int] = []
    notification_offsets: list[int] = Field(default_factory=list)

    @field_validator("notification_offsets")
    @classmethod
    def _offsets_non_negative(cls, offsets: list[int]) -> list[int]:
        if any(offset < 0 for offset in offsets):
            raise ValueError("notification_offsets must be non-negative")
        return offsets


class TaskUpdate(BaseModel):
    type: TaskType | None = None
    title: str | None = None
    notes: str | None = None
    estimated_duration: int | None = None
    importance: int | None = Field(default=None, ge=1, le=3)
    urgency: int | None = Field(default=None, ge=1, le=3)
    allow_afternoon: bool | None = None
    deadline_at: datetime | None = None
    scheduled_at: datetime | None = None
    prep_duration: int | None = None
    scheduled_time: time | None = None
    location: str | None = None
    status: str | None = None
    recurrence: RecurrenceCreate | None = None
    preset_id: int | None = None
    allowed_days: str | None = None
    tag_ids: list[int] | None = None
    notification_offsets: list[int] | None = None  # None = leave unchanged, [] = remove all

    @field_validator("notification_offsets")
    @classmethod
    def _offsets_non_negative(cls, offsets: list[int] | None) -> list[int] | None:
        if offsets is not None and any(offset < 0 for offset in offsets):
            raise ValueError("notification_offsets must be non-negative")
        return offsets


class TaskResponse(BaseModel):
    id: str
    type: str
    title: str
    notes: str | None
    estimated_duration: int | None
    importance: int | None
    urgency: int | None
    allow_afternoon: bool
    deadline_at: datetime | None
    scheduled_at: datetime | None
    prep_duration: int | None
    scheduled_time: time | None
    location: str | None
    status: str
    deferred_count: int
    preset_id: int | None = None
    allowed_days: str | None = None
    notification_offsets: list[int] = []
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


class TaskCompleteRequest(BaseModel):
    actual_duration: int | None = None
    notes: str | None = None


class VariableRecurringCompleteRequest(TaskCompleteRequest):
    days_until_next: int = Field(..., ge=1)


class WorkoutCompleteRequest(BaseModel):
    exercise_id: int
    sets: int = Field(..., ge=1)
    reps: int = Field(..., ge=1)
    weight_kg: float = Field(..., ge=0)
    intensity: str = Field(..., pattern="^(heavy|light)$")


class TaskPresetCreate(BaseModel):
    name: str
    type: TaskType
    title: str | None = None
    notes: str | None = None
    estimated_duration: int | None = None
    importance: int | None = Field(default=None, ge=1, le=3)
    urgency: int | None = Field(default=None, ge=1, le=3)
    allow_afternoon: bool = False
    deadline_at: datetime | None = None
    scheduled_at: datetime | None = None
    prep_duration: int | None = None
    scheduled_time: time | None = None
    location: str | None = None
    interval_type: str | None = None
    interval_multiple: int | None = Field(default=None, ge=1)
    day_of_week: str | None = None
    day_of_month: str | None = None
    month_of_year: str | None = None
    allowed_days: str | None = None


class TaskPresetResponse(BaseModel):
    id: int
    name: str
    type: str
    title: str | None
    notes: str | None
    estimated_duration: int | None
    importance: int | None
    urgency: int | None
    allow_afternoon: bool
    deadline_at: datetime | None
    scheduled_at: datetime | None
    prep_duration: int | None
    scheduled_time: time | None
    location: str | None
    interval_type: str | None
    interval_multiple: int | None
    day_of_week: str | None
    day_of_month: str | None
    month_of_year: str | None
    allowed_days: str | None

    class Config:
        from_attributes = True
