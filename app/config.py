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
    
    # Push notifications
    vapid_private_key: str = ""
    vapid_public_key: str = ""
    vapid_subject: str = "mailto:admin@example.com"
    notification_lead_minutes: int = 30

    class Config:
        env_file = ".env"


settings = Settings()
