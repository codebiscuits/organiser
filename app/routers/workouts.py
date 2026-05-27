from fastapi import APIRouter, Depends, Request
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from app.database import get_db

router = APIRouter()
templates = Jinja2Templates(directory="app/templates")


@router.get("/today")
async def get_todays_workout(request: Request, db: Session = Depends(get_db)):
    # TODO: Implement workout algorithm to select exercises
    return templates.TemplateResponse(
        "workout.html",
        {"request": request, "exercises": []}
    )


@router.post("/log")
async def log_workout(request: Request, db: Session = Depends(get_db)):
    # TODO: Implement workout logging
    pass


@router.get("/history")
async def workout_history(request: Request, db: Session = Depends(get_db)):
    # TODO: Implement workout history view
    pass
