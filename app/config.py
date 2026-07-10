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

    # Errand auto-deadlines (Theme A A4): every new errand without a
    # user-chosen deadline gets deadline_at = now + this many days...
    errand_auto_deadline_days: int = 365
    # ...and once this fraction of the creation->deadline span has elapsed,
    # the daily view prompts "when will you actually do this?"
    errand_prompt_fraction: float = 0.5
    
    # Push notifications
    vapid_private_key: str = ""
    vapid_public_key: str = ""
    vapid_subject: str = "mailto:admin@example.com"
    notification_lead_minutes: int = 30

    class Config:
        env_file = ".env"


settings = Settings()
