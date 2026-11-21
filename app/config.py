from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "sqlite:///./booking.db"
    secret_key: str = "dev-only-change-me-dev-only-change-me"
    token_minutes: int = 60
    default_timezone: str = "America/New_York"
    slot_step_minutes: int = 15
    min_notice_minutes: int = 60
    max_days_ahead: int = 90
    login_rate_limit: int = 5
    login_rate_window_seconds: int = 60
    owner_email: str | None = None
    owner_password: str | None = None
    auto_create_tables: bool = False
