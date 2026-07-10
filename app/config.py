from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    database_url: str = "sqlite:///./organiser.db"
    
    # Scheduling windows (hours)
    main_window_start: int = 9
    main_window_end: int = 15
    afternoon_window_start: int = 15
    afternoon_window_end: int = 18
    evening_window_start: int = 18
    evening_window_end: int = 23
    
    # Urgency thresholds (days)
    urgency_low_threshold: int = 7
    urgency_medium_threshold: int = 2

    # VRT overdue escalation: overdue_ratio >= this fraction of the task's
    # own cadence pushes effective urgency to 3 (see effective_urgency_for_vrt)
    vrt_escalation_half_ratio: float = 0.5

    # Auto-generated prep task for long deadlines (Theme A A5):
    # created at prep_task_fraction of the creation->deadline span, only for
    # deadlines at least prep_task_min_span_days long
    prep_task_fraction: float = 0.75
    prep_task_min_span_days: int = 14

    # Errand auto-deadlines (Theme A A4): every new errand without a
    # user-chosen deadline gets deadline_at = now + this many days...
    errand_auto_deadline_days: int = 365
    # ...and once this fraction of the creation->deadline span has elapsed,
    # the daily view prompts "when will you actually do this?"
    errand_prompt_fraction: float = 0.5

    # Errand backlog boost (Theme A A3): when the pending-errand count
    # exceeds soft, the oldest third get +1 urgency; past hard, the oldest
    # third get +2 and the next third +1 (computed at list-build time,
    # never stored — see compute_errand_backlog_boosts)
    errand_backlog_soft: int = 8
    errand_backlog_hard: int = 15
    
    # Push notifications
    vapid_private_key: str = ""
    vapid_public_key: str = ""
    vapid_subject: str = "mailto:admin@example.com"
    notification_lead_minutes: int = 30

    class Config:
        env_file = ".env"


settings = Settings()
