"""Configuration management for API service."""
import secrets
from urllib.parse import quote_plus

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application settings loaded from environment variables."""

    # Postgres
    postgres_host: str = "postgres"
    postgres_port: int = 5432
    postgres_user: str = "quiz"
    postgres_password: str = "quiz"
    postgres_db: str = "quiz"
    postgres_connect_timeout: int = Field(default=2, ge=1)
    postgres_pool_size: int = Field(default=10, ge=1, le=100)
    postgres_max_overflow: int = Field(default=20, ge=0, le=200)
    postgres_pool_recycle: int = Field(default=1800, ge=60)
    postgres_sslmode: str | None = None
    postgres_sslrootcert: str | None = None

    # API
    api_port: int = Field(default=8000, ge=1, le=65535)
    max_request_size_bytes: int = Field(default=32 * 1024, ge=1024)
    default_quiz_version: str = "1"
    session_cookie_name: str = "quiz_platform_session"
    student_session_cookie_name: str = "quiz_platform_student_session"
    admin_session_cookie_name: str = "quiz_platform_admin_session"
    session_ttl_seconds: int = Field(default=60 * 60 * 12, ge=300)
    allow_open_registration: bool = True
    bootstrap_admin_email: str = "admin@example.com"
    bootstrap_admin_password: str = "change-me-admin"
    bootstrap_admin_name: str = "Platform Admin"
    admin_default_page_size: int = Field(default=25, ge=5, le=250)
    admin_max_page_size: int = Field(default=100, ge=10, le=500)
    attempt_default_page_size: int = Field(default=10, ge=1, le=50)
    attempt_max_page_size: int = Field(default=20, ge=1, le=100)
    app_timezone: str = "Asia/Kolkata"
    payu_mode: str = "test"
    payu_payment_url: str | None = None
    payu_certificate_fee: str | None = None

    # Security
    csrf_secret_key: str = Field(default_factory=lambda: secrets.token_urlsafe(32))
    secure_cookies: bool = False  # Set True in production (HTTPS)
    cors_allowed_origins: str = ""  # Comma-separated origins, empty = same-origin only

    # Rate Limiting (per IP, per minute)
    rate_limit_login: int = Field(default=10, ge=1)
    rate_limit_register: int = Field(default=5, ge=1)
    rate_limit_api: int = Field(default=60, ge=1)

    # Environment
    environment: str = "development"

    model_config = SettingsConfigDict(env_file=".env", case_sensitive=False, extra="ignore")

    @property
    def sqlalchemy_database_url(self) -> str:
        return (
            "postgresql+asyncpg://"
            f"{quote_plus(self.postgres_user)}:{quote_plus(self.postgres_password)}"
            f"@{self.postgres_host}:{self.postgres_port}/{quote_plus(self.postgres_db)}"
        )

    @property
    def is_production(self) -> bool:
        return self.environment.lower() in ("production", "prod")

    @property
    def parsed_cors_origins(self) -> list[str]:
        if not self.cors_allowed_origins:
            return []
        return [origin.strip() for origin in self.cors_allowed_origins.split(",") if origin.strip()]


settings = Settings()
