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
        "ALTER TABLE action_log ADD COLUMN exclusions_snapshot TEXT",
        "ALTER TABLE tasks ADD COLUMN deadline_auto BOOLEAN NOT NULL DEFAULT 0",
        # Theme A component A4 data migration: existing errands with no
        # deadline get an auto-deadline of max(created_at + 365 days,
        # today + 30 days) — old stock starts moving without anything
        # becoming instantly urgent. Idempotent: only touches errands whose
        # deadline_at IS NULL, so re-running on every startup is a no-op
        # once they're dated. SQLite's scalar MAX compares the ISO-8601
        # strings lexicographically, which is chronologically correct.
        """UPDATE tasks
           SET deadline_at = MAX(
                   datetime(created_at, '+365 days'),
                   datetime('now', 'localtime', '+30 days')
               ),
               deadline_auto = 1
           WHERE type = 'errand' AND deadline_at IS NULL""",
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
