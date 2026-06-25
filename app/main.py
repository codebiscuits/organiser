from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from app.database import engine, run_migrations
from app.models import Base
from app.routers import tasks, workouts, admin, presets, undo
from app.routers import push as push_router
from app.services import scheduler


@asynccontextmanager
async def lifespan(app: FastAPI):
    scheduler.start()
    yield
    scheduler.stop()


app = FastAPI(title="Life Organiser", lifespan=lifespan)

# Create database tables
Base.metadata.create_all(bind=engine)
run_migrations(engine)

# Static files and templates
app.mount("/static", StaticFiles(directory="app/static"), name="static")
templates = Jinja2Templates(directory="app/templates")


@app.get("/sw.js")
async def service_worker():
    return FileResponse("app/static/sw.js", media_type="application/javascript")


@app.get("/")
async def index(request: Request):
    return templates.TemplateResponse(request, "index.html")


# Include routers
app.include_router(tasks.router, prefix="/tasks", tags=["tasks"])
app.include_router(workouts.router, prefix="/workouts", tags=["workouts"])
app.include_router(admin.router, prefix="/admin", tags=["admin"])
app.include_router(presets.router, prefix="/presets", tags=["presets"])
app.include_router(undo.router, prefix="/undo", tags=["undo"])
app.include_router(push_router.router, prefix="/push", tags=["push"])


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("app.main:app", host="0.0.0.0", port=8888, reload=True)
