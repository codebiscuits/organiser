from fastapi import FastAPI, Request
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from app.database import engine
from app.models import Base
from app.routers import tasks, workouts, admin

app = FastAPI(title="Life Organiser")

# Create database tables
Base.metadata.create_all(bind=engine)

# Static files and templates
app.mount("/static", StaticFiles(directory="app/static"), name="static")
templates = Jinja2Templates(directory="app/templates")


@app.get("/")
async def index(request: Request):
    return templates.TemplateResponse(request, "index.html")


# Include routers
app.include_router(tasks.router, prefix="/tasks", tags=["tasks"])
app.include_router(workouts.router, prefix="/workouts", tags=["workouts"])
app.include_router(admin.router, prefix="/admin", tags=["admin"])
