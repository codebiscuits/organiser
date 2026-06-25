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
        "ALTER TABLE tasks ADD COLUMN push_notified_at DATETIME",
        """CREATE TABLE IF NOT EXISTS push_subscriptions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            endpoint VARCHAR UNIQUE NOT NULL,
            p256dh VARCHAR NOT NULL,
            auth VARCHAR NOT NULL,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP
        )""",
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
