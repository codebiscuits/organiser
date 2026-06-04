from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.preset import TaskPreset
from app.schemas.task import TaskPresetCreate, TaskPresetResponse

router = APIRouter()


@router.get("/", response_model=list[TaskPresetResponse])
async def list_presets(db: Session = Depends(get_db)):
    return db.query(TaskPreset).order_by(TaskPreset.name).all()


@router.post("/", response_model=TaskPresetResponse)
async def create_preset(data: TaskPresetCreate, db: Session = Depends(get_db)):
    preset = TaskPreset(
        name=data.name,
        type=data.type.value,
        title=data.title,
        notes=data.notes,
        estimated_duration=data.estimated_duration,
        importance=data.importance,
        urgency=data.urgency,
        allow_afternoon=data.allow_afternoon,
        deadline_at=data.deadline_at,
        scheduled_at=data.scheduled_at,
        prep_duration=data.prep_duration,
        scheduled_time=data.scheduled_time,
        location=data.location,
        interval_type=data.interval_type,
        interval_multiple=data.interval_multiple,
        day_of_week=data.day_of_week,
        day_of_month=data.day_of_month,
        month_of_year=data.month_of_year,
        allowed_days=data.allowed_days,
    )
    db.add(preset)
    db.commit()
    db.refresh(preset)
    return preset


@router.get("/{preset_id}", response_model=TaskPresetResponse)
async def get_preset(preset_id: int, db: Session = Depends(get_db)):
    preset = db.query(TaskPreset).filter(TaskPreset.id == preset_id).first()
    if not preset:
        raise HTTPException(status_code=404, detail="Preset not found")
    return preset


@router.delete("/{preset_id}")
async def delete_preset(preset_id: int, db: Session = Depends(get_db)):
    preset = db.query(TaskPreset).filter(TaskPreset.id == preset_id).first()
    if not preset:
        raise HTTPException(status_code=404, detail="Preset not found")
    db.delete(preset)
    db.commit()
    return {"status": "ok"}
