from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker, Session
from typing import Generator

from app.config import settings

engine = create_engine(
    settings.database_url,
    connect_args={"check_same_thread": False}  # SQLite specific
)

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


def run_migrations(eng):
    migrations = [
        "ALTER TABLE tasks ADD COLUMN preset_id INTEGER",
        "ALTER TABLE tasks ADD COLUMN allowed_days VARCHAR",
        "ALTER TABLE completed_tasks ADD COLUMN task_type VARCHAR",
        "ALTER TABLE completed_tasks ADD COLUMN task_title VARCHAR",
        "ALTER TABLE completed_tasks ADD COLUMN auto_completed BOOLEAN DEFAULT 0",
    ]
    with eng.connect() as conn:
        for sql in migrations:
            try:
                conn.execute(text(sql))
                conn.commit()
            except Exception:
                conn.rollback()


def get_db() -> Generator[Session, None, None]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
